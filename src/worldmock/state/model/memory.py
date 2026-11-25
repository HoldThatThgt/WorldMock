"""@brief 按职责保存现有定义与纯计算。"""


from __future__ import annotations
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any, Literal, get_args




#: 主观快照恰为四个基本键。










#: 记忆的类别闭集（docs/design/06-agent.md「记忆的类别」）；次序即「近三天」的键序、回顾与检索的类别次序。
MemoryCategory = Literal["饮食", "工作", "生活"]
MEMORY_CATEGORIES: tuple[MemoryCategory, ...] = get_args(MemoryCategory)

#: 回顾登记事件的 data.kind。
MEMORY_DIGEST_KIND = "memory_digest_generated"
#: 回顾的三级，按零点任务里的处理次序。
MEMORY_DIGEST_TIERS = ("day", "month", "year")
#: 回顾正文与事件里的类别键，与 MEMORY_CATEGORIES 逐一对应。
MEMORY_DIGEST_KEYS = ("food", "work", "life")
#: 各级每类正文的字数上限（len，标点也算）。
MEMORY_DIGEST_TEXT_LIMITS = MappingProxyType({"day": 1000, "month": 800, "year": 2000})

#: 实际主观记忆的类型闭集，写入和评估共用。
SUBJECTIVE_TYPES = frozenset({"commitment", "relationship", "preference", "goal", "conflict",
                              "resolution", "identity", "routine", "reflection"})

#: 主观快照恰为四个基本键。
SUBJECTIVE_SNAPSHOT_FIELDS = frozenset({"id", "type", "text", "epistemic_status"})



def valid_subjective_snapshot(item: Any) -> bool:
    """@brief 校验实际主观条目快照：主观快照恰为四个基本键。"""
    if not isinstance(item, Mapping):
        return False
    return (set(item) == SUBJECTIVE_SNAPSHOT_FIELDS and isinstance(item.get("id"), str) and bool(item["id"])
            and item.get("type") in SUBJECTIVE_TYPES and isinstance(item.get("text"), str)
            and bool(item["text"]) and item.get("epistemic_status") == "subjective")



def valid_subjective_snapshots(episodes: Sequence, identities: Sequence[str]) -> bool:
    """@brief 核对所有主观快照与本次持久身份一一对应。"""
    return (len(episodes) == len(identities) and all(valid_subjective_snapshot(item) for item in episodes)
            and {item["id"] for item in episodes} == set(identities))
