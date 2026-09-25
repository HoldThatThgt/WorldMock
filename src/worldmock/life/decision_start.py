"""@brief 两阶段决策的 decision_started 证据装配（docs/dev/SPEC-life-realism.md「两阶段决策」）。

decision_started 事件不可变（events_no_update 触发器），而决策自述在决策轮
开启之后才由人物模型产生——因此引擎延迟落库：人物决策自述可得时才写
decision_started（data 含 decision_statement，occurred_at=决策提供时刻），
事件一次写完整、从不更新。纯函数装配在本模块，落库时序由 engine 接线：
决策自述响应写入完整事件；引擎直驱路径在首个动作登记、结算或中止时写入不含自述的事件。
"""

from __future__ import annotations

from typing import Any

from worldmock.footprints.models import (
    DECISION_STATEMENT_FIELD,
    WAKE_REASONS_FIELD,
    EventDraft,
    validate_decision_rhythm,
)
from worldmock.life.narration import DecisionStatementValidationError, validate_decision_statement

#: 决策自述阶段采样参数，与人物请求保持同值。
#: 与 custom/agents/life_agent.py 的同名常数同值（agent 层不可被 src 反向导入）。
_DECISION_STATEMENT_TEMPERATURE = 1.0
_DECISION_STATEMENT_MAX_TOKENS = 8000


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


def pop_decision_start(pending: dict[int, dict], agent_id: int, occurred_at: int,
                       decision_statement: str | None = None) -> EventDraft | None:
    """@brief 取出并移除一个人物的延迟 decision_started 草稿（无待写返回 None）。"""
    start = pending.pop(agent_id, None)
    if start is None:
        return None
    payload = dict(start["data"])
    if decision_statement is not None:
        payload[DECISION_STATEMENT_FIELD] = decision_statement
    return EventDraft(event_type="decision_started", occurred_at=occurred_at,
                      agent_id=agent_id, decision_id=start["decision_id"],
                      observed_at=occurred_at, data=payload)


def flush_decision_starts(pending: dict[int, dict], occurred_at: int) -> list[EventDraft]:
    """@brief 取出引擎直驱路径的全部剩余延迟草稿，不制造决策自述。"""
    return [draft for draft in (pop_decision_start(pending, agent_id, occurred_at)
                                for agent_id in sorted(pending)) if draft is not None]


def maybe_decision_statement_start(pending: dict[int, dict], agent_id: int, occurred_at: int,
                          exchange: Any) -> EventDraft | None:
    """@brief 模型交换是有效决策自述响应时取出该人物的 decision_started 草稿。

    决策自述响应判定（与 agent 层 prelude_decision_statement_request 的形状一致）：
    请求不带 tools、temperature=1.0、max_tokens=8000 且禁用思考——记忆巩固/反思的
    LLM 调用不带这些参数，行动阶段 ReAct 请求必带 tools，均被排除。
    响应带协议错误（超长/空）或文本不满足决策自述协议时返回 None（等待重试
    或引擎终止本次决策），不抛错。
    """
    request = getattr(exchange, "request", None)
    if not isinstance(request, dict) or "tools" in request:
        return None
    if (request.get("temperature") != _DECISION_STATEMENT_TEMPERATURE
            or request.get("max_tokens") != _DECISION_STATEMENT_MAX_TOKENS):
        return None
    extra_body = request.get("extra_body")
    if not isinstance(extra_body, dict) or extra_body.get("thinking") != {"type": "disabled"}:
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
        decision_statement = validate_decision_statement(str(content or "").strip())
    except DecisionStatementValidationError:
        return None
    return pop_decision_start(pending, agent_id, occurred_at, decision_statement)
