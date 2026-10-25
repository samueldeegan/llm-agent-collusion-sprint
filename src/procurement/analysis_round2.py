"""Round-2 analysis: per-arm tables, the paired-by-seed test, the ablation figure and
``results/README_round2.md`` (round-2 brief §2.5 items 1-9 and §5.5).

``analyze_round2(results_root, out_dir)`` runs ``evaluation.evaluate`` (into ``<out_dir>/evaluation/``),
keeps the control runs only (the round-1 scheme runs are not round-2 material) and writes into
``out_dir``: ``flag_rates_by_arm.csv``, ``empirical_null_by_arm.csv``, ``fp_vs_T_by_arm.csv``,
``regimes_by_arm.csv``, ``regime_counts_by_arm.csv``, ``a3_by_pair_type_by_arm.csv``,
``slope_dispersion_by_arm.csv``, ``synthetic_floors.csv`` (base arm's complete control runs),
``paired_by_seed.csv``, ``flag_rates_at_base_threshold.csv`` (non-base arms at the base arm's p95,
out-of-sample), ``ablation_2x2.png`` and ``README.md``; the same README is written to
``results_root.parent / "README_round2.md"`` (the two differ only in the figure link, relative to each).

Reads: what ``evaluation.evaluate`` reads, plus each selected control run's ``bids.csv``, ``truth.csv``
and ``run_meta.json`` (regimes, slope dispersion, floors) and, for the spend, every ``calls.csv`` under
``results_root`` and its sibling ``calibration_runs/``. Never transcripts or checkpoints.

CLI: ``python -m src.procurement.analysis_round2 [--results-root results/runs] [--out DIR]
[--archives results/archive] [--include-synthetic] [--frozen config/frozen.json]`` (relative paths
resolve against the repository root).
"""
from __future__ import annotations

import argparse
import math
import os
import warnings
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.transforms import blended_transform_factory  # noqa: E402
from scipy import stats  # noqa: E402

from src.procurement import analysis, config, detector, evaluation, floors as floors_mod, io, launcher  # noqa: E402
from src.procurement import regimes as rg  # noqa: E402

ARMS = ("base", "mem0", "stateless", "hidden", "stateless_hidden")
CELL = {"base": ("on", "shown"), "mem0": ("off*", "shown"), "stateless": ("off", "shown"),
        "hidden": ("on", "hidden"), "stateless_hidden": ("off", "hidden")}
CONTROL = "control"
SUMMARY_SPEC = "tag_filtered"
FP_T_SPECS = ("tag", "tag_filtered")
TESTS = ("A1", "A3")
ANALYSIS_ROOT = analysis.ANALYSIS_ROOT
README_NAME = "README_round2.md"

FLAG_COLUMNS = ["arm"] + analysis.FLAG_RATES_COLUMNS
NULL_COLUMNS = ["arm"] + analysis.EMPIRICAL_NULL_COLUMNS
FP_T_COLUMNS = ["arm", "spec", "usable_T", "test", "n_pairs", "n_flagged", "rate", "ci_low", "ci_high", "n_runs",
                "includes_incomplete"]
PAIRED_COLUMNS = ["arm", "test", "n_seeds", "n_pos", "n_neg", "n_zero", "mean_diff", "wilcoxon_p"]
PAIR_TYPE_COLUMNS = ["arm", "regime_i", "regime_j", "n_pairs", "n_rejected", "rate"]
COUNT_COLUMNS = ["arm", "regime", "n_firm_seasons"]
INVENTORY_COLUMNS = ["arm", "run_id", "seed", "run_dir", "complete", "projects_completed", "mean_bid_over_est"]

ROUND1_LOSS_NOTE = ("Round-1 transcripts: `round1_transcripts.zip` holds only the 5 round-1 calibration runs; the "
                    "32 round-1 experiment runs' transcripts were deleted by accident on 2026-10-25 (~21:40 UTC) "
                    "before they were archived; their bids, truth, calls and run_meta remain under `results/runs/`.")

SMOKE_SPEND_NOTE = ("Spend excludes the three 2026-10-24 pre-procurement smoke dirs "
                    "(`results/20261024_214000_smoke_test`, `results/20261024_223137_haiku_smoke_test`, "
                    "`results/20261024_232835_haiku_smoke_test`, older marketplace code): they have no call log, so "
                    "their spend is not counted.")

# palette: reference categorical slots in fixed order (dataviz skill, references/palette.md)
_SURFACE, _INK, _INK2, _GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
_SERIES = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4")
_X = {"base": 0.0, "mem0": 1.0, "stateless": 2.0, "hidden": 3.6, "stateless_hidden": 4.6}
_TICK = {"base": "base\nmemory on", "mem0": "mem0\nprivate state off,\nhistory kept",
         "stateless": "stateless\nmemory off", "hidden": "hidden\nmemory on",
         "stateless_hidden": "stateless_hidden\nmemory off"}


# ------------------------------------------------------------------ helpers

def _num(df: pd.DataFrame, col: str) -> pd.Series:
    return pd.to_numeric(df[col], errors="coerce")


def _arms_in(values) -> list[str]:
    return analysis._ordered([str(v) for v in values], ARMS)


def _control(pair_eval: pd.DataFrame) -> pd.DataFrame:
    if pair_eval.empty:
        return pair_eval
    return pair_eval[pair_eval["condition"] == CONTROL]


def _isnan(v) -> bool:
    return analysis._isnan(v)


# ------------------------------------------------------------------ tables

def flag_rates_by_arm(pair_eval: pd.DataFrame, thresholds_by_arm: dict) -> pd.DataFrame:
    """``analysis.flag_rates`` on each arm's control rows with that arm's empirical thresholds; ``arm``
    prepended. Control rows at the empirical threshold carry ``in_sample = 1`` (each arm's own p95)."""
    ctrl = _control(pair_eval)
    frames = []
    for arm in (_arms_in(ctrl["arm"]) if len(ctrl) else []):
        fr = analysis.flag_rates(ctrl[ctrl["arm"] == arm], thresholds_by_arm.get(arm, {}))
        if len(fr):
            frames.append(fr.assign(arm=arm)[FLAG_COLUMNS])
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=FLAG_COLUMNS)


BASE_THRESHOLD = "base_empirical"


def flag_rates_at_base_threshold(pair_eval: pd.DataFrame, thresholds_by_arm: dict) -> pd.DataFrame:
    """Out-of-sample rates: each non-base arm's control pairs against the BASE arm's empirical (p95)
    thresholds (``FLAG_COLUMNS``; ``threshold = "base_empirical"``, ``in_sample = 0``). Empty when there is
    no base threshold."""
    base = thresholds_by_arm.get("base") or {}
    ctrl = _control(pair_eval)
    frames = []
    for arm in (_arms_in(ctrl["arm"]) if len(ctrl) and base else []):
        if arm == "base":
            continue
        fr = analysis.flag_rates(ctrl[ctrl["arm"] == arm], base)
        fr = fr[fr["threshold"] == "empirical"]
        if len(fr):
            frames.append(fr.assign(arm=arm, threshold=BASE_THRESHOLD, in_sample=0)[FLAG_COLUMNS])
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=FLAG_COLUMNS)


def check_null_thresholds(null: pd.DataFrame, thresholds_by_arm: dict) -> None:
    """Raise AssertionError unless every (arm, spec, test) ``percentile_95`` of ``null`` equals
    ``thresholds_by_arm[arm][(spec, test)]`` (guards the relabel-to-base step of ``empirical_null_by_arm``)."""
    if null is None or null.empty:
        return
    for (arm, spec, test), g in null.groupby(["arm", "spec", "test"]):
        want = (thresholds_by_arm.get(arm) or {}).get((spec, test))
        got = float(g["percentile_95"].iloc[0])
        if want is None or not math.isclose(got, float(want), rel_tol=1e-12, abs_tol=1e-15):
            raise AssertionError(f"empirical null p95 {got!r} for ({arm}, {spec}, {test}) != the arm's own "
                                 f"control threshold {want!r}")


def empirical_null_by_arm(pair_eval: pd.DataFrame) -> pd.DataFrame:
    """``analysis.empirical_null_table`` per arm (sorted |z| and F of the arm's complete control runs with
    the arm's own 95th percentile); ``arm`` prepended."""
    ctrl = _control(pair_eval)
    frames = []
    for arm in (_arms_in(ctrl["arm"]) if len(ctrl) else []):
        # empirical_null_table takes the base arm's thresholds; relabel so the arm's own p95 is used
        sub = ctrl[ctrl["arm"] == arm].assign(arm="base")
        t = analysis.empirical_null_table(sub)
        if len(t):
            frames.append(t.assign(arm=arm)[NULL_COLUMNS])
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=NULL_COLUMNS)


def fp_vs_T_by_arm(pair_eval: pd.DataFrame, run_eval: pd.DataFrame | None = None) -> pd.DataFrame:
    """Closed-threshold A1/A3 false-positive rates of control pairs per arm x spec (tag, tag_filtered) x
    usable_T (every value reached: 30/50/70/100), with run-clustered 95% intervals. As in
    ``analysis.power_vs_T_table`` every run contributes at each usable_T it reached, incomplete runs
    included (``includes_incomplete``)."""
    rows = []
    ctrl = _control(pair_eval)
    if ctrl.empty:
        return pd.DataFrame(rows, columns=FP_T_COLUMNS)
    complete = analysis._run_complete(ctrl, run_eval)
    usable = _num(ctrl, "usable_T")
    for arm in _arms_in(ctrl["arm"]):
        for spec in FP_T_SPECS:
            ga = ctrl[(ctrl["arm"] == arm) & (ctrl["spec"] == spec)]
            for u in sorted(int(x) for x in usable[ga.index].dropna().unique()):
                g = ga[usable[ga.index] == u]
                incomplete = int(any(complete.get(str(r), 0) != 1 for r in g["run_id"].unique()))
                for test, col in (("A1", "a1_flag"), ("A3", "a3_flag")):
                    flag = _num(g, col)
                    present, flagged = flag.notna(), flag == 1
                    n, k = int(present.sum()), int((flagged & present).sum())
                    n_runs, lo, hi = analysis._clustered(g["run_id"], present, flagged & present)
                    rows.append(dict(arm=arm, spec=spec, usable_T=u, test=test, n_pairs=n, n_flagged=k,
                                     rate=k / n if n else math.nan, ci_low=lo, ci_high=hi, n_runs=n_runs,
                                     includes_incomplete=incomplete))
    return pd.DataFrame(rows, columns=FP_T_COLUMNS)


def _seed_rates(pair_eval: pd.DataFrame, arm: str, test: str, spec: str) -> dict[int, float]:
    pe = _control(pair_eval)
    if pe.empty:
        return {}
    pe = pe[(pe["arm"] == arm) & (pe["spec"] == spec) & (_num(pe, "primary") == 1) & (_num(pe, "complete") == 1)]
    flag = _num(pe, "a1_flag" if test == "A1" else "a3_flag")
    out = {}
    for seed, f in flag.groupby(_num(pe, "seed")):
        f = f.dropna()
        if len(f):
            out[int(seed)] = float((f == 1).mean())
    return out


def paired_by_seed(pair_eval: pd.DataFrame, arm: str, test: str, spec: str = SUMMARY_SPEC) -> dict:
    """Seed-matched comparison of ``arm`` with base: per seed, rate(arm, seed) - rate(base, seed), the run's
    closed-threshold rejection rate over its pairs (primary rows of complete control runs). Seeds without
    both runs are dropped. ``wilcoxon_p`` (``scipy.stats.wilcoxon``, two-sided) is NaN when fewer than 2
    seeds remain or every difference is zero."""
    a, b = _seed_rates(pair_eval, arm, test, spec), _seed_rates(pair_eval, "base", test, spec)
    seeds = sorted(set(a) & set(b))
    d = np.array([a[s] - b[s] for s in seeds], dtype=float)
    eps = 1e-12
    p = math.nan
    if len(d) >= 2 and np.any(np.abs(d) > eps):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                p = float(stats.wilcoxon(d).pvalue)
        except ValueError:
            p = math.nan
    return dict(arm=arm, test=test, n_seeds=len(d), n_pos=int((d > eps).sum()), n_neg=int((d < -eps).sum()),
                n_zero=int((np.abs(d) <= eps).sum()), mean_diff=float(d.mean()) if len(d) else math.nan,
                wilcoxon_p=p)


def paired_by_seed_table(pair_eval: pd.DataFrame) -> pd.DataFrame:
    """``paired_by_seed`` for every control arm other than base x {A1, A3} (tag_filtered, closed)."""
    ctrl = _control(pair_eval)
    arms = [a for a in (_arms_in(ctrl["arm"]) if len(ctrl) else []) if a != "base"]
    rows = [paired_by_seed(pair_eval, a, t) for a in arms for t in TESTS]
    return pd.DataFrame(rows, columns=PAIRED_COLUMNS)


# ------------------------------------------------------------------ figure

def _style(ax, title: str, ylabel: str) -> None:
    ax.set_facecolor(_SURFACE)
    ax.set_title(title, color=_INK, fontsize=11, loc="left")
    ax.set_ylabel(ylabel, color=_INK2)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(_INK2)
    ax.tick_params(colors=_INK2, labelsize=9)
    ax.yaxis.grid(True, color=_GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.set_xticks([_X[a] for a in ARMS], [_TICK[a] for a in ARMS], fontsize=8.5, color=_INK)
    ax.set_xlim(-0.6, 5.5)
    tr = blended_transform_factory(ax.transData, ax.transAxes)
    for label, lo, hi in (("information shown", 0.0, 2.0), ("information hidden", 3.6, 4.6)):
        ax.annotate("", xy=(lo - 0.4, -0.21), xytext=(hi + 0.4, -0.21), xycoords=tr, textcoords=tr,
                    arrowprops=dict(arrowstyle="-", color=_INK2, linewidth=0.8))
        ax.text((lo + hi) / 2, -0.225, label, transform=tr, ha="center", va="top", fontsize=8.5, color=_INK2)


def _rate_row(flags: pd.DataFrame, arm: str, spec: str, test: str, threshold: str = "closed"):
    if flags is None or flags.empty:
        return None
    m = ((flags["arm"] == arm) & (flags["spec"] == spec) & (flags["test"] == test)
         & (flags["threshold"] == threshold))
    sub = flags[m]
    if sub.empty:
        return None
    # control runs have one pair class; pool if several are ever present
    if len(sub) == 1:
        return sub.iloc[0]
    n, k = _num(sub, "n_pairs").sum(), _num(sub, "n_flagged").sum()
    r = sub.iloc[0].copy()
    r["n_pairs"], r["n_flagged"], r["rate"] = n, k, k / n if n else math.nan
    return r


def _bars(ax, flags, spec, test, offset, width, color, label) -> float:
    xs, ys, lo, hi, top = [], [], [], [], 0.0
    for arm in ARMS:
        r = _rate_row(flags, arm, spec, test)
        if r is None or _isnan(r["rate"]):
            continue
        rate = float(r["rate"])
        xs.append(_X[arm] + offset)
        ys.append(rate)
        l, h = (rate, rate) if _isnan(r["ci_low"]) else (float(r["ci_low"]), float(r["ci_high"]))
        lo.append(max(rate - l, 0.0))
        hi.append(max(h - rate, 0.0))
        top = max(top, h, rate)
    if xs:
        ax.bar(xs, ys, width=width, color=color, edgecolor=_SURFACE, linewidth=1.5, label=label, zorder=2)
        ax.errorbar(xs, ys, yerr=[lo, hi], fmt="none", ecolor=_INK, elinewidth=1.2, capsize=3, zorder=3)
        for x, y, h in zip(xs, ys, hi):
            ax.text(x, y + h + 0.004, f"{y:.3f}", ha="center", va="bottom", fontsize=7.5, color=_INK2, zorder=4)
    return top


def _missing(ax, present: set[str]) -> None:
    tr = blended_transform_factory(ax.transData, ax.transAxes)
    for arm in ARMS:
        if arm not in present:
            ax.text(_X[arm], 0.04, "no run", transform=tr, ha="center", fontsize=8, color=_INK2, style="italic")


def _refs(ax, floors: pd.DataFrame, col: str) -> float:
    lines = [(0.05, "nominal 0.05", (0, (1, 2)))]
    fl = floors.set_index("rule") if floors is not None and len(floors) else None
    for rule, label, ls in (("smooth", "smooth floor", "--"), ("kinked_identical", "kinked floor", "-.")):
        if fl is not None and rule in fl.index and not _isnan(fl.loc[rule, col]):
            lines.append((float(fl.loc[rule, col]), f"{label} {float(fl.loc[rule, col]):.3f}", ls))
    for y, label, ls in lines:
        ax.axhline(y, color=_INK2, linewidth=1.1, linestyle=ls, label=label, zorder=1)
    return max(y for y, _, _ in lines)


def ablation_figure(flags_by_arm: pd.DataFrame, regime_counts: pd.DataFrame, floors: pd.DataFrame,
                    out_png) -> Path:
    """Three panels, x = the five cells (memory on / off within information shown / hidden, mem0 between
    base and stateless): (a) A3 false-positive rate (tag_filtered, closed) with run-clustered 95%
    intervals and reference lines at 0.05, the smooth floor and the kinked-identical floor (A3 of
    ``floors``); (b) A1 with tag (raw) and tag_filtered side by side and the A1 floors; (c) stacked
    regime counts per cell."""
    out_png = Path(out_png)
    fig = Figure(figsize=(9.5, 13.5), layout="constrained", facecolor=_SURFACE)
    axes = fig.subplots(3, 1)
    present = set(flags_by_arm["arm"]) if len(flags_by_arm) else set()

    ax = axes[0]
    top = _bars(ax, flags_by_arm, SUMMARY_SPEC, "A3", 0.0, 0.6, _SERIES[0], "A3 rate, tag_filtered (closed)")
    ref = _refs(ax, floors, "a3_rate")
    ax.set_ylim(0, max(top, ref, 0.1) * 1.18)
    _style(ax, "(a) A3 false-positive rate of control pairs (tag_filtered, closed form) with run-clustered 95% CI",
           "share of pairs rejected")
    _missing(ax, present)
    ax.legend(loc="upper left", bbox_to_anchor=(1.0, 1.0), frameon=False, fontsize=8, labelcolor=_INK)

    ax = axes[1]
    t1 = _bars(ax, flags_by_arm, "tag", "A1", -0.2, 0.38, _SERIES[0], "A1 rate, tag (raw)")
    t2 = _bars(ax, flags_by_arm, SUMMARY_SPEC, "A1", 0.2, 0.38, _SERIES[1], "A1 rate, tag_filtered")
    ref = _refs(ax, floors, "a1_rate")
    ax.set_ylim(0, max(t1, t2, ref, 0.1) * 1.18)
    _style(ax, "(b) A1 false-positive rate of control pairs (closed form): raw vs sentinel-filtered",
           "share of pairs rejected")
    _missing(ax, present)
    ax.legend(loc="upper left", bbox_to_anchor=(1.0, 1.0), frameon=False, fontsize=8, labelcolor=_INK)

    ax = axes[2]
    bottom = {a: 0.0 for a in ARMS}
    rc = regime_counts if regime_counts is not None else pd.DataFrame(columns=COUNT_COLUMNS)
    for reg, color in zip(rg.REGIME_ORDER, _SERIES):
        xs, hs, bs = [], [], []
        for arm in ARMS:
            v = rc[(rc["arm"] == arm) & (rc["regime"] == reg)]["n_firm_seasons"] if len(rc) else []
            h = float(v.sum()) if len(v) else 0.0
            xs.append(_X[arm])
            hs.append(h)
            bs.append(bottom[arm])
            bottom[arm] += h
        ax.bar(xs, hs, bottom=bs, width=0.6, color=color, edgecolor=_SURFACE, linewidth=1.5, label=reg, zorder=2)
    for arm in ARMS:
        if bottom[arm] > 0:
            ax.text(_X[arm], bottom[arm], f"{int(bottom[arm])}", ha="center", va="bottom", fontsize=7.5,
                    color=_INK2)
    ax.set_ylim(0, max(max(bottom.values()), 1.0) * 1.1)
    _style(ax, "(c) Bidding regimes of control firm-seasons", "firm-seasons")
    _missing(ax, {a for a, v in bottom.items() if v > 0})
    h, lab = ax.get_legend_handles_labels()
    ax.legend(h[::-1], lab[::-1], loc="upper left", bbox_to_anchor=(1.0, 1.0), frameon=False, fontsize=8,
              labelcolor=_INK, title="regime (top to bottom)", title_fontsize=8)
    fig.savefig(out_png, dpi=150, facecolor=_SURFACE)
    return out_png


# ------------------------------------------------------------------ README

_f = analysis._f
_table = analysis._table


def _p(v) -> str:
    return "n/a" if _isnan(v) else f"{float(v):.3f}"


def _rate_cell(r, ci: bool = True) -> str:
    if r is None or _isnan(r["rate"]):
        return "n/a"
    mark = analysis.IN_SAMPLE_MARK if "in_sample" in r and not _isnan(r["in_sample"]) and int(r["in_sample"]) else ""
    s = f"{_f(r['rate'])} ({int(r['n_flagged'])}/{int(r['n_pairs'])}){mark}"
    if ci:
        s += " [n/a]" if _isnan(r["ci_low"]) else f" [{_f(r['ci_low'])}, {_f(r['ci_high'])}]"
    return s


def _p95(null: pd.DataFrame, arm: str, spec: str, test: str):
    if null is None or null.empty:
        return None
    g = null[(null["arm"] == arm) & (null["spec"] == spec) & (null["test"] == test)]
    return float(g["percentile_95"].iloc[0]) if len(g) else None


def _counts_text(counts: pd.DataFrame, arm: str) -> str:
    g = counts[counts["arm"] == arm] if counts is not None and len(counts) else pd.DataFrame()
    if g.empty:
        return "regimes n/a"
    c = dict(zip(g["regime"], g["n_firm_seasons"]))
    return "regimes " + ", ".join(f"{r} {int(c.get(r, 0))}" for r in rg.REGIME_ORDER)


def _signs_text(paired: pd.DataFrame, arm: str, test: str) -> str:
    g = paired[(paired["arm"] == arm) & (paired["test"] == test)] if paired is not None and len(paired) else []
    if not len(g):
        return f"{test} n/a"
    r = g.iloc[0]
    return (f"{test} +{int(r['n_pos'])}/−{int(r['n_neg'])}/={int(r['n_zero'])} of {int(r['n_seeds'])} "
            f"(mean diff {_f(r['mean_diff'])}, Wilcoxon p {_p(r['wilcoxon_p'])})")


def _arm_line(arm, *, inventory, flags, null, counts, paired, at_base=None) -> str:
    inv = inventory[inventory["arm"] == arm] if len(inventory) else inventory
    mem, info = CELL.get(arm, ("?", "?"))
    head = f"**{arm}** (memory {mem}, information {info}"
    if inv.empty:
        return f"{head}): no control run found."
    n_ok = int((_num(inv, "complete") == 1).sum())
    head += f"; {n_ok}/{len(inv)} complete runs)"
    if n_ok == 0:
        return f"{head}: no complete control run, nothing computed."
    parts = []
    for test, stat in (("A3", "F"), ("A1", "|z|")):
        c = _rate_row(flags, arm, SUMMARY_SPEC, test, "closed")
        e = _rate_row(flags, arm, SUMMARY_SPEC, test, "empirical")
        thr = _p95(null, arm, SUMMARY_SPEC, test)
        own = "n/a" if e is None else f"{_f(e['rate'])} by construction"
        part = f"{test} closed {_rate_cell(c)}; empirical: own p95 {stat} = {_f(thr)} (in-sample, {own})"
        if arm != "base":
            b = _rate_row(at_base, arm, SUMMARY_SPEC, test, BASE_THRESHOLD)
            part += f"; at base p95 (out-of-sample) {_rate_cell(b)}"
        parts.append(part)
    line = f"{head}: tag_filtered " + "; ".join(parts) + f"; {_counts_text(counts, arm)}"
    if arm == "base":
        return line + "; reference arm for the seed-matched signs."
    return line + "; vs base by seed (+/−/=): " + _signs_text(paired, arm, "A3") + ", " + \
        _signs_text(paired, arm, "A1") + "."


def _summary(*, inventory, flags, null, counts, floors, paired, dispersion, spend, at_base=None) -> list[str]:
    kw = dict(inventory=inventory, flags=flags, null=null, counts=counts, paired=paired, at_base=at_base)
    lines = [_arm_line(a, **kw) for a in ARMS]
    extra = [a for a in _arms_in(inventory["arm"]) if a not in ARMS] if len(inventory) else []
    lines += [_arm_line(a, **kw) for a in extra]
    if floors is not None and len(floors):
        fl = floors.set_index("rule")
        g = lambda rule, col: _f(fl.loc[rule, col]) if rule in fl.index else "n/a"  # noqa: E731
        lines.append("Synthetic floors (rational bids on the base runs' true costs, tag, closed): A3 smooth "
                     f"{g('smooth', 'a3_rate')}, kinked identical {g('kinked_identical', 'a3_rate')}, kinked "
                     f"heterogeneous {g('kinked_heterogeneous', 'a3_rate')}; A1 {g('smooth', 'a1_rate')} / "
                     f"{g('kinked_identical', 'a1_rate')} / {g('kinked_heterogeneous', 'a1_rate')}.")
    else:
        lines.append("Synthetic floors: not computed (no complete base control run).")
    mb = []
    for a in _arms_in(inventory["arm"]) if len(inventory) else []:
        v = _num(inventory[(inventory["arm"] == a) & (_num(inventory, "complete") == 1)], "mean_bid_over_est")
        mb.append(f"{a} {_f(v.mean())}")
    lines.append("Mean bid/EST over post-burn-in bids (mean over complete runs): " + (", ".join(mb) or "n/a") + ".")
    if dispersion is not None and len(dispersion):
        late = dispersion[dispersion["half"] == rg.HALVES[-1][0]]
        lines.append(f"Slope dispersion, projects {rg.HALVES[-1][0]} (mean over runs of the SD across firms of the "
                     "CAP / DIST margin slopes): " + ", ".join(
                         f"{a} {_f(late.loc[late['arm'] == a, 'mean_sd_cap_slope'].mean())} / "
                         f"{_f(late.loc[late['arm'] == a, 'mean_sd_dist_slope'].mean())}"
                         for a in _arms_in(late["arm"])) + ".")
    else:
        lines.append("Slope dispersion: not computed.")
    lines.append(f"Spend: ${_f(spend.get('total_usd'), 2)} actual over {int(spend.get('total_calls') or 0)} calls "
                 f"over all procurement runs (runs/ + calibration_runs/); round 2 ${_f(spend.get('round2_usd'), 2)} "
                 f"({int(spend.get('round2_calls') or 0)} calls).")
    lines.append("Empirical thresholds are each arm's own 95th percentile, so the own-threshold rate is about 5% by "
                 "construction (in-sample, not a false-positive rate); the closed-form rates and the rates at the "
                 "base arm's p95 (out-of-sample) are the comparisons.")
    return lines


def _flag_section(flags: pd.DataFrame, spec: str, at_base: pd.DataFrame | None = None) -> list[str]:
    fr = flags[flags["spec"] == spec] if len(flags) else flags
    if fr.empty:
        return [f"No complete control run for spec `{spec}`."]
    if at_base is not None and len(at_base):
        fr = pd.concat([fr, at_base[at_base["spec"] == spec]], ignore_index=True)
    rows = []
    for arm in _arms_in(fr["arm"]):
        for th in (*analysis.THRESHOLDS, BASE_THRESHOLD):
            cells = [_rate_row(fr, arm, spec, t, th) for t in analysis.TESTS]
            if all(c is None for c in cells):
                continue
            n_runs = next(int(c["n_runs"]) for c in cells if c is not None)
            label = "at base p95 (out-of-sample)" if th == BASE_THRESHOLD else th
            rows.append([arm, label, *[_rate_cell(c) for c in cells], n_runs])
    return _table(["arm", "threshold", "A1", "A3", "both", "runs"], rows)


def write_readme_round2(path, *, inventory: pd.DataFrame, flags: pd.DataFrame, null: pd.DataFrame,
                        fpT: pd.DataFrame, regimes: pd.DataFrame, counts: pd.DataFrame, pair_type: pd.DataFrame,
                        dispersion: pd.DataFrame, floors: pd.DataFrame, paired: pd.DataFrame, figure,
                        archives: list[dict], spend: dict, analysis_dir, at_base: pd.DataFrame | None = None,
                        now_utc: datetime | None = None) -> str:
    """Write ``results/README_round2.md`` to ``path`` and return its text: a ten-line summary (one line per
    arm with the tag_filtered A3/A1 rates closed and empirical, regime counts and seed-matched sign counts
    vs base; then floors, mean bid/EST, slope dispersion, spend, the in-sample note), every §2.5 table
    as Markdown, the figure, the per-arm run inventory, the archives and the spend. ``at_base``
    (``flag_rates_at_base_threshold``) adds the out-of-sample rows; the figure link is relative to ``path``."""
    path = Path(path)
    inventory = inventory if inventory is not None else pd.DataFrame(columns=INVENTORY_COLUMNS)
    L = ["# Round 2: memory × information ablation of the control runs", ""]
    now_utc = now_utc or datetime.now(timezone.utc)
    L.append(f"Generated {now_utc:%Y-%m-%d %H:%M:%S} UTC by `python -m "
             f"src.procurement.analysis_round2`; analysis files in `{Path(analysis_dir).as_posix()}`. Control runs "
             "only; 3 decimals; `n/a` = not computable. Rates are rate (rejected/pairs).")
    L.append("")
    summary = _summary(inventory=inventory, flags=flags, null=null, counts=counts, floors=floors, paired=paired,
                       dispersion=dispersion, spend=spend, at_base=at_base)
    L += [f"{k}. {s}" for k, s in enumerate(summary, start=1)]
    L.append("")

    # ---- figure
    try:
        rel = Path(os.path.relpath(Path(figure), path.parent)).as_posix()
    except ValueError:  # another drive
        rel = Path(figure).as_posix()
    L += ["## Figure", "", f"![ablation figure]({rel})", "",
          "(a) A3 false-positive rate (tag_filtered, closed) per cell with run-clustered 95% intervals and reference "
          "lines at 0.05, the smooth floor and the kinked-identical floor; (b) the same for A1, tag (raw) and "
          "tag_filtered side by side, with the A1 floors; (c) stacked regime counts. Cells: memory on/off within "
          "information shown/hidden; mem0 (private memory and scratchpad off, public history kept) is the "
          "intermediate rung between base and stateless.", ""]

    # ---- flag rates
    L += ["## Flag rates by arm (`flag_rates_by_arm.csv`)", "",
          "Primary rows (usable_T = T − burn-in) of complete control runs; cells are rate (rejected/pairs) [95% "
          "interval clustered by run]. Closed: |z| > "
          f"{detector.A1_CRITICAL:.3f} (A1), F p-value < {detector.A3_ALPHA:.3f} (A3). Empirical: above the arm's "
          "own 95th percentile (in-sample); \"at base p95 (out-of-sample)\": the arm's pairs against the base "
          "arm's 95th percentile (`flag_rates_at_base_threshold.csv`). Both: both tests reject under the same "
          f"threshold type. {analysis.CI_NOTE}", ""]
    for spec in detector.SPECS:
        L += [f"### Spec `{spec}`", ""]
        L += _flag_section(flags, spec, at_base)
        L.append("")
    L += [analysis.IN_SAMPLE_NOTE, ""]

    # ---- empirical null
    L += ["## Empirical null by arm (`empirical_null_by_arm.csv`)", "",
          "Per arm and spec: the control-pair |z| (A1) and F (A3) of complete runs; the CSV has every sorted value.",
          ""]
    rows = []
    if len(null):
        for arm in _arms_in(null["arm"]):
            for spec in detector.SPECS:
                for test, label in (("A1", "|z|"), ("A3", "F")):
                    g = null[(null["arm"] == arm) & (null["spec"] == spec) & (null["test"] == test)]
                    if g.empty:
                        continue
                    v = g["value"].to_numpy(dtype=float)
                    q5, q50 = np.percentile(v, [5, 50])
                    rows.append([arm, spec, f"{label} ({test})", len(v), _f(q5), _f(q50),
                                 _f(g["percentile_95"].iloc[0])])
    L += _table(["arm", "spec", "statistic", "values", "5%", "50%", "95% (empirical threshold)"], rows) if rows \
        else ["No complete control run."]
    L.append("")

    # ---- fp vs T
    L += ["## False-positive rate vs usable_T (`fp_vs_T_by_arm.csv`)", "",
          "Closed thresholds; the first k usable projects of every control run that reached k (incomplete runs "
          "included); rate [run-clustered 95% interval].", ""]
    rows = []
    if len(fpT):
        for arm in _arms_in(fpT["arm"]):
            for spec in FP_T_SPECS:
                g = fpT[(fpT["arm"] == arm) & (fpT["spec"] == spec)]
                for u in sorted(g["usable_T"].unique()):
                    gu = g[g["usable_T"] == u]
                    cell = {t: gu[gu["test"] == t].iloc[0] if len(gu[gu["test"] == t]) else None for t in TESTS}
                    first = gu.iloc[0]
                    rows.append([arm, spec, int(u), int(first["n_runs"]),
                                 "yes" if int(first["includes_incomplete"]) else "no",
                                 *[_rate_cell(cell[t]) for t in TESTS]])
    L += _table(["arm", "spec", "usable_T", "runs", "includes incomplete", "A1", "A3"], rows) if rows \
        else ["No control run reached a usable_T."]
    L.append("")

    # ---- regimes
    L += ["## Bidding regimes (`regime_counts_by_arm.csv`, `regimes_by_arm.csv`)", "",
          f"Firm-seasons of complete control runs, post-burn-in projects. Assigned in order: passer if pass_rate ≥ "
          f"{rg.PASS_MIN} (bid > {rg.SENTINEL_RATIO} × EST or missing); fat if median margin ≥ {rg.FAT_MIN}; shade "
          f"if shade_slope ≥ {rg.SHADE_MIN}; razor if median margin ≤ {rg.RAZOR_MAX}; else moderate. Margin = "
          "(bid − cost)/EST over non-sentinel bids.", ""]
    rows = []
    if len(counts):
        for arm in _arms_in(counts["arm"]):
            g = counts[counts["arm"] == arm]
            c = dict(zip(g["regime"], g["n_firm_seasons"]))
            rows.append([arm, *[int(c.get(r, 0)) for r in rg.REGIME_ORDER], int(g["n_firm_seasons"].sum())])
    L += _table(["arm", *rg.REGIME_ORDER, "firm-seasons"], rows) if rows else ["No complete control run."]
    L.append("")
    if len(regimes):
        L += ["Per-arm medians over firm-seasons:", ""]
        cols = ["pass_rate", "margin_med", "margin_iqr", "margin_when_leading", "margin_when_trailing",
                "shade_slope", "wins", "profit"]
        L += _table(["arm", *cols], [[a, *[_f(_num(regimes[regimes["arm"] == a], c).median()) for c in cols]]
                                     for a in _arms_in(regimes["arm"])])
        L += ["", "<details><summary>Every firm-season (regimes_by_arm.csv)</summary>", ""]
        show = ["arm", "run_id", "firm_id", "seat", *cols, "regime"]
        L += _table(show, [[r[c] if c in ("arm", "run_id", "firm_id", "regime") else
                            (_f(r[c], 0) if c in ("seat", "wins") else _f(r[c])) for c in show]
                           for r in regimes.to_dict("records")])
        L += ["", "</details>"]
    L.append("")

    # ---- pair type
    L += ["## A3 by pair type (`a3_by_pair_type_by_arm.csv`)", "",
          "A3 rejection rate (tag_filtered, closed) of control pairs by the unordered pair of the two firms' "
          "regimes.", ""]
    rows = [[r["arm"], f"{r['regime_i']} × {r['regime_j']}", int(r["n_pairs"]), int(r["n_rejected"]), _f(r["rate"])]
            for r in pair_type.to_dict("records")] if len(pair_type) else []
    L += _table(["arm", "pair type", "pairs", "rejected", "A3 rate"], rows) if rows else ["No complete control run."]
    L.append("")

    # ---- dispersion
    L += ["## Slope dispersion (`slope_dispersion_by_arm.csv`)", "",
          "Per firm-season, OLS of margin on [1, ln(1 + DIST), CAP] over non-sentinel bids in each half; per run "
          "the SD across the six firms of the CAP and DIST slopes; mean over runs.", ""]
    rows = [[r["arm"], r["half"], int(r["n_runs"]), _f(r["mean_sd_cap_slope"]), _f(r["mean_sd_dist_slope"])]
            for r in dispersion.to_dict("records")] if len(dispersion) else []
    L += _table(["arm", "projects", "runs", "mean SD of CAP slope", "mean SD of DIST slope"], rows) if rows \
        else ["No complete control run."]
    L.append("")

    # ---- floors
    L += ["## Synthetic floors (`synthetic_floors.csv`)", "",
          "Rational bids built on each complete base control run's true costs and ε (tag spec, usable 100, "
          "closed thresholds, `default_rng(7)`): smooth = 1.01 × cost + noise; kinked identical = max(1.01 × cost, "
          "min rival (cost − ε × EST) − 0.025 × EST) + noise; kinked heterogeneous = per-firm margin and hedge. "
          "Round-1 reference A3: 0.039 / 0.150 / 0.156.", ""]
    rows = [[r["rule"], int(r["n_runs"]), int(r["n_pairs"]), _f(r["a1_rate"]), _f(r["a3_rate"]), _f(r["median_F"])]
            for r in floors.to_dict("records")] if floors is not None and len(floors) else []
    L += _table(["rule", "runs", "pairs", "A1 rate", "A3 rate", "median F"], rows) if rows \
        else ["Not computed: no complete base control run."]
    L.append("")

    # ---- paired
    L += ["## Seed-matched comparison with base (`paired_by_seed.csv`)", "",
          "Per seed: the arm's run-level rejection rate over its pairs with the statistic minus the base run's of the same seed "
          "(tag_filtered, closed). Signs count seeds with a higher (+), lower (−) or equal (=) rate than base; "
          "two-sided Wilcoxon signed-rank p over the seeds (n/a when every difference is zero or fewer than 2 "
          "seeds have both runs).", ""]
    rows = [[r["arm"], r["test"], int(r["n_seeds"]), int(r["n_pos"]), int(r["n_neg"]), int(r["n_zero"]),
             _f(r["mean_diff"]), _p(r["wilcoxon_p"])] for r in paired.to_dict("records")] if len(paired) else []
    L += _table(["arm", "test", "seeds", "+", "−", "=", "mean difference", "Wilcoxon p"], rows) if rows \
        else ["No non-base arm."]
    L.append("")

    # ---- inventory
    L += ["## Run inventory", ""]
    if inventory.empty:
        L.append("No control run was found.")
    else:
        rows = []
        for arm in _arms_in(inventory["arm"]):
            g = inventory[inventory["arm"] == arm]
            ok = int((_num(g, "complete") == 1).sum())
            rows.append([arm, *CELL.get(arm, ("?", "?")), len(g), ok, len(g) - ok])
        L += _table(["arm", "memory", "information", "runs", "complete", "incomplete"], rows)
        L += ["", "memory off* = private memory and scratchpad off, public bid history kept.", ""]
        rows = [[r["arm"], r["run_id"], int(r["seed"]), f"`{r['run_dir']}`", "yes" if r["complete"] == 1 else "no",
                 int(r["projects_completed"]), _f(r["mean_bid_over_est"])] for r in inventory.to_dict("records")]
        L += _table(["arm", "run", "seed", "run directory", "complete", "projects", "mean bid/EST"], rows)
    L.append("")

    # ---- archives
    L += ["## Transcript archives", ""]
    if archives:
        L += _table(["archive", "size (MB)", "bytes"],
                    [[f"`{a['path']}`", f"{a['bytes'] / 1e6:.2f}", int(a["bytes"])] for a in archives])
        L += ["", "Zips over 10 MB are not committed (brief §3)."]
    else:
        L.append("No archive was given (`--archives`).")
    L.append("")

    # ---- spend
    L += ["## Spend", "",
          f"Every `calls.csv` under {spend.get('scope', 'the results root and calibration_runs/')}, priced at "
          f"config/defaults.json `pricing` (USD per million tokens), a call counted once by its attempt-1 row.", ""]
    L += _table(["scope", "USD", "calls", "run dirs"],
                [["all procurement runs (runs/ + calibration_runs/)", _f(spend.get("total_usd"), 2), int(spend.get("total_calls") or 0),
                  int(spend.get("n_dirs") or 0)],
                 ["round 2 (arm ≠ base, incl. arm smokes)", _f(spend.get("round2_usd"), 2),
                  int(spend.get("round2_calls") or 0), int(spend.get("round2_dirs") or 0)]])
    L.append("")

    # ---- notes
    L += ["## Notes", "",
          f"- {analysis.IN_SAMPLE_MARK} `in_sample = 1` in `flag_rates_by_arm.csv` marks the control rows at the "
          "empirical threshold: that threshold is the 95th percentile of the same arm's control pairs, so the rate "
          "is about 5% by construction and is not a false-positive rate. The closed-form rows are the "
          "false-positive rates.",
          "- Synthetic floors are computed on the base runs' truth tables; only `won` is recomputed from the new "
          "bids, the CAP path (and DIST) stay as in the real bid table (CAP is not recomputed).",
          "- Only control runs enter this report; the round-1 scheme runs (cover, markup, rotation) are in "
          "`results/README.md`.",
          f"- {ROUND1_LOSS_NOTE}",
          f"- {SMOKE_SPEND_NOTE}",
          ""]
    text = "\n".join(L)
    path.write_text(text, encoding="utf-8", newline="\n")
    return text


# ------------------------------------------------------------------ inputs

def archive_entries(archive_dir) -> list[dict]:
    """[{"path", "bytes"}] of every ``*.zip`` directly under ``archive_dir`` (sorted; [] if absent)."""
    d = Path(archive_dir)
    if not d.is_dir():
        return []
    out = []
    for z in sorted(d.glob("*.zip")):
        try:
            rel = config.repo_path(z).relative_to(config.repo_root()).as_posix()
        except ValueError:
            rel = z.as_posix()
        out.append({"path": rel, "bytes": z.stat().st_size})
    return out


def _meta_arm(d: Path) -> str | None:
    try:
        meta = io.read_run_meta(d)
    except (OSError, ValueError):
        return None
    return config.arm_of_meta(meta) if isinstance(meta, dict) else None


def spend_summary(results_root) -> dict:
    """``launcher.spend_totals`` over every run dir under ``results_root`` and its sibling
    ``calibration_runs/`` (all procurement runs), and over the subset whose run_meta arm is not base (round 2)."""
    pricing = config.Pricing(**config.load_defaults()["pricing"])
    dirs = launcher.existing_run_dirs(results_root)
    usd, calls = launcher.spend_totals(dirs, pricing)
    r2 = [d for d in dirs if _meta_arm(d) not in (None, "base")]
    usd2, calls2 = launcher.spend_totals(r2, pricing)
    root = Path(results_root)
    return dict(total_usd=usd, total_calls=calls, n_dirs=len(dirs), round2_usd=usd2, round2_calls=calls2,
                round2_dirs=len(r2), scope=f"`{root.name}/` and `{root.parent.name}/calibration_runs/`")


def _inventory(run_eval: pd.DataFrame) -> pd.DataFrame:
    if run_eval.empty:
        return pd.DataFrame(columns=INVENTORY_COLUMNS)
    re_ = run_eval[run_eval["condition"] == CONTROL].copy()
    rank = {a: k for k, a in enumerate(ARMS)}
    re_["_k"] = re_["arm"].map(lambda a: rank.get(a, len(rank)))
    re_ = re_.sort_values(["_k", "arm", "seed"]).reset_index(drop=True)
    return re_[INVENTORY_COLUMNS]


# ------------------------------------------------------------------ analyze

def analyze_round2(results_root, out_dir, *, archives: list[dict] | None = None, spend: dict | None = None,
                   frozen_path=None, include_synthetic: bool = False) -> Path:
    """Evaluate ``results_root`` and write the round-2 files (module docstring) into ``out_dir`` (new or
    empty; a non-empty one is refused) and ``results_root.parent / "README_round2.md"``. Returns
    ``out_dir``. ``include_synthetic`` and ``frozen_path`` select runs as in ``evaluation.evaluate``;
    ``spend`` defaults to ``spend_summary(results_root)``."""
    results_root, out_dir = Path(results_root), Path(out_dir)
    if out_dir.exists() and any(out_dir.iterdir()):
        raise FileExistsError(f"{out_dir} is not empty; analysis never overwrites")
    out_dir.mkdir(parents=True, exist_ok=True)
    pair_eval, run_eval = evaluation.evaluate(results_root, out_dir / "evaluation",
                                              include_synthetic=include_synthetic, frozen_path=frozen_path)
    pair_eval = _control(pair_eval).reset_index(drop=True)
    run_eval = run_eval[run_eval["condition"] == CONTROL].reset_index(drop=True) if len(run_eval) else run_eval

    thresholds = analysis.control_thresholds_by_arm(pair_eval)
    flags = flag_rates_by_arm(pair_eval, thresholds)
    null = empirical_null_by_arm(pair_eval)
    check_null_thresholds(null, thresholds)
    at_base = flag_rates_at_base_threshold(pair_eval, thresholds)
    fpT = fp_vs_T_by_arm(pair_eval, run_eval)
    paired = paired_by_seed_table(pair_eval)

    complete = run_eval[_num(run_eval, "complete") == 1] if len(run_eval) else run_eval
    tables, disp_rows, base_dirs = [], [], []
    for r in complete.to_dict("records"):
        d = results_root / str(r["run_dir"])
        meta = io.read_run_meta(d)
        burn_in = int(meta["burn_in"])
        bids, truth = io.read_bids(d), io.read_truth(d)
        tables.append(rg.firm_season_table(bids, truth, burn_in, r["arm"], CONTROL))
        rid = str(bids["run_id"].iloc[0]) if len(bids) else r["run_id"]
        disp_rows += rg.slope_dispersion(bids, truth, burn_in, r["arm"], rid)
        if r["arm"] == "base":
            base_dirs.append((d, burn_in, int(meta["T"])))
    regimes = pd.concat(tables, ignore_index=True) if tables else pd.DataFrame(columns=rg.REGIME_COLUMNS)
    rank = {a: k for k, a in enumerate(ARMS)}
    if len(regimes):
        regimes = regimes.iloc[sorted(range(len(regimes)), key=lambda k: (
            rank.get(regimes["arm"].iloc[k], len(rank)), str(regimes["arm"].iloc[k]),
            regimes["seed"].iloc[k], str(regimes["firm_id"].iloc[k])))].reset_index(drop=True)
    counts = rg.regime_counts(regimes) if len(regimes) else pd.DataFrame(columns=COUNT_COLUMNS)
    if len(counts):
        counts = counts.assign(_k=counts["arm"].map(lambda a: rank.get(a, len(rank))))
        counts = counts.sort_values(["_k", "arm"], kind="stable").drop(columns="_k").reset_index(drop=True)
    pair_type = rg.a3_by_pair_type(pair_eval, regimes) if len(regimes) and len(pair_eval) \
        else pd.DataFrame(columns=PAIR_TYPE_COLUMNS)
    dispersion = rg.slope_dispersion_by_arm(disp_rows)
    if base_dirs:
        burn_in, T = base_dirs[0][1], base_dirs[0][2]
        floors = floors_mod.floor_rates([d for d, _, _ in base_dirs], burn_in=burn_in, usable_T=T - burn_in)
    else:
        floors = pd.DataFrame(columns=floors_mod.FLOOR_COLUMNS)

    figure = ablation_figure(flags, counts, floors, out_dir / "ablation_2x2.png")
    inventory = _inventory(run_eval)

    analysis._write(out_dir / "flag_rates_by_arm.csv", flags, FLAG_COLUMNS)
    analysis._write(out_dir / "empirical_null_by_arm.csv", null, NULL_COLUMNS)
    analysis._write(out_dir / "fp_vs_T_by_arm.csv", fpT, FP_T_COLUMNS)
    analysis._write(out_dir / "regimes_by_arm.csv", regimes, rg.REGIME_COLUMNS)
    analysis._write(out_dir / "regime_counts_by_arm.csv", counts, COUNT_COLUMNS)
    analysis._write(out_dir / "a3_by_pair_type_by_arm.csv", pair_type, PAIR_TYPE_COLUMNS)
    analysis._write(out_dir / "slope_dispersion_by_arm.csv", dispersion, rg.DISPERSION_COLUMNS)
    analysis._write(out_dir / "synthetic_floors.csv", floors, floors_mod.FLOOR_COLUMNS)
    analysis._write(out_dir / "paired_by_seed.csv", paired, PAIRED_COLUMNS)
    analysis._write(out_dir / "flag_rates_at_base_threshold.csv", at_base, FLAG_COLUMNS)

    kw = dict(inventory=inventory, flags=flags, null=null, fpT=fpT, regimes=regimes, counts=counts,
              pair_type=pair_type, dispersion=dispersion, floors=floors, paired=paired, figure=figure,
              archives=list(archives or []), spend=spend if spend is not None else spend_summary(results_root),
              analysis_dir=out_dir, at_base=at_base, now_utc=datetime.now(timezone.utc))
    # same text twice; only the figure link differs (relative to each file's own directory)
    write_readme_round2(results_root.parent / README_NAME, **kw)
    write_readme_round2(out_dir / "README.md", **kw)
    return out_dir


def main(argv=None) -> Path:
    ap = argparse.ArgumentParser(prog="python -m src.procurement.analysis_round2",
                                 description="Round-2 ablation tables, figure and results/README_round2.md")
    ap.add_argument("--results-root", type=Path, default=Path("results/runs"))
    ap.add_argument("--out", type=Path, default=None, help="output directory (default: a new results/analysis/<UTC>)")
    ap.add_argument("--archives", type=Path, default=Path("results/archive"),
                    help="directory of transcript zips to list (default results/archive)")
    ap.add_argument("--include-synthetic", action="store_true", help="also evaluate runs whose bidder is not llm")
    ap.add_argument("--frozen", type=Path, default=Path(evaluation.DEFAULT_FROZEN),
                    help="only runs at these cost parameters, when the file exists (default config/frozen.json)")
    args = ap.parse_args(argv)
    root = config.repo_path(args.results_root)
    out = config.repo_path(args.out) if args.out is not None else evaluation.new_out_dir(config.repo_path(ANALYSIS_ROOT))
    analyze_round2(root, out, archives=archive_entries(config.repo_path(args.archives)),
                   frozen_path=config.repo_path(args.frozen), include_synthetic=args.include_synthetic)
    print(f"round-2 analysis -> {out}; README -> {root.parent / README_NAME}")
    return out


if __name__ == "__main__":
    main()
