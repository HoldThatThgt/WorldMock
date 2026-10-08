"""立体人格字段（background_story/health_habits/social_style）与真实编译档案测试。"""

from __future__ import annotations

from tests.data_paths import ENVIRONMENT_ROOT

import json
import copy
from pathlib import Path

import pytest
from pydantic import ValidationError

from worldmock.life.scenario import LifeScenario
from worldmock.life.world_setup import load_scenario


_FORMAL = ENVIRONMENT_ROOT / "scenarios/single-person.json"
_NEW_PERSONA_FIELDS = ("background_story", "health_habits", "social_style")
#: 人工人格夹具明确声明叙事字段，不把最小环境未声明的字段当成正式人物证据。
_PERSONA = {
    "background_story": ("陈曦今年三十二岁，是一家小公司的产品经理，与伴侣合租两居室。"
                         "他每天整理项目进展，喜欢把会议重点记在手机里，晚上偶尔看视频放松。"
                         "他与母亲保持联系，也会和老朋友交流近况。周末他习惯在附近公园散步，"
                         "最近希望让工作与休息更规律，避免因为临时消息打乱吃饭和睡眠。"),
    "health_habits": "每天散步，按时吃饭，尽量在十一点前睡觉。",
    "social_style": "与熟悉的人直接交流，收到消息后先看清对方的意思。",
}


def _payload() -> dict:
    """读取当前人工场景并声明本模块需要的人格字段，每个用例取得独立对象。"""
    payload = json.loads(_FORMAL.read_text(encoding="utf-8"))
    payload["agents"][0].update(_PERSONA)
    return payload


def _persona_scenario():
    """@brief 当前运行场景使用经过人物模型校验的独立叙事档案，不改变共享环境。"""
    scenario, _ = load_scenario(_FORMAL)
    person = type(scenario.agents[0]).model_validate({**scenario.agents[0].model_dump(mode="python"), **_PERSONA})
    return scenario.model_copy(update={"agents": (person, *scenario.agents[1:])})


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
    assert not hasattr(agent, "occupation")  # 模板字段已删除（docs/design/06-agent.md「画像编译」）


def test_persona_layer_from_the_scenario_profile_reaches_the_native_llm_prompt() -> None:
    """人设层（docs/design/06-agent.md 第一章「人设层」）：有编译卡片的人物取卡片的身份、作息与关系，场景联系人
    随后；没有卡片的人物只填姓名；卡片渲染文本、当下处境与经历、健康习惯、社交风格等画像字段都不进提示。"""
    from agentsociety2.agent.base import ReactLoopRequest

    from custom.agents.life_agent import LifeAgent
    from tests.integration.runtime.test_life_agent_protocol import _observations
    from worldmock.run_setup import _agent_specs

    scenario = _persona_scenario()

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
    """记忆巩固与反思读取的人物段（工作区 MEMORY.md 的「身份」）：新建运行编译出卡片的人物取卡片渲染文本（卡片先经场景
    作息覆盖，docs/design/06-agent.md ）；没有卡片的人物由姓名与场景声明的经历、健康习惯、社交风格逐行组装。
    两者都经真实 LifeAgent 与 WorldMock 记忆运行时写出。"""
    from agentsociety2.agent.base import ReactLoopRequest
    from agentsociety2.agent.memory_runtime import MemoryRuntimeConfig

    from custom.agents.life_agent import LifeAgent
    from tests.integration.runtime.test_life_agent_protocol import _observations
    from tests.profile.conftest import make_card
    from worldmock.profile.injection import apply_scenario_rhythm
    from worldmock.profile.textgen import render_card
    from worldmock.run_setup import _agent_specs

    scenario = _persona_scenario()

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
    assert carded == quoted(render_card(apply_scenario_rhythm(card, person).card).strip())
    assert plain == quoted("\n".join((f"你是{person.name}。", f"你的经历：{person.background_story.strip()}",
                                      f"你的健康习惯：{person.health_habits.strip()}",
                                      f"你的社交风格：{person.social_style.strip()}")))


# ---------------------------------------------------------------------------
# 画像编译 v2（PersonaCard 合同，docs/design/06-agent.md「画像编译」）：以下用例自包含（打桩提取层，
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
    assert compiled.schema_version == 3
    assert compiled.card.identity.age > 0
    assert compiled.card.relationships and compiled.card.time_anchors.wake
    assert 7.0 <= compiled.card.sleep_need_hours <= 9.0
    assert compiled.card.habits and compiled.card.tastes
    assert compiled.world_dynamics.mood_pulse_amplitude > 0
    assert compiled.source.prompt_version == "card-v3"


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
