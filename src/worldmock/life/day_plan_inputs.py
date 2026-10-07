"""@brief 零点计划 plan_day 的只读输入装配（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第四章「输入、调用、失败与记录」）。

输入全部来自世界状态与足迹记录，不读人物工作区与记忆运行时：人物档案与场景背景原文（background_story）、
回顾时段里已核验经历的句子与本人已结算的公开动作结果（消息送达、订单受理与交付、结束的活动），二者都取
settled_text 的共享句式（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第三章「settled 的写法」）、
回顾时段里已受理的 conclude_activity 请求记下的 summary 与记忆（经 control_items 读出，含看法与偏好；标「你自己记下的，
未经核验」；被拒与重放的请求不列），以及此刻可见的事实子集：工作主题、还没办完的个人事务、收件箱、在途订单及预计送达、
联系人、上一天目标的推进情况。
「个人事务」是只读键（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-零点计划修订与只读问答 ）：行与待办层同一构造（errands.plan_rows），写名称、要办成
什么、已办到哪、备注与提醒时刻，不登记来源事件；零点计划不创建、不更改、不终结事务，「待办任务」只呈现分配的工作主题。
「上班安排」是场景声明的工作安排按输入「计划日期」所指的本地日求值的结果（docs/dev/SPEC-as2-integration.md#errands-workday-运行修复入库 4.6；
零点边界上即新开始的一日）：与环境层 duty 同源（work_schedule.day_arrangement），不含 status，地点写显示名，
没有工作安排为 null。
不读身体数值、交互对象、私有绑定、未到期的预排事件与人物的主观记忆库，也不含内部身份。

回顾时段自 day_start 至 now：零点边界上是刚结束的本地日（前一日零点至本次零点），启动路径上是生成时刻所在
本地日里生成时刻之前的部分（起点恰为零点时为空）。动作结果与收尾按发生时刻取闭区间；经历按结算区间的结束时刻取左开右闭，
day_start 那一刻结束的区间属于前一日，本次零点结束的末段属于刚结束的这一日。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from worldmock.footprints.experiences import project_verified_experiences
from worldmock.life import errands, progress_layer, projection, work_schedule
from worldmock.life.control_items import control_items
from worldmock.life.readable import duration_phrase
from worldmock.life.day_planning import SELF_REPORT_NOTE, plan_date
from worldmock.life.settled_text import (
    ActivityFacts,
    activity_endings,
    app_progress,
    ending_lines,
    experience_sentence,
    pause_intervals,
    record_lines,
    tool_record,
)

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


def plan_inputs(context: Any, observations: Any, midnight_pending: bool, agent_id: int) -> PlanInputs:
    """@brief 为一个人物装配零点计划输入（只读引擎的世界状态、场景与足迹库，不产生任何事件）。"""
    state, zone = context.state, context.scenario.timezone
    window = _review_window(state.current_time, zone, midnight_pending)
    agent = state.agents[agent_id]
    experiences = _experience_sentences(context, observations, agent_id, window)
    results, result_sources = _result_sentences(context, observations, agent_id, window)
    conclusions, conclusion_sources = _conclusion_records(context, agent_id, window)
    goals, goal_sources = _previous_goals(context, agent_id, window)
    local = datetime.fromtimestamp(state.current_time, ZoneInfo(zone))
    payload = {
        "计划日期": plan_date(state.current_time, zone),
        "生成时刻": f"{window.when(state.current_time)}（{_WEEKDAYS[local.weekday()]}）",
        "回顾时段": f"{window.when(window.start)} 至 {window.when(window.end)}",
        "人物": _persona(context, agent_id),
        "上班安排": _duty_arrangement(context, agent_id),
        "动作结果": results,
        "收尾记录": conclusions,
        "待办任务": _task_rows(context, observations, agent, window),
        "个人事务": errands.plan_rows(agent, _product_names(context), zone),
        "收件箱": _inbox_rows(context, agent, window),
        "在途订单": _pending_orders(context, agent_id, window),
        "联系人": [
            {"称呼": row["salutation"], "关系": row["relation"]}
            for row in projection._contact_rows(context.scenario, agent)
        ],
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
    """@brief 场景声明的人物档案与背景原文（不读工作区里编译过的卡片）：下班、到家时段只取场景字段、未声明省略，只来自
    卡片的下班与到家时刻不进这里（docs/dev/SPEC-life-realism.md#errands-workday-作息补全与工作安排-场景作息覆盖 ）。"""
    config = next(item for item in context.scenario.agents if item.agent_id == agent_id)
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
    """@brief 自午夜起的小时数窗口写成「HH:MM–HH:MM」（末端可跨午夜）。"""
    if window is None:
        return None
    parts = []
    for value in window:
        minutes = round(value * 60) % (24 * 60)
        parts.append(f"{minutes // 60:02d}:{minutes % 60:02d}")
    return "–".join(parts)


def _duty_arrangement(context: Any, agent_id: int) -> dict[str, Any] | None:
    """@brief 计划日期当天的上班安排（与环境层 duty 同源，不含 status）；没有工作安排为 None。"""
    config = next(item for item in context.scenario.agents if item.agent_id == agent_id)
    day = date.fromisoformat(plan_date(context.state.current_time, context.scenario.timezone))
    places = {item.location_id: item.name for item in context.scenario.locations}
    return work_schedule.day_arrangement(config.work_schedule, day, places)


def _experience_sentences(
    context: Any, observations: Any, agent_id: int, window: _Window
) -> tuple[tuple[str, str], ...]:
    """@brief 回顾时段里已核验经历的确定性句子（settled_text 的共享写法），按时间顺序，带来源事件身份。"""
    store = context.store
    events = [*store.read_agent_events("activity_progress", agent_id),
              *store.read_agent_events("environment_event", agent_id)]
    events.sort(key=lambda event: event.event_seq)
    facts = project_verified_experiences(events, agent_id=agent_id, cutoff_seq=store.last_event_seq)
    context = observations._layer_context(context.state.agents[agent_id], [])
    facts.sort(key=lambda item: (item["event_seq"], item["kind"], item.get("goods_id", "")))
    return tuple(
        (experience_sentence(context, fact), fact["source_event_id"])
        for fact in facts
        if window.ends_within(fact["occurred_at"])
    )


def _result_sentences(context: Any, observations: Any, agent_id: int, window: _Window) -> tuple[list[str], list[str]]:
    """@brief 本人已结算的公开动作结果与送达，写成 settled 句（与记忆层同一共享句式，不含身份）：发消息、下单、
    送达，以及在回顾时段内结束的活动（按结束事件，起止含已恢复的暂停区间）；失败、重放的请求与其余工具不写。"""
    store = context.store
    context = observations._layer_context(context.state.agents[agent_id], [])
    outcomes = store.read_agent_events("action_result", agent_id)
    environment = store.read_agent_events("environment_event", agent_id)
    results: dict[str, Any] = {}
    for event in outcomes:
        results.setdefault(event.action_id, event)
    rows: list[tuple[int, str, str]] = []
    for request in store.read_agent_events("action_requested", agent_id):
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
    progress = store.read_agent_events("activity_progress", agent_id)
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
    return next(item.name for item in context.scenario.locations if item.location_id == location_id)


def _conclusion_records(context: Any, agent_id: int, window: _Window) -> tuple[list[dict], list[str]]:
    """@brief 回顾时段里已受理的 conclude_activity 请求逐项记下的 summary 与记忆原文（人物自述，未经核验；经 control_items
    读出，每项先普通记忆、后看法与偏好）。

    只取结果为 completed 的首次请求：被拒的请求与重放（reused）的请求不列，与动作结果的口径一致。
    """
    store = context.store
    completed = {
        event.action_id
        for event in store.read_agent_events("action_result", agent_id)
        if event.data.get("status") == "completed" and not event.data.get("reused")
    }
    records, sources = [], []
    for event in store.read_agent_events("action_requested", agent_id):
        if (
            event.data.get("tool_name") != "conclude_activity"
            or event.data.get("reused")
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


def _task_rows(context: Any, observations: Any, agent: Any, window: _Window) -> list[dict[str, Any]]:
    """@brief 未完成的分配工作内容只写标题、总时长和实际已做秒数。"""
    config = next(item for item in context.scenario.agents if item.agent_id == agent.agent_id)
    rows = []
    for content_id in config.work_content_ids:
        content = context.semantics.catalog.contents[content_id]
        progress = agent.content_progress.get(content_id)
        if progress is not None and progress.completed_rounds:
            continue
        seconds = 0 if progress is None else progress.position_seconds
        rows.append({"主题": content.title, "目标": duration_phrase(content.duration_seconds),
                     "已做": duration_phrase(seconds)})
    return rows


def _product_names(context: Any) -> dict[str, str]:
    """@brief 冻结目录里商品的商品身份 → 商品名（个人事务「要办成什么」按它写商品名，与待办层同一张表）。"""
    return {product_id: item.name for product_id, item in context.semantics.catalog.goods.items()}


def _inbox_rows(context: Any, agent: Any, window: _Window) -> list[dict[str, Any]]:
    """@brief 收件箱：发件人、摘要、是否已读与时间（与观察同源，不含消息身份）。"""
    inputs = projection.ProjectionInputs(
        state=context.state,
        scenario=context.scenario,
        agent=agent,
        extras={"scenario_messages": context.rules.messages},
    )
    rows = projection._public_inbox(inputs, projection._inbox_rows(inputs))
    return [
        {"发件人": row["sender"], "摘要": row["head"], "已读": row["read"], "时间": window.when(row["created_at"])}
        for row in rows
    ]


def _pending_orders(context: Any, agent_id: int, window: _Window) -> list[dict[str, Any]]:
    """@brief 在途订单及预计送达（state.deliveries 中本人待交付的订单）。"""
    return [
        {
            "商品": context.semantics.catalog.goods[item.goods_id].name,
            "数量": item.quantity,
            "预计送达": window.when(item.expected_at),
            "送到": _place(context, item.destination_id),
        }
        for item in sorted(context.state.deliveries.values(), key=lambda value: (value.expected_at, value.order_id))
        if item.agent_id == agent_id and item.status == "pending"
    ]


def _previous_goals(context: Any, agent_id: int, window: _Window) -> tuple[list[dict], list[str]]:
    """@brief 上一天目标的推进情况：回顾时段结束于本次零点时，取该日计划最后一份快照（含末段推进）。"""
    snapshots = []
    for event_type in ("environment_event", "action_result", "activity_progress"):
        for event in context.store.read_agent_events(event_type, agent_id):
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
            "推进": "已推进" if goal.get("progressed_at") is not None else "未推进",
        }
        for goal in plan["goals"]
    ], [source]
