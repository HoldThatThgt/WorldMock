"""@brief 按职责保存现有定义与纯计算。"""


from __future__ import annotations
import logging
from dataclasses import dataclass
from worldmock.foundation.serialization import is_sha256
from typing import Any, Literal
from worldmock.foundation.errors import ProtocolError
from worldmock.state.decisions.round import DecisionContext
from worldmock.state.interaction.control_items import control_items

from worldmock.state.model.memory import valid_subjective_snapshots

#: 记忆写入点闭集，按此顺序列出本步实际发生的写入点。
MEMORY_WRITE_POINTS = ("activity_conclusion",)
#: memory_updated.data 的十一个键，按引擎构造次序。
MEMORY_UPDATE_KEYS = ("operation", "created_episode_ids", "merged_episode_ids", "episodes",
                      "memory_before_sha256", "memory_sha256", "episodes_sha256",
                      "observation_event_id", "verified_event_ids", "trigger", "decision_id")


@dataclass(frozen=True, slots=True)
class MemoryUpdateEvidence:
    """@brief 人物工作区已落盘记忆的内容身份（步尾登记）。"""

    context: DecisionContext                          # 本决策的上下文
    operation: Literal["append_episodes", "refresh_summary"]   # 有新建或合并为 append_episodes，否则 refresh_summary
    created_episode_ids: tuple[str, ...]              # 新建条目的身份
    merged_episode_ids: tuple[str, ...]               # 合并条目的身份
    episodes: tuple[dict[str, str], ...]              # 四键快照，新建在前
    memory_before_sha256: str | None                  # 写入前 MEMORY.md 的摘要，人物第一次保存时为 None
    memory_sha256: str                                # 写入后 MEMORY.md 的摘要
    episodes_sha256: str                              # 写入后 memory/episodes.jsonl 的摘要
    observation_event_id: str                         # 本决策冻结观察的事件身份
    verified_event_ids: tuple[str, ...]               # 进入事实视图的来源事件身份



def conclusion_has_memories(request: Any) -> bool:
    """@brief conclude_activity 请求是否带至少一条记忆或看法（items[].memories 与 items[].views）。"""
    return any(item.memories for item in control_items("conclude_activity", request))



def memory_write_points(requests: list, decision_id: str, written: bool) -> list[str]:
    """@brief 推导步尾登记的记忆写入点（只可能是 activity_conclusion），并核对来源。

    结束活动对应同一决策已登记的带记忆 conclude_activity。
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
    """@brief 核验实际记忆操作、主观条目快照及三个文件身份；写入前摘要可为空，来源观察为非空字符串。

    返回按 MEMORY_UPDATE_KEYS 前九个键次序构造的字典，trigger 与 decision_id 由引擎随后补上。
    """
    created = tuple(evidence.created_episode_ids)
    merged = tuple(evidence.merged_episode_ids)
    identities = created + merged
    episodes = tuple(evidence.episodes)
    verified = tuple(evidence.verified_event_ids)
    before = evidence.memory_before_sha256
    if (evidence.operation not in {"append_episodes", "refresh_summary"}
            or (before is not None and not is_sha256(before))
            or any(not is_sha256(value) for value in (evidence.memory_sha256, evidence.episodes_sha256))
            or not isinstance(evidence.observation_event_id, str) or not evidence.observation_event_id
            or any(not isinstance(item, str) or not item for item in identities + verified)
            or len(set(identities)) != len(identities)
            or len(set(verified)) != len(verified)
            or (evidence.operation == "append_episodes" and not identities)
            or (evidence.operation == "refresh_summary" and
                (identities or before == evidence.memory_sha256))
            or not valid_subjective_snapshots(episodes, identities)):
        logging.error("Memory update evidence is invalid")
        raise ProtocolError("Memory update payload is invalid")
    return {"operation": evidence.operation, "created_episode_ids": list(created),
            "merged_episode_ids": list(merged), "episodes": list(episodes),
            "memory_before_sha256": before,
            "memory_sha256": evidence.memory_sha256,
            "episodes_sha256": evidence.episodes_sha256,
            "observation_event_id": evidence.observation_event_id,
            "verified_event_ids": list(verified)}
