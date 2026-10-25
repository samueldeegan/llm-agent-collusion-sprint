"""Hand-run tier check (spec A46, A54; step-4 audit Part 2.3, Part 4.2 step 7): one minimal Messages call
per API key, reading the rate-limit headers.

    .venv\\Scripts\\python.exe scripts\\tier_check.py [--results-dir results]

Slot 1 uses ``ANTHROPIC_API_KEY``; slot 2 uses ``ANTHROPIC_API_KEY_2`` when it is set. Each call is
``{"model": <defaults model>, "max_tokens": 64, "messages": [{"role": "user", "content": "Reply with OK."}]}``
through the client's SDK transport (``with_raw_response``, ``max_retries=0``). The headers
``anthropic-ratelimit-{requests,input-tokens,output-tokens}-limit`` go to ``limits`` in
``<results-dir>/calibration.json`` in the launcher's schema (``{"1": {requests, input_tokens,
output_tokens}, "2": {...}}``) with ``tier_check_utc``; the sizing rule and the launcher's process cap
are printed.

A non-auth transport error (429, 5xx, timeout, connection) is retried once. If the slot then still
yields no limit header at all (from the response, or from the error response's headers), that slot's
earlier values in calibration.json are kept, with ``last_error`` added ("transient error, limits not
updated"); a 429 whose response carries the headers has its limits read.

Exit codes: 0 all headers read; 4 a header missing (what was found is written, with null for the rest;
the launcher then refuses the file in real mode) or a slot whose limits were not updated; 5 ``ANTHROPIC_API_KEY`` not set, or a key rejected
(401/403: "key invalid for slot N", audit 4.3 condition 5). The key is never printed or written."""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.procurement import calibration, client, config, launcher  # noqa: E402
from src.procurement.client import FatalAPIError, TransportError  # noqa: E402
from src.procurement.config import RunConfig  # noqa: E402

PROG = "tier_check"
HEADERS = {"requests": "anthropic-ratelimit-requests-limit",
           "input_tokens": "anthropic-ratelimit-input-tokens-limit",
           "output_tokens": "anthropic-ratelimit-output-tokens-limit"}
PLAN_SIZE = 32  # the full plan (12/12/4/4), for the printed process cap
RETRY_WAIT_S = 2.0  # before the one retry of a non-auth transport error (retry-after wins, capped at 30 s)
_sleep = time.sleep  # tests replace it


def request(model: str) -> dict:
    return {"model": model, "max_tokens": 64, "messages": [{"role": "user", "content": "Reply with OK."}]}


def _int(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def read_limits(headers: dict) -> dict:
    """``{requests, input_tokens, output_tokens}`` as ints (None where the header is missing or bad)."""
    low = {str(k).lower(): v for k, v in (headers or {}).items()}
    return {k: _int(low.get(h)) for k, h in HEADERS.items()}


def _existing_limits(data: dict) -> dict[str, dict]:
    """The ``limits`` of an earlier calibration.json by slot (a flat entry is slot 1)."""
    raw = data.get("limits") or {}
    if not isinstance(raw, dict):
        return {}
    if "output_tokens" in raw or "requests" in raw:
        raw = {"1": raw}
    return {str(k): dict(v) for k, v in raw.items() if isinstance(v, dict)}


def _call(send, kwargs: dict, slot: int):
    """``(body, headers, error)``: one call, retried once on a non-auth TransportError. ``error`` is the
    last TransportError when both attempts failed (``body`` is then ``{}`` and ``headers`` its headers).
    Auth errors and FatalAPIError propagate."""
    for attempt in (1, 2):
        try:
            body, headers = send(kwargs)
            return body, headers, None
        except TransportError as err:
            if err.kind == "auth" or err.status in (401, 403):
                raise
            if attempt == 1:
                wait = min(max(err.retry_after or RETRY_WAIT_S, 1.0), 30.0)
                print(f"{PROG}: warning: slot {slot} call failed ({err.kind}, status {err.status}); retrying once "
                      f"in {wait:g} s", file=sys.stderr, flush=True)
                _sleep(wait)
                continue
            print(f"{PROG}: warning: slot {slot} retry failed ({err.kind}, status {err.status}); reading the "
                  "headers of the error response", file=sys.stderr, flush=True)
            return {}, err.headers, err
    raise AssertionError("unreachable")


def main(argv=None, *, bidder_factory=None, transport=None) -> int:
    ap = argparse.ArgumentParser(prog=PROG, description="One minimal call per key; record the rate limits.")
    ap.add_argument("--results-dir", default="results", help="parent of calibration.json (default: results)")
    args = ap.parse_args(argv)

    var = calibration.missing_key(1)
    if var:
        print(f"{PROG}: {var} is not set; set it in the environment and rerun", file=sys.stderr)
        return calibration.EXIT_REFUSE
    has_key2 = calibration.missing_key(2) is None
    slots = (1, 2) if has_key2 else (1,)

    defaults = config.load_defaults()
    pricing = calibration.pricing_from_defaults(defaults)
    model = defaults.get("model", RunConfig.model)
    cal_path = calibration.repo_path(args.results_dir) / "calibration.json"
    try:
        earlier = _existing_limits(calibration.read_calibration_file(cal_path))
    except ValueError as exc:
        print(f"{PROG}: refusing to run: {exc}", file=sys.stderr)
        return calibration.EXIT_REFUSE
    print(calibration.estimate_line(PROG, len(slots), pricing,
                                    f"minimal, model {model}, max_tokens 64, key slot(s) "
                                    f"{', '.join(map(str, slots))}; a large over-estimate for 64-token calls"),
          flush=True)

    limits: dict[str, dict] = {}
    code = calibration.EXIT_OK
    usd = 0.0

    def save() -> None:
        calibration.update_calibration_sections(cal_path, {
            "limits": limits, "tier_check_utc": datetime.now(timezone.utc).isoformat()})

    for slot in slots:
        send = transport if transport is not None else client._DefaultTransport(calibration.KEY_VARS[slot])
        try:
            body, headers, error = _call(send, request(model), slot)
        except FatalAPIError as exc:  # the key variable vanished between the check and the call
            print(f"{PROG}: key invalid for slot {slot}: {calibration.scrub(str(exc))}", file=sys.stderr)
            if limits:
                save()
            return calibration.EXIT_REFUSE
        except TransportError as err:
            if err.kind == "auth" or err.status in (401, 403):
                print(f"{PROG}: key invalid for slot {slot} ({calibration.KEY_VARS[slot]}): HTTP {err.status} "
                      f"(step-4 audit Part 4.3 condition 5)", file=sys.stderr)
                if limits:
                    save()
                return calibration.EXIT_REFUSE
            raise  # _call re-raises only auth errors
        found = read_limits(headers)
        if all(v is None for v in found.values()):
            what = (f"{error.kind}, status {error.status}" if error is not None
                    else "response without rate-limit headers")
            keep = {k: v for k, v in earlier.get(str(slot), {}).items() if k != "last_error"}
            limits[str(slot)] = {**{k: None for k in HEADERS}, **keep, "last_error": what}
            print(f"{PROG}: slot {slot}: transient error, limits not updated ({what}); "
                  + ("earlier values kept" if keep else "no earlier values"), file=sys.stderr)
            code = calibration.EXIT_PARTIAL
            continue
        limits[str(slot)] = found
        usage = (body or {}).get("usage") or {}
        usd += pricing.cost_usd(_int(usage.get("input_tokens")) or 0, _int(usage.get("output_tokens")) or 0,
                                _int(usage.get("cache_creation_input_tokens")) or 0,
                                _int(usage.get("cache_read_input_tokens")) or 0)
        missing = [HEADERS[k] for k, v in found.items() if v is None]
        if missing:
            print(f"{PROG}: warning: slot {slot}: header(s) missing or unreadable: {', '.join(missing)}",
                  file=sys.stderr)
            code = calibration.EXIT_PARTIAL
        print(f"{PROG}: slot {slot} ({calibration.KEY_VARS[slot]}): requests/min {found['requests']}, "
              f"input tokens/min {found['input_tokens']}, output tokens/min {found['output_tokens']}")

    save()
    print(f"{PROG}: wrote limits and tier_check_utc to {cal_path}; cost ${usd:.6f}")
    out1 = limits["1"].get("output_tokens")
    if out1 is not None:
        sized = launcher.apply_sizing(limits["1"], has_key2)
        print(f"{PROG}: sizing rule (output-tokens limit {out1}, key 2 {'set' if has_key2 else 'not set'}): "
              f"R = {config.sizing_rule(out1, has_key2)}, keys {sized['keys']}")
        req1 = limits["1"].get("requests")
        if req1 is not None:
            cal = launcher.read_calibration(cal_path) or {}
            call_s = cal.get("mean_latency_s") or 10.0
            print(f"{PROG}: process cap per key at call_s {call_s:g}: "
                  f"{launcher.max_procs(req1, call_s, plan_size=PLAN_SIZE)} of {PLAN_SIZE} runs (slot 1)")
    else:
        print(f"{PROG}: no slot-1 output-tokens limit: the sizing rule cannot be applied; the launcher will "
              "refuse this calibration.json in real mode", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
