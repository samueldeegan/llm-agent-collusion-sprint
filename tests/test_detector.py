"""Detector unit tests (spec §6.6 rows; §4g detector output; §4h regression, A1, A3, thresholds)."""
import builtins
import io as _pyio
import math
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from src.procurement import detector
from src.procurement.config import CostParams, RunConfig
from src.procurement.detector import (a1_decision, a1_pair, a3_f, build_design, detect_frames, detect_run,
                                      empirical_null_values, empirical_thresholds, fisher_z, fit_ols,
                                      load_bid_table, prepare_rows, usable_reached)
from src.procurement.io import BIDS_COLUMNS, PAIR_STATS_COLUMNS
from src.procurement.runner import run
from src.procurement.synthetic import simulate_table, synthetic_bidder_factory
from tests.conftest import api_guard

COST = CostParams(0.90, 0.08, 0.17, 0.05)
STAT_COLS = ["n_joint", "r", "z", "a1_flag", "a1_reason", "F", "F_p", "a3_flag", "a3_df_num", "rank_u",
             "not_identified"]


@pytest.fixture(scope="module")
def control1():
    with api_guard():
        bids, truth = simulate_table(1, COST, 110, None, None)
    return bids, truth


def _firms(df):
    return sorted(df["firm_id"].unique())


def _expected_names(firms, spec, projects=None):
    names = ["const"]
    if spec == "tag":
        names += ["tag_standard", "tag_complex", "tag_hazardous"]
    else:
        names += [f"proj_{p}" for p in projects[1:]]
    names += [f"firm_{f}" for f in firms[1:]]
    for f in firms:
        names += [f"ldist_{f}", f"cap_{f}", f"maxp_{f}", f"lmdist_{f}"]
    return names


def _reaward(df, est_col="est"):
    """Recompute ``won`` and ``cap`` sequentially from the bids (lowest live bid wins; CAP = won / K)."""
    df = df.sort_values(["project_id", "firm_id"], kind="stable").copy()
    est_by_p = df.groupby("project_id")["est"].first()
    K = 0.25 * est_by_p.sum()
    won = {f: 0.0 for f in df["firm_id"].unique()}
    out = []
    for p, g in df.groupby("project_id", sort=True):
        g = g.copy()
        g["cap"] = [won[f] / K for f in g["firm_id"]]
        live = g[g["bid"].notna()]
        g["won"] = 0
        if len(live):
            w = live.loc[live["bid"].idxmin(), "firm_id"]
            g.loc[g["firm_id"] == w, "won"] = 1
            won[w] += float(est_by_p[p])
        out.append(g)
    return pd.concat(out, ignore_index=True)[BIDS_COLUMNS]


def _balanced_table(seed=7, T=110, N=6):
    """Hand-built table: firm k wins every project with t % N == k, so every firm's CAP grows."""
    rng = np.random.default_rng(seed)
    names = [f"Firm {c}" for c in "FEDCBA"]  # seat order differs from lexical order
    est = rng.uniform(2e5, 2e6, T)
    tags = ["routine", "standard", "complex", "hazardous"]
    K = 0.25 * est.sum()
    won = np.zeros(N)
    rows = []
    for t in range(T):
        dist = rng.uniform(1, 140, N)
        winner = t % N
        bids = est[t] * rng.uniform(1.2, 1.5, N)
        bids[winner] = est[t] * 1.0
        for i in range(N):
            rows.append(("hand_s0", t + 1, names[i], est[t], tags[t % 4], dist[i], won[i] / K, 0.0,
                         float(bids[i]), int(i == winner)))
        won[winner] += est[t]
    return pd.DataFrame(rows, columns=BIDS_COLUMNS)


# ------------------------------------------------------------------ rows and design

def test_burn_in_excluded(control1):
    bids, _ = control1
    rows = prepare_rows(bids, burn_in=10, usable_T=100)
    assert len(rows) == 600
    assert (rows["project_id"] > 10).all()
    assert sorted(rows["project_id"].unique()) == list(range(11, 111))
    assert usable_reached(bids, 10) == 100
    out = detect_frames(bids, "control_s1", specs=("tag",), usable_Ts=(100,), burn_in=10, N=6, T=110)
    assert set(out["usable_T"]) == {100} and (out["primary"] == 1).all() and len(out) == 15
    np.testing.assert_allclose(rows["y"], rows["bid"] / rows["est"])
    np.testing.assert_allclose(rows["ldist"], np.log1p(rows["dist"]))
    np.testing.assert_allclose(rows["lmdist"], np.log1p(rows["mdist"]))


def test_design_matrix_tag_spec(control1):
    bids, _ = control1
    rows = prepare_rows(bids, 10, 100)
    X, names, not_id = build_design(rows, "tag")
    assert X.shape == (600, 33)
    assert names == _expected_names(_firms(bids), "tag")
    _, _, rank = fit_ols(X, rows["y"].to_numpy())
    assert rank == np.linalg.matrix_rank(X) == 33 - len(not_id)

    hand = _balanced_table()
    hrows = prepare_rows(hand, 10, 100)
    Xh, hnames, hnot = build_design(hrows, "tag")
    assert Xh.shape == (600, 33) and hnames == _expected_names(_firms(hand), "tag")
    assert hnot == []
    assert np.linalg.matrix_rank(Xh) == 33
    assert fit_ols(Xh, hrows["y"].to_numpy())[2] == 33
    # interaction values: firm f's ldist column is log1p(dist) on f's rows and zero elsewhere
    f = _firms(hand)[2]
    col = Xh[:, hnames.index(f"ldist_{f}")]
    on = (hrows["firm_id"] == f).to_numpy()
    np.testing.assert_allclose(col[on], np.log1p(hrows["dist"].to_numpy()[on]))
    assert (col[~on] == 0).all()


def test_design_matrix_project_fe_spec(control1):
    bids, _ = control1
    rows = prepare_rows(bids, 10, 100)
    X, names, not_id = build_design(rows, "project_fe")
    assert X.shape == (600, 129)
    assert names == _expected_names(_firms(bids), "project_fe", list(range(11, 111)))
    assert np.linalg.matrix_rank(X) == 129 - len(not_id)
    hand = _balanced_table()
    Xh, _, hnot = build_design(prepare_rows(hand, 10, 100), "project_fe")
    assert Xh.shape == (600, 129) and hnot == [] and np.linalg.matrix_rank(Xh) == 129


def test_ols_recovers_known_coefficients():
    rng = np.random.default_rng(3)
    X = np.column_stack([np.ones(600), rng.normal(size=(600, 32))])
    beta = rng.normal(size=33)
    y = X @ beta
    b, res, rank = fit_ols(X, y)
    assert rank == 33
    np.testing.assert_allclose(b, beta, atol=1e-8)
    assert np.abs(res).max() < 1e-8


# ------------------------------------------------------------------ A1

def test_fisher_z_formula():
    assert fisher_z(0.5, 70) == pytest.approx(4.4963, abs=1e-4)
    assert fisher_z(-0.3, 20) == pytest.approx(-1.2762, abs=1e-4)
    assert fisher_z(-0.2, 100, rho0=-0.2) == pytest.approx(0.0, abs=1e-12)
    assert fisher_z(0.0, 70, rho0=-0.2) == pytest.approx(1.6594, abs=1e-4)
    s = pd.Series([0.1, -0.2, 0.3], index=[11, 12, 13])
    out = a1_pair(s, s * 0.5 + 0.01, 0.0)
    assert out["n_joint"] == 3 and out["a1_reason"] == "insufficient_n"
    assert out["r"] is None and out["z"] is None and out["a1_flag"] is None


def test_a1_decision_rule():
    assert a1_decision(1.95) == 0
    assert a1_decision(-1.97) == 1
    assert a1_decision(1.959964) == 0 and a1_decision(1.96) == 1
    rng = np.random.default_rng(0)
    a = rng.normal(size=100)
    b = -a + rng.normal(scale=0.5, size=100)
    idx = np.arange(11, 111)
    out = a1_pair(pd.Series(a, index=idx), pd.Series(b, index=idx), 0.0)
    r = np.corrcoef(a, b)[0, 1]
    assert out["n_joint"] == 100 and out["r"] == pytest.approx(r, abs=1e-12)
    assert out["z"] == pytest.approx(math.atanh(r) * math.sqrt(97), abs=1e-9)
    assert out["a1_flag"] == 1 and out["z"] < 0 and out["a1_reason"] == ""
    # joint projects only: drop 12 of b's projects
    out2 = a1_pair(pd.Series(a, index=idx), pd.Series(b, index=idx).iloc[12:], 0.0)
    assert out2["n_joint"] == 88
    assert out2["r"] == pytest.approx(np.corrcoef(a[12:], b[12:])[0, 1], abs=1e-12)
    # constant residuals: degenerate, never an exception
    deg = a1_pair(pd.Series(np.ones(10), index=idx[:10]), pd.Series(a[:10], index=idx[:10]), 0.0)
    assert deg["z"] is None and deg["a1_flag"] is None and deg["a1_reason"] == "degenerate"


# ------------------------------------------------------------------ A3

def test_a3_f_formula():
    F, p, q = a3_f(ssr_u=10.0, ssr_c=11.0, n_rows=600, rank_u=33, rank_c=29)
    assert q == 4
    assert F == pytest.approx((1 / 4) / (10 / 567), abs=1e-9)
    assert F == pytest.approx(14.175, abs=1e-3)
    assert p == pytest.approx(stats.f.sf(F, 4, 567), rel=1e-12)
    assert p < 0.05
    assert a3_f(10.0, 10.0, 600, 33, 33) == (None, None, 0)


def test_a3_constrained_design(control1):
    bids, _ = control1
    rows = prepare_rows(bids, 10, 100)
    firms = _firms(bids)
    fi, fj = firms[1], firms[4]
    XU, nu, _ = build_design(rows, "tag")
    XC, nc, _ = build_design(rows, "tag", constrain=(fi, fj))
    assert XC.shape == (600, 29)
    shared = ["ldist_shared", "cap_shared", "maxp_shared", "lmdist_shared"]
    assert all(s in nc for s in shared)
    for f in (fi, fj):
        for c in ("ldist", "cap", "maxp", "lmdist"):
            assert f"{c}_{f}" not in nc
    on_i = (rows["firm_id"] == fi).to_numpy()
    on_j = (rows["firm_id"] == fj).to_numpy()
    for c in ("ldist", "cap", "maxp", "lmdist"):
        sc = XC[:, nc.index(f"{c}_shared")]
        np.testing.assert_array_equal(sc[on_i], XU[on_i, nu.index(f"{c}_{fi}")])
        np.testing.assert_array_equal(sc[on_j], XU[on_j, nu.index(f"{c}_{fj}")])
        assert (sc[~(on_i | on_j)] == 0).all()
    for name in nc:
        if name not in shared:
            np.testing.assert_array_equal(XC[:, nc.index(name)], XU[:, nu.index(name)])
    # the shared block sits where firm i's block was
    assert nc.index("ldist_shared") == nu.index(f"ldist_{fi}")


def test_never_wins_firm(control1):
    bids, truth = control1
    seat0 = truth.loc[truth["seat"] == 0, "firm_id"].iloc[0]
    b = bids.copy()
    b.loc[b["firm_id"] == seat0, "bid"] = b.loc[b["firm_id"] == seat0, "bid"] * 3.0
    b = _reaward(b)
    assert b.loc[b["firm_id"] == seat0, "won"].sum() == 0
    rows = prepare_rows(b, 10, 100)
    XU, names, not_id = build_design(rows, "tag")
    others = [f for f in _firms(b) if f != seat0]
    assert (XU[:, names.index(f"cap_{seat0}")] == 0).all()
    for f in others:
        on = (rows["firm_id"] == f).to_numpy()
        assert (XU[on, names.index(f"maxp_{f}")] == 1.0).all()
    assert set(not_id) == {f"cap_{seat0}"} | {f"maxp_{f}" for f in others}
    rank_xu = np.linalg.matrix_rank(XU)
    out = detect_frames(b, "never_s1", specs=("tag",), usable_Ts=(100,), burn_in=10, N=6, T=110)
    assert len(out) == 15
    assert (out["rank_u"] == rank_xu).all()
    assert set(out["not_identified"].iloc[0].split(";")) == set(not_id)
    y = rows["y"].to_numpy()
    bu = np.linalg.lstsq(XU, y, rcond=None)[0]
    ssr_u = float(np.sum((y - XU @ bu) ** 2))
    assert rank_xu < 33
    for _, r in out.iterrows():
        XC, _, _ = build_design(rows, "tag", constrain=(r["firm_i"], r["firm_j"]))
        q = rank_xu - np.linalg.matrix_rank(XC)
        assert r["a3_df_num"] == q
        assert r["a3_df_num"] > 0 and np.isfinite(r["F_p"])
        bc = np.linalg.lstsq(XC, y, rcond=None)[0]
        ssr_c = float(np.sum((y - XC @ bc) ** 2))
        F = ((ssr_c - ssr_u) / q) / (ssr_u / (600 - rank_xu))  # denominator df from the rank, not m
        assert r["F"] == pytest.approx(F, rel=1e-8)
        assert r["F_p"] == pytest.approx(stats.f.sf(F, q, 600 - rank_xu), rel=1e-6)


# ------------------------------------------------------------------ missing data

def test_missing_bids_in_detector(control1):
    bids, _ = control1
    firm = _firms(bids)[3]
    b = bids.copy()
    drop = (b["firm_id"] == firm) & b["project_id"].isin(range(21, 33))
    b.loc[drop, "bid"] = np.nan
    rows = prepare_rows(b, 10, 100)
    assert len(rows) == 588
    full = prepare_rows(bids, 10, 100)
    # a rival with a missing bid still counts in the others' MDIST / MAXP
    keep = ["project_id", "firm_id", "mdist", "maxp"]
    merged = rows[keep].merge(full[keep], on=["project_id", "firm_id"], suffixes=("", "_full"))
    np.testing.assert_array_equal(merged["mdist"], merged["mdist_full"])
    np.testing.assert_array_equal(merged["maxp"], merged["maxp_full"])
    out = detect_frames(b, "control_s1", specs=("tag",), usable_Ts=(100,), burn_in=10, N=6, T=110)
    has = (out["firm_i"] == firm) | (out["firm_j"] == firm)
    assert (out.loc[has, "n_joint"] == 88).all() and has.sum() == 5
    assert (out.loc[~has, "n_joint"] == 100).all()
    assert out["z"].notna().all() and out["F"].notna().all()


def test_detector_firm_never_bids(control1):
    bids, _ = control1
    firm = _firms(bids)[2]
    b = bids.copy()
    b.loc[b["firm_id"] == firm, "bid"] = np.nan
    b.loc[b["firm_id"] == firm, "won"] = 0
    b = _reaward(b)
    out = detect_frames(b, "control_s1", specs=("tag", "project_fe"), usable_Ts=(100,), burn_in=10, N=6,
                        T=110)
    assert len(out) == 30
    for spec, g in out.groupby("spec"):
        has = (g["firm_i"] == firm) | (g["firm_j"] == firm)
        assert has.sum() == 5
        dead = g[has]
        assert (dead["n_joint"] == 0).all() and (dead["a1_reason"] == "insufficient_n").all()
        assert dead["r"].isna().all() and dead["z"].isna().all() and dead["a1_flag"].isna().all()
        assert dead["F"].isna().all() and dead["F_p"].isna().all() and dead["a3_flag"].isna().all()
        assert (dead["a3_df_num"] == 0).all()
        assert all("not_identified" in s.split(";") for s in dead["not_identified"])
        live = g[~has]
        assert len(live) == 10
        assert (live["n_joint"] == 100).all() and live["z"].notna().all() and live["F"].notna().all()
        nid = set(g["not_identified"].iloc[0].split(";"))
        assert {f"{c}_{firm}" for c in ("ldist", "cap", "maxp", "lmdist")} <= nid
    vals = empirical_null_values(out)
    for key in [("tag", "A1"), ("tag", "A3"), ("project_fe", "A1"), ("project_fe", "A3")]:
        assert len(vals[key]) == 10


def test_incomplete_run_detector(control1):
    bids, _ = control1
    short = bids[bids["project_id"] <= 60].reset_index(drop=True)
    assert usable_reached(short, 10) == 50
    out = detect_frames(short, "control_s1", specs=("tag", "project_fe"), usable_Ts=(30, 50, 70, 100),
                        burn_in=10, N=6, T=110)
    assert sorted(out["usable_T"].unique()) == [30, 50]
    assert len(out) == 15 * 2 * 2
    assert (out["complete"] == 0).all() and (out["primary"] == 0).all()


# ------------------------------------------------------------------ thresholds and output

def test_empirical_null_threshold():
    stat = np.arange(1, 21, dtype=float)
    df = pd.DataFrame({"spec": "tag", "primary": 1, "z": np.where(np.arange(20) % 2, stat, -stat), "F": stat})
    extra = pd.DataFrame({"spec": ["tag"] * 3, "primary": [0, 1, 1], "z": [500.0, np.nan, np.nan],
                          "F": [500.0, np.nan, np.nan]})
    th = empirical_thresholds(pd.concat([df, extra], ignore_index=True))
    assert th[("tag", "A1")] == pytest.approx(19.05)
    assert th[("tag", "A3")] == pytest.approx(19.05)
    assert 19.5 > th[("tag", "A1")] and not 18.9 > th[("tag", "A1")]

    with api_guard():
        frames = [detect_frames(simulate_table(s, COST, 110, None, None)[0], f"control_s{s}",
                                specs=("tag", "project_fe"), usable_Ts=(30, 100), burn_in=10, N=6, T=110)
                  for s in range(1, 7)]
    pooled = pd.concat(frames, ignore_index=True)
    vals = empirical_null_values(pooled)
    th = empirical_thresholds(pooled)
    for key in [("tag", "A1"), ("tag", "A3"), ("project_fe", "A1"), ("project_fe", "A3")]:
        assert len(vals[key]) == 90
        assert th[key] == pytest.approx(np.percentile(vals[key], 95))
        assert (np.diff(vals[key]) >= 0).all()
    prim = pooled[(pooled["primary"] == 1) & (pooled["spec"] == "tag")]
    np.testing.assert_allclose(vals[("tag", "A1")], np.sort(np.abs(prim["z"].to_numpy())))


def test_pair_stats_schema(control1, tmp_path):
    bids, _ = control1
    out = detect_frames(bids, "control_s1", specs=("tag", "project_fe"), usable_Ts=(30, 50, 70, 100),
                        burn_in=10, N=6, T=110)
    assert list(out.columns) == PAIR_STATS_COLUMNS
    assert len(out) == 120
    assert (out["firm_i"] < out["firm_j"]).all()
    assert out.groupby(["spec", "usable_T"]).size().eq(15).all()
    assert set(out.loc[out["spec"] == "tag", "rho0"]) == {0.0}
    assert np.allclose(out.loc[out["spec"] == "project_fe", "rho0"], -0.2)
    assert (out["primary"] == (out["usable_T"] == 100).astype(int)).all()
    assert (out["complete"] == 1).all() and (out["run_id"] == "control_s1").all()
    assert (out.loc[out["spec"] == "tag", "rank_u"] <= 33).all()
    assert (out.loc[out["spec"] == "project_fe", "rank_u"] <= 129).all()

    d = tmp_path / "run"
    d.mkdir()
    bids.to_csv(d / "bids.csv", index=False)
    df = detect_run(d, T=110, N=6, burn_in=10)
    text = (d / "detector" / "pair_stats.csv").read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines[0] == ",".join(PAIR_STATS_COLUMNS) and len(lines) == 121
    assert "nan" not in text.lower() and "<NA>" not in text and "None" not in text
    raw = pd.read_csv(d / "detector" / "pair_stats.csv", dtype=str, keep_default_na=False)
    for col in ("usable_T", "primary", "n_joint", "a1_flag", "a3_flag", "a3_df_num", "rank_u", "complete"):
        assert raw[col].str.fullmatch(r"-?\d*").all(), col  # ints, never "1.0"
    assert len(df) == 120


def test_cli(control1, tmp_path):
    bids, _ = control1
    d = tmp_path / "run"
    d.mkdir()
    bids.to_csv(d / "bids.csv", index=False)
    detector.main(["--run-dir", str(d), "--T", "110", "--N", "6", "--burn-in", "10"])
    raw = pd.read_csv(d / "detector" / "pair_stats.csv", dtype=str, keep_default_na=False)
    assert len(raw) == 120 and set(raw["complete"]) == {"1"}
    detector.main(["--run-dir", str(d), "--T", "120"])
    raw = pd.read_csv(d / "detector" / "pair_stats.csv", dtype=str, keep_default_na=False)
    assert set(raw["complete"]) == {"0"} and set(raw["primary"]) == {"0"}


def test_power_vs_T_output():
    with api_guard():
        bids, _ = simulate_table(1, COST, 110, "cover", (0, 3))
    out = detect_frames(bids, "cover_s1", specs=("tag", "project_fe"), usable_Ts=(30, 50, 70, 100),
                        burn_in=10, N=6, T=110)
    assert sorted(out["usable_T"].unique()) == [30, 50, 70, 100]
    for k in (30, 50, 70):
        short = bids[bids["project_id"] <= 10 + k]
        ref = detect_frames(short, "cover_s1", specs=("tag", "project_fe"), usable_Ts=(k,), burn_in=10,
                            N=6, T=110)
        got = out[out["usable_T"] == k].reset_index(drop=True)
        pd.testing.assert_frame_equal(got[STAT_COLS], ref[STAT_COLS])
        assert (got["n_joint"] == k).all()


def test_detector_module_is_regulator_view():
    src = Path(detector.__file__).read_text(encoding="utf-8")
    for banned in ("truth", "messages", "run_meta", "transcripts", "calls.csv"):
        assert banned not in src, banned


def test_detector_reads_only_bids(tmp_path, monkeypatch):
    d = run(RunConfig(condition="cover", seed=1, T=40), synthetic_bidder_factory, results_root=tmp_path)
    opened = []
    real_builtin, real_io = builtins.open, _pyio.open
    real_path_open = Path.open

    def rec_builtin(file, mode="r", *a, **k):
        opened.append((str(file), mode))
        return real_builtin(file, mode, *a, **k)

    def rec_io(file, mode="r", *a, **k):
        opened.append((str(file), mode))
        return real_io(file, mode, *a, **k)

    def rec_path(self, mode="r", *a, **k):
        opened.append((str(self), mode))
        return real_path_open(self, mode, *a, **k)

    with monkeypatch.context() as mp:
        mp.setattr(builtins, "open", rec_builtin)
        mp.setattr(_pyio, "open", rec_io)
        mp.setattr(Path, "open", rec_path)
        detect_run(d, T=40, N=6, burn_in=10)
    assert opened
    reads = {Path(p).resolve() for p, m in opened if not any(c in m for c in "wax+")}
    writes = {Path(p).resolve() for p, m in opened if any(c in m for c in "wax+")}
    assert reads == {(d / "bids.csv").resolve()}
    det = (d / "detector").resolve()
    assert writes and all(det in w.parents for w in writes)
    first = (d / "detector" / "pair_stats.csv").read_bytes()
    assert len(first.decode("utf-8").splitlines()) == 1 + 15 * 2  # usable 30, both specs

    for name in ("truth.csv", "messages.csv", "calls.csv", "run_meta.json", "heartbeat.json",
                 "checkpoint.json"):
        (d / name).unlink()
    shutil.rmtree(d / "transcripts", ignore_errors=True)
    shutil.rmtree(d / "detector")
    assert sorted(p.name for p in d.iterdir()) == ["bids.csv"]
    detect_run(d, T=40, N=6, burn_in=10)
    assert (d / "detector" / "pair_stats.csv").read_bytes() == first
    tbl = load_bid_table(d)
    assert len(tbl) == 240


def test_cli_never_writes_into_unfinished_run(tmp_path, capsys):
    """B1: on a run dir whose run_meta has finished_utc null the CLI computes in memory, writes nothing."""
    import json as _json
    d = run(RunConfig(condition="control", seed=1, T=40), synthetic_bidder_factory, results_root=tmp_path)
    meta = _json.loads((d / "run_meta.json").read_text(encoding="utf-8"))
    meta["finished_utc"] = None
    (d / "run_meta.json").write_text(_json.dumps(meta), encoding="utf-8")
    detector.main(["--run-dir", str(d), "--T", "40"])
    assert not (d / "detector").exists()
    assert "nothing written" in capsys.readouterr().out
    meta["finished_utc"] = "2026-10-25T12:00:00+00:00"
    (d / "run_meta.json").write_text(_json.dumps(meta), encoding="utf-8")
    detector.main(["--run-dir", str(d), "--T", "40"])
    assert (d / "detector" / "pair_stats.csv").is_file()
