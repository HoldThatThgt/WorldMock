"""@brief 从冻结足迹事件确定性计算人物行为指标与协议门禁。

输入为足迹事件 dict 列表（Event.model_dump(mode="json") 形态，事件类型为冻结枚举）。
指标按事件顶层 code_sha256 分组输出，供写入 quality.json；全部计算确定性，
同一输入两次计算逐字节相等。协议门禁在证据缺失时按不通过处理（fail-closed）。

当前事件合同已提供共同身体结算的 decision_offered / wake_reasons、主观记忆快照及
实际活动与内容消费的区间。认证输入若缺少这些指标所需证据，报告以未知值和 notes
明示，相关协议门禁按不通过处理；不得把缺证据解释成零次事件。
观察重复门禁按（人物，模拟秒）的续接链计：同一秒内的续接复用链首 user 合法。事件顶层 code_sha256 缺失时
归入 code_sha256_unreported 组并注明，不猜测代码身份。
工作负荷 workload 与作息偏离 routine_deviation 需要事件之外的输入（一个可选的打包对象，见 workload_metrics），
没有它时两项为 null 并写说明；它们只报告，报告结构标识不变（docs/design/07-evaluation.md ）。
"""

from __future__ import annotations

from worldmock.footprints.intervals import valid_interval, body_settlements
from worldmock.footprints.purchases import project_purchase_facts
from worldmock.footprints.timeline_labels import local_day_bounds, window_days
from worldmock.life.action_contract import START_TOOLS
from worldmock.life.memory_evidence import valid_subjective_snapshot
import math
import statistics
from collections import Counter
from datetime import datetime
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from worldmock.evaluation.goal_metrics import day_plan_metrics
from worldmock.catalog.models import Location
from worldmock.footprints.app_usage import validate_activity_prefix
from worldmock.errors import IntegrityError
from worldmock.evaluation.errors import EvaluationError
from worldmock.evaluation.workload_metrics import WorkdayInputs, workday_reports

#: MEMORY.md 示例身份残留标记（来源：AS2 memory_runtime.py:55-56 巩固提示示例）。
MEMORY_IDENTITY_MARKERS = (
    "marketing analyst", "28-year-old female", "aoi 500038964", "aoi 500021584",
)

#: code_sha256 缺失时的分组键。
UNREPORTED_CODE_KEY = "code_sha256_unreported"

#: 各协议门禁缺失证据时的统一说明前缀。
_NOT_PROVIDED = "未提供"

#: 行动请求才开放的世界请求工具（start_main_activity）；带工具而没有它的请求（就寝反思）不是人物决策请求。
_ACTION_TOOL = START_TOOLS[0]


def behavior_metrics_report(
    events: list[dict[str, Any]],
    *,
    timezone: str = "Asia/Shanghai",
    locations: Mapping[str, Location] | None = None,
    memory_md_texts: Mapping[int | str, str] | None = None,
    workday: WorkdayInputs | None = None,
) -> dict[str, Any]:
    """@brief 计算全部行为指标，按事件携带的 code_sha256 分组返回报告。

    :param events: 冻结足迹事件 dict 列表（可跨多次运行）。
    :param timezone: 模拟时间的本地时区（默认与全部冻结场景一致的 Asia/Shanghai）。
    :param locations: 地点身份到当前目录地点对象的映射；提供才计算早到分钟。
    :param memory_md_texts: 人物编号到 MEMORY.md 文本的映射；提供才计算身份残留。
    :param workday: 工作负荷与作息偏离的打包输入（workload_metrics.workday_inputs）；提供才计算这两项。
    :returns: {code_sha256: {schema_version, gates, reports, notes}} 的报告字典。
    """
    try:
        validate_activity_prefix(events)
    except IntegrityError as exc:
        raise EvaluationError("invalid_input", str(exc)) from exc
    groups: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        groups.setdefault(str(event.get("code_sha256") or UNREPORTED_CODE_KEY), []).append(event)
    reports: dict[str, Any] = {}
    goal_reports = day_plan_metrics(events, timezone)
    for code, group_events in sorted(groups.items()):
        reports[code] = _group_report(group_events, timezone, locations, memory_md_texts, workday)
        reports[code]["reports"].update({key: value for key, value in goal_reports[code].items() if key != "gates"})
        reports[code]["gates"].update(goal_reports[code]["gates"])
        if code == UNREPORTED_CODE_KEY:
            reports[code]["notes"].append("事件未携带 code_sha256，归入未上报组")
    return reports


def _group_report(
    events: list[dict[str, Any]],
    timezone: str,
    locations: Mapping[str, Location] | None,
    memory_md_texts: Mapping[int | str, str] | None,
    workday: WorkdayInputs | None,
) -> dict[str, Any]:
    """@brief 汇总单个代码组内的协议门禁、报告项与口径说明；工作负荷与作息偏离按打包输入算一次，按真实观察事实计算。"""
    notes: list[str] = []
    gates = {
        "observation_repetition": _observation_gate(events, notes),
        "silent_boundary_ratio": _silent_boundary_gate(events, notes),
        "memory_repetition_ratio": _memory_repetition_gate(events, notes),
        "memory_md_identity_residue": _memory_residue_gate(memory_md_texts, notes),
    }
    token_median = _decision_token_median(events, notes)
    early = _early_minutes(events, timezone, locations, notes)
    workload = workday_reports(events, timezone, workday)
    notes.extend(workload["notes"])
    reports = {
        "decision_prompt_token_median": token_median,
        "wake_reason_distribution": _wake_reason_distribution(events, notes),
        "content_contact_minutes": _content_contact_minutes(events),
        "person_elapsed_minutes": _person_elapsed_minutes(events, notes),
        "early_arrival_minutes": early,
        "sleep_schedule": _sleep_schedule(events, timezone, notes),
        "meal_diversity": _meal_diversity(events),
        "workload": workload["workload"],
        "routine_deviation": workload["routine_deviation"],
    }
    return {"schema_version": "behavior-metrics-3", "gates": gates, "reports": reports, "notes": notes}


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


def _request_tool_names(request: Any) -> list[str]:
    """@brief 一次模型请求实际开放的工具名；没有工具或形状不符时为空列表。"""
    tools = request.get("tools") if isinstance(request, Mapping) else None
    return [item["function"].get("name") for item in tools if isinstance(item, Mapping)
            and isinstance(item.get("function"), Mapping)] if isinstance(tools, list) else []


def _decision_model_requests(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """@brief 取人物决策的模型请求事件：排除任一带 purpose 的系统请求（工作验收、零点计划 plan_day）；带工具的请求
    只在工具中有 start_main_activity 时计入，所以只带 submit_reflection 的就寝反思及其校验重试不计，不带工具的记忆巩固
    照计（docs/design/07-evaluation.md 「身份与判定」）。"""
    selected = []
    for event in events:
        data = event.get("data", {})
        if event.get("event_type") != "model_request" or data.get("phase") != "request":
            continue
        names = _request_tool_names(data.get("request"))
        if not event.get("data", {}).get("purpose") and (not names or _ACTION_TOOL in names):
            selected.append(event)
    return selected


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
    """@brief 统计同一 user 观察文本出现于不同（人物，模拟秒）续接链中的最大数目；门禁必须为 1。

    同一人物同一模拟秒内的决策构成一条续接链，链内各决策复用链首 user 合法，只计一次；
    跨秒或跨人物重复仍计为重复。带 purpose 的系统请求（工作验收、零点计划）不参与统计；就寝反思的请求（含校验重试，
    其附回消息不带时刻与人物内容，不是观察）也不参与。
    """
    chains: dict[str, set[tuple[Any, Any]]] = {}
    user_messages = 0
    for event in _decision_model_requests(events):
        messages = event.get("data", {}).get("request", {}).get("messages", [])
        for message in messages if isinstance(messages, list) else []:
            text = _message_text(message) if isinstance(message, Mapping) else None
            if text is not None:
                user_messages += 1
                chains.setdefault(text, set()).add((event.get("agent_id"), event.get("occurred_at")))
    if not chains:
        notes.append("决策 model_request 无 user 消息，观察重复率无法计算")
        return _gate(None, "==", 1)
    counts = [len(keys) for keys in chains.values()]
    entry = _gate(max(counts), "==", 1)
    entry["duplicated_observation_count"] = sum(1 for count in counts if count > 1)
    entry["user_message_count"] = user_messages
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
    sleep_ids = {event.get("activity_id") for event in _activity_progress_events(events)
                 if _snapshot(event).get("kind") == "sleep"}
    waking = [event for event in body_settlements(events)
              if not event.get("data", {}).get("activity_ids")
              or any(identity not in sleep_ids for identity in event["data"]["activity_ids"])]
    known = [event for event in waking if "decision_offered" in event.get("data", {})]
    if not known:
        notes.append("body_settlement.data.decision_offered 未提供，静默边界占比无法计算")
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








#: 只做结束、暂停或修订的控制轮：单列计数，不计入自主或被动（docs/design/07-evaluation.md「2.2 报告项」）。




def _wake_reason_distribution(events: list[dict[str, Any]], notes: list[str]) -> dict[str, Any]:
    """@brief 统计唤醒原因计数（报告项），分两份：边界取共同身体结算，决策轮取 decision_started。

    同刻轮次不写共同身体结算，其原因只在 decision_started.data.wake_reasons 中计数。
    """
    boundaries = body_settlements(events)
    counts, with_reasons = _reason_counts(boundaries)
    started = [event for event in events if event.get("event_type") == "decision_started"]
    started_counts, started_with_reasons = _reason_counts(started)
    if with_reasons == 0:
        notes.append("body_settlement.data.wake_reasons 未提供，唤醒原因分布为空")
    return {"counts": dict(sorted(counts.items())), "events_with_wake_reasons": with_reasons,
            "body_settlement_events": len(boundaries),
            "decision_started_counts": dict(sorted(started_counts.items())),
            "decision_started_events_with_wake_reasons": started_with_reasons}


def _reason_counts(events: list[dict[str, Any]]) -> tuple[Counter[str], int]:
    """@brief 汇总事件 data.wake_reasons 的成员计数，并数出以列表携带该字段的事件数。"""
    counts: Counter[str] = Counter()
    with_reasons = 0
    for event in events:
        reasons = event.get("data", {}).get("wake_reasons")
        if isinstance(reasons, list):
            with_reasons += 1
            counts.update(str(reason) for reason in reasons)
    return counts, with_reasons


def _interval_minutes(event: Mapping[str, Any]) -> float:
    """@brief 取单个结算区间的分钟数（interval_end - interval_start）。"""
    data = event.get("data", {})
    start, end = data.get("interval_start"), data.get("interval_end")
    if valid_interval(start, end):
        return (end - start) / 60.0
    return 0.0


def _early_minutes(
    events: list[dict[str, Any]], timezone: str,
    locations: Mapping[str, Location] | None, notes: list[str],
) -> dict[str, Any]:
    """@brief 统计真实到达早于开门的分钟，不累计等待。"""
    arrivals: dict[str, list[tuple[str, float]]] = {}
    for event in _activity_progress_events(events):
        kind = _snapshot(event).get("kind")
        agent = str(event.get("agent_id"))
        detail = _snapshot(event).get("data")
        arrived = isinstance(detail, dict) and isinstance(detail.get("arrival"), dict)
        if kind == "travel" and arrived and locations:
            destination = str(_snapshot(event).get("destination_id"))
            if destination in locations:
                arrivals.setdefault(destination, []).append((agent, _interval_end(event)))
    early: dict[str, Any] = {"value": None}
    if locations is not None:
        early = _early_arrival_summary(arrivals, locations, timezone)
    else:
        notes.append("locations 未提供，早到分钟无法计算")
    return early


def _content_contact_minutes(events: list[dict[str, Any]]) -> dict[str, Any]:
    """@brief 只计真实前进的媒体位置，不把等待或浏览目录算内容接触。"""
    per_agent: dict[str, float] = {}
    for event in _activity_progress_events(events):
        content = event.get("data", {}).get("content")
        if not isinstance(content, dict):
            continue
        before, after = content.get("position_before_seconds"), content.get("position_after_seconds")
        if isinstance(before, (int, float)) and isinstance(after, (int, float)) and after > before:
            agent = str(event.get("agent_id"))
            per_agent[agent] = per_agent.get(agent, 0.0) + _interval_minutes(event)
    return {"per_agent_minutes": dict(sorted(per_agent.items())),
            "total_minutes": round(sum(per_agent.values()), 6)}


def _person_elapsed_minutes(events: list[dict[str, Any]], notes: list[str]) -> dict[str, Any]:
    """@brief 共同结算按人物取区间并集，保留活动家族的独立时间口径。"""
    by_agent: dict[str, list[tuple[float, float]]] = {}
    missing = 0
    for event in body_settlements(events):
        start, end = event.get("data", {}).get("interval_start"), event.get("data", {}).get("interval_end")
        if not valid_interval(start, end):
            missing += 1
            continue
        by_agent.setdefault(str(event.get("agent_id")), []).append((start, end))
    per_agent = {agent: round(_interval_union_seconds(intervals) / 60.0, 6)
                 for agent, intervals in sorted(by_agent.items())}
    if missing:
        notes.append(f"{missing} 条共同身体结算缺少有效区间，人物经过时长不完整")
    return {"per_agent_minutes": per_agent if missing == 0 else None,
            "known_per_agent_minutes": per_agent, "missing_interval_events": missing}


def _interval_union_seconds(intervals: list[tuple[float, float]]) -> float:
    """@brief 合并相交或接续的真实时间区间。"""
    total, previous_end = 0.0, 0.0
    for start, end in sorted(intervals):
        total += max(0.0, end - max(start, previous_end))
        previous_end = max(previous_end, end)
    return total


def _interval_end(event: Mapping[str, Any]) -> int | float:
    """@brief 已结束活动读取终局时刻，仍在进行的活动读取真实结算区间终点。"""
    snapshot_data = _snapshot(event)
    ended = snapshot_data.get("ended_at")
    if type(ended) is int:
        return ended
    end = event.get("data", {}).get("interval_end")
    if type(end) is not int:
        raise EvaluationError("invalid_input", "Activity progress requires an integer interval_end")
    return end


def _early_arrival_summary(
    arrivals: Mapping[str, list[tuple[str, float]]], locations: Mapping[str, Location],
    timezone: str,
) -> dict[str, Any]:
    """@brief 按本地日期的实际营业安排与例外计算早到分钟。"""
    zone = ZoneInfo(timezone)
    per_location: dict[str, float] = {}
    per_agent: dict[str, float] = {}
    for destination, samples in sorted(arrivals.items()):
        for agent, arrival in samples:
            local = datetime.fromtimestamp(arrival, zone)
            intervals = locations[destination].opening_intervals(local.date())
            opening = intervals[0][0] if intervals else None
            early = 0.0 if opening is None else max(0.0,
                (local.replace(hour=int(opening[:2]), minute=int(opening[3:]), second=0, microsecond=0)
                 - local).total_seconds() / 60.0)
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
                    if valid_interval(record["start"], record["end"])),
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
        """将真实时刻转换为本地日内分钟。"""
        local = datetime.fromtimestamp(epoch, zone)
        return local.hour * 60 + local.minute

    bed = statistics.median(sorted(minutes_of_day(item["start"]) for item in valid))
    rise = statistics.median(sorted(minutes_of_day(item["end"]) for item in valid))
    duration = statistics.mean((item["end"] - item["start"]) / 3600.0 for item in valid)
    return {"median_bed_minutes_of_day": bed, "median_rise_minutes_of_day": rise,
            "mean_duration_hours": round(duration, 4), "session_count": len(valid)}


def _meal_sessions(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """@brief 每张本人食品收货订单是一次真实收货，不推导用餐区间。"""
    return [{"occurred_at": row["received_at"], "products": [row["goods_id"]], "order_id": row["order_id"]}
            for row in project_purchase_facts(events) if row["fact_kind"] == "receipt"
            and row["category"] in {"meal", "snack"}]


def _meal_diversity(events: list[dict[str, Any]]) -> dict[str, Any]:
    """@brief 只以本人食品收货的商品标识计算 Simpson 集中度与 24 小时重复率。"""
    consumed = _meal_sessions(events)
    product_counts: Counter[str] = Counter()
    for event in consumed:
        product_counts.update(event["products"])
    total_units = sum(product_counts.values())
    if not product_counts or not total_units:
        return {"simpson_concentration": None, "repetition_24h_rate": None,
                "receipt_count": len(consumed), "distinct_products": len(product_counts)}
    simpson = sum((count / total_units) ** 2 for count in product_counts.values())
    return {"simpson_concentration": round(simpson, 6),
            "repetition_24h_rate": _repetition_24h(consumed),
            "receipt_count": len(consumed), "distinct_products": len(product_counts),
            "product_counts": dict(sorted(product_counts.items()))}


def _repetition_24h(consumed: list[dict[str, Any]]) -> float | None:
    """@brief 计算与前 24 小时内已食商品重复的进食动作占比。"""
    ordered = sorted(consumed, key=lambda event: (event.get("occurred_at", 0),
                                                  event.get("event_seq", 0),
                                                  str(event.get("event_id"))))
    history: list[tuple[int, set[str]]] = []
    repeated = 0
    for event in ordered:
        products = set(event["products"])
        occurred_at = event.get("occurred_at", 0)
        if any(products & seen and 0 < occurred_at - at <= 86400 for at, seen in history):
            repeated += 1
        history.append((occurred_at, products))
    return repeated / len(ordered)


















def _bigrams(text: str) -> frozenset[str]:
    """@brief 取文本的字符 bigram 集合（中文记忆文本的确定性切分）。"""
    return frozenset(text[index:index + 2] for index in range(len(text) - 1))


def _episode_pairs(events: list[dict[str, Any]]) -> tuple[int, int, int, int]:
    """@brief 仅从规范主观快照统计同类型 bigram Jaccard；畸形快照记缺证。"""
    by_type: dict[str, list[str]] = {}
    texts_found = 0
    malformed = 0
    for event in events:
        if event.get("event_type") != "memory_updated":
            continue
        data = event.get("data", {})
        episodes = data.get("episodes")
        if not isinstance(episodes, list):
            malformed += 1
            continue
        for episode in episodes:
            if not valid_subjective_snapshot(episode):
                malformed += 1
                continue
            texts_found += 1
            by_type.setdefault(episode["type"], []).append(episode["text"])
    qualifying = 0
    total = 0
    for texts in by_type.values():
        for index, left in enumerate(texts):
            for right in texts[index + 1:]:
                total += 1
                qualifying += _bigram_jaccard(left, right) >= 0.6
    return qualifying, total, texts_found, malformed


def _bigram_jaccard(left: str, right: str) -> float:
    """@brief 计算两段文本字符 bigram 集合的 Jaccard 相似度。"""
    left_set, right_set = _bigrams(left), _bigrams(right)
    union = left_set | right_set
    if not union:
        return 1.0 if left == right else 0.0
    return len(left_set & right_set) / len(union)


def _memory_repetition_gate(events: list[dict[str, Any]], notes: list[str]) -> dict[str, Any]:
    """@brief 计算同类型记忆两两 bigram Jaccard ≥ 0.6 的比例；门禁 ≤ 0.2。"""
    qualifying, total, texts_found, malformed = _episode_pairs(events)
    if malformed:
        notes.append(f"{malformed} 条主观记忆快照缺失或畸形，重复率无法计算")
        return _gate(None, "<=", 0.2)
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
