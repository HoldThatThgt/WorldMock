"""@brief 商品曝光的稳定分组与屏障提交呈现在状态上的纯合并。"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping

from worldmock.state.world.world import WorldState


def tiebreak_digest(environment_id: str, agent_id: int, local_date: str, object_id: str) -> str:
    """@brief 使用环境、人物、本地日期与对象生成稳定决胜键。"""
    text = f"{environment_id}|{agent_id}|{local_date}|{object_id}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def subtype_of(kind: str, category: str = "", object_id: str = "") -> str:
    """@brief 反塌缩去重的子类标签：候选类别 + 品类 + 对象标识的稳定前缀。"""
    label = f"{kind}:{category}" if category else kind
    return f"{label}:{_object_prefix(object_id)}" if object_id else label


def _object_prefix(object_id: str) -> str:
    """@brief 对象标识在首个数字串之前的前缀；无前缀时取整个标识。"""
    head = object_id.rstrip("0123456789").rstrip("-_")
    return head or object_id


def merge_presentation(state: WorldState, pending_by_agent: Mapping[int, Mapping[str, int]]) -> WorldState:
    """@brief 在屏障提交或搜索完成后将各已完成唤醒的曝光时间戳逐人并入 presentation_state。"""
    if not pending_by_agent:
        return state
    presented = dict(state.presentation_state)
    for agent_id, stamps in pending_by_agent.items():
        if stamps:
            presented[agent_id] = {**presented.get(agent_id, {}), **stamps}
    return state.model_copy(update={"presentation_state": presented}, deep=True)
