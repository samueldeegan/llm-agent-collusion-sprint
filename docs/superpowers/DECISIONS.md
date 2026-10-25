# Decisions log

One line per decision made autonomously under `docs/superpowers/step4-audit.md` Part 4.1. Format: UTC time | decision | one-line reason | reversible?

- 2026-10-25 06:55 | Saved the lead's audit file verbatim as `docs/superpowers/step4-audit.md` and treat it as the source of record that wins over design-v2 where they conflict | the spec must cite a file in the repo, not a chat message | yes
- 2026-10-25 06:55 | Committed the two smoke-test patches (`src/resources/model_wrappers.py`: no temperature, first text block; `evaluation/evaluate.py`: skip judge without OpenAI key) on `master` before Step 6 | audit Part 3 item 14 | yes
- 2026-10-25 06:55 | Re-calibrating every scheme-signature threshold at β₁ = 0.08, σ_ε = 0.05, T = 110 (100 usable), 100 seeds before writing §6, instead of reusing the T = 70 numbers | audit Part 1.3 requires it; thresholds set at observed − 0.07 | yes
- 2026-10-25 06:55 | Deadline read from the system clock: 2026-10-26 11:59 UTC, 29.1 h away at this entry; time-ladder rungs are therefore 06:59 (drop rotation), 07:59 (T = 80, markup R = 2), 08:59 (control/cover R = 6), 09:29 UTC (stop if nothing launched) | audit Part 4.2 | n/a
