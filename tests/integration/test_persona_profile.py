import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.agent.base import ReactLoopRequest
from custom.agents.life_agent import LifeAgent
from worldmock.life.scenario import LifeScenario, load_scenario
from worldmock.runtime import _agent_specs


SCENARIO = Path(__file__).resolve().parents[2] / 'scenarios/white-collar-12-weeks.json'
TRAITS = ('trait_source_kind',)


def test_frozen_persona_identity_reaches_the_native_agent_prompt_as_the_persona_layer():
    """冻结人物以人设层进入 user（docs/dev/SPEC-persona-prompt.md 第一章「人设层」）：阶段一只填姓名与场景联系人，
    卡片渲染文本与画像元字段都不进提示；系统层没有身份前言。"""
    from tests.integration.test_life_agent_protocol import _observations

    scenario, _ = load_scenario(SCENARIO)
    specification = _agent_specs(
        scenario, persona_compiler=lambda agent: '画像卡片：陈曦，产品经理。')[0]
    agent = LifeAgent()
    agent._id = specification['id']
    agent._name = specification['profile']['name']
    agent._profile = specification['profile']
    agent._enable_memory = False
    request = ReactLoopRequest(0, scenario.start_at, _observations())
    agent.prepare_react_loop(request)
    system, user = (item['content'] for item in agent.build_react_messages(request))
    person = scenario.agents[0]
    persona = json.loads(user)[1]
    assert persona['identity'] == {'name': person.name, 'age': None, 'occupation': None,
                                   'living_arrangement': None}
    assert [(row['person'], row['role']) for row in persona['relationships']] == [
        (contact.salutation, contact.relation) for contact in person.contacts]
    assert all(row['closeness'] is None and row['channels'] == [] for row in persona['relationships'])
    assert '你不是AI助手' not in system and person.name not in system
    assert '画像卡片' not in user
    for stale in ('"profile"', '"trait_source_kind"', '"occupation":"', '"persona_card"'):
        assert stale not in user
    # 冻结画像特征仍在文档层闭合（提示层不泄元字段）。
    assert person.trait_source_kind == 'experiment_assumption'


@pytest.mark.parametrize('field', TRAITS)
def test_persona_traits_cannot_be_silently_omitted(field):
    value = json.loads(SCENARIO.read_text(encoding='utf-8'))
    del value['agents'][0][field]
    with pytest.raises(ValidationError, match=field):
        LifeScenario.model_validate(value)


@pytest.mark.parametrize('field', ('occupation', 'routine_preference',
                                   'consumption_preference', 'risk_preference'))
def test_deleted_template_fields_stay_out_of_documents(field):
    """docs/dev/SPEC-life-realism.md「画像编译」：四个模板字段删除；文档中再次出现即被封闭模型拒绝。"""
    value = json.loads(SCENARIO.read_text(encoding='utf-8'))
    value['agents'][0][field] = 'legacy'
    with pytest.raises(ValidationError):
        LifeScenario.model_validate(value)
