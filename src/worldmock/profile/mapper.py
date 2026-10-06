"""@brief 大五刻度 → 世界层动力学的确定性映射（零 LLM，纯函数）。

映射系数全部来自引擎包资源 persona-mapping.toml [world_dynamics.*]
（人工 authored，含 anchored/assumption 溯源标注）；本模块只做读取与线性
组合 + 格点吸附。同（大五, 配置内容）输入必产生逐字节相同的 WorldDynamics。
输出只供引擎内部使用（情绪脉冲、社交账本、内部评分器），不进提示。
"""

from __future__ import annotations

import logging
import tomllib
from importlib.resources import files
from pathlib import Path
from typing import Mapping

from worldmock.profile.models import (
    BIG_FIVE_DIMENSIONS,
    BigFiveProfile,
    WorldDynamics,
    normalized_scores,
    quantize,
)

#: 动力学参数名清单；与 TOML [world_dynamics.*] section 一一对应集中对齐。
DYNAMICS_FIELDS = (
    "mood_pulse_amplitude", "mood_halflife_minutes", "social_need_growth_per_hour",
    "conversation_relief_per_hour", "diversity_weight", "invitation_acceptance",
    "reply_tendency", "present_preference")
#: mapper 单独使用时的溯源标记：数值来自确定性映射，未经 LLM 共识提取。
DETERMINISTIC_SOURCE_MODEL = "persona-mapping v2 (deterministic, zero-LLM)"


def load_mapping_config(path: Path | str | None = None) -> dict:
    """@brief 读取显式路径或引擎包的 v2 映射规则表，缺声明时直接失败。"""
    config_resource = (files("worldmock.data").joinpath("persona-mapping.toml")
                       if path is None else Path(path))
    try:
        with config_resource.open("rb") as handle:
            config = tomllib.load(handle)
        _validate_config(config)
    except (OSError, UnicodeError, tomllib.TOMLDecodeError, AssertionError):
        logging.exception("Failed to load persona mapping rules: %s", config_resource)
        raise
    return config


def _validate_config(config: Mapping) -> None:
    """@brief 结构校验 v2 规则表：版本、四 section、八参数完整与斜率维度合法。"""
    assert config.get("version") == 2, "persona-mapping.toml must declare version = 2"
    required = {"big_five_center", "world_dynamics", "defaults"}
    missing = required - set(config)
    assert not missing, f"persona-mapping.toml missing sections: {sorted(missing)}"
    center = config["big_five_center"]
    assert {"center", "span"} <= set(center), "big_five_center needs center/span"
    dynamics = config["world_dynamics"]
    assert set(dynamics) == set(DYNAMICS_FIELDS), (
        f"world_dynamics must exactly cover {sorted(DYNAMICS_FIELDS)}")
    for name in DYNAMICS_FIELDS:
        section = dynamics[name]
        assert {"intercept", "min", "max", "step"} <= set(section), (
            f"world_dynamics.{name} needs intercept/min/max/step")
        slopes = {key for key in section if key.startswith("slope_")}
        assert all(key[len("slope_"):] in BIG_FIVE_DIMENSIONS for key in slopes), (
            f"world_dynamics.{name} slopes must name big-five dimensions")
    defaults = config["defaults"]
    assert {"sleep_need_hours", "chronotype_midsleep", "time_anchors"} <= set(defaults), (
        "defaults needs sleep_need_hours/chronotype_midsleep/time_anchors")
    assert {"wake", "leave_home", "lunch", "dinner", "bedtime"} <= set(defaults["time_anchors"]), (
        "defaults.time_anchors must cover the five anchors")


def map_world_dynamics(big_five: BigFiveProfile,
                       config: Mapping | None = None) -> WorldDynamics:
    """@brief 纯函数映射：大五 → 世界层动力学（格点全吸附，同输入同输出）。"""
    if config is None:
        config = load_mapping_config()
    center_config = config["big_five_center"]
    scores = normalized_scores(big_five, float(center_config["center"]),
                               float(center_config["span"]))
    values = {name: _linear_grid(config["world_dynamics"][name], scores)
              for name in DYNAMICS_FIELDS}
    return WorldDynamics.model_validate(values)


def _linear_grid(section: Mapping, scores: Mapping[str, float]) -> float:
    """@brief intercept + Σ slope_dim×normalized → 钳制 → step 格点吸附。"""
    value = float(section["intercept"])
    for key, slope in section.items():
        if key.startswith("slope_"):
            value += float(slope) * scores[key[len("slope_"):]]
    return quantize(value, float(section["min"]), float(section["max"]),
                    float(section["step"]))
