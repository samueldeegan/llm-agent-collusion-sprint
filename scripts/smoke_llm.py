"""Hand-run smoke test (step-4 audit Part 4.2 step 4; spec §6.12): one 3-project control run on Haiku
with the sealed-bid prompt, 18 bid calls.

    .venv\\Scripts\\python.exe scripts\\smoke_llm.py [--results-dir results]

Writes the run under ``<results-dir>/calibration_runs/`` (``purpose = "smoke"``) and the ``smoke`` and
``measured`` sections of ``<results-dir>/calibration.json``. Prints the parse count over 18, latency
(mean, min, max), token totals and cost. Exit codes: 0 when 18/18 bids parse, 1 otherwise, 5 when
``ANTHROPIC_API_KEY`` is not set or the key is rejected. The key is read from the environment and never
printed or written."""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.procurement import calibration, config, io, launcher  # noqa: E402
from src.procurement.client import FatalAPIError  # noqa: E402

PROG = "smoke_llm"


def main(argv=None, *, bidder_factory=None, transport=None) -> int:
    ap = argparse.ArgumentParser(prog=PROG, description="3-project control smoke run on Haiku (18 calls).")
    ap.add_argument("--results-dir", default="results",
                    help="parent of calibration_runs/ and calibration.json (default: results)")
    args = ap.parse_args(argv)

    var = calibration.missing_key(1)
    if var:
        print(f"{PROG}: {var} is not set; set it in the environment and rerun", file=sys.stderr)
        return calibration.EXIT_REFUSE

    defaults = config.load_defaults()
    pricing = calibration.pricing_from_defaults(defaults)
    cfg = calibration.run_config(defaults, condition="control", seed=1, T=3, burn_in=0, bidder="llm")
    results_dir = calibration.repo_path(args.results_dir)
    cal_path = results_dir / "calibration.json"
    factory = calibration.choose_factory(cfg, bidder_factory, transport)

    print(calibration.estimate_line(PROG, launcher.planned_calls([cfg]), pricing,
                                    f"control, seed 1, T={cfg.T}, N={cfg.N}, model {cfg.model}, "
                                    f"max_tokens {cfg.max_tokens}"), flush=True)
    try:
        run_dir = calibration.run_calibration(cfg, factory, results_dir, "smoke")
    except FatalAPIError as exc:
        print(f"{PROG}: key invalid or missing: {calibration.scrub(str(exc))}", file=sys.stderr)
        return calibration.EXIT_REFUSE

    bids = io.read_bids(run_dir)
    parsed, total = calibration.bid_parse(bids)
    stats = calibration.call_stats(run_dir, pricing)
    reasons = calibration.failure_reasons(run_dir, "bid")
    rate = parsed / total if total else None
    print(f"{PROG}: run dir {run_dir}")
    print(f"{PROG}: parsed {parsed}/{total} bids" + (f"; failures {reasons}" if reasons else ""))
    calibration.print_call_stats(stats, PROG)

    section = {"run_dir": str(run_dir), "utc": datetime.now(timezone.utc).isoformat(), "parsed": parsed,
               "n_bids": total, "parse_rate": rate, "failure_reasons": reasons, **stats}
    calibration.update_calibration_sections(cal_path, {
        "smoke": section, "measured": calibration.measured_section(stats, rate, "smoke")})
    print(f"{PROG}: wrote smoke and measured to {cal_path}")
    expected = cfg.T * cfg.N
    if parsed == expected and total == expected:
        return calibration.EXIT_OK
    print(f"{PROG}: FAIL: {parsed}/{expected} bids parsed (audit 4.2 step 4: fix parsing until 18/18)")
    return calibration.EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
