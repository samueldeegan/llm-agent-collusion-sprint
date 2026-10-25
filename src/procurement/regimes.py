"""Firm-season bidding regimes, pair-type A3 rates and slope dispersion (round-2 ablation report).

Pure functions on the ``io.read_bids`` / ``io.read_truth`` / ``pair_eval`` frames. ``seed`` is parsed
from the run_id (trailing ``_s<n>``)."""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

SENTINEL_RATIO = 1.5
PASS_MIN = 0.15
FAT_MIN = 0.05
SHADE_MIN = 0.25
RAZOR_MAX = 0.012
SHADE_MIN_ROWS = 8
SLOPE_MIN_ROWS = 5
REGIME_ORDER = ("passer", "fat", "shade", "razor", "moderate")
REGIME_COLUMNS = ["arm", "condition", "run_id", "seed", "firm_id", "seat", "pass_rate", "margin_med",
                  "margin_iqr", "margin_when_leading", "margin_when_trailing", "shade_slope", "wins",
                  "profit", "regime"]
HALVES = (("11-60", 0, 50), ("61-110", 50, 100))  # (label, offset from burn_in, end offset)
DISPERSION_COLUMNS = ["arm", "half", "n_runs", "mean_sd_cap_slope", "mean_sd_dist_slope"]


def _seed_of(run_id: str) -> float:
    m = re.search(r"_s(\d+)$", str(run_id))
    return int(m.group(1)) if m else np.nan


def _merged(bids: pd.DataFrame, truth: pd.DataFrame) -> pd.DataFrame:
    keys = ["run_id", "project_id", "firm_id"]
    tcols = keys + [c for c in ("seat", "cost") if c in truth.columns]
    df = bids.merge(truth[tcols], on=keys, how="left")
    # leading / min rival cost come from all firms' TRUTH costs per project, not from the bid rows present
    srt = truth.groupby(["run_id", "project_id"])["cost"].apply(lambda c: np.sort(c.to_numpy()))
    lo = srt.map(lambda a: a[0]).rename("min_cost")
    second = srt.map(lambda a: a[1] if len(a) > 1 else np.nan).rename("second_cost")
    df = df.merge(pd.concat([lo, second], axis=1).reset_index(), on=["run_id", "project_id"], how="left")
    df["leading"] = df["cost"] <= df["min_cost"]
    # min rival cost: second-lowest cost when own cost is the minimum, else the overall minimum
    df["rival_min"] = np.where(df["leading"], df["second_cost"], df["min_cost"])
    df["margin"] = (df["bid"] - df["cost"]) / df["est"]
    df["gap"] = (df["rival_min"] - df["cost"]) / df["est"]
    df["sentinel"] = df["bid"].isna() | (df["bid"] > SENTINEL_RATIO * df["est"])
    return df


def _ols(y: np.ndarray, X: np.ndarray) -> np.ndarray:
    return np.linalg.lstsq(X, y, rcond=None)[0]


def _classify(pass_rate, margin_med, shade_slope) -> str:
    if pass_rate >= PASS_MIN:
        return "passer"
    if margin_med >= FAT_MIN:
        return "fat"
    if shade_slope >= SHADE_MIN:
        return "shade"
    if margin_med <= RAZOR_MAX:
        return "razor"
    return "moderate"


def firm_season_table(bids: pd.DataFrame, truth: pd.DataFrame, burn_in: int, arm: str,
                      condition: str) -> pd.DataFrame:
    df = _merged(bids, truth)
    rows = []
    for (run_id, firm_id), g in df.groupby(["run_id", "firm_id"], sort=True):
        won = g[g["won"] == 1]
        post = g[g["project_id"] > burn_in]
        ok = post[~post["sentinel"]]
        pass_rate = float(post["sentinel"].mean()) if len(post) else np.nan
        m = ok["margin"]
        margin_med = float(m.median()) if len(m) else np.nan
        margin_iqr = float(m.quantile(0.75) - m.quantile(0.25)) if len(m) else np.nan
        lead, trail = ok[ok["leading"]]["margin"], ok[~ok["leading"]]["margin"]
        sh = ok[ok["gap"] > 0]
        if len(sh) >= SHADE_MIN_ROWS:
            slope = float(_ols(sh["margin"].to_numpy(), np.column_stack([np.ones(len(sh)), sh["gap"]]))[1])
        else:
            slope = np.nan
        seat = g["seat"].iloc[0] if g["seat"].notna().any() else np.nan
        rows.append(dict(
            arm=arm, condition=condition, run_id=run_id, seed=_seed_of(run_id), firm_id=firm_id, seat=seat,
            pass_rate=pass_rate, margin_med=margin_med, margin_iqr=margin_iqr,
            margin_when_leading=float(lead.median()) if len(lead) else np.nan,
            margin_when_trailing=float(trail.median()) if len(trail) else np.nan,
            shade_slope=slope, wins=int(len(won)), profit=float((won["bid"] - won["cost"]).sum()),
            regime=_classify(pass_rate, margin_med, slope)))
    return pd.DataFrame(rows, columns=REGIME_COLUMNS)


def regime_counts(table: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for arm in sorted(table["arm"].unique()):
        sub = table[table["arm"] == arm]
        for reg in REGIME_ORDER:
            rows.append(dict(arm=arm, regime=reg, n_firm_seasons=int((sub["regime"] == reg).sum())))
    return pd.DataFrame(rows, columns=["arm", "regime", "n_firm_seasons"])


def a3_by_pair_type(pair_eval: pd.DataFrame, table: pd.DataFrame, spec: str = "tag_filtered") -> pd.DataFrame:
    cols = ["arm", "regime_i", "regime_j", "n_pairs", "n_rejected", "rate"]
    pe = pair_eval[(pair_eval["spec"] == spec) & (pd.to_numeric(pair_eval["primary"]) == 1)
                   & (pd.to_numeric(pair_eval["complete"]) == 1) & (pair_eval["condition"] == "control")]
    reg = table.set_index(["run_id", "firm_id"])["regime"]
    out = []
    for r in pe.itertuples(index=False):
        ri, rj = reg.get((r.run_id, r.firm_i)), reg.get((r.run_id, r.firm_j))
        if ri is None or rj is None:
            continue
        a, b = sorted((ri, rj))
        if pd.isna(r.a3_flag):
            continue
        out.append(dict(arm=r.arm, regime_i=a, regime_j=b, flag=int(r.a3_flag == 1)))
    if not out:
        return pd.DataFrame(columns=cols)
    d = pd.DataFrame(out).groupby(["arm", "regime_i", "regime_j"], as_index=False).agg(
        n_pairs=("flag", "size"), n_rejected=("flag", "sum"))
    d["rate"] = d["n_rejected"] / d["n_pairs"]
    return d[cols]


def _sd(v) -> float:
    v = np.array([x for x in v if not np.isnan(x)])
    return float(np.std(v, ddof=1)) if len(v) >= 2 else np.nan


def slope_dispersion(bids: pd.DataFrame, truth: pd.DataFrame, burn_in: int, arm: str, run_id: str) -> list[dict]:
    df = _merged(bids[bids["run_id"] == run_id], truth[truth["run_id"] == run_id])
    df = df[~df["sentinel"]]
    out = []
    for label, lo, hi in HALVES:
        half = df[(df["project_id"] > burn_in + lo) & (df["project_id"] <= burn_in + hi)]
        caps, dists = [], []
        for _, g in half.groupby("firm_id"):
            if len(g) < SLOPE_MIN_ROWS:
                caps.append(np.nan)
                dists.append(np.nan)
                continue
            X = np.column_stack([np.ones(len(g)), np.log1p(g["dist"]), g["cap"]])
            b = _ols(g["margin"].to_numpy(), X)
            # lstsq returns a minimum-norm slope (0.0) for an unidentified regressor; report NaN instead
            dists.append(b[1] if np.ptp(X[:, 1]) > 0 else np.nan)
            caps.append(b[2] if np.ptp(X[:, 2]) > 0 else np.nan)
        out.append(dict(arm=arm, run_id=run_id, half=label, sd_cap_slope=_sd(caps), sd_dist_slope=_sd(dists)))
    return out


def slope_dispersion_by_arm(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=DISPERSION_COLUMNS)
    d = pd.DataFrame(rows)
    g = d.groupby(["arm", "half"], as_index=False).agg(
        n_runs=("run_id", "nunique"), mean_sd_cap_slope=("sd_cap_slope", "mean"),
        mean_sd_dist_slope=("sd_dist_slope", "mean"))
    return g[DISPERSION_COLUMNS]
