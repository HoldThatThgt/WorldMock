"""@brief background_story -> PersonaCard + 大五的 LLM 提取层（三次一致性口径）。

沿用既有口径：温度 0.3 独立采样 N 次（默认 3，三次一致性），逐字段取众数、
大五逐维度取中位数；单次运行内 JSON 解析/校验失败重试至多 2 次。字段众数
占比 < 2/3 标黄警告。凭据 ~/.config/worldmock/deepseek.env（DEEPSEEK_API_KEY）。
故事未提及的时型、睡眠需要与时间锚点输出 null，由 persona-mapping.toml
[defaults] 确定性补齐（不在提示层编造）。
"""

from __future__ import annotations

import json
import math
import os
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

import requests
from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field

from worldmock.profile.mapper import load_mapping_config
from worldmock.profile.models import (
    BIG_FIVE_DIMENSIONS,
    PROMPT_VERSION,
    BigFiveProfile,
    HabitSignature,
    Identity,
    PersonaCard,
    Relationship,
    Situation,
    Tastes,
    TimeAnchors,
    TimeWindow,
    parse_clock,
    quantize,
)

API_URL = "https://api.deepseek.com/chat/completions"
DEFAULT_MODEL = "deepseek-chat"
#: 三次一致性口径：默认 3 次独立采样（规格 L1657）。
DEFAULT_RUNS = 3
#: 采样温度：要多样性做共识，但保持在低躁区间。
TEMPERATURE = 0.3
#: 单次运行内 JSON 解析/校验失败的重试上限。
MAX_PARSE_RETRIES = 2
#: 任一字段众数占比低于该阈值 -> 标黄警告（2/3 与三次口径一致）。
AGREEMENT_WARNING_THRESHOLD = 2 / 3
#: 请求超时（秒）。
TIMEOUT_SECONDS = 60.0
CREDENTIALS_PATH = Path.home() / ".config" / "worldmock" / "deepseek.env"

#: 抽取载荷的顶层键（三次一致性按这些字段逐个取共识）。
PAYLOAD_KEYS = ("big_five", "identity", "relationships", "situation",
                "time_anchors", "chronotype_midsleep", "sleep_need_hours",
                "habits", "tastes", "coping")

_SYSTEM_PROMPT = """你是人物档案编译助手。根据给出的 background_story，抽取结构化人物卡片与
大五人格评分，只输出一个 JSON 对象，字段与规则如下：
- big_five：大五 1-7 整数分 {"openness","conscientiousness","extraversion","agreeableness","neuroticism"}；
  文本无证据的维度给 4，不要臆测
- identity：{"age": 整数, "occupation": 职业短语, "living_arrangement": 居住方式短语}
- relationships：最多 4 条 [{"name_or_appellation": 姓名或称谓, "role": 角色短语,
  "intimacy": 0-30 整数亲密度, "channels": ["电话","微信","见面"] 最多 3 个渠道}]
- situation：{"busy_this_week": 本周在忙什么（一句话）, "cares_about": 在意什么（一句话）}
- time_anchors：{"wake","leave_home","lunch","dinner","bedtime"} 各为 "HH:MM-HH:MM" 窗口；
  就寝窗口可跨午夜（如 "23:00-01:00"）；故事未提及的锚点写 null
- chronotype_midsleep："HH:MM" 自由日睡眠中点；未提及写 null
- sleep_need_hours：7-9 之间 0.25 步进的数字；未提及写 null
- habits：最多 5 条 [{"trigger": 情境短语, "response": 行为短语, "time_window": "HH:MM-HH:MM"}]
- tastes：{"content_genres": 最多 6 个内容题材词, "food_preferences": 最多 6 个饮食喜好词,
  "media": 最多 4 个常用媒介词}；无相关内容给空数组
- coping：压力时做什么（一句话）
全部值用中文（时间与数字除外）；只依据故事证据，不编造故事外事实。"""


class ExtractionError(RuntimeError):
    """@brief 提取失败：凭据缺失、API 不可达或超次重试仍无法解析。"""


class PersonaExtraction(BaseModel):
    """@brief 三次一致性共识提取结果（编译入口消费，不进入提示合同）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    card: PersonaCard
    big_five: BigFiveProfile
    runs: int = Field(ge=1)
    agreement: float = Field(ge=0.0, le=1.0)
    per_field: dict[str, float]
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


def extract_persona(background_story: str, n_runs: int = DEFAULT_RUNS, *,
                    model: str = DEFAULT_MODEL, api_key: str | None = None,
                    session: Any | None = None) -> PersonaExtraction:
    """@brief N 次独立 LLM 运行 -> 卡片与大五的三次一致性共识。

    逐字段共识规则（确定性）：大五逐维度取中位数（偶数样本 round half up）；
    其余顶层字段取众数（按 canonical JSON 序列化计数，平手取字典序最小序列化）。
    agreement 为各字段众数占比均值；任一字段 < 2/3 -> warning=True。
    """
    story = background_story.strip()
    if not story:
        raise ExtractionError("background story is empty")
    if n_runs < 1:
        raise ExtractionError(f"n_runs must be >= 1, got {n_runs}")
    key = resolve_api_key(api_key)
    caller = session if session is not None else requests
    config = load_mapping_config()
    payloads = [_single_run(story, model, key, caller, config)
                for _ in range(n_runs)]
    consensus, per_field = _consensus(payloads)
    card = _build_card(consensus, config)
    big_five = BigFiveProfile(**consensus["big_five"])
    agreement = sum(per_field.values()) / len(per_field)
    return PersonaExtraction(
        card=card,
        big_five=big_five,
        runs=n_runs,
        agreement=round(agreement, 4),
        per_field={name: round(share, 4) for name, share in per_field.items()},
        warning=any(share < AGREEMENT_WARNING_THRESHOLD for share in per_field.values()),
        model=model,
        prompt_version=PROMPT_VERSION,
    )


def _single_run(story: str, model: str, api_key: str, caller: Any,
                config: Mapping) -> dict:
    """@brief 单次 API 调用 + JSON 解析（失败重试至多 MAX_PARSE_RETRIES 次）。"""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": f"background_story：\n{story}"},
        ],
        "temperature": TEMPERATURE,
        "max_tokens": 2048,
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {api_key}",
               "Content-Type": "application/json"}
    last_error = "no attempt made"
    for _ in range(MAX_PARSE_RETRIES + 1):
        response = caller.post(API_URL, json=payload, headers=headers,
                               timeout=TIMEOUT_SECONDS)
        if response.status_code != 200:
            last_error = f"HTTP {response.status_code}: {response.text[:200]}"
            continue
        try:
            content = response.json()["choices"][0]["message"]["content"]
            return _parse_payload(content, config)
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as error:
            last_error = f"{type(error).__name__}: {error}"
            continue
    raise ExtractionError(
        f"persona extraction failed after {MAX_PARSE_RETRIES + 1} attempts: {last_error}")


def _parse_payload(content: str, config: Mapping) -> dict:
    """@brief 解析模型输出为完整载荷（结构校验走卡片模型；失败即 ValueError 重试）。"""
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as error:
        raise ValueError(f"non-JSON model output: {content[:120]}") from error
    if not isinstance(parsed, dict) or set(parsed) != set(PAYLOAD_KEYS):
        raise ValueError(f"unexpected payload keys: "
                         f"{sorted(parsed) if isinstance(parsed, dict) else type(parsed)}")
    _build_card({key: parsed[key] for key in PAYLOAD_KEYS}, config)
    return parsed


def _consensus(payloads: list[dict]) -> tuple[dict, dict[str, float]]:
    """@brief 逐字段共识：canonical JSON 众数 + 大五逐维度中位数。

    @return (共识载荷, 逐字段众数占比)
    """
    consensus: dict[str, Any] = {}
    per_field: dict[str, float] = {}
    for key in PAYLOAD_KEYS:
        values = [payload[key] for payload in payloads]
        if key == "big_five":
            consensus[key] = {dim: _median_int([value[dim] for value in values])
                              for dim in BIG_FIVE_DIMENSIONS}
            for dim in BIG_FIVE_DIMENSIONS:
                per_field[f"big_five.{dim}"] = _mode_share(
                    [value[dim] for value in values])
        else:
            best, share = _mode_value(values)
            consensus[key] = best
            per_field[key] = share
    return consensus, per_field


def _canonical(value: Any) -> str:
    """@brief canonical JSON 序列化（排序键、非 ASCII 原样）作为共识计数单位。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _mode_value(values: list) -> tuple[Any, float]:
    """@brief 众数值与众数占比：平手取字典序最小 canonical 序列化（确定性）。"""
    counts = Counter(_canonical(value) for value in values)
    best = min(counts, key=lambda serialization: (-counts[serialization], serialization))
    share = counts[best] / len(values)
    for value in values:
        if _canonical(value) == best:
            return value, share
    raise AssertionError("unreachable: mode serialization must exist")


def _mode_share(values: list) -> float:
    """@brief 众数占比 = 最频繁值次数 / 样本数。"""
    return max(Counter(values).values()) / len(values)


def _median_int(values: list[int]) -> int:
    """@brief 整数刻度中位数（偶数个样本 round half up，确定性）。"""
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[middle]
    return int(math.floor((ordered[middle - 1] + ordered[middle]) / 2 + 0.5))


def _build_card(consensus: Mapping, config: Mapping) -> PersonaCard:
    """@brief 共识载荷 -> PersonaCard（null 缺省按 TOML [defaults] 确定性补齐）。"""
    defaults = config["defaults"]
    return PersonaCard(
        identity=Identity.model_validate(consensus["identity"]),
        relationships=tuple(Relationship.model_validate(item)
                            for item in consensus["relationships"]),
        situation=Situation.model_validate(consensus["situation"]),
        time_anchors=_build_anchors(consensus["time_anchors"],
                                    defaults["time_anchors"]),
        chronotype_midsleep=_chronotype(consensus, defaults),
        sleep_need_hours=_sleep_need(consensus, defaults),
        habits=tuple(HabitSignature.model_validate(
            {**item, "time_window": TimeWindow.from_clock_range(item["time_window"])})
            for item in consensus["habits"]),
        tastes=Tastes.model_validate(consensus["tastes"]),
        coping=consensus["coping"],
    )


def _chronotype(consensus: Mapping, defaults: Mapping) -> float:
    """@brief 时型：null -> TOML 缺省；"HH:MM" 解析后吸附 0.25 小时格点。"""
    raw = consensus["chronotype_midsleep"] or defaults["chronotype_midsleep"]
    return quantize(parse_clock(raw), 0.0, 23.75, 0.25)


def _sleep_need(consensus: Mapping, defaults: Mapping) -> float:
    """@brief 睡眠需要：null -> TOML 缺省；吸附 [7,9] 内 0.25 小时格点。"""
    raw = consensus["sleep_need_hours"] or defaults["sleep_need_hours"]
    return quantize(float(raw), 7.0, 9.0, 0.25)


def _build_anchors(raw: Mapping, defaults: Mapping) -> TimeAnchors:
    """@brief 五个锚点：null -> TOML 缺省窗口；跨午夜窗口自动 +24。"""
    fields = {}
    for name in ("wake", "leave_home", "lunch", "dinner", "bedtime"):
        fields[name] = TimeWindow.from_clock_range(raw[name] or defaults[name])
    return TimeAnchors.model_validate(fields)
