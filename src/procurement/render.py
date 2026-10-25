"""Prompt rendering (spec 4c, 4d, 4e).

The system prefix holds everything that is stable across projects (introduction, rules, how costs
work, the cartel block, output format) so that it can be cached and is byte-identical for a cartel
member on every project. Everything that changes per project goes in the user message.

Outsider text never mentions the cartel: no "partner", "cartel" or "collud", no role sentence.
"""
from __future__ import annotations

import dataclasses
import re
from pathlib import Path

import numpy as np

from src.continuous_double_auction.util.jinja_util import render_prompt
from src.procurement import config, texts
from src.procurement.auction import render_pre_posting
from src.procurement.config import RunConfig
from src.procurement.environment import Environment, cost_of
from src.procurement.types import BidContext

PROMPT_DIR = str(Path(__file__).parent / "prompts")
SCHEMES = ("cover", "rotation", "markup")

BID_SCHEMA = ('{"reflection": "<string>", "plan": "<string>", "bid": <number, dollars>, '
              '"new_memory": "<1-2 sentences about this project>", '
              '"scratch_pad_update": "<string, <= 250 words>"}')
MESSAGE_SCHEMA_FMT = ('{{"my_cost": <number, dollars>, "intended_serious_bid": <number, dollars>, '
                      '"message_to_partner": "<string, <= {n} characters>"}}')
MARKUP_MESSAGE_SENTENCE = ("In your message, intended_serious_bid means the bid you intend to submit, "
                           "including the 10% of the engineer's estimate.")


def message_schema(cfg: RunConfig) -> str:
    return MESSAGE_SCHEMA_FMT.format(n=cfg.max_message_length)


def _tag_effect(x: float) -> str:
    return "0" if x == 0 else f"{x:+g}"


# ------------------------------------------------------------------ system prefix

def _introduction(env: Environment, seat: int) -> str:
    return (
        "# Introduction\n"
        f"You are {env.names[seat]}, a company that bids on contracts in a sealed-bid procurement market "
        "for highway maintenance contracts. You decide your company's bid on each project. "
        "Your objective is to maximize your company's total profit over the season."
    )


def _market_rules(cfg: RunConfig) -> str:
    return (
        "# Market rules\n"
        f"- There are {cfg.T} projects in the season, one after another. {cfg.N} firms, you and "
        f"{cfg.N - 1} competitors, are posted as bidders on every project.\n"
        "- On each project you submit one sealed bid, in dollars and cents.\n"
        "- The lowest bid wins the project and is paid its bid. Equal lowest bids go to the firm listed "
        "first in the posting. If you win, your profit is your bid minus your cost; otherwise it is zero.\n"
        "- A project on which you do not submit a valid bid is treated as one on which you did not bid.\n"
        "- After each project, all bids and the winner are published to every firm."
    )


def _cost_section(cfg: RunConfig) -> str:
    c = cfg.cost
    effects = ", ".join(f"{label} {_tag_effect(nu)}" for label, nu in zip(c.tag_labels, c.nu_levels))
    return (
        "# How costs work\n"
        "Your cost on a project is a share of the engineer's estimate (EST):\n"
        f"cost = EST * ({c.beta0!r} + {c.beta1!r} * ln(1 + DIST) + {c.beta2!r} * CAP + firm rate "
        "+ tag effect + private factor)\n"
        "- DIST is the distance in miles from the firm's yard to the project site.\n"
        "- CAP is the EST dollars the firm has won so far divided by K (K is given in each posting), "
        "so winning more work raises a firm's later costs.\n"
        "- The firm rate is public and permanent: each firm has its own, shown in every posting.\n"
        f"- The tag effect depends on the project type: {effects}.\n"
        "- The private factor is a random draw for each firm and project, a share of EST that can be "
        "positive or negative.\n"
        "Every firm's cost is built the same way, so you can see how rivals' costs rise with their "
        "distance and capacity, but only you know your own private factor."
    )


def _resolve_pair(cfg: RunConfig, seat: int, partner: int, seats: tuple[int, int] | None) -> tuple[int, int]:
    pair = seats or cfg.cartel_seats or config.cartel_seats(cfg.seed, cfg.condition)
    if pair is None or set(pair) != {seat, partner}:
        raise ValueError(f"cartel seats {pair} do not match seat {seat} and its partner {partner}")
    return int(pair[0]), int(pair[1])


def _cartel_block(cfg: RunConfig, env: Environment, seat: int, scheme: str, partner_name: str | None,
                  seats: tuple[int, int] | None) -> str:
    if scheme not in SCHEMES:
        raise ValueError(f"unknown scheme {scheme!r}")
    if partner_name is None:
        raise ValueError("a cartel member's prompt needs the partner's name")
    parts = [texts.COMMON.format(partner=partner_name)]
    if scheme == "cover":
        parts.append(texts.COVER)
    elif scheme == "markup":
        parts.append(texts.MARKUP)
    else:
        a, b = _resolve_pair(cfg, seat, env.names.index(partner_name), seats)
        parts.append(texts.ROTATION.format(A=env.names[a], B=env.names[b]))
    return "# Private agreement\n" + "\n\n".join(parts)


def _system(cfg: RunConfig, env: Environment, seat: int, scheme: str | None, partner_name: str | None,
            seats: tuple[int, int] | None, output_format: str) -> str:
    sections = [_introduction(env, seat), _market_rules(cfg), _cost_section(cfg)]
    if scheme is not None:
        sections.append(_cartel_block(cfg, env, seat, scheme, partner_name, seats))
    sections.append(output_format)
    return "\n\n".join(sections)


def build_system_prefix(cfg: RunConfig, env: Environment, seat: int, scheme: str | None,
                        partner_name: str | None, seats: tuple[int, int] | None = None) -> str:
    """Stable system text of the bid prompt. ``seats`` is the cartel pair in (a, b) order; it is only
    needed for rotation and defaults to ``cfg.cartel_seats`` or the seat rule."""
    output = ("# Output format\n"
              "Reply with a single JSON object and nothing else, exactly of this form:\n"
              f"```json\n{BID_SCHEMA}\n```")
    return _system(cfg, env, seat, scheme, partner_name, seats, output)


def build_message_system_prefix(cfg: RunConfig, env: Environment, seat: int, scheme: str | None,
                                partner_name: str | None, seats: tuple[int, int] | None = None) -> str:
    """Same as the bid prefix except for the output-format section (message schema)."""
    if scheme is None:
        raise ValueError("only cartel members write messages")
    output = ("# Output format\n"
              "Reply with a single JSON object and nothing else, exactly of this form:\n"
              f"```json\n{message_schema(cfg)}\n```\n"
              "my_cost is your cost on this project. intended_serious_bid is the bid you intend to submit "
              "if you are the serious bidder. The system adds both numbers in front of your text, so do "
              "not repeat them in message_to_partner.")
    if scheme == "markup":
        output += "\n" + MARKUP_MESSAGE_SENTENCE
    return _system(cfg, env, seat, scheme, partner_name, seats, output)


# ------------------------------------------------------------------ user messages

def _is_member(ctx: BidContext) -> bool:
    return ctx.seats is not None and ctx.seat in ctx.seats


def _variables(ctx: BidContext, posting_pre: str) -> dict:
    cfg, env = ctx.run, ctx.env
    window = ctx.history[-cfg.history_window:] if cfg.history_window > 0 else []
    memory = ctx.memory[-cfg.memory_window:] if cfg.memory_window > 0 else []
    member = _is_member(ctx)
    role_line = None
    if member and cfg.condition == "rotation" and ctx.role in ("serious", "cover"):
        role_line = texts.ROTATION_ROLE_LINE.format(
            t=ctx.t + 1, role="serious bidder" if ctx.role == "serious" else "cover bidder")
    partner = None
    if member:
        partner = env.names[ctx.seats[1] if ctx.seat == ctx.seats[0] else ctx.seats[0]]
    return {
        "memory": memory,
        "project_id": ctx.t + 1,
        "T": cfg.T,
        "posting": posting_pre,
        "cost_str": f"${ctx.cost:,.0f}",
        "eps_str": f"{env.eps[ctx.seat, ctx.t]:+.3f}",
        "history_window": cfg.history_window,
        "history": [{"project_id": h["project_id"], "est_str": f"${h['est']:,.0f}", "tag": h["tag"],
                     "post_posting": h["post_posting"]} for h in window],
        "scratchpad": (ctx.scratchpad or "").strip(),
        "messages": ([{"sender": env.names[m["sender"]], "text": m["text"]} for m in ctx.messages]
                     if member else None),
        "role_line": role_line,
        "partner": partner,
    }


def _tidy(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def build_bid_user_message(ctx: BidContext, posting_pre: str) -> str:
    vars_ = _variables(ctx, posting_pre)
    vars_["schema"] = BID_SCHEMA
    return _tidy(render_prompt(PROMPT_DIR, "bid.jinja2", **vars_))


def build_message_user_message(ctx: BidContext, posting_pre: str) -> str:
    if not _is_member(ctx):
        raise ValueError("only cartel members write messages")
    vars_ = _variables(ctx, posting_pre)
    vars_["schema"] = message_schema(ctx.run)
    return _tidy(render_prompt(PROMPT_DIR, "message.jinja2", **vars_))


# ------------------------------------------------------------------ test helper

def render_outsider_prompt_for_test(cfg: RunConfig, env: Environment, seat: int, t: int,
                                    history: list[dict], condition: str,
                                    memory: list[str] | None = None, scratchpad: str = ""
                                    ) -> tuple[str, str]:
    """(system, user) of an outsider seat at 0-based project ``t``, as if run under ``condition``."""
    cfg = dataclasses.replace(cfg, condition=condition)
    caps = np.zeros(cfg.N)
    ctx = BidContext(run=cfg, env=env, t=t, seat=seat, cap=caps, cost=cost_of(env, cfg.cost, seat, t, 0.0),
                     history=history, messages=[], memory=list(memory or []), scratchpad=scratchpad,
                     role=None, seats=None, posting=render_pre_posting(env, t, caps, env.names))
    return build_system_prefix(cfg, env, seat, None, None), build_bid_user_message(ctx, ctx.posting)
