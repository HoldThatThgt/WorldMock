"""@brief 从冻结足迹事件确定性计算人物行为指标与协议门禁。

输入为足迹事件 dict 列表（Event.model_dump(mode="json") 形态，事件类型为冻结枚举）。
指标按事件顶层 code_sha256 分组输出，供写入 quality.json；全部计算确定性，
同一输入两次计算逐字节相等。协议门禁在证据缺失时按不通过处理（fail-closed）。

当前引擎尚未写入的未来字段按"未提供"处理并在报告 notes 中注明，不臆造数值：
- activity_progress.data.decision_offered / wake_reasons（决策节律合同）；
- decision_started.data.inner_monologue（两阶段决策合同，本版指标未消费）；
- lifestyle 活动快照的 family / category 字段（十类闭集聚合）；
- memory_updated.data.episodes 内的文本与类型（记忆重复率；本模块假定未来
  episodes 为 [{text, kind|type}, ...] 形态，属待验证假设）；
- 事件顶层 code_sha256（缺失时归入 code_sha256_unreported 组并注明）；
- start_social_activity / send_message 请求与 environment_event kind=message_arrival
  （社交最小可行版；duration_minutes / kind 为假定的请求字段名，属待验证假设）。
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from datetime import datetime
from typing import Any, Mapping
from zoneinfo import ZoneInfo

#: 十类活动家族闭集（外出 outing 并入通勤，见 CONVENTIONS）。
ACTIVITY_FAMILY_ORDER = (
    "sleep", "morning_routine", "commute", "work", "meal", "rest",
    "entertainment", "social", "chore", "exercise",
)

#: 原始活动 kind 到家族的确定性映射；wait 计入休息、consume 计入用餐为口径约定。
_KIND_FAMILY = {
    "sleep": "sleep", "travel": "commute", "work": "work", "consume": "meal",
    "watch": "entertainment", "play_game": "entertainment", "browse": "entertainment",
    "read_mail": "entertainment", "wait": "rest",
}

#: lifestyle 快照携带 category（现有语料品类）或 family（闭集名）时的映射。
_LIFESTYLE_CATEGORY_FAMILY = {
    "sport": "exercise", "household": "chore", "social": "social", "outdoor": "outing",
    "hobby": "entertainment", "learning": "entertainment", "wellness": "rest",
    "pet": "chore", "volunteer": "social",
}

_FAMILY_ALIASES = {
    "sleep": "sleep", "睡眠": "sleep", "morning_routine": "morning_routine",
    "晨间例程": "morning_routine", "commute": "commute", "通勤": "commute",
    "work": "work", "工作": "work", "meal": "meal", "用餐": "meal", "rest": "rest",
    "休息": "rest", "entertainment": "entertainment", "娱乐": "entertainment",
    "social": "social", "社交": "social", "chore": "chore", "家务": "chore",
    "exercise": "exercise", "运动": "exercise", "outing": "outing", "外出": "outing",
}

#: 2018 年全国时间利用调查公报参照（分钟；城镇/工作日口径，居民均值，规格 L1704-1706）。
#: 睡觉 9h13m、个人卫生 50m、用餐 1h44m、就业参与者工作 7h40m、交通 44m、
#: 社会交往 21m、健身 41m、看电视 100m、休闲娱乐 65m（后两项合并为屏幕娱乐 165m）。
TIME_USE_REFERENCE_MINUTES = {
    "sleep": 553, "hygiene": 50, "meals": 104, "work": 460, "transport": 44,
    "socializing": 21, "exercise": 41, "screen_entertainment": 165, "no_reference": 0,
}

REFERENCE_SOURCE = (
    "2018年全国时间利用调查公报（城镇/工作日口径，各项口径不同、均为居民均值；"
    "只作方向对照，不当作目标人物真值）"
)

#: JS 距离口径说明（闭集聚合与参照桶的合并规则）。
CONVENTIONS = [
    "外出(outing)并入通勤(commute)口径，闭集聚合为十类",
    "wait 活动时间计入休息(rest)",
    "consume 活动时间计入用餐（含 use 模式）",
    "参照的看电视(100m)与休闲娱乐(65m)合并为屏幕娱乐 165m 口径",
    "休息与家务无参照项，并入无参照桶（参照 0 分钟）",
    "lifestyle 活动缺 family/category 字段时计为未归类分钟，不进入 JS 距离",
]

#: MEMORY.md 示例身份残留标记（来源：AS2 memory_runtime.py:55-56 巩固提示示例）。
MEMORY_IDENTITY_MARKERS = (
    "marketing analyst", "28-year-old female", "aoi 500038964", "aoi 500021584",
)

#: code_sha256 缺失时的分组键。
UNREPORTED_CODE_KEY = "code_sha256_unreported"

#: 各协议门禁缺失证据时的统一说明前缀。
_NOT_PROVIDED = "未提供"


def behavior_metrics_report(
    events: list[dict[str, Any]],
    *,
    timezone: str = "Asia/Shanghai",
    location_open_hours: Mapping[str, int] | None = None,
    memory_md_texts: Mapping[int | str, str] | None = None,
) -> dict[str, Any]:
    """@brief 计算全部行为指标，按事件携带的 code_sha256 分组返回报告。

    :param events: 冻结足迹事件 dict 列表（可跨多次运行）。
    :param timezone: 模拟时间的本地时区（默认与全部冻结场景一致的 Asia/Shanghai）。
    :param location_open_hours: 地点到开门整点小时的映射；提供才计算早到分钟。
    :param memory_md_texts: 人物编号到 MEMORY.md 文本的映射；提供才计算身份残留。
    :returns: {code_sha256: {schema_version, gates, reports, notes}} 的报告字典。
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        groups.setdefault(str(event.get("code_sha256") or UNREPORTED_CODE_KEY), []).append(event)
    reports: dict[str, Any] = {}
    for code, group_events in sorted(groups.items()):
        reports[code] = _group_report(group_events, timezone, location_open_hours, memory_md_texts)
        if code == UNREPORTED_CODE_KEY:
            reports[code]["notes"].append("事件未携带 code_sha256，归入未上报组")
    return reports


def _group_report(
    events: list[dict[str, Any]],
    timezone: str,
    location_open_hours: Mapping[str, int] | None,
    memory_md_texts: Mapping[int | str, str] | None,
) -> dict[str, Any]:
    """@brief 汇总单个代码组内的协议门禁、报告项与口径说明。"""
    notes: list[str] = []
    gates = {
        "observation_repetition": _observation_gate(events, notes),
        "silent_boundary_ratio": _silent_boundary_gate(events, notes),
        "memory_repetition_ratio": _memory_repetition_gate(events, notes),
        "memory_md_identity_residue": _memory_residue_gate(memory_md_texts, notes),
    }
    token_median = _decision_token_median(events, notes)
    autonomous = _autonomous_decision_rate(events)
    wait_and_early = _wait_and_early_minutes(events, timezone, location_open_hours, notes)
    distribution = _activity_distribution(events, timezone)
    reports = {
        "decision_prompt_token_median": token_median,
        "effective_autonomous_decision_rate": autonomous,
        "wake_reason_distribution": _wake_reason_distribution(events, notes),
        "wait_minutes": wait_and_early["wait_minutes"],
        "early_arrival_minutes": wait_and_early["early_arrival_minutes"],
        "sleep_schedule": _sleep_schedule(events, timezone, notes),
        "meal_diversity": _meal_diversity(events),
        "activity_distribution": distribution,
        "persona_distinguishability": _persona_distinguishability(events),
        "social": _social(events, notes),
    }
    return {"schema_version": "behavior-metrics-1", "gates": gates, "reports": reports, "notes": notes}


def _gate(value: Any, comparison: str, threshold: Any, note: str | None = None) -> dict[str, Any]:
    """@brief 形成带阈值与判定结论的门禁条目；证据缺失时不通过。"""
    entry: dict[str, Any] = {"value": value, "comparison": comparison, "threshold": threshold}
    if value is None:
        entry["pass"] = False
        entry["note"] = note or _NOT_PROVIDED
        return entry
    entry["pass"] = bool({"==": value == threshold, ">=": value >= threshold,
                          "<=": value <= threshold}[comparison])
    if note:
        entry["note"] = note
    return entry


def _decision_model_requests(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """@brief 取决策类模型请求事件（排除工作验收请求）。"""
    return [event for event in events if event.get("event_type") == "model_request"
            and event.get("data", {}).get("phase") == "request"
            and event.get("data", {}).get("purpose") != "work_review"]


def _message_text(message: Mapping[str, Any]) -> str | None:
    """@brief 提取单条提示消息的文本（字符串或内容块列表两种形态）。"""
    if message.get("role") != "user":
        return None
    content = message.get("content")
    if isinstance(content, str):
        return content or None
    if isinstance(content, list):
        parts = [block.get("text", "") for block in content
                 if isinstance(block, Mapping) and block.get("type") == "text"]
        return "".join(parts) or None
    return None


def _observation_gate(events: list[dict[str, Any]], notes: list[str]) -> dict[str, Any]:
    """@brief 统计 user 消息中同一观察文本的最大出现次数；门禁必须为 1。"""
    texts: Counter[str] = Counter()
    for event in _decision_model_requests(events):
        messages = event.get("data", {}).get("request", {}).get("messages", [])
        for message in messages if isinstance(messages, list) else []:
            text = _message_text(message) if isinstance(message, Mapping) else None
            if text is not None:
                texts[text] += 1
    if not texts:
        notes.append("决策 model_request 无 user 消息，观察重复率无法计算")
        return _gate(None, "==", 1)
    duplicated = sorted(text for text, count in texts.items() if count > 1)
    value = max(texts.values())
    entry = _gate(value, "==", 1)
    entry["duplicated_observation_count"] = len(duplicated)
    entry["user_message_count"] = sum(texts.values())
    return entry


def _activity_progress_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """@brief 取携带活动快照与结算区间的 activity_progress 事件。"""
    return [event for event in events if event.get("event_type") == "activity_progress"]


def _snapshot(event: Mapping[str, Any]) -> Mapping[str, Any]:
    """@brief 取事件 data 内的活动权威快照；缺失按空快照处理。"""
    activity = event.get("data", {}).get("activity")
    return activity if isinstance(activity, Mapping) else {}


def _silent_boundary_gate(events: list[dict[str, Any]], notes: list[str]) -> dict[str, Any]:
    """@brief 计算清醒活动结算边界中未提供决策（decision_offered=false）的占比。"""
    waking = [event for event in _activity_progress_events(events)
              if _snapshot(event).get("kind") != "sleep"]
    known = [event for event in waking if "decision_offered" in event.get("data", {})]
    if not known:
        notes.append("activity_progress.data.decision_offered 未提供，静默边界占比无法计算")
        return _gate(None, ">=", 0.5)
    silent = sum(1 for event in known if event["data"]["decision_offered"] is False)
    missing = len(waking) - len(known)
    entry = _gate(silent / len(known), ">=", 0.5)
    entry["silent_boundaries"] = silent
    entry["known_boundaries"] = len(known)
    entry["boundaries_without_field"] = missing
    return entry


def _usage_prompt_tokens(payload: Mapping[str, Any]) -> int | float | None:
    """@brief 从事件 data 或其中 request/response 字典提取 prompt token 数。"""
    for holder in (payload, payload.get("request"), payload.get("response")):
        if not isinstance(holder, Mapping):
            continue
        usage = holder.get("usage")
        if isinstance(usage, Mapping):
            tokens = usage.get("prompt_tokens")
            if isinstance(tokens, (int, float)) and not isinstance(tokens, bool):
                return tokens
    return None


def _decision_token_median(events: list[dict[str, Any]], notes: list[str]) -> dict[str, Any]:
    """@brief 按调用配对提取决策 prompt token 并取中位数（报告项，目标 15000 仅作对照）。"""
    paired: dict[str, int | float] = {}
    for event in events:
        data = event.get("data", {})
        if event.get("event_type") == "model_response" and data.get("phase") == "response":
            tokens = _usage_prompt_tokens(data)
            if tokens is not None:
                paired[str(data.get("call_id"))] = tokens
    tokens_list: list[int | float] = []
    for event in _decision_model_requests(events):
        tokens = _usage_prompt_tokens(event.get("data", {})) or paired.get(
            str(event.get("data", {}).get("call_id")))
        if tokens is not None:
            tokens_list.append(tokens)
    if not tokens_list:
        notes.append("model_request 无可用 prompt token，决策 token 中位数无法计算")
        return {"value": None, "target_reference": 15000, "sample_size": 0}
    return {"value": statistics.median(sorted(tokens_list)), "target_reference": 15000,
            "sample_size": len(tokens_list)}


def _final_action_by_decision(events: list[dict[str, Any]]) -> dict[str, Mapping[str, Any]]:
    """@brief 按决策取 event_seq 最大的动作请求作为最终动作。"""
    final: dict[str, Mapping[str, Any]] = {}
    for event in events:
        if event.get("event_type") != "action_requested":
            continue
        decision_id = str(event.get("decision_id"))
        order = (event.get("event_seq", 0), event.get("occurred_at", 0), str(event.get("event_id")))
        held = final.get(decision_id)
        if held is None or order > (held.get("event_seq", 0), held.get("occurred_at", 0),
                                    str(held.get("event_id"))):
            final[decision_id] = event
    return final


def _is_passive_action(tool_name: str, request: Mapping[str, Any]) -> bool:
    """@brief 判断最终动作是否为 continue 或 wait（非自主动作）。"""
    if tool_name == "continue_activity":
        return True
    activity_type = str(request.get("activity_type", ""))
    return tool_name.startswith("start_") and activity_type.split(":")[0] == "wait"


def _autonomous_decision_rate(events: list[dict[str, Any]]) -> dict[str, Any]:
    """@brief 计算最终动作既非 continue 也非 wait 的决策占比（报告项）。"""
    decisions = [event for event in events if event.get("event_type") == "decision_started"]
    final = _final_action_by_decision(events)
    decided = [decision for decision in decisions if str(decision.get("decision_id")) in final]
    autonomous = 0
    for decision in decided:
        action = final[str(decision.get("decision_id"))]
        request = action.get("data", {}).get("request", {})
        if not _is_passive_action(str(action.get("data", {}).get("tool_name")), request):
            autonomous += 1
    if not decided:
        return {"value": None, "decision_count": len(decisions), "decisions_without_action":
                len(decisions)}
    return {"value": autonomous / len(decided), "decision_count": len(decisions),
            "decisions_with_action": len(decided), "autonomous_decisions": autonomous}


def _wake_reason_distribution(events: list[dict[str, Any]], notes: list[str]) -> dict[str, Any]:
    """@brief 统计 activity_progress.data.wake_reasons 内各唤醒原因的计数（报告项）。"""
    counts: Counter[str] = Counter()
    with_reasons = 0
    for event in _activity_progress_events(events):
        reasons = event.get("data", {}).get("wake_reasons")
        if isinstance(reasons, list) and reasons:
            with_reasons += 1
            counts.update(str(reason) for reason in reasons)
        elif isinstance(reasons, list):
            with_reasons += 1
    total = len(_activity_progress_events(events))
    if with_reasons == 0:
        notes.append("activity_progress.data.wake_reasons 未提供，唤醒原因分布为空")
    return {"counts": dict(sorted(counts.items())), "events_with_wake_reasons": with_reasons,
            "activity_progress_events": total}


def _interval_minutes(event: Mapping[str, Any]) -> float:
    """@brief 取单个结算区间的分钟数（interval_end - interval_start）。"""
    data = event.get("data", {})
    start, end = data.get("interval_start"), data.get("interval_end")
    if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end >= start:
        return (end - start) / 60.0
    return 0.0


def _wait_and_early_minutes(
    events: list[dict[str, Any]], timezone: str,
    location_open_hours: Mapping[str, int] | None, notes: list[str],
) -> dict[str, Any]:
    """@brief 统计 wait 活动总分钟与到达开门地点早于开门的分钟（报告项）。"""
    wait_total: dict[str, float] = {}
    arrivals: dict[str, list[tuple[str, float]]] = {}
    for event in _activity_progress_events(events):
        kind = _snapshot(event).get("kind")
        agent = str(event.get("agent_id"))
        if kind == "wait":
            wait_total[agent] = wait_total.get(agent, 0.0) + _interval_minutes(event)
        if kind == "travel" and location_open_hours:
            destination = str(_snapshot(event).get("destination_id"))
            if destination in location_open_hours:
                arrivals.setdefault(destination, []).append((agent, _interval_end(event)))
    early: dict[str, Any] = {"value": None}
    if location_open_hours is not None:
        early = _early_arrival_summary(arrivals, location_open_hours, timezone)
    else:
        notes.append("location_open_hours 未提供，早到分钟无法计算")
    return {"wait_minutes": {"per_agent_minutes": dict(sorted(wait_total.items())),
                             "total_minutes": round(sum(wait_total.values()), 6)},
            "early_arrival_minutes": early}


def _interval_end(event: Mapping[str, Any]) -> int | float:
    """@brief 取活动快照的结束时刻，缺 ended_at 时退回最后结算区间终点。"""
    snapshot_data = _snapshot(event)
    ended = snapshot_data.get("ended_at")
    if isinstance(ended, (int, float)):
        return ended
    fallback = event.get("data", {}).get("interval_end")
    return fallback if isinstance(fallback, (int, float)) else event.get("occurred_at", 0)


def _early_arrival_summary(
    arrivals: Mapping[str, list[tuple[str, float]]], location_open_hours: Mapping[str, int],
    timezone: str,
) -> dict[str, Any]:
    """@brief 按地点将到达时刻与开门整点比较，累计早到分钟。"""
    zone = ZoneInfo(timezone)
    per_location: dict[str, float] = {}
    per_agent: dict[str, float] = {}
    for destination, samples in sorted(arrivals.items()):
        open_hour = location_open_hours[destination]
        for agent, arrival in samples:
            local = datetime.fromtimestamp(arrival, zone)
            opening = local.replace(hour=open_hour, minute=0, second=0, microsecond=0)
            early = max(0.0, (opening - local).total_seconds() / 60.0)
            per_location[destination] = per_location.get(destination, 0.0) + early
            per_agent[agent] = per_agent.get(agent, 0.0) + early
    return {"value": round(sum(per_location.values()), 6),
            "per_location_minutes": {key: round(value, 6) for key, value in sorted(per_location.items())},
            "per_agent_minutes": {key: round(value, 6) for key, value in sorted(per_agent.items())}}


def _sleep_schedule(events: list[dict[str, Any]], timezone: str, notes: list[str]) -> dict[str, Any]:
    """@brief 由 sleep 活动区间计算就寝、起床时刻与睡眠时长（报告项）。"""
    zone = ZoneInfo(timezone)
    sessions: dict[str, dict[str, Any]] = {}
    for event in _activity_progress_events(events):
        snapshot_data = _snapshot(event)
        if snapshot_data.get("kind") != "sleep":
            continue
        activity_id = str(event.get("activity_id"))
        record = sessions.setdefault(activity_id, {"agent_id": str(event.get("agent_id")),
                                                   "start": snapshot_data.get("started_at"),
                                                   "end": _interval_end(event)})
        record["end"] = max(record["end"], _interval_end(event))
    valid = sorted((record for record in sessions.values()
                    if isinstance(record["start"], (int, float))
                    and isinstance(record["end"], (int, float)) and record["end"] > record["start"]),
                   key=lambda item: (item["start"], item["agent_id"]))
    if not valid:
        notes.append("无有效 sleep 活动区间，就寝与起床时刻无法计算")
        return {"sessions": [], "summary": None}
    return {"sessions": _sleep_sessions(valid, zone), "summary": _sleep_summary(valid, zone)}


def _sleep_sessions(valid: list[dict[str, Any]], zone: ZoneInfo) -> list[dict[str, Any]]:
    """@brief 将每个 sleep 区间投影为本地就寝/起床时刻与时长。"""
    sessions = []
    for record in valid:
        start_local = datetime.fromtimestamp(record["start"], zone)
        end_local = datetime.fromtimestamp(record["end"], zone)
        sessions.append({
            "agent_id": record["agent_id"],
            "bed_time": start_local.strftime("%H:%M"),
            "rise_time": end_local.strftime("%H:%M"),
            "duration_hours": round((record["end"] - record["start"]) / 3600.0, 4),
            "started_at": record["start"], "ended_at": record["end"],
        })
    return sessions


def _sleep_summary(valid: list[dict[str, Any]], zone: ZoneInfo) -> dict[str, Any]:
    """@brief 汇总就寝/起床时刻中位数（自零点的分钟，不做环形换算）与平均时长。"""
    def minutes_of_day(epoch: int | float) -> int:
        local = datetime.fromtimestamp(epoch, zone)
        return local.hour * 60 + local.minute

    bed = statistics.median(sorted(minutes_of_day(item["start"]) for item in valid))
    rise = statistics.median(sorted(minutes_of_day(item["end"]) for item in valid))
    duration = statistics.mean((item["end"] - item["start"]) / 3600.0 for item in valid)
    return {"median_bed_minutes_of_day": bed, "median_rise_minutes_of_day": rise,
            "mean_duration_hours": round(duration, 4), "session_count": len(valid)}


def _consume_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """@brief 取进食（use_mode=eat）的 consume 动作请求事件。"""
    result = []
    for event in events:
        if event.get("event_type") != "action_requested":
            continue
        data = event.get("data", {})
        if data.get("tool_name") != "consume":
            continue
        request = data.get("request", {})
        if str(request.get("use_mode", "eat")) == "eat":
            result.append(event)
    return result


def _consume_products(event: Mapping[str, Any]) -> list[str]:
    """@brief 取一次进食动作的商品清单（未来多件 items 或现订单件 product_id）。"""
    request = event.get("data", {}).get("request", {})
    items = request.get("items")
    if isinstance(items, list) and items:
        return [str(item.get("product_id")) for item in items if isinstance(item, Mapping)]
    return [str(request.get("product_id"))] if request.get("product_id") else []


def _meal_diversity(events: list[dict[str, Any]]) -> dict[str, Any]:
    """@brief 计算 consume 商品的 Simpson 集中度与 24 小时内重复进食率（报告项）。"""
    consumed = _consume_events(events)
    product_counts: Counter[str] = Counter()
    for event in consumed:
        product_counts.update(_consume_products(event))
    total_units = sum(product_counts.values())
    if not product_counts or not total_units:
        return {"simpson_concentration": None, "repetition_24h_rate": None,
                "consume_event_count": len(consumed), "distinct_products": len(product_counts)}
    simpson = sum((count / total_units) ** 2 for count in product_counts.values())
    return {"simpson_concentration": round(simpson, 6),
            "repetition_24h_rate": _repetition_24h(consumed),
            "consume_event_count": len(consumed), "distinct_products": len(product_counts),
            "product_counts": dict(sorted(product_counts.items()))}


def _repetition_24h(consumed: list[dict[str, Any]]) -> float | None:
    """@brief 计算与前 24 小时内已食商品重复的进食动作占比。"""
    ordered = sorted(consumed, key=lambda event: (event.get("occurred_at", 0),
                                                  event.get("event_seq", 0),
                                                  str(event.get("event_id"))))
    history: list[tuple[int, set[str]]] = []
    repeated = 0
    for event in ordered:
        products = set(_consume_products(event))
        occurred_at = event.get("occurred_at", 0)
        if any(products & seen and 0 < occurred_at - at <= 86400 for at, seen in history):
            repeated += 1
        history.append((occurred_at, products))
    return repeated / len(ordered)


def _family_of(snapshot_data: Mapping[str, Any]) -> str | None:
    """@brief 将活动快照映射到十类闭集；lifestyle 需 family/category 字段。"""
    kind = str(snapshot_data.get("kind"))
    if kind != "lifestyle":
        return _KIND_FAMILY.get(kind)
    for field, table in (("family", _FAMILY_ALIASES), ("category", _LIFESTYLE_CATEGORY_FAMILY)):
        value = snapshot_data.get(field)
        if isinstance(value, str) and value.lower() in table:
            return table[value.lower()]
    return None


def _family_minutes(events: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """@brief 按（运行，人物）累计各家族分钟与未归类分钟。"""
    totals: dict[str, dict[str, float]] = {}
    for event in _activity_progress_events(events):
        run_id = str(event.get("run_id"))
        agent = str(event.get("agent_id"))
        family = _family_of(_snapshot(event))
        bucket = totals.setdefault(run_id, {}).setdefault(agent, {})
        key = family or "unclassified"
        bucket[key] = bucket.get(key, 0.0) + _interval_minutes(event)
    return totals


def _agent_days(events: list[dict[str, Any]], agent_id: str, zone: ZoneInfo) -> int:
    """@brief 以人物事件跨度覆盖的本地自然日数为分母口径。"""
    stamps = [event.get("occurred_at") for event in events
              if str(event.get("agent_id")) == agent_id
              and isinstance(event.get("occurred_at"), (int, float))]
    if not stamps:
        return 0
    dates = {datetime.fromtimestamp(stamp, zone).date() for stamp in stamps}
    first = min(dates)
    last = max(dates)
    return (last - first).days + 1


def _js_buckets(family_minutes: Mapping[str, float]) -> list[float]:
    """@brief 将十类家族分钟投影到参照的九个 JS 桶（外出并入通勤，休息+家务为无参照）。"""
    commute = family_minutes.get("commute", 0.0) + family_minutes.get("outing", 0.0)
    return [
        family_minutes.get("sleep", 0.0), family_minutes.get("morning_routine", 0.0),
        family_minutes.get("meal", 0.0), family_minutes.get("work", 0.0), commute,
        family_minutes.get("social", 0.0), family_minutes.get("exercise", 0.0),
        family_minutes.get("entertainment", 0.0),
        family_minutes.get("rest", 0.0) + family_minutes.get("chore", 0.0),
    ]


def _js_distance(left: list[float], right: list[float]) -> tuple[float, float]:
    """@brief 计算两个分钟向量的 JS 距离（log2 口径），返回 (散度, 距离=开方)。"""
    left_total, right_total = sum(left), sum(right)
    if left_total <= 0 or right_total <= 0:
        return float("nan"), float("nan")
    p = [value / left_total for value in left]
    q = [value / right_total for value in right]
    divergence = 0.0
    for pi, qi in zip(p, q):
        middle = (pi + qi) / 2.0
        if pi > 0 and middle > 0:
            divergence += 0.5 * pi * math.log2(pi / middle)
        if qi > 0 and middle > 0:
            divergence += 0.5 * qi * math.log2(qi / middle)
    return divergence, math.sqrt(divergence)


def _activity_distribution(events: list[dict[str, Any]], timezone: str) -> dict[str, Any]:
    """@brief 计算各人物十类活动日时间分布与时间利用调查参照的 JS 距离（报告项）。"""
    zone = ZoneInfo(timezone)
    totals = _family_minutes(events)
    per_agent: dict[str, Any] = {}
    js_values: list[float] = []
    for run_id in sorted(totals):
        for agent in sorted(totals[run_id]):
            minutes = totals[run_id][agent]
            days = max(_agent_days(events, agent, zone), 1)
            daily = {family: round(minutes.get(family, 0.0) / days, 6)
                     for family in ACTIVITY_FAMILY_ORDER}
            entry = {"run_id": run_id, "observed_days": days,
                     "family_minutes": {family: round(minutes.get(family, 0.0), 6)
                                        for family in ACTIVITY_FAMILY_ORDER},
                     "outing_minutes_merged_into_commute": round(minutes.get("outing", 0.0), 6),
                     "unclassified_minutes": round(minutes.get("unclassified", 0.0), 6),
                     "daily_minutes": daily}
            divergence, distance = _js_distance(_js_buckets(minutes),
                                                list(TIME_USE_REFERENCE_MINUTES.values()))
            if not math.isnan(distance):
                entry["js_divergence_bits"] = round(divergence, 9)
                entry["js_distance"] = round(distance, 9)
                js_values.append(distance)
            per_agent[f"{run_id}:{agent}"] = entry
    return {"per_agent": per_agent,
            "js_distance_mean": round(statistics.mean(js_values), 9) if js_values else None,
            "reference_minutes": dict(TIME_USE_REFERENCE_MINUTES), "reference_source": REFERENCE_SOURCE,
            "conventions": list(CONVENTIONS)}


def _persona_distinguishability(events: list[dict[str, Any]]) -> dict[str, Any]:
    """@brief 同代码 ≥3 次运行中按活动分布做留一最近邻的人物识别准确率（报告项）。"""
    totals = _family_minutes(events)
    vectors: dict[tuple[str, str], list[float]] = {}
    for run_id, agents in totals.items():
        for agent, minutes in agents.items():
            vector = [minutes.get(family, 0.0) for family in ACTIVITY_FAMILY_ORDER]
            if sum(vector) > 0:
                vectors[(run_id, agent)] = [value / sum(vector) for value in vector]
    runs = sorted({run_id for run_id, _ in vectors})
    agents = sorted({agent for _, agent in vectors})
    if len(runs) < 3 or len(agents) < 2:
        return {"accuracy": None, "run_count": len(runs), "agent_count": len(agents),
                "note": "需要同代码 ≥3 次运行且 ≥2 个人物才有留一最近邻结果"}
    correct = 0
    for (run_id, agent), vector in sorted(vectors.items()):
        neighbours = [((other_run, other_agent), other)
                      for (other_run, other_agent), other in vectors.items() if other_run != run_id]
        best_key, _ = min(neighbours, key=lambda item: (sum(abs(a - b) for a, b in zip(vector, item[1])),
                                                        item[0]))
        correct += best_key[1] == agent
    return {"accuracy": correct / len(vectors), "run_count": len(runs),
            "agent_count": len(agents), "vector_count": len(vectors)}


def _social(events: list[dict[str, Any]], notes: list[str]) -> dict[str, Any]:
    """@brief 统计交谈/通话分钟与发送/收到的消息数（报告项）。"""
    minutes: Counter[str] = Counter()
    missing_fields = 0
    sent = 0
    received = 0
    for event in events:
        data = event.get("data", {})
        if event.get("event_type") == "action_requested":
            if data.get("tool_name") == "send_message":
                sent += 1
            if data.get("tool_name") == "start_social_activity":
                request = data.get("request", {})
                duration = request.get("duration_minutes")
                if isinstance(request.get("kind"), str) and isinstance(duration, (int, float)):
                    minutes[request["kind"]] += float(duration)
                else:
                    missing_fields += 1
        if event.get("event_type") == "environment_event" and data.get("kind") == "message_arrival":
            received += 1
    if missing_fields:
        notes.append(f"{missing_fields} 个 start_social_activity 请求缺 kind/duration_minutes 字段")
    return {"chat_call_minutes": dict(sorted(minutes.items())),
            "sent_message_count": sent, "received_message_count": received}


def _bigrams(text: str) -> frozenset[str]:
    """@brief 取文本的字符 bigram 集合（中文记忆文本的确定性切分）。"""
    return frozenset(text[index:index + 2] for index in range(len(text) - 1))


def _episode_pairs(events: list[dict[str, Any]]) -> tuple[int, int, int]:
    """@brief 统计同类型记忆两两组合中 bigram Jaccard ≥ 0.6 的对数、总对数与文本条数。"""
    by_type: dict[str, list[str]] = {}
    texts_found = 0
    for event in events:
        if event.get("event_type") != "memory_updated":
            continue
        data = event.get("data", {})
        episodes = data.get("episodes")
        if not isinstance(episodes, list):
            continue
        for episode in episodes:
            if not isinstance(episode, Mapping) or not isinstance(episode.get("text"), str):
                continue
            texts_found += 1
            kind = str(episode.get("kind") or episode.get("type") or data.get("memory_type")
                       or "unclassified")
            by_type.setdefault(kind, []).append(episode["text"])
    qualifying = 0
    total = 0
    for texts in by_type.values():
        for index, left in enumerate(texts):
            for right in texts[index + 1:]:
                total += 1
                qualifying += _bigram_jaccard(left, right) >= 0.6
    return qualifying, total, texts_found


def _bigram_jaccard(left: str, right: str) -> float:
    """@brief 计算两段文本字符 bigram 集合的 Jaccard 相似度。"""
    left_set, right_set = _bigrams(left), _bigrams(right)
    union = left_set | right_set
    if not union:
        return 1.0 if left == right else 0.0
    return len(left_set & right_set) / len(union)


def _memory_repetition_gate(events: list[dict[str, Any]], notes: list[str]) -> dict[str, Any]:
    """@brief 计算同类型记忆两两 bigram Jaccard ≥ 0.6 的比例；门禁 ≤ 0.2。"""
    qualifying, total, texts_found = _episode_pairs(events)
    if texts_found == 0:
        notes.append("memory_updated 未提供 episodes 文本，记忆重复率无法计算")
        return _gate(None, "<=", 0.2)
    if total == 0:
        return _gate(0.0, "<=", 0.2, note="同类型记忆均不足两条，无重复对可比较")
    return _gate(round(qualifying / total, 6), "<=", 0.2,
                 note=f"{qualifying}/{total} 对同类型记忆 Jaccard ≥ 0.6")


def _memory_residue_gate(
    memory_md_texts: Mapping[int | str, str] | None, notes: list[str],
) -> dict[str, Any]:
    """@brief 检查 MEMORY.md 是否残留示例身份或示例编号；门禁必须为 0。"""
    if not memory_md_texts:
        notes.append("MEMORY.md 文本未提供，身份残留无法检查")
        return _gate(None, "==", 0)
    matched: dict[str, list[str]] = {}
    for agent, text in sorted(memory_md_texts.items(), key=lambda item: str(item[0])):
        hits = sorted(marker for marker in MEMORY_IDENTITY_MARKERS
                      if marker in text.lower())
        if hits:
            matched[str(agent)] = hits
    entry = _gate(len(matched), "==", 0)
    entry["matched_agents"] = matched
    return entry
