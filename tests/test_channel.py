import pytest

from src.procurement.channel import Channel
import pandas as pd

from src.procurement.io import MESSAGES_COLUMNS, write_csv
from src.procurement.rules import rotation_roles
from src.procurement.types import MessageResult

ROW_KEYS = [c for c in MESSAGES_COLUMNS if c != "run_id"]


def _result(text="hello", my_cost=100_000.0, intended=120_000.0, reason=""):
    return MessageResult(text=text, my_cost=my_cost, intended_serious_bid=intended, reason=reason,
                         raw=None, call_records=[])


class _Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, seat, messages):
        self.calls.append((seat, [dict(m) for m in messages]))
        return _result(text=f"from {seat}", my_cost=1000.0 * (seat + 1), intended=2000.0 * (seat + 1))


def test_only_cartel_can_message():
    ch = Channel((1, 4), "cover")
    assert ch.can_message(1) is True
    assert ch.can_message(4) is True
    assert ch.can_message(2) is False
    assert ch.can_message(0) is False

    control = Channel(None, "control")
    assert not any(control.can_message(s) for s in range(6))
    rec = _Recorder()
    with pytest.raises(ValueError):
        control.exchange(1, rec)
    assert rec.calls == []


def test_sequential_order_and_delivery():
    ch = Channel((1, 4), "cover")

    rec = _Recorder()
    rows = ch.exchange(3, rec)
    assert [c[0] for c in rec.calls] == [1, 4]
    assert rec.calls[0][1] == []
    assert len(rec.calls[1][1]) == 1
    assert rec.calls[1][1][0]["sender"] == 1
    assert rec.calls[1][1][0]["text"] == rows[0]["text"]

    assert len(rows) == 2
    for r in rows:
        assert list(r.keys()) == ROW_KEYS
    assert [r["order"] for r in rows] == [1, 2]
    assert [r["project_id"] for r in rows] == [3, 3]
    assert (rows[0]["sender"], rows[0]["recipient"]) == (1, 4)
    assert (rows[1]["sender"], rows[1]["recipient"]) == (4, 1)
    assert rows[0]["text"] == "Cost: $2,000. If I am the serious bidder I intend to bid $4,000. from 1"
    assert rows[1]["my_cost"] == 5000.0 and rows[1]["intended_serious_bid"] == 10000.0
    assert rows[0]["reason"] == ""

    rec4 = _Recorder()
    rows4 = ch.exchange(4, rec4)
    assert [c[0] for c in rec4.calls] == [4, 1]
    assert rec4.calls[0][1] == []
    assert rec4.calls[1][1][0]["sender"] == 4
    assert (rows4[0]["sender"], rows4[1]["sender"]) == (4, 1)

    rot = Channel((1, 4), "rotation")
    serious, _ = rotation_roles(3, 1, 4)
    assert rot.first_speaker(3) == serious
    rec_rot = _Recorder()
    rot.exchange(3, rec_rot)
    assert rec_rot.calls[0][0] == serious
    serious4, _ = rotation_roles(4, 1, 4)
    assert rot.first_speaker(4) == serious4 == 4


def test_message_format_and_cap():
    ch = Channel((0, 3), "cover")
    text = ch.deliver(_result(text="x" * 600, my_cost=912345, intended=958000))
    assert text.startswith("Cost: $912,345. If I am the serious bidder I intend to bid $958,000. x")
    assert len(text) == 500

    short = ch.deliver(_result(text="  ok  ", my_cost=912345, intended=958000))
    assert short == "Cost: $912,345. If I am the serious bidder I intend to bid $958,000. ok"

    assert len(Channel((0, 3), "cover", max_len=40).deliver(_result(text="y" * 100))) == 40


def test_message_partial_fields(tmp_path):
    ch = Channel((0, 3), "cover")
    no_cost = _result(text="hello", my_cost=None, intended=958000)
    text = ch.deliver(no_cost)
    assert "Cost:" not in text
    assert text == "If I am the serious bidder I intend to bid $958,000. hello"

    no_intended = ch.deliver(_result(text="hello", my_cost=912345, intended=None))
    assert no_intended == "Cost: $912,345. hello"
    assert ch.deliver(_result(text="hello", my_cost=None, intended=None)) == "hello"
    assert ch.deliver(_result(text="", my_cost=None, intended=None)) == ""

    def send(seat, messages):
        return no_cost if seat == 0 else _result()

    rows = ch.exchange(1, send)
    assert rows[0]["my_cost"] is None
    assert rows[0]["intended_serious_bid"] == 958000
    assert rows[0]["text"] == text

    path = tmp_path / "messages.csv"
    write_csv(path, [{"run_id": "cover_s1", **r} for r in rows], MESSAGES_COLUMNS)
    back = pd.read_csv(path, dtype=str, keep_default_na=False)
    assert list(back.columns) == MESSAGES_COLUMNS
    assert back.loc[0, "my_cost"] == ""
    assert back.loc[0, "intended_serious_bid"] == "958000"
    assert back.loc[0, "text"] == text
    assert back.loc[1, "my_cost"] != ""
