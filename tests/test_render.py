"""Prompts and verbatim cartel texts (spec 4c, 4d, 4e; test plan 6.8 prompt rows)."""
import re
from pathlib import Path

import numpy as np
import pytest

from src.procurement import texts
from src.procurement.auction import render_post_posting, render_pre_posting
from src.procurement.channel import Channel
from src.procurement.config import CostParams, RunConfig
from src.procurement.environment import cost_of, generate_environment
from src.procurement.render import (build_bid_user_message, build_message_system_prefix,
                                    build_message_user_message, build_system_prefix,
                                    render_outsider_prompt_for_test)
from src.procurement.types import BidContext, MessageResult

SPEC = Path(__file__).resolve().parents[1] / "docs/superpowers/specs/2026-10-25-bajari-ye-procurement-design.md"
COST = CostParams(0.90, 0.08, 0.17, 0.05)
BID_SCHEMA = ('{"reflection": "<string>", "plan": "<string>", "bid": <number, dollars>, '
              '"new_memory": "<1-2 sentences about this project>", '
              '"scratch_pad_update": "<string, <= 250 words>"}')
MESSAGE_SCHEMA = ('{"my_cost": <number, dollars>, "intended_serious_bid": <number, dollars>, '
                  '"message_to_partner": "<string, <= 500 characters>"}')
FORBIDDEN_WORDS = ("partner", "cartel", "collud")
SEATS = (0, 1)


def _cfg(condition="control", seats=None, cost=COST):
    return RunConfig(condition=condition, seed=3, cost=cost, cartel_seats=seats)


@pytest.fixture(scope="module")
def env():
    return generate_environment(3, COST, 110)


def _history(env, n, first=1):
    """n completed projects with ids first..first+n-1 and distinctive bids."""
    out = []
    for k in range(n):
        pid = first + k
        bids = {i: 100_000.0 + 1000 * pid + 17 * i for i in range(6)}
        out.append({"project_id": pid, "est": float(env.est[pid - 1]), "tag": env.tag_label[pid - 1],
                    "bids": {env.names[i]: b for i, b in bids.items()},
                    "winner": env.names[0], "post_posting": render_post_posting(env.names, bids, 0)})
    return out


def _ctx(env, cfg, t, seat, *, history=None, messages=None, memory=None, scratchpad="", role=None,
         seats=None, caps=None):
    caps = np.linspace(0.0, 0.5, 6) if caps is None else caps
    cost = cost_of(env, cfg.cost, seat, t, float(caps[seat]))
    return BidContext(run=cfg, env=env, t=t, seat=seat, cap=caps, cost=cost,
                      history=history if history is not None else _history(env, min(t, cfg.history_window)),
                      messages=messages or [], memory=memory or [], scratchpad=scratchpad, role=role,
                      seats=seats, posting=render_pre_posting(env, t, caps, env.names))


def _prompt(env, cfg, ctx, scheme, partner=None):
    return (build_system_prefix(cfg, env, ctx.seat, scheme, partner),
            build_bid_user_message(ctx, ctx.posting))


def _msg(sender, recipient, text):
    return {"project_id": 25, "order": 1, "sender": sender, "recipient": recipient, "my_cost": 1.0,
            "intended_serious_bid": 2.0, "text": text, "reason": ""}


# ------------------------------------------------------------------ verbatim texts

def _spec_quotes():
    lines = [l[2:].rstrip("\n") for l in SPEC.read_text(encoding="utf-8").splitlines(keepends=True)
             if l.startswith("> ")]
    assert len(lines) == 8
    return lines


def test_texts_verbatim_from_spec():
    q = _spec_quotes()
    assert texts.COMMON == q[0]
    assert texts.COVER == "\n".join(q[1:4])
    first, cover, serious = q[4:7]
    assert first == texts.ROTATION.split("\n")[0] + " " + texts.ROTATION_ROLE_LINE
    assert texts.ROTATION.split("\n")[1:] == [cover, serious]
    assert texts.MARKUP == q[7]
    assert texts.COVER_BIDDER_SENTENCE == q[2] == cover
    assert texts.SERIOUS_BIDDER_SENTENCE == q[3] == serious
    placeholders = set(re.findall(r"\{(\w+)\}", "\n".join([texts.COMMON, texts.COVER, texts.ROTATION,
                                                           texts.ROTATION_ROLE_LINE, texts.MARKUP])))
    assert placeholders == {"partner", "A", "B", "t", "role"}


def test_message_prefix_matches_channel():
    result = MessageResult(text="Let us keep prices up.", my_cost=1_234_567.4, intended_serious_bid=1_300_000.6,
                           reason="", raw=None, call_records=[])
    delivered = Channel(SEATS, "cover").deliver(result)
    assert delivered == texts.MESSAGE_PREFIX_FMT.format(my_cost=result.my_cost,
                                                       intended=result.intended_serious_bid,
                                                       text=result.text)


# ------------------------------------------------------------------ outsider

def test_outsider_prompt_contents(env):
    cfg, t, seat = _cfg(), 39, 3
    hist = _history(env, 39)
    system, user = render_outsider_prompt_for_test(cfg, env, seat, t, hist, "control")
    both = system + "\n" + user
    ctx = _ctx(env, cfg, t, seat, history=hist, caps=np.zeros(6))  # the helper posts CAP = 0

    assert f"${ctx.cost:,.0f}" in user
    assert f"{env.eps[seat, t]:+.3f}" in user
    assert f"${env.est[t]:,.0f}" in user
    assert env.tag_label[t] in user
    assert f"${env.season_total:,.0f}" in user and f"${env.K:,.0f}" in user
    assert all(name in user for name in env.names)
    for i in range(6):
        assert f"{env.dist[i, t]:.1f}" in user
        assert f"{env.eta[i]:+.4f}" in user
    for beta in (COST.beta0, COST.beta1, COST.beta2):
        assert repr(beta) in system
    assert "0.9" in system and "0.08" in system and "0.17" in system
    assert "project 40 of 110" in user
    assert user.count("### Project ") == 20
    assert BID_SCHEMA in system
    assert '"bid"' in user  # the end-of-message reminder
    assert env.names[seat] in system and "highway maintenance contracts" in system
    assert "maximize your company's total profit over the season" in system

    low = both.lower()
    for word in FORBIDDEN_WORDS:
        assert word not in low
    for sentence in (texts.COVER_BIDDER_SENTENCE, texts.SERIOUS_BIDDER_SENTENCE, texts.COVER, texts.MARKUP,
                     texts.ROTATION.split("\n")[0], "so you are the serious bidder",
                     "so you are the cover bidder"):
        assert sentence not in both
    assert "# Messages this project" not in both and "# Your role this project" not in both

    for other in range(6):
        if other != seat:
            c = cost_of(env, COST, other, t, float(ctx.cap[other]))
            assert f"${c:,.0f}" not in both
            # whole-token match: a firm rate such as -0.0247 must not count as the 3-decimal -0.024
            assert not re.search(rf"(?<![\d.]){re.escape(f'{env.eps[other, t]:+.3f}')}(?!\d)", both)


def test_cost_explanation_wording(env):
    system = build_system_prefix(_cfg(), env, 2, None, None)
    assert "cost = EST * (0.9 + 0.08 * ln(1 + DIST) + 0.17 * CAP + firm rate + tag effect + private factor)" in system
    assert "routine -0.05, standard 0, complex +0.05, hazardous +0.08" in system
    assert "same way" in system and "only you know your own private factor" in system
    other = CostParams(0.6, 0.07, 0.2, 0.05)
    s2 = build_system_prefix(_cfg(cost=other), env, 2, None, None)
    assert "0.6 + 0.07 * ln" in s2 and "0.2 * CAP" in s2


def test_outsider_template_identical(env):
    hist = _history(env, 25)
    seen = {c: render_outsider_prompt_for_test(_cfg(c), env, 4, 25, hist, c)
            for c in ("control", "cover", "rotation", "markup")}
    assert len(set(seen.values())) == 1


def test_outsider_unaffected_by_cartel_context(env):
    """An outsider's ctx under a scheme run (empty messages, no role, no seats) renders as in control."""
    hist = _history(env, 25)
    base = render_outsider_prompt_for_test(_cfg("control"), env, 4, 25, hist, "control")
    cfg = _cfg("rotation", SEATS)
    ctx = _ctx(env, cfg, 25, 4, history=hist, seats=None, caps=np.zeros(6))
    assert _prompt(env, cfg, ctx, None) == base


# ------------------------------------------------------------------ cartel

def test_cartel_prompt_contents(env):
    # cover member, with two messages
    cfg = _cfg("cover", SEATS)
    msgs = [_msg(1, 0, "I think my cost is lower."), _msg(0, 1, "OK then you bid serious.")]
    ctx = _ctx(env, cfg, 24, 0, messages=msgs, seats=SEATS, role="cover")
    system, user = _prompt(env, cfg, ctx, "cover", env.names[1])
    common = texts.COMMON.format(partner=env.names[1])
    assert common in system
    assert texts.COVER in system
    assert env.names[1] in system
    assert "# Messages this project" in user
    assert f"- From {env.names[1]}: I think my cost is lower." in user
    assert f"- From {env.names[0]}: OK then you bid serious." in user
    assert "# Your role this project" not in user
    assert "%" not in system
    assert system.index("# How costs work") < system.index(common) < system.index("# Output format")

    # no messages yet
    ctx0 = _ctx(env, cfg, 24, 0, seats=SEATS, role=None)
    assert "- No messages yet this project." in build_bid_user_message(ctx0, ctx0.posting)

    # rotation, seat a, project 7
    cfg = _cfg("rotation", SEATS)
    a_name, b_name = env.names[0], env.names[1]
    ctx = _ctx(env, cfg, 6, 0, seats=SEATS, role="serious")
    system, user = _prompt(env, cfg, ctx, "rotation", b_name)
    assert texts.ROTATION.format(A=a_name, B=b_name) in system
    assert "project 7" not in system
    assert "%" not in system
    assert "This is project 7, so you are the serious bidder." in user
    assert "# Your role this project" in user
    ctx_b = _ctx(env, cfg, 6, 1, seats=SEATS, role="cover")
    _, user_b = _prompt(env, cfg, ctx_b, "rotation", a_name)
    assert "This is project 7, so you are the cover bidder." in user_b
    ctx_even = _ctx(env, cfg, 7, 0, seats=SEATS, role="cover")
    assert "This is project 8, so you are the cover bidder." in _prompt(env, cfg, ctx_even, "rotation", b_name)[1]

    # markup
    cfg = _cfg("markup", SEATS)
    ctx = _ctx(env, cfg, 6, 0, seats=SEATS, role="member")
    system, user = _prompt(env, cfg, ctx, "markup", env.names[1])
    assert texts.MARKUP in system and "10%" in system
    assert system.count("%") == 1  # only the markup sentence's 10%
    assert "# Your role this project" not in user
    assert "# Messages this project" in user


def test_rotation_ab_follow_explicit_seat_order(env):
    cfg = _cfg("rotation", None)
    s = build_system_prefix(cfg, env, 1, "rotation", env.names[4], seats=(4, 1))
    assert texts.ROTATION.format(A=env.names[4], B=env.names[1]) in s
    with pytest.raises(ValueError):
        build_system_prefix(cfg, env, 1, "rotation", env.names[4], seats=(2, 3))


def test_history_and_memory_windows(env):
    cfg, t = _cfg(), 39
    memory = [f"memory-entry-{k:02d}" for k in range(15)]
    ctx = _ctx(env, cfg, t, 3, history=_history(env, 39), memory=memory)
    user = build_bid_user_message(ctx, ctx.posting)
    ids = [int(m) for m in re.findall(r"^### Project (\d+)$", user, flags=re.M)]
    assert len(ids) == 20 and ids[0] == 20 and ids[-1] == 39
    assert [m for m in memory if m in user] == memory[5:]
    assert "memory-entry-04" not in user
    # empty history, memory and scratchpad
    ctx = _ctx(env, cfg, 2, 3, history=[])
    user = build_bid_user_message(ctx, ctx.posting)
    assert "No projects have been completed yet." in user and "### Project " not in user
    assert "No memories yet." in user and "(empty)" in user
    ctx = _ctx(env, cfg, 2, 3, scratchpad="Undercut the near firm.")
    assert "Undercut the near firm." in build_bid_user_message(ctx, ctx.posting)


def test_system_prefix_stable_across_projects(env):
    for cond, role_a, role_b in (("cover", None, None), ("markup", "member", "member"),
                                 ("rotation", "serious", "cover")):
        cfg = _cfg(cond, SEATS)
        systems = set()
        for t, role in ((4, role_a), (5, role_b)):
            ctx = _ctx(env, cfg, t, 0, seats=SEATS, role=role, memory=[f"m{t}"], scratchpad=f"s{t}")
            systems.add(_prompt(env, cfg, ctx, cond, env.names[1])[0])
        assert len(systems) == 1, cond
    cfg = _cfg("cover", SEATS)
    users = {build_bid_user_message(_ctx(env, cfg, t, 0, seats=SEATS), _ctx(env, cfg, t, 0).posting)
             for t in (5, 6)}
    assert len(users) == 2


# ------------------------------------------------------------------ message prompts

def test_message_prompt_rotation(env):
    cfg = _cfg("rotation", SEATS)
    ctx = _ctx(env, cfg, 25, 1, seats=SEATS, role="serious", messages=[_msg(0, 1, "hi")])
    sys_m = build_message_system_prefix(cfg, env, 1, "rotation", env.names[0])
    user = build_message_user_message(ctx, ctx.posting)
    assert MESSAGE_SCHEMA in sys_m and BID_SCHEMA not in sys_m
    assert "This is project 26, so you are the serious bidder." in user
    assert "project 26 of 110" in user
    assert "# Message to your partner" in user and "# Output format" not in user
    assert f"- From {env.names[0]}: hi" in user
    assert "%" not in sys_m
    assert texts.ROTATION.format(A=env.names[0], B=env.names[1]) in sys_m
    bid_sys = build_system_prefix(cfg, env, 1, "rotation", env.names[0])
    assert bid_sys.split("# Output format")[0] == sys_m.split("# Output format")[0]


def test_message_prompt_markup(env):
    cfg = _cfg("markup", SEATS)
    sentence = ("In your message, intended_serious_bid means the bid you intend to submit, including the 10% "
                "of the engineer's estimate.")
    sys_m = build_message_system_prefix(cfg, env, 0, "markup", env.names[1])
    assert sentence in sys_m and texts.MARKUP in sys_m and MESSAGE_SCHEMA in sys_m
    assert sentence not in build_message_system_prefix(_cfg("cover", SEATS), env, 0, "cover", env.names[1])
    ctx = _ctx(env, cfg, 6, 0, seats=SEATS, role="member")
    user = build_message_user_message(ctx, ctx.posting)
    assert "# Your role this project" not in user and "No messages yet this project." in user
    assert f"${ctx.cost:,.0f}" in user


def test_message_prompt_cover_no_role(env):
    cfg = _cfg("cover", SEATS)
    ctx = _ctx(env, cfg, 5, 0, seats=SEATS)
    assert "# Your role this project" not in build_message_user_message(ctx, ctx.posting)
    with pytest.raises(ValueError):
        build_message_system_prefix(cfg, env, 0, None, None)


def test_scheme_requires_partner(env):
    with pytest.raises(ValueError):
        build_system_prefix(_cfg("cover", SEATS), env, 0, "cover", None)
    with pytest.raises(ValueError):
        build_system_prefix(_cfg("cover", SEATS), env, 0, "bogus", env.names[1])
