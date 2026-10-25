"""Shared dataclasses and the Bidder protocol."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np

from src.procurement.config import RunConfig
from src.procurement.environment import Environment


@dataclass
class BidContext:
    run: RunConfig
    env: Environment
    t: int
    seat: int
    cap: np.ndarray
    cost: float
    history: list[dict]
    messages: list[dict]
    memory: list[str]
    scratchpad: str
    role: str | None
    seats: tuple[int, int] | None
    posting: str = ""  # rendered pre-bid public posting for project t


@dataclass
class BidResult:
    bid: float | None
    reason: str
    parsed: dict | None
    raw: str | None
    call_records: list[dict]


@dataclass
class MessageResult:
    text: str
    my_cost: float | None
    intended_serious_bid: float | None
    reason: str
    raw: str | None
    call_records: list[dict]


class Bidder(Protocol):
    def bid(self, ctx: BidContext) -> BidResult: ...

    def message(self, ctx: BidContext) -> MessageResult: ...
