"""@brief 执行全部数据集动作，按真实秒数记录内容并静默接续娱乐。"""

from __future__ import annotations

import math
from dataclasses import dataclass

from worldmock.catalog.affordances import engine_affordances
from worldmock.catalog.models import Catalog, Content
from worldmock.errors import BusinessRuleError
from worldmock.exchange.types import ActionCategory
from worldmock.life.activities import ActivityOrigin
from worldmock.life.app_push import open_app_push, release_app_pushes
from worldmock.life.body import apply_mood_pulse
from worldmock.life.content_select import ContentRequest, eligible_contents, required_work_location, select_content
from worldmock.life.models import (
    ActivityKind, ActivityState, ActivityStatus, AgentState, ContentProgress, DERIVED_DURATION_CAP_SECONDS, WorldState,
)
from worldmock.life.randomness import random_stream
from worldmock.life.rules import action_location_refusal
from worldmock.life.readable import duration_phrase
from worldmock.life.scenario import RuntimeScenario
from worldmock.life.work import work_location_refusal


@dataclass(frozen=True, slots=True)
class ActionStart:
    """@brief 人物亲选的动作、app 与对方，以及本次实际活动身份。"""

    agent_id: int
    activity_id: str
    action_id: str
    app_id: str | None
    counterpart: str | None


@dataclass(frozen=True, slots=True)
class PreparedAction:
    """@brief 只读选出的内容快照与规则时长，准入成功后直接执行，不再抽取。"""

    request: ActionStart
    duration_seconds: int
    data: dict


def prepare_action(state: WorldState, catalog: Catalog, scenario: RuntimeScenario,
                   request: ActionStart) -> PreparedAction:
    """@brief 对精确动作与 app 只选择一次内容，不修改世界、推送或随机状态。"""
    action = catalog.actions[request.action_id]
    _validate_action_fields(action, request)
    present = state.agents[request.agent_id].location_id
    if any(item.agent_id == request.agent_id and item.kind == ActivityKind.TRAVEL
           and item.status == ActivityStatus.ACTIVE for item in state.activities.values()):
        present = None
    refusal = action_location_refusal(action.category, catalog.locations.get(present))
    if refusal is not None:
        raise refusal.error()
    query = ContentRequest(request.agent_id, request.action_id, request.app_id, request.activity_id)
    _require_candidates(catalog, scenario, state, query)
    stream = random_stream(state.random_state, f"{request.activity_id}:0")
    content, pinned = select_content(catalog, scenario, state, query, stream)
    progress = state.agents[request.agent_id].content_progress.get(content.content_id)
    position = 0 if progress is None else progress.position_seconds
    data = {"action_id": action.action_id, "category": action.category.value, "app_id": request.app_id,
            "manner": None if action.manner is None else action.manner.value,
            "communication_mode": None if action.communication_mode is None else action.communication_mode.value,
            "counterpart": request.counterpart,
            "content_id": content.content_id, "start_position_seconds": position,
            "pinned_by_push_id": pinned, "draw_index": 0, "played": []}
    return PreparedAction(request, _remaining(content, position), data)


def _validate_action_fields(action, request: ActionStart) -> None:
    """@brief 动作绑定与人物亲填字段必须匹配，不代选 app 或交流对象。"""
    if action.app_ids and request.app_id is None:
        raise BusinessRuleError("APP_REQUIRED")
    if request.app_id not in action.app_ids and request.app_id is not None:
        raise BusinessRuleError("APP_NOT_OFFERED")
    if action.category == ActionCategory.SOCIAL and request.counterpart is None:
        raise BusinessRuleError("COUNTERPART_REQUIRED")
    if action.category != ActionCategory.SOCIAL and request.counterpart is not None:
        raise BusinessRuleError("ROUTE_FIELDS_INVALID")


def _require_candidates(catalog: Catalog, scenario: RuntimeScenario, state: WorldState,
                        request: ContentRequest) -> None:
    """@brief 合格内容为空时区分真正耗尽与必须前往工作地点。"""
    if eligible_contents(catalog, scenario, state, request):
        return
    required = required_work_location(catalog, scenario, state, request)
    if required is not None:
        place = next(item.display_name for item in scenario.locations if item.location_id == required)
        raise work_location_refusal(catalog.actions[request.action_id].name, place).error()
    raise BusinessRuleError("CONTENT_EXHAUSTED")


def initialize_action(state: WorldState, prepared: PreparedAction, origin: ActivityOrigin,
                      catalog: Catalog) -> WorldState:
    """@brief 准入成功后构造已选内容的活动，并打开命中的同 app、同动作推送。"""
    request, data = prepared.request, prepared.data
    changed = state.model_copy(deep=True)
    if data["pinned_by_push_id"] is not None:
        changed, _ = open_app_push(changed, request.agent_id, request.app_id, request.action_id, catalog)
    agent, now = changed.agents[request.agent_id], changed.current_time
    claims = engine_affordances().resource_claims("action", category=data["category"], manner=data["manner"])
    activity = ActivityState(
        activity_id=request.activity_id, agent_id=agent.agent_id, location_id=agent.location_id,
        kind=ActivityKind.ACTION, status=ActivityStatus.ACTIVE, started_at=now, last_settled_at=now,
        planned_end_at=now + prepared.duration_seconds, elapsed_seconds=0,
        planned_duration_seconds=prepared.duration_seconds, media_position_seconds=data["start_position_seconds"],
        resource_claims=set(claims), origin_action_id=origin.action_id, origin_decision_id=origin.decision_id,
        origin_request_event_id=origin.request_event_id, report_anchor_at=now, data=dict(data))
    changed.activities[activity.activity_id] = activity
    return changed


def action_content_result(activity: ActivityState, catalog: Catalog) -> dict:
    """@brief 公开实际选中的内容摘要，不制造人物点名内容的请求字段。"""
    content = catalog.contents[activity.data["content_id"]]
    app_id = activity.data["app_id"]
    return {"action": catalog.actions[activity.data["action_id"]].name,
            "app": None if app_id is None else catalog.apps[app_id].name,
            "title": content.title, "summary": content.summary,
            "duration": duration_phrase(content.duration_seconds - activity.media_position_seconds),
            "pushed": activity.data["pinned_by_push_id"] is not None}


def resume_action(state: WorldState, activity: ActivityState, catalog: Catalog,
                  scenario: RuntimeScenario) -> WorldState:
    """@brief 原内容仍合格才恢复，最新全局位置覆盖活动游标且不重抽。"""
    query = ContentRequest(activity.agent_id, activity.data["action_id"], activity.data["app_id"], activity.activity_id)
    _require_candidates(catalog, scenario, state, query)
    eligible = {item.content_id for item in eligible_contents(catalog, scenario, state, query)}
    if activity.data["content_id"] not in eligible:
        raise BusinessRuleError("CONTENT_EXHAUSTED")
    changed = state.model_copy(deep=True)
    content = catalog.contents[activity.data["content_id"]]
    progress = changed.agents[activity.agent_id].content_progress.get(content.content_id)
    position = 0 if progress is None else progress.position_seconds
    changed.activities[activity.activity_id] = _content_snapshot(activity, content, position)
    return changed


def _remaining(content: Content, position: float) -> int:
    """@brief 内容余量只按向上取整与单次一天上限形成结束边界。"""
    return min(math.ceil(content.duration_seconds - position), DERIVED_DURATION_CAP_SECONDS)


def _content_snapshot(activity: ActivityState, content: Content, position: float) -> ActivityState:
    """@brief 开始、接续与恢复使用同一内容余量公式。"""
    remaining = _remaining(content, position)
    data = {**activity.data, "content_id": content.content_id, "start_position_seconds": position}
    return activity.model_copy(update={"data": data, "media_position_seconds": position,
        "planned_duration_seconds": activity.elapsed_seconds + remaining,
        "planned_end_at": activity.last_settled_at + remaining, "next_interaction_at": None,
        "status": ActivityStatus.ACTIVE, "ended_at": None, "boundary_type": None,
        "paused_at": None, "report_anchor_at": activity.report_anchor_at}, deep=True)


def settle_action(state: WorldState, activity: ActivityState, catalog: Catalog,
                  scenario: RuntimeScenario, position_before: float) -> tuple[WorldState, dict]:
    """@brief 身体已按实际区间推进后，更新全局内容证据及唯一一次完成效果。"""
    changed = state.model_copy(deep=True)
    content = catalog.contents[activity.data["content_id"]]
    agent = changed.agents[activity.agent_id]
    previous = agent.content_progress.get(content.content_id)
    rounds = 0 if previous is None else previous.completed_rounds
    before = 0 if previous is None else previous.position_seconds
    seconds = activity.media_position_seconds - position_before
    after = min(content.duration_seconds, before + seconds)
    completed = seconds > 0 and after >= content.duration_seconds
    if seconds > 0:
        agent.content_progress[content.content_id] = ContentProgress(
            content_id=content.content_id, position_seconds=0 if completed else after,
            completed_rounds=rounds + int(completed), last_consumed_at=activity.last_settled_at)
    evidence = {"content_id": content.content_id, "app_id": activity.data["app_id"],
        "position_before_seconds": before, "position_after_seconds": after,
        "completed": completed, "completed_rounds_after": rounds + int(completed)}
    if completed:
        activity = _completed_content(activity, content)
        changed = release_app_pushes(changed, agent.agent_id, content.content_id)
        if _assigned(scenario, agent.agent_id, content.content_id):
            agent.body = apply_mood_pulse(agent.body, "goal_progressed")
        changed.agents[agent.agent_id] = agent
        if activity.data["category"] == ActionCategory.ENTERTAINMENT:
            activity = _continue_action(changed, activity, catalog, scenario)
    changed.agents[agent.agent_id] = agent
    changed.activities[activity.activity_id] = activity
    return changed, evidence


def _completed_content(activity: ActivityState, content: Content) -> ActivityState:
    """@brief 完成当前内容保留实际播放区间；非娱乐保持既有计划结束。"""
    played = [*activity.data["played"], {"content_id": content.content_id,
        "from_position": activity.data["start_position_seconds"], "to_position": content.duration_seconds}]
    return activity.model_copy(update={"data": {**activity.data, "played": played},
        "status": ActivityStatus.COMPLETED, "boundary_type": "planned_end",
        "ended_at": activity.last_settled_at, "next_interaction_at": None,
        "media_position_seconds": content.duration_seconds}, deep=True)


def _assigned(scenario: RuntimeScenario, agent_id: int, content_id: str) -> bool:
    """@brief 完成脉冲只属于环境明确指派给本人的内容。"""
    return content_id in next(item.work_content_ids for item in scenario.agents if item.agent_id == agent_id)


def _continue_action(state: WorldState, activity: ActivityState, catalog: Catalog,
                     scenario: RuntimeScenario) -> ActivityState:
    """@brief 娱乐按相同历史静默接续，接续不会打开任何推送。"""
    index = activity.data["draw_index"] + 1
    query = ContentRequest(activity.agent_id, activity.data["action_id"], activity.data["app_id"],
                           activity.activity_id, activity.data["content_id"])
    if not eligible_contents(catalog, scenario, state, query):
        return activity.model_copy(update={"status": ActivityStatus.COMPLETED, "boundary_type": "planned_end",
            "ended_at": activity.last_settled_at, "next_interaction_at": None}, deep=True)
    content, _ = select_content(catalog, scenario, state, query,
                               random_stream(state.random_state, f"{activity.activity_id}:{index}"))
    progress = state.agents[activity.agent_id].content_progress.get(content.content_id)
    position = 0 if progress is None else progress.position_seconds
    activity = activity.model_copy(update={"data": {**activity.data, "draw_index": index,
                                                   "pinned_by_push_id": None}}, deep=True)
    return _content_snapshot(activity, content, position)
