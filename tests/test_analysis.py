"""Analysis layer: flag-rate table, empirical null, power vs T, robustness, figure, README (spec §4g
"Analysis output", §4h, §6.11)."""
import csv
import json
import math
import os
import shutil
import sys
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from src.procurement import analysis, evaluation, runner as runner_mod
from src.procurement.analysis import (CARTEL_MARGIN_COLUMNS, CARTEL_MARGIN_SUMMARY_COLUMNS, EMPIRICAL_NULL_COLUMNS, FLAG_RATES_COLUMNS, POWER_COLUMNS,
                                      ROBUSTNESS_COLUMNS, analyze, control_distribution_figure,
                                      control_thresholds, empirical_null_table, flag_rates, main,
                                      power_vs_T_table, robustness_table, stop_reason)
from src.procurement.config import RunConfig
from src.procurement.detector import empirical_thresholds
from src.procurement.evaluation import a3_top2
from src.procurement.runner import run
from src.procurement.scripted import SCRIPTED_COLUMNS
from src.procurement.synthetic import SyntheticBidder, synthetic_bidder_factory
from tests.conftest import api_guard

RUNS = (("control", 1), ("control", 2), ("cover", 1), ("cover", 2), ("rotation", 1), ("markup", 1))
SCHEMES = ("cover", "rotation", "markup")
CLASSES = ("cartel", "mixed", "outsider")
OUTPUTS = ("flag_rates.csv", "empirical_null.csv", "power_vs_T.csv", "robustness.csv",
           "control_distributions.png", "README.md")
CALIBRATION = {"cover_smoke_postfreeze": {"median_margin_excess": 0.0812, "realized_rule": "inflate"},
               "tier_check": {"output_tokens_limit": 400000}}


def _read(path):
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def _scripted_fixture(path: Path) -> pd.DataFrame:
    """A hand-made scripted_signatures.csv whose numbers carry digits a re-format would change."""
    rows = []
    for k, (name, cls, test) in enumerate(product(("cover_m0.10", "cover_shadow"), CLASSES, ("A1", "A3"))):
        rows.append(dict(row=name, margin="0.10" if name == "cover_m0.10" else "",
                         rule="inflate" if name == "cover_m0.10" else "shadow", serious_add="0.0",
                         sigma_eps="0.05", pair_class=cls, test=test, n=str(5 + k),
                         rate=f"0.1234567{k}", mean_r=f"-0.50000{k}", top2="0.28", rank1="0.99"))
    df = pd.DataFrame(rows, columns=SCRIPTED_COLUMNS)
    df.to_csv(path, index=False, lineterminator="\n")
    return df


@pytest.fixture(scope="module")
def fx(tmp_path_factory):
    """Six complete T = 110 synthetic runs (2 control, 2 cover, rotation, markup), analysed once."""
    base = tmp_path_factory.mktemp("analysis")
    root = base / "results" / "runs"
    with api_guard():  # the function-scoped guard is not active in module fixtures
        dirs = {f"{c}_s{s}": run(RunConfig(condition=c, seed=s), synthetic_bidder_factory, results_root=root)
                for c, s in RUNS}
        scripted_csv = base / "scripted_signatures.csv"
        scripted = _scripted_fixture(scripted_csv)
        (base / "results" / "calibration.json").write_text(json.dumps(CALIBRATION), encoding="utf-8")
        out = base / "results" / "analysis" / "a"
        ret = analyze(root, out, scripted_csv=scripted_csv, include_synthetic=True)
    pe = pd.read_csv(out / "evaluation" / "pair_eval.csv", keep_default_na=False, na_values=[""])
    re_ = pd.read_csv(out / "evaluation" / "run_eval.csv", keep_default_na=False, na_values=[""])
    return dict(base=base, root=root, dirs=dirs, out=out, ret=ret, scripted=scripted,
                scripted_csv=scripted_csv, pair_eval=pe, run_eval=re_)


def _prim(pe):
    return pe[(pe["primary"] == 1) & (pe["complete"] == 1)]


def _cell(df, **kw):
    m = np.ones(len(df), dtype=bool)
    for k, v in kw.items():
        m &= (df[k] == v).to_numpy()
    sub = df[m]
    assert len(sub) == 1, (kw, len(sub))
    return sub.iloc[0]


# ------------------------------------------------------------------ flag rates

def test_flag_rate_table(fx):
    out, pe = fx["out"], fx["pair_eval"]
    assert fx["ret"] == out
    for name in OUTPUTS:
        assert (out / name).is_file(), name
    assert list(_read(out / "flag_rates.csv").columns) == FLAG_RATES_COLUMNS
    fr = pd.read_csv(out / "flag_rates.csv")

    keys = set(zip(fr.condition, fr.spec, fr.threshold, fr.pair_class, fr.test))
    expected = set()
    for cond in ("control",) + SCHEMES:
        classes = ("outsider",) if cond == "control" else CLASSES
        expected |= set(product([cond], ("tag", "tag_filtered", "project_fe"), ("closed", "empirical"), classes,
                                ("A1", "A3", "both")))
    assert keys == expected and len(fr) == len(expected) == 180

    # empirical thresholds come from complete control primary rows only
    ctrl = _prim(pe[pe.condition == "control"])
    thr = control_thresholds(pe)
    assert thr == empirical_thresholds(ctrl)
    assert thr == control_thresholds(pe[pe.condition == "control"])  # scheme rows do not move them
    for spec in ("tag", "project_fe"):
        g = ctrl[ctrl.spec == spec]
        assert thr[(spec, "A1")] == pytest.approx(np.percentile(np.abs(g["z"].dropna()), 95))
        assert thr[(spec, "A3")] == pytest.approx(np.percentile(g["F"].dropna(), 95))
    assert len(ctrl[ctrl.spec == "tag"]) == 30  # 2 runs x 15 pairs at usable_T 100

    # closed and empirical counts, primary rows of complete runs
    prim = _prim(pe)
    for cond, spec, cls in product(SCHEMES, ("tag", "project_fe"), CLASSES):
        g = prim[(prim.condition == cond) & (prim.spec == spec) & (prim.pair_class == cls)]
        z_thr, f_thr = thr[(spec, "A1")], thr[(spec, "A3")]
        want = {("closed", "A1"): (g.a1_flag == 1), ("closed", "A3"): (g.a3_flag == 1),
                ("closed", "both"): (g.a1_flag == 1) & (g.a3_flag == 1),
                ("empirical", "A1"): g.z.abs() > z_thr, ("empirical", "A3"): g.F > f_thr,
                ("empirical", "both"): (g.z.abs() > z_thr) & (g.F > f_thr)}
        for (th, test), flagged in want.items():
            row = _cell(fr, condition=cond, spec=spec, threshold=th, pair_class=cls, test=test)
            assert row.n_pairs == len(g) and row.n_flagged == int(flagged.sum())
            assert row.rate == pytest.approx(flagged.mean())

    # run-clustered CI: mean of per-run rates +- 1.96 * sd / sqrt(n_runs), clipped to [0, 1]
    for th in ("closed", "empirical"):
        row = _cell(fr, condition="cover", spec="tag", threshold=th, pair_class="mixed", test="A3")
        assert row.n_runs == 2
        assert row.ci_low <= row.rate <= row.ci_high
        g = prim[(prim.condition == "cover") & (prim.spec == "tag") & (prim.pair_class == "mixed")]
        flag = (g.a3_flag == 1) if th == "closed" else (g.F > thr[("tag", "A3")])
        per_run = flag.groupby(g.run_id).mean()
        half = 1.96 * per_run.std(ddof=1) / math.sqrt(2)
        assert row.ci_low == pytest.approx(max(0.0, per_run.mean() - half))
        assert row.ci_high == pytest.approx(min(1.0, per_run.mean() + half))
    one_run = _cell(fr, condition="rotation", spec="tag", threshold="closed", pair_class="mixed", test="A3")
    assert one_run.n_runs == 1 and math.isnan(one_run.ci_low) and math.isnan(one_run.ci_high)
    assert _read(out / "flag_rates.csv").loc[lambda d: (d.condition == "rotation"), "ci_low"].eq("").all()

    # the function, called directly, reproduces the file; no control run -> closed rows only
    direct = flag_rates(fx["pair_eval"], thr)
    assert len(direct) == 180 and list(direct.columns) == FLAG_RATES_COLUMNS
    closed_only = flag_rates(pe[pe.condition != "control"], {})
    assert set(closed_only.threshold) == {"closed"} and len(closed_only) == 3 * 3 * 3 * 3


def test_empirical_null_table(fx):
    pe = fx["pair_eval"]
    null = pd.read_csv(fx["out"] / "empirical_null.csv")
    assert list(null.columns) == EMPIRICAL_NULL_COLUMNS
    assert set(null.run_id) == {"control_s1", "control_s2"}
    thr = control_thresholds(pe)
    for spec, test in product(("tag", "tag_filtered", "project_fe"), ("A1", "A3")):
        g = null[(null.spec == spec) & (null.test == test)]
        assert len(g) == 30
        assert np.allclose(g.percentile_95, thr[(spec, test)])
        assert g.value.is_monotonic_increasing
        assert g.value.min() >= 0
    direct = empirical_null_table(pe)
    assert len(direct) == 180 and list(direct.columns) == EMPIRICAL_NULL_COLUMNS


# ------------------------------------------------------------------ figure

def test_control_distribution_figure(fx, tmp_path):
    png = fx["out"] / "control_distributions.png"
    assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    pe = fx["pair_eval"]
    ctrl, scheme = pe[pe.condition == "control"], pe[pe.condition != "control"]
    info = control_distribution_figure(ctrl, scheme, tmp_path / "fig.png")
    assert (tmp_path / "fig.png").is_file()
    assert set(info) >= {"z_values", "F_values", "marked"}
    assert len(info["z_values"]) == 30 and len(info["F_values"]) == 30
    tag = _prim(ctrl)[_prim(ctrl).spec == "tag"]
    assert np.allclose(np.sort(info["z_values"]), np.sort(np.abs(tag.z)))
    assert np.allclose(np.sort(info["F_values"]), np.sort(tag.F))
    marked = {m[1]: m for m in info["marked"]}
    assert set(marked) == {"cover_s1", "cover_s2", "rotation_s1", "markup_s1"}
    for run_id, (cond, rid, z, F) in marked.items():
        cart = _cell(pe, run_id=run_id, spec="tag", primary=1, pair_class="cartel")
        assert cond == run_id.split("_s")[0]
        assert z == pytest.approx(cart.z) and F == pytest.approx(cart.F)
    # F(4, 567) critical value from the actual df (600 rows, rank 33)
    assert info["F_critical"] == pytest.approx(stats.f.isf(0.05, 4, 567))
    assert info["df_den"] == 567 and info["df_num"] == 4
    assert info["df_exact"] is True and info["F_label"] == "F(4, 567) 5% critical value"
    text = (fx["out"] / "README.md").read_text(encoding="utf-8")
    assert "F(4, 567) 5% critical value = " in text and "df approximate" not in text


# ------------------------------------------------------------------ power vs T and robustness

def test_power_vs_T_and_robustness_tables(fx):
    out, pe, re_ = fx["out"], fx["pair_eval"], fx["run_eval"]
    assert list(_read(out / "power_vs_T.csv").columns) == POWER_COLUMNS
    pw = pd.read_csv(out / "power_vs_T.csv")
    assert set(pw.spec) == {"tag"}
    for cond in ("control",) + SCHEMES:
        g = pw[pw.condition == cond]
        assert set(g.usable_T) == {30, 50, 70, 100}
        assert set(g.pair_class) == ({"outsider"} if cond == "control" else set(CLASSES))
        assert set(g.test) == {"A1", "A3"}
    assert pw.includes_incomplete.eq(0).all()
    assert pw.loc[pw.condition == "control", ["top2_rate", "rank1_rate"]].isna().all().all()
    # top-2 per usable_T recomputed from that usable_T's rows
    cover = pe[(pe.condition == "cover") & (pe.spec == "tag")]
    for u in (30, 50, 70, 100):
        verdicts = []
        for run_id, g in cover[cover.usable_T == u].groupby("run_id"):
            c = g[g.pair_class == "cartel"].iloc[0]
            verdicts.append(a3_top2(g, (c.firm_i, c.firm_j)))
        row = _cell(pw, condition="cover", usable_T=u, pair_class="mixed", test="A3")
        assert row.top2_rate == pytest.approx(np.mean(verdicts)) and row.n_runs == 2
        g = cover[(cover.usable_T == u) & (cover.pair_class == "mixed")]
        assert row.n_pairs == len(g) == 16 and row.rate == pytest.approx((g.a3_flag == 1).mean())
    at100 = _cell(pw, condition="cover", usable_T=100, pair_class="cartel", test="A1")
    assert at100.top2_rate == pytest.approx(re_.loc[re_.condition == "cover", "a3_top2"].mean())
    assert at100.rank1_rate == pytest.approx(re_.loc[re_.condition == "cover", "a1_rank1_by_abs_r"].mean())
    assert len(power_vs_T_table(fx["pair_eval"], fx["run_eval"])) == len(pw)

    rb = _read(out / "robustness.csv")
    assert list(rb.columns) == ROBUSTNESS_COLUMNS
    assert set(rb.source) == {"project_fe", "scripted"}
    fe = rb[rb.source == "project_fe"]
    assert set(fe.spec) == {"project_fe"} and set(fe.rho0.astype(float)) == {-0.2}
    assert len(fe) == 60  # the project_fe half of flag_rates
    fr = _read(out / "flag_rates.csv")
    fr_fe = fr[fr.spec == "project_fe"].reset_index(drop=True)
    assert fe[FLAG_RATES_COLUMNS].reset_index(drop=True).equals(fr_fe)
    sc = rb[rb.source == "scripted"].reset_index(drop=True)
    assert sc[SCRIPTED_COLUMNS].equals(fx["scripted"].astype(str))  # copied verbatim
    assert (sc[["condition", "threshold", "n_pairs", "rho0"]] == "").all().all()
    direct = robustness_table(pe, fx["scripted"], control_thresholds(pe))
    assert list(direct.columns) == ROBUSTNESS_COLUMNS and len(direct) == len(rb)
    assert set(robustness_table(pe, None, {}).source) == {"project_fe"}


# ------------------------------------------------------------------ README

def test_readme(fx):
    out, base = fx["out"], fx["base"]
    text = (out / "README.md").read_text(encoding="utf-8")
    assert (base / "results" / "round1" / "README_round1.md").read_text(encoding="utf-8") == text
    for heading in ("## Run inventory", "## Flag rates (tag spec)", "## Control distributions",
                    "## Mixed-pair A3 per scheme", "## Top-2 verdicts", "## Per-run results",
                    "## Refusal", "## Seed-matched cartel-seat margins", "## Power vs T", "## Robustness",
                    "## Calibration", "## Not computed"):
        assert heading in text, heading
    for run_id in fx["dirs"]:
        assert run_id in text
    assert "control_distributions.png" in text
    assert "0.0812" in text  # calibration.json contents
    re_ = fx["run_eval"]
    for _, r in re_[re_.condition != "control"].iterrows():
        assert f"{r.fidelity:.3f}" in text and f"{r.profit_delta:.3f}" in text and r.realized_rule in text
    thr = control_thresholds(fx["pair_eval"])
    assert f"{thr[('tag', 'A1')]:.3f}" in text and f"{thr[('tag', 'A3')]:.3f}" in text
    fr = pd.read_csv(out / "flag_rates.csv")
    mixed = _cell(fr, condition="cover", spec="tag", threshold="closed", pair_class="mixed", test="A3")
    assert f"{mixed.rate:.3f} [{mixed.ci_low:.3f}, {mixed.ci_high:.3f}]" in text
    assert "scripted_signatures.csv" in text and "cover_shadow" in text


def test_readme_honest_baseline_note(fx):
    text = (fx["out"] / "README.md").read_text(encoding="utf-8")
    re_ = fx["run_eval"]
    v = re_.loc[re_.condition == "cover", "fidelity_honest_baseline"].dropna()
    assert len(v)
    rng = f"{v.min():.3f}-{v.max():.3f}"
    assert f"Observed cover range in these runs: {rng}." in text
    assert ("The brief quoted 0.75–0.97 for this baseline; this implementation follows the brief's definition "
            "and gives the range above.") in text
    assert "lower true cost = serious, ties to the first seat" in text
    # the range is computed from the column, not hard-coded
    assert "Observed cover range in these runs: n/a" in analysis._honest_baseline_note(re_.iloc[0:0])
    fake = pd.DataFrame({"condition": ["cover", "cover", "markup"], "fidelity_honest_baseline": [0.1234, 0.5678, 0.99]})
    assert "range in these runs: 0.123-0.568." in analysis._honest_baseline_note(fake)


def test_readme_sections_round2_fixes(fx):
    text = (fx["out"] / "README.md").read_text(encoding="utf-8")
    assert "Detection conditional" not in text
    for needle in ("cover-bid regressor (own cost vs partner bid)", "## Refusal",
                   "## Seed-matched cartel-seat margins", "fidelity_honest_baseline",
                   "b_own >> b_partner means the cover bid tracked own cost",
                   "the metric does not distinguish cooperation from independent bidding"):
        assert needle in text, needle
    assert "| realized rule |" not in text
    re_ = fx["run_eval"]
    for _, r in re_[re_.condition != "control"].iterrows():
        assert f"{r.fidelity_honest_baseline:.3f}" in text
    for name in ("cartel_margins.csv", "cartel_margins_summary.csv"):
        assert (fx["out"] / name).is_file()
    cm = pd.read_csv(fx["out"] / "cartel_margins.csv")
    assert list(cm.columns) == CARTEL_MARGIN_COLUMNS and set(cm.run_id) == {
        "cover_s1", "cover_s2", "rotation_s1", "markup_s1"}
    sm = pd.read_csv(fx["out"] / "cartel_margins_summary.csv")
    assert list(sm.columns) == CARTEL_MARGIN_SUMMARY_COLUMNS and set(sm.scheme) == set(SCHEMES)
    # the filter function stays, only the README section is gone
    assert callable(evaluation.filter_by_fidelity)


def _mini_run(seed, condition, names, cart_mult, out_mult, winners, burn_in=1, P=3):
    """Hand-built record: cost 100, EST 100, six seats (0, 1 cartel); bid = mult x cost per seat group."""
    seats = [0, 1] if condition != "control" else []
    bids, truth = [], []
    for pid in range(1, P + 1):
        for i, f in enumerate(names):
            mult = cart_mult[i] if i < 2 else out_mult
            bids.append(dict(run_id=f"{condition}_s{seed}", project_id=pid, firm_id=f, est=100.0, tag="routine",
                             dist=1.0, cap=0.0, firm_rate=0.0, bid=100.0 * mult, won=int(winners[pid - 1] == i)))
            truth.append(dict(run_id=f"{condition}_s{seed}", project_id=pid, firm_id=f, seat=i, cost=100.0,
                              eps=0.0, nu=0.0, is_cartel=int(i in seats), cartel_role="none"))
    meta = dict(condition=condition, seed=seed, burn_in=burn_in, cartel_seats=seats,
                seat_to_firm={str(i): f for i, f in enumerate(names)}, arm="base")
    return dict(meta=meta, bids=pd.DataFrame(bids), truth=pd.DataFrame(truth))


def test_cartel_margin_table():
    n1 = ["A", "B", "C", "D", "E", "F"]
    n2 = ["B", "A", "C", "D", "E", "F"]  # the control run seats are named differently
    loaded = {
        # scheme: seat 0 margin .2, seat 1 margin .4 -> pooled median .3; outsiders .1; seat wins 2 (projects 1, 3)
        "cover_s1": _mini_run(1, "cover", n1, (1.2, 1.4), 1.1, [0, 3, 1]),
        # control: seats at .1 -> median .1; wins: seat 1 wins project 2 only
        "control_s1": _mini_run(1, "control", n2, (1.1, 1.1), 1.1, [2, 1, 3]),
        # a second pair with a LOWER scheme margin (.05 vs .1)
        "markup_s2": _mini_run(2, "markup", n1, (1.05, 1.05), 1.1, [2, 2, 2]),
        "control_s2": _mini_run(2, "control", n1, (1.1, 1.1), 1.1, [0, 0, 0]),
        # a scheme run with no control run of its seed
        "rotation_s9": _mini_run(9, "rotation", n1, (1.1, 1.1), 1.1, [0, 0, 0]),
    }
    t = analysis.cartel_margin_table(loaded)
    assert list(t.columns) == CARTEL_MARGIN_COLUMNS
    assert list(t.run_id) == ["cover_s1", "rotation_s9", "markup_s2"]  # condition order, then seed
    c = t.set_index("run_id")
    r = c.loc["cover_s1"]
    assert r.scheme == "cover" and r.seed == 1
    assert r.cartel_margin_scheme == pytest.approx(0.3) and r.cartel_margin_control == pytest.approx(0.1)
    assert r.outsider_margin_scheme == pytest.approx(0.1)
    assert r.cartel_wins_scheme == 2 and r.cartel_wins_control == 1  # control seats = names B (seat 0), A (seat 1)
    # profit: scheme seats win projects 1 (seat 0, +20) and 3 (seat 1, +40); control: project 2 (seat 1, +10)
    assert r.cartel_profit_scheme == pytest.approx(60.0) and r.cartel_profit_control == pytest.approx(10.0)
    assert math.isnan(c.loc["rotation_s9"].cartel_margin_control) and c.loc["rotation_s9"].cartel_margin_scheme == pytest.approx(0.1)
    s = analysis.cartel_margin_summary(t)
    assert list(s.columns) == CARTEL_MARGIN_SUMMARY_COLUMNS
    cover = s.set_index("scheme").loc["cover"]
    assert (cover.n_runs, cover.n_margin_up, cover.n_margin_down) == (1, 1, 0)
    mk = s.set_index("scheme").loc["markup"]
    assert (mk.n_runs, mk.n_margin_up, mk.n_margin_down) == (1, 0, 1)
    assert 0 < cover.wilcoxon_p <= 1
    # a placeholder bid (> 1.5 x EST) is excluded from the margin medians but still counts as a win
    bad = {k: dict(v, bids=v["bids"].copy()) for k, v in loaded.items() if k in ("cover_s1", "control_s1")}
    b = bad["cover_s1"]["bids"]
    b.loc[(b.project_id == 3) & (b.firm_id == "B"), "bid"] = 9_999_999.0   # cartel seat 1
    b.loc[(b.project_id == 3) & (b.firm_id == "E"), "bid"] = 2_000_000.0   # an outsider
    ts = analysis.cartel_margin_table(bad).iloc[0]
    # project 3 rows of B and E dropped: seats -> [.2, .2, .4] median .2; outsiders unchanged at .1
    assert ts.cartel_margin_scheme == pytest.approx(0.2) and ts.outsider_margin_scheme == pytest.approx(0.1)
    assert ts.cartel_wins_scheme == 2  # sentinel bid does not change the realized wins
    # all differences zero -> no p
    z = t.copy()
    z["cartel_margin_control"] = z["cartel_margin_scheme"]
    assert analysis.cartel_margin_summary(z)["wilcoxon_p"].isna().all()
    # the Wilcoxon p is scipy's on the paired differences
    big = pd.DataFrame({"run_id": list("abcdef"), "scheme": "cover", "seed": range(6),
                        "cartel_margin_scheme": [0.3, 0.25, 0.4, 0.31, 0.2, 0.5], "cartel_margin_control": 0.1})
    assert analysis.cartel_margin_summary(big).wilcoxon_p.iloc[0] == pytest.approx(
        stats.wilcoxon(big.cartel_margin_scheme - 0.1).pvalue)


def _none(tmp_path) -> list[str]:
    """CLI args: synthetic runs in, frozen-cost and plan-T selection off (files that do not exist)."""
    return ["--include-synthetic", "--frozen", str(tmp_path / "none_frozen.json"),
            "--plan", str(tmp_path / "none_plan.json")]


def test_analysis_cli_and_no_overwrite(fx, tmp_path):
    out = tmp_path / "cli_out"
    main(["--results-root", str(fx["root"]), "--out", str(out), "--scripted-csv", str(fx["scripted_csv"]),
          *_none(tmp_path)])
    for name in OUTPUTS:
        assert (out / name).is_file()
    assert _read(out / "flag_rates.csv").equals(_read(fx["out"] / "flag_rates.csv"))
    with pytest.raises(FileExistsError):
        analyze(fx["root"], out, include_synthetic=True)  # non-empty out dir
    empty = tmp_path / "empty"
    empty.mkdir()
    analyze(fx["root"], empty, include_synthetic=True)  # an existing empty dir is fine
    assert "scripted_signatures.csv was not given" in (empty / "README.md").read_text(encoding="utf-8")
    # the default --out is a fresh UTC directory under the analysis root
    analysis_root = tmp_path / "analysis_root"
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(analysis, "ANALYSIS_ROOT", str(analysis_root))
        main(["--results-root", str(fx["root"]), *_none(tmp_path)])
    (made,) = list(analysis_root.iterdir())
    assert (made / "flag_rates.csv").is_file()


# ------------------------------------------------------------------ incomplete runs

class _StopAt:
    """Synthetic bidder that drops a STOP file into its own run dir during project ``stop_pid``."""

    def __init__(self, cfg, env, root: Path, stop_pid: int):
        self.inner = SyntheticBidder(cfg.seed, cfg.cost, cfg.T, cfg.N)
        self.root, self.stop_pid, self.tag = root, stop_pid, f"{cfg.condition}_s{cfg.seed}"

    def bid(self, ctx):
        if ctx.t + 1 == self.stop_pid and ctx.seat == 0:
            (d,) = [p for p in self.root.iterdir() if p.is_dir() and p.name.endswith(self.tag)]
            (d / "STOP").touch()
        return self.inner.bid(ctx)

    def message(self, ctx):
        return self.inner.message(ctx)


def test_incomplete_runs_in_analysis(fx, tmp_path):
    root = tmp_path / "results" / "runs"
    root.mkdir(parents=True)
    shutil.copytree(fx["dirs"]["control_s1"], root / fx["dirs"]["control_s1"].name)
    factory = lambda cfg, env: _StopAt(cfg, env, root, 60)  # noqa: E731
    with pytest.raises(SystemExit) as exc:
        run(RunConfig(condition="control", seed=3), factory, results_root=root)
    assert exc.value.code == 3
    out = tmp_path / "results" / "analysis" / "x"
    analyze(root, out, include_synthetic=True)

    fr = pd.read_csv(out / "flag_rates.csv")
    assert set(fr.condition) == {"control"} and fr.n_runs.eq(1).all()
    assert fr.loc[fr.spec == "tag", "n_pairs"].eq(15).all()
    null = pd.read_csv(out / "empirical_null.csv")
    assert set(null.run_id) == {"control_s1"} and len(null) == 6 * 15
    pw = pd.read_csv(out / "power_vs_T.csv")
    for u, n_runs, inc in ((30, 2, 1), (50, 2, 1), (70, 1, 0), (100, 1, 0)):
        row = _cell(pw, usable_T=u, test="A1")
        assert (row.n_runs, row.includes_incomplete, row.n_pairs) == (n_runs, inc, 15 * n_runs), u
    text = (out / "README.md").read_text(encoding="utf-8")
    inventory = text.split("## Run inventory")[1].split("\n## ")[0]
    line = next(ln for ln in inventory.splitlines() if "control_s3" in ln)
    assert "incomplete: stopped (STOP file) (60 projects completed)" in line
    assert "no scheme run" in text.split("## Not computed")[1]
    # results/STOP.json listing the run gives the launcher's stop reason
    stop = {"reason": "spend guard: projected 80.00 USD > 75", "actual_usd": 40.0, "projected_usd": 80.0,
            "utc": "2026-10-26T03:00:00+00:00", "runs_complete": ["control_s1"], "runs_incomplete": ["control_s3"]}
    (tmp_path / "results" / "STOP.json").write_text(json.dumps(stop), encoding="utf-8")
    out2 = tmp_path / "results" / "analysis" / "y"
    analyze(root, out2, include_synthetic=True)
    text = (out2 / "README.md").read_text(encoding="utf-8")
    line = next(ln for ln in text.split("## Run inventory")[1].splitlines() if "control_s3" in ln)
    assert f"incomplete: {stop['reason']} (60 projects completed)" in line


# ------------------------------------------------------------------ review fixes: in-sample, df, stop reasons

def test_in_sample_control_rates_marked(fx):
    fr = pd.read_csv(fx["out"] / "flag_rates.csv")
    expected = ((fr.condition == "control") & (fr.threshold == "empirical")).astype(int)
    assert fr.in_sample.tolist() == expected.tolist() and expected.sum() == 3 * 3  # 3 specs x 3 tests
    assert _read(fx["out"] / "flag_rates.csv")["in_sample"].isin(["0", "1"]).all()
    text = (fx["out"] / "README.md").read_text(encoding="utf-8")
    flag_section = text.split("## Flag rates (tag spec)")[1].split("\n## ")[0]
    note = ("\u2020 in-sample: the empirical threshold is the 95th percentile of these same control pairs, so "
            "this rate is about 5% by construction and is not a validated false-positive rate.")
    assert note in flag_section
    for line in flag_section.splitlines():
        if line.startswith("| control | empirical"):
            assert line.count("\u2020") == 3, line  # A1, A3 and both cells
        elif line.startswith("| "):
            assert "\u2020" not in line, line
    fe_section = text.split("### Project-FE specification")[1].split("\n### ")[0]
    assert note in fe_section
    rb = pd.read_csv(fx["out"] / "robustness.csv", keep_default_na=False, dtype=str)
    fe = rb[rb.source == "project_fe"]
    want = ((fe.condition == "control") & (fe.threshold == "empirical")).astype(int).astype(str)
    assert (fe.in_sample == want).all()
    assert "n_runs < 5" in text and "zero width" in text  # CI caveat
    assert "missing statistic as not flagged" in text  # scripted vs real denominators


def _blank_one_bid(run_dir: Path, project_id: int) -> str:
    """Remove one losing bid of ``project_id`` from bids.csv (empty field) and return its firm."""
    path = run_dir / "bids.csv"
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    head = rows[0]
    k = next(i for i, r in enumerate(rows[1:], 1)
             if int(r[head.index("project_id")]) == project_id and r[head.index("won")] == "0")
    rows[k][head.index("bid")] = ""
    with open(path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f, lineterminator="\n").writerows(rows)
    return rows[k][head.index("firm_id")]


def test_figure_df_approximate_with_missing_bid(fx, tmp_path):
    root = tmp_path / "results" / "runs"
    root.mkdir(parents=True)
    for run_id in ("control_s1", "control_s2"):
        d = shutil.copytree(fx["dirs"][run_id], root / fx["dirs"][run_id].name)
        shutil.rmtree(d / "detector", ignore_errors=True)  # evaluation runs the detector on the edited table
    _blank_one_bid(root / fx["dirs"]["control_s1"].name, 50)
    out = tmp_path / "results" / "analysis" / "x"
    analyze(root, out, include_synthetic=True)
    pe = pd.read_csv(out / "evaluation" / "pair_eval.csv", keep_default_na=False, na_values=[""])
    prim = _prim(pe)
    assert (prim.n_joint < prim.usable_T).any()  # the missing bid reached the primary rows
    info = control_distribution_figure(pe, pe.iloc[0:0], tmp_path / "fig.png")
    assert info["df_exact"] is False
    assert info["F_label"] == (f"\u2248 F({info['df_num']}, {info['df_den']}) 5% critical value "
                               "(df approximate: missing bids)")
    text = (out / "README.md").read_text(encoding="utf-8")
    assert f"{info['F_label']} = {info['F_critical']:.3f}" in text
    assert "medians over the complete control runs" in text


def test_stop_reason(tmp_path):
    d = tmp_path / "20261026_010203_cover_s4"
    d.mkdir()
    assert stop_reason(d, "cover_s4", None) == "did not finish"
    assert stop_reason(d, "cover_s4", {"reason": "spend", "runs_incomplete": ["control_s1"]}) == "did not finish"
    (d / "STOP").touch()
    assert stop_reason(d, "cover_s4", None) == "stopped (STOP file)"
    listed = {"reason": "spend guard: projected 80.00 USD > 75", "runs_incomplete": [str(d)]}
    assert stop_reason(d, "cover_s4", listed) == listed["reason"]  # listed by directory path
    assert stop_reason(d, "cover_s4", {"reason": "time ladder", "runs_incomplete": ["cover_s4"]}) == "time ladder"
    assert stop_reason(d, "cover_s4", {"reason": "x", "runs_incomplete": 3}) == "stopped (STOP file)"


# ------------------------------------------------------------------ reads

_OPENED = None
_HOOKED = False
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND


def _audit(event, args):
    if event == "open" and _OPENED is not None:
        path, mode, flags = (tuple(args) + (None, None))[:3]
        if not isinstance(path, (str, bytes, Path)):
            return
        if isinstance(mode, str):
            reading = not any(c in mode for c in "wax+")
        else:
            reading = not (int(flags or 0) & _WRITE_FLAGS)
        _OPENED.append((Path(path.decode() if isinstance(path, bytes) else path), reading))


def test_analysis_reads_allowed_files_only(fx, tmp_path):
    """T = 40 runs (one usable_T, 30, which is primary), with every forbidden file present."""
    global _OPENED, _HOOKED
    root = tmp_path / "results" / "runs"
    with api_guard():
        for cond in ("control", "cover"):
            d = run(RunConfig(condition=cond, seed=1, T=40), synthetic_bidder_factory, results_root=root)
            (d / "transcripts").mkdir(exist_ok=True)
            (d / "transcripts" / "Somebody.md").write_text("prompt", encoding="utf-8")
            for name in ("messages.csv", "calls.csv", "checkpoint.json", "heartbeat.json"):
                assert (d / name).is_file(), name
    (tmp_path / "results" / "calibration.json").write_text(json.dumps(CALIBRATION), encoding="utf-8")
    (tmp_path / "results" / "STOP.json").write_text(json.dumps({"reason": "test", "runs_incomplete": []}),
                                                    encoding="utf-8")
    scripted_csv = tmp_path / "scripted_signatures.csv"
    shutil.copy(fx["scripted_csv"], scripted_csv)
    plan = tmp_path / "config" / "run_plan.json"
    plan.parent.mkdir()
    plan.write_text(json.dumps({"R": {"control": 1, "cover": 1}, "include_rotation": True, "T": 40}),
                    encoding="utf-8")
    frozen = tmp_path / "config" / "frozen.json"
    frozen.write_text(json.dumps({"cost": {"beta0": 0.90, "beta1": 0.08, "beta2": 0.17, "sigma_eps": 0.05}}),
                      encoding="utf-8")
    out = tmp_path / "results" / "analysis" / "r"
    if not _HOOKED:
        sys.addaudithook(_audit)  # audit hooks cannot be removed; it records only while _OPENED is a list
        _HOOKED = True
    _OPENED = []
    try:
        analyze(root, out, scripted_csv=scripted_csv, include_synthetic=True, frozen_path=frozen,
                plan_path=plan)
    finally:
        opened, _OPENED = _OPENED, None
    base = tmp_path.resolve()
    reads = {p.resolve() for p, reading in opened if reading and p.resolve().is_relative_to(base)}
    allowed = {"pair_stats.csv", "truth.csv", "bids.csv", "run_meta.json", "pair_eval.csv", "run_eval.csv",
               "excluded_runs.json", "scripted_signatures.csv", "calibration.json", "STOP.json", "STOP",
               "run_plan.json", "frozen.json", "messages.csv"}  # messages.csv: refusal columns (spec 2.7)
    assert {p.name for p in reads} <= allowed, {p.name for p in reads} - allowed
    msg_dirs = {p.parent.name for p in reads if p.name == "messages.csv"}
    assert msg_dirs and not any("control" in d for d in msg_dirs), msg_dirs  # scheme runs only
    assert not any("transcripts" in p.parts for p in reads)
    for name in ("excluded_runs.json", "scripted_signatures.csv", "calibration.json", "STOP.json", "bids.csv",
                 "run_plan.json", "frozen.json"):
        assert any(p.name == name for p in reads), name  # the monitor sees the reads it should

    fr = pd.read_csv(out / "flag_rates.csv")
    assert set(fr.condition) == {"control", "cover"}
    assert fr.loc[(fr.condition == "control") & (fr.spec == "tag"), "n_pairs"].eq(15).all()
    pw = pd.read_csv(out / "power_vs_T.csv")
    assert set(pw.usable_T) == {30}


# ------------------------------------------------------------------ final review fix wave

def test_stop_and_calibration_json_read_defensively(tmp_path):
    """A3: an unreadable, invalid or non-object STOP.json / calibration.json is treated as absent and
    named under "Not computed"; analysis does not crash."""
    results = tmp_path / "results"
    root = results / "runs"
    with api_guard():
        run(RunConfig(condition="control", seed=1, T=12), synthetic_bidder_factory, results_root=root)
    cases = [("[1, 2]", "{not json", "is not a JSON object (a list)", "is not valid JSON"),
             ("{not json", "[3]", "is not valid JSON", "is not a JSON object (a list)")]
    for k, (stop_text, cal_text, stop_msg, cal_msg) in enumerate(cases):
        (results / "STOP.json").write_text(stop_text, encoding="utf-8")
        (results / "calibration.json").write_text(cal_text, encoding="utf-8")
        out = results / "analysis" / f"d{k}"
        analyze(root, out, include_synthetic=True)
        notes = (out / "README.md").read_text(encoding="utf-8").split("## Not computed")[1]
        assert f"results/STOP.json: the file {stop_msg}" in notes
        assert f"calibration section: results/calibration.json {cal_msg}" in notes
    # unreadable (a directory where the file should be)
    (results / "STOP.json").unlink()
    (results / "STOP.json").mkdir()
    (results / "calibration.json").unlink()
    (results / "calibration.json").mkdir()
    out = results / "analysis" / "dir"
    analyze(root, out, include_synthetic=True)
    notes = (out / "README.md").read_text(encoding="utf-8").split("## Not computed")[1]
    assert "results/STOP.json: the file cannot be read" in notes
    assert "results/calibration.json cannot be read" in notes


def test_readme_lists_runs_without_directory(tmp_path):
    """B6: the run inventory lists STOP.json runs_not_started and the plan rows with no directory."""
    results = tmp_path / "results"
    root = results / "runs"
    with api_guard():
        run(RunConfig(condition="control", seed=1, T=12), synthetic_bidder_factory, results_root=root)
    (results / "STOP.json").write_text(json.dumps({"reason": "spend", "runs_incomplete": [],
                                                   "runs_not_started": ["cover_s2", "control_s1"]}),
                                       encoding="utf-8")
    plan = tmp_path / "run_plan.json"
    plan.write_text(json.dumps({"R": {"control": 2, "cover": 2, "markup": 0, "rotation": 1},
                                "include_rotation": False, "T": 12}), encoding="utf-8")
    out = results / "analysis" / "a"
    analyze(root, out, include_synthetic=True, plan_path=plan)
    readme = (out / "README.md").read_text(encoding="utf-8")
    inv = readme.split("## Run inventory")[1].split("## Flag rates")[0]
    table = inv.split("Runs without a run directory:")[1]
    rows = {ln.split("|")[1].strip(): ln.split("|")[2].strip() for ln in table.splitlines()
            if ln.startswith("| ") and "---" not in ln and not ln.startswith("| run ")}
    assert set(rows) == {"cover_s2", "control_s2", "cover_s1"}  # control_s1 has a directory; no rotation
    assert rows["cover_s2"] == "not started: launcher stop (spend, results/STOP.json)"
    assert rows["control_s2"].startswith("planned in config/run_plan.json but no run directory")
    assert rows["cover_s1"].startswith("planned in config/run_plan.json")


def test_analysis_cli_paths_resolve_against_repo_root(fx, tmp_path, monkeypatch):
    """C4: relative --results-root and the default output root resolve against the repository root."""
    from src.procurement import config
    fake_repo = tmp_path / "repo"
    src = fx["dirs"]["control_s1"]
    shutil.copytree(src, fake_repo / "results" / "runs" / src.name)
    monkeypatch.setattr(config, "repo_root", lambda: fake_repo)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    main(["--results-root", "results/runs", "--include-synthetic", "--frozen", "none.json", "--plan", "none.json"])
    (made,) = list((fake_repo / "results" / "round1" / "analysis").iterdir())
    assert (made / "flag_rates.csv").is_file() and (fake_repo / "results" / "round1" / "README_round1.md").is_file()
    assert not (elsewhere / "results").exists()


def test_round1_analysis_ignores_other_arms(fx, tmp_path):
    root = tmp_path / "results" / "runs"
    shutil.copytree(fx["root"], root)
    cfg = RunConfig(condition="control", seed=1, T=110, arm="mem0", bidder="synthetic", memory_window=0)
    with api_guard():
        run(cfg, runner_mod.bidder_factory_for(cfg), results_root=root)
    out = tmp_path / "results" / "analysis" / "b"
    analyze(root, out, include_synthetic=True)
    for name in ("flag_rates.csv", "empirical_null.csv", "power_vs_T.csv"):
        assert (out / name).read_text(encoding="utf-8") == (fx["out"] / name).read_text(encoding="utf-8"), name
    readme = (out / "README.md").read_text(encoding="utf-8")
    assert "Other arms (see ../round2/README_round2.md): mem0 (1 run)" in readme
    assert "control_mem0_s1" not in readme


def test_dir_run_ids_arm_aware(tmp_path):
    for n in ("20261025_223000_control_s1", "20261025_223000_control_mem0_s2",
              "20261025_223100_cover_stateless_hidden_s3", "20261025_223100_cover_s4_2"):
        (tmp_path / n).mkdir()
    assert analysis._dir_run_ids(tmp_path) == {"control_s1", "control_mem0_s2", "cover_stateless_hidden_s3",
                                               "cover_s4"}
