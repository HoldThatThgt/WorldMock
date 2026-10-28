"""@brief 零点计划只读人物声明、当前上班安排、真实经历与结果、主观收尾、个人事务和本人在途订单。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from worldmock.recording_engine.projections.activities.experiences import project_verified_experiences
from worldmock.recording_engine.events.models import Event
from worldmock.simulation_engine.scheduling.plans import errands
from worldmock.simulation_engine.observation.layers import progress_layer
from worldmock.configuration.persona import work_schedule
from worldmock.state.interaction.control_items import control_items
from worldmock.simulation_engine.scheduling.plans.day_planning import SELF_REPORT_NOTE, plan_date
from worldmock.simulation_engine.observation.wording.settled_text import (
    ActivityFacts,
    activity_endings,
    app_progress,
    ending_lines,
    experience_sentence,
    pause_intervals,
    record_lines,
    tool_record,
)

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


@dataclass(frozen=True, slots=True)
class _AgentHistory:
    """@brief 一次零点计划读到的本人四类事件，各读一次、按事件序号，几个部分共用。"""

    #: 本人全部 activity_progress。
    progress: tuple[Event, ...]
    #: 本人全部 environment_event。
    environment: tuple[Event, ...]
    #: 本人全部 action_result。
    results: tuple[Event, ...]
    #: 本人全部 action_requested。
    requests: tuple[Event, ...]


def _agent_history(store: Any, agent_id: int) -> _AgentHistory:
    """@brief 本人四类事件各读一次（不加时间界：恢复、暂停和结束可能跨日）。"""
    return _AgentHistory(*(tuple(store.read_agent_events(event_type, agent_id)) for event_type in
                           ("activity_progress", "environment_event", "action_result", "action_requested")))


def plan_inputs(context: Any, observations: Any, midnight_pending: bool, agent_id: int) -> PlanInputs:
    """@brief 为一个人物装配零点计划输入（只读引擎的世界状态、场景与足迹库，不产生任何事件）。"""
    state, zone = context.state, context.config.timezone
    window = _review_window(state.current_time, zone, midnight_pending)
    agent = state.agents[agent_id]
    history = _agent_history(context.store, agent_id)
    experiences = _experience_sentences(context, observations, agent_id, history, window)
    results, result_sources = _result_sentences(context, observations, agent_id, history, window)
    conclusions, conclusion_sources = _conclusion_records(history, window)
    goals, goal_sources = _previous_goals(history, window)
    local = datetime.fromtimestamp(state.current_time, ZoneInfo(zone))
    payload = {
        "计划日期": plan_date(state.current_time, zone),
        "生成时刻": f"{window.when(state.current_time)}（{work_schedule.WEEKDAY_NAMES[local.weekday()]}）",
        "回顾时段": f"{window.when(window.start)} 至 {window.when(window.end)}",
        "人物": _persona(context, agent_id),
        "上班安排": _duty_arrangement(context, agent_id),
        "动作结果": results,
        "收尾记录": conclusions,
        "个人事务": errands.plan_rows(agent, _product_names(context), zone),
        "在途订单": _pending_orders(context, agent_id, window),
        "上一天的目标": goals,
    }
    return PlanInputs(payload, experiences, (*result_sources, *conclusion_sources, *goal_sources))


def _review_window(now: int, timezone_name: str, midnight_boundary: bool) -> _Window:
    """@brief 回顾时段：零点边界（midnight_pending）回顾刚结束的前一日，自前一日零点至 now；
    启动路径（新建、恢复与反事实分支）回顾生成时刻所在本地日中生成时刻之前的部分，起点恰为零点时为空时段。
    """
    local = datetime.fromtimestamp(now - 1 if midnight_boundary else now, ZoneInfo(timezone_name))
    start = local.replace(hour=0, minute=0, second=0, microsecond=0, fold=0)
    return _Window(int(start.timestamp()), now, timezone_name)


def _persona(context: Any, agent_id: int) -> dict[str, Any]:
    """@brief 人设表里的人物档案与背景原文：午饭、晚饭、就寝时段必填所以总在，健康习惯、社交风格与下班、到家时段
    没有写时省略（docs/design/06-agent.md「人物身份与画像」）。"""
    config = context.persona
    fields = {
        "名字": config.name,
        "背景": config.background_story,
        "健康习惯": config.health_habits,
        "社交风格": config.social_style,
        "午饭时段": _hours(config.lunch_window),
        "下班时段": _hours(config.leave_work_window),
        "到家时段": _hours(config.arrive_home_window),
        "晚饭时段": _hours(config.dinner_window),
        "就寝时段": _hours(config.bedtime_window),
    }
    return {key: value for key, value in fields.items() if value is not None}


def _hours(window: tuple[float, float] | None) -> str | None:
    """@brief 自午夜起的小时数窗口写成「HH:MM–HH:MM」（按钟面读，大于 24 的小时数对一天取模）；人设表的值不取整，
    分钟的取整规则与人设层的时刻写法相同，两处显示同一分钟。"""
    if window is None:
        return None
    parts = []
    for value in window:
        minutes = round(value * 60) % (24 * 60)
        parts.append(f"{minutes // 60:02d}:{minutes % 60:02d}")
    return "–".join(parts)


def _duty_arrangement(context: Any, agent_id: int) -> dict[str, Any] | None:
    """@brief 计划日期当天的上班安排（与环境层 duty 同源，不含 status）；没有工作安排为 None。"""
    config = context.persona
    day = date.fromisoformat(plan_date(context.state.current_time, context.config.timezone))
    places = {key: item.display_name for key, item in context.catalog.locations.items()}
    return work_schedule.day_arrangement(config.work_schedule, day, places)


def _experience_sentences(
    context: Any, observations: Any, agent_id: int, history: _AgentHistory, window: _Window
) -> tuple[tuple[str, str], ...]:
    """@brief 回顾时段里已核验经历的确定性句子（settled_text 的共享写法），按时间顺序，带来源事件身份；
    事件取自本次计划共用的一次读取。"""
    events = [*history.progress, *history.environment]
    events.sort(key=lambda event: event.event_seq)
    facts = project_verified_experiences(events, agent_id=agent_id, cutoff_seq=context.store.last_event_seq)
    context = observations._layer_context(context.state.agents[agent_id], [])
    facts.sort(key=lambda item: (item["event_seq"], item["kind"], item.get("goods_id", "")))
    return tuple(
        (experience_sentence(context, fact), fact["source_event_id"])
        for fact in facts
        if window.ends_within(fact["occurred_at"])
    )


def _result_sentences(
    context: Any, observations: Any, agent_id: int, history: _AgentHistory, window: _Window
) -> tuple[list[str], list[str]]:
    """@brief 本人已结算的公开动作结果与送达，写成 settled 句（与记忆层同一共享句式，不含身份）：下单、
    送达，以及在回顾时段内结束的活动（按结束事件，起止含已恢复的暂停区间）；失败的请求与其余工具不写。
    事件取自本次计划共用的一次读取。"""
    context = observations._layer_context(context.state.agents[agent_id], [])
    outcomes = history.results
    environment = history.environment
    results: dict[str, Any] = {}
    for event in outcomes:
        results.setdefault(event.action_id, event)
    rows: list[tuple[int, str, str]] = []
    for request in history.requests:
        result = results.get(request.action_id)
        if result is None or result.visibility != "agent" or not window.covers(result.occurred_at):
            continue
        rows.extend(
            (result.event_seq, text, result.event_id)
            for text in record_lines(context, tool_record(context, request), [request, result])
        )
    for event in environment:
        if event.data.get("kind") == "delivery_arrival" and window.covers(event.occurred_at):
            rows.extend(
                (event.event_seq, line, event.event_id)
                for item in progress_layer._delivery_records(context, event)
                for line in record_lines(context, item.record, [event])
            )
    progress = history.progress
    endings = activity_endings([*progress, *environment, *outcomes])
    facts = ActivityFacts(pause_intervals(environment), endings, app_progress(progress, {}))
    rows.extend(
        (event.event_seq, text, event.event_id)
        for event, text in ending_lines(context, facts, lambda event: window.covers(event.occurred_at))
    )
    rows.sort(key=lambda item: item[0])
    return [text for _, text, _ in rows], [source for _, _, source in rows]


def _place(context: Any, location_id: str) -> str:
    """@brief 地点名称。"""
    return context.catalog.locations[location_id].display_name


def _conclusion_records(history: _AgentHistory, window: _Window) -> tuple[list[dict], list[str]]:
    """@brief 回顾时段里已受理的 conclude_activity 请求逐项记下的 summary 与记忆原文（人物自述，未经核验；经 control_items
    读出，每项先普通记忆、后看法与偏好）。

    只取结果为 completed 的首次请求：被拒的请求不列，与动作结果的口径一致。事件取自本次计划共用的一次读取。
    """
    completed = {
        event.action_id
        for event in history.results
        if event.data.get("status") == "completed"
    }
    records, sources = [], []
    for event in history.requests:
        if (
            event.data.get("tool_name") != "conclude_activity"
            or event.action_id not in completed
            or not window.covers(event.occurred_at)
        ):
            continue
        for item in control_items("conclude_activity", event.data["request"]):
            records.append(
                {
                    "时间": window.clock(event.occurred_at),
                    "活动": item.activity_name,
                    "收尾说明": item.summary,
                    "想记住的": [memory["text"] for memory in item.memories],
                    "说明": SELF_REPORT_NOTE,
                }
            )
        sources.append(event.event_id)
    return records, sources




def _product_names(context: Any) -> dict[str, str]:
    """@brief 冻结目录里商品的商品身份 → 商品名（个人事务「要办成什么」按它写商品名，与待办层同一张表）。"""
    return {product_id: item.name for product_id, item in context.catalog.goods.items()}




def _pending_orders(context: Any, agent_id: int, window: _Window) -> list[dict[str, Any]]:
    """@brief 在途订单及预计送达（state.deliveries 中本人待交付的订单）。"""
    return [
        {
            "商品": context.catalog.goods[item.goods_id].name,
            "数量": item.quantity,
            "预计送达": window.when(item.expected_at),
            "送到": _place(context, item.destination_id),
        }
        for item in sorted(context.state.deliveries.values(), key=lambda value: (value.expected_at, value.order_id))
        if item.agent_id == agent_id and item.status == "pending"
    ]


def _previous_goals(history: _AgentHistory, window: _Window) -> tuple[list[dict], list[str]]:
    """@brief 上一天目标的推进情况：回顾时段结束于本次零点时，取该日计划最后一份快照（含末段推进）；
    事件取自本次计划共用的一次读取。"""
    snapshots = []
    for event_type, events in (("environment_event", history.environment), ("action_result", history.results),
                               ("activity_progress", history.progress)):
        for event in events:
            plan = (
                event.data.get("day_plan")
                if event_type != "action_result"
                else event.data.get("data", {}).get("day_plan")
            )
            if isinstance(plan, dict) and plan.get("day_end") == window.end:
                snapshots.append((event.event_seq, plan, event.event_id))
    if not snapshots:
        return [], []
    _, plan, source = max(snapshots, key=lambda item: item[0])
    return [
        {
            "意图": goal["intent_kind"],
            "目标": goal["goal"],
        }
        for goal in plan["goals"]
    ], [source]
