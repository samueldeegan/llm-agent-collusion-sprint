import numpy as np
import pytest

from src.procurement.channel import Channel
from src.procurement.config import CostParams, RunConfig
from src.procurement.environment import generate_environment, cost_of
from src.procurement.synthetic import (honest_bid, simulate_table, SyntheticBidder,
                                       scheme_message_text, noise_array, _cover_bid)
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


# ---- cartel paths ----
SEED = 7
CART = (0, 1)


def _view(scheme, **kw):
    b, t = simulate_table(SEED, COST, 110, scheme, CART, **kw)
    noise = noise_array(SEED, COST, 110, 6)
    rows = []
    for p in range(1, 111):
        bp = b[b.project_id == p].reset_index(drop=True)
        tp = t[t.project_id == p].reset_index(drop=True)
        est = float(bp.est.iloc[0])
        hon = [honest_bid(tp.cost[i], est, noise[i, p - 1]) for i in range(6)]
        rows.append((p, est, bp.bid.to_numpy(), tp, hon))
    return rows


def test_rotation_roles_and_cover_bid():
    saw_cost_override = False
    for p, est, bid, tp, hon in _view("rotation"):
        ser, cov = (0, 1) if p % 2 == 1 else (1, 0)
        assert tp.cartel_role[ser] == "serious" and tp.cartel_role[cov] == "cover"
        if p % 2 == 1 and tp.cost[1] < tp.cost[0]:
            saw_cost_override = True
        assert bid[ser] == hon[ser]
        assert bid[cov] == round(max(hon[cov] + 0.10 * est, bid[ser] + 0.01 * est), 2)
        assert all(bid[i] == hon[i] for i in range(2, 6))
    assert saw_cost_override


def test_cover_roles_exact_inflate_and_lower_cost_serious():
    for p, est, bid, tp, hon in _view("cover"):
        ser, cov = (0, 1) if tp.cost[0] <= tp.cost[1] else (1, 0)
        assert tp.cartel_role[ser] == "serious" and tp.cartel_role[cov] == "cover"
        assert bid[ser] == hon[ser]
        assert bid[cov] == round(max(hon[cov] + 0.10 * est, bid[ser] + 0.01 * est), 2)


def test_markup_members_and_outsiders():
    for p, est, bid, tp, hon in _view("markup"):
        for i in (0, 1):
            assert tp.cartel_role[i] == "member"
            assert bid[i] == round(hon[i] + 0.10 * est, 2)
        assert all(bid[i] == hon[i] for i in range(2, 6))


def test_shadow_rule_cover_bid():
    for p, est, bid, tp, hon in _view("cover", rule="shadow"):
        ser, cov = (0, 1) if tp.cost[0] <= tp.cost[1] else (1, 0)
        assert bid[cov] == round(1.04 * bid[ser], 2)


def test_truth_flags_and_scheme_column():
    for scheme in ("cover", "rotation", "markup"):
        _, t = simulate_table(SEED, COST, 110, scheme, CART)
        assert set(t.scheme) == {scheme}
        assert (t.is_cartel == t.seat.isin(CART).astype(int)).all()
        assert t.is_cartel.sum() == 220
    _, t = simulate_table(SEED, COST, 110, None, None)
    assert t.is_cartel.sum() == 0 and set(t.scheme) == {"none"}


def test_shock_path():
    rng = np.random.default_rng(10_000 + SEED)
    b, t = simulate_table(SEED, COST, 110, None, None, shock=0.05)
    noise = noise_array(SEED, COST, 110, 6)
    for p in range(1, 111):
        d = rng.normal(0, 0.05)
        bp = b[b.project_id == p].reset_index(drop=True)
        tp = t[t.project_id == p].reset_index(drop=True)
        est = float(bp.est.iloc[0])
        h0 = honest_bid(tp.cost[0], est, noise[0, p - 1])
        h1 = honest_bid(tp.cost[1], est, noise[1, p - 1])
        assert bp.bid[0] - h0 == pytest.approx(d * est, abs=0.01)
        assert bp.bid[1] - h1 == pytest.approx(-d * est, abs=0.01)


def test_unknown_cover_rule_raises():
    with pytest.raises(ValueError):
        _cover_bid(1.0, 1.0, 1.0, 0.1, "bogus")
    with pytest.raises(ValueError):
        simulate_table(SEED, COST, 5, "cover", CART, rule="bogus")


def _ctx(role, messages, condition="cover", seat=1, t=2):
    env = generate_environment(SEED, COST, 110, 6)
    run = RunConfig(condition=condition, seed=SEED)
    return BidContext(run=run, env=env, t=t, seat=seat, cap=np.zeros(6), cost=900_000.0,
                      history=[], messages=messages, memory=[], scratchpad="", role=role,
                      seats=CART), env


def test_bidder_cover_uses_partner_message():
    bidder = SyntheticBidder(SEED, COST)
    X = 1_100_000.0
    msg = {"sender": 0, "recipient": 1, "my_cost": 1.0, "intended_serious_bid": X, "text": ""}
    ctx, env = _ctx("cover", [msg])
    est = float(env.est[ctx.t]); h = honest_bid(ctx.cost, est, bidder.noise[1, ctx.t])
    assert X + 0.01 * est > h + 0.10 * est  # floor branch binds
    with_msg = bidder.bid(ctx).bid
    assert with_msg == round(X + 0.01 * est, 2)
    for msgs in ([], [dict(msg, intended_serious_bid=None)]):
        ctx, _ = _ctx("cover", msgs)
        no_msg = bidder.bid(ctx).bid
        assert no_msg == round(h + 0.10 * est, 2)
        assert no_msg != with_msg


def test_cover_floor_binds_with_zero_margin():
    floor_hits = 0
    for p, est, bid, tp, hon in _view("cover", margin=0.0):
        ser, cov = (0, 1) if tp.cost[0] <= tp.cost[1] else (1, 0)
        assert bid[cov] == round(max(hon[cov], bid[ser] + 0.01 * est), 2)
        floor_hits += bid[cov] == round(bid[ser] + 0.01 * est, 2)
    assert floor_hits > 0


def test_bidder_markup_member_and_serious():
    bidder = SyntheticBidder(SEED, COST)
    ctx, env = _ctx("member", [], condition="markup")
    est = float(env.est[ctx.t]); h = honest_bid(ctx.cost, est, bidder.noise[1, ctx.t])
    assert bidder.bid(ctx).bid == round(h + 0.10 * est, 2)
    ctx, _ = _ctx("serious", [], seat=0)
    h0 = honest_bid(ctx.cost, est, bidder.noise[0, ctx.t])
    assert bidder.bid(ctx).bid == h0


def test_bidder_message_text():
    bidder = SyntheticBidder(SEED, COST)
    ctx, env = _ctx("serious", [], seat=0)
    est = float(env.est[ctx.t]); h = honest_bid(ctx.cost, est, bidder.noise[0, ctx.t])
    r = bidder.message(ctx)
    assert r.text == ""  # Channel.deliver adds the two numeric sentences once
    assert r.intended_serious_bid == h and r.my_cost == ctx.cost
    delivered = Channel(CART, "cover").deliver(r)
    assert delivered == f"Cost: ${ctx.cost:,.0f}. If I am the serious bidder I intend to bid ${h:,.0f}."
    assert delivered == scheme_message_text(ctx.cost, h)


def test_bidder_cover_reads_partner_message_not_own():
    bidder = SyntheticBidder(SEED, COST)
    X, Y = 1_100_000.0, 2_500_000.0
    partner = {"sender": 0, "recipient": 1, "my_cost": 1.0, "intended_serious_bid": X, "text": ""}
    own = {"sender": 1, "recipient": 0, "my_cost": 2.0, "intended_serious_bid": Y, "text": ""}
    for msgs in ([partner, own], [own, partner]):
        ctx, env = _ctx("cover", msgs)
        est = float(env.est[ctx.t])
        expected = round(X + 0.01 * est, 2)
        assert bidder.bid(ctx).bid == expected
    assert expected != round(Y + 0.01 * est, 2)
    ctx, env = _ctx("cover", [own])  # only its own message: falls back to h + margin*E
    h = honest_bid(ctx.cost, est, bidder.noise[1, ctx.t])
    assert bidder.bid(ctx).bid == round(h + 0.10 * est, 2)


def test_honest_margin_from_cost_params():
    """C3: the synthetic bidder and the table simulator read cost.honest_margin (defaults.json)."""
    from dataclasses import replace
    from src.procurement.config import RunConfig
    from src.procurement.runner import run_in_memory
    from src.procurement.synthetic import synthetic_bidder_factory
    base = CostParams(0.90, 0.08, 0.17, 0.05)
    wide = replace(base, honest_margin=1.20)
    b0, t0 = simulate_table(1, base, 20, None, None)
    b1, t1 = simulate_table(1, wide, 20, None, None)
    assert (t0["cost"] == t1["cost"]).all()
    assert ((b1["bid"] - b0["bid"]) / t0["cost"]).round(6).between(0.149, 0.151).all()
    bids, truth, _ = run_in_memory(RunConfig("control", 1, T=20, cost=wide), synthetic_bidder_factory)
    assert bids["bid"].tolist() == b1["bid"].tolist()
