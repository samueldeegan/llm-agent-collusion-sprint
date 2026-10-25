"""Hand-run per-arm smoke test (round 2): one 3-project control run of an ablation arm on Haiku
(18 bid calls) plus a check of the real logged prompt's sections.

    .venv\\Scripts\\python.exe scripts\\arm_smoke.py --arm mem0 [--results-dir results]

The arm's seed-1 control config comes from the ``arms`` entries of ``config/run_plan.json`` with
``T=3, burn_in=0, bidder="llm"`` and the cost frozen in ``config/frozen.json`` (refuses without it).
Writes the run under ``<results-dir>/calibration_runs/`` (``purpose = "smoke"``) and merges
``round2_smokes[<arm>]`` into ``<results-dir>/calibration.json``. Exit codes: 0 when 18/18 bids parse
and no prompt check is violated, 1 otherwise, 5 when ``ANTHROPIC_API_KEY`` is not set or rejected. The
key is read from the environment and never printed or written."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.procurement import calibration, config, io, launcher  # noqa: E402
from src.procurement.client import FatalAPIError  # noqa: E402
from src.procurement.prompt_sections import check_logged_prompt  # noqa: E402

PROG = "arm_smoke"


def main(argv=None, *, bidder_factory=None, transport=None) -> int:
    ap = argparse.ArgumentParser(prog=PROG, description="3-project smoke run of one ablation arm (18 calls).")
    ap.add_argument("--arm", required=True, help="arm name in the plan's arms (mem0, stateless, hidden, ...)")
    ap.add_argument("--results-dir", default="results",
                    help="parent of calibration_runs/ and calibration.json (default: results)")
    ap.add_argument("--plan", default="config/run_plan.json", help="run plan with the arms entries")
    ap.add_argument("--frozen", default="config/frozen.json", help="frozen cost parameters")
    args = ap.parse_args(argv)

    var = calibration.missing_key(1)
    if var:
        print(f"{PROG}: {var} is not set; set it in the environment and rerun", file=sys.stderr)
        return calibration.EXIT_REFUSE

    frozen_path = calibration.repo_path(args.frozen)
    plan_path = calibration.repo_path(args.plan)
    if not frozen_path.is_file():
        print(f"{PROG}: FAIL: {frozen_path} not found; the smoke must run on the frozen parameters",
              file=sys.stderr)
        return calibration.EXIT_FAIL
    try:
        plan_file = json.loads(plan_path.read_text(encoding="utf-8"))
        defaults = config.load_defaults()
        cost = launcher.read_frozen(frozen_path, config.cost_from_defaults(defaults))
        cfgs = config.arm_plan(plan_file, [args.arm], 3, defaults=defaults, cost=cost, burn_in=0, bidder="llm")
    except (OSError, ValueError) as exc:
        print(f"{PROG}: FAIL: {exc}", file=sys.stderr)
        return calibration.EXIT_FAIL
    cfg = next((c for c in cfgs if c.condition == "control" and c.seed == 1), None)
    if cfg is None:
        print(f"{PROG}: FAIL: arm {args.arm!r} has no control seed-1 config", file=sys.stderr)
        return calibration.EXIT_FAIL

    pricing = calibration.pricing_from_defaults(defaults)
    results_dir = calibration.repo_path(args.results_dir)
    cal_path = results_dir / "calibration.json"
    try:
        calibration.read_calibration_file(cal_path)  # a corrupt file fails before any call is spent
    except ValueError as exc:
        print(f"{PROG}: FAIL: {exc}", file=sys.stderr)
        return calibration.EXIT_FAIL
    factory = calibration.choose_factory(cfg, bidder_factory, transport)

    print(calibration.estimate_line(PROG, launcher.planned_calls([cfg]), pricing,
                                    f"arm {args.arm}, control, seed 1, T={cfg.T}, N={cfg.N}, model {cfg.model}, "
                                    f"max_tokens {cfg.max_tokens}"), flush=True)
    try:
        run_dir = calibration.run_calibration(cfg, factory, results_dir, "smoke", frozen_from=args.frozen)
    except FatalAPIError as exc:
        print(f"{PROG}: key invalid or missing: {calibration.scrub(str(exc))}", file=sys.stderr)
        return calibration.EXIT_REFUSE

    bids = io.read_bids(run_dir)
    parsed, total = calibration.bid_parse(bids)
    stats = calibration.call_stats(run_dir, pricing)
    reasons = calibration.failure_reasons(run_dir, "bid")
    rate = parsed / total if total else None
    violations = check_logged_prompt(run_dir, cfg, seat=0, project_id=2)
    print(f"{PROG}: run dir {run_dir}")
    print(f"{PROG}: parsed {parsed}/{total} bids" + (f"; failures {reasons}" if reasons else ""))
    calibration.print_call_stats(stats, PROG)
    for v in violations:
        print(f"{PROG}: PROMPT CHECK FAILED: {v}")
    if not violations:
        print(f"{PROG}: prompt checks passed (seat 0, project 2)")

    section = {"run_dir": str(run_dir), "utc": datetime.now(timezone.utc).isoformat(), "parsed": parsed,
               "n_bids": total, "parse_rate": rate, "failure_reasons": reasons,
               "prompt_checks": violations, **stats}
    smokes = calibration.read_calibration_file(cal_path).get("round2_smokes")
    smokes = dict(smokes) if isinstance(smokes, dict) else {}
    smokes[args.arm] = section
    calibration.update_calibration_sections(cal_path, {"round2_smokes": smokes})
    print(f"{PROG}: wrote round2_smokes[{args.arm}] to {cal_path}")
    expected = cfg.T * cfg.N
    if parsed == expected and total == expected and not violations:
        return calibration.EXIT_OK
    print(f"{PROG}: FAIL: {parsed}/{expected} bids parsed, {len(violations)} prompt check violation(s)")
    return calibration.EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
