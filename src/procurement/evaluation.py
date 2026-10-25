"""Evaluation layer: pair_stats joined with ground truth (spec §4g "Evaluation output", §4h verdicts).

For every selected experiment run under a results root this writes ``pair_eval.csv`` (the detector's
``pair_stats.csv`` plus pair class and run identity) and ``run_eval.csv`` (one row per run: completeness,
execution fidelity, realized cover rule, margins, profit vs the matched control run, per-run verdicts and
bid summaries), plus ``excluded_runs.json`` listing the directories not used.

Evaluation reads only ``pair_stats.csv``, ``truth.csv``, ``bids.csv`` and ``run_meta.json``. When a run
has no ``pair_stats.csv`` it runs the detector, passing T, N and burn_in read from run_meta, so the
detector itself stays blind to run_meta.

CLI: ``python -m src.procurement.evaluation --results-root results/runs [--out results/evaluation/<UTC>]``.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from src.procurement import detector, fidelity as fid, io
from src.procurement.io import PAIR_STATS_COLUMNS

PAIR_EVAL_COLUMNS = PAIR_STATS_COLUMNS + ["condition", "scheme", "seed", "cartel_seats", "pair_class"]
RUN_EVAL_COLUMNS = ["run_id", "condition", "scheme", "seed", "run_dir", "complete", "projects_completed",
                    "fidelity", "realized_rule", "b_own", "b_partner", "n_cover", "median_margin_raw",
                    "median_margin_excess", "cartel_profit", "matched_control_profit", "profit_delta",
                    "verdict_usable_T", "a1_rank1_by_abs_r", "a3_top2", "cartel_r", "cartel_z",
                    "n_missing_bids", "mean_bid_over_est", "sd_bid_over_est"]
OUTPUT_FILES = ("pair_eval.csv", "run_eval.csv", "excluded_runs.json")
VERDICT_SPEC = "tag"
EXPERIMENT = "experiment"
EVALUATION_ROOT = "results/evaluation"
RULE_SCHEMES = ("cover", "rotation")  # schemes with cover projects

_RUN_EVAL_INT = ("seed", "complete", "projects_completed", "n_cover", "verdict_usable_T", "a1_rank1_by_abs_r",
                 "a3_top2", "n_missing_bids")
_RUN_EVAL_STR = ("run_id", "condition", "scheme", "run_dir", "realized_rule")


# ------------------------------------------------------------------ pair classes and verdicts

def pair_class(firm_i: str, firm_j: str, cartel_names) -> str:
    """"cartel" if both firms are cartel firms, "mixed" if exactly one is, else "outsider"."""
    k = (firm_i in cartel_names) + (firm_j in cartel_names)
    return ("outsider", "mixed", "cartel")[k]


def _check_pairs(ps: pd.DataFrame, cartel_names, N: int) -> list[str]:
    firms = sorted(set(ps["firm_i"]) | set(ps["firm_j"]))
    if len(ps) != N * (N - 1) // 2 or len(firms) != N:
        raise ValueError(f"expected the {N * (N - 1) // 2} pairs of {N} firms, got {len(ps)} rows "
                         f"over {len(firms)} firms")
    if len(set(cartel_names)) != 2 or not set(cartel_names) <= set(firms):
        raise ValueError(f"cartel {cartel_names!r} is not two of the firms {firms}")
    return firms


def _is_cartel_row(ps: pd.DataFrame, cartel_names) -> pd.Series:
    c = set(cartel_names)
    return ps["firm_i"].isin(c) & ps["firm_j"].isin(c)


def a3_top2(pair_stats_run_spec_T: pd.DataFrame, cartel_names, N: int = 6,
            alpha: float = detector.A3_ALPHA) -> int:
    """1 iff the two cartel firms rank above every other firm by (number of its N-1 pairs with
    F_p < alpha, then summed F over its pairs); a tie with an outsider is not top two. Pairs with an
    empty statistic count 0 rejections and 0 F. Input: one run's rows for one spec and usable_T."""
    ps = pair_stats_run_spec_T
    firms = _check_pairs(ps, cartel_names, N)
    p = pd.to_numeric(ps["F_p"]).to_numpy(dtype=float)
    F = np.nan_to_num(pd.to_numeric(ps["F"]).to_numpy(dtype=float), nan=0.0)
    rej = (p < alpha).astype(int)  # NaN < alpha is False
    score = {f: [0, 0.0] for f in firms}
    for fi, fj, k, f in zip(ps["firm_i"], ps["firm_j"], rej, F):
        for firm in (fi, fj):
            score[firm][0] += int(k)
            score[firm][1] += float(f)
    worst_cartel = min(tuple(score[c]) for c in cartel_names)
    best_other = max(tuple(score[f]) for f in firms if f not in cartel_names)
    return int(worst_cartel > best_other)


def a1_rank1(pair_stats_run_spec_T: pd.DataFrame, cartel_names, N: int = 6) -> int:
    """1 iff the cartel pair has the strictly largest |r| of the N(N-1)/2 pairs (empty r counts 0)."""
    ps = pair_stats_run_spec_T
    _check_pairs(ps, cartel_names, N)
    absr = np.abs(np.nan_to_num(pd.to_numeric(ps["r"]).to_numpy(dtype=float), nan=0.0))
    is_c = _is_cartel_row(ps, cartel_names).to_numpy()
    return int(absr[is_c][0] > absr[~is_c].max())


def verdict_rows(pair_stats: pd.DataFrame) -> tuple[int | None, pd.DataFrame]:
    """(usable_T, rows) for the per-run verdicts: spec "tag" at the primary usable_T (T - burn_in), or
    the largest usable_T reached when the run has no primary rows; (None, empty) if it reached none."""
    tag = pair_stats[pair_stats["spec"] == VERDICT_SPEC]
    if tag.empty:
        return None, tag
    prim = tag[pd.to_numeric(tag["primary"]) == 1]
    u = int(prim["usable_T"].iloc[0]) if len(prim) else int(pd.to_numeric(tag["usable_T"]).max())
    return u, tag[pd.to_numeric(tag["usable_T"]) == u]


# ------------------------------------------------------------------ run selection

def _utc(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


def _rank(item: tuple[Path, dict]):
    d, meta = item
    finished, started = _utc(meta.get("finished_utc")), _utc(meta.get("started_utc"))
    return (finished is not None, finished or started or _EPOCH, d.name)


def select_run_dirs(results_root) -> tuple[dict[str, Path], list[dict]]:
    """Experiment run directories directly under ``results_root``, one per run_id ``<condition>_s<seed>``.

    Only immediate subdirectories holding a ``run_meta.json`` are considered; a directory whose
    ``purpose`` is not "experiment" is skipped. Per run_id the directory with the latest non-null
    ``finished_utc`` wins (the latest ``started_utc`` if none finished); the others are skipped, never
    merged. Returns (selected {run_id: dir}, skipped [{"dir", "reason"}])."""
    root = Path(results_root)
    if not root.is_dir():
        return {}, []
    groups: dict[str, list[tuple[Path, dict]]] = {}
    skipped: list[dict] = []
    for d in sorted(p for p in root.iterdir() if p.is_dir() and (p / "run_meta.json").is_file()):
        meta = io.read_run_meta(d)
        purpose = meta.get("purpose")
        if purpose != EXPERIMENT:
            skipped.append({"dir": str(d), "reason": f"purpose {purpose!r} is not {EXPERIMENT!r}"})
            continue
        groups.setdefault(f"{meta['condition']}_s{meta['seed']}", []).append((d, meta))
    selected: dict[str, Path] = {}
    for run_id in sorted(groups):
        members = groups[run_id]
        best = max(members, key=_rank)
        selected[run_id] = best[0]
        how = "latest finished_utc" if _rank(best)[0] else "latest started_utc, none finished"
        for d, _ in members:
            if d != best[0]:
                skipped.append({"dir": str(d), "reason": f"{run_id}: superseded by {best[0].name} ({how})"})
    return selected, skipped


# ------------------------------------------------------------------ one run

def _load(run_dir: Path) -> dict:
    meta = io.read_run_meta(run_dir)
    path = run_dir / "detector" / "pair_stats.csv"
    if path.is_file():
        ps = io.read_pair_stats(path)
    else:
        ps = detector.detect_run(run_dir, T=int(meta["T"]), N=len(meta["seat_to_firm"]),
                                 burn_in=int(meta["burn_in"]))
    return dict(dir=run_dir, meta=meta, pair_stats=ps, bids=io.read_bids(run_dir),
                truth=io.read_truth(run_dir))


def _scheme(meta: dict) -> str:
    return meta.get("scheme") or ("none" if meta["condition"] == "control" else meta["condition"])


def _finished(meta: dict) -> bool:
    return meta.get("finished_utc") is not None


def _pair_stats_complete(rec: dict) -> int:
    ps, meta = rec["pair_stats"], rec["meta"]
    if len(ps):
        return int(pd.to_numeric(ps["complete"]).min() == 1)
    return int(len(rec["bids"]) == int(meta["T"]) * len(meta["seat_to_firm"]))  # no usable_T reached


def _median(s: pd.Series) -> float | None:
    s = s.dropna()
    return float(s.median()) if len(s) else None


def _pair_eval(rec: dict) -> pd.DataFrame:
    meta, ps = rec["meta"], rec["pair_stats"].copy()
    names = fid.cartel_names(meta)
    ps["complete"] = (pd.to_numeric(ps["complete"]).astype("Int64") * int(_finished(meta))).astype("Int64")
    ps["condition"] = meta["condition"]
    ps["scheme"] = _scheme(meta)
    ps["seed"] = pd.array([int(meta["seed"])] * len(ps), dtype="Int64")
    ps["cartel_seats"] = ";".join(str(int(s)) for s in meta.get("cartel_seats") or [])
    ps["pair_class"] = [pair_class(fi, fj, names) for fi, fj in zip(ps["firm_i"], ps["firm_j"])]
    return ps[PAIR_EVAL_COLUMNS]


def _run_eval(run_id: str, rec: dict, control: dict | None) -> dict:
    meta, bids, truth = rec["meta"], rec["bids"], rec["truth"]
    burn_in = int(meta["burn_in"])
    scheme = _scheme(meta)
    names = fid.cartel_names(meta)
    row = {c: None for c in RUN_EVAL_COLUMNS}
    row.update(run_id=run_id, condition=meta["condition"], scheme=scheme, seed=int(meta["seed"]),
               run_dir=rec["dir"].name, complete=int(_finished(meta) and _pair_stats_complete(rec)),
               projects_completed=int(bids["project_id"].nunique()), realized_rule="n/a",
               n_missing_bids=int(bids["bid"].isna().sum()))
    post = bids[(bids["project_id"] > burn_in) & bids["bid"].notna()]
    y = post["bid"] / post["est"]
    row["mean_bid_over_est"] = float(y.mean()) if len(y) else None
    row["sd_bid_over_est"] = float(y.std(ddof=1)) if len(y) > 1 else None
    if not names:
        return row  # control: no cartel, no fidelity, profit or verdicts

    row["fidelity"] = fid.fidelity(bids, truth, names, scheme)
    if scheme in RULE_SCHEMES:
        rr = fid.realized_rule(bids, truth, names)
        row.update(realized_rule=rr["label"], b_own=rr["b_own"], b_partner=rr["b_partner"],
                   n_cover=rr["n_cover"])
        m = fid.cover_margins(bids, truth, names)
        row["median_margin_raw"] = _median(m["margin_raw"])
        row["median_margin_excess"] = _median(m["margin_excess"])
    if control is not None:
        cp, mp = fid.matched_profits(bids, truth, meta, control["bids"], control["truth"], control["meta"])
        row.update(cartel_profit=cp, matched_control_profit=mp, profit_delta=cp - mp)
    else:
        row["cartel_profit"] = fid.cartel_profit(bids, truth, names)

    u, vrows = verdict_rows(rec["pair_stats"])
    if u is not None:
        N = len(meta["seat_to_firm"])
        cart = vrows[_is_cartel_row(vrows, names)]
        row.update(verdict_usable_T=u, a1_rank1_by_abs_r=a1_rank1(vrows, names, N),
                   a3_top2=a3_top2(vrows, names, N), cartel_r=float(cart["r"].iloc[0]),
                   cartel_z=float(cart["z"].iloc[0]))
    return row


def _typed_run_eval(rows: list[dict]) -> pd.DataFrame:
    cols = {}
    for c in RUN_EVAL_COLUMNS:
        values = [r[c] for r in rows]
        if c in _RUN_EVAL_INT:
            cols[c] = pd.array([None if v is None else int(v) for v in values], dtype="Int64")
        elif c in _RUN_EVAL_STR:
            cols[c] = pd.Series(values, dtype=object)
        else:
            cols[c] = np.array([np.nan if v is None else float(v) for v in values], dtype="float64")
    return pd.DataFrame(cols, columns=RUN_EVAL_COLUMNS)


def _records(df: pd.DataFrame) -> list[dict]:
    out = []
    for rec in df.to_dict("records"):
        out.append({k: (None if v is None or v is pd.NA or (isinstance(v, float) and math.isnan(v)) else v)
                    for k, v in rec.items()})
    return out


# ------------------------------------------------------------------ evaluate

def evaluate(results_root, out_dir) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Evaluate every selected experiment run under ``results_root`` into ``out_dir``.

    Writes ``pair_eval.csv`` (``PAIR_EVAL_COLUMNS``; ``complete`` = pair_stats complete AND
    ``finished_utc`` set), ``run_eval.csv`` (``RUN_EVAL_COLUMNS``; incomplete runs included with
    complete 0) and ``excluded_runs.json``; refuses to overwrite any of them. Returns
    (pair_eval, run_eval)."""
    out_dir = Path(out_dir)
    existing = [n for n in OUTPUT_FILES if (out_dir / n).exists()]
    if existing:
        raise FileExistsError(f"{out_dir} already holds {existing}; evaluation never overwrites")
    selected, skipped = select_run_dirs(results_root)
    loaded = {run_id: _load(d) for run_id, d in selected.items()}
    order = sorted(loaded, key=lambda r: (loaded[r]["meta"]["condition"], int(loaded[r]["meta"]["seed"])))

    pair_frames, run_rows = [], []
    for run_id in order:
        rec = loaded[run_id]
        pair_frames.append(_pair_eval(rec))
        control = loaded.get(f"control_s{rec['meta']['seed']}")
        run_rows.append(_run_eval(run_id, rec, control if rec["meta"]["condition"] != "control" else None))
    pair_eval = (pd.concat(pair_frames, ignore_index=True) if pair_frames
                 else pd.DataFrame(columns=PAIR_EVAL_COLUMNS))
    run_eval = _typed_run_eval(run_rows)

    out_dir.mkdir(parents=True, exist_ok=True)
    io.write_csv(out_dir / "pair_eval.csv", _records(pair_eval), PAIR_EVAL_COLUMNS)
    io.write_csv(out_dir / "run_eval.csv", _records(run_eval), RUN_EVAL_COLUMNS)
    (out_dir / "excluded_runs.json").write_text(json.dumps(skipped, indent=2), encoding="utf-8", newline="\n")
    return pair_eval, run_eval


def filter_by_fidelity(pair_eval: pd.DataFrame, run_eval: pd.DataFrame, min_fidelity: float = 0.80
                       ) -> pd.DataFrame:
    """pair_eval rows of the scheme runs whose fidelity is > ``min_fidelity`` (A78), plus every control
    run's rows unchanged."""
    fidelity = pd.to_numeric(run_eval["fidelity"], errors="coerce")
    control = set(run_eval.loc[run_eval["condition"] == "control", "run_id"])
    faithful = set(run_eval.loc[(run_eval["condition"] != "control") & (fidelity > min_fidelity), "run_id"])
    return pair_eval[pair_eval["run_id"].isin(control | faithful)].copy()


# ------------------------------------------------------------------ CLI

def new_out_dir(root=EVALUATION_ROOT, now_utc: datetime | None = None) -> Path:
    """A fresh ``<root>/<yyyymmdd_HHMMSS>`` (suffixed ``_1``, ``_2``... if taken); never an existing one."""
    now_utc = now_utc or datetime.now(timezone.utc)
    base = Path(root) / f"{now_utc:%Y%m%d_%H%M%S}"
    base.parent.mkdir(parents=True, exist_ok=True)
    k = 0
    while True:
        path = base if k == 0 else base.with_name(f"{base.name}_{k}")
        try:
            path.mkdir(exist_ok=False)
            return path
        except FileExistsError:
            k += 1


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m src.procurement.evaluation",
                                 description="Join detector pair_stats with ground truth per run")
    ap.add_argument("--results-root", type=Path, default=Path("results/runs"))
    ap.add_argument("--out", type=Path, default=None,
                    help="output directory (default: a new results/evaluation/<UTC>)")
    args = ap.parse_args(argv)
    out = args.out if args.out is not None else new_out_dir()
    pair_eval, run_eval = evaluate(args.results_root, out)
    print(f"{len(run_eval)} runs, {len(pair_eval)} pair rows -> {out}")


if __name__ == "__main__":
    main()
