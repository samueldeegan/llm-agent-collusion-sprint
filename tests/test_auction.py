import numpy as np

from src.procurement.auction import (
    AwardLedger, render_post_posting, render_pre_posting, resolve_project, rival_covariates,
)
from src.procurement.config import CostParams
from src.procurement.environment import generate_environment

COST = CostParams(0.90, 0.08, 0.17, 0.05)


def test_lowest_bid_wins_paid_bid():
    assert resolve_project({0: 1000.0, 1: 900.0, 2: 950.0}) == (1, 900.0)


def test_tie_break():
    bids = {0: 1_000_000.0, 1: None, 2: 940_000.00, 3: 950_000.0, 4: 940_000.00, 5: None}
    assert resolve_project(bids) == (2, 940_000.00)


def test_missing_bid_excluded():
    assert resolve_project({0: None, 1: 500.0, 2: None}) == (1, 500.0)


def test_all_missing():
    assert resolve_project({0: None, 1: None}) == (None, None)


def test_cap_updates_after_award():
    led = AwardLedger(10_000_000.0, 6)
    assert led.cap(3) == 0.0
    led.award(3, 400_000.0)
    assert abs(led.cap(3) - 0.04) < 1e-12
    led.award(3, 20_000_000.0)
    assert abs(led.cap(3) - 2.04) < 1e-12
    assert led.cap(0) == 0.0


def test_cap_uses_past_awards_only():
    K = 10_000_000.0
    led = AwardLedger(K, 6)
    seen = [led.cap(1)]
    led.award(1, 300_000.0)
    seen.append(led.cap(1))
    led.award(1, 500_000.0)
    seen.append(led.cap(1))
    assert seen == [0.0, 300_000.0 / K, 800_000.0 / K]
    assert np.allclose(led.caps(), [0, 800_000.0 / K, 0, 0, 0, 0])
    assert led.won[1] == 800_000.0


def test_rival_covariates():
    dist = np.array([10.0, 20, 30, 40, 50, 60])
    caps = np.array([0.1, 0.5, 0.2, 0.0, 0.3, 0.9])
    mdist, maxp = rival_covariates(dist, caps)
    assert mdist.shape == (6,) and maxp.shape == (6,)
    assert mdist[0] == 20 and mdist[1] == 10
    assert maxp[0] == 1.0 and maxp[3] == 0.9


def test_posting_contents():
    env = generate_environment(1, COST, 110)
    caps = np.array([0.0, 0.1, 0.25, 0.0, 0.5, 1.2])
    text = render_pre_posting(env, 4, caps, env.names)
    assert "Project 5" in text
    assert f"${int(env.est[4]):,}" in text
    assert env.tag_label[4] in text
    assert f"${int(env.season_total):,}" in text and f"${int(env.K):,}" in text
    assert "K" in text
    for n in env.names:
        assert n in text
    low = text.lower()
    assert "cost" not in low and "private" not in low
    assert f"{env.dist[2, 4]:.1f}" in text
    assert "0.250" in text and "1.200" in text
    assert f"{env.eta[0]:+.4f}" in text

    post = render_post_posting(env.names, {0: 123456.789, 1: None, 2: 130000.0, 3: None, 4: None, 5: None}, 0)
    assert "no bid" in post
    assert "$123,456.79" in post
    assert env.names[0] in post
    assert "Winner" in post or "winner" in post
    none_post = render_post_posting(env.names, {i: None for i in range(6)}, None)
    assert "No winner" in none_post


def test_posting_aligns_longest_firm_name():
    """C2: the firm column is wide enough for the longest configured firm name, so every row's DIST
    column starts at the same offset."""
    import json
    from pathlib import Path
    names = json.loads((Path(__file__).resolve().parents[1] / "config" / "firms.json").read_text(encoding="utf-8"))
    longest = max(names, key=len)
    env = generate_environment(1, COST, 10)
    text = render_pre_posting(env, 0, np.zeros(6), env.names)
    rows = text.splitlines()[-7:]
    header, body = rows[0], rows[1:]
    assert header.index("DIST") > len(longest)
    width = header.index("DIST (mi)") + len("DIST (mi)")
    for line, name in zip(body, env.names):
        assert line.startswith(name) and line[len(name):width].strip() != "" and line[len(name)] == " "
        assert len(line) == len(header), line
    assert longest in env.names
