"""Project environment (sites, yards, estimates) and the cost model."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from src.procurement.config import CostParams


@dataclass(frozen=True)
class Environment:
    seed: int
    T: int
    N: int
    est: np.ndarray
    yards: np.ndarray
    sites: np.ndarray
    dist: np.ndarray
    eta: np.ndarray
    tag: np.ndarray
    tag_label: list[str]
    nu: np.ndarray
    eps: np.ndarray
    names: list[str]
    K: float
    season_total: float


def firm_names(seed: int) -> list[str]:
    path = Path(__file__).resolve().parents[2] / "config" / "firms.json"
    with open(path, encoding="utf-8") as f:
        names = list(json.load(f))
    rng = np.random.default_rng(1000 + seed)
    return [names[i] for i in rng.permutation(len(names))]


def generate_environment(seed: int, cost: CostParams, T: int, N: int = 6) -> Environment:
    rng = np.random.default_rng(seed)
    est = np.round(np.exp(rng.uniform(np.log(cost.est_min), np.log(cost.est_max), T)))
    yards = rng.uniform(0, cost.map_size, (N, 2))
    sites = rng.uniform(0, cost.map_size, (T, 2))
    eta = rng.uniform(-cost.eta_range, cost.eta_range, N)
    tag = rng.integers(0, 4, T)
    eps = rng.normal(0, cost.sigma_eps, (N, T))
    dist = np.linalg.norm(yards[:, None] - sites[None], axis=2)
    nu = np.array(cost.nu_levels)[tag]
    return Environment(
        seed=seed, T=T, N=N, est=est, yards=yards, sites=sites, dist=dist, eta=eta,
        tag=tag, tag_label=[cost.tag_labels[t] for t in tag], nu=nu, eps=eps,
        names=firm_names(seed), K=cost.k_frac * float(est.sum()), season_total=float(est.sum()),
    )


def cost_of(env: Environment, cost: CostParams, seat: int, t: int, cap: float) -> float:
    """Cost of project t (0-based) for a seat at capacity utilisation cap."""
    return float(env.est[t] * (cost.beta0 + cost.beta1 * np.log1p(env.dist[seat, t])
                               + cost.beta2 * cap + env.eta[seat] + env.nu[t] + env.eps[seat, t]))
