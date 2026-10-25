"""Pilot beta0 loop helpers and the calibration.json merge helper (no API: pure functions on frames)."""
import json
import os

import numpy as np
import pandas as pd
import pytest

from src.procurement import calibration, launcher, pilot
from src.procurement.config import CostParams
from src.procurement.synthetic import simulate_table

COST = CostParams(0.90, 0.08, 0.17, 0.05)


# ------------------------------------------------------------------ next_beta0

def test_next_beta0():
    assert pilot.next_beta0(0.90, 1.05) == (0.90, True)
    b, frozen = pilot.next_beta0(0.90, 1.20)
    assert b == pytest.approx(0.75) and frozen is False
    b, frozen = pilot.next_beta0(0.90, 0.95)
    assert b == pytest.approx(1.00) and frozen is False
    # band edges are inside
    assert pilot.next_beta0(0.70, 1.00)[1] and pilot.next_beta0(0.70, 1.10)[1]
    assert not pilot.next_beta0(0.70, 1.1001)[1]


# ------------------------------------------------------------------ pilot_summary

def test_pilot_summary():
    bids, truth = simulate_table(3, COST, 110, None, None)
    bids = bids.copy()
    rng = np.random.default_rng(0)
    drop = rng.choice(len(bids), size=20, replace=False)
    bids.loc[drop, "bid"] = np.nan
    bids.loc[bids["bid"].isna(), "won"] = 0  # a missing bid never wins
    s = pilot.pilot_summary(bids, truth, burn_in=10)
    assert set(s) == {"mean_bid_over_est", "sd_bid_over_est", "winning_mean", "winning_sd", "parse_rate"}
    assert s["parse_rate"] == pytest.approx(bids["bid"].notna().sum() / 660)
    assert s["parse_rate"] == pytest.approx(640 / 660)
    post = bids[(bids["project_id"] > 10) & bids["bid"].notna()]
    ratio = post["bid"] / post["est"]
    assert s["mean_bid_over_est"] == pytest.approx(ratio.mean())
    assert s["sd_bid_over_est"] == pytest.approx(ratio.std(ddof=1))
    win = post[post["won"] == 1]
    assert s["winning_mean"] == pytest.approx((win["bid"] / win["est"]).mean())
    assert s["winning_sd"] == pytest.approx((win["bid"] / win["est"]).std(ddof=1))
    # the honest synthetic bidder at beta0 0.90 sits near 1.35 x EST (spec calibration: 1.345)
    assert 1.25 < s["mean_bid_over_est"] < 1.45


def test_pilot_summary_burn_in_excluded():
    bids, truth = simulate_table(3, COST, 20, None, None)
    bids = bids.copy()
    bids.loc[bids["project_id"] <= 10, "bid"] = bids.loc[bids["project_id"] <= 10, "est"] * 9.0
    s = pilot.pilot_summary(bids, truth, burn_in=10)
    assert s["mean_bid_over_est"] < 2.0  # the x9 bids on projects 1-10 are not in the mean
    assert s["parse_rate"] == 1.0


# ------------------------------------------------------------------ write_frozen

HISTORY = [
    {"beta0": 0.90, "m": 1.35, "sd": 0.10, "winning_mean": 1.25, "winning_sd": 0.08, "parse_rate": 1.0,
     "run_dir": "r1"},
    {"beta0": 0.60, "m": 1.05, "sd": 0.09, "winning_mean": 0.97, "winning_sd": 0.07, "parse_rate": 1.0,
     "run_dir": "r2"},
]


def test_write_frozen_roundtrip(tmp_path):
    path = tmp_path / "frozen.json"
    frozen_cost = CostParams(0.60, 0.08, 0.17, 0.05)
    pilot.write_frozen(path, frozen_cost, HISTORY)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["cost"] == {"beta0": 0.60, "beta1": 0.08, "beta2": 0.17, "sigma_eps": 0.05}
    assert data["pilot_history"] == HISTORY
    assert data["frozen_utc"] and "git_commit" in data
    back = launcher.read_frozen(path, COST)
    assert back == frozen_cost
    # never overwritten (audit 4.4)
    with pytest.raises(FileExistsError):
        pilot.write_frozen(path, COST, HISTORY)
    assert launcher.read_frozen(path, COST).beta0 == 0.60


# ------------------------------------------------------------------ update_calibration

def test_update_calibration_merges(tmp_path, monkeypatch):
    path = tmp_path / "calibration.json"
    calibration.update_calibration(path, "limits", {"1": {"requests": 50, "input_tokens": 1, "output_tokens": 2}})
    calibration.update_calibration(path, "smoke", {"parsed": 18, "x": float("nan")})
    calibration.update_calibration(path, "tier_check_utc", "2026-10-25T00:00:00+00:00")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["limits"]["1"]["requests"] == 50
    assert data["smoke"] == {"parsed": 18, "x": None}  # NaN written as null, never as invalid JSON
    assert data["tier_check_utc"].startswith("2026")
    # a section is replaced, others kept
    calibration.update_calibration(path, "smoke", {"parsed": 17})
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["smoke"] == {"parsed": 17} and "limits" in data and "tier_check_utc" in data
    assert not (tmp_path / "calibration.json.tmp").exists()

    # atomic: a failure in the replace leaves the old file intact
    before = path.read_text(encoding="utf-8")

    def boom(src, dst):
        raise OSError("disk gone")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        calibration.update_calibration(path, "measured", {"mean_latency_s": 1.0})
    assert path.read_text(encoding="utf-8") == before


def test_update_calibration_refuses_corrupt_file(tmp_path):
    path = tmp_path / "calibration.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        calibration.update_calibration(path, "smoke", {})
    assert path.read_text(encoding="utf-8") == "{not json"  # never overwritten


def test_call_stats(tmp_path):
    from src.procurement import io
    rows = []
    base = {c: None for c in io.CALLS_COLUMNS}
    for i, (attempt, lat, inp, out, reason) in enumerate([(1, 2000, 100, 20, ""), (1, 4000, None, None, "http_529"),
                                                          (2, 1000, 300, 40, ""), (1, 6000, 200, 30, "")]):
        rows.append({**base, "run_id": "r", "project_id": 1 + i, "firm_id": "F", "phase": "bid",
                     "attempt": attempt, "latency_ms": lat, "input_tokens": inp, "output_tokens": out,
                     "cache_read_input_tokens": 10 if inp else None, "cache_creation_input_tokens": 0,
                     "reason": reason})
    io.write_csv(tmp_path / "calls.csv", rows, io.CALLS_COLUMNS)
    from src.procurement.config import Pricing
    s = calibration.call_stats(tmp_path, Pricing())
    assert s["n_calls"] == 3                      # attempt-1 rows
    assert s["mean_latency_s"] == pytest.approx(4.0)  # (2 + 4 + 6) / 3, attempt-1 rows only
    assert s["min_latency_s"] == 2.0 and s["max_latency_s"] == 6.0
    assert s["input_tokens"] == 600 and s["output_tokens"] == 90 and s["cache_read_input_tokens"] == 30
    assert s["mean_output_tokens"] == pytest.approx(30.0)  # over responses with usage
    assert s["cost_usd"] == pytest.approx(Pricing().cost_usd(600, 90, 0, 30))
