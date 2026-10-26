# How this project was set up (Claude, Oct 24 2026)

## Assumptions I made — check these first

1. **"Agrawal LLM collusion repo" = `github.com/andyjliu/llm-agent-collusion`.**
   Evidence: it is the official code for Agrawal, Teo, Vazquez et al., *Evaluating LLM Agent Collusion in Double Auctions* (arXiv 2507.01413); the README cites the paper and alphaXiv lists it as the paper's implementation.
2. **"superpowers ai harness repo" = `github.com/obra/superpowers`.**
   Evidence: the only widely known repo by that name; its README describes support for many "harnesses" (Claude Code, Codex, Cursor, ...).
   It is **not a Python project** (112 markdown files, 50 shell scripts, 6 small Python test files), so per your condition it was only downloaded, not set up in PyCharm. It lives at `C:\Users\samue\PycharmProjects\superpowers` with its full git history.
3. **You will run the simulation with OpenAI models.** Evidence: none — that is just the repo's default (gpt-4.1 for all 10 agents). Change `--seller_models` / `--buyer_models` in the run configuration if you want something else.

## What is on disk

| Path | What it is |
|---|---|
| `C:\Users\samue\PycharmProjects\llm-agent-collusion\` | the repo: code + the paper's experiment logs (`data\`, 292 MB) |
| `.venv\` | Python 3.11 virtual environment; everything in `requirements.txt` is installed (PyCharm created it) |
| `.idea\runConfigurations\` | three ready-made run configurations (see below) |
| `results\` | run outputs. The two `*_smoke_test` folders already in there are empty leftovers from my two test runs (one hit the syntax error, one stopped at the missing API key) — safe to delete |
| `C:\Users\samue\PycharmProjects\_claude_transfer\`, `PycharmProjects\superpowers.zip`, `PycharmProjects\llm-agent-collusion.zip` | leftover transfer files — safe to delete |

The collusion repo copy has **no `.git` folder**: the original has a single commit, and its history would have been another 26 MB. Run `git init` in the folder if you want to track your own changes.

## How to run it

1. Give Windows your OpenAI key, once (the code reads it from the environment variable `OPENAI_API_KEY`):
   Start menu → type `environment variables` → open **"Edit environment variables for your account"** → under *User variables* click **New...** → Variable name `OPENAI_API_KEY`, Variable value = your key → OK, OK.
   Then **close and reopen PyCharm** so it picks the variable up.
   (Alternative: in PyCharm, run-configuration dropdown → *Edit Configurations...* → *Environment variables* → append `;OPENAI_API_KEY=your-key` — but you'd have to do that in each of the three configurations.)
2. In the run-configuration dropdown at the top of PyCharm pick **`1 - Simulation smoke test`** and press Run.
   That is 5 sellers + 5 buyers, all gpt-4.1-mini, 3 rounds.
3. Output lands in `results\<timestamp>_<tag>\` (one `.md` transcript per agent, `auction_results.md`, `collusion_metrics.json`, `experiment_metadata.json`).

The other two configurations are the paper's settings: **`2`** = base case (gpt-4.1, 30 rounds), **`3`** = same with seller communication switched on.

How many model calls each run makes (counted from the code, not measured):
each round calls every agent once, and the end-of-run judge (gpt-4.1-mini) makes one call per seller per round.

- smoke test: 30 agent calls + 15 judge calls, all gpt-4.1-mini
- paper default: 300 agent calls (gpt-4.1) + 150 judge calls (gpt-4.1-mini)

## Three things I did differently from the README

1. **One line of the repo's code was changed** — `src\continuous_double_auction\util\logging_util.py`, line 41.
   The original used double quotes inside a double-quoted f-string, which only Python 3.12+ accepts (the README says Python 3.12; your PyCharm created the venv with your installed Python 3.11, and the first test run died with `SyntaxError: f-string: unmatched '['`). I changed the inner quotes to single quotes — same behaviour, parses on 3.11. Every other file in the repo compiles fine on 3.11 (checked). If you would rather stay exactly on the authors' setup, make the venv from a Python 3.12 base interpreter instead and revert that line.
2. **`WANDB_MODE=disabled` is set in every run configuration.**
   The last step of `simulation.py` logs metrics to the authors' private Weights & Biases team (`spar-llm-collusion`). Without this you would get a W&B login prompt / permission error at the end of every run. The results are already saved to `results\` before that step, so nothing is lost.
3. **`pip install -e .` was not run** (I had no terminal on your PC).
   The run configurations put the project folder on `PYTHONPATH` instead, so the `from src...` imports work when you run from PyCharm.
   If you want to run from a terminal instead, do one of these once in the project folder with the `.venv` active:
   - `pip install -e .`  (what the README says), or
   - run with `python -m src.continuous_double_auction.simulation ...` instead of `python src/continuous_double_auction/simulation.py ...`

## Superpowers

To actually use it, install it as a Claude Code plugin (it is not something you run from PyCharm):

```
/plugin install superpowers@claude-plugins-official
```

The local clone is only useful for reading the skills (`skills\*\SKILL.md`) or hacking on them.
