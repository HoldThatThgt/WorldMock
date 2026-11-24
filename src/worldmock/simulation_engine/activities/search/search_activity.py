"""@brief search_world 启动、搜索耗时随机流、孤立会话任务构造、Fact Guard 与结果归因。"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Sequence

from pydantic import ValidationError

from worldmock.configuration.inputs.environment import EnvironmentInputs
from worldmock.exchange_engine.definitions.types import GOODS_CATEGORY_LABELS
from worldmock.foundation.errors import ProtocolError
from worldmock.foundation.randomness import random_stream
from worldmock.foundation.time.readable import iso_time, local_date
from worldmock.recording_engine.events.models import EventDraft
from worldmock.simulation_engine.actions import admission
from worldmock.simulation_engine.actions.name_resolution import resolution_data
from worldmock.simulation_engine.actions.start_group import ToolInvocation, frozen_observation
from worldmock.simulation_engine.activities.lifecycle import activity_control, sessions
from worldmock.simulation_engine.activities.lifecycle.activities import ActivityStartParams, start_activity
from worldmock.simulation_engine.activities.search.search_catalog import (
    SearchCatalogView,
    project_goods_row,
    project_place_row,
    validate_fact_ids,
)
from worldmock.simulation_engine.commerce.purchase.order_apps import order_app_ids, preferred_app_id, selling_app_ids
from worldmock.simulation_engine.evidence.actions import (
    _activity_terminal_draft,
    require_origin_action,
)
from worldmock.simulation_engine.observation.wording.activity_titles import (
    activity_snapshot,
    progress_snapshot,
    title_index,
)
from worldmock.simulation_engine.observation.wording.guidance import Refusal
from worldmock.simulation_engine.scheduling.clock.weather import WEATHER_LABELS, current_weather
from worldmock.simulation_engine.world.context import EngineContext
from worldmock.simulation_engine.world.world_updates import _activity_origin, _put_activity, place_names
from worldmock.state.decisions.barrier import SearchCommitReceipt, SearchSessionOutcome, SearchTaskSpec
from worldmock.state.decisions.results import ActionResult
from worldmock.state.interaction.request_errors import argument_report
from worldmock.state.interaction.requests import SearchWorldRequest
from worldmock.state.world.activity import ActivityState
from worldmock.state.world.kinds import ActionStatus, ActivityStatus
from worldmock.state.world.world import WorldState

_LOGGER = logging.getLogger(__name__)
#: search_world 搜索时长闭区间秒数。
SEARCH_SECONDS_MIN = 180
SEARCH_SECONDS_MAX = 420
#: 搜索子会话允许提交的纯证据事件类型。
_SEARCH_EVIDENCE_TYPES = frozenset({"model_request", "model_response"})


def search_seconds(random_state: str, action_id: str) -> int:
    """@brief 按 search-duration:{action_id} 的随机流在 180 到 420 秒之间抽取搜索耗时。"""
    return random_stream(random_state, f"search-duration:{action_id}").randint(SEARCH_SECONDS_MIN, SEARCH_SECONDS_MAX)


def attribute_search_app(inputs: EnvironmentInputs, random_state: str,
                         activity: ActivityState, goods_ids: tuple[str, ...]) -> str | None:
    """@brief 确定性归因搜索占用 app：起始已定向则保持，否则按命中商品多数票归因或按 search-app 流抽取。"""
    if activity.data.get("app_id") is not None:
        return activity.data["app_id"]
    counts: Counter[str] = Counter()
    for gid in goods_ids:
        for app_id in selling_app_ids(inputs.catalog, (gid,)):
            counts[app_id] += 1
    if counts:
        return min(counts, key=lambda app_id: (-counts[app_id], app_id))
    apps = order_app_ids(inputs.catalog)
    if not apps:
        return None
    return random_stream(random_state, f"search-app:{activity.origin_action_id}").choice(apps)


def start_search(context: EngineContext, state: WorldState,
                 pending: ToolInvocation) -> tuple[WorldState, ActionResult]:
    """@brief 校验并受理 search_world，建立占第二槽位的搜索活动并等待异步子会话完成。"""
    request = _validate_search_request(pending)
    agent_id = pending.agent_id
    observed = frozen_observation(pending)
    names = {row["activity_id"]: row["name"] for row in observed.get("activity_bindings", [])}
    open_activities = activity_control.open_activities(state, agent_id)
    waiting = activity_control.pending_conclusions(open_activities)
    if waiting:
        raise activity_control.conclusion_required(waiting, names)
    refusal = admission.search_refusal(
        admission.Context(state, agent_id, open_activities, place_names(context.catalog), names, context.inputs))
    if refusal is not None:
        raise refusal.error()
    app_id = preferred_app_id(context.catalog, request.prefer)
    duration = search_seconds(state.random_state, pending.action_id)
    label = request.query.strip()[:20]
    data = {"action": "search", "query": request.query, "prefer": request.prefer,
            "app_id": app_id, "scoped": app_id is not None}
    origin = _activity_origin(pending.action_id, pending.wakeup_id, pending.requested_event_id)
    agent, activity = start_activity(state.agents[agent_id], ActivityStartParams("search", duration, data=data),
                                     state.current_time, origin, context.bodies[agent_id])
    named = activity.model_copy(
        update={"name": activity_control.activity_name("搜索", label, state.current_time, state.timezone)},
        deep=True,
    )
    changed = _put_activity(state, agent, named)
    current_session = sessions.current_session(changed, agent_id)
    changed = sessions.assign(changed, current_session.session_id, [named.activity_id])
    snapshot = activity_snapshot(named, title_index(context.catalog))
    result = ActionResult(
        action_id=pending.action_id, agent_id=agent_id, status=ActionStatus.IN_PROGRESS,
        occurred_at=state.current_time, activity_id=named.activity_id, message="Search started",
        data=resolution_data({"activities": [snapshot], "orders": []}, (), ()),
    )
    return changed, result


def _validate_search_request(pending: ToolInvocation) -> SearchWorldRequest:
    """@brief 用严格 SearchWorldRequest 校验参数。"""
    try:
        return SearchWorldRequest.model_validate(pending.request)
    except ValidationError as error:
        _LOGGER.error("search_world validation failed: %s", error)
        report = argument_report("search_world", error, SearchWorldRequest, pending.request, "ACTION_REQUEST_INVALID")
        raise Refusal("ACTION_REQUEST_INVALID", report.reason, fix=report.fix or "", items=report.items).error()


def build_search_task(inputs: EnvironmentInputs, state: WorldState,
                      activity: ActivityState) -> tuple[SearchTaskSpec, SearchCatalogView]:
    """@brief 为到达计划终点的搜索活动装配隔离子会话任务契约与只读目录视图。"""
    routing = inputs.config.model_routing
    override = routing.agent_overrides.get(str(activity.agent_id))
    profile_id = override.search if override and override.search is not None else routing.search
    turn_budget = inputs.config.models[profile_id].max_react_turns
    loc_id = activity.location_id
    counts = Counter(goods.category for goods in inputs.catalog.goods.values())
    overview = {GOODS_CATEGORY_LABELS.get(cat, cat): counts[cat] for cat in sorted(counts)}
    spec = SearchTaskSpec(
        action_id=activity.origin_action_id, agent_id=activity.agent_id,
        activity_id=activity.activity_id, origin_wakeup_id=activity.origin_wakeup_id,
        query=activity.data["query"], prefer=activity.data.get("prefer"),
        scoped_app_id=activity.data.get("app_id"), current_location_id=loc_id,
        location_name=inputs.catalog.locations[loc_id].display_name,
        opened_at_clock=iso_time(activity.started_at, state.timezone),
        local_date=local_date(state.current_time, state.timezone),
        weather_label=WEATHER_LABELS[current_weather(state)],
        open_apps=tuple(inputs.catalog.apps[aid].name for aid in order_app_ids(inputs.catalog)),
        category_overview=overview, turn_budget=turn_budget,
    )
    return spec, SearchCatalogView(inputs, state, spec)


def complete_searches(context: EngineContext, due: Sequence[ActivityState],
                      outcomes: Sequence[SearchSessionOutcome]) -> SearchCommitReceipt:
    """@brief 按 Fact Guard 复核全部到期搜索会话产出并原子提交完成态、证据与唤醒计划。"""
    by_activity = _matched_outcomes(due, outcomes)
    staged = context.state.model_copy(deep=True)
    drafts: list[EventDraft] = []
    index = title_index(context.catalog)
    for activity in due:
        outcome = by_activity[activity.activity_id]
        _validate_outcome(context, activity, outcome)
        staged, item_drafts = _apply_one_search(context, staged, activity, outcome, index)
        drafts.extend(item_drafts)
    staged.version = context.state.version + 1
    context.commit_step(staged, drafts)
    return SearchCommitReceipt(completed_count=len(due), activity_ids=tuple(a.activity_id for a in due),
                               event_seq_last=context.store.last_event_seq)


def _matched_outcomes(due: Sequence[ActivityState],
                      outcomes: Sequence[SearchSessionOutcome]) -> dict[str, SearchSessionOutcome]:
    """@brief 要求本次交回的结果恰好与全部到期搜索活动一一对应。"""
    due_ids = {item.activity_id for item in due}
    by_id: dict[str, SearchSessionOutcome] = {}
    for outcome in outcomes:
        if outcome.activity_id in by_id or outcome.activity_id not in due_ids:
            _LOGGER.error("Search completion outcome %s does not match due searches", outcome.activity_id)
            raise ProtocolError("Search outcome does not match due searches")
        by_id[outcome.activity_id] = outcome
    if set(by_id) != due_ids:
        _LOGGER.error("Missing search completion outcomes for due activities")
        raise ProtocolError("Missing search completion outcomes for due searches")
    return by_id


def _validate_outcome(context: EngineContext, activity: ActivityState, outcome: SearchSessionOutcome) -> None:
    """@brief 校验归属、动作身份、白名单 Fact Guard 与模型证据类型。"""
    if outcome.agent_id != activity.agent_id or outcome.action_id != activity.origin_action_id:
        _LOGGER.error("Search outcome ownership mismatch for activity %s", activity.activity_id)
        raise ProtocolError("Search outcome ownership mismatch")
    bad_goods, bad_places = validate_fact_ids(
        context.catalog, activity.data.get("app_id"), outcome.goods_ids, outcome.place_ids)
    if bad_goods or bad_places:
        _LOGGER.error("Fact guard rejected fabricated search ids: goods=%s places=%s", bad_goods, bad_places)
        raise ProtocolError("Fact guard rejected search outcome ids")
    for draft in outcome.evidence_drafts:
        if draft.agent_id != activity.agent_id or draft.event_type not in _SEARCH_EVIDENCE_TYPES:
            _LOGGER.error("Search evidence draft has invalid owner or type: %s", draft.event_type)
            raise ProtocolError("Invalid search evidence draft")


def _apply_one_search(context: EngineContext, staged: WorldState, activity: ActivityState,
                      outcome: SearchSessionOutcome, index) -> tuple[WorldState, list[EventDraft]]:
    """@brief 将单条经 Fact Guard 核验的搜索成果写入活动终态、生成证据并重设唤醒时间。"""
    now = staged.current_time
    require_origin_action(staged, activity)
    goods_rows = [project_goods_row(context.inputs, staged, activity.agent_id, gid, activity.data.get("app_id"))
                  for gid in outcome.goods_ids]
    place_rows = [project_place_row(context.inputs, staged, (activity.agent_id, activity.location_id), pid)
                  for pid in outcome.place_ids]
    app_use = attribute_search_app(context.inputs, staged.random_state, activity, tuple(outcome.goods_ids))
    answer = {
        "query": activity.data["query"], "prefer": activity.data.get("prefer"),
        "scoped": activity.data.get("scoped", False), "app_use": app_use,
        "goods_rows": goods_rows, "place_rows": place_rows, "summary": outcome.summary,
    }
    delta = max(0, now - activity.last_settled_at)
    completed = activity.model_copy(update={
        "status": ActivityStatus.COMPLETED, "ended_at": now, "concluded_at": now,
        "boundary_type": "planned_end", "next_interaction_at": None, "last_settled_at": now,
        "elapsed_seconds": activity.elapsed_seconds + delta,
        "data": {**activity.data, "app_id": app_use, "app_use": app_use, "answer": answer},
    }, deep=True)
    staged.activities[activity.activity_id] = completed
    agent = staged.agents[activity.agent_id]
    reasons = list(agent.pending_wake_reasons)
    if "planned_end" not in reasons:
        reasons.append("planned_end")
    staged.agents[activity.agent_id] = agent.model_copy(
        update={"next_decision_at": now, "pending_wake_reasons": reasons}, deep=True)
    evidence = [draft.model_copy(update={"occurred_at": now}) for draft in outcome.evidence_drafts]
    extra: list[EventDraft] = []
    causes = [activity.origin_request_event_id]
    if delta > 0:
        progress = EventDraft(
            event_type="activity_progress", occurred_at=now, agent_id=activity.agent_id,
            wakeup_id=activity.origin_wakeup_id, action_id=activity.origin_action_id,
            activity_id=activity.activity_id, caused_by=causes,
            data={"interval_start": activity.last_settled_at, "interval_end": now,
                  "activity": progress_snapshot(completed, index)},
        )
        extra.append(progress)
        causes = [progress.event_id]
    terminal = _activity_terminal_draft(staged, completed, now, causes, index)
    return staged, [*evidence, *extra, terminal]
