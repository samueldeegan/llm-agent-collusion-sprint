"""Launcher, spend guard, time ladder and runner CLI. No test spawns an API process or calls the API:
processes are fakes driven by a fake clock, and every file lives under ``tmp_path``."""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from src.procurement import io, launcher, runner
from src.procurement.client import FatalAPIError
from src.procurement.config import CostParams, Pricing, RunConfig, run_plan
from src.procurement.synthetic import SyntheticBidder

T0 = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
FULL_R = {"control": 12, "cover": 12, "markup": 4, "rotation": 4}
SECRET = "sk-ant-test-SECRET-do-not-print-7c1e"


class FakeClock:
    def __init__(self, start: datetime = T0):
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += timedelta(seconds=max(0.0, float(seconds)))

    def elapsed(self) -> float:
        return (self.now - T0).total_seconds()


def _arg(cmd: list[str], flag: str) -> str | None:
    return cmd[cmd.index(flag) + 1] if flag in cmd else None


class FakeProc:
    """A stand-in for a runner process. ``exit_at``: seconds after start when it exits with ``code``
    (None: never). ``stall``: never touches heartbeat activity. ``honour_stop``: exits 3 once its run
    dir holds a STOP file. A live, non-stalled fake touches activity whenever it is polled."""

    def __init__(self, clock, run_dir: Path, exit_at=None, code=0, stall=False, honour_stop=True):
        self.clock, self.run_dir = clock, run_dir
        self.start = clock()
        self.exit_at, self.code, self.stall, self.honour_stop = exit_at, code, stall, honour_stop
        self.returncode = None
        self.killed = False
        self.pid = 1000 + id(self) % 1000

    def poll(self):
        if self.returncode is not None:
            return self.returncode
        if self.honour_stop and (self.run_dir / "STOP").exists():
            self.returncode = 3
        elif self.exit_at is not None and (self.clock() - self.start).total_seconds() >= self.exit_at:
            if self.code == 0:
                meta = io.read_run_meta(self.run_dir)
                meta["finished_utc"] = self.clock().isoformat()
                io.write_run_meta(self.run_dir, meta)
            self.returncode = self.code
        elif not self.stall:
            io.touch_activity(self.run_dir, self.clock())
        return self.returncode

    def kill(self):
        self.killed = True
        if self.returncode is None:
            self.returncode = 1

    def wait(self, timeout=None):
        return self.returncode


class FakePopen:
    """``popen(cmd, **kw)``: creates the run dir the real runner would, announces it, and returns a
    FakeProc whose behaviour ``behave(name, attempt)`` decides (a dict of FakeProc kwargs plus
    ``checkpoint``: write a checkpoint)."""

    def __init__(self, clock, behave=None):
        self.clock = clock
        self.behave = behave or (lambda name, attempt: {"exit_at": 30, "code": 0})
        self.calls: list[tuple[str, int, list[str], dict]] = []
        self.procs: list[FakeProc] = []
        self.attempts: dict[str, int] = {}

    def __call__(self, cmd, **kw):
        cfg = RunConfig.from_dict({k: v for k, v in json.loads(Path(_arg(cmd, "--config")).read_text(
            encoding="utf-8")).items() if k not in ("frozen_from", "limits")})
        name = Path(_arg(cmd, "--config")).stem
        attempt = self.attempts.get(name, 0)
        self.attempts[name] = attempt + 1
        spec = dict(self.behave(name, attempt))
        checkpoint = spec.pop("checkpoint", False)
        run_dir = io.new_run_dir(_arg(cmd, "--results-root"), cfg.condition, cfg.seed, self.clock())
        meta = {"purpose": "experiment", "condition": cfg.condition, "seed": cfg.seed, "finished_utc": None}
        if _arg(cmd, "--resume-dir"):
            meta["resumed_from"] = Path(_arg(cmd, "--resume-dir")).name
        io.write_run_meta(run_dir, meta)
        io.write_heartbeat(run_dir, 0, None, self.clock())
        if checkpoint:
            io.write_checkpoint(run_dir, {"last_completed_project": 3})
        Path(_arg(cmd, "--announce")).write_text(str(run_dir.resolve()), encoding="utf-8")
        proc = FakeProc(self.clock, run_dir, **spec)
        self.calls.append((name, attempt, list(cmd), kw))
        self.procs.append(proc)
        return proc


def _plan(R=None, T=110, **kw):
    return run_plan(R or FULL_R, T=T, **kw)


def _launcher(tmp_path, plan, popen, clock, caps=None, keys=1, **kw):
    results_root = tmp_path / "results" / "runs"
    manifest = launcher.build_manifest(plan, keys)
    workspace = launcher.new_launch_dir(results_root.parent / "launches", clock())
    manifest = launcher.write_workspace(workspace, manifest)
    return launcher.Launcher(manifest, workspace, results_root, caps=caps or {1: len(plan)},
                             pricing=Pricing(), planned_calls=launcher.planned_calls(plan), clock=clock,
                             sleep=clock.sleep, popen=popen, **kw)


def _write_calls(run_dir: Path, rows: list[dict]) -> None:
    full = [{c: r.get(c) for c in io.CALLS_COLUMNS} for r in rows]
    io.write_csv(run_dir / "calls.csv", full, io.CALLS_COLUMNS)


def _call(pid=1, firm="A", attempt=1, inp=0, out=0, cc=0, cr=0):
    return {"run_id": "control_s1", "project_id": pid, "firm_id": firm, "phase": "bid", "attempt": attempt,
            "input_tokens": inp, "output_tokens": out, "cache_creation_input_tokens": cc,
            "cache_read_input_tokens": cr}


# ---------------------------------------------------------------- config and runner CLI

def test_runconfig_json_roundtrip():
    cfg = RunConfig(condition="cover", seed=3, T=80, bidder="llm", cartel_seats=(1, 4), api_key_slot=2,
                    cost=CostParams(0.88, 0.07, 0.16, 0.05, nu_levels=(-0.1, 0.0, 0.1)))
    back = RunConfig.from_json(cfg.to_json())
    assert back == cfg
    assert isinstance(back.cost.nu_levels, tuple) and isinstance(back.cartel_seats, tuple)
    d = json.loads(cfg.to_json())
    assert d["cost"]["beta0"] == 0.88 and set(d) == set(RunConfig.__dataclass_fields__)
    assert RunConfig.from_json(RunConfig(condition="control", seed=1).to_json()) == RunConfig("control", 1)
    with pytest.raises(ValueError, match="unknown RunConfig keys"):
        RunConfig.from_dict({**d, "bogus": 1})


def _cli_config(tmp_path, **kw) -> Path:
    cfg = RunConfig(condition=kw.pop("condition", "control"), seed=1, T=12,
                    results_root=str(tmp_path / "runs"), **kw)
    path = tmp_path / "cfg.json"
    data = {**cfg.to_dict(), "frozen_from": "config/frozen.json",
            "limits": {"requests": 1000, "input_tokens": 2000000, "output_tokens": 400000}}
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class _StopAt:
    def __init__(self, cfg, env, announce: Path, pid: int):
        self.inner, self.announce, self.pid = SyntheticBidder(cfg.seed, cfg.cost, cfg.T, cfg.N), announce, pid

    def bid(self, ctx):
        if ctx.t + 1 == self.pid and ctx.seat == 0:
            (Path(self.announce.read_text(encoding="utf-8")) / "STOP").touch()
        return self.inner.bid(ctx)

    def message(self, ctx):
        return self.inner.message(ctx)


def test_runner_cli_exit_codes(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    cfg_path, root = _cli_config(tmp_path), tmp_path / "runs"
    ann = tmp_path / "ann.txt"
    assert runner.main(["--config", str(cfg_path), "--announce", str(ann), "--results-root", str(root)]) == 0
    run_dir = Path(ann.read_text(encoding="utf-8"))
    assert run_dir.is_absolute() and run_dir.parent == root.resolve() and not ann.with_suffix(".txt.tmp").exists()
    meta = io.read_run_meta(run_dir)
    assert meta["finished_utc"] is not None and meta["frozen_from"] == "config/frozen.json"
    assert meta["limits"]["requests"] == 1000 and meta["purpose"] == "experiment"
    assert len(pd.read_csv(run_dir / "bids.csv")) == 72

    # STOP -> 3, then resume from the stopped dir -> 0 in a new, announced dir
    ann2 = tmp_path / "ann2.txt"
    monkeypatch.setattr(runner, "bidder_factory_for", lambda cfg: (lambda c, e: _StopAt(c, e, ann2, 5)))
    assert runner.main(["--config", str(cfg_path), "--announce", str(ann2), "--results-root", str(root)]) == 3
    stopped = Path(ann2.read_text(encoding="utf-8"))
    assert io.read_run_meta(stopped)["finished_utc"] is None
    monkeypatch.undo()
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    ann3 = tmp_path / "ann3.txt"
    assert runner.main(["--config", str(cfg_path), "--announce", str(ann3), "--results-root", str(root),
                        "--resume-dir", str(stopped)]) == 0
    resumed = Path(ann3.read_text(encoding="utf-8"))
    assert resumed != stopped and io.read_run_meta(resumed)["resumed_from"] == stopped.name
    assert len(pd.read_csv(resumed / "bids.csv")) == 72

    # FatalAPIError -> 4 with one line naming class and status
    def fatal_factory(cfg):
        def make(c, e):
            raise FatalAPIError("API authentication failed (status 401); check the API key")
        return make
    monkeypatch.setattr(runner, "bidder_factory_for", fatal_factory)
    capsys.readouterr()
    assert runner.main(["--config", str(cfg_path), "--announce", str(tmp_path / "a4.txt"),
                        "--results-root", str(root)]) == 4
    out = capsys.readouterr()
    lines = [ln for ln in out.err.splitlines() if ln.strip()]
    assert len(lines) == 1 and "FatalAPIError" in lines[0] and "401" in lines[0]
    assert SECRET not in out.out + out.err

    # any other exception -> 1 with the traceback
    def crash_factory(cfg):
        def make(c, e):
            raise RuntimeError("boom")
        return make
    monkeypatch.setattr(runner, "bidder_factory_for", crash_factory)
    assert runner.main(["--config", str(cfg_path), "--announce", str(tmp_path / "a5.txt"),
                        "--results-root", str(root)]) == 1
    err = capsys.readouterr().err
    assert "Traceback" in err and "RuntimeError: boom" in err and SECRET not in err


# ---------------------------------------------------------------- manifest, sizing, caps

def test_launcher_manifest_and_order():
    plan = _plan()
    m = launcher.build_manifest(plan, keys=1)
    assert len(m) == 32 and [e["launch_index"] for e in m] == list(range(32))
    order = [(e["condition"], e["seed"]) for e in m]
    expected = ([("cover", s) for s in range(1, 13)] + [("markup", s) for s in range(1, 5)]
                + [("rotation", s) for s in range(1, 5)] + [("control", s) for s in range(1, 13)])
    assert order == expected
    assert [e["stagger_s"] for e in m] == [5 * i for i in range(32)]
    assert {e["api_key_slot"] for e in m} == {1}
    m2 = launcher.build_manifest(plan, keys=2)
    assert [e["api_key_slot"] for e in m2] == [2 if i % 2 == 1 else 1 for i in range(32)]
    assert all(e["config"]["api_key_slot"] == e["api_key_slot"] for e in m2)
    assert m2[0]["name"] == "00_cover_s1" and m2[31]["name"] == "31_control_s12"


def test_sizing_rule_applied():
    assert launcher.apply_sizing({"output_tokens": 1_200_000}, False) == {"R": FULL_R, "keys": 1}
    assert launcher.apply_sizing({"output_tokens": 1_200_000}, True) == {"R": FULL_R, "keys": 1}
    assert launcher.apply_sizing({"output_tokens": 400_000}, True) == {"R": FULL_R, "keys": 2}
    assert launcher.apply_sizing({"output_tokens": 400_000}, False) == {
        "R": {"control": 8, "cover": 8, "markup": 4, "rotation": 4}, "keys": 1}


def _plan_file(tmp_path, deadline="2026-10-27T11:59:00Z", R=None, T=110, include_rotation=True) -> Path:
    p = tmp_path / "run_plan.json"
    p.write_text(json.dumps({"R": R or FULL_R, "include_rotation": include_rotation, "T": T,
                             "deadline_utc": deadline}), encoding="utf-8")
    return p


def _no_popen(*a, **k):
    raise AssertionError("a process was spawned")


def test_sizing_rule_from_calibration_dry_run(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY_2", raising=False)
    monkeypatch.setattr(subprocess, "Popen", _no_popen)
    results = tmp_path / "results"
    results.mkdir()
    (results / "calibration.json").write_text(json.dumps({
        "limits": {"1": {"requests": 1000, "input_tokens": 2_000_000, "output_tokens": 400_000}},
        "measured": {"mean_latency_s": 10.0}}), encoding="utf-8")
    rc = launcher.main(["--plan", str(_plan_file(tmp_path)), "--dry-run", "--allow-unfrozen",
                        "--results-root", str(results / "runs"), "--decisions", str(tmp_path / "D.md")],
                       clock=FakeClock())
    assert rc == 0
    out = capsys.readouterr().out
    (ws,) = (results / "launches").iterdir()
    status = json.loads((ws / "status.json").read_text(encoding="utf-8"))
    assert status["R"] == {"control": 8, "cover": 8, "markup": 4, "rotation": 4}
    assert len(status["runs"]) == 24 and status["cap"] == 22 and status["keys"] == 1
    assert "24 runs" in out and "cap 22" in out
    assert "R = 8/8/4/4" in (ws / "launcher.log").read_text(encoding="utf-8")


def test_concurrency_cap(tmp_path):
    assert launcher.max_procs(1000, plan_size=32) == 22
    assert launcher.max_procs(4000, plan_size=32) == 32
    assert launcher.max_procs(10, plan_size=32) == 1
    assert launcher.max_procs(1000, 20.0, 6, plan_size=100) == 44
    m2 = launcher.build_manifest(_plan(), keys=2)
    lim = {1: {"requests": 1000}, 2: {"requests": 1000}}
    assert launcher.slot_caps(lim, m2, 10.0) == {1: 16, 2: 16}
    assert launcher.slot_caps({1: {"requests": 1000}}, launcher.build_manifest(_plan(), 1), 10.0) == {1: 22}

    # a fake spawn: cap 2, five runs finishing at different times
    clock = FakeClock()
    plan = _plan({"control": 2, "cover": 3})
    durations = {"00_cover_s1": 200, "01_cover_s2": 50, "02_cover_s3": 300, "03_control_s1": 30,
                 "04_control_s2": 30}
    alive_at_start = []

    class Recording(FakePopen):
        def __call__(self, cmd, **kw):
            alive_at_start.append(sum(1 for p in self.procs if p.returncode is None))
            return super().__call__(cmd, **kw)

    popen = Recording(clock, lambda name, a: {"exit_at": durations[name], "code": 0})
    lau = _launcher(tmp_path, plan, popen, clock, caps={1: 2}, poll_s=10)
    assert lau.monitor() == 0
    assert [c[0] for c in popen.calls] == list(durations)  # manifest order
    assert max(alive_at_start) <= 1  # at most one other alive when a start makes it two
    assert all(r.state == "done" for r in lau.runs)


# ---------------------------------------------------------------- spend guard and stop

def test_spend_guard(tmp_path):
    p = Pricing()
    d1, d2 = tmp_path / "r1", tmp_path / "r2"
    d1.mkdir(), d2.mkdir()
    # $80 from all four token columns: 100M in ($10) + 100M out ($50) + 80M cache write ($10) + 1000M read ($10)
    _write_calls(d1, [_call(1, "A", inp=100_000_000, out=100_000_000),
                      _call(1, "B", cc=80_000_000, cr=1_000_000_000)])
    actual, projected, stop = launcher.spend_guard([d1], p, planned_calls=1000)
    assert actual == pytest.approx(80.0) and stop  # actual > 75 stops even below the 200-call floor
    assert projected == pytest.approx(80.0 * 1000 / 2)

    # $30 over 300 calls (retry rows are not extra calls); planned 600 -> projected 60: continue
    rows = [_call(pid, f"F{k}", out=200_000) for pid in range(1, 51) for k in range(6)]  # 300 calls, $30
    rows.append(_call(1, "F0", attempt=2))
    _write_calls(d2, rows)
    actual, projected, stop = launcher.spend_guard([d2], p, planned_calls=600)
    assert actual == pytest.approx(30.0) and projected == pytest.approx(60.0) and not stop
    # same spend, planned 1000 -> projected 100 > 75 with >= 200 calls: stop
    actual, projected, stop = launcher.spend_guard([d2], p, planned_calls=1000)
    assert projected == pytest.approx(100.0) and stop
    # below the 200-call floor a projection alone does not stop
    d3 = tmp_path / "r3"
    d3.mkdir()
    _write_calls(d3, [_call(1, "A", out=20_000_000)])  # $10 on one call, projected $10,000
    assert launcher.spend_guard([d3], p, planned_calls=1000)[2] is False
    # completed runs count, missing calls.csv is zero, empty token fields are zero
    d4 = tmp_path / "r4"
    d4.mkdir()
    _write_calls(d4, [{**_call(2, "A"), "input_tokens": None, "output_tokens": None}])
    assert launcher.spend_guard([d2, d4, tmp_path / "nope"], p, 600)[0] == pytest.approx(30.0)


def test_spend_guard_resumed_dir_not_double_counted(tmp_path):
    p = Pricing()
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir(), new.mkdir()
    io.write_checkpoint(old, {"last_completed_project": 2})
    io.write_run_meta(old, {"purpose": "experiment"})
    io.write_run_meta(new, {"purpose": "experiment", "resumed_from": "old"})
    first = [_call(1, "A", out=2_000_000), _call(2, "A", out=2_000_000)]  # $1 each
    _write_calls(old, first + [_call(3, "A", out=2_000_000)])  # aborted project 3: real spend
    _write_calls(new, first + [_call(3, "A", out=2_000_000), _call(4, "A", out=2_000_000)])
    actual, projected, _ = launcher.spend_guard([old, new], p, planned_calls=5)
    assert actual == pytest.approx(5.0)  # 1, 2, 3 (old) + 3, 4 (new); copied 1, 2 not twice
    assert projected == pytest.approx(5.0)


def test_stop_file_on_condition_2(tmp_path):
    clock = FakeClock()
    root = tmp_path / "results" / "runs"
    dirs = [io.new_run_dir(root, "cover", s, T0) for s in (1, 2, 3)]
    for d in dirs:
        io.write_run_meta(d, {"purpose": "experiment", "finished_utc": None})
    meta = io.read_run_meta(dirs[2])
    meta["finished_utc"] = T0.isoformat()
    io.write_run_meta(dirs[2], meta)
    honours = FakeProc(clock, dirs[0], exit_at=None)
    straggler = FakeProc(clock, dirs[1], exit_at=None, honour_stop=False)
    finished = FakeProc(clock, dirs[2], exit_at=0, code=0)
    finished.poll()
    old_stop = {"reason": "spend", "utc": "2026-10-25T10:00:00+00:00", "runs_incomplete": ["x"]}
    (tmp_path / "results" / "STOP.json").write_text(json.dumps(old_stop), encoding="utf-8")
    copy_dir = tmp_path / "launch"
    copy_dir.mkdir()
    rc = launcher.stop_all([honours, straggler, finished], dirs, root, reason="spend", actual=80.0,
                           projected=90.0, clock=clock, sleep=clock.sleep, not_started=["control_s9"],
                           copy_dir=copy_dir)
    assert rc == 2
    rotated = tmp_path / "results" / "STOP_20261025T100000Z.json"
    assert json.loads(rotated.read_text(encoding="utf-8")) == old_stop  # earlier stop kept, never deleted
    assert (dirs[0] / "STOP").exists() and (dirs[1] / "STOP").exists() and not (dirs[2] / "STOP").exists()
    assert (dirs[0] / "STOP").read_bytes() == b""
    assert honours.returncode == 3 and not honours.killed
    assert straggler.killed and clock.elapsed() >= 120
    stop = json.loads((tmp_path / "results" / "STOP.json").read_text(encoding="utf-8"))
    assert set(stop) == {"reason", "actual_usd", "projected_usd", "utc", "runs_complete", "runs_incomplete",
                         "runs_not_started"}
    assert stop["runs_not_started"] == ["control_s9"]
    assert json.loads((copy_dir / "STOP.json").read_text(encoding="utf-8")) == stop
    assert stop["reason"] == "spend" and stop["actual_usd"] == 80.0 and stop["projected_usd"] == 90.0
    assert stop["runs_complete"] == [dirs[2].name]
    assert sorted(stop["runs_incomplete"]) == sorted([dirs[0].name, dirs[1].name])
    assert not (tmp_path / "results" / "STOP.json.tmp").exists()


def test_monitor_spend_stop_exit_2(tmp_path):
    clock = FakeClock()
    popen = FakePopen(clock, lambda name, a: {"exit_at": None})
    lau = _launcher(tmp_path, _plan({"control": 2}, T=10), popen, clock, poll_s=60, stagger_s=0)
    real_poll = FakeProc.poll

    def spending_poll(self):
        rc = real_poll(self)
        if rc is None and (self.clock() - T0).total_seconds() >= 120:
            _write_calls(self.run_dir, [_call(1, "A", out=200_000_000)])  # $100
        return rc

    FakeProc.poll = spending_poll
    try:
        assert lau.monitor() == 2
    finally:
        FakeProc.poll = real_poll
    stop = json.loads((tmp_path / "results" / "STOP.json").read_text(encoding="utf-8"))
    assert stop["reason"] == "spend" and stop["actual_usd"] >= 100
    assert all((p.run_dir / "STOP").exists() for p in popen.procs)
    assert {r.state for r in lau.runs} == {"stopped"}


# ---------------------------------------------------------------- restarts

def test_stall_restart_once(tmp_path):
    clock = FakeClock()
    behave = lambda name, a: ({"stall": True, "checkpoint": True} if name.endswith("cover_s1")  # noqa: E731
                              else {"exit_at": 1500, "code": 0})
    popen = FakePopen(clock, behave)
    lau = _launcher(tmp_path, _plan({"control": 1, "cover": 1}), popen, clock, poll_s=60, stall_s=600)
    assert lau.monitor() == 6
    stalled, healthy = lau.runs
    assert [c[0] for c in popen.calls] == ["00_cover_s1", "01_control_s1", "00_cover_s1"]
    first, second = popen.procs[0], popen.procs[2]
    assert first.killed and second.killed
    restart_cmd = popen.calls[2][2]
    assert _arg(restart_cmd, "--resume-dir") == str(first.run_dir.resolve())
    assert stalled.state == "stopped" and stalled.restarts == 1
    assert (second.run_dir / "STOP").exists()  # analysis reports it as stopped
    assert len(stalled.run_dirs) == 2 and stalled.run_dir == second.run_dir.resolve()
    assert healthy.state == "done" and healthy.restarts == 0 and not popen.procs[1].killed
    # first kill only after more than 600 s without activity
    log = (lau.workspace / "launcher.log").read_text(encoding="utf-8")
    assert "stall" in log and "restart" in log
    status = json.loads((lau.workspace / "status.json").read_text(encoding="utf-8"))
    assert status["runs"][0]["state"] == "stopped" and status["runs"][0]["restarts"] == 1


def test_stall_measured_from_activity(tmp_path):
    clock = FakeClock()
    popen = FakePopen(clock, lambda name, a: {"stall": True})
    lau = _launcher(tmp_path, _plan({"control": 1}), popen, clock, poll_s=60, stall_s=600)
    lau.start_due()
    for _ in range(10):  # 600 s: not older than stall_s yet
        clock.sleep(60)
        lau.poll_once()
    assert not popen.procs[0].killed
    clock.sleep(60)
    lau.poll_once()
    assert popen.procs[0].killed and lau.runs[0].restarts == 1


def test_crash_restart_once_then_failed(tmp_path):
    clock = FakeClock()

    def behave(name, attempt):
        if name == "00_cover_s1":  # crashes twice, with a checkpoint
            return {"exit_at": 30, "code": 1, "checkpoint": True}
        return {"exit_at": 30, "code": 1 if attempt == 0 else 0}  # crash without checkpoint, then done

    popen = FakePopen(clock, behave)
    lau = _launcher(tmp_path, _plan({"control": 1, "cover": 1}), popen, clock, poll_s=60)
    assert lau.monitor() == 6
    a, b = lau.runs
    assert a.state == "failed" and a.restarts == 1 and a.exit_code == 1
    assert b.state == "done" and b.restarts == 1 and b.exit_code == 0
    cmds = {(n, att): cmd for n, att, cmd, _ in popen.calls}
    assert _arg(cmds[("00_cover_s1", 1)], "--resume-dir") == str(popen.procs[0].run_dir.resolve())
    assert "--resume-dir" not in cmds[("01_control_s1", 1)]  # no checkpoint: fresh
    assert len(popen.calls) == 4
    # every attempt has its own announce and log files
    assert len({_arg(c[2], "--announce") for c in popen.calls}) == 4
    assert len({c[3]["stdout"].name for c in popen.calls}) == 4


def test_fatal_api_exit_stops_all(tmp_path):
    clock = FakeClock()
    behave = lambda name, a: ({"exit_at": 30, "code": 4} if name == "00_cover_s1"  # noqa: E731
                              else {"exit_at": None})
    popen = FakePopen(clock, behave)
    lau = _launcher(tmp_path, _plan({"control": 2, "cover": 1}), popen, clock, poll_s=60, stagger_s=5)
    assert lau.monitor() == 5
    assert len(popen.calls) == 3
    assert all((p.run_dir / "STOP").exists() for p in popen.procs[1:])
    stop = json.loads((tmp_path / "results" / "STOP.json").read_text(encoding="utf-8"))
    assert stop["reason"] == "fatal_api_error"
    assert sorted(stop["runs_incomplete"]) == sorted(p.run_dir.name for p in popen.procs)
    assert lau.runs[0].state == "failed" and lau.runs[0].exit_code == 4 and lau.runs[0].restarts == 0
    assert {r.state for r in lau.runs[1:]} == {"stopped"}


def test_spawn_command_and_kwargs(tmp_path):
    clock = FakeClock()
    popen = FakePopen(clock)
    lau = _launcher(tmp_path, _plan({"control": 1}), popen, clock, python_exe="PY")
    lau.start_due()
    name, attempt, cmd, kw = popen.calls[0]
    root = str((tmp_path / "results" / "runs").resolve())
    assert cmd[:3] == ["PY", "-m", "src.procurement.runner"]
    assert _arg(cmd, "--results-root") == root and Path(_arg(cmd, "--config")).is_file()
    assert Path(kw["cwd"]) == Path(launcher.REPO_ROOT)
    if os.name == "nt":
        assert kw["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP
    assert kw["stdout"].name.endswith(".out") and kw["stderr"].name.endswith(".err")
    assert not any("sk-" in part for part in cmd) and "env" not in kw


# ---------------------------------------------------------------- status, ladder, planned calls

def test_status_json_written(tmp_path):
    clock = FakeClock()
    popen = FakePopen(clock, lambda name, a: {"exit_at": 150, "code": 0})
    lau = _launcher(tmp_path, _plan({"control": 1, "cover": 1}), popen, clock, poll_s=60,
                    info={"R": {"control": 1, "cover": 1}, "cap": 2, "keys": 1})
    snaps = []
    real_sleep = clock.sleep

    def sleep(s):
        path = lau.workspace / "status.json"
        if path.exists():
            snaps.append(json.loads(path.read_text(encoding="utf-8")))
        real_sleep(s)

    lau.sleep = sleep
    assert lau.monitor() == 0
    final = json.loads((lau.workspace / "status.json").read_text(encoding="utf-8"))
    assert final["R"] == {"control": 1, "cover": 1} and final["cap"] == 2 and final["keys"] == 1
    assert {"actual_usd", "projected_usd", "planned_calls", "last_poll_utc", "exit_code"} <= set(final)
    for r in final["runs"]:
        assert r["state"] == "done" and r["restarts"] == 0 and r["exit_code"] == 0
        assert Path(r["run_dir"]).is_dir() and r["last_poll_utc"] == final["last_poll_utc"]
    assert any(any(r["state"] == "running" for r in s["runs"]) for s in snaps)
    polls = {s["last_poll_utc"] for s in snaps}
    assert len(polls) >= 2  # rewritten every poll
    assert not (lau.workspace / "status.json.tmp").exists()


def test_time_ladder():
    D = datetime(2026, 10, 26, 11, 59, tzinfo=timezone.utc)
    plan = {"R": dict(FULL_R), "include_rotation": True, "T": 110, "deadline_utc": "2026-10-26T11:59:00Z"}
    snapshot = json.dumps(plan, sort_keys=True)
    p, rung, stop = launcher.time_ladder(D - timedelta(hours=6), D, False, plan)
    assert p == plan and rung == 0 and not stop
    p, rung, stop = launcher.time_ladder(D - timedelta(hours=5), D, False, plan)
    assert not p["include_rotation"] and p["T"] == 110 and p["R"]["markup"] == 4 and rung == 1 and not stop
    p, rung, stop = launcher.time_ladder(D - timedelta(hours=4), D, False, plan)
    assert not p["include_rotation"] and p["T"] == 80 and p["R"]["markup"] == 2 and p["R"]["control"] == 12
    assert rung == 2 and not stop
    p, rung, stop = launcher.time_ladder(D - timedelta(hours=3), D, False, plan)
    assert p["R"] == {"control": 6, "cover": 6, "markup": 2, "rotation": 4} and p["T"] == 80
    assert not p["include_rotation"] and rung == 3 and not stop
    p, rung, stop = launcher.time_ladder(D - timedelta(hours=2.5), D, False, plan)
    assert stop and rung == 4
    p, rung, stop = launcher.time_ladder(D - timedelta(hours=1), D, True, plan)
    assert p == plan and rung == 0 and not stop  # already launched: unchanged
    assert json.dumps(plan, sort_keys=True) == snapshot  # pure
    # a sized R below a rung value is never raised by the rung
    small = {**plan, "R": {"control": 4, "cover": 4, "markup": 1, "rotation": 4}}
    p, _, _ = launcher.time_ladder(D - timedelta(hours=3), D, False, small)
    assert p["R"]["control"] == 4 and p["R"]["markup"] == 1
    # planned calls follow the resulting plan
    p, _, _ = launcher.time_ladder(D - timedelta(hours=3), D, False, plan)
    runs = run_plan(p["R"], include_rotation=p["include_rotation"], T=p["T"])
    assert launcher.planned_calls(runs) == 6 * 80 * 6 + 6 * 80 * 8 + 2 * 80 * 8


def test_time_ladder_cli_logs_rungs(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "Popen", _no_popen)
    decisions = tmp_path / "D.md"
    decisions.write_text("# Decisions\n", encoding="utf-8")
    clock = FakeClock(datetime(2026, 10, 26, 7, 59, tzinfo=timezone.utc))  # deadline - 4 h
    args = ["--plan", str(_plan_file(tmp_path, "2026-10-26T11:59:00Z")), "--allow-unfrozen",
            "--bidder", "synthetic", "--results-root", str(tmp_path / "results" / "runs"),
            "--decisions", str(decisions)]
    # dry run: the plan shows the rungs but DECISIONS.md is untouched
    assert launcher.main(args + ["--dry-run"], clock=clock) == 0
    assert decisions.read_text(encoding="utf-8") == "# Decisions\n"
    assert "T 80" in capsys.readouterr().out
    popen = FakePopen(clock, lambda name, a: {"exit_at": 10, "code": 0})
    assert launcher.main(args + ["--poll-s", "5"], clock=clock, sleep=clock.sleep, popen=popen) == 0
    lines = [ln for ln in decisions.read_text(encoding="utf-8").splitlines() if "time ladder" in ln]
    assert len(lines) == 2 and "rotation" in lines[0] and "T = 80" in lines[1]
    assert {c[0].split("_", 1)[1] for c in popen.calls} == (
        {f"cover_s{s}" for s in range(1, 13)} | {"markup_s1", "markup_s2"} | {f"control_s{s}" for s in range(1, 13)})
    # deadline - 2.5 h with nothing launched: stop, no launch
    clock2 = FakeClock(datetime(2026, 10, 26, 9, 29, tzinfo=timezone.utc))
    popen2 = FakePopen(clock2)
    assert launcher.main(args, clock=clock2, sleep=clock2.sleep, popen=popen2) == 7
    assert popen2.calls == [] and "stop" in decisions.read_text(encoding="utf-8").splitlines()[-1]


def test_planned_calls():
    assert launcher.planned_calls(_plan()) == 7920 + 10560 + 3520 + 3520
    assert launcher.planned_calls(_plan({"control": 8, "cover": 8, "markup": 4, "rotation": 4})) == (
        8 * 110 * 6 + 8 * 110 * 8 + 4 * 110 * 8 * 2)
    assert launcher.planned_calls(_plan({"control": 6, "cover": 6, "markup": 2}, T=80)) == 8000
    assert launcher.planned_calls([]) == 0


# ---------------------------------------------------------------- CLI: frozen, preflight, dry run

def _frozen(tmp_path) -> Path:
    p = tmp_path / "frozen.json"
    p.write_text(json.dumps({"cost": {"beta0": 0.93, "beta1": 0.08, "beta2": 0.17, "sigma_eps": 0.05}}),
                 encoding="utf-8")
    return p


def test_refuses_without_frozen(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "Popen", _no_popen)
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    results = tmp_path / "results"
    rc = launcher.main(["--plan", str(_plan_file(tmp_path)), "--results-root", str(results / "runs"),
                        "--frozen", str(tmp_path / "missing_frozen.json"), "--decisions", str(tmp_path / "D.md")],
                       clock=FakeClock(), popen=_no_popen)
    assert rc == 5
    err = capsys.readouterr().err
    assert "frozen" in err and SECRET not in err
    assert not (results / "launches").exists()


def test_frozen_params_in_every_config(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "Popen", _no_popen)
    frozen = _frozen(tmp_path)
    results = tmp_path / "results"
    assert launcher.main(["--plan", str(_plan_file(tmp_path)), "--dry-run", "--frozen", str(frozen),
                          "--results-root", str(results / "runs"), "--decisions", str(tmp_path / "D.md")],
                         clock=FakeClock()) == 0
    (ws,) = (results / "launches").iterdir()
    configs = sorted((ws / "configs").glob("*.json"))
    assert len(configs) == 32
    for path in configs:
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["cost"]["beta0"] == 0.93 and data["frozen_from"] == str(frozen)
        assert data["bidder"] == "llm" and data["results_root"] == str((results / "runs").resolve())


def test_preflight_missing_key_exit_5(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "Popen", _no_popen)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY_2", SECRET)
    results = tmp_path / "results"
    ladder_plan = _plan_file(tmp_path, "2026-10-25T16:00:00Z")  # 4 h left at T0: rungs 1-2 would apply
    rc = launcher.main(["--plan", str(ladder_plan), "--frozen", str(_frozen(tmp_path)),
                        "--results-root", str(results / "runs"), "--decisions", str(tmp_path / "D.md")],
                       clock=FakeClock(), popen=_no_popen)
    assert rc == 5
    out = capsys.readouterr()
    assert "ANTHROPIC_API_KEY" in out.err and SECRET not in out.out + out.err
    assert not (results / "launches").exists() and not (tmp_path / "D.md").exists()  # nothing written
    # dry runs skip the check
    assert launcher.main(["--plan", str(_plan_file(tmp_path)), "--frozen", str(_frozen(tmp_path)), "--dry-run",
                          "--results-root", str(results / "runs"), "--decisions", str(tmp_path / "D.md")],
                         clock=FakeClock()) == 0
    out = capsys.readouterr()
    assert SECRET not in out.out + out.err
    for f in (results / "launches").rglob("*"):
        if f.is_file():
            assert SECRET not in f.read_text(encoding="utf-8")


def test_dry_run_writes_manifest_and_summary(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "Popen", _no_popen)
    monkeypatch.delenv("ANTHROPIC_API_KEY_2", raising=False)
    results = tmp_path / "results"
    rc = launcher.main(["--plan", str(_plan_file(tmp_path)), "--dry-run", "--allow-unfrozen",
                        "--results-root", str(results / "runs"), "--decisions", str(tmp_path / "D.md")],
                       clock=FakeClock())
    assert rc == 0
    out = capsys.readouterr()
    (ws,) = (results / "launches").iterdir()
    assert ws.name == "20261025_120000"
    manifest = json.loads((ws / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["runs"]) == 32 and manifest["runs"][0]["name"] == "00_cover_s1"
    assert all(Path(e["config"]).is_file() for e in manifest["runs"])
    status = json.loads((ws / "status.json").read_text(encoding="utf-8"))
    assert {r["state"] for r in status["runs"]} == {"queued"} and status["cap"] == 32
    assert "calibration" in out.err.lower()  # missing calibration warns
    s = out.out
    for needle in ("32 runs", "cover 12", "markup 4", "rotation 4", "control 12", "T 110", "cap 32",
                   "keys 1", "planned calls 25520", "$28.07"):
        assert needle in s, needle
    assert not (results / "runs").exists()
    # a second launch never overwrites the first workspace
    assert launcher.main(["--plan", str(_plan_file(tmp_path)), "--dry-run", "--allow-unfrozen",
                          "--results-root", str(results / "runs"), "--decisions", str(tmp_path / "D.md")],
                         clock=FakeClock()) == 0
    assert len(list((results / "launches").iterdir())) == 2


def test_run_plan_config_file(repo_root):
    data = json.loads((repo_root / "config" / "run_plan.json").read_text(encoding="utf-8"))
    arms = data.pop("arms")
    assert data == {"R": FULL_R, "include_rotation": True, "T": 110, "deadline_utc": "2026-10-26T11:59:00Z"}
    assert [e["arm"] for e in arms] == [e["arm"] for e in ARM_ENTRIES]
    assert all(e["R"] == {"control": 12} for e in arms)
    assert [{k: v for k, v in e.items() if k != "R"} for e in arms] == ARM_ENTRIES


@pytest.mark.slow
def test_launcher_cli_subprocess_smoke(tmp_path, repo_root):
    """Real processes, synthetic bidder, T = 12: the launcher spawns two runner processes and both finish."""
    plan = _plan_file(tmp_path, R={"control": 1, "cover": 1}, T=12)
    results = tmp_path / "results"
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC_API_KEY")}
    proc = subprocess.run([sys.executable, "-m", "src.procurement.launcher", "--plan", str(plan),
                           "--allow-unfrozen", "--bidder", "synthetic", "--poll-s", "1", "--stagger-s", "0",
                           "--results-root", str(results / "runs"), "--decisions", str(tmp_path / "D.md")],
                          cwd=repo_root, env=env, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    (ws,) = (results / "launches").iterdir()
    status = json.loads((ws / "status.json").read_text(encoding="utf-8"))
    assert [r["state"] for r in status["runs"]] == ["done", "done"]
    for r in status["runs"]:
        assert len(pd.read_csv(Path(r["run_dir"]) / "bids.csv")) == 72
        assert io.read_run_meta(r["run_dir"])["finished_utc"] is not None


# ---------------------------------------------------------------- review round 1: guard holes

def _never_exit(name, attempt):
    return {"exit_at": None}


def test_launcher_error_stops_children(tmp_path, monkeypatch):
    clock = FakeClock()
    popen = FakePopen(clock, _never_exit)
    lau = _launcher(tmp_path, _plan({"control": 2, "cover": 1}), popen, clock, poll_s=60, caps={1: 2})
    real = launcher.spend_guard

    def failing_guard(*a, **k):
        if clock.elapsed() >= 120:
            raise RuntimeError("boom in poll")
        return real(*a, **k)

    monkeypatch.setattr(launcher, "spend_guard", failing_guard)
    with pytest.raises(RuntimeError, match="boom in poll"):
        lau.monitor()
    assert len(popen.procs) == 2 and all((p.run_dir / "STOP").exists() for p in popen.procs)
    assert all(p.returncode == 3 for p in popen.procs)
    stop = json.loads((tmp_path / "results" / "STOP.json").read_text(encoding="utf-8"))
    assert stop["reason"] == "launcher_error" and stop["runs_not_started"] == ["control_s2"]
    assert (lau.workspace / "STOP.json").is_file()
    assert [r.state for r in lau.runs] == ["stopped", "stopped", "not_started"]
    status = json.loads((lau.workspace / "status.json").read_text(encoding="utf-8"))
    assert status["stop_reason"] == "launcher_error" and status["runs"][2]["state"] == "not_started"


def test_keyboard_interrupt_stops_children(tmp_path):
    clock = FakeClock()
    popen = FakePopen(clock, _never_exit)
    lau = _launcher(tmp_path, _plan({"control": 1}), popen, clock, poll_s=60)
    fired = []

    def sleep(s):
        if clock.elapsed() >= 100 and not fired:
            fired.append(1)
            raise KeyboardInterrupt
        clock.sleep(s)

    lau.sleep = sleep
    with pytest.raises(KeyboardInterrupt):
        lau.monitor()
    assert (popen.procs[0].run_dir / "STOP").exists()
    stop = json.loads((tmp_path / "results" / "STOP.json").read_text(encoding="utf-8"))
    assert stop["reason"] == "interrupted"


def test_status_and_log_errors_do_not_stop_monitor(tmp_path, capsys):
    clock = FakeClock()
    popen = FakePopen(clock, lambda name, a: {"exit_at": 30, "code": 0})
    lau = _launcher(tmp_path, _plan({"control": 1}), popen, clock, poll_s=60)
    (lau.workspace / "status.json.tmp").mkdir()  # every status write fails
    (lau.workspace / "launcher.log").mkdir()  # every log write fails
    assert lau.monitor() == 0
    err = capsys.readouterr().err
    assert err.count("cannot write status.json") == 1 and err.count("cannot write launcher.log") == 1


def test_spend_guard_spans_launches(tmp_path):
    p = Pricing()
    results = tmp_path / "results"
    old = io.new_run_dir(results / "runs", "control", 1, T0)
    io.write_run_meta(old, {"purpose": "experiment", "finished_utc": T0.isoformat()})
    _write_calls(old, [_call(1, "A", out=140_000_000)])  # $70
    cal = io.new_run_dir(results / "calibration_runs", "control", 1, T0)
    io.write_run_meta(cal, {"purpose": "pilot", "finished_utc": T0.isoformat()})
    _write_calls(cal, [_call(1, "A", out=8_000_000)])  # $4
    actual, projected, _ = launcher.spend_guard([], p, 100, baseline=launcher.spend_totals([old, cal], p))
    assert actual == pytest.approx(74.0) and projected == pytest.approx(74.0 * 102 / 2)

    clock = FakeClock()
    popen = FakePopen(clock, _never_exit)
    lau = _launcher(tmp_path, _plan({"cover": 1}, T=10), popen, clock, poll_s=60)
    assert lau.baseline == (pytest.approx(74.0), 2) and set(lau.baseline_dirs) == {old, cal}
    real_poll = FakeProc.poll

    def spending_poll(self):
        rc = real_poll(self)
        if rc is None and clock.elapsed() >= 120:
            _write_calls(self.run_dir, [_call(1, "A", out=4_000_000)])  # $2 in the new launch
        return rc

    FakeProc.poll = spending_poll
    try:
        assert lau.monitor() == 2
    finally:
        FakeProc.poll = real_poll
    stop = json.loads((results / "STOP.json").read_text(encoding="utf-8"))
    assert stop["reason"] == "spend" and stop["actual_usd"] == pytest.approx(76.0)
    log = (lau.workspace / "launcher.log").read_text(encoding="utf-8")
    assert "spend baseline $74.00 over 2 calls" in log
    assert lau._all_dirs() == [popen.procs[0].run_dir.resolve()]  # pre-existing dirs only in the baseline


def test_spend_counts_files_missing_a_token_column(tmp_path, capsys):
    d = tmp_path / "r"
    d.mkdir()
    cols = [c for c in io.CALLS_COLUMNS if c != "cache_creation_input_tokens"]
    row = {c: "" for c in cols}
    row.update(run_id="control_s1", project_id=1, firm_id="A", phase="bid", attempt=1, output_tokens=2_000_000)
    io.write_csv(d / "calls.csv", [row], cols)
    usd, calls = launcher.spend_totals([d], Pricing())
    assert usd == pytest.approx(1.0) and calls == 1
    assert "cache_creation_input_tokens" in capsys.readouterr().err


def test_unkillable_stalled_run_marked_failed(tmp_path):
    class Unkillable(FakeProc):
        def kill(self):
            self.killed = True  # returncode stays None

    class P(FakePopen):
        def __call__(self, cmd, **kw):
            proc = super().__call__(cmd, **kw)
            proc.__class__ = Unkillable
            return proc

    clock = FakeClock()
    popen = P(clock, lambda name, a: {"stall": True, "checkpoint": True})
    lau = _launcher(tmp_path, _plan({"control": 1}), popen, clock, poll_s=60, stall_s=600)
    assert lau.monitor() == 6
    assert len(popen.calls) == 1 and lau.runs[0].state == "failed" and lau.runs[0].restarts == 0
    assert "did not end the process" in (lau.workspace / "launcher.log").read_text(encoding="utf-8")


def test_fatal_and_crash_in_one_pass_no_restart(tmp_path):
    clock = FakeClock()
    behave = lambda name, a: ({"exit_at": 30, "code": 4} if name == "01_control_s1"  # noqa: E731
                              else {"exit_at": 30, "code": 1, "checkpoint": True})
    popen = FakePopen(clock, behave)
    lau = _launcher(tmp_path, _plan({"control": 1, "cover": 1}), popen, clock, poll_s=60)
    assert lau.monitor() == 5
    assert len(popen.calls) == 2  # the crashed cover run was not restarted
    assert [(r.state, r.restarts) for r in lau.runs] == [("failed", 0), ("failed", 0)]


def test_skip_finished_runs(tmp_path, monkeypatch, capsys):
    from src.procurement.synthetic import synthetic_bidder_factory
    root = tmp_path / "results" / "runs"
    done = runner.run(RunConfig("control", 1, T=12, results_root=str(root)), synthetic_bidder_factory, root)
    runner.run(RunConfig("control", 2, T=10, results_root=str(root)), synthetic_bidder_factory, root)  # other T
    monkeypatch.setattr(subprocess, "Popen", _no_popen)  # after the runs (they read the git commit)
    plan = _plan_file(tmp_path, R={"control": 2}, T=12)
    base = ["--plan", str(plan), "--dry-run", "--allow-unfrozen", "--frozen", str(tmp_path / "none.json"),
            "--bidder", "synthetic", "--results-root", str(root), "--decisions", str(tmp_path / "D.md")]
    assert launcher.main(base, clock=FakeClock()) == 0
    assert "skip control_s1" in capsys.readouterr().out
    (ws,) = (tmp_path / "results" / "launches").iterdir()
    status = json.loads((ws / "status.json").read_text(encoding="utf-8"))
    states = {(r["condition"], r["seed"]): r["state"] for r in status["runs"]}
    assert states == {("control", 1): "skipped_done", ("control", 2): "queued"}
    skipped = [r for r in status["runs"] if r["state"] == "skipped_done"]
    assert status["skipped_done"] == ["control_s1"] and Path(skipped[0]["run_dir"]) == done
    assert status["planned_calls"] == 12 * 6
    # --rerun-finished overrides; a different bidder is not the same run either
    assert launcher.main(base + ["--rerun-finished"], clock=FakeClock(T0 + timedelta(seconds=1))) == 0
    ws2 = sorted((tmp_path / "results" / "launches").iterdir())[-1]
    status = json.loads((ws2 / "status.json").read_text(encoding="utf-8"))
    assert [r["state"] for r in status["runs"]] == ["queued", "queued"] and status["skipped_done"] == []
    assert launcher.finished_runs(root, [RunConfig("control", 1, T=12, bidder="llm")]) == {}


def test_calibration_without_limits_refused_and_key2_ignored(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "Popen", _no_popen)
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    monkeypatch.setenv("ANTHROPIC_API_KEY_2", SECRET + "2")
    results = tmp_path / "results"
    args = ["--plan", str(_plan_file(tmp_path)), "--allow-unfrozen", "--frozen", str(tmp_path / "none.json"),
            "--results-root", str(results / "runs"), "--decisions", str(tmp_path / "D.md")]
    # no calibration: one key only, key 2 ignored with a warning
    assert launcher.main(args + ["--dry-run"], clock=FakeClock()) == 0
    err = capsys.readouterr().err
    assert "ANTHROPIC_API_KEY_2 is set but ignored" in err and SECRET not in err
    (ws,) = (results / "launches").iterdir()
    status = json.loads((ws / "status.json").read_text(encoding="utf-8"))
    assert status["keys"] == 1 and {r["api_key_slot"] for r in status["runs"]} == {1}
    # a calibration file without slot-1 limits: refused in real mode, warned in a dry run
    (results / "calibration.json").write_text(json.dumps({"measured": {"mean_latency_s": 9}}), encoding="utf-8")
    assert launcher.main(args, clock=FakeClock(), popen=_no_popen) == 5
    assert "no slot-1 limits" in capsys.readouterr().err
    assert len(list((results / "launches").iterdir())) == 1
    assert launcher.main(args + ["--dry-run"], clock=FakeClock()) == 0


def test_relative_paths_resolve_against_repo_root(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "Popen", _no_popen)
    monkeypatch.chdir(tmp_path)  # not the repo root
    rc = launcher.main(["--plan", "config/run_plan.json", "--dry-run", "--allow-unfrozen",
                        "--frozen", str(tmp_path / "none.json"), "--results-root", str(tmp_path / "r" / "runs"),
                        "--decisions", str(tmp_path / "D.md")], clock=FakeClock())
    assert rc == 0 and "32 runs" in capsys.readouterr().out
    assert launcher._repo_path("docs/x.md") == launcher.REPO_ROOT / "docs/x.md"
    assert launcher._repo_path(tmp_path) == tmp_path


# ---------------------------------------------------------------- final review fix wave

def test_run_configs_from_defaults_json(tmp_path, monkeypatch, capsys):
    """A1: every run config takes model, max_tokens, burn_in, N, windows, message cap and concurrency
    from config/defaults.json, cost from frozen.json over defaults, and the plan's T."""
    from src.procurement import config
    monkeypatch.setattr(subprocess, "Popen", _no_popen)
    real = config.load_defaults()
    changed = {**real, "model": "claude-test-model", "max_tokens": 6000, "burn_in": 12, "history_window": 15,
               "memory_window": 7, "max_message_length": 300, "concurrency": 3,
               "cost": {**real["cost"], "honest_noise": 0.04}}
    monkeypatch.setattr(config, "load_defaults", lambda *a, **k: changed)
    results = tmp_path / "results"
    assert launcher.main(["--plan", str(_plan_file(tmp_path, T=100)), "--dry-run", "--frozen", str(_frozen(tmp_path)),
                          "--results-root", str(results / "runs"), "--decisions", str(tmp_path / "D.md")],
                         clock=FakeClock()) == 0
    (ws,) = (results / "launches").iterdir()
    configs = sorted((ws / "configs").glob("*.json"))
    assert len(configs) == 32
    for path in configs:
        d = json.loads(path.read_text(encoding="utf-8"))
        assert (d["model"], d["max_tokens"], d["burn_in"], d["N"], d["history_window"], d["memory_window"],
                d["max_message_length"], d["concurrency"], d["T"]) == (
            "claude-test-model", 6000, 12, 6, 15, 7, 300, 3, 100), path.name
        assert d["cost"]["beta0"] == 0.93 and d["cost"]["honest_noise"] == 0.04


def test_refuses_synthetic_or_unfrozen_into_default_root(tmp_path, monkeypatch, capsys):
    """B3: a real launch into results/runs refuses --bidder synthetic and --allow-unfrozen (exit 5,
    nothing written); another results root is fine."""
    monkeypatch.setattr(subprocess, "Popen", _no_popen)
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    launches = launcher.REPO_ROOT / "results" / "launches"
    before = sorted(launches.iterdir()) if launches.is_dir() else []
    plan = str(_plan_file(tmp_path))
    for extra in (["--bidder", "synthetic", "--frozen", str(_frozen(tmp_path))],
                  ["--allow-unfrozen", "--frozen", str(tmp_path / "none.json")],
                  ["--bidder", "synthetic", "--allow-unfrozen", "--results-root", "results/runs"]):
        rc = launcher.main(["--plan", plan, "--decisions", str(tmp_path / "D.md"), *extra], clock=FakeClock(),
                           popen=_no_popen)
        assert rc == 5, extra
        assert "refusing to start" in capsys.readouterr().err
    assert (sorted(launches.iterdir()) if launches.is_dir() else []) == before
    assert not (tmp_path / "D.md").exists()
    # elsewhere: allowed
    assert launcher.main(["--plan", plan, "--bidder", "synthetic", "--allow-unfrozen", "--dry-run",
                          "--frozen", str(tmp_path / "none.json"), "--results-root", str(tmp_path / "r" / "runs"),
                          "--decisions", str(tmp_path / "D.md")], clock=FakeClock()) == 0
    clock = FakeClock()
    popen = FakePopen(clock)
    assert launcher.main(["--plan", str(_plan_file(tmp_path, R={"control": 1})), "--bidder", "synthetic",
                          "--allow-unfrozen", "--frozen", str(tmp_path / "none.json"), "--poll-s", "5",
                          "--results-root", str(tmp_path / "r2" / "runs"), "--decisions", str(tmp_path / "D.md")],
                         clock=clock, sleep=clock.sleep, popen=popen) == 0
    assert len(popen.calls) == 1


def test_plan_R_caps_sized_R(tmp_path, monkeypatch, capsys):
    """B4: with calibration limits R is the per-condition minimum of the plan's R and the sized R."""
    monkeypatch.setattr(subprocess, "Popen", _no_popen)
    monkeypatch.delenv("ANTHROPIC_API_KEY_2", raising=False)
    results = tmp_path / "results"
    results.mkdir()
    (results / "calibration.json").write_text(json.dumps({
        "limits": {"1": {"requests": 4000, "input_tokens": 4_000_000, "output_tokens": 1_000_000}}}),
        encoding="utf-8")
    plan = _plan_file(tmp_path, R={"control": 0, "cover": 1, "markup": 0, "rotation": 0})
    assert launcher.main(["--plan", str(plan), "--dry-run", "--allow-unfrozen", "--frozen", str(tmp_path / "n.json"),
                          "--results-root", str(results / "runs"), "--decisions", str(tmp_path / "D.md")],
                         clock=FakeClock()) == 0
    (ws,) = (results / "launches").iterdir()
    status = json.loads((ws / "status.json").read_text(encoding="utf-8"))
    assert [(r["condition"], r["seed"]) for r in status["runs"]] == [("cover", 1)]
    assert status["R"] == {"control": 0, "cover": 1, "markup": 0, "rotation": 0}
    log = (ws / "launcher.log").read_text(encoding="utf-8")
    assert "sized R 12/12/4/4" in log and "plan R 0/1/0/0" in log and "-> R 0/1/0/0" in log


def test_read_calibration_non_integer_slot(tmp_path, capsys, monkeypatch):
    """C6: a non-integer slot key is an invalid calibration file: None, refused in real mode (exit 5)."""
    path = tmp_path / "results" / "calibration.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"limits": {"1": {"requests": 1000, "output_tokens": 400000},
                                           "two": {"requests": 1}}}), encoding="utf-8")
    assert launcher.read_calibration(path) is None
    path.with_name("c2.json").write_text(json.dumps({"limits": [1, 2]}), encoding="utf-8")
    assert launcher.read_calibration(path.with_name("c2.json")) is None
    monkeypatch.setattr(subprocess, "Popen", _no_popen)
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    rc = launcher.main(["--plan", str(_plan_file(tmp_path)), "--frozen", str(_frozen(tmp_path)),
                        "--results-root", str(tmp_path / "results" / "runs"), "--decisions", str(tmp_path / "D.md")],
                       clock=FakeClock(), popen=_no_popen)
    assert rc == 5 and "refusing to start" in capsys.readouterr().err
    assert not (tmp_path / "results" / "launches").exists()


def _experiment_dir(root: Path, cfg: RunConfig, name: str, *, finished=False, checkpoint=False,
                    activity: datetime | None = None) -> Path:
    """A run dir with the run_meta the runner writes for ``cfg`` (purpose experiment)."""
    from src.procurement.environment import generate_environment
    d = root / name
    d.mkdir(parents=True)
    meta = runner.make_run_meta(cfg, generate_environment(cfg.seed, cfg.cost, cfg.T, cfg.N))
    meta["finished_utc"] = "2026-10-25T11:00:00+00:00" if finished else None
    io.write_run_meta(d, meta)
    if activity is not None:
        io.write_heartbeat(d, 3, activity, activity)
    if checkpoint:
        io.write_checkpoint(d, {"last_completed_project": 3})
    return d


def _synthetic_cfg(condition, seed, T=12):
    from src.procurement import config
    return config.run_config_from_defaults(config.load_defaults(), condition=condition, seed=seed, T=T,
                                           bidder="synthetic")


def _relaunch_args(tmp_path, root, R, T=12, extra=()):
    return ["--plan", str(_plan_file(tmp_path, R=R, T=T)), "--bidder", "synthetic", "--allow-unfrozen",
            "--frozen", str(tmp_path / "none.json"), "--results-root", str(root), "--poll-s", "5",
            "--stagger-s", "0", "--decisions", str(tmp_path / "D.md"), *extra]


def test_relaunch_live_run_refused(tmp_path, capsys):
    """B5: an unfinished matching run with activity within stall_s refuses the launch (exit 5) unless
    --ignore-live."""
    root = tmp_path / "results" / "runs"
    live = _experiment_dir(root, _synthetic_cfg("cover", 1), "20261025_115000_cover_s1", checkpoint=True,
                           activity=T0 - timedelta(seconds=60))
    clock = FakeClock()
    popen = FakePopen(clock)
    rc = launcher.main(_relaunch_args(tmp_path, root, {"cover": 1}), clock=clock, sleep=clock.sleep, popen=popen)
    assert rc == 5 and popen.calls == []
    err = capsys.readouterr().err
    assert "cover_s1" in err and live.name in err and "--ignore-live" in err
    assert not (tmp_path / "results" / "launches").exists() and not (tmp_path / "D.md").exists()
    assert launcher.main(_relaunch_args(tmp_path, root, {"cover": 1}, extra=["--dry-run"]), clock=clock) == 0
    assert "a real launch would refuse" in capsys.readouterr().err  # a dry run only warns
    rc = launcher.main(_relaunch_args(tmp_path, root, {"cover": 1}, extra=["--ignore-live"]), clock=clock,
                       sleep=clock.sleep, popen=popen)
    assert rc == 0 and _arg(popen.calls[0][2], "--resume-dir") == str(live)


def test_relaunch_stale_checkpointed_run_resumed(tmp_path):
    """B5: a stale unfinished run with a checkpoint is resumed with --resume-dir (the latest such dir);
    the resume uses the one-restart budget, so a crash afterwards is final."""
    root = tmp_path / "results" / "runs"
    cfg = _synthetic_cfg("cover", 1)
    _experiment_dir(root, cfg, "20261025_090000_cover_s1", checkpoint=True, activity=T0 - timedelta(hours=3))
    latest = _experiment_dir(root, cfg, "20261025_100000_cover_s1", checkpoint=True,
                             activity=T0 - timedelta(hours=2))
    _experiment_dir(root, _synthetic_cfg("cover", 1, T=40), "20261025_110000_cover_s1", checkpoint=True,
                    activity=T0 - timedelta(hours=1))  # another T: not the same run
    clock = FakeClock()
    popen = FakePopen(clock, lambda name, attempt: {"exit_at": 30, "code": 1})  # every attempt crashes
    rc = launcher.main(_relaunch_args(tmp_path, root, {"cover": 1, "control": 1}), clock=clock,
                       sleep=clock.sleep, popen=popen)
    assert rc == 6
    cover = [c for c in popen.calls if "cover" in c[0]]
    assert len(cover) == 1 and _arg(cover[0][2], "--resume-dir") == str(latest)  # not restarted again
    control = [c for c in popen.calls if "control" in c[0]]
    assert len(control) == 2 and _arg(control[0][2], "--resume-dir") is None  # a fresh run keeps its restart
    (ws,) = (tmp_path / "results" / "launches").iterdir()
    manifest = json.loads((ws / "manifest.json").read_text(encoding="utf-8"))
    assert {e["name"]: e["resume_dir"] for e in manifest["runs"]} == {"00_cover_s1": str(latest),
                                                                       "01_control_s1": None}
    status = json.loads((ws / "status.json").read_text(encoding="utf-8"))
    st = {r["name"]: (r["state"], r["restarts"]) for r in status["runs"]}
    assert st == {"00_cover_s1": ("failed", 1), "01_control_s1": ("failed", 1)}
    assert "resume cover_s1: unfinished run " + latest.name in (ws / "launcher.log").read_text(encoding="utf-8")


def test_relaunch_stale_run_without_checkpoint_fresh(tmp_path):
    """B5: a stale unfinished run without a checkpoint is started fresh."""
    root = tmp_path / "results" / "runs"
    _experiment_dir(root, _synthetic_cfg("cover", 1), "20261025_100000_cover_s1",
                    activity=T0 - timedelta(hours=2))
    clock = FakeClock()
    popen = FakePopen(clock)
    assert launcher.main(_relaunch_args(tmp_path, root, {"cover": 1}), clock=clock, sleep=clock.sleep,
                         popen=popen) == 0
    assert len(popen.calls) == 1 and _arg(popen.calls[0][2], "--resume-dir") is None


def test_relaunch_keeps_T_of_existing_runs(tmp_path):
    """C5: with a finished or resumable run of the plan already there, the time ladder keeps that T
    (never changing T mid-experiment) while still cutting R and dropping rotation."""
    root = tmp_path / "results" / "runs"
    _experiment_dir(root, _synthetic_cfg("cover", 1, T=110), "20261025_060000_cover_s1", finished=True)
    decisions = tmp_path / "D.md"
    clock = FakeClock(datetime(2026, 10, 26, 7, 59, tzinfo=timezone.utc))  # deadline - 4 h: rung 2
    popen = FakePopen(clock, lambda name, a: {"exit_at": 10, "code": 0})
    args = ["--plan", str(_plan_file(tmp_path, "2026-10-26T11:59:00Z")), "--allow-unfrozen", "--bidder",
            "synthetic", "--frozen", str(tmp_path / "none.json"), "--results-root", str(root), "--poll-s", "5",
            "--decisions", str(decisions)]
    assert launcher.main(args, clock=clock, sleep=clock.sleep, popen=popen) == 0
    (ws,) = (tmp_path / "results" / "launches").iterdir()
    status = json.loads((ws / "status.json").read_text(encoding="utf-8"))
    assert status["T"] == 110 and status["skipped_done"] == ["cover_s1"]
    assert status["R"] == {"control": 12, "cover": 11, "markup": 2, "rotation": 0}
    for path in (ws / "configs").glob("*.json"):
        assert json.loads(path.read_text(encoding="utf-8"))["T"] == 110
    text = decisions.read_text(encoding="utf-8")
    assert "T = 80" in text and "T kept at 110" in text
    # without the existing run the ladder lowers T as before
    root2 = tmp_path / "other" / "runs"
    args2 = [a if a != str(root) else str(root2) for a in args]
    assert launcher.main(args2 + ["--dry-run"], clock=clock) == 0
    (ws2,) = (tmp_path / "other" / "launches").iterdir()
    assert json.loads((ws2 / "status.json").read_text(encoding="utf-8"))["T"] == 80


# ---------------------------------------------------------------- round 2: arms, --no-ladder, run ids

ARM_ENTRIES = [
    {"arm": "mem0", "memory_window": 0, "scratchpad_enabled": False, "history_window": 20, "bidder_info": "shown"},
    {"arm": "stateless", "memory_window": 0, "scratchpad_enabled": False, "history_window": 0,
     "bidder_info": "shown"},
    {"arm": "hidden", "memory_window": 10, "scratchpad_enabled": True, "history_window": 20,
     "bidder_info": "hidden"},
    {"arm": "stateless_hidden", "memory_window": 0, "scratchpad_enabled": False, "history_window": 0,
     "bidder_info": "hidden"},
]


def _plan_file_with_arms(tmp_path, n=12, T=110, deadline="2026-10-27T11:59:00Z") -> Path:
    p = tmp_path / "run_plan_arms.json"
    p.write_text(json.dumps({"R": FULL_R, "include_rotation": True, "T": T, "deadline_utc": deadline,
                             "arms": [{**e, "R": {"control": n}} for e in ARM_ENTRIES]}), encoding="utf-8")
    return p


def _arm_args(tmp_path, arms, *extra, plan=None):
    return ["--plan", str(plan or _plan_file_with_arms(tmp_path)), "--dry-run", "--allow-unfrozen",
            "--results-root", str(tmp_path / "results" / "runs"), "--decisions", str(tmp_path / "D.md"),
            "--arms", arms, *extra]


def test_launcher_arms_subset(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "Popen", _no_popen)
    rc = launcher.main(_arm_args(tmp_path, "mem0,stateless"), clock=FakeClock(), popen=_no_popen)
    assert rc == 0
    (ws,) = (tmp_path / "results" / "launches").iterdir()
    manifest = json.loads((ws / "manifest.json").read_text(encoding="utf-8"))
    runs = manifest["runs"]
    assert len(runs) == 24 and {r["arm"] for r in runs} == {"mem0", "stateless"}
    assert sorted(r["run_id"] for r in runs)[:2] == ["control_mem0_s1", "control_mem0_s10"]
    mem0 = next(r for r in runs if r["run_id"] == "control_mem0_s1")
    data = json.loads(Path(mem0["config"]).read_text(encoding="utf-8"))
    assert (data["memory_window"], data["scratchpad_enabled"], data["bidder_info"]) == (0, False, "shown")
    st = next(r for r in runs if r["arm"] == "stateless")
    data = json.loads(Path(st["config"]).read_text(encoding="utf-8"))
    assert data["history_window"] == 0 and data["T"] == 110 and data["condition"] == "control"
    assert data["arm"] == "stateless"
    log = (ws / "launcher.log").read_text(encoding="utf-8")
    assert "launch plan (dry run): 24 runs" in log and "time ladder: off" in log
    assert "mem0/control 12" in log and "stateless/control 12" in log


def test_arms_unknown_refused(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "Popen", _no_popen)
    rc = launcher.main(_arm_args(tmp_path, "mem0,nope"), clock=FakeClock(), popen=_no_popen)
    assert rc == launcher.EXIT_PREFLIGHT
    assert "nope" in capsys.readouterr().err
    assert not (tmp_path / "results" / "launches").exists()


def test_no_ladder_skips_rungs(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "Popen", _no_popen)
    near = datetime(2026, 10, 25, 13, 0, tzinfo=timezone.utc)  # 1 h before the deadline below
    plan = _plan_file(tmp_path, "2026-10-25T14:00:00Z")
    base = ["--plan", str(plan), "--dry-run", "--allow-unfrozen", "--decisions", str(tmp_path / "D.md"),
            "--results-root", str(tmp_path / "results" / "runs")]
    assert launcher.main(base, clock=FakeClock(near), popen=_no_popen) == launcher.EXIT_LADDER
    assert launcher.main([*base, "--no-ladder"], clock=FakeClock(near), popen=_no_popen) == 0
    (ws,) = (tmp_path / "results" / "launches").iterdir()
    assert "time ladder: off" in (ws / "launcher.log").read_text(encoding="utf-8")
    status = json.loads((ws / "status.json").read_text(encoding="utf-8"))
    assert status["R"] == FULL_R and status["rung"] == 0  # full plan, nothing lowered


def test_arms_imply_no_ladder_and_skip_sizing_minimum(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", _no_popen)
    monkeypatch.delenv("ANTHROPIC_API_KEY_2", raising=False)
    results = tmp_path / "results"
    results.mkdir()
    (results / "calibration.json").write_text(json.dumps({
        "limits": {"1": {"requests": 1000, "input_tokens": 2_000_000, "output_tokens": 400_000}}}),
        encoding="utf-8")
    near = datetime(2026, 10, 25, 13, 0, tzinfo=timezone.utc)
    plan = _plan_file_with_arms(tmp_path, deadline="2026-10-25T14:00:00Z")
    assert launcher.main(_arm_args(tmp_path, "mem0", plan=plan), clock=FakeClock(near), popen=_no_popen) == 0
    (ws,) = (results / "launches").iterdir()
    manifest = json.loads((ws / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["runs"]) == 12  # sized control R would be 8; arms take R from the plan
    assert "time ladder: off" in (ws / "launcher.log").read_text(encoding="utf-8")


def test_spend_cap_from_config(tmp_path, monkeypatch):
    from src.procurement import config
    assert config.load_defaults()["spend_stop_usd"] == 150
    seen = {}
    real = launcher.Launcher

    class Spy(real):
        def __init__(self, *a, **k):
            seen["stop_usd"] = k["stop_usd"]
            super().__init__(*a, **k)

    monkeypatch.setattr(launcher, "Launcher", Spy)
    monkeypatch.setattr(subprocess, "Popen", _no_popen)
    assert launcher.main(_arm_args(tmp_path, "mem0"), clock=FakeClock(), popen=_no_popen) == 0
    assert seen["stop_usd"] == 150.0


def _arm_cfg(arm, condition="control", seed=1, T=12, **kw):
    from src.procurement import config
    entry = next(e for e in ARM_ENTRIES if e["arm"] == arm) if arm != "base" else {}
    fields = {k: v for k, v in entry.items() if k != "arm"}
    return config.run_config_from_defaults(config.load_defaults(), condition=condition, seed=seed, T=T,
                                           bidder="synthetic", arm=arm, **fields, **kw)


def test_finished_runs_keyed_by_arm(tmp_path):
    root = tmp_path / "runs"
    _experiment_dir(root, _arm_cfg("base"), "20261025_100000_control_s1", finished=True)
    plan = [_arm_cfg("mem0"), _arm_cfg("base")]
    assert list(launcher.finished_runs(root, plan)) == ["control_s1"]  # control_mem0_s1 is not done
    _experiment_dir(root, _arm_cfg("mem0"), "20261025_110000_control_mem0_s1", finished=True)
    assert set(launcher.finished_runs(root, plan)) == {"control_s1", "control_mem0_s1"}


def test_live_other_arm_does_not_block(tmp_path, capsys):
    """A live unfinished wave-1 run (mem0) neither blocks nor gets resumed by a hidden-arm launch."""
    root = tmp_path / "results" / "runs"
    live = _experiment_dir(root, _arm_cfg("mem0", seed=3), "20261025_115000_control_mem0_s3", checkpoint=True,
                           activity=T0 - timedelta(seconds=30))
    plan = _plan_file_with_arms(tmp_path, n=1)
    args = ["--plan", str(plan), "--bidder", "synthetic", "--allow-unfrozen", "--frozen", str(tmp_path / "n.json"),
            "--results-root", str(root), "--poll-s", "5", "--stagger-s", "0", "--decisions", str(tmp_path / "D.md"),
            "--arms", "hidden,stateless_hidden"]
    clock = FakeClock()
    popen = FakePopen(clock)
    assert launcher.main(args, clock=clock, sleep=clock.sleep, popen=popen) == 0
    assert len(popen.calls) == 2 and all(_arg(c[2], "--resume-dir") is None for c in popen.calls)
    assert live.name not in capsys.readouterr().err
    # a launch for the live arm itself (seed 3 needs n >= 3) is refused
    plan3 = _plan_file_with_arms(tmp_path, n=3)
    args3 = [str(plan3) if a == str(plan) else a for a in args]
    args3[-1] = "mem0"
    rc = launcher.main(args3, clock=clock, sleep=clock.sleep, popen=FakePopen(clock))
    assert rc == launcher.EXIT_PREFLIGHT
