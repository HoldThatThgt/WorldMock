"""@brief 编译入口：卡片+大五+动力学组装、旧档案明确报错、确定性。

本文件取代旧 test_cli.py：`worldmock profile` CLI 子命令在 v2 合同下的接线
由 B 波在 cli.py 重接（profile 包不再暴露九参数编译入口），包级行为在此直接
针对 compile_persona 断言。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from worldmock.profile.compile import (
    LegacyTemplateFieldError,
    compile_persona,
)
from worldmock.profile.extractor import (
    ExtractionError,
    PersonaExtraction,
    extract_persona,
)
from worldmock.profile.mapper import load_mapping_config
from worldmock.profile.models import BigFiveProfile, CompiledPersona
from tests.profile.conftest import make_card, make_payload


class _FakeResponse:
    """@brief 固定 200 响应，包装脚本化载荷文本。"""

    def __init__(self, content):
        self.status_code = 200
        self._content = content
        self.text = content

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


class FakeSession:
    """@brief 循环弹出脚本化响应（提取层打桩，可跨多次编译复用）。"""

    def __init__(self, contents):
        self.responses = [_FakeResponse(content) for content in contents]
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append(json)
        item = self.calls and self.responses[(len(self.calls) - 1) % len(self.responses)]
        return item


@pytest.fixture(autouse=True)
def fake_extraction_session(monkeypatch):
    """@brief 编译测试不真实调 API：打桩三次一致的提取会话。"""
    monkeypatch.setenv("DEEPSEEK_NODE_KEYS", "test-key")
    session = FakeSession([make_payload()] * 3)

    def fake_extract(story, n_runs=3, **kwargs):
        return extract_persona(story, n_runs=n_runs, session=session, **kwargs)

    monkeypatch.setattr("worldmock.profile.compile.extract_persona", fake_extract)
    return session


STORY = ("陈曦在张江做产品经理，和伴侣合租。本周推进新版上线，惦记着母亲的体检。"
         "习惯下班后慢跑，睡前读小说，爱看悬疑剧，压力大时拼模型。" * 2)


def test_compile_outputs_v2_contract_shape():
    """@brief 编译产物：schema v2、卡片 + 大五 + 世界层动力学 + 溯源。"""
    compiled = compile_persona(STORY)
    assert isinstance(compiled, CompiledPersona)
    payload = compiled.model_dump()
    assert set(payload) == {"schema_version", "card", "big_five", "world_dynamics",
                            "source", "warnings"}
    assert payload["schema_version"] == 3
    assert payload["card"]["identity"]["occupation"] == "产品经理"
    assert payload["world_dynamics"]["mood_pulse_amplitude"] > 0
    assert list(payload["warnings"]) == []
    assert payload["source"]["prompt_version"] == "card-v3"
    assert payload["source"]["consensus"]["runs"] == 3


def test_compile_is_deterministic_two_runs_byte_identical():
    """@brief 同输入两次编译逐字节相同（确定性映射 + 打桩一致的提取）。"""
    first = compile_persona(STORY).model_dump_json()
    second = compile_persona(STORY).model_dump_json()
    assert first == second


def test_compile_accepts_agent_document_with_story():
    compiled = compile_persona({"background_story": STORY})
    assert compiled.card.identity.age == 32


def test_compile_rejects_legacy_template_fields():
    """@brief 旧字段删除后编译旧档案明确报错（不静默兼容）。"""
    legacy = {"background_story": STORY, "occupation": "产品经理",
              "routine_preference": "早睡早起", "consumption_preference": "精打细算",
              "risk_preference": "稳健"}
    with pytest.raises(LegacyTemplateFieldError,
                       match=r"\['consumption_preference', 'occupation', "
                             r"'risk_preference', 'routine_preference'\]"):
        compile_persona(legacy)
    partial = {"background_story": STORY, "risk_preference": "稳健"}
    with pytest.raises(LegacyTemplateFieldError, match="risk_preference"):
        compile_persona(partial)


def test_compile_rejects_document_without_story():
    from worldmock.errors import WorldMockError
    with pytest.raises(WorldMockError, match="background_story"):
        compile_persona({"name": "陈曦"})
    with pytest.raises(WorldMockError, match="empty"):
        compile_persona("   ")


def test_compile_flags_low_agreement_fields():
    """@brief 提取共识标黄字段列入 warnings。"""
    drift = make_payload(coping="去江边散步")
    from worldmock.profile.compile import compile_persona as compile_entry
    import worldmock.profile.compile as compile_module
    script = iter([make_payload(), drift, make_payload(coping="听老歌")])

    class LazySession:
        """@brief 每次调用按序弹出脚本化响应（共识漂移场景）。"""

        def post(self, url, json=None, headers=None, timeout=None):
            return _FakeResponse(next(script))

    def fake_extract(story, n_runs=3, **kwargs):
        return extract_persona(story, n_runs=n_runs, session=LazySession(), **kwargs)

    original = compile_module.extract_persona
    compile_module.extract_persona = fake_extract
    try:
        compiled = compile_entry(STORY)
    finally:
        compile_module.extract_persona = original
    assert compiled.warnings
    assert "field_agreement_below_two_thirds" in compiled.warnings[0]
    assert "coping" in compiled.warnings[0]


def test_compile_world_dynamics_match_mapping_of_extracted_big_five():
    """@brief 编译产物动力学 == mapper 对提取大五的确定性映射。"""
    from worldmock.profile.mapper import map_world_dynamics
    compiled = compile_persona(STORY)
    expected = map_world_dynamics(compiled.big_five, load_mapping_config())
    assert compiled.world_dynamics == expected


def test_compile_with_prebuilt_extraction_skips_llm():
    """@brief 预取共识（extraction 注入）时不调用提取层。"""
    from worldmock.profile.compile import compile_persona as compile_entry
    import worldmock.profile.compile as compile_module

    def must_not_call(*args, **kwargs):
        raise AssertionError("extraction must not be called when prebuilt")

    original = compile_module.extract_persona
    compile_module.extract_persona = must_not_call
    try:
        extraction = extract_persona(STORY, n_runs=3, session=FakeSession([make_payload()] * 3))
        compiled = compile_entry(STORY, extraction=extraction)
    finally:
        compile_module.extract_persona = original
    assert compiled.card == extraction.card


def _out_of_order(**overrides) -> str:
    """@brief 下班 7 点早于出门 8 点、到家 23 点半晚于就寝 23 点的载荷：共识后两个工作锚点都被降级。"""
    return make_payload(work_anchors={"leave_work": "07:00-07:30", "arrive_home": "23:30-24:00"}, **overrides)


#: _out_of_order 载荷在共识后的两条降级说明（按核对顺序）。
_DEMOTION_NOTES = ("work_anchor_demoted: leave_work start 7 is outside leave_home 8..bedtime 23",
                   "work_anchor_demoted: arrive_home start 23.5 is outside leave_home 8..bedtime 23")


def test_demotion_notes_follow_the_agreement_warning_and_stand_alone_without_it():
    """@brief 真实抽取与编译：下班、到家的降级说明进 CompiledPersona.warnings；有一致性警告时接在它之后，没有一致性警告时
    单独列出；卡片照常编译、两个锚点为 null。"""
    steady = extract_persona(STORY, n_runs=3, session=FakeSession([_out_of_order()] * 3))
    assert steady.warning is False and steady.demotion_notes == _DEMOTION_NOTES
    assert compile_persona(STORY, extraction=steady).warnings == _DEMOTION_NOTES
    drifting = extract_persona(STORY, n_runs=3, session=FakeSession(
        [_out_of_order(), _out_of_order(coping="去江边散步"), _out_of_order(coping="听老歌")]))
    compiled = compile_persona(STORY, extraction=drifting)
    assert compiled.warnings == ("field_agreement_below_two_thirds: ['coping']", *_DEMOTION_NOTES)
    assert (compiled.card.time_anchors.leave_work, compiled.card.time_anchors.arrive_home) == (None, None)
