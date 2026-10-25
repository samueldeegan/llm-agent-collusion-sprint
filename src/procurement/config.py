"""Configuration, run plan, cartel seat rule, first-speaker rule and pricing."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

CONDITIONS = ("control", "cover", "markup", "rotation")
_SEAT_K = {"cover": 1, "rotation": 2, "markup": 3}


@dataclass(frozen=True)
class CostParams:
    beta0: float
    beta1: float
    beta2: float
    sigma_eps: float
    k_frac: float = 0.25
    est_min: float = 200_000.0
    est_max: float = 2_000_000.0
    map_size: float = 100.0
    eta_range: float = 0.06
    nu_levels: tuple[float, ...] = (-0.05, 0.0, 0.05, 0.08)
    tag_labels: tuple[str, ...] = ("routine", "standard", "complex", "hazardous")
    honest_margin: float = 1.05
    honest_noise: float = 0.03


@dataclass(frozen=True)
class RunConfig:
    condition: str
    seed: int
    T: int = 110
    burn_in: int = 10
    N: int = 6
    bidder: str = "synthetic"
    cost: CostParams = CostParams(0.90, 0.08, 0.17, 0.05)
    cartel_seats: tuple[int, int] | None = None  # None -> seat rule
    history_window: int = 20
    memory_window: int = 10
    max_message_length: int = 500
    concurrency: int = 6
    model: str = "claude-haiku-5-5"
    max_tokens: int = 8000
    api_key_slot: int = 1
    results_root: str = "results/runs"
    scripted_margin: float = 0.10
    scripted_rule: str = "inflate"
    serious_add: float = 0.0


def cartel_seats(seed: int, condition: str) -> tuple[int, int] | None:
    """Seat pair of the cartel for a (seed, condition); control has none."""
    if condition not in _SEAT_K:
        return None
    a = (seed + _SEAT_K[condition] - 2) % 6
    return (a, (a + 3) % 6)


def first_speaker(project_id: int, condition: str, seats: tuple[int, int]) -> int:
    """Seat that speaks first on a 1-based project: odd -> seats[0], even -> seats[1]
    for cover and markup; under rotation, the scheduled serious bidder (same alternation)."""
    return seats[(project_id - 1) % 2]


def run_plan(R: dict[str, int], include_rotation: bool = True, T: int = 110) -> list[RunConfig]:
    unknown = set(R) - set(CONDITIONS)
    if unknown:
        raise ValueError(f"unknown condition keys in R: {sorted(unknown)}")
    plan: list[RunConfig] = []
    for cond in CONDITIONS:
        if cond == "rotation" and not include_rotation:
            continue
        for seed in range(1, R.get(cond, 0) + 1):
            plan.append(RunConfig(condition=cond, seed=seed, T=T))
    return plan


def load_defaults(path: str = "config/defaults.json") -> dict:
    p = Path(path)
    if not p.is_absolute() and not p.exists():
        p = Path(__file__).resolve().parents[2] / path
    with open(p, encoding="utf-8") as f:
        return json.load(f)


@dataclass(frozen=True)
class Pricing:
    """USD per million tokens."""
    input: float = 0.10
    output: float = 0.50
    cache_creation: float = 0.125
    cache_read: float = 0.01

    def cost_usd(self, input_tokens: int, output_tokens: int,
                 cache_creation: int, cache_read: int) -> float:
        return (input_tokens * self.input + output_tokens * self.output
                + cache_creation * self.cache_creation + cache_read * self.cache_read) / 1e6


def sizing_rule(output_token_limit: int, has_key2: bool) -> dict[str, int]:
    if output_token_limit >= 1_000_000 or has_key2:
        return {"control": 12, "cover": 12, "markup": 4, "rotation": 4}
    return {"control": 8, "cover": 8, "markup": 4, "rotation": 4}
