"""@brief 零点计划 plan_day 的只读输入装配（docs/dev/SPEC-time-and-energy.md 第四章「输入、调用、失败与记录」）。

输入全部来自世界状态与足迹记录，不读人物工作区与记忆运行时：人物档案与场景背景原文（background_story）、
回顾时段里已核验经历的句子（第七章口径）、本人已结算的公开动作结果（消息送达、订单受理与交付、工作验收）、
回顾时段里 conclude_activity 请求记下的 summary 与 memories（标「你自己记下的，未经核验」），以及此刻可见的
事实子集：可见任务、收件箱、在途订单及预计送达、联系人、上一天目标的推进情况。不读身体数值、交互对象、
私有绑定、未到期的预排事件、决策自述与人物的主观记忆库，也不含内部身份。

回顾时段自 day_start 至 now：零点边界上是刚结束的本地日（前一日零点至本次零点），启动路径上是生成时刻所在
本地日里生成时刻之前的部分。动作结果与收尾按发生时刻取闭区间；经历按结算区间的结束时刻取左开右闭，
day_start 那一刻结束的区间属于前一日，本次零点结束的末段属于刚结束的这一日。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from worldmock.footprints.experiences import project_verified_experiences
from worldmock.life import projection
from worldmock.life.day_planning import SELF_REPORT_NOTE, plan_date

#: 中文星期名。
_WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")


@dataclass(frozen=True, slots=True)
class PlanInputs:
    """@brief 一次零点计划的输入：除经历外的载荷、按时间顺序的经历句子及其来源、其余来源事件身份。"""

    payload: dict[str, Any]
    experiences: tuple[tuple[str, str], ...]
    sources: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Window:
    """@brief 回顾时段（start 至 end）与场景时区。"""

    start: int
    end: int
    timezone: str

    def covers(self, epoch: int) -> bool:
        """@brief 动作结果、收尾与交付的发生时刻是否落在回顾时段内（闭区间）。"""
        return self.start <= epoch <= self.end

    def ends_within(self, epoch: int) -> bool:
        """@brief 结算区间的结束时刻是否落在回顾时段内（左开右闭：起点那一刻结束的区间属于前一日）。"""
        return self.start < epoch <= self.end

    def clock(self, epoch: int) -> str:
        """@brief 本地时刻 HH:MM。"""
        return f"{datetime.fromtimestamp(epoch, ZoneInfo(self.timezone)):%H:%M}"

    def when(self, epoch: int) -> str:
        """@brief 人读日期时刻「M月D日 HH:MM」。"""
        local = datetime.fromtimestamp(epoch, ZoneInfo(self.timezone))
        return f"{local.month}月{local.day}日 {local:%H:%M}"


def plan_inputs(engine: Any, agent_id: int) -> PlanInputs:
    """@brief 为一个人物装配零点计划输入（只读引擎的世界状态、场景与足迹库，不产生任何事件）。"""
    state, zone = engine.state, engine.scenario.timezone
    window = _review_window(state.current_time, zone)
    agent = state.agents[agent_id]
    experiences = _experience_sentences(engine, agent_id, window)
    results, result_sources = _result_sentences(engine, agent_id, window)
    conclusions, conclusion_sources = _conclusion_records(engine, agent_id, window)
    goals, goal_sources = _previous_goals(engine, agent_id, window)
    local = datetime.fromtimestamp(state.current_time, ZoneInfo(zone))
    payload = {
        "计划日期": plan_date(state.current_time, zone),
        "生成时刻": f"{window.when(state.current_time)}（{_WEEKDAYS[local.weekday()]}）",
        "回顾时段": f"{window.when(window.start)} 至 {window.when(window.end)}",
        "人物": _persona(engine, agent_id),
        "动作结果": results,
        "收尾记录": conclusions,
        "待办任务": _task_rows(engine, agent, window),
        "收件箱": _inbox_rows(engine, agent, window),
        "在途订单": _pending_orders(engine, agent_id, window),
        "联系人": [{"称呼": row["salutation"], "关系": row["relation"]}
                for row in projection._contact_rows(engine.scenario, agent)],
        "上一天的目标": goals,
    }
    return PlanInputs(payload, experiences, (*result_sources, *conclusion_sources, *goal_sources))


def _review_window(now: int, timezone_name: str) -> _Window:
    """@brief 自前一刻所在本地日的零点至 now：零点边界回顾前一日，其余时刻回顾当日已过去的部分。"""
    zone = ZoneInfo(timezone_name)
    before = datetime.fromtimestamp(now, zone) - timedelta(seconds=1)
    start = before.replace(hour=0, minute=0, second=0, microsecond=0)
    return _Window(int(start.timestamp()), now, timezone_name)


def _persona(engine: Any, agent_id: int) -> dict[str, Any]:
    """@brief 场景声明的人物档案与背景原文（不读工作区里编译过的卡片）。"""
    config = next(item for item in engine.scenario.agents if item.agent_id == agent_id)
    fields = {"名字": config.name, "背景": config.background_story, "健康习惯": config.health_habits,
              "社交风格": config.social_style, "午饭时段": _hours(config.lunch_window),
              "晚饭时段": _hours(config.dinner_window), "就寝时段": _hours(config.bedtime_window)}
    return {key: value for key, value in fields.items() if value is not None}


def _hours(window: tuple[float, float] | None) -> str | None:
    """@brief 自午夜起的小时数窗口写成「HH:MM–HH:MM」（末端可跨午夜）。"""
    if window is None:
        return None
    parts = []
    for value in window:
        minutes = round(value * 60) % (24 * 60)
        parts.append(f"{minutes // 60:02d}:{minutes % 60:02d}")
    return "–".join(parts)


def _experience_sentences(engine: Any, agent_id: int, window: _Window) -> tuple[tuple[str, str], ...]:
    """@brief 回顾时段里已核验经历的确定性句子（与观察同一写法），按时间顺序，带来源事件身份。"""
    store = engine.store
    events = store.read_agent_events("activity_progress", agent_id)
    facts = project_verified_experiences(events, agent_id=agent_id, cutoff_seq=store.last_event_seq)
    inputs = projection.ProjectionInputs(
        state=engine.state, scenario=engine.scenario, agent=engine.state.agents[agent_id],
        recent_results=(), task_rows=(), extras={"content_titles": engine.rules.content_titles})
    facts.sort(key=lambda item: (item["event_seq"], item["kind"], item.get("product_id", "")))
    return tuple((projection._experience_sentence(inputs, fact), fact["source_event_id"])
                 for fact in facts if window.ends_within(fact["occurred_at"]))


def _result_sentences(engine: Any, agent_id: int, window: _Window) -> tuple[list[str], list[str]]:
    """@brief 本人已结算的公开动作结果：消息送达、订单受理、配送交付与工作验收，写成不含身份的句子。"""
    store = engine.store
    requests = {event.action_id: event.data for event in store.read_agent_events("action_requested", agent_id)}
    rows: list[tuple[int, str, str]] = []
    for event in store.read_agent_events("action_result", agent_id):
        request = requests.get(event.action_id)
        if event.visibility != "agent" or request is None or not window.covers(event.occurred_at):
            continue
        rows.extend((event.event_seq, text, event.event_id)
                    for text in _result_texts(engine, window, request, event))
    for event in store.read_agent_events("environment_event", agent_id):
        if event.data.get("kind") == "delivery_arrival" and window.covers(event.occurred_at):
            rows.extend((event.event_seq, f"{window.clock(event.occurred_at)} {_order_text(engine, order)}送到了"
                         f"{_place(engine, order['destination_id'])}", event.event_id)
                        for order in event.data.get("orders", []))
    rows.sort(key=lambda item: item[0])
    return [text for _, text, _ in rows], [source for _, _, source in rows]


def _result_texts(engine: Any, window: _Window, request: dict[str, Any], event: Any) -> list[str]:
    """@brief 单个动作结果的公开句子（其余工具与被拒的请求不列）。"""
    clock, data, body = window.clock(event.occurred_at), event.data, request["request"]
    if data.get("reused"):
        return []
    if request["tool_name"] == "send_message" and data.get("status") == "completed":
        return [f"{clock} 给{body['recipient_name']}发了消息：{body['text']}"]
    review = data.get("data", {}).get("work_review")
    if request["tool_name"] == "submit_work" and isinstance(review, dict):
        return [f"{clock} 提交了「{body['task_name']}」，{'通过了验收' if review.get('accepted') else '没有通过验收'}"]
    orders = data.get("data", {}).get("orders", []) if data.get("status") != "rejected" else []
    return [f"{clock} 下单了{_order_text(engine, order)}，预计{window.when(order['expected_at'])}"
            + (f"送到{_place(engine, order['destination_id'])}" if order.get("fulfillment") == "delivery"
               else f"可在{_place(engine, order['destination_id'])}取")
            for order in orders]


def _order_text(engine: Any, order: dict[str, Any]) -> str:
    """@brief 订单商品与份数。"""
    return f"{engine.rules.products[order['product_id']].name}×{order['quantity']}"


def _place(engine: Any, location_id: str) -> str:
    """@brief 地点名称。"""
    return next(item.name for item in engine.scenario.locations if item.location_id == location_id)


def _conclusion_records(engine: Any, agent_id: int, window: _Window) -> tuple[list[dict], list[str]]:
    """@brief 回顾时段里 conclude_activity 请求记下的 summary 与 memories（人物自述，未经核验）。"""
    records, sources = [], []
    for event in engine.store.read_agent_events("action_requested", agent_id):
        if event.data.get("tool_name") != "conclude_activity" or not window.covers(event.occurred_at):
            continue
        body = event.data["request"]
        for task, summary, memories in zip(body["task"], body["summary"], body["memories"]):
            records.append({"时间": window.clock(event.occurred_at), "活动": task, "收尾说明": summary,
                            "想记住的": [item["text"] for item in memories], "说明": SELF_REPORT_NOTE})
        sources.append(event.event_id)
    return records, sources


def _task_rows(engine: Any, agent: Any, window: _Window) -> list[dict[str, Any]]:
    """@brief 此刻可见且未付款、未过截止的任务：名称、截止与要求的地点。"""
    return [{"任务": row["title"], "截止": window.when(row["due_at"]),
             "地点": _place(engine, row["required_location_id"]) if row.get("required_location_id") else None}
            for row in engine._visible_task_rows(agent)]


def _inbox_rows(engine: Any, agent: Any, window: _Window) -> list[dict[str, Any]]:
    """@brief 收件箱：发件人、摘要、是否已读与时间（与观察同源，不含消息身份）。"""
    inputs = projection.ProjectionInputs(
        state=engine.state, scenario=engine.scenario, agent=agent, recent_results=(), task_rows=(),
        extras={"scenario_messages": engine.rules.messages})
    rows = projection._public_inbox(inputs, projection._inbox_rows(inputs))
    return [{"发件人": row["sender"], "摘要": row["head"], "已读": row["read"], "时间": window.when(row["created_at"])}
            for row in rows]


def _pending_orders(engine: Any, agent_id: int, window: _Window) -> list[dict[str, Any]]:
    """@brief 在途订单及预计送达（state.deliveries 中本人待交付的订单）。"""
    return [{"商品": engine.rules.products[item.product_id].name, "数量": item.quantity,
             "预计送达": window.when(item.expected_at), "送到": _place(engine, item.destination_id)}
            for item in sorted(engine.state.deliveries.values(), key=lambda value: (value.expected_at, value.order_id))
            if item.agent_id == agent_id and item.status == "pending"]


def _previous_goals(engine: Any, agent_id: int, window: _Window) -> tuple[list[dict], list[str]]:
    """@brief 上一天目标的推进情况：回顾时段结束于本次零点时，取该日计划最后一份快照（含末段推进）。"""
    snapshots = []
    for event_type in ("environment_event", "action_result", "activity_progress"):
        for event in engine.store.read_agent_events(event_type, agent_id):
            plan = (event.data.get("day_plan") if event_type != "action_result"
                    else event.data.get("data", {}).get("day_plan"))
            if isinstance(plan, dict) and plan.get("day_end") == window.end:
                snapshots.append((event.event_seq, plan, event.event_id))
    if not snapshots:
        return [], []
    _, plan, source = max(snapshots, key=lambda item: item[0])
    return [{"意图": goal["intent_kind"], "目标": goal["goal"],
             "推进": "已推进" if goal.get("progressed_at") is not None else "未推进"}
            for goal in plan["goals"]], [source]
