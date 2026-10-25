"""Run directory, CSV/JSON writers and readers, heartbeat, checkpoint and transcripts."""
from __future__ import annotations

import csv
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

BIDS_COLUMNS = ["run_id", "project_id", "firm_id", "est", "tag", "dist", "cap", "firm_rate", "bid", "won"]
TRUTH_COLUMNS = ["run_id", "project_id", "firm_id", "seat", "cost", "eps", "nu", "is_cartel",
                 "cartel_role", "scheme"]
MESSAGES_COLUMNS = ["run_id", "project_id", "order", "sender", "recipient", "my_cost",
                    "intended_serious_bid", "text", "reason"]
CALLS_COLUMNS = ["run_id", "project_id", "firm_id", "phase", "attempt", "latency_ms", "input_tokens",
                 "output_tokens", "cache_read_input_tokens", "stop_reason", "reason",
                 "ratelimit_requests_remaining", "ratelimit_input_tokens_remaining",
                 "ratelimit_output_tokens_remaining"]
PAIR_STATS_COLUMNS = ["run_id", "spec", "rho0", "usable_T", "primary", "firm_i", "firm_j", "n_joint", "r",
                      "z", "a1_flag", "a1_reason", "F", "F_p", "a3_flag", "a3_df_num", "rank_u",
                      "not_identified", "complete"]

RUN_PURPOSES = ("experiment", "smoke", "cover_smoke", "pilot")

_BIDS_INT_COLUMNS = ("project_id", "won")
_BIDS_STR_COLUMNS = ("run_id", "firm_id", "tag")


# ---------------------------------------------------------------- run directory

def new_run_dir(results_root, condition, seed, now_utc: datetime | None = None) -> Path:
    """Create a fresh run directory ``<yyyymmdd_HHMMSS>_<condition>_s<seed>``; never reuse one."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    base = f"{now_utc:%Y%m%d_%H%M%S}_{condition}_s{seed}"
    root = Path(results_root)
    root.mkdir(parents=True, exist_ok=True)
    suffix = 0
    while True:
        name = base if suffix == 0 else f"{base}_{suffix}"
        path = root / name
        try:
            path.mkdir(parents=True, exist_ok=False)
            return path
        except FileExistsError:
            suffix += 1


# ---------------------------------------------------------------- CSV

def _csv_field(value) -> str | int:
    if value is None:
        return ""
    if isinstance(value, (bool, np.bool_)):
        return int(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        f = float(value)
        return "" if math.isnan(f) else repr(f)
    return value


def _csv_row(row: dict, columns) -> list:
    return [_csv_field(row[c]) for c in columns]


def write_csv(path, rows: list[dict], columns) -> None:
    """Write header plus rows in ``columns`` order. Missing/None/NaN values become empty fields."""
    columns = list(columns)
    body = [_csv_row(r, columns) for r in rows]  # KeyError before the file is touched
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=",", lineterminator="\n")
        w.writerow(columns)
        w.writerows(body)


def append_csv_row(path, row: dict, columns) -> None:
    """Append one row, writing the header first if the file does not exist (or is empty)."""
    columns = list(columns)
    fields = _csv_row(row, columns)
    path = Path(path)
    new_file = (not path.exists()) or path.stat().st_size == 0
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=",", lineterminator="\n")
        if new_file:
            w.writerow(columns)
        w.writerow(fields)


def read_bids(run_dir) -> pd.DataFrame:
    """Read ``bids.csv``: project_id/won as int, text columns as str, the rest float (NaN if empty)."""
    raw = pd.read_csv(Path(run_dir) / "bids.csv", dtype=str, keep_default_na=False, encoding="utf-8")
    out = pd.DataFrame(index=raw.index)
    for col in raw.columns:
        s = raw[col]
        if col in _BIDS_STR_COLUMNS:
            out[col] = s
        elif col in _BIDS_INT_COLUMNS:
            out[col] = s.astype("int64")
        else:
            out[col] = pd.to_numeric(s.replace("", np.nan)).astype("float64")
    return out


# ---------------------------------------------------------------- JSON helpers

def _atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


# ---------------------------------------------------------------- run meta

def write_run_meta(run_dir, meta: dict) -> None:
    if "purpose" not in meta or meta["purpose"] not in RUN_PURPOSES:
        raise ValueError(f"run meta needs purpose in {RUN_PURPOSES}, got {meta.get('purpose')!r}")
    text = json.dumps(meta, indent=2, sort_keys=True)
    Path(run_dir, "run_meta.json").write_text(text, encoding="utf-8", newline="\n")


def read_run_meta(run_dir) -> dict:
    return json.loads(Path(run_dir, "run_meta.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- heartbeat

def write_heartbeat(run_dir, last_completed_project, last_completed_utc, last_activity_utc) -> None:
    hb = {"last_completed_project": int(last_completed_project),
          "last_completed_utc": _iso(last_completed_utc),
          "last_activity_utc": _iso(last_activity_utc)}
    _atomic_write_text(Path(run_dir) / "heartbeat.json", json.dumps(hb, indent=2))


def touch_activity(run_dir, now_utc: datetime | None = None) -> None:
    """Update only ``last_activity_utc``; create the heartbeat (project 0, never completed) if absent."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    path = Path(run_dir) / "heartbeat.json"
    if path.exists():
        hb = json.loads(path.read_text(encoding="utf-8"))
    else:
        hb = {"last_completed_project": 0, "last_completed_utc": None}
    write_heartbeat(run_dir, hb["last_completed_project"], hb["last_completed_utc"], now_utc)


# ---------------------------------------------------------------- checkpoint

def _checkpoint_default(obj):
    if isinstance(obj, np.ndarray):
        return {"__ndarray__": obj.tolist(), "dtype": str(obj.dtype), "shape": list(obj.shape)}
    if isinstance(obj, np.generic):
        return obj.item()
    raise TypeError(f"cannot serialise {type(obj).__name__} in checkpoint")


def _checkpoint_hook(d: dict):
    if "__ndarray__" in d:
        return np.array(d["__ndarray__"], dtype=np.dtype(d["dtype"])).reshape(d["shape"])
    return d


def write_checkpoint(run_dir, state: dict) -> None:
    _atomic_write_text(Path(run_dir) / "checkpoint.json", json.dumps(state, default=_checkpoint_default))


def read_checkpoint(run_dir) -> dict:
    text = Path(run_dir, "checkpoint.json").read_text(encoding="utf-8")
    return json.loads(text, object_hook=_checkpoint_hook)


# ---------------------------------------------------------------- transcripts

def append_transcript(run_dir, firm_id, project_id, phase, prompt, raw_response) -> None:
    folder = Path(run_dir) / "transcripts"
    folder.mkdir(parents=True, exist_ok=True)
    context = f"Project {project_id} ({phase})"
    stamp = datetime.now(timezone.utc).isoformat()
    with open(folder / f"{firm_id}.md", "a", encoding="utf-8", newline="\n") as f:
        f.write(f"\n## Prompt: {context} - {stamp}\n")
        f.write(f"``````\n{prompt}\n``````\n")
        f.write(f"\n## Response: {context} - {stamp}\n")
        f.write(f"````\n{raw_response}\n````\n")
