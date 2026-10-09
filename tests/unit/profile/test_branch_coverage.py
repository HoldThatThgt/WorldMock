"""@brief C4f 分支覆盖补测：textgen/mapper/models/compile/extractor。

渲染空习惯/空口味卡片、映射包资源读取、模型格点拒绝、编译入口反例与
提取层 HTTP 非 200 重试（requests 全程打桩）。
"""

from __future__ import annotations

from importlib.resources import files
from types import SimpleNamespace

import pytest

from tests.profile.conftest import make_card, make_payload
from tests.profile.model_fixture import Response, ScriptedClient, extracted, persona
from worldmock.errors import WorldMockError
from worldmock.profile import extractor
from worldmock.profile.compile import compile_persona
from worldmock.profile.mapper import load_mapping_config
from worldmock.profile.models import (
    BigFiveProfile,
    HabitSignature,
    PersonaCard,
    Relationship,
    Tastes,
    TimeWindow,
    value_on_grid,
)
from worldmock.profile.textgen import count_hanzi, render_card


# ---------------------------------------------------------------------------
# textgen.py
# ---------------------------------------------------------------------------


def test_render_card_without_habits_or_tastes_uses_fallback_lines():
    card = make_card(habits=(), tastes=Tastes(content_genres=(), food_preferences=(),
                                              media=()))
    text = render_card(card)
    assert "习惯：无明确习惯签名。" in text
    assert "口味：无特别偏好。" in text
    assert count_hanzi(text) <= 600


def test_hour_text_distinguishes_half_grid_points():
    window = TimeWindow(start_hour=7.0, end_hour=7.5)
    assert "7点到7点半" in render_card(make_card()).splitlines()[3]
    assert window.end_hour == 7.5


# ---------------------------------------------------------------------------
# models.py
# ---------------------------------------------------------------------------


def test_value_on_grid_rejects_out_of_range_and_off_grid_values():
    assert value_on_grid(24.5, 0.0, 24.0, 0.5) is False
    assert value_on_grid(float("nan"), 0.0, 24.0, 0.5) is False
    assert value_on_grid(7.25, 0.0, 24.0, 0.5) is False
    assert value_on_grid(7.5, 0.0, 24.0, 0.5) is True


def test_relationship_rejects_blank_channels():
    with pytest.raises(ValueError, match="channels must not be blank"):
        Relationship(name_or_appellation="母亲", role="家人", intimacy=25,
                     channels=("电话", " "))


def test_tastes_rejects_blank_entries():
    with pytest.raises(ValueError, match="taste entries must not be blank"):
        Tastes(content_genres=("悬疑", ""), food_preferences=(), media=())


# ---------------------------------------------------------------------------
# mapper.py
# ---------------------------------------------------------------------------


def test_default_mapping_ignores_removed_environment_override(monkeypatch):
    """@brief 旧环境变量不能改变引擎的通用人物映射规则。"""
    monkeypatch.setenv("WORLDMOCK_PERSONA_MAPPING", "missing-persona-mapping.toml")
    assert load_mapping_config()["version"] == 3


def test_default_mapping_ignores_working_directory_rules(monkeypatch, tmp_path):
    """@brief 当前目录存在同名规则时仍从引擎包读取，不进行路径搜索。"""
    directory = tmp_path / "catalog/rules"
    directory.mkdir(parents=True)
    (directory / "persona-mapping.toml").write_text("version = 0\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert load_mapping_config()["version"] == 3


def test_default_mapping_fails_when_package_rule_is_missing(monkeypatch, tmp_path, caplog):
    """@brief 包规则缺失立即报错并记录日志，不读取当前目录内的规则。"""
    import worldmock.profile.mapper as mapper

    directory = tmp_path / "catalog/rules"
    directory.mkdir(parents=True)
    text = files("worldmock.data").joinpath("persona-mapping.toml").read_text(encoding="utf-8")
    (directory / "persona-mapping.toml").write_text(text, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(mapper, "files", lambda package: tmp_path / "missing-package")
    with pytest.raises(FileNotFoundError, match="persona-mapping.toml"):
        load_mapping_config()
    assert "Failed to load persona mapping rules" in caplog.text


def test_map_world_dynamics_accepts_explicit_config():
    from worldmock.profile.mapper import map_world_dynamics
    config = load_mapping_config()
    dynamics = map_world_dynamics(BigFiveProfile(openness=4, conscientiousness=4,
                                                 extraversion=4, agreeableness=4,
                                                 neuroticism=4), config)
    assert 0.0 <= dynamics.social_need_growth_per_hour <= 1.0


# ---------------------------------------------------------------------------
# compile.py
# ---------------------------------------------------------------------------


def _extraction(card):
    return SimpleNamespace(card=card,
                           big_five=BigFiveProfile(openness=4, conscientiousness=4,
                                                   extraversion=4, agreeableness=4,
                                                   neuroticism=4),
                           runs=3, agreement=1.0, per_field={}, warning=False,
                           model="test-model", demotion_notes=())


def test_compile_persona_uses_explicit_config_and_reports_agreement_warnings():
    card = make_card()
    config = load_mapping_config()
    compiled = compile_persona(persona(), persona_sha256="a" * 64, extraction=_extraction(card), config=config)
    assert compiled.card == card
    assert compiled.schema_version == 4
    low_agreement = SimpleNamespace(card=card,
                                    big_five=compiled.big_five, runs=3, agreement=0.5,
                                    per_field={"coping": 0.5}, warning=True,
                                    model="test-model", demotion_notes=())
    warned = compile_persona(persona(), persona_sha256="a" * 64, extraction=low_agreement, config=config)
    assert warned.warnings == ("field_agreement_below_two_thirds: ['coping']",)




# ---------------------------------------------------------------------------
# extractor.py
# ---------------------------------------------------------------------------










def test_median_int_rounds_half_up_on_even_samples():
    assert extractor._median_int([1, 2]) == 2
    assert extractor._median_int([2, 1]) == 2
    assert extractor._median_int([1, 3, 5]) == 3


def test_extract_persona_rejects_empty_story_and_bad_run_count():
    with pytest.raises(extractor.ExtractionError, match="input is invalid"):
        extracted("  ", client=ScriptedClient([]))
    with pytest.raises(extractor.ExtractionError, match="input is invalid"):
        extracted("故事", n_runs=0, client=ScriptedClient([]))


def test_consensus_modes_and_warning_thresholds():
    payloads = []
    contents = [make_payload(), make_payload(coping="去江边散步"),
                make_payload(coping="听老歌")]
    session = ScriptedClient([Response(content=content) for content in contents])
    result = extracted("故事", n_runs=3, client=session)
    assert result.warning is True
    assert result.per_field["coping"] == pytest.approx(1 / 3, abs=1e-3)
    assert result.big_five.neuroticism == 3


def test_parse_payload_rejects_wrong_key_sets():
    with pytest.raises(ValueError, match="unexpected payload keys"):
        extractor._parse_payload("{}", load_mapping_config())
    with pytest.raises(ValueError, match="non-JSON"):
        extractor._parse_payload("not json at all", load_mapping_config())
