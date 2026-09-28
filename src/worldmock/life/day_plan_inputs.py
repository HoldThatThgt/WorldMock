"""@brief 零点计划 plan_day 的只读输入装配（docs/dev/SPEC-time-and-energy.md 第四章「输入、调用、失败与记录」）。

输入全部来自世界状态与足迹记录，不读人物工作区与记忆运行时：人物档案与场景背景原文（background_story）、
回顾时段里已核验经历的句子与本人已结算的公开动作结果（消息送达、订单受理与交付、工作验收、结束的活动），二者都取
settled_text 的共享句式（docs/dev/SPEC-persona-prompt.md 第三章「settled 的写法」）、
回顾时段里已受理的 conclude_activity 请求记下的 summary 与 memories（标「你自己记下的，未经核验」；被拒与重放的
请求不列），以及此刻可见的事实子集：可见任务、收件箱、在途订单及预计送达、联系人、上一天目标的推进情况。
不读身体数值、交互对象、私有绑定、未到期的预排事件与人物的主观记忆库，也不含内部身份。

回顾时段自 day_start 至 now：零点边界上是刚结束的本地日（前一日零点至本次零点），启动路径上是生成时刻所在
本地日里生成时刻之前的部分（起点恰为零点时为空）。动作结果与收尾按发生时刻取闭区间；经历按结算区间的结束时刻取左开右闭，
day_start 那一刻结束的区间属于前一日，本次零点结束的末段属于刚结束的这一日。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from worldmock.footprints.experiences import project_verified_experiences
from worldmock.life import progress_layer, projection
from worldmock.life.day_planning import SELF_REPORT_NOTE, plan_date
from worldmock.life.settled_text import experience_sentence, record_lines, tool_record

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
    window = _review_window(state.current_time, zone, engine.midnight_rhythm_pending)
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


def _review_window(now: int, timezone_name: str, midnight_boundary: bool) -> _Window:
    """@brief 回顾时段：零点边界（engine.midnight_rhythm_pending）回顾刚结束的前一日，自前一日零点至 now；
    启动路径（新建、恢复与反事实分支）回顾生成时刻所在本地日中生成时刻之前的部分，起点恰为零点时为空时段。
    """
    local = datetime.fromtimestamp(now - 1 if midnight_boundary else now, ZoneInfo(timezone_name))
    start = local.replace(hour=0, minute=0, second=0, microsecond=0, fold=0)
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
    """@brief 回顾时段里已核验经历的确定性句子（settled_text 的共享写法），按时间顺序，带来源事件身份。"""
    store = engine.store
    events = store.read_agent_events("activity_progress", agent_id)
    facts = project_verified_experiences(events, agent_id=agent_id, cutoff_seq=store.last_event_seq)
    context = engine._layer_context(engine.state.agents[agent_id], (), [])
    facts.sort(key=lambda item: (item["event_seq"], item["kind"], item.get("product_id", "")))
    return tuple((experience_sentence(context, fact), fact["source_event_id"])
                 for fact in facts if window.ends_within(fact["occurred_at"]))


def _result_sentences(engine: Any, agent_id: int, window: _Window) -> tuple[list[str], list[str]]:
    """@brief 本人已结算的公开动作结果与送达，写成 settled 句（与记忆层同一共享句式，不含身份）：发消息、下单、提交工作、
    结束活动与送达；失败、重放的请求与其余工具不写。"""
    store = engine.store
    context = engine._layer_context(engine.state.agents[agent_id], (), [])
    results: dict[str, Any] = {}
    for event in store.read_agent_events("action_result", agent_id):
        results.setdefault(event.action_id, event)
    rows: list[tuple[int, str, str]] = []
    for request in store.read_agent_events("action_requested", agent_id):
        result = results.get(request.action_id)
        if result is None or result.visibility != "agent" or not window.covers(result.occurred_at):
            continue
        rows.extend((result.event_seq, text, result.event_id)
                    for text in record_lines(context, tool_record(context, request), [request, result]))
    for event in store.read_agent_events("environment_event", agent_id):
        if event.data.get("kind") == "delivery_arrival" and window.covers(event.occurred_at):
            rows.extend((event.event_seq, line, event.event_id) for item in progress_layer._delivery_records(
                context, event) for line in record_lines(context, item.record, [event]))
    rows.sort(key=lambda item: item[0])
    return [text for _, text, _ in rows], [source for _, _, source in rows]


def _place(engine: Any, location_id: str) -> str:
    """@brief 地点名称。"""
    return next(item.name for item in engine.scenario.locations if item.location_id == location_id)


def _conclusion_records(engine: Any, agent_id: int, window: _Window) -> tuple[list[dict], list[str]]:
    """@brief 回顾时段里已受理的 conclude_activity 请求记下的 summary 与 memories（人物自述，未经核验）。

    只取结果为 completed 的首次请求：被拒的请求与重放（reused）的请求不列，与动作结果的口径一致。
    """
    store = engine.store
    completed = {event.action_id for event in store.read_agent_events("action_result", agent_id)
                 if event.data.get("status") == "completed" and not event.data.get("reused")}
    records, sources = [], []
    for event in store.read_agent_events("action_requested", agent_id):
        if (event.data.get("tool_name") != "conclude_activity" or event.data.get("reused")
                or event.action_id not in completed or not window.covers(event.occurred_at)):
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
        state=engine.state, scenario=engine.scenario, agent=agent,
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
