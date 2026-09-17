"""@brief 两阶段决策的 decision_started 证据装配（修订合同 L1562-1573）。

decision_started 事件不可变（events_no_update 触发器），而独白在决策轮
开启之后才由人物模型产生——因此引擎延迟落库：人物独白可得时才写
decision_started（data 含 inner_monologue，occurred_at=决策提供时刻），
事件一次写完整、从不更新。纯函数装配在本模块，落库时序由 engine 接线：
独白响应（含独白）→ 首个动作登记/结算/中止（不含独白的引擎直驱路径兜底）。
"""

from __future__ import annotations

from typing import Any

from worldmock.footprints.models import (
    INNER_MONOLOGUE_FIELD,
    WAKE_REASONS_FIELD,
    EventDraft,
)
from worldmock.life.narration import MonologueValidationError, validate_monologue

#: 独白阶段采样参数（规格 L1562：DeepSeek 一般对话推荐温度 1.3、max_tokens 400）。
#: 与 custom/agents/life_agent.py 的同名常数同值（agent 层不可被 src 反向导入）。
_MONOLOGUE_TEMPERATURE = 1.3
_MONOLOGUE_MAX_TOKENS = 400


def defer_decision_starts(contexts: list[Any], round_id: str,
                          state: Any) -> dict[int, dict]:
    """@brief 冻结一轮各人物的 decision_started 轮次字段（延迟落库簿记）。

    :param contexts: 本轮宿主决策上下文（含 agent_id/decision_id）
    :param round_id: 决策轮标识
    :param state: 当前世界状态（取 version 与人物待处理唤醒原因）
    :returns: agent_id -> {decision_id, data}（data 为轮次字段闭集）
    """
    return {
        item.agent_id: {
            "decision_id": item.decision_id,
            "data": {"round_id": round_id, "round_order": order,
                     "world_state_version": state.version,
                     WAKE_REASONS_FIELD: list(
                         state.agents[item.agent_id].pending_wake_reasons)},
        } for order, item in enumerate(contexts)}


def pop_decision_start(pending: dict[int, dict], agent_id: int, occurred_at: int,
                       inner_monologue: str | None = None) -> EventDraft | None:
    """@brief 取出并移除一个人物的延迟 decision_started 草稿（无待写返回 None）。"""
    start = pending.pop(agent_id, None)
    if start is None:
        return None
    payload = dict(start["data"])
    if inner_monologue is not None:
        payload[INNER_MONOLOGUE_FIELD] = inner_monologue
    return EventDraft(event_type="decision_started", occurred_at=occurred_at,
                      agent_id=agent_id, decision_id=start["decision_id"],
                      observed_at=occurred_at, data=payload)


def flush_decision_starts(pending: dict[int, dict], occurred_at: int) -> list[EventDraft]:
    """@brief 兜底取出全部剩余延迟草稿（引擎直驱路径，不含独白）。"""
    return [draft for draft in (pop_decision_start(pending, agent_id, occurred_at)
                                for agent_id in sorted(pending)) if draft is not None]


def maybe_monologue_start(pending: dict[int, dict], agent_id: int, occurred_at: int,
                          exchange: Any) -> EventDraft | None:
    """@brief 模型交换是有效独白响应时取出该人物的 decision_started 草稿。

    独白响应判定（与 agent 层 prelude_monologue_request 的形状一致）：
    请求不带 tools、temperature=1.3 且 max_tokens=400——记忆巩固/反思的
    LLM 调用不带这两个参数，行动阶段 ReAct 请求必带 tools，均被排除。
    响应带协议错误（超长/空）或文本不满足独白协议时返回 None（等待重试
    或兜底落库），不抛错。
    """
    request = getattr(exchange, "request", None)
    if not isinstance(request, dict) or "tools" in request:
        return None
    if (request.get("temperature") != _MONOLOGUE_TEMPERATURE
            or request.get("max_tokens") != _MONOLOGUE_MAX_TOKENS):
        return None
    if getattr(exchange, "error", None) is not None:
        return None
    response = getattr(exchange, "response", None)
    if not isinstance(response, dict):
        return None
    choices = response.get("choices")
    message = choices[0].get("message") if isinstance(choices, list) and choices else None
    content = message.get("content") if isinstance(message, dict) else None
    try:
        monologue = validate_monologue(str(content or "").strip())
    except MonologueValidationError:
        return None
    return pop_decision_start(pending, agent_id, occurred_at, monologue)
