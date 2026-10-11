"""@brief decision_started 证据装配与行动请求判定（docs/design/06-agent.md「单一行动阶段」）。

decision_started 事件不可变（events_no_update 触发器），决策轮开启时只冻结轮次字段；引擎在本次决策的首个
行动请求（phase=request、带生活工具、thinking 开启、无 purpose）与该请求同批提交 decision_started，排在请求之前。
引擎直驱路径（没有模型证据）在首个动作登记、结算或中止时写入。data 恒为闭集
{round_id, round_order, world_state_version, wake_reasons}。纯函数装配在本模块，落库时序由 engine 接线。
"""

from __future__ import annotations

from typing import Any

from worldmock.footprints.models import WAKE_REASONS_FIELD, EventDraft, validate_decision_rhythm
from worldmock.life.action_contract import START_TOOLS

#: 行动请求才开放的世界请求工具（start_main_activity）；只读问答、记忆巩固与反思的请求都不带它。
_ACTION_TOOL = START_TOOLS[0]


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


def is_action_request(request: Any) -> bool:
    """@brief 行动请求的唯一判定：没有 purpose、tools 含 start_main_activity 且 thinking 开启。

    与存储侧 SQL 谓词同义（docs/design/06-agent.md「单一行动阶段」）；工作验收与零点计划带 purpose，
    只读问答、记忆巩固与反思不带 start_main_activity。
    """
    if not isinstance(request, dict) or request.get("purpose") is not None:
        return False
    extra_body = request.get("extra_body")
    if not isinstance(extra_body, dict) or extra_body.get("thinking") != {"type": "enabled"}:
        return False
    tools = request.get("tools")
    if not isinstance(tools, list):
        return False
    return any(isinstance(item, dict) and isinstance(item.get("function"), dict)
               and item["function"].get("name") == _ACTION_TOOL for item in tools)


def maybe_action_request_start(pending: dict[int, dict], agent_id: int, occurred_at: int,
                               exchange: Any) -> EventDraft | None:
    """@brief 模型交换是本次决策的行动请求时取出该人物的 decision_started 草稿。

    只认 phase=request 的交换；响应、失败与非行动请求返回 None。已取出过的人物再次命中时返回 None，
    因此只有首个行动请求带出 decision_started。
    """
    if getattr(exchange, "phase", None) != "request" or not is_action_request(getattr(exchange, "request", None)):
        return None
    return pop_decision_start(pending, agent_id, occurred_at)


def response_self_note(response: Any) -> str | None:
    """@brief 提交世界请求那一次回复的正文：去掉首尾空白后的 choices[0].message.content，空或缺失时为 None。"""
    choices = response.get("choices") if isinstance(response, dict) else None
    first = choices[0] if isinstance(choices, list) and choices else None
    message = first.get("message") if isinstance(first, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    text = content.strip() if isinstance(content, str) else ""
    return text or None
