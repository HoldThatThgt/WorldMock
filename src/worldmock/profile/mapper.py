"""@brief 大五刻度 → 九参数动机档案的确定性映射（零 LLM，纯函数）。

映射系数全部来自 catalog/rules/persona-mapping.toml（人工 authored，含
anchored/assumption 溯源标注）；本模块只做读取与线性组合 + 格点吸附。
同（大五, 配置内容）输入必产生逐字节相同的 MotivationProfile。
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Mapping

from worldmock.profile.models import (
    BIG_FIVE_DIMENSIONS,
    PROMPT_VERSION,
    WEIGHT_KEYS,
    attention_keys,
    AttentionPrior,
    BigFiveProfile,
    Comfort,
    LossAversion,
    MotivationProfile,
    Weights,
    Aspiration,
    Consensus,
    ProfileSource,
    BETA_PRESENT_BIAS_RANGE,
    BETA_PRESENT_BIAS_STEP,
    EMOTION_SENSITIVITY_RANGE,
    EMOTION_SENSITIVITY_STEP,
    LOSS_AVERSION_RANGE,
    LOSS_AVERSION_STEP,
    normalize_to_grid,
    normalized_scores,
    persona_text_sha256,
    quantize,
)

RULES_PATH = Path("catalog/rules/persona-mapping.toml")
#: mapper 单独使用时的溯源标记：数值来自确定性映射，未经 LLM 共识提取。
DETERMINISTIC_SOURCE_MODEL = "persona-mapping v1 (deterministic, zero-LLM)"


def default_mapping_path() -> Path:
    """@brief 解析 persona-mapping.toml 默认路径：env 覆盖 > 包定位仓库根 > CWD。"""
    override = os.environ.get("WORLDMOCK_PERSONA_MAPPING")
    if override:
        return Path(override)
    package_root = Path(__file__).resolve().parents[3]
    candidates = (package_root / RULES_PATH, Path.cwd() / RULES_PATH)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        f"persona mapping config not found at {package_root / RULES_PATH} or {Path.cwd() / RULES_PATH}")


def load_mapping_config(path: Path | str | None = None) -> dict:
    """@brief 读取并结构校验映射系数表（缺 section/键即失败，不静默缺省）。"""
    config_path = Path(path) if path is not None else default_mapping_path()
    with config_path.open("rb") as handle:
        config = tomllib.load(handle)
    _validate_config(config)
    return config


def _validate_config(config: Mapping) -> None:
    required = {"big_five_center", "weights", "tau", "beta_present_bias", "loss_aversion",
                "comfort", "aspiration", "emotion_sensitivity", "attention"}
    missing = required - set(config)
    assert not missing, f"persona-mapping.toml missing sections: {sorted(missing)}"
    center = config["big_five_center"]
    assert {"center", "span"} <= set(center), "big_five_center needs center/span"
    weights = config["weights"]
    assert set(weights["base"]) == set(WEIGHT_KEYS), "weights.base must cover all five keys"
    tau_table = config["tau"]["by_conscientiousness"]
    assert {str(score) for score in range(1, 8)} <= set(tau_table), \
        "tau.by_conscientiousness must cover scores 1-7"
    attention = config["attention"]
    expected = attention_keys()
    assert set(attention["base"]) == set(expected), (
        f"attention.base must exactly cover the registry attention_required "
        f"kinds {list(expected)}")
    assert set(attention["slope"]) <= set(expected), (
        f"attention.slope keys must stay within the registry attention_required "
        f"kinds {list(expected)}")
    for loss_key in ("money_fen", "energy_kcal"):
        assert loss_key in config["loss_aversion"], f"loss_aversion.{loss_key} missing"


def map_profile(big_five: BigFiveProfile, config: Mapping | None = None, *,
                persona_text: str | None = None, source_model: str = DETERMINISTIC_SOURCE_MODEL,
                consensus_runs: int = 1, consensus_agreement: float = 0.0) -> MotivationProfile:
    """@brief 纯函数映射：大五 → 九参数动机档案（格点全吸附，同输入同输出）。

    persona_text 仅用于 source.persona_text_sha256 溯源指纹（None 时指纹为
    64 个 0 的占位）；source_model/consensus_* 仅用于溯源字段回填——都不影响
    任何数值参数。编译入口传入 extractor 的真实共识统计。
    """
    if config is None:
        config = load_mapping_config()
    center_config = config["big_five_center"]
    scores = normalized_scores(big_five, float(center_config["center"]), float(center_config["span"]))
    return MotivationProfile(
        big_five=big_five,
        weights=_weights(scores, config["weights"]),
        tau=_tau(big_five, config["tau"]),
        beta_present_bias=_linear_grid(
            config["beta_present_bias"], scores,
            *BETA_PRESENT_BIAS_RANGE, BETA_PRESENT_BIAS_STEP),
        loss_aversion=_loss_aversion(scores, config["loss_aversion"]),
        comfort=_comfort(scores, config["comfort"]),
        aspiration=_aspiration(scores, config["aspiration"]),
        emotion_sensitivity=_linear_grid(
            config["emotion_sensitivity"], scores,
            *EMOTION_SENSITIVITY_RANGE, EMOTION_SENSITIVITY_STEP),
        attention_prior=_attention(scores, config["attention"]),
        source=ProfileSource(
            persona_text_sha256=(persona_text_sha256(persona_text)
                                 if persona_text is not None else "0" * 64),
            model=source_model,
            prompt_version=PROMPT_VERSION,
            consensus=Consensus(runs=consensus_runs, agreement=consensus_agreement),
        ),
    )


def _linear(section: Mapping, scores: Mapping[str, float]) -> float:
    """@brief intercept + Σ slope×normalized 的线性组合。"""
    value = float(section["intercept"])
    for dimension, slope in section.get("slope", {}).items():
        assert dimension in BIG_FIVE_DIMENSIONS, f"unknown big-five dimension: {dimension}"
        value += float(slope) * scores[dimension]
    return value


def _linear_grid(section: Mapping, scores: Mapping[str, float],
                 low: float, high: float, step: float) -> float:
    """@brief 线性组合后吸附格点。"""
    return quantize(_linear(section, scores), low, high, step)


def _weights(scores: Mapping[str, float], section: Mapping) -> Weights:
    """@brief 五资源权重：base+slope 线性组合、逐键钳制、0.01 格点归一。"""
    clamp_min = float(section["clamp"]["min"])
    clamp_max = float(section["clamp"]["max"])
    slopes = section["slope"]
    raw = {}
    for key in WEIGHT_KEYS:
        value = float(section["base"][key])
        for dimension, slope in slopes.get(key, {}).items():
            value += float(slope) * scores[dimension]
        raw[key] = min(max(value, clamp_min), clamp_max)
    return Weights.model_validate(normalize_to_grid(raw))


def _tau(big_five: BigFiveProfile, section: Mapping) -> float:
    """@brief tau 按尽责性分段查表（anchored: 尽责性→tau 负向）。"""
    table = section["by_conscientiousness"]
    return float(table[str(big_five.conscientiousness)])


def _loss_aversion(scores: Mapping[str, float], section: Mapping) -> LossAversion:
    """@brief 损失厌恶：两资源独立线性组合后 0.25 格点吸附。"""
    return LossAversion(
        money_fen=_linear_grid(section["money_fen"], scores,
                               *LOSS_AVERSION_RANGE, LOSS_AVERSION_STEP),
        energy_kcal=_linear_grid(section["energy_kcal"], scores,
                                 *LOSS_AVERSION_RANGE, LOSS_AVERSION_STEP),
    )


def _comfort(scores: Mapping[str, float], section: Mapping) -> Comfort:
    """@brief 舒适缓冲：线性组合 + 整数粒度取整 + 正下限。"""
    return Comfort(
        balance_fen=_rounded_int(section["balance_fen"], scores),
        energy_kcal=_rounded_int(section["energy_kcal"], scores),
    )


def _aspiration(scores: Mapping[str, float], section: Mapping) -> Aspiration:
    """@brief 志向：每日期望有效工作秒数。"""
    return Aspiration(daily_effective_work_seconds=_rounded_int(
        section["daily_effective_work_seconds"], scores))


def _rounded_int(section: Mapping, scores: Mapping[str, float]) -> int:
    """@brief intercept+Σslope 后按 round_to 粒度取整并钳制到正下限。"""
    value = _linear(section, scores)
    round_to = int(section["round_to"])
    minimum = int(section["min"])
    return max(minimum, int(round(value / round_to)) * round_to)


def _attention(scores: Mapping[str, float], section: Mapping) -> AttentionPrior:
    """@brief 注意先验：注册表键集上 base+slope（slope 可省）、钳制、与均匀
    分布按外向性混合系数均衡后归一。"""
    keys = attention_keys()
    clamp_min = float(section["clamp"]["min"])
    clamp_max = float(section["clamp"]["max"])
    balance = section["balance"]
    mix = min(max(float(balance["intercept"])
                  + float(balance["extraversion"]) * scores["extraversion"], 0.0), 1.0)
    slopes = section["slope"]
    raw = {}
    for key in keys:
        value = float(section["base"][key])
        for dimension, slope in slopes.get(key, {}).items():
            value += float(slope) * scores[dimension]
        value = min(max(value, clamp_min), clamp_max)
        raw[key] = (1.0 - mix) * value + mix / len(keys)
    return AttentionPrior.model_validate(normalize_to_grid(raw))
