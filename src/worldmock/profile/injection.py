"""@brief 运行输入警告与场景作息覆盖：注入即核对（docs/dev/SPEC-dayrun-fidelity.md 1.3、2.1、8.1）。

纯函数模块：只按显式输入计算，不读文件、不调模型、不改输入。新建运行（runtime.creation_inputs）与 validate 调用同一组
函数，警告冻结进运行清单 input_warnings、计入质量报告 input_warnings 一节并逐条写一条 ops 警告；运行路径永不因警告失败，
validate 在覆盖率低于 80% 时照旧失败（裁决 D4）。

每条警告的形状固定为 {code, agent_id, message, details}：code 取警告码闭集 INPUT_WARNING_CODES；agent_id 为人物编号，
场景级警告（配送路线、地点显示名）为 null；message 是给数据集维护者看的一句说明；details 的键按警告码固定（见各函数）。
场景作息覆盖（apply_scenario_rhythm，裁决 D16）：场景显式声明的作息字段覆盖卡片抽取值，未声明的字段仍取卡片值；
字段为七项、窗口槽为五个（docs/dev/SPEC-errands-workday.md 4.2 增下班、到家），新槽的卡片值可以是 null。
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Literal, NamedTuple, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from worldmock.errors import IntegrityError
from worldmock.profile.coverage import COVERAGE_THRESHOLD, PersonaCoverageReport
from worldmock.profile.models import (
    SLEEP_NEED_MAX_H,
    SLEEP_NEED_MIN_H,
    PersonaCard,
    TimeAnchors,
    TimeWindow,
    quantize,
)

#: 运行输入警告码闭集（按规格 1.3 与 8.1 的登记顺序）。
InputWarningCode = Literal[
    "TASTE_GENRES_UNMATCHED",
    "CONTENT_WEIGHTS_ALL_ZERO",
    "CONTENT_EMPTY_WITH_MEDIA_TASTES",
    "GAMES_WITHOUT_TASTE_WEIGHTS",
    "COVERAGE_BELOW_THRESHOLD",
    "CARD_FIELD_OVERRIDDEN",
    "CARD_FIELD_CONFLICT",
    "PERSONA_AGREEMENT_LOW",
    "DELIVERY_ROUTE_UNAVAILABLE",
    "LOCATION_NAME_SUSPICIOUS",
]
INPUT_WARNING_CODES: tuple[str, ...] = get_args(InputWarningCode)
#: 场景人物可显式声明、覆盖卡片抽取值的七个作息字段（scenario.AgentConfig）。
RHYTHM_FIELDS = ("chronotype_midsleep", "sleep_need_hours", "lunch_window", "leave_work_window",
                 "arrive_home_window", "dinner_window", "bedtime_window")
#: 场景窗口字段 → 卡片时间锚点的五个槽；插入顺序即落槽顺序（午餐、下班、到家、晚餐、就寝）。
_WINDOW_SLOTS = {"lunch_window": "lunch", "leave_work_window": "leave_work", "arrive_home_window": "arrive_home",
                 "dinner_window": "dinner", "bedtime_window": "bedtime"}
#: 警告正文列出的场景内容最常见题材个数。
TOP_CONTENT_GENRES = 5
#: 「看、刷、听」：卡片口味或习惯里出现即说明人物有观看或收听的习惯。
_MEDIA_VERBS = ("看", "刷", "听")
#: 坐标形态：成对的小数经纬度（如「（31.2, 121.5）」「东经121.50°、北纬31.23°」）或单个带经纬前缀的数值。
_COORDINATE = r"(?:[东西]经|[南北]纬)?\s*[-+]?\d{1,3}\.\d+\s*°?"
_COORDINATE_PAIR = re.compile(rf"{_COORDINATE}\s*[,，、;；]\s*{_COORDINATE}")
_LABELED_COORDINATE = re.compile(r"(?:[东西]经|[南北]纬)\s*[-+]?\d+(?:\.\d+)?\s*°?")
#: 标识形态：以连字符或下划线相连的小写 ASCII 段（如「-sim-01」「poi-node-1」）。
_IDENTIFIER = re.compile(r"(?<![A-Za-z0-9])[a-z][a-z0-9]*(?:[-_][a-z0-9]+)+(?![A-Za-z0-9])")


class InputWarning(BaseModel):
    """@brief 一条运行输入警告（清单 input_warnings 的条目形状，拒绝未知字段）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: InputWarningCode
    agent_id: int | None = Field(gt=0)
    message: str = Field(min_length=1)
    details: dict[str, Any]


class RhythmOverride(NamedTuple):
    """@brief 场景作息覆盖的结果：覆盖后的卡片与逐处警告（CARD_FIELD_OVERRIDDEN、CARD_FIELD_CONFLICT）。"""

    card: PersonaCard
    warnings: tuple[dict[str, Any], ...]


def frozen_input_warnings(value: Iterable[Mapping[str, Any]] | None) -> list[dict[str, Any]] | None:
    """@brief 冻结进清单前逐条按 InputWarning 校验；None 保持 None（引擎直驱，没有计算警告）。

    @exception IntegrityError 条目不是闭集形状（运行时与引擎之间的内部合同违约，不是一条输入警告）
    """
    if value is None:
        return None
    try:
        return [InputWarning.model_validate(item).model_dump(mode="json") for item in value]
    except ValidationError as error:
        raise IntegrityError("Input warnings do not match the closed warning shape") from error


def apply_scenario_rhythm(card: PersonaCard, agent: Any) -> RhythmOverride:
    """@brief 场景显式声明的作息字段覆盖卡片抽取值（规格 2.1，裁决 D16：场景优先）。

    只覆盖 agent.model_fields_set 与七个作息字段的交集中值非空的字段——覆盖必须在原始加载的场景人物上做，经过
    dump/validate 往返的对象会把全部字段记为已设。取值按卡片格点量化（中点与睡眠需要 0.25 小时，窗口 0.5 小时）；
    窗口经 TimeAnchors 回绕归一、validate_order 与下班到家的顺序约束重建锚点，按落槽顺序反复尝试直到没有槽能再落下，
    落不下的槽保留卡片值（下班、到家槽的卡片值可以是 null）并记 CARD_FIELD_CONFLICT；每处实际改变卡片值的覆盖记
    CARD_FIELD_OVERRIDDEN。不降级任何卡片锚点（docs/dev/SPEC-errands-workday.md 4.2）。结果是不动点：对结果再覆盖一次
    卡片不变。

    @param card 画像编译的卡片（或恢复时的结构化卡片 PersonaCardStruct）
    @param agent 场景人物声明（带可靠的 model_fields_set）
    @return 覆盖后的卡片（无变化时就是输入对象）与警告
    """
    fields_set = getattr(agent, "model_fields_set", set())
    declared = {name for name in RHYTHM_FIELDS if name in fields_set and getattr(agent, name) is not None}
    updates, warnings = _hour_overrides(card, agent, declared)
    anchors, window_warnings = _window_overrides(card, agent, declared)
    if anchors != card.time_anchors:
        updates["time_anchors"] = anchors
    overridden = card.model_copy(update=updates) if updates else card
    return RhythmOverride(overridden, tuple(warnings + window_warnings))


def _hour_overrides(card: PersonaCard, agent: Any, declared: set[str]) -> tuple[dict[str, Any], list[dict]]:
    """@brief 睡眠中点（0.25 小时格点，24 点即 0 点）与睡眠需要（0.25 小时格点，7–9 小时）的覆盖。"""
    updates: dict[str, Any] = {}
    warnings: list[dict] = []
    for name in ("chronotype_midsleep", "sleep_need_hours"):
        if name not in declared:
            continue
        raw = float(getattr(agent, name))
        value = (quantize(raw, 0.0, 24.0, 0.25) % 24.0 if name == "chronotype_midsleep"
                 else quantize(raw, SLEEP_NEED_MIN_H, SLEEP_NEED_MAX_H, 0.25))
        before = getattr(card, name)
        if value != before:
            updates[name] = value
            warnings.append(_overridden(agent.agent_id, name, (before, raw, value)))
    return updates, warnings


def _window_overrides(card: PersonaCard, agent: Any, declared: set[str]) -> tuple[TimeAnchors, list[dict]]:
    """@brief 午餐、下班、到家、晚餐、就寝窗口的覆盖：反复按落槽顺序落槽直到不动点，剩下的槽记冲突（卡片值可为 null），
    改变了值的槽记覆盖。"""
    anchors = card.time_anchors
    pending: dict[str, tuple[str, TimeWindow]] = {}
    warnings: list[dict] = []
    for field, slot in _WINDOW_SLOTS.items():
        if field not in declared:
            continue
        raw = tuple(float(item) for item in getattr(agent, field))
        target = _window_target(raw)
        if target is None:
            warnings.append(_conflict(agent.agent_id, field, (getattr(anchors, slot), raw), "window_off_grid"))
        else:
            pending[slot] = (field, target)
    progressed = True
    while progressed:
        progressed = False
        for slot, (_, target) in list(pending.items()):
            candidate = _with_slot(anchors, slot, target)
            if candidate is not None:
                anchors, progressed = candidate, True
                del pending[slot]
    for slot, (field, _) in pending.items():
        raw = tuple(float(item) for item in getattr(agent, field))
        warnings.append(_conflict(agent.agent_id, field, (getattr(anchors, slot), raw), "anchor_order"))
    for field, slot in _WINDOW_SLOTS.items():
        before, after = getattr(card.time_anchors, slot), getattr(anchors, slot)
        if field in declared and before != after:
            raw = tuple(float(item) for item in getattr(agent, field))
            warnings.append(_overridden(agent.agent_id, field, (_window_value(before), raw, _window_value(after))))
    return anchors, warnings


def _window_target(raw: tuple[float, ...]) -> TimeWindow | None:
    """@brief 场景窗口量化到 0.5 小时格点；量化后退化或越界、写不成卡片窗口时为 None。"""
    start, end = (quantize(value, 0.0, 30.0, 0.5) for value in raw)
    try:
        return TimeWindow(start_hour=start, end_hour=end)
    except ValidationError:
        return None


def _with_slot(anchors: TimeAnchors, slot: str, window: TimeWindow) -> TimeAnchors | None:
    """@brief 换掉一个槽后经回绕归一与 validate_order 重建锚点；起点不再非递减（顺序冲突）时为 None。"""
    values = {name: getattr(anchors, name) for name in TimeAnchors.model_fields}
    values[slot] = window
    try:
        return TimeAnchors.model_validate(values)
    except ValidationError:
        return None


def _window_value(window: TimeWindow | None) -> list[float] | None:
    """@brief 卡片窗口的 JSON 值 [start_hour, end_hour]；下班、到家槽为 null 时为 None。"""
    if window is None:
        return None
    return [window.start_hour, window.end_hour]


def _overridden(agent_id: int, field: str, values: tuple[Any, Any, Any]) -> dict[str, Any]:
    """@brief CARD_FIELD_OVERRIDDEN：values 为（卡片值, 场景声明值, 写进卡片的值）；窗口写成 [start, end]，
    卡片槽为 null 时卡片值为 None（正文写 null）。"""
    card_value, scenario_value, applied = values
    declared = list(scenario_value) if isinstance(scenario_value, tuple) else scenario_value
    return _warning("CARD_FIELD_OVERRIDDEN", agent_id,
                    f"场景显式声明的 {field} 覆盖了卡片抽取值：卡片 {_json_text(card_value)}，"
                    f"场景 {_json_text(declared)}，人设层与卡片结构取 {_json_text(applied)}。",
                    field=field, card_value=card_value, scenario_value=declared, applied_value=applied)


def _conflict(agent_id: int, field: str, values: tuple[TimeWindow | None, tuple[float, ...]], reason: str) -> dict:
    """@brief CARD_FIELD_CONFLICT：场景窗口落不进卡片锚点（顺序冲突或写不成 0.5 小时格点窗口），保留卡片值；
    下班、到家槽的卡片值可以是 null（details.card_value 为 null，正文写 null）。"""
    kept, scenario_value = values
    why = ("与卡片其余时间锚点的顺序冲突（起床、出门、午餐、晚餐、就寝的起点须非递减；下班不早于出门、"
           "到家不早于下班或出门，二者都不晚于就寝）" if reason == "anchor_order"
           else "量化到 0.5 小时格点后不是合法窗口")
    return _warning("CARD_FIELD_CONFLICT", agent_id,
                    f"场景显式声明的 {field} {_json_text(list(scenario_value))}{why}，保留卡片值 "
                    f"{_json_text(_window_value(kept))}。",
                    field=field, card_value=_window_value(kept), scenario_value=list(scenario_value), reason=reason)


def taste_warnings(agent_id: int, card: PersonaCard | None, contents: Sequence[Mapping[str, Any]],
                   weights: Mapping[str, float] | None) -> list[dict[str, Any]]:
    """@brief 口味与内容：TASTE_GENRES_UNMATCHED、CONTENT_WEIGHTS_ALL_ZERO、CONTENT_EMPTY_WITH_MEDIA_TASTES。

    口味只按卡片 content_genres 与内容条目 genre 精确匹配（裁决 D1，题材词表归数据集）；「B站视频」这类平台词无论写在哪个
    字段都对不上题材，media 不参与匹配，只随正文列出。正文同时列出卡片的 content_genres、media 与场景内容最常见的题材，
    由数据集决定改卡片还是补题材标签。
    @param agent_id 人物编号
    @param card 覆盖前后口味不变的卡片；没有卡片的人物为 None（没有口味权重）
    @param contents 场景内容条目 [{item_id, genre}]（只含内容，不含游戏）
    @param weights 实际注入的口味权重（没有卡片时为 None）
    @return details 键：card_content_genres、card_media、top_content_genres、content_items；
        CONTENT_EMPTY_WITH_MEDIA_TASTES 为 content_genres、media、habits（带看、刷、听的条目）
    """
    genres = list(card.tastes.content_genres) if card is not None else []
    media = list(card.tastes.media) if card is not None else []
    if not contents:
        return _empty_content_warnings(agent_id, card)
    counts = Counter(genre for row in contents for genre in row.get("genre") or ())
    top = [genre for genre, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:TOP_CONTENT_GENRES]]
    details = {"card_content_genres": genres, "card_media": media, "top_content_genres": top,
               "content_items": len(contents)}
    tail = f"卡片口味题材 {_listing(genres)}、媒介 {_listing(media)}；场景内容最常见的题材 {_listing(top)}。"
    found = []
    if genres and not set(genres) & set(counts):
        found.append(_warning("TASTE_GENRES_UNMATCHED", agent_id,
                              "人物口味题材与场景内容的题材没有交集（只按 content_genres 精确匹配，平台词对不上题材）；"
                              + tail, **details))
    if all((weights or {}).get(row["item_id"], 0.0) <= 0.0 for row in contents):
        lead = "人物没有卡片与口味权重" if card is None else "人物的口味权重全为 0"
        found.append(_warning("CONTENT_WEIGHTS_ALL_ZERO", agent_id,
                              f"场景有 {len(contents)} 条内容，但{lead}，观看缓解按零权重计；" + tail, **details))
    return found


def _empty_content_warnings(agent_id: int, card: PersonaCard | None) -> list[dict[str, Any]]:
    """@brief 场景没有内容时：卡片口味题材、带看刷听的媒介或习惯任一存在即 CONTENT_EMPTY_WITH_MEDIA_TASTES。"""
    if card is None:
        return []
    genres = list(card.tastes.content_genres)
    media = [item for item in card.tastes.media if _mentions_media_verb(item)]
    habits = [habit.response for habit in card.habits
              if _mentions_media_verb(habit.response) or _mentions_media_verb(habit.trigger)]
    if not (genres or media or habits):
        return []
    return [_warning("CONTENT_EMPTY_WITH_MEDIA_TASTES", agent_id,
                     f"场景没有可看、可听的内容，但卡片或习惯里有看、刷、听：口味题材 {_listing(genres)}、"
                     f"媒介 {_listing(media)}、习惯 {_listing(habits)}。",
                     content_genres=genres, media=media, habits=habits)]


def game_weight_warnings(agent_id: int, game_ids: Sequence[str],
                         weights: Mapping[str, float] | None) -> list[dict[str, Any]]:
    """@brief GAMES_WITHOUT_TASTE_WEIGHTS：场景有游戏，但实际注入的口味权重表里没有这些游戏的条目。

    只看显式输入的权重表是否含游戏条目（没有题材声明的游戏权重为 0 仍算已有条目），与权重如何计算无关。
    @return details 键：games（场景游戏数）、missing_game_ids（权重表缺条目的游戏）
    """
    table = weights or {}
    missing = [game_id for game_id in game_ids if game_id not in table]
    if not missing:
        return []
    return [_warning("GAMES_WITHOUT_TASTE_WEIGHTS", agent_id,
                     f"场景有 {len(game_ids)} 个游戏，其中 {len(missing)} 个在口味权重表里没有条目，游戏缓解按零权重计。",
                     games=len(game_ids), missing_game_ids=missing)]


def coverage_warnings(agent_id: int, report: PersonaCoverageReport) -> list[dict[str, Any]]:
    """@brief COVERAGE_BELOW_THRESHOLD：复用 validate_coverage 的报告，可执行比例低于 80% 时警告（运行路径只警告）。

    @return details 键：executable_ratio、threshold、total_items、covered_items、missing（[{kind, value}]）
    """
    if report.passed:
        return []
    missing = [{"kind": item.kind, "value": item.value} for item in report.missing]
    listed = "、".join(f"{item['kind']}={item['value']}" for item in missing)
    return [_warning("COVERAGE_BELOW_THRESHOLD", agent_id,
                     f"人物卡片的世界可执行覆盖率 {report.executable_ratio} 低于 {COVERAGE_THRESHOLD}，"
                     f"未命中：{listed}。",
                     executable_ratio=report.executable_ratio, threshold=COVERAGE_THRESHOLD,
                     total_items=report.total_items, covered_items=report.covered_items, missing=missing)]


def agreement_warnings(agent_id: int, compiler_warnings: Sequence[str]) -> list[dict[str, Any]]:
    """@brief PERSONA_AGREEMENT_LOW：逐条透传画像编译留下的警告（一致性偏低的字段、下班与到家锚点的降级说明等）。

    该码泛指画像编译留下的警告，消息不预设原因（docs/dev/SPEC-errands-workday.md 4.1）。
    @return details 键：compiler_warning（编译产物 warnings 中的原文）
    """
    return [_warning("PERSONA_AGREEMENT_LOW", agent_id, f"画像编译留下警告：{text}。", compiler_warning=text)
            for text in compiler_warnings]


def delivery_route_warnings(scenario: Any) -> list[dict[str, Any]]:
    """@brief DELIVERY_ROUTE_UNAVAILABLE：配送供给到声明目的地没有唯一的 reachable 驾车路线（裁决 D19：只警告）。

    与配送结算同一判据：门店就在目的地时不需路线，其余只认唯一一条明确可达的冻结驾车路线；按（门店, 目的地, 路线状态）
    合并，列出受影响的供给。unreachable、unknown 是地图的合法状态，不拒绝场景。
    @return details 键：location_id、destination_id、route_status（missing / unreachable / unknown）、supply_ids
    """
    statuses: dict[tuple[str, str], list[str]] = {}
    for route in scenario.routes:
        if route.mode == "driving":
            statuses.setdefault((route.origin_id, route.destination_id), []).append(route.status)
    groups: dict[tuple[str, str, str], list[str]] = {}
    for supply in scenario.supplies:
        if "delivery" not in supply.fulfillment_modes:
            continue
        for destination in supply.delivery_destination_ids:
            found = statuses.get((supply.location_id, destination), [])
            if supply.location_id == destination or found == ["reachable"]:
                continue
            status = found[0] if len(found) == 1 else "missing"
            groups.setdefault((supply.location_id, destination, status), []).append(supply.supply_id)
    names = {location.location_id: location.name for location in scenario.locations}
    return [_warning("DELIVERY_ROUTE_UNAVAILABLE", None,
                     f"门店 {origin}（{names.get(origin, origin)}）到目的地 {destination}"
                     f"（{names.get(destination, destination)}）没有可达的驾车路线（{status}），"
                     f"{len(supplies)} 项配送供给送不到：{_listing(sorted(supplies))}。",
                     location_id=origin, destination_id=destination, route_status=status,
                     supply_ids=sorted(supplies))
            for (origin, destination, status), supplies in sorted(groups.items())]


def location_name_warnings(scenario: Any) -> list[dict[str, Any]]:
    """@brief LOCATION_NAME_SUSPICIOUS：地点显示名带坐标或标识形态（裁决 D19：只警告，人物会照抄这个名字）。

    @return details 键：location_id、name、patterns（coordinates / identifier）
    """
    found = []
    for location in sorted(scenario.locations, key=lambda item: item.location_id):
        patterns = suspicious_name_patterns(location.name)
        if patterns:
            kinds = "、".join({"coordinates": "坐标", "identifier": "标识形态"}[item] for item in patterns)
            found.append(_warning("LOCATION_NAME_SUSPICIOUS", None,
                                  f"地点 {location.location_id} 的显示名「{location.name}」带{kinds}；人物会照抄显示名，"
                                  "请改成人物会说的名字（同名地点按路名或楼宇区分）。",
                                  location_id=location.location_id, name=location.name, patterns=patterns))
    return found


def suspicious_name_patterns(name: str) -> list[str]:
    """@brief 显示名里的可疑形态：coordinates（成对小数经纬度或带经纬前缀的数值）、identifier（小写 ASCII 段以连字符或
    下划线相连，且带纯数字段或至少三段，如 sim-01、poi-node-1；「7-Eleven」「e-bike」这类名字不算）。"""
    patterns = []
    if _COORDINATE_PAIR.search(name) or _LABELED_COORDINATE.search(name):
        patterns.append("coordinates")
    for match in _IDENTIFIER.finditer(name):
        parts = re.split(r"[-_]", match.group())
        if any(part.isdigit() for part in parts) or len(parts) >= 3:
            patterns.append("identifier")
            break
    return patterns


def _mentions_media_verb(text: str) -> bool:
    """@brief 文本里是否有「看、刷、听」。"""
    return any(verb in text for verb in _MEDIA_VERBS)


def _listing(values: Sequence[str]) -> str:
    """@brief 正文里的列表写法：[甲、乙]，空列表写 [无]。"""
    return "[" + ("、".join(values) if values else "无") + "]"


def _json_text(value: Any) -> str:
    """@brief 正文里的数值写法：窗口写成 [start, end]，小时数原样，空的卡片槽写 null。"""
    if value is None:
        return "null"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(f"{item:g}" for item in value) + "]"
    return f"{value:g}"


def _warning(code: str, agent_id: int | None, message: str, **details: Any) -> dict[str, Any]:
    """@brief 按闭集形状组装一条警告（经 InputWarning 校验后的 JSON 形状）。"""
    return InputWarning(code=code, agent_id=agent_id, message=message, details=details).model_dump(mode="json")
