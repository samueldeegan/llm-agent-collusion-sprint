"""Private cartel channel: who may message, speaking order, delivered text, message rows."""
from __future__ import annotations

from typing import Callable

from src.procurement import config
from src.procurement.types import MessageResult


class Channel:
    """The two-member cartel channel. ``seats=None`` (control) is a closed channel."""

    def __init__(self, seats: tuple[int, int] | None, condition: str, max_len: int = 500):
        self.seats = None if seats is None else (int(seats[0]), int(seats[1]))
        self.condition = condition
        self.max_len = max_len

    def can_message(self, seat: int) -> bool:
        return self.seats is not None and seat in self.seats

    def _require_open(self) -> tuple[int, int]:
        if self.seats is None:
            raise ValueError(f"no cartel channel in a {self.condition!r} run")
        return self.seats

    def first_speaker(self, project_id: int) -> int:
        return config.first_speaker(project_id, self.condition, self._require_open())

    def second_speaker(self, project_id: int) -> int:
        a, b = self._require_open()
        return b if self.first_speaker(project_id) == a else a

    def deliver(self, result: MessageResult) -> str:
        """Text the partner receives: the numeric clauses that are present, then the free text."""
        parts = []
        if result.my_cost is not None:
            parts.append(f"Cost: ${result.my_cost:,.0f}.")
        if result.intended_serious_bid is not None:
            parts.append(f"If I am the serious bidder I intend to bid ${result.intended_serious_bid:,.0f}.")
        text = (result.text or "").strip()
        if text:
            parts.append(text)
        return " ".join(parts).strip()[: self.max_len]

    def _row(self, project_id: int, order: int, sender: int, recipient: int,
             result: MessageResult) -> dict:
        return {"project_id": project_id, "order": order, "sender": sender, "recipient": recipient,
                "my_cost": result.my_cost, "intended_serious_bid": result.intended_serious_bid,
                "text": self.deliver(result), "reason": result.reason}

    def exchange(self, project_id: int, send: Callable[[int, list[dict]], MessageResult]) -> list[dict]:
        """Message 1 from the first speaker (no partner message), then message 2 with message 1.

        Rows carry seat ints for sender/recipient; the runner maps them to firm ids and adds run_id."""
        first, second = self.first_speaker(project_id), self.second_speaker(project_id)
        row1 = self._row(project_id, 1, first, second, send(first, []))
        row2 = self._row(project_id, 2, second, first, send(second, [dict(row1)]))
        return [row1, row2]
