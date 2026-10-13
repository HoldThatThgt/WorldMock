"""@brief 真实容器互动、共享内容进度与同系列自动接续。"""
from __future__ import annotations
import logging
import math
from dataclasses import dataclass
from worldmock.errors import BusinessRuleError, IntegrityError
from worldmock.exchange.constants import RELIEF_PER_HOUR
from worldmock.footprints.models import EventDraft, validate_resource_flows
from worldmock.life.activities import ActivityStartParams, start_activity, schedule_feedback
from worldmock.life.activity_control import activity_name
from worldmock.life.app_push import open_app_push, release_app_pushes
from worldmock.life.completion_effects import settle_completion_effects
from worldmock.life.content_select import ContentRequest, select_content
from worldmock.life.location_activities import next_content
from worldmock.life.models import ActivityState, ActivityStatus, ContentProgress, WorldState
from worldmock.life.randomness import random_stream
from worldmock.life.world_ops import _activity_origin, _put_activity, _put_agent

@dataclass(frozen=True)
class InteractivityRuntime:
    """@brief 内容状态只保存在世界活动与人物共享进度中。"""
    #: 唯一引擎上下文。
    context: object

    def start(self, state: WorldState, pending, row,
              stay_location_id: str | None) -> tuple[WorldState, ActivityState, list[EventDraft]]:
        """@brief 整组已准入后开始一项 interact：内容 app 由引擎在合格内容里推荐一次并打开真实钉住推送，地点按人物
        点名的单元开始。

        :param row: 名称解析出的绑定行（内容 app，或带单元动作行 selected_action 的当前地点）。
        :param stay_location_id: 本轮请求生效之前的停留地点，内容前提按它筛选；与出发同组时是出发地，路上为 None。
        """
        agent = state.agents[pending.agent_id]
        origin = _activity_origin(state, pending)
        if row["kind"] == "app":
            request = ContentRequest(agent.agent_id, row["object_id"], stay_location_id,
                                     f"activity-{origin.action_id}")
            label, duration, data = self._app_plan(state, agent, request)
        else:
            label, duration, data = self._unit_plan(agent, row["selected_action"])
        params = ActivityStartParams("interact", duration, data=data)
        agent, activity = start_activity(agent, params, state.current_time, origin, self.context.body)
        activity = activity.model_copy(update={"name": activity_name("进行", label, state.current_time,
            state.timezone), "media_position_seconds": data.get("start_position_seconds", 0.0)}, deep=True)
        activity = schedule_feedback(activity, state.current_time, self.context.config.activity_feedback_seconds)
        changed = _put_activity(state, agent, activity)
        if data.get("pinned_by_push_id") is not None:
            changed, _ = open_app_push(changed, agent.agent_id, data["app_id"], data["content_id"])
        return self._collect(changed, activity, row)

    def _app_plan(self, state: WorldState, agent, request: ContentRequest) -> tuple[str, int, dict]:
        """@brief 内容 app 的活动名、计划时长与活动数据：在合格内容里抽取一次并从共享位置继续。"""
        app_id, catalog = request.app_id, self.context.catalog
        app = catalog.apps[app_id]
        content, pinned = select_content(catalog, state, request,
                                         random_stream(state.random_state, f"{request.activity_id}:0"))
        progress = agent.content_progress.get(content.content_id)
        position = 0.0 if progress is None else progress.position_seconds
        return app.name, _remaining(content, position), _content_data(content, app_id, position, pinned)

    def _unit_plan(self, agent, action: dict) -> tuple[str, int, dict]:
        """@brief 地点单元的活动名、计划时长与活动数据：活动名用单元名，从单元的下一条内容的共享位置开始，不抽随机数。"""
        contents = [self.context.catalog.contents[key] for key in action["content_ids"]]
        content, position = next_content(contents, agent)
        return action["detail"], _remaining(content, position), _content_data(content, None, position, None)

    def _collect(self, state: WorldState, activity: ActivityState, row) -> tuple:
        """@brief 驿站收货与人物点名的地点单元在同次受理中完成。"""
        place = self.context.catalog.locations.get(row["object_id"]) if row["kind"] == "location" else None
        if place is None or not place.accepts_parcels:
            return state, activity, []
        from worldmock.life.storefront_runtime import StorefrontRuntime

        changed, receipts = StorefrontRuntime(self.context).collect_parcels(state, activity.agent_id,
                                                                           place.location_id)
        return changed, activity, receipts

    def advance(self, state: WorldState, activity: ActivityState, target: int) -> tuple[WorldState, dict | None]:
        """@brief 泛型游标已推进后保存实际内容区间，不再另加心理效果。"""
        content_id = activity.data["content_id"]
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
            "completed": completed, "completed_rounds_after": progress.completed_rounds, "completion_effects": None}
        if completed:
            changed = release_app_pushes(changed, agent.agent_id, content_id)
            activity = self._complete_content(changed, activity, content, progress.completed_rounds)
            changed.activities[activity.activity_id] = activity
        return changed, evidence

    def settle_completion(self, state: WorldState, activity: ActivityState,
                          evidence: dict | None) -> tuple[WorldState, dict | None, list[dict]]:
        """@brief 刚做完的地点内容写了 completion_effects 时，在这一秒共同身体结算之后结算一次完成效果。

        读冻结目录里 evidence 指的那条内容（系列是刚做完的那一集，不是接下来那一集）；evidence 为空、completed 不为真
        或这条内容没有声明时原样返回。结算的身体写回人物，证据写进内容证据的 completion_effects，分录已由结算自己取走，
        盖上人物身份后返回，由时间推进排在这条 activity_progress 的 resource_flows 最前
        （docs/design/04-simulation-engine.md「出行、内容与查询」）。

        :param evidence: advance 返回的内容证据。
        :returns: （世界，内容证据，分录字典列表）。
        """
        if evidence is None or not evidence["completed"]:
            return state, evidence, []
        effects = self.context.catalog.contents[evidence["content_id"]].completion_effects
        if effects is None:
            return state, evidence, []
        agent = state.agents[activity.agent_id]
        body, settled, entries = settle_completion_effects(agent.body, effects, self.context.body)
        changed = _put_agent(state, agent.model_copy(update={"body": body}, deep=True))
        flows = [{"resource": entry.resource, "owner": f"agent:{agent.agent_id}", "direction": entry.direction,
                  "amount": entry.amount, "cause": entry.cause} for entry in entries]
        return changed, {**evidence, "completion_effects": settled}, validate_resource_flows(flows)

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
