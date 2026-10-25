"""Synthetic (scripted) bidder and the in-loop table simulator."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.procurement.config import CostParams, RunConfig
from src.procurement.environment import Environment, cost_of, generate_environment
from src.procurement.io import BIDS_COLUMNS, TRUTH_COLUMNS
from src.procurement.rules import cover_inflate, cover_shadow, cover_roles, markup_bid, rotation_roles
from src.procurement.types import BidContext, Bidder, BidResult, MessageResult

HONEST_MARGIN = 1.05  # CostParams.honest_margin's default (config/defaults.json cost.honest_margin)


def honest_bid(cost: float, est: float, noise: float, margin: float = HONEST_MARGIN) -> float:
    return round(cost * margin + est * noise, 2)


def scheme_message_text(cost: float, intended: float) -> str:
    return f"Cost: ${cost:,.0f}. If I am the serious bidder I intend to bid ${intended:,.0f}."


def noise_array(seed: int, cost: CostParams, T: int, N: int) -> np.ndarray:
    """noise[seat, t]: the draw made in (project, seat) order."""
    return np.random.default_rng(2000 + seed).normal(0.0, cost.honest_noise, size=(T, N)).T


def _cover_bid(h: float, s: float, est: float, margin: float, rule: str) -> float:
    if rule == "shadow":
        return cover_shadow(s)
    if rule == "inflate":
        return round(cover_inflate(h, s, est, margin), 2)
    raise ValueError(f"unknown cover rule: {rule!r}")


class SyntheticBidder:
    def __init__(self, seed: int, cost: CostParams, T: int = 110, N: int = 6):
        self.seed = seed
        self.cost = cost
        self.noise = noise_array(seed, cost, T, N)

    def _honest(self, ctx: BidContext) -> float:
        return honest_bid(ctx.cost, float(ctx.env.est[ctx.t]), float(self.noise[ctx.seat, ctx.t]),
                          self.cost.honest_margin)

    def _serious(self, ctx: BidContext) -> float:
        est = float(ctx.env.est[ctx.t])
        return round(self._honest(ctx) + ctx.run.serious_add * est, 2)

    def bid(self, ctx: BidContext) -> BidResult:
        est = float(ctx.env.est[ctx.t])
        h = self._honest(ctx)
        cond = ctx.run.condition
        if ctx.seats is None or ctx.seat not in ctx.seats or ctx.role is None:
            b = h
        elif cond == "markup":
            b = round(markup_bid(h, est), 2)
        elif ctx.role == "serious":
            b = self._serious(ctx)
        elif ctx.role == "cover":
            s = None
            # the partner's latest message carrying intended_serious_bid; never its own message
            for m in ctx.messages:
                if m.get("sender") != ctx.seat and m.get("intended_serious_bid") is not None:
                    s = float(m["intended_serious_bid"])
            if s is None:
                b = round(h + ctx.run.scripted_margin * est, 2)
            else:
                b = _cover_bid(h, s, est, ctx.run.scripted_margin, ctx.run.scripted_rule)
        else:
            b = h
        return BidResult(bid=b, reason="", parsed=None, raw=None, call_records=[])

    def message(self, ctx: BidContext) -> MessageResult:
        """No free text: ``Channel.deliver`` renders the two numeric sentences of
        ``scheme_message_text`` from ``my_cost`` and ``intended_serious_bid``."""
        intended = self._serious(ctx)
        return MessageResult(text="", my_cost=ctx.cost, intended_serious_bid=intended, reason="",
                             raw=None, call_records=[])


def synthetic_bidder_factory(cfg: RunConfig, env: Environment) -> Bidder:
    """One SyntheticBidder serving all seats of a run (it routes by ``ctx.seat``)."""
    return SyntheticBidder(cfg.seed, cfg.cost, cfg.T, cfg.N)


def simulate_table(seed: int, cost: CostParams, T: int, scheme: str | None,
                   cartel: tuple[int, int] | None, margin: float = 0.10, rule: str = "inflate",
                   serious_add: float = 0.0, shock: float | None = None,
                   N: int = 6) -> tuple[pd.DataFrame, pd.DataFrame]:
    env = generate_environment(seed, cost, T, N)
    noise = noise_array(seed, cost, T, N)
    shock_rng = np.random.default_rng(10_000 + seed)
    scheme = scheme if cartel is not None else None
    won = np.zeros(N)
    run_id = f"{scheme or 'control'}_s{seed}"
    brow, trow = [], []
    for t in range(T):
        est = float(env.est[t])
        cap = won / env.K
        costs = [cost_of(env, cost, i, t, cap[i]) for i in range(N)]
        bids = [honest_bid(costs[i], est, noise[i, t], cost.honest_margin) for i in range(N)]
        if shock is not None:
            d = shock_rng.normal(0, shock)
            bids[0] = round(bids[0] + d * est, 2)
            bids[1] = round(bids[1] - d * est, 2)
        roles = ["none"] * N
        if scheme is not None:
            a, b = cartel
            if scheme == "markup":
                for i in (a, b):
                    bids[i] = round(markup_bid(bids[i], est), 2)
                    roles[i] = "member"
            else:
                if scheme == "cover":
                    lo, hi = cover_roles(costs[a], costs[b], a, b)
                else:
                    lo, hi = rotation_roles(t + 1, a, b)
                bids[lo] = round(bids[lo] + serious_add * est, 2)
                bids[hi] = _cover_bid(bids[hi], bids[lo], est, margin, rule)
                roles[lo], roles[hi] = "serious", "cover"
        winner = int(np.argmin(bids))
        for i in range(N):
            brow.append((run_id, t + 1, env.names[i], est, env.tag_label[t], float(env.dist[i, t]),
                         float(cap[i]), float(env.eta[i]), bids[i], int(i == winner)))
            trow.append((run_id, t + 1, env.names[i], i, costs[i], float(env.eps[i, t]),
                         float(env.nu[t]), int(roles[i] != "none"), roles[i], scheme or "none"))
        won[winner] += est
    bids_df = pd.DataFrame(brow, columns=BIDS_COLUMNS)
    truth_df = pd.DataFrame(trow, columns=TRUTH_COLUMNS)
    return bids_df, truth_df
