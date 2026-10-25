I need you to write a design spec for a coding harness called Superpowers (a Claude Code plugin). The harness will read the spec, write an implementation plan made of small test-first tasks, and build it with fresh subagents. The standard the spec must meet: a good engineer who has never seen this project and cannot talk to me could plan the whole build from the spec alone, without guessing anything that matters.

The design is already settled. It lives in one file in the repo, `docs/superpowers/design-v2.md`. Every decision in that file is current, jointly agreed by the team, and supersedes anything else you may find. Read it first and treat it as the source of record. You do not have access to our earlier chats, and you do not need them.

The project: The Agrawal et al. (2025) repo (github.com/andyjliu/llm-agent-collusion) is a harness in which LLM agents trade as sellers in a repeated double auction, with per-round memory, a strategy scratchpad, and a seller-to-seller messaging channel. Bajari and Ye (2003, *Review of Economics and Statistics* 85(4)) give two regression-based tests, conditional independence and exchangeability of bids given public cost covariates, that distinguish competitive bidding from bid rigging in sealed-bid procurement. The repo is to be rewired into a sealed-bid procurement market with six LLM sellers (Claude Haiku 5.5), a known cost model with public covariates (distance, capacity used, firm rate, project tag) and one private draw, and four conditions: a control with no cartel and three instructed two-firm schemes (cover bidding, bid rotation, uniform markup) coordinated over the existing messaging channel. A detector that sees only bids and public covariates runs the two B&Y tests on each run's logged bid table, thresholded both at the published 5% level and at the 95th percentile of the control runs, and reports flag rates on the cartel pair, mixed pairs and outsider pairs across four runs per condition.

Ground rules

1. No fact comes from memory. Every fact about the repo comes from reading the repo and is tagged with the file path and line numbers. Every fact about Bajari and Ye comes from the paper and is tagged with the page, equation or table number. Every design decision comes from `docs/superpowers/design-v2.md` and is tagged [design-v2 §Section name]. Anything you fill in yourself is tagged [proposal] with a one-line reason. If you can't find a fact or a decision, ask me or mark it as a proposal. Never write a line without a tag.

2. How to read the design doc. It is complete and current. Its appendix "Decisions not taken" lists things we deliberately removed; do not reintroduce them as proposals. Its "Open items" section lists the only decisions still open; each has a stated default, and you use the default unless I say otherwise. Where the design doc gives a starting value (the cost-model table, T, R, the memory window), that value is the spec value; the pilot may later adjust σ_ε and β₀, so make those two configurable with the stated starting values as defaults. Where the design doc gives only a description and the spec needs a number or a name, that is a [proposal].

3. The spec starts with an Assumptions table listing every [design-v2] decision and every [proposal], each with its source and evidence status. I will read this table before anything else, so a wrong assumption gets caught early.

4. Exact values, not descriptions. The plan writer will copy numbers from the spec straight into tests. Write the formula, the distribution and its parameters, the random seed rule, the model name and settings, the number of auctions and firms, the significance level, and the exact column names and types of the logged data. If a plan writer would have to pick a number or a name, the spec has a leak.

5. Talk to me one question at a time, in plain language, and say in one sentence why the answer changes what gets built. Only ask what the repo, the paper, and the design doc don't already answer.

6. Every fundamentally new or changed piece of functionality gets its tests designed by you, in the spec, not left to the builder. The build agent writes each test first, watches it fail, then writes the code that makes it pass. A test you don't design is behavior nobody checks.

Process

Step 1. Read `docs/superpowers/design-v2.md`. Compile a list of every design decision in it, each with its section, and separately list the open items with their defaults. Show me the list in a short message and wait for me to confirm it. If the file is missing, stop and tell me right away. Do not fill the gap from memory.

Step 2. Read the repo. Tell me in a short message what exists today: how the market is set up, how the LLM agents are called, how the messaging channel works and what restricts who can message whom, what gets logged, what tests exist and what command runs them, and what language and dependency versions it uses. Wait for me to confirm. Specifically answer: is restricting the channel to one pair of agents a config change or a code change? The design doc's fallback depends on the answer.

Step 3. Read the paper. Tell me in a short message the exact auction setting, the cost factors, and the two tests (conditional independence and exchangeability) written as regression specifications with the test statistic and the decision rule, each with a reference. Check them against the Detection section of the design doc and flag any discrepancy. Wait for me to confirm.

Step 4. Ask me only the decisions still open after Steps 1 to 3, one at a time, biggest first. The research question and what counts as success are settled in the design doc; do not reopen them.

Step 5. Design the test plan. List every piece of functionality that is new or changed, using Step 2 (what we replace) and the target design. For each one, design the tests that prove it works. Write each test as: a name, exactly what goes in, exactly what must come out, and one plain sentence on what it proves, written so a teammate who hasn't read the paper can follow it. The plan must include these kinds of tests, not only checks that functions return the right shapes:
   - Known-answer tests for the two statistical tests, run on synthetic bids where the truth is built in. Bids generated to be independent given the cost factors must not be flagged more often than the significance level across many seeds. Bids generated with a built-in negative correlation between two firms must be flagged on A1 with the negative sign reported. For exchangeability: data where every firm responds to its covariates the same way must pass, and data where one firm's slopes differ must fail.
   - Known-answer tests for each scheme's predicted signature, built by applying the scheme's rule to synthetic honest bids after the fact (the design doc's scripted overwrite check): cover bidding must fail A1 with ρ < 0 and fail A3; rotation must fail both; uniform additive markup of 10% of EST must pass both. Mixed pairs under cover and rotation must fail A3 and pass A1; outsider pairs must pass both.
   - A known-answer test for the empirical-null path: given several synthetic control-like bid tables, the thresholding function returns the 95th percentile of the pooled pair statistics, and a cartel-pair statistic above it is flagged while one below it is not.
   - Tests for the execution-fidelity scorer: given bids and true costs for a pair under each scheme, it returns the stated share of projects on which the scheme was executed, including the all-executed and none-executed cases.
   - Checks that the cost generator produces the distribution the spec states: sample mean and spread within a stated tolerance at a fixed seed; the same seed across two conditions yields identical firm coordinates, η_i, project locations and tags; CAP uses past awards only.
   - The synthetic honest bidder (the design doc's cost × 1.05 + N(0, 0.03)), a stand-in that returns scripted bids without calling any model, so the whole suite runs with zero API calls. Plus one small, clearly marked smoke run with the real model that we run by hand, outside the suite.
   - An end-to-end run on the synthetic bidder that produces the logged table with exactly the specified columns and types, for a control run and for each scheme run, with the channel either restricted to the cartel pair or replaced by the scripted fallback.
   - Malformed model responses handled exactly as the spec says: treated as missing for that project, never as a phantom bid.
   - Every test deterministic (fixed seeds), the whole suite runnable with one command, and nothing in the suite ever calling the real API.
   Show us the test plan in a short message and wait for us to approve it before writing the spec.

Step 6. Write the spec with the structure below and save it to `docs/superpowers/specs/YYYY-MM-DD-bajari-ye-procurement-design.md` in the repo.

Step 7. Run a builder check. Start a fresh context (a subagent, or a new chat with no history) and give it exactly this:

"You are a talented software engineer. You've been handed a design document and asked to put together a plan to build what it describes. You will not be able to talk to the person who wrote it. Read [path]. Report only the questions you'd still need answered before you could plan this well: places where you'd have to guess and a wrong guess would matter. For each, give the question and why the answer changes the plan. Rank them by importance. Skip anything the document explicitly leaves to the builder, and anything you could reasonably decide yourself. Skip nitpicks about wording. If nothing important is left, say 'nothing important left'."

Sort its questions: answer from the repo, the paper, or the design doc where you can, settle minor ones yourself as [proposal], and bring me only the ones that are mine to decide. Fix the spec and run the check again, until it says "nothing important left". Then hand me the spec with a short list of the proposals you made.

Spec structure

0. Assumptions and sources (the table, at the very top).
1. Intent: the research question, why we're doing this, and what success looks like.
2. Goals, non-goals, and anti-goals (things that would make this a failure even if the code works). The anti-goals include: the detector reading anything other than the bid table; any condition in which the cartel's scheme is not instructed; any run that calls the API from inside the test suite.
3. Current state: what the repo does today, what we keep, what we replace, with paths.
4. Target design, one subsection each:
   a. The auction: mechanism, who wins, ties, engineer's estimate, number of bidders per auction, treatment of missing bids.
   b. Cost generation: the exact formula, each input's distribution and parameters, units, seeding, and the matched-seed rule across conditions.
   c. Information each firm receives: exactly which of its own and its rivals' variables, the history window, the memory window, and what it does not see.
   d. The LLM bidder: model name, settings, what the prompt must and must not contain for outsiders and for each cartel scheme, how a bid is parsed, what happens on a malformed response. Also the synthetic honest bidder: how it is swapped in and how it is scripted.
   e. Conditions and the cartel channel: the four conditions, the exact instruction text for each scheme, the pooled-profit objective, who may message whom and when, the one-message-each-per-project cap, the scripted cost-sharing fallback, and how the execution-fidelity scorer decides whether the scheme was run on a given project.
   f. The run: number of auctions, burn-in, number of firms, runs per condition, seeds, cartel seat rotation, cost and time budget, and why that sample size is enough for the tests to mean anything.
   g. Logged data: the exact table, column names and types. This is the contract between the simulation and the tests. It includes the bid table the detector reads, the ground-truth columns it never reads (condition, cartel seats, true costs, transcripts), and which file holds which.
   h. The two tests and the two thresholds: regression specification, bid normalization, test statistic, the closed-form threshold, the empirical-null threshold computed from the control runs, minimum sample, the three pair classes, and the output table and figure each produces.
5. Approaches considered: two or three ways to build it, what each trades off, which one I chose and why.
6. Test plan and definition of done: the approved test plan from Step 5, one table per component, each row a test with its name, inputs, exact expected result, and what it proves. Open this section with this sentence, addressed to the build agent: "Every test in this section must appear in the implementation plan as a test written before the code it checks, and the full suite must pass before the branch is finished. Add tests where you see gaps, but do not drop, weaken, or merge any test listed here."
7. Global constraints: language and dependency versions, allowed libraries, API and cost limits, reproducibility requirements, and the one command that runs the suite.
8. Left to the builder: list explicitly what the builder may decide alone.
