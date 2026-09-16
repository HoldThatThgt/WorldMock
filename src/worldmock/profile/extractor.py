"""@brief 自然语言画像 → 大五刻度的 LLM 提取层（DeepSeek 直连，多次采样共识）。

架构三段式的第一段：粗粒度（1-7 整数刻度）且稳定——温度 0.3 采样多样性 +
N 次独立运行逐维度取中位数。凭据 ~/.config/worldmock/deepseek.env
（DEEPSEEK_API_KEY），与仓库既有探针同法（dotenv_values，不走 labelkit/AS2）。
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

import requests
from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field

from worldmock.profile.models import BIG_FIVE_DIMENSIONS, PROMPT_VERSION, BigFiveProfile

API_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_RUNS = 5
#: 采样温度：要多样性做共识，但保持在低躁区间。
TEMPERATURE = 0.3
#: 单次运行内 JSON 解析/校验失败的重试上限（≤2）。
MAX_PARSE_RETRIES = 2
#: 任一维度众数占比低于该阈值 → 标黄警告。
AGREEMENT_WARNING_THRESHOLD = 0.6
CREDENTIALS_PATH = Path.home() / ".config" / "worldmock" / "deepseek.env"

_SYSTEM_PROMPT = """你是心理测量助手。根据给出的人物画像文本，在大五人格每个维度上打 1-7 的整数分：
- openness 开放性：1=保守务实、偏好熟悉确定的事物；4=中等；7=好奇心强、想象力丰富、主动寻求新鲜体验
- conscientiousness 尽责性：1=冲动、缺乏计划、难坚持；4=中等；7=自律、有条理、可靠、目标导向
- extraversion 外向性：1=安静、偏好独处、社交耗能；4=中等；7=热情健谈、主动社交、寻求刺激
- agreeableness 宜人性：1=竞争、直接、置疑他人；4=中等；7=合作、体贴、信任他人
- neuroticism 神经质：1=情绪稳定、抗压；4=中等；7=敏感、易焦虑、情绪波动大
评分只依据文本中的证据；文本未提及的维度给中间值 4，不要臆测。
只输出一个 JSON 对象，包含且仅包含五个字段（均为 1-7 整数）：
{"openness": <int>, "conscientiousness": <int>, "extraversion": <int>, "agreeableness": <int>, "neuroticism": <int>}"""


class ExtractionError(RuntimeError):
    """@brief 提取失败：凭据缺失、API 不可达或超次重试仍无法解析。"""


class BigFiveExtraction(BaseModel):
    """@brief N 次运行的共识提取结果（编译入口消费，不进入动机档案合同）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    profile: BigFiveProfile
    runs: int = Field(ge=1)
    agreement: float = Field(ge=0.0, le=1.0)
    per_dimension: dict[str, dict]
    warning: bool
    model: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)


def resolve_api_key(explicit: str | None = None) -> str:
    """@brief 凭据解析：显式参数 > 环境变量 > ~/.config/worldmock/deepseek.env。"""
    if explicit:
        return explicit
    if os.environ.get("DEEPSEEK_API_KEY"):
        return os.environ["DEEPSEEK_API_KEY"]
    values = dotenv_values(CREDENTIALS_PATH, interpolate=False)
    key = values.get("DEEPSEEK_API_KEY")
    if not key:
        raise ExtractionError(
            f"DEEPSEEK_API_KEY not found in environment or {CREDENTIALS_PATH}")
    return key


def extract_big_five(persona_text: str, n_runs: int = DEFAULT_RUNS, *,
                     model: str = DEFAULT_MODEL, api_key: str | None = None,
                     session: Any | None = None, timeout: float = 60.0) -> "BigFiveExtraction":
    """@brief N 次独立 LLM 运行 → 逐维度中位数 + 众数占比共识。

    每次运行独立采样（温度 0.3），单次运行内 JSON 解析失败重试至多 2 次。
    中位数取整规则：偶数个样本时四舍五入（round half up）；agreement 为
    各维度众数占比的算术平均；任一维度占比 < 0.6 → warning=True。
    """
    text = persona_text.strip()
    if not text:
        raise ExtractionError("persona text is empty")
    if n_runs < 1:
        raise ExtractionError(f"n_runs must be >= 1, got {n_runs}")
    key = resolve_api_key(api_key)
    caller = session if session is not None else requests
    samples: dict[str, list[int]] = {dimension: [] for dimension in BIG_FIVE_DIMENSIONS}
    for _ in range(n_runs):
        scores = _single_run(text, model, key, caller, timeout)
        for dimension, value in scores.model_dump().items():
            samples[dimension].append(value)
    per_dimension: dict[str, dict[str, Any]] = {}
    for dimension, values in samples.items():
        median = _median_int(values)
        mode_share = _mode_share(values)
        per_dimension[dimension] = {
            "values": values,
            "median": median,
            "mode_share": round(mode_share, 4),
        }
    profile = BigFiveProfile(**{dimension: per_dimension[dimension]["median"]
                                for dimension in BIG_FIVE_DIMENSIONS})
    agreement = sum(item["mode_share"] for item in per_dimension.values()) / len(per_dimension)
    return BigFiveExtraction(
        profile=profile,
        runs=n_runs,
        agreement=round(agreement, 4),
        per_dimension=per_dimension,
        warning=any(item["mode_share"] < AGREEMENT_WARNING_THRESHOLD
                    for item in per_dimension.values()),
        model=model,
        prompt_version=PROMPT_VERSION,
    )


def _single_run(persona_text: str, model: str, api_key: str, caller: Any,
                timeout: float) -> BigFiveProfile:
    """@brief 单次 API 调用 + JSON 解析（失败重试至多 MAX_PARSE_RETRIES 次）。"""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": f"人物画像文本：\n{persona_text}"},
        ],
        "temperature": TEMPERATURE,
        "max_tokens": 256,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {api_key}",
               "Content-Type": "application/json"}
    last_error = "no attempt made"
    for _ in range(MAX_PARSE_RETRIES + 1):
        response = caller.post(API_URL, json=payload, headers=headers, timeout=timeout)
        if response.status_code != 200:
            last_error = f"HTTP {response.status_code}: {response.text[:200]}"
            continue
        try:
            content = response.json()["choices"][0]["message"]["content"]
            return _parse_scores(content)
        except (KeyError, IndexError, ValueError, json.JSONDecodeError) as error:
            last_error = f"{type(error).__name__}: {error}"
            continue
    raise ExtractionError(f"big-five run failed after {MAX_PARSE_RETRIES + 1} attempts: {last_error}")


def _parse_scores(content: str) -> BigFiveProfile:
    """@brief 解析模型输出为 1-7 整数大五（越界/缺字段即 ValueError 触发重试）。"""
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as error:
        raise ValueError(f"non-JSON model output: {content[:120]}") from error
    if not isinstance(parsed, dict) or set(parsed) != set(BIG_FIVE_DIMENSIONS):
        raise ValueError(f"unexpected keys: {sorted(parsed) if isinstance(parsed, dict) else type(parsed)}")
    scores = {}
    for dimension in BIG_FIVE_DIMENSIONS:
        value = parsed[dimension]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value != int(value):
            raise ValueError(f"{dimension}={value!r} is not an integer score")
        scores[dimension] = int(value)
    return BigFiveProfile.model_validate(scores)


def _median_int(values: list[int]) -> int:
    """@brief 整数刻度中位数（偶数个样本 round half up，确定性）。"""
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[middle]
    return int(math.floor((ordered[middle - 1] + ordered[middle]) / 2 + 0.5))


def _mode_share(values: list[int]) -> float:
    """@brief 众数占比 = 最频繁值次数 / 样本数（平手不影响占比）。"""
    counts: dict[int, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return max(counts.values()) / len(values)
