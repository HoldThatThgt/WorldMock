"""@brief 按职责保存现有定义与纯计算。"""


from __future__ import annotations
from collections.abc import Mapping, Sequence
from typing import Any




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
