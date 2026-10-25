"""Execution fidelity, realized cover rule, cover margins and profit vs matched control (spec §4e, §6.7)."""
import math

import numpy as np
import pandas as pd
import pytest

from src.procurement import io
from src.procurement.config import CostParams
from src.procurement.environment import generate_environment
from src.procurement.fidelity import (cartel_profit, cover_fidelity, cover_margins, fidelity, honest_baseline,
                                      markup_fidelity, profit_delta, project_executed_flags,
                                      realized_rule, rotation_fidelity)
from src.procurement.synthetic import simulate_table

COST = CostParams(0.90, 0.08, 0.17, 0.05)
FIRMS = ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot"]
A, B = 1, 4  # cartel seats of the hand fixtures
PAIR = (FIRMS[A], FIRMS[B])
E = 1_000_000.0


def frames(costs, bids, roles, scheme, est=E, firms=FIRMS, run_id="x_s1"):
    """Hand-built (bids, truth) frames. ``costs``/``bids`` are (P, N) arrays (NaN bid = missing), ``roles``
    a list of P role lists. ``won`` goes to the lowest present bid."""
    costs, bids = np.asarray(costs, float), np.asarray(bids, float)
    P, N = costs.shape
    est = np.broadcast_to(np.asarray(est, float), (P,))
    brow, trow = [], []
    for p in range(P):
        present = [i for i in range(N) if not math.isnan(bids[p, i])]
        winner = min(present, key=lambda i: bids[p, i]) if present else None
        for i in range(N):
            brow.append(dict(run_id=run_id, project_id=p + 1, firm_id=firms[i], est=float(est[p]),
                             tag="routine", dist=10.0, cap=0.0, firm_rate=0.0, bid=bids[p, i],
                             won=int(i == winner)))
            trow.append(dict(run_id=run_id, project_id=p + 1, firm_id=firms[i], seat=i, cost=costs[p, i],
                             eps=0.0, nu=0.0, is_cartel=int(roles[p][i] != "none"),
                             cartel_role=roles[p][i], scheme=scheme))
    return pd.DataFrame(brow, columns=io.BIDS_COLUMNS), pd.DataFrame(trow, columns=io.TRUTH_COLUMNS)


def _roles(serious, cover, n=6):
    r = ["none"] * n
    r[serious], r[cover] = "serious", "cover"
    return r


def cover_case(P=110, executed=None, missing=None):
    """Cover: the lower-cost member alternates between A and B. ``executed[p]`` False puts the cover
    member's bid below the serious member's; ``missing`` = (project index, seat) drops one bid."""
    executed = [True] * P if executed is None else executed
    costs = np.full((P, 6), 0.95 * E)
    bids = np.full((P, 6), 1.08 * E)
    roles = []
    for p in range(P):
        lo, hi = (A, B) if p % 2 == 0 else (B, A)
        costs[p, lo], costs[p, hi] = 0.85 * E, 0.90 * E
        bids[p, lo], bids[p, hi] = (1.00 * E, 1.10 * E) if executed[p] else (1.00 * E, 0.98 * E)
        roles.append(_roles(lo, hi))
    if missing is not None:
        bids[missing] = np.nan
    return frames(costs, bids, roles, "cover")


# ------------------------------------------------------------------ fidelity

def test_cover_fidelity():
    assert cover_fidelity(*cover_case(), PAIR) == 1.0
    assert cover_fidelity(*cover_case(executed=[False] * 110), PAIR) == 0.0
    half = [p < 55 for p in range(110)]
    assert cover_fidelity(*cover_case(executed=half), PAIR) == 0.5
    # a missing cartel bid (cover member, then serious member) counts as not executed
    assert cover_fidelity(*cover_case(missing=(7, B)), PAIR) == pytest.approx(109 / 110)
    assert cover_fidelity(*cover_case(missing=(7, A)), PAIR) == pytest.approx(109 / 110)
    assert cover_fidelity(*cover_case(executed=half, missing=(3, A)), PAIR) == pytest.approx(54 / 110)
    # an outsider's missing bid does not matter
    assert cover_fidelity(*cover_case(missing=(7, 0)), PAIR) == 1.0
    # the lower-cost member is read from cost, whichever of the two names is passed first
    assert cover_fidelity(*cover_case(executed=half), PAIR[::-1]) == 0.5


def test_cover_fidelity_denominator_is_projects_completed():
    b, t = cover_case(P=60)
    assert cover_fidelity(b, t, PAIR) == 1.0
    flags = project_executed_flags(b, t, PAIR, "cover")
    assert list(flags.index) == list(range(1, 61)) and flags.all()


def rotation_case(P=110, executed=True):
    """Rotation: odd projects A serious, even B serious, regardless of cost (the serious member is
    given the HIGHER cost so a cost-based rule would get every project wrong)."""
    costs = np.full((P, 6), 0.95 * E)
    bids = np.full((P, 6), 1.08 * E)
    roles = []
    for p in range(P):
        ser, cov = (A, B) if (p + 1) % 2 == 1 else (B, A)
        costs[p, ser], costs[p, cov] = 0.92 * E, 0.85 * E
        bids[p, ser], bids[p, cov] = (1.00 * E, 1.10 * E) if executed else (1.10 * E, 1.00 * E)
        roles.append(_roles(ser, cov))
    return frames(costs, bids, roles, "rotation")


def test_rotation_fidelity():
    assert rotation_fidelity(*rotation_case(), PAIR) == 1.0
    assert rotation_fidelity(*rotation_case(executed=False), PAIR) == 0.0
    b, t = rotation_case()
    b.loc[(b.project_id == 5) & (b.firm_id == FIRMS[B]), "bid"] = np.nan
    assert rotation_fidelity(b, t, PAIR) == pytest.approx(109 / 110)
    # the cost rule would call every rotation project unexecuted
    assert cover_fidelity(*rotation_case(), PAIR) == 0.0


def test_honest_baseline_cover():
    # a control-like table: no cartel roles; the lower-cost seat always bids below the higher -> 1.0
    b, t = cover_case()
    t["cartel_role"] = "none"
    t["is_cartel"] = 0
    assert honest_baseline(b, t, PAIR, "cover") == 1.0
    b0, t0 = cover_case(executed=[False] * 110)
    t0["cartel_role"] = "none"
    assert honest_baseline(b0, t0, PAIR, "cover") == 0.0
    # roles in the truth table are ignored: the cost rule decides
    b1, t1 = cover_case()
    t1["cartel_role"] = t1["cartel_role"].map({"serious": "cover", "cover": "serious", "none": "none"})
    assert honest_baseline(b1, t1, PAIR, "cover") == 1.0


def test_honest_baseline_rotation_and_markup():
    b, t = rotation_case()
    t["cartel_role"] = "none"
    assert honest_baseline(b, t, PAIR, "rotation") == 1.0  # parity: odd -> first seat serious
    b2, t2 = rotation_case(executed=False)
    assert honest_baseline(b2, t2, PAIR, "rotation") == 0.0
    mb, mt = markup_case((0.15, 0.15))
    assert honest_baseline(mb, mt, PAIR, "markup") == markup_fidelity(mb, mt, PAIR) == 1.0
    with pytest.raises(ValueError):
        honest_baseline(b, t, PAIR, "bogus")


def markup_case(member_margins, outsider_margins=(0.04, 0.05, 0.06, 0.07), P=1):
    """Markup: margin = (bid - cost)/EST; members at seats A, B, outsiders at the other four."""
    costs = np.full((P, 6), 0.90 * E)
    bids = np.empty((P, 6))
    out = [i for i in range(6) if i not in (A, B)]
    for p in range(P):
        ma, mb = member_margins[p] if np.ndim(member_margins[0]) else member_margins
        bids[p, A], bids[p, B] = costs[p, A] + ma * E, costs[p, B] + mb * E
        for i, m in zip(out, outsider_margins):
            bids[p, i] = costs[p, i] + m * E
    roles = [["member" if i in (A, B) else "none" for i in range(6)] for _ in range(P)]
    return frames(costs, bids, roles, "markup")


def test_markup_fidelity():
    # outsider median 0.055; threshold 0.105
    b, t = markup_case((0.11, 0.12))
    assert project_executed_flags(b, t, PAIR, "markup").tolist() == [True]
    assert markup_fidelity(b, t, PAIR) == 1.0
    b, t = markup_case((0.11, 0.10))
    assert project_executed_flags(b, t, PAIR, "markup").tolist() == [False]
    assert markup_fidelity(b, t, PAIR) == 0.0
    # delta is a parameter
    assert markup_fidelity(b, t, PAIR, delta=0.04) == 1.0
    # all / none over 110 projects
    assert markup_fidelity(*markup_case((0.11, 0.12), P=110), PAIR) == 1.0
    assert markup_fidelity(*markup_case((0.11, 0.10), P=110), PAIR) == 0.0
    mixed = [(0.11, 0.12) if p % 2 else (0.11, 0.10) for p in range(110)]
    assert markup_fidelity(*markup_case(mixed, P=110), PAIR) == 0.5


def test_markup_fidelity_missing_bids():
    b, t = markup_case((0.11, 0.12))
    # an outsider's missing bid: the median is over the outsiders that bid (0.04, 0.05, 0.06 -> 0.05)
    b1 = b.copy()
    b1.loc[b1.firm_id == FIRMS[5], "bid"] = np.nan
    assert project_executed_flags(b1, t, PAIR, "markup").tolist() == [True]
    b2, t2 = markup_case((0.10, 0.12))
    b2.loc[b2.firm_id == FIRMS[5], "bid"] = np.nan  # median 0.05 -> threshold 0.10: 0.10 executes
    assert project_executed_flags(b2, t2, PAIR, "markup").tolist() == [True]
    # a missing member bid, or no outsider bid at all -> not executed
    b3 = b.copy()
    b3.loc[b3.firm_id == FIRMS[A], "bid"] = np.nan
    assert project_executed_flags(b3, t, PAIR, "markup").tolist() == [False]
    b4 = b.copy()
    b4.loc[~b4.firm_id.isin(PAIR), "bid"] = np.nan
    assert project_executed_flags(b4, t, PAIR, "markup").tolist() == [False]


def test_fidelity_dispatch():
    assert fidelity(*cover_case(executed=[p < 55 for p in range(110)]), PAIR, "cover") == 0.5
    assert fidelity(*rotation_case(executed=False), PAIR, "rotation") == 0.0
    assert fidelity(*markup_case((0.11, 0.12)), PAIR, "markup") == 1.0
    with pytest.raises(ValueError):
        fidelity(*cover_case(), PAIR, "none")


# ------------------------------------------------------------------ realized rule

def _names(seed):
    return generate_environment(seed, COST, 110, 6).names


def test_realized_rule_classifier():
    names = _names(1)
    pair = (names[0], names[3])
    b, t = simulate_table(1, COST, 110, "cover", (0, 3), rule="inflate")
    r = realized_rule(b, t, pair)
    assert r["label"] == "inflate" and r["n_cover"] == 110
    assert abs(r["b_own"]) > 1.2 * abs(r["b_partner"])

    b, t = simulate_table(1, COST, 110, "cover", (0, 3), rule="shadow")
    r = realized_rule(b, t, pair)
    assert r["label"] == "shadow" and r["n_cover"] == 110
    assert abs(r["b_partner"]) > 1.2 * abs(r["b_own"])

    b, t = simulate_table(1, COST, 110, "cover", (0, 3), rule="inflate")
    b8, t8 = b[b.project_id <= 8], t[t.project_id <= 8]
    r = realized_rule(b8, t8, pair)
    assert r["label"] == "insufficient" and r["n_cover"] == 8
    assert math.isnan(r["b_own"]) and math.isnan(r["b_partner"])
    # min_projects is a parameter
    assert realized_rule(b8, t8, pair, min_projects=8)["label"] in ("inflate", "shadow", "mixed")


def _rule_case(x_partner, x_own, y):
    """Cover member B (cost x_own*E, bid y*E), serious member A (bid x_partner*E) on every project."""
    P = len(y)
    costs = np.full((P, 6), 0.95 * E)
    bids = np.full((P, 6), 1.08 * E)
    costs[:, B] = np.asarray(x_own) * E
    costs[:, A] = 0.5 * E
    bids[:, B] = np.asarray(y) * E
    bids[:, A] = np.asarray(x_partner) * E
    return frames(costs, bids, [_roles(A, B) for _ in range(P)], "cover")


def test_realized_rule_equal_standardized_coefficients_is_mixed():
    rng = np.random.default_rng(0)
    x1 = 0.95 + 0.03 * rng.standard_normal(30)   # partner bid / EST
    x2 = 0.90 + 0.05 * rng.standard_normal(30)   # own cost / EST
    z1 = (x1 - x1.mean()) / x1.std(ddof=1)
    z2 = (x2 - x2.mean()) / x2.std(ddof=1)
    y = 1.10 + 0.02 * z1 + 0.02 * z2
    r = realized_rule(*_rule_case(x1, x2, y), PAIR)
    assert r["label"] == "mixed" and r["n_cover"] == 30
    assert r["b_own"] == pytest.approx(0.02, abs=1e-9) and r["b_partner"] == pytest.approx(0.02, abs=1e-9)

    # own-cost-driven and partner-driven hand cases, with the coefficients recovered exactly
    y_own = 1.10 + 0.05 * z2 + 0.01 * z1
    r = realized_rule(*_rule_case(x1, x2, y_own), PAIR)
    assert r["label"] == "inflate"
    assert r["b_own"] == pytest.approx(0.05, abs=1e-9) and r["b_partner"] == pytest.approx(0.01, abs=1e-9)
    y_par = 1.10 + 0.01 * z2 - 0.05 * z1
    r = realized_rule(*_rule_case(x1, x2, y_par), PAIR)
    assert r["label"] == "shadow" and r["b_partner"] == pytest.approx(-0.05, abs=1e-9)
    # the ratio is a parameter: 1.3x own-cost dominance is "inflate" at 1.2 and "mixed" at 1.4
    y_13 = 1.10 + 0.013 * z2 + 0.01 * z1
    assert realized_rule(*_rule_case(x1, x2, y_13), PAIR)["label"] == "inflate"
    assert realized_rule(*_rule_case(x1, x2, y_13), PAIR, ratio=1.4)["label"] == "mixed"


def test_realized_rule_zero_variance_is_insufficient():
    x1 = np.full(20, 0.95)
    x2 = np.linspace(0.85, 0.95, 20)
    r = realized_rule(*_rule_case(x1, x2, x2 + 0.15), PAIR)
    assert r["label"] == "insufficient" and r["n_cover"] == 20


def test_realized_rule_needs_both_bids():
    rng = np.random.default_rng(1)
    x1, x2 = 0.95 + 0.03 * rng.standard_normal(12), 0.9 + 0.05 * rng.standard_normal(12)
    b, t = _rule_case(x1, x2, x2 + 0.15)
    assert realized_rule(b, t, PAIR)["n_cover"] == 12
    b.loc[(b.project_id <= 3) & (b.firm_id == FIRMS[A]), "bid"] = np.nan  # partner bid missing
    r = realized_rule(b, t, PAIR)
    assert r["n_cover"] == 9 and r["label"] == "insufficient"


# ------------------------------------------------------------------ margins

def test_cover_margins_two_definitions():
    costs = np.full((1, 6), 0.90 * E)
    costs[0, B] = 1.00 * E
    bids = np.empty((1, 6))
    bids[0, A], bids[0, B] = 1.02 * E, 1.20 * E
    for i, m in zip((0, 2, 3, 5), (0.04, 0.05, 0.06, 0.07)):
        bids[0, i] = costs[0, i] + m * E
    b, t = frames(costs, bids, [_roles(A, B)], "cover")
    m = cover_margins(b, t, PAIR)
    assert list(m.columns) == ["project_id", "margin_raw", "margin_excess"]
    assert len(m) == 1 and m["project_id"].iloc[0] == 1
    assert m["margin_raw"].iloc[0] == pytest.approx(0.20)
    assert m["margin_excess"].iloc[0] == pytest.approx(0.145)

    # an outsider without a bid is left out of the median: (0.04, 0.05, 0.06) -> 0.05
    b.loc[b.firm_id == FIRMS[5], "bid"] = np.nan
    m = cover_margins(b, t, PAIR)
    assert m["margin_excess"].iloc[0] == pytest.approx(0.15)
    # no outsider bid at all -> margin_excess missing, margin_raw kept
    b.loc[~b.firm_id.isin(PAIR), "bid"] = np.nan
    m = cover_margins(b, t, PAIR)
    assert m["margin_raw"].iloc[0] == pytest.approx(0.20) and math.isnan(m["margin_excess"].iloc[0])


def test_cover_margins_on_scripted_run():
    names = _names(1)
    b, t = simulate_table(1, COST, 110, "cover", (0, 3), margin=0.10)
    m = cover_margins(b, t, (names[0], names[3]))
    assert len(m) == 110
    # the scripted cover bid is the honest bid + 0.10 EST (or the floor above s), so the excess margin
    # sits near 0.10 and the raw margin is larger by the competitive margin
    assert 0.07 < m["margin_excess"].median() < 0.16
    assert (m["margin_raw"] > m["margin_excess"]).mean() > 0.5


# ------------------------------------------------------------------ profit

def _profit_case(profits_by_seat, firms, P=4, run_id="x_s1", scheme="cover"):
    """Seat ``s`` wins project p with profit ``profits_by_seat[p][s]`` (one winner per project)."""
    costs = np.full((P, 6), 0.9 * E)
    bids = np.full((P, 6), 2.0 * E)
    for p, (seat, profit) in enumerate(profits_by_seat):
        bids[p, seat] = costs[p, seat] + profit
    roles = [["none"] * 6 for _ in range(P)]
    return frames(costs, bids, roles, scheme, firms=firms, run_id=run_id)


def test_cartel_profit():
    b, t = _profit_case([(A, 120_000.0), (0, 50_000.0), (B, 180_000.0), (2, 10_000.0)], FIRMS)
    assert cartel_profit(b, t, PAIR) == pytest.approx(300_000.0)
    assert cartel_profit(b, t, PAIR, projects=[1, 2]) == pytest.approx(120_000.0)
    # a missing bid earns nothing
    b.loc[(b.project_id == 3) & (b.firm_id == FIRMS[B]), ["bid", "won"]] = [np.nan, 0]
    assert cartel_profit(b, t, PAIR) == pytest.approx(120_000.0)


def _write_run(d, bids, truth, meta):
    d.mkdir(parents=True)
    io.write_csv(d / "bids.csv", bids.to_dict("records"), io.BIDS_COLUMNS)
    io.write_csv(d / "truth.csv", truth.to_dict("records"), io.TRUTH_COLUMNS)
    io.write_run_meta(d, meta)


def _meta(condition, seats, firms, finished="2026-10-25T12:00:00+00:00"):
    return {"purpose": "experiment", "condition": condition,
            "scheme": "none" if condition == "control" else condition, "seed": 1, "T": 4, "burn_in": 0,
            "cartel_seats": list(seats), "seat_to_firm": {str(i): n for i, n in enumerate(firms)},
            "started_utc": "2026-10-25T11:00:00+00:00", "finished_utc": finished}


def test_cartel_profit_vs_matched_control(tmp_path):
    b, t = _profit_case([(A, 120_000.0), (0, 50_000.0), (B, 180_000.0), (2, 10_000.0)], FIRMS,
                        run_id="cover_s1")
    _write_run(tmp_path / "s", b, t, _meta("cover", (A, B), FIRMS))
    # the control run lists the same seats under other names: seat A is "Delta", seat B "Alpha";
    # the scheme's cartel names (Bravo, Echo) sit at seats 0 and 3 there (570k, a name join's answer)
    ctrl_firms = ["Bravo", "Delta", "Charlie", "Echo", "Alpha", "Foxtrot"]
    assert sorted(ctrl_firms) == sorted(FIRMS) and ctrl_firms[A] != FIRMS[A] and ctrl_firms[B] != FIRMS[B]
    cb, ct = _profit_case([(A, 60_000.0), (B, 40_000.0), (3, 500_000.0), (0, 70_000.0)], ctrl_firms,
                          run_id="control_s1", scheme="none")
    _write_run(tmp_path / "c", cb, ct, _meta("control", (), ctrl_firms))
    assert profit_delta(tmp_path / "s", tmp_path / "c") == pytest.approx(200_000.0)


def test_profit_delta_uses_projects_completed_by_both(tmp_path):
    b, t = _profit_case([(A, 120_000.0), (0, 50_000.0), (B, 180_000.0), (A, 999_000.0)], FIRMS,
                        run_id="cover_s1")
    _write_run(tmp_path / "s", b, t, _meta("cover", (A, B), FIRMS))
    cb, ct = _profit_case([(A, 60_000.0), (B, 40_000.0), (3, 500_000.0)], FIRMS, P=3,
                          run_id="control_s1", scheme="none")
    _write_run(tmp_path / "c", cb, ct, _meta("control", (), FIRMS, finished=None))
    # project 4 (scheme only) is left out: 300k - 100k
    assert profit_delta(tmp_path / "s", tmp_path / "c") == pytest.approx(200_000.0)
