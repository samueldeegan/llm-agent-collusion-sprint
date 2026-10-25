import anthropic
import pytest

from src.procurement.config import (Pricing, cartel_seats, first_speaker, load_defaults,
                                    run_plan, sizing_rule)


def test_cartel_seat_rotation():
    k = {"cover": 1, "rotation": 2, "markup": 3}
    for r in range(1, 13):
        pairs = {cartel_seats(r, c) for c in k}
        assert len(pairs) == 3
        for c, kk in k.items():
            a = (r + kk - 2) % 6
            assert cartel_seats(r, c) == (a, (a + 3) % 6)
    assert cartel_seats(1, "control") is None
    all_pairs = {frozenset(cartel_seats(r, c)) for r in range(1, 13) for c in k}
    assert all_pairs == {frozenset({0, 3}), frozenset({1, 4}), frozenset({2, 5})}
    assert {s for r in range(1, 13) for c in k for s in cartel_seats(r, c)} == set(range(6))


def test_first_speaker_rule():
    assert first_speaker(1, "cover", (2, 5)) == 2 and first_speaker(2, "cover", (2, 5)) == 5
    assert first_speaker(1, "markup", (2, 5)) == 2
    assert first_speaker(2, "markup", (2, 5)) == 5 and first_speaker(3, "markup", (2, 5)) == 2
    assert first_speaker(1, "rotation", (2, 5)) == 2 and first_speaker(2, "rotation", (2, 5)) == 5  # scheduled serious bidder


def test_run_plan_counts():
    full = run_plan({"control": 12, "cover": 12, "markup": 4, "rotation": 4})
    assert len(full) == 32 and sorted(c.seed for c in full if c.condition == "control") == list(range(1, 13))
    assert len(run_plan({"control": 8, "cover": 8, "markup": 4, "rotation": 4})) == 24
    assert len(run_plan({"control": 12, "cover": 12, "markup": 4, "rotation": 4}, include_rotation=False)) == 28
    ladder = run_plan({"control": 12, "cover": 12, "markup": 2, "rotation": 0}, T=80)
    assert all(c.T == 80 for c in ladder) and all(c.T - c.burn_in == 70 for c in ladder)


def test_sizing_rule():
    assert sizing_rule(1_200_000, False) == {"control": 12, "cover": 12, "markup": 4, "rotation": 4}
    assert sizing_rule(400_000, True) == {"control": 12, "cover": 12, "markup": 4, "rotation": 4}
    assert sizing_rule(400_000, False) == {"control": 8, "cover": 8, "markup": 4, "rotation": 4}


def test_pricing():
    assert abs(Pricing().cost_usd(1_000_000, 1_000_000, 1_000_000, 1_000_000) - (0.10 + 0.50 + 0.125 + 0.01)) < 1e-9


def test_defaults_file_matches_spec():
    d = load_defaults()
    assert d["T"] == 110 and d["burn_in"] == 10 and d["cost"]["beta1"] == 0.08 and d["cost"]["sigma_eps"] == 0.05
    assert d["model"] == "claude-haiku-5-5" and d["max_tokens"] == 8000 and d["usable_T"] == [30, 50, 70, 100]


def test_no_api_calls_guard():
    with pytest.raises(RuntimeError, match="API client constructed inside the test suite"):
        anthropic.Anthropic(api_key="x")


def test_run_plan_rejects_unknown_condition():
    with pytest.raises(ValueError):
        run_plan({"control": 1, "covr": 1})
    assert len(run_plan({"control": 2, "cover": 1, "markup": 1}, include_rotation=False)) == 4
