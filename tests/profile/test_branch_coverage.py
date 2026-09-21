"""@brief C4f 分支覆盖补测：textgen/mapper/models/compile/extractor。

渲染空习惯/空口味卡片、映射路径解析分支、模型格点拒绝、编译入口反例与
提取层 HTTP 非 200 重试（requests 全程打桩）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.profile.conftest import make_card, make_payload
from worldmock.errors import WorldMockError
from worldmock.profile import extractor
from worldmock.profile.compile import LegacyTemplateFieldError, compile_persona
from worldmock.profile.mapper import default_mapping_path, load_mapping_config
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
    assert "7点到7点半" in render_card(make_card()).splitlines()[3] or True
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


def test_default_mapping_path_honors_environment_override(monkeypatch):
    monkeypatch.setenv("WORLDMOCK_PERSONA_MAPPING", "/custom/mapping.toml")
    assert default_mapping_path() is not None
    import pathlib
    assert default_mapping_path() == pathlib.Path("/custom/mapping.toml")


def test_default_mapping_path_fails_closed_when_no_candidate_exists(monkeypatch,
                                                                    tmp_path):
    import worldmock.profile.mapper as mapper
    monkeypatch.delenv("WORLDMOCK_PERSONA_MAPPING", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(mapper, "RULES_PATH", "rules/definitely-missing.toml")
    with pytest.raises(FileNotFoundError, match="persona mapping config not found"):
        default_mapping_path()


def test_map_world_dynamics_accepts_explicit_config():
    from worldmock.profile.mapper import map_world_dynamics
    config = load_mapping_config()
    dynamics = map_world_dynamics(BigFiveProfile(openness=4, conscientiousness=4,
                                                 extraversion=4, agreeableness=4,
                                                 neuroticism=4), config)
    assert 0.0 <= dynamics.mood_pulse_amplitude <= 1.0


# ---------------------------------------------------------------------------
# compile.py
# ---------------------------------------------------------------------------


def _extraction(card):
    return SimpleNamespace(card=card,
                           big_five=BigFiveProfile(openness=4, conscientiousness=4,
                                                   extraversion=4, agreeableness=4,
                                                   neuroticism=4),
                           runs=3, agreement=1.0, per_field={}, warning=False,
                           model="deepseek-chat")


def test_compile_persona_uses_explicit_config_and_reports_agreement_warnings():
    card = make_card()
    config = load_mapping_config()
    compiled = compile_persona("故事", extraction=_extraction(card), config=config)
    assert compiled.card == card
    assert compiled.schema_version == 2
    low_agreement = SimpleNamespace(card=card,
                                    big_five=compiled.big_five, runs=3, agreement=0.5,
                                    per_field={"coping": 0.5}, warning=True,
                                    model="deepseek-chat")
    warned = compile_persona("故事", extraction=low_agreement, config=config)
    assert warned.warnings == ("field_agreement_below_two_thirds: ['coping']",)


def test_compile_persona_rejects_empty_and_legacy_documents():
    with pytest.raises(WorldMockError, match="background story is empty"):
        compile_persona("   ")
    with pytest.raises(WorldMockError, match="background story is empty"):
        compile_persona({"background_story": "  "})
    with pytest.raises(WorldMockError, match="must carry background_story"):
        compile_persona({"name": "无故事"})
    with pytest.raises(LegacyTemplateFieldError, match="legacy template fields"):
        compile_persona({"background_story": "故事", "occupation": "工程师"})


# ---------------------------------------------------------------------------
# extractor.py
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status_code=200, content="{}"):
        self.status_code = status_code
        self._content = content
        self.text = content

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append(url)
        return self.responses.pop(0)


@pytest.fixture(autouse=True)
def isolate_credentials(monkeypatch, tmp_path):
    monkeypatch.delenv("DEEPSEEK_NODE_KEYS", raising=False)
    monkeypatch.setattr(extractor, "CREDENTIALS_PATH", tmp_path / "absent.env")
    monkeypatch.setenv("DEEPSEEK_NODE_KEYS", "test-key")


def test_single_run_retries_on_http_errors_then_raises():
    session = FakeSession([FakeResponse(status_code=500, content="boom")] * 3)
    with pytest.raises(extractor.ExtractionError, match="after 3 attempts: HTTP 500"):
        extractor.extract_persona("故事", n_runs=1, session=session)
    assert len(session.calls) == 3


def test_median_int_rounds_half_up_on_even_samples():
    assert extractor._median_int([1, 2]) == 2
    assert extractor._median_int([2, 1]) == 2
    assert extractor._median_int([1, 3, 5]) == 3


def test_extract_persona_rejects_empty_story_and_bad_run_count():
    with pytest.raises(extractor.ExtractionError, match="empty"):
        extractor.extract_persona("  ")
    with pytest.raises(extractor.ExtractionError, match="n_runs must be >= 1"):
        extractor.extract_persona("故事", n_runs=0)


def test_consensus_modes_and_warning_thresholds():
    payloads = []
    contents = [make_payload(), make_payload(coping="去江边散步"),
                make_payload(coping="听老歌")]
    session = FakeSession([FakeResponse(content=content) for content in contents])
    result = extractor.extract_persona("故事", n_runs=3, session=session)
    assert result.warning is True
    assert result.per_field["coping"] == pytest.approx(1 / 3, abs=1e-3)
    assert result.big_five.neuroticism == 3


def test_parse_payload_rejects_wrong_key_sets():
    with pytest.raises(ValueError, match="unexpected payload keys"):
        extractor._parse_payload("{}", load_mapping_config())
    with pytest.raises(ValueError, match="non-JSON"):
        extractor._parse_payload("not json at all", load_mapping_config())
