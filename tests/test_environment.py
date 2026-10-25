import json
import math
from dataclasses import replace
from pathlib import Path

import numpy as np

from src.procurement.config import CostParams, load_defaults
from src.procurement.environment import Environment, cost_of, firm_names, generate_environment

COST = CostParams(0.90, 0.08, 0.17, 0.05)


def test_env_shapes_and_ranges():
    env = generate_environment(1, COST, 110)
    assert env.est.shape == (110,)
    assert env.est.min() >= 200_000 and env.est.max() <= 2_000_000
    assert np.all(env.est == np.round(env.est))
    assert env.yards.shape == (6, 2) and env.sites.shape == (110, 2)
    assert env.yards.min() >= 0 and env.yards.max() <= 100
    assert env.sites.min() >= 0 and env.sites.max() <= 100
    assert np.allclose(env.dist, np.linalg.norm(env.yards[:, None] - env.sites[None], axis=2))
    assert env.dist.shape == (6, 110) and env.dist.max() <= 141.43
    assert np.all(np.abs(env.eta) <= 0.06)
    assert set(np.unique(env.tag)) <= {0, 1, 2, 3}
    assert env.tag_label == [COST.tag_labels[t] for t in env.tag]
    assert np.array_equal(env.nu, np.array(COST.nu_levels)[env.tag])
    assert env.eps.shape == (6, 110)
    assert env.K == COST.k_frac * env.est.sum() and env.season_total == env.est.sum()


def test_est_log_uniform():
    env = generate_environment(1, COST, 100000)
    le = np.log(env.est)
    assert abs(le.mean() - 13.357) < 0.01
    assert abs(le.std() - 0.6647) < 0.01


def test_tag_uniform():
    env = generate_environment(1, COST, 100000)
    for k in range(4):
        assert abs((env.tag == k).mean() - 0.25) < 0.01


def test_eps_normal():
    env = generate_environment(1, COST, 10000)
    assert env.eps.size == 60000
    assert abs(env.eps.mean()) < 0.001
    assert abs(env.eps.std() - 0.05) < 0.001


def _fixed_env(cost):
    z = np.zeros((1, 1))
    return Environment(seed=0, T=1, N=1, est=np.array([1_000_000.0]), yards=z.reshape(1, 1)[:, [0, 0]],
                       sites=np.zeros((1, 2)), dist=np.array([[math.e - 1]]), eta=np.array([0.01]),
                       tag=np.array([2]), tag_label=["complex"], nu=np.array([0.05]), eps=z,
                       names=["X"], K=0.0, season_total=1_000_000.0)


def test_cost_formula_exact():
    # 0.90 + 0.08*1 + 0.17*0.5 + 0.01 + 0.05 + 0 = 1.125
    assert math.isclose(cost_of(_fixed_env(COST), COST, 0, 0, 0.5), 1_125_000.0, rel_tol=1e-12)


def test_sigma_eps_and_beta0_configurable():
    d = load_defaults()["cost"]
    assert d["sigma_eps"] == 0.05 and d["beta0"] == 0.90
    env = generate_environment(2, replace(COST, sigma_eps=0.08), 10000)
    assert abs(env.eps.std() - 0.08) < 0.002
    c95 = replace(COST, beta0=0.95)
    assert math.isclose(cost_of(_fixed_env(c95), c95, 0, 0, 0.5), 1_175_000.0, rel_tol=1e-12)


def test_matched_seed_across_conditions():
    a, b = generate_environment(3, COST, 110), generate_environment(3, COST, 110)
    for f in ("est", "yards", "sites", "dist", "eta", "tag", "nu", "eps"):
        assert np.array_equal(getattr(a, f), getattr(b, f))
    assert a.names == b.names and a.tag_label == b.tag_label and a.K == b.K
    assert not np.array_equal(a.est, generate_environment(4, COST, 110).est)


def test_dist_varies():
    env = generate_environment(1, COST, 110)
    assert all(env.dist[:, t].std() > 0 for t in range(110))
    assert all(env.dist[i].std() > 0 for i in range(6))


def test_firm_names_by_seed():
    firms = json.loads((Path(__file__).resolve().parents[1] / "config" / "firms.json").read_text(encoding="utf-8"))
    n1 = firm_names(1)
    assert len(n1) == 6 and len(set(n1)) == 6 and set(n1) <= set(firms)
    assert n1 != firm_names(2)
    assert n1 == firm_names(1)
