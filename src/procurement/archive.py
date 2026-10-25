"""Read-only zip archives of run transcripts and checkpoints (nothing under a run dir is modified)."""
from __future__ import annotations

import os
import zipfile
from pathlib import Path

from . import config, io

SOURCE_DIRS = ("runs", "calibration_runs")


def _is_run(d: Path) -> bool:
    return (d / "run_meta.json").is_file() or (d / "transcripts").is_dir()


def _run_dirs_under(root: Path) -> list[Path]:
    """Run dirs (holding run_meta.json) directly under ``root`` or one purpose level below."""
    found: list[Path] = []
    if not root.is_dir():
        return found
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if _is_run(child):
            found.append(child)
        else:
            found.extend(g for g in sorted(child.iterdir()) if g.is_dir() and _is_run(g))
    return found


def select_runs(results_root, *, arms: set[str] | None, round1: bool,
                skipped: list | None = None) -> list[Path]:
    """Run dirs with a transcripts/ folder whose arm is base (``round1``) or in ``arms``.

    Dirs with transcripts but a missing/unreadable run_meta.json are appended to ``skipped`` as
    (dir, reason)."""
    results_root = Path(results_root)
    out: list[Path] = []
    for sub in SOURCE_DIRS:
        for run_dir in _run_dirs_under(results_root / sub):
            if not (run_dir / "transcripts").is_dir():
                continue
            try:
                arm = config.arm_of_meta(io.read_run_meta(run_dir))
            except (OSError, ValueError) as e:
                if skipped is not None:
                    skipped.append((run_dir, f"run_meta.json missing or unreadable ({type(e).__name__})"))
                continue
            if (arm == "base") if round1 else (arms is not None and arm in arms):
                out.append(run_dir)
    return out


def zip_transcripts(run_dirs: list[Path], out_zip: Path) -> dict:
    """Write ``<parent>/<run>/transcripts/**`` and ``<parent>/<run>/checkpoint.json`` of each run dir
    into a new zip (via ``<out_zip>.part``, renamed on success). With nothing to archive, writes
    nothing and returns ``zip=None``."""
    out_zip = Path(out_zip)
    if out_zip.exists():
        raise FileExistsError(f"refusing to overwrite {out_zip}")
    plan: list[tuple[Path, str]] = []
    seen: set[str] = set()
    runs = 0
    for run_dir in map(Path, run_dirs):
        members: list[Path] = []
        tdir = run_dir / "transcripts"
        if tdir.is_dir():
            members.extend(p for p in sorted(tdir.rglob("*")) if p.is_file())
        ck = run_dir / "checkpoint.json"
        if ck.is_file():
            members.append(ck)
        if not members:
            continue
        runs += 1
        for p in members:
            arc = f"{run_dir.parent.name}/{run_dir.name}/{p.relative_to(run_dir).as_posix()}"
            if arc in seen:
                raise ValueError(f"duplicate archive member {arc}")
            seen.add(arc)
            plan.append((p, arc))
    if not plan:
        return {"zip": None, "bytes": 0, "runs": 0, "files": 0}
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    part = out_zip.with_name(out_zip.name + ".part")
    try:
        with zipfile.ZipFile(part, "x", compression=zipfile.ZIP_DEFLATED) as zf:
            for p, arc in plan:
                zf.write(p, arcname=arc)
        os.replace(part, out_zip)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return {"zip": str(out_zip), "bytes": out_zip.stat().st_size, "runs": runs, "files": len(plan)}
