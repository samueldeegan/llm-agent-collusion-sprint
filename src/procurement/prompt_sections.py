"""Section headings of a rendered prompt, and the headings an arm's prompt should have."""
from __future__ import annotations

from src.procurement.config import RunConfig


def sections_of(text: str) -> list[str]:
    """The ``# ...`` headings of ``text`` in order (``### Project n`` sub-headings are excluded)."""
    return [line for line in text.splitlines() if line.startswith("# ")]


def expected_sections(cfg: RunConfig, phase: str = "bid", member: bool = False,
                      t: int = 14) -> tuple[list[str], list[str]]:
    """(system headings, user headings) of an outsider/control seat's bid prompt at 0-based project t."""
    if phase != "bid" or member:
        raise NotImplementedError("expected_sections covers the outsider/control bid prompt only")
    system = ["# Introduction", "# Market rules", "# How costs work", "# Output format"]
    user = []
    if cfg.memory_window > 0:
        user.append("# Your memory")
    user += [f"# Project {t + 1} of {cfg.T}", "# Your private information"]
    if cfg.history_window > 0:
        user.append(f"# Last {cfg.history_window} projects")
    if cfg.scratchpad_enabled:
        user.append("# Strategy scratchpad")
    user.append("# Reply format")
    return system, user
