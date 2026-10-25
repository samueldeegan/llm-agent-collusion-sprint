"""Scheme runs on the synthetic bidder, checkpoint resume, and equivalence with ``simulate_table``."""
import json
import re
import threading
import time
from pathlib import Path

import pandas as pd
import pytest

from src.procurement import io
from src.procurement.channel import Channel
from src.procurement.config import CostParams, RunConfig
from src.procurement.environment import generate_environment
from src.procurement.io import (BIDS_COLUMNS, CALLS_COLUMNS, MESSAGES_COLUMNS, TRUTH_COLUMNS,
                                read_checkpoint, read_run_meta)
from src.procurement.rules import cover_roles, rotation_roles
from src.procurement.runner import _transcript_upto, resolve_cartel_seats, run, run_in_memory
from src.procurement.synthetic import (SyntheticBidder, honest_bid, noise_array, simulate_table,
                                       synthetic_bidder_factory)
from src.procurement.types import MessageResult
from tests.conftest import api_guard

COST = CostParams(0.90, 0.08, 0.17, 0.05)
SCHEMES = ("cover", "rotation", "markup")
SEATS_SEED1 = {"cover": (0, 3), "rotation": (1, 4), "markup": (2, 5)}
RUN_FILES = ["bids.csv", "truth.csv", "messages.csv", "calls.csv", "run_meta.json",
             "heartbeat.json", "checkpoint.json"]
CSV_FILES = ["bids.csv", "truth.csv", "messages.csv", "calls.csv"]
META_KEYS = {"purpose", "condition", "scheme", "seed", "T", "burn_in", "cartel_seats", "seat_to_firm",
             "cost", "windows", "model", "max_tokens", "effort", "bidder", "concurrency",
             "api_key_slot", "frozen_from", "limits", "started_utc", "finished_utc", "git_commit",
             "scripted"}
ENV1 = generate_environment(1, COST, 110, 6)


@pytest.fixture(scope="module")
def scheme_runs(tmp_path_factory):
    root = tmp_path_factory.mktemp("scheme_runs")
    with api_guard():  # the function-scoped guard is not active in module fixtures
        return {c: run(RunConfig(condition=c, seed=1), synthetic_bidder_factory, results_root=root / c)
                for c in SCHEMES}


def _only_dir(root: Path) -> Path:
    (d,) = [p for p in root.iterdir() if p.is_dir()]
    return d


def _snapshot(d: Path) -> dict:
    return {p.relative_to(d).as_posix(): p.read_bytes() for p in d.rglob("*") if p.is_file()}


def _delivered(my_cost: float, intended: float) -> str:
    return f"Cost: ${my_cost:,.0f}. If I am the serious bidder I intend to bid ${intended:,.0f}."


# ------------------------------------------------------------------ files and messages

def test_messages_and_meta(scheme_runs, tmp_path):
    d = scheme_runs["cover"]
    a, b = ENV1.names[0], ENV1.names[3]
    m = pd.read_csv(d / "messages.csv", dtype=str, keep_default_na=False)
    assert list(m.columns) == MESSAGES_COLUMNS
    assert len(m) == 220
    assert set(m["run_id"]) == {"cover_s1"}
    assert list(m["project_id"].astype(int)) == [p for p in range(1, 111) for _ in (1, 2)]
    assert list(m["order"].astype(int)) == [1, 2] * 110
    assert set(m["sender"]) == {a, b} and set(m["recipient"]) == {a, b}
    assert (m["sender"] != m["recipient"]).all()
    first = m[m["order"] == "1"]
    assert list(first["sender"]) == [a if p % 2 == 1 else b for p in range(1, 111)]
    assert (m["reason"] == "").all()
    assert (m["my_cost"] != "").all() and (m["intended_serious_bid"] != "").all()
    for _, r in m.iterrows():
        assert r["text"] == _delivered(float(r["my_cost"]), float(r["intended_serious_bid"]))

    meta = read_run_meta(d)
    assert set(meta) == META_KEYS  # no resumed_from on a fresh run
    assert meta["condition"] == "cover" and meta["scheme"] == "cover"
    assert meta["cartel_seats"] == [0, 3]
    assert meta["finished_utc"] is not None

    ctrl = run(RunConfig(condition="control", seed=1), synthetic_bidder_factory, results_root=tmp_path)
    lines = (ctrl / "messages.csv").read_text(encoding="utf-8").splitlines()
    assert lines == [",".join(MESSAGES_COLUMNS)]


def test_scripted_messenger_in_suite(scheme_runs):
    d = scheme_runs["cover"]
    b = pd.read_csv(d / "bids.csv", float_precision="round_trip")
    t = pd.read_csv(d / "truth.csv", keep_default_na=False, float_precision="round_trip")
    m = pd.read_csv(d / "messages.csv", keep_default_na=False, float_precision="round_trip")
    noise = noise_array(1, COST, 110, 6)

    def check(p):
        tp, bp = t[t.project_id == p], b[b.project_id == p].set_index("firm_id")
        est = float(bp["est"].iloc[0])
        cov = tp[tp.cartel_role == "cover"].iloc[0]
        ser = tp[tp.cartel_role == "serious"].iloc[0]
        h = honest_bid(float(cov.cost), est, float(noise[int(cov.seat), p - 1]))
        partner = m[(m.project_id == p) & (m.sender == ser.firm_id)]
        assert len(partner) == 1 and partner.recipient.iloc[0] == cov.firm_id
        s = float(partner.intended_serious_bid.iloc[0])
        assert s == bp.loc[ser.firm_id, "bid"]
        assert bp.loc[cov.firm_id, "bid"] == round(max(h + 0.10 * est, s + 0.01 * est), 2)
        # the cover member's own message carries its own (losing) intended bid, not s
        own = m[(m.project_id == p) & (m.sender == cov.firm_id)]
        assert float(own.my_cost.iloc[0]) == float(cov.cost)

    check(15)
    for p in range(1, 111):
        check(p)


def test_messages_never_leak(scheme_runs):
    for scheme, d in scheme_runs.items():
        header = (d / "bids.csv").read_text(encoding="utf-8").splitlines()[0].split(",")
        assert header == BIDS_COLUMNS
        for col in ("order", "sender", "recipient", "my_cost", "intended_serious_bid", "text", "reason"):
            assert col not in header
        body = (d / "bids.csv").read_text(encoding="utf-8")
        assert "Cost:" not in body and "intend" not in body

    rec = _Recorder()
    run_in_memory(RunConfig(condition="cover", seed=1, T=8), rec.factory)
    seats = (0, 3)
    for kind, ctx, _ in rec.calls:
        if ctx.seat not in seats:
            assert kind == "bid"
            assert ctx.messages == [] and ctx.role is None and ctx.seats is None


@pytest.mark.parametrize("scheme", SCHEMES)
def test_end_to_end_synthetic_each_scheme_files(scheme_runs, scheme):
    d = scheme_runs[scheme]
    a, b = SEATS_SEED1[scheme]
    for name in RUN_FILES:
        assert (d / name).is_file(), name
    assert d.name.endswith(f"_{scheme}_s1")
    bids = pd.read_csv(d / "bids.csv", dtype=str, keep_default_na=False)
    assert list(bids.columns) == BIDS_COLUMNS and len(bids) == 660
    assert (bids["bid"] != "").all()
    assert (bids["won"].astype(int).groupby(bids["project_id"]).sum() == 1).all()

    t = pd.read_csv(d / "truth.csv", keep_default_na=False, float_precision="round_trip")
    assert list(t.columns) == TRUTH_COLUMNS and len(t) == 660
    assert set(t["scheme"]) == {scheme}
    cartel = t[t.is_cartel == 1]
    assert set(cartel.seat) == {a, b}
    assert set(cartel.firm_id) == {ENV1.names[a], ENV1.names[b]}
    assert len(cartel) == 220
    assert set(t[t.is_cartel == 0].cartel_role) == {"none"}
    for p, tp in t.groupby("project_id"):
        roles = dict(zip(tp.seat, tp.cartel_role))
        if scheme == "markup":
            assert roles[a] == roles[b] == "member"
            continue
        if scheme == "cover":
            costs = dict(zip(tp.seat, tp.cost))
            ser, cov = cover_roles(costs[a], costs[b], a, b)
        else:
            ser, cov = rotation_roles(int(p), a, b)
        assert (roles[ser], roles[cov]) == ("serious", "cover")

    assert len(pd.read_csv(d / "messages.csv")) == 220
    calls = (d / "calls.csv").read_text(encoding="utf-8").splitlines()
    assert calls == [",".join(CALLS_COLUMNS)]
    meta = read_run_meta(d)
    assert meta["cartel_seats"] == [a, b] and meta["scheme"] == scheme
    cp = read_checkpoint(d)
    assert cp["last_completed_project"] == 110 and len(cp["messages"]) == 220
    assert "bidder_state" not in cp  # SyntheticBidder is stateless

    mb, mt, mm = run_in_memory(RunConfig(condition=scheme, seed=1), synthetic_bidder_factory)
    file_bids = pd.read_csv(d / "bids.csv", float_precision="round_trip")
    pd.testing.assert_frame_equal(file_bids, mb, check_dtype=False, check_exact=True)
    pd.testing.assert_frame_equal(t, mt, check_dtype=False, check_exact=True)
    msgs = pd.read_csv(d / "messages.csv", keep_default_na=False)
    assert len(mm) == 220
    assert list(msgs["sender"]) == [r["sender"] for r in mm]
    assert list(msgs["text"]) == [r["text"] for r in mm]


# ------------------------------------------------------------------ equivalence with the simulator

@pytest.mark.parametrize("cfg", [
    RunConfig(condition="control", seed=1),
    RunConfig(condition="cover", seed=1),
    RunConfig(condition="rotation", seed=1),
    RunConfig(condition="markup", seed=1),
    RunConfig(condition="cover", seed=1, scripted_rule="shadow"),
    RunConfig(condition="cover", seed=1, scripted_margin=0.0),
    RunConfig(condition="cover", seed=1, serious_add=0.05),
    RunConfig(condition="rotation", seed=3, scripted_margin=0.20),
    RunConfig(condition="cover", seed=7, cartel_seats=(0, 1)),
], ids=lambda c: f"{c.condition}-s{c.seed}-{c.scripted_rule}-m{c.scripted_margin}-a{c.serious_add}-{c.cartel_seats}")
def test_runner_equals_simulate_table(cfg):
    bids, truth, messages = run_in_memory(cfg, synthetic_bidder_factory)
    scheme = None if cfg.condition == "control" else cfg.condition
    eb, et = simulate_table(cfg.seed, cfg.cost, cfg.T, scheme, resolve_cartel_seats(cfg),
                            margin=cfg.scripted_margin, rule=cfg.scripted_rule, serious_add=cfg.serious_add)
    pd.testing.assert_frame_equal(bids, eb, check_dtype=False, check_exact=True)
    pd.testing.assert_frame_equal(truth, et, check_dtype=False, check_exact=True)
    assert len(messages) == (0 if scheme is None else 220)


def test_matched_seed_runs_share_environment():
    cb, ct, _ = run_in_memory(RunConfig(condition="control", seed=1), synthetic_bidder_factory)
    sb, st, _ = run_in_memory(RunConfig(condition="cover", seed=1), synthetic_bidder_factory)
    for col in ("project_id", "firm_id", "est", "tag", "dist", "firm_rate"):
        assert list(cb[col]) == list(sb[col]), col
    for col in ("eps", "nu"):
        assert list(ct[col]) == list(st[col]), col
    assert not cb["bid"].equals(sb["bid"])  # the scheme does change bids


# ------------------------------------------------------------------ contexts, roles, concurrency

class _Recorder:
    """Wraps SyntheticBidder; records (kind, ctx, result) and optionally rewrites message results."""

    def __init__(self, edit_message=None):
        self.calls = []
        self.lock = threading.Lock()
        self.edit_message = edit_message

    def factory(self, cfg, env):
        self.inner = SyntheticBidder(cfg.seed, cfg.cost, cfg.T, cfg.N)
        return self

    def bid(self, ctx):
        r = self.inner.bid(ctx)
        with self.lock:
            self.calls.append(("bid", ctx, r))
        return r

    def message(self, ctx):
        r = self.inner.message(ctx)
        if self.edit_message is not None:
            r = self.edit_message(ctx, r)
        with self.lock:
            self.calls.append(("message", ctx, r))
        return r


@pytest.mark.parametrize("scheme", SCHEMES)
def test_context_roles(scheme):
    rec = _Recorder()
    cfg = RunConfig(condition=scheme, seed=1, T=10)
    _, truth, _ = run_in_memory(cfg, rec.factory)
    a, b = SEATS_SEED1[scheme]
    first_speaker = {}
    for kind, ctx, _ in rec.calls:
        pid = ctx.t + 1
        if kind == "message":
            assert ctx.seat in (a, b) and ctx.seats == (a, b)
            if scheme == "cover":
                assert ctx.role is None
            elif scheme == "markup":
                assert ctx.role == "member"
            else:
                assert ctx.role == ("serious" if ctx.seat == rotation_roles(pid, a, b)[0] else "cover")
            if ctx.messages == []:
                first_speaker[pid] = ctx.seat
            else:
                (m1,) = ctx.messages
                assert m1["order"] == 1 and m1["recipient"] == ctx.seat
                assert type(m1["sender"]) is int and m1["sender"] != ctx.seat
    assert first_speaker == {p: (a if p % 2 == 1 else b) for p in range(1, 11)}

    for kind, ctx, _ in rec.calls:
        if kind != "bid" or ctx.seat not in (a, b):
            continue
        pid = ctx.t + 1
        assert ctx.seats == (a, b)
        assert [m["order"] for m in ctx.messages] == [1, 2]
        assert {m["sender"] for m in ctx.messages} == {a, b}
        if scheme == "markup":
            assert ctx.role == "member"
        elif scheme == "rotation":
            assert ctx.role == ("serious" if ctx.seat == rotation_roles(pid, a, b)[0] else "cover")
        else:
            cost = {m["sender"]: m["my_cost"] for m in ctx.messages}
            assert ctx.role == ("serious" if ctx.seat == cover_roles(cost[a], cost[b], a, b)[0] else "cover")
        tr = truth[(truth.project_id == pid) & (truth.seat == ctx.seat)].iloc[0]
        assert tr.cartel_role == ctx.role  # synthetic messages report true costs
    n_bids = sum(k == "bid" for k, _, _ in rec.calls)
    n_msgs = sum(k == "message" for k, _, _ in rec.calls)
    assert (n_bids, n_msgs) == (60, 20)


def test_cover_bid_roles_follow_messages_truth_follows_costs():
    a, b = 0, 3
    # seat a always claims a cost far below seat b's claim: messages say a is serious every project
    def lie(ctx, r):
        claimed = 1.0 if ctx.seat == a else 1e12
        return MessageResult(text="", my_cost=claimed, intended_serious_bid=r.intended_serious_bid,
                             reason="", raw=None, call_records=[])

    rec = _Recorder(edit_message=lie)
    _, truth, msgs = run_in_memory(RunConfig(condition="cover", seed=1, T=30), rec.factory)
    flips = 0
    for kind, ctx, _ in rec.calls:
        if kind == "bid" and ctx.seat in (a, b):
            assert ctx.role == ("serious" if ctx.seat == a else "cover")
            tp = truth[truth.project_id == ctx.t + 1].set_index("seat")
            ser, _ = cover_roles(tp.cost[a], tp.cost[b], a, b)
            assert tp.cartel_role[ser] == "serious"
            flips += ser == b
    assert flips > 0  # some projects where the true serious member differs from the claimed one
    assert {r["my_cost"] for r in msgs} == {1.0, 1e12}


def test_cover_bid_roles_fall_back_to_true_costs():
    a, b = 0, 3

    def no_cost(ctx, r):
        my_cost = None if ctx.seat == b else r.my_cost  # one message lacks a cost
        return MessageResult(text="", my_cost=my_cost, intended_serious_bid=r.intended_serious_bid,
                             reason="", raw=None, call_records=[])

    rec = _Recorder(edit_message=no_cost)
    _, truth, msgs = run_in_memory(RunConfig(condition="cover", seed=1, T=30), rec.factory)
    b_serious = 0
    for kind, ctx, _ in rec.calls:
        if kind == "bid" and ctx.seat in (a, b):
            tp = truth[truth.project_id == ctx.t + 1].set_index("seat")
            ser, _ = cover_roles(tp.cost[a], tp.cost[b], a, b)
            assert ctx.role == ("serious" if ctx.seat == ser else "cover")
            b_serious += ctx.seat == ser == b
    assert b_serious > 0  # the fallback is not "seat a serious"
    assert any(r["my_cost"] is None for r in msgs)


def test_outsiders_parallel_with_message():
    events = []
    lock = threading.Lock()

    class Stamp:
        def __init__(self, cfg, env):
            self.inner = SyntheticBidder(cfg.seed, cfg.cost, cfg.T, cfg.N)

        def bid(self, ctx):
            with lock:
                events.append(("bid", ctx.t, ctx.seat))
            return self.inner.bid(ctx)

        def message(self, ctx):
            order = 1 if ctx.messages == [] else 2
            with lock:
                events.append((f"msg{order}", ctx.t, ctx.seat))
            result = self.inner.message(ctx)
            if order == 1:
                time.sleep(0.05)  # message 1 is slow; outsiders must not wait for it
                with lock:
                    events.append(("msg1_return", ctx.t, ctx.seat))
            return result

    cfg = RunConfig(condition="cover", seed=1, T=4)
    bids, _, _ = run_in_memory(cfg, Stamp)
    seats = (0, 3)
    for t in range(4):
        ev = [e for e in events if e[1] == t]
        pos = {e: i for i, e in enumerate(ev)}
        m1 = [i for i, e in enumerate(ev) if e[0] == "msg1"]
        m1_ret = [i for i, e in enumerate(ev) if e[0] == "msg1_return"]
        m2 = [i for i, e in enumerate(ev) if e[0] == "msg2"]
        assert len(m1) == len(m1_ret) == len(m2) == 1
        outsider = [pos[("bid", t, s)] for s in range(6) if s not in seats]
        cartel = [pos[("bid", t, s)] for s in seats]
        assert len(outsider) == 4
        assert max(outsider) < m1_ret[0]  # every outsider bid STARTS before message 1 RETURNS
        assert m1[0] < m1_ret[0] < m2[0] < min(cartel)
    eb, _ = simulate_table(1, COST, 4, "cover", (0, 3))
    pd.testing.assert_frame_equal(bids, eb, check_dtype=False, check_exact=True)


# ------------------------------------------------------------------ STOP

def test_runner_honours_stop_file_scheme(tmp_path):
    class StopAfter:
        def __init__(self, cfg, env):
            self.inner = SyntheticBidder(cfg.seed, cfg.cost, cfg.T, cfg.N)

        def bid(self, ctx):
            if ctx.t + 1 == 12 and ctx.seat == 1:
                (_only_dir(tmp_path) / "STOP").touch()
            return self.inner.bid(ctx)

        def message(self, ctx):
            return self.inner.message(ctx)

    with pytest.raises(SystemExit) as exc:
        run(RunConfig(condition="cover", seed=1), StopAfter, results_root=tmp_path)
    assert exc.value.code == 3
    d = _only_dir(tmp_path)
    b = pd.read_csv(d / "bids.csv", float_precision="round_trip")
    assert len(b) == 72 and b["project_id"].max() == 12
    m = pd.read_csv(d / "messages.csv")
    assert len(m) == 24 and m["project_id"].max() == 12
    assert read_run_meta(d)["finished_utc"] is None
    hb = json.loads((d / "heartbeat.json").read_text(encoding="utf-8"))
    assert hb["last_completed_project"] == 12
    cp = read_checkpoint(d)
    assert cp["last_completed_project"] == 12 and len(cp["messages"]) == 24


# ------------------------------------------------------------------ resume

class _CrashOnce:
    """Synthetic bidder that raises once, at project ``pid``, on the first call matching ``where``."""

    def __init__(self, cfg, env, pid: int, where: str):
        self.inner = SyntheticBidder(cfg.seed, cfg.cost, cfg.T, cfg.N)
        self.seats = resolve_cartel_seats(cfg)
        self.pid, self.where = pid, where
        self.fired = False
        self.lock = threading.Lock()

    def _maybe_raise(self, hit: bool):
        with self.lock:
            if hit and not self.fired:
                self.fired = True
                raise RuntimeError(f"crash at project {self.pid} ({self.where})")

    def bid(self, ctx):
        here = ctx.t + 1 == self.pid
        if self.where == "first_bid":
            self._maybe_raise(here)
        elif self.where == "cartel_bid":
            self._maybe_raise(here and ctx.seats is not None and ctx.seat in ctx.seats)
        return self.inner.bid(ctx)

    def message(self, ctx):
        if self.where == "message2":
            self._maybe_raise(ctx.t + 1 == self.pid and ctx.messages != [])
        return self.inner.message(ctx)


def _crash_factory(pid, where):
    return lambda cfg, env: _CrashOnce(cfg, env, pid, where)


@pytest.fixture(scope="module")
def reference(tmp_path_factory):
    """Uninterrupted synthetic run per config, built once per module (under the API guard)."""
    root = tmp_path_factory.mktemp("reference")
    cache = {}

    def get(cfg):
        key = (cfg.condition, cfg.seed, cfg.T)
        if key not in cache:
            with api_guard():
                cache[key] = run(cfg, synthetic_bidder_factory, results_root=root / "_".join(map(str, key)))
        return cache[key]

    return get


class _RawBidder:
    """Synthetic bidder that returns raw text and call records (so transcripts and calls.csv fill)."""

    def __init__(self, cfg, env, crash_pid=None):
        self.inner = SyntheticBidder(cfg.seed, cfg.cost, cfg.T, cfg.N)
        self.crash_pid = crash_pid

    def bid(self, ctx):
        if ctx.t + 1 == self.crash_pid:
            raise RuntimeError("crash")
        r = self.inner.bid(ctx)
        r.raw = f"bid raw {ctx.t + 1}"
        r.call_records = [{"attempt": 1, "latency_ms": 5, "reason": ""}]
        return r

    def message(self, ctx):
        r = self.inner.message(ctx)
        r.raw = f"msg raw {ctx.t + 1}"
        r.call_records = [{"attempt": 1, "latency_ms": 7, "reason": ""}]
        return r


def _torn_at(monkeypatch, pid: int):
    """Make the checkpoint write of project ``pid`` fail: CSVs and transcripts end one project ahead."""
    real = io.write_checkpoint

    def torn(run_dir, state):
        if state["last_completed_project"] == pid:
            raise OSError("disk went away")
        real(run_dir, state)

    monkeypatch.setattr(io, "write_checkpoint", torn)
    return lambda: monkeypatch.setattr(io, "write_checkpoint", real)


def _assert_resumed(old: Path, new: Path, ref: Path, before: dict, T: int = 110):
    assert new != old and new.parent == old.parent
    assert _snapshot(old) == before  # the crashed directory is never rewritten
    for name in CSV_FILES:
        assert (new / name).read_bytes() == (ref / name).read_bytes(), name
    old_meta, meta = read_run_meta(old), read_run_meta(new)
    assert set(meta) == META_KEYS | {"resumed_from"}
    assert meta["resumed_from"] == old.name
    assert meta["started_utc"] == old_meta["started_utc"]
    assert meta["finished_utc"] is not None and old_meta["finished_utc"] is None
    assert set(read_run_meta(ref)) == META_KEYS
    cp = read_checkpoint(new)
    assert cp["last_completed_project"] == T and Path(cp["run_dir"]) == new
    hb = json.loads((new / "heartbeat.json").read_text(encoding="utf-8"))
    assert hb["last_completed_project"] == T


def test_checkpoint_resume(tmp_path, reference):
    """The one full-length (T = 110) resume: crash on the first bid call of project 38."""
    cfg = RunConfig(condition="cover", seed=2)
    root = tmp_path / "runs"
    with pytest.raises(RuntimeError, match="crash at project 38"):
        run(cfg, _crash_factory(38, "first_bid"), results_root=root)
    old = _only_dir(root)
    assert read_checkpoint(old)["last_completed_project"] == 37
    assert len(pd.read_csv(old / "bids.csv")) == 37 * 6
    assert len(pd.read_csv(old / "messages.csv")) == 37 * 2
    before = _snapshot(old)

    new = run(cfg, synthetic_bidder_factory, resume_dir=old)
    _assert_resumed(old, new, reference(cfg), before)
    cp = read_checkpoint(new)
    assert len(cp["history"]) == 110 and len(cp["messages"]) == 220


@pytest.mark.parametrize("where", ["message2", "first_bid", "cartel_bid"])
def test_checkpoint_resume_mid_project(tmp_path, reference, where):
    cfg = RunConfig(condition="cover", seed=2, T=50)  # shorter season, same crash project 40
    root = tmp_path / "runs"
    with pytest.raises(RuntimeError, match="crash at project 40"):
        run(cfg, _crash_factory(40, where), results_root=root)
    old = _only_dir(root)
    assert read_checkpoint(old)["last_completed_project"] == 39
    assert pd.read_csv(old / "messages.csv")["project_id"].max() == 39
    before = _snapshot(old)

    new = run(cfg, synthetic_bidder_factory, resume_dir=old)
    m = pd.read_csv(new / "messages.csv")
    assert len(m[m.project_id == 40]) == 2
    assert list(m[m.project_id == 40]["order"]) == [1, 2]
    assert len(m) == 100
    _assert_resumed(old, new, reference(cfg), before, T=50)


def test_resume_drops_rows_beyond_checkpoint(tmp_path, monkeypatch, reference):
    """A crash between the CSV appends and the checkpoint leaves rows one project ahead."""
    cfg = RunConfig(condition="cover", seed=1, T=20)
    restore = _torn_at(monkeypatch, 12)
    with pytest.raises(OSError):
        run(cfg, synthetic_bidder_factory, results_root=tmp_path / "runs")
    restore()
    old = _only_dir(tmp_path / "runs")
    assert read_checkpoint(old)["last_completed_project"] == 11
    assert pd.read_csv(old / "bids.csv")["project_id"].max() == 12  # one project past the checkpoint
    assert pd.read_csv(old / "messages.csv")["project_id"].max() == 12
    before = _snapshot(old)

    new = run(cfg, synthetic_bidder_factory, resume_dir=old)
    ref = reference(cfg)
    assert _snapshot(old) == before
    for name in CSV_FILES:
        assert (new / name).read_bytes() == (ref / name).read_bytes(), name
    assert '"' in (new / "messages.csv").read_text(encoding="utf-8")  # quoted text fields survived


def test_resume_copies_transcripts_and_calls(tmp_path):
    cfg = RunConfig(condition="markup", seed=1, T=6)
    root = tmp_path / "runs"
    with pytest.raises(RuntimeError):
        run(cfg, lambda c, e: _RawBidder(c, e, crash_pid=4), results_root=root)
    old = _only_dir(root)
    before = _snapshot(old)
    new = run(cfg, lambda c, e: _RawBidder(c, e), resume_dir=old)
    assert _snapshot(old) == before

    calls = pd.read_csv(new / "calls.csv", keep_default_na=False)
    assert len(calls) == 6 * (6 + 2)
    p1 = calls[calls.project_id == 1]
    assert list(p1["phase"]) == ["message", "message"] + ["bid"] * 6
    assert set(calls[calls.phase == "message"]["latency_ms"]) == {7}

    names = generate_environment(1, COST, 6, 6).names
    member = names[2]
    old_t = (old / "transcripts" / f"{member}.md").read_text(encoding="utf-8")
    new_t = (new / "transcripts" / f"{member}.md").read_text(encoding="utf-8")
    assert new_t.startswith(old_t)
    assert "Project 3 (message)" in old_t and "Project 4 (message)" not in old_t
    assert "## Response: Project 6 (message)" in new_t and "msg raw 6" in new_t
    assert "## Response: Project 6 (bid)" in new_t
    outsider_t = (new / "transcripts" / f"{names[0]}.md").read_text(encoding="utf-8")
    assert "(message)" not in outsider_t


_STAMP = re.compile(r" - \d{4}-\d\d-\d\dT[0-9:.+]+$", re.M)


def test_resume_transcripts_cut_at_checkpoint(tmp_path, monkeypatch):
    """Torn crash at project 4: the old transcripts already hold project 4; the resumed run's do not
    duplicate it, and the entries kept from the old run are byte-identical."""
    cfg = RunConfig(condition="markup", seed=1, T=6)
    restore = _torn_at(monkeypatch, 4)
    with pytest.raises(OSError):
        run(cfg, lambda c, e: _RawBidder(c, e), results_root=tmp_path / "runs")
    restore()
    old = _only_dir(tmp_path / "runs")
    assert read_checkpoint(old)["last_completed_project"] == 3
    before = _snapshot(old)
    new = run(cfg, lambda c, e: _RawBidder(c, e), resume_dir=old)
    ref = run(cfg, lambda c, e: _RawBidder(c, e), results_root=tmp_path / "ref")
    assert _snapshot(old) == before

    names = generate_environment(1, COST, 6, 6).names
    for name in names:
        old_t = (old / "transcripts" / f"{name}.md").read_text(encoding="utf-8")
        new_t = (new / "transcripts" / f"{name}.md").read_text(encoding="utf-8")
        ref_t = (ref / "transcripts" / f"{name}.md").read_text(encoding="utf-8")
        assert "## Prompt: Project 4 (bid)" in old_t  # the torn project reached the old transcript
        cut = old_t.index("\n## Prompt: Project 4 (")
        assert new_t.startswith(old_t[:cut])  # kept entries byte-identical
        for p in range(1, 7):
            assert new_t.count(f"## Prompt: Project {p} (bid)") == 1
            assert new_t.count(f"## Response: Project {p} (bid)") == 1
        assert _STAMP.sub("", new_t) == _STAMP.sub("", ref_t)


def test_transcript_filter_ignores_headers_inside_fences():
    entry = ("\n## Prompt: Project {p} (bid) - 2026-10-25T00:00:00+00:00\n``````\n{prompt}\n``````\n"
             "\n## Response: Project {p} (bid) - 2026-10-25T00:00:00+00:00\n````\n{raw}\n````\n")
    tricky = entry.format(p=2, prompt="## Prompt: Project 9 (bid)\n\nbody", raw="## Response: Project 9 (x)")
    text = entry.format(p=1, prompt="a", raw="b") + tricky + entry.format(p=3, prompt="c", raw="d")
    assert _transcript_upto(text, 2) == entry.format(p=1, prompt="a", raw="b") + tricky
    assert _transcript_upto(text, 3) == text
    assert _transcript_upto(text, 0) == ""


class _Stateful:
    def __init__(self, cfg, env, crash_pid=None, seen=None):
        self.inner = SyntheticBidder(cfg.seed, cfg.cost, cfg.T, cfg.N)
        self.n_bids = 0
        self.lock = threading.Lock()
        self.crash_pid = crash_pid
        self.seen = seen if seen is not None else {}

    def get_state(self):
        return {"n_bids": self.n_bids}

    def set_state(self, state):
        self.seen["state"] = dict(state)
        self.n_bids = state["n_bids"]

    def bid(self, ctx):
        if ctx.t + 1 == self.crash_pid:
            raise RuntimeError("crash")
        with self.lock:
            self.n_bids += 1
        return self.inner.bid(ctx)

    def message(self, ctx):
        return self.inner.message(ctx)


def test_resume_restores_bidder_state(tmp_path):
    seen = {}
    cfg = RunConfig(condition="rotation", seed=1, T=10)
    root = tmp_path / "runs"
    with pytest.raises(RuntimeError):
        run(cfg, lambda c, e: _Stateful(c, e, crash_pid=6), results_root=root)
    old = _only_dir(root)
    cp = read_checkpoint(old)
    assert cp["last_completed_project"] == 5 and cp["bidder_state"] == {"n_bids": 30}
    holder = {}

    def factory(c, e):
        holder["b"] = _Stateful(c, e, seen=seen)
        return holder["b"]

    new = run(cfg, factory, resume_dir=old)
    assert seen["state"] == {"n_bids": 30}
    assert holder["b"].n_bids == 60
    assert read_checkpoint(new)["bidder_state"] == {"n_bids": 60}


class _OnlyGet(_RawBidder):
    def get_state(self):
        return {}


class _OnlySet(_RawBidder):
    def set_state(self, state):
        pass


def test_state_hook_must_be_paired(tmp_path):
    cfg = RunConfig(condition="cover", seed=1, T=3)
    for cls in (_OnlyGet, _OnlySet):
        with pytest.raises(TypeError, match="get_state/set_state"):
            run(cfg, cls, results_root=tmp_path / "runs")
        with pytest.raises(TypeError):
            run_in_memory(cfg, cls)
    assert not (tmp_path / "runs").exists() or not any((tmp_path / "runs").iterdir())


def test_resume_stateful_bidder_needs_bidder_state(tmp_path):
    cfg = RunConfig(condition="cover", seed=1, T=10)
    root = tmp_path / "runs"
    with pytest.raises(RuntimeError):
        run(cfg, _crash_factory(5, "first_bid"), results_root=root)  # stateless: no bidder_state
    old = _only_dir(root)
    assert "bidder_state" not in read_checkpoint(old)
    before = _snapshot(old)
    with pytest.raises(ValueError, match="bidder_state"):
        run(cfg, _Stateful, resume_dir=old)
    assert [p for p in root.iterdir() if p.is_dir()] == [old]
    assert _snapshot(old) == before


@pytest.mark.parametrize("other", [
    RunConfig(condition="control", seed=1, T=10),
    RunConfig(condition="cover", seed=2, T=10),
    RunConfig(condition="cover", seed=1, T=20),
    RunConfig(condition="cover", seed=1, T=10, cartel_seats=(0, 1)),
    RunConfig(condition="cover", seed=1, T=10, N=5),
    RunConfig(condition="cover", seed=1, T=10, bidder="llm"),
    RunConfig(condition="cover", seed=1, T=10, model="claude-other"),
    RunConfig(condition="cover", seed=1, T=10, cost=CostParams(0.90, 0.08, 0.17, 0.10)),
    RunConfig(condition="cover", seed=1, T=10, cost=CostParams(0.91, 0.08, 0.17, 0.05)),
    RunConfig(condition="cover", seed=1, T=10, scripted_margin=0.15),
    RunConfig(condition="cover", seed=1, T=10, scripted_rule="shadow"),
    RunConfig(condition="cover", seed=1, T=10, serious_add=0.05),
], ids=["condition", "seed", "T", "seats", "N", "bidder", "model", "sigma_eps", "beta0",
        "margin", "rule", "serious_add"])
def test_resume_rejects_other_run(tmp_path, other):
    root = tmp_path / "runs"
    with pytest.raises(RuntimeError):
        run(RunConfig(condition="cover", seed=1, T=10), _crash_factory(5, "first_bid"), results_root=root)
    old = _only_dir(root)
    before = _snapshot(old)
    with pytest.raises(ValueError, match="cannot resume"):
        run(other, synthetic_bidder_factory, resume_dir=old)
    assert [p for p in root.iterdir() if p.is_dir()] == [old]  # nothing created
    assert _snapshot(old) == before


def test_resume_rejects_meta_without_scripted(tmp_path):
    """A run_meta written before the ``scripted`` key existed cannot be verified, so it is refused."""
    cfg = RunConfig(condition="cover", seed=1, T=10)
    root = tmp_path / "runs"
    with pytest.raises(RuntimeError):
        run(cfg, _crash_factory(5, "first_bid"), results_root=root)
    old = _only_dir(root)
    meta = read_run_meta(old)
    del meta["scripted"]
    io.write_run_meta(old, meta)
    with pytest.raises(ValueError, match="scripted"):
        run(cfg, synthetic_bidder_factory, resume_dir=old)
    assert [p for p in root.iterdir() if p.is_dir()] == [old]


def test_channel_delivery_of_synthetic_message():
    rec = _Recorder()
    _, _, msgs = run_in_memory(RunConfig(condition="cover", seed=1, T=3), rec.factory)
    ch = Channel((0, 3), "cover")
    delivered = []
    for kind, ctx, r in rec.calls:
        if kind == "message":
            assert r.text == "" and r.my_cost == ctx.cost
            delivered.append(ch.deliver(r))
            assert delivered[-1] == _delivered(ctx.cost, r.intended_serious_bid)
    assert [m["text"] for m in msgs] == delivered
