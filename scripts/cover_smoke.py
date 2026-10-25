"""Hand-run cover-margin smoke (spec A48, A79b, §4e "Realized cover margin"; step-4 audit Part 1.2 item 3,
Part 4.2 step 5): one 10-project cover run on Haiku, 80 calls (6 bids + 2 messages per project).

    .venv\\Scripts\\python.exe scripts\\cover_smoke.py --phase prefreeze|postfreeze
        [--results-dir results] [--config-dir config] [--seeds 100] [--skip-scripted]
    .venv\\Scripts\\python.exe scripts\\cover_smoke.py --note-skip-postfreeze [--results-dir results]

``prefreeze`` runs before the pilot at the ``config/defaults.json`` cost parameters and is the audit
4.3 condition 3 stop check; ``postfreeze`` runs after the pilot at the ``config/frozen.json`` parameters
(refused with exit 5 when that file is missing) and is the measurement behind the "realized median
margin" sensitivity row. The run goes under ``<results-dir>/calibration_runs/`` (``purpose =
"cover_smoke"``); ``<results-dir>/calibration.json`` gets ``cover_smoke_<phase>`` (per-project
``margin_raw`` and ``margin_excess``, their medians, the realized rule, fidelity, latency, mean output
tokens) and ``measured``. Then, unless ``--skip-scripted``, the scripted signatures are simulated (no
API) at 0.10 and at the realized median excess margin (rounded to 0.01, floored at 0.01) into a NEW
``<results-dir>/analysis/<UTC>/scripted_signatures.csv``. A re-run keeps the previous record of its
section in the section's ``history`` list.

``--note-skip-postfreeze`` (A48: the time ladder forces skipping the second smoke) makes no API call: it
writes ``cover_smoke_postfreeze = {"skipped": true, "reason": "time ladder", "using":
"cover_smoke_prefreeze"}`` and exits 0; it refuses (exit 5) when there is no prefreeze record or when a
real postfreeze measurement already exists.

A realized rule of "insufficient" (fewer than 10 cover projects with both bids) is not a stop: the
section records ``rule_insufficient: true`` and, in the prefreeze phase, a WARNING recommends one rerun
(controller ruling D2: the shadow stop check needs a realized rule).

Exit codes: 0 done (postfreeze: also on shadow or low fidelity, which print a WARNING; prefreeze: also on
an "insufficient" rule); 1 the scripted step failed without a stop (the run's numbers are recorded; rerun
``python -m src.procurement.scripted``), either phase; 3 STOP, prefreeze only: realized rule "shadow" or
fidelity < 0.5 (written to calibration.json and printed before the scripted step, and exit 3 whatever that
step does); 5 key not set or rejected, an unreadable calibration.json, or (postfreeze) no usable
``config/frozen.json``. The key is read from the environment and never printed or written."""
from __future__ import annotations

import argparse
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.procurement import calibration, config, evaluation, fidelity, io, launcher, scripted  # noqa: E402
from src.procurement.client import FatalAPIError  # noqa: E402

PROG = "cover_smoke"
FIDELITY_FLOOR = 0.5
RULE_MIN_PROJECTS = 10
STOP_REF = "step-4 audit Part 4.3 condition 3"


def rounded_margin(median_excess: float | None) -> float | None:
    """The sensitivity row's margin: the median excess margin rounded to 0.01 and floored at 0.01."""
    if median_excess is None or not math.isfinite(median_excess):
        return None
    return max(0.01, round(float(median_excess), 2))


def _finite(x) -> float | None:
    return float(x) if x is not None and math.isfinite(float(x)) else None


def stop_conditions(rule_label: str, fid: float) -> list[str]:
    out = []
    if rule_label == "shadow":
        out.append("realized cover rule is 'shadow' (the sign prediction flips)")
    if fid is not None and math.isfinite(fid) and fid < FIDELITY_FLOOR:
        out.append(f"cover execution fidelity {fid:.2f} < {FIDELITY_FLOOR} (Haiku is not running the scheme)")
    return out


def _scripted_summary(df, rows) -> list[str]:
    lines = []
    for row in rows:
        g = df[df["row"] == row]
        if g.empty:
            continue
        a1 = g[(g["pair_class"] == "cartel") & (g["test"] == "A1")]
        a3 = g[(g["pair_class"] == "mixed") & (g["test"] == "A3")]
        lines.append(f"{row}: cartel A1 rate {a1['rate'].iloc[0]:.2f}, cartel mean r {a1['mean_r'].iloc[0]:+.2f}, "
                     f"mixed A3 rate {a3['rate'].iloc[0]:.2f}, top-2 {g['top2'].iloc[0]:.2f}, "
                     f"rank-1 {g['rank1'].iloc[0]:.2f}")
    return lines


def note_skip_postfreeze(cal_path: Path) -> int:
    """A48: record that the post-freeze smoke was skipped and the prefreeze numbers stand. No API."""
    try:
        data = calibration.read_calibration_file(cal_path)
    except ValueError as exc:
        print(f"{PROG}: refusing --note-skip-postfreeze: {exc}", file=sys.stderr)
        return calibration.EXIT_REFUSE
    if not isinstance(data.get("cover_smoke_prefreeze"), dict):
        print(f"{PROG}: refusing --note-skip-postfreeze: no cover_smoke_prefreeze record in {cal_path}",
              file=sys.stderr)
        return calibration.EXIT_REFUSE
    post = data.get("cover_smoke_postfreeze")
    if isinstance(post, dict) and not post.get("skipped"):
        print(f"{PROG}: refusing --note-skip-postfreeze: {cal_path} already holds a post-freeze measurement",
              file=sys.stderr)
        return calibration.EXIT_REFUSE
    note = {"skipped": True, "reason": "time ladder", "using": "cover_smoke_prefreeze",
            "utc": datetime.now(timezone.utc).isoformat(),
            "history": calibration.section_history(cal_path, "cover_smoke_postfreeze")}
    calibration.update_calibration(cal_path, "cover_smoke_postfreeze", note)
    print(f"{PROG}: recorded cover_smoke_postfreeze as skipped (time ladder); the prefreeze numbers are used "
          f"({cal_path})")
    return calibration.EXIT_OK


def main(argv=None, *, bidder_factory=None, transport=None) -> int:
    ap = argparse.ArgumentParser(prog=PROG, description="10-project cover smoke run on Haiku (80 calls).")
    ap.add_argument("--phase", choices=("prefreeze", "postfreeze"), help="required unless --note-skip-postfreeze")
    ap.add_argument("--note-skip-postfreeze", action="store_true",
                    help="record that the post-freeze smoke is skipped (A48 time ladder); no API call")
    ap.add_argument("--results-dir", default="results",
                    help="parent of calibration_runs/, analysis/ and calibration.json (default: results)")
    ap.add_argument("--config-dir", default="config", help="where frozen.json is read (default: config)")
    ap.add_argument("--seeds", type=int, default=100, help="seeds of the scripted signatures (default 100)")
    ap.add_argument("--skip-scripted", action="store_true", help="do not run the scripted signatures")
    args = ap.parse_args(argv)
    if args.note_skip_postfreeze:
        if args.phase == "prefreeze":
            ap.error("--note-skip-postfreeze concerns the postfreeze phase")
        return note_skip_postfreeze(calibration.repo_path(args.results_dir) / "calibration.json")
    if args.phase is None:
        ap.error("--phase is required (or --note-skip-postfreeze)")

    var = calibration.missing_key(1)
    if var:
        print(f"{PROG}: {var} is not set; set it in the environment and rerun", file=sys.stderr)
        return calibration.EXIT_REFUSE

    defaults = config.load_defaults()
    pricing = calibration.pricing_from_defaults(defaults)
    cost, frozen_from = calibration.cost_from_defaults(defaults), None
    if args.phase == "postfreeze":
        frozen_path = calibration.repo_path(args.config_dir) / "frozen.json"
        if not frozen_path.is_file():
            print(f"{PROG}: refusing --phase postfreeze: {frozen_path} not found (run scripts/pilot.py first)",
                  file=sys.stderr)
            return calibration.EXIT_REFUSE
        try:
            cost = launcher.read_frozen(frozen_path, cost)
        except ValueError as exc:
            print(f"{PROG}: refusing --phase postfreeze: {exc}", file=sys.stderr)
            return calibration.EXIT_REFUSE
        frozen_from = str(frozen_path)

    cfg = calibration.run_config(defaults, condition="cover", seed=1, T=10, burn_in=0, bidder="llm", cost=cost)
    results_dir = calibration.repo_path(args.results_dir)
    cal_path = results_dir / "calibration.json"
    section_name = f"cover_smoke_{args.phase}"
    factory = calibration.choose_factory(cfg, bidder_factory, transport)
    try:
        history = calibration.section_history(cal_path, section_name)  # before any API call
    except ValueError as exc:
        print(f"{PROG}: refusing to run: {exc}", file=sys.stderr)
        return calibration.EXIT_REFUSE

    print(f"{PROG}: phase {args.phase}, beta0 {cost.beta0:g}"
          + (f" (from {frozen_from})" if frozen_from else " (config/defaults.json)"))
    print(calibration.estimate_line(PROG, launcher.planned_calls([cfg]), pricing,
                                    f"cover, seed 1, T={cfg.T}, model {cfg.model}, max_tokens {cfg.max_tokens}"),
          flush=True)
    try:
        run_dir = calibration.run_calibration(cfg, factory, results_dir, "cover_smoke", frozen_from)
    except FatalAPIError as exc:
        print(f"{PROG}: key invalid or missing: {calibration.scrub(str(exc))}", file=sys.stderr)
        return calibration.EXIT_REFUSE

    bids, truth, meta = io.read_bids(run_dir), io.read_truth(run_dir), io.read_run_meta(run_dir)
    names = fidelity.cartel_names(meta)
    margins = fidelity.cover_margins(bids, truth, names)
    rule = fidelity.realized_rule(bids, truth, names, min_projects=RULE_MIN_PROJECTS)
    fid = float(fidelity.cover_fidelity(bids, truth, names))
    med_raw = _finite(margins["margin_raw"].median()) if len(margins) else None
    med_excess = _finite(margins["margin_excess"].median()) if len(margins) else None
    extra = rounded_margin(med_excess)
    stats = calibration.call_stats(run_dir, pricing)
    parsed, total = calibration.bid_parse(bids)
    rate = parsed / total if total else None
    problems = stop_conditions(rule["label"], fid)
    stop = "; ".join(problems) if problems and args.phase == "prefreeze" else None
    warning = "; ".join(problems) if problems and args.phase == "postfreeze" else None

    section = {
        "phase": args.phase, "run_dir": str(run_dir), "utc": datetime.now(timezone.utc).isoformat(),
        "beta0": cost.beta0, "cost": {k: getattr(cost, k) for k in ("beta0", "beta1", "beta2", "sigma_eps")},
        "frozen_from": frozen_from, "cartel_firms": list(names),
        "margins": margins.to_dict("records"), "n_cover": int(rule["n_cover"]),
        "median_margin_raw": med_raw, "median_margin_excess": med_excess,
        "realized_median_excess_margin_rounded": extra,
        "realized_rule": rule["label"], "b_own": rule["b_own"], "b_partner": rule["b_partner"],
        "fidelity": fid, "parsed": parsed, "n_bids": total, "parse_rate": rate,
        "failure_reasons": calibration.failure_reasons(run_dir, "bid"),
        "message_failure_reasons": calibration.failure_reasons(run_dir, "message"),
        **stats,
        "rule_insufficient": rule["label"] == "insufficient",
        "stop": stop, "warning": warning, "scripted_signatures": None, "scripted_seeds": None,
        "scripted_extra_margin": extra, "scripted_error": None, "history": history,
    }
    calibration.update_calibration_sections(cal_path, {
        section_name: section, "measured": calibration.measured_section(stats, rate, section_name)})

    def fmt(x, f=".4f"):
        return "n/a" if x is None else format(x, f)
    print(f"{PROG}: run dir {run_dir}")
    print(f"{PROG}: cartel {names[0]} / {names[1]}; {section['n_cover']} cover projects with both bids")
    for r in section["margins"]:
        print(f"{PROG}:   project {r['project_id']:>2}: margin_raw {fmt(r['margin_raw'], '+.4f')}, "
              f"margin_excess {fmt(r['margin_excess'], '+.4f')}")
    print(f"{PROG}: median margin_raw {fmt(med_raw, '+.4f')}, median margin_excess {fmt(med_excess, '+.4f')} "
          f"-> sensitivity margin {fmt(extra, '.2f')}")
    print(f"{PROG}: realized rule {rule['label']} (b_own {fmt(_finite(rule['b_own']), '+.4f')}, b_partner "
          f"{fmt(_finite(rule['b_partner']), '+.4f')}, min {RULE_MIN_PROJECTS} cover projects)"
          + ("; 'insufficient' is a finding, not a failure" if rule["label"] == "insufficient" else ""))
    print(f"{PROG}: cover fidelity {fid:.2f}; parsed {parsed}/{total} bids")
    calibration.print_call_stats(stats, PROG)
    print(f"{PROG}: wrote {section_name} and measured to {cal_path}", flush=True)

    if args.phase == "prefreeze" and rule["label"] == "insufficient":
        print(f"{PROG}: WARNING: realized rule 'insufficient' ({section['n_cover']} cover projects with both bids, "
              f"{RULE_MIN_PROJECTS} needed): the shadow stop check could not be made. Recommended: rerun this "
              f"prefreeze smoke once (recorded as rule_insufficient in {section_name}); not a stop.", flush=True)
    if stop:  # reported now, so a failure in the (offline) scripted step cannot hide it
        print(f"{PROG}: STOP ({STOP_REF}): {stop}. Recorded in {cal_path} [{section_name}]. "
              "Do not run the pilot; the design cannot run as specified.", flush=True)

    scripted_failed = False
    if not args.skip_scripted:
        try:
            out_dir = evaluation.new_out_dir(results_dir / "analysis")
            path = out_dir / scripted.OUTPUT_NAME
            print(f"{PROG}: scripted signatures (no API), {args.seeds} seeds, at 0.10"
                  + (f" and {extra:.2f}" if extra is not None else " (no realized margin: 0.10 only)")
                  + f" -> {path}", flush=True)
            df = scripted.run_scripted(seeds=args.seeds, extra_margin=extra)
            scripted.write_scripted(df, path)
            rows = ["cover_m0.10"] + ([f"cover_m{extra:.2f}"] if extra is not None else [])
            for line in _scripted_summary(df, dict.fromkeys(rows)):
                print(f"{PROG}:   {line}")
            section.update(scripted_signatures=str(path), scripted_seeds=args.seeds)
        except Exception as exc:  # noqa: BLE001 - recorded; the run's own numbers stand
            scripted_failed = True
            err = calibration.scrub(f"{type(exc).__name__}: {exc}")
            section.update(scripted_error=err)
            rerun = f"python -m src.procurement.scripted --seeds {args.seeds}" + (
                f" --extra-margin {extra:.2f}" if extra is not None else "")
            print(f"{PROG}: ERROR: scripted signatures failed ({err}); recorded as scripted_error. "
                  f"Rerun offline: {rerun}", file=sys.stderr, flush=True)
        calibration.update_calibration(cal_path, section_name, section)

    if stop:
        if scripted_failed:
            print(f"{PROG}: STOP stands ({stop}); the scripted step failed separately (see above).")
        return calibration.EXIT_STOP
    if warning:
        print(f"{PROG}: WARNING (post-freeze measurement, no stop): {warning}")
    return calibration.EXIT_FAIL if scripted_failed else calibration.EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
