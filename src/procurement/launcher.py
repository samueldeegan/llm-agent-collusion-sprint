"""Launcher: one OS process per run, a monitor loop (exits, stalls, spend), file-based stop, time ladder.

``python -m src.procurement.launcher --plan config/run_plan.json [--dry-run] [--allow-unfrozen]
[--poll-s 60] [--results-root results/runs]`` (spec §4f, §4g stop semantics and concurrency cap;
A46, A49-A54, A95; step-4 audit Part 2 and Part 4.2 steps 7-8).

Every launch works in a new ``<results>/launches/<UTC yyyymmdd_HHMMSS>/`` (never overwritten):
``manifest.json``, ``configs/<launch_index>_<condition>_s<seed>.json`` (the runner's ``--config``),
``announce/<name>[.r<k>].txt`` (each attempt's run dir, written by the runner), ``logs/<name>[.r<k>].out``
and ``.err`` (each process's stdout and stderr), ``launcher.log`` and ``status.json`` (rewritten
atomically every poll). Each run is ``python -m src.procurement.runner --config ... --announce ...
--results-root ...`` (plus ``--resume-dir`` on a restart); the child inherits the environment, so the
API key never appears on a command line, in a file or in a log.

Exit codes: 0 every run done; 2 spend stop (audit 4.3 condition 2); 5 pre-flight refusal (no
``config/frozen.json``, a key variable not set) or a run's ``FatalAPIError`` (all runs stopped);
6 every run terminal but not all done; 7 time ladder stop (nothing launched by deadline - 2.5 h).

Inputs read here and written elsewhere (only these keys are read):

``results/calibration.json`` (tier check and smoke runs)::

    {"limits": {"1": {"requests": 1000, "input_tokens": 2000000, "output_tokens": 400000},
                "2": {"requests": 1000, "input_tokens": 2000000, "output_tokens": 400000}},
     "measured": {"mean_latency_s": 10.2}}

``limits`` is per API key slot ("2" only when a second key was checked); a flat
``"limits": {"requests": ..., "output_tokens": ...}`` is read as slot 1. Without the file (or without
slot-1 limits) the plan's R is used, the process cap is the plan size, and a warning is printed.

``config/frozen.json`` (pilot): ``{"cost": {"beta0": ..., "beta1": ..., "beta2": ..., "sigma_eps": ...},
...}``; the four keys may also sit at the top level. They replace those of ``config/defaults.json`` in
every run config.
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import math
import os
import subprocess
import sys
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from src.procurement import config, io
from src.procurement.config import CONDITIONS, CostParams, Pricing, RunConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
LAUNCH_ORDER = ("cover", "markup", "rotation", "control")  # scheme runs first (A49)
STOP_GRACE_S = 120.0
SPEND_FLOOR_CALLS = 200  # below this many calls a projection alone never stops (start-up noise)
A_PRIORI_INPUT, A_PRIORI_OUTPUT = 3500, 1500  # tokens per call (audit Part 2.2, expected case)
KEY_VARS = {1: "ANTHROPIC_API_KEY", 2: "ANTHROPIC_API_KEY_2"}
FROZEN_KEYS = ("beta0", "beta1", "beta2", "sigma_eps")

EXIT_DONE, EXIT_SPEND, EXIT_PREFLIGHT, EXIT_FATAL, EXIT_INCOMPLETE, EXIT_LADDER = 0, 2, 5, 5, 6, 7
RUNNER_STOP, RUNNER_FATAL = 3, 4
TERMINAL = ("done", "stopped", "failed")
ALIVE = ("running", "restarted")

LADDER = {  # rung: (hours before the deadline, what it does)
    1: (5.0, "drop rotation"),
    2: (4.0, "T = 80 and markup R = 2"),
    3: (3.0, "control and cover R = 6"),
    4: (2.5, "stop: nothing launched (audit 4.3 condition 4)"),
}

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(t: datetime | None) -> str | None:
    return None if t is None else t.astimezone(timezone.utc).isoformat()


def _write_json(path: Path, data) -> None:
    io._atomic_write_text(Path(path), json.dumps(data, indent=2))


# ---------------------------------------------------------------- plan, sizing, caps

def build_manifest(plan: list[RunConfig], keys: int, stagger_s: float = 5) -> list[dict]:
    """Launch order: cover, markup, rotation, then control, seeds ascending. Start ``i`` is staggered
    ``stagger_s * i``; with two keys odd launch indices (even-numbered launches, 1-based) use slot 2."""
    rank = {c: i for i, c in enumerate(LAUNCH_ORDER)}
    out = []
    for i, cfg in enumerate(sorted(plan, key=lambda c: (rank[c.condition], c.seed))):
        slot = 2 if keys == 2 and i % 2 == 1 else 1
        cfg = replace(cfg, api_key_slot=slot)
        out.append({"launch_index": i, "name": f"{i:02d}_{cfg.condition}_s{cfg.seed}",
                    "condition": cfg.condition, "seed": cfg.seed, "T": cfg.T, "stagger_s": stagger_s * i,
                    "api_key_slot": slot, "config": cfg.to_dict()})
    return out


def planned_calls(plan: list[RunConfig]) -> int:
    """Model calls of a plan: T x 6 per control run, T x 8 per scheme run (two messages per project)."""
    return sum(c.T * (c.N if c.condition == "control" else c.N + 2) for c in plan)


def apply_sizing(limits: dict, has_key2: bool) -> dict:
    """Spec A46 sizing rule from the slot-1 limits: ``{"R": {...}, "keys": 1 | 2}``. Key 2 is used only
    when the output-token limit is below 1,000,000 and ``ANTHROPIC_API_KEY_2`` is set."""
    out = int(limits["output_tokens"])
    return {"R": config.sizing_rule(out, has_key2), "keys": 2 if has_key2 and out < 1_000_000 else 1}


def max_procs(requests_limit: int, call_s: float = 10.0, concurrency: int = 6, *, plan_size: int) -> int:
    """Process cap from a requests-per-minute limit (spec §4g): 22 at 1,000 rpm, 32 (all) at 4,000."""
    return min(plan_size, max(1, math.floor(0.8 * requests_limit * call_s / (60 * concurrency))))


def slot_caps(limits_by_slot: dict, manifest: list[dict], call_s: float = 10.0, concurrency: int = 6
              ) -> dict[int, int]:
    """Per key slot, the cap ``max_procs`` gives for that slot's requests limit over that slot's runs
    (each key has its own limit; the total cap is the sum). A slot without limits falls back to slot 1's;
    with no limits at all a slot's cap is its run count."""
    counts: dict[int, int] = {}
    for e in manifest:
        counts[e["api_key_slot"]] = counts.get(e["api_key_slot"], 0) + 1
    caps = {}
    for slot, n in sorted(counts.items()):
        lim = limits_by_slot.get(slot) or limits_by_slot.get(1)
        caps[slot] = n if not lim or lim.get("requests") is None else max_procs(
            int(lim["requests"]), call_s, concurrency, plan_size=n)
    return caps


def read_calibration(path) -> dict | None:
    """``{"limits": {slot: {...}}, "mean_latency_s": float | None}`` from ``results/calibration.json``
    (schema in the module docstring); None when the file or its slot-1 limits are missing."""
    path = Path(path)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    raw = data.get("limits") or {}
    if "output_tokens" in raw or "requests" in raw:
        raw = {"1": raw}
    limits = {int(k): v for k, v in raw.items() if isinstance(v, dict)}
    if not limits.get(1) or limits[1].get("output_tokens") is None:
        return None
    lat = (data.get("measured") or {}).get("mean_latency_s")
    return {"limits": limits, "mean_latency_s": float(lat) if lat else None}


def read_frozen(path, base: CostParams) -> CostParams:
    """``base`` with beta0, beta1, beta2 and sigma_eps from ``config/frozen.json``."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    src = data.get("cost", data) if isinstance(data, dict) else {}
    missing = [k for k in FROZEN_KEYS if not isinstance(src.get(k), (int, float))]
    if missing:
        raise ValueError(f"{path}: missing cost parameters {missing}")
    return replace(base, **{k: float(src[k]) for k in FROZEN_KEYS})


def time_ladder(now_utc: datetime, deadline_utc: datetime, launched: bool, plan: dict
                ) -> tuple[dict, int, bool]:
    """Audit Part 4.2 time ladder on a ``run_plan.json`` dict: ``(plan, rung, stop)``. Rungs are
    cumulative and only ever lower R and T. An already launched plan is returned unchanged. Pure."""
    p = copy.deepcopy(plan)
    if launched:
        return p, 0, False
    hours_left = (deadline_utc - now_utc).total_seconds() / 3600
    rung = max([k for k, (h, _) in LADDER.items() if hours_left <= h], default=0)
    R = p["R"]
    if rung >= 1:
        p["include_rotation"] = False
    if rung >= 2:
        p["T"] = min(int(p["T"]), 80)
        R["markup"] = min(R.get("markup", 0), 2)
    if rung >= 3:
        for c in ("control", "cover"):
            R[c] = min(R.get(c, 0), 6)
    return p, rung, rung >= 4


# ---------------------------------------------------------------- spend guard and stop

def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _copied_upto(run_dir: Path) -> int:
    """For a resumed run dir, the last project whose calls.csv rows were copied from the old dir (they
    are counted there); 0 otherwise."""
    try:
        old = io.read_run_meta(run_dir).get("resumed_from")
        if not old:
            return 0
        return int(io.read_checkpoint(run_dir.parent / old)["last_completed_project"])
    except (OSError, ValueError, KeyError, TypeError):
        return 0


def spend_totals(run_dirs, pricing: Pricing) -> tuple[float, int]:
    """(USD, calls) over every ``calls.csv``: all four token columns priced, a call counted by its
    attempt-1 row. Torn or unparsable rows (a file being appended) are skipped."""
    usd, calls = 0.0, 0
    for d in run_dirs:
        d = Path(d)
        path = d / "calls.csv"
        if not path.is_file():
            continue
        skip = _copied_upto(d)
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if not header:
                continue
            col = {c: i for i, c in enumerate(header)}
            for row in reader:
                if len(row) != len(header):
                    continue
                pid = _int(row[col["project_id"]])
                if pid is None or pid <= skip:
                    continue
                tok = [_int(row[col[c]]) or 0 for c in ("input_tokens", "output_tokens",
                                                       "cache_creation_input_tokens", "cache_read_input_tokens")]
                usd += pricing.cost_usd(*tok)
                calls += _int(row[col["attempt"]]) == 1
    return usd, calls


def spend_guard(run_dirs, pricing: Pricing, planned_calls: int, stop_usd: float = 75.0
                ) -> tuple[float, float, bool]:
    """(actual, projected, stop?): projected = actual x planned / completed calls; stop when actual >
    ``stop_usd``, or when projected > ``stop_usd`` once at least 200 calls are in (spec A50)."""
    actual, calls = spend_totals(run_dirs, pricing)
    projected = actual * planned_calls / max(calls, 1)
    stop = actual > stop_usd or (calls >= SPEND_FLOOR_CALLS and projected > stop_usd)
    return actual, projected, bool(stop)


def _finished(run_dir: Path) -> bool:
    try:
        return io.read_run_meta(run_dir).get("finished_utc") is not None
    except (OSError, ValueError):
        return False


def _kill(proc) -> None:
    try:
        proc.kill()
        proc.wait(timeout=30)
    except Exception:  # already gone, or wait timed out: nothing more to do
        pass


def stop_all(procs, run_dirs, results_root, reason: str, actual: float, projected: float,
             clock: Clock = _utc_now, sleep: Callable[[float], None] = time.sleep,
             grace_s: float = STOP_GRACE_S, poll_every_s: float = 2.0) -> int:
    """Spec §4g stop: an empty ``STOP`` file in every running run's dir (``procs[i]`` runs in
    ``run_dirs[i]``; a None proc is not running), up to ``grace_s`` of ``clock`` for them to exit,
    ``kill()`` for stragglers, then ``<results_root>/../STOP.json`` (the ``results/STOP.json`` the
    analysis reads) with run-dir basenames. Returns 2."""
    pairs = list(zip(procs, run_dirs))
    running = [(p, d) for p, d in pairs if p is not None and p.poll() is None]
    for _, d in running:
        if d is not None:
            try:
                (Path(d) / "STOP").write_bytes(b"")
            except OSError:
                pass
    deadline = clock() + timedelta(seconds=grace_s)
    while any(p.poll() is None for p, _ in running):
        left = (deadline - clock()).total_seconds()
        if left <= 0:
            break
        sleep(min(poll_every_s, left))
    for p, _ in running:
        if p.poll() is None:
            _kill(p)
    names, complete, incomplete = set(), [], []
    for d in run_dirs:
        if d is None or Path(d).name in names:
            continue
        names.add(Path(d).name)
        (complete if _finished(Path(d)) else incomplete).append(Path(d).name)
    _write_json(Path(results_root).parent / "STOP.json", {
        "reason": reason, "actual_usd": actual, "projected_usd": projected, "utc": _iso(clock()),
        "runs_complete": complete, "runs_incomplete": incomplete})
    return EXIT_SPEND


# ---------------------------------------------------------------- workspace

def new_launch_dir(launches_root, now_utc: datetime) -> Path:
    base = f"{now_utc.astimezone(timezone.utc):%Y%m%d_%H%M%S}"
    root = Path(launches_root)
    root.mkdir(parents=True, exist_ok=True)
    k = 0
    while True:
        path = root / (base if k == 0 else f"{base}_{k}")
        try:
            path.mkdir(exist_ok=False)
            return path
        except FileExistsError:
            k += 1


def write_workspace(workspace, manifest: list[dict], frozen_from: str | None = None,
                    limits_by_slot: dict | None = None, header: dict | None = None) -> list[dict]:
    """Write each run's config file and ``manifest.json``; return the manifest with paths (``config``
    becomes the config file's path; ``announce``, ``stdout``, ``stderr`` are the first attempt's)."""
    ws = Path(workspace)
    for sub in ("configs", "announce", "logs"):
        (ws / sub).mkdir(parents=True, exist_ok=True)
    out = []
    for e in manifest:
        cfg_path = ws / "configs" / f"{e['name']}.json"
        data = {**e["config"], "frozen_from": frozen_from,
                "limits": (limits_by_slot or {}).get(e["api_key_slot"])}
        cfg_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8", newline="\n")
        out.append({**{k: v for k, v in e.items() if k != "config"}, "config": str(cfg_path),
                    "announce": str(ws / "announce" / f"{e['name']}.txt"),
                    "stdout": str(ws / "logs" / f"{e['name']}.out"),
                    "stderr": str(ws / "logs" / f"{e['name']}.err")})
    _write_json(ws / "manifest.json", {**(header or {}), "runs": out})
    return out


def spawn_command(python_exe: str, config_path, announce, results_root, resume_dir=None) -> list[str]:
    cmd = [python_exe, "-m", "src.procurement.runner", "--config", str(config_path),
           "--announce", str(announce), "--results-root", str(results_root)]
    if resume_dir is not None:
        cmd += ["--resume-dir", str(resume_dir)]
    return cmd


# ---------------------------------------------------------------- launcher

class _Run:
    def __init__(self, entry: dict):
        self.entry, self.name, self.slot = entry, entry["name"], entry["api_key_slot"]
        self.state = "queued"
        self.proc = None
        self.attempts = 0
        self.restarts = 0
        self.current_dir: Path | None = None  # this attempt's announced run dir
        self.run_dirs: list[Path] = []  # every attempt's, in order
        self.exit_code: int | None = None
        self.started_at: datetime | None = None
        self.announce: Path | None = None
        self.handles: list = []

    @property
    def run_dir(self) -> Path | None:
        return self.run_dirs[-1] if self.run_dirs else None

    @property
    def alive(self) -> bool:
        return self.state in ALIVE


class Launcher:
    """Starts the manifest's runs (at most ``caps[slot]`` alive per key slot, ``stagger_s`` between
    starts, queued runs in manifest order) and monitors them every ``poll_s``: exits, stalls (A52)
    and the spend guard (A50). ``clock``, ``sleep`` and ``popen`` are injectable for tests."""

    def __init__(self, manifest: list[dict], workspace, results_root, *, caps: dict[int, int],
                 pricing: Pricing, planned_calls: int, clock: Clock = _utc_now,
                 sleep: Callable[[float], None] = time.sleep, popen=subprocess.Popen,
                 python_exe: str = sys.executable, poll_s: float = 60, stall_s: float = 600,
                 stop_usd: float = 75.0, max_restarts: int = 1, stagger_s: float = 5,
                 info: dict | None = None, repo_root=REPO_ROOT):
        self.runs = [_Run(e) for e in manifest]
        self.workspace, self.results_root = Path(workspace), Path(results_root).resolve()
        self.caps, self.pricing, self.planned_calls = dict(caps), pricing, planned_calls
        self.clock, self.sleep, self.popen, self.python_exe = clock, sleep, popen, python_exe
        self.poll_s, self.stall_s, self.stop_usd = poll_s, stall_s, stop_usd
        self.max_restarts, self.stagger_s = max_restarts, stagger_s
        self.info, self.repo_root = dict(info or {}), Path(repo_root)
        self.next_start_at: datetime | None = None
        self.actual = self.projected = 0.0
        self.last_poll: datetime | None = None
        self.exit_code: int | None = None
        self.stop_reason: str | None = None

    # ------------------------------------------------------------ logging and status

    def log(self, msg: str) -> None:
        line = f"{_iso(self.clock())} {msg}"
        with open(self.workspace / "launcher.log", "a", encoding="utf-8", newline="\n") as f:
            f.write(line + "\n")
        print(line, flush=True)

    def write_status(self) -> None:
        poll = _iso(self.last_poll)
        runs = [{"name": r.name, "launch_index": r.entry["launch_index"], "condition": r.entry["condition"],
                 "seed": r.entry["seed"], "api_key_slot": r.slot, "state": r.state,
                 "run_dir": None if r.run_dir is None else str(r.run_dir),
                 "run_dirs": [str(d) for d in r.run_dirs], "attempts": r.attempts, "restarts": r.restarts,
                 "exit_code": r.exit_code, "last_poll_utc": poll} for r in self.runs]
        _write_json(self.workspace / "status.json", {
            **self.info, "launch_dir": str(self.workspace), "utc": _iso(self.clock()), "last_poll_utc": poll,
            "caps": {str(k): v for k, v in self.caps.items()}, "planned_calls": self.planned_calls,
            "actual_usd": self.actual, "projected_usd": self.projected, "exit_code": self.exit_code,
            "stop_reason": self.stop_reason, "runs": runs})

    # ------------------------------------------------------------ starting

    def _slot_free(self, slot: int) -> bool:
        alive = sum(1 for r in self.runs if r.alive and r.slot == slot)
        return alive < self.caps.get(slot, len(self.runs))

    def _can_start(self) -> bool:
        return any(r.state == "queued" and self._slot_free(r.slot) for r in self.runs)

    def start_due(self) -> bool:
        """Start the first queued run (manifest order) whose key slot has room, if the stagger allows."""
        now = self.clock()
        if self.next_start_at is not None and now < self.next_start_at:
            return False
        for run in self.runs:
            if run.state == "queued" and self._slot_free(run.slot):
                self._start(run, resume_dir=None)
                self.next_start_at = now + timedelta(seconds=self.stagger_s)
                return True
        return False

    def _start(self, run: _Run, resume_dir: Path | None) -> None:
        suffix = "" if run.attempts == 0 else f".r{run.attempts}"
        run.announce = self.workspace / "announce" / f"{run.name}{suffix}.txt"
        logs = self.workspace / "logs"
        cmd = spawn_command(self.python_exe, run.entry["config"], run.announce, self.results_root, resume_dir)
        out = open(logs / f"{run.name}{suffix}.out", "w", encoding="utf-8")
        err = open(logs / f"{run.name}{suffix}.err", "w", encoding="utf-8")
        kwargs = {"cwd": str(self.repo_root), "stdout": out, "stderr": err}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        run.attempts += 1
        run.current_dir, run.started_at, run.exit_code = None, self.clock(), None
        try:
            run.proc = self.popen(cmd, **kwargs)
        except OSError as exc:
            out.close(), err.close()
            run.proc, run.state = None, "failed"
            self.log(f"{run.name}: spawn failed ({type(exc).__name__}: {exc}); marked failed")
            return
        run.handles = [out, err]
        run.state = "running" if run.restarts == 0 else "restarted"
        how = "fresh" if resume_dir is None else f"resume from {Path(resume_dir).name}"
        self.log(f"{run.name}: start attempt {run.attempts} (slot {run.slot}, {how}, "
                 f"pid {getattr(run.proc, 'pid', '?')})")

    # ------------------------------------------------------------ monitoring

    def _refresh_dir(self, run: _Run) -> None:
        if run.announce is None or not run.announce.is_file():
            return
        try:
            text = run.announce.read_text(encoding="utf-8").strip()
        except OSError:
            return
        if text:
            d = Path(text)
            if d not in run.run_dirs:
                run.run_dirs.append(d)
            run.current_dir = d

    def _last_activity(self, run: _Run) -> datetime:
        last = run.started_at
        if run.current_dir is not None:
            try:
                hb = json.loads((run.current_dir / "heartbeat.json").read_text(encoding="utf-8"))
                t = datetime.fromisoformat(hb["last_activity_utc"])
                last = max(last, t if t.tzinfo else t.replace(tzinfo=timezone.utc))
            except (OSError, ValueError, KeyError, TypeError):
                pass
        return last

    def _close(self, run: _Run) -> None:
        for h in run.handles:
            try:
                h.close()
            except OSError:
                pass
        run.handles = []

    def _resume_dir(self, run: _Run) -> Path | None:
        d = run.current_dir
        return d if d is not None and (d / "checkpoint.json").is_file() else None

    def _restart_or(self, run: _Run, why: str, final_state: str) -> None:
        if run.restarts < self.max_restarts:
            resume = self._resume_dir(run)
            run.restarts += 1
            self.log(f"{run.name}: {why}; restart {run.restarts} "
                     f"({'from checkpoint ' + resume.name if resume else 'fresh, no checkpoint'})")
            self._start(run, resume)
        else:
            run.state = final_state
            self.log(f"{run.name}: {why} again after {run.restarts} restart(s); marked {final_state}")

    def _on_exit(self, run: _Run, rc: int) -> bool:
        """Handle a finished process; True on a fatal API error."""
        run.exit_code = rc
        self._close(run)
        if rc == 0:
            run.state = "done"
            self.log(f"{run.name}: done")
        elif rc == RUNNER_STOP:
            run.state = "stopped"
            self.log(f"{run.name}: stopped by STOP file (exit 3)")
        elif rc == RUNNER_FATAL:
            run.state = "failed"
            self.log(f"{run.name}: FatalAPIError (exit 4); stopping all runs")
            return True
        else:
            self._restart_or(run, f"crashed (exit {rc})", "failed")
        return False

    def _on_stall(self, run: _Run, idle_s: float) -> None:
        why = f"stalled (no activity for {idle_s:.0f} s)"
        if run.restarts >= self.max_restarts and run.current_dir is not None:
            try:  # the analysis reports a run with a STOP file as stopped
                (run.current_dir / "STOP").write_bytes(b"")
            except OSError:
                pass
        _kill(run.proc)
        run.exit_code = run.proc.poll()
        self._close(run)
        self._restart_or(run, why, "stopped")

    def _all_dirs(self) -> list[Path]:
        return [d for r in self.runs for d in r.run_dirs]

    def _stop(self, reason: str, code: int) -> int:
        procs, dirs = [], []
        for r in self.runs:
            for d in r.run_dirs:
                if not (r.alive and d == r.current_dir):
                    procs.append(None), dirs.append(d)
            if r.alive:
                procs.append(r.proc), dirs.append(r.current_dir)
        self.log(f"stop all runs: {reason} (actual ${self.actual:.2f}, projected ${self.projected:.2f})")
        stop_all(procs, dirs, self.results_root, reason, self.actual, self.projected, self.clock, self.sleep)
        for r in self.runs:
            if r.alive:
                r.exit_code = r.proc.poll()
                r.state = "stopped"
                self._close(r)
        self.stop_reason, self.exit_code = reason, code
        self.log(f"STOP.json written; launcher exit {code}")
        self.write_status()
        return code

    def poll_once(self) -> int | None:
        """One monitor pass: exits, stalls, spend guard, status. Returns the launcher exit code once
        the launch is over, else None."""
        now = self.last_poll = self.clock()
        fatal = False
        for run in self.runs:
            if run.alive:
                self._refresh_dir(run)
                rc = run.proc.poll()
                if rc is not None:
                    fatal |= self._on_exit(run, rc)
        if fatal:
            self.actual, self.projected, _ = spend_guard(self._all_dirs(), self.pricing, self.planned_calls,
                                                         self.stop_usd)
            return self._stop("fatal_api_error", EXIT_FATAL)
        for run in self.runs:
            if run.alive:
                idle = (now - self._last_activity(run)).total_seconds()
                if idle > self.stall_s:
                    self._on_stall(run, idle)
        self.actual, self.projected, stop = spend_guard(self._all_dirs(), self.pricing, self.planned_calls,
                                                        self.stop_usd)
        if stop:
            return self._stop("spend", EXIT_SPEND)
        if all(r.state in TERMINAL for r in self.runs):
            self.exit_code = EXIT_DONE if all(r.state == "done" for r in self.runs) else EXIT_INCOMPLETE
            self.log(f"all runs terminal; launcher exit {self.exit_code}")
        self.write_status()
        return self.exit_code

    def monitor(self) -> int:
        """Launch and monitor until every run is terminal or a stop; returns the launcher exit code."""
        self.write_status()
        next_poll = self.clock() + timedelta(seconds=self.poll_s)
        try:
            while True:
                self.start_due()
                now = self.clock()
                if now >= next_poll:
                    code = self.poll_once()
                    if code is not None:
                        return code
                    next_poll = now + timedelta(seconds=self.poll_s)
                wake = next_poll
                if self._can_start() and self.next_start_at is not None:
                    wake = min(wake, self.next_start_at)
                delay = (wake - self.clock()).total_seconds()
                if delay > 0:
                    self.sleep(delay)
        finally:
            for r in self.runs:
                self._close(r)


# ---------------------------------------------------------------- CLI

def _append_decision(path: Path, now: datetime, decision: str, reason: str) -> None:
    path = Path(path)
    lead = ""
    if path.is_file() and path.stat().st_size:
        with open(path, "rb") as f:
            f.seek(-1, os.SEEK_END)
            lead = "" if f.read(1) == b"\n" else "\n"
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(f"{lead}- {now.astimezone(timezone.utc):%Y-%m-%d %H:%M} | {decision} | {reason} | yes\n")


def _cost_from_defaults(defaults: dict) -> CostParams:
    return CostParams(**{k: tuple(v) if isinstance(v, list) else v for k, v in defaults["cost"].items()})


def _r_text(R: dict) -> str:
    return "/".join(str(R.get(c, 0)) for c in CONDITIONS) + " (control/cover/markup/rotation)"


def main(argv=None, *, clock: Clock | None = None, sleep: Callable[[float], None] | None = None,
         popen=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.procurement.launcher",
                                 description="Launch and monitor the experiment runs (one process each).")
    ap.add_argument("--plan", required=True, help="config/run_plan.json")
    ap.add_argument("--dry-run", action="store_true", help="write manifest, configs and status; spawn nothing")
    ap.add_argument("--allow-unfrozen", action="store_true",
                    help="tests and dry runs only: proceed without config/frozen.json (defaults.json costs)")
    ap.add_argument("--poll-s", type=float, default=60.0)
    ap.add_argument("--results-root", default="results/runs")
    ap.add_argument("--frozen", default="config/frozen.json")
    ap.add_argument("--calibration", default=None, help="default: <results>/calibration.json")
    ap.add_argument("--decisions", default="docs/superpowers/DECISIONS.md")
    ap.add_argument("--bidder", default="llm", choices=("llm", "synthetic"))
    ap.add_argument("--stagger-s", type=float, default=5.0)
    args = ap.parse_args(argv)
    clock = clock or _utc_now
    sleep = sleep or time.sleep
    popen = popen or subprocess.Popen

    defaults = config.load_defaults()
    plan_file = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    results_root = Path(args.results_root).resolve()
    results_dir = results_root.parent

    # frozen cost parameters (audit 4.2 step 6)
    cost, frozen_from = _cost_from_defaults(defaults), None
    if Path(args.frozen).is_file():
        try:
            cost = read_frozen(args.frozen, cost)
        except ValueError as exc:
            print(f"launcher: refusing to start: {exc}", file=sys.stderr)
            return EXIT_PREFLIGHT
        frozen_from = args.frozen
    elif not args.allow_unfrozen:
        print(f"launcher: refusing to start: {args.frozen} (the frozen cost parameters) not found; "
              "--allow-unfrozen is for tests and dry runs only", file=sys.stderr)
        return EXIT_PREFLIGHT
    else:
        print(f"launcher: warning: {args.frozen} not found; using config/defaults.json cost parameters",
              file=sys.stderr)

    # sizing rule and caps (A46, §4g concurrency cap)
    has_key2 = bool(os.environ.get(KEY_VARS[2]))
    cal_path = Path(args.calibration) if args.calibration else results_dir / "calibration.json"
    cal = read_calibration(cal_path)
    if cal is not None:
        sized = apply_sizing(cal["limits"][1], has_key2)
        R, keys = sized["R"], sized["keys"]
        sizing = (f"sizing rule from {cal_path.name}: output-tokens limit {cal['limits'][1]['output_tokens']}, "
                  f"key 2 {'set' if has_key2 else 'not set'}")
    else:
        R, keys = dict(plan_file["R"]), 2 if has_key2 else 1
        sizing = f"no calibration limits ({cal_path}): R from the plan, cap = plan size"
        print(f"launcher: warning: {sizing}", file=sys.stderr)
    call_s = (cal or {}).get("mean_latency_s") or 10.0

    # pre-flight: key variables present (values never read beyond presence); before any file is written
    if not args.dry_run and args.bidder == "llm":
        for slot in (1, 2) if keys == 2 else (1,):
            if not os.environ.get(KEY_VARS[slot]):
                print(f"launcher: pre-flight failed: {KEY_VARS[slot]} is not set", file=sys.stderr)
                return EXIT_PREFLIGHT

    plan = {"R": R, "include_rotation": bool(plan_file.get("include_rotation", True)),
            "T": int(plan_file.get("T", defaults["T"]))}

    # time ladder (audit 4.2)
    now = clock()
    deadline = datetime.fromisoformat(plan_file["deadline_utc"].replace("Z", "+00:00"))
    plan, rung, stop = time_ladder(now, deadline, False, plan)
    rung_lines = [f"time ladder rung {k} (not launched by deadline - {LADDER[k][0]:g} h, "
                  f"deadline {plan_file['deadline_utc']}): {LADDER[k][1]}" for k in range(1, rung + 1)]
    if not args.dry_run:
        for line in rung_lines:
            _append_decision(args.decisions, now, line, "audit Part 4.2 time ladder")
    if stop:
        print(f"launcher: {rung_lines[-1]}; nothing launched", file=sys.stderr)
        return EXIT_LADDER

    runs = config.run_plan(plan["R"], plan["include_rotation"], plan["T"])
    runs = [replace(c, cost=cost, bidder=args.bidder, results_root=str(results_root)) for c in runs]
    manifest = build_manifest(runs, keys, args.stagger_s)
    limits_by_slot = cal["limits"] if cal else {}
    caps = slot_caps(limits_by_slot, manifest, call_s)
    cap, calls = sum(caps.values()), planned_calls(runs)
    counts = {c: sum(1 for r in runs if r.condition == c) for c in CONDITIONS}

    pricing = Pricing(**defaults["pricing"])
    a_priori = calls * pricing.cost_usd(A_PRIORI_INPUT, A_PRIORI_OUTPUT, 0, 0)
    info = {"R": counts, "T": plan["T"], "include_rotation": plan["include_rotation"], "cap": cap,
            "keys": keys, "rung": rung, "dry_run": args.dry_run, "call_s": call_s, "sizing": sizing,
            "frozen_from": frozen_from, "bidder": args.bidder, "deadline_utc": plan_file["deadline_utc"]}
    workspace = new_launch_dir(results_dir / "launches", now)
    manifest = write_workspace(workspace, manifest, frozen_from, limits_by_slot,
                               header={"created_utc": _iso(now), **info, "planned_calls": calls})
    lau = Launcher(manifest, workspace, results_root, caps=caps, pricing=pricing, planned_calls=calls,
                   clock=clock, sleep=sleep, popen=popen, poll_s=args.poll_s,
                   stall_s=float(defaults.get("stall_s", 600)), stop_usd=float(defaults.get("spend_stop_usd", 75)),
                   stagger_s=args.stagger_s, info=info)
    per_cond = ", ".join(f"{c} {counts[c]}" for c in LAUNCH_ORDER)
    slots = ", ".join(f"{k}: {v}" for k, v in caps.items())
    lau.log(f"launch plan{' (dry run)' if args.dry_run else ''}: {len(runs)} runs: {per_cond}")
    lau.log(f"R = {_r_text(counts)}, T {plan['T']}, cap {cap} (per key slot {slots}; call_s {call_s:g}), "
            f"keys {keys}, bidder {args.bidder}")
    lau.log(f"planned calls {calls}; a-priori cost ${a_priori:.2f} at {A_PRIORI_INPUT / 1000:g}k input / "
            f"{A_PRIORI_OUTPUT / 1000:g}k output tokens per call")
    lau.log(sizing)
    lau.log(f"frozen parameters: {frozen_from or 'none (defaults.json)'}; time ladder: "
            + ("; ".join(rung_lines) if rung_lines else "no rung"))
    if args.dry_run:
        lau.write_status()
        return EXIT_DONE
    _append_decision(args.decisions, now, f"launch {workspace.name}: {len(runs)} runs, R = {_r_text(counts)}, "
                     f"T {plan['T']}, keys {keys}, cap {cap}; {sizing}", "audit Part 2.3 sizing rule, 4.2 step 8")
    return lau.monitor()


if __name__ == "__main__":
    sys.exit(main())
