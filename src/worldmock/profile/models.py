"""@brief 画像编译器的数值合同：大五刻度、九参数动机档案与格点量化。

MotivationProfile 与场景 agents[].motivation 字段形状一一对应（schema v1，
消费端 life/motivation 层按此校验）。所有连续参数在模型层强制吸附格点：
tau ∈ {0.5,1.0,2.0,3.0}、beta_present_bias 步进 0.05、loss_aversion 步进
0.25、emotion_sensitivity 步进 0.1、weights/attention_prior 步进 0.01 且
规范化总和恰为 1.00——同输入必同输出（配合 mapper 纯函数）。
"""

from __future__ import annotations

import math
from typing import Iterable, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = 1
PROMPT_VERSION = "v1"

BIG_FIVE_DIMENSIONS = ("openness", "conscientiousness", "extraversion",
                       "agreeableness", "neuroticism")
WEIGHT_KEYS = ("money_fen", "energy_kcal", "sleep_debt_hours",
               "entertainment_need", "goal_task")
ATTENTION_KEYS = ("feature_film", "news_feed", "joke_feed",
                  "webnovel_excerpt", "drama_series")

TAU_GRID = (0.5, 1.0, 2.0, 3.0)
BETA_PRESENT_BIAS_RANGE = (0.5, 1.0)
BETA_PRESENT_BIAS_STEP = 0.05
LOSS_AVERSION_RANGE = (1.0, 3.0)
LOSS_AVERSION_STEP = 0.25
EMOTION_SENSITIVITY_RANGE = (0.0, 1.0)
EMOTION_SENSITIVITY_STEP = 0.1
#: weights/attention_prior 的公共格点：每个键吸附 0.01，总和规范化为 1.00。
WEIGHT_GRID_STEP = 0.01
_GRID_TOLERANCE = 1e-6


class _Frozen(BaseModel):
    """@brief 拒绝未知字段且不可变，序列化即合同。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


def _on_grid(value: float, low: float, high: float, step: float) -> bool:
    """@brief 判断值是否落在 [low, high] 内步进 step 的格点上。"""
    if not math.isfinite(value) or value < low - _GRID_TOLERANCE or value > high + _GRID_TOLERANCE:
        return False
    units = (value - low) / step
    return abs(units - round(units)) <= _GRID_TOLERANCE


def value_on_grid(value: float, low: float, high: float, step: float) -> bool:
    """@brief 公共格点判定（check 命令对序列化数值独立复核用）。"""
    return _on_grid(value, low, high, step)


def quantize(value: float, low: float, high: float, step: float) -> float:
    """@brief 吸附到 [low, high] 内最近格点（越界先钳制），返回规范十进制表示。"""
    clamped = min(max(float(value), low), high)
    units = round((clamped - low) / step)
    return round(low + units * step, 10)


def quantize_tau(value: float) -> float:
    """@brief 吸附到 tau 离散格点 {0.5,1.0,2.0,3.0}（最近邻，平手取更小值）。"""
    return min(TAU_GRID, key=lambda grid: (abs(grid - float(value)), grid))


def normalize_to_grid(values: Mapping[str, float], step: float = WEIGHT_GRID_STEP) -> dict[str, float]:
    """@brief 最大余数法：正值缩放至总和 1 后吸附 0.01 格点，总和恰为 1.00。

    钳制负值为 0；平手按键序稳定裁决——纯确定性，同输入同输出。
    """
    keys = tuple(values)
    if not keys:
        raise ValueError("normalize_to_grid requires at least one value")
    raw = [max(float(values[key]), 0.0) for key in keys]
    total = sum(raw)
    if total <= 0:
        raise ValueError("normalize_to_grid requires a positive total")
    scaled_units = [value / total / step for value in raw]
    unit_total = round(1.0 / step)
    floors = [math.floor(units) for units in scaled_units]
    remainder = unit_total - sum(floors)
    order = sorted(range(len(keys)),
                   key=lambda index: (-(scaled_units[index] - floors[index]), index))
    for index in order[:remainder]:
        floors[index] += 1
    return {key: round(count * step, 10) for key, count in zip(keys, floors)}


class BigFiveProfile(_Frozen):
    """@brief 大五人格 1-7 整数刻度（extractor 共识的中位数输出）。"""

    openness: int = Field(ge=1, le=7)
    conscientiousness: int = Field(ge=1, le=7)
    extraversion: int = Field(ge=1, le=7)
    agreeableness: int = Field(ge=1, le=7)
    neuroticism: int = Field(ge=1, le=7)


class Weights(_Frozen):
    """@brief 五资源动机权重：各键 [0,1] 吸附 0.01，总和规范化为 1.00。"""

    money_fen: float = Field(ge=0.0, le=1.0)
    energy_kcal: float = Field(ge=0.0, le=1.0)
    sleep_debt_hours: float = Field(ge=0.0, le=1.0)
    entertainment_need: float = Field(ge=0.0, le=1.0)
    goal_task: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_grid(self) -> "Weights":
        """@brief 逐键格点校验 + 总和恰为 1.00。"""
        for key, value in self.model_dump().items():
            if not _on_grid(value, 0.0, 1.0, WEIGHT_GRID_STEP):
                raise ValueError(f"weights.{key}={value} is off the 0.01 grid")
        if round(sum(self.model_dump().values()), 2) != 1.0:
            raise ValueError(f"weights sum {sum(self.model_dump().values()):.4f} is not normalized to 1.00")
        return self


class LossAversion(_Frozen):
    """@brief 损失厌恶系数：各资源 [1.0,3.0] 步进 0.25。"""

    money_fen: float = Field(ge=1.0, le=3.0)
    energy_kcal: float = Field(ge=1.0, le=3.0)

    @field_validator("money_fen", "energy_kcal")
    @classmethod
    def validate_grid(cls, value: float) -> float:
        """@brief 强制 0.25 格点。"""
        if not _on_grid(value, *LOSS_AVERSION_RANGE, LOSS_AVERSION_STEP):
            raise ValueError(f"loss_aversion value {value} is off the 0.25 grid")
        return value


class Comfort(_Frozen):
    """@brief 舒适缓冲阈值（低于即产生不适压力）。"""

    balance_fen: int = Field(gt=0)
    energy_kcal: int = Field(gt=0)


class Aspiration(_Frozen):
    """@brief 志向：期望的每日有效工作秒数。"""

    daily_effective_work_seconds: int = Field(gt=0)


class AttentionPrior(_Frozen):
    """@brief 五类内容注意先验：各键 [0,1] 吸附 0.01，总和规范化为 1.00。"""

    feature_film: float = Field(ge=0.0, le=1.0)
    news_feed: float = Field(ge=0.0, le=1.0)
    joke_feed: float = Field(ge=0.0, le=1.0)
    webnovel_excerpt: float = Field(ge=0.0, le=1.0)
    drama_series: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_grid(self) -> "AttentionPrior":
        """@brief 逐键格点校验 + 总和恰为 1.00。"""
        for key, value in self.model_dump().items():
            if not _on_grid(value, 0.0, 1.0, WEIGHT_GRID_STEP):
                raise ValueError(f"attention_prior.{key}={value} is off the 0.01 grid")
        if round(sum(self.model_dump().values()), 2) != 1.0:
            raise ValueError("attention_prior sum is not normalized to 1.00")
        return self


class Consensus(_Frozen):
    """@brief 提取共识：运行次数与平均众数占比。"""

    runs: int = Field(ge=1)
    agreement: float = Field(ge=0.0, le=1.0)


class ProfileSource(_Frozen):
    """@brief 档案溯源：画像文本指纹、提取模型、提示词版本与共识。"""

    persona_text_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    model: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    consensus: Consensus


class MotivationProfile(_Frozen):
    """@brief 九参数动机档案（schema v1，格点全强制，冻结不可变）。"""

    schema_version: Literal[1] = SCHEMA_VERSION
    big_five: BigFiveProfile
    weights: Weights
    tau: float
    beta_present_bias: float = Field(ge=0.5, le=1.0)
    loss_aversion: LossAversion
    comfort: Comfort
    aspiration: Aspiration
    emotion_sensitivity: float = Field(ge=0.0, le=1.0)
    attention_prior: AttentionPrior
    source: ProfileSource

    @field_validator("tau")
    @classmethod
    def validate_tau(cls, value: float) -> float:
        """@brief tau 只允许离散格点 {0.5,1.0,2.0,3.0}。"""
        if not any(abs(value - grid) <= _GRID_TOLERANCE for grid in TAU_GRID):
            raise ValueError(f"tau={value} is off the grid {TAU_GRID}")
        return value

    @field_validator("beta_present_bias")
    @classmethod
    def validate_beta(cls, value: float) -> float:
        """@brief 强制 [0.5,1.0] 内 0.05 格点。"""
        if not _on_grid(value, *BETA_PRESENT_BIAS_RANGE, BETA_PRESENT_BIAS_STEP):
            raise ValueError(f"beta_present_bias={value} is off the 0.05 grid")
        return value

    @field_validator("emotion_sensitivity")
    @classmethod
    def validate_emotion(cls, value: float) -> float:
        """@brief 强制 [0.0,1.0] 内 0.1 格点。"""
        if not _on_grid(value, *EMOTION_SENSITIVITY_RANGE, EMOTION_SENSITIVITY_STEP):
            raise ValueError(f"emotion_sensitivity={value} is off the 0.1 grid")
        return value


def normalized_scores(big_five: BigFiveProfile, center: float, span: float) -> dict[str, float]:
    """@brief (score-center)/span 把 1-7 刻度归一到 [-1,1]，供映射层线性组合。"""
    return {dimension: (getattr(big_five, dimension) - center) / span
            for dimension in BIG_FIVE_DIMENSIONS}


def persona_text_sha256(persona_text: str) -> str:
    """@brief 画像文本的 sha256 指纹（source.persona_text_sha256）。"""
    import hashlib
    return hashlib.sha256(persona_text.encode("utf-8")).hexdigest()


def iterate_lattice(profile: MotivationProfile) -> Iterable[tuple[str, float]]:
    """@brief 展开档案的全部格点数值（check 命令的一致性清单用）。"""
    yield "tau", profile.tau
    yield "beta_present_bias", profile.beta_present_bias
    yield "emotion_sensitivity", profile.emotion_sensitivity
    for key, value in profile.loss_aversion.model_dump().items():
        yield f"loss_aversion.{key}", value
    for key, value in profile.weights.model_dump().items():
        yield f"weights.{key}", value
    for key, value in profile.attention_prior.model_dump().items():
        yield f"attention_prior.{key}", value
