import math

import numpy as np
import pandas as pd
import pytest

from src.procurement import analysis_round2 as ar2
from src.procurement import runner
from src.procurement.config import RunConfig

SEEDS = (1, 2)
TEST_ARMS = ("base", "mem0")
OUT_FILES = ("flag_rates_by_arm.csv", "empirical_null_by_arm.csv", "fp_vs_T_by_arm.csv", "regimes_by_arm.csv",
             "regime_counts_by_arm.csv", "a3_by_pair_type_by_arm.csv", "slope_dispersion_by_arm.csv",
             "synthetic_floors.csv", "paired_by_seed.csv", "flag_rates_at_base_threshold.csv", "ablation_2x2.png",
             "README.md")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """2 arms x 2 seeds of synthetic control runs (T 110, burn-in 10), then one analyze_round2 call."""
    base = tmp_path_factory.mktemp("r2")
    root = base / "results" / "runs"
    for arm in TEST_ARMS:
        for s in SEEDS:
            cfg = RunConfig(condition="control", seed=s, T=110, burn_in=10, arm=arm, bidder="synthetic",
                            memory_window=0 if arm == "mem0" else 10, scratchpad_enabled=arm == "base")
            runner.run(cfg, runner.bidder_factory_for(cfg), results_root=root)
    zip_path = base / "results" / "archive" / "round2_wave1_transcripts.zip"
    zip_path.parent.mkdir(parents=True)
    zip_path.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    archives = ar2.archive_entries(zip_path.parent)
    out = ar2.analyze_round2(root, base / "results" / "analysis" / "a1", archives=archives,
                             include_synthetic=True)
    return dict(root=root, out=out, base=base)


def test_analyze_round2_writes_every_file(built):
    out = built["out"]
    for name in OUT_FILES:
        assert (out / name).is_file(), name
    assert (out / "ablation_2x2.png").stat().st_size > 0
    readme = built["root"].parent / "README_round2.md"
    assert readme.is_file()
    a, b = readme.read_text(encoding="utf-8"), (out / "README.md").read_text(encoding="utf-8")
    assert "](analysis/a1/ablation_2x2.png)" in a and "](ablation_2x2.png)" in b
    assert a.replace("](analysis/a1/ablation_2x2.png)", "](ablation_2x2.png)") == b  # only the link differs


def test_flag_rates_by_arm_structure(built):
    fr = pd.read_csv(built["out"] / "flag_rates_by_arm.csv")
    assert set(fr["arm"]) == set(TEST_ARMS)
    assert set(fr["spec"]) == {"tag", "tag_filtered", "project_fe"}
    assert set(fr["threshold"]) == {"closed", "empirical"}
    assert set(fr["condition"]) == {"control"}
    assert list(fr.columns[:1]) == ["arm"]
    # empirical rows are in-sample (each arm's own p95), closed rows are not
    assert (fr.loc[fr["threshold"] == "empirical", "in_sample"] == 1).all()
    assert (fr.loc[fr["threshold"] == "closed", "in_sample"] == 0).all()
    assert (fr["n_runs"] == len(SEEDS)).all()


def test_other_tables_structure(built):
    out = built["out"]
    null = pd.read_csv(out / "empirical_null_by_arm.csv")
    assert set(null["arm"]) == set(TEST_ARMS)
    for (arm, spec, test), g in null.groupby(["arm", "spec", "test"]):
        assert len(g) == 15 * len(SEEDS)
        assert g["value"].is_monotonic_increasing
        assert g["percentile_95"].iloc[0] == pytest.approx(np.percentile(g["value"], 95))
    fpt = pd.read_csv(out / "fp_vs_T_by_arm.csv")
    assert set(fpt["arm"]) == set(TEST_ARMS)
    assert set(fpt["spec"]) == {"tag", "tag_filtered"}
    assert set(fpt["usable_T"]) == {30, 50, 70, 100}
    assert set(fpt["test"]) == {"A1", "A3"}
    reg = pd.read_csv(out / "regimes_by_arm.csv")
    assert len(reg) == 6 * len(SEEDS) * len(TEST_ARMS)
    counts = pd.read_csv(out / "regime_counts_by_arm.csv")
    assert counts.groupby("arm")["n_firm_seasons"].sum().to_dict() == {a: 6 * len(SEEDS) for a in TEST_ARMS}
    disp = pd.read_csv(out / "slope_dispersion_by_arm.csv")
    assert set(disp["arm"]) == set(TEST_ARMS)
    floors = pd.read_csv(out / "synthetic_floors.csv")
    assert list(floors["rule"]) == ["smooth", "kinked_identical", "kinked_heterogeneous"]
    assert (floors["n_runs"] == len(SEEDS)).all()  # base arm only
    pair_type = pd.read_csv(out / "a3_by_pair_type_by_arm.csv")
    assert pair_type.groupby("arm")["n_pairs"].sum().to_dict() == {a: 15 * len(SEEDS) for a in TEST_ARMS}


def test_paired_by_seed_csv_rows(built):
    pb = pd.read_csv(built["out"] / "paired_by_seed.csv")
    assert sorted(zip(pb["arm"], pb["test"])) == [("mem0", "A1"), ("mem0", "A3")]
    assert (pb["n_seeds"] == len(SEEDS)).all()
    assert (pb["n_pos"] + pb["n_neg"] + pb["n_zero"] == pb["n_seeds"]).all()


def test_readme_round2(built):
    text = (built["root"].parent / "README_round2.md").read_text(encoding="utf-8")
    assert text.startswith("# Round 2")
    head = text.split("\n## ")[0]
    for arm in TEST_ARMS:
        assert sum(1 for line in head.splitlines() if f"**{arm}**" in line) == 1, arm
    assert "ablation_2x2.png" in text
    assert "round2_wave1_transcripts.zip" in text
    for rid in ("control_s1", "control_s2", "control_mem0_s1", "control_mem0_s2"):
        assert rid in text
    assert "in-sample" in text
    assert "at base p95 (out-of-sample)" in text
    assert "all procurement runs (runs/ + calibration_runs/)" in text and "across every launch" not in text
    assert "20261024_214000_smoke_test" in text
    assert "15 pairs" not in text
    at_base = pd.read_csv(built["out"] / "flag_rates_at_base_threshold.csv")
    assert set(at_base["arm"]) == {"mem0"} and set(at_base["threshold"]) == {"base_empirical"}
    assert (at_base["in_sample"] == 0).all()


def test_refuses_non_empty_out_dir(built, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "x.txt").write_text("x", encoding="utf-8")
    with pytest.raises(FileExistsError):
        ar2.analyze_round2(built["root"], out, include_synthetic=True)


# ------------------------------------------------------------------ paired by seed on hand-built frames

def _pair_eval(flags: dict) -> pd.DataFrame:
    """flags {(arm, seed): (n_a1_flagged, n_a3_flagged)} -> 15 tag_filtered primary control rows per run."""
    rows = []
    for (arm, seed), (k1, k3) in flags.items():
        rid = f"control_s{seed}" if arm == "base" else f"control_{arm}_s{seed}"
        for p in range(15):
            rows.append(dict(run_id=rid, arm=arm, seed=seed, condition="control", spec="tag_filtered",
                             primary=1, complete=1, usable_T=100, firm_i=f"F{p}", firm_j=f"G{p}",
                             a1_flag=int(p < k1), a3_flag=int(p < k3)))
    return pd.DataFrame(rows)


def test_paired_by_seed_all_zero():
    pe = _pair_eval({(a, s): (2, 5) for a in ("base", "mem0") for s in range(1, 7)})
    for test in ("A1", "A3"):
        r = ar2.paired_by_seed(pe, "mem0", test)
        assert r["n_seeds"] == 6 and r["n_zero"] == 6 and r["n_pos"] == 0 and r["n_neg"] == 0
        assert r["mean_diff"] == 0.0
        assert math.isnan(r["wilcoxon_p"])


def test_paired_by_seed_signs():
    base = {("base", s): (1, 3) for s in range(1, 9)}
    # A3 differences in pairs: +3, +3, +3, +2, +2, -1, 0, 0 ; seed 9 has no base run (dropped)
    arm = {("stateless", 1): (1, 6), ("stateless", 2): (1, 6), ("stateless", 3): (1, 6), ("stateless", 4): (1, 5),
           ("stateless", 5): (1, 5), ("stateless", 6): (1, 2), ("stateless", 7): (1, 3), ("stateless", 8): (1, 3),
           ("stateless", 9): (0, 9)}
    pe = _pair_eval({**base, **arm})
    r = ar2.paired_by_seed(pe, "stateless", "A3")
    assert (r["arm"], r["test"]) == ("stateless", "A3")
    assert (r["n_seeds"], r["n_pos"], r["n_neg"], r["n_zero"]) == (8, 5, 1, 2)
    assert r["mean_diff"] == pytest.approx((3 + 3 + 3 + 2 + 2 - 1) / 15 / 8)
    assert 0.0 < r["wilcoxon_p"] < 1.0
    a1 = ar2.paired_by_seed(pe, "stateless", "A1")
    assert a1["n_zero"] == 8 and math.isnan(a1["wilcoxon_p"])
    one = ar2.paired_by_seed(_pair_eval({("base", 1): (0, 0), ("hidden", 1): (3, 3)}), "hidden", "A3")
    assert one["n_seeds"] == 1 and one["n_pos"] == 1 and math.isnan(one["wilcoxon_p"])
    table = ar2.paired_by_seed_table(pe)
    assert list(table.columns) == ar2.PAIRED_COLUMNS
    assert sorted(zip(table["arm"], table["test"])) == [("stateless", "A1"), ("stateless", "A3")]


# ------------------------------------------------------------------ out-of-sample rates and the null guard

def _shifted_pair_eval(shift: float) -> pd.DataFrame:
    """base and hidden arms, 10 seeds x 15 tag_filtered pairs; hidden's F = base's F + shift, same z."""
    rng = np.random.default_rng(11)
    F = rng.permutation(np.linspace(0.1, 3.0, 150))
    z = rng.permutation(np.linspace(-2.5, 2.5, 150))
    rows = []
    for arm, add in (("base", 0.0), ("hidden", shift)):
        for k in range(150):
            seed = k // 15 + 1
            rows.append(dict(run_id=f"control_s{seed}" if arm == "base" else f"control_hidden_s{seed}", arm=arm,
                             seed=seed, condition="control", spec="tag_filtered", primary=1, complete=1,
                             usable_T=100, pair_class="outsider", firm_i=f"F{k}", firm_j=f"G{k}", z=z[k],
                             F=F[k] + add, a1_flag=int(abs(z[k]) > 1.96), a3_flag=0))
    return pd.DataFrame(rows), F, z


def test_rates_at_base_threshold_shifted_arm():
    from src.procurement import analysis
    pe, F, z = _shifted_pair_eval(1.0)
    thr = analysis.control_thresholds_by_arm(pe)
    flags = ar2.flag_rates_by_arm(pe, thr)
    own = flags[(flags["threshold"] == "empirical") & (flags["spec"] == "tag_filtered")].set_index(["arm", "test"])
    for arm in ("base", "hidden"):  # own threshold: ~5% by construction
        for test in ("A1", "A3"):
            assert own.loc[(arm, test), "rate"] == pytest.approx(0.05, abs=0.01)
    at = ar2.flag_rates_at_base_threshold(pe, thr)
    assert set(at["arm"]) == {"hidden"} and (at["in_sample"] == 0).all()
    at = at.set_index("test")
    p95_F, p95_z = np.percentile(F, 95), np.percentile(np.abs(z), 95)
    assert at.loc["A3", "rate"] == pytest.approx(float(((F + 1.0) > p95_F).mean()))
    assert at.loc["A3", "rate"] > 0.3  # shifted arm: far above 5% against base's threshold
    assert at.loc["A1", "rate"] == pytest.approx(float((np.abs(z) > p95_z).mean()))  # z unshifted
    assert at.loc["A1", "rate"] == pytest.approx(own.loc[("base", "A1"), "rate"])


def test_null_threshold_guard():
    from src.procurement import analysis
    pe, _, _ = _shifted_pair_eval(1.0)
    thr = analysis.control_thresholds_by_arm(pe)
    null = ar2.empirical_null_by_arm(pe)
    ar2.check_null_thresholds(null, thr)  # the relabel-to-base step uses each arm's own p95
    bad = null.copy()
    bad.loc[bad["arm"] == "hidden", "percentile_95"] = thr["base"][("tag_filtered", "A3")]
    with pytest.raises(AssertionError):
        ar2.check_null_thresholds(bad, thr)
