"""@brief 世界状态替换、活动来源及持久证据的纯函数。"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any


from worldmock.recording_engine.events.models import EventDraft, validate_resource_flows
from worldmock.foundation.serialization import sha256_json
from worldmock.simulation_engine.body import felt
from worldmock.simulation_engine.body.body import last_flows
from worldmock.state.world.kinds import ActivityKind
from worldmock.state.decisions.state import PendingAction
from worldmock.state.world.world import WorldState, active_activities

if TYPE_CHECKING:
    from worldmock.simulation_engine.activities.lifecycle.activities import ActivityOrigin
    from agentsociety2.society import DecisionContext, DirectToolCall, SchedulingState
    from worldmock.recording_engine.events.models import Checkpoint


def _put_activity(state: WorldState, agent, activity) -> WorldState:
    """@brief 将人物和活动一起投影到世界副本。"""
    agents = dict(state.agents)
    agents[agent.agent_id] = agent
    activities = dict(state.activities)
    activities[activity.activity_id] = activity
    return state.model_copy(update={"agents": agents, "activities": activities}, deep=True)


def _activity_origin(state: WorldState, pending: PendingAction) -> ActivityOrigin:
    """@brief 一条动作只开始一项活动，活动保留这条动作、决策与请求事件的归因。"""
    from worldmock.simulation_engine.activities.lifecycle.activities import ActivityOrigin

    return ActivityOrigin(pending.action_id, pending.decision_id, pending.requested_event_id)


def _put_agent(state: WorldState, agent) -> WorldState:
    """@brief 将人物投影到世界副本。"""
    agents = dict(state.agents)
    agents[agent.agent_id] = agent
    return state.model_copy(update={"agents": agents}, deep=True)


def _request_sha(tool_name: str, request) -> str:
    """@brief 对工具名和规范化 Pydantic 请求计算稳定身份。"""
    payload = {"tool_name": tool_name, "request": request.model_dump(mode="json")}
    return sha256_json(payload)


def _body_settlement_data(settlement) -> dict:
    """@brief 将有类型身体结算转换为可持久 JSON 证据。"""
    return {
        "before": settlement.before.model_dump(mode="json"),
        "after": settlement.after.model_dump(mode="json"),
        "duration_seconds": settlement.duration_seconds,
        "basal_energy_kcal": settlement.basal_energy_kcal,
        "extra_energy_kcal": settlement.extra_energy_kcal,
    }


def _resource_flows_data(agent_id: int, occurred_at: int) -> list[dict[str, Any]]:
    """@brief
    取走本区间身体结算分录并重盖主体与时间后校验序列化（docs/design/05-recording-engine.md「事件与来源」）。

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


def meal_windows_by_agent(persona) -> dict[int, "felt.MealWindows"]:
    """@brief 人物档案餐时窗口索引（观察与滞回判定同源）。"""
    return {persona.agent_id: felt.MealWindows(lunch=persona.lunch_window, dinner=persona.dinner_window)}


def place_names(catalog) -> dict[str, str]:
    """@brief 地点身份到完整显示名的纯投影。"""
    return {key: item.display_name for key, item in catalog.locations.items()}
