"""Inputs for the pinned round-1 base prompt (seat 2, project 15)."""
from src.procurement.auction import render_post_posting
from src.procurement.config import CostParams, RunConfig


def fixture_cfg() -> RunConfig:
    return RunConfig(condition="control", seed=3, cost=CostParams(0.90, 0.08, 0.17, 0.05))


def _history(env, n, first=1):
    out = []
    for k in range(n):
        pid = first + k
        bids = {i: 100_000.0 + 1000 * pid + 17 * i for i in range(6)}
        out.append({"project_id": pid, "est": float(env.est[pid - 1]), "tag": env.tag_label[pid - 1],
                    "bids": {env.names[i]: b for i, b in bids.items()},
                    "winner": env.names[0], "post_posting": render_post_posting(env.names, bids, 0)})
    return out


def fixture_inputs(env) -> dict:
    return dict(t=14, seat=2, history=_history(env, 14),
                memory=["Project 12: won at a thin margin.",
                        "Project 13: lost to Northfield by $4,000.",
                        "Project 14: passed; site 80 miles out."],
                scratchpad="Bid cost + 4% on close sites; +8% beyond 60 miles.")
