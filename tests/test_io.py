import json
import math
import threading
from datetime import datetime, timezone

import numpy as np
import pytest

from src.procurement import io as io_mod
from src.procurement.io import (BIDS_COLUMNS, TRUTH_COLUMNS, MESSAGES_COLUMNS, CALLS_COLUMNS,
                                PAIR_STATS_COLUMNS, new_run_dir, write_csv, append_csv_row, read_bids,
                                write_run_meta, read_run_meta, write_heartbeat, touch_activity,
                                write_checkpoint, read_checkpoint, append_transcript)

NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def _bid_row(**kw):
    row = {"run_id": "r", "project_id": 1, "firm_id": "A", "est": 1000000.0, "tag": "small",
           "dist": 3.5, "cap": 0.25, "firm_rate": 0.02, "bid": 1070000.0, "won": 1}
    row.update(kw)
    return row


def test_column_constants_match_spec():
    assert BIDS_COLUMNS == ["run_id", "project_id", "firm_id", "est", "tag", "dist", "cap", "firm_rate",
                            "bid", "won"]
    assert TRUTH_COLUMNS == ["run_id", "project_id", "firm_id", "seat", "cost", "eps", "nu", "is_cartel",
                             "cartel_role", "scheme"]
    assert MESSAGES_COLUMNS == ["run_id", "project_id", "order", "sender", "recipient", "my_cost",
                                "intended_serious_bid", "text", "reason"]
    assert CALLS_COLUMNS == ["run_id", "project_id", "firm_id", "phase", "attempt", "latency_ms",
                             "input_tokens", "output_tokens", "cache_read_input_tokens",
                             "cache_creation_input_tokens", "stop_reason", "reason",
                             "ratelimit_requests_remaining", "ratelimit_input_tokens_remaining",
                             "ratelimit_output_tokens_remaining"]
    assert PAIR_STATS_COLUMNS == ["run_id", "spec", "rho0", "usable_T", "primary", "firm_i", "firm_j",
                                  "n_joint", "r", "z", "a1_flag", "a1_reason", "F", "F_p", "a3_flag",
                                  "a3_df_num", "rank_u", "not_identified", "complete"]


def test_new_run_dir_never_overwrites(tmp_path):
    a = new_run_dir(tmp_path, "cond", 7, now_utc=NOW)
    b = new_run_dir(tmp_path, "cond", 7, now_utc=NOW)
    c = new_run_dir(tmp_path, "cond", 7, now_utc=NOW)
    assert a.name == "20260102_030405_cond_s7"
    assert b.name == "20260102_030405_cond_s7_1"
    assert c.name == "20260102_030405_cond_s7_2"
    assert a.exists() and b.exists() and c.exists()
    assert len({a, b, c}) == 3


def test_new_run_dir_creates_parents(tmp_path):
    d = new_run_dir(tmp_path / "x" / "y", "c", 1, now_utc=NOW)
    assert d.is_dir()


def test_csv_missing_is_empty_field(tmp_path):
    p = tmp_path / "bids.csv"
    write_csv(p, [_bid_row(bid=None), _bid_row(project_id=2, bid=float("nan"))], BIDS_COLUMNS)
    text = p.read_text(encoding="utf-8")
    assert "nan" not in text.lower() and "None" not in text
    lines = text.splitlines()
    assert lines[0] == ",".join(BIDS_COLUMNS)
    assert lines[1] == "r,1,A,1000000.0,small,3.5,0.25,0.02,,1"
    assert lines[2].endswith(",,1")


def test_csv_float_roundtrip(tmp_path):
    x = 0.1 + 0.2
    p = tmp_path / "f.csv"
    write_csv(p, [_bid_row(bid=x)], BIDS_COLUMNS)
    got = p.read_text(encoding="utf-8").splitlines()[1].split(",")[BIDS_COLUMNS.index("bid")]
    assert float(got) == x


def test_csv_bool_numpy_and_line_endings(tmp_path):
    p = tmp_path / "b.csv"
    write_csv(p, [_bid_row(won=True, project_id=np.int64(4), est=np.float64(2.5))], BIDS_COLUMNS)
    raw = p.read_bytes()
    assert b"\r" not in raw
    assert raw.splitlines()[1] == b"r,4,A,2.5,small,3.5,0.25,0.02,1070000.0,1"


def test_write_csv_missing_column_raises(tmp_path):
    row = _bid_row()
    del row["cap"]
    with pytest.raises(KeyError):
        write_csv(tmp_path / "x.csv", [row], BIDS_COLUMNS)


def test_append_csv_row_creates_header_once(tmp_path):
    p = tmp_path / "bids.csv"
    append_csv_row(p, _bid_row(), BIDS_COLUMNS)
    append_csv_row(p, _bid_row(project_id=2), BIDS_COLUMNS)
    lines = p.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3 and lines[0] == ",".join(BIDS_COLUMNS)


def test_read_bids_types(tmp_path):
    write_csv(tmp_path / "bids.csv", [_bid_row(), _bid_row(project_id=2, bid=None, won=0)], BIDS_COLUMNS)
    df = read_bids(tmp_path)
    assert list(df.columns) == BIDS_COLUMNS
    assert df["project_id"].dtype.kind == "i" and df["won"].dtype.kind == "i"
    assert df["bid"].dtype.kind == "f" and math.isnan(df["bid"].iloc[1])
    assert df["est"].dtype.kind == "f" and df["dist"].dtype.kind == "f"
    assert df["firm_id"].iloc[0] == "A" and df["tag"].iloc[0] == "small"
    assert df["bid"].iloc[0] == 1070000.0


def test_run_meta_roundtrip(tmp_path):
    write_run_meta(tmp_path, {"purpose": "smoke", "seed": 3, "b": [1, 2]})
    assert read_run_meta(tmp_path) == {"purpose": "smoke", "seed": 3, "b": [1, 2]}


def test_run_meta_requires_purpose(tmp_path):
    with pytest.raises(ValueError):
        write_run_meta(tmp_path, {"seed": 1})
    with pytest.raises(ValueError):
        write_run_meta(tmp_path, {"purpose": "bogus"})
    for p in ("experiment", "smoke", "cover_smoke", "pilot"):
        write_run_meta(tmp_path, {"purpose": p})


def test_heartbeat_fields(tmp_path):
    t1 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 1, 1, 0, 5, 0, tzinfo=timezone.utc)
    write_heartbeat(tmp_path, 12, t1, t1)
    hb = json.loads((tmp_path / "heartbeat.json").read_text(encoding="utf-8"))
    assert set(hb) == {"last_completed_project", "last_completed_utc", "last_activity_utc"}
    assert hb["last_completed_project"] == 12
    assert hb["last_completed_utc"] == t1.isoformat()
    touch_activity(tmp_path, now_utc=t2)
    hb2 = json.loads((tmp_path / "heartbeat.json").read_text(encoding="utf-8"))
    assert hb2["last_activity_utc"] == t2.isoformat()
    assert hb2["last_completed_project"] == 12 and hb2["last_completed_utc"] == hb["last_completed_utc"]
    assert not (tmp_path / "heartbeat.json.tmp").exists()


def test_touch_activity_creates_missing(tmp_path):
    touch_activity(tmp_path)
    hb = json.loads((tmp_path / "heartbeat.json").read_text(encoding="utf-8"))
    assert hb["last_completed_project"] == 0 and hb["last_completed_utc"] is None
    assert hb["last_activity_utc"].endswith("+00:00")


def test_touch_activity_is_thread_safe(tmp_path):
    """Six threads touching (as six bidder threads do on every attempt) plus heartbeat writes from the
    loop: no exception, and the file is always a valid, complete heartbeat."""
    write_heartbeat(tmp_path, 7, NOW, NOW)
    errors = []
    start = threading.Barrier(7)

    def toucher():
        start.wait()
        for _ in range(40):
            try:
                touch_activity(tmp_path)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

    def writer():
        start.wait()
        for _ in range(40):
            try:
                write_heartbeat(tmp_path, 7, NOW, datetime.now(timezone.utc))
            except Exception as e:  # noqa: BLE001
                errors.append(e)

    threads = [threading.Thread(target=toucher) for _ in range(6)] + [threading.Thread(target=writer)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert errors == []
    hb = json.loads((tmp_path / "heartbeat.json").read_text(encoding="utf-8"))
    assert hb["last_completed_project"] == 7 and hb["last_completed_utc"] == NOW.isoformat()
    assert not (tmp_path / "heartbeat.json.tmp").exists()


def test_atomic_write_retries_transient_permission_error(tmp_path, monkeypatch):
    real_replace = io_mod.os.replace
    failures = []

    def flaky(src, dst):
        if len(failures) < 3:  # Windows: the target is briefly open in another handle
            failures.append(1)
            raise PermissionError(13, "Access is denied")
        real_replace(src, dst)

    monkeypatch.setattr(io_mod.time, "sleep", lambda s: None)
    monkeypatch.setattr(io_mod.os, "replace", flaky)
    write_heartbeat(tmp_path, 3, NOW, NOW)
    assert len(failures) == 3
    assert json.loads((tmp_path / "heartbeat.json").read_text(encoding="utf-8"))["last_completed_project"] == 3

    def always(src, dst):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(io_mod.os, "replace", always)
    with pytest.raises(PermissionError):  # a persistent failure still surfaces
        write_heartbeat(tmp_path, 4, NOW, NOW)


def test_checkpoint_roundtrip(tmp_path):
    state = {"project": 5, "arr": np.arange(6, dtype=np.int32).reshape(2, 3),
             "nested": {"x": np.array([1.5, 2.5]), "n": np.int64(3), "f": np.float64(0.5),
                        "l": [1, {"a": np.zeros(2)}]},
             "s": "text", "none": None}
    write_checkpoint(tmp_path, state)
    assert not (tmp_path / "checkpoint.json.tmp").exists()
    got = read_checkpoint(tmp_path)
    assert got["project"] == 5 and got["s"] == "text" and got["none"] is None
    assert isinstance(got["arr"], np.ndarray) and got["arr"].dtype == np.int32
    assert got["arr"].shape == (2, 3) and (got["arr"] == state["arr"]).all()
    assert (got["nested"]["x"] == np.array([1.5, 2.5])).all()
    assert got["nested"]["n"] == 3 and got["nested"]["f"] == 0.5
    assert isinstance(got["nested"]["l"][1]["a"], np.ndarray)


def test_checkpoint_rng_state_roundtrip(tmp_path):
    rng = np.random.default_rng(12345)
    rng.random(10)
    state = {"rng": rng.bit_generator.state}
    write_checkpoint(tmp_path, state)
    got = read_checkpoint(tmp_path)
    assert got["rng"] == state["rng"]
    rng2 = np.random.default_rng(0)
    rng2.bit_generator.state = got["rng"]
    assert rng2.random(5).tolist() == rng.random(5).tolist()


def test_transcript_format(tmp_path):
    append_transcript(tmp_path, "firmA", 3, "bid", "the prompt", "the response")
    append_transcript(tmp_path, "firmA", 4, "message", "p2", "r2")
    text = (tmp_path / "transcripts" / "firmA.md").read_text(encoding="utf-8")
    assert "## Prompt: Project 3 (bid) - " in text
    assert "## Response: Project 3 (bid) - " in text
    assert "## Prompt: Project 4 (message) - " in text
    assert "``````\nthe prompt\n``````\n" in text
    assert "````\nthe response\n````\n" in text
    assert text.index("## Prompt: Project 3") < text.index("## Response: Project 3") < text.index("## Prompt: Project 4")
