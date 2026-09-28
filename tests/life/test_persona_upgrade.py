"""立体人格字段（background_story/health_habits/social_style）与真实编译档案测试。"""

from __future__ import annotations

import hashlib
import json
import copy
from pathlib import Path

import pytest
from pydantic import ValidationError

from worldmock.life.scenario import LifeScenario, load_scenario


_FORMAL = Path("scenarios/white-collar-12-weeks.json")
_MULTI = Path("scenarios/multi-person.json")
_NEW_PERSONA_FIELDS = ("background_story", "health_habits", "social_style")


def _payload() -> dict:
    """读取正式场景为每个用例提供独立对象。"""
    return json.loads(_FORMAL.read_text(encoding="utf-8"))


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


def test_new_persona_fields_are_optional_and_template_fields_are_gone() -> None:
    """缺省新字段的人物文档合法；修订合同删除的四个模板字段不再存在。"""
    legacy = _payload()
    for field in _NEW_PERSONA_FIELDS:
        del legacy["agents"][0][field]
    for field in ("occupation", "routine_preference", "consumption_preference",
                  "risk_preference"):
        legacy["agents"][0].pop(field, None)  # 场景文件已随合同清理
    document = LifeScenario.model_validate(legacy)
    agent = document.agents[0]
    assert agent.background_story is None
    assert agent.health_habits is None
    assert agent.social_style is None
    assert not hasattr(agent, "occupation")  # 模板字段已删除（docs/dev/SPEC-life-realism.md「画像编译」）


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


def test_persona_layer_from_the_scenario_profile_reaches_the_native_llm_prompt() -> None:
    """人设层（docs/dev/SPEC-persona-prompt.md 第一章「人设层」）：有编译卡片的人物取卡片的身份、作息与关系，场景联系人
    随后；没有卡片的人物只填姓名；卡片渲染文本、当下处境与经历、健康习惯、社交风格等画像字段都不进提示。"""
    from agentsociety2.agent.base import ReactLoopRequest

    from custom.agents.life_agent import LifeAgent
    from tests.integration.test_life_agent_protocol import _observations
    from worldmock.runtime import _agent_specs

    scenario, _ = load_scenario(_FORMAL)

    def persona(profile: dict) -> tuple[dict, str]:
        agent = LifeAgent()
        agent._id = profile["id"]
        agent._name = profile["name"]
        agent._profile = profile
        agent._enable_memory = False
        request = ReactLoopRequest(0, scenario.start_at, copy.deepcopy(_observations()))
        agent.prepare_react_loop(request)
        user = agent.build_react_messages(request)[1]["content"]
        return json.loads(user)[1], user

    from tests.profile.conftest import make_card
    from worldmock.profile.textgen import render_card

    card = make_card()
    carded, carded_user = persona(_agent_specs(scenario, persona_compiler=lambda agent: card)[0]["profile"])
    plain, plain_user = persona(_agent_specs(scenario, persona_compiler=lambda agent: None)[0]["profile"])
    assert carded["identity"] == {"name": scenario.agents[0].name, "age": 32, "occupation": "产品经理",
                                  "living_arrangement": "与伴侣合租两居室"}
    assert carded["rhythm"]["wake"] == "7点到7点半" and plain["rhythm"] is None
    assert plain["identity"] == {"name": scenario.agents[0].name, "age": None, "occupation": None,
                                 "living_arrangement": None}
    contacts = [contact.salutation for contact in scenario.agents[0].contacts]
    assert [row["person"] for row in carded["relationships"]] == ["母亲", "老周", *contacts]
    assert [row["person"] for row in plain["relationships"]] == contacts
    assert render_card(card) not in carded_user and card.situation.cares_about not in carded_user
    for field in _NEW_PERSONA_FIELDS:
        assert getattr(scenario.agents[0], field) not in plain_user
    assert '"profile"' not in carded_user and '"profile"' not in plain_user


@pytest.mark.asyncio
async def test_memory_view_identity_uses_the_card_text_or_the_scenario_persona_fields(tmp_path) -> None:
    """记忆巩固与反思读取的人物段（工作区 MEMORY.md 的「身份」）：新建运行编译出卡片的人物取卡片渲染文本；没有卡片的人物
    由姓名与场景声明的经历、健康习惯、社交风格逐行组装。两者都经真实 LifeAgent 与 WorldMock 记忆运行时写出。"""
    from agentsociety2.agent.base import ReactLoopRequest
    from agentsociety2.agent.memory_runtime import MemoryRuntimeConfig

    from custom.agents.life_agent import LifeAgent
    from tests.integration.test_life_agent_protocol import _observations
    from tests.profile.conftest import make_card
    from worldmock.profile.textgen import render_card
    from worldmock.runtime import _agent_specs

    scenario, _ = load_scenario(_FORMAL)

    async def identity(profile: dict, workspace: Path) -> str:
        """按一份人物档案走一次观察准备与记忆步，读回 MEMORY.md「身份」区段。"""
        agent = LifeAgent()
        agent._id = profile["id"]
        agent._name = profile["name"]
        agent._profile = profile
        agent._enable_memory = False
        agent.prepare_react_loop(ReactLoopRequest(0, scenario.start_at, copy.deepcopy(_observations())))
        runtime = agent._new_memory_runtime(MemoryRuntimeConfig())
        runtime.ensure_store(workspace)
        await runtime.after_step(tick=0, t=scenario.start_at, step_count=1, finish_memories=[])
        view = runtime.store.memory_md_path.read_text(encoding="utf-8")
        return view.split("## 身份\n", 1)[1].split("\n\n## 已核验经历", 1)[0]

    def quoted(text: str) -> str:
        """视图把人物段逐行封入 Markdown 引用。"""
        return "\n".join("> " + line for line in text.splitlines())

    card = make_card()
    carded = await identity(_agent_specs(scenario, persona_compiler=lambda agent: card)[0]["profile"],
                            tmp_path / "carded")
    plain = await identity(_agent_specs(scenario, persona_compiler=lambda agent: None)[0]["profile"],
                           tmp_path / "plain")
    person = scenario.agents[0]
    assert carded == quoted(render_card(card).strip())
    assert plain == quoted("\n".join((f"你是{person.name}。", f"你的经历：{person.background_story.strip()}",
                                      f"你的健康习惯：{person.health_habits.strip()}",
                                      f"你的社交风格：{person.social_style.strip()}")))


# ---------------------------------------------------------------------------
# 画像编译 v2（PersonaCard 合同，docs/dev/SPEC-life-realism.md「画像编译」）：以下用例自包含（打桩提取层，
# 直接读场景 JSON 取 background_story，不经 load_scenario 的语料清单完整性门禁）。
# ---------------------------------------------------------------------------

class _FakeResponse:
    """@brief 固定 200 响应，包装脚本化载荷文本。"""

    def __init__(self, content):
        self.status_code = 200
        self._content = content
        self.text = content

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


class _CyclingSession:
    """@brief 循环弹出脚本化响应（三次一致性提取打桩，不真实调 API）。"""

    def __init__(self, content):
        self._response = _FakeResponse(content)
        self.calls = 0

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls += 1
        return self._response


def _compiled_from_story(story: str):
    """@brief 以打桩会话编译真实 background_story（零真实 API 调用）。"""
    from worldmock.profile.compile import compile_persona
    from worldmock.profile.extractor import extract_persona
    from tests.profile.conftest import make_payload
    session = _CyclingSession(make_payload())

    def fake_extract(text, n_runs=3, **kwargs):
        return extract_persona(text, n_runs=n_runs, api_key="test-key", session=session, **kwargs)

    import worldmock.profile.compile as compile_module
    original = compile_module.extract_persona
    compile_module.extract_persona = fake_extract
    try:
        return compile_persona(story)
    finally:
        compile_module.extract_persona = original


def test_v2_compile_rejects_old_template_agent_document() -> None:
    """旧字段删除后编译旧档案明确报错（不静默兼容）。"""
    from worldmock.profile.compile import LegacyTemplateFieldError, compile_persona
    story = _payload()["agents"][0]["background_story"]
    legacy = {"background_story": story, "occupation": "产品经理",
              "routine_preference": "早睡早起", "consumption_preference": "精打细算",
              "risk_preference": "稳健"}
    with pytest.raises(LegacyTemplateFieldError, match="legacy template fields"):
        compile_persona(legacy)


def test_v2_contract_rejects_old_nine_parameter_profile() -> None:
    """旧 v1 九参数动机档案 JSON 被 schema v2 明确拒绝。"""
    from worldmock.profile.models import CompiledPersona
    old_payload = {
        "schema_version": 1,
        "big_five": {"openness": 4, "conscientiousness": 4, "extraversion": 4,
                     "agreeableness": 4, "neuroticism": 4},
        "weights": {"money_fen": 0.2, "energy_kcal": 0.2, "sleep_deficit_hours": 0.2,
                    "entertainment_need": 0.2, "goal_task": 0.2},
        "tau": 2.0, "beta_present_bias": 0.75,
        "loss_aversion": {"money_fen": 2.0, "energy_kcal": 1.5},
        "comfort": {"balance_fen": 15000, "energy_kcal": 1000},
        "aspiration": {"daily_effective_work_seconds": 18000},
        "emotion_sensitivity": 0.5, "attention_prior": {}, "source": None,
    }
    with pytest.raises(ValidationError, match="schema_version"):
        CompiledPersona.model_validate(old_payload)


def test_real_background_story_compiles_to_v2_card() -> None:
    """真实场景 background_story 走 v2 编译：卡片 + 大五 + 世界层动力学齐全。"""
    compiled = _compiled_from_story(_payload()["agents"][0]["background_story"])
    assert compiled.schema_version == 2
    assert compiled.card.identity.age > 0
    assert compiled.card.relationships and compiled.card.time_anchors.wake
    assert 7.0 <= compiled.card.sleep_need_hours <= 9.0
    assert compiled.card.habits and compiled.card.tastes
    assert compiled.world_dynamics.mood_pulse_amplitude > 0
    assert compiled.source.prompt_version == "card-v1"


def test_v2_compilation_is_deterministic_for_real_story() -> None:
    """同输入两次编译逐字节相同（确定性映射）。"""
    story = _payload()["agents"][0]["background_story"]
    assert _compiled_from_story(story).model_dump_json() == \
        _compiled_from_story(story).model_dump_json()


def test_card_render_never_leaks_big_five_or_random_floats() -> None:
    """大五与随机浮点不出现在渲染文本（黑名单断言）。"""
    import re

    from worldmock.profile.textgen import count_hanzi, render_card
    compiled = _compiled_from_story(_payload()["agents"][0]["background_story"])
    text = render_card(compiled.card)
    for term in ("开放性", "尽责性", "外向性", "宜人性", "神经质", "tau", "beta",
                 "λ", "权重", "%", "weights"):
        assert term not in text
    assert not re.search(r"\d+\.\d{2,}", text)
    assert count_hanzi(text) <= 600
