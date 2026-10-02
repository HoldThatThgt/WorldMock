"""@brief 已落盘主观记忆更新的封闭证据校验与写入点推导。

记忆写入点只有两处（docs/dev/SPEC-time-and-energy.md 第七章「finish 并入 conclude_activity」）：结束活动
（activity_conclusion）与就寝反思（reflection），阈值巩固不是写入点。AS2 的 MemoryUpdateEvidence 不带写入点，
由引擎按登记推导 memory_updated.data.trigger：同一决策已登记带记忆或看法的 conclude_activity（经 control_items
读出，唯一 items 形状）时含 activity_conclusion；绑定观察的唤醒原因含 wake_from_sleep 时含 reflection；顺序固定。
"""

from __future__ import annotations

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


def _is_sha256(value: Any) -> bool:
    """@brief 判断字符串是否为规范小写 SHA-256。"""
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


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
            or any(not _is_sha256(value) for value in hashes)
            or type(evidence.consolidated) is not bool
            or not isinstance(evidence.observation_event_id, str)
            or not evidence.observation_event_id
            or any(not isinstance(item, str) or not item for item in identities + verified)
            or len(set(identities)) != len(identities)
            or len(set(verified)) != len(verified)
            or (evidence.operation == "append_episodes" and not identities)
            or (evidence.operation == "refresh_summary" and
                (identities or evidence.memory_before_sha256 == evidence.memory_sha256))
            or not _valid_memory_snapshots(episodes, identities)):
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
_SNAPSHOT_KEYS = frozenset({"id", "type", "text", "epistemic_status"})


def _snapshot_keys_valid(item: dict) -> bool:
    """@brief 快照键集是四个基本键，或另加 source 且来源为就寝反思。"""
    return set(item) == _SNAPSHOT_KEYS or (set(item) == _SNAPSHOT_KEYS | {"source"} and item["source"] == "reflection")


def _valid_memory_snapshots(episodes: tuple[dict, ...], identities: tuple[str, ...]) -> bool:
    """@brief 主观快照逐条对应本次新增或合并的稳定身份。"""
    allowed = {"commitment", "relationship", "preference", "goal", "conflict",
               "resolution", "identity", "routine", "reflection"}
    if len(episodes) != len(identities):
        return False
    for item in episodes:
        if (not isinstance(item, dict)
                or not _snapshot_keys_valid(item)
                or item.get("id") not in identities or item.get("type") not in allowed
                or not isinstance(item.get("text"), str) or not item["text"]
                or item.get("epistemic_status") != "subjective"):
            return False
    return {item["id"] for item in episodes} == set(identities)


