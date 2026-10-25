# Bajari–Ye Procurement Market Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the sealed-bid procurement market, the exclusive cartel channel, the B&Y detector, the evaluation and analysis layer, and the launcher described in the spec, with a test suite that runs end to end with zero API calls.

**Architecture:** A new package `src/procurement/` owns the market loop, channel, logging, detector, evaluation, analysis and launcher, and imports only the reusable agent plumbing from the existing code (`MemoryManager`, `render_prompt`, `extract_json`, transcript format). A vectorized synthetic simulator (`synthetic.py`) reproduces the runner's bids exactly for fixed seeds and powers the slow known-answer tests; the runner is proven equivalent to it on one seed. The LLM path (client, prompts, channel) is built after the offline suite is green and is exercised in tests only through a stub client.

**Tech Stack:** Python 3.11, numpy 2.2.2, scipy 1.17.1, pandas 2.2.3, jinja2 3.1.5, anthropic 0.45.2, matplotlib 3.10.0, pytest 8.3.4.

**Spec:** `docs/superpowers/specs/2026-10-25-bajari-ye-procurement-design.md` (read it first; this plan cites its §/A-numbers). Operating rules: `docs/superpowers/step4-audit.md` Part 4. Decisions log: `docs/superpowers/DECISIONS.md`.

## Global Constraints

- Python 3.11.9; must import on 3.12. Libraries: `requirements.txt` plus `scipy==1.17.1` (Task 1 adds it). No statsmodels, no asyncio frameworks.
- Zero API calls in the suite: `tests/conftest.py` autouse fixture makes `anthropic.Anthropic.__init__` and `openai.OpenAI.__init__` raise `RuntimeError("API client constructed inside the test suite")`.
- One command: `python -m pytest tests -q` from the repo root with `.venv` active; must be green, zero skips, under 15 minutes.
- Every random draw from a named `numpy.random.default_rng`: environment `default_rng(seed)` drawing EST, yards, sites, η, tags, ε in that order; names `default_rng(1000 + seed)`; synthetic noise `default_rng(2000 + seed)` in (project, seat) order.
- Config values, never literals: T=110, burn_in=10, N=6, β₀=0.90, β₁=0.08, β₂=0.17, σ_ε=0.05, honest margin 1.05, honest noise 0.03, K fraction 0.25, EST range [200000, 2000000], map 100, η range ±0.06, ν levels {−0.05, 0, 0.05, 0.08} with labels routine/standard/complex/hazardous, history window 20, memory window 10, message cap 500, concurrency 6, model `claude-haiku-5-5`, max_tokens 8000, A1 critical |z| 1.959964, A3 α 0.05, min n_joint 4, empirical percentile 95, usable_T list [30, 50, 70, 100], pricing 0.10/0.50/0.125/0.01 per M tokens, spend stop 75, stall 600 s, transport retry budget 600 s.
- CSV/JSON contracts of spec §4g hold byte-for-byte in column names, order and types. Missing = empty field, never `NaN`/`None` text.
- `results/` is never deleted or overwritten; each run gets `results/runs/<UTC yyyymmdd_HHMMSS>_<condition>_s<seed>/`.
- Synthetic tests pass their parameters explicitly (β₀ 0.90, β₁ 0.08, β₂ 0.17, σ_ε 0.05, noise 0.03), never read config defaults (spec A86).
- Verbatim texts (cartel blocks, role sentences, message format) are copied from spec §4d–§4e exactly.
- Commit after every green task with a conventional message ending in `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- When a faithful implementation misses a §6 bound: rerun `scripts/calibration/calib_spec_thresholds.py` on the same seeds; agree → move the bound to observed ∓ 0.07 and add a `DECISIONS.md` line; disagree → the implementation is wrong (spec §4e).

## Review Focus

Inputs the spec implies but no §6 row exercises, most likely to bite first; each has its pinning test added to the owning task below.

1. A firm's bid is a JSON number in scientific notation or with more than two decimals (`9.45e5`, `945000.555`): must parse and round to cents, not be rejected as `bad_value`. → Task 10 `test_bid_parsing_numeric_forms`.
2. A message reply that is valid JSON but `my_cost`/`intended_serious_bid` are missing or non-numeric: the text part must still be delivered, the numbers empty in `messages.csv`, and the synthetic/LLM cover member must still bid (treating the partner's intended bid as unknown → fall back to own honest bid + margin). → Task 11 `test_message_partial_fields`.
3. Resume from checkpoint in the middle of a scheme project (message 1 done, bids not): the resumed run must redo the whole project, not deliver a stale message twice or skip a bid. → Task 7 `test_checkpoint_resume_mid_project`.
4. A control run directory whose `bids.csv` has one firm missing every bid (all 110 empty): the detector must drop that firm's rows, mark its 5 pairs `insufficient_n`, still produce the other 10 pairs, and the empirical null must pool only computed statistics. → Task 8 `test_detector_firm_never_bids`.
5. Two seeds whose shuffled firm names collide in lexical order with seat order (name order ≠ seat order): the detector's firm dummy base and interaction column names are by lexical firm_id, the evaluation joins by firm_id → seat via `run_meta.seat_to_firm`; a bug here silently mislabels pair classes. → Task 9 `test_pair_class_join_uses_seat_map`.

---

## File structure

```
src/procurement/__init__.py
src/procurement/config.py          # dataclasses, defaults loading, run plan, seat rule, first speaker, pricing
src/procurement/environment.py     # Environment, generate_environment, cost(), firm_names()
src/procurement/auction.py         # resolve_project, AwardLedger, rival_covariates, Posting text
src/procurement/rules.py           # scripted scheme rules (cover_inflate, cover_shadow, markup, roles)
src/procurement/synthetic.py       # SyntheticBidder, vectorized simulate_table() for slow tests
src/procurement/io.py              # CSV/JSON writers+readers, run directory, heartbeat, checkpoint, transcripts
src/procurement/channel.py         # Channel: permissions, order, delivery text, truncation
src/procurement/runner.py          # run(config, bidder_factory) -> RunDir; project loop; phases; resume
src/procurement/detector.py        # design matrices, OLS, A1, A3, pair_stats, empirical thresholds, CLI
src/procurement/fidelity.py        # cover/rotation/markup fidelity, realized_rule, profit_delta
src/procurement/evaluation.py      # pair_eval, run_eval, pair classes, verdicts, CLI
src/procurement/analysis.py        # flag_rates, empirical_null, power_vs_T, robustness, figure, README numbers, CLI
src/procurement/prompts/bid.jinja2
src/procurement/prompts/message.jinja2
src/procurement/texts.py           # verbatim cartel texts and message format string
src/procurement/render.py          # build system prefix + user message for bid and message prompts
src/procurement/client.py          # HaikuClient (transport policy, headers), parse_bid, parse_message, CallRecord
src/procurement/llm_bidder.py      # LLMBidder (memory window, scratchpad, transcripts)
src/procurement/launcher.py        # plan → manifest → processes; monitor; sizing rule; spend guard; CLI
config/defaults.json
config/firms.json
scripts/smoke_llm.py  scripts/cover_smoke.py  scripts/pilot.py  scripts/tier_check.py
tests/conftest.py and tests/test_<module>.py per task
```

Interfaces shared by many tasks (defined in Task 1 and Task 2, used verbatim after):

```python
# config.py
@dataclass(frozen=True)
class CostParams: beta0: float; beta1: float; beta2: float; sigma_eps: float; k_frac: float = 0.25
    est_min: float = 200_000.0; est_max: float = 2_000_000.0; map_size: float = 100.0; eta_range: float = 0.06
    nu_levels: tuple[float, ...] = (-0.05, 0.0, 0.05, 0.08); tag_labels: tuple[str, ...] = ("routine", "standard", "complex", "hazardous")
    honest_margin: float = 1.05; honest_noise: float = 0.03
@dataclass(frozen=True)
class RunConfig: condition: str; seed: int; T: int = 110; burn_in: int = 10; N: int = 6; bidder: str = "synthetic"
    cost: CostParams = CostParams(0.90, 0.08, 0.17, 0.05); cartel_seats: tuple[int, int] | None = None  # None → seat rule
    history_window: int = 20; memory_window: int = 10; max_message_length: int = 500; concurrency: int = 6
    model: str = "claude-haiku-5-5"; max_tokens: int = 8000; api_key_slot: int = 1; results_root: str = "results/runs"
    scripted_margin: float = 0.10; scripted_rule: str = "inflate"; serious_add: float = 0.0
def cartel_seats(seed: int, condition: str) -> tuple[int, int] | None   # control → None
def first_speaker(project_id: int, condition: str, seats: tuple[int, int]) -> int
def run_plan(R: dict[str, int], include_rotation: bool = True, T: int = 110) -> list[RunConfig]
# environment.py
@dataclass(frozen=True)
class Environment: seed: int; T: int; N: int; est: np.ndarray; yards: np.ndarray; sites: np.ndarray; dist: np.ndarray
    eta: np.ndarray; tag: np.ndarray; tag_label: list[str]; nu: np.ndarray; eps: np.ndarray; names: list[str]; K: float; season_total: float
def generate_environment(seed: int, cost: CostParams, T: int, N: int = 6) -> Environment
def cost_of(env: Environment, cost: CostParams, seat: int, t: int, cap: float) -> float   # t is 0-based project index
```

`project_id` in files is 1-based; internal indices `t` are 0-based. `seat` is 0–5. `firm_id` is `env.names[seat]`.

---

### Task 1: Package scaffold, config, run plan, seat rule, test guard

**Files:**
- Create: `src/procurement/__init__.py`, `src/procurement/config.py`, `config/defaults.json`, `config/firms.json`, `tests/__init__.py`, `tests/conftest.py`, `tests/test_config.py`
- Modify: `requirements.txt` (append `scipy==1.17.1`)

**Interfaces:**
- Produces: the dataclasses and functions in "Interfaces shared" above; `load_defaults(path="config/defaults.json") -> dict`; `Pricing(input=0.10, output=0.50, cache_creation=0.125, cache_read=0.01)` with `cost_usd(input_tokens, output_tokens, cache_creation, cache_read) -> float`; `sizing_rule(output_token_limit: int, has_key2: bool) -> dict[str, int]` returning `{"control": 12, "cover": 12, "markup": 4, "rotation": 4}` or the 8/8/4/4 variant; `firm_names(seed) -> list[str]` lives in environment.py (Task 2) but reads `config/firms.json`.

- [ ] **Step 1: Write the failing tests** in `tests/test_config.py`

```python
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
    import anthropic, pytest
    with pytest.raises(RuntimeError, match="API client constructed inside the test suite"):
        anthropic.Anthropic(api_key="x")
```

- [ ] **Step 2: Run to verify failure**: `python -m pytest tests/test_config.py -q` → ImportError.
- [ ] **Step 3: Implement** `config.py` per the interfaces; `config/defaults.json` with every Global Constraints value; `config/firms.json` = `["Northfield Paving", "Lakeshore Asphalt", "Prairie Roadworks", "Granite Ridge Construction", "Riverbend Surfacing", "Summit Grading"]`; `tests/conftest.py` with the autouse guard fixture (monkeypatch both constructors) and `repo_root` fixture; append scipy to requirements.
- [ ] **Step 4: Run**: `python -m pytest tests/test_config.py -q` → 7 passed.
- [ ] **Step 5: Commit** `feat(procurement): config, run plan, seat rule, pricing, test guard`.

### Task 2: Environment and cost generator

**Files:** Create `src/procurement/environment.py`, `tests/test_environment.py`.

**Interfaces:** Produces `Environment`, `generate_environment`, `cost_of`, `firm_names(seed) -> list[str]` (shuffle of `config/firms.json` by `default_rng(1000 + seed)`), `K = k_frac * est.sum()`, `season_total = est.sum()`.

- [ ] **Step 1: Failing tests** (spec §6.1, names as listed there): `test_env_shapes_and_ranges` (seed 1: `est.shape == (110,)`, within [200000, 2000000], integers; yards (6,2), sites (110,2) in [0,100]; dist equals `np.linalg.norm(yards[:,None]-sites[None], axis=2)` and ≤ 141.43; η within ±0.06; tag ∈ {0,1,2,3}; `tag_label == [labels[t] for t in tag]`; `nu == levels[tag]`; eps shape (6,110)); `test_est_log_uniform` (T=100000: `abs(np.log(est).mean()-13.357) < 0.01`, `abs(np.log(est).std()-0.6647) < 0.01`); `test_tag_uniform` (T=100000, each share within 0.01 of 0.25); `test_eps_normal` (T=10000, 60000 draws: `abs(mean) < 0.001`, `abs(std-0.05) < 0.001`); `test_cost_formula_exact` (cost_of with dist e−1 → exactly 1125000.0 using `math.isclose(..., rel_tol=1e-12)`); `test_sigma_eps_and_beta0_configurable` (σ_ε 0.08 → std within 0.002; β₀ 0.95 → 1175000.0; `CostParams` defaults 0.05/0.90 via `load_defaults`); `test_matched_seed_across_conditions` (two `generate_environment(3, …)` calls equal in every array; seed 4 differs in est); `test_dist_varies` (per project `dist[:,t].std() > 0`; per firm `dist[i].std() > 0`); `test_firm_names_by_seed` (6 distinct names from firms.json; seed 1 order ≠ seed 2; same seed same order).
- [ ] **Step 2: Run** → fails (module missing).
- [ ] **Step 3: Implement** `generate_environment`: `rng = np.random.default_rng(seed)`; `est = np.round(np.exp(rng.uniform(np.log(est_min), np.log(est_max), T)))`; `yards = rng.uniform(0, map_size, (N, 2))`; `sites = rng.uniform(0, map_size, (T, 2))`; `eta = rng.uniform(-eta_range, eta_range, N)`; `tag = rng.integers(0, 4, T)`; `eps = rng.normal(0, sigma_eps, (N, T))`; dist, nu, labels, names, K, season_total derived. `cost_of` returns `est[t] * (beta0 + beta1*log1p(dist[seat,t]) + beta2*cap + eta[seat] + nu[t] + eps[seat,t])`.
- [ ] **Step 4: Run** → 9 passed. **Step 5: Commit** `feat(procurement): environment and cost generator`.

### Task 3: Scripted rules and synthetic bidder with vectorized simulator

**Files:** Create `src/procurement/types.py`, `src/procurement/rules.py`, `src/procurement/synthetic.py`, `tests/test_rules.py`, `tests/test_synthetic.py`.

**Interfaces:**
- `types.py` (defined here, consumed by Tasks 6, 7, 12): `@dataclass class BidContext: run: RunConfig; env: Environment; t: int; seat: int; cap: np.ndarray; cost: float; history: list[dict]; messages: list[dict]; memory: list[str]; scratchpad: str; role: str | None; seats: tuple[int,int] | None`; `@dataclass class BidResult: bid: float | None; reason: str; parsed: dict | None; raw: str | None; call_records: list[dict]`; `@dataclass class MessageResult: text: str; my_cost: float | None; intended_serious_bid: float | None; reason: str; raw: str | None; call_records: list[dict]`; `class Bidder(Protocol): def bid(self, ctx: BidContext) -> BidResult: ...; def message(self, ctx: BidContext) -> MessageResult: ...`.
- `rules.py`: `cover_inflate(h: float, s: float, est: float, margin: float = 0.10) -> float` = `max(h + margin*est, s + 0.01*est)`; `cover_shadow(s: float) -> float` = `round(1.04*s, 2)`; `markup_bid(b: float, est: float, pct: float = 0.10) -> float`; `cover_roles(cost_a, cost_b, seat_a, seat_b) -> (serious_seat, cover_seat)` (lower cost serious; tie → seat_a serious); `rotation_roles(project_id, seat_a, seat_b)` (odd → a serious).
- `synthetic.py`: `honest_bid(cost, est, noise) -> float` = `round(cost*1.05 + est*noise, 2)`; `class SyntheticBidder` implementing the `Bidder` protocol of Task 6 (`bid(ctx) -> BidResult`, `message(ctx) -> MessageResult`) using `default_rng(2000 + seed)` draws in (project, seat) order, applying the scripted rule for cartel seats using the delivered partner message's `intended_serious_bid`; `simulate_table(seed, cost: CostParams, T, scheme: str | None, cartel: tuple[int,int] | None, margin=0.10, rule="inflate", serious_add=0.0, shock: float | None = None) -> tuple[pd.DataFrame, pd.DataFrame]` returning `(bids_df, truth_df)` with exactly the spec §4g columns, computed in-loop (award and CAP recomputed under the scheme), noise drawn in the same order as `SyntheticBidder` so Task 7 can assert equivalence. `scheme_message_text(cost, intended) -> str` = `f"Cost: ${cost:,.0f}. If I am the serious bidder I intend to bid ${intended:,.0f}."`.

- [ ] **Step 1: Failing tests** (spec §6.2–6.3): `test_cover_inflate_rule` (1e6, 945000, 997500 → 1097500; h 800000 → 955000), `test_cover_shadow_rule` (→ 982800), `test_cover_roles_by_cost`, `test_rotation_rule` (project 1 → (a, b); project 2 → (b, a)), `test_markup_rule` (945000 → 1045000), `test_serious_add_variant` (simulate_table with serious_add 0.05: serious member's bid on a cover project equals honest + 50000 at EST 1e6 — assert via truth/bids on a hand-checked project), `test_honest_bid_formula` (→ 1070000.0), `test_honest_bidder_distribution` (100 seeds × 660: mean in [1.32, 1.37], sd in [0.09, 0.12]; mark `slow`), `test_honest_bidder_deterministic_and_offline` (seed 5 twice identical; guard fixture active), `test_simulate_table_schema` (columns exactly `["run_id","project_id","firm_id","est","tag","dist","cap","firm_rate","bid","won"]`, 660 rows, `won.sum() == 110`, truth columns per §4g).
- [ ] **Step 2: Run** → fails. **Step 3: Implement** per interfaces. In `simulate_table`, loop t over T: compute cap from ledger, costs, honest bids, apply scheme in-loop, award. CAP for project t uses awards before t.
- [ ] **Step 4: Run** `python -m pytest tests/test_rules.py tests/test_synthetic.py -q` → passed. **Step 5: Commit** `feat(procurement): scripted rules and synthetic simulator`.

### Task 4: Sealed-bid auction, ledger, rival covariates, postings

**Files:** Create `src/procurement/auction.py`, `tests/test_auction.py`.

**Interfaces:** `resolve_project(bids: dict[int, float | None]) -> tuple[int | None, float | None]` (lowest bid; ties → lowest seat; all None → (None, None)); `class AwardLedger(K: float, N: int)` with `.cap(seat) -> float`, `.award(seat, est)`, `.won` array; `rival_covariates(dist_t: np.ndarray, caps_t: np.ndarray) -> tuple[np.ndarray, np.ndarray]` returning `(mdist, maxp)` per seat over the other five; `render_pre_posting(env, t, caps, names) -> str` containing project id, EST, tag label, season total, K and a 6-row table of DIST (1 dp), CAP (3 dp), firm rate (4 dp); `render_post_posting(names, bids, winner) -> str` with "no bid" for None.

- [ ] **Step 1: Failing tests** (spec §6.4 + `rival_covariates` from §6.6): `test_lowest_bid_wins_paid_bid`, `test_tie_break` (seats 2 and 4 at 940000.00 → 2), `test_missing_bid_excluded`, `test_all_missing`, `test_cap_updates_after_award` (K 10M, award 400k → 0.04; second award 20M → cap 2.04 > 1 allowed), `test_cap_uses_past_awards_only` (ledger sequence 300k, 500k → caps 0, 300k/K, 800k/K), `test_rival_covariates` (dists [10,20,30,40,50,60], caps [0.1,0.5,0.2,0,0.3,0.9] → mdist[0]=20, mdist[1]=10, maxp[0]=1.0, maxp[3]=0.9), `test_posting_contents` (pre posting contains "Project 5", "$", the tag label, "K", all six names; does not contain "cost" or "private"; post posting lists "no bid" for a None and names the winner).
- [ ] **Step 2–5**: run (fail) → implement → run (pass) → commit `feat(procurement): sealed-bid auction and postings`.

### Task 5: Run directory, CSV/JSON writers and readers, heartbeat, checkpoint, transcripts

**Files:** Create `src/procurement/io.py`, `tests/test_io.py`.

**Interfaces:** `new_run_dir(results_root, condition, seed, now_utc: datetime | None = None) -> Path` (`<yyyymmdd_HHMMSS>_<condition>_s<seed>`; if it exists, append `_1`, `_2`… never overwrite); `BIDS_COLUMNS`, `TRUTH_COLUMNS`, `MESSAGES_COLUMNS`, `CALLS_COLUMNS`, `PAIR_STATS_COLUMNS` constants (exact §4g order); `write_csv(path, rows: list[dict], columns)` writing empty string for None/NaN; `append_csv_row(path, row, columns)`; `read_bids(run_dir) -> pd.DataFrame` (bid parsed as float with NaN for empty, used only inside the detector); `write_run_meta(run_dir, meta: dict)`, `read_run_meta`; `write_heartbeat(run_dir, last_completed_project, last_completed_utc, last_activity_utc)`, `touch_activity(run_dir)`; `write_checkpoint(run_dir, state: dict)`, `read_checkpoint`; `append_transcript(run_dir, firm_id, project_id, phase, prompt, raw_response)` writing `transcripts/<firm_id>.md` in the repo's `## Prompt:` / `## Response:` format.

- [ ] **Step 1: Failing tests**: `test_new_run_dir_never_overwrites` (two calls with the same fixed `now_utc` → two different paths, both exist), `test_csv_missing_is_empty_field` (row with `bid=None` → the line has an empty field, no "nan"/"None"), `test_column_constants_match_spec` (literal lists equal to §4g), `test_heartbeat_fields` (json has `last_completed_project`, `last_completed_utc`, `last_activity_utc`; `touch_activity` changes only the last), `test_checkpoint_roundtrip` (dict with numpy arrays and nested dicts survives write/read), `test_transcript_format` (contains `## Prompt: Project 3 (bid)` and `## Response:` blocks).
- [ ] **Step 2–5**: fail → implement → pass → commit `feat(procurement): run directory and file contracts`.

### Task 6: Bidder protocol, channel, and the control-run runner (synthetic)

**Files:** Create `src/procurement/channel.py`, `src/procurement/runner.py`, `tests/test_channel.py`, `tests/test_runner_control.py`. Modify `src/procurement/synthetic.py` (SyntheticBidder conforms to the protocol).

**Interfaces:**
- `runner.py` (consumes `BidContext`, `BidResult`, `MessageResult`, `Bidder` from `types.py`, Task 3): `run(cfg: RunConfig, bidder_factory: Callable[[RunConfig, Environment], Bidder], results_root: str | Path | None = None, resume_dir: Path | None = None) -> Path` returning the run dir; writes every §4g file; project loop: pre posting → (scheme: message phase per Task 6 channel, outsiders' bids started concurrently with message 1) → bids → award → post posting → history append → heartbeat/checkpoint. `run_in_memory(cfg, bidder_factory) -> tuple[pd.DataFrame, pd.DataFrame, list[dict]]` for tests (same loop, no files). `make_run_meta(cfg, env, limits=None, frozen_from=None) -> dict`.
- `channel.py`: `class Channel(seats: tuple[int,int], condition: str, max_len: int = 500)` with `.first_speaker(project_id) -> int`, `.deliver(result: MessageResult) -> str` (format `"Cost: ${my_cost:,.0f}. If I am the serious bidder I intend to bid ${intended:,.0f}. {text}"` truncated to max_len; if a number is missing its clause is omitted), `.can_message(seat) -> bool`, `.exchange(project_id, send: Callable[[int, list[dict]], MessageResult]) -> list[dict]` performing message 1 then message 2 and returning the two message rows in §4g `messages.csv` form.

- [ ] **Step 1: Failing tests**: `test_only_cartel_can_message` (channel for seats (1,4): `can_message(1)` True, `can_message(2)` False; `Channel(None, "control")` has no exchange); `test_sequential_order_and_delivery` (exchange with a recording `send`: project 3 → first call seat a with `messages == []`, second call seat b with one message from a; project 4 reversed; rotation project 3 → scheduled serious bidder first); `test_message_format_and_cap` (912345, 958000, 600-char text → starts with the exact prefix and `len == 500`); `test_message_partial_fields` (my_cost None → delivered text omits the cost clause; row has empty my_cost); `test_end_to_end_synthetic_control` (seed 1, `run()` with synthetic factory to a tmp results root: every file exists; bids.csv 660 rows with exact columns and types via `pd.read_csv(dtype=str)`; 6 rows per project_id 1..110; `won` has at most one "1" per project; cap non-decreasing per firm; byte-identical bids.csv on a second run); `test_truth_table_schema` (cost recomputed from bids.csv covariates + eps + nu + firm_rate within 1e-6); `test_truth_never_in_bids`; `test_matched_seed_runs_share_environment` (control seed 1 vs a cover seed 1 run_in_memory: est, tag, dist, firm_rate columns equal; eps, nu equal); `test_heartbeat_written` (after run: last_completed_project 110); `test_no_overwrite_results`; `test_run_meta_contents` (every §4g key; control → `cartel_seats == []`, `scheme == "none"`, `effort == "default (medium)"`).
- [ ] **Step 2–5**: fail → implement → pass → commit `feat(procurement): channel and synthetic control runner`.

### Task 7: Scheme runs on the synthetic bidder, resume, equivalence with the simulator

**Files:** Modify `src/procurement/runner.py`, `src/procurement/synthetic.py`; create `tests/test_runner_schemes.py`.

**Interfaces:** `SyntheticBidder.message(ctx)` returns `scheme_message_text(cost, intended)` with `intended = honest_bid` (or honest + serious_add); `SyntheticBidder.bid(ctx)` for a cartel seat in cover/rotation: role from `ctx.role` (runner sets it from the two messages' `my_cost` for cover, from parity for rotation); cover member uses `cover_inflate(h, s_from_message, est, cfg.scripted_margin)` or `cover_shadow`; if the partner's intended bid is missing, uses `h + margin*est`; markup members add 0.10·EST. Runner sets `ctx.role` after the exchange; `truth.csv` `cartel_role` filled per project.

- [ ] **Step 1: Failing tests**: `test_messages_and_meta` (cover seed 1: messages.csv 220 rows, order alternates 1,2 per project, sender/recipient ⊂ cartel names; control: header only); `test_scripted_messenger_in_suite` (cover seed 1: for project 15 the cover member's bid equals `max(h + 0.10E, s + 0.01E)` with s parsed from the partner's message row, h recomputed from truth cost and the seed-2000 noise); `test_messages_never_leak` (bids.csv has no column from messages; detector input identical with messages.csv deleted — implemented in Task 8 as a read, here assert only the CSV contract); `test_runner_equals_simulate_table` (for seed 1 and each of control/cover/rotation/markup: `run_in_memory` bids/truth frames equal `simulate_table` frames exactly — this ties the slow tests to the real runner); `test_fidelity_inputs_from_scheme_runs` is deferred to Task 9; `test_checkpoint_resume` (run cover seed 2 with a bidder that raises after project 37 is written; call `run(cfg, factory, resume_dir=dir)`: final bids.csv byte-identical to an uninterrupted run); `test_checkpoint_resume_mid_project` (raise after message 1 of project 40: resumed run redoes project 40 from message 1; messages.csv has exactly two rows for project 40). Rotation and markup runs: `test_end_to_end_synthetic_each_scheme_files` (files, two cartel firms in truth with seats per §4f rule: cover seed 1 → (0,3), rotation (1,4), markup (2,5); 220 message rows). The detector assertions of §6.10 are added in Task 8.
- [ ] **Step 2–5**: fail → implement → pass → commit `feat(procurement): scheme runs, resume, simulator equivalence`.

### Task 8: Detector

**Files:** Create `src/procurement/detector.py`, `tests/test_detector.py`, `tests/test_detector_known_answer.py`.

**Interfaces:**
- `load_bid_table(run_dir: Path) -> pd.DataFrame` (reads only `bids.csv`); `prepare_rows(df, burn_in: int, usable_T: int) -> pd.DataFrame` (project_id > burn_in, first `usable_T` usable projects, bid present; adds `y = bid/est`, `ldist`, `lmdist`, `maxp` with rivals over all five regardless of bid); `build_design(rows: pd.DataFrame, spec: str, constrain: tuple[str,str] | None = None) -> tuple[np.ndarray, list[str]]` (`spec ∈ {"tag","project_fe"}`; column order per §4h; firm order lexical; base firm = lexically first; base tag routine; base project = smallest project_id present); `fit_ols(X, y) -> tuple[np.ndarray, np.ndarray, int]` returning (beta, residuals, rank) via `np.linalg.lstsq`; `fisher_z(r: float, n: int, rho0: float = 0.0) -> float`; `a1_pair(res_i, res_j, rho0) -> dict(n_joint, r, z, a1_flag, a1_reason)`; `a3_pair(rows, spec, firm_i, firm_j, ssr_u, rank_u) -> dict(F, F_p, a3_flag, a3_df_num, cap_not_identified)`; `detect_run(run_dir, specs=("tag","project_fe"), usable_Ts=(30,50,70,100), burn_in=10, N=6) -> pd.DataFrame` with `PAIR_STATS_COLUMNS` and `rho0 = 0.0` for tag, `-1/(N-1)` for project_fe; `detect_frames(bids_df, run_id, …)` same from a DataFrame (tests); `empirical_thresholds(control_pair_stats: pd.DataFrame, percentile=95) -> dict[(spec, test), float]` using `np.percentile(abs(z) or F, 95)` on rows with computed statistics; `write_pair_stats(run_dir, df)` to `detector/pair_stats.csv`; CLI `python -m procurement.detector --run-dir <dir>`.

- [ ] **Step 1: Failing unit tests** in `tests/test_detector.py` (spec §6.6 rows): `test_burn_in_excluded`, `test_design_matrix_tag_spec` (600×33, rank 33, names), `test_design_matrix_project_fe_spec` (600×129), `test_ols_recovers_known_coefficients`, `test_fisher_z_formula` (4.4963, −1.2762, 0.0, 1.6594 within 1e-4; n=3 → `a1_reason == "insufficient_n"`), `test_a1_decision_rule`, `test_a3_f_formula` (F = 14.175 within 1e-3 for SSR 10/11, rows 600, m 33; p via `scipy.stats.f.sf`), `test_a3_constrained_design` (29 columns; shared column values), `test_never_wins_firm`, `test_missing_bids_in_detector` (12 bids removed → n_joint 88), `test_detector_firm_never_bids` (Review Focus 4), `test_empirical_null_threshold` (1..20 → 19.05; six synthetic control frames → 90 values per spec/test), `test_pair_stats_schema` (120 rows), `test_power_vs_T_output`, `test_detector_reads_only_bids` (monkeypatch `builtins.open` to record paths; delete truth/messages/transcripts; identical output).
- [ ] **Step 2: Failing known-answer tests** in `tests/test_detector_known_answer.py`, all marked `slow`, using `simulate_table` from Task 3 with explicit params and seats (0,1): `test_a1_size_independent_bids` (100 seeds: rate ≤ 0.08, |mean r| < 0.03), `test_a1_project_fe_bias_and_correction` (≥ 0.35 uncorrected, ≤ 0.08 corrected), `test_a1_detects_negative_correlation` (shock 0.05: ≥ 0.90, every flagged r < 0, mean r ≤ −0.30, others ≤ 0.09), `test_a3_size_identical_slopes` (≤ 0.08), `test_a3_detects_differing_slopes` (×3 ≥ 0.95; ×0.5 ≥ 0.55; others ≤ 0.09; implement by adding `est*(mult*β₁*ldist + mult*β₂*cap)*1.05` to firm 0's bids with mult 2.0 and −0.5 before detection), `test_scheme_signatures` parametrized over the nine rows of the §6.6 table with exactly those bracketed bounds (helper `summarize(outs, cartel)` computing per-class rates, mean r, r-sign among flagged, top-2 by A3 score with ties by summed F, rank-1 by |r|), `test_power_vs_T_cover` (usable 30/50/70/100: mixed A3 non-decreasing within 0.03 and ≥ 0.25 at 100; top-2 ≥ 0.30 at 100; cartel A1 ≥ 0.70 at 30 and ≥ 0.92 at 100). Then add to `tests/test_runner_schemes.py`: `test_end_to_end_synthetic_each_scheme_detector` (cover seed 1 run dir → `detect_run` → cartel pair row at tag/100 has r < 0 and a1_flag 1; fidelity assertions arrive in Task 9).
- [ ] **Step 3: Run** → fails. **Step 4: Implement** per interfaces. A3 numerator df = `rank_u − rank_c`; `cap_not_identified` lists firms whose `cap_<name>` column is all zero. **Step 5: Run** `python -m pytest tests/test_detector.py tests/test_detector_known_answer.py -q` → pass; if a bound is missed, follow the Global Constraints reconciliation rule. **Step 6: Commit** `feat(procurement): Bajari-Ye detector with known-answer tests`.

### Task 9: Fidelity, realized rule, profit, pair classes, verdicts, evaluation layer

**Files:** Create `src/procurement/fidelity.py`, `src/procurement/evaluation.py`, `tests/test_fidelity.py`, `tests/test_evaluation.py`.

**Interfaces:**
- `fidelity.py`: `cover_fidelity(bids: pd.DataFrame, truth: pd.DataFrame, seats_names: tuple[str,str]) -> float`; `rotation_fidelity(...)`; `markup_fidelity(..., delta=0.05)`; `project_executed_flags(...) -> pd.Series`; `realized_rule(bids, truth, seats_names, ratio=1.2, min_projects=10) -> dict(label, b_own, b_partner, n_cover)`; `cover_margins(bids, truth, seats_names) -> pd.DataFrame(project_id, margin_raw, margin_excess)`; `cartel_profit(bids, truth, seats_names) -> float`; `profit_delta(scheme_dir, control_dir) -> float` (same seats by seat index via `run_meta.seat_to_firm`).
- `evaluation.py`: `pair_class(firm_i, firm_j, cartel_names) -> str`; `a3_top2(pair_stats_run_spec_T: pd.DataFrame, cartel_names, N=6) -> int`; `a1_rank1(…) -> int`; `evaluate(results_root: Path, out_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]` producing `pair_eval.csv` (pair_stats + pair_class, condition, scheme, seed, cartel_seats) and `run_eval.csv` with the §4g columns, choosing the latest `finished_utc` directory per run_id; reads only `pair_stats.csv`, `truth.csv`, `bids.csv` (for fidelity and profit), `run_meta.json`; CLI.

- [ ] **Step 1: Failing tests** (spec §6.7 + Review Focus 5): `test_cover_fidelity` (1.0 / 0.0 / 0.5 / with one missing cartel bid → (executed−1)/110), `test_rotation_fidelity`, `test_markup_fidelity` (hand margins → executed / not; all/none), `test_realized_rule_classifier` (scripted inflate run via simulate_table → "inflate"; shadow → "shadow"; 8 cover projects → "insufficient"; equal standardized coefficients → "mixed"), `test_cover_margins_two_definitions` (hand case: cover bid 1.20E, cost 1.00E, outsider margins [0.04,0.05,0.06,0.07] → raw 0.20, excess 0.145), `test_cartel_profit_vs_matched_control` (+200k hand case), `test_pair_classes_and_verdicts` (cartel {2,5} → 1/8/6; control → 15 outsider; fixture with cartel members having 3 and 2 rejections → top2 1; rank1 by |r|), `test_pair_class_join_uses_seat_map` (names shuffled so seat order ≠ lexical order; pair_class derived from `seat_to_firm`, not from name order), `test_detection_conditional_on_fidelity`, `test_evaluation_reads_allowed_files_only`, and in `tests/test_runner_schemes.py`: `test_end_to_end_synthetic_each_scheme_fidelity` (cover and rotation seed 1 → exactly 1.0; markup seed 1 → ≥ 0.75).
- [ ] **Step 2–5**: fail → implement → pass → commit `feat(procurement): fidelity, realized rule, evaluation layer`.

### Task 10: Prompts, verbatim texts, rendering

**Files:** Create `src/procurement/texts.py`, `src/procurement/render.py`, `src/procurement/prompts/bid.jinja2`, `src/procurement/prompts/message.jinja2`, `tests/test_render.py`.

**Interfaces:** `texts.py`: `COMMON`, `COVER`, `ROTATION` (with `{A}`, `{B}` placeholders only; the per-project role line is separate: `ROTATION_ROLE_LINE = "This is project {t}, so you are the {role}."`), `MARKUP`, `COVER_BIDDER_SENTENCE`, `SERIOUS_BIDDER_SENTENCE`, `MESSAGE_PREFIX_FMT`, copied verbatim from spec §4e; `render.py`: `build_system_prefix(cfg, env, seat, scheme: str | None, partner_name: str | None) -> str` (introduction, rules, how costs work with the numeric β from `cfg.cost`, cartel block if scheme, output format); `build_bid_user_message(ctx, posting_pre: str) -> str` (memory ≤ 10, project t of T, posting, private info, last 20 history, scratchpad, `# Messages this project` and `# Your role this project` for cartel seats, output reminder); `build_message_user_message(ctx, posting_pre) -> str`; `render_outsider_prompt_for_test(...)` helper returning `(system, user)`.

- [ ] **Step 1: Failing tests** (spec §6.8 prompt rows): `test_outsider_prompt_contents` (contains own cost "$", own ε formatted with sign, EST, tag label, season total, K, six names, "0.9", "0.08", "0.17" read from the passed `CostParams` and asserted equal to the rendered strings, "project 40 of 110", exactly 20 history blocks, the JSON schema with `"bid"`; contains none of the four role sentences nor "partner", "cartel", "collud"; contains no rival cost: assert none of the other seats' cost strings appear), `test_outsider_template_identical` (same seat/project/history under the four conditions → identical system and user strings), `test_cartel_prompt_contents` (cover: COMMON, COVER, partner name present, `# Messages this project`; rotation seat a project 7: system has no "project 7", user has "This is project 7, so you are the serious bidder."; markup: MARKUP sentence; no "%" followed by "above your partner" numeric margin anywhere), `test_history_and_memory_windows`, `test_system_prefix_stable_across_projects` (cover member system string identical for t=5 and t=6).
- [ ] **Step 2–5**: fail → implement → pass → commit `feat(procurement): prompts and verbatim cartel texts`.

### Task 11: Client with transport policy, bid and message parsing

**Files:** Create `src/procurement/client.py`, `tests/test_client.py`.

**Interfaces:** `@dataclass class RawResponse: content: list[dict]  # [{"type": "thinking"|"text", "text": str}]; stop_reason: str | None; usage: dict; headers: dict`; `parse_bid(resp: RawResponse | None) -> tuple[float | None, str, dict | None]` returning `(bid, reason, parsed_json)` with reasons in spec §4d order (`transport_failed` when resp is None, `truncated`, `no_text`, `bad_json`, `no_bid_key`, `bad_value`, `""` on success); accepts numbers and numeric strings after stripping `$`, `,`, whitespace; rounds to cents; `parse_message(resp) -> tuple[dict, str]` (`my_cost`, `intended_serious_bid` numeric-or-None, `message_to_partner` str-or-""); `class HaikuClient(model, max_tokens, api_key_slot=1, transport: Callable | None = None, clock=time.monotonic, sleep=time.sleep)` whose `.create(system: str, user: str) -> tuple[RawResponse | None, list[dict]]` implements the §4d transport policy (retry-after, backoff 2→60 s + jitter, 600 s budget, `max_retries=0` on the SDK client, one `CallRecord` dict per attempt with the §4g `calls.csv` fields and rate-limit headers from `with_raw_response`); the SDK call is injected via `transport` so tests never construct a real client; `request_kwargs(system, user) -> dict` exposing exactly `{"model", "system": [{"type":"text","text":…,"cache_control":{"type":"ephemeral"}}], "messages", "max_tokens"}`.

- [ ] **Step 1: Failing tests**: `test_bid_parsing` (three forms → 945000.50), `test_bid_parsing_numeric_forms` (`9.45e5` → 945000.0; `945000.555` → 945000.56; `"$ 945,000"` → 945000.0), `test_malformed_response_is_missing` (eight cases → reasons as listed; parsed_json not None only when JSON parsed), `test_parse_message_partial` (missing numbers → None, text kept), `test_model_settings` (`request_kwargs` keys exactly; no temperature/top_p/top_k/effort/thinking; system block has cache_control), `test_transport_policy_retry_after` (fake transport raising a 429-like exception with `retry_after=1` then succeeding; fake clock/sleep; one retry, sleep ≥ 1 s), `test_transport_policy_gives_up_after_600s` (always 5xx; fake clock advancing; returns None with a `transport_failed` record; ≥ 3 attempts recorded), `test_call_record_fields` (every `CALLS_COLUMNS` key present).
- [ ] **Step 2–5**: fail → implement (exceptions mapped from `anthropic.RateLimitError`, `APIStatusError` ≥ 500, `APIConnectionError`, `APITimeoutError`; others re-raised) → pass → commit `feat(procurement): Haiku client with transport policy and parsers`.

### Task 12: LLM bidder and LLM channel integration with a stub client

**Files:** Create `src/procurement/llm_bidder.py`, `tests/test_llm_bidder.py`; modify `src/procurement/runner.py` (bidder factory for `bidder == "llm"`, outsiders-parallel-with-message-1 scheduling, transcripts, calls.csv rows).

**Interfaces:** `class LLMBidder(cfg, env, seat, client: HaikuClient, run_dir: Path | None)` implementing `Bidder`; keeps `MemoryManager` (from `src.continuous_double_auction.market.agents`) and renders only the last `memory_window` entries; updates memory/scratchpad only when `parsed_json is not None`; writes transcript and call records; `message(ctx)` uses the message prompt. `llm_bidder_factory(cfg, env) -> Bidder` per seat (one client per process, key slot from cfg). A `StubClient` in `tests/stubs.py` returning canned `RawResponse`s per (seat, phase, project) and recording call start order with a fake clock.

- [ ] **Step 1: Failing tests**: `test_memory_kept_only_if_json`, `test_memory_window_rendered` (15 memories → 10 in the prompt), `test_bidder_swap` (`bidder="synthetic"` → stub transport never called; `bidder="llm"` with StubClient → called), `test_one_message_each_no_reprompt` (malformed message → empty text delivered, one message call for that seat), `test_outsiders_parallel_with_message` (StubClient records start order: four outsider bid calls start before message 2 is issued), `test_llm_cover_run_files_with_stub` (cover seed 1, T=12 via cfg override, stub returning well-formed bids/messages → all files, calls.csv rows = bids + messages, transcripts per firm), `test_transport_failed_bid_is_missing_in_table` (stub returning None for one bid → empty field in bids.csv, `transport_failed` in calls.csv).
- [ ] **Step 2–5**: fail → implement → pass → commit `feat(procurement): LLM bidder and channel integration`.

### Task 13: Analysis script and figure

**Files:** Create `src/procurement/analysis.py`, `tests/test_analysis.py`.

**Interfaces:** `flag_rates(pair_eval: pd.DataFrame, thresholds: dict) -> pd.DataFrame` (condition × spec × threshold ∈ {closed, empirical} × pair_class × test ∈ {A1, A3, both}; n_pairs, n_flagged, rate, ci_low, ci_high with a run-clustered Wilson-style interval for mixed A3: cluster = run_id, use the mean of per-run rates and its normal-approx SE); `empirical_null_table(control_pair_eval) -> pd.DataFrame`; `power_vs_T_table(pair_eval, run_eval)`; `robustness_table(pair_eval, scripted_rows: pd.DataFrame)`; `control_distribution_figure(control_pair_eval, scheme_pair_eval, out_png) -> dict(z_values, F_values, marked)`; `analyze(results_root, out_dir) -> Path` writing all §4g analysis files and `results/README.md` numbers section; reads only `pair_eval.csv`, `run_eval.csv`, `pair_stats.csv`, `truth.csv`, `run_meta.json`; CLI.

- [ ] **Step 1: Failing tests**: `test_flag_rate_table` (fixture: 2 control + 1 cover synthetic runs through detector+evaluation → every expected row key present; empirical thresholds computed from control rows only; mixed A3 CI columns present and ci_low ≤ rate ≤ ci_high), `test_control_distribution_figure` (PNG exists, returned vectors have the right lengths), `test_power_vs_T_and_robustness_tables`, `test_analysis_reads_allowed_files_only`.
- [ ] **Step 2–5**: fail → implement → pass → commit `feat(procurement): analysis tables and figure`.

### Task 14: Launcher

**Files:** Create `src/procurement/launcher.py`, `tests/test_launcher.py`.

**Interfaces:** `build_manifest(plan: list[RunConfig], keys: int) -> list[dict]` (scheme runs first in order cover, markup, rotation, then control; `launch_index`, `stagger_s = 5*i`, `api_key_slot = 2 if keys == 2 and i % 2 == 1 else 1`); `spawn(manifest, python_exe, dry_run: bool) -> list[Popen | None]` (one `python -m procurement.runner --config <json>` per run; dry_run writes the manifest only); `monitor(run_dirs, clock, stall_s=600, poll_s=60, max_restarts=1) -> dict` reading `heartbeat.json` `last_activity_utc`, restarting once from checkpoint, marking a second stall stopped; `spend_guard(run_dirs, pricing, planned_calls, stop_usd=75.0) -> tuple[float, float, bool]` (actual, projected, stop?) from all `calls.csv`; `apply_sizing(limits: dict, has_key2: bool) -> dict`; `time_ladder(now_utc, deadline_utc, launched: bool, plan) -> plan` implementing spec §4f rungs; CLI `python -m procurement.launcher --plan config/run_plan.json [--dry-run]`.

- [ ] **Step 1: Failing tests**: `test_launcher_manifest_and_order`, `test_sizing_rule_applied`, `test_spend_guard` ($80 → stop; $30 → continue; projection = actual × planned/completed), `test_stall_restart_once` (fake clock, heartbeat file ages), `test_time_ladder` (deadline−5h → rotation removed; −4h → T 80 and markup R 2; −3h → control/cover R 6; −2.5h → `stop=True`).
- [ ] **Step 2–5**: fail → implement → pass → commit `feat(procurement): launcher with monitor, spend guard, time ladder`.

### Task 15: Hand-run scripts and the pilot's β₀ loop

**Files:** Create `scripts/smoke_llm.py`, `scripts/cover_smoke.py`, `scripts/pilot.py`, `scripts/tier_check.py`, `src/procurement/pilot.py`, `tests/test_pilot.py`.

**Interfaces:** `pilot.py`: `next_beta0(beta0: float, m: float, lo=1.00, hi=1.10, target=1.05) -> tuple[float, bool]` (frozen if m in [lo, hi] else `beta0 - (m - target)`, False); `pilot_summary(bids_df, truth_df, burn_in=10) -> dict(mean_bid_over_est, sd_bid_over_est, winning_mean, winning_sd, parse_rate)`; `write_frozen(path, cost: CostParams, pilot_history: list[dict])`. Scripts: `smoke_llm.py` (T=3 control, Haiku, prints parse rate, latency, tokens, cost; exits non-zero if < 18/18), `cover_smoke.py` (T=10 cover, writes `results/calibration.json` with realized margins raw/excess per project, median, realized rule, fidelity, latency, mean output tokens; then runs `simulate_table` signature check at 0.10 and at the realized median excess margin and appends the results), `pilot.py` (loop per spec A47 up to 3 pilots; writes `config/frozen.json`), `tier_check.py` (one minimal call per key via `with_raw_response`, records the three `anthropic-ratelimit-*-limit` headers into `results/calibration.json`). All four read the key from the environment at start and never print it.

- [ ] **Step 1: Failing tests**: `test_next_beta0` (m 1.05 → frozen; m 1.20 → 0.75; m 0.95 → 1.00), `test_pilot_summary` (synthetic frames → fields; parse_rate = non-missing/660), `test_write_frozen_roundtrip`, `test_scripts_import_without_api` (importing each script module constructs no client).
- [ ] **Step 2–5**: fail → implement → pass → commit `feat(procurement): smoke, cover-smoke, pilot and tier-check scripts`.

### Task 16: Full suite, docs, final verification

**Files:** Create `src/procurement/README.md` (how to run tests, scripts, launcher, analysis); modify `docs/superpowers/DECISIONS.md` with any bound reconciliation.

- [ ] **Step 1**: `python -m pytest tests -q` → all passed, 0 skipped; record runtime.
- [ ] **Step 2**: `python -m pytest tests -q -p no:cacheprovider` a second time → identical pass count (determinism).
- [ ] **Step 3**: `grep -rn "anthropic.Anthropic(" src/procurement tests` → only in `client.py` behind the injected transport default and in `scripts/`.
- [ ] **Step 4**: Commit `docs(procurement): README and final suite verification`.

---

## Self-review notes

- Spec coverage: §4a Task 4; §4b Task 2; §4c Task 10; §4d Tasks 3, 10, 11, 12; §4e Tasks 3, 6, 7, 9, 10; §4f Tasks 1, 14; §4g Tasks 5, 6, 8, 9, 13; §4h Task 8; §6.1 T2; §6.2–6.3 T3; §6.4 T4; §6.5 T5–T7; §6.6 T8; §6.7 T9; §6.8 T10–T12; §6.9 T6–T7, T12; §6.10 T6–T7; §6.11 T13–T14; §6.12 T1, T16; scripts T15.
- Type consistency: `BidContext/BidResult/MessageResult/Bidder` defined in Task 3 (`types.py`), consumed in 6, 7, 12; `RawResponse` defined in 11, consumed in 12; `PAIR_STATS_COLUMNS` in 5, consumed in 8–9; `simulate_table` in 3, consumed in 7–9, 15.
- Proportion: the plan carries signatures, test names and assertions; no function bodies except one-line formulas pinned by the spec.
