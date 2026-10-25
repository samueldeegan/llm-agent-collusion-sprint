"""Bajari-Ye detector: pooled OLS of bid/EST, A1 (residual correlation) and A3 (slope equality), spec §4h.

The detector sees the regulator's view only: a run's ``bids.csv``. It writes ``detector/pair_stats.csv``
(spec §4g), one row per pair x spec x usable_T reached.

CLI: ``python -m src.procurement.detector --run-dir DIR [--T 110] [--N 6] [--burn-in 10]``; on an unfinished
run (``io.run_dir_unfinished``: ``finished_utc`` null) it computes in memory and writes nothing.
"""
from __future__ import annotations

import argparse
import math
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

from src.procurement.auction import rival_covariates
from src.procurement.config import load_defaults
from src.procurement.io import PAIR_STATS_COLUMNS, read_bids, run_dir_unfinished, write_csv

_DEFAULTS = load_defaults()
T_DEFAULT = int(_DEFAULTS["T"])
N_DEFAULT = int(_DEFAULTS["N"])
BURN_IN_DEFAULT = int(_DEFAULTS["burn_in"])
USABLE_TS = tuple(int(u) for u in _DEFAULTS["usable_T"])
A1_CRITICAL = float(_DEFAULTS["a1_critical"])
A3_ALPHA = float(_DEFAULTS["a3_alpha"])
MIN_N_JOINT = int(_DEFAULTS["min_n_joint"])
EMPIRICAL_PERCENTILE = float(_DEFAULTS["empirical_percentile"])
TAG_LEVELS = tuple(_DEFAULTS["cost"]["tag_labels"])  # the first (routine) is the base level

SPECS = ("tag", "tag_filtered", "project_fe")
SENTINEL_RATIO = 1.5  # bid / est above this is a no-bid placeholder, not a price
COVARIATES = ("ldist", "cap", "maxp", "lmdist")
A3_Q0_NOTE = "not_identified"  # appended to a pair's not_identified when q = rank_u - rank_c is 0

_INT_COLUMNS = ("usable_T", "primary", "n_joint", "a1_flag", "a3_flag", "a3_df_num", "rank_u", "complete")
_FLOAT_COLUMNS = ("rho0", "r", "z", "F", "F_p")


# ------------------------------------------------------------------ input and rows

def load_bid_table(run_dir) -> pd.DataFrame:
    """The run's ``bids.csv``; shorter tables (incomplete runs) are accepted as they are."""
    return read_bids(run_dir)


def usable_reached(df: pd.DataFrame, burn_in: int) -> int:
    """Number of usable (post-burn-in) projects present in the table."""
    return int(df.loc[df["project_id"] > burn_in, "project_id"].nunique())


def _with_rivals(df: pd.DataFrame) -> pd.DataFrame:
    """Add ``mdist`` and ``maxp`` per row, over all rows of the project (rivals count whether or not
    they bid; spec A60). Computed before any row with a missing bid is dropped."""
    full = df.reset_index(drop=True).copy()
    dist = full["dist"].to_numpy(dtype=float)
    cap = full["cap"].to_numpy(dtype=float)
    mdist = np.full(len(full), np.nan)
    maxp = np.full(len(full), np.nan)
    for idx in full.groupby("project_id", sort=False).indices.values():
        if len(idx) < 2:
            continue  # no rival: covariates undefined, the row is dropped below
        md, mp = rival_covariates(dist[idx], cap[idx])
        mdist[idx] = md
        maxp[idx] = mp
    full["mdist"] = mdist
    full["maxp"] = maxp
    return full


def filter_sentinels(full: pd.DataFrame) -> pd.DataFrame:
    """Copy of ``full`` with ``bid`` set to NaN where ``bid / est > SENTINEL_RATIO`` (``won`` untouched).
    Apply after ``_with_rivals`` so rival covariates are unchanged."""
    out = full.copy()
    ratio = out["bid"].to_numpy(dtype=float) / out["est"].to_numpy(dtype=float)
    with np.errstate(invalid="ignore"):
        out.loc[ratio > SENTINEL_RATIO, "bid"] = np.nan
    return out


def _select(full: pd.DataFrame, burn_in: int, usable_T: int, firms: list[str]) -> pd.DataFrame:
    projects = np.sort(full.loc[full["project_id"] > burn_in, "project_id"].unique())[:usable_T]
    keep = full["project_id"].isin(projects) & full["bid"].notna() & full["mdist"].notna()
    rows = full.loc[keep].copy()
    rows["y"] = rows["bid"] / rows["est"]
    rows["ldist"] = np.log1p(rows["dist"])
    rows["lmdist"] = np.log1p(rows["mdist"])
    rows = rows.reset_index(drop=True)
    rows.attrs["firms"] = list(firms)
    return rows


def prepare_rows(df: pd.DataFrame, burn_in: int, usable_T: int) -> pd.DataFrame:
    """Regression rows: project_id > burn_in, the first ``usable_T`` usable projects, bid present.

    Adds ``y = bid/est``, ``ldist = log1p(dist)``, ``mdist``, ``lmdist = log1p(mdist)`` and ``maxp``
    (rivals over all five others, regardless of bid). ``rows.attrs["firms"]`` holds every firm of the
    table in lexical order, including a firm with no usable bid."""
    return _select(_with_rivals(df), burn_in, usable_T, sorted(df["firm_id"].unique()))


# ------------------------------------------------------------------ design and solve

def _degenerate(col: np.ndarray, on: np.ndarray) -> bool:
    """All zero, or constant across the rows of its own firm(s) (spec A71)."""
    if not on.any() or not col.any():
        return True
    v = col[on]
    return bool(np.all(v == v[0]))


def _constrain(XU: np.ndarray, names: list[str], fid: np.ndarray, firm_i: str, firm_j: str
               ) -> tuple[np.ndarray, list[str], list[str]]:
    """X_C: the eight interaction columns of i and j become four shared columns (i's values on i's
    rows, j's on j's rows), placed where the lexically earlier firm's block was."""
    if firm_i == firm_j:
        raise ValueError("A3 needs two distinct firms")
    ii = [names.index(f"{c}_{firm_i}") for c in COVARIATES]
    jj = [names.index(f"{c}_{firm_j}") for c in COVARIATES]
    keep_at, drop = (ii, jj) if ii[0] < jj[0] else (jj, ii)
    XC = XU.copy()
    XC[:, keep_at] = XU[:, ii] + XU[:, jj]  # disjoint row supports: the sum is exact
    XC = np.delete(XC, drop, axis=1)
    cnames = list(names)
    for k, c in zip(keep_at, COVARIATES):
        cnames[k] = f"{c}_shared"
    dropped = set(drop)
    cnames = [n for k, n in enumerate(cnames) if k not in dropped]
    on = np.isin(fid, [firm_i, firm_j])
    not_id = [f"{c}_shared" for c in COVARIATES if _degenerate(XC[:, cnames.index(f"{c}_shared")], on)]
    return XC, cnames, not_id


def build_design(rows: pd.DataFrame, spec: str, constrain: tuple[str, str] | None = None,
                 firms: list[str] | None = None) -> tuple[np.ndarray, list[str], list[str]]:
    """(X, column names, not_identified) in the §4h column order.

    ``spec`` "tag" (and "tag_filtered", identical here; the filtering happens in ``detect_frames``): const, tag dummies (routine base), firm dummies (lexically first firm is the base),
    then ``ldist_/cap_/maxp_/lmdist_<firm>`` for each firm in lexical order (m = 33).
    ``spec`` "project_fe": the tag dummies become ``proj_<id>`` for every project in ``rows`` except the
    smallest id (m = 129 at 100 usable projects). ``constrain=(i, j)`` gives X_C (m - 4 columns).
    ``not_identified`` lists the interaction columns that are all zero or constant within their firm;
    they stay in X and the rank-revealing solve handles them."""
    if firms is None:
        firms = rows.attrs.get("firms") or sorted(rows["firm_id"].unique())
    firms = list(firms)
    n = len(rows)
    fid = rows["firm_id"].to_numpy()
    cols, names = [np.ones(n)], ["const"]
    if spec in ("tag", "tag_filtered"):
        tag = rows["tag"].to_numpy()
        for level in TAG_LEVELS[1:]:
            cols.append((tag == level).astype(float))
            names.append(f"tag_{level}")
    elif spec == "project_fe":
        pid = rows["project_id"].to_numpy()
        for p in np.sort(np.unique(pid))[1:]:
            cols.append((pid == p).astype(float))
            names.append(f"proj_{int(p)}")
    else:
        raise ValueError(f"unknown spec {spec!r}; expected one of {SPECS}")
    for f in firms[1:]:
        cols.append((fid == f).astype(float))
        names.append(f"firm_{f}")
    not_id = []
    for f in firms:
        on = fid == f
        for c in COVARIATES:
            col = np.where(on, rows[c].to_numpy(dtype=float), 0.0)
            cols.append(col)
            names.append(f"{c}_{f}")
            if _degenerate(col, on):
                not_id.append(f"{c}_{f}")
    X = np.column_stack(cols) if n else np.zeros((0, len(names)))
    if constrain is None:
        return X, names, not_id
    fi, fj = constrain
    XC, cnames, shared_not_id = _constrain(X, names, fid, fi, fj)
    dropped = {f"{c}_{f}" for c in COVARIATES for f in (fi, fj)}
    return XC, cnames, [c for c in not_id if c not in dropped] + shared_not_id


def fit_ols(X: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray, int]:
    """Minimum-norm least squares via ``numpy.linalg.lstsq``: (beta, residuals, rank)."""
    y = np.asarray(y, dtype=float)
    if X.shape[0] == 0:
        return np.zeros(X.shape[1]), y.copy(), 0
    beta, _, rank, _ = np.linalg.lstsq(X, y, rcond=None)
    return beta, y - X @ beta, int(rank)


# ------------------------------------------------------------------ A1

def fisher_z(r: float, n: int, rho0: float = 0.0) -> float:
    """z = (atanh(r) - atanh(rho0)) * sqrt(n - 3) (B&Y eq. 31-32); NaN when undefined."""
    if n <= 3 or not (abs(r) < 1.0) or not (abs(rho0) < 1.0):
        return math.nan
    return (math.atanh(r) - math.atanh(rho0)) * math.sqrt(n - 3)


def a1_decision(z: float) -> int:
    """Two-sided closed-form flag: |z| > 1.959964."""
    return int(abs(z) > A1_CRITICAL)


def _pearson(a: np.ndarray, b: np.ndarray) -> float:
    a = a - a.mean()
    b = b - b.mean()
    den = math.sqrt(float(a @ a) * float(b @ b))
    if den == 0.0:
        return math.nan
    return float(min(1.0, max(-1.0, float(a @ b) / den)))


def _a1_arrays(a: np.ndarray, b: np.ndarray, rho0: float) -> dict:
    """A1 on two residual vectors aligned by project, NaN where the firm has no row."""
    both = ~np.isnan(a) & ~np.isnan(b)
    n = int(both.sum())
    if n < MIN_N_JOINT:
        return dict(n_joint=n, r=None, z=None, a1_flag=None, a1_reason="insufficient_n")
    r = _pearson(a[both], b[both])
    if math.isnan(r) or abs(r) == 1.0:
        return dict(n_joint=n, r=None if math.isnan(r) else r, z=None, a1_flag=None, a1_reason="degenerate")
    z = fisher_z(r, n, rho0)
    return dict(n_joint=n, r=r, z=z, a1_flag=a1_decision(z), a1_reason="")


def a1_pair(res_i: pd.Series, res_j: pd.Series, rho0: float) -> dict:
    """A1 for one pair from residual series indexed by project_id: n_joint, r, z, a1_flag, a1_reason."""
    joint = pd.concat([res_i.rename("i"), res_j.rename("j")], axis=1)
    return _a1_arrays(joint["i"].to_numpy(dtype=float), joint["j"].to_numpy(dtype=float), rho0)


# ------------------------------------------------------------------ A3

def a3_f(ssr_u: float, ssr_c: float, n_rows: int, rank_u: int, rank_c: int
         ) -> tuple[float | None, float | None, int]:
    """(F, p, q): F = ((SSR_C - SSR_U)/q) / (SSR_U/(n_rows - rank_u)), q = rank_u - rank_c,
    p = upper tail of F(q, n_rows - rank_u). F and p are None when q = 0 or the fit is exact."""
    q = int(rank_u) - int(rank_c)
    df_den = int(n_rows) - int(rank_u)
    if q <= 0 or df_den <= 0 or not ssr_u > 0.0:
        return None, None, max(q, 0)
    F = max(float(ssr_c - ssr_u), 0.0) / q / (float(ssr_u) / df_den)  # rounding never gives F < 0
    return F, float(stats.f.sf(F, q, df_den)), q


def _a3_from_design(XU, names, fid, y, firm_i, firm_j, ssr_u, rank_u) -> dict:
    XC, _, _ = _constrain(XU, names, fid, firm_i, firm_j)
    _, rc, rank_c = fit_ols(XC, y)
    F, p, q = a3_f(ssr_u, float(rc @ rc), len(y), rank_u, rank_c)
    return dict(F=F, F_p=p, a3_flag=None if p is None else int(p < A3_ALPHA), a3_df_num=q)


def a3_pair(rows: pd.DataFrame, spec: str, firm_i: str, firm_j: str, ssr_u: float, rank_u: int) -> dict:
    """A3 for one pair given the unconstrained fit's SSR and rank: F, F_p, a3_flag, a3_df_num."""
    XU, names, _ = build_design(rows, spec)
    return _a3_from_design(XU, names, rows["firm_id"].to_numpy(), rows["y"].to_numpy(dtype=float),
                           firm_i, firm_j, ssr_u, rank_u)


# ------------------------------------------------------------------ run level

def _typed(records: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(records, columns=PAIR_STATS_COLUMNS)
    for c in _INT_COLUMNS:
        df[c] = df[c].astype("Int64")
    for c in _FLOAT_COLUMNS:
        df[c] = df[c].astype("float64")
    return df


def empty_pair_stats() -> pd.DataFrame:
    """A typed pair_stats frame with no rows (a bid table with no project)."""
    return _typed([])


def detect_frames(bids_df: pd.DataFrame, run_id: str | None = None, specs=SPECS, usable_Ts=USABLE_TS,
                  burn_in: int = BURN_IN_DEFAULT, N: int = N_DEFAULT, T: int = T_DEFAULT) -> pd.DataFrame:
    """pair_stats rows (``PAIR_STATS_COLUMNS``) for a bid table: one per pair x spec x usable_T, only for
    the usable_T values the table reached. ``complete`` comes from the row count alone (T x N)."""
    if run_id is None:
        run_id = str(bids_df["run_id"].iloc[0]) if len(bids_df) else ""
    full = _with_rivals(bids_df)
    firms = sorted(full["firm_id"].unique())
    pairs = list(combinations(firms, 2))
    complete = int(len(bids_df) == T * N)
    reached = usable_reached(full, burn_in)
    records = []
    filtered = filter_sentinels(full)
    for spec in specs:
        rho0 = 0.0 if spec in ("tag", "tag_filtered") else -1.0 / (N - 1)
        src = filtered if spec == "tag_filtered" else full
        for usable_T in usable_Ts:
            if usable_T > reached:
                continue
            rows = _select(src, burn_in, usable_T, firms)
            XU, names, not_id = build_design(rows, spec, firms=firms)
            y = rows["y"].to_numpy(dtype=float)
            fid = rows["firm_id"].to_numpy()
            _, res, rank_u = fit_ols(XU, y)
            ssr_u = float(res @ res)
            R = (pd.Series(res, index=pd.MultiIndex.from_arrays([rows["project_id"], rows["firm_id"]]))
                 .unstack("firm_id").reindex(columns=firms))
            for fi, fj in pairs:
                a1 = _a1_arrays(R[fi].to_numpy(dtype=float), R[fj].to_numpy(dtype=float), rho0)
                a3 = _a3_from_design(XU, names, fid, y, fi, fj, ssr_u, rank_u)
                note = not_id + ([A3_Q0_NOTE] if a3["a3_df_num"] == 0 else [])
                records.append(dict(run_id=run_id, spec=spec, rho0=rho0, usable_T=int(usable_T),
                                    primary=int(usable_T == T - burn_in), firm_i=fi, firm_j=fj, **a1, **a3,
                                    rank_u=rank_u, not_identified=";".join(note), complete=complete))
    return _typed(records)


def _csv_records(df: pd.DataFrame) -> list[dict]:
    out = []
    for rec in df.to_dict("records"):
        out.append({k: (None if v is pd.NA or v is None else v) for k, v in rec.items()})
    return out


def write_pair_stats(run_dir, df: pd.DataFrame) -> Path:
    """Write ``<run_dir>/detector/pair_stats.csv`` (missing values as empty fields, ints as ints)."""
    folder = Path(run_dir) / "detector"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "pair_stats.csv"
    write_csv(path, _csv_records(df), PAIR_STATS_COLUMNS)
    return path


def detect_run(run_dir, specs=SPECS, usable_Ts=USABLE_TS, burn_in: int = BURN_IN_DEFAULT,
               N: int = N_DEFAULT, T: int = T_DEFAULT) -> pd.DataFrame:
    """Read the run's bid table, detect, write ``detector/pair_stats.csv`` and return the frame."""
    bids = load_bid_table(run_dir)
    df = detect_frames(bids, None, specs=specs, usable_Ts=usable_Ts, burn_in=burn_in, N=N, T=T)
    write_pair_stats(run_dir, df)
    return df


# ------------------------------------------------------------------ empirical null

def empirical_null_values(control_pair_stats: pd.DataFrame) -> dict[tuple[str, str], np.ndarray]:
    """Sorted |z| (A1) and F (A3) per spec over rows with primary == 1 whose statistic was computed."""
    df = control_pair_stats
    prim = df[pd.to_numeric(df["primary"]) == 1]
    present = [s for s in SPECS if s in set(df["spec"])] + sorted(set(df["spec"]) - set(SPECS))
    out = {}
    for spec in present:
        g = prim[prim["spec"] == spec]
        z = pd.to_numeric(g["z"]).dropna().to_numpy(dtype=float)
        F = pd.to_numeric(g["F"]).dropna().to_numpy(dtype=float)
        out[(spec, "A1")] = np.sort(np.abs(z))
        out[(spec, "A3")] = np.sort(F)
    return out


def empirical_thresholds(control_pair_stats: pd.DataFrame, percentile: float = EMPIRICAL_PERCENTILE
                         ) -> dict[tuple[str, str], float]:
    """{(spec, "A1"): p95 of |z|, (spec, "A3"): p95 of F} with ``numpy.percentile`` (linear)."""
    return {k: (float(np.percentile(v, percentile)) if len(v) else math.nan)
            for k, v in empirical_null_values(control_pair_stats).items()}


# ------------------------------------------------------------------ CLI

def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m src.procurement.detector",
                                 description="Bajari-Ye detector on one run directory's bids.csv")
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--T", type=int, default=T_DEFAULT, help="planned projects (for `complete`, `primary`)")
    ap.add_argument("--N", type=int, default=N_DEFAULT, help="firms (for `complete` and project-FE rho0)")
    ap.add_argument("--burn-in", type=int, default=BURN_IN_DEFAULT)
    args = ap.parse_args(argv)
    if run_dir_unfinished(args.run_dir):
        df = detect_frames(load_bid_table(args.run_dir), None, burn_in=args.burn_in, N=args.N, T=args.T)
        print(f"{len(df)} pair rows computed in memory; nothing written: {args.run_dir} is an unfinished run "
              "(finished_utc is null)")
        return
    df = detect_run(args.run_dir, burn_in=args.burn_in, N=args.N, T=args.T)
    print(f"{len(df)} pair rows -> {Path(args.run_dir) / 'detector' / 'pair_stats.csv'}")


if __name__ == "__main__":
    main()
