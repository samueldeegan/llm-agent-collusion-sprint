"""The LLM bidder and its integration with the runner and the channel, driven by ``StubClient``.

Nothing here calls the API: the autouse guard makes constructing an Anthropic client raise, and every
LLM path gets a stub (or a ``HaikuClient`` with a fake transport)."""
import csv
import json
import time
from dataclasses import replace as dataclasses_replace
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from src.procurement import config, llm_bidder, texts
from src.procurement.auction import render_pre_posting
from src.procurement.client import FatalAPIError, HaikuClient, TransportError
from src.procurement.config import RunConfig
from src.procurement.environment import cost_of, generate_environment
from src.procurement.io import read_checkpoint, read_run_meta
from src.procurement.llm_bidder import LLMBidder, llm_bidder_factory
from src.procurement.rules import rotation_roles
from src.procurement.runner import bidder_factory_for, run, run_in_memory
from src.procurement.synthetic import synthetic_bidder_factory
from src.procurement.types import BidContext
from tests.stubs import STUB_MESSAGE_TEXT, TRANSPORT_FAILED, StubClient, ok_record

T = 12
COVER = RunConfig(condition="cover", seed=1, T=T, burn_in=2, bidder="llm")
CONTROL = RunConfig(condition="control", seed=1, T=T, burn_in=2, bidder="llm")
SEATS = (0, 3)  # cover seed 1
ENV = generate_environment(1, COVER.cost, T, 6)
NAMES = ENV.names
RUN_FILES = ["bids.csv", "truth.csv", "messages.csv", "calls.csv", "run_meta.json",
             "heartbeat.json", "checkpoint.json"]


def stub_factory(stub: StubClient, holder: list | None = None):
    def factory(cfg, env):
        stub.names = list(env.names)
        bidder = llm_bidder_factory(cfg, env, client=stub)
        if holder is not None:
            holder.append(bidder)
        return bidder
    return factory


def _rows(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _only_dir(root: Path) -> Path:
    (d,) = [p for p in root.iterdir() if p.is_dir()]
    return d


def _ctx(cfg: RunConfig, seat: int, t: int) -> BidContext:
    caps = np.zeros(cfg.N)
    return BidContext(run=cfg, env=ENV, t=t, seat=seat, cap=caps, cost=cost_of(ENV, cfg.cost, seat, t, 0.0),
                      history=[], messages=[], memory=[], scratchpad="", role=None, seats=None,
                      posting=render_pre_posting(ENV, t, caps, ENV.names))


def _memory_lines(user: str) -> list[str]:
    section = user.split("# Your memory\n", 1)[1].split("\n# Project", 1)[0]
    return [line for line in section.splitlines() if line.strip()]


# ------------------------------------------------------------------ unit: memory and scratchpad

def test_memory_kept_only_if_json():
    stub = StubClient(NAMES, replies={
        (2, "bid", 1): json.dumps({"bid": "abc", "new_memory": "kept", "scratch_pad_update": "pad"}),
        (2, "bid", 2): '{"bid": 900000, "new_memory": "lost", "scratch_pad_update": "lost"',  # invalid
        (2, "bid", 3): json.dumps({"bid": 900000, "new_memory": "", "scratch_pad_update": ""}),
    })
    b = LLMBidder(CONTROL, ENV, 2, stub)
    r1 = b.bid(_ctx(CONTROL, 2, 0))
    assert r1.bid is None and r1.reason == "bad_value" and r1.parsed["new_memory"] == "kept"
    assert b.get_state() == {"memories": {"1": "kept"}, "scratchpad": "pad"}

    r2 = b.bid(_ctx(CONTROL, 2, 1))
    assert r2.bid is None and r2.reason == "bad_json" and r2.parsed is None
    assert b.get_state() == {"memories": {"1": "kept"}, "scratchpad": "pad"}  # neither updated
    _, user2 = stub.prompts[(2, "bid", 2)]
    assert _memory_lines(user2) == ["- Project 1: kept"]
    assert "# Strategy scratchpad\npad\n" in user2

    r3 = b.bid(_ctx(CONTROL, 2, 2))
    assert r3.bid == 900000.0 and r3.reason == ""
    assert b.get_state() == {"memories": {"1": "kept"}, "scratchpad": "pad"}  # empty strings: no change
    assert stub.count(2, "bid", 1) == stub.count(2, "bid", 2) == stub.count(2, "bid", 3) == 1


def test_memory_window_rendered():
    cfg = RunConfig(condition="control", seed=1, T=20, bidder="llm")
    env20 = generate_environment(1, cfg.cost, 20, 6)
    stub = StubClient(env20.names)
    b = LLMBidder(cfg, env20, 4, stub)
    b.set_state({"memories": {str(p): f"m{p}" for p in range(1, 16)}, "scratchpad": "x"})
    caps = np.zeros(6)
    ctx = BidContext(run=cfg, env=env20, t=15, seat=4, cap=caps, cost=cost_of(env20, cfg.cost, 4, 15, 0.0),
                     history=[], messages=[], memory=[], scratchpad="", role=None, seats=None,
                     posting=render_pre_posting(env20, 15, caps, env20.names))
    b.bid(ctx)
    _, user = stub.prompts[(4, "bid", 16)]
    assert _memory_lines(user) == [f"- Project {p}: m{p}" for p in range(6, 16)]
    assert "# Strategy scratchpad\nx\n" in user  # set_state restores the scratchpad too
    assert ctx.memory == [] and ctx.scratchpad == ""  # the runner's ctx is not mutated


def test_missing_response_takes_last_record_reason():
    class Http400:
        def create(self, system, user):
            return None, [dict(ok_record(), reason="http_400")]

    r = LLMBidder(CONTROL, ENV, 1, Http400()).bid(_ctx(CONTROL, 1, 0))
    assert r.bid is None and r.reason == "http_400" and r.raw is None
    assert r.call_records[-1]["reason"] == "http_400"
    assert r.prompt.startswith("[system]\n# Introduction\n") and "\n\n[user]\n# Your memory" in r.prompt


def test_message_result_and_no_memory_update():
    stub = StubClient(NAMES, replies={
        (0, "message", 1): json.dumps({"my_cost": "912,345", "intended_serious_bid": 958000,
                                       "message_to_partner": "go", "new_memory": "nope"})})
    b = LLMBidder(COVER, ENV, 0, stub)
    caps = np.zeros(6)
    ctx = BidContext(run=COVER, env=ENV, t=0, seat=0, cap=caps, cost=cost_of(ENV, COVER.cost, 0, 0, 0.0),
                     history=[], messages=[], memory=[], scratchpad="", role=None, seats=SEATS,
                     posting=render_pre_posting(ENV, 0, caps, ENV.names))
    m = b.message(ctx)
    assert (m.text, m.my_cost, m.intended_serious_bid, m.reason) == ("go", 912345.0, 958000.0, "")
    assert b.get_state() == {"memories": {}, "scratchpad": ""}
    system, _ = stub.prompts[(0, "message", 1)]
    assert NAMES[3] in system and "# Private agreement" in system


# ------------------------------------------------------------------ factory and swap

def test_bidder_factory_for():
    assert bidder_factory_for(RunConfig(condition="control", seed=1)) is synthetic_bidder_factory
    assert bidder_factory_for(RunConfig(condition="control", seed=1, bidder="llm")) is llm_bidder_factory
    with pytest.raises(ValueError, match="bidder"):
        bidder_factory_for(RunConfig(condition="control", seed=1, bidder="gpt"))


def test_bidder_swap(tmp_path, monkeypatch):
    built = []

    class Recording(StubClient):
        def __init__(self, model, max_tokens, api_key_slot):
            super().__init__(NAMES)
            built.append(self)
            self.args = (model, max_tokens, api_key_slot)

    monkeypatch.setattr(llm_bidder, "HaikuClient", Recording)
    syn = RunConfig(condition="cover", seed=1, T=3)
    run(syn, bidder_factory_for(syn), results_root=tmp_path / "syn")
    assert built == []

    llm = RunConfig(condition="cover", seed=1, T=3, bidder="llm", api_key_slot=2)
    d = run(llm, bidder_factory_for(llm), results_root=tmp_path / "llm")
    assert len(built) == 1 and built[0].args == ("claude-haiku-5-5", 8000, 2)
    assert len(built[0].calls) == 3 * (6 + 2)
    assert read_run_meta(d)["bidder"] == "llm"


# ------------------------------------------------------------------ runs with the stub

def test_llm_cover_run_files_with_stub(tmp_path):
    stub = StubClient()
    d = run(COVER, stub_factory(stub), results_root=tmp_path)
    for name in RUN_FILES:
        assert (d / name).exists(), name
    bids, msgs, calls = _rows(d / "bids.csv"), _rows(d / "messages.csv"), _rows(d / "calls.csv")
    assert len(bids) == T * 6 and all(r["bid"] != "" for r in bids)
    assert len(msgs) == T * 2 and all(r["reason"] == "" for r in msgs)
    assert msgs[0]["text"] == ("Cost: $1,000,000. If I am the serious bidder I intend to bid $1,050,000. "
                               + STUB_MESSAGE_TEXT)
    assert len(calls) == len(bids) + len(msgs)
    assert sum(r["phase"] == "bid" for r in calls) == T * 6
    assert sum(r["phase"] == "message" for r in calls) == T * 2
    assert len(stub.calls) == len({c[2:] for c in stub.calls}) == T * 8  # one call each, no re-prompt

    # stub bids are 1.05 x the cost shown in the prompt
    by_key = {(int(r["project_id"]), r["firm_id"]): float(r["bid"]) for r in bids}
    _, user = stub.prompts[(5, "bid", 4)]
    assert by_key[(4, NAMES[5])] == round(StubClient.cost_of(user) * 1.05, 2)

    files = sorted(p.name for p in (d / "transcripts").iterdir())
    assert files == sorted(f"{n}.md" for n in NAMES)
    for seat, name in enumerate(NAMES):
        text = (d / "transcripts" / f"{name}.md").read_text(encoding="utf-8")
        assert text.count("(bid) - ") == 2 * T
        assert text.count("(message) - ") == (2 * T if seat in SEATS else 0)

    # system prefixes: byte-stable per seat and phase; cartel block only for members
    for seat in range(6):
        for phase in ("bid", "message"):
            systems = {stub.prompts[k][0] for k in stub.prompts if k[0] == seat and k[1] == phase}
            assert len(systems) == (1 if phase == "bid" or seat in SEATS else 0)
        bid_system = stub.prompts[(seat, "bid", 1)][0]
        if seat in SEATS:
            partner = NAMES[SEATS[1] if seat == SEATS[0] else SEATS[0]]
            assert "# Private agreement" in bid_system and partner in bid_system
        else:
            assert "partner" not in bid_system and "# Private agreement" not in bid_system

    # memory and scratchpad carried into later prompts; checkpoint holds them
    _, user12 = stub.prompts[(1, "bid", 12)]
    assert _memory_lines(user12) == [f"- Project {p}: m{p}" for p in range(2, 12)]
    assert "# Strategy scratchpad\ns11\n" in user12
    state = read_checkpoint(d)["bidder_state"]
    assert set(state) == {str(s) for s in range(6)}
    assert state["1"]["memories"] == {str(p): f"m{p}" for p in range(1, 13)}
    assert state["1"]["scratchpad"] == "s12"


def test_transcripts_hold_real_prompt(tmp_path):
    stub = StubClient()
    d = run(CONTROL, stub_factory(stub), results_root=tmp_path)
    text = (d / "transcripts" / f"{NAMES[1]}.md").read_text(encoding="utf-8")
    system, user = stub.prompts[(1, "bid", 3)]
    assert f"``````\n[system]\n{system}\n\n[user]\n{user}\n``````" in text
    assert text.startswith("\n## Prompt: Project 1 (bid)")
    raw = json.dumps(StubClient.default_bid(StubClient.cost_of(user), 3))
    assert f"````\n{raw}\n````" in text


def test_one_message_each_no_reprompt(tmp_path):
    stub = StubClient(replies={(0, "message", 3): "I refuse to answer in JSON."})
    d = run(COVER, stub_factory(stub), results_root=tmp_path)
    msgs = _rows(d / "messages.csv")
    (bad,) = [r for r in msgs if r["project_id"] == "3" and r["sender"] == NAMES[0]]
    assert bad["order"] == "1" and bad["text"] == "" and bad["reason"] == "bad_json"
    assert bad["my_cost"] == "" and bad["intended_serious_bid"] == ""
    assert stub.count(0, "message", 3) == 1
    for p in range(1, T + 1):
        for s in SEATS:
            assert stub.count(s, "message", p) == 1
    bids = _rows(d / "bids.csv")
    assert len(bids) == T * 6
    assert all(r["bid"] != "" for r in bids if r["project_id"] == "3")  # bidding proceeds
    calls = [r for r in _rows(d / "calls.csv")
             if r["project_id"] == "3" and r["firm_id"] == NAMES[0] and r["phase"] == "message"]
    assert len(calls) == 1 and calls[0]["reason"] == "bad_json"
    _, partner_bid_user = stub.prompts[(3, "bid", 3)]
    assert f"- From {NAMES[0]}: \n" in partner_bid_user  # the empty message was delivered


def test_outsiders_parallel_with_message():
    first = {1: 0, 2: 3, 3: 0}  # cover: odd projects seat a speaks first, even seat b
    stub = StubClient(delays={(seat, "message", p): 0.2 for p, seat in first.items()})
    cfg = RunConfig(condition="cover", seed=1, T=3, bidder="llm")
    run_in_memory(cfg, stub_factory(stub))
    for p in (1, 2, 3):
        ev = {(c[2], c[3]): c for c in stub.calls if c[4] == p}
        second = 3 if first[p] == 0 else 0
        m1, m2 = ev[(first[p], "message")], ev[(second, "message")]
        outsiders = [ev[(s, "bid")] for s in range(6) if s not in SEATS]
        assert len(outsiders) == 4
        assert max(o[0] for o in outsiders) < m1[1]  # all four start while message 1 is in flight
        assert max(o[0] for o in outsiders) < m2[0]  # ... and before message 2 is issued
        assert m2[0] >= m1[1]
        assert min(ev[(s, "bid")][0] for s in SEATS) >= m2[1]


def test_transport_failed_bid_is_missing_in_table(tmp_path):
    stub = StubClient(replies={(2, "bid", 5): TRANSPORT_FAILED})
    d = run(COVER, stub_factory(stub), results_root=tmp_path)
    bids = _rows(d / "bids.csv")
    (row,) = [r for r in bids if r["project_id"] == "5" and r["firm_id"] == NAMES[2]]
    assert row["bid"] == "" and row["won"] == "0"
    assert sum(r["bid"] == "" for r in bids) == 1
    raw_line = [ln for ln in (d / "bids.csv").read_text(encoding="utf-8").splitlines()
                if ln.startswith(f"cover_s1,5,{NAMES[2]},")]
    assert len(raw_line) == 1 and ",,0" in raw_line[0]  # the empty field, not NaN
    (call,) = [r for r in _rows(d / "calls.csv")
               if r["project_id"] == "5" and r["firm_id"] == NAMES[2] and r["phase"] == "bid"]
    assert call["reason"] == "transport_failed"
    text = (d / "transcripts" / f"{NAMES[2]}.md").read_text(encoding="utf-8")
    assert "(no response: transport_failed)" in text
    state = read_checkpoint(d)["bidder_state"]["2"]
    assert "5" not in state["memories"] and len(state["memories"]) == T - 1


def test_content_reason_on_last_record(tmp_path):
    stub = StubClient(replies={(4, "bid", 2): "no braces at all"})
    d = run(COVER, stub_factory(stub), results_root=tmp_path)
    (call,) = [r for r in _rows(d / "calls.csv")
               if r["project_id"] == "2" and r["firm_id"] == NAMES[4] and r["phase"] == "bid"]
    assert call["reason"] == "bad_json" and call["stop_reason"] == "end_turn"
    assert sum(r["reason"] != "" for r in _rows(d / "calls.csv")) == 1
    (row,) = [r for r in _rows(d / "bids.csv") if r["project_id"] == "2" and r["firm_id"] == NAMES[4]]
    assert row["bid"] == ""


def test_fatal_api_error_propagates(tmp_path):
    def auth_fail(kwargs):
        raise TransportError("auth", 401, None, "invalid x-api-key")

    client = HaikuClient(CONTROL.model, CONTROL.max_tokens, 1, transport=auth_fail)

    def factory(cfg, env):
        return llm_bidder_factory(cfg, env, client=client)

    with pytest.raises(FatalAPIError):
        run(CONTROL, factory, results_root=tmp_path)
    d = _only_dir(tmp_path)
    assert _rows(d / "bids.csv") == []
    assert read_run_meta(d)["finished_utc"] is None
    calls = _rows(d / "calls.csv")  # the six fatal attempts of the aborted project are still logged
    assert len(calls) == 6 and {r["reason"] for r in calls} == {"http_401"}
    assert {r["firm_id"] for r in calls} == set(NAMES) and {r["project_id"] for r in calls} == {"1"}


def test_aborted_project_logs_completed_calls(tmp_path):
    fatal = FatalAPIError("API authentication failed (status 401)",
                          [dict(ok_record("http_401"), input_tokens=None, output_tokens=None, stop_reason=None)])
    stub = StubClient(replies={(2, "bid", 3): fatal})  # seat 2: an outsider in cover seed 1
    with pytest.raises(FatalAPIError):
        run(COVER, stub_factory(stub), results_root=tmp_path)
    d = _only_dir(tmp_path)
    calls = _rows(d / "calls.csv")
    assert sum(r["project_id"] in ("1", "2") for r in calls) == 2 * 8
    p3 = [r for r in calls if r["project_id"] == "3"]
    assert len(p3) == 8  # 2 messages + 5 completed bids + the fatal attempt; nothing lost
    assert [r["phase"] for r in p3[:2]] == ["message", "message"]
    assert [r["firm_id"] for r in p3[2:]] == [NAMES[s] for s in range(6)]  # bids by seat
    (bad,) = [r for r in p3 if r["reason"] != ""]
    assert (bad["firm_id"], bad["phase"], bad["reason"]) == (NAMES[2], "bid", "http_401")
    assert stub.count(0, "bid", 3) == stub.count(3, "bid", 3) == 1  # issued calls finished first
    # nothing else of the aborted project is written
    assert {r["project_id"] for r in _rows(d / "bids.csv")} == {"1", "2"}
    assert {r["project_id"] for r in _rows(d / "truth.csv")} == {"1", "2"}
    assert {r["project_id"] for r in _rows(d / "messages.csv")} == {"1", "2"}
    assert read_checkpoint(d)["last_completed_project"] == 2
    assert all("Project 3 (" not in p.read_text(encoding="utf-8") for p in (d / "transcripts").iterdir())


# ------------------------------------------------------------------ activity heartbeat (A52)

def _read_json_retry(path: Path) -> dict:
    for _ in range(50):  # a reader racing os.replace on Windows can see a transient PermissionError
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except PermissionError:
            time.sleep(0.01)
    return json.loads(path.read_text(encoding="utf-8"))


def test_activity_touched_on_every_call(tmp_path):
    cfg = RunConfig(condition="cover", seed=1, T=3, bidder="llm")

    class Probe(StubClient):
        """Reads the heartbeat as each call returns: its last_activity must postdate the call's start."""

        def __init__(self):
            keys = [(s, "bid", p) for s in range(6) for p in (1, 2, 3)]
            keys += [(s, ph, p) for s in SEATS for ph in ("message",) for p in (1, 2, 3)]
            super().__init__(delays={k: 0.02 for k in keys})
            self.seen = []

        def create(self, system, user):
            t0 = datetime.now(timezone.utc)
            out = super().create(system, user)  # pings on_attempt at its start and end
            hb = _read_json_retry(_only_dir(tmp_path) / "heartbeat.json")
            with self.lock:
                self.seen.append((self.project_of(user), t0, hb))
            return out

    stub = Probe()
    d = run(cfg, stub_factory(stub), results_root=tmp_path)
    assert stub.on_attempt is not None
    assert len(stub.seen) == 3 * 8
    for pid, t0, hb in stub.seen:
        assert datetime.fromisoformat(hb["last_activity_utc"]) > t0  # touched inside the call
        assert hb["last_completed_project"] == pid - 1
    hb = json.loads((d / "heartbeat.json").read_text(encoding="utf-8"))  # valid JSON after concurrent touches
    assert hb["last_completed_project"] == 3 and not (d / "heartbeat.json.tmp").exists()

    mem = StubClient()
    run_in_memory(cfg, stub_factory(mem))
    assert mem.on_attempt is None  # no run dir, no callback


# ------------------------------------------------------------------ prefix cache guard and isolation

def test_prefix_cache_guard():
    stub = StubClient(NAMES)
    b = LLMBidder(COVER, ENV, 0, stub)
    member = dataclasses_replace(_ctx(COVER, 0, 0), seats=SEATS)
    b.message(member)
    b.bid(member)  # same (scheme, partner, seats): reuses the cache
    with pytest.raises(ValueError, match="prefixes cached"):
        b.bid(_ctx(COVER, 0, 1))  # now as an outsider
    other = LLMBidder(COVER, ENV, 0, StubClient(NAMES))
    other.bid(_ctx(COVER, 0, 0))
    with pytest.raises(ValueError, match="prefixes cached"):
        other.message(dataclasses_replace(_ctx(COVER, 0, 1), seats=SEATS))


def test_cover_outsider_prompts_isolated(tmp_path):
    stub = StubClient()
    d = run(COVER, stub_factory(stub), results_root=tmp_path)
    for seat, name in enumerate(NAMES):
        text = (d / "transcripts" / f"{name}.md").read_text(encoding="utf-8")
        if seat in SEATS:  # positive control: members do see the messages
            assert STUB_MESSAGE_TEXT in text and "# Messages this project" in text
            continue
        for needle in (STUB_MESSAGE_TEXT, "# Messages this project", "# Your role this project"):
            assert needle not in text, (name, needle)
    for (seat, phase, _), (system, user) in stub.prompts.items():
        if seat not in SEATS:
            assert STUB_MESSAGE_TEXT not in system + user


def test_rotation_member_role_lines():
    cfg = RunConfig(condition="rotation", seed=1, T=4, bidder="llm")
    a, b = config.cartel_seats(1, "rotation")
    stub = StubClient()
    run_in_memory(cfg, stub_factory(stub))
    for p in range(1, 5):
        serious, cover = rotation_roles(p, a, b)
        for seat, role in ((serious, "serious bidder"), (cover, "cover bidder")):
            other = "cover bidder" if role == "serious bidder" else "serious bidder"
            for phase in ("bid", "message"):
                _, user = stub.prompts[(seat, phase, p)]
                assert texts.ROTATION_ROLE_LINE.format(t=p, role=role) in user, (seat, phase, p)
                assert texts.ROTATION_ROLE_LINE.format(t=p, role=other) not in user
        for seat in set(range(6)) - {a, b}:
            assert "so you are the" not in stub.prompts[(seat, "bid", p)][1]


# ------------------------------------------------------------------ state and resume

def test_state_round_trips_through_json():
    holder = []
    stub = StubClient()
    run_in_memory(RunConfig(condition="cover", seed=1, T=4, bidder="llm"), stub_factory(stub, holder))
    state = holder[0].get_state()
    again = llm_bidder_factory(COVER, ENV, client=StubClient(NAMES))
    again.set_state(json.loads(json.dumps(state)))
    assert again.get_state() == state
    assert state["3"] == {"memories": {"1": "m1", "2": "m2", "3": "m3", "4": "m4"}, "scratchpad": "s4"}


def test_resume_keeps_llm_memory(tmp_path):
    ref_stub = StubClient()
    ref = run(COVER, stub_factory(ref_stub), results_root=tmp_path / "ref")

    crash = StubClient(replies={(1, "bid", 7): RuntimeError("crash")})
    with pytest.raises(RuntimeError, match="crash"):
        run(COVER, stub_factory(crash), results_root=tmp_path / "runs")
    old = _only_dir(tmp_path / "runs")
    cp = read_checkpoint(old)
    assert cp["last_completed_project"] == 6
    assert cp["bidder_state"]["1"]["memories"] == {str(p): f"m{p}" for p in range(1, 7)}

    resumed_stub = StubClient()
    new = run(COVER, stub_factory(resumed_stub), resume_dir=old)
    assert min(c[4] for c in resumed_stub.calls) == 7
    for seat in range(6):
        for phase in ("bid", "message"):
            key = (seat, phase, 7)
            if phase == "message" and seat not in SEATS:
                continue
            assert resumed_stub.prompts[key] == ref_stub.prompts[key], key
        assert _memory_lines(resumed_stub.prompts[(seat, "bid", 7)][1]) == \
            [f"- Project {p}: m{p}" for p in range(1, 7)]
    assert (new / "bids.csv").read_bytes() == (ref / "bids.csv").read_bytes()
    assert read_checkpoint(new)["bidder_state"] == read_checkpoint(ref)["bidder_state"]


def test_scratchpad_disabled_never_updates():
    cfg = dataclasses_replace(CONTROL, memory_window=0, scratchpad_enabled=False)
    reply = '{"reflection":"r","plan":"p","bid":650000,"new_memory":"REMEMBER","scratch_pad_update":"PAD"}'
    stub = StubClient(NAMES, replies={(0, "bid", 1): reply, (0, "bid", 2): reply})
    bidder = LLMBidder(cfg, ENV, 0, stub)
    first = bidder.bid(_ctx(cfg, 0, 0))
    second = bidder.bid(_ctx(cfg, 0, 1))
    assert first.bid == 650000.0 and bidder.memories == {} and bidder.scratchpad == ""
    assert bidder.get_state() == {"memories": {}, "scratchpad": ""}
    assert "REMEMBER" not in second.prompt and "PAD" not in second.prompt
