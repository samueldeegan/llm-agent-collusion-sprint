"""Read-only zip archives of run transcripts and checkpoints (nothing under a run dir is modified)."""
from __future__ import annotations

import zipfile
from pathlib import Path

from . import config, io

SOURCE_DIRS = ("runs", "calibration_runs")


def _run_dirs_under(root: Path) -> list[Path]:
    """Run dirs (holding run_meta.json) directly under ``root`` or one purpose level below."""
    found: list[Path] = []
    if not root.is_dir():
        return found
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if (child / "run_meta.json").is_file():
            found.append(child)
        else:
            found.extend(g for g in sorted(child.iterdir()) if g.is_dir() and (g / "run_meta.json").is_file())
    return found


def select_runs(results_root, *, arms: set[str] | None, round1: bool) -> list[Path]:
    """Run dirs with a transcripts/ folder whose arm is base (``round1``) or in ``arms``."""
    results_root = Path(results_root)
    out: list[Path] = []
    for sub in SOURCE_DIRS:
        for run_dir in _run_dirs_under(results_root / sub):
            if not (run_dir / "transcripts").is_dir():
                continue
            try:
                arm = config.arm_of_meta(io.read_run_meta(run_dir))
            except (OSError, ValueError):
                continue
            if (arm == "base") if round1 else (arms is not None and arm in arms):
                out.append(run_dir)
    return out


def zip_transcripts(run_dirs: list[Path], out_zip: Path) -> dict:
    """Write ``<run>/transcripts/**`` and ``<run>/checkpoint.json`` of each run dir into a new zip."""
    out_zip = Path(out_zip)
    if out_zip.exists():
        raise FileExistsError(f"refusing to overwrite {out_zip}")
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    runs = files = 0
    with zipfile.ZipFile(out_zip, "x", compression=zipfile.ZIP_DEFLATED) as zf:
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
                zf.write(p, arcname=f"{run_dir.name}/{p.relative_to(run_dir).as_posix()}")
                files += 1
    return {"zip": str(out_zip), "bytes": out_zip.stat().st_size, "runs": runs, "files": files}
