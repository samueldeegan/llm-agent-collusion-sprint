# Can a regulator's collusion test see LLM bidders collude?

Six LLM agents (Claude Haiku 5.5) bid against each other as construction firms in a sealed-bid,
first-price procurement market built after Bajari and Ye (2003). In some runs two of the firms are
seated as a cartel with a private channel and an instructed scheme. A regulator who sees only the
bids then runs the Bajari–Ye tests on every pair of firms: **A1**, are the two firms' bid residuals
correlated, and **A3**, do they respond to cost shifters with the same slopes. The question is
whether those tests tell the cartel from the honest pairs, and, just as important, how often they
accuse honest LLM bidders.

Two rounds, 80 runs of 110 projects each, all on 2026-10-25, $59.79 of API spend in total.

| Round | What was run | Finding |
| --- | --- | --- |
| **1** | 32 runs: 12 honest controls and 20 cartel runs (12 cover-bidding, 4 bid-rotation, 4 markup) | The cartel agents refused the schemes, and the tests flagged a third of the honest pairs. |
| **2** | 48 honest-control runs in four ablation arms that remove the agents' private memory and hide cost information | The false positives come from the agents' private state. Remove it and the tests behave as the theory says. |

## Round 1: refusals, and a detector that cries wolf

- **The cartels did not form.** In 19 of the 20 scheme runs the first refusal in the cartel channel
  came on project 1 or 2 (the exception held for five projects); 79–100% of the projects with messages
  contained a refusal naming bid rigging, price fixing or antitrust. The scheme runs are therefore not
  collusion runs, and the headline verdict (do the two cartel firms rank top-2 by A3?) was 0 of 12
  cover, 0 of 4 rotation and 0 of 4 markup runs.
- **The tests accuse honest bidders far above the nominal 5%.** Closed-form A3 flagged 37% of the
  180 honest control pairs (32% after treating "sit-out" placeholder bids above 1.5× the estimate as
  missing); A1 flagged 37% (7% filtered). An in-sample empirical threshold hides this by construction.
- **Why:** some firms stop bidding seriously on projects they do not want, and bidding styles drift
  across the run, so the panel the test assumes (stable, independent cost-based bidding) is not what
  the agents produce. That motivated round 2.

Full report: [results/round1/README_round1.md](results/round1/README_round1.md).
Figure: [control distributions](results/round1/analysis/20261026_064028/control_distributions.png).

## Round 2: where the false positives come from

Four arms of 12 honest-control runs, seed-matched to the round-1 controls (the `base` arm). The
`tag_filtered` specification, closed-form thresholds, 180 pairs per arm; the last column is a
seed-matched comparison against base over the 12 seeds.

| Arm | Private memory and scratchpad | Cost formula and rival rates | A3 false positives | A1 false positives | A3 lower than base in … of 12 seeds (Wilcoxon p) |
| --- | --- | --- | --- | --- | --- |
| base (round-1 controls) | on | shown | 0.322 | 0.072 | reference |
| mem0 | off, public bid history kept | shown | 0.094 | 0.039 | 10 (0.009) |
| stateless | off, no history | shown | 0.156 | 0.039 | 9 (0.010) |
| hidden | on | hidden | 0.267 | 0.056 | 7 (0.48, not significant) |
| stateless_hidden | off, no history | hidden | 0.044 | 0.044 | 11 (0.001) |

- **Private state drives the excess.** Removing the agents' private memory and scratchpad cuts A3
  false positives from 32% to 9%, below the 15% that rational bidders with a kinked shading rule
  produce on the same cost tables; the firms that sat out projects and the firms with fat markups
  (10 and 2 of 72 in base) both vanish.
- **Hiding cost information alone does little** (27%, not significant).
- **Removing both gives the static market the theory assumes.** A3 falls to 4.4%, about the nominal
  5% and the 3.9% floor of smooth rational bidders; 59 of 72 firm-seasons settle on a steady markup.
- **Prices do not move.** Mean bid over estimate is 1.056–1.065 in every arm; what changes is how
  often a firm skips a project with a placeholder bid (4.7% of bids in base, about 0% without state).

Full report: [results/round2/README_round2.md](results/round2/README_round2.md).
Figure: [the 2×2 ablation](results/round2/analysis/20261026_064109/ablation_2x2.png).

## Read in this order

1. This file.
2. The two reports above. Each starts with a ten-line summary and ends with a "Not computed"
   section listing anything it could not produce.
3. [docs/round1/design-spec.md](docs/round1/design-spec.md): the full design, every parameter and
   formula with its source. [docs/round2/brief.md](docs/round2/brief.md): what round 2 changed.
4. [docs/README.md](docs/README.md): the rest of the documents, including the decision log and the
   build records.
5. [src/procurement/README.md](src/procurement/README.md): the code, module by module, and the
   operator's guide to running it.

## Repository map

```
README.md            this file
docs/                design documents, the decision log and the build records, by round
src/procurement/     the experiment: market, LLM bidders, cartel channel, detector, evaluation, analysis
src/continuous_double_auction/   the upstream paper's code; procurement reuses only its Jinja helper
tests/               538 tests, no API calls
scripts/             the hand-run calibration scripts (smoke, cover smoke, pilot, tier check, arm smoke)
config/              defaults, firm roster, the run plan, and the cost parameters frozen by the pilot
results/             every run table, analysis folder, report and transcript archive (see results/README.md)
upstream/            the upstream paper's data, experiment scripts, figures and README, untouched
```

Git tags `round1` and `round2` mark the commits each round's results were produced from.

## Reproducing

Python 3.11. Windows paths are shown; on other systems use `.venv/bin/python`.

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\pip install -e .
.venv\Scripts\python.exe -m pytest -q          # about 5 minutes, no API calls
```

The analyses are offline and deterministic from the committed run tables:

```
.venv\Scripts\python.exe -m src.procurement.analysis --results-root results/runs --scripted-csv results/analysis/20261025_181052/scripted_signatures.csv
.venv\Scripts\python.exe -m src.procurement.analysis_round2 --results-root results/runs
```

Each writes a new `results/round{1,2}/analysis/<UTC>/` folder and overwrites the round's report.

Re-running the experiment itself needs `ANTHROPIC_API_KEY` in the environment (it is read at process
start and never written anywhere). The order is: the calibration scripts in
`src/procurement/README.md` ("Hand-run sequence"), then the launcher:

```
.venv\Scripts\python.exe -m src.procurement.launcher --plan config/run_plan.json --dry-run
.venv\Scripts\python.exe -m src.procurement.launcher --plan config/run_plan.json            # round 1
.venv\Scripts\python.exe -m src.procurement.launcher --plan config/run_plan.json --arms mem0,stateless   # round 2, wave 1 (arms imply --no-ladder)
```

The launcher runs one OS process per run, stops everything if projected spend crosses
`spend_stop_usd` in `config/defaults.json`, and never overwrites a results folder. A full rerun of
both rounds cost about $60 at Haiku 5.5 prices.

## Provenance

- Market, detector and experiment design: `docs/round1/design-spec.md`, after Patrick Bajari and
  Lixin Ye, "Deciding Between Competition and Collusion", *Review of Economics and Statistics* 85(4),
  2003, 971–989.
- The repository is a fork of [andyjliu/llm-agent-collusion](https://github.com/andyjliu/llm-agent-collusion),
  the code for Agrawal et al., "Evaluating LLM Agent Collusion in Double Auctions"
  ([arXiv:2507.01413](https://arxiv.org/abs/2507.01413)). Its code, data and README are kept under
  `upstream/` and `src/continuous_double_auction/`, unchanged except for Python 3.11 and Claude 5.5
  compatibility fixes.
- Bidders: `claude-haiku-5-5`. Runs: 2026-10-25 (round 1 18:11–18:55 UTC; round 2 22:30–22:57 UTC).
- The build was done with Claude Code under the autonomous-mode rules in
  `docs/round1/step4-audit.md`; every decision taken without a human is one line in
  `docs/DECISIONS.md`.
