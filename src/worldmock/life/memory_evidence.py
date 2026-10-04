"""@brief 已落盘主观记忆更新的封闭证据校验与写入点推导。

记忆写入点只有两处（docs/dev/SPEC-time-and-energy.md 第七章「finish 并入 conclude_activity」）：结束活动
（activity_conclusion）与就寝反思（reflection），阈值巩固不是写入点。AS2 的 MemoryUpdateEvidence 不带写入点，
由引擎按登记推导 memory_updated.data.trigger：同一决策已登记带记忆或看法的 conclude_activity（经 control_items
读出，唯一 items 形状）时含 activity_conclusion；绑定观察的唤醒原因含 wake_from_sleep 时含 reflection；顺序固定。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from worldmock.footprints.models import is_sha256
from typing import Any

from worldmock.errors import ProtocolError
from worldmock.life.control_items import control_items

#: 记忆写入点闭集，按此顺序列出本步实际发生的写入点。
MEMORY_WRITE_POINTS = ("activity_conclusion", "reflection")


def conclusion_has_memories(request: Any) -> bool:
    """@brief conclude_activity 请求是否带至少一条记忆或看法（items[].memories 与 items[].views）。"""
    return any(item.memories for item in control_items("conclude_activity", request))


def memory_write_points(requests: list, decision_id: str, observation: Any, written: bool) -> list[str]:
    """@brief 推导本步的记忆写入点列表（顺序为 activity_conclusion、reflection），并按写入点核对来源。

    每个写入点都要有登记：结束活动对应同一决策已登记的带记忆 conclude_activity，就寝反思对应醒来那一轮的观察；
    本步写入了条目却找不到任何写入点，就是宿主越过写入点写记忆，按协议违约拒绝。

    :param requests: 本人已持久化的 action_requested 事件。
    :param decision_id: 本步所属决策。
    :param observation: 本步绑定的观察事件。
    :param written: 本步是否新增或合并了主观条目。
    :raises ProtocolError: 写入了条目却没有任何写入点。
    """
    points = []
    if any(event.decision_id == decision_id and event.data.get("tool_name") == "conclude_activity"
           and conclusion_has_memories(event.data.get("request")) for event in requests):
        points.append(MEMORY_WRITE_POINTS[0])
    if "wake_from_sleep" in observation.data.get("wake_reasons", []):
        points.append(MEMORY_WRITE_POINTS[1])
    if written and not points:
        raise ProtocolError("Memory update has no registered write point")
    return points





def _memory_evidence_data(evidence: Any) -> dict[str, Any]:
    """@brief 核验实际记忆操作、主观条目快照及四个文件身份。"""
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
            or not isinstance(evidence.observation_event_id, str)
            or not evidence.observation_event_id
            or any(not isinstance(item, str) or not item for item in identities + verified)
            or len(set(identities)) != len(identities)
            or len(set(verified)) != len(verified)
            or (evidence.operation == "append_episodes" and not identities)
            or (evidence.operation == "refresh_summary" and
                (identities or evidence.memory_before_sha256 == evidence.memory_sha256))
            or not valid_subjective_snapshots(episodes, identities)):
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


#: 主观快照的键集：四个基本键；就寝反思另带来源 source=reflection（宿主标注，记忆层据此找归属日期）。










#: 实际主观记忆的类型闭集，写入和评估共用。
SUBJECTIVE_TYPES = frozenset({"commitment", "relationship", "preference", "goal", "conflict",
                              "resolution", "identity", "routine", "reflection"})
#: 普通快照必须恰含四个字段，反思可另携系统来源。
SUBJECTIVE_SNAPSHOT_FIELDS = frozenset({"id", "type", "text", "epistemic_status"})


def valid_subjective_snapshot(item: Any) -> bool:
    """@brief 校验实际主观条目快照，独立反思来源属于合法形状。"""
    if not isinstance(item, Mapping):
        return False
    keys = set(item)
    valid_keys = keys == SUBJECTIVE_SNAPSHOT_FIELDS or (
        keys == SUBJECTIVE_SNAPSHOT_FIELDS | {"source"} and item["source"] == "reflection")
    return (valid_keys and isinstance(item.get("id"), str) and bool(item["id"])
            and item.get("type") in SUBJECTIVE_TYPES and isinstance(item.get("text"), str)
            and bool(item["text"]) and item.get("epistemic_status") == "subjective")


def valid_subjective_snapshots(episodes: Sequence, identities: Sequence[str]) -> bool:
    """@brief 核对所有主观快照与本次持久身份一一对应。"""
    return (len(episodes) == len(identities) and all(valid_subjective_snapshot(item) for item in episodes)
            and {item["id"] for item in episodes} == set(identities))
