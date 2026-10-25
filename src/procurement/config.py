"""Configuration, run plan, cartel seat rule, first-speaker rule and pricing."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
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
    scratchpad_enabled: bool = True
    arm: str = "base"
    bidder_info: str = "shown"

    def __post_init__(self):
        if self.bidder_info not in ("shown", "hidden"):
            raise ValueError(f"bidder_info must be 'shown' or 'hidden', got {self.bidder_info!r}")

    def to_dict(self) -> dict:
        """Every field as plain JSON types; ``cost`` as a dict of ``CostParams`` fields."""
        d = asdict(self)
        d["cost"] = {k: list(v) if isinstance(v, tuple) else v for k, v in d["cost"].items()}
        if d["cartel_seats"] is not None:
            d["cartel_seats"] = list(d["cartel_seats"])
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "RunConfig":
        """Inverse of ``to_dict``. Unknown keys raise ValueError; missing ones take their defaults."""
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"unknown RunConfig keys: {sorted(unknown)}")
        d = dict(data)
        if "cost" in d and not isinstance(d["cost"], CostParams):
            cost = dict(d["cost"])
            unknown = set(cost) - {f.name for f in fields(CostParams)}
            if unknown:
                raise ValueError(f"unknown CostParams keys: {sorted(unknown)}")
            for k in ("nu_levels", "tag_labels"):
                if k in cost:
                    cost[k] = tuple(cost[k])
            d["cost"] = CostParams(**cost)
        if d.get("cartel_seats") is not None:
            d["cartel_seats"] = tuple(int(x) for x in d["cartel_seats"])
        return cls(**d)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> "RunConfig":
        return cls.from_dict(json.loads(text))


def run_id_of(condition: str, seed: int, arm: str = "base") -> str:
    """Run identity: ``<condition>_s<seed>`` for the base arm, ``<condition>_<arm>_s<seed>`` otherwise."""
    return f"{condition}_s{seed}" if arm == "base" else f"{condition}_{arm}_s{seed}"


def arm_of_meta(meta: dict) -> str:
    """Arm of a run_meta dict; round-1 metas without one are "base"."""
    return meta.get("arm") or "base"


def run_id_from_meta(meta: dict) -> str:
    return run_id_of(meta["condition"], meta["seed"], arm_of_meta(meta))


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


LADDER_T = 80  # time-ladder rung 2 caps T at this (audit Part 4.2)

# RunConfig fields every run takes from config/defaults.json (besides ``cost`` and ``T``): the single
# settings source of the launcher and the hand-run scripts
DEFAULTS_RUN_FIELDS = ("N", "burn_in", "model", "max_tokens", "history_window", "memory_window",
                       "max_message_length", "concurrency", "scratchpad_enabled")


def cost_from_defaults(defaults: dict) -> CostParams:
    """``CostParams`` from ``defaults["cost"]`` (lists become tuples)."""
    return CostParams(**{k: tuple(v) if isinstance(v, list) else v for k, v in defaults["cost"].items()})


def run_config_from_defaults(defaults: dict, *, condition: str, seed: int, T: int | None = None,
                             cost: CostParams | None = None, **overrides) -> RunConfig:
    """The one way a run config is built from ``config/defaults.json``: N, burn_in, model, max_tokens,
    the history and memory windows, the message cap and concurrency from ``defaults``; ``T`` (default
    ``defaults["T"]``); cost from ``cost`` or ``defaults["cost"]``; then ``overrides`` (any RunConfig
    field, e.g. a script's burn_in or bidder)."""
    settings = {k: defaults[k] for k in DEFAULTS_RUN_FIELDS if k in defaults}
    settings["T"] = int(T if T is not None else defaults.get("T", 110))
    settings.update(overrides)
    return RunConfig(condition=condition, seed=seed,
                     cost=cost if cost is not None else cost_from_defaults(defaults), **settings)


def arm_plan(plan_file: dict, arms: list[str], T: int, *, defaults: dict,
             cost: CostParams | None = None, **overrides) -> list[RunConfig]:
    """Configs of the ablation arms named in ``arms`` (in that order) from the ``arms`` entries of
    ``config/run_plan.json``: seeds 1..n per condition of the entry's ``R``; the entry's other fields
    (windows, scratchpad_enabled, bidder_info) are RunConfig overrides. An unknown arm is a ValueError."""
    entries = {e["arm"]: e for e in plan_file.get("arms", [])}
    unknown = [a for a in arms if a not in entries]
    if unknown:
        raise ValueError(f"unknown arm(s) {unknown}; the plan has {sorted(entries)}")
    plan: list[RunConfig] = []
    for arm in arms:
        entry = entries[arm]
        fields = {k: v for k, v in entry.items() if k not in ("arm", "R")}
        for cond, n in entry["R"].items():
            for seed in range(1, int(n) + 1):
                plan.append(run_config_from_defaults(defaults, condition=cond, seed=seed, T=T, cost=cost,
                                                     arm=arm, **{**fields, **overrides}))
    return plan


def run_plan(R: dict[str, int], include_rotation: bool = True, T: int = 110, *,
             defaults: dict | None = None, cost: CostParams | None = None, **overrides) -> list[RunConfig]:
    """One config per (condition, seed), seeds 1..R[condition]. With ``defaults`` every config is built by
    ``run_config_from_defaults`` (the launcher); without, from the RunConfig defaults."""
    unknown = set(R) - set(CONDITIONS)
    if unknown:
        raise ValueError(f"unknown condition keys in R: {sorted(unknown)}")
    plan: list[RunConfig] = []
    for cond in CONDITIONS:
        if cond == "rotation" and not include_rotation:
            continue
        for seed in range(1, R.get(cond, 0) + 1):
            if defaults is not None:
                plan.append(run_config_from_defaults(defaults, condition=cond, seed=seed, T=T, cost=cost,
                                                     **overrides))
            else:
                kw = dict(overrides)
                if cost is not None:
                    kw["cost"] = cost
                plan.append(RunConfig(condition=cond, seed=seed, T=T, **kw))
    return plan


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def repo_path(value) -> Path:
    """An absolute path; a relative one is taken relative to the repository root."""
    p = Path(value)
    return p if p.is_absolute() else repo_root() / p


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
