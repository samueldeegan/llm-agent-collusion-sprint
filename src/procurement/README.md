# src/procurement

A sealed-bid procurement market with LLM bidders (Claude Haiku), after Bajari and Ye. Six firms bid
for projects in a first-price auction. Some conditions seat a two-firm cartel with a private channel
and a scripted instruction (cover, markup, rotation); the control has none. A regulator-view detector
(pooled OLS, tests A1 and A3) is run on `bids.csv` only, then scored against ground truth.

## Module map

- `config.py`: run configuration, run plan, cartel seat rule, first-speaker rule, pricing.
- `types.py`: shared dataclasses and the `Bidder` protocol.
- `environment.py`: project environment (sites, yards, estimates) and cost model.
- `auction.py`: first-price auction, award ledger, rival covariates, public postings.
- `channel.py`: private cartel channel (who may message, order, delivered text).
- `texts.py`: verbatim cartel instruction texts.
- `render.py`: prompt rendering (cached system prefix, per-project user message); `prompts/` holds text.
- `client.py`: the Haiku client, the only code that talks to the API; parses bids and messages.
- `llm_bidder.py`: the LLM bidder, one call per bid and per message, per-seat memory.
- `rules.py`, `synthetic.py`, `scripted.py`: scripted cartel rules, synthetic bidder and table
  simulator, scripted robustness rows (`scripted_signatures.csv`). Offline.
- `runner.py`: the project loop for one run (postings, messages, bids, award, logging, STOP, resume).
- `io.py`: run directory layout, CSV/JSON writers and readers, heartbeat, checkpoint, transcripts.
- `detector.py`: Bajari-Ye detector, writes `detector/pair_stats.csv`.
- `fidelity.py`: execution fidelity, realized cover rule, margins, cartel profit (ground truth).
- `evaluation.py`: joins `pair_stats` with ground truth; writes `pair_eval.csv`, `run_eval.csv`.
- `analysis.py`: round-1 tables, control-distribution figure, `results/round1/analysis/<UTC>/` and
  `results/round1/README_round1.md`.
- `analysis_round2.py`: round-2 per-arm tables, paired-by-seed test, ablation figure,
  `results/round2/analysis/<UTC>/` and `results/round2/README_round2.md`.
- `regimes.py`: classifies each firm-season's bidding style (passer, fat, shade, razor, moderate).
- `floors.py`: synthetic rational bidders (smooth and kinked shading) on real truth tables; the A1/A3
  false-positive floors.
- `prompt_sections.py`: checks which prompt sections an arm's rendered prompt must and must not contain.
- `archive.py`: zips run transcripts (used by `scripts/archive_transcripts.py`).
- `calibration.py`: shared plumbing of the hand-run scripts (`results/calibration.json`).
- `launcher.py`: one OS process per run, monitor loop, spend guard, STOP, time ladder.
- `pilot.py`: pilot support code used by `scripts/pilot.py`.

## Tests

    .venv\Scripts\python.exe -m pytest tests -q

About 540 tests, roughly 330 s. The suite makes zero API calls: the client transport is injected, and
no test constructs a real SDK client. `pytest.ini` defines a `slow` marker for the long known-answer
tests; they run by default. Skip them with `-m "not slow"`, run only them with `-m slow`.

## Hand-run sequence (needs `ANTHROPIC_API_KEY`)

Run from the repo root in PowerShell. Each script prints `about to make N model calls ...` first.
Keys are read from the environment only. Step order matters.

1. Smoke (18 calls): `.venv\Scripts\python.exe scripts\smoke_llm.py [--results-dir results]`
   Writes a run under `results/calibration_runs/` and the `smoke` and `measured` sections of
   `results/calibration.json`. Exit 0: 18/18 bids parsed. 1: fewer parsed. 5: key missing or rejected.
2. Cover smoke, prefreeze (80 calls):
   `.venv\Scripts\python.exe scripts\cover_smoke.py --phase prefreeze`
   Other flags: `--results-dir`, `--config-dir`, `--seeds 100`, `--skip-scripted`.
   Writes a run under `results/calibration_runs/`, `cover_smoke_prefreeze` and `measured` in
   `calibration.json`, and (unless `--skip-scripted`) `results/analysis/<UTC>/scripted_signatures.csv`.
   Exit 0: proceed. A realized rule of "insufficient" (fewer than 10 cover projects with both bids) is
   not a stop: it prints a WARNING recommending one rerun of this step (the shadow check needs a realized
   rule) and records `rule_insufficient: true`. 1: the scripted-signatures step failed; the run's
   numbers are already recorded, so rerun the offline command the script prints,
   `python -m src.procurement.scripted --seeds N [--extra-margin X]`. 3: STOP, realized rule is shadow
   or fidelity < 0.5; do not run the pilot (3 wins even if the scripted step also failed). 5: key problem
   or an unreadable `calibration.json`.
3. Pilot (120 calls per pilot, at most 3 pilots, at most 360): `.venv\Scripts\python.exe scripts\pilot.py`
   Flags: `--results-dir`, `--config-dir`, `--decisions`. Loops on beta0 until the mean bid/EST of
   projects 11-20 is in [1.00, 1.10]. The pilot runs at k_frac = 0.25 x 110 / 20 = 1.375 so that CAP
   grows per project as in the 110-project experiment (recorded as `k_frac` in the `pilot` section and in
   `frozen.json`'s `pilot_history`; the experiment keeps 0.25). Writes `config/frozen.json`, appends a line to
   `docs/DECISIONS.md`, writes the `pilot` section. Exit 0: frozen. 3: STOP (parse rate
   < 0.95, or frozen m outside [0.95, 1.15]; no `frozen.json`). 5: bad key, `frozen.json` exists, or
   `calibration.json` is unreadable.
4. Cover smoke, postfreeze (80 calls):
   `.venv\Scripts\python.exe scripts\cover_smoke.py --phase postfreeze`
   Runs at the frozen parameters; writes `cover_smoke_postfreeze` and `measured` (these now win) and a
   new `scripted_signatures.csv` (pass it to analysis as `--scripted-csv`). Exit 0 (also on shadow or
   low fidelity: it prints a WARNING and does not stop). 1: the scripted-signatures step failed; the
   run's numbers are already recorded, so rerun the offline `python -m src.procurement.scripted
   --seeds N [--extra-margin X]` the script prints. 5: `config/frozen.json` missing or key problem.
   If the time ladder forces skipping it: `scripts\cover_smoke.py --note-skip-postfreeze` (no API, exit 0;
   exit 5 when there is no prefreeze record or a real postfreeze measurement already exists).
5. Tier check (1 call per key): `.venv\Scripts\python.exe scripts\tier_check.py [--results-dir results]`
   Slot 1 uses `ANTHROPIC_API_KEY`, slot 2 uses `ANTHROPIC_API_KEY_2` if set. Writes `limits` and
   `tier_check_utc` to `calibration.json`. Exit 0: proceed. 4: a header is missing or a transient
   error (limits not updated, earlier good values kept; rerun). 5: key invalid for a slot.

## Round-2 scripts

- `scripts\arm_smoke.py --arm ARM` (18 calls): one short control run under `results/calibration_runs/`
  for one ablation arm (`mem0`, `stateless`, `hidden`, `stateless_hidden`); records `round2_smokes[ARM]`
  in `calibration.json`. Exit 0: 18/18 bids parsed.
- `scripts\archive_transcripts.py --wave WAVE`: zips the transcripts and checkpoints of a wave's runs
  (read-only on the run directories).
- `scripts\capture_prompt_fixture.py`: regenerates `tests/fixtures/base_prompt_*.txt`, the byte-exact
  base prompt the round-2 prompt flags are tested against.

## Launching the experiment

Dry run first (writes the manifest, configs and status, spawns nothing). It needs `config/frozen.json`,
so it is available only after the pilot; before that add `--allow-unfrozen` (defaults.json costs):

    .venv\Scripts\python.exe -m src.procurement.launcher --plan config/run_plan.json --dry-run

Then real:

    .venv\Scripts\python.exe -m src.procurement.launcher --plan config/run_plan.json

Other flags: `--allow-unfrozen` (tests and dry runs only), `--poll-s 60`, `--results-root results/runs`,
`--frozen config/frozen.json`, `--calibration`, `--decisions`, `--bidder llm|synthetic`,
`--stagger-s 5`, `--rerun-finished`, `--ignore-live`. A real launch into `results/runs` refuses
`--bidder synthetic` and `--allow-unfrozen` (exit 5); use another `--results-root` for those. Every run
takes model, max_tokens, burn-in, N, windows, message cap and concurrency from `config/defaults.json`,
the cost parameters from `config/frozen.json` over the defaults, and T from the plan. With tier-check
limits in `calibration.json`, R per condition is the smaller of the plan's R and the sizing rule's
(both are logged).

Relaunching after a crash or stop: a (condition, seed) with a finished matching run is skipped unless
`--rerun-finished`. One with an unfinished matching run whose heartbeat shows activity within the last
600 s (`stall_s`) refuses the whole launch (exit 5, naming the runs; probably still running; a dry run only warns) unless
`--ignore-live`. Otherwise it resumes from the latest unfinished checkpointed directory
(`--resume-dir`; this uses the run's one restart), or starts fresh when there is no checkpoint. When
runs of the plan already exist, T stays at their T whatever the time ladder says (logged as "T kept
at ...").

Run the launcher from a dedicated console, not inside a background task that can be killed. Exit
codes: 0 all runs done; 2 spend stop (stops when actual spend exceeds $75, or projected spend does after
200 calls; both count every launch); 5 pre-flight refusal (no `frozen.json`, unusable or invalid
`calibration.json`, key not set, synthetic or unfrozen into `results/runs`, a live unfinished run) or a
fatal API error; 6 all runs terminal but not all done; 7 time ladder stop (see below).

Time ladder: the deadline is `deadline_utc` in `config/run_plan.json` (2026-10-26 11:59 UTC). If nothing
is launched yet, the plan is cut by hours left: 5 h or less drops rotation; 4 h: T 80 and markup R 2;
3 h: control and cover R 6; 2.5 h: stop, nothing launched, exit 7. A real launch logs each rung to DECISIONS.md.

A run stops (runner exit 3) when a file named `STOP` appears in its run directory. The launcher does
this to every child on a spend stop, fatal error, failure or interrupt, and records the reason in
`results/STOP.json` (for example `launcher_error`, `interrupted`). A `STOP` that appears during the last
project does not stop the run: its table is complete, so it finishes (exit 0, `finished_utc` set).

## Analysis

    .venv\Scripts\python.exe -m src.procurement.analysis --results-root results/runs [--out DIR] [--scripted-csv PATH]

Runs evaluation, then writes the tables, figure and `README.md` into a new `results/round1/analysis/<UTC>/`
(or `--out`). The same `README.md` is also written to `results/round1/README_round1.md` (`round1/`
under the parent of `--results-root`). The round-2 counterpart,
`python -m src.procurement.analysis_round2 --results-root results/runs [--out DIR] [--archives DIR]`,
keeps the control runs of every arm (round 2's `base` arm is the round-1 controls) and writes
`results/round2/analysis/<UTC>/` and `results/round2/README_round2.md`. Relative paths (here and in `python -m src.procurement.evaluation` and `scripted`)
resolve against the repository root. Evaluation takes LLM runs only (`--include-synthetic` adds the
others; tests), and, when `config/frozen.json` and `config/run_plan.json` exist (`--frozen`, `--plan`),
only runs at the frozen cost and the plan's T (or the ladder's 80). Every directory not used is listed
in `excluded_runs.json` and the README with the reason (another bidder, cost or T; no or unreadable
`run_meta.json`, no `bids.csv`; superseded). A run with no completed project is listed with 0 projects
and kept out of the tables. The README's run inventory also lists the planned runs with no directory
(STOP.json `runs_not_started`, plan rows never launched); an unreadable `STOP.json` or
`calibration.json` is named under "Not computed". Evaluation never writes into a run directory: a run's
`detector/pair_stats.csv` is used only when it is newer than `bids.csv` and matches its row count,
otherwise the detector runs in memory. The detector alone: `python -m src.procurement.detector --run-dir
DIR` (on an unfinished run it computes in memory and writes nothing). Scripted rows:
`python -m src.procurement.scripted --seeds 100`.

## Where outputs land

- `results/runs/`: experiment runs (one directory each).
- `results/calibration_runs/`: smoke, cover smoke and pilot runs.
- `results/launches/<UTC>/`: per launch manifest, configs, announce files, logs, `status.json`.
- `results/round1/analysis/<UTC>/` and `results/round2/analysis/<UTC>/`: analysis output per round.
- `results/round1/README_round1.md`, `results/round2/README_round2.md`: copies of the latest analysis
  README of each round (written by the analysis).
- `results/analysis/<UTC>/`: `scripted_signatures.csv` from the cover smokes.
- `results/calibration.json`: one section per hand-run script.
- `config/frozen.json`: cost parameters frozen by the pilot.
- `results/STOP.json`: written when the launcher stops runs; an existing one is renamed
  `STOP_<utc>.json`, never deleted.

## Guard rails

- The test suite makes zero API calls.
- Never delete `results/`; every output is new and timestamped.
- No parameter changes after `config/frozen.json` exists.
- The API key is read from the environment only; it is never printed, written or put on a command line.
