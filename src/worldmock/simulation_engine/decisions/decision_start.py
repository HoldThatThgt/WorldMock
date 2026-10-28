"""@brief 按职责保存现有定义与纯计算。"""


from __future__ import annotations
from typing import Any
from worldmock.recording_engine.events.models import WAKE_REASONS_FIELD, EventDraft, validate_decision_rhythm

from worldmock.state.model.exchange import is_action_request


def defer_decision_starts(contexts: list[Any], round_id: str,
                          state: Any) -> dict[int, dict]:
    """@brief 冻结一轮各人物的 decision_started 轮次字段（延迟落库簿记）。

    本轮唤醒原因在冻结时即经 validate_decision_rhythm 校验，闭集外的原因在写入时拒绝。

    :param contexts: 本轮宿主决策上下文（含 agent_id/decision_id）
    :param round_id: 决策轮标识
    :param state: 当前世界状态（取 version 与人物待处理唤醒原因）
    :returns: agent_id -> {decision_id, data}（data 为轮次字段闭集）
    :raises ValueError: 人物待处理唤醒原因含冻结词表之外的成员。
    """
    starts: dict[int, dict] = {}
    for order, item in enumerate(contexts):
        data = {"round_id": round_id, "round_order": order,
                "world_state_version": state.version,
                WAKE_REASONS_FIELD: list(state.agents[item.agent_id].pending_wake_reasons)}
        validate_decision_rhythm(data)
        starts[item.agent_id] = {"decision_id": item.decision_id, "data": data}
    return starts



def pop_decision_start(pending: dict[int, dict], agent_id: int, occurred_at: int) -> EventDraft | None:
    """@brief 取出并移除一个人物的延迟 decision_started 草稿（无待写返回 None）。"""
    start = pending.pop(agent_id, None)
    if start is None:
        return None
    return EventDraft(event_type="decision_started", occurred_at=occurred_at,
                      agent_id=agent_id, decision_id=start["decision_id"],
                      observed_at=occurred_at, data=dict(start["data"]))



def flush_decision_starts(pending: dict[int, dict], occurred_at: int) -> list[EventDraft]:
    """@brief 取出引擎直驱路径的全部剩余延迟草稿。"""
    return [draft for draft in (pop_decision_start(pending, agent_id, occurred_at)
                                for agent_id in sorted(pending)) if draft is not None]



def maybe_action_request_start(pending: dict[int, dict], agent_id: int, occurred_at: int,
                               exchange: Any) -> EventDraft | None:
    """@brief 模型交换是本次决策的行动请求时取出该人物的 decision_started 草稿。

    只认 phase=request 的交换；响应、失败与非行动请求返回 None。已取出过的人物再次命中时返回 None，
    因此只有首个行动请求带出 decision_started。
    """
    if getattr(exchange, "phase", None) != "request" or not is_action_request(getattr(exchange, "request", None)):
        return None
    return pop_decision_start(pending, agent_id, occurred_at)
