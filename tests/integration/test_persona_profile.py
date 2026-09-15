import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agentsociety2.agent.base import ReactLoopRequest
from custom.agents.life_agent import LifeAgent
from worldmock.life.scenario import LifeScenario, load_scenario
from worldmock.runtime import _agent_specs


SCENARIO = Path(__file__).resolve().parents[2] / 'scenarios/white-collar-12-weeks.json'
TRAITS = ('occupation', 'routine_preference', 'consumption_preference', 'risk_preference', 'trait_source_kind')


def test_frozen_persona_traits_reach_the_actual_native_agent_prompt():
    scenario, _ = load_scenario(SCENARIO)
    specification = _agent_specs(scenario)[0]
    agent = LifeAgent()
    agent._id = specification['id']
    agent._name = specification['profile']['name']
    agent._profile = specification['profile']
    agent._enable_memory = False
    messages = agent.build_react_messages(ReactLoopRequest(0, scenario.start_at, {'LifeEnv': {}}))
    sent_profile = json.loads(messages[1]['content'])['profile']
    for field in TRAITS:
        assert sent_profile[field] == getattr(scenario.agents[0], field)
    assert sent_profile['trait_source_kind'] == 'experiment_assumption'


@pytest.mark.parametrize('field', TRAITS)
def test_persona_traits_cannot_be_silently_omitted(field):
    value = json.loads(SCENARIO.read_text())
    del value['agents'][0][field]
    with pytest.raises(ValidationError, match=field):
        LifeScenario.model_validate(value)
