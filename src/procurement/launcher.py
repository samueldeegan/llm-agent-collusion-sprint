"""Launcher: one OS process per run, a monitor loop (exits, stalls, spend), file-based stop, time ladder.

``python -m src.procurement.launcher --plan config/run_plan.json [--dry-run] [--allow-unfrozen]
[--poll-s 60] [--results-root results/runs] [--ignore-live]`` (spec §4f, §4g stop semantics and concurrency cap;
A46, A49-A54, A95; step-4 audit Part 2 and Part 4.2 steps 7-8).

Every launch works in a new ``<results>/launches/<UTC yyyymmdd_HHMMSS>/`` (never overwritten):
``manifest.json``, ``configs/<launch_index>_<condition>_s<seed>.json`` (the runner's ``--config``),
``announce/<name>[.r<k>].txt`` (each attempt's run dir, written by the runner), ``logs/<name>[.r<k>].out``
and ``.err`` (each process's stdout and stderr), ``launcher.log`` and ``status.json`` (rewritten
atomically every poll). Each run is ``python -m src.procurement.runner --config ... --announce ...
--results-root ...`` (plus ``--resume-dir`` on a restart); the child inherits the environment, so the
API key never appears on a command line, in a file or in a log.

Exit codes: 0 every run done; 2 spend stop (audit 4.3 condition 2); 5 pre-flight refusal (no
``config/frozen.json``, an unusable or invalid ``calibration.json``, a key variable not set,
``--bidder synthetic`` or ``--allow-unfrozen`` in a real launch into ``results/runs``, an unfinished
matching run with activity within ``stall_s`` and no ``--ignore-live``) or a run's ``FatalAPIError``
(all runs stopped); 6 every run terminal but not all done; 7 time ladder stop
(nothing launched by deadline - 2.5 h). If the launcher itself fails or is interrupted, it stops every
child (STOP files, ``STOP.json`` reason ``launcher_error`` / ``interrupted``) before re-raising.

The spend guard covers total spend: at start it sums every ``calls.csv`` already under the results
root and ``calibration_runs/`` (the baseline) and adds this launch's runs to it. A (condition, seed)
that already has a finished experiment run with the same configuration is skipped
(``skipped_done``) unless ``--rerun-finished``; one with a stale unfinished matching run resumes from
the latest checkpointed one (``--resume-dir``, counted as its one restart; manifest ``resume_dir``).
When runs of the plan already exist, T stays at theirs whatever the time ladder gives. Every run
config comes from ``config.run_plan(..., defaults=config/defaults.json)`` (the scripts' helper), with
the frozen cost parameters and the plan's T. Relative paths given to ``--plan``, ``--frozen``,
``--calibration``, ``--decisions`` and ``--results-root`` resolve against the repository root.

Inputs read here and written elsewhere (only these keys are read):

``results/calibration.json`` (tier check and smoke runs)::

    {"limits": {"1": {"requests": 1000, "input_tokens": 2000000, "output_tokens": 400000},
                "2": {"requests": 1000, "input_tokens": 2000000, "output_tokens": 400000}},
     "measured": {"mean_latency_s": 10.2}}

``limits`` is per API key slot ("2" only when a second key was checked); a flat
``"limits": {"requests": ..., "output_tokens": ...}`` is read as slot 1. With limits, R per condition is
min(plan R, sized R). Without the file (or without
slot-1 limits) the plan's R is used, the process cap is the plan size, only key 1 is used (A54: key 2
only when the sizing rule calls for it) and a warning is printed. A file that exists but yields no
slot-1 limits is refused in real mode (exit 5).

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

from src.procurement import config, io, runner
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
TERMINAL = ("done", "stopped", "failed", "not_started")
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
        out.append({"launch_index": i, "name": f"{i:02d}_{config.run_id_of(cfg.condition, cfg.seed, cfg.arm)}",
                    "condition": cfg.condition, "seed": cfg.seed, "arm": cfg.arm,
                    "run_id": config.run_id_of(cfg.condition, cfg.seed, cfg.arm), "T": cfg.T, "stagger_s": stagger_s * i,
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
    (schema in the module docstring); None when the file or its slot-1 limits are missing, or the file
    is invalid (not a JSON object, a slot key that is not an integer)."""
    path = Path(path)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get("limits") or {}
    if "output_tokens" in raw or "requests" in raw:
        raw = {"1": raw}
    if not isinstance(raw, dict):
        return None
    try:
        limits = {int(k): v for k, v in raw.items() if isinstance(v, dict)}
    except (TypeError, ValueError):  # a slot key that is not an integer: an invalid file
        return None
    if not limits.get(1) or limits[1].get("output_tokens") is None:
        return None
    measured = data.get("measured")
    lat = measured.get("mean_latency_s") if isinstance(measured, dict) else None
    try:
        lat = float(lat) if lat else None
    except (TypeError, ValueError):
        lat = None
    return {"limits": limits, "mean_latency_s": lat}


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


_TOKEN_COLUMNS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


_WARNED: set[str] = set()


def _warn_once(key: str, msg: str, warn: Callable[[str], None] | None) -> None:
    if key in _WARNED:
        return
    _WARNED.add(key)
    if warn is not None:
        warn(msg)
    else:
        print(f"launcher: warning: {msg}", file=sys.stderr, flush=True)


def spend_totals(run_dirs, pricing: Pricing, warn: Callable[[str], None] | None = None) -> tuple[float, int]:
    """(USD, calls) over every ``calls.csv``: the token columns present priced (a missing one counts 0,
    with a warning once per file), a call counted by its attempt-1 row. Rows a resumed dir copied from
    its predecessor are skipped (counted there); torn or unparsable rows (a file being appended) too."""
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
            if not {"project_id", "attempt"} <= set(col):
                _warn_once(str(path), f"{path}: no project_id/attempt column; file not counted", warn)
                continue
            missing = [c for c in _TOKEN_COLUMNS if c not in col]
            if missing:
                _warn_once(str(path), f"{path}: no column(s) {missing}; counted as 0", warn)
            for row in reader:
                if len(row) != len(header):
                    continue
                pid = _int(row[col["project_id"]])
                if pid is None or pid <= skip:
                    continue
                tok = [(_int(row[col[c]]) or 0) if c in col else 0 for c in _TOKEN_COLUMNS]
                usd += pricing.cost_usd(*tok)
                calls += _int(row[col["attempt"]]) == 1
    return usd, calls


def existing_run_dirs(results_root) -> list[Path]:
    """Every run dir already under the results root and its sibling ``calibration_runs/``."""
    root = Path(results_root)
    out = []
    for base in (root, root.parent / Path(runner.CALIBRATION_ROOT).name):
        if base.is_dir():
            out += sorted(d for d in base.iterdir() if d.is_dir())
    return out


def spend_guard(run_dirs, pricing: Pricing, planned_calls: int, stop_usd: float = 75.0,
                baseline: tuple[float, int] = (0.0, 0), warn: Callable[[str], None] | None = None,
                own_dirs=None) -> tuple[float, float, bool]:
    """(actual, projected, stop?) over total spend: ``baseline`` (USD, calls) already spent before this
    launch plus every ``calls.csv`` in ``run_dirs``. projected = actual x (baseline calls + planned) /
    completed calls; stop when actual > ``stop_usd``, or when projected > ``stop_usd`` once at least 200
    calls are in (spec A50)."""
    usd, calls = spend_totals(run_dirs, pricing, warn)
    actual, calls = baseline[0] + usd, baseline[1] + calls
    if own_dirs is None:
        projected = actual * (baseline[1] + planned_calls) / max(calls, 1)
    else:  # run_dirs = every launch's dirs; only this launch's remaining calls are projected
        remaining = max(planned_calls - spend_totals(own_dirs, pricing, warn)[1], 0)
        projected = actual * (calls + remaining) / max(calls, 1)
    stop = actual > stop_usd or (calls >= SPEND_FLOOR_CALLS and projected > stop_usd)
    return actual, projected, bool(stop)


def _finished(run_dir: Path) -> bool:
    try:
        return io.read_run_meta(run_dir).get("finished_utc") is not None
    except (OSError, ValueError):
        return False


def _kill(proc) -> bool:
    """Kill ``proc``; True when it is gone afterwards."""
    try:
        proc.kill()
        proc.wait(timeout=30)
    except Exception:  # already gone, or wait timed out: checked below
        pass
    try:
        return proc.poll() is not None
    except Exception:
        return False


def _rotate_stop_json(path: Path) -> None:
    """Rename an existing ``STOP.json`` to ``STOP_<its utc>.json`` (never deleted)."""
    if not path.is_file():
        return
    try:
        t = datetime.fromisoformat(str(json.loads(path.read_text(encoding="utf-8"))["utc"]))
        t = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except (OSError, ValueError, KeyError, TypeError):
        t = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    stamp = f"{t.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}"
    k = 0
    while True:
        target = path.with_name(f"STOP_{stamp}{'' if k == 0 else f'_{k}'}.json")
        if not target.exists():
            os.replace(path, target)
            return
        k += 1


def stop_all(procs, run_dirs, results_root, reason: str, actual: float, projected: float,
             clock: Clock = _utc_now, sleep: Callable[[float], None] = time.sleep,
             grace_s: float = STOP_GRACE_S, poll_every_s: float = 2.0, not_started=None,
             copy_dir=None) -> int:
    """Spec §4g stop: an empty ``STOP`` file in every running run's dir (``procs[i]`` runs in
    ``run_dirs[i]``; a None proc is not running), up to ``grace_s`` of ``clock`` for them to exit,
    ``kill()`` for stragglers, then ``<results_root>/../STOP.json`` (the ``results/STOP.json`` the
    analysis reads; an older one is first renamed ``STOP_<utc>.json``) with run-dir basenames and the
    run ids of ``not_started`` runs, and a copy in ``copy_dir`` (the launch dir). Returns 2."""
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
    data = {"reason": reason, "actual_usd": actual, "projected_usd": projected, "utc": _iso(clock()),
            "runs_complete": complete, "runs_incomplete": incomplete,
            "runs_not_started": list(not_started or [])}
    stop_path = Path(results_root).parent / "STOP.json"
    stop_path.parent.mkdir(parents=True, exist_ok=True)
    _rotate_stop_json(stop_path)
    _write_json(stop_path, data)
    if copy_dir is not None:
        try:
            _write_json(Path(copy_dir) / "STOP.json", data)
        except OSError:
            pass
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
        # a relaunch resuming an unfinished run (B5) starts with --resume-dir; it uses the restart budget
        self.initial_resume = Path(entry["resume_dir"]) if entry.get("resume_dir") else None
        self.restarts = 1 if self.initial_resume is not None else 0
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
                 info: dict | None = None, repo_root=REPO_ROOT, skipped: list[dict] | None = None,
                 baseline: tuple[float, int] | None = None):
        self.runs = [_Run(e) for e in manifest]
        self.skipped = list(skipped or [])
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
        self._stopping = False
        self._io_errors: set[str] = set()
        # total spend before this launch (spec §4f "all calls.csv"); taken before any spawn
        self.baseline_dirs = existing_run_dirs(self.results_root)
        self.baseline = baseline if baseline is not None else spend_totals(self.baseline_dirs, pricing)

    # ------------------------------------------------------------ logging and status

    def _io_error(self, what: str, exc: OSError) -> None:
        if what not in self._io_errors:
            self._io_errors.add(what)
            print(f"launcher: cannot write {what} ({type(exc).__name__}: {exc}); continuing",
                  file=sys.stderr, flush=True)

    def log(self, msg: str) -> None:
        line = f"{_iso(self.clock())} {msg}"
        print(line, flush=True)
        try:
            with open(self.workspace / "launcher.log", "a", encoding="utf-8", newline="\n") as f:
                f.write(line + "\n")
        except OSError as exc:
            self._io_error("launcher.log", exc)

    def write_status(self) -> None:
        try:
            self._write_status()
        except OSError as exc:
            self._io_error("status.json", exc)

    def _write_status(self) -> None:
        poll = _iso(self.last_poll)
        runs = [{"name": r.name, "launch_index": r.entry["launch_index"], "condition": r.entry["condition"],
                 "seed": r.entry["seed"], "api_key_slot": r.slot, "state": r.state,
                 "run_dir": None if r.run_dir is None else str(r.run_dir),
                 "run_dirs": [str(d) for d in r.run_dirs], "attempts": r.attempts, "restarts": r.restarts,
                 "exit_code": r.exit_code, "last_poll_utc": poll} for r in self.runs]
        runs += [{**s, "state": "skipped_done", "last_poll_utc": poll} for s in self.skipped]
        _write_json(self.workspace / "status.json", {
            **self.info, "launch_dir": str(self.workspace), "utc": _iso(self.clock()), "last_poll_utc": poll,
            "caps": {str(k): v for k, v in self.caps.items()}, "planned_calls": self.planned_calls,
            "actual_usd": self.actual, "projected_usd": self.projected, "baseline_usd": self.baseline[0],
            "baseline_calls": self.baseline[1], "exit_code": self.exit_code,
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
                self._start(run, resume_dir=run.initial_resume)
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

    def _on_exit(self, run: _Run, rc: int, restart: bool = True) -> None:
        """Handle a finished process. ``restart=False`` (a fatal API error in the same pass): a crash
        is marked failed, not restarted."""
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
        elif not restart:
            run.state = "failed"
            self.log(f"{run.name}: crashed (exit {rc}); not restarted (fatal API error in this pass)")
        else:
            self._restart_or(run, f"crashed (exit {rc})", "failed")

    def _on_stall(self, run: _Run, idle_s: float) -> None:
        why = f"stalled (no activity for {idle_s:.0f} s)"
        if run.restarts >= self.max_restarts and run.current_dir is not None:
            try:  # the analysis reports a run with a STOP file as stopped
                (run.current_dir / "STOP").write_bytes(b"")
            except OSError:
                pass
        gone = _kill(run.proc)
        run.exit_code = run.proc.poll()
        self._close(run)
        if not gone:
            run.state = "failed"
            self.log(f"{run.name}: {why}; kill() did not end the process; marked failed, not restarted")
            return
        self._restart_or(run, why, "stopped")

    def _all_dirs(self) -> list[Path]:
        return [d for r in self.runs for d in r.run_dirs]

    def _spend(self) -> bool:
        # actual spend over every run dir under the results root and calibration_runs, re-listed each poll
        # (other launches' dirs included: the stop is across all launches); resumed dirs are not double
        # counted. Projected adds only this launcher's own remaining planned calls.
        own = self._all_dirs()
        seen, dirs = set(), []
        for d in [*existing_run_dirs(self.results_root), *own]:
            key = Path(d).resolve()
            if key not in seen:
                seen.add(key)
                dirs.append(d)
        self.actual, self.projected, stop = spend_guard(dirs, self.pricing, self.planned_calls, self.stop_usd,
                                                        (0.0, 0), self.log, own_dirs=own)
        return stop

    def _stop(self, reason: str, code: int | None) -> int | None:
        self._stopping = True
        procs, dirs = [], []
        for r in self.runs:
            for d in r.run_dirs:
                if not (r.alive and d == r.current_dir):
                    procs.append(None)
                    dirs.append(d)
            if r.alive:
                procs.append(r.proc)
                dirs.append(r.current_dir)
        self.log(f"stop all runs: {reason} (actual ${self.actual:.2f}, projected ${self.projected:.2f})")
        not_started = [r for r in self.runs if r.state == "queued"]
        stop_all(procs, dirs, self.results_root, reason, self.actual, self.projected, self.clock, self.sleep,
                 not_started=[r.entry.get("run_id") or config.run_id_of(r.entry['condition'], r.entry['seed'])
                              for r in not_started],
                 copy_dir=self.workspace)
        for r in not_started:
            r.state = "not_started"
        for r in self.runs:
            if r.alive:
                r.exit_code = r.proc.poll()
                if r.exit_code is None:
                    r.state = "failed"
                    self.log(f"{r.name}: still alive after kill(); marked failed")
                else:
                    r.state = "stopped"
                self._close(r)
        self.stop_reason, self.exit_code = reason, code
        self.log(f"STOP.json written; launcher exit {code if code is not None else '(error, re-raised)'}")
        self.write_status()
        return code

    def poll_once(self) -> int | None:
        """One monitor pass: exits, stalls, spend guard, status. Returns the launcher exit code once
        the launch is over, else None."""
        now = self.last_poll = self.clock()
        exits = []
        for run in self.runs:  # collect every exit of this pass before acting on any
            if run.alive:
                self._refresh_dir(run)
                rc = run.proc.poll()
                if rc is not None:
                    exits.append((run, rc))
        fatal = any(rc == RUNNER_FATAL for _, rc in exits)
        for run, rc in exits:
            self._on_exit(run, rc, restart=not fatal)
        if fatal:
            self._spend()
            return self._stop("fatal_api_error", EXIT_FATAL)
        for run in self.runs:
            if run.alive:
                idle = (now - self._last_activity(run)).total_seconds()
                if idle > self.stall_s:
                    self._on_stall(run, idle)
        if self._spend():
            return self._stop("spend", EXIT_SPEND)
        if all(r.state in TERMINAL for r in self.runs):
            self.exit_code = EXIT_DONE if all(r.state == "done" for r in self.runs) else EXIT_INCOMPLETE
            self.log(f"all runs terminal; launcher exit {self.exit_code}")
        self.write_status()
        return self.exit_code

    def monitor(self) -> int:
        """Launch and monitor until every run is terminal or a stop; returns the launcher exit code.
        Any exception (or Ctrl+C, which never reaches the children's own process groups) first stops
        every child: STOP files, ``STOP.json`` with reason ``launcher_error`` or ``interrupted``."""
        try:
            self.log(f"spend baseline ${self.baseline[0]:.2f} over {self.baseline[1]} calls in "
                     f"{len(self.baseline_dirs)} existing run dir(s) under {self.results_root.parent}")
            self.write_status()
            next_poll = self.clock() + timedelta(seconds=self.poll_s)
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
        except BaseException as exc:
            self._emergency_stop(exc)
            raise
        finally:
            for r in self.runs:
                self._close(r)

    def _emergency_stop(self, exc: BaseException) -> None:
        reason = "interrupted" if isinstance(exc, KeyboardInterrupt) else "launcher_error"
        print(f"launcher: {type(exc).__name__}: {exc}; stopping every run ({reason})", file=sys.stderr,
              flush=True)
        try:
            if not self._stopping:
                self._stop(reason, None)
                return
        except BaseException as exc2:  # the stop itself failed: at least end the children
            print(f"launcher: stop failed ({type(exc2).__name__}: {exc2}); killing children",
                  file=sys.stderr, flush=True)
        for r in self.runs:
            if r.alive and r.proc is not None:
                _kill(r.proc)


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
    return config.cost_from_defaults(defaults)


def _repo_path(value) -> Path:
    """An absolute path; a relative one is taken relative to the repository root."""
    p = Path(value)
    return p if p.is_absolute() else REPO_ROOT / p


def _experiment_metas(results_root) -> list[tuple[Path, dict]]:
    """(dir, run_meta) of every experiment run dir directly under ``results_root``, by name."""
    root = Path(results_root)
    out = []
    if not root.is_dir():
        return out
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        try:
            meta = io.read_run_meta(d)
        except (OSError, ValueError):
            continue
        if isinstance(meta, dict) and meta.get("purpose") == "experiment":
            out.append((d, meta))
    return out


def _meta_run_id(meta: dict) -> str | None:
    try:
        return config.run_id_from_meta(meta)
    except (KeyError, TypeError):
        return None


def _plan_by_run_id(plan: list[RunConfig]) -> dict[str, RunConfig]:
    return {config.run_id_of(c.condition, c.seed, c.arm): c for c in plan}


def finished_runs(results_root, plan: list[RunConfig]) -> dict[str, str]:
    """run_id -> run dir name, for each plan entry that already has a finished experiment
    run under ``results_root`` with the same configuration (the runner's resume check: condition,
    seed, T, cartel seats, N, bidder, model, windows, message cap, concurrency, scripted fields, cost
    parameters)."""
    by_key = _plan_by_run_id(plan)
    found: dict[str, str] = {}
    for d, meta in _experiment_metas(results_root):
        if meta.get("finished_utc") is None:
            continue
        key = _meta_run_id(meta)
        cfg = by_key.get(key)
        if cfg is not None and runner.same_run(cfg, meta):
            found.setdefault(key, d.name)
    return found


def existing_experiment_T(results_root, plan: list[RunConfig]) -> tuple[int | None, list[str]]:
    """The T of the experiment runs already under ``results_root`` that belong to ``plan`` (the same run
    as a plan entry except possibly T) and are finished or resumable (hold a checkpoint): ``(T, dir
    names)``; ``(None, [])`` when there is none. Several T values: the most common; on a tie the plan's
    own T when it is among them, else the latest run's."""
    by_key = _plan_by_run_id(plan)
    hits: list[tuple[int, str]] = []
    for d, meta in _experiment_metas(results_root):
        cfg = by_key.get(_meta_run_id(meta))
        T = meta.get("T")
        if cfg is None or not isinstance(T, int) or isinstance(T, bool):
            continue
        if meta.get("finished_utc") is None and not (d / "checkpoint.json").is_file():
            continue
        if runner.same_run(replace(cfg, T=T), meta):
            hits.append((T, d.name))
    if not hits:
        return None, []
    counts: dict[int, int] = {}
    for T, _ in hits:
        counts[T] = counts.get(T, 0) + 1
    best = max(counts.values())
    tied = [t for t, _ in hits if counts[t] == best]
    plan_T = {c.T for c in plan}
    T = next((t for t in tied if t in plan_T), tied[-1])
    return T, [name for _, name in hits]


def _last_activity_of(run_dir: Path) -> datetime | None:
    """``last_activity_utc`` of a run dir's heartbeat; without a readable heartbeat, the mtime of its
    run_meta.json (a run that has just started)."""
    try:
        hb = json.loads((run_dir / "heartbeat.json").read_text(encoding="utf-8"))
        t = datetime.fromisoformat(str(hb["last_activity_utc"]))
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        return datetime.fromtimestamp((run_dir / "run_meta.json").stat().st_mtime, timezone.utc)
    except OSError:
        return None


def unfinished_runs(results_root, plan: list[RunConfig], now: datetime, stall_s: float
                    ) -> tuple[dict[str, list[str]], dict[str, Path]]:
    """Unfinished experiment dirs matching a plan entry (``runner.same_run``): ``(live, resume)``.
    ``live[run_id]``: the dir names whose last activity is within ``stall_s`` of ``now``;
    ``resume[run_id]``: the latest (by name) unfinished matching dir holding a
    ``checkpoint.json``, for the entries with no live dir."""
    by_key = _plan_by_run_id(plan)
    live: dict[str, list[str]] = {}
    resume: dict[str, Path] = {}
    for d, meta in _experiment_metas(results_root):
        if meta.get("finished_utc") is not None:
            continue
        key = _meta_run_id(meta)
        cfg = by_key.get(key)
        if cfg is None or not runner.same_run(cfg, meta):
            continue
        last = _last_activity_of(d)
        if last is not None and (now - last).total_seconds() <= stall_s:
            live.setdefault(key, []).append(d.name)
        if (d / "checkpoint.json").is_file():
            resume[key] = d  # sorted by name: the latest wins
    return live, {k: v for k, v in resume.items() if k not in live}


def _r_text(R: dict) -> str:
    return "/".join(str(R.get(c, 0)) for c in CONDITIONS) + " (control/cover/markup/rotation)"


DEFAULT_RESULTS_ROOT = "results/runs"


def main(argv=None, *, clock: Clock | None = None, sleep: Callable[[float], None] | None = None,
         popen=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.procurement.launcher",
                                 description="Launch and monitor the experiment runs (one process each).")
    ap.add_argument("--plan", required=True, help="config/run_plan.json")
    ap.add_argument("--dry-run", action="store_true", help="write manifest, configs and status; spawn nothing")
    ap.add_argument("--allow-unfrozen", action="store_true",
                    help="tests and dry runs only: proceed without config/frozen.json (defaults.json costs); "
                         "refused in a real launch into results/runs")
    ap.add_argument("--poll-s", type=float, default=60.0)
    ap.add_argument("--results-root", default=DEFAULT_RESULTS_ROOT)
    ap.add_argument("--frozen", default="config/frozen.json")
    ap.add_argument("--calibration", default=None, help="default: <results>/calibration.json")
    ap.add_argument("--decisions", default="docs/superpowers/DECISIONS.md")
    ap.add_argument("--bidder", default="llm", choices=("llm", "synthetic"),
                    help="synthetic is refused in a real launch into results/runs")
    ap.add_argument("--stagger-s", type=float, default=5.0)
    ap.add_argument("--rerun-finished", action="store_true",
                    help="also launch (condition, seed) pairs that already have a finished matching run")
    ap.add_argument("--ignore-live", action="store_true",
                    help="start even when an unfinished matching run shows activity within stall_s")
    ap.add_argument("--arms", default=None,
                    help="comma list of ablation arms from the plan's arms entries (implies --no-ladder; "
                         "the sizing rule's per-condition R minimum is not applied)")
    ap.add_argument("--no-ladder", action="store_true", help="skip the time ladder (rung 0)")
    args = ap.parse_args(argv)
    clock = clock or _utc_now
    sleep = sleep or time.sleep
    popen = popen or subprocess.Popen

    results_root = _repo_path(args.results_root).resolve()
    results_dir = results_root.parent
    # the experiment's own results root takes only real, frozen runs (B3); before any file is written
    if not args.dry_run and results_root == _repo_path(DEFAULT_RESULTS_ROOT).resolve():
        for flag, bad in (("--bidder synthetic", args.bidder == "synthetic"),
                          ("--allow-unfrozen", args.allow_unfrozen)):
            if bad:
                print(f"launcher: refusing to start: {flag} in a real launch into the experiment results "
                      f"root {DEFAULT_RESULTS_ROOT} (pass a different --results-root, or --dry-run)",
                      file=sys.stderr)
                return EXIT_PREFLIGHT

    defaults = config.load_defaults()
    plan_file = json.loads(_repo_path(args.plan).read_text(encoding="utf-8"))
    frozen_path, decisions = _repo_path(args.frozen), _repo_path(args.decisions)
    stall_s = float(defaults.get("stall_s", 600))
    if "spend_stop_usd" not in defaults:
        print("launcher: refusing to start: config/defaults.json has no spend_stop_usd", file=sys.stderr)
        return EXIT_PREFLIGHT
    arms = [a.strip() for a in args.arms.split(",") if a.strip()] if args.arms is not None else None
    if arms is not None:  # an empty or unknown arm is refused before any file is written
        known = [e.get("arm") for e in plan_file.get("arms", [])]
        bad = [a for a in arms if a not in known] or ([""] if not arms else [])
        if bad:
            print(f"launcher: refusing to start: unknown or empty --arms {bad} (the plan has {known})",
                  file=sys.stderr)
            return EXIT_PREFLIGHT

    # frozen cost parameters (audit 4.2 step 6)
    cost, frozen_from = _cost_from_defaults(defaults), None
    if frozen_path.is_file():
        try:
            cost = read_frozen(frozen_path, cost)
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

    # sizing rule and caps (A46, §4g concurrency cap); R = per-condition min(plan R, sized R)
    has_key2 = bool(os.environ.get(KEY_VARS[2]))
    cal_path = _repo_path(args.calibration) if args.calibration else results_dir / "calibration.json"
    cal = read_calibration(cal_path)
    if cal is None and cal_path.is_file():
        msg = f"{cal_path} exists but yields no slot-1 limits (limits.output_tokens) or is invalid"
        if not args.dry_run:
            print(f"launcher: refusing to start: {msg}", file=sys.stderr)
            return EXIT_PREFLIGHT
        print(f"launcher: warning: {msg}", file=sys.stderr)
    plan_R = dict(plan_file["R"])
    if cal is not None:
        sized = apply_sizing(cal["limits"][1], has_key2)
        keys = sized["keys"]
        R = {c: min(int(plan_R.get(c, 0)), int(sized["R"].get(c, 0))) for c in {**plan_R, **sized["R"]}}
        if arms is not None:  # R comes from the arms entries; the sizing rule gives the key count only
            R = plan_R
        sizing = (f"sizing rule from {cal_path.name}: output-tokens limit {cal['limits'][1]['output_tokens']}, "
                  f"key 2 {'set' if has_key2 else 'not set'}; sized R {_r_text(sized['R'])}, plan R "
                  f"{_r_text(plan_R)} -> R {_r_text(R)} (per-condition minimum"
                  + ("; not applied with --arms: R from the arms entries)" if arms is not None else ")"))
    else:
        R, keys = plan_R, 1
        sizing = f"no calibration limits ({cal_path}): R from the plan, cap = plan size, one key"
        if has_key2:
            sizing += f"; {KEY_VARS[2]} is set but ignored (A54: key 2 only when the sizing rule calls for it)"
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

    def configs(p: dict) -> list[RunConfig]:  # the single settings source (A1): config/defaults.json
        if arms is not None:
            return config.arm_plan(plan_file, arms, p["T"], defaults=defaults, cost=cost,
                                   bidder=args.bidder, results_root=str(results_root))
        return config.run_plan(p["R"], p["include_rotation"], p["T"], defaults=defaults, cost=cost,
                               bidder=args.bidder, results_root=str(results_root))

    if arms is not None:  # a bad arms entry is refused before any file is written
        try:
            configs(plan)
        except ValueError as exc:
            print(f"launcher: refusing to start: {exc}", file=sys.stderr)
            return EXIT_PREFLIGHT

    # T of the runs already made for this plan: never changed mid-experiment (C5)
    existing_T, existing_names = existing_experiment_T(results_root, configs(plan))

    # time ladder (audit 4.2): may lower R and drop rotation; T stays at the existing runs' T
    now = clock()
    deadline = datetime.fromisoformat(plan_file["deadline_utc"].replace("Z", "+00:00"))
    no_ladder = args.no_ladder or arms is not None
    if no_ladder:
        rung, stop = 0, False
    else:
        plan, rung, stop = time_ladder(now, deadline, False, plan)
    rung_lines = [f"time ladder rung {k} (not launched by deadline - {LADDER[k][0]:g} h, "
                  f"deadline {plan_file['deadline_utc']}): {LADDER[k][1]}" for k in range(1, rung + 1)]
    t_line = None
    if existing_T is not None:
        if plan["T"] != existing_T:
            t_line = (f"T kept at {existing_T} (the T of {len(existing_names)} existing experiment run(s) of "
                      f"this plan; the ladder would give T {plan['T']})")
        plan["T"] = existing_T
    if stop:
        if not args.dry_run:
            for line in rung_lines:
                _append_decision(decisions, now, line, "audit Part 4.2 time ladder")
        print(f"launcher: {rung_lines[-1]}; nothing launched", file=sys.stderr)
        return EXIT_LADDER

    runs = configs(plan)
    done = {} if args.rerun_finished else finished_runs(results_root, runs)
    by_id = _plan_by_run_id(runs)
    skipped = [{"name": rid, "condition": by_id[rid].condition, "seed": by_id[rid].seed,
                "arm": by_id[rid].arm, "run_dir": str(results_root / d)} for rid, d in sorted(done.items())]
    runs = [c for c in runs if config.run_id_of(c.condition, c.seed, c.arm) not in done]

    # relaunch safety (B5): a live unfinished run refuses the launch; a stale checkpointed one resumes
    live, resume = unfinished_runs(results_root, runs, now, stall_s)
    if live and not args.ignore_live:
        names = "; ".join(f"{rid}: {', '.join(v)}" for rid, v in sorted(live.items()))
        msg = (f"unfinished run(s) with activity in the last {stall_s:g} s, probably still running: {names} "
               "(stop them first, or pass --ignore-live)")
        if not args.dry_run:
            print(f"launcher: refusing to start: {msg}", file=sys.stderr)
            return EXIT_PREFLIGHT
        print(f"launcher: warning: a real launch would refuse: {msg}", file=sys.stderr)
    if live:  # --ignore-live: a live run is treated as stale
        resume = unfinished_runs(results_root, runs, now, -1.0)[1]

    if not args.dry_run:
        for line in rung_lines:
            _append_decision(decisions, now, line, "audit Part 4.2 time ladder")
        if t_line is not None:
            _append_decision(decisions, now, t_line, "never change T mid-experiment (final review C5)")

    manifest = build_manifest(runs, keys, args.stagger_s)
    for e in manifest:
        d = resume.get(e["run_id"])
        e["resume_dir"] = None if d is None else str(d)
    limits_by_slot = cal["limits"] if cal else {}
    caps = slot_caps(limits_by_slot, manifest, call_s)
    cap, calls = sum(caps.values()), planned_calls(runs)
    if arms is not None:
        counts = {f"{a}/{c}": n for a in arms for c in CONDITIONS
                  if (n := sum(1 for r in runs if r.arm == a and r.condition == c))}
    else:
        counts = {c: sum(1 for r in runs if r.condition == c) for c in CONDITIONS}
    counts_text = (", ".join(f"{k} {n}" for k, n in counts.items()) if arms is not None
                   else _r_text(counts))

    pricing = Pricing(**defaults["pricing"])
    a_priori = calls * pricing.cost_usd(A_PRIORI_INPUT, A_PRIORI_OUTPUT, 0, 0)
    info = {"R": counts, "T": plan["T"], "include_rotation": plan["include_rotation"], "cap": cap,
            "keys": keys, "rung": rung, "dry_run": args.dry_run, "call_s": call_s, "sizing": sizing,
            "frozen_from": frozen_from, "bidder": args.bidder, "deadline_utc": plan_file["deadline_utc"],
            "skipped_done": [x["name"] for x in skipped]}
    workspace = new_launch_dir(results_dir / "launches", now)
    manifest = write_workspace(workspace, manifest, frozen_from, limits_by_slot,
                               header={"created_utc": _iso(now), **info, "planned_calls": calls})
    lau = Launcher(manifest, workspace, results_root, caps=caps, pricing=pricing, planned_calls=calls,
                   clock=clock, sleep=sleep, popen=popen, poll_s=args.poll_s,
                   stall_s=stall_s, stop_usd=float(defaults["spend_stop_usd"]),
                   stagger_s=args.stagger_s, info=info, skipped=skipped)
    per_cond = counts_text if arms is not None else ", ".join(f"{c} {counts[c]}" for c in LAUNCH_ORDER)
    slots = ", ".join(f"{k}: {v}" for k, v in caps.items())
    lau.log(f"launch plan{' (dry run)' if args.dry_run else ''}: {len(runs)} runs: {per_cond}")
    lau.log(f"R = {counts_text}, T {plan['T']}, cap {cap} (per key slot {slots}; call_s {call_s:g}), "
            f"keys {keys}, bidder {args.bidder}")
    lau.log(f"planned calls {calls}; a-priori cost ${a_priori:.2f} at {A_PRIORI_INPUT / 1000:g}k input / "
            f"{A_PRIORI_OUTPUT / 1000:g}k output tokens per call")
    lau.log(sizing)
    if t_line is not None:
        lau.log(t_line)
    for x in skipped:
        lau.log(f"skip {x['name']}: finished matching run {Path(x['run_dir']).name} (skipped_done; "
                "--rerun-finished to run it again)")
    for rid, d in sorted(resume.items()):
        lau.log(f"resume {rid}: unfinished run {d.name} restarts from its checkpoint (--resume-dir; "
                "uses the one-restart budget)")
    lau.log(f"spend baseline ${lau.baseline[0]:.2f} over {lau.baseline[1]} calls in "
            f"{len(lau.baseline_dirs)} existing run dir(s)")
    lau.log(f"frozen parameters: {frozen_from or 'none (defaults.json)'}; time ladder: "
            + (f"off ({'--arms implies --no-ladder' if arms is not None else '--no-ladder'})" if no_ladder
                  else "; ".join(rung_lines) if rung_lines else "no rung"))
    if args.dry_run:
        lau.write_status()
        return EXIT_DONE
    _append_decision(decisions, now, f"launch {workspace.name}: {len(runs)} runs, R = {counts_text}, "
                     f"T {plan['T']}, keys {keys}, cap {cap}; {sizing}", "audit Part 2.3 sizing rule, 4.2 step 8")
    return lau.monitor()


if __name__ == "__main__":
    sys.exit(main())
