"""The four hand-run scripts (smoke_llm, cover_smoke, pilot, tier_check), run offline: the synthetic
bidder, ``tests.stubs.StubClient`` or a fake transport stands in for the API. Every file lives under
``tmp_path``; no test constructs a real client (the conftest guard makes that raise)."""
import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

from src.procurement import client, fidelity, io, launcher, pilot
from src.procurement.client import TransportError
from src.procurement.llm_bidder import llm_bidder_factory
from src.procurement.synthetic import synthetic_bidder_factory
from tests.stubs import StubClient

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = ("smoke_llm", "cover_smoke", "pilot", "tier_check")
SECRET = "sk-ant-test-SECRET-do-not-print-91ab"
SECRET2 = "sk-ant-test-SECRET2-do-not-print-44cd"


def load_script(name: str):
    path = REPO / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_hand_script_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    monkeypatch.delenv("ANTHROPIC_API_KEY_2", raising=False)


def stub_factory(stub: StubClient):
    def factory(cfg, env):
        stub.names = list(env.names)
        return llm_bidder_factory(cfg, env, client=stub)
    return factory


def _cal(tmp_path) -> dict:
    return json.loads((tmp_path / "calibration.json").read_text(encoding="utf-8"))


def _run_dirs(tmp_path) -> list[Path]:
    root = tmp_path / "calibration_runs"
    return sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []


def _no_secret(tmp_path, *texts):
    for t in texts:
        assert SECRET not in t and SECRET2 not in t
    for p in tmp_path.rglob("*"):
        if p.is_file():
            data = p.read_text(encoding="utf-8", errors="replace")
            assert SECRET not in data and SECRET2 not in data, p


# ------------------------------------------------------------------ import

def test_scripts_import_without_api(monkeypatch):
    """Importing a script constructs no client and no transport (the conftest guard covers the SDK)."""
    def boom(*a, **k):
        raise AssertionError("client constructed at import")
    monkeypatch.setattr(client.HaikuClient, "__init__", boom)
    monkeypatch.setattr(client._DefaultTransport, "__init__", boom)
    for name in SCRIPTS:
        mod = load_script(name)
        assert callable(mod.main)
        assert str(REPO) in sys.path


# ------------------------------------------------------------------ keys

@pytest.mark.parametrize("name", SCRIPTS)
def test_missing_key_exit_5_no_echo(name, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY_2", SECRET2)
    mod = load_script(name)
    args = {"smoke_llm": [], "cover_smoke": ["--phase", "prefreeze", "--config-dir", str(tmp_path)],
            "pilot": ["--config-dir", str(tmp_path)], "tier_check": []}[name]
    code = mod.main(["--results-dir", str(tmp_path), *args], bidder_factory=synthetic_bidder_factory,
                    transport=None)
    out = capsys.readouterr()
    assert code == 5
    assert "ANTHROPIC_API_KEY" in out.out + out.err
    _no_secret(tmp_path, out.out, out.err)
    assert not _run_dirs(tmp_path) and not (tmp_path / "calibration.json").exists()


# ------------------------------------------------------------------ smoke

def test_smoke_offline(tmp_path, key, capsys):
    mod = load_script("smoke_llm")
    assert mod.main(["--results-dir", str(tmp_path)], bidder_factory=synthetic_bidder_factory) == 0
    out = capsys.readouterr()
    (d,) = _run_dirs(tmp_path)
    meta = io.read_run_meta(d)
    assert meta["purpose"] == "smoke" and meta["T"] == 3 and meta["condition"] == "control"
    assert meta["finished_utc"] is not None
    cal = _cal(tmp_path)
    assert cal["smoke"]["parsed"] == 18 and cal["smoke"]["n_bids"] == 18
    assert cal["smoke"]["run_dir"] == str(d)
    assert cal["measured"]["source"] == "smoke" and cal["measured"]["parse_rate"] == 1.0
    assert set(cal["measured"]) >= {"mean_latency_s", "mean_input_tokens", "mean_output_tokens", "n_calls",
                                    "parse_rate", "source"}
    assert "18/18" in out.out and "cost" in out.out.lower()
    _no_secret(tmp_path, out.out, out.err)


def test_smoke_with_stub_client_measures_calls(tmp_path, key, capsys):
    stub = StubClient()
    mod = load_script("smoke_llm")
    assert mod.main(["--results-dir", str(tmp_path)], bidder_factory=stub_factory(stub)) == 0
    m = _cal(tmp_path)["measured"]
    assert m["n_calls"] == 18 and m["mean_latency_s"] == pytest.approx(0.003)
    assert m["mean_input_tokens"] == 100 and m["mean_output_tokens"] == 20
    cal = launcher.read_calibration  # the launcher reads latency only together with limits
    assert cal(tmp_path / "calibration.json") is None
    s = _cal(tmp_path)["smoke"]
    assert s["input_tokens"] == 1800 and s["output_tokens"] == 360
    assert s["cost_usd"] == pytest.approx((1800 * 0.10 + 360 * 0.50) / 1e6)


def test_smoke_through_fake_transport(tmp_path, key, capsys):
    """``transport=`` runs the real LLM bidder on a real ``HaikuClient`` over a fake SDK transport."""
    seen = []

    def transport(kwargs):
        seen.append(kwargs)
        user = kwargs["messages"][0]["content"]
        bid = round(StubClient.cost_of(user) * 1.07, 2)
        body = {"content": [{"type": "text", "text": json.dumps({"reflection": "", "plan": "", "bid": bid,
                                                                  "new_memory": "", "scratch_pad_update": ""})}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 3000, "output_tokens": 500, "cache_read_input_tokens": 1000,
                          "cache_creation_input_tokens": 0}}
        return body, {"anthropic-ratelimit-requests-remaining": "999"}
    mod = load_script("smoke_llm")
    assert mod.main(["--results-dir", str(tmp_path)], transport=transport) == 0
    assert len(seen) == 18 and all(k["model"] == "claude-haiku-5-5" and k["max_tokens"] == 8000 for k in seen)
    cal = _cal(tmp_path)
    assert cal["measured"]["n_calls"] == 18 and cal["measured"]["mean_input_tokens"] == 3000
    assert cal["smoke"]["cache_read_input_tokens"] == 18000
    assert cal["smoke"]["cost_usd"] == pytest.approx((54000 * 0.10 + 9000 * 0.50 + 18000 * 0.01) / 1e6)
    _no_secret(tmp_path, capsys.readouterr().out)


def test_smoke_exit_1_below_18(tmp_path, key, capsys):
    stub = StubClient(replies={(2, "bid", 2): "no json here"})
    mod = load_script("smoke_llm")
    assert mod.main(["--results-dir", str(tmp_path)], bidder_factory=stub_factory(stub)) == 1
    assert _cal(tmp_path)["smoke"]["parsed"] == 17
    assert "17/18" in capsys.readouterr().out


# ------------------------------------------------------------------ cover smoke

def test_cover_smoke_prefreeze_offline(tmp_path, key, capsys):
    mod = load_script("cover_smoke")
    code = mod.main(["--phase", "prefreeze", "--results-dir", str(tmp_path), "--config-dir", str(tmp_path),
                     "--seeds", "3"], bidder_factory=synthetic_bidder_factory)
    out = capsys.readouterr()
    assert code == 0, out.out + out.err
    (d,) = _run_dirs(tmp_path)
    meta = io.read_run_meta(d)
    assert meta["purpose"] == "cover_smoke" and meta["T"] == 10 and meta["condition"] == "cover"
    sec = _cal(tmp_path)["cover_smoke_prefreeze"]
    assert sec["realized_rule"] == "inflate" and sec["fidelity"] == 1.0
    assert sec["phase"] == "prefreeze" and sec["beta0"] == 0.90 and sec["run_dir"] == str(d)
    assert len(sec["margins"]) == sec["n_cover"] == 10
    excess = [r["margin_excess"] for r in sec["margins"]]
    assert sec["median_margin_excess"] == pytest.approx(pd.Series(excess).median())
    assert sec["median_margin_raw"] == pytest.approx(pd.Series([r["margin_raw"] for r in sec["margins"]]).median())
    want = max(0.01, round(sec["median_margin_excess"], 2))
    assert sec["realized_median_excess_margin_rounded"] == pytest.approx(want)
    assert _cal(tmp_path)["measured"]["source"] == "cover_smoke_prefreeze"
    # scripted signatures at 0.10 and at the realized margin, in a new analysis dir
    sig = Path(sec["scripted_signatures"])
    assert sig.is_file() and sig.parent.parent == tmp_path / "analysis"
    rows = set(pd.read_csv(sig)["row"])
    assert "cover_m0.10" in rows and f"cover_m{want:.2f}" in rows
    assert "STOP" not in out.out


def test_cover_smoke_scripted_never_overwrites(tmp_path, key):
    mod = load_script("cover_smoke")
    args = ["--phase", "prefreeze", "--results-dir", str(tmp_path), "--config-dir", str(tmp_path),
            "--seeds", "2"]
    assert mod.main(args, bidder_factory=synthetic_bidder_factory) == 0
    first = _cal(tmp_path)["cover_smoke_prefreeze"]["scripted_signatures"]
    assert mod.main(args, bidder_factory=synthetic_bidder_factory) == 0
    second = _cal(tmp_path)["cover_smoke_prefreeze"]["scripted_signatures"]
    assert first != second and Path(first).is_file() and Path(second).is_file()
    assert len(_run_dirs(tmp_path)) == 2


@pytest.mark.parametrize("patch, condition", [
    ("realized_rule", "shadow"),
    ("cover_fidelity", "fidelity"),
])
def test_cover_smoke_stop_on_shadow(patch, condition, tmp_path, key, monkeypatch, capsys):
    if patch == "realized_rule":
        monkeypatch.setattr(fidelity, "realized_rule",
                            lambda *a, **k: dict(label="shadow", b_own=0.1, b_partner=0.9, n_cover=10))
    else:
        monkeypatch.setattr(fidelity, "cover_fidelity", lambda *a, **k: 0.3)
    mod = load_script("cover_smoke")
    code = mod.main(["--phase", "prefreeze", "--results-dir", str(tmp_path), "--config-dir", str(tmp_path),
                     "--skip-scripted"], bidder_factory=synthetic_bidder_factory)
    out = capsys.readouterr().out
    assert code == 3
    assert "STOP" in out and condition in out
    sec = _cal(tmp_path)["cover_smoke_prefreeze"]  # written before the stop
    assert sec["stop"] and condition in sec["stop"]
    assert "scripted_signatures" not in sec or sec["scripted_signatures"] is None


def test_cover_smoke_postfreeze_never_stops(tmp_path, key, monkeypatch, capsys):
    pilot.write_frozen(tmp_path / "frozen.json", _frozen_cost(0.61), [])
    monkeypatch.setattr(fidelity, "realized_rule",
                        lambda *a, **k: dict(label="shadow", b_own=0.1, b_partner=0.9, n_cover=10))
    mod = load_script("cover_smoke")
    code = mod.main(["--phase", "postfreeze", "--results-dir", str(tmp_path), "--config-dir", str(tmp_path),
                     "--skip-scripted"], bidder_factory=synthetic_bidder_factory)
    out = capsys.readouterr().out
    assert code == 0 and "WARNING" in out
    sec = _cal(tmp_path)["cover_smoke_postfreeze"]
    assert sec["beta0"] == 0.61 and sec["realized_rule"] == "shadow"
    (d,) = _run_dirs(tmp_path)
    meta = io.read_run_meta(d)
    assert meta["cost"]["beta0"] == 0.61 and meta["frozen_from"]


def _frozen_cost(beta0):
    from src.procurement.config import CostParams
    return CostParams(beta0, 0.08, 0.17, 0.05)


def test_cover_smoke_postfreeze_requires_frozen(tmp_path, key, capsys):
    mod = load_script("cover_smoke")
    code = mod.main(["--phase", "postfreeze", "--results-dir", str(tmp_path), "--config-dir", str(tmp_path)],
                    bidder_factory=synthetic_bidder_factory)
    assert code == 5
    assert "frozen.json" in capsys.readouterr().err
    assert not _run_dirs(tmp_path) and not (tmp_path / "calibration.json").exists()


# ------------------------------------------------------------------ pilot

def _pilot_args(tmp_path):
    return ["--results-dir", str(tmp_path), "--config-dir", str(tmp_path),
            "--decisions", str(tmp_path / "DECISIONS.md")]


def test_pilot_loop_offline(tmp_path, key, capsys):
    (tmp_path / "DECISIONS.md").write_text("# Decisions log\n\n- old | line | x | yes\n", encoding="utf-8")
    mod = load_script("pilot")
    code = mod.main(_pilot_args(tmp_path), bidder_factory=synthetic_bidder_factory)
    out = capsys.readouterr().out
    assert code == 0, out
    sec = _cal(tmp_path)["pilot"]
    hist = sec["pilots"]
    assert hist[0]["beta0"] == 0.90 and hist[0]["m"] > 1.25  # the synthetic bidder at the default
    for a, b in zip(hist, hist[1:]):
        nxt, frozen = pilot.next_beta0(a["beta0"], a["m"])
        assert not frozen and b["beta0"] == pytest.approx(nxt)
    assert pilot.next_beta0(hist[-1]["beta0"], hist[-1]["m"])[1]  # stopped because frozen
    assert 1.0 <= hist[-1]["m"] <= 1.10 and len(hist) <= 3
    frozen = launcher.read_frozen(tmp_path / "frozen.json", _frozen_cost(0.90))
    assert frozen.beta0 == pytest.approx(hist[-1]["beta0"])
    assert (frozen.beta1, frozen.beta2, frozen.sigma_eps) == (0.08, 0.17, 0.05)  # sigma_eps never tuned
    data = json.loads((tmp_path / "frozen.json").read_text(encoding="utf-8"))
    assert [h["beta0"] for h in data["pilot_history"]] == [h["beta0"] for h in hist]
    assert sec["status"] == "frozen" and sec["frozen_beta0"] == pytest.approx(frozen.beta0)
    lines = (tmp_path / "DECISIONS.md").read_text(encoding="utf-8").splitlines()
    new = [ln for ln in lines if ln.startswith("- ") and "old | line" not in ln]
    assert len(new) == 1 and new[0].count(" | ") == 3 and "β₀" in new[0]
    dirs = _run_dirs(tmp_path)
    assert len(dirs) == len(hist)
    assert all(io.read_run_meta(d)["purpose"] == "pilot" and io.read_run_meta(d)["T"] == 20 for d in dirs)
    assert "0.18" in out


def test_pilot_freezes_closest_after_three(tmp_path, key, monkeypatch, capsys):
    ms = iter([1.30, 1.20, 1.12])
    real = pilot.pilot_summary

    def fake(bids, truth, burn_in=10):
        s = real(bids, truth, burn_in)
        s["mean_bid_over_est"] = next(ms)
        return s
    monkeypatch.setattr(pilot, "pilot_summary", fake)
    mod = load_script("pilot")
    assert mod.main(_pilot_args(tmp_path), bidder_factory=synthetic_bidder_factory) == 0
    sec = _cal(tmp_path)["pilot"]
    assert len(sec["pilots"]) == 3 and sec["frozen_index"] == 2
    assert launcher.read_frozen(tmp_path / "frozen.json", _frozen_cost(0.9)).beta0 == \
        pytest.approx(sec["pilots"][2]["beta0"])


def test_pilot_stops_out_of_band(tmp_path, key, monkeypatch, capsys):
    ms = iter([1.40, 1.30, 1.25])
    real = pilot.pilot_summary

    def fake(bids, truth, burn_in=10):
        s = real(bids, truth, burn_in)
        s["mean_bid_over_est"] = next(ms)
        return s
    monkeypatch.setattr(pilot, "pilot_summary", fake)
    mod = load_script("pilot")
    assert mod.main(_pilot_args(tmp_path), bidder_factory=synthetic_bidder_factory) == 3
    assert "STOP" in capsys.readouterr().out
    assert not (tmp_path / "frozen.json").exists()
    sec = _cal(tmp_path)["pilot"]
    assert sec["status"] == "stop_out_of_band" and sec["frozen_index"] is None and sec["closest_index"] == 2
    # the loop still followed next_beta0 between pilots
    assert sec["pilots"][1]["beta0"] == pytest.approx(0.90 - (1.40 - 1.05))
    assert sec["pilots"][2]["beta0"] == pytest.approx(sec["pilots"][1]["beta0"] - (1.30 - 1.05))
    assert not (tmp_path / "DECISIONS.md").exists()


def test_pilot_refuses_if_frozen_exists(tmp_path, key, capsys):
    (tmp_path / "frozen.json").write_text("{}", encoding="utf-8")
    mod = load_script("pilot")
    assert mod.main(_pilot_args(tmp_path), bidder_factory=synthetic_bidder_factory) == 5
    assert "frozen.json" in capsys.readouterr().err
    assert not _run_dirs(tmp_path) and (tmp_path / "frozen.json").read_text(encoding="utf-8") == "{}"


def test_pilot_stops_on_low_parse_rate(tmp_path, key, capsys):
    bad = {(seat, "bid", p): "I would rather not say." for seat in (0, 1) for p in range(1, 21)}
    stub = StubClient(replies=bad)
    mod = load_script("pilot")
    assert mod.main(_pilot_args(tmp_path), bidder_factory=stub_factory(stub)) == 3
    out = capsys.readouterr().out
    assert "STOP" in out and "parse" in out
    assert not (tmp_path / "frozen.json").exists()
    sec = _cal(tmp_path)["pilot"]
    assert sec["status"] == "stop_parse_rate" and len(sec["pilots"]) == 1
    assert sec["pilots"][0]["parse_rate"] == pytest.approx(80 / 120)
    assert len(_run_dirs(tmp_path)) == 1


# ------------------------------------------------------------------ tier check

HEADERS_1 = {"anthropic-ratelimit-requests-limit": "4000", "anthropic-ratelimit-input-tokens-limit": "2000000",
             "anthropic-ratelimit-output-tokens-limit": "400000"}
HEADERS_2 = {"anthropic-ratelimit-requests-limit": "1000", "anthropic-ratelimit-input-tokens-limit": "450000",
             "anthropic-ratelimit-output-tokens-limit": "90000"}
BODY = {"content": [{"type": "text", "text": "OK"}], "stop_reason": "end_turn",
        "usage": {"input_tokens": 11, "output_tokens": 2}}


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, kwargs):
        self.requests.append(kwargs)
        r = self.responses.pop(0)
        if isinstance(r, BaseException):
            raise r
        return BODY, r


def test_tier_check_offline(tmp_path, key, monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY_2", SECRET2)
    fake = FakeTransport(HEADERS_1, HEADERS_2)
    mod = load_script("tier_check")
    assert mod.main(["--results-dir", str(tmp_path)], transport=fake) == 0
    out = capsys.readouterr().out
    assert fake.requests[0] == {"model": "claude-haiku-5-5", "max_tokens": 64,
                                "messages": [{"role": "user", "content": "Reply with OK."}]}
    assert len(fake.requests) == 2
    cal = _cal(tmp_path)
    assert cal["limits"] == {"1": {"requests": 4000, "input_tokens": 2000000, "output_tokens": 400000},
                             "2": {"requests": 1000, "input_tokens": 450000, "output_tokens": 90000}}
    assert cal["tier_check_utc"]
    back = launcher.read_calibration(tmp_path / "calibration.json")
    assert back["limits"][1]["output_tokens"] == 400000 and back["limits"][2]["requests"] == 1000
    assert "'control': 12" in out  # output limit < 1M but key 2 set -> 12/12/4/4
    _no_secret(tmp_path, out)


def test_tier_check_one_key_keeps_other_sections(tmp_path, key, capsys):
    from src.procurement import calibration
    calibration.update_calibration(tmp_path / "calibration.json", "measured", {"mean_latency_s": 9.5})
    mod = load_script("tier_check")
    assert mod.main(["--results-dir", str(tmp_path)], transport=FakeTransport(HEADERS_1)) == 0
    cal = _cal(tmp_path)
    assert set(cal["limits"]) == {"1"} and cal["measured"]["mean_latency_s"] == 9.5
    assert launcher.read_calibration(tmp_path / "calibration.json")["mean_latency_s"] == 9.5
    assert "'control': 8" in capsys.readouterr().out  # 400k output, no key 2 -> 8/8/4/4


def test_tier_check_auth_error_exit_5(tmp_path, key, capsys):
    mod = load_script("tier_check")
    fake = FakeTransport(TransportError("auth", 401, None, "invalid x-api-key"))
    assert mod.main(["--results-dir", str(tmp_path)], transport=fake) == 5
    out = capsys.readouterr()
    assert "key invalid for slot 1" in out.out + out.err
    _no_secret(tmp_path, out.out, out.err)


def test_tier_check_missing_header_exit_4(tmp_path, key, capsys):
    partial = {k: v for k, v in HEADERS_1.items() if "output" not in k}
    mod = load_script("tier_check")
    assert mod.main(["--results-dir", str(tmp_path)], transport=FakeTransport(partial)) == 4
    cal = _cal(tmp_path)
    assert cal["limits"]["1"] == {"requests": 4000, "input_tokens": 2000000, "output_tokens": None}
    # the launcher refuses such a file in real mode rather than guessing
    assert launcher.read_calibration(tmp_path / "calibration.json") is None
    assert "output-tokens" in capsys.readouterr().err


# ------------------------------------------------------------------ review round 1 fixes

def _no_sleep(mod):
    mod._sleep = lambda s: None
    return mod


def test_cover_smoke_stop_survives_scripted_failure(tmp_path, key, monkeypatch, capsys):
    from src.procurement import scripted
    monkeypatch.setattr(fidelity, "realized_rule",
                        lambda *a, **k: dict(label="shadow", b_own=0.1, b_partner=0.9, n_cover=10))

    def boom(*a, **k):
        raise RuntimeError("scripted exploded")
    monkeypatch.setattr(scripted, "run_scripted", boom)
    mod = load_script("cover_smoke")
    code = mod.main(["--phase", "prefreeze", "--results-dir", str(tmp_path), "--config-dir", str(tmp_path),
                     "--seeds", "2"], bidder_factory=synthetic_bidder_factory)
    out = capsys.readouterr()
    assert code == 3
    assert "STOP" in out.out and "shadow" in out.out
    sec = _cal(tmp_path)["cover_smoke_prefreeze"]
    assert sec["stop"] and "scripted exploded" in sec["scripted_error"]
    assert sec["scripted_signatures"] is None
    assert "scripted exploded" in out.err


def test_cover_smoke_scripted_failure_without_stop_exit_1(tmp_path, key, monkeypatch, capsys):
    from src.procurement import scripted

    def boom(*a, **k):
        raise RuntimeError("scripted exploded")
    monkeypatch.setattr(scripted, "run_scripted", boom)
    mod = load_script("cover_smoke")
    code = mod.main(["--phase", "prefreeze", "--results-dir", str(tmp_path), "--config-dir", str(tmp_path),
                     "--seeds", "2"], bidder_factory=synthetic_bidder_factory)
    out = capsys.readouterr()
    assert code == 1 and "STOP" not in out.out and "python -m src.procurement.scripted" in out.err
    sec = _cal(tmp_path)["cover_smoke_prefreeze"]
    assert sec["stop"] is None and "scripted exploded" in sec["scripted_error"]
    assert sec["realized_rule"] == "inflate"  # the run's own numbers stand


def test_cover_smoke_rerun_keeps_history(tmp_path, key, monkeypatch, capsys):
    monkeypatch.setattr(fidelity, "realized_rule",
                        lambda *a, **k: dict(label="shadow", b_own=0.1, b_partner=0.9, n_cover=10))
    mod = load_script("cover_smoke")
    args = ["--phase", "prefreeze", "--results-dir", str(tmp_path), "--config-dir", str(tmp_path),
            "--skip-scripted"]
    assert mod.main(args, bidder_factory=synthetic_bidder_factory) == 3
    first = _cal(tmp_path)["cover_smoke_prefreeze"]
    assert first["history"] == []
    monkeypatch.undo()
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    assert mod.main(args, bidder_factory=synthetic_bidder_factory) == 0
    second = _cal(tmp_path)["cover_smoke_prefreeze"]
    assert second["stop"] is None and len(second["history"]) == 1
    old = second["history"][0]
    assert "history" not in old and old["stop"] and old["run_dir"] == first["run_dir"]
    assert mod.main(args, bidder_factory=synthetic_bidder_factory) == 0
    third = _cal(tmp_path)["cover_smoke_prefreeze"]
    assert [h["run_dir"] for h in third["history"]] == [first["run_dir"], second["run_dir"]]


def test_note_skip_postfreeze(tmp_path, key, monkeypatch, capsys):
    mod = load_script("cover_smoke")
    note = ["--note-skip-postfreeze", "--results-dir", str(tmp_path)]
    assert mod.main(note) == 5  # no prefreeze record yet
    assert mod.main(["--phase", "prefreeze", "--results-dir", str(tmp_path), "--config-dir", str(tmp_path),
                     "--skip-scripted"], bidder_factory=synthetic_bidder_factory) == 0
    n_dirs = len(_run_dirs(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY")  # no API call, so no key needed

    def no_run(*a, **k):
        raise AssertionError("the skip note must not run anything")
    assert mod.main(note, bidder_factory=no_run) == 0
    sec = _cal(tmp_path)["cover_smoke_postfreeze"]
    assert {k: sec[k] for k in ("skipped", "reason", "using")} == \
        {"skipped": True, "reason": "time ladder", "using": "cover_smoke_prefreeze"}
    assert len(_run_dirs(tmp_path)) == n_dirs
    assert _cal(tmp_path)["cover_smoke_prefreeze"]["realized_rule"] == "inflate"  # untouched


def test_note_skip_refuses_over_real_postfreeze(tmp_path, key, capsys):
    from src.procurement import calibration
    calibration.update_calibration(tmp_path / "calibration.json", "cover_smoke_prefreeze", {"phase": "prefreeze"})
    calibration.update_calibration(tmp_path / "calibration.json", "cover_smoke_postfreeze", {"phase": "postfreeze"})
    mod = load_script("cover_smoke")
    assert mod.main(["--note-skip-postfreeze", "--results-dir", str(tmp_path)]) == 5
    assert _cal(tmp_path)["cover_smoke_postfreeze"] == {"phase": "postfreeze"}


def test_defaults_reach_run_meta(tmp_path, key, monkeypatch, capsys):
    from src.procurement import config
    real = config.load_defaults()
    changed = {**real, "memory_window": 7, "history_window": 15, "max_tokens": 6000, "concurrency": 3,
               "max_message_length": 300}
    monkeypatch.setattr(config, "load_defaults", lambda *a, **k: changed)
    for name, args in (("smoke_llm", []),
                       ("cover_smoke", ["--phase", "prefreeze", "--config-dir", str(tmp_path), "--skip-scripted"]),
                       ("pilot", ["--config-dir", str(tmp_path), "--decisions", str(tmp_path / "D.md")])):
        mod = load_script(name)
        assert mod.main(["--results-dir", str(tmp_path), *args], bidder_factory=synthetic_bidder_factory) == 0
    dirs = _run_dirs(tmp_path)
    assert len(dirs) >= 3
    for d in dirs:
        meta = io.read_run_meta(d)
        assert meta["windows"] == {"history": 15, "memory": 7}, d
        assert meta["max_tokens"] == 6000 and meta["concurrency"] == 3
    out = capsys.readouterr().out
    assert out.count("a-priori cost $") >= 3


def test_estimate_line_before_calls(tmp_path, key, capsys):
    mod = load_script("smoke_llm")
    assert mod.main(["--results-dir", str(tmp_path)], bidder_factory=synthetic_bidder_factory) == 0
    first = capsys.readouterr().out.splitlines()[0]
    assert "about to make 18 model calls" in first and "a-priori cost $0.0198" in first


def _frozen_text(tmp_path) -> dict:
    def bad(c):
        raise AssertionError(f"non-JSON constant {c} in frozen.json")
    return json.loads((tmp_path / "frozen.json").read_text(encoding="utf-8"), parse_constant=bad)


def _fake_summary(monkeypatch, **override):
    real = pilot.pilot_summary

    def fake(bids, truth, burn_in=10):
        s = real(bids, truth, burn_in)
        s.update(override)
        return s
    monkeypatch.setattr(pilot, "pilot_summary", fake)


def test_pilot_frozen_history_nan_safe(tmp_path, key, monkeypatch, capsys):
    _fake_summary(monkeypatch, mean_bid_over_est=1.05, winning_sd=float("nan"))
    mod = load_script("pilot")
    assert mod.main(_pilot_args(tmp_path), bidder_factory=synthetic_bidder_factory) == 0
    data = _frozen_text(tmp_path)
    assert data["pilot_history"][0]["winning_sd"] is None and data["cost"]["beta0"] == 0.90
    assert "n/a" in (tmp_path / "DECISIONS.md").read_text(encoding="utf-8")


def test_pilot_decision_failure_keeps_freeze(tmp_path, key, monkeypatch, capsys):
    _fake_summary(monkeypatch, mean_bid_over_est=1.05)

    def broken(*a, **k):
        raise OSError("decisions file locked")
    monkeypatch.setattr(pilot, "append_decision", broken)
    mod = load_script("pilot")
    assert mod.main(_pilot_args(tmp_path), bidder_factory=synthetic_bidder_factory) == 0
    err = capsys.readouterr().err
    assert "WARNING" in err and "decisions file locked" in err and "Froze β₀ = 0.9000" in err
    assert launcher.read_frozen(tmp_path / "frozen.json", _frozen_cost(0.5)).beta0 == 0.90
    sec = _cal(tmp_path)["pilot"]
    assert sec["status"] == "frozen" and sec["decision_logged"] is False


def test_pilot_records_freezing_before_write(tmp_path, key, monkeypatch):
    _fake_summary(monkeypatch, mean_bid_over_est=1.05)

    def broken(*a, **k):
        raise OSError("config dir read-only")
    monkeypatch.setattr(pilot, "write_frozen", broken)
    mod = load_script("pilot")
    with pytest.raises(OSError):
        mod.main(_pilot_args(tmp_path), bidder_factory=synthetic_bidder_factory)
    assert _cal(tmp_path)["pilot"]["status"] == "freezing"
    assert not (tmp_path / "frozen.json").exists() and not (tmp_path / "DECISIONS.md").exists()


def test_pilot_rerun_keeps_history(tmp_path, key, capsys):
    bad = {(seat, "bid", p): "I would rather not say." for seat in (0, 1) for p in range(1, 21)}
    mod = load_script("pilot")
    assert mod.main(_pilot_args(tmp_path), bidder_factory=stub_factory(StubClient(replies=bad))) == 3
    first = _cal(tmp_path)["pilot"]
    assert first["history"] == []
    assert mod.main(_pilot_args(tmp_path), bidder_factory=synthetic_bidder_factory) == 0
    second = _cal(tmp_path)["pilot"]
    assert second["status"] == "frozen" and len(second["history"]) == 1
    old = second["history"][0]
    assert old["status"] == "stop_parse_rate" and "history" not in old
    assert old["pilots"][0]["run_dir"] == first["pilots"][0]["run_dir"]


def test_tier_check_transient_error_keeps_good_limits(tmp_path, key, capsys):
    from src.procurement import calibration
    good = {"requests": 4000, "input_tokens": 2000000, "output_tokens": 400000}
    calibration.update_calibration(tmp_path / "calibration.json", "limits", {"1": good})
    fake = FakeTransport(TransportError("rate_limit", 429, None, "slow down"),
                         TransportError("rate_limit", 429, None, "slow down"))
    mod = _no_sleep(load_script("tier_check"))
    assert mod.main(["--results-dir", str(tmp_path)], transport=fake) == 4
    assert len(fake.requests) == 2  # retried once
    lim = _cal(tmp_path)["limits"]["1"]
    assert {k: lim[k] for k in good} == good and "429" in lim["last_error"]
    assert launcher.read_calibration(tmp_path / "calibration.json")["limits"][1]["output_tokens"] == 400000
    assert "transient error, limits not updated" in capsys.readouterr().err


def test_tier_check_transient_error_without_earlier_values(tmp_path, key, capsys):
    fake = FakeTransport(TransportError("server", 529, None, "overloaded"),
                         TransportError("server", 529, None, "overloaded"))
    mod = _no_sleep(load_script("tier_check"))
    assert mod.main(["--results-dir", str(tmp_path)], transport=fake) == 4
    lim = _cal(tmp_path)["limits"]["1"]
    assert lim["output_tokens"] is None and "529" in lim["last_error"]
    assert launcher.read_calibration(tmp_path / "calibration.json") is None


def test_tier_check_429_with_headers_reads_limits(tmp_path, key, capsys):
    fake = FakeTransport(TransportError("rate_limit", 429, None, "x", HEADERS_1),
                         TransportError("rate_limit", 429, None, "x", HEADERS_1))
    mod = _no_sleep(load_script("tier_check"))
    assert mod.main(["--results-dir", str(tmp_path)], transport=fake) == 0
    assert _cal(tmp_path)["limits"]["1"] == {"requests": 4000, "input_tokens": 2000000, "output_tokens": 400000}


def test_tier_check_retry_then_success(tmp_path, key, capsys):
    fake = FakeTransport(TransportError("connection", None, None, "reset"), HEADERS_1)
    mod = _no_sleep(load_script("tier_check"))
    assert mod.main(["--results-dir", str(tmp_path)], transport=fake) == 0
    assert len(fake.requests) == 2 and _cal(tmp_path)["limits"]["1"]["requests"] == 4000
