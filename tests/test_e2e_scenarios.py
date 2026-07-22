from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest_e2e import e2e_bot, e2e_llm_bot  # noqa: F401
from tests.e2e_harness import ScenarioRunner, load_scenario

SCENARIO_DIR = Path(__file__).with_name("e2e_scenarios")
SCENARIOS = tuple(sorted(SCENARIO_DIR.glob("*.json")))


@pytest.mark.integration
@pytest.mark.parametrize("scenario_path", SCENARIOS, ids=lambda path: path.stem)
@pytest.mark.asyncio
async def test_data_driven_e2e_scenario(e2e_llm_bot, tmp_path, scenario_path):  # noqa: F811
    scenario = load_scenario(scenario_path)
    runner = ScenarioRunner(e2e_llm_bot, tmp_path / scenario["name"])
    await runner.run(scenario)
