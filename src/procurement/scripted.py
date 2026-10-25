"""Scripted robustness rows (spec §4e "Scripted robustness rows are not run directories", §6.6).

Each row of ``SCRIPTED_ROWS`` is simulated in memory with ``synthetic.simulate_table`` (the scheme applied
in-loop, cartel seats (0, 1)) and detected with ``detector.detect_frames`` (tag spec, primary usable_T)
on seeds ``range(seeds)``. Nothing passes through run directories, ``run_meta.json`` or the evaluation
layer. The output, ``scripted_signatures.csv``, is what ``analysis.robustness_table`` copies.

CLI: ``python -m src.procurement.scripted --seeds 100 [--extra-margin M] [--out PATH]``. ``PATH`` ending
in ``.csv`` is the file; otherwise it is a directory that receives ``scripted_signatures.csv``. The
default is a new ``results/analysis/<UTC>/``. An existing file is never overwritten.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from src.procurement import evaluation, io
from src.procurement.config import CostParams
from src.procurement.detector import A3_ALPHA, detect_frames
from src.procurement.synthetic import simulate_table

# (name, margin, rule, serious_add, sigma_eps, scheme), the nine §6.6 scheme_signatures rows.
# margin/rule are None where the scheme has no cover bid (markup adds 0.10 EST; control has no cartel).
SCRIPTED_ROWS = [
    ("cover_m0.10", 0.10, "inflate", 0.0, 0.05, "cover"),
    ("cover_m0.15", 0.15, "inflate", 0.0, 0.05, "cover"),
    ("cover_m0.20", 0.20, "inflate", 0.0, 0.05, "cover"),
    ("cover_m0.10_serious+0.05", 0.10, "inflate", 0.05, 0.05, "cover"),
    ("cover_m0.10_sigma0.10", 0.10, "inflate", 0.0, 0.10, "cover"),
    ("cover_shadow", None, "shadow", 0.0, 0.05, "cover"),
    ("rotation_m0.10", 0.10, "inflate", 0.0, 0.05, "rotation"),
    ("markup", None, None, 0.0, 0.05, "markup"),
    ("control", None, None, 0.0, 0.05, None),
]
SCRIPTED_COLUMNS = ["row", "margin", "rule", "serious_add", "sigma_eps", "pair_class", "test", "n", "rate",
                    "mean_r", "top2", "rank1"]
OUTPUT_NAME = "scripted_signatures.csv"
ANALYSIS_ROOT = "results/analysis"
CARTEL_SEATS = (0, 1)
SPEC = "tag"
CLASSES = ("cartel", "mixed", "outsider")
DEFAULT_COST = CostParams(0.90, 0.08, 0.17, 0.05)


def summarize(frames: list[pd.DataFrame], cartels: list[tuple[str, str]]) -> dict:
    """Per-class A1/A3 rates and mean r, plus run-level top-2 and rank-1 shares.

    ``frames[k]`` is one run's pair_stats rows for one spec and usable_T; ``cartels[k]`` its two cartel
    firm names (for the control row, the names of seats (0, 1), which do not collude). A1 is flagged
    iff ``a1_flag == 1`` and A3 iff ``F_p < 0.05``; ``n`` counts every pair of the class, so a pair with
    an empty statistic counts as not flagged. ``mean_r`` is the mean of the non-empty r. Top-2 and
    rank-1 are ``evaluation.a3_top2`` / ``evaluation.a1_rank1``, the verdicts run_eval reports."""
    by_class = {c: [] for c in CLASSES}
    top2, rank1 = [], []
    for f, cartel in zip(frames, cartels):
        classes = [evaluation.pair_class(fi, fj, cartel) for fi, fj in zip(f["firm_i"], f["firm_j"])]
        g = f.assign(pair_class=classes)
        for c in CLASSES:
            by_class[c].append(g[g["pair_class"] == c])
        top2.append(evaluation.a3_top2(f, cartel))
        rank1.append(evaluation.a1_rank1(f, cartel))
    out = {"top2": float(np.mean(top2)), "rank1": float(np.mean(rank1))}
    for c, parts in by_class.items():
        g = pd.concat(parts, ignore_index=True)
        if g.empty:
            continue
        r = pd.to_numeric(g["r"]).dropna()
        out[c] = {"n": len(g),
                  "A1": float((pd.to_numeric(g["a1_flag"]) == 1).mean()),
                  "A3": float((pd.to_numeric(g["F_p"]) < A3_ALPHA).mean()),
                  "mean_r": float(r.mean()) if len(r) else None}
    return out


def _rows(extra_margin: float | None) -> list[tuple]:
    rows = list(SCRIPTED_ROWS)
    if extra_margin is not None:
        name = f"cover_m{extra_margin:.2f}"
        if name not in {r[0] for r in rows}:
            rows.append((name, float(extra_margin), "inflate", 0.0, 0.05, "cover"))
    return rows


def _cartel_names(truth: pd.DataFrame) -> tuple[str, str]:
    seat = truth.drop_duplicates("seat").set_index("seat")["firm_id"]
    return str(seat[CARTEL_SEATS[0]]), str(seat[CARTEL_SEATS[1]])


def run_scripted(seeds: int = 100, extra_margin: float | None = None, cost: CostParams = DEFAULT_COST,
                 T: int = 110, burn_in: int = 10, usable_T: int = 100) -> pd.DataFrame:
    """One row per (scripted row x pair_class x test in {A1, A3}) with ``SCRIPTED_COLUMNS``.

    Seeds ``range(seeds)``, cartel seats (0, 1), tag spec at ``usable_T``. Each row runs at its own
    ``sigma_eps`` (``cost`` supplies the other parameters), so the column states what was simulated.
    ``extra_margin=m`` appends the row ``cover_m<m:.2f>`` (inflate, margin m) unless that name exists."""
    out = []
    for name, margin, rule, serious_add, sigma_eps, scheme in _rows(extra_margin):
        c = cost if sigma_eps == cost.sigma_eps else replace(cost, sigma_eps=sigma_eps)
        frames, cartels = [], []
        for seed in range(seeds):
            bids, truth = simulate_table(seed, c, T, scheme, CARTEL_SEATS if scheme else None,
                                         margin=0.10 if margin is None else margin,
                                         rule=rule if rule in ("inflate", "shadow") else "inflate",
                                         serious_add=serious_add)
            frames.append(detect_frames(bids, str(bids["run_id"].iloc[0]), specs=(SPEC,), usable_Ts=(usable_T,),
                                        burn_in=burn_in, N=int(bids["firm_id"].nunique()), T=T))
            cartels.append(_cartel_names(truth))
        s = summarize(frames, cartels)
        for cls in CLASSES:
            for test in ("A1", "A3"):
                out.append(dict(row=name, margin=margin, rule=rule, serious_add=serious_add,
                                sigma_eps=sigma_eps, pair_class=cls, test=test, n=s[cls]["n"],
                                rate=s[cls][test], mean_r=s[cls]["mean_r"], top2=s["top2"],
                                rank1=s["rank1"]))
    df = pd.DataFrame(out, columns=SCRIPTED_COLUMNS)
    for col in ("margin", "serious_add", "sigma_eps", "rate", "mean_r", "top2", "rank1"):
        df[col] = df[col].astype("float64")
    df["n"] = df["n"].astype("int64")
    return df


def write_scripted(df: pd.DataFrame, path) -> Path:
    """Write ``df`` to ``path`` (missing values as empty fields); refuses an existing file."""
    path = Path(path)
    if path.exists():
        raise FileExistsError(f"{path} exists; scripted signatures are never overwritten")
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [{k: (None if isinstance(v, float) and np.isnan(v) else v) for k, v in r.items()}
               for r in df.to_dict("records")]
    io.write_csv(path, records, SCRIPTED_COLUMNS)
    return path


def main(argv=None) -> Path:
    ap = argparse.ArgumentParser(prog="python -m src.procurement.scripted",
                                 description="Scripted scheme signatures on synthetic bids (no API)")
    ap.add_argument("--seeds", type=int, default=100)
    ap.add_argument("--extra-margin", type=float, default=None,
                    help="add a cover-inflate row at this margin (e.g. the cover smoke's realized median)")
    ap.add_argument("--out", type=Path, default=None,
                    help="a .csv path, or a directory for scripted_signatures.csv "
                         "(default: a new results/analysis/<UTC>/)")
    args = ap.parse_args(argv)
    if args.out is None:
        path = evaluation.new_out_dir(ANALYSIS_ROOT) / OUTPUT_NAME
    elif args.out.suffix.lower() == ".csv":
        path = args.out
    else:
        path = args.out / OUTPUT_NAME
    if path.exists():  # before the (slow) simulation
        raise FileExistsError(f"{path} exists; scripted signatures are never overwritten")
    df = run_scripted(seeds=args.seeds, extra_margin=args.extra_margin)
    write_scripted(df, path)
    print(f"{df['row'].nunique()} scripted rows x {args.seeds} seeds -> {path}")
    return path


if __name__ == "__main__":
    main()
