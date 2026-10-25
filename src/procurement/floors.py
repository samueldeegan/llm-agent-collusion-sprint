"""Synthetic floor bidders: rational bids built on real true-cost tables, run through the detector to
measure its false-positive rates (the reference lines of the round-2 report).

Note: ``dist``/``cap`` stay as in the real bids table. The CAP path is NOT recomputed from the new
winners; only ``won`` is recomputed.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from src.procurement import detector, io

RULES = ("smooth", "kinked_identical", "kinked_heterogeneous")
FLOOR_COLUMNS = ["rule", "n_runs", "n_pairs", "a1_rate", "a3_rate", "median_F"]
MARKUP = 0.01
NOISE_SD = 0.003
KINK_SPREAD = 0.05
KINK_DELTA = 0.5


def construct_bids(bids: pd.DataFrame, truth: pd.DataFrame, rule: str, rng: np.random.Generator
                   ) -> pd.DataFrame:
    """Copy of ``bids`` with ``bid`` replaced by the rule's rational bid and ``won`` recomputed
    (lowest bid in the project; ties go to the lowest seat). Draw order from ``rng``: rule-specific
    per-firm draws first (heterogeneous: all ``m`` then all ``delta``, firms in lexical order), then
    one N(0, 0.003) noise draw per row ordered by (project, seat)."""
    if rule not in RULES:
        raise ValueError(f"rule must be one of {RULES}, got {rule!r}")
    t = truth[["project_id", "firm_id", "seat", "cost", "eps"]]
    df = bids.reset_index(drop=True).copy()
    df["_row"] = np.arange(len(df))
    df = df.merge(t, on=["project_id", "firm_id"], how="left", validate="one_to_one")
    firms = sorted(df["firm_id"].unique())
    if rule == "kinked_heterogeneous":
        m_arr = rng.uniform(0.003, 0.02, size=len(firms))
        d_arr = rng.uniform(0.2, 1.0, size=len(firms))
    else:
        m_arr = np.full(len(firms), MARKUP)
        d_arr = np.full(len(firms), KINK_DELTA)
    m = df["firm_id"].map(dict(zip(firms, m_arr))).to_numpy(dtype=float)
    delta = df["firm_id"].map(dict(zip(firms, d_arr))).to_numpy(dtype=float)
    cost = df["cost"].to_numpy(dtype=float)
    est = df["est"].to_numpy(dtype=float)
    base = (1.0 + m) * cost
    if rule == "smooth":
        det = base
    else:
        rival_min = np.empty(len(df))
        shifted = df["cost"] - df["eps"] * df["est"]
        for idx in df.groupby("project_id", sort=False).indices.values():
            s = shifted.to_numpy()[idx]
            for k, i in enumerate(idx):
                rival_min[i] = np.min(np.delete(s, k))
        det = np.maximum(base, rival_min - delta * KINK_SPREAD * est)
    order = np.lexsort((df["seat"].to_numpy(), df["project_id"].to_numpy()))
    noise = np.empty(len(df))
    noise[order] = rng.normal(0.0, NOISE_SD, size=len(df))
    df["bid"] = det + est * noise
    df["won"] = 0
    win = df.sort_values(["project_id", "bid", "seat"]).groupby("project_id", sort=False).head(1)
    df.loc[win.index, "won"] = 1
    df = df.sort_values("_row")
    return df[list(bids.columns)].reset_index(drop=True)


def floor_rates(run_dirs: list[Path], burn_in: int = 10, usable_T: int = 100, seed: int = 7
                ) -> pd.DataFrame:
    """Per rule: the share of primary pair rows flagged by A1 and by A3 (spec ``tag``, closed
    thresholds) over all runs, and the median F. One shared rng, rules in ``RULES`` order."""
    rng = np.random.default_rng(seed)
    out = []
    for rule in RULES:
        frames = []
        for rd in run_dirs:
            bids, truth = io.read_bids(rd), io.read_truth(rd)
            new = construct_bids(bids, truth, rule, rng)
            ps = detector.detect_frames(new, run_id=str(bids["run_id"].iloc[0]), specs=("tag",),
                                        usable_Ts=(usable_T,), burn_in=burn_in)
            frames.append(ps[(ps["spec"] == "tag") & (ps["primary"] == 1)])
        rows = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        n = len(rows)
        out.append(dict(rule=rule, n_runs=len(run_dirs), n_pairs=n,
                        a1_rate=float(rows["a1_flag"].mean()) if n else float("nan"),
                        a3_rate=float(rows["a3_flag"].mean()) if n else float("nan"),
                        median_F=float(rows["F"].median()) if n else float("nan")))
    return pd.DataFrame(out, columns=FLOOR_COLUMNS)
