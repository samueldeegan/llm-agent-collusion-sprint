r"""Zip run transcripts and checkpoints into results/archive/ (read-only on run dirs).

    .venvScripts\python.exe scriptsrchive_transcripts.py --wave 1|2|round1 [--results-dir results]

wave 1: arms mem0, stateless; wave 2: hidden, stateless_hidden; round1: base-arm runs that still
have transcripts under runs/ and calibration_runs/. Refuses to overwrite an existing zip."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.procurement import archive  # noqa: E402

WAVES = {
    "1": ({"mem0", "stateless"}, "round2_wave1_transcripts.zip"),
    "2": ({"hidden", "stateless_hidden"}, "round2_wave2_transcripts.zip"),
    "round1": (None, "round1_transcripts.zip"),
}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--wave", required=True, choices=list(WAVES))
    ap.add_argument("--results-dir", default=str(REPO_ROOT / "results"))
    a = ap.parse_args(argv)
    root = Path(a.results_dir)
    arms, name = WAVES[a.wave]
    skipped: list = []
    runs = archive.select_runs(root, arms=arms, round1=arms is None, skipped=skipped)
    for d, why in skipped:
        print(f"skipped {d}: {why}")
    if arms is None:
        n_cal = sum(1 for r in runs if r.parent.name == "calibration_runs")
        print(f"round1: found {len(runs)} base-arm run dirs with transcripts "
              f"({len(runs) - n_cal} under runs/, {n_cal} under calibration_runs/)")
    else:
        print(f"wave {a.wave}: found {len(runs)} run dirs with transcripts")
    try:
        info = archive.zip_transcripts(runs, root / "archive" / name)
    except FileExistsError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if info["zip"] is None:
        print("nothing to archive: no matching run has transcripts or a checkpoint; no zip written")
        return 0
    if info["bytes"] > 10_000_000:
        print("WARNING: over 10 MB - do not commit")
    print(f"{info['zip']}  {info['bytes'] / 1e6:.2f} MB  ({info['runs']} runs, {info['files']} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
