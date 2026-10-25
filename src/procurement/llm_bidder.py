"""The LLM bidder (spec 4d): one ``HaikuClient`` call per bid and per message, no re-prompt.

``llm_bidder_factory`` returns one object that serves all seats of a run, routing by ``ctx.seat`` to a
per-seat ``LLMBidder``. Each seat keeps its own memory (by project id) and strategy scratchpad and
builds its two system prefixes once, so the cached prefix is byte-stable across projects. Up to six
seats run on the runner's threads at once, but each seat has at most one call in flight (a member's
message returns before its bid is issued, and project t+1 starts after every bid of project t), so a
seat's state is only ever touched by one thread at a time. The seat map is read-only after
construction, and the shared client keeps no per-call state.

The bidder never writes files: the prompt goes back in ``BidResult.prompt`` / ``MessageResult.prompt``
and the runner writes transcripts and ``calls.csv``."""
from __future__ import annotations

import copy
import dataclasses

from src.procurement.client import HaikuClient, RawResponse, parse_bid, parse_message, with_content_reason
from src.procurement.config import RunConfig
from src.procurement.environment import Environment
from src.procurement.render import (build_bid_user_message, build_message_system_prefix,
                                    build_message_user_message, build_system_prefix)
from src.procurement.runner import scheme_of
from src.procurement.types import BidContext, Bidder, BidResult, MessageResult


def _raw_text(resp: RawResponse | None) -> str | None:
    """The first text block's text, ``""`` when there is none, None when the call is missing."""
    if resp is None:
        return None
    for block in resp.content:
        if block.get("type") == "text":
            return block.get("text") or ""
    return ""


def _prompt(system: str, user: str) -> str:
    return f"[system]\n{system}\n\n[user]\n{user}"


def _settle(resp: RawResponse | None, records: list[dict], parse_reason: str) -> tuple[str, list[dict]]:
    """The call's reason and records: a missing call keeps the client's last reason (transport_failed,
    http_<code>, ...); a content failure is written onto the last record."""
    if resp is None:
        return (records[-1].get("reason") or "transport_failed") if records else "transport_failed", records
    if parse_reason:
        return parse_reason, with_content_reason(records, parse_reason)
    return "", records


class LLMBidder:
    """One seat's LLM bidder."""

    def __init__(self, cfg: RunConfig, env: Environment, seat: int, client: HaikuClient):
        self.cfg, self.env, self.seat, self.client = cfg, env, seat, client
        self.memories: dict[int, str] = {}  # project id -> new_memory
        self.scratchpad = ""
        self._bid_system: str | None = None
        self._message_system: str | None = None
        self._prefix_key: tuple | None = None  # (scheme, partner name, seats) the prefixes were built for

    # -------------------------------------------------------------- state

    def get_state(self) -> dict:
        return {"memories": {str(pid): text for pid, text in sorted(self.memories.items())},
                "scratchpad": self.scratchpad}

    def set_state(self, state: dict) -> None:
        self.memories = {int(pid): str(text) for pid, text in state["memories"].items()}
        self.scratchpad = str(state["scratchpad"])

    def memory_lines(self) -> list[str]:
        """The last ``memory_window`` memories by project id, as rendered under ``# Your memory``."""
        if self.cfg.memory_window <= 0:
            return []
        pids = sorted(self.memories)[-self.cfg.memory_window:]
        return [f"Project {pid}: {self.memories[pid]}" for pid in pids]

    # -------------------------------------------------------------- prompts

    def _cartel(self, ctx: BidContext) -> tuple[str | None, str | None]:
        """(scheme, partner name) for a cartel member; (None, None) for outsiders and control."""
        if ctx.seats is None or ctx.seat not in ctx.seats:
            return None, None
        partner = ctx.seats[1] if ctx.seat == ctx.seats[0] else ctx.seats[0]
        return scheme_of(self.cfg), self.env.names[partner]

    def _system(self, ctx: BidContext, phase: str) -> str:
        scheme, partner = self._cartel(ctx)
        key = (scheme, partner, ctx.seats)
        if self._prefix_key is None:
            self._prefix_key = key
        elif key != self._prefix_key:  # the cached prefixes were built for another cartel setup
            raise ValueError(f"seat {self.seat}: prefixes cached for (scheme, partner, seats) "
                             f"{self._prefix_key!r}, got {key!r}")
        if phase == "bid":
            if self._bid_system is None:
                self._bid_system = build_system_prefix(self.cfg, self.env, self.seat, scheme, partner,
                                                       seats=ctx.seats)
            return self._bid_system
        if self._message_system is None:
            self._message_system = build_message_system_prefix(self.cfg, self.env, self.seat, scheme, partner,
                                                               seats=ctx.seats)
        return self._message_system

    def _with_memory(self, ctx: BidContext) -> BidContext:
        if ctx.seat != self.seat:
            raise ValueError(f"LLMBidder for seat {self.seat} got a context for seat {ctx.seat}")
        pad = self.scratchpad if self.cfg.scratchpad_enabled else ""
        return dataclasses.replace(ctx, memory=self.memory_lines(), scratchpad=pad)

    # -------------------------------------------------------------- calls

    def bid(self, ctx: BidContext) -> BidResult:
        ctx = self._with_memory(ctx)
        system, user = self._system(ctx, "bid"), build_bid_user_message(ctx, ctx.posting)
        resp, records = self.client.create(system, user)  # FatalAPIError propagates
        bid, parse_reason, parsed = parse_bid(resp)
        reason, records = _settle(resp, records, parse_reason)
        if parsed is not None:  # the reply parsed as JSON, even if the bid was bad
            memory = parsed.get("new_memory")
            if self.cfg.memory_window > 0 and isinstance(memory, str) and memory.strip():
                self.memories[ctx.t + 1] = memory
            pad = parsed.get("scratch_pad_update")
            if self.cfg.scratchpad_enabled and isinstance(pad, str) and pad.strip():
                self.scratchpad = pad
        return BidResult(bid=bid, reason=reason, parsed=copy.deepcopy(parsed), raw=_raw_text(resp),
                         call_records=records, prompt=_prompt(system, user))

    def message(self, ctx: BidContext) -> MessageResult:
        """The member's one message this project; a malformed reply gives empty fields (the channel
        then delivers ``""``). Never updates memory or scratchpad."""
        ctx = self._with_memory(ctx)
        system, user = self._system(ctx, "message"), build_message_user_message(ctx, ctx.posting)
        resp, records = self.client.create(system, user)
        parsed, parse_reason = parse_message(resp)
        reason, records = _settle(resp, records, parse_reason)
        return MessageResult(text=parsed["message_to_partner"], my_cost=parsed["my_cost"],
                             intended_serious_bid=parsed["intended_serious_bid"], reason=reason,
                             raw=_raw_text(resp), call_records=records, prompt=_prompt(system, user))


class LLMBidders:
    """All seats of one run behind the ``Bidder`` interface, sharing one client."""

    def __init__(self, cfg: RunConfig, env: Environment, client: HaikuClient):
        self.client = client
        self.seats = {seat: LLMBidder(cfg, env, seat, client) for seat in range(cfg.N)}

    def bid(self, ctx: BidContext) -> BidResult:
        return self.seats[ctx.seat].bid(ctx)

    def message(self, ctx: BidContext) -> MessageResult:
        return self.seats[ctx.seat].message(ctx)

    def set_activity_hook(self, callback) -> None:
        """The runner's activity hook (A52): the client calls it at the start and end of every attempt."""
        self.client.on_attempt = callback

    def get_state(self) -> dict:
        return {str(seat): b.get_state() for seat, b in self.seats.items()}

    def set_state(self, state: dict) -> None:
        if set(state) != {str(seat) for seat in self.seats}:
            raise ValueError(f"bidder_state seats {sorted(state)} do not match the run's {len(self.seats)} seats")
        for seat, b in self.seats.items():
            b.set_state(state[str(seat)])


def llm_bidder_factory(cfg: RunConfig, env: Environment, client: HaikuClient | None = None) -> Bidder:
    """The run's LLM bidder: one ``HaikuClient`` (model, max_tokens and key slot from ``cfg``) shared by
    six per-seat bidders. ``client`` replaces it (tests inject a stub). The runner installs its
    activity callback with ``set_activity_hook`` once the run directory exists."""
    if client is None:
        client = HaikuClient(cfg.model, cfg.max_tokens, cfg.api_key_slot)
    return LLMBidders(cfg, env, client)
