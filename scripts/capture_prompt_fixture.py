"""Write the base-prompt fixture files under tests/fixtures (run once on round-1 code)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.procurement.environment import generate_environment  # noqa: E402
from src.procurement.render import render_outsider_prompt_for_test  # noqa: E402
from tests.fixtures.prompt_fixture import fixture_cfg, fixture_inputs  # noqa: E402


def main() -> None:
    cfg = fixture_cfg()
    env = generate_environment(3, cfg.cost, 110)
    fx = fixture_inputs(env)
    system, user = render_outsider_prompt_for_test(cfg, env, fx["seat"], fx["t"], fx["history"], "control",
                                                   memory=fx["memory"], scratchpad=fx["scratchpad"])
    d = ROOT / "tests" / "fixtures"
    for name, text in (("system", system), ("user", user)):
        with open(d / f"base_prompt_seat2_p15.{name}.txt", "w", encoding="utf-8", newline="\n") as f:
            f.write(text)


if __name__ == "__main__":
    main()
