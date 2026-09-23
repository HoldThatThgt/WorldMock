import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://openrouter.ai/api/v1")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.agent.base import ReactLoopRequest
from custom.agents.life_agent import LifeAgent
from worldmock.life.scenario import LifeScenario, load_scenario
from worldmock.runtime import _agent_specs


SCENARIO = Path(__file__).resolve().parents[2] / 'scenarios/white-collar-12-weeks.json'
TRAITS = ('trait_source_kind',)


def _wrapped_observation(scenario) -> list[dict]:
    """构造三段消息合同需要的最小结构化观察（scene + facts 两层投影）。"""
    current_time = scenario.start_at.isoformat()
    data = {
        'scene': {'current_time': current_time, 'location': '办公室'},
        'facts': {'current_time': current_time, 'location': '办公室',
                  'people_here': [], 'contacts': [], 'inbox': [], 'tasks': [],
                  'progress': {'sessions': [], 'results': [], 'alarms': []}},
    }
    return [{'turn': 0, 'action': 'observe', 'ok': True, 'data': data}]


def test_frozen_persona_traits_reach_the_actual_native_agent_prompt():
    """冻结画像实质以 persona_card 渲染文本进入三段消息（docs/dev/SPEC-life-runtime.md「观察投影与提示合同」）。"""
    scenario, _ = load_scenario(SCENARIO)
    specification = _agent_specs(
        scenario, persona_compiler=lambda agent: '画像卡片：陈曦，产品经理。')[0]
    agent = LifeAgent()
    agent._id = specification['id']
    agent._name = specification['profile']['name']
    agent._profile = specification['profile']
    agent._enable_memory = False
    messages = agent.build_react_messages(
        ReactLoopRequest(0, scenario.start_at, _wrapped_observation(scenario)))
    system, user = messages[0]['content'], messages[1]['content']
    person = scenario.agents[0]
    # 身份前言以人物为主语；画像编译产出的 persona_card 渲染文本原样进入
    # user 消息首段，不再有 JSON 画像整包。
    assert person.name in system and '你不是AI助手' in system
    assert '画像卡片：陈曦，产品经理。' in user
    assert '你在办公室' in user and '"location":"办公室"' in user
    for stale in ('"profile"', '"trait_source_kind"', '"occupation"'):
        assert stale not in user
    # 冻结画像特征仍在文档层闭合（提示层只渲染实质文本，不泄元字段）。
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
