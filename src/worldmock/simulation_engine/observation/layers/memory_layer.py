"""@brief 记忆层的门面：按决策组装「可查」「近三天」「今天」并执行字数上限（组装与兜底在 observation/memory/layout.py），
另给人设看法证据与回看去重；「今天」的条目每次决策一条、从下一次决策起可见，由 observation/memory/entries.py 生成。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from weakref import WeakKeyDictionary

from worldmock.agents.prompt.narration import render_layer_json
from worldmock.foundation.time.readable import local_date
from worldmock.recording_engine.storage.store import FootprintStore
from worldmock.simulation_engine.observation.memory.categories import memory_book
from worldmock.simulation_engine.observation.memory.days import day_digests, published_today, recent_block
from worldmock.simulation_engine.observation.memory.entries import (
    PROGRESS_PREFIX,
    activity_facts,
    agent_events,
    note_labels,
    published_entry,
    short_name,
    successful_requests,
)
from worldmock.simulation_engine.observation.memory.layout import (
    PublishedEntry,
    TodayFold,
    assemble_layer,
    earliest_date,
    fold_entry,
    fold_items,
    require_budget,
)
from worldmock.simulation_engine.observation.targets.tool_layer import LayerContext
from worldmock.simulation_engine.observation.wording.settled_text import ActivityFacts
from worldmock.state.interaction.control_items import control_items
from worldmock.state.world.activity import ActivityState


@dataclass(frozen=True, slots=True)
class MemoryView:
    """@brief 记忆层、真实活动证据、仍可回看的活动名称与已发布界。"""

    layer: dict
    facts: ActivityFacts
    note_labels: dict[str, tuple[str, ...]]
    #: 已发布界 P = max(C_last, B_today)（【E5】）。
    published: int


@dataclass(slots=True)
class _TodayCache:
    """@brief 一个人物当天「今天」的递推缓存：日期、固定部分（可查与近三天）的渲染文本、已折入的条目数与递推状态。"""

    date: str
    fixed: str
    count: int
    fold: TodayFold


#: 存储对象 → 人物 → 当天的递推缓存；换成恢复后的新存储对象时从当天第一条重新递推。
_FOLDS: WeakKeyDictionary[FootprintStore, dict[int, _TodayCache]] = WeakKeyDictionary()


def memory_view(context: LayerContext, store: FootprintStore) -> MemoryView:
    """@brief 这次观察的记忆层（可查、近三天、今天，合计不超过字数上限）、本人真实活动的事实、回看标注与已发布界。"""
    book = memory_book(store, context.inputs)
    today = local_date(context.state.current_time, context.config.timezone)
    digests = day_digests(store, context.agent_id, store.last_event_seq)
    recent = recent_block(digests, today, book.categories, context.persona.work_schedule)
    earliest = earliest_date(context.config.start_time, context.config.timezone)
    published = published_today(context.inputs, store, context.agent_id, context.state.current_time,
                                store.last_event_seq)
    entries = tuple(published_entry(entry) for entry in published.entries)
    layer = assemble_layer(earliest, recent, _today(context, store, entries, earliest, recent))
    require_budget(layer)
    facts = activity_facts(agent_events(store, context.agent_id, context.catalog.kit_states()))
    labels = note_labels(published.entries, layer["今天"], context.names["activities"])
    return MemoryView(layer=layer, facts=facts, note_labels=labels, published=published.published)


def _today(context: LayerContext, store: FootprintStore, entries: Sequence[PublishedEntry],
           earliest: str, recent: dict) -> list[dict]:
    """@brief 这次观察的「今天」：从缓存的递推状态接着折入新发布的条目；日期或固定部分变了、条目变少时从头递推。"""
    date = local_date(context.state.current_time, context.config.timezone)
    fixed = render_layer_json(assemble_layer(earliest, recent, []))
    folds = _FOLDS.setdefault(store, {})
    cached = folds.get(context.agent_id)
    if cached is None or (cached.date, cached.fixed) != (date, fixed) or cached.count > len(entries):
        cached = folds[context.agent_id] = _TodayCache(date, fixed, 0, TodayFold())
    for entry in entries[cached.count:]:
        fold_entry(cached.fold, entry, earliest, recent)
    cached.count = len(entries)
    return fold_items(cached.fold)


def persona_views(context: LayerContext, store: FootprintStore) -> dict:
    """@brief 收尾写下的关系看法与偏好，作为观察私有证据（不进任何提示层，供宿主求已知人物并随观察存储与导出）：
    成功的 conclude_activity 写下的看法与偏好（经 control_items 读出的带 subject 的 relationship 与 preference 记忆，
    由现行请求的 views 派生），同一对象按事件顺序取最后一条（事件照旧只追加，两条都保留，裁决 #33）。"""
    cached = agent_events(store, context.agent_id, context.catalog.kit_states())
    relationships: dict[str, str] = {}
    preferences: dict[str, dict] = {}
    for request in successful_requests(cached, "conclude_activity"):
        noted_on = local_date(request.occurred_at, context.config.timezone)
        for item in control_items("conclude_activity", request.data["request"]):
            for memory in item.views:
                if memory["type"] == "relationship":
                    relationships[memory["subject"]] = memory["text"]
                else:
                    preferences[memory["subject"]] = {"subject": memory["subject"], "preference": memory["text"],
                                                      "noted_on": noted_on}
    return {"relationships": [{"person": person, "view": view} for person, view in relationships.items()],
            "preferences": list(preferences.values())}


def noted_label(layer: dict, activity: ActivityState) -> str | None:
    """@brief 这项活动的最新自述若以整项照写的总结或进展留在记忆层「今天」里，返回它所用的标注，否则为 None；回看据此
    不重复正文（docs/design/06-agent.md「今天条目」）。"""
    note = activity.progress_note
    texts = {text for item in layer["今天"] for text in item.get("自述", ())}
    return next((label for label in (short_name(activity.name), activity.name)
                 if f"{label}：{note}" in texts or f"{PROGRESS_PREFIX}{label}：{note}" in texts), None)
