# Round-2 build rulings

Every controller ruling from the round-2 SDD ledger (plan: docs/superpowers/plans/2026-10-25-round2-ablation.md), in the order made. Format: decision — why — what it costs if wrong.

- Ruling: Task 4 (evaluation grouping by arm) runs AFTER the wave-1 launch (order 1,2,3,5,11 → smokes → launch → 4,6 → wave 2) — it changes no run output and the 03:00 UTC launch deadline is the binding risk; brief §5.2 lists §2.2 before launch but §2.2's run-identity half (Task 2) is in — costs nothing if wrong beyond ordering.
- Ruling: implementers run sequentially (skill rule) even though the deadline is tight; mechanical tasks go to sonnet to keep turn count low — costs time if a cheap model stalls.
- Ruling: guard tests must target non-existent probe paths so an unguarded run raises FileNotFoundError, never deletes — fix round 1 on Task 1 — costs nothing if wrong.
- Ruling: brief §3 round1_transcripts.zip recorded as unavailable in README_round2 and DECISIONS; T13 archive script covers round-2 waves only for round 1 — costs the raw round-1 record, already lost.
- Ruling: Task 2 implementer dispatched while the Task 1 review runs (reviewer is read-only; disjoint files) — deadline pressure; any Task 1 fix round waits for Task 2's report — costs a possible index race if wrong.
- Ruling: round-1 run dirs need not be resumable after Task 2 (all 32 are finished and the launcher skips finished runs) — costs a manual header fix if a round-1 resume is ever needed.
- Ruling: prompt_sections.expected_sections stays in src/ (reviewer suggested tests/) — Task 11's smoke script uses it at run time on real transcripts — costs a move if wrong.
- Ruling: Task 11 tests the hidden-arm prompt checks on hand-made strings now; end-to-end hidden smoke tests move to Task 6's dispatch (Task 6 not built yet) — costs one extra test step in Task 6.
- Ruling: fix = recompute ACTUAL spend each poll over every run dir under results_root and calibration_runs (resumed dirs not double counted, as spend_totals already does); PROJECTED = that actual + this launcher's own remaining planned calls × cost per call — other live launches' remaining calls are not projected — costs at most a late spend stop if per-call cost spikes (the actual stop still binds at $150 across all launches).
- Ruling: Task 5 fix round 1 runs in parallel with the Task 11 implementer (disjoint files; each stages only its own files, retry on index.lock) — deadline — costs a retried commit if wrong.
- Ruling: Task 11 findings affect only hidden-arm checks (wave 2) — wave-1 smokes stand — costs a re-check of wave-1 transcripts if wrong (free).
- Ruling: Task 6 in worktree .worktrees/round2-hidden (branch round2-hidden); wave 2 launched from there; main-checkout tasks avoid runner-path modules during wave 1 — templates are re-read per render — costs a merge step if wrong.
- Ruling: Tasks 8, 9, 13 (new modules regimes.py, floors.py, archive.py + their own test files) run in parallel with Task 7 in the main checkout; each runs only its own test file, stages only its own paths; controller runs the full suite after they land — deadline for analysis code before the runs finish (~00:00 UTC) — costs a merge-order fix if two touch a shared file.
- Ruling: the regenerated round-1 README may gain tag_filtered rows in the control-distributions table (extra content, existing numbers unchanged) — brief §2.7 requires numbers unchanged, not identical layout — costs a filter if the lead objects.
- Ruling: honest-baseline definition kept (it is the brief's definition: same metric, same two seats, matched control run, roles from control true costs); the brief's 0.75–0.97 is round-1 context computed outside this code; README note + DECISIONS line give the computed range 0.845–0.973 and the exact definition — costs a code change if the lead meant another baseline.
- Ruling: mean bid/EST in round-2 report switches to non-sentinel mean + sentinel share; round-1 run_eval.mean_bid_over_est (round-1 README) left unchanged so round-1 numbers stay identical — costs a round-1 relabel if the lead wants it there too.
- Ruling: stray empty dir results/__guard_probe_dir__ (Task 1 red phase) left for the user — guard rail forbids agents deleting under results/ — costs nothing.

## Deferred minors (not fixed; triaged by the final review as safe to leave)

- Task 2: minor (deferred): RunConfig.arm unvalidated (empty / '_s' in name); long lines io.py:293, launcher.py:~1106; DECISIONS 21:40 line before 21:38 lines; no pre-Task-2 config JSON load test.
- Task 1: re-review of 3d0ac63..de5625b — all findings addressed; minor (deferred): DECISIONS stand-in is an existing tmp file (write-open on existing tmp path, harmless).
- Task 3: minor (deferred): no message-prompt test under mem0/stateless; non-base arms checked by headings not exact whitespace; memory/scratchpad gates tested together only; no set_state-under-disabled-arm test.
- Task 5: minor (deferred): concurrent stops rotate shared results/STOP.json (documented); defaults without spend_stop_usd now KeyError; finished_runs key type changed (in-diff callers updated).
- Task 5: minor (deferred): self.baseline now only used for the start log (dead state); pooled per-call cost across launches skews projection.
- Task 4: minor (deferred): groups-by-arm test arms have identical synthetic bids (catches pooling, not arm swap); no regex test for arm + resume suffix; round-1 regenerated evaluation CSVs gain arm column (schema differs, numbers same).
- Task 13: minor (deferred): .part unlink also fires if the ZipFile open fails on a pre-existing stale .part (unlinks a file this call didn't create, but only ever <out>.part under results/archive); exists-check precedes zero-runs short-circuit; ValueError on dup arcnames not caught in CLI.
- Task 9: minor (deferred): no tests for tie→lowest seat, input non-mutation, m/δ ranges; single-firm project would raise (impossible with N=6).
- Task 8: minor (deferred): truth-vs-bid min cost not distinguished by a test; seed float when no _s<n>; groupby.apply with ndarray returns.
- Task 7: minor (deferred): no exact-1.5 boundary test; filter_sentinels copies the frame even when tag_filtered not requested; test_analysis threshold loop omits tag_filtered.
- Task 10: minor (deferred): floors burn_in/T from first base dir only (assert uniform).
- Task 12: minor (deferred): refusal share denominator wording ('projects with messages' filtered to cartel senders); wins/profit over common projects vs margins over all post-burn-in (disclosed); long lines.
- Task 7: minor (deferred): empty stored pair_stats acceptance path untested.
