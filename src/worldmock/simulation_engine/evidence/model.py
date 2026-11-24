"""@brief 失败证据追记。"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from worldmock.foundation.errors import ProtocolError
from worldmock.recording_engine.events.models import EventDraft
from worldmock.simulation_engine.world.context import EngineContext

_LOGGER = logging.getLogger(__name__)
#: 失败追记入口允许写入的纯证据事件类型。
FAILED_EVIDENCE_TYPES = frozenset({"decision_started", "observation", "model_request", "model_response"})


def validate_failed_evidence(agent_id: int, wakeup_id: str,
                             drafts: Sequence[EventDraft]) -> tuple[EventDraft, ...]:
    """@brief 核验模型失败前传回的纯证据草稿同属该人物与唤醒且不夹带状态事件。"""
    checked = tuple(drafts)
    for draft in checked:
        if draft.agent_id != agent_id or draft.wakeup_id != wakeup_id:
            _LOGGER.error("Failed evidence ownership mismatch for wakeup %s", wakeup_id)
            raise ProtocolError("Failed evidence ownership mismatch")
        if draft.event_type not in FAILED_EVIDENCE_TYPES:
            _LOGGER.error("Failed evidence rejects non-evidence event type: %s", draft.event_type)
            raise ProtocolError("Failed evidence contains non-evidence event type")
    return checked


def record_failed_evidence(context: EngineContext, agent_id: int, wakeup_id: str,
                           drafts: Sequence[EventDraft]) -> None:
    """@brief 仅在故障退出前追加失败模型或唤醒证据，不改变已提交世界版本或状态。"""
    if agent_id not in context.state.agents:
        _LOGGER.error("Failed evidence targets unknown agent %s", agent_id)
        raise ProtocolError("Failed evidence targets unknown agent")
    checked = validate_failed_evidence(agent_id, wakeup_id, drafts)
    if checked:
        context.store.append_evidence(list(checked))
