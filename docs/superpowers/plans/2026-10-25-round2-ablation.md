# Round 2: Memory × Information Ablation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four seed-matched control arms (mem0, stateless, hidden, stateless_hidden) to the procurement experiment, a sentinel-filtered detector spec, per-arm evaluation/analysis with regime, floor and paired tables, and the round-1 README corrections.

**Architecture:** Three new `RunConfig` flags (`scratchpad_enabled`, `arm`, `bidder_info`) drive prompt rendering and run identity; `arm` rides inside `run_id` so every downstream grouping by `run_id` separates arms for free. Round-2 analysis is a new module (`analysis_round2.py` + `regimes.py` + `floors.py`) that reuses `evaluation.evaluate` and the round-1 table builders per arm; the round-1 `analysis.analyze` is restricted to `arm == "base"` so its README regenerates with unchanged numbers.

**Tech Stack:** Python 3.11 venv (`.venv\Scripts\python.exe`), numpy, scipy, pandas, matplotlib, jinja2, pytest. Zero API calls in the suite (`tests/conftest.py` guard).

**Spec:** `docs/superpowers/round2-brief.md` (wins over `docs/superpowers/specs/2026-10-25-bajari-ye-procurement-design.md` and `docs/superpowers/step4-audit.md`).

## Global Constraints

- Every test is written before the code it checks; the full suite (`.venv\Scripts\python.exe -m pytest tests -q`) stays green with zero API calls; nothing in the suite writes under `results/`, `config/` or `docs/superpowers/DECISIONS.md` (Task 1 enforces this).
- Do not change: cost parameters, EST range, map, η range, ν levels, K, T = 110, burn-in 10, seeds, firm names, the bid JSON schema beyond the two fields named in brief §2.1 (no `no_bid`), the forced "Cost: $X / intended bid $Y" message prefix, the model, `max_tokens`, absence of temperature/effort, concurrency 6. Any file under round-1 run, calibration-run, launch or analysis directories is read-only.
- `bids.csv` columns and the detector input contract are unchanged. `arm` goes to `truth.csv` and `run_meta.json`, never to `bids.csv`.
- Base arm (`arm == "base"`) keeps `run_id = "{condition}_s{seed}"` and the directory name `<UTC>_{condition}_s{seed}`; a `run_meta.json` without `arm` reads as `"base"`.
- For `arm == "base"` the rendered system prefix and user message are byte-identical to the round-1 code path (fixture in `tests/fixtures/`).
- Guard rails: never overwrite or delete a results directory; the API key is read from the environment and never written to disk or logs; no force pushes; log every self-made decision as one line in `docs/superpowers/DECISIONS.md` (`- YYYY-MM-DD HH:MM | decision | reason | reversible?`).
- Commit after every task with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` as the last line.
- New analysis output always goes to a new `results/analysis/<UTC>/` (never into an existing one).

## Review Focus

1. A round-1 `run_meta.json` (no `arm`, no `bidder_info`, `windows` without `scratchpad`) must still load, evaluate and resume-check as before — Task 2 `test_arm_defaults_to_base_when_absent`, Task 4 `test_round1_meta_evaluates_as_base`.
2. A `history_window = 0` config must not slice `history[-0:]` (which is the whole list) — Task 3 `test_history_window_zero_omits_section`.
3. An LLM reply that carries `scratch_pad_update`/`new_memory` under a disabled flag must be ignored, not stored and not crash the parse — Task 3 `test_scratchpad_disabled_never_updates`.
4. A `tag_filtered` table where one firm loses every post-burn-in row to the sentinel filter must give `insufficient_n`/`not_identified` rows, not an exception — Task 7 `test_filtered_spec_all_sentinel_firm`.
5. Wilcoxon over 12 seed differences that are all zero must give `p = NaN`, not raise — Task 10 `test_paired_by_seed_all_zero`.

---

### Task 1: Test-suite write guard for results/, config/ and DECISIONS.md

**Files:**
- Modify: `tests/conftest.py`
- Test: `tests/test_write_guard.py`

**Interfaces:**
- Produces: a session-autouse fixture `_protect_repo_dirs` in `tests/conftest.py` that makes any write-mode `open`, `os.mkdir`, `os.makedirs`, `pathlib.Path.mkdir`, `os.remove`, `os.unlink`, `os.rename`, `os.replace`, `shutil.rmtree`, `shutil.move` whose resolved target is under `REPO_ROOT/results`, `REPO_ROOT/config` or equals `REPO_ROOT/docs/superpowers/DECISIONS.md` raise `PermissionError("test suite must not write under ...")`. Read-mode `open` is untouched. `REPO_ROOT = Path(__file__).resolve().parents[1]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_write_guard.py
import os, shutil
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

@pytest.mark.parametrize("rel", ["results/__guard_probe__.txt", "config/__guard_probe__.json",
                                 "docs/superpowers/DECISIONS.md"])
def test_write_open_refused(rel):
    with pytest.raises(PermissionError):
        open(REPO_ROOT / rel, "a")
    assert not (REPO_ROOT / "results/__guard_probe__.txt").exists()

def test_mkdir_refused():
    with pytest.raises(PermissionError):
        (REPO_ROOT / "results" / "__guard_probe_dir__").mkdir()
    with pytest.raises(PermissionError):
        os.makedirs(REPO_ROOT / "config" / "__guard_probe_dir__", exist_ok=True)

def test_delete_refused(tmp_path):
    with pytest.raises(PermissionError):
        shutil.rmtree(REPO_ROOT / "results" / "runs")
    with pytest.raises(PermissionError):
        os.remove(REPO_ROOT / "results" / "README.md")

def test_read_allowed_and_tmp_allowed(tmp_path):
    assert (REPO_ROOT / "config" / "defaults.json").read_text(encoding="utf-8")
    p = tmp_path / "results" / "x.txt"   # a 'results' folder OUTSIDE the repo is fine
    p.parent.mkdir(parents=True); p.write_text("ok"); assert p.read_text() == "ok"
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_write_guard.py -q`
Expected: FAIL (no PermissionError raised; the probe files are created — delete `results/__guard_probe__.txt` and `config/__guard_probe__.json` by hand if the failing run created them; `DECISIONS.md` was opened in append mode and is unchanged).

- [ ] **Step 3: Implement the guard in `tests/conftest.py`**

Session-autouse fixture using `pytest.MonkeyPatch.context()`; wrap `builtins.open` (write modes: any of `w a x +` in the mode string), `io.open`, `os.mkdir`, `os.makedirs`, `os.remove`, `os.unlink`, `os.rename`, `os.replace`, `shutil.rmtree`, `shutil.move`, and `pathlib.Path.mkdir`/`Path.write_text`/`Path.write_bytes`/`Path.unlink`/`Path.rename`/`Path.replace`/`Path.open` (write modes). A path is protected when `Path(os.fspath(p)).resolve()` (non-strict) is `REPO_ROOT/results`, `REPO_ROOT/config` or any descendant, or equals `REPO_ROOT/docs/superpowers/DECISIONS.md`. Keep the existing `api_guard` and `_no_api_calls` unchanged.

- [ ] **Step 4: Run the full suite**

Run: `.venv\Scripts\python.exe -m pytest tests -q`
Expected: all pass (443 + 4 new). If any existing test now raises PermissionError, that test was writing into the real tree: fix the test to use `tmp_path`, never the guard.

- [ ] **Step 5: Commit**

```bash
git add tests/conftest.py tests/test_write_guard.py
git commit -m "test: refuse writes under results/, config/ and DECISIONS.md from the suite"
```

---

### Task 2: RunConfig flags and arm-aware run identity

**Files:**
- Modify: `src/procurement/config.py` (RunConfig, `DEFAULTS_RUN_FIELDS`), `config/defaults.json`, `src/procurement/io.py:16-18,37-56` (`TRUTH_COLUMNS`, `new_run_dir`, `read_truth`), `src/procurement/runner.py:80-112,170,419,522-566` (`make_run_meta`, `_Loop.run_id`, truth rows, `_check_resumable`, `_start_resume`, `run`)
- Test: `tests/test_config.py`, `tests/test_runner_control.py`, `tests/test_io.py`

**Interfaces:**
- Produces:
  - `RunConfig` fields `scratchpad_enabled: bool = True`, `arm: str = "base"`, `bidder_info: str = "shown"` (`from_dict`/`to_dict` round-trip; `bidder_info` not in `("shown", "hidden")` raises `ValueError` in `__post_init__`).
  - `DEFAULTS_RUN_FIELDS` gains `"scratchpad_enabled"`; `config/defaults.json` gains `"scratchpad_enabled": true` and `"spend_stop_usd": 150`.
  - `config.run_id_of(condition: str, seed: int, arm: str = "base") -> str` → `"control_s3"` for base, `"control_mem0_s3"` otherwise.
  - `config.run_id_from_meta(meta: dict) -> str` using `meta.get("arm") or "base"`.
  - `config.arm_of_meta(meta: dict) -> str`.
  - `io.new_run_dir(results_root, condition, seed, now_utc=None, arm="base")` → `<UTC>_{condition}_s{seed}` for base, `<UTC>_{condition}_{arm}_s{seed}` otherwise.
  - `io.TRUTH_COLUMNS` ends with `"arm"`; `io.read_truth` fills `arm = "base"` when the column is absent (round-1 files).
  - `run_meta.json`: top-level `"arm"`, `"bidder_info"`, and `"windows": {"history", "memory", "scratchpad": bool}`; `_check_resumable` compares `arm`, `bidder_info` and the three-key `windows` (an old meta without `scratchpad` in `windows` compares as `True`; a missing `arm` as `"base"`, a missing `bidder_info` as `"shown"`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_config.py (append)
def test_run_id_carries_arm():
    assert config.run_id_of("control", 3) == "control_s3"
    assert config.run_id_of("control", 3, "mem0") == "control_mem0_s3"
    assert config.run_id_from_meta({"condition": "control", "seed": 3}) == "control_s3"
    assert config.run_id_from_meta({"condition": "control", "seed": 3, "arm": "stateless"}) == "control_stateless_s3"

def test_new_flags_roundtrip_and_defaults():
    cfg = RunConfig(condition="control", seed=1)
    assert (cfg.scratchpad_enabled, cfg.arm, cfg.bidder_info) == (True, "base", "shown")
    cfg2 = RunConfig.from_json(RunConfig(condition="control", seed=1, scratchpad_enabled=False, arm="mem0",
                                         bidder_info="hidden").to_json())
    assert (cfg2.scratchpad_enabled, cfg2.arm, cfg2.bidder_info) == (False, "mem0", "hidden")
    with pytest.raises(ValueError):
        RunConfig(condition="control", seed=1, bidder_info="secret")
    d = config.load_defaults()
    assert d["scratchpad_enabled"] is True and d["spend_stop_usd"] == 150
    assert "scratchpad_enabled" in config.DEFAULTS_RUN_FIELDS
```

```python
# tests/test_runner_control.py (append; use the module's existing synthetic-run helper)
def test_arm_in_run_dir_meta_and_truth(tmp_path):
    cfg = RunConfig(condition="control", seed=2, T=3, burn_in=0, arm="mem0", memory_window=0,
                    scratchpad_enabled=False)
    run_dir = runner.run(cfg, runner.bidder_factory_for(cfg), results_root=tmp_path)
    assert run_dir.name.endswith("_control_mem0_s2")
    meta = io.read_run_meta(run_dir)
    assert meta["arm"] == "mem0" and meta["bidder_info"] == "shown"
    assert meta["windows"] == {"history": 20, "memory": 0, "scratchpad": False}
    truth = io.read_truth(run_dir)
    assert set(truth["arm"]) == {"mem0"} and set(truth["run_id"]) == {"control_mem0_s2"}
    assert "arm" not in io.read_bids(run_dir).columns

def test_arm_defaults_to_base_when_absent(tmp_path):
    # a round-1 run_meta.json: no arm, no bidder_info, two-key windows
    cfg = RunConfig(condition="control", seed=1, T=2, burn_in=0)
    run_dir = runner.run(cfg, runner.bidder_factory_for(cfg), results_root=tmp_path)
    meta = io.read_run_meta(run_dir)
    for k in ("arm", "bidder_info"): meta.pop(k)
    meta["windows"].pop("scratchpad")
    io.write_run_meta(run_dir, meta)
    assert config.run_id_from_meta(meta) == "control_s1"
    assert runner.same_run(cfg, meta) is True
    assert runner.same_run(dataclasses.replace(cfg, arm="mem0"), meta) is False
    truth = io.read_truth(run_dir)  # written by this task's code, so it has the column...
    # ...and a file without it reads as base:
    text = (run_dir / "truth.csv").read_text(encoding="utf-8").splitlines()
    (tmp_path / "old_truth.csv").write_text("\n".join(l.rsplit(",", 1)[0] for l in text) + "\n", encoding="utf-8")
    old = io.read_truth_file(tmp_path / "old_truth.csv")
    assert set(old["arm"]) == {"base"}
```

(Add `io.read_truth_file(path)` as the file-level reader that `io.read_truth(run_dir)` delegates to.)

- [ ] **Step 2: Run to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_config.py tests/test_runner_control.py -q -k "arm or flags"`
Expected: FAIL (`AttributeError`/`TypeError` on the new names).

- [ ] **Step 3: Implement**

`config.py`: the three fields with defaults, `__post_init__` validation of `bidder_info`, `run_id_of`, `run_id_from_meta`, `arm_of_meta`, `DEFAULTS_RUN_FIELDS` + `"scratchpad_enabled"`. `config/defaults.json`: `"scratchpad_enabled": true`, `"spend_stop_usd": 150`. `io.py`: `new_run_dir(..., arm="base")`, `TRUTH_COLUMNS + ["arm"]`, `_TRUTH_STR_COLUMNS + ("arm",)`, `read_truth_file`. `runner.py`: `self.run_id = config.run_id_of(cfg.condition, cfg.seed, cfg.arm)`; truth rows carry `"arm": cfg.arm`; `make_run_meta` adds `arm`, `bidder_info`, `windows.scratchpad`; `_check_resumable` expected/got for the new keys with the defaults above; both `io.new_run_dir(...)` calls pass `arm=cfg.arm`. Grep for every other `f"{...condition}_s{...seed}"` in `src/procurement/` (`evaluation.py:237,440`, `launcher.py`, `analysis.py:780-792`) and switch to `config.run_id_of`/`run_id_from_meta` — Task 4/5 own the semantics, but the string construction moves here.

- [ ] **Step 4: Run the full suite**

Run: `.venv\Scripts\python.exe -m pytest tests -q`
Expected: all pass. Tests that compare `TRUTH_COLUMNS` literally need the new column added.

- [ ] **Step 5: DECISIONS + commit**

Append to `docs/superpowers/DECISIONS.md`: `spend_stop_usd 75 -> 150 (brief §2.3; round-1 spend $33.70, four arms ≈ $40)` and `truth.csv gains an arm column in every new run (base included) so one reader serves both rounds; round-1 files without it read as "base"`.

```bash
git add src/procurement/config.py src/procurement/io.py src/procurement/runner.py config/defaults.json tests docs/superpowers/DECISIONS.md
git commit -m "feat(round2): RunConfig arm/scratchpad/bidder_info flags, arm-aware run ids, truth.csv arm column"
```

---

### Task 3: Prompt state flags (scratchpad, memory, history sections and schema)

**Files:**
- Create: `tests/fixtures/prompt_fixture.py`, `tests/fixtures/base_prompt_seat2_p15.system.txt`, `tests/fixtures/base_prompt_seat2_p15.user.txt`, `src/procurement/prompt_sections.py`
- Modify: `src/procurement/render.py` (`BID_SCHEMA` → `bid_schema(cfg)`, `_variables`, `build_system_prefix`), `src/procurement/prompts/bid.jinja2`, `src/procurement/llm_bidder.py:109-118`
- Test: `tests/test_render.py`, `tests/test_llm_bidder.py`

**Interfaces:**
- Consumes: `RunConfig.scratchpad_enabled`, `memory_window`, `history_window` (Task 2).
- Produces:
  - `render.bid_schema(cfg: RunConfig) -> str`: the round-1 `BID_SCHEMA` string when `memory_window > 0 and scratchpad_enabled`; drops `"new_memory": ...` when `memory_window == 0`; drops `"scratch_pad_update": ...` when `not scratchpad_enabled`; with both off exactly `{"reflection": "<string>", "plan": "<string>", "bid": <number, dollars>}`. `BID_SCHEMA` stays as the full-schema constant.
  - `prompt_sections.sections_of(text: str) -> list[str]`: the `# ...` headings (lines starting with `"# "`, in order; `### Project n` history sub-headings excluded).
  - `prompt_sections.expected_sections(cfg: RunConfig, phase: str = "bid", member: bool = False, t: int = 14) -> tuple[list[str], list[str]]` → (system headings, user headings) for an outsider/control seat: system `["# Introduction", "# Market rules", "# How costs work", "# Output format"]`; user: `"# Your memory"` only if `memory_window > 0`, then `f"# Project {t+1} of {cfg.T}"`, `"# Private agreement"`-free, `"# Your private information"`, `f"# Last {cfg.history_window} projects"` only if `history_window > 0`, `"# Strategy scratchpad"` only if `scratchpad_enabled`, `"# Reply format"`.
  - `tests/fixtures/prompt_fixture.py::fixture_inputs(env) -> dict(t=14, seat=2, history=<14 completed projects 1..14 built like test_render._history>, memory=["Project 12: won at a thin margin.", "Project 13: lost to Northfield by $4,000.", "Project 14: passed; site 80 miles out."], scratchpad="Bid cost + 4% on close sites; +8% beyond 60 miles.")` and `fixture_cfg() -> RunConfig(condition="control", seed=3, cost=CostParams(0.90, 0.08, 0.17, 0.05))`; env is `generate_environment(3, fixture_cfg().cost, 110)`.

- [ ] **Step 1: Capture the round-1 fixture BEFORE touching render.py or the template**

Write `tests/fixtures/prompt_fixture.py` (above) and `scripts/capture_prompt_fixture.py` that renders `render_outsider_prompt_for_test(cfg, env, 2, 14, history, "control", memory=..., scratchpad=...)` and writes the two `.txt` files with `newline="\n"`, UTF-8. Run it once: `.venv\Scripts\python.exe scripts\capture_prompt_fixture.py`. Confirm the user text contains `# Your memory`, `# Last 20 projects`, `# Strategy scratchpad` and `"new_memory"`. Commit the fixture: `git add tests/fixtures scripts/capture_prompt_fixture.py && git commit -m "test: capture round-1 base prompt fixture (seat 2, project 15)"`.

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_render.py (append)
ARMS = {"base": dict(memory_window=10, scratchpad_enabled=True, history_window=20),
        "mem0": dict(memory_window=0, scratchpad_enabled=False, history_window=20),
        "stateless": dict(memory_window=0, scratchpad_enabled=False, history_window=0)}

@pytest.mark.parametrize("arm", list(ARMS))
def test_state_flags_prompt_sections(env, arm):
    cfg = dataclasses.replace(fixture_cfg(), arm=arm, **ARMS[arm]); fx = fixture_inputs(env)
    system, user = render_outsider_prompt_for_test(cfg, env, fx["seat"], fx["t"], fx["history"], "control",
                                                   memory=fx["memory"], scratchpad=fx["scratchpad"])
    exp_sys, exp_user = expected_sections(cfg, "bid", t=fx["t"])
    assert sections_of(system) == exp_sys and sections_of(user) == exp_user
    if arm != "base":
        assert "No memories yet." not in user and "No projects have been completed yet." not in user
        assert "scratch_pad_update" not in system and "new_memory" not in system
    if arm == "stateless":
        assert "# Last" not in user and '{"reflection": "<string>", "plan": "<string>", "bid": <number, dollars>}' in system

def test_base_prompt_unchanged(env):
    cfg, fx = fixture_cfg(), fixture_inputs(env)
    system, user = render_outsider_prompt_for_test(cfg, env, fx["seat"], fx["t"], fx["history"], "control",
                                                   memory=fx["memory"], scratchpad=fx["scratchpad"])
    d = Path(__file__).parent / "fixtures"
    assert system == (d / "base_prompt_seat2_p15.system.txt").read_text(encoding="utf-8")
    assert user == (d / "base_prompt_seat2_p15.user.txt").read_text(encoding="utf-8")

def test_history_window_zero_omits_section(env):
    cfg = dataclasses.replace(fixture_cfg(), history_window=0); fx = fixture_inputs(env)
    _, user = render_outsider_prompt_for_test(cfg, env, 2, 14, fx["history"], "control")
    assert "# Last" not in user and "### Project" not in user
```

```python
# tests/test_llm_bidder.py (append; use the module's StubClient pattern)
def test_scratchpad_disabled_never_updates(env):
    cfg = dataclasses.replace(_cfg(), memory_window=0, scratchpad_enabled=False)
    reply = '{"reflection":"r","plan":"p","bid":650000,"new_memory":"REMEMBER","scratch_pad_update":"PAD"}'
    bidder = LLMBidder(cfg, env, 0, StubClient(replies={...: reply}))   # every call returns `reply`
    first = bidder.bid(_ctx(env, cfg, 0, 0)); second = bidder.bid(_ctx(env, cfg, 1, 0))
    assert first.bid == 650000.0 and bidder.memories == {} and bidder.scratchpad == ""
    assert bidder.get_state() == {"memories": {}, "scratchpad": ""}
    assert "REMEMBER" not in second.prompt and "PAD" not in second.prompt
```

- [ ] **Step 3: Run to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_render.py tests/test_llm_bidder.py -q -k "state_flags or base_prompt or history_window_zero or never_updates"`
Expected: `test_base_prompt_unchanged` PASSES already (that is the point); the others FAIL.

- [ ] **Step 4: Implement**

`bid.jinja2`: wrap `# Your memory` in `{% if show_memory %}`, `# Last N projects` in `{% if show_history %}`, `# Strategy scratchpad` in `{% if show_scratchpad %}` — keeping the base rendering byte-identical (watch blank lines: render base and diff against the fixture after every edit). `_variables`: `show_memory = cfg.memory_window > 0`, `show_history = cfg.history_window > 0`, `show_scratchpad = cfg.scratchpad_enabled`, history slice guarded by `> 0` (already), `scratchpad` forced `""` when disabled. `build_system_prefix` and `build_bid_user_message` use `bid_schema(cfg)`. `llm_bidder.bid`: store `new_memory` only if `cfg.memory_window > 0`; store `scratch_pad_update` only if `cfg.scratchpad_enabled`; `_with_memory` passes `scratchpad=""` when disabled. `message.jinja2` extends `bid.jinja2` so it inherits the flags.

- [ ] **Step 5: Run the full suite**

Run: `.venv\Scripts\python.exe -m pytest tests -q`
Expected: all pass, `test_base_prompt_unchanged` included.

- [ ] **Step 6: Commit**

```bash
git add src/procurement/render.py src/procurement/prompts/bid.jinja2 src/procurement/llm_bidder.py src/procurement/prompt_sections.py tests
git commit -m "feat(round2): scratchpad/memory/history prompt flags with base prompt pinned to the round-1 fixture"
```

---

### Task 4: Evaluation and round-1 analysis grouped by arm

**Files:**
- Modify: `src/procurement/evaluation.py` (`PAIR_EVAL_COLUMNS`, `RUN_EVAL_COLUMNS`, `select_run_dirs`, `_pair_eval`, `_run_eval`, `evaluate`), `src/procurement/analysis.py:111-120,780-820,822-872` (`control_thresholds`, `missing_runs`, `analyze`)
- Test: `tests/test_evaluation.py`, `tests/test_analysis.py`

**Interfaces:**
- Consumes: `config.run_id_from_meta`, `config.arm_of_meta` (Task 2).
- Produces:
  - `pair_eval.csv` and `run_eval.csv` gain an `arm` column (after `condition`).
  - `select_run_dirs` groups by `config.run_id_from_meta(meta)`; `evaluate` matches a scheme run's control by `config.run_id_of("control", seed, arm)`.
  - `analysis.control_thresholds(pair_eval, arm: str = "base")` uses complete control rows of that arm only; `analysis.control_thresholds_by_arm(pair_eval) -> dict[str, dict]`.
  - `analysis.analyze(...)` (round-1 README) filters `pair_eval`/`run_eval` to `arm == "base"` before every table; other arms are listed in the README's run inventory under a "Other arms (see README_round2.md)" line with counts only.
  - `analysis.missing_runs` compares planned run_ids via `config.run_id_of`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_evaluation.py (append; reuse _copy_run / synthetic-run helpers of the module)
def test_evaluation_groups_by_arm(tmp_path):
    root = tmp_path / "runs"
    for arm in ("base", "mem0"):
        cfg = RunConfig(condition="control", seed=1, T=14, burn_in=10, arm=arm, bidder="synthetic",
                        memory_window=0 if arm == "mem0" else 10, scratchpad_enabled=arm == "base")
        runner.run(cfg, runner.bidder_factory_for(cfg), results_root=root)
    pe, re_ = evaluation.evaluate(root, tmp_path / "out", include_synthetic=True)
    assert sorted(re_["run_id"]) == ["control_mem0_s1", "control_s1"]
    assert dict(zip(re_["run_id"], re_["arm"])) == {"control_s1": "base", "control_mem0_s1": "mem0"}
    by_arm = analysis.control_thresholds_by_arm(pe)
    assert set(by_arm) == {"base", "mem0"}
    prim = pe[(pe["spec"] == "tag") & (pe["primary"] == 1)]
    for arm in by_arm:   # each arm's threshold is the p95 of its own 15 pairs only
        F = prim.loc[prim["arm"] == arm, "F"].astype(float)
        assert by_arm[arm][("tag", "A3")] == pytest.approx(np.percentile(F.dropna(), 95))

def test_round1_meta_evaluates_as_base(evaluated):
    # strip arm/bidder_info from a run_meta copy; evaluation still yields arm "base"
    ...
    assert set(re_["arm"]) == {"base"}
```

```python
# tests/test_analysis.py (append)
def test_round1_analysis_ignores_other_arms(fx, tmp_path):
    # fx has base runs; add one mem0 control run (synthetic) under the same root, run analyze():
    # flag_rates.csv, empirical_null.csv and thresholds equal the base-only result, and the README
    # run inventory names the mem0 run under "Other arms".
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_evaluation.py tests/test_analysis.py -q -k "arm"`
Expected: FAIL (`KeyError: 'arm'` / `AttributeError`).

- [ ] **Step 3: Implement** per Interfaces. `_pair_eval`/`_run_eval` add `arm = config.arm_of_meta(meta)`. `_RUN_EVAL_STR` gains `"arm"`; `PAIR_EVAL` string typing likewise.

- [ ] **Step 4: Run the full suite**

Run: `.venv\Scripts\python.exe -m pytest tests -q`
Expected: all pass. The `test_readme`/column-list tests need `arm` added.

- [ ] **Step 5: DECISIONS + commit**

DECISIONS line: `round-1 analysis.analyze restricts every table to arm "base" so results/README.md regenerates with unchanged numbers once arm runs share results/runs; round-2 tables live in analysis_round2`.

```bash
git add src/procurement/evaluation.py src/procurement/analysis.py tests docs/superpowers/DECISIONS.md
git commit -m "feat(round2): evaluation and round-1 analysis keyed by (condition, arm)"
```

---

### Task 5: Launcher arms plan, `--arms`, `--no-ladder`, spend cap from config

**Files:**
- Modify: `config/run_plan.json`, `src/procurement/config.py` (`arm_plan`), `src/procurement/launcher.py:108-125,830-912,920-1115` (`build_manifest`, `finished_runs`, `existing_experiment_T`, `unfinished_runs`, `main`)
- Test: `tests/test_launcher.py`

**Interfaces:**
- Consumes: `config.run_id_of`, `RunConfig` flags (Task 2).
- Produces:
  - `config/run_plan.json` gains
    ```json
    "arms": [
      {"arm": "mem0", "R": {"control": 12}, "memory_window": 0, "scratchpad_enabled": false, "history_window": 20, "bidder_info": "shown"},
      {"arm": "stateless", "R": {"control": 12}, "memory_window": 0, "scratchpad_enabled": false, "history_window": 0, "bidder_info": "shown"},
      {"arm": "hidden", "R": {"control": 12}, "memory_window": 10, "scratchpad_enabled": true, "history_window": 20, "bidder_info": "hidden"},
      {"arm": "stateless_hidden", "R": {"control": 12}, "memory_window": 0, "scratchpad_enabled": false, "history_window": 0, "bidder_info": "hidden"}
    ]
    ```
    `R`, `include_rotation`, `T`, `deadline_utc` unchanged.
  - `config.arm_plan(plan_file: dict, arms: list[str], T: int, *, defaults: dict, cost: CostParams | None, **overrides) -> list[RunConfig]`: for each entry whose `arm` is in `arms` (order of `arms`; unknown arm → `ValueError`), `run_config_from_defaults(defaults, condition=cond, seed=s, T=T, cost=cost, arm=..., memory_window=..., scratchpad_enabled=..., history_window=..., bidder_info=..., **overrides)` for `cond, n in entry["R"].items()`, seeds `1..n`.
  - Launcher CLI: `--arms a,b` (comma list) and `--no-ladder`. With `--arms`: the plan is `arm_plan`, the sizing rule's per-condition R cap is NOT applied (R from the plan file; `cap` from `slot_caps` as before), `--no-ladder` is implied and logged, `counts` is per `(arm, condition)`. With `--no-ladder` alone the ladder is skipped (rung 0) and a line is logged. `finished_runs`, `unfinished_runs`, `existing_experiment_T` key by `run_id` (`config.run_id_of(c.condition, c.seed, c.arm)`); manifest entries gain `"arm"` and `"run_id"`; `skipped` entries use `run_id`.
  - `Launcher(..., stop_usd=float(defaults["spend_stop_usd"]))` — already read from defaults; the test pins that it is 150 from `config/defaults.json`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_launcher.py (append; reuse _plan_file, _frozen, _no_popen, capsys patterns)
def test_launcher_arms_subset(tmp_path, monkeypatch):
    plan = _plan_file_with_arms(tmp_path)          # helper: writes the config/run_plan.json content above
    rc = launcher.main(["--plan", str(plan), "--dry-run", "--allow-unfrozen", "--arms", "mem0,stateless",
                        "--results-root", str(tmp_path / "runs")], clock=_Clock(), popen=_no_popen)
    assert rc == 0
    manifest = json.loads(next((tmp_path / "launches").rglob("manifest.json")).read_text())  # or the workspace file the launcher writes
    runs = manifest["runs"]
    assert len(runs) == 24 and {r["arm"] for r in runs} == {"mem0", "stateless"}
    assert sorted(r["run_id"] for r in runs)[:2] == ["control_mem0_s1", "control_mem0_s10"]
    cfg = RunConfig.from_json(Path(runs[0]["config_path"]).read_text())
    assert (cfg.memory_window, cfg.scratchpad_enabled, cfg.bidder_info) == (0, False, "shown")
    st = next(r for r in runs if r["arm"] == "stateless"); cfg = RunConfig.from_json(Path(st["config_path"]).read_text())
    assert cfg.history_window == 0 and cfg.T == 110 and cfg.condition == "control"

def test_arms_unknown_refused(tmp_path):
    rc = launcher.main([..., "--arms", "mem0,nope", "--dry-run", "--allow-unfrozen"], ...)
    assert rc == launcher.EXIT_PREFLIGHT

def test_no_ladder_skips_rungs(tmp_path, monkeypatch, capsys):
    # clock 1 h before the deadline: without --no-ladder main returns EXIT_LADDER; with it, rc 0 and
    # the log contains "time ladder: off"
    ...

def test_spend_cap_from_config(tmp_path):
    # Launcher built by main(--dry-run) has stop_usd == config.load_defaults()["spend_stop_usd"] == 150
    ...

def test_finished_runs_keyed_by_arm(tmp_path):
    # a finished control_s1 (base) directory does not mark control_mem0_s1 as done
    ...
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_launcher.py -q -k "arms or ladder or spend_cap or keyed"`
Expected: FAIL (`unrecognized arguments: --arms`).

- [ ] **Step 3: Implement** per Interfaces. Keep the round-1 path (no `--arms`) behaviour identical, including the sizing-rule R minimum and the ladder.

- [ ] **Step 4: Run the full suite**

Run: `.venv\Scripts\python.exe -m pytest tests -q`
Expected: all pass.

- [ ] **Step 5: Dry run against the real plan**

Run: `.venv\Scripts\python.exe -m src.procurement.launcher --plan config/run_plan.json --dry-run --arms mem0,stateless --no-ladder`
Expected: exit 0; log `launch plan (dry run): 24 runs`; a-priori cost printed; the new launch workspace is under `results/launches/` (a dry run writes only there).

- [ ] **Step 6: Commit**

```bash
git add config/run_plan.json src/procurement/config.py src/procurement/launcher.py tests/test_launcher.py
git commit -m "feat(round2): run_plan arms, launcher --arms/--no-ladder, run ids keyed by arm"
```

---

### Task 6: `bidder_info = "hidden"` prompt (wave 2)

**Files:**
- Modify: `src/procurement/render.py` (`_cost_section`, `_variables`), `src/procurement/prompts/bid.jinja2` (`# Your private information`), `src/procurement/auction.py:47-61` (`render_pre_posting`), `src/procurement/runner.py` (the `render_pre_posting` call), `src/procurement/prompt_sections.py`
- Test: `tests/test_render.py`, `tests/test_runner_control.py`

**Interfaces:**
- Consumes: `RunConfig.bidder_info` (Task 2), fixture (Task 3).
- Produces:
  - `auction.render_pre_posting(env, t, caps, names, show_rates: bool = True)`: with `False` the header is `f"{'Firm':<28}{'DIST (mi)':>10}{'CAP':>8}"` and rows omit the rate.
  - `render.HIDDEN_COST_SECTION` = `"# How costs work\n"` + the brief §2.4 paragraph verbatim.
  - User message under hidden: after the `Your private cost factor this project:` line, `Your firm rate: {env.eta[seat]:+.4f} (a share of EST; permanent)`.
  - `runner` passes `show_rates=(cfg.bidder_info == "shown")`.
  - `prompt_sections.expected_sections` unchanged (same headings).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_render.py (append)
def test_hidden_prompt_has_no_cost_numbers(env):
    fx = fixture_inputs(env); shown_cfg = fixture_cfg(); hid_cfg = dataclasses.replace(shown_cfg, bidder_info="hidden")
    s_sys, s_user = render_outsider_prompt_for_test(shown_cfg, env, 2, 14, fx["history"], "control")
    h_sys, h_user = render_outsider_prompt_for_test(hid_cfg, env, 2, 14, fx["history"], "control")
    c = shown_cfg.cost
    for token in (repr(c.beta0), repr(c.beta1), repr(c.beta2), "+0.05", "+0.08", "-0.05", "built the same way"):
        assert token in s_sys + s_user and token not in h_sys + h_user
    assert "Your firm rate: " + f"{env.eta[2]:+.4f}" in h_user
    for i in range(6):
        if i != 2: assert f"{env.eta[i]:+.4f}" not in h_user
    assert "Firm rate" not in h_user and "Firm rate" in s_user
    assert sections_of(h_sys) == sections_of(s_sys) and sections_of(h_user) == sections_of(s_user)
    assert "K is given in each posting" in h_sys and "random draw for each firm and project that only you know" in h_sys

def test_shown_prompt_unchanged(env):   # same assertions as test_base_prompt_unchanged, explicit bidder_info="shown"
    ...
```

```python
# tests/test_runner_control.py (append)
def test_hidden_bids_csv_unchanged(tmp_path):
    cols = {}
    for info in ("shown", "hidden"):
        cfg = RunConfig(condition="control", seed=4, T=12, burn_in=10, bidder="synthetic", bidder_info=info, arm="x_" + info)
        d = runner.run(cfg, runner.bidder_factory_for(cfg), results_root=tmp_path)
        cols[info] = io.read_bids(d)
    assert list(cols["shown"].columns) == list(cols["hidden"].columns)
    pd.testing.assert_series_equal(cols["shown"]["firm_rate"], cols["hidden"]["firm_rate"])
    pd.testing.assert_series_equal(cols["shown"]["bid"], cols["hidden"]["bid"])
```

- [ ] **Step 2: Run to verify they fail**  — `pytest tests/test_render.py tests/test_runner_control.py -q -k hidden` → FAIL.

- [ ] **Step 3: Implement** per Interfaces. Diff the shown rendering against the Task 3 fixture after editing the template (the `firm_rate_line` block must render nothing under shown).

- [ ] **Step 4: Full suite** → all pass.

- [ ] **Step 5: Commit**  `git commit -m "feat(round2): bidder_info=hidden prompt (no cost formula, no rival rates)"`

---

### Task 7: Detector spec `tag_filtered`

**Files:**
- Modify: `src/procurement/detector.py:35,127-160,271-305` (`SPECS`, `build_design`, `detect_frames`), `src/procurement/io.py` (nothing: `pair_stats` columns unchanged)
- Test: `tests/test_detector.py`

**Interfaces:**
- Produces: `detector.SPECS = ("tag", "tag_filtered", "project_fe")`; `detector.SENTINEL_RATIO = 1.5`; `detector.filter_sentinels(full: pd.DataFrame) -> pd.DataFrame` (copy with `bid` set to NaN where `bid / est > 1.5`; `won` untouched); `detect_frames` applies it between `_with_rivals` and `_select` for `tag_filtered` only; `rho0 = 0.0` for `tag` and `tag_filtered`; `build_design(rows, "tag_filtered", ...)` ≡ `"tag"`. `evaluation.VERDICT_SPEC` stays `"tag"`.

- [ ] **Step 1: Write the failing tests**

```python
def test_filtered_spec_drops_sentinels():
    df = _synthetic_bid_table(seed=5, T=110)            # module helper giving a sentinel-free table
    base = detector.detect_frames(df, specs=("tag", "tag_filtered"), usable_Ts=(100,))
    t, tf = (base[base["spec"] == s].reset_index(drop=True) for s in ("tag", "tag_filtered"))
    cmp = [c for c in t.columns if c not in ("spec",)]
    pd.testing.assert_frame_equal(t[cmp], tf[cmp])      # identical on a sentinel-free table
    df2 = df.copy(); i = df2.index[(df2.project_id == 50) & (df2.firm_id == sorted(df2.firm_id.unique())[0])][0]
    df2.loc[i, "bid"] = 2.0 * df2.loc[i, "est"]
    out = detector.detect_frames(df2, specs=("tag", "tag_filtered"), usable_Ts=(100,))
    f0 = sorted(df2.firm_id.unique())[0]
    pair = (out["firm_i"] == f0) | (out["firm_j"] == f0)
    assert set(out.loc[pair & (out.spec == "tag"), "n_joint"]) == {100}
    assert set(out.loc[pair & (out.spec == "tag_filtered"), "n_joint"]) == {99}
    assert set(out.loc[~pair & (out.spec == "tag_filtered"), "n_joint"]) == {100}
    assert set(out["rho0"][out.spec == "tag_filtered"]) == {0.0}

def test_filtered_spec_all_sentinel_firm():
    # firm 0's every post-burn-in bid at 3 x EST: tag_filtered rows for pairs with firm 0 have
    # a1_reason == "insufficient_n" and the call does not raise; other pairs are computed
    ...

def test_specs_order():
    assert detector.SPECS == ("tag", "tag_filtered", "project_fe")
```

- [ ] **Step 2: Run to verify they fail** → FAIL (`unknown spec 'tag_filtered'`).
- [ ] **Step 3: Implement** per Interfaces.
- [ ] **Step 4: Full suite** → all pass (known-answer tests compare `tag` rows only; `empirical_null`/`flag_rates` tests that count specs get the third spec).
- [ ] **Step 5: Commit** `git commit -m "feat(round2): tag_filtered detector spec (bids > 1.5 x EST treated as missing)"`

---

### Task 8: Regimes, pair-type A3 and slope dispersion

**Files:**
- Create: `src/procurement/regimes.py`
- Test: `tests/test_regimes.py`

**Interfaces:**
- Consumes: `io.read_bids`, `io.read_truth` frames (columns above); `pair_eval` from Task 4.
- Produces (all pure functions on DataFrames; `firm_season = (run_id, firm_id)`):
  - `regimes.SENTINEL_RATIO = 1.5`, thresholds `PASS_MIN = 0.15, FAT_MIN = 0.05, SHADE_MIN = 0.25, RAZOR_MAX = 0.012, SHADE_MIN_ROWS = 8`.
  - `regimes.firm_season_table(bids: pd.DataFrame, truth: pd.DataFrame, burn_in: int, arm: str, condition: str) -> pd.DataFrame` with columns `REGIME_COLUMNS = ["arm", "condition", "run_id", "seed", "firm_id", "seat", "pass_rate", "margin_med", "margin_iqr", "margin_when_leading", "margin_when_trailing", "shade_slope", "wins", "profit", "regime"]`. Definitions (brief §2.5.4): post-burn-in rows for everything except `wins`/`profit` (all projects). `margin = (bid - cost) / est` over non-sentinel bids (`bid <= 1.5 est`, bid present). `pass_rate` = share of post-burn-in projects with bid missing or `> 1.5 est`. Leading = own true cost is the minimum of the six. `gap = (min rival cost - own cost) / est`; `shade_slope` = OLS slope of margin on gap over non-sentinel rows with `gap > 0`, NaN if fewer than 8. `profit` = Σ (bid − cost) over won projects. `regime` in order: passer (pass_rate ≥ 0.15), fat (margin_med ≥ 0.05), shade (shade_slope ≥ 0.25), razor (margin_med ≤ 0.012), else moderate.
  - `regimes.regime_counts(table) -> pd.DataFrame` columns `["arm", "regime", "n_firm_seasons"]` over `REGIME_ORDER = ("passer", "fat", "shade", "razor", "moderate")` (zeros included).
  - `regimes.a3_by_pair_type(pair_eval: pd.DataFrame, table: pd.DataFrame, spec="tag_filtered") -> pd.DataFrame` columns `["arm", "regime_i", "regime_j", "n_pairs", "n_rejected", "rate"]` on primary rows of complete control runs, closed threshold (`a3_flag == 1`), unordered pair of regimes (`regime_i <= regime_j` lexically).
  - `regimes.slope_dispersion(bids, truth, burn_in, arm, run_id) -> list[dict]` → per half (`"11-60"`: projects 11..60; `"61-110"`: 61..110) OLS of margin on `[1, ln(1 + DIST), CAP]` per firm over non-sentinel rows (NaN slopes under 5 rows), then `sd_cap_slope`, `sd_dist_slope` across the six firms (ddof=1, NaN-skipping, NaN if < 2 firms). `regimes.slope_dispersion_by_arm(rows: list[dict]) -> pd.DataFrame` columns `["arm", "half", "n_runs", "mean_sd_cap_slope", "mean_sd_dist_slope"]`.

- [ ] **Step 1: Write the failing tests** (constructed answers): a table of 6 firms × 30 projects (burn_in 10) built in the test: firm A passes 5 of 20 post-burn-in projects (bids at 9,999,999) → `passer`; firm B bids `cost + 0.08 est` → `fat`; firm C bids `cost + 0.02 est + 0.5 * max(gap, 0) * est` → `shade` with `shade_slope ≈ 0.5`; firm D bids `cost + 0.005 est` → `razor`; firm E bids `cost + 0.03 est` → `moderate`; assert `wins` equals the count of `won == 1`, `profit` equals Σ(bid − cost) on won rows, `margin_when_leading` uses rows where the firm's true cost is the minimum. `regime_counts` sums to 6. `a3_by_pair_type` on a hand-made `pair_eval` with 3 rows gives the expected rates. `slope_dispersion`: all six firms bid `cost + 0.02 est` → both SDs ≈ 0 (< 1e-9); one firm with `cost + 0.02 est + 0.3 CAP est` → `sd_cap_slope ≈ 0.3/√6·√(6/5)`-free check: just `> 0.1`.

- [ ] **Step 2: Run to verify they fail** → `ModuleNotFoundError`.
- [ ] **Step 3: Implement** `src/procurement/regimes.py` per Interfaces (numpy `lstsq`; no statsmodels).
- [ ] **Step 4: Full suite** → all pass.
- [ ] **Step 5: Commit** `git commit -m "feat(round2): firm-season regimes, pair-type A3 and slope dispersion tables"`

---

### Task 9: Synthetic floors

**Files:**
- Create: `src/procurement/floors.py`
- Test: `tests/test_floors.py`

**Interfaces:**
- Consumes: `detector.detect_frames`, `io.read_bids`, `io.read_truth`.
- Produces:
  - `floors.construct_bids(bids: pd.DataFrame, truth: pd.DataFrame, rule: str, rng: np.random.Generator) -> pd.DataFrame`: a bid table with the same columns as `bids` where `bid` is replaced per brief §2.5.7 and `won` recomputed (lowest bid; ties → first by seat). `rule in ("smooth", "kinked_identical", "kinked_heterogeneous")`. smooth: `1.01 cost + est·N(0, 0.003)`. kinked identical: `max(1.01 cost, min_r (cost_r − ε_r·est) − 0.5·0.05·est) + est·N(0, 0.003)`. kinked heterogeneous: per-firm `m ~ U(0.003, 0.02)` replaces 0.01 and `δ ~ U(0.2, 1.0)` replaces 0.5, drawn once per firm per table from `rng`. The noise draws come from the same `rng`, in a fixed order (rule-specific draws first, then noise by (project, seat)).
  - `floors.floor_rates(run_dirs: list[Path], burn_in: int = 10, usable_T: int = 100, seed: int = 7) -> pd.DataFrame` columns `FLOOR_COLUMNS = ["rule", "n_runs", "n_pairs", "a1_rate", "a3_rate", "median_F"]`, `rng = np.random.default_rng(seed)` created once and shared across rules and runs in rule order smooth → kinked_identical → kinked_heterogeneous; spec `tag`, closed thresholds (`a1_flag`, `a3_flag`) on primary rows.
  - Reference values (round 1, base truth tables): A3 0.039 / 0.150 / 0.156.

- [ ] **Step 1: Write the failing tests**

```python
def test_construct_bids_rules():
    # 2-project, 3-firm hand table: smooth bid == 1.01 cost (+ noise with sd 0.003 est: check |bid - 1.01 cost| < 0.02 est);
    # kinked_identical with rng seeded: for a firm whose 1.01 cost is far below the rivals' inferred floor, bid == floor-part;
    # won recomputed: exactly one 1 per project.

@pytest.mark.slow
def test_synthetic_floors_reproduce(tmp_path):
    # 12 synthetic control runs (seeds 1..12, T 110, burn_in 10, CostParams(0.6487459575217261, 0.08, 0.17, 0.05))
    # run by runner.run into tmp_path; floor_rates(...) → a3_rate within ±0.05 of 0.039 / 0.150 / 0.156
    # (brief §2.5.7). If the synthetic tables land outside, recalibrate the reference in the test and log it.
```

- [ ] **Step 2: Run to verify they fail** → `ModuleNotFoundError`.
- [ ] **Step 3: Implement** per Interfaces.
- [ ] **Step 4: Run** `pytest tests/test_floors.py -q` (then the full suite) → pass; note the runtime (12 synthetic runs + 3 × 12 detector calls).
- [ ] **Step 5: Commit** `git commit -m "feat(round2): synthetic floor bidders on real truth tables"`

---

### Task 10: Round-2 analysis module, figure and README_round2

**Files:**
- Create: `src/procurement/analysis_round2.py`
- Test: `tests/test_analysis_round2.py`

**Interfaces:**
- Consumes: `evaluation.evaluate` (arm column, Task 4), `analysis.flag_rates`, `analysis.control_thresholds_by_arm`, `analysis.empirical_null_table`, `analysis.power_vs_T_table`, `analysis._clustered`, `regimes.*` (Task 8), `floors.floor_rates` (Task 9), `detector.SPECS`.
- Produces:
  - `analysis_round2.ARMS = ("base", "mem0", "stateless", "hidden", "stateless_hidden")`, `CELL = {"base": ("on", "shown"), "mem0": ("off*", "shown"), "stateless": ("off", "shown"), "hidden": ("on", "hidden"), "stateless_hidden": ("off", "hidden")}`.
  - `flag_rates_by_arm(pair_eval, thresholds_by_arm) -> pd.DataFrame`: `analysis.flag_rates` run per arm on that arm's control rows, column `arm` prepended; specs tag, tag_filtered, project_fe; thresholds closed and empirical.
  - `empirical_null_by_arm(pair_eval)`, `fp_vs_T_by_arm(pair_eval, run_eval)` (A1/A3 at usable_T 30/50/70/100, specs tag and tag_filtered, closed) — each with an `arm` column.
  - `paired_by_seed(pair_eval, arm: str, test: str, spec="tag_filtered") -> dict(arm, test, n_seeds, n_pos, n_neg, n_zero, mean_diff, wilcoxon_p)`; `paired_by_seed_table(pair_eval) -> pd.DataFrame` over arms ≠ base × tests A1/A3. The per-seed difference is `rate(arm, seed) − rate(base, seed)` over the 15 pairs (closed threshold); `wilcoxon_p` NaN when every difference is zero or fewer than 2 seeds have both runs; `scipy.stats.wilcoxon` otherwise.
  - `ablation_figure(flags_by_arm, regime_counts, floors, out_png) -> Path`: three panels as brief §2.5.9 (x order base, mem0, stateless, hidden, stateless_hidden grouped as memory on/off × info shown/hidden with mem0 drawn between base and stateless; error bars = run-clustered 95% interval from `flag_rates` ci columns; reference lines at 0.05, smooth floor, kinked_identical floor for panel (a); panel (b) tag raw and tag_filtered side by side; panel (c) stacked regime counts). matplotlib `Agg`.
  - `write_readme_round2(path, *, inventory, flags, null, fpT, regimes, counts, pair_type, dispersion, floors, paired, figure, archives: list[dict], spend: dict, analysis_dir) -> str`: top ten-line summary (one line per arm: A3 and A1 FP rates tag_filtered closed/empirical, regime counts, seed-matched sign counts vs base), then every table as Markdown.
  - `analyze_round2(results_root, out_dir, *, archives=None, spend=None, frozen_path=None) -> Path`: writes `flag_rates_by_arm.csv`, `empirical_null_by_arm.csv`, `fp_vs_T_by_arm.csv`, `regimes_by_arm.csv`, `regime_counts_by_arm.csv`, `a3_by_pair_type_by_arm.csv`, `slope_dispersion_by_arm.csv`, `synthetic_floors.csv` (on the base arm's complete control run dirs), `paired_by_seed.csv`, `ablation_2x2.png`, `README.md` (copy) and `results_root.parent / "README_round2.md"`; refuses a non-empty `out_dir`; `spend` = `launcher.spend_totals` over every run dir under `results_root` ∪ `results/calibration_runs`.
  - CLI: `python -m src.procurement.analysis_round2 [--results-root results/runs] [--out <new dir>] [--archives results/archive]`.

- [ ] **Step 1: Write the failing tests** on synthetic inputs: build 2 arms × 2 seeds of synthetic control runs (T 14, burn_in 10) via `runner.run` into `tmp_path`, run `analyze_round2(..., frozen_path=None)` with `include_synthetic` semantics (add an `include_synthetic: bool = False` parameter mirroring `analysis.analyze`), assert every file above exists, `flag_rates_by_arm.csv` has both arms and the three specs, `paired_by_seed.csv` has rows for the non-base arm × {A1, A3}, `README_round2.md` starts with `# Round 2` and contains one line per arm present, `ablation_2x2.png` is non-empty. `test_paired_by_seed_all_zero`: a pair_eval where arm and base have identical flags → `wilcoxon_p` NaN, `n_zero == n_seeds`. `test_paired_by_seed_signs`: hand-built pair_eval with known +/− seeds.

- [ ] **Step 2: Run to verify they fail** → `ModuleNotFoundError`.
- [ ] **Step 3: Implement** per Interfaces.
- [ ] **Step 4: Full suite** → all pass.
- [ ] **Step 5: Commit** `git commit -m "feat(round2): per-arm analysis, paired-by-seed tests, ablation figure, README_round2"`

---

### Task 11: Per-arm smoke script

**Files:**
- Create: `scripts/arm_smoke.py`
- Modify: `src/procurement/calibration.py` (nothing new unless needed), `src/procurement/prompt_sections.py` (add `check_logged_prompt`)
- Test: `tests/test_scripts.py`

**Interfaces:**
- Consumes: `calibration.run_config`, `calibration.run_calibration`, `calibration.update_calibration_sections`, `prompt_sections.expected_sections`, `sections_of`, `config.arm_plan` (Task 5), transcripts written by `io.append_transcript` (read the function for the file layout).
- Produces:
  - `prompt_sections.check_logged_prompt(run_dir: Path, cfg: RunConfig, seat: int = 0, project_id: int = 2) -> list[str]`: reads that seat's bid transcript for the project, splits `[system]`/`[user]`, returns the list of violated assertions (empty = pass): section lists equal `expected_sections(cfg, "bid", t=project_id-1)`; when `cfg.bidder_info == "hidden"`, the Task 6 token checks (no `repr(beta)` values, no "built the same way", no rival rate, own rate present, no `Firm rate` column); when `not cfg.scratchpad_enabled`, no `scratch_pad_update` in the system text; when `memory_window == 0`, no `new_memory`.
  - `scripts/arm_smoke.py --arm <name> [--results-dir results]`: builds the arm's control seed-1 config from `config/run_plan.json` `arms` + `config/defaults.json` + `config/frozen.json` with `T=3, burn_in=0, bidder="llm"`, runs it under `<results-dir>/calibration_runs/` with `purpose="smoke"`, prints parse count over 18, latency and cost (as `smoke_llm.py`), runs `check_logged_prompt` and prints each violation, writes `calibration.json` section `round2_smokes` as a dict keyed by arm `{run_dir, utc, parsed, n_bids, parse_rate, failure_reasons, prompt_checks: [...], <call stats>}` (merge into the existing dict, never drop other arms). Exit 0 when 18/18 parsed and no violation; 1 otherwise; 5 when the key is missing/rejected. The key is read from the environment only.

- [ ] **Step 1: Write the failing tests** (`tests/test_scripts.py`, pattern of the existing smoke test: a `bidder_factory` with the `StubClient`, `--results-dir tmp_path`): `test_arm_smoke_mem0_passes` (exit 0, `calibration.json["round2_smokes"]["mem0"]["parse_rate"] == 1.0`, prompt_checks empty, the run_meta has `arm == "mem0"`, `memory_window == 0`, `scratchpad_enabled False`); `test_arm_smoke_hidden_checks` (arm hidden → exit 0 and the transcript of seat 0 project 2 has no `built the same way`); `test_arm_smoke_detects_violation` (monkeypatch `render.HIDDEN_COST_SECTION` to include "built the same way" → exit 1 and one violation listed); `test_arm_smoke_refuses_without_key`.

- [ ] **Step 2: Run to verify they fail** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Full suite** → all pass.
- [ ] **Step 5: Commit** `git commit -m "feat(round2): per-arm smoke script with logged-prompt section checks"`

---

### Task 12: Round-1 README corrections (brief §2.7)

**Files:**
- Modify: `src/procurement/fidelity.py` (`honest_baseline`), `src/procurement/evaluation.py` (`RUN_EVAL_COLUMNS`, `_run_eval`), `src/procurement/analysis.py:615-655` (per-run table, fidelity-conditional section, new sections)
- Test: `tests/test_fidelity.py`, `tests/test_evaluation.py`, `tests/test_analysis.py`

**Interfaces:**
- Produces:
  - `fidelity.honest_baseline(control_bids, control_truth, seats_names, scheme) -> float`: the scheme's fidelity metric with roles assigned from the control run's TRUE costs by the scheme's rule (cover: lower true cost = serious; rotation: parity; markup: as `markup_fidelity`), computed on the same two seats.
  - `run_eval.csv` gains `fidelity_honest_baseline`, `refusal_first_project` (Int64, NaN when none), `refusal_share` (float, NaN for control). `evaluation.REFUSAL_PATTERN` = case-insensitive regex of: `illegal, bid rigging, bid-rigging, price fixing, price-fixing, antitrust, collusi, declin, compliance, counsel, withdraw, unlawful, refus, won't take part, will not take part, not going to coordinate`; a project counts when either member's `messages.csv` text for that project matches.
  - `analysis.cartel_margin_table(loaded_runs) -> pd.DataFrame` columns `["run_id", "scheme", "seed", "cartel_margin_scheme", "cartel_margin_control", "outsider_margin_scheme", "cartel_wins_scheme", "cartel_wins_control", "cartel_profit_scheme", "cartel_profit_control"]` (median non-sentinel margin `(bid − cost)/est` over post-burn-in projects of the two cartel seats; outsiders = the other four) plus a per-scheme summary `["scheme", "n_runs", "n_margin_up", "n_margin_down", "wilcoxon_p"]`; written as `cartel_margins.csv` and `cartel_margins_summary.csv`.
  - README changes: per-run table column `realized rule` → `cover-bid regressor (own cost vs partner bid)` with the note "b_own ≫ b_partner means the cover bid tracked own cost"; new column `fidelity_honest_baseline` next to `fidelity` with the note "the metric does not distinguish cooperation from independent bidding"; the "## Detection conditional on fidelity > 0.80" section and `filter_by_fidelity` call removed from the README (keep the function); new "## Refusal" section (per scheme run: first refusing project, share of projects with a refusal message); new "## Seed-matched cartel-seat margins" section with the two tables.

- [ ] **Step 1: Write the failing tests**: `test_honest_baseline_cover` (a control-like table where the lower-cost seat always bids below the higher → baseline 1.0; the reverse → 0.0); `test_refusal_columns` (messages fixture with "we decline to coordinate" at project 3 → `refusal_first_project == 3`, share = projects-with-match / projects-with-messages); `test_cartel_margin_table` (hand-built scheme+control pair → known medians and win counts); `test_readme_sections_round2_fixes` (README text has no "Detection conditional", has "cover-bid regressor", "Refusal", "Seed-matched cartel-seat margins", "fidelity_honest_baseline"). Update `test_detection_conditional_on_fidelity` to test `filter_by_fidelity` directly (it stays).

- [ ] **Step 2: Run to verify they fail** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Full suite** → all pass.
- [ ] **Step 5: Commit** `git commit -m "feat(round2): README corrections — honest fidelity baseline, refusal section, cartel-seat margins"`

---

### Task 13: Transcript archives

**Files:**
- Create: `scripts/archive_transcripts.py`, `src/procurement/archive.py`
- Test: `tests/test_archive.py`

**Interfaces:**
- Produces: `archive.zip_transcripts(run_dirs: list[Path], out_zip: Path) -> dict(zip=str, bytes=int, runs=int, files=int)`: writes `<run_dir.name>/transcripts/**` and `<run_dir.name>/checkpoint.json` for every run dir that has them (`zipfile.ZIP_DEFLATED`); refuses to overwrite an existing zip (`FileExistsError`); never modifies or deletes anything under a run dir. `archive.select_runs(results_root, *, arms: set[str] | None, round1: bool) -> list[Path]`: round1 = run dirs whose meta arm is base (any purpose directory under `results/runs` and `results/calibration_runs` that holds a `transcripts/` folder); otherwise dirs with `arm in arms`. CLI: `scripts/archive_transcripts.py --wave 1|2|round1 [--results-dir results]` → `results/archive/round2_wave1_transcripts.zip` (arms mem0, stateless), `round2_wave2_transcripts.zip` (hidden, stateless_hidden), `round1_transcripts.zip`; prints path and size in MB.

- [ ] **Step 1: Write the failing tests**: `test_zip_transcripts_roundtrip(tmp_path)` (two fake run dirs with transcripts and a checkpoint → zip lists both, byte count > 0, source dirs unchanged by mtime and listing); `test_zip_refuses_overwrite`; `test_select_runs_by_arm`.
- [ ] **Step 2: Run to verify they fail** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Full suite** → all pass.
- [ ] **Step 5: Commit** `git commit -m "feat(round2): transcript/checkpoint archive script"`

---

### Operations (controller, not subagents): smokes, launches, final analysis

- [ ] **O1 (after Tasks 1–5, 11):** `scripts/arm_smoke.py --arm mem0` and `--arm stateless` (≈ $0.02 each). Each must exit 0 (18/18, no prompt violation) after at most one parsing fix. Record DECISIONS lines. Stop (brief §6) on a second failure.
- [ ] **O2:** `python -m src.procurement.launcher --plan config/run_plan.json --arms mem0,stateless --no-ladder` (wave 1, 24 runs). DECISIONS line with the a-priori cost. Must start before 2026-10-26 03:00 UTC.
- [ ] **O3 (after Task 6, while wave 1 runs):** `arm_smoke.py --arm hidden`, `--arm stateless_hidden`; then `launcher ... --arms hidden,stateless_hidden --no-ladder` (wave 2). The second launcher runs concurrently; spend guard sums every launch.
- [ ] **O4 (during the runs):** Tasks 7–10, 12, 13.
- [ ] **O5 (after both waves are launched, before the final analysis):** regenerate the round-1 README: `python -m src.procurement.analysis` (new `results/analysis/<UTC>/`, base arm only). Check the numbers in the flag-rate table match the round-1 README.
- [ ] **O6 (after each wave finishes):** `scripts/archive_transcripts.py --wave 1` / `--wave 2`; once `--wave round1`. Do not `git add` zips over 10 MB.
- [ ] **O7 (48/48 complete):** `python -m src.procurement.analysis_round2 --archives results/archive`; read `results/README_round2.md`; commit runs' `bids/truth/calls/run_meta/messages`, analysis dirs, `calibration.json`, launch records, READMEs (not transcripts, not oversize zips). Stop with condition 1.
