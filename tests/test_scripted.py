"""Scripted robustness rows (spec §4e "Scripted robustness rows are not run directories", §6.11
``scripted_signatures_module``): in-memory ``simulate_table`` + ``detect_frames``, no run directories."""
from itertools import product

import numpy as np
import pandas as pd
import pytest

from src.procurement import scripted
from src.procurement.scripted import SCRIPTED_COLUMNS, SCRIPTED_ROWS, main, run_scripted
from tests.conftest import api_guard

ROW_NAMES = ["cover_m0.10", "cover_m0.15", "cover_m0.20", "cover_m0.10_serious+0.05", "cover_m0.10_sigma0.10",
             "cover_shadow", "rotation_m0.10", "markup", "control"]
COLUMNS = ["row", "margin", "rule", "serious_add", "sigma_eps", "pair_class", "test", "n", "rate", "mean_r",
           "top2", "rank1"]
CLASSES = ("cartel", "mixed", "outsider")
TESTS = ("A1", "A3")


@pytest.fixture(scope="module")
def five_seeds():
    with api_guard():  # the function-scoped guard is not active in module fixtures
        return run_scripted(seeds=5)


def test_scripted_signatures_module(five_seeds, tmp_path, monkeypatch):
    assert [r[0] for r in SCRIPTED_ROWS] == ROW_NAMES
    assert all(len(r) == 6 for r in SCRIPTED_ROWS)
    assert SCRIPTED_COLUMNS == COLUMNS
    df = five_seeds
    assert list(df.columns) == COLUMNS
    assert len(df) == 9 * 3 * 2
    assert set(zip(df["row"], df["pair_class"], df["test"])) == set(product(ROW_NAMES, CLASSES, TESTS))
    # 5 seeds x (1 cartel, 8 mixed, 6 outsider) pairs
    assert dict(df.groupby("pair_class")["n"].first()) == {"cartel": 5, "mixed": 40, "outsider": 30}
    assert ((df["rate"] >= 0) & (df["rate"] <= 1)).all()
    for name, g in df.groupby("row"):
        assert g["top2"].nunique() == 1 and g["rank1"].nunique() == 1, name  # run-level, repeated
        for c in CLASSES:  # mean_r is the class mean, the same on the A1 and A3 rows
            assert g.loc[g.pair_class == c, "mean_r"].nunique() == 1

    def cell(row, cls, test, col="rate"):
        (v,) = df.loc[(df.row == row) & (df.pair_class == cls) & (df.test == test), col]
        return v

    # row parameters as configured in §6.6
    sig = df[df.row == "cover_m0.10_sigma0.10"]
    assert set(sig["sigma_eps"]) == {0.10}
    assert set(df.loc[df.row != "cover_m0.10_sigma0.10", "sigma_eps"]) == {0.05}
    assert set(df.loc[df.row == "cover_m0.10_serious+0.05", "serious_add"]) == {0.05}
    assert set(df.loc[df.row == "cover_m0.20", "margin"]) == {0.20}
    assert set(df.loc[df.row == "cover_shadow", "rule"]) == {"shadow"}
    assert df.loc[df.row == "cover_shadow", "margin"].isna().all()
    assert set(df.loc[df.row == "rotation_m0.10", "rule"]) == {"inflate"}
    # signatures visible even at 5 seeds (sim at 100: 0.99 / -0.50; shadow +0.67)
    assert cell("cover_m0.10", "cartel", "A1") >= 0.8
    assert cell("cover_m0.10", "cartel", "A1", "mean_r") < -0.2
    assert cell("cover_shadow", "cartel", "A1", "mean_r") > 0.2
    assert abs(cell("control", "cartel", "A1", "mean_r")) < 0.3
    assert cell("cover_m0.10", "cartel", "A1", "rank1") >= 0.8

    # CLI: explicit csv path, never overwritten
    out = tmp_path / "x" / "scripted_signatures.csv"
    main(["--seeds", "2", "--out", str(out)])
    got = pd.read_csv(out, keep_default_na=False, dtype=str)
    assert list(got.columns) == COLUMNS and len(got) == 54
    assert (got["margin"][got["row"] == "cover_shadow"] == "").all()  # missing = empty field
    with pytest.raises(FileExistsError):
        main(["--seeds", "2", "--out", str(out)])
    # CLI default: a new UTC directory under the analysis root holding scripted_signatures.csv
    monkeypatch.setattr(scripted, "ANALYSIS_ROOT", str(tmp_path / "analysis"))
    main(["--seeds", "1", "--extra-margin", "0.13"])
    main(["--seeds", "1"])
    dirs = sorted((tmp_path / "analysis").iterdir())
    assert len(dirs) == 2
    first = pd.read_csv(dirs[0] / "scripted_signatures.csv")
    assert "cover_m0.13" in set(first["row"]) and len(first) == 60
    assert set(first.loc[first.row == "cover_m0.13", "margin"]) == {0.13}


def test_scripted_extra_margin_and_determinism():
    a = run_scripted(seeds=2, extra_margin=0.13)
    b = run_scripted(seeds=2, extra_margin=0.13)
    pd.testing.assert_frame_equal(a, b)
    assert list(dict.fromkeys(a["row"])) == ROW_NAMES + ["cover_m0.13"]
    extra = a[a.row == "cover_m0.13"]
    assert len(extra) == 6 and set(extra["rule"]) == {"inflate"} and set(extra["margin"]) == {0.13}
    # an extra margin equal to an existing row adds nothing
    assert len(run_scripted(seeds=1, extra_margin=0.10)) == 54
    # n counts pairs; rate is n_flagged / n
    k = a["rate"] * a["n"]
    assert np.allclose(k, k.round())


def test_cli_relative_out_resolves_against_repo_root(tmp_path, monkeypatch):
    """C4: a relative --out (and the default analysis root) resolve against the repository root."""
    from src.procurement import config
    fake_repo = tmp_path / "repo"
    monkeypatch.setattr(config, "repo_root", lambda: fake_repo)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    path = main(["--seeds", "1", "--out", "results/analysis/x/scripted_signatures.csv"])
    assert path == fake_repo / "results" / "analysis" / "x" / "scripted_signatures.csv" and path.is_file()
    path2 = main(["--seeds", "1"])
    assert path2.parent.parent == fake_repo / "results" / "analysis"
    assert not (elsewhere / "results").exists()
