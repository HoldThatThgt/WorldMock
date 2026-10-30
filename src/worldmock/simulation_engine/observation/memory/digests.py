"""@brief 日、月、年回顾事件的唯一读取入口：按足迹库对象与人物增量读取 memory_digest_generated 事件。

回顾事件由零点任务登记，同一人物同一级同一期至多一条；「近三天」与检索都经 registered_digests 读回顾，再按各自的截止
序号筛选。本模块只读事件，不写任何东西。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from weakref import WeakKeyDictionary

from worldmock.foundation.errors import IntegrityError
from worldmock.recording_engine.events.models import Event
from worldmock.recording_engine.storage.store import FootprintStore
from worldmock.state.model.memory import MEMORY_DIGEST_KIND


@dataclass(slots=True)
class _Registry:
    """@brief 一个人物已读到的回顾事件：读到的末事件序号与（级，期）→ 登记事件。"""

    last_seq: int = 0
    digests: dict[tuple[str, str], Event] = field(default_factory=dict)


#: 存储对象 → 人物 → 已读到的回顾事件；换成恢复后的新存储对象时从头读取。
_REGISTRIES: WeakKeyDictionary[FootprintStore, dict[int, _Registry]] = WeakKeyDictionary()


def registered_digests(store: FootprintStore, agent_id: int) -> Mapping[tuple[str, str], Event]:
    """@brief 这个人物已登记的回顾事件（只读映射，键是（级，期））；同一期读到第二条是完整性故障。"""
    registry = _REGISTRIES.setdefault(store, {}).setdefault(agent_id, _Registry())
    last = store.last_event_seq
    if last > registry.last_seq:
        digests = dict(registry.digests)
        for event in store.read_agent_events("environment_event", agent_id, after=registry.last_seq):
            if event.data.get("kind") != MEMORY_DIGEST_KIND:
                continue
            key = (event.data["tier"], event.data["period"])
            if key in digests:
                logging.error("Memory digest period was registered twice")
                raise IntegrityError("Memory digest period was registered twice")
            digests[key] = event
        registry.digests, registry.last_seq = digests, last
    return MappingProxyType(registry.digests)
