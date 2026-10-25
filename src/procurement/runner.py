"""The project loop: postings, (message phase), bids, award, logging, heartbeat/checkpoint, STOP, resume."""
from __future__ import annotations

import argparse
import copy
import csv
import json
import math
import os
import re
import subprocess
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from src.procurement import io
from src.procurement.auction import AwardLedger, render_post_posting, render_pre_posting, resolve_project
from src.procurement.channel import Channel
from src.procurement.client import FatalAPIError
from src.procurement import config
from src.procurement.config import RunConfig
from src.procurement.environment import Environment, cost_of, generate_environment
from src.procurement.rules import cover_roles, rotation_roles
from src.procurement.synthetic import synthetic_bidder_factory
from src.procurement.types import BidContext, Bidder, BidResult, MessageResult

BidderFactory = Callable[[RunConfig, Environment], Bidder]

CALIBRATION_ROOT = "results/calibration_runs"
EFFORT = "default (medium)"
STOP_EXIT_CODE = 3
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _git_commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=_REPO_ROOT, capture_output=True,
                             text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    sha = out.stdout.strip()
    return sha if out.returncode == 0 and sha else "unknown"


def resolve_cartel_seats(cfg: RunConfig) -> tuple[int, int] | None:
    """The run's cartel pair: ``cfg.cartel_seats`` if set, else the §4f seat rule; None for control."""
    if cfg.condition == "control":
        return None
    seats = cfg.cartel_seats if cfg.cartel_seats is not None else config.cartel_seats(cfg.seed, cfg.condition)
    if seats is None:
        raise ValueError(f"no cartel seats for condition {cfg.condition!r}")
    return int(seats[0]), int(seats[1])


def scheme_of(cfg: RunConfig) -> str:
    """The ``scheme`` value of run_meta and truth.csv: "none" for control, else the condition."""
    return "none" if cfg.condition == "control" else cfg.condition


def bidder_factory_for(cfg: RunConfig) -> BidderFactory:
    """The bidder factory ``cfg.bidder`` names: "synthetic" or "llm"."""
    if cfg.bidder == "synthetic":
        return synthetic_bidder_factory
    if cfg.bidder == "llm":
        from src.procurement.llm_bidder import llm_bidder_factory  # imports this module
        return llm_bidder_factory
    raise ValueError(f"unknown bidder {cfg.bidder!r}; expected 'synthetic' or 'llm'")


def make_run_meta(cfg: RunConfig, env: Environment, limits: dict | None = None,
                  frozen_from: str | None = None, purpose: str = "experiment") -> dict:
    """``run_meta.json`` content per spec §4g, with ``finished_utc`` still null."""
    seats = resolve_cartel_seats(cfg)
    return {
        "purpose": purpose,
        "condition": cfg.condition,
        "scheme": scheme_of(cfg),
        "seed": cfg.seed,
        "T": cfg.T,
        "burn_in": cfg.burn_in,
        "cartel_seats": [] if seats is None else list(seats),
        "seat_to_firm": {str(i): name for i, name in enumerate(env.names)},
        "cost": {"beta0": cfg.cost.beta0, "beta1": cfg.cost.beta1, "beta2": cfg.cost.beta2,
                 "sigma_eps": cfg.cost.sigma_eps, "K": float(env.K),
                 "season_total_est": float(env.season_total)},
        "windows": {"history": cfg.history_window, "memory": cfg.memory_window},
        "model": cfg.model,
        "max_tokens": cfg.max_tokens,
        "effort": EFFORT,
        "bidder": cfg.bidder,
        "concurrency": cfg.concurrency,
        "api_key_slot": cfg.api_key_slot,
        "frozen_from": frozen_from,
        "limits": limits,
        "scripted": {"margin": cfg.scripted_margin, "rule": cfg.scripted_rule,
                     "serious_add": cfg.serious_add},
        "started_utc": _utc_now().isoformat(),
        "finished_utc": None,
        "git_commit": _git_commit(),
    }


def _clean_bid(result: BidResult) -> float | None:
    """Cents-rounded bid; None (missing) for an absent or non-finite value."""
    if result.bid is None:
        return None
    b = float(result.bid)
    return round(b, 2) if math.isfinite(b) else None


def _finite(x) -> float | None:
    """A usable messaged cost: a finite number, else None."""
    if x is None or isinstance(x, bool):
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _check_state_hook(bidder: Bidder) -> None:
    """The optional bidder state hook is all-or-nothing: ``get_state`` and ``set_state`` together."""
    has_get = callable(getattr(bidder, "get_state", None))
    has_set = callable(getattr(bidder, "set_state", None))
    if has_get != has_set:
        raise TypeError(f"{type(bidder).__name__} defines only one of get_state/set_state; define both or neither")


def _transcript(seat: int, phase: str, posting: str, result: BidResult | MessageResult
                ) -> tuple[int, str, str, str] | None:
    """A transcript entry for one call: the prompt the bidder sent (``result.prompt``; the posting when
    a bidder reports none) and its raw reply. Nothing when neither a prompt nor a reply exists (the
    synthetic bidder); a sent prompt with no reply records why it is missing."""
    if result.prompt is None and result.raw is None:
        return None
    prompt = posting if result.prompt is None else result.prompt
    raw = result.raw if result.raw is not None else f"(no response: {result.reason or 'missing'})"
    return seat, phase, prompt, raw


def _call_rows(run_id: str, project_id: int, firm_id: str, phase: str, records: list[dict]) -> list[dict]:
    rows = []
    for rec in records or []:
        row = {c: rec.get(c) for c in io.CALLS_COLUMNS}
        row["run_id"], row["project_id"] = run_id, project_id
        row["firm_id"] = rec.get("firm_id") or firm_id
        row["phase"] = rec.get("phase") or phase
        rows.append(row)
    return rows


class _Loop:
    """One run's project loop. With ``run_dir=None`` nothing is written (``run_in_memory``)."""

    def __init__(self, cfg: RunConfig, env: Environment, bidder: Bidder, run_dir: Path | None):
        _check_state_hook(bidder)
        self.cfg, self.env, self.bidder, self.run_dir = cfg, env, bidder, run_dir
        self.run_id = f"{cfg.condition}_s{cfg.seed}"
        self.ledger = AwardLedger(env.K, cfg.N)
        self.seats = resolve_cartel_seats(cfg)
        self.channel = Channel(self.seats, cfg.condition, cfg.max_message_length)  # control: closed
        self.history: list[dict] = []
        self.messages: list[dict] = []  # messages.csv rows of completed projects
        self.bid_rows: list[dict] = []
        self.truth_rows: list[dict] = []
        self.start = 0  # 0-based index of the first project this loop plays

    def restore(self, checkpoint: dict) -> None:
        """Continue after ``checkpoint["last_completed_project"]``: awards, history, messages, bidder state."""
        last = int(checkpoint["last_completed_project"])
        won = np.asarray(checkpoint["won"], dtype=float)
        if won.shape != (self.cfg.N,) or len(checkpoint["history"]) != last or not 0 <= last <= self.cfg.T:
            raise ValueError(f"checkpoint inconsistent with this run (last_completed_project {last})")
        stateful = hasattr(self.bidder, "set_state")
        if stateful and "bidder_state" not in checkpoint:
            raise ValueError("bidder has set_state but the checkpoint holds no bidder_state")
        self.ledger.won = won.copy()
        self.history = list(checkpoint["history"])
        self.messages = list(checkpoint["messages"])
        if stateful:
            self.bidder.set_state(checkpoint["bidder_state"])
        self.start = last

    # ------------------------------------------------------------ files

    def _write_headers(self) -> None:
        for name, cols in (("bids.csv", io.BIDS_COLUMNS), ("truth.csv", io.TRUTH_COLUMNS),
                           ("messages.csv", io.MESSAGES_COLUMNS), ("calls.csv", io.CALLS_COLUMNS)):
            io.write_csv(self.run_dir / name, [], cols)

    def _touch(self) -> None:
        if self.run_dir is not None:
            io.touch_activity(self.run_dir)

    def install_activity_hook(self) -> None:
        """Give a bidder that defines ``set_activity_hook`` (the LLM bidder) a callback that touches
        ``last_activity_utc`` (A52); its client calls it at the start and end of every API attempt, so
        a retry storm or a serial message phase still shows activity. No run dir: no hook."""
        hook = getattr(self.bidder, "set_activity_hook", None)
        if self.run_dir is not None and callable(hook):
            run_dir = self.run_dir
            hook(lambda: io.touch_activity(run_dir))

    def checkpoint_state(self, last_completed_project: int) -> dict:
        state = {"last_completed_project": last_completed_project, "won": self.ledger.won.copy(),
                 "history": self.history, "messages": self.messages, "run_dir": str(self.run_dir)}
        if hasattr(self.bidder, "get_state"):
            state["bidder_state"] = self.bidder.get_state()
        return state

    def _log_project(self, t: int, bid_rows, truth_rows, message_rows, call_rows,
                     transcripts: list[tuple[int, str, str, str]]) -> None:
        if self.run_dir is None:
            return
        d = self.run_dir
        for r in message_rows:
            io.append_csv_row(d / "messages.csv", r, io.MESSAGES_COLUMNS)
        for r in bid_rows:
            io.append_csv_row(d / "bids.csv", r, io.BIDS_COLUMNS)
        for r in truth_rows:
            io.append_csv_row(d / "truth.csv", r, io.TRUTH_COLUMNS)
        for r in call_rows:
            io.append_csv_row(d / "calls.csv", r, io.CALLS_COLUMNS)
        for seat, phase, prompt, raw in transcripts:
            io.append_transcript(d, self.env.names[seat], t + 1, phase, prompt, raw)
        now = _utc_now()
        io.write_heartbeat(d, t + 1, now, now)
        io.write_checkpoint(d, self.checkpoint_state(t + 1))

    def _check_stop(self) -> None:
        if self.run_dir is not None and (self.run_dir / "STOP").exists():
            # heartbeat and checkpoint for this project were written by _log_project
            raise SystemExit(STOP_EXIT_CODE)

    # ------------------------------------------------------------ roles

    def _message_role(self, pid: int, seat: int) -> str | None:
        """``ctx.role`` while writing a message: the schedule (rotation), "member" (markup); cover: None."""
        a, b = self.seats
        if self.cfg.condition == "rotation":
            return "serious" if seat == rotation_roles(pid, a, b)[0] else "cover"
        if self.cfg.condition == "markup":
            return "member"
        return None  # cover: the role is not known until both costs are exchanged

    def _bid_roles(self, pid: int, rows: list[dict], costs: list[float]) -> dict[int, str]:
        """``ctx.role`` of the two cartel seats at bid time. Cover: lower *messaged* cost is serious
        (tie: seat a), falling back to true costs if either message lacks a usable cost."""
        a, b = self.seats
        if self.cfg.condition == "markup":
            return {a: "member", b: "member"}
        if self.cfg.condition == "rotation":
            serious, cover = rotation_roles(pid, a, b)
        else:
            said = {r["sender"]: r["my_cost"] for r in rows}
            ca, cb = _finite(said.get(a)), _finite(said.get(b))
            if ca is None or cb is None:
                ca, cb = costs[a], costs[b]
            serious, cover = cover_roles(ca, cb, a, b)
        return {serious: "serious", cover: "cover"}

    def _truth_roles(self, pid: int, costs: list[float]) -> list[str]:
        """truth.csv ``cartel_role``: cover always from TRUE costs; rotation by parity; markup "member"."""
        roles = ["none"] * self.cfg.N
        if self.seats is None:
            return roles
        a, b = self.seats
        if self.cfg.condition == "markup":
            roles[a] = roles[b] = "member"
            return roles
        if self.cfg.condition == "rotation":
            serious, cover = rotation_roles(pid, a, b)
        else:
            serious, cover = cover_roles(costs[a], costs[b], a, b)
        roles[serious], roles[cover] = "serious", "cover"
        return roles

    # ------------------------------------------------------------ phases

    def _ctx(self, t: int, seat: int, caps, costs, posting: str, window: list[dict],
             messages: list[dict], role: str | None, seats) -> BidContext:
        return BidContext(run=self.cfg, env=self.env, t=t, seat=seat, cap=caps.copy(), cost=costs[seat],
                          history=copy.deepcopy(window), messages=copy.deepcopy(messages), memory=[],
                          scratchpad="", role=role, seats=seats, posting=posting)

    @staticmethod
    def _tracked(fn, seat: int, phase: str, ctx: BidContext, done: list):
        """``fn(ctx)``, appending ``(seat, phase, call records)`` to ``done`` when it returns, or the
        attempts of a ``FatalAPIError`` when it raises one (logged if the project aborts)."""
        try:
            res = fn(ctx)
        except FatalAPIError as err:
            done.append((seat, phase, err.records))
            raise
        done.append((seat, phase, res.call_records))
        return res

    def _calls(self, pool: ThreadPoolExecutor, t: int, pid: int, caps, costs, posting: str, done: list
               ) -> tuple[dict[int, BidResult], list[dict], dict[int, MessageResult]]:
        """All model calls of one project (spec A49). Control: one wave of six bids. Scheme: the four
        outsider bids and message 1 are issued together; message 2 when message 1 returns; the two
        cartel bids when message 2 returns. Results by seat. Any exception propagates once every call
        already issued has finished, so ``done`` then holds the records of all completed calls."""
        cfg = self.cfg
        window = self.history[-cfg.history_window:] if cfg.history_window > 0 else []
        issued = []

        def submit(fn, seat: int, phase: str, ctx: BidContext):
            fut = pool.submit(self._tracked, fn, seat, phase, ctx, done)
            issued.append(fut)
            return fut

        try:
            if self.seats is None:
                futures = {seat: submit(self.bidder.bid, seat, "bid",
                                        self._ctx(t, seat, caps, costs, posting, window, [], None, None))
                           for seat in range(cfg.N)}
                return {seat: futures[seat].result() for seat in range(cfg.N)}, [], {}

            futures = {}
            for seat in range(cfg.N):  # outsiders: no messages, no role, no cartel seats
                if not self.channel.can_message(seat):
                    futures[seat] = submit(self.bidder.bid, seat, "bid",
                                           self._ctx(t, seat, caps, costs, posting, window, [], None, None))
            msg_results: dict[int, MessageResult] = {}

            def send(seat: int, so_far: list[dict]) -> MessageResult:
                ctx = self._ctx(t, seat, caps, costs, posting, window, so_far, self._message_role(pid, seat),
                                self.seats)
                msg_results[seat] = submit(self.bidder.message, seat, "message", ctx).result()
                return msg_results[seat]

            rows = self.channel.exchange(pid, send)  # seat ints in sender/recipient
            roles = self._bid_roles(pid, rows, costs)
            for seat in self.seats:
                futures[seat] = submit(self.bidder.bid, seat, "bid",
                                       self._ctx(t, seat, caps, costs, posting, window, rows, roles[seat],
                                                 self.seats))
            return {seat: futures[seat].result() for seat in range(cfg.N)}, rows, msg_results
        except Exception:
            wait(issued)  # let in-flight calls finish so their records are in ``done``
            raise

    def _log_aborted_calls(self, pid: int, done: list) -> None:
        """calls.csv rows of an aborted project's completed calls (and a fatal call's attempts):
        messages in the order they were sent, then bids by seat. Nothing else of the project is logged."""
        if self.run_dir is None:
            return
        entries = sorted(enumerate(done), key=lambda e: (e[1][1] != "message",
                                                         e[0] if e[1][1] == "message" else e[1][0]))
        for _, (seat, phase, records) in entries:
            for r in _call_rows(self.run_id, pid, self.env.names[seat], phase, records):
                io.append_csv_row(self.run_dir / "calls.csv", r, io.CALLS_COLUMNS)

    # ------------------------------------------------------------ loop

    def run(self) -> None:
        cfg = self.cfg
        if self.run_dir is not None and self.start == 0:
            self._write_headers()
        with ThreadPoolExecutor(max_workers=cfg.concurrency) as pool:
            for t in range(self.start, cfg.T):
                self._project(pool, t)
                self._check_stop()

    def _project(self, pool: ThreadPoolExecutor, t: int) -> None:
        cfg, env = self.cfg, self.env
        pid, est = t + 1, float(env.est[t])
        caps = self.ledger.caps().copy()  # CAP before this project's award
        costs = [cost_of(env, cfg.cost, i, t, float(caps[i])) for i in range(cfg.N)]
        posting = render_pre_posting(env, t, caps, env.names)

        self._touch()
        done: list = []
        try:
            results, exchanged, msg_results = self._calls(pool, t, pid, caps, costs, posting, done)
        except Exception:
            self._log_aborted_calls(pid, done)
            raise
        self._touch()

        bids = {seat: _clean_bid(results[seat]) for seat in range(cfg.N)}
        winner, _price = resolve_project(bids)
        if winner is not None:
            self.ledger.award(winner, est)

        message_rows, call_rows, transcripts = [], [], []
        for r in exchanged:
            sender = r["sender"]
            message_rows.append({**r, "run_id": self.run_id, "sender": env.names[sender],
                                 "recipient": env.names[r["recipient"]]})
            res = msg_results[sender]
            call_rows += _call_rows(self.run_id, pid, env.names[sender], "message", res.call_records)
            entry = _transcript(sender, "message", posting, res)
            if entry is not None:
                transcripts.append(entry)

        roles = self._truth_roles(pid, costs)
        bid_rows, truth_rows = [], []
        for i in range(cfg.N):
            firm = env.names[i]
            bid_rows.append({"run_id": self.run_id, "project_id": pid, "firm_id": firm, "est": est,
                             "tag": env.tag_label[t], "dist": float(env.dist[i, t]),
                             "cap": float(caps[i]), "firm_rate": float(env.eta[i]),
                             "bid": bids[i], "won": int(i == winner)})
            truth_rows.append({"run_id": self.run_id, "project_id": pid, "firm_id": firm, "seat": i,
                               "cost": costs[i], "eps": float(env.eps[i, t]), "nu": float(env.nu[t]),
                               "is_cartel": int(self.seats is not None and i in self.seats),
                               "cartel_role": roles[i], "scheme": scheme_of(cfg)})
            call_rows += _call_rows(self.run_id, pid, firm, "bid", results[i].call_records)
            entry = _transcript(i, "bid", posting, results[i])
            if entry is not None:
                transcripts.append(entry)

        self.history.append({
            "project_id": pid, "est": est, "tag": env.tag_label[t],
            "bids": {env.names[i]: bids[i] for i in range(cfg.N)},
            "winner": None if winner is None else env.names[winner],
            "post_posting": render_post_posting(env.names, bids, winner),
        })
        self.messages += message_rows
        self.bid_rows += bid_rows
        self.truth_rows += truth_rows
        self._log_project(t, bid_rows, truth_rows, message_rows, call_rows, transcripts)


def _default_root(cfg: RunConfig, purpose: str) -> str:
    return cfg.results_root if purpose == "experiment" else CALIBRATION_ROOT


_RUN_CSVS = (("bids.csv", io.BIDS_COLUMNS), ("truth.csv", io.TRUTH_COLUMNS),
             ("messages.csv", io.MESSAGES_COLUMNS), ("calls.csv", io.CALLS_COLUMNS))


def _copy_rows_upto(src: Path, dst: Path, columns, last: int) -> None:
    """Copy ``src``'s header and its complete rows with project_id <= ``last`` to ``dst``.

    Rows past the checkpoint (or torn by a crash) are dropped. Kept rows are re-emitted by the same
    csv dialect that wrote them, so their bytes are unchanged."""
    columns = list(columns)
    rows = []
    if src.exists():
        with open(src, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
    if rows and rows[0] != columns:
        raise ValueError(f"{src}: unexpected header {rows[0]}")
    k = columns.index("project_id")
    keep = []
    for r in rows[1:]:
        if len(r) != len(columns):
            continue
        try:
            pid = int(r[k])
        except ValueError:
            continue
        if pid <= last:
            keep.append(r)
    with open(dst, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=",", lineterminator="\n")
        w.writerow(columns)
        w.writerows(keep)


_TRANSCRIPT_HEADER = re.compile(r"^## (?:Prompt|Response): Project (\d+) \(")
_TRANSCRIPT_FENCES = ("``````", "````")  # prompt and response fences of io.append_transcript


def _transcript_upto(text: str, last: int) -> str:
    """The transcript entries of projects <= ``last``, byte for byte; header lines inside a fenced
    prompt or response are content, not headers."""
    out: list[str] = []
    pending: list[str] = []  # blank lines between entries belong to the entry that follows
    keep, fence = True, None
    for line in text.splitlines(keepends=True):
        bare = line.rstrip("\r\n")
        if fence is None:
            m = _TRANSCRIPT_HEADER.match(bare)
            if m:
                keep = int(m.group(1)) <= last
            elif bare in _TRANSCRIPT_FENCES:
                fence = bare
            elif bare == "":
                pending.append(line)
                continue
        elif bare == fence:
            fence = None
        if keep:
            out += pending
            out.append(line)
        pending = []
    if keep:
        out += pending
    return "".join(out)


def _copy_transcripts_upto(src: Path, dst: Path, last: int) -> None:
    if not src.is_dir():
        return
    dst.mkdir(parents=True, exist_ok=True)
    for path in sorted(src.iterdir()):
        if path.is_file():
            with open(path, encoding="utf-8", newline="") as f:
                kept = _transcript_upto(f.read(), last)
            if kept:
                with open(dst / path.name, "w", encoding="utf-8", newline="") as f:
                    f.write(kept)


def _check_resumable(cfg: RunConfig, old_meta: dict, name: str) -> None:
    """Raise ValueError unless ``old_meta`` describes the same run as ``cfg``."""
    seats = resolve_cartel_seats(cfg)
    expected = {"condition": cfg.condition, "seed": cfg.seed, "T": cfg.T,
                "cartel_seats": [] if seats is None else list(seats),
                "bidder": cfg.bidder, "model": cfg.model,
                "scripted": {"margin": cfg.scripted_margin, "rule": cfg.scripted_rule,
                             "serious_add": cfg.serious_add}}
    got = {k: old_meta.get(k) for k in expected}
    got_cost = {k: (old_meta.get("cost") or {}).get(k) for k in ("beta0", "beta1", "beta2", "sigma_eps")}
    expected["cost"] = {k: getattr(cfg.cost, k) for k in got_cost}
    got["cost"] = got_cost
    expected["N"], got["N"] = cfg.N, len(old_meta.get("seat_to_firm") or {})
    diff = [k for k in expected if got[k] != expected[k]]
    if diff:
        detail = "; ".join(f"{k}: run has {got[k]!r}, config has {expected[k]!r}" for k in diff)
        raise ValueError(f"cannot resume {name}: {detail}")


def _start_resume(cfg: RunConfig, env: Environment, old_dir: Path, old_meta: dict, last: int, root: Path,
                  limits: dict | None, frozen_from: str | None) -> tuple[Path, dict]:
    """A NEW run dir seeded from ``old_dir`` up to project ``last``. ``old_dir`` is only read."""
    new_dir = io.new_run_dir(root, cfg.condition, cfg.seed)
    for name, cols in _RUN_CSVS:
        _copy_rows_upto(old_dir / name, new_dir / name, cols, last)
    _copy_transcripts_upto(old_dir / "transcripts", new_dir / "transcripts", last)
    meta = make_run_meta(cfg, env, limits=old_meta.get("limits") if limits is None else limits,
                         frozen_from=old_meta.get("frozen_from") if frozen_from is None else frozen_from,
                         purpose=old_meta["purpose"])
    meta["started_utc"] = old_meta["started_utc"]
    meta["resumed_from"] = old_dir.name
    io.write_run_meta(new_dir, meta)
    return new_dir, meta


def run(cfg: RunConfig, bidder_factory: BidderFactory, results_root: str | Path | None = None,
        resume_dir: Path | None = None, purpose: str = "experiment", *,
        limits: dict | None = None, frozen_from: str | None = None,
        on_run_dir: Callable[[Path], None] | None = None) -> Path:
    """Run one market to a fresh run directory and return it. A ``STOP`` file raises SystemExit(3).

    ``resume_dir``: continue that run after its checkpoint in a NEW directory (under ``results_root``,
    default the old directory's parent) that starts with the old rows up to the checkpoint; the old
    directory is never modified. The resumed run keeps the old ``purpose`` and ``started_utc`` and
    records ``resumed_from``; ``purpose`` is ignored. Every check (config against the old run_meta,
    checkpoint, bidder state hook) runs before any directory is created. ``on_run_dir(run_dir)`` is
    called once the run directory holds its run_meta (and, on resume, its heartbeat and checkpoint),
    before the first project."""
    if purpose not in io.RUN_PURPOSES:
        raise ValueError(f"purpose must be one of {io.RUN_PURPOSES}, got {purpose!r}")
    cp = old_meta = None
    if resume_dir is not None:
        resume_dir = Path(resume_dir)
        old_meta = io.read_run_meta(resume_dir)
        _check_resumable(cfg, old_meta, resume_dir.name)
        cp = io.read_checkpoint(resume_dir)
    env = generate_environment(cfg.seed, cfg.cost, cfg.T, cfg.N)
    loop = _Loop(cfg, env, bidder_factory(cfg, env), None)  # TypeError on a half state hook
    if cp is not None:
        loop.restore(cp)  # ValueError on an inconsistent checkpoint or missing bidder_state
        root = Path(results_root) if results_root is not None else resume_dir.parent
        run_dir, meta = _start_resume(cfg, env, resume_dir, old_meta, loop.start, root, limits, frozen_from)
    else:
        root = results_root if results_root is not None else _default_root(cfg, purpose)
        run_dir = io.new_run_dir(root, cfg.condition, cfg.seed)
        meta = make_run_meta(cfg, env, limits=limits, frozen_from=frozen_from, purpose=purpose)
        io.write_run_meta(run_dir, meta)
    loop.run_dir = run_dir
    loop.install_activity_hook()
    if cp is not None:
        now = _utc_now()
        io.write_heartbeat(run_dir, loop.start, now, now)
        io.write_checkpoint(run_dir, loop.checkpoint_state(loop.start))  # the new dir is resumable at once
    if on_run_dir is not None:
        on_run_dir(run_dir)
    loop.run()
    meta["finished_utc"] = _utc_now().isoformat()
    io.write_run_meta(run_dir, meta)
    return run_dir


def run_in_memory(cfg: RunConfig, bidder_factory: BidderFactory
                  ) -> tuple[pd.DataFrame, pd.DataFrame, list[dict]]:
    """Same loop as ``run`` with no files: (bids frame, truth frame, messages.csv rows)."""
    env = generate_environment(cfg.seed, cfg.cost, cfg.T, cfg.N)
    loop = _Loop(cfg, env, bidder_factory(cfg, env), None)
    loop.run()
    bids = pd.DataFrame(loop.bid_rows, columns=io.BIDS_COLUMNS)
    truth = pd.DataFrame(loop.truth_rows, columns=io.TRUTH_COLUMNS)
    return bids, truth, loop.messages


# ---------------------------------------------------------------- CLI

FATAL_EXIT_CODE = 4
CRASH_EXIT_CODE = 1
_KEY_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY_2")


def _scrub(text: str) -> str:
    """``text`` with any API key value from the environment replaced by ***."""
    for var in _KEY_VARS:
        secret = os.environ.get(var)
        if secret:
            text = text.replace(secret, "***")
    return text


def load_cli_config(path) -> tuple[RunConfig, str | None, dict | None]:
    """A launcher config file: every ``RunConfig`` field (``RunConfig.to_dict``) plus two optional
    run-level keys passed to ``run``: ``frozen_from`` and ``limits``."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    frozen_from = data.pop("frozen_from", None)
    limits = data.pop("limits", None)
    return RunConfig.from_dict(data), frozen_from, limits


def _announce(path: Path, run_dir: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    io._atomic_write_text(path, str(Path(run_dir).resolve()))


def main(argv=None) -> int:
    """``python -m src.procurement.runner --config CFG.json [--resume-dir DIR] --announce FILE
    [--results-root ROOT] [--purpose experiment]``. Exit codes: 0 finished, 3 stopped by a STOP file,
    4 ``FatalAPIError`` (one line: class and message, never the key), 1 any other exception."""
    ap = argparse.ArgumentParser(prog="python -m src.procurement.runner",
                                 description="Run one market (one process per run; see the launcher).")
    ap.add_argument("--config", required=True, help="RunConfig JSON (RunConfig.to_dict + frozen_from, limits)")
    ap.add_argument("--resume-dir", default=None, help="continue this run after its checkpoint, in a new dir")
    ap.add_argument("--announce", required=True, help="file that receives the run directory's absolute path")
    ap.add_argument("--results-root", default=None, help="parent of the run dir (default: config results_root)")
    ap.add_argument("--purpose", default="experiment", choices=io.RUN_PURPOSES)
    args = ap.parse_args(argv)
    try:
        cfg, frozen_from, limits = load_cli_config(args.config)
        run(cfg, bidder_factory_for(cfg), results_root=args.results_root,
            resume_dir=None if args.resume_dir is None else Path(args.resume_dir), purpose=args.purpose,
            limits=limits, frozen_from=frozen_from,
            on_run_dir=lambda run_dir: _announce(Path(args.announce), run_dir))
    except SystemExit as exc:
        if exc.code == STOP_EXIT_CODE:
            return STOP_EXIT_CODE
        raise
    except FatalAPIError as exc:
        print(_scrub(f"{type(exc).__name__}: {exc}"), file=sys.stderr, flush=True)
        return FATAL_EXIT_CODE
    except Exception:
        print(_scrub(traceback.format_exc()), file=sys.stderr, flush=True)
        return CRASH_EXIT_CODE
    return 0


if __name__ == "__main__":
    sys.exit(main())
