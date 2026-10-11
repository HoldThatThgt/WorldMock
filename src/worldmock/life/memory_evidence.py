"""@brief 主观记忆只在真实活动收尾或当天第一次睡醒时写入，来源与快照严格核验。"""

from __future__ import annotations

import logging

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from worldmock.footprints.models import is_sha256
from typing import Any
from zoneinfo import ZoneInfo

from worldmock.errors import ProtocolError
from worldmock.life.control_items import control_items
from worldmock.life.models import ActivityKind, ActivityStatus

#: 记忆写入点闭集，按此顺序列出本步实际发生的写入点。
MEMORY_WRITE_POINTS = ("activity_conclusion", "reflection")


def conclusion_has_memories(request: Any) -> bool:
    """@brief conclude_activity 请求是否带至少一条记忆或看法（items[].memories 与 items[].views）。"""
    return any(item.memories for item in control_items("conclude_activity", request))


def due_reflection(state: Any, agent_id: int) -> dict[str, str] | None:
    """@brief 就寝反思此刻该不该做：本人恰在此刻睡醒、这是当天第一次睡醒、这一秒还没有提交过决策时，给出归属日期与所属睡眠。

    只读世界状态里已有的事实。归属日期是睡醒所在本地日的前一天；恰在本地零点结束的睡眠属于新的一天，
    被运行终点截断的睡眠不是睡醒。

    :param state: 世界状态（读 current_time、timezone、agents 与 activities）。
    :param agent_id: 人物身份。
    :returns: 不该做时为 None，否则为 {date, sleep_activity_id}。
    """
    now, zone = state.current_time, ZoneInfo(state.timezone)
    if state.agents[agent_id].last_decision_time == now:
        return None
    sleeps = [item for item in state.activities.values() if item.agent_id == agent_id
              and item.kind == ActivityKind.SLEEP and item.status == ActivityStatus.COMPLETED]
    woke = next((item for item in sleeps if item.ended_at == now), None)
    if woke is None:
        return None
    today = datetime.fromtimestamp(now, zone).date()
    if any(item is not woke and datetime.fromtimestamp(item.ended_at, zone).date() == today for item in sleeps):
        return None
    return {"date": (today - timedelta(days=1)).isoformat(), "sleep_activity_id": woke.activity_id}


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


#: 主观快照的键集：四个基本键；就寝反思另带来源 source=reflection（宿主标注，环境据此区分两路登记）。










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
