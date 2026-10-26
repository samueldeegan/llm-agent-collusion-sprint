"""Hand-run pilot with the beta0 loop (spec A47; step-4 audit Part 1.2 item 2, Part 3 item 15, Part 4.2
step 6): up to three 20-project control runs on Haiku, 120 calls each.

    .venv\\Scripts\\python.exe scripts\\pilot.py [--results-dir results] [--config-dir config]
        [--decisions docs/DECISIONS.md]

Each pilot runs at the current beta0 (the first at config/defaults.json's 0.90); m = mean bid/EST over the
present bids of projects 11-20. m in [1.00, 1.10] freezes beta0; else beta0 <- beta0 - (m - 1.05) and the
next pilot runs. After three pilots without a freeze, the pilot whose m is closest to 1.05 is frozen.
Only beta0 changes; beta1, beta2 and sigma_eps are written unchanged. The SD of bid/EST is printed against
B&Y Table 5 (all bids 1.0819 / 0.1837; winning bids 1.0031 / 0.1573, secondary) and never targeted.

K scale (controller ruling D1): a pilot season is 20 projects, so with the experiment's k_frac (0.25 of
the season's total EST) CAP would grow about 5.5 times faster per project than in the 110-project
experiment and bias the frozen beta0 low. The pilot runs at k_frac = 0.25 x 110 / 20 = 1.375
(``pilot_k_frac``), so CAP grows per project as in the experiment. The value is recorded in the ``pilot``
section and in every ``pilot_history`` entry of ``frozen.json``; frozen.json's cost keys are unchanged
and the experiment keeps k_frac 0.25.

Writes ``<config-dir>/frozen.json`` (read by the launcher and ``cover_smoke.py --phase postfreeze``), the
``pilot`` section of ``<results-dir>/calibration.json`` (after every pilot) and one line in the decisions
log. Runs go under ``<results-dir>/calibration_runs/`` (``purpose = "pilot"``).

A re-run keeps the previous ``pilot`` record in the section's ``history`` list. The end of a successful
run is ordered: ``pilot.status = "freezing"``, ``frozen.json``, the decisions line, ``pilot.status =
"frozen"``; if the decisions line cannot be written the freeze stands (exit 0) and the line to add by
hand is printed.

Exit codes: 0 frozen; 3 STOP (audit 4.3 condition 3): a pilot's parse rate below 0.95, or the frozen m
outside [0.95, 1.15] (no frozen.json is written); 5 key not set or rejected, or ``frozen.json`` already
exists (audit 4.4: no parameter change after the freeze). The key is never printed or written."""
from __future__ import annotations

import argparse
import math
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.procurement import calibration, config, io, launcher  # noqa: E402
from src.procurement import pilot as pl  # noqa: E402
from src.procurement.client import FatalAPIError  # noqa: E402

PROG = "pilot"
T, BURN_IN = 20, 10
STOP_REF = "step-4 audit Part 4.3 condition 3"


def _safe_print(text: str, file) -> None:
    """Print ``text``; on a console that cannot encode it (cp1252 and the beta0 symbol of a decisions
    line), print it with backslash escapes instead of crashing."""
    try:
        print(text, file=file, flush=True)
    except UnicodeEncodeError:
        print(text.encode("ascii", "backslashreplace").decode("ascii"), file=file, flush=True)


def pilot_k_frac(base_k_frac: float, experiment_T: int, pilot_T: int = T) -> float:
    """The pilot's k_frac: the experiment's scaled by experiment_T / pilot_T (D1), so that CAP = won / K
    grows per project at the experiment's rate (K = k_frac x season total EST)."""
    return float(base_k_frac) * int(experiment_T) / int(pilot_T)


def _f(x, fmt=".4f") -> str:
    return "n/a" if x is None or (isinstance(x, float) and not math.isfinite(x)) else format(x, fmt)


def main(argv=None, *, bidder_factory=None, transport=None) -> int:
    ap = argparse.ArgumentParser(prog=PROG, description="Pilot beta0 loop: up to 3 x 20-project control runs.")
    ap.add_argument("--results-dir", default="results",
                    help="parent of calibration_runs/ and calibration.json (default: results)")
    ap.add_argument("--config-dir", default="config", help="where frozen.json is written (default: config)")
    ap.add_argument("--decisions", default="docs/DECISIONS.md", help="decisions log to append to")
    args = ap.parse_args(argv)

    var = calibration.missing_key(1)
    if var:
        print(f"{PROG}: {var} is not set; set it in the environment and rerun", file=sys.stderr)
        return calibration.EXIT_REFUSE
    frozen_path = calibration.repo_path(args.config_dir) / "frozen.json"
    if frozen_path.exists():
        print(f"{PROG}: refusing to run: {frozen_path} exists (audit Part 4.4: no cost-parameter change after "
              "the freeze)", file=sys.stderr)
        return calibration.EXIT_REFUSE

    defaults = config.load_defaults()
    pricing = calibration.pricing_from_defaults(defaults)
    base = calibration.cost_from_defaults(defaults)
    results_dir = calibration.repo_path(args.results_dir)
    cal_path = results_dir / "calibration.json"
    decisions = calibration.repo_path(args.decisions)
    try:
        prior = calibration.section_history(cal_path, "pilot")  # before any API call
    except ValueError as exc:
        print(f"{PROG}: refusing to run: {exc}", file=sys.stderr)
        return calibration.EXIT_REFUSE

    k_frac = pilot_k_frac(base.k_frac, int(defaults["T"]))

    def pilot_config(beta0: float):
        return calibration.run_config(defaults, condition="control", seed=1, T=T, burn_in=BURN_IN, bidder="llm",
                                      cost=replace(base, beta0=beta0, k_frac=k_frac))

    per_pilot = launcher.planned_calls([pilot_config(base.beta0)])
    print(calibration.estimate_line(PROG, pl.MAX_PILOTS * per_pilot, pricing,
                                    f"at most {pl.MAX_PILOTS} pilots x {per_pilot}: control, seed 1, T={T}, "
                                    f"burn-in {BURN_IN}; start beta0 {base.beta0:g}"), flush=True)
    print(f"{PROG}: k_frac {k_frac:g} = {base.k_frac:g} x {defaults['T']} / {T} (CAP grows per project as in the "
          f"{defaults['T']}-project experiment; the experiment keeps k_frac {base.k_frac:g})", flush=True)

    history: list[dict] = []
    spend: list[dict] = []
    started = datetime.now(timezone.utc).isoformat()

    def record(status: str, frozen_index: int | None = None, extra: dict | None = None) -> None:
        sec = {"status": status, "started_utc": started, "utc": datetime.now(timezone.utc).isoformat(),
               "pilots": history, "calls": spend, "frozen_index": frozen_index,
               "frozen_beta0": None if frozen_index is None else history[frozen_index]["beta0"],
               "band": list(pl.BAND), "target": pl.TARGET, "stop_band": list(pl.STOP_BAND),
               "comparators": {"all_bids": pl.COMPARATOR_ALL, "winning_bids": pl.COMPARATOR_WINNING,
                               "source": "B&Y Table 5, p. 980"},
               "k_frac": k_frac, "experiment_k_frac": base.k_frac,
               "cost_usd": sum(s["cost_usd"] for s in spend), **(extra or {}), "history": prior}
        calibration.update_calibration(cal_path, "pilot", sec)

    beta0, idx = base.beta0, None
    for i in range(pl.MAX_PILOTS):
        cfg = pilot_config(beta0)
        factory = calibration.choose_factory(cfg, bidder_factory, transport)
        print(f"{PROG}: pilot {i + 1}/{pl.MAX_PILOTS} at beta0 {beta0:.4f} ({per_pilot} calls)", flush=True)
        try:
            run_dir = calibration.run_calibration(cfg, factory, results_dir, "pilot")
        except FatalAPIError as exc:
            print(f"{PROG}: key invalid or missing: {calibration.scrub(str(exc))}", file=sys.stderr)
            if history:
                record("stop_api_error")
            return calibration.EXIT_REFUSE
        s = pl.pilot_summary(io.read_bids(run_dir), io.read_truth(run_dir), burn_in=BURN_IN)
        stats = calibration.call_stats(run_dir, pricing)
        entry = {"beta0": beta0, "m": s["mean_bid_over_est"], "sd": s["sd_bid_over_est"],
                 "winning_mean": s["winning_mean"], "winning_sd": s["winning_sd"],
                 "parse_rate": s["parse_rate"], "k_frac": k_frac, "run_dir": str(run_dir)}
        history.append(entry)
        spend.append({"run_dir": str(run_dir), "failure_reasons": calibration.failure_reasons(run_dir, "bid"),
                      **stats})
        print(f"{PROG}: pilot {i + 1}: m = {_f(entry['m'])} (freeze band [{pl.BAND[0]:.2f}, {pl.BAND[1]:.2f}]); "
              f"SD {_f(entry['sd'])} vs B&Y Table 5 all bids SD {pl.COMPARATOR_ALL['sd']} (mean "
              f"{pl.COMPARATOR_ALL['mean']}), reported, not targeted")
        print(f"{PROG}: pilot {i + 1}: winning bids mean {_f(entry['winning_mean'])}, SD {_f(entry['winning_sd'])} "
              f"(secondary: B&Y winning bids {pl.COMPARATOR_WINNING['mean']} / {pl.COMPARATOR_WINNING['sd']}); "
              f"parse rate {_f(entry['parse_rate'], '.3f')}")
        calibration.print_call_stats(stats, f"{PROG}: pilot {i + 1}")

        if not entry["parse_rate"] >= pl.PARSE_FLOOR:
            record("stop_parse_rate")
            print(f"{PROG}: STOP ({STOP_REF}; audit Part 3 item 15): parse rate {_f(entry['parse_rate'], '.3f')} "
                  f"< {pl.PARSE_FLOOR} in pilot {i + 1} ({run_dir}); failures "
                  f"{spend[-1]['failure_reasons']}. No frozen.json written.")
            return calibration.EXIT_STOP
        nxt, frozen = pl.next_beta0(beta0, entry["m"])
        if frozen:
            idx = i
            break
        if i + 1 < pl.MAX_PILOTS:
            print(f"{PROG}: m outside the band; next beta0 = {beta0:.4f} - ({_f(entry['m'])} - {pl.TARGET}) "
                  f"= {nxt:.4f}")
            record("running")
        beta0 = nxt
    if idx is None:
        idx = pl.closest(history)
        print(f"{PROG}: {pl.MAX_PILOTS} pilots without m in the band; freezing the closest to {pl.TARGET}: "
              f"pilot {idx + 1} (beta0 {history[idx]['beta0']:.4f}, m {_f(history[idx]['m'])})")
    chosen = history[idx]

    lo, hi = pl.STOP_BAND
    if not lo <= chosen["m"] <= hi:
        record("stop_out_of_band", None, {"closest_index": idx})
        print(f"{PROG}: STOP ({STOP_REF}): pilot mean bid/EST {_f(chosen['m'])} at beta0 {chosen['beta0']:.4f} "
              f"is outside [{lo:.2f}, {hi:.2f}] after {len(history)} pilots. No frozen.json written.")
        return calibration.EXIT_STOP

    frozen_cost = replace(base, beta0=float(chosen["beta0"]))
    record("freezing", idx, {"frozen_path": str(frozen_path)})
    pl.write_frozen(frozen_path, frozen_cost, calibration.json_clean(history))  # NaN -> null: valid JSON
    hist_text = "; ".join(f"pilot {k + 1}: beta0 {h['beta0']:.4f} -> m {_f(h['m'])}, SD {_f(h['sd'])}"
                          for k, h in enumerate(history))
    decision = (f"Froze β₀ = {chosen['beta0']:.4f} from pilot {idx + 1} of {len(history)} (m = {_f(chosen['m'])}, "
                f"SD {_f(chosen['sd'])} vs B&Y 0.1837 not targeted; winning mean {_f(chosen['winning_mean'])}, "
                f"SD {_f(chosen['winning_sd'])}); β₁ {frozen_cost.beta1:g}, β₂ {frozen_cost.beta2:g}, "
                f"σ_ε {frozen_cost.sigma_eps:g} unchanged; history: {hist_text}; written to {frozen_path.name}")
    reason, reversible = "spec A47, step-4 audit Part 4.2 step 6", "no (audit 4.4: a change after the freeze needs a stop)"
    logged = True
    try:
        pl.append_decision(decisions, decision, reason, reversible)
    except Exception as exc:  # noqa: BLE001 - the freeze is valid; the log line can be added by hand
        logged = False
        line = f"- {datetime.now(timezone.utc):%Y-%m-%d %H:%M} | {decision} | {reason} | {reversible}"
        _safe_print(f"{PROG}: WARNING: frozen.json is written and valid, but the decisions line could not be "
                    f"appended to {decisions} ({type(exc).__name__}: {exc}). Add this line by hand (UTF-8):\n"
                    f"{line}", sys.stderr)
    record("frozen", idx, {"frozen_path": str(frozen_path), "decision_logged": logged})
    print(f"{PROG}: froze beta0 {chosen['beta0']:.4f} (pilot {idx + 1}) -> {frozen_path}; decision "
          + (f"logged in {decisions}" if logged else "NOT logged (see warning)")
          + f"; total pilot cost ${sum(s['cost_usd'] for s in spend):.4f}")
    return calibration.EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
