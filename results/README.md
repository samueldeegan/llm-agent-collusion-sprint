# Results

Every output of the two experiment rounds: the raw run tables, the analysis folders, the generated
reports and the transcript archives. The top-level [README](../README.md) has the findings; this
file says what is where.

## The two reports

| Round | Report (generated, overwritten on each analysis run) | Final analysis folder | Runs |
| --- | --- | --- | --- |
| 1 | [round1/README_round1.md](round1/README_round1.md) | `round1/analysis/20261026_064028/` | 32: `control_s1-12`, `cover_s1-12`, `markup_s1-4`, `rotation_s1-4` |
| 2 | [round2/README_round2.md](round2/README_round2.md) | `round2/analysis/20261026_064109/` | 48: `control_{mem0,stateless,hidden,stateless_hidden}_s1-12` (plus round 1's 12 controls as the `base` arm) |

Both reports are written by the analysis code (`python -m src.procurement.analysis` and
`python -m src.procurement.analysis_round2`), every number to 3 decimals, with a "Not computed"
section at the end naming anything it could not produce. The final analysis folders above hold the
same report plus every table as CSV and the figures; the folders are dated 2026-10-26 because they
were regenerated when the repository was reorganised; their tables are identical to the 2026-10-25
originals listed under "Superseded" below (checked file by file, up to line endings).

## Layout

```
results/
  round1/
    README_round1.md            round-1 report
    analysis/<UTC>/             round-1 analysis output (tables, control_distributions.png, evaluation/)
    round1_transcripts.zip      transcripts of the 5 round-1 calibration runs (see "Transcripts")
    round1_parsed_responses.zip the model responses of all 32 round-1 runs, parsed to JSONL (see "Transcripts")
  round2/
    README_round2.md            round-2 report
    analysis/<UTC>/             round-2 analysis output (tables by arm, ablation_2x2.png, evaluation/)
    round2_wave1_transcripts.zip  transcripts of the 24 mem0 + stateless runs
    round2_wave2_transcripts.zip  transcripts of the 24 hidden + stateless_hidden runs
  runs/<UTC>_<run_id>/          one folder per experiment run, both rounds (80 folders)
  calibration_runs/             smoke, cover-smoke, pilot and arm-smoke runs (same layout as runs/)
  launches/<UTC>/               one folder per launcher invocation: manifest, per-run configs, logs, status
  calibration.json              what each hand-run calibration script measured, one section per script
  analysis/<UTC>/               superseded analysis folders and the cover smokes' scripted_signatures.csv
```

### A run folder

| File | Written by | Contents |
| --- | --- | --- |
| `run_meta.json` | runner | condition, seed, arm, cartel seats, cost parameters, windows, start/finish times |
| `truth.csv` | runner | ground truth per firm and project: true cost, estimate, capacity, distance, cartel role |
| `bids.csv` | runner | every bid as the regulator sees it: firm, project, bid, award; what the detector reads |
| `messages.csv` | runner | the cartel channel (scheme runs only; empty in control) |
| `calls.csv` | runner | one row per API call: tokens, cost in USD, parse outcome; what the spend guard sums |
| `transcripts/` | runner, not committed | the full prompt and reply of every call (see "Transcripts") |
| `checkpoint.json`, `heartbeat.json` | runner, not committed | resume state and liveness for the launcher |
| `detector/pair_stats.csv` | detector, optional | cached A1/A3 statistics; recomputed in memory when stale |

Run ids: `{condition}_s{seed}` for round 1, `control_{arm}_s{seed}` for round 2. The folder prefix is
the UTC start time.

### Analysis folders

Round 1 (`round1/analysis/<UTC>/`): `flag_rates.csv`, `empirical_null.csv`, `power_vs_T.csv`,
`robustness.csv`, `cartel_margins.csv`, `cartel_margins_summary.csv`, `control_distributions.png`,
`evaluation/{pair_eval,run_eval}.csv`, `evaluation/excluded_runs.json`, `README.md`.

Round 2 (`round2/analysis/<UTC>/`): `flag_rates_by_arm.csv`, `flag_rates_at_base_threshold.csv`,
`empirical_null_by_arm.csv`, `fp_vs_T_by_arm.csv`, `regimes_by_arm.csv`, `regime_counts_by_arm.csv`,
`a3_by_pair_type_by_arm.csv`, `slope_dispersion_by_arm.csv`, `synthetic_floors.csv`,
`paired_by_seed.csv`, `ablation_2x2.png`, `evaluation/`, `README.md`.

## Transcripts

Transcripts are not committed (about 325 MB) but are archived as zips of at most 10 MB each.

- **Round 2:** complete. `round2/round2_wave1_transcripts.zip` and `round2_wave2_transcripts.zip`
  hold every prompt and reply of the 48 ablation runs.
- **Round 1:** partial. The transcripts of the 32 experiment runs were deleted on 2026-10-25 at
  21:40 UTC by a defective test (the INCIDENT line in `docs/DECISIONS.md`). What survives:
  `round1/round1_transcripts.zip` (the 5 calibration runs, intact) and
  `round1/round1_parsed_responses.zip`: a copy of every model *response* from the 32 runs, parsed to
  one JSON object per line (25,520 responses; 136 unparsed) shortly before the deletion. The prompts
  are not in it; its `README.txt` gives the format and a checksum. The bid, truth, message and call
  tables of all 32 runs were never affected.

## Superseded analysis folders

Kept because a results folder is never deleted. Their tables are identical to the final folders
above; only the generated date line and the paths differ.

| Folder | What it was |
| --- | --- |
| `analysis/20261025_181052/` | the cover smoke's `scripted_signatures.csv` (an input to the round-1 analysis), not an analysis run |
| `analysis/20261025_185607/` | round-1 analysis at the end of round 1 (commit `63b9d97`, tag `round1`) |
| `analysis/20261025_232124/` | round-1 analysis regenerated during round 2, before the final review fixes |
| `analysis/20261025_232222/` | round-2 analysis before the final review fixes |
| `analysis/20261025_234357/` | round-1 analysis at the end of round 2 (tag `round2`) |
| `analysis/20261025_234438/` | round-2 analysis at the end of round 2 (tag `round2`) |

## Spend

$59.79 over 57,690 API calls across every run under `runs/` and `calibration_runs/`: $33.70 for
round 1, $26.09 for round 2. Summed from the `calls.csv` files; the round-2 report prints the total.
