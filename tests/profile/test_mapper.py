"""@brief 世界层动力学映射：确定性（同输入两次逐字节相同）、方向锚定与配置校验。"""

from __future__ import annotations

from importlib.resources import files

import pytest

from worldmock.profile.mapper import (
    load_mapping_config,
    map_world_dynamics,
)
from worldmock.profile.models import BigFiveProfile


def make_big_five(**scores) -> BigFiveProfile:
    """@brief 大五工厂：默认全 4（刻度中心）。"""
    defaults = {"openness": 4, "conscientiousness": 4, "extraversion": 4,
                "agreeableness": 4, "neuroticism": 4}
    return BigFiveProfile(**{**defaults, **scores})


def test_mapping_is_deterministic_two_runs_byte_identical():
    """@brief 纯函数+格点：同输入两次编译逐字节相同（零 LLM、零随机）。"""
    big_five = make_big_five(openness=6, conscientiousness=2, extraversion=5,
                             agreeableness=3, neuroticism=6)
    config = load_mapping_config()
    first = map_world_dynamics(big_five, config).model_dump_json()
    second = map_world_dynamics(big_five, config).model_dump_json()
    assert first == second


def test_dynamics_fields_cover_the_five_trait_routes():
    """@brief 五条大五路由齐全：神经质/外向性/开放性/宜人性/尽责性各就其位。"""
    dynamics = map_world_dynamics(make_big_five()).model_dump()
    assert set(dynamics) == {
        "mood_pulse_amplitude", "mood_halflife_minutes",
        "social_need_growth_per_hour", "conversation_relief_per_hour",
        "diversity_weight", "invitation_acceptance", "reply_tendency",
        "present_preference"}


def test_direction_anchors():
    """@brief 方向锚定：神经质->脉冲幅度升/半衰期降；外向->社交升；开放->多样性；
    宜人->邀约与回复升；尽责->现时偏好降。"""
    config = load_mapping_config()
    high_n = map_world_dynamics(make_big_five(neuroticism=7), config)
    low_n = map_world_dynamics(make_big_five(neuroticism=1), config)
    assert high_n.mood_pulse_amplitude > low_n.mood_pulse_amplitude
    assert high_n.mood_halflife_minutes < low_n.mood_halflife_minutes
    high_e = map_world_dynamics(make_big_five(extraversion=7), config)
    low_e = map_world_dynamics(make_big_five(extraversion=1), config)
    assert high_e.social_need_growth_per_hour > low_e.social_need_growth_per_hour
    assert high_e.conversation_relief_per_hour > low_e.conversation_relief_per_hour
    assert map_world_dynamics(make_big_five(openness=7), config).diversity_weight > \
        map_world_dynamics(make_big_five(openness=1), config).diversity_weight
    high_a = map_world_dynamics(make_big_five(agreeableness=7), config)
    low_a = map_world_dynamics(make_big_five(agreeableness=1), config)
    assert high_a.invitation_acceptance > low_a.invitation_acceptance
    assert high_a.reply_tendency > low_a.reply_tendency
    assert map_world_dynamics(make_big_five(conscientiousness=7), config).present_preference < \
        map_world_dynamics(make_big_five(conscientiousness=1), config).present_preference


def test_extremes_stay_in_bounds():
    """@brief 全 1 / 全 4 / 全 7 / 四角混合全部落在模型区间（格点由模型层兜底）。"""
    config = load_mapping_config()
    for scores in (
        {"openness": 1, "conscientiousness": 1, "extraversion": 1, "agreeableness": 1, "neuroticism": 1},
        {"openness": 4, "conscientiousness": 4, "extraversion": 4, "agreeableness": 4, "neuroticism": 4},
        {"openness": 7, "conscientiousness": 7, "extraversion": 7, "agreeableness": 7, "neuroticism": 7},
        {"openness": 7, "conscientiousness": 1, "extraversion": 1, "agreeableness": 7, "neuroticism": 7},
    ):
        map_world_dynamics(make_big_five(**scores), config).model_validate(
            map_world_dynamics(make_big_five(**scores), config).model_dump())


def test_default_config_reads_packaged_rules():
    """@brief 默认动力学规则作为引擎包资源发布。"""
    resource = files("worldmock.data").joinpath("persona-mapping.toml")
    assert resource.name == "persona-mapping.toml" and resource.is_file()
    config = load_mapping_config()
    assert config["version"] == 2
    assert {"sleep_need_hours", "chronotype_midsleep"} <= set(config["defaults"])


def test_config_validation_rejects_missing_or_wrong_sections(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text("version = 2\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="missing sections"):
        load_mapping_config(bad)
    stale = tmp_path / "stale.toml"
    stale.write_text("version = 1\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="version = 2"):
        load_mapping_config(stale)


def test_config_rejects_wrong_dynamics_field_set(tmp_path):
    """@brief [world_dynamics] 键集必须精确等于八参数清单：少 section 即断言失败。"""
    import re
    full = files("worldmock.data").joinpath("persona-mapping.toml").read_text(encoding="utf-8")
    partial = tmp_path / "partial.toml"
    partial.write_text(
        re.sub(r"\[world_dynamics\.present_preference\][^\[]*", "", full),
        encoding="utf-8")
    with pytest.raises(AssertionError, match="world_dynamics must exactly cover"):
        load_mapping_config(partial)


def test_config_rejects_unknown_dimension_slope(tmp_path):
    """@brief slope 键必须命名大五维度：拼错维度名加载期失败。"""
    full = files("worldmock.data").joinpath("persona-mapping.toml").read_text(encoding="utf-8")
    tampered = tmp_path / "tampered.toml"
    tampered.write_text(full.replace("slope_neuroticism = 0.4",
                                     "slope_neuroticism = 0.4\nslope_greed = 0.1", 1),
                        encoding="utf-8")
    with pytest.raises(AssertionError, match="must name big-five dimensions"):
        load_mapping_config(tampered)
