"""立体人格字段（background_story/health_habits/social_style）与真实编译档案测试。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from worldmock.life.scenario import LifeScenario, load_scenario


_FORMAL = Path("scenarios/white-collar-12-weeks.json")
_MULTI = Path("scenarios/multi-person.json")
_NEW_PERSONA_FIELDS = ("background_story", "health_habits", "social_style")


def _payload() -> dict:
    """读取正式场景为每个用例提供独立对象。"""
    return json.loads(_FORMAL.read_text())


def test_short_background_story_is_rejected() -> None:
    """background_story 存在时至少 100 字符，防止退化回四句模板拼接。"""
    shortened = _payload()
    shortened["agents"][0]["background_story"] = "陈曦，32岁，产品经理。" * 4
    assert len(shortened["agents"][0]["background_story"]) < 100
    with pytest.raises(ValidationError, match="at least 100 characters"):
        LifeScenario.model_validate(shortened)


def test_whitespace_padded_background_story_cannot_pass_the_floor() -> None:
    """空白填充不构成立体人格：原始长度超阈值、去空白后不足仍被拒绝。"""
    padded = _payload()
    padded["agents"][0]["background_story"] = "   " + "产品经理。" * 19 + "   "
    raw_length = len(padded["agents"][0]["background_story"])
    assert raw_length >= 100 and len(padded["agents"][0]["background_story"].strip()) < 100
    with pytest.raises(ValidationError, match="at least 100 characters"):
        LifeScenario.model_validate(padded)


def test_new_persona_fields_are_optional_for_backward_compatibility() -> None:
    """缺省新字段的人物文档仍然合法（既有四字段合同不破）。"""
    legacy = _payload()
    for field in _NEW_PERSONA_FIELDS:
        del legacy["agents"][0][field]
    document = LifeScenario.model_validate(legacy)
    agent = document.agents[0]
    assert agent.background_story is None
    assert agent.health_habits is None
    assert agent.social_style is None
    assert agent.occupation  # 既有四字段继续存在


@pytest.mark.parametrize("path", [_FORMAL, _MULTI])
def test_scenarios_carry_rich_personas(path: Path) -> None:
    """两份场景的人物带立体人格字段并通过完整目录组装加载。"""
    scenario, _ = load_scenario(path)
    by_id = {agent.agent_id: agent for agent in scenario.agents}
    chenxi = by_id[1]
    assert chenxi.name == "陈曦"
    assert len(chenxi.background_story) >= 100
    assert chenxi.health_habits and chenxi.social_style
    if 2 in by_id:
        lifan = by_id[2]
        assert lifan.name == "李凡"
        assert len(lifan.background_story) >= 100
        assert lifan.health_habits and lifan.social_style


def test_compiled_profiles_left_the_neutral_flatline() -> None:
    """真实 DeepSeek 编译的大五不再全 4：陈曦外向高、李凡神经质高。"""
    scenario, _ = load_scenario(_MULTI)
    by_id = {agent.agent_id: agent for agent in scenario.agents}
    chenxi, lifan = by_id[1].motivation, by_id[2].motivation
    assert chenxi is not None and lifan is not None
    assert chenxi.big_five["extraversion"] >= 5
    assert lifan.big_five["neuroticism"] >= 5
    for profile in (chenxi, lifan):
        assert set(profile.big_five.values()) != {4}


def test_compiled_weights_are_no_longer_uniform() -> None:
    """动机权重不再均分：陈曦偏娱乐、李凡偏能量与睡眠。"""
    scenario, _ = load_scenario(_MULTI)
    by_id = {agent.agent_id: agent for agent in scenario.agents}
    chenxi = by_id[1].motivation.weights
    lifan = by_id[2].motivation.weights
    assert max(chenxi.values()) - min(chenxi.values()) > 0.05
    assert max(lifan.values()) - min(lifan.values()) > 0.05
    assert chenxi["entertainment_need"] > chenxi["energy_kcal"]
    assert lifan["energy_kcal"] > lifan["entertainment_need"]


def test_motivation_source_hash_matches_background_story() -> None:
    """档案溯源指纹与场景内 background_story 字节一致。"""
    for path in (_FORMAL, _MULTI):
        scenario, _ = load_scenario(path)
        for agent in scenario.agents:
            assert agent.motivation is not None
            digest = hashlib.sha256(agent.background_story.encode("utf-8")).hexdigest()
            assert agent.motivation.source.persona_text_sha256 == digest


def test_same_persona_compiles_to_the_same_profile_across_scenarios() -> None:
    """同一人物（陈曦）在两份场景中的动机档案完全一致。"""
    formal, _ = load_scenario(_FORMAL)
    multi, _ = load_scenario(_MULTI)
    assert formal.agents[0].motivation == multi.agents[0].motivation


def test_rich_persona_fields_reach_the_native_llm_prompt() -> None:
    """新字段随 profile 整包进入 LifeAgent 的 ReAct 用户消息。"""
    from agentsociety2.agent.base import ReactLoopRequest

    from custom.agents.life_agent import LifeAgent
    from worldmock.runtime import _agent_specs

    scenario, _ = load_scenario(_FORMAL)
    specification = _agent_specs(scenario)[0]
    agent = LifeAgent()
    agent._id = specification["id"]
    agent._name = specification["profile"]["name"]
    agent._profile = specification["profile"]
    agent._enable_memory = False
    messages = agent.build_react_messages(
        ReactLoopRequest(0, scenario.start_at, {"LifeEnv": {}}))
    sent_profile = json.loads(messages[1]["content"])["profile"]
    for field in _NEW_PERSONA_FIELDS:
        assert sent_profile[field] == getattr(scenario.agents[0], field)
