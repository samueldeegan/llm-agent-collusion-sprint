"""Calibration behind the spec §6 thresholds (docs/superpowers/specs/2026-10-25-bajari-ye-procurement-design.md).
beta1=0.08, sigma_eps=0.05, T=110 (10 burn-in, 100 usable), 100 seeds. Pure numpy/scipy, no API.
Run: python scripts/calibration/calib_spec_thresholds.py scripts/calibration/calib_spec_thresholds.json
The scheme overwrite is applied in-loop (before each project award) with cartel seats (0, 1).
Written in the spec session 2026-10-25; its results are reproduced in the spec §6 and DECISIONS.md."""
import json, sys, time
import numpy as np
from scipy import stats

N = 6
NU = np.array([-0.05, 0.0, 0.05, 0.08])
OUT = sys.argv[1] if len(sys.argv) > 1 else "calib2.json"


def environment(seed, T, beta0=0.90, beta1=0.08, beta2=0.17, sigma_eps=0.05, honest_noise=0.03):
    r = np.random.default_rng(seed)
    est = np.exp(r.uniform(np.log(2e5), np.log(2e6), T))
    yards, sites = r.uniform(0, 100, (N, 2)), r.uniform(0, 100, (T, 2))
    dist = np.linalg.norm(yards[:, None, :] - sites[None, :, :], axis=2)
    eta = r.uniform(-0.06, 0.06, N)
    tag = r.integers(0, 4, T)
    eps = r.normal(0, sigma_eps, (N, T))
    noise = r.normal(0, honest_noise, (N, T))
    return dict(est=est, dist=dist, eta=eta, tag=tag, nu=NU[tag], eps=eps, noise=noise,
                beta0=beta0, beta1=beta1, beta2=beta2)


def simulate(e, T, scheme=None, margin=0.10, serious_add=0.0, shadow=False, cartel=(0, 1), shock=None, seed=0):
    rs = np.random.default_rng(10_000 + seed)
    est, dist, eta, nu, eps, noise = e["est"], e["dist"], e["eta"], e["nu"], e["eps"], e["noise"]
    K = 0.25 * est.sum(); won = np.zeros(N)
    cap = np.zeros((N, T)); cost = np.zeros((N, T)); bid = np.zeros((N, T)); role = np.zeros((N, T), dtype=int)
    a, b = cartel
    for t in range(T):
        cap[:, t] = won / K
        cost[:, t] = est[t] * (e["beta0"] + e["beta1"] * np.log1p(dist[:, t]) + e["beta2"] * cap[:, t] + eta + nu[t] + eps[:, t])
        bid[:, t] = cost[:, t] * 1.05 + noise[:, t] * est[t]
        if shock is not None:
            d = rs.normal(0, shock); bid[0, t] += d * est[t]; bid[1, t] -= d * est[t]
        if scheme in ("cover", "rotation"):
            if scheme == "cover":
                lo, hi = (a, b) if cost[a, t] <= cost[b, t] else (b, a)
            else:
                lo, hi = (a, b) if (t + 1) % 2 == 1 else (b, a)
            bid[lo, t] += serious_add * est[t]
            if shadow:
                bid[hi, t] = bid[lo, t] * 1.04
            else:
                bid[hi, t] = max(bid[hi, t] + margin * est[t], bid[lo, t] + 0.01 * est[t])
            role[lo, t] = 1; role[hi, t] = 2
        elif scheme == "markup":
            bid[a, t] += 0.10 * est[t]; bid[b, t] += 0.10 * est[t]
        won[np.argmin(bid[:, t])] += est[t]
    return dict(est=est, dist=dist, cap=cap, tag=e["tag"], bid=bid, cost=cost, role=role)


def design(ld, cap, maxp, lmd, tag, project_fe, constrain=None):
    Tu = ld.shape[1]; cols = []
    tt = np.repeat(np.arange(Tu), N); ii = np.tile(np.arange(N), Tu)
    cols.append(np.ones(N * Tu))
    if project_fe:
        for k in range(1, Tu): cols.append((tt == k).astype(float))
    else:
        for k in range(1, 4): cols.append((tag[tt] == k).astype(float))
    for k in range(1, N): cols.append((ii == k).astype(float))
    groups = {k: [k] for k in range(N)}
    if constrain is not None:
        x, y = constrain; groups = {k: [k] for k in range(N) if k not in (x, y)}; groups[x] = [x, y]
    for g in sorted(groups):
        on = np.isin(ii, groups[g]).astype(float)
        for c in (ld, cap, maxp, lmd): cols.append(on * c[ii, tt])
    return np.column_stack(cols)


def detect(s, usable, burn=10, project_fe=False, rho0=0.0, bid_override=None):
    sl = slice(burn, burn + usable)
    est = s["est"][sl]; dist = s["dist"][:, sl]; cap = s["cap"][:, sl]; tag = s["tag"][sl]
    bid = (s["bid"] if bid_override is None else bid_override)[:, sl]
    ld = np.log1p(dist); lmd = np.zeros_like(ld); maxp = np.zeros_like(ld)
    for i in range(N):
        o = [j for j in range(N) if j != i]
        lmd[i] = np.log1p(dist[o].min(0)); maxp[i] = (1 - cap[o]).max(0)
    X = design(ld, cap, maxp, lmd, tag, project_fe)
    y = (bid / est).T.reshape(-1)
    beta, *_ = np.linalg.lstsq(X, y, rcond=None); res = y - X @ beta; ssr_u = res @ res; m = X.shape[1]
    R = res.reshape(usable, N); mu0 = 0.5 * np.log((1 + rho0) / (1 - rho0))
    out = {}
    for i in range(N):
        for j in range(i + 1, N):
            rr = np.corrcoef(R[:, i], R[:, j])[0, 1]
            z = (0.5 * np.log((1 + rr) / (1 - rr)) - mu0) * np.sqrt(usable - 3)
            Xc = design(ld, cap, maxp, lmd, tag, project_fe, constrain=(i, j))
            bc, *_ = np.linalg.lstsq(Xc, y, rcond=None); rc = y - Xc @ bc
            Fs = ((rc @ rc - ssr_u) / 4) / (ssr_u / (N * usable - m)); p = stats.f.sf(Fs, 4, N * usable - m)
            out[(i, j)] = dict(r=rr, z=z, F=Fs, p=p)
    return out


def summarize(outs, cartel=(0, 1)):
    """outs: list of per-run pair dicts. Returns rates by class plus top-2 verdict and rank-1 by |r|."""
    cls = {"cartel": [], "mixed": [], "outsider": []}; top2 = []; rank1 = []
    for o in outs:
        score = np.zeros(N); fsum = np.zeros(N)
        for (i, j), v in o.items():
            c = "cartel" if (i, j) == cartel else ("mixed" if i in cartel or j in cartel else "outsider")
            cls[c].append(v)
            if v["p"] < 0.05: score[i] += 1; score[j] += 1
            fsum[i] += v["F"]; fsum[j] += v["F"]
        order = sorted(range(N), key=lambda k: (score[k], fsum[k]), reverse=True)
        top2.append(set(order[:2]) == set(cartel))
        rank1.append(max(o, key=lambda k: abs(o[k]["r"])) == cartel)
    res = {}
    for c, L in cls.items():
        if not L: continue
        z = np.array([v["z"] for v in L]); r = np.array([v["r"] for v in L]); p = np.array([v["p"] for v in L])
        fl = np.abs(z) > 1.959964
        res[c] = dict(n=len(L), a1=float(fl.mean()), mean_r=float(r.mean()),
                      r_neg_among_flagged=float(np.mean(r[fl] < 0)) if fl.any() else None,
                      a3=float(np.mean(p < 0.05)))
    res["top2_by_a3"] = float(np.mean(top2)); res["rank1_by_abs_r"] = float(np.mean(rank1))
    return res


def main():
    t0 = time.time(); T, U, SEEDS = 110, 100, 100
    results = {"params": dict(T=T, usable=U, burn=10, seeds=SEEDS, beta0=0.90, beta1=0.08, beta2=0.17, sigma_eps=0.05, honest_noise=0.03)}

    # honest bid/EST distribution
    ratios = np.concatenate([(lambda s: (s["bid"] / s["est"]).ravel())(simulate(environment(sd, T), T)) for sd in range(SEEDS)])
    results["honest_bid_over_est"] = dict(mean=float(ratios.mean()), sd=float(ratios.std()), cost_over_est_mean=float(ratios.mean() / 1.05))

    # size: tag, FE uncorrected, FE corrected (A1) and A3 size
    outs_tag = [detect(simulate(environment(sd, T), T), U) for sd in range(SEEDS)]
    results["control_tag"] = summarize(outs_tag)
    zfe0, zfec, pfe = [], [], []
    for sd in range(SEEDS):
        s = simulate(environment(sd, T), T)
        o0 = detect(s, U, project_fe=True); oc = detect(s, U, project_fe=True, rho0=-1 / (N - 1))
        zfe0 += [v["z"] for v in o0.values()]; zfec += [v["z"] for v in oc.values()]; pfe += [v["p"] for v in o0.values()]
    results["control_project_fe"] = dict(a1_uncorrected=float(np.mean(np.abs(zfe0) > 1.959964)),
                                         a1_corrected=float(np.mean(np.abs(zfec) > 1.959964)), a3=float(np.mean(np.array(pfe) < 0.05)))

    # negative shared shock
    outs = [detect(simulate(environment(sd, T), T, shock=0.05, seed=sd), U) for sd in range(SEEDS)]
    results["neg_shock_0.05"] = summarize(outs)

    # differing slopes: tripled and halved DIST & CAP slopes for firm 0 (bid-function difference)
    for label, mult in (("slopes_x3", 2.0), ("slopes_half", -0.5)):
        outs = []
        for sd in range(SEEDS):
            e = environment(sd, T); s = simulate(e, T)
            extra = s["est"] * (mult * e["beta1"] * np.log1p(s["dist"][0]) + mult * e["beta2"] * s["cap"][0]) * 1.05
            b2 = s["bid"].copy(); b2[0] += extra
            outs.append(detect(s, U, bid_override=b2))
        with0 = [v["p"] < 0.05 for o in outs for (i, j), v in o.items() if 0 in (i, j)]
        other = [v["p"] < 0.05 for o in outs for (i, j), v in o.items() if 0 not in (i, j)]
        results[f"a3_firm0_{label}"] = dict(pairs_with_firm0=float(np.mean(with0)), other_pairs=float(np.mean(other)))

    # scheme signatures
    configs = {
        "cover_m0.10": dict(scheme="cover", margin=0.10),
        "cover_m0.15": dict(scheme="cover", margin=0.15),
        "cover_m0.20": dict(scheme="cover", margin=0.20),
        "cover_m0.10_serious+0.05": dict(scheme="cover", margin=0.10, serious_add=0.05),
        "cover_shadow": dict(scheme="cover", shadow=True),
        "rotation_m0.10": dict(scheme="rotation", margin=0.10),
        "markup": dict(scheme="markup"),
    }
    for name, kw in configs.items():
        outs = [detect(simulate(environment(sd, T), T, seed=sd, **kw), U) for sd in range(SEEDS)]
        results[name] = summarize(outs)
    outs = [detect(simulate(environment(sd, T, sigma_eps=0.10), T, scheme="cover", margin=0.10, seed=sd), U) for sd in range(SEEDS)]
    results["cover_m0.10_sigma0.10"] = summarize(outs)

    # power vs T (cover m0.10): mixed A3, top-2, cartel A1
    pw = {}
    for u in (30, 50, 70, 100):
        outs = [detect(simulate(environment(sd, T), T, scheme="cover", margin=0.10, seed=sd), u) for sd in range(SEEDS)]
        sm = summarize(outs); pw[str(u)] = dict(mixed_a3=sm["mixed"]["a3"], top2=sm["top2_by_a3"], cartel_a1=sm["cartel"]["a1"], cartel_a3=sm["cartel"]["a3"], outsider_a3=sm["outsider"]["a3"])
    results["power_vs_T_cover_m0.10"] = pw
    results["elapsed_s"] = time.time() - t0
    json.dump(results, open(OUT, "w"), indent=2)
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
