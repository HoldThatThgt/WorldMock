"""@brief 已落盘主观记忆更新的封闭证据校验。"""

from __future__ import annotations

from typing import Any

from worldmock.errors import ProtocolError


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


def _valid_memory_snapshots(episodes: tuple[dict, ...], identities: tuple[str, ...]) -> bool:
    """@brief 主观快照逐条对应本次新增或合并的稳定身份。"""
    allowed = {"commitment", "relationship", "preference", "goal", "conflict",
               "resolution", "identity", "routine", "reflection"}
    if len(episodes) != len(identities):
        return False
    for item in episodes:
        if (not isinstance(item, dict)
                or set(item) != {"id", "type", "text", "epistemic_status"}
                or item.get("id") not in identities or item.get("type") not in allowed
                or not isinstance(item.get("text"), str) or not item["text"]
                or item.get("epistemic_status") != "subjective"):
            return False
    return {item["id"] for item in episodes} == set(identities)


