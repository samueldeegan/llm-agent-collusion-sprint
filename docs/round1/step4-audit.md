# Audit resolutions for the Step 5 test plan — instructions to the spec agent

Received 2026-10-25 from the project lead. Verbatim copy of the file pasted into the spec session; the only edits are Markdown table formatting. This file wins over design-v2 where they conflict; the contradiction is recorded in the spec's Assumptions table rather than by editing design-v2.

Paste this whole file to the agent writing the spec. Everything here is a decision, not a question. Tag each item in the Assumptions table as [step-4 audit] unless a line says otherwise. Where this file contradicts design-v2, this file wins; record the contradiction in the Assumptions table rather than editing design-v2.

## Part 1 — Exchangeability (A3): what changes and why

Finding (100-seed overwrite simulations, tag-dummy spec, T = 70 usable). The cartel-pair A3 rate under cover bidding is ~0.30 at a 10%-of-EST cover margin and does not rise with T (0.35 / 0.32 / 0.28 at T = 70 / 150 / 300) and is unchanged when η is set to zero. It is therefore not power against a slope difference; it is the F-test reacting to the kinked, heteroskedastic bid function of a symmetric cartel. Under the efficient cover rule both members run the same rule, so permuting their covariates permutes their bid distributions: the two members are exchangeable with each other. This is exactly B&Y's statement (p. 975, A3 discussion: bidding fails exchangeability "as compared to a control group of noncartel firms"; §V.B p. 977 and Theorem 7: the criterion "will hold for firms that do not collude but will fail for firms that do collude" when compared against non-colluders). Design-v2 §Evaluation extended that to the cartel pair; that extension is withdrawn.

Mixed-pair A3 (member vs outsider) is true power: it rises with T (0.16 / 0.19 / 0.26 / 0.36 at T = 30 / 50 / 70 / 100 usable, β₁ = 0.08), with covariate signal, and with the cover margin, and falls with private noise.

### 1.1 Predictions and metrics (replace design-v2 §Conditions and §Evaluation A3 rows)

| Pair class | Predicted A3 (cover, rotation) | Role in the paper |
| --- | --- | --- |
| Mixed (8 per run) | fail, as a rate: ≥ 0.30 at the published 5% level with the parameters below (0.26 at 70 usable, 0.36 at 100); higher with Haiku's realized margin | The A3 headline. 8 pairs × 12 cover runs = 96 tests, though the effective sample is nearer R than 8R because the 8 pairs in a run share the same two members and the same LLM trajectory; report the rate with a run-clustered interval. |
| Cartel (1 per run) | no failure predicted for a symmetric cartel; observed rate reported (expect ~0.3 at the published level, a size distortion) | Reported as the symmetric-cartel blind spot, alongside uniform markup. The empirical-null threshold is the honest one here. |
| Outsider (6 per run) | pass | False-positive pool, unchanged. |

New per-run A3 verdict (B&Y §VI.E "candidate set" reading). For each firm, A3 score = number of its 5 pairs rejected at the published level, ties broken by the summed F. A run "identifies the cartel by A3" when the two cartel members are the top two. Replace the §Evaluation Rank row's "cartel pair first of 15 by F" with this; keep "cartel pair first of 15 by |r|" for A1. Predicted: 0.30 at a 10% margin, 0.5–0.7 at 15–20%.

Rotation: cartel pair is symmetric by construction, so cartel-pair A3 falls toward nominal as T grows (0.27 → 0.15 at T = 70 → 150) and mixed A3 is ~0.10. Rotation's signature is A1 (≥ 0.90, ρ < 0). State that; rotation stays first to drop.

### 1.2 Parameter changes (these are what make the A3 claim strong)

1. β₁ = 0.08 (was 0.04, B&Y Table 8). Reason: Theorem 6 — the tests' power comes from covariate variation. In a 100-mile square log(1+DIST) has SD ≈ 0.75, so at β₁ = 0.04 the distance signal has SD ≈ 0.03, below the private noise σ_ε = 0.05. Doubling it lifts mixed-pair A3 from 0.14 to 0.25 and leaves control false positives at 0.06. Same β for every firm, so A3's validity is untouched. Scaling the map does not do this (log compresses; ×4 distance gave no gain), which is why the slope is the lever. β₂, MAXP, MDIST unchanged.
2. σ_ε stays 0.05 and is not tuned upward. Pilot rule becomes: tune β₀ only until mean bid/EST is in 1.0–1.1 (comparator: B&Y Table 5 all-bids row, 1.08). Report the SD against 0.18; do not target it. Reason: raising σ_ε to 0.10–0.12 (what hitting SD 0.18 would take) drives mixed-pair A3 to 0.06–0.09, i.e. the null, and cuts cartel |ρ| from 0.55 to 0.36–0.39. B&Y's dispersion came from cost heterogeneity, much of it public; matching it with private noise destroys the experiment. Expected β₀ after the pilot: ≈ 0.6 (mean cost/EST ≈ β₀ + 0.39 at β₁ = 0.08). σ_ε remains configurable (the brief requires it) with default 0.05.
3. Cover-margin smoke run before freezing (hand-run, outside the suite, ~80 calls, ~5 min): one cover-condition run of 10 projects. Record Haiku's realized cover margin per project, (cover bid − own cost)/EST, and the realized rule (inflate vs shadow, classifier below). The scripted overwrite check and the power tables are then run at both 0.10 and the realized median margin. A realized margin below 0.10 is a finding, not a failure.
4. T = 110 (10 burn-in, 100 usable); R = 12 control, 12 cover (matched seeds 1–12), 4 markup, 4 rotation (seeds 1–4). Decided 2026-10-25, supersedes design-v2 §Runs and the 6/6/4/4 plan. Reason: T sets the per-run detection rate a regulator would get (mixed-pair A3 0.26 → 0.36 and the top-2 verdict 0.36 → 0.39–0.44 from 70 to 100–120 usable; gains flatten past 120), and a longer run contains the shorter one, since the detector already reports the first 30/50/70 usable projects. R sets precision: the empirical null goes from 90 to 180 values (the 95th percentile rests on ~9 values instead of ~4), and runs are the independent unit for LLM behaviour. Fallback tied to the tier read in Part 4.2 step 7: if the output-tokens-per-minute limit is below 1,000,000, control and cover run at R = 8 (seeds 1–8). K stays 25% of the season's total EST, now over 110 projects. Renumber every count in the test plan accordingly: project_id 1–110; 660 rows in bids.csv; 600 post-burn-in rows; design matrix 600 × 33 (tag spec) and 600 × 129 (project-FE spec: 1 + 99 + 5 + 24); usable_T = 100; F denominator T − m = 600 − 33 = 567; pooled empirical-null values 180 (or 120 at R = 8); power_vs_T rows at 30, 50, 70, 100 usable; honest_bidder_distribution over 100 seeds × 660 bids; run_plan 32 runs (or 24).

### 1.3 Scheme-signature assertions

(re-calibrate at β₁ = 0.08, σ_ε = 0.05, T = 100 usable, 100 seeds, cartel seats 1 and 2; set thresholds ≈ observed − 0.07 as before)

Reference values from the audit simulation at 70 usable (yours at 100 usable will be higher for mixed A3 and top-2; re-run yours):

| Config (cover-inflate unless stated) | cartel A1 | mean r | cartel A3 | mixed A3 | outsider A3 | members top-2 by A3 score |
| --- | --- | --- | --- | --- | --- | --- |
| margin 0.10 | 0.99 | −0.51 | 0.30 | 0.25 | 0.01 | 0.30 |
| margin 0.15 | 1.00 | −0.63 | 0.42 | 0.42 | 0.01 | 0.51 |
| margin 0.20 | 1.00 | −0.71 | 0.51 | 0.53 | 0.00 | 0.69 |
| margin 0.10, serious bidder +0.05·EST | 0.73 | −0.30 | 0.22 | 0.12 | 0.03 | 0.19 |
| margin 0.10, σ_ε = 0.10 | 0.98 | −0.39 | 0.24 | 0.09 | 0.04 | 0.18 |
| margin 0.10, T = 120 usable | 1.00 | −0.51 | 0.39 | 0.35 | 0.01 | 0.48 |
| rotation-inflate, margin 0.10 | 0.91 | −0.38 | 0.22 | 0.10 | 0.02 | 0.18 |
| control (size) | — | — | — | — | 0.06 | 0.03 |

Required rows in scheme_signatures: margin 0.10 (primary); margin 0.15 and 0.20 (margin sensitivity); the serious-bidder +0.05 row (documents that repricing by the serious member weakens both signatures — this is the predicted cost of the "four competitors, not five" sentence, which stays); the σ_ε = 0.10 row (documents why the pilot must not tune SD); cover-shadow (robustness, r > 0); rotation; markup. Assert mixed A3 ≥ 0.18 and top-2 ≥ 0.20 on the primary row; assert cartel A3 only as "above outsider A3", with the explanation above recorded in the test's docstring.

power_vs_T (synthetic, no API): report mixed A3 and top-2 at T = 30, 50, 70, 100 usable so the paper can state what T buys (reference, 200 seeds at 70+: mixed 0.16 / 0.19 / 0.26 / 0.36; top-2 at 70 and 100: 0.36 / 0.39).

a3_detects_differing_slopes: add a halved-slope case for firm 1 (the shadow-type signature) next to the tripled one; assert pairs with firm 1 flagged ≥ 0.50 (calibrate).

## Part 2 — Wall-clock, parallelization and rate limits

Measured: ~10 s per Haiku 5.5 call at default effort (smoke test: 10 agents, thread cap 3, 32 s per round). One call per bid; two extra serial calls per project in scheme runs. Assumptions until the smoke run measures them: 3.5k input tokens per call (5k worst), 1.5k output tokens including thinking (3k worst), 15 s per call worst.

### 2.1 What is serial and what is parallel

| Level | Serial or parallel | Rule |
| --- | --- | --- |
| Projects within a run | Serial. Project t+1's posting (CAP, history, memory) depends on project t's winner. | No cross-project pipelining. |
| Cartel chain within a scheme project | Serial, 3 calls. Message 1 → message 2 → cartel bids. This is the critical path: ~30 s per project, 55 min per 110-project run. | Nothing shortens it except simultaneous messaging (not taken) or lower effort (not taken). |
| Outsider bids within a scheme project | Parallel with the message exchange. Outsiders never see messages, so their four bid calls are issued at the same moment as message 1, not after message 2. | Does not shorten the critical path; it smooths load and makes the project's wall-clock exactly the cartel chain. |
| Six bids in a control project | Parallel. One wave of 6 concurrent calls, ~10 s. | Per-process concurrency cap = 6 (config value; the repo's cap of 3 turns a wave into two). |
| Runs | Parallel. One OS process per run; all runs launched together. | A launcher script spawns them, staggered 5 s apart (acceleration limits penalize sudden ramps), writes a manifest, and monitors heartbeats. |
| API keys | Parallel by process. If a second key is present (ANTHROPIC_API_KEY_2), even-numbered runs in the launch order use it. | No key switching inside a process. |

The 11-hour figure was runs executed one after another. Parallel, the whole run phase is bounded below by the 55-minute critical path of a single scheme run and above by the organization's output-tokens-per-minute cap.

### 2.2 Budget

Calls: control 12 × 110 × 6 = 7,920; cover 12 × 110 × 8 = 10,560; markup and rotation 4 × 110 × 8 = 3,520 each; smoke, cover smoke and up to three pilots ≈ 460. ≈ 26,000 calls (≈ 19,800 at the R = 8 fallback). Pricing per design-v2: $0.10 / $0.50 per million input / output tokens.

| | Expected (10 s, 3.5k in, 1.5k out) | Worst (15 s, 5k in, 3k out) |
| --- | --- | --- |
| Cost, R = 12/12/4/4 | ≈ $29 | ≈ $57 incl. truncation retries |
| Cost, R = 8/8/4/4 | ≈ $22 | ≈ $40 |
| Wall-clock, Build tier (1M out/min) | ≈ 65 min | ≈ 90 min |
| Wall-clock, Start tier (400k out/min), one key, R = 8/8 | ≈ 80 min | ≈ 2.5 h (throughput-bound: ~133 calls/min) |
| Wall-clock, Start tier, two keys, R = 12/12 | ≈ 65 min | ≈ 1.5 h |

Before launch: ~20 min of API time for the smoke, the cover smoke and the pilots. After: analysis is minutes. Spend stop in 4.3 is $75, above the worst case.

### 2.3 Rate limits (platform.claude.com/docs/en/api/rate-limits, read 2026-10-25)

Haiku 5.5, Start tier: 1,000 requests/min, 2,000,000 input tokens/min (uncached only), 400,000 output tokens/min. Build tier: 5,000 / 5,000,000 / 1,000,000. New organizations may sit in an Evaluation tier with lower, unpublished limits. Limits are per organization, so a second account is a second budget. With 32 runs in flight: ≈ 750 requests/min (fits), ≈ 2.6M input tokens/min at 3.5k per call (over Start, under Build; prompt caching of the static prefix brings it under), output tokens/min ≈ 750 × output per call (≈ 1.1M at 1.5k: just over Build, 2.8× Start). Output tokens per minute is the binding limit; max_tokens does not count, only tokens actually produced.

Sizing rule, applied by the launcher from the header read in 4.2 step 7 and the measured output per call: if output-tokens-limit ≥ 1,000,000 → R = 12/12/4/4, one key. If below and ANTHROPIC_API_KEY_2 is set → R = 12/12/4/4 split across the two keys. If below and no second key → R = 8/8/4/4. Record the choice and the limits in results/calibration.json and every run_meta. Launch order: scheme runs first (they are the critical path), then control runs; all within the first ~3 minutes.

### 2.4 Client behaviour (spec §4d and §7)

- Transport errors (429, 5xx, timeout, connection error): honor retry-after when present, else exponential backoff from 2 s doubling to a 60 s cap, with jitter; keep retrying for up to 10 minutes of wall time per call, then mark the bid missing with reason transport_failed. A bounded attempt count is not enough: under sustained throttling it would silently convert bids into missing data.
- Log on every call to calls.csv: run_id, project_id, firm_id, phase, attempt, latency_ms, input_tokens, output_tokens, stop_reason, reason, and the anthropic-ratelimit-requests-remaining, -input-tokens-remaining, -output-tokens-remaining headers.
- Prompt caching on the static prefix (role instructions, cost formula, JSON schema) if it meets the model's caching minimum; cache reads do not count toward the input-token limit.
- Heartbeat: each run writes results/<run>/heartbeat.json (last completed project, UTC time) after every project; per-project state checkpoint (bids, CAP, memory, scratchpad, history, messages) so a run can resume from its last completed project.
- Launcher: restarts a run with no heartbeat for 10 minutes, once, from its checkpoint; logs the restart; a second stall stops the run and is reported in results/README.md.

### 2.5 Measure, then write §4f from measurement

The smoke and cover smoke runs log per-call latency and usage. Replace design-v2's 15–20 s per round, 20–30 min per run, "under 2 hours", 300 output tokens and $4.50 with measured values and the tables above recomputed from them.

### 2.6 Levers held in reserve (not taken; list them in §4f)

Simultaneous instead of sequential messaging (−18 min per scheme run at T = 110); effort low (per-call latency unknown); T back to 80 (−15 min); dropping rotation (fewer calls, no wall-clock gain since runs are parallel). The time ladder in 4.2 says when each is pulled.

## Part 3 — Plan fixes (apply as written)

1. cartel_seat_rotation. Keep a = (r + k − 2) mod 6, b = (a + 3) mod 6. Fix the assertion: the three conditions within a seed get three distinct pairs; pairs repeat across seeds (only {0,3}, {1,4}, {2,5} exist); every seat is a cartel seat in at least one run over seeds 1–12; names shuffled by seed.
2. Detector vs evaluation. pair_stats.csv is keyed by firm_i, firm_j with no pair_class. A separate evaluation step joins run_meta.json (cartel seats) to add pair_class, fidelity, profit. detector_reads_only_bids applies to the detector; §11's "reads from pair_stats.csv only" becomes "reads pair_stats.csv, truth.csv and run_meta.json; never prompts, messages or transcripts".
3. Markup cartel A3 at 0.12. Explained: markup members win ~6 of 80 projects instead of ~13, so their CAP is nearly constant and the F-test is distorted in small samples (0.11 at T = 70 → 0.08 at T = 300; no rank deficiency). Keep the ≤ 0.20 assertion with this reason in the docstring. Add never_wins_firm: a firm with CAP ≡ 0 → its CAP interaction column is all zeros → detector uses a rank-revealing solve, numerator df = rank(X_U) − rank(X_C) for pairs containing that firm, and marks the firm's CAP slope "not identified" in pair_stats; no exception.
4. Fallback. State the Step 2 answer in §3 (config change or code change). The scripted cost-sharing fallback is dropped; pair restriction is in scope either way. Delete the fallback from §4e and the end-to-end test.
5. Transport vs content errors. Transport (429, 5xx, timeout, connection error): retry per Part 2.4 for up to 10 minutes of wall time; only then is the bid missing with reason transport_failed. Content: stop_reason == "max_tokens" → truncated; no text block → no_text; invalid JSON → bad_json; no bid key → no_bid_key; null, ≤ 0 or non-numeric → bad_value. Content errors are never re-prompted. Reason codes go to calls.csv (run_id, project_id, firm_id, phase, attempt, latency_ms, input_tokens, output_tokens, stop_reason, reason), not bids.csv. Add bid_parsing: a response whose content is [thinking block, text block] parses from the first text block.
6. Pilot and test pinning. Synthetic tests pass their parameters explicitly (β₀ = 0.90, β₁ = 0.08, β₂ = 0.17, σ_ε = 0.05, honest noise 0.03) rather than reading config defaults; outsider_prompt_contents reads the configured β and asserts the rendered numbers equal them; honest_bidder_distribution bounds are re-derived at β₁ = 0.08. The pilot adjusts β₀ only (Part 1.2 item 2); run_meta records the frozen values.
7. Missing bids in the detector. Rows with a missing bid are dropped from the regression; A1 uses n_joint = projects on which both firms bid, Fisher z uses n_joint; n_joint < 4 → insufficient_n. MDIST and MAXP are computed over all five rivals whether or not they bid (every firm is posted as a bidder; a missing bid is a failure, not a no-bid decision). bid_table_schema: "at most one won per project".
8. Empirical null. A1: 95th percentile of |z| over pooled control pairs, computed separately for the tag spec (ρ₀ = 0) and the project-FE spec (ρ₀ = −1/(N−1) = −0.2, N = 6 fixed). A3: 95th percentile of F. 180 values each (120 at the R = 8 fallback); report the whole distribution, as design-v2 asks.
9. realized_rule_classifier. Over the cover projects of a run (cover member identified from truth), regress cover bid/EST on [1, partner bid/EST, own cost/EST] with both regressors standardized. "inflate" if |b_own| > 1.2·|b_partner|; "shadow" if |b_partner| > 1.2·|b_own|; otherwise "mixed"; fewer than 10 cover projects → "insufficient". Scripted inflate → "inflate"; scripted shadow → "shadow".
10. CAP > 1. Not capped. CAP = EST dollars won / K and may exceed 1; MAXP = max over rivals of (1 − CAP) may be negative. Prompt shows CAP as a decimal; regression uses it as is.
11. Leaks closed. Tag: uniform over four levels; labels "routine", "standard", "complex", "hazardous" → ν = −0.05, 0, +0.05, +0.08 in that order. Honest synthetic noise: EST × N(0, 0.03) [proposal; design-v2 ambiguous]. Messages: 500-character cap, truncated not rejected; one per member per project; no re-prompt. Burn-in: add burn_in_excluded (no project_id ≤ 10 in the design matrix; usable_T = 100). Outsider prompt: add outsider_template_identical (rendered outsider prompt for the same seat, project and history is byte-identical across the four conditions) and replace the banned-word list with: none of the four cartel instruction sentences, and none of "partner", "cartel", "collud". Margin for fidelity: (bid − cost)/EST. Markup fidelity rule, written out [step-4 Q3]: executed on a project iff both members' margins exceed the median outsider margin on that project by ≥ 0.05. Profit metric [step-4 Q5]: the two cartel seats' realized profit summed over the run, minus the same two seats' summed profit in the control run of the same seed. Model settings [step-4 Q1]: request carries model claude-haiku-5-5 and max_tokens 8000 only; no temperature, top_p, top_k or effort key; run_meta records effort: "default (medium)" as provenance of what was not sent.
12. Message protocol under cover. Each member's message prompt asks for: its cost in dollars and the bid it intends to submit if it turns out to be the serious bidder. First speaker: odd projects seat A, even seat B (cover, markup); under rotation the scheduled serious bidder. Both messages reach both members before bidding, so the cover bidder always has the serious bidder's intended bid and "above your partner's bid" is satisfiable. Fidelity for cover is unchanged (lower-cost member's bid below the partner's).
13. Provenance. Design-v2 is not updated. Every item from Steps 3–4 and from this file enters the Assumptions table as [step-4 Qn] or [step-4 audit] with the design-v2 section it supersedes named in the Evidence column.
14. Repo state. Commit the model_wrappers.py and evaluate.py patches before Step 6; list them in §3 Current State.
15. Smoke/pilot. The 3-project smoke run and the 20-project pilot run on the sealed-bid prompt; the pilot reports mean and SD of bid/EST, the parse rate (block the runs if < 0.95), per-call latency and token usage.

## Part 4 — Operating mode: autonomous to the end

From this point the brief's "wait for me to confirm" steps are void, and so is every approval checkpoint in the Superpowers workflow (plan approval, task sign-off, subagent review gates). You run the whole pipeline below without stopping, and you stop only on a condition in 4.3. The sprint closes Sun Oct 25, 23:59 AoE = Mon Oct 26, 11:59 UTC; read the system clock and plan backward from that.

### 4.1 Decision rule

Anything not covered by design-v2, Steps 3–4, or this file: decide it yourself, take the reversible option when there is one, and append one line to docs/superpowers/DECISIONS.md: UTC time, the decision, the one-line reason, and whether it is reversible. That file is how the team reviews what you did; it replaces asking. Where the brief says to bring a question to me, write your answer there instead.

### 4.2 Pipeline, run end to end

1. Step 6: write the spec with every item in Parts 1–3 applied. Commit.
2. Step 7: builder check. Settle every question it raises yourself (from the repo, the paper, design-v2, this file, or as a [proposal] in the Assumptions table). Loop at most three times; after the third, proceed whatever it says. Commit.
3. Implementation plan, then the build with subagents, test-first per the spec's §6. Commit at every green milestone. Full suite green with zero API calls before any model call.
4. Hand-run 3-project smoke on the sealed-bid prompt. Fix parsing until 18/18 parse. Log latency and usage per call.
5. Cover smoke run, 10 projects (Part 1.2 item 3). Record realized cover margin, realized rule, per-call latency and mean output tokens in results/calibration.json. Re-run the scripted signature check at the realized median margin and at 0.10.
6. Pilot (20 projects, control) with β₀ auto-tuned: compute m = mean bid/EST on projects 11–20. If m ∈ [1.00, 1.10], freeze. Else set β₀ ← β₀ − (m − 1.05), rerun. At most three pilots; after the third, freeze the closest and log it. σ_ε is never touched. Record frozen values in config/frozen.json and in every run_meta.
7. Tier check without a human: make one minimal Messages call and read anthropic-ratelimit-requests-limit, -input-tokens-limit, -output-tokens-limit from the response headers (per key, if two); record them in results/calibration.json. Apply the sizing rule in Part 2.3 to pick R and the key split; log the choice in DECISIONS.md.
8. Launch with the launcher script: one process per run, staggered 5 s, scheme runs first (cover, markup, rotation) then control, keys alternated by launch order if two are set. Monitor heartbeats per Part 2.4; restart a stalled run once from its checkpoint; log every restart.
9. When all runs finish: detector, evaluation, analysis script, tables and figures, the scripted robustness tables, results/README.md summarizing every number the paper needs (flag-rate table across conditions and thresholds, control distributions of |z| and F with cartel values marked, mixed-pair A3 rate with a run-clustered interval, top-2 verdict count, sign and realized rule per run, fidelity, profit vs matched control, power vs T). Commit. Then stop with the results (4.3, condition 1).

Time ladder, measured against the sprint deadline (Mon Oct 26, 11:59 UTC), given a 55-minute critical path and up to 100 minutes of run phase at Start tier: if the runs have not launched by deadline − 5 h, drop rotation; by deadline − 4 h, T = 80 (critical path 40 min) and markup R = 2; by deadline − 3 h, control and cover R = 6; by deadline − 2.5 h with nothing launched, stop (condition 4). Each rung is logged in DECISIONS.md and the counts in the spec's §4f are recomputed from the config, which is why T and R must be config values, never literals.

### 4.3 The only reasons to stop and wake me

A stop is a hard stop, so before stopping, finish everything that does not depend on the answer, and write the stop message as: what happened, the options, your recommended option first, and what one-word reply resumes you.

1. Done. All runs analyzed, results committed, README written.
2. Spend. Projected or actual API spend above $75 (worst case in Part 2.2 is ≈ $57), or an enforced_spend_limit_reached error.
3. The design cannot run as specified. Any of: parse rate below 0.95 after one parsing fix; pilot mean bid/EST outside [0.95, 1.15] after three β₀ iterations; cover-smoke execution fidelity below 0.5 (Haiku is not running the scheme, e.g. refusing the instruction); the realized cover rule is "shadow" in the smoke run (sign prediction flips; the spec's predictions need my sign-off before the runs are spent).
4. The timeline is lost. Deadline − 2 h and no runs launched; or a launched run set projected to finish after deadline − 1 h at the measured pace, even with the ladder applied.
5. API blockers you cannot fix. Key invalid or revoked; limits so low that the full plan cannot finish before deadline − 1 h (say which tier the headers imply and whether the second key would fix it).
6. An irreversible action outside the repo. There should be none; if one appears, stop.

Not reasons to stop: thresholds in tests that need recalibrating (recalibrate and log); a scheme-signature rate that differs from this file's reference values (record the observed value); 429s (backoff handles them); a single failed run (restart it); builder-check questions (settle them); choice of library, file layout, or naming.

### 4.4 Guard rails

- Never delete or overwrite a results/ directory; new runs get new timestamped directories.
- After config/frozen.json is written, no change to the cost parameters, the instruction text, the conditions, or the detector regression without a stop (condition 3).
- No force pushes; no commits of keys or results/*.csv larger than 10 MB.
- The API key is read from the environment at process start and never written to disk or logs.

## Part 5 — Human checklist (the only human steps)

- Rotate the Anthropic API key that was pasted into the chat transcript, before the agent reaches step 4.2.4 or after the final run — never between; processes read it at start.
- Optional, but it removes a possible stop: buy credits on the second account now and set ANTHROPIC_API_KEY_2 before step 4.2.7. The launcher uses it only if the first key's output-token limit is below 1M.
- If the headers in step 4.2.7 imply an Evaluation tier (limits below 1,000 RPM / 2M / 400k for Haiku 5.5) and there is no second key, request the increase at platform.claude.com/usage/limits; the agent will have stopped under 4.3.5 only if even R = 8/8/4/4 cannot finish in time.
- Reply to a stop with the one-word option it names.
