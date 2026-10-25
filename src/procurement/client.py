"""The Haiku client: the only code that talks to the Anthropic API.

``HaikuClient.create`` owns the transport policy of spec 4d (A51): the SDK is called once per attempt
with ``max_retries=0`` and every attempt yields one ``calls.csv`` record. The SDK call itself is the
injectable ``transport`` so the test suite never constructs a real client. ``parse_bid`` and
``parse_message`` turn a response into a value plus a reason code (A8)."""
from __future__ import annotations

import json
import math
import os
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import anthropic

KEY_VARS = {1: "ANTHROPIC_API_KEY", 2: "ANTHROPIC_API_KEY_2"}
BUDGET_SECONDS = 600.0
BACKOFF_START = 2.0
BACKOFF_CAP = 60.0
SDK_TIMEOUT = 180.0

_RETRIED_KINDS = ("rate_limit", "server", "timeout", "connection")
_RATELIMIT_HEADERS = {
    "ratelimit_requests_remaining": "anthropic-ratelimit-requests-remaining",
    "ratelimit_input_tokens_remaining": "anthropic-ratelimit-input-tokens-remaining",
    "ratelimit_output_tokens_remaining": "anthropic-ratelimit-output-tokens-remaining",
}


class TransportError(Exception):
    """A failed attempt. ``kind`` is rate_limit, server, timeout, connection, auth or client."""

    def __init__(self, kind: str, status: int | None = None, retry_after: float | None = None,
                 message: str = ""):
        super().__init__(f"{kind} status={status} {message}".strip())
        self.kind, self.status, self.retry_after, self.message = kind, status, retry_after, message


class FatalAPIError(RuntimeError):
    """An invalid key (401/403) or a missing key: the run must stop loudly. ``records`` holds the
    attempt records made before the failure."""

    def __init__(self, message: str, records: list[dict] | None = None):
        super().__init__(message)
        self.records = records or []


@dataclass
class RawResponse:
    content: list[dict]          # [{"type": "thinking" | "text" | ..., "text": str}]
    stop_reason: str | None
    usage: dict = field(default_factory=dict)
    headers: dict = field(default_factory=dict)


def response_from_json(body: dict, headers: dict | None = None) -> RawResponse:
    """Build a ``RawResponse`` from the Messages API JSON body; a thinking block's ``thinking`` field
    becomes its ``text``."""
    content = []
    for block in body.get("content") or []:
        btype = block.get("type")
        if btype == "thinking":
            text = block.get("thinking")
        else:
            text = block.get("text")
        content.append({"type": btype, "text": text if isinstance(text, str) else ""})
    return RawResponse(content=content, stop_reason=body.get("stop_reason"),
                       usage=dict(body.get("usage") or {}),
                       headers={str(k).lower(): v for k, v in (headers or {}).items()})


# ---------------------------------------------------------------- SDK transport

def _retry_after(exc) -> float | None:
    try:
        raw = exc.response.headers.get("retry-after")
        value = float(raw)
    except (AttributeError, TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def map_sdk_error(exc: anthropic.APIError) -> TransportError:
    """Map an SDK exception onto a ``TransportError``."""
    if isinstance(exc, anthropic.APITimeoutError):
        return TransportError("timeout", None, None, "request timed out")
    if isinstance(exc, anthropic.APIConnectionError):
        return TransportError("connection", None, None, str(exc))
    if isinstance(exc, anthropic.APIStatusError):
        status = exc.status_code
        if isinstance(exc, anthropic.RateLimitError):
            return TransportError("rate_limit", status, _retry_after(exc), str(exc))
        if status >= 500:
            return TransportError("server", status, None, str(exc))
        if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
            return TransportError("auth", status, None, str(exc))
        return TransportError("client", status, None, str(exc))
    return TransportError("client", None, None, str(exc))


def _scrub(err: TransportError, secret: str | None) -> TransportError:
    if secret and secret in err.message:
        err = TransportError(err.kind, err.status, err.retry_after, err.message.replace(secret, "***"))
    return err


class _DefaultTransport:
    """Builds the SDK client lazily on first call; ``max_retries=0`` so this module owns retries.
    The key is read from the environment here and never stored, logged or put in a message."""

    def __init__(self, key_var: str):
        self._key_var = key_var
        self._sdk = None
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return f"_DefaultTransport(key_var={self._key_var!r})"

    def _client(self):
        with self._lock:
            if self._sdk is None:
                key = os.environ.get(self._key_var)
                if not key:
                    raise FatalAPIError(f"{self._key_var} is not set")
                self._sdk = anthropic.Anthropic(api_key=key, max_retries=0, timeout=SDK_TIMEOUT)
            return self._sdk

    def __call__(self, kwargs: dict) -> tuple[dict, dict]:
        sdk = self._client()
        try:
            raw = sdk.messages.with_raw_response.create(**kwargs)
            return raw.http_response.json(), dict(raw.headers)
        except anthropic.APIError as exc:
            raise _scrub(map_sdk_error(exc), os.environ.get(self._key_var)) from None


# ---------------------------------------------------------------- the client

def with_content_reason(records: list[dict], reason: str) -> list[dict]:
    """A copy of ``records`` whose last record has ``reason`` set (the bidder calls this after parsing)."""
    out = [dict(r) for r in records]
    if out:
        out[-1]["reason"] = reason
    return out


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class HaikuClient:
    def __init__(self, model: str, max_tokens: int, api_key_slot: int = 1,
                 transport: Callable[[dict], tuple[dict, dict]] | None = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 jitter_seed: int | None = None):
        if api_key_slot not in KEY_VARS:
            raise ValueError(f"api_key_slot must be 1 or 2, got {api_key_slot!r}")
        self.model, self.max_tokens, self.api_key_slot = model, max_tokens, api_key_slot
        self._transport = transport if transport is not None else _DefaultTransport(KEY_VARS[api_key_slot])
        self._clock, self._sleep = clock, sleep
        self._rng = random.Random(jitter_seed)

    def __repr__(self) -> str:
        return (f"HaikuClient(model={self.model!r}, max_tokens={self.max_tokens}, "
                f"api_key_slot={self.api_key_slot})")

    def request_kwargs(self, system: str, user: str) -> dict:
        return {"model": self.model, "max_tokens": self.max_tokens,
                "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                "messages": [{"role": "user", "content": user}]}

    def _record(self, attempt: int, latency_ms: int, reason: str, resp: RawResponse | None) -> dict:
        usage = resp.usage if resp else {}
        headers = resp.headers if resp else {}
        rec = {"attempt": attempt, "latency_ms": latency_ms,
               "input_tokens": _int_or_none(usage.get("input_tokens")),
               "output_tokens": _int_or_none(usage.get("output_tokens")),
               "cache_read_input_tokens": _int_or_none(usage.get("cache_read_input_tokens")),
               "cache_creation_input_tokens": _int_or_none(usage.get("cache_creation_input_tokens")),
               "stop_reason": resp.stop_reason if resp else None,
               "reason": reason}
        for column, header in _RATELIMIT_HEADERS.items():
            rec[column] = _int_or_none(headers.get(header))
        return rec

    def create(self, system: str, user: str) -> tuple[RawResponse | None, list[dict]]:
        """One model call under the transport policy. Returns ``(response, records)``; the response is
        None when the call is missing (budget exhausted or a non-retried 4xx), with the last record's
        ``reason`` set to ``transport_failed`` or ``http_<status>``. An auth failure raises
        ``FatalAPIError`` (its ``records`` hold the attempts)."""
        kwargs = self.request_kwargs(system, user)
        start = self._clock()
        records: list[dict] = []
        retries = 0
        while True:
            attempt = len(records) + 1
            t0 = self._clock()
            try:
                body, headers = self._transport(kwargs)
            except TransportError as err:
                latency = int((self._clock() - t0) * 1000)
                reason = err.kind if err.kind in ("timeout", "connection") else f"http_{err.status}"
                if err.kind == "rate_limit" and err.status is None:
                    reason = "http_429"
                records.append(self._record(attempt, latency, reason, None))
                if err.kind == "auth":
                    raise FatalAPIError(f"API authentication failed (status {err.status}); "
                                        f"check the API key", records) from None
                if err.kind not in _RETRIED_KINDS:
                    return None, records
                if err.retry_after is not None:
                    wait = max(0.0, err.retry_after)
                else:
                    wait = min(BACKOFF_START * 2 ** retries, BACKOFF_CAP) + self._rng.uniform(0, 1)
                retries += 1
                if self._clock() + wait > start + BUDGET_SECONDS:
                    return None, with_content_reason(records, "transport_failed")
                self._sleep(wait)
                continue
            except FatalAPIError as err:
                err.records = records
                raise
            latency = int((self._clock() - t0) * 1000)
            resp = response_from_json(body, headers)
            records.append(self._record(attempt, latency, "", resp))
            return resp, records


# ---------------------------------------------------------------- parsing

def _first_text(resp: RawResponse) -> str | None:
    for block in resp.content:
        if block.get("type") == "text":
            return block.get("text") or ""
    return None


def _parse_number(value) -> float | None:
    """A positive finite number, from a JSON number or a numeric string (``$``, ``,`` and whitespace
    stripped), rounded to cents; None otherwise (booleans included)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        x = float(value)
    elif isinstance(value, str):
        cleaned = "".join(ch for ch in value if ch not in "$,").strip()
        cleaned = "".join(cleaned.split())
        if "_" in cleaned:
            return None
        try:
            x = float(cleaned)
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(x) or x <= 0:
        return None
    return round(x, 2)


def _parse_json_reply(resp: RawResponse | None) -> tuple[dict | None, str]:
    """The shared front of the reason chain: transport_failed, truncated, no_text, bad_json.

    A response with ``stop_reason == "max_tokens"`` is always reported as ``truncated``, even when its
    text happens to hold valid JSON: a cut-off reply cannot be trusted to be complete."""
    if resp is None:
        return None, "transport_failed"
    if resp.stop_reason == "max_tokens":
        return None, "truncated"
    text = _first_text(resp)
    if text is None:
        return None, "no_text"
    lo, hi = text.find("{"), text.rfind("}")
    if lo < 0 or hi < lo:
        return None, "bad_json"
    try:
        parsed = json.loads(text[lo:hi + 1])
    except ValueError:
        return None, "bad_json"
    if not isinstance(parsed, dict):
        return None, "bad_json"
    return parsed, ""


def parse_bid(resp: RawResponse | None) -> tuple[float | None, str, dict | None]:
    """``(bid, reason, parsed_json)``. Reasons in checking order: transport_failed, truncated, no_text,
    bad_json, no_bid_key, bad_value, ``""`` on success. ``parsed_json`` is set only when the JSON parsed
    (so also for no_bid_key and bad_value); a truncated response is never parsed."""
    parsed, reason = _parse_json_reply(resp)
    if parsed is None:
        return None, reason, None
    if "bid" not in parsed:
        return None, "no_bid_key", parsed
    bid = _parse_number(parsed["bid"])
    if bid is None:
        return None, "bad_value", parsed
    return bid, "", parsed


def parse_message(resp: RawResponse | None) -> tuple[dict, str]:
    """``({"my_cost", "intended_serious_bid", "message_to_partner"}, reason)``. The numbers are parsed
    like bids and become None when missing or bad without failing the message; a missing or non-string
    ``message_to_partner`` becomes ``""``. A truncated response is reported as truncated."""
    out = {"my_cost": None, "intended_serious_bid": None, "message_to_partner": ""}
    parsed, reason = _parse_json_reply(resp)
    if parsed is None:
        return out, reason
    out["my_cost"] = _parse_number(parsed.get("my_cost"))
    out["intended_serious_bid"] = _parse_number(parsed.get("intended_serious_bid"))
    text = parsed.get("message_to_partner")
    out["message_to_partner"] = text if isinstance(text, str) else ""
    return out, ""
