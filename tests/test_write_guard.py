import os, shutil
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

@pytest.mark.parametrize("rel", ["results/__guard_probe__.txt", "config/__guard_probe__.json",
                                 "docs/superpowers/DECISIONS.md"])
def test_write_open_refused(rel):
    with pytest.raises(PermissionError):
        open(REPO_ROOT / rel, "a")
    assert not (REPO_ROOT / "results/__guard_probe__.txt").exists()

def test_mkdir_refused():
    with pytest.raises(PermissionError):
        (REPO_ROOT / "results" / "__guard_probe_dir__").mkdir()
    with pytest.raises(PermissionError):
        os.makedirs(REPO_ROOT / "config" / "__guard_probe_dir__", exist_ok=True)

def test_delete_refused(tmp_path):
    with pytest.raises(PermissionError):
        shutil.rmtree(REPO_ROOT / "results" / "runs")
    with pytest.raises(PermissionError):
        os.remove(REPO_ROOT / "results" / "README.md")

def test_read_allowed_and_tmp_allowed(tmp_path):
    assert (REPO_ROOT / "config" / "defaults.json").read_text(encoding="utf-8")
    p = tmp_path / "results" / "x.txt"   # a 'results' folder OUTSIDE the repo is fine
    p.parent.mkdir(parents=True); p.write_text("ok"); assert p.read_text() == "ok"
