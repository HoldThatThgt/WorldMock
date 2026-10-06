
from tests.data_paths import ENVIRONMENT_ROOT
import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com/beta")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.agent.base import ReactLoopRequest
from custom.agents.life_agent import LifeAgent
from worldmock.life.scenario import LifeScenario
from worldmock.life.world_setup import load_scenario
from worldmock.run_setup import _agent_specs


SCENARIO = ENVIRONMENT_ROOT / 'scenarios/single-person.json'
TRAITS = ('trait_source_kind',)


def test_frozen_persona_identity_reaches_the_native_agent_prompt_as_the_persona_layer():
    """冻结人物以人设层进入 user（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第一章「人设层」）：身份取自结构化卡片，卡片关系在前、
    对不上的场景联系人随后（亲密度 null、渠道为空）；卡片渲染文本、当下处境与画像元字段都不进提示；系统层没有身份前言。"""
    from tests.integration.test_life_agent_protocol import _observations
    from tests.profile.conftest import make_card
    from worldmock.profile.textgen import render_card

    scenario, _ = load_scenario(SCENARIO)
    card = make_card()
    specification = _agent_specs(scenario, persona_compiler=lambda agent: card)[0]
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
    assert persona['identity'] == {'name': person.name, 'age': 32, 'occupation': '产品经理',
                                   'living_arrangement': '与伴侣合租两居室'}
    assert [(row['person'], row['role']) for row in persona['relationships']] == [
        ('母亲', '家人'), ('老周', '老友'), *((contact.salutation, contact.relation) for contact in person.contacts)]
    assert all(row['closeness'] is None and row['channels'] == [] for row in persona['relationships'][2:])
    assert '你不是AI助手' not in system and person.name not in system
    assert render_card(card) not in user and card.situation.busy_this_week not in user
    for stale in ('"profile"', '"trait_source_kind"', '"persona_card"', '"persona_card_struct"', '"situation"'):
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
    """docs/dev/SPEC-life-realism.md#spec-life-realism-特性规格人物决策与画像「画像编译」：四个模板字段删除；文档中再次出现即被封闭模型拒绝。"""
    value = json.loads(SCENARIO.read_text(encoding='utf-8'))
    value['agents'][0][field] = 'legacy'
    with pytest.raises(ValidationError):
        LifeScenario.model_validate(value)
