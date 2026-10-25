"""Analysis: the paper's tables, the control-distribution figure and ``results/README.md`` (spec §4g
"Analysis output", §4h "Empirical-null thresholds" and "Outputs", A78).

``analyze(results_root, out_dir, scripted_csv)`` runs ``evaluation.evaluate`` itself (into
``<out_dir>/evaluation/``) and works from the frames it returns, writing into ``out_dir``:

- ``flag_rates.csv``: condition x spec x threshold (closed / empirical) x pair_class x test (A1 / A3 /
  both) on the primary rows of complete runs, with a run-clustered 95% interval on every row;
- ``empirical_null.csv``: the control null, one row per statistic value, with its 95th percentile;
- ``power_vs_T.csv``: tag spec, every usable_T reached, incomplete runs included;
- ``robustness.csv``: the project-FE rows of the flag-rate table plus ``scripted_signatures.csv`` verbatim;
- ``control_distributions.png``: |z| and F of control pairs with the scheme runs' cartel pairs marked;
- ``README.md``, also written to ``results_root.parent / "README.md"`` (``results/README.md``).

Reads: ``pair_stats.csv``, ``truth.csv``, ``bids.csv``, ``run_meta.json`` (through evaluation and the
detector), ``excluded_runs.json``, ``scripted_signatures.csv``, ``results/calibration.json``,
``results/STOP.json``, ``config/run_plan.json`` and ``config/frozen.json`` (when given; run selection and
the planned runs without a directory) and the existence of a run dir's ``STOP`` marker (stop reasons of
incomplete runs). ``STOP.json`` and ``calibration.json`` are read defensively: an unreadable file, or one
that is not a JSON object, is treated as absent and named under "Not computed". Never messages, calls,
transcripts, checkpoints or heartbeats.

CLI: ``python -m src.procurement.analysis --results-root results/runs [--out DIR] [--scripted-csv PATH]
[--include-synthetic] [--frozen config/frozen.json] [--plan config/run_plan.json]`` (relative paths resolve
against the repository root).
"""
from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from matplotlib.figure import Figure
from scipy import stats

from src.procurement import config, detector, evaluation, io

FLAG_RATES_COLUMNS = ["condition", "spec", "threshold", "pair_class", "test", "n_pairs", "n_flagged", "rate",
                      "ci_low", "ci_high", "n_runs", "in_sample"]
EMPIRICAL_NULL_COLUMNS = ["spec", "test", "run_id", "firm_i", "firm_j", "value", "percentile_95"]
POWER_COLUMNS = ["condition", "spec", "usable_T", "pair_class", "test", "n_pairs", "rate", "n_runs",
                 "top2_rate", "rank1_rate", "includes_incomplete"]
ROBUSTNESS_COLUMNS = ["source", "condition", "spec", "rho0", "threshold", "row", "margin", "rule",
                      "serious_add", "sigma_eps", "pair_class", "test", "n_pairs", "n_flagged", "n", "rate",
                      "ci_low", "ci_high", "n_runs", "in_sample", "mean_r", "top2", "rank1"]
SCRIPTED_NAME = "scripted_signatures.csv"
ANALYSIS_ROOT = "results/analysis"

CONDITION_ORDER = ("control", "cover", "rotation", "markup")
THRESHOLDS = ("closed", "empirical")
TESTS = ("A1", "A3", "both")
CLASSES = ("cartel", "mixed", "outsider")
PRIMARY_SPEC = evaluation.VERDICT_SPEC  # "tag"
FE_SPEC = "project_fe"
CI_Z = 1.96
FIDELITY_MIN = 0.80

_SURFACE, _INK, _INK2, _BARS = "#fcfcfb", "#0b0b0b", "#52514e", "#b5b4ae"
_MARK = {"cover": ("v", "#2a78d6"), "rotation": ("s", "#eb6834"), "markup": ("D", "#1baf7a")}
_MARK_OTHER = ("o", "#6250d6")


# ------------------------------------------------------------------ helpers

def _num(df: pd.DataFrame, col: str) -> pd.Series:
    return pd.to_numeric(df[col], errors="coerce")


def _primary_complete(pe: pd.DataFrame) -> pd.DataFrame:
    """Rows with ``primary == 1`` of runs with evaluation ``complete == 1``."""
    if pe.empty:
        return pe
    return pe[(_num(pe, "primary") == 1) & (_num(pe, "complete") == 1)]


def _ordered(values, order) -> list:
    present = set(values)
    return [v for v in order if v in present] + sorted(present - set(order))


def _isnan(v) -> bool:
    return v is None or v is pd.NA or (isinstance(v, (float, np.floating)) and math.isnan(float(v)))


def _records(df: pd.DataFrame) -> list[dict]:
    return [{k: (None if _isnan(v) else v) for k, v in r.items()} for r in df.to_dict("records")]


def _write(path: Path, df: pd.DataFrame, columns) -> None:
    io.write_csv(path, _records(df), columns)


def _cartel_of(run_rows: pd.DataFrame) -> tuple[str, str] | None:
    c = run_rows[run_rows["pair_class"] == "cartel"]
    if c.empty:
        return None
    return str(c["firm_i"].iloc[0]), str(c["firm_j"].iloc[0])


def _n_firms(rows: pd.DataFrame) -> int:
    return len(set(rows["firm_i"]) | set(rows["firm_j"]))


# ------------------------------------------------------------------ thresholds and flag rates

def control_thresholds(pair_eval: pd.DataFrame) -> dict[tuple[str, str], float]:
    """Empirical thresholds {(spec, "A1"|"A3"): p95} from complete control runs' primary rows only
    (``detector.empirical_thresholds``); {} when there is no complete control run."""
    if pair_eval.empty:
        return {}
    ctrl = _primary_complete(pair_eval[pair_eval["condition"] == "control"])
    if ctrl.empty:
        return {}
    return {k: v for k, v in detector.empirical_thresholds(ctrl).items() if not math.isnan(v)}


def _masks(g: pd.DataFrame, spec: str, threshold: str, thresholds: dict) -> dict | None:
    """{test: (statistic present, flagged)}; None when the empirical thresholds of ``spec`` are missing."""
    if threshold == "closed":
        a1, a3 = _num(g, "a1_flag"), _num(g, "a3_flag")
        a1p, a1f, a3p, a3f = a1.notna(), a1 == 1, a3.notna(), a3 == 1
    else:
        tz, tf = thresholds.get((spec, "A1")), thresholds.get((spec, "A3"))
        if tz is None or tf is None:
            return None
        z, F = _num(g, "z").abs(), _num(g, "F")
        a1p, a1f, a3p, a3f = z.notna(), z > tz, F.notna(), F > tf
    return {"A1": (a1p, a1f & a1p), "A3": (a3p, a3f & a3p), "both": (a1p & a3p, a1f & a3f & a1p & a3p)}


def _clustered(run_ids: pd.Series, present: pd.Series, flagged: pd.Series) -> tuple[int, float, float]:
    """(n_runs, ci_low, ci_high): per-run rates, mean +- 1.96 sd(ddof=1)/sqrt(n_runs), clipped to [0, 1];
    NaN bounds with fewer than two runs."""
    df = pd.DataFrame({"run": run_ids.to_numpy(), "p": present.to_numpy(), "f": flagged.to_numpy()})
    df = df[df["p"]]
    rates = df.groupby("run")["f"].mean().astype(float)
    n = len(rates)
    if n < 2:
        return n, math.nan, math.nan
    mean, half = float(rates.mean()), CI_Z * float(rates.std(ddof=1)) / math.sqrt(n)
    return n, max(0.0, mean - half), min(1.0, mean + half)


def flag_rates(pair_eval: pd.DataFrame, thresholds: dict) -> pd.DataFrame:
    """The flag-rate table (``FLAG_RATES_COLUMNS``) on primary rows of complete runs.

    Closed: ``a1_flag == 1`` / ``a3_flag == 1``; empirical: |z| > thresholds[(spec, "A1")] and
    F > thresholds[(spec, "A3")]; "both": both flagged under the same threshold type. ``n_pairs`` counts
    pairs with the needed statistic(s); ``rate = n_flagged / n_pairs``. Empirical rows exist only for the
    specs in ``thresholds``. Rows exist only for the pair classes a condition has. ``in_sample`` is 1
    exactly on the control rows at the empirical threshold: that threshold is the 95th percentile of
    these same pairs, so their rate is about 5% by construction and is not a false-positive rate."""
    prim = _primary_complete(pair_eval)
    rows = []
    if prim.empty:
        return pd.DataFrame(rows, columns=FLAG_RATES_COLUMNS)
    for cond in _ordered(prim["condition"], CONDITION_ORDER):
        pc = prim[prim["condition"] == cond]
        for spec in _ordered(pc["spec"], detector.SPECS):
            ps = pc[pc["spec"] == spec]
            for threshold in THRESHOLDS:
                for cls in CLASSES:
                    g = ps[ps["pair_class"] == cls]
                    masks = None if g.empty else _masks(g, spec, threshold, thresholds)
                    if masks is None:
                        continue
                    for test in TESTS:
                        present, flagged = masks[test]
                        n, k = int(present.sum()), int(flagged.sum())
                        n_runs, lo, hi = _clustered(g["run_id"], present, flagged)
                        rows.append(dict(condition=cond, spec=spec, threshold=threshold, pair_class=cls,
                                         test=test, n_pairs=n, n_flagged=k, rate=k / n if n else math.nan,
                                         ci_low=lo, ci_high=hi, n_runs=n_runs,
                                         in_sample=int(cond == "control" and threshold == "empirical")))
    return pd.DataFrame(rows, columns=FLAG_RATES_COLUMNS)


def empirical_null_table(control_pair_eval: pd.DataFrame) -> pd.DataFrame:
    """The control null in long form (``EMPIRICAL_NULL_COLUMNS``): |z| (A1) and F (A3) of every complete
    control run's primary rows, sorted by value, with the 95th percentile repeated on each row. Rows of
    other conditions or incomplete runs in the input are ignored."""
    rows = []
    thr = control_thresholds(control_pair_eval)
    if thr:
        ctrl = _primary_complete(control_pair_eval[control_pair_eval["condition"] == "control"])
        for spec in _ordered(ctrl["spec"], detector.SPECS):
            g = ctrl[ctrl["spec"] == spec]
            for test, values in (("A1", _num(g, "z").abs()), ("A3", _num(g, "F"))):
                if (spec, test) not in thr:
                    continue
                sub = g.assign(value=values)[values.notna()].sort_values(["value", "run_id", "firm_i", "firm_j"])
                for r in sub.itertuples(index=False):
                    rows.append(dict(spec=spec, test=test, run_id=r.run_id, firm_i=r.firm_i, firm_j=r.firm_j,
                                     value=float(r.value), percentile_95=thr[(spec, test)]))
    return pd.DataFrame(rows, columns=EMPIRICAL_NULL_COLUMNS)


# ------------------------------------------------------------------ power vs T and robustness

def _run_complete(pair_eval: pd.DataFrame, run_eval: pd.DataFrame | None) -> dict[str, int]:
    out = {}
    if run_eval is not None and len(run_eval):
        out = {str(r): int(c == 1) for r, c in zip(run_eval["run_id"], _num(run_eval, "complete"))}
    for r, g in pair_eval.groupby("run_id"):
        out.setdefault(str(r), int((_num(g, "complete") == 1).all()))
    return out


def power_vs_T_table(pair_eval: pd.DataFrame, run_eval: pd.DataFrame | None = None) -> pd.DataFrame:
    """Closed-threshold A1/A3 rates by condition x usable_T x pair_class (tag spec, ``POWER_COLUMNS``).

    Every run contributes at each usable_T it reached, incomplete runs included
    (``includes_incomplete`` = 1 when one of the contributing runs is incomplete). ``top2_rate`` and
    ``rank1_rate`` are the mean ``evaluation.a3_top2`` / ``a1_rank1`` over the condition's runs on that
    usable_T's rows; empty for control."""
    rows = []
    if pair_eval.empty:
        return pd.DataFrame(rows, columns=POWER_COLUMNS)
    complete = _run_complete(pair_eval, run_eval)
    tag = pair_eval[pair_eval["spec"] == PRIMARY_SPEC]
    usable = _num(tag, "usable_T")
    for cond in _ordered(tag["condition"], CONDITION_ORDER):
        for u in sorted(int(x) for x in usable[tag["condition"] == cond].dropna().unique()):
            g = tag[(tag["condition"] == cond) & (usable == u)]
            runs = sorted(g["run_id"].unique())
            incomplete = int(any(complete.get(str(r), 0) != 1 for r in runs))
            top2 = rank1 = math.nan
            if cond != "control":
                t2, r1 = [], []
                for _, gr in g.groupby("run_id"):
                    names = _cartel_of(gr)
                    if names is not None:
                        t2.append(evaluation.a3_top2(gr, names, _n_firms(gr)))
                        r1.append(evaluation.a1_rank1(gr, names, _n_firms(gr)))
                if t2:
                    top2, rank1 = float(np.mean(t2)), float(np.mean(r1))
            for cls in CLASSES:
                gc = g[g["pair_class"] == cls]
                if gc.empty:
                    continue
                for test, col in (("A1", "a1_flag"), ("A3", "a3_flag")):
                    flag = _num(gc, col)
                    n = int(flag.notna().sum())
                    rows.append(dict(condition=cond, spec=PRIMARY_SPEC, usable_T=u, pair_class=cls, test=test,
                                     n_pairs=n, rate=float((flag == 1).sum()) / n if n else math.nan,
                                     n_runs=len(runs), top2_rate=top2, rank1_rate=rank1,
                                     includes_incomplete=incomplete))
    return pd.DataFrame(rows, columns=POWER_COLUMNS)


def _rho0_by_condition(pair_eval: pd.DataFrame, spec: str) -> dict[str, object]:
    out = {}
    g = pair_eval[pair_eval["spec"] == spec] if len(pair_eval) else pair_eval
    for cond, gc in g.groupby("condition"):
        vals = sorted(set(float(v) for v in _num(gc, "rho0").dropna()))
        out[cond] = vals[0] if len(vals) == 1 else ";".join(repr(v) for v in vals)
    return out


def robustness_table(pair_eval: pd.DataFrame, scripted: pd.DataFrame | None,
                     thresholds: dict | None = None) -> pd.DataFrame:
    """``ROBUSTNESS_COLUMNS``: the project-FE rows of ``flag_rates(pair_eval, thresholds)`` (source
    "project_fe", with that spec's rho0, -0.2 at N = 6) followed by every row of ``scripted`` copied
    verbatim (source "scripted"). Columns a source does not have are empty."""
    fr = flag_rates(pair_eval, thresholds or {})
    rho0 = _rho0_by_condition(pair_eval, FE_SPEC)
    records = []
    for r in fr[fr["spec"] == FE_SPEC].to_dict("records"):
        records.append({"source": FE_SPEC, "rho0": rho0.get(r["condition"]), **r})
    if scripted is not None:
        for r in scripted.to_dict("records"):
            records.append({"source": "scripted", **r})
    return pd.DataFrame(records, columns=ROBUSTNESS_COLUMNS, dtype=object)


# ------------------------------------------------------------------ figure

def _df_den(ctrl_tag: pd.DataFrame) -> int | None:
    """Median over runs of n_rows - rank_u, with n_rows = sum over firms of the firm's largest n_joint
    (exact whenever each firm shares all its projects with a firm that never missed a bid)."""
    dfs = []
    for _, g in ctrl_tag.groupby("run_id"):
        nj = _num(g, "n_joint")
        firms = set(g["firm_i"]) | set(g["firm_j"])
        n_rows = sum(int(nj[(g["firm_i"] == f) | (g["firm_j"] == f)].max()) for f in firms)
        rank = _num(g, "rank_u").dropna()
        if len(rank):
            dfs.append(n_rows - int(rank.median()))
    return int(round(float(np.median(dfs)))) if dfs else None


def _df_exact(ctrl_tag: pd.DataFrame) -> bool:
    """True when no control primary row lost a bid (every n_joint equals its usable_T), i.e. the
    reconstructed n_rows of ``_df_den`` is the regression's row count."""
    return bool(len(ctrl_tag)) and bool((_num(ctrl_tag, "n_joint") == _num(ctrl_tag, "usable_T")).all())


def f_critical_label(q, df_den, exact: bool) -> str:
    """The name of the closed-form A3 reference line; marked approximate when bids are missing."""
    if exact:
        return f"F({q}, {df_den}) 5% critical value"
    return f"≈ F({q}, {df_den}) 5% critical value (df approximate: missing bids)"


def control_distribution_figure(control_pair_eval: pd.DataFrame, scheme_pair_eval: pd.DataFrame, out_png,
                                thresholds: dict | None = None) -> dict:
    """Two panels: control-pair |z| (A1) and F (A3), tag spec, primary rows of complete control runs.

    Each panel marks the empirical threshold and the closed-form one: |z| = 1.96, and the F(q, df) 5%
    critical value, where q (``a3_df_num``) and df (n_rows - rank_u) are medians over the complete control
    runs of each run's value; F(4, 567) at 600 rows and m = 33. pair_stats has no n_rows, so df is
    reconstructed by ``_df_den``: exact when no control bid is missing, otherwise approximate and the
    label says so (``F_label``, ``df_exact``). Each complete scheme run's cartel pair is marked (marker per
    condition). Returns ``z_values`` (|z|, sorted), ``F_values`` (sorted), ``marked``
    [(condition, run_id, z, F)] with signed z (plotted at |z|), and the reference lines
    (``z_threshold``, ``F_threshold``, ``F_critical``, ``F_label``, ``df_num``, ``df_den``, ``df_exact``)."""
    thresholds = control_thresholds(control_pair_eval) if thresholds is None else thresholds
    ctrl = _primary_complete(control_pair_eval)
    ctrl = ctrl[(ctrl["spec"] == PRIMARY_SPEC) & (ctrl["condition"] == "control")] if len(ctrl) else ctrl
    z_values = np.sort(_num(ctrl, "z").abs().dropna().to_numpy(dtype=float)) if len(ctrl) else np.array([])
    F_values = np.sort(_num(ctrl, "F").dropna().to_numpy(dtype=float)) if len(ctrl) else np.array([])
    df_den = _df_den(ctrl) if len(ctrl) else None
    q_runs = _num(ctrl, "a3_df_num").groupby(ctrl["run_id"]).median().dropna() if len(ctrl) else pd.Series()
    q = int(round(float(q_runs.median()))) if len(q_runs) else None
    F_crit = float(stats.f.isf(detector.A3_ALPHA, q, df_den)) if df_den and q else math.nan
    df_exact = _df_exact(ctrl)
    F_label = f_critical_label(q, df_den, df_exact)

    marked = []
    sch = _primary_complete(scheme_pair_eval)
    if len(sch):
        sch = sch[(sch["spec"] == PRIMARY_SPEC) & (sch["pair_class"] == "cartel") & (sch["condition"] != "control")]
        rank = {c: k for k, c in enumerate(CONDITION_ORDER)}
        sch = sch.assign(_k=sch["condition"].map(lambda c: rank.get(c, len(rank))))
        for r in sch.sort_values(["_k", "condition", "run_id"]).itertuples(index=False):
            marked.append((str(r.condition), str(r.run_id), float(r.z), float(r.F)))

    z_thr, F_thr = thresholds.get((PRIMARY_SPEC, "A1")), thresholds.get((PRIMARY_SPEC, "A3"))
    fig = Figure(figsize=(11, 5.2), layout="constrained", facecolor=_SURFACE)
    axes = fig.subplots(1, 2)
    panels = (
        (axes[0], z_values, [abs(m[2]) for m in marked], z_thr, detector.A1_CRITICAL,
         f"closed form |z| = {detector.A1_CRITICAL:.3f}", "A1: |z| of control pairs (tag spec)", "|z|"),
        (axes[1], F_values, [m[3] for m in marked], F_thr, F_crit,
         f"{F_label} = {F_crit:.3f}" if not math.isnan(F_crit) else "closed form n/a",
         "A3: F of control pairs (tag spec)", "F"),
    )
    for ax, values, marks, emp, closed, closed_label, title, xlabel in panels:
        ax.set_facecolor(_SURFACE)
        refs = [v for v in [emp, closed] if v is not None and not math.isnan(v)]
        hi = max([*values, *[m for m in marks if not math.isnan(m)], *refs, 1.0]) * 1.08
        bins = np.linspace(0.0, hi, 31)
        ymax = 1.0
        if len(values):
            counts, _, _ = ax.hist(values, bins=bins, color=_BARS, edgecolor=_SURFACE, linewidth=1.0,
                                   label=f"control pairs (n = {len(values)})")
            ymax = max(1.0, float(counts.max()))
        else:
            ax.text(0.5, 0.5, "no complete control run", transform=ax.transAxes, ha="center", color=_INK2)
        if emp is not None and not math.isnan(emp):
            ax.axvline(emp, color=_INK, linewidth=1.6, label=f"empirical 95th percentile = {emp:.3f}")
        if not math.isnan(closed):
            ax.axvline(closed, color=_INK2, linewidth=1.4, linestyle="--", label=closed_label)
        conds = _ordered([m[0] for m in marked], CONDITION_ORDER)
        for k, cond in enumerate(conds):
            xs = [v for m, v in zip(marked, marks) if m[0] == cond]
            shape, color = _MARK.get(cond, _MARK_OTHER)
            ax.scatter(xs, [ymax * (1.10 + 0.09 * k)] * len(xs), marker=shape, s=64, color=color,
                       edgecolors=_SURFACE, linewidths=1.5, zorder=3,
                       label=f"{cond} cartel pair ({len(xs)} run{'s' if len(xs) != 1 else ''})")
        ax.set_ylim(0, ymax * (1.25 + 0.09 * max(len(conds), 1)))
        ax.set_xlim(0, hi)
        ax.set_title(title, color=_INK, fontsize=11, loc="left")
        ax.set_xlabel(xlabel, color=_INK2)
        ax.set_ylabel("pairs", color=_INK2)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(_INK2)
        ax.tick_params(colors=_INK2, labelsize=9)
        ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.16), ncol=2, frameon=False, fontsize=8,
                  labelcolor=_INK)
    fig.savefig(out_png, dpi=150, facecolor=_SURFACE)
    return dict(z_values=z_values, F_values=F_values, marked=marked, z_threshold=z_thr, F_threshold=F_thr,
                F_critical=F_crit, F_label=F_label, df_num=q, df_den=df_den, df_exact=df_exact)


# ------------------------------------------------------------------ README

def _f(x, nd: int = 3) -> str:
    return "n/a" if _isnan(x) else f"{float(x):.{nd}f}"


def _md(c) -> str:
    return str(c).replace("|", r"\|")  # a literal pipe (|z|) would split the Markdown cell


def _table(headers: list[str], rows: list[list]) -> list[str]:
    out = ["| " + " | ".join(_md(h) for h in headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    out += ["| " + " | ".join(_md(c) for c in r) + " |" for r in rows]
    return out


def _cell(df: pd.DataFrame, **kw):
    if df is None or df.empty:
        return None
    m = np.ones(len(df), dtype=bool)
    for k, v in kw.items():
        m &= (df[k] == v).to_numpy()
    sub = df[m]
    return sub.iloc[0] if len(sub) else None


IN_SAMPLE_MARK = "†"
IN_SAMPLE_NOTE = (f"{IN_SAMPLE_MARK} in-sample: the empirical threshold is the 95th percentile of these same control "
                  "pairs, so this rate is about 5% by construction and is not a validated false-positive rate.")
CI_NOTE = ("With few runs (n_runs < 5) the interval is unreliable; with identical per-run rates it has zero "
           "width.")


def _rate_frac(row) -> str:
    if row is None:
        return "n/a"
    mark = IN_SAMPLE_MARK if "in_sample" in row and not _isnan(row["in_sample"]) and int(row["in_sample"]) else ""
    return f"{_f(row['rate'])} ({int(row['n_flagged'])}/{int(row['n_pairs'])}){mark}"


def _rate_ci(row) -> str:
    if row is None:
        return "n/a"
    if _isnan(row["ci_low"]):
        return f"{_f(row['rate'])} [n/a: {int(row['n_runs'])} run]"
    return f"{_f(row['rate'])} [{_f(row['ci_low'])}, {_f(row['ci_high'])}]"


def _flag_table(flags: pd.DataFrame, spec: str) -> list[str]:
    fr = flags[flags["spec"] == spec] if len(flags) else flags
    if fr.empty:
        return [f"No primary rows of complete runs for spec `{spec}`."]
    rows = []
    for cond in _ordered(fr["condition"], CONDITION_ORDER):
        for th in THRESHOLDS:
            for cls in CLASSES:
                cells = [_cell(fr, condition=cond, threshold=th, pair_class=cls, test=t) for t in TESTS]
                if all(c is None for c in cells):
                    continue
                n_runs = next(int(c["n_runs"]) for c in cells if c is not None)
                rows.append([cond, th, cls, *[_rate_frac(c) for c in cells], n_runs])
    out = _table(["condition", "threshold", "pair class", "A1", "A3", "both", "runs"], rows)
    if "in_sample" in fr and (_num(fr, "in_sample") == 1).any():
        out += ["", IN_SAMPLE_NOTE]
    return out


def stop_reason(run_dir, run_id: str, stop_json: dict | None) -> str:
    """Why an incomplete run stopped: the ``results/STOP.json`` reason when it lists the run (by run_id or
    directory name in ``runs_incomplete``), "stopped (STOP file)" when the run dir holds a ``STOP`` file,
    else "did not finish"."""
    run_dir = Path(run_dir)
    if stop_json:
        listed = stop_json.get("runs_incomplete")
        names = {str(x) for x in listed} if isinstance(listed, (list, tuple)) else set()
        names |= {Path(x).name for x in names}
        if run_id in names or run_dir.name in names:
            return str(stop_json.get("reason") or "stopped (STOP.json, no reason given)")
    if (run_dir / "STOP").is_file():
        return "stopped (STOP file)"
    return "did not finish"


def readme_numbers(*, pair_eval: pd.DataFrame, run_eval: pd.DataFrame, flags: pd.DataFrame,
                   null: pd.DataFrame, power: pd.DataFrame, robustness: pd.DataFrame, thresholds: dict,
                   figure: dict, excluded: list[dict], calibration: dict | None, scripted_given: bool,
                   results_root=None, analysis_dir=None, stop_reasons: dict | None = None,
                   missing_runs: list[dict] | None = None, input_notes: list[str] | None = None,
                   calibration_problem: str | None = None) -> str:
    """``results/README.md``: every number the paper needs (step-4 audit Part 4.2 step 9), 3 decimals.
    ``missing_runs`` ([{"run_id", "reason"}]) are runs without any directory (STOP.json
    ``runs_not_started``, plan rows never launched); ``input_notes`` go under "Not computed";
    ``calibration_problem`` replaces "not found" when ``calibration.json`` exists but is unusable."""
    notes: list[str] = list(input_notes or [])
    L: list[str] = ["# Results: Bajari-Ye procurement experiment", ""]
    L.append(f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC by `python -m src.procurement.analysis`"
             f" from `{results_root}`; analysis files in `{analysis_dir}`. Numbers to 3 decimals; "
             "`n/a` = not computable. Ground truth is the condition assignment; the detector read only "
             "`bids.csv`.")
    L.append("")

    # ---- run inventory
    L += ["## Run inventory", ""]
    re_ = run_eval if run_eval is not None else pd.DataFrame(columns=evaluation.RUN_EVAL_COLUMNS)
    if len(re_):
        rank = {c: k for k, c in enumerate(CONDITION_ORDER)}
        re_ = re_.iloc[sorted(range(len(re_)), key=lambda k: (rank.get(re_["condition"].iloc[k], len(rank)),
                                                              str(re_["condition"].iloc[k]),
                                                              int(re_["seed"].iloc[k])))].reset_index(drop=True)
    complete = _num(re_, "complete") == 1 if len(re_) else pd.Series(dtype=bool)
    if re_.empty:
        L.append("No experiment run was found.")
        notes.append("every run-based section: no experiment run under the results root")
    else:
        counts = []
        for cond in _ordered(re_["condition"], CONDITION_ORDER):
            m = re_["condition"] == cond
            counts.append([cond, int((m & complete).sum()), int((m & ~complete).sum())])
        L += _table(["condition", "complete runs", "incomplete runs"], counts)
        L.append("")
        rows = []
        for r, ok in zip(re_.to_dict("records"), complete):
            reason = (stop_reasons or {}).get(r["run_id"], "did not finish")
            status = ("complete" if ok else
                      f"incomplete: {reason} ({int(r['projects_completed'])} projects completed); "
                      "excluded from flag rates and the empirical null, included in power vs T")
            rows.append([r["run_id"], r["condition"], int(r["seed"]), f"`{r['run_dir']}`", status,
                         int(r["projects_completed"])])
        L += _table(["run", "condition", "seed", "directory", "status", "projects completed"], rows)
    L.append("")
    if missing_runs:
        L.append("Runs without a run directory:")
        L.append("")
        L += _table(["run", "reason"], [[m["run_id"], m["reason"]] for m in missing_runs])
        L.append("")
    if excluded:
        L.append("Directories not used (`excluded_runs.json`):")
        L.append("")
        L += [f"- `{e.get('dir')}`: {e.get('reason')}" for e in excluded]
    else:
        L.append("No directory was excluded (`excluded_runs.json` is empty).")
    L.append("")

    # ---- flag rates
    L += ["## Flag rates (tag spec)", "",
          "Primary rows (usable_T = T - burn-in) of complete runs; cells are rate (flagged/pairs). Closed: "
          f"|z| > {detector.A1_CRITICAL:.3f} (A1), F p-value < {detector.A3_ALPHA:.3f} (A3). Empirical: above "
          "the 95th percentile of the complete control runs (thresholds below). Both: both tests flag under "
          "the same threshold type. All rows with run-clustered intervals are in `flag_rates.csv`.", ""]
    L += _flag_table(flags, PRIMARY_SPEC)
    L.append("")
    if not thresholds:
        notes.append("empirical thresholds, the empirical null and every empirical flag-rate row: "
                     "no complete control run")

    # ---- control distributions
    L += ["## Control distributions", "",
          "Figure: `control_distributions.png` (|z| and F of control pairs, tag spec, with each scheme run's "
          "cartel pair marked).", ""]
    if null.empty:
        L.append("No complete control run: the control distributions cannot be computed.")
    else:
        rows = []
        for spec in _ordered(null["spec"], detector.SPECS):
            for test, label in (("A1", "|z|"), ("A3", "F")):
                v = null[(null["spec"] == spec) & (null["test"] == test)]["value"].to_numpy(dtype=float)
                if not len(v):
                    continue
                q5, q50, q95 = np.percentile(v, [5, 50, 95])
                rows.append([spec, f"{label} ({test})", len(v), _f(q5), _f(q50), _f(q95),
                             _f(thresholds.get((spec, test)))])
        L += _table(["spec", "statistic", "values", "5%", "50%", "95%", "empirical threshold"], rows)
        L.append("")
        L.append(f"Closed-form references: |z| = {detector.A1_CRITICAL:.3f}; {figure.get('F_label')} = "
                 f"{_f(figure.get('F_critical'))} (q and df are medians over the complete control runs).")
    L.append("")
    if figure.get("marked"):
        L.append("Cartel pairs marked in the figure (tag spec, primary rows):")
        L.append("")
        L += _table(["run", "condition", "z", "|z| > empirical", "F", "F > empirical"],
                    [[rid, cond, _f(z), _yes(abs(z), thresholds.get((PRIMARY_SPEC, "A1"))), _f(F),
                      _yes(F, thresholds.get((PRIMARY_SPEC, "A3")))] for cond, rid, z, F in figure["marked"]])
        L.append("")

    schemes = [c for c in _ordered(re_["condition"], CONDITION_ORDER) if c != "control"] if len(re_) else []
    if not schemes:
        notes.append("per-scheme sections (mixed-pair A3, top-2 verdicts, per-run results, fidelity-"
                     "conditional detection): no scheme run")

    # ---- mixed-pair A3
    L += ["## Mixed-pair A3 per scheme", "",
          "Rate [95% interval clustered by run: mean of per-run rates +- 1.96 SE], tag spec, complete runs; "
          "outsider pairs for comparison. " + CI_NOTE, ""]
    tag_flags = flags[flags["spec"] == PRIMARY_SPEC] if len(flags) else flags
    rows = []
    for cond in schemes:
        for th in THRESHOLDS:
            m = _cell(tag_flags, condition=cond, threshold=th, pair_class="mixed", test="A3")
            o = _cell(tag_flags, condition=cond, threshold=th, pair_class="outsider", test="A3")
            if m is None:
                continue
            rows.append([cond, th, _rate_ci(m), int(m["n_pairs"]), int(m["n_runs"]), _rate_ci(o)])
    L += _table(["scheme", "threshold", "mixed A3", "mixed pairs", "runs", "outsider A3"], rows) if rows \
        else ["No complete scheme run."]
    L.append("")

    # ---- top-2
    L += ["## Top-2 verdicts", "",
          "Per complete run, tag spec at the primary usable_T: A3 top-2 = the two cartel firms rank above "
          "every other firm by (pairs with p < 0.05, then summed F); A1 rank-1 = the cartel pair has the "
          "largest |r| of the 15.", ""]
    rows = []
    for cond in schemes:
        g = re_[(re_["condition"] == cond) & complete & _num(re_, "a3_top2").notna()]
        n = len(g)
        t2, r1 = int(_num(g, "a3_top2").sum()), int(_num(g, "a1_rank1_by_abs_r").sum())
        rows.append([cond, n, f"{t2}/{n}", _f(t2 / n if n else None), f"{r1}/{n}", _f(r1 / n if n else None)])
    L += _table(["scheme", "runs", "A3 top-2", "share", "A1 rank-1", "share"], rows) if rows \
        else ["No complete scheme run."]
    L.append("")

    # ---- per run
    L += ["## Per-run results", "",
          "Sign of the cartel pair's r (tag spec, verdict usable_T), realized cover rule (A76), execution "
          "fidelity (A74/A75), median realized cover margin over the outsiders' (A79b) and cartel profit "
          "against the matched control run of the same seed (A77; projects completed by both runs).", ""]
    rows = []
    for r in (re_[re_["condition"] != "control"].to_dict("records") if len(re_) else []):
        cr = r["cartel_r"]
        sign = "n/a" if _isnan(cr) else ("-" if cr < 0 else "+")
        rule = r["realized_rule"]
        if not _isnan(r["b_own"]):
            rule = f"{rule} (b_own {_f(r['b_own'])}, b_partner {_f(r['b_partner'])})"
        rows.append([r["run_id"], int(r["complete"]), int(r["projects_completed"]), sign, _f(cr),
                     _f(r["cartel_z"]), rule, _f(r["fidelity"]), _f(r["median_margin_excess"]),
                     _f(r["cartel_profit"]), _f(r["matched_control_profit"]), _f(r["profit_delta"])])
        if _isnan(r["matched_control_profit"]):
            notes.append(f"profit vs matched control for {r['run_id']}: no control run of seed {r['seed']}")
    L += _table(["run", "complete", "projects", "sign of r", "cartel r", "cartel z", "realized rule",
                 "fidelity", "median margin excess", "cartel profit", "matched control profit",
                 "profit delta"], rows) if rows else ["No scheme run."]
    L.append("")

    # ---- fidelity-conditional
    L += ["## Detection conditional on fidelity > 0.80", "",
          "Flag rates (tag spec) recomputed on the scheme runs with fidelity > 0.80 (A78).", ""]
    rows = []
    if schemes:
        kept = evaluation.filter_by_fidelity(pair_eval, re_, FIDELITY_MIN)
        kf = flag_rates(kept, thresholds)
        kf = kf[kf["spec"] == PRIMARY_SPEC] if len(kf) else kf
        for cond in schemes:
            m = re_["condition"] == cond
            fid = _num(re_, "fidelity")
            n_ok = int((m & complete & (fid > FIDELITY_MIN)).sum())
            cells = [_cell(kf, condition=cond, threshold=th, pair_class=cls, test=t)
                     for th in THRESHOLDS for cls, t in (("cartel", "A1"), ("mixed", "A3"))]
            rows.append([cond, f"{n_ok}/{int((m & complete).sum())}", *[_rate_frac(c) for c in cells]])
    L += _table(["scheme", "complete runs kept", "cartel A1 closed", "mixed A3 closed", "cartel A1 empirical",
                 "mixed A3 empirical"], rows) if rows else ["No scheme run."]
    L.append("")

    # ---- power vs T
    L += ["## Power vs T", "",
          "Tag spec, closed thresholds, the first k usable projects of every run that reached k (incomplete "
          "runs included; `power_vs_T.csv` has every row).", ""]
    rows = []
    if len(power):
        for cond in _ordered(power["condition"], CONDITION_ORDER):
            for u in sorted(power.loc[power["condition"] == cond, "usable_T"].unique()):
                g = power[(power["condition"] == cond) & (power["usable_T"] == u)]
                c = lambda cls, t: _cell(g, pair_class=cls, test=t)  # noqa: E731
                first = g.iloc[0]
                rows.append([cond, int(u), int(first["n_runs"]), "yes" if int(first["includes_incomplete"]) else "no",
                             _f(first["top2_rate"]), _f(first["rank1_rate"]),
                             *[_f(None if c(cls, t) is None else c(cls, t)["rate"])
                               for cls, t in (("cartel", "A1"), ("mixed", "A3"), ("outsider", "A1"),
                                              ("outsider", "A3"))]])
    L += _table(["condition", "usable_T", "runs", "includes incomplete", "top-2", "rank-1", "cartel A1",
                 "mixed A3", "outsider A1", "outsider A3"], rows) if rows else ["No run reached a usable_T."]
    L.append("")

    # ---- robustness
    L += ["## Robustness", "", f"### Project-FE specification (rho0 = {_fe_rho0(pair_eval)})", ""]
    L += _flag_table(flags, FE_SPEC)
    L += ["", f"### Scripted rows (`{SCRIPTED_NAME}`)", ""]
    sc = robustness[robustness["source"] == "scripted"] if len(robustness) else robustness
    if not scripted_given:
        L.append(f"{SCRIPTED_NAME} was not given; run `python -m src.procurement.scripted --seeds 100` and pass "
                 "`--scripted-csv`.")
        notes.append(f"scripted robustness rows: {SCRIPTED_NAME} was not given")
    elif sc.empty:
        L.append(f"{SCRIPTED_NAME} has no rows.")
        notes.append(f"scripted robustness rows: {SCRIPTED_NAME} is empty")
    else:
        L.append("Synthetic bids with the scheme applied in-loop, cartel seats (0, 1), tag spec, usable 100 "
                 "(spec §6.6); copied verbatim into `robustness.csv`. Denominators differ from the real-run "
                 "tables: a scripted row counts a pair with a missing statistic as not flagged (n = every pair "
                 "of the class), while the real-run rows exclude it from n_pairs; the two bases differ only "
                 "when statistics are missing.")
        L.append("")
        rows = []
        for name in dict.fromkeys(sc["row"]):
            g = sc[sc["row"] == name]
            v = lambda cls, t, col="rate": _sc(g, cls, t, col)  # noqa: E731
            first = g.iloc[0]
            rows.append([name, v("cartel", "A1"), v("cartel", "A1", "mean_r"), v("cartel", "A3"), v("mixed", "A3"),
                         v("outsider", "A1"), v("outsider", "A3"), _f(_float(first["top2"])),
                         _f(_float(first["rank1"])), v("cartel", "A1", "n")])
        L += _table(["row", "cartel A1", "cartel mean r", "cartel A3", "mixed A3", "outsider A1", "outsider A3",
                     "top-2", "rank-1", "seeds"], rows)
    L.append("")

    # ---- calibration
    L += ["## Calibration", ""]
    if calibration is None and calibration_problem:
        L.append(f"`results/calibration.json` not used: {calibration_problem}.")
        notes.append(f"calibration section: results/calibration.json {calibration_problem}")
    elif calibration is None:
        L.append("`results/calibration.json` not found.")
        notes.append("calibration section: results/calibration.json not found")
    else:
        L += ["Contents of `results/calibration.json`:", "", "```json",
              json.dumps(calibration, indent=2, sort_keys=True), "```"]
    L.append("")

    # ---- not computed
    L += ["## Not computed", ""]
    L += [f"- {n}" for n in notes] if notes else ["Every section was computed."]
    L.append("")
    return "\n".join(L)


def _yes(v, thr) -> str:
    if thr is None or _isnan(v):
        return "n/a"
    return "yes" if v > thr else "no"


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _sc(g: pd.DataFrame, cls: str, test: str, col: str) -> str:
    sub = g[(g["pair_class"] == cls) & (g["test"] == test)]
    if sub.empty:
        return "n/a"
    v = _float(sub[col].iloc[0])
    if col == "n":
        return "n/a" if v is None else str(int(v))
    return _f(v)


def _fe_rho0(pair_eval: pd.DataFrame) -> str:
    vals = sorted({float(v) for v in _rho0_by_condition(pair_eval, FE_SPEC).values() if not isinstance(v, str)})
    return ", ".join(f"{v:.3f}" for v in vals) if vals else "n/a"


# ------------------------------------------------------------------ analyze

def _read_scripted(path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def read_json_object(path) -> tuple[dict | None, str | None]:
    """``(content, None)`` for a JSON object file; ``(None, None)`` when it does not exist; ``(None,
    problem)`` when it cannot be read, is not valid JSON or is not a JSON object."""
    path = Path(path)
    if not path.exists():
        return None, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return None, f"cannot be read ({type(exc).__name__})"
    except ValueError:
        return None, "is not valid JSON"
    if not isinstance(data, dict):
        return None, f"is not a JSON object (a {type(data).__name__})"
    return data, None


_RUN_DIR_NAME = re.compile(r"_(?P<cond>[a-z]+)_s(?P<seed>\d+)(?:_\d+)?$")


def _dir_run_ids(results_root) -> set[str]:
    """``<condition>_s<seed>`` of every directory under the results root, from its name."""
    root = Path(results_root)
    out = set()
    if root.is_dir():
        for d in root.iterdir():
            m = _RUN_DIR_NAME.search(d.name) if d.is_dir() else None
            if m:
                out.add(config.run_id_of(m['cond'], int(m['seed'])))
    return out


def missing_runs(results_root, stop_json: dict | None, plan: dict | None) -> list[dict]:
    """Runs with no directory at all under ``results_root``: those ``STOP.json`` lists in
    ``runs_not_started``, then the rows of ``plan`` (``config/run_plan.json``: R, include_rotation)
    never launched, each with a reason. [{"run_id", "reason"}]."""
    present = _dir_run_ids(results_root)
    out, seen = [], set()
    listed = (stop_json or {}).get("runs_not_started")
    for x in listed if isinstance(listed, (list, tuple)) else []:
        rid = str(x)
        if rid not in present and rid not in seen:
            seen.add(rid)
            out.append({"run_id": rid, "reason": f"not started: launcher stop "
                                                  f"({(stop_json or {}).get('reason') or 'no reason given'}, "
                                                  "results/STOP.json)"})
    if plan is not None:
        try:
            rows = config.run_plan({c: int(v) for c, v in dict(plan.get("R") or {}).items()},
                                   bool(plan.get("include_rotation", True)))
        except (TypeError, ValueError, AttributeError):
            rows = []
        for c in rows:
            rid = config.run_id_of(c.condition, c.seed)
            if rid not in present and rid not in seen:
                seen.add(rid)
                out.append({"run_id": rid, "reason": "planned in config/run_plan.json but no run directory "
                                                      "(not launched: sizing rule, time ladder or launch "
                                                      "not yet run)"})
    return out


def analyze(results_root, out_dir, scripted_csv=None, *, include_synthetic: bool = False, frozen_path=None,
            plan_path=None) -> Path:
    """Evaluate ``results_root`` and write every §4g analysis file into ``out_dir`` (which must be new or
    empty) plus ``results_root.parent / "README.md"``. Returns ``out_dir``. ``include_synthetic``,
    ``frozen_path`` and ``plan_path`` select runs as in ``evaluation.evaluate``; ``plan_path`` also
    lists the planned runs without a directory."""
    results_root, out_dir = Path(results_root), Path(out_dir)
    if out_dir.exists() and any(out_dir.iterdir()):
        raise FileExistsError(f"{out_dir} is not empty; analysis never overwrites")
    out_dir.mkdir(parents=True, exist_ok=True)
    eval_dir = out_dir / "evaluation"
    pair_eval, run_eval = evaluation.evaluate(results_root, eval_dir, include_synthetic=include_synthetic,
                                              frozen_path=frozen_path, plan_path=plan_path)
    excluded = json.loads((eval_dir / "excluded_runs.json").read_text(encoding="utf-8"))

    thresholds = control_thresholds(pair_eval)
    flags = flag_rates(pair_eval, thresholds)
    null = empirical_null_table(pair_eval)
    power = power_vs_T_table(pair_eval, run_eval)
    scripted = _read_scripted(scripted_csv) if scripted_csv is not None else None
    robust = robustness_table(pair_eval, scripted, thresholds)
    is_ctrl = pair_eval["condition"] == "control" if len(pair_eval) else pd.Series(dtype=bool)
    figure = control_distribution_figure(pair_eval[is_ctrl] if len(pair_eval) else pair_eval,
                                         pair_eval[~is_ctrl] if len(pair_eval) else pair_eval,
                                         out_dir / "control_distributions.png", thresholds)
    input_notes = []
    stop_json, stop_problem = read_json_object(results_root.parent / "STOP.json")
    if stop_problem:
        input_notes.append(f"stop reasons from results/STOP.json: the file {stop_problem}; treated as absent")
    stop_reasons = {}
    for r in (run_eval.to_dict("records") if len(run_eval) else []):
        if _isnan(r["complete"]) or int(r["complete"]) != 1:
            stop_reasons[r["run_id"]] = stop_reason(results_root / str(r["run_dir"]), r["run_id"], stop_json)
    calibration, cal_problem = read_json_object(results_root.parent / "calibration.json")
    plan = evaluation.read_plan_file(plan_path) if plan_path is not None else None
    if plan_path is not None and plan is None and Path(plan_path).exists():
        input_notes.append("planned runs without a directory: config/run_plan.json is unreadable")
    absent = missing_runs(results_root, stop_json, plan)

    _write(out_dir / "flag_rates.csv", flags, FLAG_RATES_COLUMNS)
    _write(out_dir / "empirical_null.csv", null, EMPIRICAL_NULL_COLUMNS)
    _write(out_dir / "power_vs_T.csv", power, POWER_COLUMNS)
    _write(out_dir / "robustness.csv", robust, ROBUSTNESS_COLUMNS)
    text = readme_numbers(pair_eval=pair_eval, run_eval=run_eval, flags=flags, null=null, power=power,
                          robustness=robust, thresholds=thresholds, figure=figure, excluded=excluded,
                          calibration=calibration, scripted_given=scripted is not None,
                          results_root=results_root.as_posix(), analysis_dir=out_dir.as_posix(),
                          stop_reasons=stop_reasons, missing_runs=absent, input_notes=input_notes,
                          calibration_problem=cal_problem)
    for path in (out_dir / "README.md", results_root.parent / "README.md"):
        path.write_text(text, encoding="utf-8", newline="\n")
    return out_dir


def main(argv=None) -> Path:
    ap = argparse.ArgumentParser(prog="python -m src.procurement.analysis",
                                 description="Tables, figure and results/README.md from the experiment runs")
    ap.add_argument("--results-root", type=Path, default=Path("results/runs"))
    ap.add_argument("--out", type=Path, default=None, help="output directory (default: a new results/analysis/<UTC>)")
    ap.add_argument("--scripted-csv", type=Path, default=None, help=f"{SCRIPTED_NAME} from python -m "
                                                                    "src.procurement.scripted")
    evaluation.add_selection_args(ap)
    args = ap.parse_args(argv)  # relative paths resolve against the repository root
    root = config.repo_path(args.results_root)
    out = config.repo_path(args.out) if args.out is not None else evaluation.new_out_dir(config.repo_path(ANALYSIS_ROOT))
    analyze(root, out, scripted_csv=None if args.scripted_csv is None else config.repo_path(args.scripted_csv),
            include_synthetic=args.include_synthetic, frozen_path=config.repo_path(args.frozen),
            plan_path=config.repo_path(args.plan))
    print(f"analysis -> {out}; README -> {root.parent / 'README.md'}")
    return out


if __name__ == "__main__":
    main()
