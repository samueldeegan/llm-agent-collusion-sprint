"""A stand-in for ``HaikuClient`` that never touches the network.

``StubClient.create(system, user)`` has the client's signature and returns canned ``RawResponse``s.
It reads the phase and project from the user text (the message prompt carries the message schema key
``"message_to_partner"``; the bid prompt says "This is project N of T.") and the seat from the firm
name in the system text ("You are <name>, a company ...")."""
from __future__ import annotations

import json
import re
import threading
import time

from src.procurement.client import RawResponse

_PROJECT = re.compile(r"This is project (\d+) of \d+\.")
_COST = re.compile(r"Your cost on this project: \$([\d,]+)")

STUB_MESSAGE_TEXT = "STUB-MSG-7f3a"  # distinctive, so leaks of message text are detectable
DEFAULT_MESSAGE = {"my_cost": 1000000, "intended_serious_bid": 1050000,
                   "message_to_partner": STUB_MESSAGE_TEXT}

TRANSPORT_FAILED = None  # override value: create returns (None, [transport_failed record])
_NO_OVERRIDE = object()


def ok_record(reason: str = "") -> dict:
    return {"attempt": 1, "latency_ms": 3, "input_tokens": 100, "output_tokens": 20,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0, "stop_reason": "end_turn",
            "reason": reason, "ratelimit_requests_remaining": None,
            "ratelimit_input_tokens_remaining": None, "ratelimit_output_tokens_remaining": None}


def failed_record() -> dict:
    rec = ok_record("transport_failed")
    rec.update(input_tokens=None, output_tokens=None, cache_read_input_tokens=None,
               cache_creation_input_tokens=None, stop_reason=None)
    return rec


class StubClient:
    """``replies[(seat, phase, project)]`` overrides one call: a str is the raw reply text,
    None (``TRANSPORT_FAILED``) makes the call missing, an Exception instance is raised. ``delays`` holds
    seconds to sleep inside that call. ``calls`` gets ``(start, end, seat, phase, project)`` per call;
    ``prompts[(seat, phase, project)]`` the ``(system, user)`` pair sent. Like ``HaikuClient``, each call
    (one attempt) runs ``on_attempt`` at its start and end and drops its exceptions."""

    def __init__(self, names: list[str] | None = None, replies: dict | None = None,
                 delays: dict | None = None):
        self.names = names
        self.replies = dict(replies or {})
        self.delays = dict(delays or {})
        self.calls: list[tuple[float, float, int, str, int]] = []
        self.prompts: dict[tuple[int, str, int], tuple[str, str]] = {}
        self.lock = threading.Lock()
        self.on_attempt = None

    def _ping(self) -> None:
        if self.on_attempt is not None:
            try:
                self.on_attempt()
            except Exception:  # noqa: BLE001
                pass

    # -------------------------------------------------------------- decoding the prompt

    def seat_of(self, system: str) -> int:
        hits = [i for i, n in enumerate(self.names) if f"You are {n}, a company" in system]
        assert len(hits) == 1, f"cannot tell the seat from the system text ({hits})"
        return hits[0]

    @staticmethod
    def phase_of(user: str) -> str:
        return "message" if '"message_to_partner"' in user else "bid"

    @staticmethod
    def project_of(user: str) -> int:
        m = _PROJECT.search(user)
        assert m, "no 'This is project N of T.' line in the user text"
        return int(m.group(1))

    @staticmethod
    def cost_of(user: str) -> float:
        m = _COST.search(user)
        assert m, "no cost line in the user text"
        return float(m.group(1).replace(",", ""))

    # -------------------------------------------------------------- replies

    @staticmethod
    def default_bid(cost: float, pid: int) -> dict:
        return {"reflection": "", "plan": "", "bid": round(cost * 1.05, 2),
                "new_memory": f"m{pid}", "scratch_pad_update": f"s{pid}"}

    def _default_text(self, phase: str, user: str, pid: int) -> str:
        if phase == "message":
            return json.dumps(DEFAULT_MESSAGE)
        return json.dumps(self.default_bid(self.cost_of(user), pid))

    def count(self, seat: int, phase: str, project: int) -> int:
        with self.lock:
            return sum(1 for c in self.calls if c[2:] == (seat, phase, project))

    def create(self, system: str, user: str):
        self._ping()
        start = time.perf_counter()
        seat, phase, pid = self.seat_of(system), self.phase_of(user), self.project_of(user)
        key = (seat, phase, pid)
        with self.lock:
            self.prompts[key] = (system, user)
        if key in self.delays:
            time.sleep(self.delays[key])
        reply = self.replies.get(key, _NO_OVERRIDE)
        try:
            if isinstance(reply, BaseException):
                raise reply
            if reply is None:  # TRANSPORT_FAILED
                return None, [failed_record()]
            text = reply if isinstance(reply, str) else self._default_text(phase, user, pid)
            return RawResponse(content=[{"type": "text", "text": text}], stop_reason="end_turn",
                               usage={"input_tokens": 100, "output_tokens": 20}), [ok_record()]
        finally:
            end = time.perf_counter()
            with self.lock:
                self.calls.append((start, end, seat, phase, pid))
            self._ping()
