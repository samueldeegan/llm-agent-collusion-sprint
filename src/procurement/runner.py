"""The project loop: postings, (message phase), bids, award, logging, heartbeat/checkpoint, STOP."""
from __future__ import annotations

import math
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pandas as pd

from src.procurement import io
from src.procurement.auction import AwardLedger, render_post_posting, render_pre_posting, resolve_project
from src.procurement.channel import Channel
from src.procurement.config import RunConfig
from src.procurement.environment import Environment, cost_of, generate_environment
from src.procurement.types import BidContext, Bidder, BidResult

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


def _require_control(cfg: RunConfig) -> None:
    if cfg.condition != "control":
        raise NotImplementedError("scheme runs: Task 7")


def make_run_meta(cfg: RunConfig, env: Environment, limits: dict | None = None,
                  frozen_from: str | None = None, purpose: str = "experiment") -> dict:
    """``run_meta.json`` content per spec §4g, with ``finished_utc`` still null."""
    seats = None if cfg.condition == "control" else cfg.cartel_seats
    return {
        "purpose": purpose,
        "condition": cfg.condition,
        "scheme": "none" if cfg.condition == "control" else cfg.condition,
        "seed": cfg.seed,
        "T": cfg.T,
        "burn_in": cfg.burn_in,
        "cartel_seats": [] if seats is None else [int(s) for s in seats],
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
        self.cfg, self.env, self.bidder, self.run_dir = cfg, env, bidder, run_dir
        self.run_id = f"{cfg.condition}_s{cfg.seed}"
        self.ledger = AwardLedger(env.K, cfg.N)
        self.channel = Channel(None, cfg.condition, cfg.max_message_length)  # control: closed
        self.history: list[dict] = []
        self.messages: list[dict] = []
        self.bid_rows: list[dict] = []
        self.truth_rows: list[dict] = []

    # ------------------------------------------------------------ files

    def _write_headers(self) -> None:
        for name, cols in (("bids.csv", io.BIDS_COLUMNS), ("truth.csv", io.TRUTH_COLUMNS),
                           ("messages.csv", io.MESSAGES_COLUMNS), ("calls.csv", io.CALLS_COLUMNS)):
            io.write_csv(self.run_dir / name, [], cols)

    def _touch(self) -> None:
        if self.run_dir is not None:
            io.touch_activity(self.run_dir)

    def _log_project(self, t: int, bid_rows, truth_rows, message_rows, call_rows,
                     results: dict[int, BidResult], posting: str) -> None:
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
        for seat in range(self.cfg.N):
            if results[seat].raw is not None:
                io.append_transcript(d, self.env.names[seat], t + 1, "bid", posting, results[seat].raw)
        now = _utc_now()
        io.write_heartbeat(d, t + 1, now, now)
        io.write_checkpoint(d, {"last_completed_project": t + 1, "won": self.ledger.won.copy(),
                                "history": self.history, "messages": self.messages,
                                "run_dir": str(d)})

    def _check_stop(self) -> None:
        if self.run_dir is not None and (self.run_dir / "STOP").exists():
            # heartbeat and checkpoint for this project were written by _log_project
            raise SystemExit(STOP_EXIT_CODE)

    # ------------------------------------------------------------ phases

    def _message_phase(self, t: int) -> list[dict]:
        """Cartel exchange for project t (Task 7). A closed channel (control) has no message phase."""
        if self.channel.seats is None:
            return []
        raise NotImplementedError("scheme runs: Task 7")

    def _bid_phase(self, pool: ThreadPoolExecutor, t: int, caps, costs: list[float], posting: str,
                   messages: list[dict], roles: list[str | None]) -> dict[int, BidResult]:
        window = self.history[-self.cfg.history_window:] if self.cfg.history_window > 0 else []
        futures = {}
        for seat in range(self.cfg.N):
            ctx = BidContext(run=self.cfg, env=self.env, t=t, seat=seat, cap=caps.copy(),
                             cost=costs[seat], history=list(window), messages=list(messages),
                             memory=[], scratchpad="", role=roles[seat], seats=self.channel.seats,
                             posting=posting)
            futures[seat] = pool.submit(self.bidder.bid, ctx)
        return {seat: futures[seat].result() for seat in range(self.cfg.N)}  # by seat; errors propagate

    # ------------------------------------------------------------ loop

    def run(self) -> None:
        cfg = self.cfg
        if self.run_dir is not None:
            self._write_headers()
        with ThreadPoolExecutor(max_workers=cfg.concurrency) as pool:
            for t in range(cfg.T):
                self._project(pool, t)
                self._check_stop()

    def _project(self, pool: ThreadPoolExecutor, t: int) -> None:
        cfg, env = self.cfg, self.env
        pid, est = t + 1, float(env.est[t])
        caps = self.ledger.caps().copy()  # CAP before this project's award
        costs = [cost_of(env, cfg.cost, i, t, float(caps[i])) for i in range(cfg.N)]
        posting = render_pre_posting(env, t, caps, env.names)

        message_rows = self._message_phase(t)
        roles: list[str | None] = [None] * cfg.N

        self._touch()
        results = self._bid_phase(pool, t, caps, costs, posting, message_rows, roles)
        self._touch()

        bids = {seat: _clean_bid(results[seat]) for seat in range(cfg.N)}
        winner, _price = resolve_project(bids)
        if winner is not None:
            self.ledger.award(winner, est)

        bid_rows, truth_rows, call_rows = [], [], []
        for i in range(cfg.N):
            firm = env.names[i]
            role = roles[i] or "none"
            bid_rows.append({"run_id": self.run_id, "project_id": pid, "firm_id": firm, "est": est,
                             "tag": env.tag_label[t], "dist": float(env.dist[i, t]),
                             "cap": float(caps[i]), "firm_rate": float(env.eta[i]),
                             "bid": bids[i], "won": int(i == winner)})
            truth_rows.append({"run_id": self.run_id, "project_id": pid, "firm_id": firm, "seat": i,
                               "cost": costs[i], "eps": float(env.eps[i, t]), "nu": float(env.nu[t]),
                               "is_cartel": int(role != "none"), "cartel_role": role,
                               "scheme": "none" if cfg.condition == "control" else cfg.condition})
            call_rows += _call_rows(self.run_id, pid, firm, "bid", results[i].call_records)

        self.history.append({
            "project_id": pid, "est": est, "tag": env.tag_label[t],
            "bids": {env.names[i]: bids[i] for i in range(cfg.N)},
            "winner": None if winner is None else env.names[winner],
            "post_posting": render_post_posting(env.names, bids, winner),
        })
        self.messages += message_rows
        self.bid_rows += bid_rows
        self.truth_rows += truth_rows
        self._log_project(t, bid_rows, truth_rows, message_rows, call_rows, results, posting)


def _default_root(cfg: RunConfig, purpose: str) -> str:
    return cfg.results_root if purpose == "experiment" else CALIBRATION_ROOT


def run(cfg: RunConfig, bidder_factory: BidderFactory, results_root: str | Path | None = None,
        resume_dir: Path | None = None, purpose: str = "experiment", *,
        limits: dict | None = None, frozen_from: str | None = None) -> Path:
    """Run one market to a fresh run directory and return it. A ``STOP`` file raises SystemExit(3)."""
    _require_control(cfg)
    if resume_dir is not None:
        raise NotImplementedError("resume: Task 7")
    if purpose not in io.RUN_PURPOSES:
        raise ValueError(f"purpose must be one of {io.RUN_PURPOSES}, got {purpose!r}")
    env = generate_environment(cfg.seed, cfg.cost, cfg.T, cfg.N)
    bidder = bidder_factory(cfg, env)
    root = results_root if results_root is not None else _default_root(cfg, purpose)
    run_dir = io.new_run_dir(root, cfg.condition, cfg.seed)
    meta = make_run_meta(cfg, env, limits=limits, frozen_from=frozen_from, purpose=purpose)
    io.write_run_meta(run_dir, meta)
    _Loop(cfg, env, bidder, run_dir).run()
    meta["finished_utc"] = _utc_now().isoformat()
    io.write_run_meta(run_dir, meta)
    return run_dir


def run_in_memory(cfg: RunConfig, bidder_factory: BidderFactory
                  ) -> tuple[pd.DataFrame, pd.DataFrame, list[dict]]:
    """Same loop as ``run`` with no files: (bids frame, truth frame, message rows)."""
    _require_control(cfg)
    env = generate_environment(cfg.seed, cfg.cost, cfg.T, cfg.N)
    loop = _Loop(cfg, env, bidder_factory(cfg, env), None)
    loop.run()
    bids = pd.DataFrame(loop.bid_rows, columns=io.BIDS_COLUMNS)
    truth = pd.DataFrame(loop.truth_rows, columns=io.TRUTH_COLUMNS)
    return bids, truth, loop.messages
