"""The pilot's β₀ loop (spec A47; step-4 audit Part 1.2 item 2, Part 4.2 step 6): the pilot summary,
the β₀ update rule and ``config/frozen.json``. ``scripts/pilot.py`` drives the loop.

m is the mean bid/EST over the present bids of the post-burn-in projects (11-20 of a 20-project control
run). m in [1.00, 1.10] freezes β₀; otherwise β₀ <- β₀ - (m - 1.05) and the pilot reruns, at most three
times, after which the pilot whose m is closest to 1.05 is frozen. Only β₀ is tuned: β₁, β₂ and σ_ε are
written unchanged. The SD of bid/EST is reported against B&Y Table 5's all-bids row and never targeted;
the winning-bid row is a secondary check."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.procurement import io
from src.procurement.config import CostParams
from src.procurement.runner import _git_commit

BAND = (1.00, 1.10)
TARGET = 1.05
STOP_BAND = (0.95, 1.15)  # audit 4.3 condition 3: the frozen m outside this stops the pipeline
MAX_PILOTS = 3
PARSE_FLOOR = 0.95  # audit Part 3 item 15: block the runs below this parse rate
# B&Y Table 5, p. 980: bid/EST mean and SD (all bids; winning bids)
COMPARATOR_ALL = {"mean": 1.0819, "sd": 0.1837}
COMPARATOR_WINNING = {"mean": 1.0031, "sd": 0.1573}
FROZEN_COST_KEYS = ("beta0", "beta1", "beta2", "sigma_eps")


def next_beta0(beta0: float, m: float, lo: float = BAND[0], hi: float = BAND[1],
               target: float = TARGET) -> tuple[float, bool]:
    """``(beta0, True)`` when m is in [lo, hi] (freeze); else ``(beta0 - (m - target), False)``."""
    if lo <= m <= hi:
        return beta0, True
    return beta0 - (m - target), False


def _mean_sd(ratio: pd.Series) -> tuple[float, float]:
    n = len(ratio)
    mean = float(ratio.mean()) if n else float("nan")
    sd = float(ratio.std(ddof=1)) if n > 1 else float("nan")
    return mean, sd


def pilot_summary(bids_df: pd.DataFrame, truth_df: pd.DataFrame | None = None, burn_in: int = 10) -> dict:
    """``mean_bid_over_est`` / ``sd_bid_over_est`` (sample SD) over the present bids on projects after
    ``burn_in``; ``winning_mean`` / ``winning_sd`` over the winning bids on the same projects;
    ``parse_rate`` = present bids / all bid rows over every project (burn-in included). bid/EST needs
    the bid table only; ``truth_df`` is accepted so callers pass a run's two frames together and is
    not read."""
    present = bids_df["bid"].notna()
    post = bids_df[(bids_df["project_id"] > burn_in) & present]
    ratio = post["bid"] / post["est"]
    win = post[post["won"] == 1]
    mean, sd = _mean_sd(ratio)
    wmean, wsd = _mean_sd(win["bid"] / win["est"])
    return {"mean_bid_over_est": mean, "sd_bid_over_est": sd, "winning_mean": wmean, "winning_sd": wsd,
            "parse_rate": float(present.sum()) / len(bids_df) if len(bids_df) else float("nan")}


def closest(history: list[dict], target: float = TARGET) -> int:
    """Index of the pilot whose m is closest to ``target`` (the earliest on a tie)."""
    return min(range(len(history)), key=lambda i: (abs(history[i]["m"] - target), i))


def write_frozen(path, cost: CostParams, pilot_history: list[dict]) -> Path:
    """Write ``config/frozen.json``: ``{"cost": {beta0, beta1, beta2, sigma_eps}, "pilot_history": [...],
    "frozen_utc", "git_commit"}`` (read by ``launcher.read_frozen``). An existing file is never
    overwritten (audit 4.4: no cost-parameter change after the freeze): FileExistsError."""
    path = Path(path)
    if path.exists():
        raise FileExistsError(f"{path} exists; the frozen parameters are never overwritten")
    data = {"cost": {k: float(getattr(cost, k)) for k in FROZEN_COST_KEYS},
            "pilot_history": list(pilot_history),
            "frozen_utc": datetime.now(timezone.utc).isoformat(),
            "git_commit": _git_commit()}
    path.parent.mkdir(parents=True, exist_ok=True)
    io._atomic_write_text(path, json.dumps(data, indent=2, allow_nan=False) + "\n")
    return path


def append_decision(path, decision: str, reason: str, reversible: str, now: datetime | None = None) -> None:
    """Append ``- <UTC yyyy-mm-dd HH:MM> | decision | reason | reversible`` to the decisions log."""
    now = now or datetime.now(timezone.utc)
    path = Path(path)
    lead = ""
    if path.is_file() and path.stat().st_size:
        with open(path, "rb") as f:
            f.seek(-1, 2)
            lead = "" if f.read(1) == b"\n" else "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(f"{lead}- {now.astimezone(timezone.utc):%Y-%m-%d %H:%M} | {decision} | {reason} | {reversible}\n")
