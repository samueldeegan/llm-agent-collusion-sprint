import numpy as np
import pytest

from src.procurement.config import CostParams, RunConfig
from src.procurement.environment import generate_environment, cost_of
from src.procurement.synthetic import (honest_bid, simulate_table, SyntheticBidder,
                                       scheme_message_text)
from src.procurement.types import BidContext

COST = CostParams(0.90, 0.08, 0.17, 0.05)
BID_COLS = ["run_id", "project_id", "firm_id", "est", "tag", "dist", "cap", "firm_rate", "bid", "won"]
TRUTH_COLS = ["run_id", "project_id", "firm_id", "seat", "cost", "eps", "nu", "is_cartel",
              "cartel_role", "scheme"]


def test_honest_bid_formula():
    assert honest_bid(1_000_000, 1_000_000, 0.02) == 1_070_000.0


def test_simulate_table_schema():
    b, t = simulate_table(1, COST, 110, None, None)
    assert list(b.columns) == BID_COLS
    assert list(t.columns) == TRUTH_COLS
    assert len(b) == 660 and len(t) == 660
    assert int(b["won"].sum()) == 110
    assert (b.groupby("project_id")["won"].sum() == 1).all()
    assert set(t["scheme"]) == {"none"} and set(t["cartel_role"]) == {"none"}
    assert b["run_id"].iloc[0] == "control_s1"


def test_serious_add_variant():
    b0, t0 = simulate_table(3, COST, 20, "cover", (0, 3))
    b1, t1 = simulate_table(3, COST, 20, "cover", (0, 3), serious_add=0.05)
    # project 1 (t=0): no history yet, so identical costs/noise in both runs
    s0 = t0[(t0.project_id == 1) & (t0.cartel_role == "serious")].iloc[0]
    s1 = t1[(t1.project_id == 1) & (t1.cartel_role == "serious")].iloc[0]
    assert s0.firm_id == s1.firm_id
    est = b0[b0.project_id == 1].est.iloc[0]
    x0 = b0[(b0.project_id == 1) & (b0.firm_id == s0.firm_id)].bid.iloc[0]
    x1 = b1[(b1.project_id == 1) & (b1.firm_id == s1.firm_id)].bid.iloc[0]
    assert x1 - x0 == pytest.approx(0.05 * est, abs=0.011)


def test_cover_scheme_inflates_partner():
    b, t = simulate_table(3, COST, 20, "cover", (0, 3))
    for p in (1, 2, 3):
        tp = t[t.project_id == p]
        bp = b[b.project_id == p].set_index("firm_id")
        s = bp.loc[tp[tp.cartel_role == "serious"].firm_id.iloc[0], "bid"]
        c = bp.loc[tp[tp.cartel_role == "cover"].firm_id.iloc[0], "bid"]
        assert c >= s + 0.01 * bp.est.iloc[0] - 0.01


def test_honest_bidder_deterministic_and_offline():
    a = simulate_table(5, COST, 110, None, None)[0]
    b = simulate_table(5, COST, 110, None, None)[0]
    assert a.equals(b)


@pytest.mark.slow
def test_honest_bidder_distribution():
    ratios = []
    for seed in range(1, 101):
        b, _ = simulate_table(seed, COST, 110, None, None)
        ratios.append((b.bid / b.est).to_numpy())
    r = np.concatenate(ratios)
    assert 1.32 <= r.mean() <= 1.37
    assert 0.09 <= r.std() <= 0.12


def test_synthetic_bidder_matches_simulate_table_control():
    seed = 4
    env = generate_environment(seed, COST, 110, 6)
    run = RunConfig(condition="control", seed=seed)
    bidder = SyntheticBidder(seed, COST)
    won = np.zeros(6)
    bids = []
    for t in range(12):
        cap = won / env.K
        row = []
        for seat in range(6):
            c = cost_of(env, COST, seat, t, cap[seat])
            ctx = BidContext(run=run, env=env, t=t, seat=seat, cap=cap, cost=c, history=[],
                             messages=[], memory=[], scratchpad="", role=None, seats=None)
            row.append(bidder.bid(ctx).bid)
        won[int(np.argmin(row))] += env.est[t]
        bids.append(row)
    b, _ = simulate_table(seed, COST, 110, None, None)
    assert np.allclose(np.array(bids).reshape(-1), b.bid.to_numpy()[:72])


def test_scheme_message_text():
    assert scheme_message_text(912_345, 958_000) == \
        "Cost: $912,345. If I am the serious bidder I intend to bid $958,000."
