import numpy as np
import pandas as pd

from src.procurement import regimes as rg

FIRMS = list("ABCDEF")
RID = "x_control_base_s3"
EST = 1000.0


def build(bid_fn, n=30, seed=0, offsets=None):
    rng = np.random.default_rng(seed)
    brow, trow = [], []
    for p in range(1, n + 1):
        # narrow cost range keeps gaps small: wide gaps push C margin_med >= 0.05 so it would classify as fat, not shade
        cost = {f: EST * rng.uniform(0.6, 0.7) - (0.03 * EST if f == "C" else 0) + (offsets or {}).get(f, 0) * EST for f in FIRMS}
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
    assert dict(zip(counts["regime"], counts["n_firm_seasons"])) == {
        "passer": 1, "fat": 1, "shade": 1, "razor": 1, "moderate": 2}
    assert list(counts["regime"]) == list(rg.REGIME_ORDER)


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


def test_leading_trailing_margins_differ():
    b, t = build(mixed)
    tab = rg.firm_season_table(b, t, 10, "base", "control").set_index("firm_id")
    post = b[b.project_id > 10].merge(t, on=["run_id", "project_id", "firm_id"])
    post["m"] = (post.bid - post.cost) / post.est
    lead_mask = post.groupby("project_id")["cost"].transform("min") == post["cost"]
    c = post[post.firm_id == "C"]
    cl, ct = c[lead_mask[c.index]], c[~lead_mask[c.index]]
    assert len(cl) >= 8 and len(ct) >= 1
    assert abs(tab.loc["C", "margin_when_leading"] - cl.m.median()) < 1e-12
    assert abs(tab.loc["C", "margin_when_trailing"] - 0.02) < 1e-9
    assert abs(tab.loc["C", "margin_when_trailing"] - ct.m.median()) < 1e-12
    assert tab.loc["C", "margin_when_leading"] > tab.loc["C", "margin_when_trailing"] + 0.005


def test_wins_profit_include_burn_in():
    b, t = build(mixed)
    tab = rg.firm_season_table(b, t, 10, "base", "control").set_index("firm_id")
    w = b[b.won == 1].merge(t, on=["run_id", "project_id", "firm_id"])
    d = w[w.firm_id == "D"]
    assert (d.project_id <= 10).any() and (d.project_id > 10).any()
    assert tab.loc["D", "wins"] == len(d) > len(d[d.project_id > 10])
    assert abs(tab.loc["D", "profit"] - (d.bid - d.cost).sum()) < 1e-9
    assert tab.loc["D", "profit"] > (d[d.project_id > 10].bid - d[d.project_id > 10].cost).sum()


def test_a3_filters_and_arms():
    tab = pd.DataFrame({"run_id": [RID] * 3 + ["r2"] * 2, "firm_id": list("ABC") + list("AB"),
                        "regime": ["razor", "fat", "razor", "fat", "razor"]})
    base = dict(run_id=RID, spec="tag_filtered", primary=1, complete=1, condition="control", arm="base",
                firm_i="A", firm_j="B", a3_flag=1)
    rows = [dict(base),
            dict(base, firm_i="B", firm_j="C", a3_flag=0),
            dict(base, spec="tag"), dict(base, complete=0), dict(base, condition="cartel"),
            dict(base, primary=0), dict(base, a3_flag=np.nan),
            dict(base, run_id="r2", arm="mem0", firm_i="A", firm_j="B", a3_flag=0),
            dict(base, run_id="r2", arm="mem0", firm_i="B", firm_j="A", a3_flag=1)]
    out = rg.a3_by_pair_type(pd.DataFrame(rows), tab)
    got = {(r.arm, r.regime_i, r.regime_j): (r.n_pairs, r.n_rejected, r.rate) for r in out.itertuples()}
    assert got[("base", "fat", "razor")] == (2, 1, 0.5)
    assert ("base", "razor", "razor") not in got
    assert sum(v[0] for k, v in got.items() if k[0] == "base") == 2
    assert got[("mem0", "fat", "razor")] == (2, 1, 0.5)


def test_slope_dispersion_exact_and_by_arm():
    def fn(f, p, o, g, c):
        return o + 0.02 * EST
    b, t = build(fn, n=110)
    b["bid"] = [r.bid + (EST * (0.1 * np.log1p(r.dist) + 0.3 * r.cap) if r.firm_id == "A" else 0)
                for r in b.itertuples()]
    rows = rg.slope_dispersion(b, t, 10, "base", RID)
    for r in rows:
        assert abs(r["sd_dist_slope"] - 0.1 / np.sqrt(6)) < 1e-9
        assert abs(r["sd_cap_slope"] - 0.3 / np.sqrt(6)) < 1e-9
    hand = [dict(arm="a", run_id="r1", half="11-60", sd_cap_slope=0.1, sd_dist_slope=0.4),
            dict(arm="a", run_id="r2", half="11-60", sd_cap_slope=0.3, sd_dist_slope=0.2),
            dict(arm="a", run_id="r1", half="61-110", sd_cap_slope=0.5, sd_dist_slope=0.5),
            dict(arm="b", run_id="r3", half="11-60", sd_cap_slope=0.7, sd_dist_slope=0.1)]
    agg = rg.slope_dispersion_by_arm(hand).set_index(["arm", "half"])
    assert agg.loc[("a", "11-60"), "n_runs"] == 2
    assert abs(agg.loc[("a", "11-60"), "mean_sd_cap_slope"] - 0.2) < 1e-12
    assert abs(agg.loc[("a", "11-60"), "mean_sd_dist_slope"] - 0.3) < 1e-12
    assert agg.loc[("a", "61-110"), "n_runs"] == 1 and agg.loc[("b", "11-60"), "mean_sd_cap_slope"] == 0.7


def test_nan_paths():
    # A passes every post-burn-in project; F is always the highest-cost firm so gap <= 0 (no shade rows)
    def fn(f, p, o, g, c):
        return 9_999_999.0 if f == "A" and p > 10 else o + 0.03 * EST
    b, t = build(fn, offsets={"F": 0.3})
    tab = rg.firm_season_table(b, t, 10, "base", "control").set_index("firm_id")
    assert tab.loc["A", "regime"] == "passer" and tab.loc["A", "pass_rate"] == 1.0
    assert np.isnan(tab.loc["A", "margin_med"]) and np.isnan(tab.loc["A", "margin_iqr"])
    assert np.isnan(tab.loc["A", "margin_when_leading"]) and np.isnan(tab.loc["A", "shade_slope"])
    assert np.isnan(tab.loc["F", "shade_slope"]) and np.isnan(tab.loc["F", "margin_when_leading"])
    assert tab.loc["F", "regime"] == "moderate"


def _threshold_frames(k):
    """Firm A leads (gap > 0, varying) in exactly k post-burn-in projects (11..), trails in the rest."""
    brow, trow = [], []
    for p in range(1, 31):
        lead = 11 <= p < 11 + k
        costs = {f: 600.0 for f in FIRMS}
        costs["A"] = 600.0 - 10.0 * (p - 10) if lead else 700.0
        for i, f in enumerate(FIRMS):
            gap = (600.0 - costs["A"]) / EST
            bid = costs[f] + (0.02 + 0.5 * max(gap, 0)) * EST if f == "A" else costs[f] + 0.03 * EST
            brow.append(dict(run_id=RID, project_id=p, firm_id=f, est=EST, tag="t", dist=1.0, cap=0.5,
                             firm_rate=0.0, bid=bid, won=0))
            trow.append(dict(run_id=RID, project_id=p, firm_id=f, seat=i, cost=costs[f], eps=0.0, nu=0.0,
                             is_cartel=0, cartel_role="none", scheme="none", arm="base"))
    return pd.DataFrame(brow), pd.DataFrame(trow)


def test_shade_slope_min_rows_threshold():
    for k, finite in ((7, False), (8, True)):
        b, t = _threshold_frames(k)
        tab = rg.firm_season_table(b, t, 10, "base", "control").set_index("firm_id")
        if finite:
            assert abs(tab.loc["A", "shade_slope"] - 0.5) < 1e-9
        else:
            assert np.isnan(tab.loc["A", "shade_slope"])


def test_slope_dispersion_unidentified_slope_is_nan():
    # CAP constant for firm A (0), so A's CAP slope is unidentified: NaN, not the minimum-norm 0.0
    b, t = build(lambda f, p, o, g, c: o + 0.02 * EST, n=110)
    b.loc[b["firm_id"] == "A", "cap"] = 0.0
    b["bid"] = [r.bid + (EST * 0.3 * r.cap if r.firm_id == "B" else 0) for r in b.itertuples()]
    rows = rg.slope_dispersion(b, t, 10, "base", RID)
    # SD across the five identified firms only: B has slope 0.3, the rest 0 -> sd of [0.3,0,0,0,0] (ddof 1)
    expected = float(np.std([0.3, 0, 0, 0, 0], ddof=1))
    for r in rows:
        assert abs(r["sd_cap_slope"] - expected) < 1e-9
    # constant ln(1+DIST) -> DIST slope NaN for that firm
    b2, t2 = build(lambda f, p, o, g, c: o + 0.02 * EST, n=110)
    b2.loc[b2["firm_id"] == "A", "dist"] = 2.0
    rows2 = rg.slope_dispersion(b2, t2, 10, "base", RID)
    assert all(np.isfinite(r["sd_dist_slope"]) and r["sd_dist_slope"] < 1e-9 for r in rows2)
    only_a = b2[b2["firm_id"].isin(["A", "B"])]
    rows3 = rg.slope_dispersion(only_a, t2[t2["firm_id"].isin(["A", "B"])], 10, "base", RID)
    assert all(np.isnan(r["sd_dist_slope"]) for r in rows3)  # one identified firm: SD undefined
