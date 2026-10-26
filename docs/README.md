# Documents

Everything written *about* the experiment, as opposed to the code (`src/procurement/`) and the
outputs (`results/`). Start with the top-level [README](../README.md) for the overview and findings;
come here for the design and the record of how the work was done.

## Round 1: the Bajari–Ye procurement experiment

Read in this order.

| File | What it is | Read it when |
| --- | --- | --- |
| [round1/design-spec.md](round1/design-spec.md) | **The authoritative design.** Every parameter, prompt rule, detector formula, acceptance bound and output file, each tagged with its source. | You want to know exactly what was built and why. |
| [round1/design-v2.md](round1/design-v2.md) | The project lead's design note the spec was compiled from. | You want the original reasoning in the lead's own words. |
| [round1/spec-brief.md](round1/spec-brief.md) | Instructions for turning design-v2 into the spec. | You want to know how the spec was produced. |
| [round1/step4-audit.md](round1/step4-audit.md) | The lead's audit of the build steps, plus Part 4: the autonomous-mode rules (decision log, stop conditions, time ladder, guard rails) the build ran under. | You want to know what the builder was and was not allowed to decide alone. |
| [round1/build-plan.md](round1/build-plan.md) | The task-by-task implementation plan (test-first). | You want to follow a module back to the task that created it. |
| [round1/build-rulings.md](round1/build-rulings.md) | Every judgement call made during the build, with what it costs if wrong. | You are checking a decision the spec did not pin down. |

## Round 2: the memory × information ablation

| File | What it is | Read it when |
| --- | --- | --- |
| [round2/brief.md](round2/brief.md) | **The round-2 brief.** Four control-only arms that switch off the agents' private state and hide cost information; it wins over the round-1 documents where they conflict. | You want to know what round 2 changed and what it had to deliver. |
| [round2/build-plan.md](round2/build-plan.md) | The implementation plan for the round-2 code changes. | Same as above, for round 2. |
| [round2/build-rulings.md](round2/build-rulings.md) | Round-2 judgement calls, and the minor review findings left as they were. | Same as above, for round 2. |

## Across both rounds

- [DECISIONS.md](DECISIONS.md): the decision log required by the audit's autonomous mode. One line per
  decision made without asking, in UTC order, including the 21:40 incident line (a test deleted the
  uncommitted round-1 transcripts; see `results/README.md` for what survives).
- [history/2026-10-24-setup-notes.md](history/2026-10-24-setup-notes.md): how the fork and the Python
  environment were set up the day before round 1. Superseded by the top-level README's setup section.
- `references/` (not committed): the Bajari and Ye (2003) paper, kept locally because of the
  publisher's copyright. Citation in the top-level README.

## Where these files used to be

These documents were written during the build under `docs/superpowers/` and moved here on
2026-10-26 to split the rounds. **Their text is unchanged**, so paths quoted inside them (for
example "save this file as `docs/superpowers/round2-brief.md`") refer to the old layout:

| Old path | New path |
| --- | --- |
| `docs/superpowers/specs/2026-10-25-bajari-ye-procurement-design.md` | `docs/round1/design-spec.md` |
| `docs/superpowers/design-v2_1.md` | `docs/round1/design-v2.md` |
| `docs/superpowers/spec-brief.md` | `docs/round1/spec-brief.md` |
| `docs/superpowers/step4-audit.md` | `docs/round1/step4-audit.md` |
| `docs/superpowers/plans/2026-10-25-bajari-ye-procurement.md` | `docs/round1/build-plan.md` |
| `docs/superpowers/build-rulings.md` | `docs/round1/build-rulings.md` |
| `docs/superpowers/round2-brief.md` | `docs/round2/brief.md` |
| `docs/superpowers/plans/2026-10-25-round2-ablation.md` | `docs/round2/build-plan.md` |
| `docs/superpowers/round2-build-rulings.md` | `docs/round2/build-rulings.md` |
| `docs/superpowers/DECISIONS.md` | `docs/DECISIONS.md` |
| `CLAUDE_SETUP_NOTES.md` | `docs/history/2026-10-24-setup-notes.md` |
