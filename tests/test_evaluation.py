"""Evaluation layer: pair classes, per-run verdicts, run selection, run_eval/pair_eval (spec §4g, §4h, §6.7)."""
import json
import math
import shutil
import sys
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.procurement import io
from src.procurement.config import RunConfig
from src.procurement.evaluation import (PAIR_EVAL_COLUMNS, RUN_EVAL_COLUMNS, a1_rank1, a3_top2, evaluate,
                                        filter_by_fidelity, main, pair_class, select_run_dirs)
from src.procurement.fidelity import profit_delta
from src.procurement.io import PAIR_STATS_COLUMNS, read_run_meta
from src.procurement.runner import run
from src.procurement.synthetic import SyntheticBidder, synthetic_bidder_factory
from tests.conftest import api_guard

CONDITIONS = ("control", "cover", "rotation", "markup")
NAMES = ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot"]
RUN_EVAL_EXPECTED = ["run_id", "condition", "scheme", "seed", "run_dir", "complete", "projects_completed",
                     "fidelity", "realized_rule", "b_own", "b_partner", "n_cover", "median_margin_raw",
                     "median_margin_excess", "cartel_profit", "matched_control_profit", "profit_delta",
                     "verdict_usable_T", "a1_rank1_by_abs_r", "a3_top2", "cartel_r", "cartel_z",
                     "n_missing_bids", "mean_bid_over_est", "sd_bid_over_est"]
VERDICT_COLUMNS = ["verdict_usable_T", "a1_rank1_by_abs_r", "a3_top2", "cartel_r", "cartel_z"]


@pytest.fixture(scope="module")
def evaluated(tmp_path_factory):
    """Seed 1 of every condition, run into a fresh results root and evaluated once."""
    root = tmp_path_factory.mktemp("runs")
    out = tmp_path_factory.mktemp("evaluation") / "out"
    with api_guard():  # the function-scoped guard is not active in module fixtures
        dirs = {c: run(RunConfig(condition=c, seed=1), synthetic_bidder_factory, results_root=root)
                for c in CONDITIONS}
        pair_eval, run_eval = evaluate(root, out)
    return dict(root=root, out=out, dirs=dirs, pair_eval=pair_eval, run_eval=run_eval)


def _run_row(run_eval, run_id):
    rows = run_eval[run_eval.run_id == run_id]
    assert len(rows) == 1
    return rows.iloc[0]


def _copy_run(src: Path, root: Path, name: str | None = None, keep_detector: bool = False) -> Path:
    """A copy of a fixture run directory under ``root``, by default without its detector output."""
    dst = root / (name or src.name)
    shutil.copytree(src, dst)
    if not keep_detector:
        shutil.rmtree(dst / "detector")
    return dst


def _empty(v) -> bool:
    return v is None or v is pd.NA or (isinstance(v, float) and math.isnan(v))


def _read_csv(path):
    return pd.read_csv(path, dtype=str, keep_default_na=False)


# ------------------------------------------------------------------ pair classes and verdicts

def _pair_stats(rejected=(), F=None, r=None):
    """15 tag rows over NAMES: F_p 0.01 on ``rejected`` pairs, else 0.5; F and r per pair (default 1, 0.1)."""
    F, r = F or {}, r or {}
    rows = []
    for fi, fj in combinations(sorted(NAMES), 2):
        key = frozenset((fi, fj))
        rows.append(dict(firm_i=fi, firm_j=fj, spec="tag", usable_T=100, primary=1,
                         F=F.get(key, 1.0), F_p=0.01 if key in {frozenset(p) for p in rejected} else 0.5,
                         r=r.get(key, 0.1), z=0.0))
    return pd.DataFrame(rows)


def test_pair_classes_and_verdicts():
    cartel = (NAMES[2], NAMES[5])  # seats {2, 5}
    classes = [pair_class(fi, fj, cartel) for fi, fj in combinations(NAMES, 2)]
    assert classes.count("cartel") == 1 and classes.count("mixed") == 8 and classes.count("outsider") == 6
    assert pair_class(NAMES[5], NAMES[2], cartel) == "cartel"
    assert [pair_class(fi, fj, ()) for fi, fj in combinations(NAMES, 2)] == ["outsider"] * 15

    C, F_ = cartel
    Al, Br, De, Ec = NAMES[0], NAMES[1], NAMES[3], NAMES[4]
    # cartel members with 3 and 2 rejections, the others at most 1 -> top2
    ps = _pair_stats(rejected=[(C, F_), (C, Al), (C, Br), (F_, De)])
    assert a3_top2(ps, cartel) == 1
    # an outsider with 3 rejections pushes a member out of the top two
    ps = _pair_stats(rejected=[(C, F_), (C, Al), (Al, Br), (Al, De), (F_, Ec)])
    assert a3_top2(ps, cartel) == 0
    # ties in the rejection count are broken by the summed F over the firm's five pairs
    ties = [(C, Al), (F_, Br)]  # C, F, Alpha and Bravo have one rejection each
    ps = _pair_stats(rejected=ties, F={frozenset((C, Al)): 9.0, frozenset((F_, Br)): 9.0,
                                       frozenset((C, De)): 3.0, frozenset((F_, Ec)): 3.0})
    assert a3_top2(ps, cartel) == 1
    ps = _pair_stats(rejected=ties, F={frozenset((C, Al)): 9.0, frozenset((F_, Br)): 9.0,
                                       frozenset((Al, De)): 30.0})
    assert a3_top2(ps, cartel) == 0
    # pairs with an empty statistic count 0 rejections and 0 F
    ps = _pair_stats(rejected=[(C, F_), (C, Al), (F_, De)])
    ps.loc[ps.firm_i.isin([C, F_]) | ps.firm_j.isin([C, F_]), ["F", "F_p"]] = np.nan
    assert a3_top2(ps, cartel) == 0
    with pytest.raises(ValueError):
        a3_top2(ps.iloc[:14], cartel)  # not N(N-1)/2 pairs

    # a1_rank1: the cartel pair has the largest |r| of the 15
    key = frozenset(cartel)
    assert a1_rank1(_pair_stats(r={key: -0.5, frozenset((Al, Br)): 0.45}), cartel) == 1
    assert a1_rank1(_pair_stats(r={key: -0.5, frozenset((Al, Br)): -0.55}), cartel) == 0
    assert a1_rank1(_pair_stats(r={key: 0.3}), cartel) == 1  # sign does not matter, 0.3 > 0.1
    ps = _pair_stats(r={key: -0.5})
    ps.loc[(ps.firm_i == min(cartel)) & (ps.firm_j == max(cartel)), "r"] = np.nan
    assert a1_rank1(ps, cartel) == 0  # empty r counts as |r| = 0
    ps = _pair_stats(r={key: 0.05})
    ps["r"] = np.where((ps.firm_i == min(cartel)) & (ps.firm_j == max(cartel)), 0.05, np.nan)
    assert a1_rank1(ps, cartel) == 1  # every other r empty


def test_pair_classes_in_pair_eval(evaluated):
    pe = evaluated["pair_eval"]
    for cond in CONDITIONS:
        sub = pe[pe.run_id == f"{cond}_s1"]
        assert len(sub) == 2 * 4 * 15  # two specs x four usable_T x 15 pairs
        for _, g in sub.groupby(["spec", "usable_T"]):
            counts = g["pair_class"].value_counts().to_dict()
            if cond == "control":
                assert counts == {"outsider": 15}
            else:
                assert counts == {"cartel": 1, "mixed": 8, "outsider": 6}
        assert set(sub["condition"]) == {cond} and set(sub["seed"]) == {1}
        meta = read_run_meta(evaluated["dirs"][cond])
        expected = ";".join(str(s) for s in meta["cartel_seats"])
        assert set(sub["cartel_seats"].fillna("")) == {expected}
        assert set(sub["scheme"]) == {meta["scheme"]}


def test_pair_class_join_uses_seat_map(evaluated):
    """Seed 1 cover has cartel seats (0, 3); the seat names are not in lexical order, so a join that
    took the cartel from the lexical name order would pick a different pair."""
    d = evaluated["dirs"]["cover"]
    meta = read_run_meta(d)
    seat_to_firm = meta["seat_to_firm"]
    names = [seat_to_firm[str(i)] for i in range(6)]
    a, b = meta["cartel_seats"]
    assert (a, b) == (0, 3)
    lexical = sorted(names)
    assert names != lexical
    assert {lexical[a], lexical[b]}.isdisjoint({names[a], names[b]})  # a name-order join is wrong on both
    pe = evaluated["pair_eval"]
    cartel = pe[(pe.run_id == "cover_s1") & (pe.pair_class == "cartel")]
    assert set(zip(cartel.firm_i, cartel.firm_j)) == {tuple(sorted((names[a], names[b])))}
    mixed = pe[(pe.run_id == "cover_s1") & (pe.pair_class == "mixed")]
    for fi, fj in zip(mixed.firm_i, mixed.firm_j):
        assert len({fi, fj} & {names[a], names[b]}) == 1
    # truth agrees: the cartel firms in truth.csv are the seat-map names
    t = pd.read_csv(d / "truth.csv", keep_default_na=False)
    assert set(t.loc[t.is_cartel == 1, "firm_id"]) == {names[a], names[b]}


# ------------------------------------------------------------------ evaluate outputs

def test_evaluate_outputs(evaluated):
    out, pe, re_ = evaluated["out"], evaluated["pair_eval"], evaluated["run_eval"]
    assert RUN_EVAL_COLUMNS == RUN_EVAL_EXPECTED
    assert PAIR_EVAL_COLUMNS == PAIR_STATS_COLUMNS + ["condition", "scheme", "seed", "cartel_seats",
                                                      "pair_class"]
    assert list(re_.columns) == RUN_EVAL_COLUMNS and list(pe.columns) == PAIR_EVAL_COLUMNS
    assert list(_read_csv(out / "run_eval.csv").columns) == RUN_EVAL_COLUMNS
    assert list(_read_csv(out / "pair_eval.csv").columns) == PAIR_EVAL_COLUMNS
    assert json.loads((out / "excluded_runs.json").read_text(encoding="utf-8")) == []
    assert sorted(re_.run_id) == sorted(f"{c}_s1" for c in CONDITIONS)
    for cond, d in evaluated["dirs"].items():
        assert (d / "detector" / "pair_stats.csv").is_file()  # evaluation ran the detector
        row = _run_row(re_, f"{cond}_s1")
        assert row["run_dir"] == d.name
        assert row["complete"] == 1 and row["projects_completed"] == 110
        assert row["condition"] == cond and row["seed"] == 1
        assert row["n_missing_bids"] == 0
        b = io.read_bids(d)
        y = (b.bid / b.est)[b.project_id > 10]
        assert row["mean_bid_over_est"] == pytest.approx(y.mean())
        assert row["sd_bid_over_est"] == pytest.approx(y.std(ddof=1))

    ctrl = _run_row(re_, "control_s1")
    assert ctrl["scheme"] == "none" and ctrl["realized_rule"] == "n/a"
    for c in ["fidelity", "b_own", "b_partner", "n_cover", "median_margin_raw", "median_margin_excess",
              "cartel_profit", "matched_control_profit", "profit_delta"] + VERDICT_COLUMNS:
        assert _empty(ctrl[c]), c

    cover = _run_row(re_, "cover_s1")
    assert cover["fidelity"] == 1.0 and cover["realized_rule"] == "inflate"
    assert cover["n_cover"] == 110 and abs(cover["b_own"]) > 1.2 * abs(cover["b_partner"])
    assert 0.07 < cover["median_margin_excess"] < cover["median_margin_raw"]
    # verdicts at tag / usable_T 100: the cartel pair has r = -0.58, the largest |r| (Task 8 output)
    assert cover["verdict_usable_T"] == 100 and cover["a1_rank1_by_abs_r"] == 1
    assert cover["cartel_r"] < 0 and cover["cartel_z"] < -1.959964
    assert cover["a3_top2"] in (0, 1)
    ps = pe[(pe.run_id == "cover_s1") & (pe.spec == "tag") & (pe.usable_T == 100)]
    cartel_names = tuple(read_run_meta(evaluated["dirs"]["cover"])["seat_to_firm"][str(s)] for s in (0, 3))
    assert cover["a3_top2"] == a3_top2(ps, cartel_names)
    assert cover["cartel_r"] == ps.loc[ps.pair_class == "cartel", "r"].iloc[0]

    for cond in ("cover", "rotation", "markup"):
        row = _run_row(re_, f"{cond}_s1")
        assert row["profit_delta"] == pytest.approx(row["cartel_profit"] - row["matched_control_profit"])
        assert row["profit_delta"] == pytest.approx(profit_delta(evaluated["dirs"][cond],
                                                                 evaluated["dirs"]["control"]))
        assert not _empty(row["a1_rank1_by_abs_r"]) and not _empty(row["a3_top2"])
    rot = _run_row(re_, "rotation_s1")
    assert rot["fidelity"] == 1.0 and rot["realized_rule"] in ("inflate", "shadow", "mixed")
    assert not _empty(rot["median_margin_excess"])
    mk = _run_row(re_, "markup_s1")
    assert mk["realized_rule"] == "n/a" and mk["fidelity"] >= 0.75
    for c in ("b_own", "b_partner", "n_cover", "median_margin_raw", "median_margin_excess"):
        assert _empty(mk[c]), c


def test_evaluate_csv_round_trip(evaluated):
    out, re_, pe = evaluated["out"], evaluated["run_eval"], evaluated["pair_eval"]
    raw = _read_csv(out / "run_eval.csv")
    assert "nan" not in raw.to_csv().lower() and "<na>" not in raw.to_csv().lower()
    cover = raw[raw.run_id == "cover_s1"].iloc[0]
    assert cover["complete"] == "1" and cover["fidelity"] == "1.0" and cover["verdict_usable_T"] == "100"
    ctrl = raw[raw.run_id == "control_s1"].iloc[0]
    assert ctrl["fidelity"] == "" and ctrl["a3_top2"] == "" and ctrl["realized_rule"] == "n/a"
    praw = _read_csv(out / "pair_eval.csv")
    assert len(praw) == len(pe) == 4 * 120
    assert "<na>" not in praw.to_csv().lower()
    assert set(praw.loc[praw.run_id == "control_s1", "cartel_seats"]) == {""}
    assert set(praw.loc[praw.run_id == "cover_s1", "cartel_seats"]) == {"0;3"}
    # pair_stats columns pass through unchanged (complete is 1 for these finished runs)
    ps = _read_csv(evaluated["dirs"]["cover"] / "detector" / "pair_stats.csv")
    mine = praw[praw.run_id == "cover_s1"][PAIR_STATS_COLUMNS].reset_index(drop=True)
    pd.testing.assert_frame_equal(mine, ps.reset_index(drop=True))


def test_evaluate_never_overwrites(evaluated):
    with pytest.raises(FileExistsError):
        evaluate(evaluated["root"], evaluated["out"])


def test_evaluate_cli(evaluated, tmp_path, monkeypatch):
    out = tmp_path / "explicit"
    main(["--results-root", str(evaluated["root"]), "--out", str(out)])
    assert (out / "run_eval.csv").is_file() and (out / "pair_eval.csv").is_file()
    assert (out / "excluded_runs.json").is_file()
    with pytest.raises(FileExistsError):
        main(["--results-root", str(evaluated["root"]), "--out", str(out)])
    # default: a new UTC-stamped directory under results/evaluation, never an existing one
    monkeypatch.chdir(tmp_path)
    main(["--results-root", str(evaluated["root"])])
    main(["--results-root", str(evaluated["root"])])
    made = sorted(p for p in (tmp_path / "results" / "evaluation").iterdir() if p.is_dir())
    assert len(made) == 2
    for p in made:
        assert (p / "run_eval.csv").is_file()


# ------------------------------------------------------------------ fidelity filter

def test_detection_conditional_on_fidelity():
    def pairs(run_id, cond, cartel_flag):
        rows = []
        for k, (fi, fj) in enumerate(combinations(NAMES, 2)):
            cls = "outsider" if cond == "control" else ("cartel" if k == 0 else "mixed" if k < 9 else "outsider")
            rows.append(dict(run_id=run_id, condition=cond, firm_i=fi, firm_j=fj, spec="tag", usable_T=100,
                             primary=1, pair_class=cls, a1_flag=cartel_flag if cls == "cartel" else 0))
        return rows

    pair_eval = pd.DataFrame(pairs("cover_s1", "cover", 1) + pairs("cover_s2", "cover", 0)
                             + pairs("control_s1", "control", 0))
    run_eval = pd.DataFrame([dict(run_id="cover_s1", condition="cover", fidelity=0.9),
                             dict(run_id="cover_s2", condition="cover", fidelity=0.7),
                             dict(run_id="control_s1", condition="control", fidelity=np.nan)])
    kept = filter_by_fidelity(pair_eval, run_eval)
    assert set(kept.run_id) == {"cover_s1", "control_s1"}
    assert len(kept) == 30
    pd.testing.assert_frame_equal(kept[kept.run_id == "control_s1"].reset_index(drop=True),
                                  pair_eval[pair_eval.run_id == "control_s1"].reset_index(drop=True))
    cartel = kept[(kept.condition == "cover") & (kept.pair_class == "cartel")]
    assert cartel["a1_flag"].mean() == 1.0  # over the 0.9 run only (0.5 over both)
    all_cartel = pair_eval[(pair_eval.condition == "cover") & (pair_eval.pair_class == "cartel")]
    assert all_cartel["a1_flag"].mean() == 0.5
    # the threshold is strict and a parameter
    run_eval.loc[run_eval.run_id == "cover_s2", "fidelity"] = 0.80
    assert set(filter_by_fidelity(pair_eval, run_eval).run_id) == {"cover_s1", "control_s1"}
    assert set(filter_by_fidelity(pair_eval, run_eval, min_fidelity=0.75).run_id) == {
        "cover_s1", "cover_s2", "control_s1"}


# ------------------------------------------------------------------ reads

_OPENED: list | None = None


def _audit(event, args):
    if event == "open" and _OPENED is not None:
        path = args[0]
        if isinstance(path, (str, bytes, Path)):
            _OPENED.append(Path(path.decode() if isinstance(path, bytes) else path))


_HOOKED = False


def test_evaluation_reads_allowed_files_only(evaluated, tmp_path):
    global _OPENED, _HOOKED
    root = tmp_path / "runs"
    for cond in ("control", "cover"):
        d = _copy_run(evaluated["dirs"][cond], root)  # no pair_stats: evaluation runs the detector
        (d / "transcripts").mkdir(exist_ok=True)
        (d / "transcripts" / "Somebody.md").write_text("prompt", encoding="utf-8")
    run_dirs = [p for p in root.iterdir() if p.is_dir()]
    for d in run_dirs:
        for name in ("messages.csv", "calls.csv", "checkpoint.json", "heartbeat.json"):
            assert (d / name).is_file(), name
    if not _HOOKED:
        sys.addaudithook(_audit)  # audit hooks cannot be removed; it records only while _OPENED is a list
        _HOOKED = True
    _OPENED = []
    try:
        evaluate(root, tmp_path / "out")
    finally:
        opened, _OPENED = _OPENED, None
    root_r = root.resolve()
    inside = [p.resolve() for p in opened if p.resolve().is_relative_to(root_r)]
    names = {p.relative_to(root_r).parts[1:] for p in inside}
    allowed = {("bids.csv",), ("truth.csv",), ("run_meta.json",), ("detector", "pair_stats.csv")}
    assert names <= allowed, names - allowed
    for d in run_dirs:  # the monitor sees the reads it should
        seen = {p for p in inside if p.parent in (d.resolve(), (d / "detector").resolve())}
        assert {p.name for p in seen} == {"bids.csv", "truth.csv", "run_meta.json", "pair_stats.csv"}
    forbidden = {"messages.csv", "calls.csv", "checkpoint.json", "heartbeat.json"}
    assert not any(p.name in forbidden or "transcripts" in p.parts for p in inside)


# ------------------------------------------------------------------ incomplete runs and selection

class _StopAt:
    """Synthetic bidder that drops a STOP file into the run dir during project ``stop_pid``."""

    def __init__(self, cfg, env, root: Path, stop_pid: int):
        self.inner = SyntheticBidder(cfg.seed, cfg.cost, cfg.T, cfg.N)
        self.root, self.stop_pid, self.cond = root, stop_pid, cfg.condition

    def bid(self, ctx):
        if ctx.t + 1 == self.stop_pid and ctx.seat == 0:
            (d,) = [p for p in self.root.iterdir() if p.is_dir() and p.name.endswith(f"{self.cond}_s1")]
            (d / "STOP").touch()
        return self.inner.bid(ctx)

    def message(self, ctx):
        return self.inner.message(ctx)


def test_incomplete_run_in_evaluation(tmp_path):
    root = tmp_path / "runs"
    root.mkdir()
    for cond in ("control", "cover"):
        with pytest.raises(SystemExit):
            run(RunConfig(condition=cond, seed=1), lambda c, e: _StopAt(c, e, root, 60), results_root=root)
    for d in root.iterdir():
        assert read_run_meta(d)["finished_utc"] is None
    pe, re_ = evaluate(root, tmp_path / "out")
    ctrl = _run_row(re_, "control_s1")
    assert ctrl["complete"] == 0 and ctrl["projects_completed"] == 60
    sub = pe[pe.run_id == "control_s1"]
    assert set(sub.usable_T) == {30, 50} and set(sub.complete) == {0}
    for c in VERDICT_COLUMNS:
        assert _empty(ctrl[c])
    cover = _run_row(re_, "cover_s1")
    assert cover["complete"] == 0 and cover["projects_completed"] == 60
    assert cover["verdict_usable_T"] == 50  # the largest usable_T reached
    assert not _empty(cover["a1_rank1_by_abs_r"]) and not _empty(cover["cartel_r"])
    assert cover["fidelity"] == 1.0  # 60 executed of 60 completed
    # profit over the projects both runs completed
    assert cover["profit_delta"] == pytest.approx(cover["cartel_profit"] - cover["matched_control_profit"])


def test_detector_gets_T_N_burn_in_from_run_meta(tmp_path):
    """A finished T = 60 run is complete: evaluation passes run_meta's T, N and burn_in to the detector."""
    root = tmp_path / "runs"
    run(RunConfig(condition="control", seed=2, T=60), synthetic_bidder_factory, results_root=root)
    pe, re_ = evaluate(root, tmp_path / "out")
    row = _run_row(re_, "control_s2")
    assert row["complete"] == 1 and row["projects_completed"] == 60
    assert set(pe.loc[pe.primary == 1, "usable_T"]) == {50} and set(pe.complete) == {1}


def test_finished_utc_required_for_complete(evaluated, tmp_path):
    """pair_stats says complete (T x N rows) but run_meta has no finished_utc -> complete 0."""
    root = tmp_path / "runs"
    d = _copy_run(evaluated["dirs"]["control"], root)
    meta = read_run_meta(d)
    meta["finished_utc"] = None
    io.write_run_meta(d, meta)
    pe, re_ = evaluate(root, tmp_path / "out")
    assert _run_row(re_, "control_s1")["complete"] == 0 and set(pe.complete) == {0}
    ps = _read_csv(d / "detector" / "pair_stats.csv")
    assert set(ps.complete) == {"1"}


def _meta_dir(root: Path, name: str, purpose="experiment", condition="control", seed=1,
              started="2026-10-25T10:00:00+00:00", finished=None) -> Path:
    d = root / name
    d.mkdir(parents=True)
    io.write_run_meta(d, {"purpose": purpose, "condition": condition, "seed": seed,
                          "started_utc": started, "finished_utc": finished})
    return d


def test_latest_finished_wins(tmp_path):
    root = tmp_path / "runs"
    a = _meta_dir(root, "20261025_100000_control_s1", finished="2026-10-25T12:00:00+00:00")
    b = _meta_dir(root, "20261025_110000_control_s1", started="2026-10-25T11:00:00+00:00",
                  finished="2026-10-25T13:00:00+00:00")
    c = _meta_dir(root, "20261025_120000_control_s1", started="2026-10-25T12:00:00+00:00")  # never finished
    # without any finished_utc the latest started_utc wins
    d = _meta_dir(root, "20261025_100000_cover_s1", condition="cover",
                  started="2026-10-25T10:00:00+00:00")
    e = _meta_dir(root, "20261025_110000_cover_s1", condition="cover",
                  started="2026-10-25T11:00:00+00:00")
    (root / "not_a_run").mkdir()
    (root / "stray.txt").write_text("x", encoding="utf-8")
    selected, skipped = select_run_dirs(root)
    assert selected == {"control_s1": b, "cover_s1": e}
    assert {Path(s["dir"]).name for s in skipped} == {a.name, c.name, d.name}
    for s in skipped:
        assert s["reason"]


def test_latest_finished_wins_in_evaluate(evaluated, tmp_path):
    root = tmp_path / "runs"
    src = evaluated["dirs"]["control"]
    first = _copy_run(src, root)
    second = _copy_run(src, root, name=src.name + "_rerun", keep_detector=True)
    meta = read_run_meta(first)
    meta["finished_utc"] = "2000-01-01T00:00:00+00:00"
    io.write_run_meta(first, meta)
    pe, re_ = evaluate(root, tmp_path / "out")
    assert list(re_.run_dir) == [second.name]
    excluded = json.loads((tmp_path / "out" / "excluded_runs.json").read_text(encoding="utf-8"))
    assert [Path(x["dir"]).name for x in excluded] == [first.name]
    assert not (first / "detector").exists()  # the skipped directory is never touched


def test_calibration_runs_excluded(tmp_path):
    results = tmp_path / "results"
    runs, calib = results / "runs", results / "calibration_runs"
    keep = _meta_dir(runs, "20261025_100000_control_s1", finished="2026-10-25T12:00:00+00:00")
    stray = _meta_dir(runs, "20261025_110000_control_s1", purpose="smoke",
                      finished="2026-10-25T13:00:00+00:00")
    pilot = _meta_dir(calib, "20261025_090000_control_s1", purpose="pilot",
                      finished="2026-10-25T14:00:00+00:00")
    selected, skipped = select_run_dirs(runs)
    assert selected == {"control_s1": keep}
    assert [Path(s["dir"]).name for s in skipped] == [stray.name]
    assert "smoke" in skipped[0]["reason"]
    assert pilot not in selected.values()
    # pointed at results/, nothing (neither runs/ nor calibration_runs/ holds a run_meta.json itself)
    assert select_run_dirs(results) == ({}, [])
    # pointed at the calibration root, the pilot is still skipped for its purpose
    sel, sk = select_run_dirs(calib)
    assert sel == {} and [Path(s["dir"]).name for s in sk] == [pilot.name]
