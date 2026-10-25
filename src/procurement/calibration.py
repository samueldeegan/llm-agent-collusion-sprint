"""Shared plumbing of the hand-run calibration scripts (``scripts/smoke_llm.py``, ``cover_smoke.py``,
``pilot.py``, ``tier_check.py``): the ``results/calibration.json`` merge, per-run call statistics from
``calls.csv``, the key check and path resolution. Nothing here talks to the API.

``results/calibration.json`` holds one section per writer (schema of the sections the launcher reads in
``launcher``'s module docstring): ``limits`` and ``tier_check_utc`` (tier check), ``smoke``, ``measured``
(smoke and cover smoke; the most recent measuring script overwrites it, so post-freeze cover smoke
numbers win), ``cover_smoke_prefreeze`` / ``cover_smoke_postfreeze`` and ``pilot``. Each script writes
only its own sections, through ``update_calibration``."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

import numpy as np
import pandas as pd

from src.procurement import io
from src.procurement.config import CostParams, Pricing
from src.procurement.launcher import A_PRIORI_INPUT, A_PRIORI_OUTPUT  # audit Part 2.2 expected case

REPO_ROOT = Path(__file__).resolve().parents[2]
KEY_VARS = {1: "ANTHROPIC_API_KEY", 2: "ANTHROPIC_API_KEY_2"}
EXIT_OK, EXIT_FAIL, EXIT_STOP, EXIT_PARTIAL, EXIT_REFUSE = 0, 1, 3, 4, 5
TOKEN_COLUMNS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def repo_path(value) -> Path:
    """An absolute path; a relative one is taken relative to the repository root (as the launcher does)."""
    p = Path(value)
    return p if p.is_absolute() else REPO_ROOT / p


def json_clean(obj):
    """``obj`` with NaN/inf floats as None and numpy scalars as Python values, so the JSON is valid."""
    if isinstance(obj, dict):
        return {str(k): json_clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_clean(v) for v in obj]
    if isinstance(obj, np.generic):
        obj = obj.item()
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    if isinstance(obj, Path):
        return str(obj)
    return obj


# ---------------------------------------------------------------- calibration.json

def read_calibration_file(path) -> dict:
    """The calibration file's content; ``{}`` when it does not exist. A file that is not a JSON object
    raises ValueError (callers never overwrite it)."""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError(f"{path} is not valid JSON; refusing to overwrite it ({exc})") from None
    if not isinstance(data, dict):
        raise ValueError(f"{path} does not hold a JSON object; refusing to overwrite it")
    return data


def section_history(path, section: str) -> list[dict]:
    """The ``history`` list a script's new ``section`` record carries: the previous record's own
    ``history`` plus the previous record itself (without its ``history``), so a re-run never loses an
    earlier record (a STOP, say). Read once at script start, before any API call; ``[]`` when there is
    no previous record."""
    prev = read_calibration_file(path).get(section)
    if not isinstance(prev, dict):
        return []
    hist = [h for h in prev.get("history") or [] if isinstance(h, dict)]
    return hist + [{k: v for k, v in prev.items() if k != "history"}]


def update_calibration_sections(path, sections: dict) -> dict:
    """Set every ``sections[name]`` in the calibration file at ``path`` and keep every other section.
    Reads the file (``{}`` when it does not exist), writes a temp file and ``os.replace``s it, so a
    reader never sees a half-written file. A file that exists but is not a JSON object raises
    ValueError and is left untouched (never overwritten). Returns the new content."""
    path = Path(path)
    data = read_calibration_file(path)
    for name, value in sections.items():
        data[str(name)] = json_clean(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    io._atomic_write_text(path, json.dumps(data, indent=2, allow_nan=False) + "\n")
    return data


def update_calibration(path, section: str, data) -> dict:
    """``update_calibration_sections(path, {section: data})``."""
    return update_calibration_sections(path, {section: data})


# ---------------------------------------------------------------- call statistics

def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.replace("", np.nan), errors="coerce")


def call_stats(run_dir, pricing: Pricing) -> dict:
    """Per-run call statistics from ``calls.csv``. A call is its attempt-1 row (retries are not extra
    calls): ``n_calls`` and the latency figures (``latency_ms`` / 1000) are over attempt-1 rows. Token
    totals and ``cost_usd`` sum every row (each attempt that got a response reports its usage);
    ``mean_input_tokens`` / ``mean_output_tokens`` are per response with usage. Empty or missing file:
    zero calls, latency None."""
    path = Path(run_dir) / "calls.csv"
    df = (pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8")
          if path.is_file() else pd.DataFrame(columns=io.CALLS_COLUMNS))
    for c in ("attempt", "latency_ms", *TOKEN_COLUMNS):
        df[c] = _num(df[c]) if c in df else np.nan
    first = df[df["attempt"] == 1]
    lat = first["latency_ms"].dropna() / 1000.0
    tot = {c: int(df[c].fillna(0).sum()) for c in TOKEN_COLUMNS}
    with_usage = df[df["output_tokens"].notna()]

    def _mean(s):
        return float(s.mean()) if len(s) else None
    return {
        "n_calls": int(len(first)),
        "n_attempts": int(len(df)),
        "mean_latency_s": _mean(lat),
        "min_latency_s": float(lat.min()) if len(lat) else None,
        "max_latency_s": float(lat.max()) if len(lat) else None,
        **tot,
        "mean_input_tokens": _mean(with_usage["input_tokens"]),
        "mean_output_tokens": _mean(with_usage["output_tokens"]),
        "cost_usd": pricing.cost_usd(tot["input_tokens"], tot["output_tokens"],
                                     tot["cache_creation_input_tokens"], tot["cache_read_input_tokens"]),
    }


def bid_parse(bids: pd.DataFrame) -> tuple[int, int]:
    """(present bids, bid rows) of a bid table."""
    return int(bids["bid"].notna().sum()), int(len(bids))


def measured_section(stats: dict, parse_rate: float | None, source: str) -> dict:
    """The ``measured`` section the launcher reads (``mean_latency_s``) plus what §4f is rewritten from."""
    return {"mean_latency_s": stats["mean_latency_s"], "mean_input_tokens": stats["mean_input_tokens"],
            "mean_output_tokens": stats["mean_output_tokens"], "n_calls": stats["n_calls"],
            "parse_rate": parse_rate, "source": source}


def print_call_stats(stats: dict, label: str) -> None:
    def f(x, fmt):
        return "n/a" if x is None else format(x, fmt)
    print(f"{label}: {stats['n_calls']} calls ({stats['n_attempts']} attempts); latency mean "
          f"{f(stats['mean_latency_s'], '.2f')} s, min {f(stats['min_latency_s'], '.2f')} s, max "
          f"{f(stats['max_latency_s'], '.2f')} s")
    print(f"{label}: tokens input {stats['input_tokens']}, output {stats['output_tokens']}, cache read "
          f"{stats['cache_read_input_tokens']}, cache creation {stats['cache_creation_input_tokens']}; "
          f"mean per response input {f(stats['mean_input_tokens'], '.0f')}, output "
          f"{f(stats['mean_output_tokens'], '.0f')}")
    print(f"{label}: cost ${stats['cost_usd']:.4f}")


# ---------------------------------------------------------------- keys, defaults

def missing_key(slot: int = 1) -> str | None:
    """The variable name when the slot's key is not set, else None. The value is never read further."""
    var = KEY_VARS[slot]
    return None if os.environ.get(var) else var


def scrub(text: str) -> str:
    """``text`` with any API key value from the environment replaced by ***."""
    for var in KEY_VARS.values():
        secret = os.environ.get(var)
        if secret:
            text = text.replace(secret, "***")
    return text


def cost_from_defaults(defaults: dict) -> CostParams:
    return CostParams(**{k: tuple(v) if isinstance(v, list) else v for k, v in defaults["cost"].items()})


def pricing_from_defaults(defaults: dict) -> Pricing:
    return Pricing(**defaults["pricing"]) if "pricing" in defaults else Pricing()


# RunConfig fields a calibration run takes from config/defaults.json (besides ``cost``)
DEFAULTS_RUN_FIELDS = ("model", "max_tokens", "history_window", "memory_window", "max_message_length",
                       "concurrency")


def run_config(defaults: dict, *, condition: str, seed: int, T: int, burn_in: int, bidder: str = "llm",
               cost: CostParams | None = None):
    """A calibration ``RunConfig``: model, max_tokens, windows, message cap and concurrency from
    ``defaults`` (``config/defaults.json``), cost from ``cost`` or ``defaults["cost"]``, then the
    script's condition, seed, T, burn_in and bidder."""
    from src.procurement.config import RunConfig
    settings = {k: defaults[k] for k in DEFAULTS_RUN_FIELDS if k in defaults}
    return RunConfig(condition=condition, seed=seed, T=T, burn_in=burn_in, bidder=bidder,
                     cost=cost if cost is not None else cost_from_defaults(defaults), **settings)


def estimate_line(prog: str, calls: int, pricing: Pricing, what: str) -> str:
    """The one-line pre-call estimate: calls and a-priori cost at 3.5k input / 1.5k output per call."""
    usd = calls * pricing.cost_usd(A_PRIORI_INPUT, A_PRIORI_OUTPUT, 0, 0)
    return (f"{prog}: about to make {calls} model calls ({what}); a-priori cost ${usd:.4f} at "
            f"{A_PRIORI_INPUT / 1000:g}k input / {A_PRIORI_OUTPUT / 1000:g}k output tokens per call")


# ---------------------------------------------------------------- running a calibration run

def choose_factory(cfg, bidder_factory=None, transport=None):
    """The bidder factory a script runs with: ``bidder_factory`` when given (tests: the synthetic bidder
    or a stub client); else, with ``transport``, the LLM bidder on a ``HaikuClient`` over that transport;
    else ``runner.bidder_factory_for(cfg)`` (the real Haiku client, built lazily on the first call)."""
    from src.procurement import runner
    if bidder_factory is not None:
        return bidder_factory
    if transport is not None:
        from src.procurement.client import HaikuClient
        from src.procurement.llm_bidder import llm_bidder_factory

        def factory(c, env):
            return llm_bidder_factory(c, env, client=HaikuClient(c.model, c.max_tokens, c.api_key_slot,
                                                                 transport=transport))
        return factory
    return runner.bidder_factory_for(cfg)


def run_calibration(cfg, factory, results_dir, purpose: str, frozen_from: str | None = None) -> Path:
    """One calibration run under ``<results_dir>/calibration_runs/`` (never ``results/runs/``)."""
    from src.procurement import runner
    return runner.run(cfg, factory, results_root=Path(results_dir) / "calibration_runs", purpose=purpose,
                      frozen_from=frozen_from)


def failure_reasons(run_dir, phase: str = "bid") -> dict[str, int]:
    """Counts of the non-empty final ``reason`` per call (last attempt row of each project, firm, phase)."""
    path = Path(run_dir) / "calls.csv"
    if not path.is_file():
        return {}
    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8")
    if df.empty:
        return {}
    df = df[df["phase"] == phase].copy()
    df["attempt"] = pd.to_numeric(df["attempt"], errors="coerce")
    last = df.sort_values("attempt").groupby(["project_id", "firm_id"], sort=False).tail(1)
    counts = last["reason"][last["reason"] != ""].value_counts()
    return {str(k): int(v) for k, v in counts.items()}
