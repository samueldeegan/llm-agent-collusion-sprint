"""Sealed-bid first-price auction, award ledger, rival covariates and public postings."""
from __future__ import annotations

import numpy as np

from src.procurement.environment import Environment


def resolve_project(bids: dict[int, float | None]) -> tuple[int | None, float | None]:
    """Lowest bid wins and is paid as bid; ties go to the lowest seat; None bids are ignored."""
    live = [(b, s) for s, b in bids.items() if b is not None]
    if not live:
        return None, None
    price, seat = min(live)
    return seat, price


class AwardLedger:
    """Cumulative EST dollars won per seat; CAP = won / K (not capped at 1)."""

    def __init__(self, K: float, N: int):
        self.K = K
        self.won = np.zeros(N)

    def cap(self, seat: int) -> float:
        return float(self.won[seat] / self.K)

    def caps(self) -> np.ndarray:
        return self.won / self.K

    def award(self, seat: int, est: float) -> None:
        self.won[seat] += est


def rival_covariates(dist_t: np.ndarray, caps_t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per seat: nearest rival distance and highest rival remaining-capacity (1 - CAP), over all other seats."""
    N = len(dist_t)
    mdist = np.empty(N)
    maxp = np.empty(N)
    for i in range(N):
        others = [j for j in range(N) if j != i]
        mdist[i] = min(dist_t[j] for j in others)
        maxp[i] = max(1.0 - caps_t[j] for j in others)
    return mdist, maxp


def render_pre_posting(env: Environment, t: int, caps: np.ndarray, names: list[str]) -> str:
    """Public notice shown to every firm before bidding. t is the 0-based project index."""
    lines = [
        f"Project {t + 1}",
        f"EST: ${env.est[t]:,.0f}",
        f"Type: {env.tag_label[t]}",
        f"Season total EST: ${env.season_total:,.0f}",
        f"K: ${env.K:,.0f}",
        "CAP is the EST dollars a firm has won so far divided by K.",
        "",
        f"{'Firm':<24}{'DIST (mi)':>10}{'CAP':>8}{'Firm rate':>11}",
    ]
    for i, name in enumerate(names):
        lines.append(f"{name:<24}{env.dist[i, t]:>10.1f}{caps[i]:>8.3f}{env.eta[i]:>+11.4f}")
    return "\n".join(lines)


def render_post_posting(names: list[str], bids: dict[int, float | None], winner: int | None) -> str:
    """Public result of a project: every firm's bid (or no bid) and the winner."""
    lines = ["Bids received:"]
    for i, name in enumerate(names):
        b = bids.get(i)
        lines.append(f"{name}: {'no bid' if b is None else f'${b:,.2f}'}")
    lines.append("No winner." if winner is None else f"Winner: {names[winner]}")
    return "\n".join(lines)
