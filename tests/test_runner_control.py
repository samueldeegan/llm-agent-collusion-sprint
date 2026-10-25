import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.procurement.config import CostParams, RunConfig
from src.procurement.environment import generate_environment
from src.procurement.io import (BIDS_COLUMNS, CALLS_COLUMNS, MESSAGES_COLUMNS, TRUTH_COLUMNS,
                                read_checkpoint, read_run_meta)
from src.procurement.runner import make_run_meta, resolve_cartel_seats, run, run_in_memory
from src.procurement.synthetic import SyntheticBidder, simulate_table, synthetic_bidder_factory
from src.procurement.types import BidResult

COST = CostParams(0.90, 0.08, 0.17, 0.05)
CFG = RunConfig(condition="control", seed=1)
RUN_FILES = ["bids.csv", "truth.csv", "messages.csv", "calls.csv", "run_meta.json",
             "heartbeat.json", "checkpoint.json"]
META_KEYS = {"purpose", "condition", "scheme", "seed", "T", "burn_in", "cartel_seats", "seat_to_firm",
             "cost", "windows", "model", "max_tokens", "effort", "bidder", "concurrency",
             "api_key_slot", "frozen_from", "limits", "started_utc", "finished_utc", "git_commit"}


@pytest.fixture(scope="module")
def control_run(tmp_path_factory):
    root = tmp_path_factory.mktemp("runs")
    return run(CFG, synthetic_bidder_factory, results_root=root)


def _header(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()[0].split(",")


def test_end_to_end_synthetic_control(control_run, tmp_path):
    d = control_run
    for name in RUN_FILES:
        assert (d / name).is_file(), name
    assert not (d / "transcripts").exists()
    assert d.name.endswith("_control_s1")

    b = pd.read_csv(d / "bids.csv", dtype=str, keep_default_na=False)
    assert list(b.columns) == BIDS_COLUMNS
    assert len(b) == 660
    assert set(b["run_id"]) == {"control_s1"}
    pid = b["project_id"].astype(int)
    assert (pid.value_counts().sort_index() == 6).all()
    assert sorted(pid.unique()) == list(range(1, 111))
    assert list(pid) == sorted(pid)  # project-major
    for col in ("est", "dist", "cap", "firm_rate", "bid"):
        assert all(math.isfinite(float(x)) for x in b[col]), col
    assert set(b["tag"]) <= {"routine", "standard", "complex", "hazardous"}
    assert set(b["won"]) <= {"0", "1"}
    assert (b["won"].astype(int).groupby(pid).sum() <= 1).all()
    caps = b.assign(cap=b["cap"].astype(float), pid=pid)
    for _, g in caps.groupby("firm_id"):
        assert g.sort_values("pid")["cap"].is_monotonic_increasing
    assert caps[caps.pid == 1]["cap"].eq(0.0).all()
    # firm order within a project is seat order
    env = generate_environment(1, COST, 110, 6)
    assert list(b[pid == 1]["firm_id"]) == env.names

    assert _header(d / "messages.csv") == MESSAGES_COLUMNS
    assert len((d / "messages.csv").read_text(encoding="utf-8").splitlines()) == 1
    assert _header(d / "calls.csv") == CALLS_COLUMNS
    assert len((d / "calls.csv").read_text(encoding="utf-8").splitlines()) == 1

    d2 = run(CFG, synthetic_bidder_factory, results_root=tmp_path)
    assert (d2 / "bids.csv").read_bytes() == (d / "bids.csv").read_bytes()
    assert (d2 / "truth.csv").read_bytes() == (d / "truth.csv").read_bytes()


def test_runner_equals_simulate_table_control():
    bids, truth, messages = run_in_memory(CFG, synthetic_bidder_factory)
    eb, et = simulate_table(1, COST, 110, None, None)
    pd.testing.assert_frame_equal(bids, eb, check_dtype=False)
    pd.testing.assert_frame_equal(truth, et, check_dtype=False)
    assert messages == []


def test_run_in_memory_matches_files(control_run):
    bids, truth, _ = run_in_memory(CFG, synthetic_bidder_factory)
    fb = pd.read_csv(control_run / "bids.csv")
    ft = pd.read_csv(control_run / "truth.csv", keep_default_na=False)
    pd.testing.assert_frame_equal(fb, bids, check_dtype=False)
    pd.testing.assert_frame_equal(ft, truth, check_dtype=False)


def test_truth_table_schema(control_run):
    b = pd.read_csv(control_run / "bids.csv")
    t = pd.read_csv(control_run / "truth.csv", keep_default_na=False)
    assert list(t.columns) == TRUTH_COLUMNS
    assert len(t) == 660
    assert (t[["run_id", "project_id", "firm_id"]].values == b[["run_id", "project_id", "firm_id"]].values).all()
    assert set(t["is_cartel"]) == {0}
    assert set(t["cartel_role"]) == {"none"}
    assert set(t["scheme"]) == {"none"}
    assert list(t["seat"][:6]) == list(range(6))
    c = COST
    recomputed = b["est"] * (c.beta0 + c.beta1 * np.log1p(b["dist"]) + c.beta2 * b["cap"]
                             + b["firm_rate"] + t["nu"] + t["eps"])
    assert np.max(np.abs(recomputed - t["cost"])) < 1e-6


def test_truth_never_in_bids(control_run):
    cols = _header(control_run / "bids.csv")
    for forbidden in ("seat", "cost", "eps", "nu", "is_cartel", "cartel_role", "scheme",
                      "my_cost", "intended_serious_bid", "text"):
        assert forbidden not in cols


def test_heartbeat_written(control_run):
    hb = json.loads((control_run / "heartbeat.json").read_text(encoding="utf-8"))
    assert hb["last_completed_project"] == 110
    assert hb["last_completed_utc"] is not None
    assert hb["last_activity_utc"] is not None
    cp = read_checkpoint(control_run)
    assert set(cp) == {"last_completed_project", "won", "history", "messages", "run_dir"}
    assert cp["last_completed_project"] == 110
    assert cp["messages"] == []
    assert len(cp["history"]) == 110
    assert Path(cp["run_dir"]) == control_run
    b = pd.read_csv(control_run / "bids.csv")
    won_est = b[b.won == 1].groupby("firm_id")["est"].sum()
    env = generate_environment(1, COST, 110, 6)
    for seat, name in enumerate(env.names):
        assert cp["won"][seat] == pytest.approx(won_est.get(name, 0.0))


def test_no_overwrite_results(control_run, tmp_path):
    before = (control_run / "bids.csv").read_bytes()
    root = control_run.parent
    n_before = len([p for p in root.iterdir() if p.is_dir()])
    short = RunConfig(condition="control", seed=1, T=5)
    d1 = run(short, synthetic_bidder_factory, results_root=root)
    first = (d1 / "bids.csv").read_bytes()
    d2 = run(short, synthetic_bidder_factory, results_root=root)  # same second: suffixed dir
    assert len({control_run, d1, d2}) == 3
    assert d1.parent == d2.parent == root
    assert len([p for p in root.iterdir() if p.is_dir()]) == n_before + 2
    assert (control_run / "bids.csv").read_bytes() == before
    assert (d1 / "bids.csv").read_bytes() == first


def test_run_meta_contents(control_run):
    meta = read_run_meta(control_run)
    assert set(meta) == META_KEYS
    env = generate_environment(1, COST, 110, 6)
    assert meta["purpose"] == "experiment"
    assert meta["condition"] == "control"
    assert meta["scheme"] == "none"
    assert meta["cartel_seats"] == []
    assert meta["effort"] == "default (medium)"
    assert meta["seed"] == 1 and meta["T"] == 110 and meta["burn_in"] == 10
    assert meta["seat_to_firm"] == {str(i): n for i, n in enumerate(env.names)}
    assert meta["cost"] == {"beta0": 0.90, "beta1": 0.08, "beta2": 0.17, "sigma_eps": 0.05,
                            "K": env.K, "season_total_est": env.season_total}
    assert meta["windows"] == {"history": 20, "memory": 10}
    assert meta["model"] == "claude-haiku-5-5" and meta["max_tokens"] == 8000
    assert meta["bidder"] == "synthetic" and meta["concurrency"] == 6 and meta["api_key_slot"] == 1
    assert meta["frozen_from"] is None and meta["limits"] is None
    assert meta["started_utc"] and meta["finished_utc"]
    assert meta["finished_utc"] >= meta["started_utc"]
    assert isinstance(meta["git_commit"], str) and meta["git_commit"]

    m = make_run_meta(CFG, env, limits={"requests": 1000}, frozen_from="config/frozen.json", purpose="smoke")
    assert m["purpose"] == "smoke" and m["limits"] == {"requests": 1000}
    assert m["frozen_from"] == "config/frozen.json"
    assert m["finished_utc"] is None


def test_calibration_purpose_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = RunConfig(condition="control", seed=2, T=3)
    d = run(cfg, synthetic_bidder_factory, purpose="smoke")
    assert d.parent.resolve() == (tmp_path / "results" / "calibration_runs").resolve()
    assert read_run_meta(d)["purpose"] == "smoke"


class _StopAfter:
    """Synthetic bidder that drops a STOP file into the (single) run dir during project ``stop_pid``."""

    def __init__(self, cfg, env, root: Path, stop_pid: int):
        self.inner = SyntheticBidder(cfg.seed, cfg.cost, cfg.T, cfg.N)
        self.root = root
        self.stop_pid = stop_pid

    def bid(self, ctx):
        if ctx.t + 1 == self.stop_pid and ctx.seat == 0:
            (run_dir,) = [p for p in self.root.iterdir() if p.is_dir()]
            (run_dir / "STOP").touch()
        return self.inner.bid(ctx)

    def message(self, ctx):
        return self.inner.message(ctx)


def test_runner_honours_stop_file(tmp_path):
    factory = lambda cfg, env: _StopAfter(cfg, env, tmp_path, 12)  # noqa: E731
    with pytest.raises(SystemExit) as exc:
        run(CFG, factory, results_root=tmp_path)
    assert exc.value.code == 3
    (d,) = [p for p in tmp_path.iterdir() if p.is_dir()]
    b = pd.read_csv(d / "bids.csv")
    assert len(b) == 72
    assert b["project_id"].max() == 12
    assert len(pd.read_csv(d / "truth.csv")) == 72
    assert read_run_meta(d)["finished_utc"] is None
    hb = json.loads((d / "heartbeat.json").read_text(encoding="utf-8"))
    assert hb["last_completed_project"] == 12
    assert read_checkpoint(d)["last_completed_project"] == 12


class _Scripted:
    """Records contexts; seat 2 never bids; project 1 seat 0 bids below everyone."""

    def __init__(self, cfg, env):
        self.inner = SyntheticBidder(cfg.seed, cfg.cost, cfg.T, cfg.N)
        self.contexts = []

    def bid(self, ctx):
        self.contexts.append(ctx)
        if ctx.seat == 2:
            return BidResult(bid=None, reason="bad_json", parsed=None, raw=None, call_records=[])
        if ctx.t == 0 and ctx.seat == 0:
            return BidResult(bid=1.234, reason="", parsed=None, raw=None, call_records=[])
        return self.inner.bid(ctx)

    def message(self, ctx):  # pragma: no cover - control never messages
        raise AssertionError("message called in a control run")


def test_missing_bid_and_contexts(tmp_path):
    holder = {}

    def factory(cfg, env):
        holder["b"] = _Scripted(cfg, env)
        return holder["b"]

    cfg = RunConfig(condition="control", seed=1, T=25, history_window=20)
    d = run(cfg, factory, results_root=tmp_path)
    raw = pd.read_csv(d / "bids.csv", dtype=str, keep_default_na=False)
    seat2 = raw.iloc[2::6]
    assert (seat2["bid"] == "").all() and (seat2["won"] == "0").all()
    assert raw.iloc[0]["bid"] == "1.23" and raw.iloc[0]["won"] == "1"

    ctxs = holder["b"].contexts
    assert len(ctxs) == 25 * 6
    for c in ctxs:
        assert c.memory == [] and c.scratchpad == "" and c.messages == []
        assert c.role is None and c.seats is None
        assert c.posting.startswith(f"Project {c.t + 1}\n")
        assert len(c.history) == min(c.t, 20)
    last = [c for c in ctxs if c.t == 24][0]
    h = last.history[-1]
    assert set(h) == {"project_id", "est", "tag", "bids", "winner", "post_posting"}
    assert h["project_id"] == 24
    assert last.history[0]["project_id"] == 5
    first = [c for c in ctxs if c.t == 1][0].history[0]
    env = generate_environment(1, cfg.cost, 25, 6)
    assert first["bids"][env.names[2]] is None
    assert first["bids"][env.names[0]] == 1.23
    assert first["winner"] == env.names[0]
    assert "no bid" in first["post_posting"]


def test_bidder_exception_propagates(tmp_path):
    class Boom:
        def bid(self, ctx):
            if ctx.t == 3:
                raise RuntimeError("bug in bidder")
            return BidResult(bid=100.0 + ctx.seat, reason="", parsed=None, raw=None, call_records=[])

        def message(self, ctx):
            raise AssertionError

    with pytest.raises(RuntimeError, match="bug in bidder"):
        run(RunConfig(condition="control", seed=1, T=10), lambda c, e: Boom(), results_root=tmp_path)


def test_scheme_and_resume_not_implemented(tmp_path):
    with pytest.raises(NotImplementedError):
        run(RunConfig(condition="cover", seed=1), synthetic_bidder_factory, results_root=tmp_path)
    with pytest.raises(NotImplementedError):
        run_in_memory(RunConfig(condition="markup", seed=1), synthetic_bidder_factory)
    with pytest.raises(NotImplementedError):
        run(CFG, synthetic_bidder_factory, results_root=tmp_path, resume_dir=tmp_path)


@pytest.mark.parametrize("cfg, expected", [
    (RunConfig(condition="cover", seed=1), [0, 3]),
    (RunConfig(condition="rotation", seed=1), [1, 4]),
    (RunConfig(condition="markup", seed=1), [2, 5]),
    (RunConfig(condition="control", seed=1), []),
    (RunConfig(condition="cover", seed=1, cartel_seats=(0, 1)), [0, 1]),
])
def test_run_meta_cartel_seats(cfg, expected):
    env = generate_environment(cfg.seed, cfg.cost, cfg.T, cfg.N)
    meta = make_run_meta(cfg, env)
    assert meta["cartel_seats"] == expected
    assert all(type(s) is int for s in meta["cartel_seats"])
    assert meta["scheme"] == ("none" if cfg.condition == "control" else cfg.condition)
    seats = resolve_cartel_seats(cfg)
    assert (seats is None) if not expected else (list(seats) == expected)


def test_transcript_written_for_raw(tmp_path):
    class RawBidder:
        def bid(self, ctx):
            return BidResult(bid=100.0 + ctx.seat, reason="", parsed=None, raw="x", call_records=[])

        def message(self, ctx):
            raise AssertionError

    cfg = RunConfig(condition="control", seed=1, T=2)
    d = run(cfg, lambda c, e: RawBidder(), results_root=tmp_path)
    env = generate_environment(1, cfg.cost, 2, 6)
    files = sorted(p.name for p in (d / "transcripts").iterdir())
    assert files == sorted(f"{n}.md" for n in env.names)
    text = (d / "transcripts" / f"{env.names[0]}.md").read_text(encoding="utf-8")
    assert "## Prompt: Project 1 (bid)" in text and "## Response: Project 2 (bid)" in text
    assert "````\nx\n````" in text


def test_history_copied_per_seat(tmp_path):
    seen = []

    class Mutator:
        def __init__(self):
            self.inner = SyntheticBidder(1, COST, 4, 6)

        def bid(self, ctx):
            seen.append((ctx.t, ctx.seat, json.dumps(ctx.history, sort_keys=True)))
            for h in ctx.history:  # try to corrupt every view
                h["winner"] = "corrupted"
                h["bids"].clear()
            ctx.history.append({"junk": True})
            return self.inner.bid(ctx)

        def message(self, ctx):
            raise AssertionError

    d = run(RunConfig(condition="control", seed=1, T=4), lambda c, e: Mutator(), results_root=tmp_path)
    assert all("corrupted" not in h and "junk" not in h for _, _, h in seen)
    by_t = {}
    for t, _, h in seen:
        by_t.setdefault(t, set()).add(h)
    assert all(len(v) == 1 for v in by_t.values())  # every seat saw the same history
    cp = read_checkpoint(d)
    assert len(cp["history"]) == 4 and all(h["winner"] != "corrupted" for h in cp["history"])
