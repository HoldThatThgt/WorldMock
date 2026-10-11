"""@brief 在原生 dispatcher 执行三次独立画像抽取，输出修正是独立逻辑请求。"""

from __future__ import annotations

from worldmock.base_models import FrozenModel
import json
import logging
import math
from collections import Counter
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field

from worldmock.profile.mapper import load_mapping_config
from worldmock.input_models import ModelConfig, ModelMetadata
from worldmock.model_endpoint import IndependentModelClient
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
    settle_work_anchors,
)

#: 每次画像编译执行三次独立一致性采样。
DEFAULT_RUNS = 3
TEMPERATURE = 0.3
MAX_COMPLETION_TOKENS = 8000
AGREEMENT_WARNING_THRESHOLD = 2 / 3

#: 抽取载荷的顶层键（三次一致性按这些字段逐个取共识）。
PAYLOAD_KEYS = ("big_five", "identity", "relationships", "situation",
                "time_anchors", "work_anchors", "chronotype_midsleep", "sleep_need_hours",
                "habits", "tastes", "coping")
#: 抽取载荷 work_anchors 的两个键（下班、到家）；不取映射表缺省。
_WORK_ANCHORS = ("leave_work", "arrive_home")

_SYSTEM_PROMPT = """你是人物档案编译助手。根据给出的 background_story，抽取结构化人物卡片与
大五人格评分，只输出一个 JSON 对象，字段与规则如下：
- big_five：大五 1-7 整数分 {"openness","conscientiousness","extraversion","agreeableness","neuroticism"}；
  文本无证据的维度给 4，不要臆测
- identity：{"age": 整数, "occupation": 职业短语, "living_arrangement": 居住方式短语}
- relationships：最多 4 条 [{"name_or_appellation": 姓名或称谓, "role": 角色短语,
  "intimacy": 0-30 整数亲密度, "channels": ["电话","微信","见面"] 中 1-3 个渠道}]；
  故事没有说明联系渠道的人不要放进 relationships，不得给 channels 空数组
- situation：{"busy_this_week": 本周在忙什么（一句话）, "cares_about": 在意什么（一句话）}
- time_anchors：{"wake","leave_home","lunch","dinner","bedtime"} 各为 "HH:MM-HH:MM" 窗口，
  按时间升序（起床 < 出门 < 午餐 < 晚餐 < 就寝）；就寝窗口可跨午夜，起点必须是当日
  晚间小时（如 "23:00-01:00" 表凌晨一点入睡；实际入睡再晚也不写 "00:30-08:00" 式
  次日起点）；故事未提及的锚点写 null
  bedtime 只表示开始就寝的短窗口，不是整夜睡眠时段。约零点十分就寝可写
  "23:30-00:30"，不能写 "00:00-08:00"；睡眠时长单独填 sleep_need_hours
- work_anchors：{"leave_work","arrive_home"} 各为 "HH:MM-HH:MM" 窗口或 null；故事写明固定的下班或到家时刻才填；
  没有固定工作地点、时间不固定、落在次日或未提及写 null，不要推测
- chronotype_midsleep："HH:MM" 自由日睡眠中点；未提及写 null
- sleep_need_hours：7-9 之间 0.25 步进的数字；未提及写 null
- habits：最多 5 条 [{"trigger": 情境短语, "response": 行为短语, "time_window": "HH:MM-HH:MM"}]
- tastes：{"content_genres": 最多 6 个内容题材词, "food_preferences": 最多 6 个饮食喜好词,
  "media": 最多 4 个常用媒介词}；无相关内容给空数组
- coping：压力时做什么（一句话）
- 任何 time_window（锚点与习惯）跨度不超过 12 小时，禁止 "00:00-24:00" 式全天窗口
- 习惯的 time_window 必须是具体窗口，不写 null；time_anchors 的锚点无法确定写 null 由缺省补齐，work_anchors 写 null 就保持 null
顶层只输出上述字段，不添加说明字段。全部值用中文（时间与数字除外）；
只依据故事证据，不编造故事外事实。"""


class ExtractionError(RuntimeError):
    """@brief 提取失败：凭据缺失、API 不可达或超次重试仍无法解析。"""


class PersonaExtraction(FrozenModel):
    """@brief 三次一致性共识提取结果（编译入口消费，不进入提示合同）。

    demotion_notes 是共识后最终卡片上下班、到家锚点的降级说明（每个被置 null 的锚点一条，按核对顺序），
    编译警告把它接在一致性警告之后。
    """


    card: PersonaCard
    big_five: BigFiveProfile
    runs: int = Field(ge=1)
    agreement: float = Field(ge=0.0, le=1.0)
    per_field: dict[str, float]
    warning: bool
    model: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    demotion_notes: tuple[str, ...] = ()


async def extract_persona(background_story: str, model_config: ModelConfig, metadata: ModelMetadata,
                          n_runs: int = DEFAULT_RUNS, *, client: Any | None = None) -> PersonaExtraction:
    """@brief 独立抽取采用实际配置与容量，每次响应修正共用同一请求调度器。"""
    story = background_story.strip()
    if not story or type(n_runs) is not int or n_runs < 1:
        logging.error("Persona extraction input is invalid")
        raise ExtractionError("Persona extraction input is invalid")
    identity = model_config.model_dump(mode="json", exclude={"api_key_env"})
    identity["model_metadata"] = metadata.model_dump(mode="json")
    caller = client if client is not None else IndependentModelClient(identity, "Persona extraction")
    mapping = load_mapping_config()
    request = {"model": model_config.requested_model, "temperature": TEMPERATURE,
               "max_tokens": min(MAX_COMPLETION_TOKENS, metadata.max_output_tokens),
               "thinking": {"type": "disabled"}, "response_format": {"type": "json_object"},
               "messages": [{"role": "system", "content": _SYSTEM_PROMPT},
                            {"role": "user", "content": f"background_story：\n{story}"}]}
    payloads = [await _single_run(request, caller, mapping, model_config.persona_validation_max_retries)
                for _ in range(n_runs)]
    consensus, per_field = _consensus(payloads)
    card, notes = _build_card(consensus, mapping)
    return PersonaExtraction(card=card, big_five=BigFiveProfile(**consensus["big_five"]), runs=n_runs,
                             agreement=round(sum(per_field.values()) / len(per_field), 4),
                             per_field={name: round(share, 4) for name, share in per_field.items()},
                             warning=any(share < AGREEMENT_WARNING_THRESHOLD for share in per_field.values()),
                             model=model_config.requested_model, prompt_version=PROMPT_VERSION,
                             demotion_notes=notes)


async def _single_run(request: dict, client: Any, mapping: Mapping, validation_retries: int) -> dict:
    """@brief 请求错误直接失败；仅解析和结构校验失败发起新的输出修正请求。"""
    payload = {**request, "messages": [dict(item) for item in request["messages"]]}
    last_error = "No attempt made"
    for attempt in range(validation_retries + 1):
        response = await client.call(payload)
        try:
            return _parse_payload(response["choices"][0]["message"]["content"], mapping)
        except (KeyError, IndexError, TypeError, ValueError) as error:
            last_error = f"{type(error).__name__}: {error}"
            logging.error("Persona extraction output failed validation: %s", last_error)
            if attempt < validation_retries:
                payload["messages"] = [*request["messages"], {
                    "role": "user", "content": "上一次输出未通过结构校验，请依据原故事重新输出完整 JSON。"
                    "只修正错误，不改写故事事实。校验错误：\n" + last_error}]
    raise ExtractionError(f"Persona extraction failed after {validation_retries + 1} attempts: {last_error}")


def _parse_payload(content: str, config: Mapping) -> dict:
    """@brief 解析模型输出为完整载荷（结构校验走卡片模型；失败即 ValueError 重试）。

    big_five 不在 PersonaCard 内，在此一并校验：null 维度若漏到共识层会在
    中位数排序处抛 TypeError（绕过重试路径），与 habits.time_window=null
    同型，一律归类为无效 payload。下班与到家锚点乱序时本次构造只为放行而降级：
    说明丢弃、返回的仍是原始载荷，不消耗重试；格式错误照常抛 ValueError 走重试。
    """
    if not isinstance(content, str) or not content.strip():
        raise ValueError("empty or non-string model content: "
                         f"{content!r} (reasoning exhausted completion budget?)")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as error:
        raise ValueError(f"non-JSON model output: {content[:120]}") from error
    if not isinstance(parsed, dict) or not set(PAYLOAD_KEYS) <= set(parsed):
        raise ValueError(f"unexpected payload keys: "
                         f"{sorted(parsed) if isinstance(parsed, dict) else type(parsed)}")
    parsed = {key: parsed[key] for key in PAYLOAD_KEYS}
    _build_card(parsed, config)
    BigFiveProfile(**parsed["big_five"])
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


def _build_card(consensus: Mapping, config: Mapping) -> tuple[PersonaCard, tuple[str, ...]]:
    """@brief 共识载荷 -> (PersonaCard, 降级说明)：五锚点等 null 按 TOML [defaults] 确定性补齐，下班与到家不取缺省。"""
    defaults = config["defaults"]
    anchors, notes = _work_anchors(consensus["work_anchors"],
                                   _build_anchors(consensus["time_anchors"], defaults["time_anchors"]))
    card = PersonaCard(
        identity=Identity.model_validate(consensus["identity"]),
        relationships=tuple(Relationship.model_validate(item)
                            for item in consensus["relationships"]),
        situation=Situation.model_validate(consensus["situation"]),
        time_anchors=anchors,
        chronotype_midsleep=_chronotype(consensus, defaults),
        sleep_need_hours=_sleep_need(consensus, defaults),
        habits=tuple(HabitSignature.model_validate(
            {**item, "time_window": TimeWindow.from_clock_range(item["time_window"])})
            for item in consensus["habits"]),
        tastes=Tastes.model_validate(consensus["tastes"]),
        coping=consensus["coping"],
    )
    return card, notes


def _chronotype(consensus: Mapping, defaults: Mapping) -> float:
    """@brief 时型：null -> TOML 缺省；模型读出的 "HH:MM" 在抽取入口取整到一刻钟（卡片模型自身不检查格点）。"""
    raw = consensus["chronotype_midsleep"] or defaults["chronotype_midsleep"]
    return quantize(parse_clock(raw), 0.0, 23.75, 0.25)


def _sleep_need(consensus: Mapping, defaults: Mapping) -> float:
    """@brief 睡眠需要：null -> TOML 缺省；模型读出的值在抽取入口取整到 [7,9] 内的一刻钟。"""
    raw = consensus["sleep_need_hours"] or defaults["sleep_need_hours"]
    return quantize(float(raw), 7.0, 9.0, 0.25)


def _build_anchors(raw: Mapping, defaults: Mapping) -> TimeAnchors:
    """@brief 五个锚点：null -> TOML 缺省窗口；模型读出的时段在抽取入口取整到半小时，跨午夜窗口自动 +24；
    下班与到家先置 null，由 _work_anchors 落下。"""
    fields: dict[str, TimeWindow | None] = {}
    for name in ("wake", "leave_home", "lunch", "dinner", "bedtime"):
        fields[name] = TimeWindow.from_clock_range(raw[name] or defaults[name])
    return TimeAnchors.model_validate({**fields, "leave_work": None, "arrive_home": None})


def _work_anchors(raw: Any, anchors: TimeAnchors) -> tuple[TimeAnchors, tuple[str, ...]]:
    """@brief 下班与到家锚点：null 保持 null、不读缺省；乱序按固定顺序降级为 null，返回最终锚点与逐条英文说明。"""
    if not isinstance(raw, dict) or set(raw) != set(_WORK_ANCHORS):
        raise ValueError(f"work_anchors must be an object with exactly the keys {list(_WORK_ANCHORS)}")
    parsed = {name: None if raw[name] is None else TimeWindow.from_clock_range(raw[name])
              for name in _WORK_ANCHORS}
    leave_work, arrive_home, problems = settle_work_anchors(
        anchors.leave_home, anchors.bedtime, parsed["leave_work"], parsed["arrive_home"])
    fields = {name: getattr(anchors, name) for name in TimeAnchors.model_fields}
    final = TimeAnchors.model_validate({**fields, "leave_work": leave_work, "arrive_home": arrive_home})
    return final, tuple(f"work_anchor_demoted: {text}" for text in problems)
