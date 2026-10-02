"""@brief settled 写法的确定性句子（docs/dev/SPEC-persona-prompt.md 第三章「settled 的写法」）。

只写人物可见、成功（completed 或 in_progress）且不是重放的世界请求与世界事件：下单（带所回应的消息与收货人）、给予、
发消息、提交工作各一句，在本段结束的活动每项一句（起止含暂停区间、终局、实际份量或有效工时，完成的浏览另写浏览到的
完整标签），收到的直接消息（全文）、
邮件（主题）、送达与新工作各一句。不写失败的请求、记忆查询、回看、report_status、暂停与恢复、revise_day_plan、提醒、
截止预警、身体结算与交谈邀请。金额按元，份数至多两位小数，时长用人读短语，跨日加「M月D日」。记忆层、进度层按预算浓缩的
lines 与零点计划输入都取这里的句子（同时是零点计划输入里已核验经历句子的唯一写法）。

活动的句子按它结束的提交归属（第三章「归属」）：结束提交是带该活动终态快照的最早事件（activity_progress、生命周期与截断
environment_event、终态 action_result）所在的提交，到达时结束的出行归路程，实验截止截断的活动归最后一段；之后的
conclude_activity 本身不写句子，只在进度层浓缩时带出它所结束的、在本 session 内结束的活动（更早结束的已在记忆层）。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from worldmock.footprints.models import Event
from worldmock.life import activity_control
from worldmock.life.activity_evidence import is_eating
from worldmock.life.interaction_menu import PURCHASE_WAYS
from worldmock.life.models import ActivityKind, ActivityState, TravelMode
from worldmock.life.readable import clock_phrase, duration_phrase, iso_time, money_phrase, portion_phrase
from worldmock.life.recipients import agent_identity, person_name
from worldmock.life.result_projection import browse_labels
from worldmock.life.tool_layer import LayerContext

#: 成功的结果状态：只有这些结果写 settled 句。
_SETTLED_STATUSES = frozenset({"completed", "in_progress"})
#: 活动的暂停区间：活动身份 → [(暂停时刻, 恢复时刻)]（只含已恢复的区间）。
PauseIntervals = Mapping[str, Sequence[tuple[int, int]]]
#: 活动的终态（快照 status 的取值）。
_ENDED_STATUSES = frozenset({"completed", "cancelled", "failed", "truncated"})


@dataclass(frozen=True, slots=True)
class ActivityFacts:
    """@brief 结束活动的句子所需的事实：已恢复的暂停区间与各活动的结束事件（带其终态快照的最早事件）。"""

    pauses: PauseIntervals
    endings: Mapping[str, Event]


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
    return []


def activity_sentence(context: LayerContext, activity: ActivityState,
                      pauses: Sequence[tuple[int, int]] = ()) -> str:
    """@brief 一项已结束活动的句子：名称、起止（含暂停区间）、终局，工作另写有效工时，进食另写吃掉与丢弃的份数
    （与收尾结果 details.outcome 同一结算事实）。

    例：「工作：会议纪要（1月5日 09:10 起）：09:10–11:50（暂停 10:43–11:16），提前结束，有效工时约 2 小时」。
    """
    now, zone = context.state.current_time, context.scenario.timezone
    name = context.names["activities"].get(activity.activity_id, activity.name)
    start = clock_phrase(activity.started_at, now, zone)
    end = clock_phrase(activity.ended_at or activity.last_settled_at, now, zone)
    paused = "、".join(f"{clock_phrase(begin, now, zone)}–{clock_phrase(until, now, zone)}" for begin, until in pauses)
    sentence = f"{name}：{start}–{end}{f'（暂停 {paused}）' if paused else ''}，{activity_control.ending_phrase(activity)}"
    if activity.kind == ActivityKind.WORK:
        sentence += f"，有效工时{duration_phrase(activity.effective_work_seconds)}"
    if is_eating(activity):
        sentence += f"，吃了 {portion_phrase(sum(activity.data.get('consumed', {}).values()))}"
        discarded = sum(activity.data.get("discarded", {}).values())
        sentence += f"，丢弃 {portion_phrase(discarded)}" if discarded > 0 else ""
    return sentence + _browse_clause(context, activity) + _recipe_clause(context, activity)


def _recipe_clause(context: LayerContext, activity: ActivityState) -> str:
    """@brief 已完成的库存转换附实际输入与产物；没有结算的制作不声称得到菜品。"""
    recipe = activity.data.get("inventory_recipe")
    if recipe is None or not activity.data.get("recipe_applied"):
        return ""
    source = context.rules.items[recipe["input_object_id"]].name
    output = context.rules.items[recipe["output_object_id"]].name
    return (f"，消耗{source} {portion_phrase(float(recipe['input_quantity']))}，"
            f"制作得到{output} {portion_phrase(float(recipe['output_quantity']))}")


def _browse_clause(context: LayerContext, activity: ActivityState) -> str:
    """@brief 完成的浏览另写浏览到的完整标签「，浏览到<门店>出售的<商品>、……」：session 结束后浏览结果随 settled
    进记忆层，离开浏览地点后人物照着按名下单（docs/dev/SPEC-dayrun-fidelity.md 5.3，裁决 D17）；没有取得结果的
    浏览不写。"""
    revealed = activity.data.get("revealed") if activity.kind == ActivityKind.BROWSE else None
    labels = browse_labels([revealed], context.scenario) if isinstance(revealed, dict) else []
    return f"，浏览到{'、'.join(labels)}" if labels else ""


def purchase_sentence(context: LayerContext, order: dict[str, Any], answering: str | None) -> str:
    """@brief 一个订单的句子：「10:20 远程下单牛肉炒面 1 份，付 32 元，预计 10:35 送到<地点>（回应王磊 10:15 的消息，
    收货人王磊）」；到店不写送达，没有关联消息不写回应，收货人是自己时不写收货人。

    :param answering: 下单请求的 answering（关联消息的名称），订单没有关联时不写。
    """
    now, zone = context.state.current_time, context.scenario.timezone
    product = context.rules.items[order["product_id"]].name
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


def give_sentence(context: LayerContext, moment: str, item: dict[str, Any]) -> str:
    """@brief 一次给予的句子：「10:40 把牛肉炒面 1 份给了王磊」（moment 为请求时刻的带时区 ISO）。"""
    product = context.rules.items[item["product_id"]].name
    person = person_name(context.state, context.scenario, context.agent_id, item["recipient_id"])
    return f"{_clock(context, moment)} 把{product} {portion_phrase(item['quantity'])}给了{person}"


def work_line(context: LayerContext, event: Event) -> str:
    """@brief 新工作到来（environment_event 的 work_available）的句子：「09:00 收到新工作「<任务>」」。"""
    moment = clock_phrase(event.occurred_at, context.state.current_time, context.scenario.timezone)
    return f"{moment} 收到新工作「{context.rules.tasks[event.data['task_id']].title}」"


def experience_sentence(context: LayerContext, fact: dict[str, Any]) -> str:
    """@brief 一条已核验经历的句子（零点计划输入）：等待、食用、播放带起止时刻，到达带到达时刻与方式。

    例：「14:17 开车到了张江办公室」「13:57–14:17 吃了番茄炒蛋」；地点、商品与内容取场景名称，出行方式取自所属出行活动。
    """
    now, zone, scenario = context.state.current_time, context.scenario.timezone, context.scenario
    if fact["kind"] == "arrived":
        travel = context.state.activities[fact["activity_id"]]
        mode = "走路" if travel.travel_mode == TravelMode.WALKING else "开车"
        place = next(item.name for item in scenario.locations if item.location_id == fact["location_id"])
        denied = "，但没能进去" if fact["access_status"] == "denied" else ""
        return f"{clock_phrase(fact['occurred_at'], now, zone)} {mode}到了{place}{denied}"
    span = f"{clock_phrase(fact['interval_start'], now, zone)}–{clock_phrase(fact['interval_end'], now, zone)}"
    if fact["kind"] == "waited":
        return f"{span} 等待了{duration_phrase(fact['duration_seconds'])}"
    if fact["kind"] == "consumed":
        names = {item.product_id: item.name for item in (*scenario.products, *scenario.inventory_items)}
        return f"{span} 吃了{names[fact['product_id']]}"
    podcast = any(item.item_id == fact["content_id"] and item.kind == "podcast_episode" for item in scenario.contents)
    return f"{span} {'听了' if podcast else '看了'}《{context.rules.content_titles[fact['content_id']]}》"


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
                                      facts.pauses.get(activity_id, ())))
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
    if tool == "submit_work" and (status in _SETTLED_STATUSES or result.get("error_code") == "WORK_REQUIRES_REWORK"):
        task = record["arguments"]["task_name"].split("：", 1)[-1]
        verdict = "通过" if status in _SETTLED_STATUSES else "未通过"
        return [f"{_clock(context, record['occurred_at'])} 提交「{task}」，{verdict}"]
    if status not in _SETTLED_STATUSES:
        return []
    payload = result.get("data") or {}
    if tool == "send_message":
        person = record["arguments"]["recipient_name"].split("：", 1)[-1]
        return [f"{_clock(context, record['occurred_at'])} 给{person}发消息：{record['arguments']['text']}"]
    if tool == "conclude_activity":
        return [] if facts is None else [
            activity_sentence(context, context.state.activities[activity_id], facts.pauses.get(activity_id, ()))
            for activity_id in payload.get("activity_ids", []) if activity_id in facts.endings]
    lines = [purchase_sentence(context, order, payload.get("answering")) for order in payload.get("orders", [])]
    return lines + [give_sentence(context, record["occurred_at"], item) for item in payload.get("given", [])]


def _clock(context: LayerContext, moment: str) -> str:
    """@brief 记录时刻（带时区 ISO）写成短语里的钟点。"""
    epoch = int(datetime.fromisoformat(moment).timestamp())
    return clock_phrase(epoch, context.state.current_time, context.scenario.timezone)
