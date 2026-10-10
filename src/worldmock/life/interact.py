"""@brief 真实容器互动、共享内容进度与同系列自动接续。"""
from __future__ import annotations
import logging
import math
from dataclasses import dataclass
from worldmock.errors import BusinessRuleError, IntegrityError
from worldmock.footprints.models import EventDraft
from worldmock.life.activities import ActivityStartParams, start_activity, schedule_feedback
from worldmock.life.activity_control import activity_name
from worldmock.life.app_push import open_app_push, release_app_pushes
from worldmock.life.content_select import ContentRequest, select_content
from worldmock.life.models import ActivityState, ActivityStatus, ContentProgress, WorldState
from worldmock.life.randomness import random_stream
from worldmock.life.world_ops import _activity_origin, _put_activity

RELIEF_PER_HOUR = {"none": 0.0, "low": 0.10, "medium": 0.30, "high": 0.50}

@dataclass(frozen=True)
class InteractivityRuntime:
    """@brief 内容状态只保存在世界活动与人物共享进度中。"""
    #: 唯一引擎上下文。
    context: object

    def start(self, state: WorldState, pending, target_kind: str,
              target_id: str) -> tuple[WorldState, ActivityState, list[EventDraft]]:
        """@brief 整组已准入后推荐一次内容，并打开真实钉住推送。"""
        agent = state.agents[pending.agent_id]
        origin = _activity_origin(state, pending)
        suffix = f":{origin.activity_ordinal:04d}" if origin.activity_ordinal > 1 else ""
        identity = f"activity-{origin.action_id}{suffix}"
        request = ContentRequest(agent.agent_id, target_kind, target_id, identity)
        app_id = target_id if target_kind == "app" else None
        app = None if app_id is None else self.context.catalog.apps[app_id]
        if app is not None and app.browse_duration_seconds is not None:
            duration, data = app.browse_duration_seconds, _browse_data(app_id)
        else:
            content, pinned = select_content(self.context.catalog, state, request,
                random_stream(state.random_state, f"{identity}:0"))
            progress = agent.content_progress.get(content.content_id)
            position = 0.0 if progress is None else progress.position_seconds
            duration, data = _remaining(content, position), _content_data(content, app_id, position, pinned)
        params = ActivityStartParams("interact", duration, data=data)
        agent, activity = start_activity(agent, params, state.current_time, origin, self.context.body)
        label = app.name if app is not None else self.context.catalog.locations[target_id].display_name
        activity = activity.model_copy(update={"name": activity_name("进行", label, state.current_time,
            state.timezone), "media_position_seconds": data.get("start_position_seconds", 0.0)}, deep=True)
        activity = schedule_feedback(activity, state.current_time, self.context.config.activity_feedback_seconds)
        changed = _put_activity(state, agent, activity)
        if data.get("pinned_by_push_id") is not None:
            changed, _ = open_app_push(changed, agent.agent_id, app_id, data["content_id"])
        return self._collect(changed, activity, target_kind, target_id)

    def _collect(self, state: WorldState, activity: ActivityState,
                 target_kind: str, target_id: str) -> tuple:
        """@brief 驿站收货与真实地点内容在同次受理中完成。"""
        place = self.context.catalog.locations.get(target_id) if target_kind == "location" else None
        if place is None or not place.accepts_parcels:
            return state, activity, []
        from worldmock.life.storefront_runtime import StorefrontRuntime

        changed, receipts = StorefrontRuntime(self.context).collect_parcels(state, activity.agent_id, target_id)
        return changed, activity, receipts

    def advance(self, state: WorldState, activity: ActivityState, target: int) -> tuple[WorldState, dict | None]:
        """@brief 泛型游标已推进后保存实际内容区间，不再另加心理效果。"""
        content_id = activity.data["content_id"]
        if content_id is None:
            return state, None
        content = self.context.catalog.contents[content_id]
        changed = state.model_copy(deep=True)
        agent = changed.agents[activity.agent_id]
        previous = agent.content_progress.get(content_id)
        before = activity.data["start_position_seconds"] if previous is None else previous.position_seconds
        rounds = 0 if previous is None else previous.completed_rounds
        after = min(float(content.duration_seconds), activity.media_position_seconds)
        if after <= before:
            return state, None
        completed = after >= content.duration_seconds
        progress = ContentProgress(content_id=content_id, position_seconds=0.0 if completed else after,
            completed_rounds=rounds + int(completed), last_consumed_at=target)
        agent = agent.model_copy(
            update={"content_progress": {**agent.content_progress, content_id: progress}}, deep=True)
        changed.agents[activity.agent_id] = agent
        evidence = {"content_id": content_id, "app_id": activity.data["app_id"],
            "position_before_seconds": before, "position_after_seconds": after,
            "completed": completed, "completed_rounds_after": progress.completed_rounds}
        if completed:
            changed = release_app_pushes(changed, agent.agent_id, content_id)
            activity = self._complete_content(changed, activity, content, progress.completed_rounds)
            changed.activities[activity.activity_id] = activity
        return changed, evidence

    def _complete_content(self, state: WorldState, activity: ActivityState, content, rounds: int) -> ActivityState:
        """@brief 独立内容立即停止；同系列仅按集号接续下一条未完成集。"""
        played = [*activity.data["played"], {"content_id": content.content_id,
            "from_position": activity.data["start_position_seconds"], "to_position": content.duration_seconds}]
        activity = activity.model_copy(update={"data": {**activity.data, "played": played}}, deep=True)
        if content.series is None:
            return activity
        ids = self.context.catalog.contents_by_series[content.series.title]
        agent = state.agents[activity.agent_id]
        candidates = [self.context.catalog.contents[key] for key in ids
            if self.context.catalog.contents[key].series.episode > content.series.episode
            and (key not in agent.content_progress or agent.content_progress[key].completed_rounds < rounds)]
        if not candidates:
            return activity
        next_content = candidates[0]
        previous = agent.content_progress.get(next_content.content_id)
        position = 0.0 if previous is None else previous.position_seconds
        data = _content_data(next_content, activity.data["app_id"], position, None)
        data.update(draw_index=activity.data["draw_index"] + 1, played=played,
                    accepted_event_id=activity.data["accepted_event_id"])
        remaining = _remaining(next_content, position)
        result = activity.model_copy(update={"data": {"action": "interact", **data},
            "media_position_seconds": position, "status": ActivityStatus.ACTIVE, "ended_at": None,
            "planned_end_at": activity.last_settled_at + remaining,
            "planned_duration_seconds": activity.elapsed_seconds + remaining, "boundary_type": None}, deep=True)
        return schedule_feedback(result, result.last_settled_at, self.context.config.activity_feedback_seconds)

    def resume(self, state: WorldState, activity: ActivityState) -> WorldState:
        """@brief 恢复实际共享位置，不抽取另一条内容。"""
        content_id = activity.data["content_id"]
        if content_id is None:
            return state
        content = self.context.catalog.contents.get(content_id)
        if content is None:
            logging.error("Persisted activity target is invalid")
            raise IntegrityError("Persisted activity target is invalid")
        progress = state.agents[activity.agent_id].content_progress.get(content_id)
        position = 0.0 if progress is None else progress.position_seconds
        remaining = _remaining(content, position)
        data = {**activity.data, "start_position_seconds": position}
        result = activity.model_copy(update={"data": data, "media_position_seconds": position,
            "planned_duration_seconds": activity.elapsed_seconds + remaining,
            "planned_end_at": state.current_time + remaining}, deep=True)
        return state.model_copy(update={"activities": {**state.activities, activity.activity_id: result}}, deep=True)

def _remaining(content, position: float) -> int:
    """@brief 内容余量按整秒向上取整，不添加目录没有声明的上限。"""
    return math.ceil(content.duration_seconds - position)

def _content_data(content, app_id: str | None, position: float, pinned: str | None) -> dict:
    """@brief 内容快照只携带实际容器、进度、强度与独立效果。"""
    return {"app_id": app_id, "content_id": content.content_id, "start_position_seconds": position,
        "pinned_by_push_id": pinned, "draw_index": 0, "played": [], "exertion": content.exertion,
        "relaxation_relief_per_hour": RELIEF_PER_HOUR[content.relaxation_relief],
        "social_relief_per_hour": RELIEF_PER_HOUR[content.social_relief]}

def _browse_data(app_id: str) -> dict:
    """@brief 纯目录浏览没有共享内容游标、推送绑定或心理效果。"""
    return {"app_id": app_id, "content_id": None, "exertion": "idle",
        "relaxation_relief_per_hour": 0.0, "social_relief_per_hour": 0.0}
