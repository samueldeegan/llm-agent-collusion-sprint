"""Section headings of a rendered prompt, and the headings an arm's prompt should have."""
from __future__ import annotations

import json
import re
from pathlib import Path

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


def _bid_prompt_text(run_dir, firm: str, project_id: int) -> str | None:
    """The first logged bid prompt of ``firm`` for ``project_id`` (layout of ``io.append_transcript``)."""
    path = Path(run_dir) / "transcripts" / f"{firm}.md"
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    i = text.find(f"## Prompt: Project {project_id} (bid) - ")
    if i < 0:
        return None
    start = text.find("``````\n", i)
    end = text.find("\n``````\n", start + 7)
    if start < 0 or end < 0:
        return None
    return text[start + 7:end]


def _shown_cost_section(cfg: RunConfig) -> str:
    """The cost section the shown prompt renders for ``cfg`` (what a hidden prompt must not contain)."""
    from dataclasses import replace

    from src.procurement import render
    return render._cost_section(replace(cfg, bidder_info="shown"))


def check_prompt_text(text: str, cfg: RunConfig, seat: int = 0, project_id: int = 2) -> list[str]:
    """Violated assertions (empty = pass) of one logged bid prompt ``[system]\n...\n\n[user]\n...``."""
    if not text.startswith("[system]\n") or "\n\n[user]\n" not in text:
        return ["prompt lacks the [system] / [user] layout"]
    out: list[str] = []
    system, user = text[len("[system]\n"):].split("\n\n[user]\n", 1)
    want_sys, want_user = expected_sections(cfg, "bid", t=project_id - 1)
    if sections_of(system) != want_sys:
        out.append(f"system sections {sections_of(system)} != expected {want_sys}")
    if sections_of(user) != want_user:
        out.append(f"user sections {sections_of(user)} != expected {want_user}")
    if not cfg.scratchpad_enabled and "scratch_pad_update" in system:
        out.append("scratch_pad_update present in the system text with the scratchpad off")
    if cfg.memory_window == 0 and "new_memory" in text:
        out.append("new_memory present with memory_window 0")
    if cfg.bidder_info == "hidden":
        from src.procurement.environment import generate_environment
        env = generate_environment(cfg.seed, cfg.cost, cfg.T, cfg.N)
        shown = _shown_cost_section(cfg)
        formula = next((ln for ln in shown.splitlines() if ln.startswith("cost = EST * (")), None)
        if formula and formula in text:
            out.append("hidden: the shown cost formula line (beta0/beta1/beta2) appears in the prompt")
        marker = "The tag effect depends on the project type: "
        if marker in shown:
            clause = shown.split(marker, 1)[1].splitlines()[0]
            if clause and clause in text:
                out.append("hidden: the shown tag-effect clause (nu levels) appears in the prompt")
        if "built the same way" in text:
            out.append("hidden: 'built the same way' appears in the prompt")
        own = f"{env.eta[seat]:+.4f}"
        for i in range(cfg.N):
            tok = f"{env.eta[i]:+.4f}"
            if i != seat and tok != own and re.search(rf"(?<![\d.]){re.escape(tok)}(?!\d)", text):
                out.append(f"hidden: rival seat {i}'s firm rate {tok} appears in the prompt")
        if own not in text:
            out.append(f"hidden: own firm rate {own} is missing from the prompt")
        if "Firm rate" in text:
            out.append("hidden: 'Firm rate' column appears in the prompt")
    return out


def check_logged_prompt(run_dir, cfg: RunConfig, seat: int = 0, project_id: int = 2) -> list[str]:
    """Violated assertions (empty = pass) of seat ``seat``'s logged bid prompt for ``project_id``
    (1-based, as in the transcript heading): section lists equal ``expected_sections(cfg, "bid",
    t=project_id - 1)``, plus the hidden-arm, scratchpad and memory token checks."""
    meta = json.loads((Path(run_dir) / "run_meta.json").read_text(encoding="utf-8"))
    firm = meta["seat_to_firm"][str(seat)]
    text = _bid_prompt_text(run_dir, firm, project_id)
    if text is None:
        return [f"no logged bid prompt for seat {seat} ({firm}), project {project_id}"]
    return check_prompt_text(text, cfg, seat, project_id)
