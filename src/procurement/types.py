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
    prompt: str | None = None  # the prompt sent, "[system]\n...\n\n[user]\n..."; None on the synthetic path


@dataclass
class MessageResult:
    text: str
    my_cost: float | None
    intended_serious_bid: float | None
    reason: str
    raw: str | None
    call_records: list[dict]
    prompt: str | None = None  # as BidResult.prompt


class Bidder(Protocol):
    """One object serving all seats of a run (routes by ``ctx.seat``).

    Optional state hook, deliberately not part of the Protocol: a bidder that carries state across
    projects (e.g. LLM memory) defines BOTH ``get_state() -> JSON-serialisable`` and
    ``set_state(state) -> None``. The runner stores ``get_state()`` in ``checkpoint.json`` under
    ``"bidder_state"`` after every project and calls ``set_state`` on resume. Defining only one of the
    two raises TypeError at run start; resuming a stateful bidder from a checkpoint without
    ``bidder_state`` raises ValueError."""

    def bid(self, ctx: BidContext) -> BidResult: ...

    def message(self, ctx: BidContext) -> MessageResult: ...
