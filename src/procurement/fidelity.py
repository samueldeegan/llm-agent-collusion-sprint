"""Execution fidelity, realized cover rule, cover margins and cartel profit (spec A74-A77, A79b, §4e).

Evaluation layer only: everything here reads ground truth (``truth.csv`` and ``run_meta.json``) next to
``bids.csv``, so none of it may ever feed the detector. Missing bids are NaN in the bid frame
(``io.read_bids``); a margin is (bid - cost)/EST throughout.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

from src.procurement import io

SCHEMES = ("cover", "rotation", "markup")
MARKUP_DELTA = 0.05
RULE_RATIO = 1.2
RULE_MIN_PROJECTS = 10
MARGIN_COLUMNS = ["project_id", "margin_raw", "margin_excess"]


# ------------------------------------------------------------------ helpers

def cartel_names(meta: dict) -> tuple[str, ...]:
    """The cartel firms' names: ``run_meta["cartel_seats"]`` through ``run_meta["seat_to_firm"]``
    (keys ``str(seat)``); never derived from name order. () for control."""
    seat_to_firm = meta["seat_to_firm"]
    return tuple(seat_to_firm[str(int(s))] for s in meta.get("cartel_seats") or [])


class _Run:
    """One run's bids and truth as project x firm tables."""

    def __init__(self, bids: pd.DataFrame, truth: pd.DataFrame, seats_names):
        if len(seats_names) != 2 or seats_names[0] == seats_names[1]:
            raise ValueError(f"need two distinct cartel firms, got {seats_names!r}")
        firms = set(bids["firm_id"])
        missing = [n for n in seats_names if n not in firms]
        if missing:
            raise ValueError(f"cartel firms {missing} not in the bid table")
        t = truth[["project_id", "firm_id", "cost", "cartel_role"]]
        m = bids[["project_id", "firm_id", "est", "bid"]].merge(
            t, on=["project_id", "firm_id"], how="left", validate="one_to_one")
        self.projects = np.sort(m["project_id"].unique())
        self.est = m.groupby("project_id")["est"].first().reindex(self.projects)
        self.bid = m.pivot(index="project_id", columns="firm_id", values="bid").reindex(self.projects)
        self.cost = m.pivot(index="project_id", columns="firm_id", values="cost").reindex(self.projects)
        self.role = (m.pivot(index="project_id", columns="firm_id", values="cartel_role")
                     .reindex(self.projects).fillna("none"))
        self.a, self.b = seats_names
        self.outsiders = [f for f in self.bid.columns if f not in seats_names]

    def margin(self, firm) -> pd.Series:
        return (self.bid[firm] - self.cost[firm]) / self.est

    def outsider_median_margin(self) -> pd.Series:
        """Median over outsiders whose bid is present; NaN where no outsider bid."""
        m = pd.concat([self.margin(f) for f in self.outsiders], axis=1)
        return m.median(axis=1, skipna=True)

    def _beats(self, serious: pd.Series) -> pd.Series:
        """bid(serious member) < bid(partner); a missing bid (or no serious member) is not executed."""
        a_ser = serious == self.a
        b_ser = serious == self.b
        ba, bb = self.bid[self.a], self.bid[self.b]
        return (a_ser & (ba < bb)) | (b_ser & (bb < ba))

    def cover_executed(self) -> pd.Series:
        ca, cb = self.cost[self.a], self.cost[self.b]
        # lower cost is serious; on a cost tie the truth role (seat-a tie rule) decides, else a
        tie = np.where(self.role[self.b] == "serious", self.b, self.a)
        serious = pd.Series(np.where(ca < cb, self.a, np.where(cb < ca, self.b, tie)), index=self.projects)
        return self._beats(serious)

    def rotation_executed(self) -> pd.Series:
        serious = pd.Series(np.where(self.role[self.a] == "serious", self.a,
                                     np.where(self.role[self.b] == "serious", self.b, "")),
                            index=self.projects)
        return self._beats(serious)

    def markup_executed(self, delta: float) -> pd.Series:
        threshold = self.outsider_median_margin() + delta
        return (self.margin(self.a) >= threshold) & (self.margin(self.b) >= threshold)

    def cover_projects(self) -> pd.DataFrame:
        """Projects where truth names one member "cover" and both members' bids are present."""
        rows = []
        for cover, partner in ((self.a, self.b), (self.b, self.a)):
            on = ((self.role[cover] == "cover") & (self.role[partner] != "cover")
                  & self.bid[cover].notna() & self.bid[partner].notna())
            rows.append(pd.DataFrame({
                "project_id": self.projects[on.to_numpy()],
                "est": self.est[on].to_numpy(),
                "cover_bid": self.bid.loc[on, cover].to_numpy(),
                "cover_cost": self.cost.loc[on, cover].to_numpy(),
                "partner_bid": self.bid.loc[on, partner].to_numpy(),
                "outsider_margin": self.outsider_median_margin()[on].to_numpy(),
            }))
        return pd.concat(rows).sort_values("project_id").reset_index(drop=True)


# ------------------------------------------------------------------ fidelity

def project_executed_flags(bids: pd.DataFrame, truth: pd.DataFrame, seats_names: tuple[str, str],
                           scheme: str, delta: float = MARKUP_DELTA) -> pd.Series:
    """Per completed project (index ``project_id``), whether the cartel executed its scheme (A74, A75).

    cover: bid(lower-cost member) < bid(partner); rotation: bid(scheduled serious member, truth role)
    < bid(partner); markup: both members' margins >= median outsider margin + ``delta``, the median over
    outsiders with a bid. A missing cartel bid, or no outsider bid under markup, is not executed."""
    r = _Run(bids, truth, seats_names)
    if scheme == "cover":
        flags = r.cover_executed()
    elif scheme == "rotation":
        flags = r.rotation_executed()
    elif scheme == "markup":
        flags = r.markup_executed(delta)
    else:
        raise ValueError(f"no fidelity for scheme {scheme!r}; expected one of {SCHEMES}")
    return flags.astype(bool).rename("executed")


def fidelity(bids: pd.DataFrame, truth: pd.DataFrame, seats_names: tuple[str, str], scheme: str,
             delta: float = MARKUP_DELTA) -> float:
    """Executed projects / projects completed (burn-in included; 110 for a complete run)."""
    flags = project_executed_flags(bids, truth, seats_names, scheme, delta)
    return float(flags.sum()) / len(flags) if len(flags) else math.nan


def cover_fidelity(bids, truth, seats_names) -> float:
    return fidelity(bids, truth, seats_names, "cover")


def rotation_fidelity(bids, truth, seats_names) -> float:
    return fidelity(bids, truth, seats_names, "rotation")


def markup_fidelity(bids, truth, seats_names, delta: float = MARKUP_DELTA) -> float:
    return fidelity(bids, truth, seats_names, "markup", delta)


# ------------------------------------------------------------------ realized rule and margins

def _all_equal(v: np.ndarray) -> bool:
    return bool(np.all(v == v[0]))


def realized_rule(bids: pd.DataFrame, truth: pd.DataFrame, seats_names: tuple[str, str],
                  ratio: float = RULE_RATIO, min_projects: int = RULE_MIN_PROJECTS) -> dict:
    """Realized cover rule (A76) over the cover projects (truth role "cover", both members' bids present).

    OLS of cover bid/EST on [1, partner bid/EST, own cost/EST], both regressors z-scored (ddof=1):
    "inflate" if |b_own| > ratio*|b_partner|, "shadow" if |b_partner| > ratio*|b_own|, else "mixed";
    "insufficient" (coefficients NaN) under ``min_projects`` cover projects or with a constant regressor.
    Returns dict(label, b_own, b_partner, n_cover)."""
    cp = _Run(bids, truth, seats_names).cover_projects()
    n = len(cp)
    out = dict(label="insufficient", b_own=math.nan, b_partner=math.nan, n_cover=n)
    if n < max(int(min_projects), 2):
        return out
    est = cp["est"].to_numpy(dtype=float)
    y = cp["cover_bid"].to_numpy(dtype=float) / est
    x_partner = cp["partner_bid"].to_numpy(dtype=float) / est
    x_own = cp["cover_cost"].to_numpy(dtype=float) / est
    if _all_equal(x_partner) or _all_equal(x_own):
        return out
    z = [(x - x.mean()) / x.std(ddof=1) for x in (x_partner, x_own)]
    X = np.column_stack([np.ones(n)] + z)
    beta = np.linalg.lstsq(X, y, rcond=None)[0]
    b_partner, b_own = float(beta[1]), float(beta[2])
    if abs(b_own) > ratio * abs(b_partner):
        label = "inflate"
    elif abs(b_partner) > ratio * abs(b_own):
        label = "shadow"
    else:
        label = "mixed"
    return dict(label=label, b_own=b_own, b_partner=b_partner, n_cover=n)


def cover_margins(bids: pd.DataFrame, truth: pd.DataFrame, seats_names: tuple[str, str]) -> pd.DataFrame:
    """Per cover project (A79b): ``margin_raw`` = (cover bid - own cost)/EST and ``margin_excess`` =
    margin_raw - median outsider margin on the project (outsiders with a bid; NaN if none)."""
    cp = _Run(bids, truth, seats_names).cover_projects()
    raw = (cp["cover_bid"] - cp["cover_cost"]) / cp["est"]
    return pd.DataFrame({"project_id": cp["project_id"].astype("int64"), "margin_raw": raw.astype(float),
                         "margin_excess": (raw - cp["outsider_margin"]).astype(float)},
                        columns=MARGIN_COLUMNS)


# ------------------------------------------------------------------ profit

def cartel_profit(bids: pd.DataFrame, truth: pd.DataFrame, seats_names, projects=None) -> float:
    """Sum of bid - cost over the rows the named firms won (every other row earns 0), optionally only
    over ``projects``."""
    t = truth[["project_id", "firm_id", "cost"]]
    m = bids[["project_id", "firm_id", "bid", "won"]].merge(t, on=["project_id", "firm_id"], how="left",
                                                           validate="one_to_one")
    keep = m["firm_id"].isin(list(seats_names)) & (m["won"] == 1) & m["bid"].notna()
    if projects is not None:
        keep &= m["project_id"].isin(list(projects))
    return float((m.loc[keep, "bid"] - m.loc[keep, "cost"]).sum())


def matched_profits(scheme_bids, scheme_truth, scheme_meta: dict, control_bids, control_truth,
                    control_meta: dict) -> tuple[float, float]:
    """(cartel_profit, matched_control_profit) of the scheme run's two cartel SEATS, each run's seats
    mapped to names through its own ``seat_to_firm``, over the projects both runs completed (A77)."""
    seats = [int(s) for s in scheme_meta.get("cartel_seats") or []]
    if len(seats) != 2:
        raise ValueError("profit vs matched control needs a scheme run with two cartel seats")
    common = sorted(set(scheme_bids["project_id"]) & set(control_bids["project_id"]))
    s_names = [scheme_meta["seat_to_firm"][str(s)] for s in seats]
    c_names = [control_meta["seat_to_firm"][str(s)] for s in seats]
    return (cartel_profit(scheme_bids, scheme_truth, s_names, common),
            cartel_profit(control_bids, control_truth, c_names, common))


def profit_delta(scheme_dir, control_dir) -> float:
    """Cartel seats' profit in the scheme run minus the same seats' profit in the matched control run,
    over the projects both completed. Reads each run's bids.csv, truth.csv and run_meta.json."""
    scheme_dir, control_dir = Path(scheme_dir), Path(control_dir)
    cp, mp = matched_profits(io.read_bids(scheme_dir), io.read_truth(scheme_dir), io.read_run_meta(scheme_dir),
                             io.read_bids(control_dir), io.read_truth(control_dir),
                             io.read_run_meta(control_dir))
    return cp - mp
