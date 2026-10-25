import json
import sys
import zipfile

import pytest

from src.procurement import archive


def _mk(root, sub, name, arm):
    d = root / sub / name
    (d / "transcripts").mkdir(parents=True)
    (d / "transcripts" / "firm_0.md").write_text("prompt and reply " * 50, encoding="utf-8")
    (d / "checkpoint.json").write_text("{}", encoding="utf-8")
    meta = {"condition": "control", "seed": 1}
    if arm:
        meta["arm"] = arm
    (d / "run_meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return d


def _snap(d):
    return sorted((str(p.relative_to(d)), p.stat().st_mtime_ns) for p in d.rglob("*"))


def test_zip_transcripts_roundtrip(tmp_path):
    a = _mk(tmp_path, "runs", "r_a", "mem0")
    b = _mk(tmp_path, "runs", "r_b", "stateless")
    before = [_snap(a), _snap(b)]
    info = archive.zip_transcripts([a, b], tmp_path / "archive" / "x.zip")
    assert info["bytes"] > 0 and info["runs"] == 2 and info["files"] == 4
    names = zipfile.ZipFile(info["zip"]).namelist()
    assert "runs/r_a/transcripts/firm_0.md" in names and "runs/r_b/checkpoint.json" in names
    assert [_snap(a), _snap(b)] == before


def test_zip_refuses_overwrite(tmp_path):
    a = _mk(tmp_path, "runs", "r_a", "mem0")
    out = tmp_path / "x.zip"
    archive.zip_transcripts([a], out)
    before = out.read_bytes()
    with pytest.raises(FileExistsError):
        archive.zip_transcripts([a], out)
    assert out.read_bytes() == before


def test_select_runs_by_arm(tmp_path):
    a = _mk(tmp_path, "runs", "r_a", "mem0")
    b = _mk(tmp_path, "runs", "r_b", "hidden")
    c = _mk(tmp_path, "calibration_runs", "r_c", None)
    d = tmp_path / "runs" / "r_d"
    d.mkdir()
    (d / "run_meta.json").write_text('{"arm": "mem0"}', encoding="utf-8")  # no transcripts
    assert archive.select_runs(tmp_path, arms={"mem0", "stateless"}, round1=False) == [a]
    assert archive.select_runs(tmp_path, arms={"hidden"}, round1=False) == [b]
    assert archive.select_runs(tmp_path, arms=None, round1=True) == [c]


def test_failure_leaves_no_zip_or_part(tmp_path, monkeypatch):
    a = _mk(tmp_path, "runs", "r_a", "mem0")
    out = tmp_path / "arch" / "x.zip"
    def boom(self, *a, **k):
        raise OSError("disk")
    monkeypatch.setattr(zipfile.ZipFile, "write", boom)
    with pytest.raises(OSError):
        archive.zip_transcripts([a], out)
    assert list((tmp_path / "arch").iterdir()) == []


def test_skipped_reported(tmp_path):
    ok = _mk(tmp_path, "runs", "r_a", "mem0")
    bad = _mk(tmp_path, "runs", "r_bad", "mem0")
    (bad / "run_meta.json").unlink()
    skipped = []
    assert archive.select_runs(tmp_path, arms={"mem0"}, round1=False, skipped=skipped) == [ok]
    assert [d for d, _ in skipped] == [bad] and skipped[0][1]


def test_cli_empty_writes_nothing_and_prints_skips(tmp_path, capsys):
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "scripts"))
    import archive_transcripts
    bad = _mk(tmp_path, "runs", "r_bad", None)
    (bad / "run_meta.json").unlink()
    assert archive_transcripts.main(["--wave", "1", "--results-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "skipped" in out and "nothing to archive" in out
    assert not (tmp_path / "archive").exists()


def test_arcnames_prefix_parent_no_collision(tmp_path):
    a = _mk(tmp_path, "runs", "same", None)
    b = _mk(tmp_path, "calibration_runs", "same", None)
    info = archive.zip_transcripts([a, b], tmp_path / "x.zip")
    names = zipfile.ZipFile(info["zip"]).namelist()
    assert "runs/same/checkpoint.json" in names and "calibration_runs/same/checkpoint.json" in names
    with pytest.raises(ValueError):
        archive.zip_transcripts([a, a], tmp_path / "y.zip")
