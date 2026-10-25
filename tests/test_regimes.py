import numpy as np
import pandas as pd

from src.procurement import regimes as rg

FIRMS = list("ABCDEF")
RID = "x_control_base_s3"
EST = 1000.0


def build(bid_fn, n=30, seed=0):
    rng = np.random.default_rng(seed)
    brow, trow = [], []
    for p in range(1, n + 1):
        cost = {f: EST * rng.uniform(0.6, 0.7) - (0.03 * EST if f == "C" else 0) for f in FIRMS}  # small gaps; C often leads
        for i, f in enumerate(FIRMS):
            dist, cap = rng.uniform(0, 5), rng.uniform(0, 1)
            own = cost[f]
            gap = (min(c for k, c in cost.items() if k != f) - own) / EST
            brow.append(dict(run_id=RID, project_id=p, firm_id=f, est=EST, tag="t", dist=dist, cap=cap,
                             firm_rate=0.0, bid=bid_fn(f, p, own, gap, cap), won=0))
            trow.append(dict(run_id=RID, project_id=p, firm_id=f, seat=i, cost=own, eps=0.0, nu=0.0,
                             is_cartel=0, cartel_role="none", scheme="none", arm="base"))
    b, t = pd.DataFrame(brow), pd.DataFrame(trow)
    for _, g in b.groupby("project_id"):
        b.loc[g["bid"].idxmin(), "won"] = 1
    return b, t


def mixed(f, p, own, gap, cap):
    if f == "A":  # passes 5 of the 20 post-burn-in projects (11..30)
        return 9_999_999.0 if p in (12, 16, 20, 24, 28) else own + 0.03 * EST
    if f == "B":
        return own + 0.08 * EST
    if f == "C":
        return own + 0.02 * EST + 0.5 * max(gap, 0) * EST
    if f == "D":
        return own + 0.005 * EST
    return own + 0.03 * EST


def test_firm_season_table():
    b, t = build(mixed)
    full = rg.firm_season_table(b, t, 10, "base", "control")
    assert list(full.columns) == rg.REGIME_COLUMNS
    tab = full.set_index("firm_id")
    assert tab.loc["A", "pass_rate"] == 0.25 and tab.loc["A", "regime"] == "passer"
    assert tab.loc["B", "regime"] == "fat"
    assert tab.loc["C", "regime"] == "shade" and abs(tab.loc["C", "shade_slope"] - 0.5) < 1e-6
    assert tab.loc["D", "regime"] == "razor"
    assert tab.loc["E", "regime"] == "moderate"
    assert (tab["seed"] == 3).all()
    for f in FIRMS:
        w = b[(b.firm_id == f) & (b.won == 1)].merge(t, on=["run_id", "project_id", "firm_id"])
        assert tab.loc[f, "wins"] == len(w)
        assert abs(tab.loc[f, "profit"] - (w.bid - w.cost).sum()) < 1e-6
    assert abs(tab.loc["E", "margin_when_leading"] - 0.03) < 1e-9
    assert abs(tab.loc["E", "margin_when_trailing"] - 0.03) < 1e-9
    counts = rg.regime_counts(full)
    assert counts["n_firm_seasons"].sum() == 6 and len(counts) == 5


def test_a3_by_pair_type():
    tab = pd.DataFrame({"run_id": [RID] * 3, "firm_id": list("ABC"), "regime": ["razor", "fat", "razor"]})
    base = dict(run_id=RID, spec="tag_filtered", primary=1, complete=1, condition="control", arm="base")
    pe = pd.DataFrame([dict(base, firm_i="A", firm_j="B", a3_flag=1),
                       dict(base, firm_i="B", firm_j="C", a3_flag=0),
                       dict(base, firm_i="A", firm_j="C", a3_flag=1),
                       dict(base, firm_i="A", firm_j="C", a3_flag=1, primary=0)])
    out = rg.a3_by_pair_type(pe, tab).set_index(["regime_i", "regime_j"])
    assert out.loc[("fat", "razor"), "n_pairs"] == 2 and out.loc[("fat", "razor"), "rate"] == 0.5
    assert out.loc[("razor", "razor"), "rate"] == 1.0 and out.loc[("razor", "razor"), "n_pairs"] == 1


def test_slope_dispersion():
    b, t = build(lambda f, p, o, g, c: o + 0.02 * EST, n=110)
    rows = rg.slope_dispersion(b, t, 10, "base", RID)
    assert [r["half"] for r in rows] == ["11-60", "61-110"]
    assert all(r["sd_cap_slope"] < 1e-9 and r["sd_dist_slope"] < 1e-9 for r in rows)
    b, t = build(lambda f, p, o, g, c: o + 0.02 * EST + (0.3 * c * EST if f == "A" else 0), n=110)
    rows = rg.slope_dispersion(b, t, 10, "base", RID)
    assert all(r["sd_cap_slope"] > 0.1 for r in rows)
    agg = rg.slope_dispersion_by_arm(rows)
    assert list(agg.columns) == rg.DISPERSION_COLUMNS and (agg["n_runs"] == 1).all()
