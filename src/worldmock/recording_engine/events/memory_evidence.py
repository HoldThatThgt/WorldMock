"""@brief 按职责保存现有定义与纯计算。"""


from __future__ import annotations
import logging
from worldmock.foundation.serialization import is_sha256
from typing import Any
from worldmock.foundation.errors import ProtocolError
from worldmock.state.interaction.control_items import control_items

from worldmock.state.model.memory import valid_subjective_snapshots

#: 记忆写入点闭集，按此顺序列出本步实际发生的写入点。
MEMORY_WRITE_POINTS = ("activity_conclusion", "reflection")



def conclusion_has_memories(request: Any) -> bool:
    """@brief conclude_activity 请求是否带至少一条记忆或看法（items[].memories 与 items[].views）。"""
    return any(item.memories for item in control_items("conclude_activity", request))



def memory_write_points(requests: list, decision_id: str, written: bool) -> list[str]:
    """@brief 推导步尾登记的记忆写入点（只可能是 activity_conclusion），并核对来源。

    结束活动对应同一决策已登记的带记忆 conclude_activity；就寝反思的写入点由取观察之前的反思登记给出，不在这里推导。
    本步写入了条目却找不到写入点，就是宿主越过写入点写记忆，按协议违约拒绝。

    :param requests: 本人已持久化的 action_requested 事件。
    :param decision_id: 本步所属决策。
    :param written: 本步是否新增或合并了主观条目。
    :raises ProtocolError: 写入了条目却没有任何写入点。
    """
    points = []
    if any(event.decision_id == decision_id and event.data.get("tool_name") == "conclude_activity"
           and conclusion_has_memories(event.data.get("request")) for event in requests):
        points.append(MEMORY_WRITE_POINTS[0])
    if written and not points:
        logging.error("Memory update evidence is invalid")
        raise ProtocolError("Memory update has no registered write point")
    return points






def _memory_evidence_data(evidence: Any) -> dict[str, Any]:
    """@brief 核验实际记忆操作、主观条目快照及四个文件身份；来源观察为空（就寝反思）或非空字符串（步尾）。"""
    created = tuple(evidence.created_episode_ids)
    merged = tuple(evidence.merged_episode_ids)
    identities = created + merged
    episodes = tuple(evidence.episodes)
    verified = tuple(evidence.verified_event_ids)
    hashes = (evidence.memory_before_sha256, evidence.memory_sha256,
              evidence.episodes_sha256, evidence.state_sha256)
    if (evidence.operation not in {"append_episodes", "refresh_summary"}
            or any(not is_sha256(value) for value in hashes)
            or type(evidence.consolidated) is not bool
            or evidence.observation_event_id is not None and (
                not isinstance(evidence.observation_event_id, str) or not evidence.observation_event_id)
            or any(not isinstance(item, str) or not item for item in identities + verified)
            or len(set(identities)) != len(identities)
            or len(set(verified)) != len(verified)
            or (evidence.operation == "append_episodes" and not identities)
            or (evidence.operation == "refresh_summary" and
                (identities or evidence.memory_before_sha256 == evidence.memory_sha256))
            or not valid_subjective_snapshots(episodes, identities)):
        logging.error("Memory update evidence is invalid")
        raise ProtocolError("Memory update payload is invalid")
    return {"operation": evidence.operation, "created_episode_ids": list(created),
            "merged_episode_ids": list(merged), "episodes": list(episodes),
            "memory_before_sha256": evidence.memory_before_sha256,
            "memory_sha256": evidence.memory_sha256,
            "episodes_sha256": evidence.episodes_sha256,
            "state_sha256": evidence.state_sha256,
            "consolidated": evidence.consolidated,
            "observation_event_id": evidence.observation_event_id,
            "verified_event_ids": list(verified)}
