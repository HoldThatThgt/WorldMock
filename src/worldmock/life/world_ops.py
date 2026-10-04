"""@brief 世界状态替换、活动来源及持久证据的纯函数。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import TYPE_CHECKING, Any


from worldmock.footprints.models import EventDraft, validate_resource_flows
from worldmock.life import felt
from worldmock.life.activities import ActivityOrigin
from worldmock.life.body import last_flows
from worldmock.life.models import ActivityKind, PendingAction, WorldState, active_activities

if TYPE_CHECKING:
    from agentsociety2.society import DecisionContext, DirectToolCall, SchedulingState
    from worldmock.footprints.models import Checkpoint


def _put_activity(state: WorldState, agent, activity) -> WorldState:
    """@brief 将人物和活动一起投影到世界副本。"""
    agents = dict(state.agents)
    agents[agent.agent_id] = agent
    activities = dict(state.activities)
    activities[activity.activity_id] = activity
    return state.model_copy(update={"agents": agents, "activities": activities}, deep=True)


def _activity_origin(state: WorldState, pending: PendingAction) -> ActivityOrigin:
    """@brief 同一模型动作内给每条活动分配独立身份，保留共同的原动作归因。"""
    ordinal = 1 + sum(item.origin_action_id == pending.action_id for item in state.activities.values())
    return ActivityOrigin(pending.action_id, pending.decision_id, pending.requested_event_id, ordinal)


def _put_agent(state: WorldState, agent) -> WorldState:
    """@brief 将人物投影到世界副本。"""
    agents = dict(state.agents)
    agents[agent.agent_id] = agent
    return state.model_copy(update={"agents": agents}, deep=True)


def _put_agent_and_defer_game_turn(state: WorldState, agent) -> WorldState:
    """@brief 保存人物，并把本秒到期的游戏回合顺延一秒：防止达到同刻上限后游戏回合让人物在同一秒再就绪。"""
    agents = dict(state.agents)
    agents[agent.agent_id] = agent
    activities = dict(state.activities)
    for activity in active_activities(state, agent.agent_id):
        if activity.kind == ActivityKind.PLAY_GAME and activity.next_interaction_at == state.current_time:
            activities[activity.activity_id] = activity.model_copy(
                update={"next_interaction_at": state.current_time + 1}, deep=True
            )
    return state.model_copy(update={"agents": agents, "activities": activities}, deep=True)


def _request_sha(tool_name: str, request) -> str:
    """@brief 对工具名和规范化 Pydantic 请求计算稳定身份。"""
    payload = {"tool_name": tool_name, "request": request.model_dump(mode="json")}
    content = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(content).hexdigest()


def _body_settlement_data(settlement) -> dict:
    """@brief 将有类型身体结算转换为可持久 JSON 证据。"""
    return {
        "before": settlement.before.model_dump(mode="json"),
        "after": settlement.after.model_dump(mode="json"),
        "duration_seconds": settlement.duration_seconds,
        "basal_energy_kcal": settlement.basal_energy_kcal,
        "extra_energy_kcal": settlement.extra_energy_kcal,
        "food_energy_kcal": settlement.food_energy_kcal,
        "food_efficiency": settlement.food_efficiency,
        "work_efficiency": settlement.work_efficiency,
    }


def _resource_flows_data(agent_id: int, occurred_at: int) -> list[dict[str, Any]]:
    """@brief
    取走本区间身体结算分录并重盖主体与时间后校验序列化（docs/design/05-recording-engine.md#recording-engine-worldmock-产品设计说明书-记录引擎「事件正文与资源证据」）。

    body 结算函数以占位 owner 与零时间戳缓冲分录；引擎按事件归属重盖为
    agent:{id} 与事件模拟时间，序列化为 {resource, owner, direction, amount,
    cause}[] 经 validate_resource_flows fail-closed 校验。无分录的结算点
    返回空列表（事件不携带该字段）。
    """
    payload = [
        {
            "resource": stamped.resource,
            "owner": stamped.owner,
            "direction": stamped.direction,
            "amount": stamped.amount,
            "cause": stamped.cause,
        }
        for stamped in (replace(entry, owner=f"agent:{agent_id}", occurred_at=occurred_at) for entry in last_flows())
    ]
    return validate_resource_flows(payload) if payload else []


def _activity_progress_phrase(activity) -> str:
    """@brief 活动进度短语（不含完整快照，观察合同禁完整活动快照）。"""
    remaining = max(0, activity.planned_duration_seconds - activity.elapsed_seconds)
    return f"还剩约{round(remaining / 60)}分钟"


def _paused_activity_control(activity) -> dict:
    """@brief 暂停活动的控制身份：activity_id 与剩余时长（不含完整快照）。"""
    remaining = max(0, activity.planned_duration_seconds - activity.elapsed_seconds)
    return {"activity_id": activity.activity_id, "kind": activity.kind.value, "remaining_seconds": remaining}


def _current_branch_event(events: list, manifest: dict):
    """@brief 定位属于当前运行且绑定当前父检查点的唯一分支创建事件。"""
    matches = [
        event
        for event in events
        if event.run_id == manifest.get("run_id")
        and event.event_type == "environment_event"
        and event.data.get("kind") == "branch_created"
        and event.data.get("parent_checkpoint_id") == manifest.get("parent_checkpoint_id")
    ]
    return matches[0] if len(matches) == 1 else None


def _failure_draft(occurred_at: int, error: Exception | str, round_id: str | None) -> EventDraft:
    """@brief 形成可覆盖先前 run_ended 的致命运行失败证据。"""
    error_type = type(error).__name__ if isinstance(error, Exception) else "RuntimeError"
    return EventDraft(
        event_type="run_failed",
        occurred_at=occurred_at,
        visibility="system",
        data={"error_type": error_type, "error": str(error), "round_id": round_id},
    )


def meal_windows_by_agent(scenario) -> dict[int, "felt.MealWindows"]:
    """@brief 人物档案餐时窗口索引（观察与滞回判定同源）。"""
    return {
        config.agent_id: felt.MealWindows(lunch=config.lunch_window, dinner=config.dinner_window)
        for config in scenario.agents
    }


def agent_config(scenario, agent_id: int):
    """@brief 取得冻结场景中该人物的配置。"""
    return next(item for item in scenario.agents if item.agent_id == agent_id)


def place_names(scenario) -> dict[str, str]:
    """@brief 地点身份到场景中可读名称的纯投影。"""
    return {item.location_id: item.name for item in scenario.locations}
