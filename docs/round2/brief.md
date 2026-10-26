# Round 2 brief — memory × information ablation (instructions to the build agent)

From the project lead, 2026-10-25. Save this file verbatim as `docs/superpowers/round2-brief.md` before doing anything else; it is the source of record for round 2 and wins over design-v2, the spec and the step-4 audit wherever they conflict. Everything here is a decision. Operate under step-4 audit Part 4 (autonomous mode, DECISIONS.md, stop conditions) with the changes in Part 6 below.

## 0. What round 1 found, in four lines (context only; do not re-analyze)

1. Haiku 5.5 refused the instructed scheme in 20/20 scheme runs within 6 projects (14/20 on project 1); the cartel pairs then bid independently. There is no colluding-LLM data.
2. On the 12 honest control runs, B&Y's closed-form thresholds reject 37% of innocent pairs on both A1 and A3. A1's excess is sentinel no-bids (bids > 1.5 × EST: $2,000,000, $9,999,999 placeholders); treating them as missing gives 7%. A3's excess survives that filter (32%).
3. Among control pairs where both firms use the dominant rule (shade toward the best rival's inferred cost when leading, cost-plus otherwise), A3 rejects 14%; identical rational bidders using that kinked rule on the same cost tables give 15% (smooth cost-plus bidders: 4%). Pairs involving a firm that drifted into an idiosyncratic regime (selective passer, fat markup) reject 50–73%.
4. Round 2 tests whether the regime drift is caused by the agents' private state (memory, scratchpad), by the public bid history, or by the transparent cost structure, with a 2 × 2 of seed-matched control runs.

## 1. The experiment

Four new arms of 12 control runs each, seeds 1–12, T = 110, burn-in 10, frozen cost parameters from `config/frozen.json` unchanged, model and request settings unchanged, `purpose = "experiment"`. The existing 12 control runs are the fifth cell (`arm = "base"`) and are not rerun.

| arm | memory_window | scratchpad_enabled | history_window | bidder_info | what it isolates |
| --- | --- | --- | --- | --- | --- |
| base (existing) | 10 | true | 20 | shown | round-1 controls |
| mem0 | 0 | false | 20 | shown | private state removed, public history kept |
| stateless | 0 | false | 0 | shown | all state removed: B&Y's static game with LLM players |
| hidden | 10 | true | 20 | hidden | cost transparency removed, state kept |
| stateless_hidden | 0 | false | 0 | hidden | both removed: the maximally B&Y-like LLM market |

Nothing else differs between arms. In particular: same seeds (so the same EST, sites, yards, η, tags and ε per seed as round 1), same firm names per seed, same posting of DIST and CAP, same JSON bid schema except the fields named in §2.1, same `max_tokens`, no temperature or effort key, same concurrency 6.

**Launch in two waves.** Wave 1 = mem0 + stateless (24 runs) as soon as §2.1–2.3 are green and the arm smokes in §2.6 pass. Wave 2 = hidden + stateless_hidden (24 runs) as soon as §2.4 is green and its smokes pass. Do not hold wave 1 for wave 2, and do not hold wave 2 for wave 1 to finish. Stagger 5 s as before; one key; no time-ladder rungs (see §6).

## 2. Code changes, test-first

The spec's §6 rule applies: every test below is written before the code it checks, the full suite stays green with zero API calls, and nothing in the suite touches `results/` or `config/`. Land the parked conftest guard (writes under `REPO_ROOT/results`, `config/`, `DECISIONS.md` refused from tests) first; real results exist now.

### 2.1 Agent state flags

- `RunConfig` gains `scratchpad_enabled: bool = True` (also in `config/defaults.json`, `DEFAULTS_RUN_FIELDS`, `run_meta.json` under `windows`).
- `scratchpad_enabled = false`: `LLMBidder` never stores `scratch_pad_update`; `BidContext.scratchpad` is `""`; the rendered bid and message prompts contain no `# Strategy scratchpad` section; the bid schema is `{"reflection": "<string>", "plan": "<string>", "bid": <number, dollars>}` (no `scratch_pad_update`, no `new_memory`) and the `# Output format` text references that schema. `memory_window = 0` already returns no memories (`llm_bidder.py:74`); additionally the `# Your memory` section is omitted from the prompt rather than rendering "No memories yet.", and `new_memory` is dropped from the schema whenever `memory_window = 0`.
- `history_window = 0`: the `# Last N projects` section is omitted rather than rendering "No projects have been completed yet."; `_variables` must not slice with `-0`.
- Tests: `state_flags_prompt_sections` (for each arm, the rendered system prefix and user message contain exactly the expected `#` sections, asserted as an ordered list); `scratchpad_disabled_never_updates` (a reply carrying `scratch_pad_update` and `new_memory` leaves bidder state untouched and the next prompt identical); `base_prompt_unchanged` (for `arm = base` the system prefix and user message are byte-identical to a fixture captured from the round-1 code path for seat 2, project 15, with a fixed history, memory and scratchpad — store the fixture under `tests/fixtures/`).

### 2.2 Arm identity and results isolation

- `RunConfig` gains `arm: str = "base"`. For `arm != "base"`: `run_id = f"{condition}_{arm}_s{seed}"`, run directory `<UTC>_{condition}_{arm}_s{seed}`, `arm` written to `run_meta.json`, and an `arm` column appended to `truth.csv` (not to `bids.csv`: the detector's input contract is unchanged). Base keeps `control_s<seed>` so the existing directories are read as before; a `run_meta.json` without `arm` reads as `"base"`.
- Evaluation and analysis group everything by `(condition, arm)`: the empirical null is computed per arm from that arm's complete control runs only; "latest `finished_utc` per run_id" dedup is per `(run_id)` with the arm inside the run_id, so a base and a mem0 run of the same seed never collide.
- Tests: `run_id_carries_arm`; `arm_defaults_to_base_when_absent` (a round-1 `run_meta.json` fixture); `evaluation_groups_by_arm` (two synthetic control runs, seed 1, arms base and mem0, both appear in `run_eval.csv` and the empirical null for each arm uses only its own 15 pairs).

### 2.3 Spend guard and launcher plan

- `config/defaults.json` `spend_stop_usd` → 150 (round-1 baseline is $33.70; four arms are ≈ $40 at the measured $1.32 per 1,000 calls, less for the stateless arms). Log in DECISIONS.md.
- `config/run_plan.json` gains `arms`: a list of `{arm, R: {control: 12}, memory_window, scratchpad_enabled, history_window, bidder_info}`; the launcher takes `--arms mem0,stateless` to launch a subset. `deadline_utc` stays `2026-10-26T11:59:00Z`; the time-ladder rungs are disabled for arm launches (`--no-ladder`) because they drop scheme conditions that do not exist here. Pre-flight still requires `config/frozen.json` and the key.
- Tests: `launcher_arms_subset` (dry run with `--arms mem0,stateless` plans exactly 24 runs with the right per-run config and run_ids); `spend_cap_from_config`.

### 2.4 Information flag (wave 2 only)

- `RunConfig` gains `bidder_info: str = "shown"` (`"shown" | "hidden"`), in `run_meta.json`.
- `"hidden"` changes the bidder prompt only; `bids.csv`, `truth.csv` and the detector are untouched (`firm_rate` is still logged).
  - `# How costs work` becomes, verbatim: "Your cost on a project is a share of the engineer's estimate (EST). It rises with the distance in miles from your yard to the site, and with CAP, the EST dollars you have won so far divided by K (K is given in each posting), so winning more work raises your later costs. Each firm has its own permanent firm rate. The project type shifts cost for every firm. A private factor, a random draw for each firm and project that only you know, moves your cost up or down." No β values, no tag-effect numbers, and no sentence saying every firm's cost is built the same way.
  - The pre-bid posting keeps project id, EST, tag label, season total EST, K, and every firm's DIST and CAP; the firm-rate column is removed. The firm's own firm rate is added to `# Your private information` as "Your firm rate: +0.0027 (a share of EST; permanent)". Own cost and own ε are shown as before.
  - Everything else (market rules, history, output format) identical to `"shown"`.
- Tests: `hidden_prompt_has_no_cost_numbers` (system prefix and user message contain none of the formatted β₀/β₁/β₂/ν strings that the shown prompt contains, no "built the same way", no rival firm rate; own rate present; posting header has no rate column); `hidden_bids_csv_unchanged` (same `bids.csv` columns and `firm_rate` values as a shown run of the same seed on the synthetic bidder); `shown_prompt_unchanged` (reuse the §2.1 fixture).

### 2.5 Detector and analysis additions

- Detector: a third spec `tag_filtered`, identical to `tag` except that rows with `bid / est > 1.5` are treated as missing before `_select` (they count as not bidding for n_joint, winner determination in `bids.csv` is not altered). `project_fe` unchanged. `pair_stats.csv` gains the rows; `rho0 = 0` for the new spec. Test: `filtered_spec_drops_sentinels` (one row at 2 × EST → that pair's n_joint is 99 under `tag_filtered`, 100 under `tag`; all other statistics of a sentinel-free table identical across the two specs).
- Analysis, per arm and for base, all written under a new `results/analysis/<UTC>/` and summarized in `results/README_round2.md` (leave `results/README.md` as it is except for §2.7):
  1. `flag_rates_by_arm.csv`: A1, A3, both; specs tag, tag_filtered, project_fe; closed and empirical (empirical threshold per arm); run-clustered intervals.
  2. `empirical_null_by_arm.csv`: per arm and spec, the sorted |z| and F vectors and their 95th percentiles.
  3. `fp_vs_T_by_arm.csv`: A1 and A3 false-positive rate at usable_T 30/50/70/100, tag and tag_filtered.
  4. `regimes_by_arm.csv`: one row per firm-season with, over post-burn-in projects: `pass_rate` (share of projects with bid > 1.5 × EST or missing), `margin_med` and `margin_iqr` of (bid − cost)/EST over non-sentinel bids, `margin_when_leading` and `margin_when_trailing` (leading = own true cost is the lowest of the six, from `truth.csv`), `shade_slope` (OLS slope of margin on gap over non-sentinel bids with gap > 0, gap = (lowest rival true cost − own cost)/EST, NaN if fewer than 8 rows), `wins`, `profit` (Σ (bid − cost) over won projects, all 110), and `regime` assigned in this order: `passer` if pass_rate ≥ 0.15; `fat` if margin_med ≥ 0.05; `shade` if shade_slope ≥ 0.25; `razor` if margin_med ≤ 0.012; else `moderate`. Plus `regime_counts_by_arm.csv`.
  5. `a3_by_pair_type_by_arm.csv`: A3 rejection rate (tag_filtered, closed) by the unordered pair of regimes.
  6. `slope_dispersion_by_arm.csv`: per firm-season, OLS of margin on [1, ln(1 + DIST), CAP] over non-sentinel bids, separately for projects 11–60 and 61–110; then per run the SD across the six firms of the CAP slope and of the DIST slope; report the mean over runs per arm and half.
  7. `synthetic_floors.csv`: on each of the 12 base control runs' `truth.csv` (true costs and ε), construct bids and run the detector (tag spec, usable 100), seeds fixed with `default_rng(7)`: (i) smooth: bid = 1.01 × cost + EST × N(0, 0.003); (ii) kinked identical: bid = max(1.01 × cost, min over rivals r of (cost_r − ε_r × EST) − 0.5 × 0.05 × EST) + EST × N(0, 0.003); (iii) kinked heterogeneous: as (ii) with per-firm margin m ~ U(0.003, 0.02) replacing 0.01 and hedge δ ~ U(0.2, 1.0) replacing 0.5. Winner recomputed as the lowest bid. Report A1 and A3 false-positive rates and median F. Round-1 reference values: 0.039 / 0.150 / 0.156 for A3; assert within ± 0.05 in a test on the synthetic bidder (`synthetic_floors_reproduce`).
  8. `paired_by_seed.csv`: for each arm vs base and each of A1/A3 (tag_filtered, closed), the per-seed difference in the run-level rejection rate over the 15 pairs, with the sign count and a Wilcoxon p over the 12 seeds.
  9. Figure `ablation_2x2.png`, three panels: (a) A3 false-positive rate (tag_filtered, closed) per cell, the five cells laid out as memory {on, off} × information {shown, hidden} with mem0 drawn between base and stateless as the intermediate rung, run-clustered 95% intervals, horizontal reference lines at 0.05, at the smooth floor and at the kinked floor from item 7; (b) the same for A1, tag (raw) and tag_filtered side by side; (c) stacked regime counts per cell.
- Tests for items 4, 6, 7, 8 on synthetic inputs with constructed answers (a firm-season built to be a passer is labeled passer, etc.); item 7 as above.

### 2.6 Per-arm smoke before each wave

For each new arm, one 3-project control run on the real model under `results/calibration_runs/` (`purpose = "smoke"`, 18 calls, ≈ $0.02): parse rate must be 18/18 after at most one parsing fix, and the logged prompt of seat 0, project 2 must pass the §2.1/§2.4 section assertions on the real rendered text. Record in `results/calibration.json` under `round2_smokes`.

### 2.7 Round-1 README corrections (after both waves are launched, before the final analysis)

Regenerate `results/README.md` from the same round-1 data with these label fixes, numbers unchanged: (i) next to the cover `fidelity` column add `fidelity_honest_baseline` = the same metric computed on the same two seats in the matched control run (round-1 values 0.75–0.97), and a note that the metric does not distinguish cooperation from independent bidding; (ii) rename `realized rule` to `cover-bid regressor (own cost vs partner bid)` and note that `b_own ≫ b_partner` means the cover bid tracked own cost; (iii) delete the "Detection conditional on fidelity > 0.80" section; (iv) add a `refusal` section: per scheme run, the first project on which either member's message matches the refusal pattern (case-insensitive: illegal, bid rigging, bid-rigging, price fixing, price-fixing, antitrust, collusi, declin, compliance, counsel, withdraw, unlawful, refus, won't take part, will not take part, not going to coordinate) and the share of projects with a refusal message; (v) add the seed-matched cartel-seat margin table (median non-sentinel margin of the two cartel seats in the scheme run vs the same seats in the control run of the same seed, with the outsiders' median in the scheme run, win counts and realized profit; sign counts and Wilcoxon p over runs).

## 3. Transcripts and checkpoints

Round 2 run directories keep `transcripts/` and `checkpoint.json` as the runner already writes them. After each wave finishes, zip every round-2 run's `transcripts/` and `checkpoint.json` into `results/archive/round2_<wave>_transcripts.zip`, and do the same once for the round-1 runs that still have them on disk into `results/archive/round1_transcripts.zip`. Do not commit zips over 10 MB; record their paths and sizes in `results/README_round2.md`. Never delete a transcript.

## 4. Do not change (these would confound the comparison with round 1)

- The cost parameters, EST range, map, η range, ν levels, K, T, burn-in, seeds, firm names.
- The bid JSON schema beyond the two fields named in §2.1; in particular no `no_bid` field (noted for round 3).
- The forced "Cost: $X / intended bid $Y" message prefix (no channel in this round; noted for round 3).
- The model, `max_tokens`, absence of temperature/effort, concurrency.
- Any file under the round-1 run, calibration-run, launch or analysis directories.
- No re-pilot: a shift in mean bid/EST in an ablation arm is a result and is reported, not calibrated away.

## 5. Deliverables, in order

1. `docs/superpowers/round2-brief.md` (this file), DECISIONS.md lines for every decision, branch `round2-ablation` from the head of `bajari-ye-procurement`.
2. §2.1–2.3 green → wave-1 smokes → wave-1 launch (`mem0`, `stateless`).
3. §2.4 green → wave-2 smokes → wave-2 launch (`hidden`, `stateless_hidden`).
4. §2.5 analysis code and tests while the runs execute; §2.7 README corrections.
5. When all 48 runs are complete: run the analysis; write `results/README_round2.md` with every table in §2.5, the figure, the archive paths, the per-arm run inventory, the spend, and a ten-line summary at the top stating for each arm the A3 and A1 false-positive rates (tag_filtered, closed and empirical), the regime counts, and the seed-matched sign counts against base. Commit (runs' bids/truth/calls/run_meta, analysis, calibration, launch records; not transcripts). Stop with condition 1.

## 6. Operating mode and stop conditions

Step-4 audit Part 4 applies with these amendments: the time ladder is off; the spend stop is $150 projected or actual across all launches; stop also if any wave has not launched by 2026-10-26 03:00 UTC, or if an arm smoke parses below 18/18 after one parsing fix. Not reasons to stop: 429s (backoff handles them; 48 concurrent runs will brush the output-tokens limit), a single failed run (restart once from its checkpoint into a new directory as in round 1), a test threshold that needs recalibrating (recalibrate and log), and any question you can settle from this file, the spec, or the round-1 data. Guard rails unchanged: never overwrite or delete a results directory, no key on disk, no force pushes.
