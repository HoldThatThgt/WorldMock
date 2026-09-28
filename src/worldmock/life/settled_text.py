"""@brief settled 写法的确定性句子（docs/dev/SPEC-persona-prompt.md 第三章「settled 的写法」）。

只写人物可见、成功的世界请求与世界事件：下单（带所回应的消息与收货人）、给予、发消息、提交工作各一句，在本段结束的活动每项一句（起止、终局、实际份量或
有效工时），收到的直接消息（全文）、邮件（主题）与送达各一句。不写失败的请求、记忆查询、回看、report_status、暂停与恢复、
revise_day_plan、提醒、截止预警与身体结算。金额按元，份数至多两位小数，时长用人读短语，跨日加「M月D日」。
进度层按预算浓缩时，被折叠记录的 lines 取这里的句子。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from worldmock.footprints.models import Event
from worldmock.life import activity_control
from worldmock.life.interaction_menu import PURCHASE_WAYS
from worldmock.life.models import ActivityKind, ActivityState
from worldmock.life.readable import clock_phrase, duration_phrase, money_phrase, portion_phrase
from worldmock.life.recipients import agent_identity, person_name
from worldmock.life.tool_layer import LayerContext

#: 成功的结果状态：只有这些结果写 settled 句。
_SETTLED_STATUSES = frozenset({"completed", "in_progress"})


def record_lines(context: LayerContext, record: dict, sources: list[Event]) -> list[str]:
    """@brief 一条进度层记录的 settled 句（没有可写的事实时为 []）。

    :param record: 进度层记录（occurred_at 为带时区 ISO）。
    :param sources: 记录的来源事件（tool_call 为 action_requested 与 action_result；派生记录为空）。
    """
    kind = record["type"]
    if kind == "tool_call":
        return _tool_lines(context, record, sources)
    if kind == "message_arrival":
        when = _clock(context, record["occurred_at"])
        if record["kind"] == "mail":
            return [f"{when} 收到邮件「{record['subject']}」"]
        return [f"{when} 收到{record['sender']}的消息：{record['text']}"]
    if kind == "delivery":
        return [f"{_clock(context, record['occurred_at'])} {record['order']}：{record['message']}"]
    if kind == "condensed":
        return list(record["lines"])
    return []


def activity_sentence(context: LayerContext, activity: ActivityState) -> str:
    """@brief 一项已结束活动的句子：名称、起止、终局，工作另写有效工时，食用另写吃掉的份数。"""
    name = context.names["activities"].get(activity.activity_id, activity.name)
    start = clock_phrase(activity.started_at, context.state.current_time, context.scenario.timezone)
    end = clock_phrase(activity.ended_at or activity.last_settled_at, context.state.current_time,
                       context.scenario.timezone)
    sentence = f"{name}：{start}–{end}，{activity_control.ending_phrase(activity)}"
    if activity.kind == ActivityKind.WORK:
        sentence += f"，有效工时{duration_phrase(activity.effective_work_seconds)}"
    if activity.kind == ActivityKind.CONSUME and activity.data.get("request", {}).get("use_mode") == "eat":
        sentence += f"，吃了 {portion_phrase(sum(activity.data.get('consumed', {}).values()))}"
    return sentence


def _tool_lines(context: LayerContext, record: dict, sources: list[Event]) -> list[str]:
    """@brief 世界请求的 settled 句：下单、给予、发消息、提交工作与结束活动；失败的请求与其他工具不写。

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
        return [activity_sentence(context, context.state.activities[activity_id])
                for activity_id in payload.get("activity_ids", [])]
    lines = [_purchase_sentence(context, order, payload.get("answering")) for order in payload.get("orders", [])]
    return lines + [_give_sentence(context, record["occurred_at"], item) for item in payload.get("given", [])]


def _give_sentence(context: LayerContext, moment: str, item: dict[str, Any]) -> str:
    """@brief 一次给予的句子：「10:40 把牛肉炒面 1 份给了王磊」。"""
    product = context.rules.items[item["product_id"]].name
    person = person_name(context.state, context.scenario, context.agent_id, item["recipient_id"])
    return f"{_clock(context, moment)} 把{product} {portion_phrase(item['quantity'])}给了{person}"


def _purchase_sentence(context: LayerContext, order: dict[str, Any], answering: str | None) -> str:
    """@brief 一个订单的句子：「10:20 远程下单牛肉炒面 1 份，付 32 元，预计 10:35 送到<地点>（回应王磊 10:15 的消息，
    收货人王磊）」；到店不写送达，没有关联消息不写回应，收货人是自己时不写收货人。"""
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


def _clock(context: LayerContext, moment: str) -> str:
    """@brief 记录时刻（带时区 ISO）写成短语里的钟点。"""
    epoch = int(datetime.fromisoformat(moment).timestamp())
    return clock_phrase(epoch, context.state.current_time, context.scenario.timezone)
