import numpy as np
import pandas as pd
import pytest

from src.procurement import runner
from src.procurement.config import CostParams, RunConfig
from src.procurement.floors import FLOOR_COLUMNS, RULES, construct_bids, floor_rates


def _tables():
    firms = ["F1", "F2", "F3"]
    costs = {1: [100.0, 120.0, 140.0], 2: [100.0, 200.0, 260.0]}
    eps = [0.0, 0.01, 0.02]
    brows, trows = [], []
    for p, cs in costs.items():
        for s, (f, c) in enumerate(zip(firms, cs), start=1):
            brows.append(dict(run_id="r", project_id=p, firm_id=f, est=1000.0, tag=0.0, dist=1.0,
                              cap=0.0, firm_rate=0.0, bid=c * 1.1, won=0))
            trows.append(dict(run_id="r", project_id=p, firm_id=f, seat=s, cost=c, eps=eps[s - 1]))
    return pd.DataFrame(brows), pd.DataFrame(trows)


def test_construct_bids_rules():
    bids, truth = _tables()
    for rule in RULES:
        out = construct_bids(bids, truth, rule, np.random.default_rng(3))
        assert list(out.columns) == list(bids.columns)
        assert out.groupby("project_id")["won"].sum().eq(1).all()
        assert (out.loc[out["won"] == 1].groupby("project_id")["bid"].min()
                == out.groupby("project_id")["bid"].min()).all()
    sm = construct_bids(bids, truth, "smooth", np.random.default_rng(3))
    cost = truth["cost"].to_numpy()
    assert (np.abs(sm["bid"].to_numpy() - 1.01 * cost) < 0.02 * 1000).all()
    # kinked identical: project 2, F1 (cost 100) has 1.01 cost far below the rivals' floor -> floor part
    ki = construct_bids(bids, truth, "kinked_identical", np.random.default_rng(3))
    r = ki[(ki.project_id == 2) & (ki.firm_id == "F1")]["bid"].iloc[0]
    floor = min(200.0 - 0.01 * 1000, 260.0 - 0.02 * 1000) - 0.5 * 0.05 * 1000
    assert floor > 1.01 * 100.0
    assert abs(r - floor) < 0.02 * 1000
    # deterministic given the rng
    a = construct_bids(bids, truth, "kinked_heterogeneous", np.random.default_rng(5))
    b = construct_bids(bids, truth, "kinked_heterogeneous", np.random.default_rng(5))
    pd.testing.assert_frame_equal(a, b)
    with pytest.raises(ValueError):
        construct_bids(bids, truth, "nope", np.random.default_rng(0))


@pytest.mark.slow
def test_synthetic_floors_reproduce(tmp_path):
    cost = CostParams(0.6487459575217261, 0.08, 0.17, 0.05)
    dirs = []
    for seed in range(1, 13):
        cfg = RunConfig(condition="control", seed=seed, T=110, burn_in=10, cost=cost)
        dirs.append(runner.run(cfg, runner.bidder_factory_for(cfg), results_root=tmp_path / "runs"))
    res = floor_rates(dirs).set_index("rule")
    assert list(res.reset_index().columns) == FLOOR_COLUMNS
    assert (res["n_runs"] == 12).all()
    # Brief reference (round-1 LLM truth tables): 0.039 / 0.150 / 0.156.
    ref = {"smooth": 0.039, "kinked_identical": 0.150, "kinked_heterogeneous": 0.156}
    for rule, v in ref.items():
        assert abs(res.loc[rule, "a3_rate"] - v) <= 0.05, (rule, res.loc[rule].to_dict())
