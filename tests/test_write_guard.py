import os, shutil
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# RULE: no test may invoke a destructive call or a write-mode open on a real path. Probes use
# non-existent files ("r+" fails with FileNotFoundError if unguarded) and non-existent parents
# (mkdir without parents fails with FileNotFoundError if unguarded), so a red run changes nothing.

@pytest.mark.parametrize("rel", ["results/__guard_probe__.txt", "config/__guard_probe__.json"])
def test_write_open_refused(rel):
    with pytest.raises(PermissionError):
        open(REPO_ROOT / rel, "r+")
    assert not (REPO_ROOT / rel).exists()

def test_decisions_md_protected_without_opening_it(tmp_path, monkeypatch):
    import importlib
    conftest = importlib.import_module("tests.conftest")
    decisions = REPO_ROOT / "docs" / "superpowers" / "DECISIONS.md"
    assert conftest._is_protected(decisions)          # predicate only; the file is never opened
    stand_in = tmp_path / "DECISIONS.md"
    stand_in.write_text("x", encoding="utf-8")
    assert not conftest._is_protected(stand_in)
    monkeypatch.setattr(conftest, "_PROTECTED_FILES", conftest._PROTECTED_FILES + (stand_in.resolve(),))
    with pytest.raises(PermissionError):
        open(stand_in, "a")
    assert stand_in.read_text(encoding="utf-8") == "x"

def test_mkdir_refused():
    with pytest.raises(PermissionError):
        os.mkdir(REPO_ROOT / "results" / "__guard_missing_parent__" / "child")
    with pytest.raises(PermissionError):
        (REPO_ROOT / "config" / "__guard_missing_parent__" / "child").mkdir()

def test_delete_refused():
    # RULE: no test may invoke a destructive call on a real path. Only non-existent probe
    # paths are used, so the guard must raise PermissionError (not FileNotFoundError) before
    # touching the filesystem, and nothing is lost if the guard is ever disabled.
    with pytest.raises(PermissionError):
        shutil.rmtree(REPO_ROOT / "results" / "__guard_probe_dir_does_not_exist__")
    with pytest.raises(PermissionError):
        os.remove(REPO_ROOT / "results" / "__guard_probe_missing__.txt")
    with pytest.raises(PermissionError):
        (REPO_ROOT / "config" / "__guard_probe_missing__.json").unlink()
    with pytest.raises(PermissionError):
        os.rename(REPO_ROOT / "results" / "__guard_probe_missing__.txt",
                  REPO_ROOT / "results" / "__guard_probe_missing2__.txt")

def test_read_allowed_and_tmp_allowed(tmp_path):
    assert (REPO_ROOT / "config" / "defaults.json").read_text(encoding="utf-8")
    p = tmp_path / "results" / "x.txt"   # a 'results' folder OUTSIDE the repo is fine
    p.parent.mkdir(parents=True); p.write_text("ok"); assert p.read_text() == "ok"
