import json
import random

import anthropic
import httpx
import pytest

from src.procurement import client as client_mod
from src.procurement.client import (FatalAPIError, HaikuClient, RawResponse, TransportError,
                                    map_sdk_error, response_from_json, parse_bid, parse_message, with_content_reason)
from src.procurement.io import CALLS_COLUMNS

ID_COLUMNS = {"run_id", "project_id", "firm_id", "phase"}
SENTINEL = "sk-ant-SENTINEL-do-not-leak-0123456789"


def _resp(text=None, stop_reason="end_turn", content=None):
    if content is None:
        content = [{"type": "thinking", "text": "let me think {not json}"}]
        if text is not None:
            content.append({"type": "text", "text": text})
    return RawResponse(content=content, stop_reason=stop_reason, usage={}, headers={})


def _api_json(text='{"bid": 1.0}', stop_reason="end_turn", usage=None):
    return {"content": [{"type": "thinking", "thinking": "hmm", "signature": "x"},
                        {"type": "text", "text": text}],
            "stop_reason": stop_reason,
            "usage": usage if usage is not None else {"input_tokens": 10, "output_tokens": 20,
                                                      "cache_read_input_tokens": 5,
                                                      "cache_creation_input_tokens": 7}}


class FakeTime:
    """Fake clock and sleep: sleeping advances the clock."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, s):
        self.sleeps.append(s)
        self.now += s


def _client(transport, ft=None, **kw):
    ft = ft or FakeTime()
    c = HaikuClient("claude-haiku-5-5", 8000, transport=transport, clock=ft.clock, sleep=ft.sleep,
                    jitter_seed=kw.pop("jitter_seed", 0), **kw)
    return c, ft


# ---------------------------------------------------------------- bid parsing

def test_bid_parsing():
    plain = '{"reflection": "r", "plan": "p", "bid": 945000.50, "new_memory": "m", "scratch_pad_update": "s"}'
    for text in (plain, "```json\n" + plain + "\n```", "Here is my answer:\n" + plain + "\nThanks."):
        bid, reason, parsed = parse_bid(_resp(text))
        assert (bid, reason) == (945000.50, "")
        assert parsed["plan"] == "p"


def test_bid_parsing_numeric_forms():
    assert parse_bid(_resp('{"bid": 9.45e5}'))[:2] == (945000.0, "")
    assert parse_bid(_resp('{"bid": 945000.555}'))[:2] == (945000.56, "")
    assert parse_bid(_resp('{"bid": "$ 945,000"}'))[:2] == (945000.0, "")
    assert parse_bid(_resp('{"bid": 945000}'))[:2] == (945000.0, "")
    assert isinstance(parse_bid(_resp('{"bid": 945000}'))[0], float)


@pytest.mark.parametrize("value", ["null", "0", "-5", '"abc"', "true", "false", '""', '"NaN"', '"inf"',
                                   "[1]", '{"a": 1}', "NaN", "Infinity", "-Infinity", '"1_000"'])
def test_bad_values(value):
    bid, reason, parsed = parse_bid(_resp('{"bid": %s}' % value))
    assert bid is None and reason == "bad_value" and parsed is not None


def test_malformed_response_is_missing():
    cases = [
        (_resp("no braces at all"), "bad_json", False),
        (_resp("{not valid json}"), "bad_json", False),
        (_resp('{"plan": "x"}'), "no_bid_key", True),
        (_resp('{"bid": null}'), "bad_value", True),
        (_resp('{"bid": 0}'), "bad_value", True),
        (_resp('{"bid": "abc"}'), "bad_value", True),
        (_resp('{"bid": 5}', stop_reason="max_tokens"), "truncated", False),
        (None, "transport_failed", False),
    ]
    assert len(cases) == 8
    for resp, want, has_json in cases:
        bid, reason, parsed = parse_bid(resp)
        assert bid is None and reason == want
        assert (parsed is not None) == has_json


def test_no_text_block_and_empty_text():
    only_thinking = RawResponse([{"type": "thinking", "text": '{"bid": 5}'}], "end_turn", {}, {})
    assert parse_bid(only_thinking) == (None, "no_text", None)
    assert parse_bid(RawResponse([], "end_turn", {}, {})) == (None, "no_text", None)
    assert parse_bid(_resp(""))[1] == "bad_json"


def test_truncated_beats_valid_json():
    """A truncated response is reported as truncated even if its text holds valid JSON."""
    bid, reason, parsed = parse_bid(_resp('{"bid": 5}', stop_reason="max_tokens"))
    assert (bid, reason, parsed) == (None, "truncated", None)
    msg, mreason = parse_message(_resp('{"my_cost": 1}', stop_reason="max_tokens"))
    assert mreason == "truncated" and msg["message_to_partner"] == ""


def test_first_text_block_used_and_braces_slice():
    resp = _resp(content=[{"type": "thinking", "text": '{"bid": 1}'},
                          {"type": "text", "text": 'x {"bid": 2, "plan": "a } b"} y'},
                          {"type": "text", "text": '{"bid": 3}'}])
    assert parse_bid(resp)[:2] == (2.0, "")


# ---------------------------------------------------------------- message parsing

def test_parse_message_ok():
    text = json.dumps({"my_cost": 900000, "intended_serious_bid": "$1,000,000.456",
                       "message_to_partner": "hello"})
    msg, reason = parse_message(_resp(text))
    assert reason == ""
    assert msg == {"my_cost": 900000.0, "intended_serious_bid": 1000000.46, "message_to_partner": "hello"}


def test_parse_message_partial():
    msg, reason = parse_message(_resp('{"my_cost": null, "intended_serious_bid": "abc", '
                                      '"message_to_partner": "keep me"}'))
    assert reason == ""
    assert msg == {"my_cost": None, "intended_serious_bid": None, "message_to_partner": "keep me"}
    msg, _ = parse_message(_resp('{"my_cost": 5}'))
    assert msg == {"my_cost": 5.0, "intended_serious_bid": None, "message_to_partner": ""}
    msg, _ = parse_message(_resp('{"my_cost": 5, "message_to_partner": 12}'))
    assert msg["message_to_partner"] == ""
    msg, _ = parse_message(_resp('{"my_cost": -5, "intended_serious_bid": 0}'))
    assert msg["my_cost"] is None and msg["intended_serious_bid"] is None


def test_parse_message_failures():
    empty = {"my_cost": None, "intended_serious_bid": None, "message_to_partner": ""}
    assert parse_message(None) == (empty, "transport_failed")
    assert parse_message(_resp("no json"))[1] == "bad_json"
    assert parse_message(RawResponse([], "end_turn", {}, {}))[1] == "no_text"
    for resp in (None, _resp("no json")):
        assert parse_message(resp)[0] == empty


# ---------------------------------------------------------------- request + response shape

def test_model_settings():
    c, _ = _client(lambda kw: ({}, {}))
    kw = c.request_kwargs("SYS", "USER")
    assert kw == {"model": "claude-haiku-5-5", "max_tokens": 8000,
                  "system": [{"type": "text", "text": "SYS", "cache_control": {"type": "ephemeral"}}],
                  "messages": [{"role": "user", "content": "USER"}]}
    for bad in ("temperature", "top_p", "top_k", "thinking", "effort", "metadata"):
        assert bad not in kw


def test_response_from_json():
    seen = {}

    def transport(kwargs):
        seen["kwargs"] = kwargs
        return _api_json('{"bid": 12.5}'), {"Anthropic-RateLimit-Requests-Remaining": "99"}

    c, _ = _client(transport)
    resp, recs = c.create("S", "U")
    assert seen["kwargs"] == c.request_kwargs("S", "U")
    assert resp.content == [{"type": "thinking", "text": "hmm"}, {"type": "text", "text": '{"bid": 12.5}'}]
    assert resp.stop_reason == "end_turn"
    assert resp.usage["cache_creation_input_tokens"] == 7
    assert resp.headers["anthropic-ratelimit-requests-remaining"] == "99"
    assert parse_bid(resp)[:2] == (12.5, "")
    assert len(recs) == 1


def test_call_record_fields():
    def transport(kwargs):
        return _api_json(), {"anthropic-ratelimit-requests-remaining": "49",
                             "anthropic-ratelimit-input-tokens-remaining": "1000",
                             "anthropic-ratelimit-output-tokens-remaining": "oops"}

    c, ft = _client(transport)
    _, recs = c.create("S", "U")
    assert len(recs) == 1
    rec = recs[0]
    assert set(rec) == set(CALLS_COLUMNS) - ID_COLUMNS
    assert rec["attempt"] == 1 and rec["latency_ms"] == 0 and rec["reason"] == ""
    assert (rec["input_tokens"], rec["output_tokens"]) == (10, 20)
    assert (rec["cache_read_input_tokens"], rec["cache_creation_input_tokens"]) == (5, 7)
    assert rec["stop_reason"] == "end_turn"
    assert rec["ratelimit_requests_remaining"] == 49
    assert rec["ratelimit_input_tokens_remaining"] == 1000
    assert rec["ratelimit_output_tokens_remaining"] is None


def test_call_record_missing_usage_and_latency():
    ft = FakeTime()

    def transport(kwargs):
        ft.now += 1.25
        return {"content": [], "stop_reason": None}, {}

    c, _ = _client(transport, ft)
    _, recs = c.create("S", "U")
    rec = recs[0]
    assert rec["latency_ms"] == 1250 and isinstance(rec["latency_ms"], int)
    assert rec["input_tokens"] is None and rec["cache_creation_input_tokens"] is None
    assert rec["stop_reason"] is None


def test_with_content_reason():
    recs = [{"attempt": 1, "reason": "http_429"}, {"attempt": 2, "reason": ""}]
    out = with_content_reason(recs, "truncated")
    assert out[-1]["reason"] == "truncated" and out[0]["reason"] == "http_429"
    assert recs[-1]["reason"] == ""  # input untouched
    assert with_content_reason([], "x") == []


# ---------------------------------------------------------------- transport policy

def test_transport_policy_retry_after():
    calls = []

    def transport(kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise TransportError("rate_limit", 429, 1.0, "slow down")
        return _api_json(), {}

    c, ft = _client(transport)
    resp, recs = c.create("S", "U")
    assert resp is not None and len(calls) == 2
    assert ft.sleeps == [1.0]
    assert [r["attempt"] for r in recs] == [1, 2]
    assert recs[0]["reason"] == "http_429" and recs[1]["reason"] == ""


def test_transport_policy_backoff_without_retry_after():
    n = []

    def transport(kwargs):
        n.append(1)
        if len(n) <= 3:
            raise TransportError("server", 529, None, "overloaded")
        return _api_json(), {}

    c, ft = _client(transport, jitter_seed=7)
    resp, recs = c.create("S", "U")
    assert resp is not None
    rng = random.Random(7)
    assert ft.sleeps == pytest.approx([2 + rng.uniform(0, 1), 4 + rng.uniform(0, 1), 8 + rng.uniform(0, 1)])
    assert [r["reason"] for r in recs] == ["http_529", "http_529", "http_529", ""]


def test_transport_policy_gives_up_after_600s():
    ft = FakeTime()
    start = ft.now

    def transport(kwargs):
        raise TransportError("server", 503, None, "down")

    c, _ = _client(transport, ft)
    resp, recs = c.create("S", "U")
    assert resp is None
    assert len(recs) >= 3
    assert recs[-1]["reason"] == "transport_failed"
    assert all(r["reason"] == "http_503" for r in recs[:-1])
    assert ft.now - start <= 600
    assert ft.now - start > 600 - 61 - 1  # waited right up to the budget
    assert max(ft.sleeps) <= 61
    assert [r["attempt"] for r in recs] == list(range(1, len(recs) + 1))


def test_budget_counts_retry_after():
    ft = FakeTime()

    def transport(kwargs):
        raise TransportError("rate_limit", 429, 400.0, "wait")

    c, _ = _client(transport, ft)
    resp, recs = c.create("S", "U")
    assert resp is None and len(recs) == 2 and ft.sleeps == [400.0]
    assert recs[-1]["reason"] == "transport_failed"


@pytest.mark.parametrize("kind", ["timeout", "connection"])
def test_timeout_and_connection_are_retried(kind):
    n = []

    def transport(kwargs):
        n.append(1)
        if len(n) == 1:
            raise TransportError(kind, None, None, "x")
        return _api_json(), {}

    c, _ = _client(transport)
    resp, recs = c.create("S", "U")
    assert resp is not None and recs[0]["reason"] == kind


def test_auth_error_is_fatal():
    def transport(kwargs):
        raise TransportError("auth", 401, None, "invalid x-api-key")

    c, ft = _client(transport)
    with pytest.raises(FatalAPIError) as ei:
        c.create("S", "U")
    assert ft.sleeps == []
    assert "401" in str(ei.value)
    assert ei.value.records[-1]["reason"] == "http_401"


# ---------------------------------------------------------------- activity callback (A52)

def test_on_attempt_at_start_and_end_of_every_attempt():
    events = []

    def transport(kwargs):
        events.append("transport")
        if events.count("transport") <= 2:
            raise TransportError("server", 503, None, "down")
        return _api_json(), {}

    ft = FakeTime()

    def sleep(s):
        events.append("sleep")
        ft.sleep(s)

    c = HaikuClient("claude-haiku-5-5", 8000, transport=transport, clock=ft.clock, sleep=sleep,
                    jitter_seed=0, on_attempt=lambda: events.append("ping"))
    resp, recs = c.create("S", "U")
    assert resp is not None and len(recs) == 3
    assert events.count("ping") == 6  # start and end x 3 attempts
    assert events == ["ping", "transport", "ping", "sleep"] * 2 + ["ping", "transport", "ping"]


def test_on_attempt_on_fatal_and_missing_calls():
    pings = []

    def auth(kwargs):
        raise TransportError("auth", 401, None, "invalid x-api-key")

    c, _ = _client(auth, on_attempt=lambda: pings.append(1))
    with pytest.raises(FatalAPIError):
        c.create("S", "U")
    assert len(pings) == 2

    def bad_request(kwargs):
        raise TransportError("client", 400, None, "bad request")

    pings.clear()
    c, _ = _client(bad_request, on_attempt=lambda: pings.append(1))
    assert c.create("S", "U")[0] is None and len(pings) == 2


def test_on_attempt_errors_are_dropped():
    calls = []

    def transport(kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise TransportError("rate_limit", 429, 1.0, "slow down")
        return _api_json('{"bid": 945000.5}'), {}

    def broken():
        raise OSError("heartbeat disk full")

    plain, _ = _client(transport)
    want_resp, want_recs = plain.create("S", "U")
    calls.clear()
    c, ft = _client(transport, on_attempt=broken)
    resp, recs = c.create("S", "U")
    assert resp == want_resp and recs == want_recs and ft.sleeps == [1.0]
    assert parse_bid(resp)[0] == 945000.5
    c.on_attempt = broken  # assignable after construction (the runner's hook)
    assert c.create("S", "U")[0] is not None


def test_client_4xx_is_missing_not_retried():
    n = []

    def transport(kwargs):
        n.append(1)
        raise TransportError("client", 400, None, "bad request")

    c, ft = _client(transport)
    resp, recs = c.create("S", "U")
    assert resp is None and len(n) == 1 and ft.sleeps == []
    assert len(recs) == 1 and recs[0]["reason"] == "http_400"


# ---------------------------------------------------------------- SDK error mapping

def _httpx_resp(status, headers=None):
    return httpx.Response(status, headers=headers or {},
                          request=httpx.Request("POST", "https://x/v1/messages"))


def test_default_transport_maps_sdk_errors():
    req = httpx.Request("POST", "https://x/v1/messages")

    e = map_sdk_error(anthropic.RateLimitError("rl", response=_httpx_resp(429, {"retry-after": "7"}),
                                               body=None))
    assert (e.kind, e.status, e.retry_after) == ("rate_limit", 429, 7.0)
    e = map_sdk_error(anthropic.RateLimitError("rl", response=_httpx_resp(429), body=None))
    assert (e.kind, e.retry_after) == ("rate_limit", None)
    e = map_sdk_error(anthropic.RateLimitError("rl", response=_httpx_resp(429, {"retry-after": "soon"}),
                                               body=None))
    assert e.retry_after is None

    for status in (500, 502, 529):
        e = map_sdk_error(anthropic.APIStatusError("s", response=_httpx_resp(status), body=None))
        assert (e.kind, e.status) == ("server", status)
    e = map_sdk_error(anthropic.InternalServerError("s", response=_httpx_resp(500), body=None))
    assert e.kind == "server"

    e = map_sdk_error(anthropic.APITimeoutError(request=req))
    assert (e.kind, e.status) == ("timeout", None)
    e = map_sdk_error(anthropic.APIConnectionError(message="down", request=req))
    assert (e.kind, e.status) == ("connection", None)

    e = map_sdk_error(anthropic.AuthenticationError("a", response=_httpx_resp(401), body=None))
    assert (e.kind, e.status) == ("auth", 401)
    e = map_sdk_error(anthropic.PermissionDeniedError("p", response=_httpx_resp(403), body=None))
    assert (e.kind, e.status) == ("auth", 403)

    for status in (400, 404, 413):
        e = map_sdk_error(anthropic.APIStatusError("c", response=_httpx_resp(status), body=None))
        assert (e.kind, e.status) == ("client", status)


class _FakeRaw:
    def __init__(self, body, headers):
        self.http_response = httpx.Response(200, json=body, request=httpx.Request("POST", "https://x"))
        self.headers = headers


def _install_fake_sdk(monkeypatch, behaviour):
    built = []

    class FakeMessages:
        class with_raw_response:  # noqa: N801
            @staticmethod
            def create(**kwargs):
                return behaviour(kwargs)

    class FakeAnthropic:
        def __init__(self, **kw):
            built.append(kw)
            self.messages = FakeMessages

    monkeypatch.setattr(client_mod.anthropic, "Anthropic", FakeAnthropic)
    return built


@pytest.mark.parametrize("slot,var", [(1, "ANTHROPIC_API_KEY"), (2, "ANTHROPIC_API_KEY_2")])
def test_default_transport_builds_sdk_client_lazily(monkeypatch, slot, var):
    monkeypatch.setenv(var, SENTINEL)
    body = _api_json('{"bid": 3}')
    built = _install_fake_sdk(monkeypatch, lambda kw: _FakeRaw(body, {"retry-after": "1"}))
    c = HaikuClient("claude-haiku-5-5", 8000, api_key_slot=slot)
    assert built == []  # nothing built at construction
    resp, recs = c.create("S", "U")
    assert built == [{"api_key": SENTINEL, "max_retries": 0, "timeout": 180.0}]
    assert parse_bid(resp)[:2] == (3.0, "")
    c.create("S", "U")
    assert len(built) == 1  # built once


def test_default_transport_raises_transport_error(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SENTINEL)

    def behaviour(kw):
        raise anthropic.RateLimitError("rl", response=_httpx_resp(429, {"retry-after": "2"}), body=None)

    _install_fake_sdk(monkeypatch, behaviour)
    c = HaikuClient("claude-haiku-5-5", 8000)
    with pytest.raises(TransportError) as ei:
        c._transport(c.request_kwargs("S", "U"))
    assert ei.value.kind == "rate_limit" and ei.value.retry_after == 2.0


def test_missing_key_is_fatal(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _install_fake_sdk(monkeypatch, lambda kw: None)
    c = HaikuClient("claude-haiku-5-5", 8000)
    with pytest.raises(FatalAPIError, match="ANTHROPIC_API_KEY"):
        c.create("S", "U")


def test_key_never_in_records_or_repr(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SENTINEL)
    n = []

    def behaviour(kw):
        n.append(1)
        if len(n) == 1:
            raise anthropic.APIStatusError(f"overloaded, key {SENTINEL}", response=_httpx_resp(529),
                                           body=None)
        if len(n) == 2:
            return _FakeRaw(_api_json(), {})
        raise anthropic.AuthenticationError(f"bad key {SENTINEL}", response=_httpx_resp(401), body=None)

    _install_fake_sdk(monkeypatch, behaviour)
    ft = FakeTime()
    c = HaikuClient("claude-haiku-5-5", 8000, clock=ft.clock, sleep=ft.sleep)
    _, recs = c.create("S", "U")
    assert SENTINEL not in json.dumps(recs) and SENTINEL not in repr(c) and SENTINEL not in str(vars(c))
    with pytest.raises(FatalAPIError) as ei:
        c.create("S", "U")
    assert SENTINEL not in str(ei.value) and SENTINEL not in repr(ei.value)
    assert SENTINEL not in json.dumps(ei.value.records) and SENTINEL not in repr(c)
    te = client_mod._scrub(TransportError("server", 500, None, f"x {SENTINEL}"), SENTINEL)
    assert SENTINEL not in te.message and SENTINEL not in repr(te)


# ---------------------------------------------------------------- hardening round

class _FakeRawBody:
    def __init__(self, content=None, json_body=None, headers=None):
        kw = {"content": content} if content is not None else {"json": json_body}
        self.http_response = httpx.Response(200, request=httpx.Request("POST", "https://x"), **kw)
        self.headers = headers or {}


@pytest.mark.parametrize("make", [
    lambda: _FakeRawBody(content=b"<html>gateway page SECRETBODY</html>"),
    lambda: _FakeRawBody(content=b""),
    lambda: _FakeRawBody(json_body=[1, 2]),
    lambda: _FakeRawBody(json_body="text"),
    lambda: _FakeRawBody(json_body=None),
])
def test_malformed_200_body_is_retried_as_server_error(monkeypatch, make):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SENTINEL)
    n = []

    def behaviour(kw):
        n.append(1)
        return make() if len(n) == 1 else _FakeRaw(_api_json('{"bid": 4}'), {})

    _install_fake_sdk(monkeypatch, behaviour)
    ft = FakeTime()
    c = HaikuClient("claude-haiku-5-5", 8000, clock=ft.clock, sleep=ft.sleep, jitter_seed=0)
    with pytest.raises(TransportError) as ei:
        c._transport(c.request_kwargs("S", "U"))
    assert ei.value.kind == "server" and ei.value.status is None
    assert "SECRETBODY" not in ei.value.message and "SECRETBODY" not in str(ei.value)
    n.clear()
    resp, recs = c.create("S", "U")
    assert parse_bid(resp)[:2] == (4.0, "")
    assert [r["reason"] for r in recs] == ["server_error", ""] and len(ft.sleeps) == 1


@pytest.mark.parametrize("body", [None, [], "x", 5, {"content": "oops"}, {"content": None},
                                  {"content": [1, "a", None]}, {"content": {"type": "text"}}])
def test_response_from_json_is_robust(body):
    resp = response_from_json(body, {"A": "b"})
    assert resp.content == [] and resp.headers == {"a": "b"}
    assert parse_bid(resp) == (None, "no_text", None)


def test_response_from_json_skips_bad_blocks_and_fields():
    resp = response_from_json({"content": ["junk", {"type": "text", "text": '{"bid": 2}'},
                                           {"type": "redacted_thinking"}],
                               "stop_reason": 5, "usage": "x"})
    assert resp.content == [{"type": "text", "text": '{"bid": 2}'},
                            {"type": "redacted_thinking", "text": ""}]
    assert resp.stop_reason is None and resp.usage == {}
    assert parse_bid(resp)[:2] == (2.0, "")


@pytest.mark.parametrize("retry_after", [0, 0.0, -3.0, None])
def test_zero_retry_after_does_not_hot_loop(retry_after):
    n = []

    def transport(kwargs):
        n.append(1)
        if len(n) <= 3:
            raise TransportError("rate_limit", 429, retry_after, "wait")
        return _api_json(), {}

    c, ft = _client(transport)
    resp, recs = c.create("S", "U")
    assert resp is not None and len(n) == 4
    assert len(ft.sleeps) == 3 and all(s >= 1.0 for s in ft.sleeps)
    assert ft.sleeps[0] >= 2.0  # falls back to the backoff schedule


def test_small_positive_retry_after_is_floored_at_one_second():
    n = []

    def transport(kwargs):
        n.append(1)
        if len(n) == 1:
            raise TransportError("rate_limit", 429, 0.2, "wait")
        return _api_json(), {}

    c, ft = _client(transport)
    c.create("S", "U")
    assert ft.sleeps == [1.0]


def test_server_error_keeps_retry_after():
    e = map_sdk_error(anthropic.APIStatusError("s", response=_httpx_resp(503, {"retry-after": "9"}),
                                               body=None))
    assert (e.kind, e.status, e.retry_after) == ("server", 503, 9.0)
    n = []

    def transport(kwargs):
        n.append(1)
        if len(n) == 1:
            raise e
        return _api_json(), {}

    c, ft = _client(transport)
    c.create("S", "U")
    assert ft.sleeps == [9.0]


def test_failed_attempt_records_carry_ratelimit_headers():
    err = map_sdk_error(anthropic.RateLimitError(
        "rl", response=_httpx_resp(429, {"Retry-After": "1",
                                         "Anthropic-RateLimit-Requests-Remaining": "0",
                                         "anthropic-ratelimit-input-tokens-remaining": "123",
                                         "anthropic-ratelimit-output-tokens-remaining": "x"}), body=None))
    assert err.headers["anthropic-ratelimit-requests-remaining"] == "0"
    n = []

    def transport(kwargs):
        n.append(1)
        if len(n) == 1:
            raise err
        return _api_json(), {}

    c, _ = _client(transport)
    _, recs = c.create("S", "U")
    assert recs[0]["reason"] == "http_429"
    assert recs[0]["ratelimit_requests_remaining"] == 0
    assert recs[0]["ratelimit_input_tokens_remaining"] == 123
    assert recs[0]["ratelimit_output_tokens_remaining"] is None
    assert recs[0]["input_tokens"] is None


def test_huge_numbers_are_bad_value_not_exceptions():
    huge = "1" + "0" * 399
    assert parse_bid(_resp('{"bid": %s}' % huge))[:2] == (None, "bad_value")
    assert parse_bid(_resp('{"bid": "%s"}' % huge))[:2] == (None, "bad_value")
    assert parse_bid(_resp('{"bid": 1e999}'))[:2] == (None, "bad_value")
    msg, reason = parse_message(_resp('{"my_cost": %s, "message_to_partner": "hi"}' % huge))
    assert reason == "" and msg["my_cost"] is None and msg["message_to_partner"] == "hi"


def test_status_less_errors_never_emit_http_none():
    for kind, want in (("client", "http_error"), ("server", "server_error")):
        def transport(kwargs, kind=kind):
            raise TransportError(kind, None, None, "odd")

        c, _ = _client(transport)
        resp, recs = c.create("S", "U")
        assert resp is None
        assert all("None" not in r["reason"] for r in recs)
        if kind == "client":
            assert [r["reason"] for r in recs] == [want]
        else:
            assert recs[0]["reason"] == want and recs[-1]["reason"] == "transport_failed"


@pytest.mark.parametrize("status", [408, 409])
def test_408_and_409_are_client_errors_not_retried(status):
    e = map_sdk_error(anthropic.APIStatusError("c", response=_httpx_resp(status), body=None))
    assert (e.kind, e.status) == ("client", status)

    def transport(kwargs):
        raise e

    c, ft = _client(transport)
    resp, recs = c.create("S", "U")
    assert resp is None and len(recs) == 1 and ft.sleeps == [] and recs[0]["reason"] == f"http_{status}"
