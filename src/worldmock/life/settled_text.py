"""@brief 本人可见成功事实的确定性句子：订单、发送与收到的消息、活动实际终局以及收货记录。"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
import logging
from typing import Any

from worldmock.errors import IntegrityError
from worldmock.footprints.models import Event
from worldmock.life import activity_control
from worldmock.life.vocabulary import PURCHASE_WAYS
from worldmock.life.models import ActivityKind, ActivityState, TravelMode
from worldmock.life.readable import clock_phrase, duration_phrase, iso_time, money_phrase, portion_phrase
from worldmock.life.recipients import agent_identity, person_name
from worldmock.life.tool_layer import LayerContext

#: 成功的结果状态：只有这些结果写 settled 句。
SETTLED_STATUSES = frozenset({"completed", "in_progress"})
#: 活动的暂停区间：活动身份 → [(暂停时刻, 恢复时刻)]（只含已恢复的区间）。
PauseIntervals = Mapping[str, Sequence[tuple[int, int]]]
#: 活动的终态（快照 status 的取值）。
_ENDED_STATUSES = frozenset({"completed", "cancelled", "failed", "truncated"})


@dataclass(frozen=True, slots=True)
class ActivityFacts:
    """@brief 结束活动的句子所需的事实：已恢复的暂停区间与各活动的结束事件（带其终态快照的最早事件）。"""

    pauses: PauseIntervals
    endings: Mapping[str, Event]
    #: 本活动当前尚未看完但确有正向消费的内容，完全由活动进度事件派生。
    app_progress: Mapping[str, str]


def record_lines(context: LayerContext, record: dict, sources: list[Event],
                 facts: ActivityFacts | None = None) -> list[str]:
    """@brief 一条进度层记录的 settled 句（没有可写的事实时为 []）。

    :param record: 进度层记录（occurred_at 为带时区 ISO）。
    :param sources: 记录的来源事件（tool_call 为 action_requested 与 action_result；派生记录为空）；重放的请求不写。
    :param facts: 进度层浓缩用：conclude_activity 写它所结束的、结束事件在 facts.endings 中的活动（本 session 内结束的）；
        为 None 时结束活动的调用不写句子（活动句按结束提交另写，见 ending_lines）。
    """
    if any(event.data.get("reused") for event in sources):
        return []
    kind = record["type"]
    if kind == "tool_call":
        return _tool_lines(context, record, sources, facts)
    if kind == "message_arrival":
        when = _clock(context, record["occurred_at"])
        if record["kind"] == "mail":
            return [f"{when} 收到邮件「{record['subject']}」"]
        if record["kind"] == "chat_invite":
            return []
        return [f"{when} 收到{record['sender']}的消息：{record['text']}"]
    if kind == "delivery":
        return [f"{_clock(context, record['occurred_at'])} {record['order']}：{record['message']}"]
    if kind == "work_completed":
        return [f"{_clock(context, record['occurred_at'])} 完成工作主题「{record['topic']}」"]
    if kind == "errand_completed":
        return [f"{_clock(context, record['occurred_at'])} 办成「{record['errand']}」"]
    return []


def activity_sentence(context: LayerContext, activity: ActivityState,
                      pauses: Sequence[tuple[int, int]] = (), app_progress: Mapping[str, str] | None = None) -> str:
    """@brief 一项已结束活动的句子：名称、起止（含暂停区间）、终局，终局与收尾结果 details.outcome 同一结算事实。

    例：「工作：会议纪要（1月5日 09:10 起）：09:10–11:50（暂停 10:43–11:16），提前结束」。
    """
    now, zone = context.state.current_time, context.scenario.timezone
    name = context.names["activities"].get(activity.activity_id, activity.name)
    start = clock_phrase(activity.started_at, now, zone)
    end = clock_phrase(activity.ended_at or activity.last_settled_at, now, zone)
    paused = "、".join(f"{clock_phrase(begin, now, zone)}–{clock_phrase(until, now, zone)}" for begin, until in pauses)
    sentence = f"{name}：{start}–{end}{f'（暂停 {paused}）' if paused else ''}，{activity_control.ending_phrase(activity)}"
    if activity.kind == ActivityKind.USE_APP:
        if app_progress is None:
            logging.error("App activity sentence has no authenticated progress evidence")
            raise IntegrityError("App activity sentence has no authenticated progress evidence")
        content = _app_sentence(context, activity, app_progress.get(activity.activity_id))
        sentence += f"，{content}" if content else ""
    return sentence


def _app_sentence(context: LayerContext, activity: ActivityState, advanced_content_id: str | None) -> str:
    """@brief 按已完成列表与当前真实推进写应用内容；刚抽到但尚未推进的内容不计入经历。"""
    titles = "".join(f"《{context.rules.contents[row['content_id']].title}》" for row in activity.data["played"])
    current = context.rules.contents[activity.data["content_id"]]
    partial = advanced_content_id == current.content_id
    if not titles and not partial:
        return ""
    app = context.rules.apps[activity.target_id].name
    if not titles:
        return f"用{app}看了《{current.title}》的一部分"
    return f"用{app}看了{titles}" + (f"（《{current.title}》看了一部分）" if partial else "")


def app_progress(events: Iterable[Event], previous: Mapping[str, str]) -> dict[str, str]:
    """@brief 从认证的本活动消费区间派生当前部分观看；完成时清除，自动抽出的下一项不算消费。"""
    progress = dict(previous)
    for event in events:
        if (event.event_type != "activity_progress" or event.source_kind != "environment"
                or event.visibility != "agent" or event.data["activity"]["kind"] != "use_app"):
            continue
        content = event.data["content"]
        if content["completed"]:
            progress.pop(event.activity_id, None)
        elif content["position_after_seconds"] > content["position_before_seconds"]:
            progress[event.activity_id] = content["content_id"]
    return progress

def purchase_sentence(context: LayerContext, order: dict[str, Any], answering: str | None) -> str:
    """@brief 一个订单的句子：「10:20 远程下单牛肉炒面 1 份，付 32 元，预计 10:35 送到<地点>（回应王磊 10:15 的消息，
    收货人王磊）」；到店不写送达，没有关联消息不写回应，收货人是自己时不写收货人。

    :param answering: 下单请求的 answering（关联消息的名称），订单没有关联时不写。
    """
    now, zone = context.state.current_time, context.scenario.timezone
    product = context.semantics.catalog.goods[order["goods_id"]].name
    sentence = (f"{clock_phrase(order['created_at'], now, zone)} {PURCHASE_WAYS[order['fulfillment']]}"
                f"{'买' if order['fulfillment'] == 'pickup' else ''}{product} {portion_phrase(order['quantity'])}，"
                f"付 {money_phrase(order['total_price_fen'])}")
    if order["fulfillment"] == "delivery":
        place = next(item.name for item in context.scenario.locations if item.location_id == order["destination_id"])
        sentence += f"，预计 {clock_phrase(order['expected_at'], now, zone)} 送到{place}"
    notes = [f"回应{answering}"] if answering is not None and order["request_message_id"] is not None else []
    if order["recipient_id"] != agent_identity(context.agent_id):
        notes.append(f"收货人{person_name(context.state, context.scenario, context.agent_id, order['recipient_id'])}")
    return sentence + (f"（{'，'.join(notes)}）" if notes else "")






def experience_sentence(context: LayerContext, fact: dict[str, Any]) -> str:
    """@brief 已核验经历写实际时刻或区间，商品与内容取冻结目录，出行方式取所属活动。"""
    now, zone, scenario = context.state.current_time, context.scenario.timezone, context.scenario
    if fact["kind"] == "arrived":
        travel = context.state.activities[fact["activity_id"]]
        mode = "走路" if travel.travel_mode == TravelMode.WALKING else "开车"
        place = next(item.name for item in scenario.locations if item.location_id == fact["location_id"])
        denied = "，但没能进去" if fact["access_status"] == "denied" else ""
        return f"{clock_phrase(fact['occurred_at'], now, zone)} {mode}到了{place}{denied}"
    if fact["kind"] == "received":
        goods = context.semantics.catalog.goods[fact["goods_id"]]
        return f"{clock_phrase(fact['occurred_at'], now, zone)} 收到{goods.name}"
    span = f"{clock_phrase(fact['interval_start'], now, zone)}–{clock_phrase(fact['interval_end'], now, zone)}"
    if fact["kind"] == "waited":
        return f"{span} 等待了{duration_phrase(fact['duration_seconds'])}"
    app = context.rules.apps[fact["app_id"]].name
    title = context.rules.contents[fact["content_id"]].title
    return f"{span} 用{app}看了《{title}》"


def activity_endings(events: Iterable[Event]) -> dict[str, Event]:
    """@brief 本人活动的结束事件：带该活动终态快照（status 为 completed、cancelled、failed 或 truncated）的最早事件；
    activity_progress、生命周期与截断 environment_event 带 data.activity，终态 action_result 另可带 data.data.activities。"""
    endings: dict[str, Event] = {}
    for event in events:
        payload = event.data.get("data")
        rows = payload.get("activities", []) if isinstance(payload, dict) else []
        for snapshot in (event.data.get("activity"), *rows):
            if not isinstance(snapshot, dict) or snapshot.get("status") not in _ENDED_STATUSES:
                continue
            known = endings.get(snapshot["activity_id"])
            if known is None or event.event_seq < known.event_seq:
                endings[snapshot["activity_id"]] = event
    return endings


def ending_lines(context: LayerContext, facts: ActivityFacts,
                 keep: Callable[[Event], bool]) -> list[tuple[Event, str]]:
    """@brief 结束事件满足 keep 的本人活动各一句（activity_sentence，起止含已恢复的暂停区间），按结束事件的序号排列。"""
    rows = [(event, activity_sentence(context, context.state.activities[activity_id],
                                      facts.pauses.get(activity_id, ()), facts.app_progress))
            for activity_id, event in facts.endings.items() if keep(event)]
    return sorted(rows, key=lambda row: row[0].event_seq)


def pause_intervals(events: Iterable[Event]) -> dict[str, list[tuple[int, int]]]:
    """@brief 本人 environment_event 中的暂停与恢复（activity_paused、activity_resumed）配成已恢复的暂停区间。"""
    opened: dict[str, int] = {}
    intervals: dict[str, list[tuple[int, int]]] = {}
    for event in events:
        kind, activity_id = event.data.get("kind"), event.data.get("activity_id")
        if kind == "activity_paused":
            opened[activity_id] = event.occurred_at
        elif kind == "activity_resumed" and activity_id in opened:
            intervals.setdefault(activity_id, []).append((opened.pop(activity_id), event.occurred_at))
    return intervals


def tool_record(context: LayerContext, request: Event) -> dict:
    """@brief 由 action_requested 事件得到 record_lines 所读的 tool_call 记录字段（发生时刻、工具与参数原文）。"""
    return {"occurred_at": iso_time(request.occurred_at, context.scenario.timezone), "type": "tool_call",
            "tool": request.data["tool_name"], "arguments": request.data["request"]}


def _tool_lines(context: LayerContext, record: dict, sources: list[Event], facts: ActivityFacts | None) -> list[str]:
    """@brief 世界请求的 settled 句：下单、给予、发消息与提交工作；结束活动只在给了 facts 时写（进度层浓缩）；失败的请求与
    其他工具不写。

    句子取请求的即时结果（sources 中第一个 action_result）：瞬时请求只有这一个结果，开始活动的请求其后的终态结果
    不改变当时下的订单；记忆查询没有 action_result，不写。
    """
    result = next((event.data for event in sources if event.event_type == "action_result"), None)
    if result is None:
        return []
    status, tool = result["status"], record["tool"]
    if status not in SETTLED_STATUSES:
        return []
    payload = result.get("data") or {}
    if tool == "send_message":
        person = record["arguments"]["recipient_name"].split("：", 1)[-1]
        return [f"{_clock(context, record['occurred_at'])} 给{person}发消息：{record['arguments']['text']}"]
    if tool == "conclude_activity":
        return [] if facts is None else [
            activity_sentence(context, context.state.activities[activity_id], facts.pauses.get(activity_id, ()),
                              facts.app_progress)
            for activity_id in payload.get("activity_ids", []) if activity_id in facts.endings]
    lines = [purchase_sentence(context, order, payload.get("answering")) for order in payload.get("orders", [])]
    return lines


def _clock(context: LayerContext, moment: str) -> str:
    """@brief 记录时刻（带时区 ISO）写成短语里的钟点。"""
    epoch = int(datetime.fromisoformat(moment).timestamp())
    return clock_phrase(epoch, context.state.current_time, context.scenario.timezone)
