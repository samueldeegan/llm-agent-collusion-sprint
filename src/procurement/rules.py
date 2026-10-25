"""Scripted cartel rules."""
from __future__ import annotations


def cover_inflate(h: float, s: float, est: float, margin: float = 0.10) -> float:
    return max(h + margin * est, s + 0.01 * est)


def cover_shadow(s: float) -> float:
    return round(1.04 * s, 2)


def markup_bid(b: float, est: float, pct: float = 0.10) -> float:
    return b + pct * est


def cover_roles(cost_a: float, cost_b: float, seat_a: int, seat_b: int) -> tuple[int, int]:
    """(serious, cover): lower cost is serious; tie -> seat_a serious."""
    return (seat_a, seat_b) if cost_a <= cost_b else (seat_b, seat_a)


def rotation_roles(project_id: int, seat_a: int, seat_b: int) -> tuple[int, int]:
    """(serious, cover) on a 1-based project: odd -> a serious."""
    return (seat_a, seat_b) if project_id % 2 == 1 else (seat_b, seat_a)
