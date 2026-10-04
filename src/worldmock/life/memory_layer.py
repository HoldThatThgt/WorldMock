"""@brief 记忆层与看法偏好：由已提交事件与世界状态在构建提示时派生（docs/dev/SPEC-persona-prompt.md 第一章「记忆层」、
第三章「进度与记忆的更新」，裁决 #32–#34）。

- session 区间：（前一边界提交的末事件序号, 本边界提交的末事件序号]，与进度层同一下界（session_bounds.session_lower），
  一个 session 的区间止于下一个 session 的下界。于是出发请求整体归旧 session，到达那一秒的收尾调用归新 session，路程中
  收到的消息、送达与发出的消息归路程 session；同一事件只在进度层或记忆层之一出现。
- earlier_today：结束时刻落在今天（场景本地日期）的已结束 session 各一条，recorded_at 为结束时刻；settled 取
  settled_text 的句子，在本 session 结束的活动按结束提交各一句（到达时结束的出行归路程，实验截止截断的活动归最后一段），
  self_notes 取成功的 pause_activity、conclude_activity 写下的 summary 与记忆原文（看法与偏好只在人设层；请求一律经
  control_items 读出，唯一 items 形状），按活动名标注，条目内不重名时去掉开始时刻后缀（note_labels 记下这类短名对应的
  完整活动名，回看按记忆层所见的名称解析）；没有内容的条目不出现。
- earlier_days：今天之前结束的 session 中此刻仍未了结的事项（本人 pending 或 ready 的订单；收到后没有成功 send_message
  回复发件人的直接消息，通话不算回复），按起源日期归入；另加归属该日的就寝反思（睡眠开始时刻前推 6 小时的本地日期）。
- 窗口：记忆层 JSON 不超过 8000 字符；超出时依次删去最旧日期的反思、再把最旧的今日条目缩成只剩未了结的 settled 句；
  未了结事项与最近一个 session 的条目永不删去，仍超出时写英文日志，交给整次请求的阈值处理。
- 零点不切断 session，也不需要钩子：按当前时刻的本地日期分区。
- 不新增持久状态；按 read_agent_events 的 after 增量读取，进程内缓存只作加速（按存储对象区分，恢复后的新存储首次全量读取）。
全文出现在记忆层的直接消息记为已呈现（presented_message_ids）；其中未回复的（open_message_ids）才能被 answering 关联。
"""

from __future__ import annotations

import json
import logging
from bisect import bisect_right
from dataclasses import dataclass, field
from weakref import WeakKeyDictionary

from worldmock.errors import IntegrityError
from worldmock.footprints.models import Event
from worldmock.footprints.store import FootprintStore
from worldmock.life import progress_layer
from worldmock.life.control_items import control_items
from worldmock.life.models import ActivityKind, SessionState, SessionStatus
from worldmock.life.name_resolution import START_SUFFIX
from worldmock.life.readable import iso_time, local_date
from worldmock.life.session_bounds import session_lower
from worldmock.life.settled_text import (
    SETTLED_STATUSES, ActivityFacts, activity_endings, ending_lines, give_sentence, pause_intervals,
    purchase_sentence, record_lines, tool_record, work_line,
)
from worldmock.life.tool_layer import LayerContext

#: 记忆层 JSON 的字符上限（新常量，docs/dev/SPEC-persona-prompt.md 第三章「窗口」）。
MEMORY_WINDOW_CHARS = 8000
#: 反思归属日期：睡眠开始时刻前推的秒数（裁决 #34）。
REFLECTION_SHIFT_SECONDS = 6 * 3600
#: 派生记忆层读取的本人事件类型。
_EVENT_TYPES = ("action_requested", "action_result", "environment_event", "memory_updated")


@dataclass(frozen=True, slots=True)
class MemoryView:
    """@brief 一次观察的记忆层与私有配套：layer 为记忆层 JSON，presented_message_ids 为全文在其中的直接消息，
    open_message_ids 为其中收到后尚未回复的（answering 可关联的记忆层消息），facts 为本人活动的已恢复暂停区间与结束
    事件（进度层结束活动的句子共用），note_labels 为按窗口截取后 self_notes 中去掉了开始时刻后缀的活动名 → 它在各条目中
    所指的完整活动名（两个条目的同一短名指不同活动时有多项）。"""

    layer: dict
    presented_message_ids: list[str]
    open_message_ids: list[str]
    facts: ActivityFacts
    note_labels: dict[str, tuple[str, ...]]


@dataclass(slots=True)
class _AgentEvents:
    """@brief 一个人物已读取的事件（进程内加速缓存）：读到的末事件序号、各类型事件及其序号、各活动的结束事件、已算出的
    session 下界。"""

    last_seq: int = 0
    events: dict[str, list[Event]] = field(default_factory=lambda: {name: [] for name in _EVENT_TYPES})
    seqs: dict[str, list[int]] = field(default_factory=lambda: {name: [] for name in _EVENT_TYPES})
    endings: dict[str, Event] = field(default_factory=dict)
    lowers: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _Build:
    """@brief 一次派生共用的只读输入：层上下文、本人事件、各动作的即时结果、成功回复与活动的暂停区间和结束事件。"""

    context: LayerContext
    cached: _AgentEvents
    results: dict[str, Event]
    replies: tuple[tuple[int, str], ...]
    facts: ActivityFacts


@dataclass(frozen=True, slots=True)
class _Line:
    """@brief 一条 settled 句：文字、排序键（发生时刻、事件序号）、是否未了结与全文在其中的直接消息身份。"""

    text: str
    key: tuple[int, int]
    open: bool = False
    message_id: str | None = None


@dataclass(slots=True)
class _Entry:
    """@brief 组装中的一条记忆条目：日期或结束时刻、settled 句与 self_notes；exempt 为最近一个 session 的条目，labels 为
    self_notes 中的活动标注 → 完整活动名。"""

    head: dict
    lines: list[_Line]
    notes: list[dict]
    exempt: bool = False
    labels: dict[str, str] = field(default_factory=dict)


#: 存储对象 → 人物 → 已读事件；换成恢复后的新存储对象时从头读取。
_CACHE: WeakKeyDictionary[FootprintStore, dict[int, _AgentEvents]] = WeakKeyDictionary()


def memory_view(context: LayerContext, store: FootprintStore) -> MemoryView:
    """@brief 记忆层 JSON、全文在其中的直接消息（按窗口截取后的内容）与本人活动的暂停区间。"""
    build = _build(context, store)
    zone = context.scenario.timezone
    today = local_date(context.state.current_time, zone)
    ranges = _session_ranges(context, store, build.cached)
    ended = [item for item in ranges if item[0].status == SessionStatus.ENDED]
    recent = [item for item in ended if local_date(item[0].ended_at, zone) == today]
    entries = [_today_entry(build, item) for item in recent]
    if entries:
        entries[-1].exempt = True
    earlier = len(ended) - len(recent)
    days = _day_entries(build, ranges[earlier][1] if earlier < len(ranges) else None, today)
    layer = _fit(days, entries)
    kept = [line for entry in (*days, *entries) for line in entry.lines if line.message_id is not None]
    return MemoryView(layer=layer, presented_message_ids=list(dict.fromkeys(line.message_id for line in kept)),
                      open_message_ids=list(dict.fromkeys(line.message_id for line in kept if line.open)),
                      facts=build.facts, note_labels=_note_labels(entries))


def _note_labels(entries: list[_Entry]) -> dict[str, tuple[str, ...]]:
    """@brief 按窗口截取后仍在 self_notes 中、去掉了开始时刻后缀的活动标注 → 所指的完整活动名（按条目先后去重）。"""
    labels: dict[str, list[str]] = {}
    for entry in entries:
        for note in entry.notes:
            full = entry.labels[note["activity"]]
            if full != note["activity"] and full not in labels.setdefault(note["activity"], []):
                labels[note["activity"]].append(full)
    return {label: tuple(names) for label, names in labels.items()}


def persona_views(context: LayerContext, store: FootprintStore) -> dict:
    """@brief 人设层的看法与偏好：成功的 conclude_activity 写下的看法与偏好（经 control_items 读出的带 subject 的
    relationship 与 preference 记忆，由现行请求的 views 派生），同一对象按事件顺序取最后一条（事件照旧
    只追加，两条都保留，裁决 #33）。"""
    build = _build(context, store)
    relationships: dict[str, str] = {}
    preferences: dict[str, dict] = {}
    for request in _successful(build.cached, build.results, "conclude_activity", (0, None)):
        noted_on = local_date(request.occurred_at, context.scenario.timezone)
        for item in control_items("conclude_activity", request.data["request"]):
            for memory in item.views:
                if memory["subject"] is None:
                    continue
                if memory["type"] == "relationship":
                    relationships[memory["subject"]] = memory["text"]
                else:
                    preferences[memory["subject"]] = {"subject": memory["subject"], "preference": memory["text"],
                                                      "noted_on": noted_on}
    return {"relationships": [{"person": person, "view": view} for person, view in relationships.items()],
            "preferences": list(preferences.values())}


def noted_label(layer: dict, name: str, text: str) -> str | None:
    """@brief 这段人物自述若仍在记忆层 self_notes 中（按活动名标注，标注可去掉开始时刻后缀），返回它所用的活动标注，
    否则为 None；回看据此不重复正文（docs/dev/SPEC-persona-prompt.md 第一章「唯一出现原则」）。"""
    labels = {name, START_SUFFIX.sub("", name, count=1)}
    return next((note["activity"] for entry in (*layer["earlier_days"], *layer["earlier_today"])
                 for note in entry["self_notes"] if note["activity"] in labels and note["text"] == text), None)


def reflection_date(sleep_started_at: int, timezone_name: str) -> str:
    """@brief 就寝反思的归属日期：睡眠开始时刻前推 6 小时的本地日期（23:00 与次日 01:00 入睡都归前一晚，14:00 小睡归当日）。"""
    return local_date(sleep_started_at - REFLECTION_SHIFT_SECONDS, timezone_name)


def _build(context: LayerContext, store: FootprintStore) -> _Build:
    """@brief 读取本人事件并整理一次派生共用的即时结果、成功回复与暂停区间。"""
    cached = _agent_events(store, context.agent_id)
    results: dict[str, Event] = {}
    for event in cached.events["action_result"]:
        results.setdefault(event.action_id, event)
    replies = tuple((event.event_seq, results[event.action_id].data["data"]["recipient_id"])
                    for event in _successful(cached, results, "send_message", (0, None)))
    facts = ActivityFacts(pause_intervals(cached.events["environment_event"]), cached.endings)
    return _Build(context, cached, results, replies, facts)


def _agent_events(store: FootprintStore, agent_id: int) -> _AgentEvents:
    """@brief 按 after 增量读取本人事件与活动的结束事件（activity_progress 只取结束事件，不留存）；缓存只作加速，读到的
    内容与一次全量读取相同。"""
    cached = _CACHE.setdefault(store, {}).setdefault(agent_id, _AgentEvents())
    last = store.last_event_seq
    if last > cached.last_seq:
        fresh = {name: store.read_agent_events(name, agent_id, after=cached.last_seq)
                 for name in (*_EVENT_TYPES, "activity_progress")}
        for name in _EVENT_TYPES:
            cached.events[name].extend(fresh[name])
            cached.seqs[name].extend(event.event_seq for event in fresh[name])
        endings = activity_endings([*fresh["activity_progress"], *fresh["environment_event"], *fresh["action_result"]])
        for activity_id, event in endings.items():
            cached.endings.setdefault(activity_id, event)
        cached.last_seq = last
    return cached


def _session_ranges(context: LayerContext, store: FootprintStore,
                    cached: _AgentEvents) -> list[tuple[SessionState, int, int | None]]:
    """@brief 本人全部 session 按开始顺序及其事件序号区间（下界不含、上界含；当前 session 没有上界）。"""
    ordered = sorted((item for item in context.state.sessions.values() if item.agent_id == context.agent_id),
                     key=lambda item: (item.started_at, item.session_id))
    lowers = [_lower(context, store, cached, item) for item in ordered]
    return [(item, lowers[index], lowers[index + 1] if index + 1 < len(ordered) else None)
            for index, item in enumerate(ordered)]


def _lower(context: LayerContext, store: FootprintStore, cached: _AgentEvents, session: SessionState) -> int:
    """@brief session 的事件序号下界（与进度层同一口径），计算一次后缓存（已提交的下界不再改变）。"""
    if session.session_id not in cached.lowers:
        cached.lowers[session.session_id] = session_lower(store, context.state, session)
    return cached.lowers[session.session_id]


def _slice(cached: _AgentEvents, name: str, bounds: tuple[int, int | None]) -> list[Event]:
    """@brief 某类本人事件中序号落在区间（lower, upper] 内的部分（按序号二分）。"""
    lower, upper = bounds
    seqs = cached.seqs[name]
    end = len(seqs) if upper is None else bisect_right(seqs, upper)
    return cached.events[name][bisect_right(seqs, lower):end]


def _successful(cached: _AgentEvents, results: dict[str, Event], tool: str,
                bounds: tuple[int, int | None]) -> list[Event]:
    """@brief 区间内指定工具的成功请求（即时结果 completed，不是重放）。"""
    return [event for event in _slice(cached, "action_requested", bounds)
            if event.data.get("tool_name") == tool and not event.data.get("reused")
            and event.action_id in results and results[event.action_id].data.get("status") == "completed"]


def _today_entry(build: _Build, item: tuple[SessionState, int, int | None]) -> _Entry:
    """@brief 一个今天结束的 session 的条目：全部 settled 句与 self_notes。"""
    session, lower, upper = item
    lines = _session_lines(build, (lower, upper), open_only=False)
    head = {"recorded_at": iso_time(session.ended_at, build.context.scenario.timezone)}
    notes, labels = _self_notes(build, (lower, upper))
    return _Entry(head=head, lines=lines, notes=notes, labels=labels)


def _day_entries(build: _Build, boundary: int | None, today: str) -> list[_Entry]:
    """@brief 更早日期：今天之前结束的 session（事件序号不超过 boundary）中仍未了结的事项按起源日期归并，加上归属该日
    的就寝反思。"""
    zone = build.context.scenario.timezone
    by_date: dict[str, _Entry] = {}
    for line in _session_lines(build, (0, boundary), open_only=True):
        day = local_date(line.key[0], zone)
        by_date.setdefault(day, _Entry(head={"date": day}, lines=[], notes=[])).lines.append(line)
    for day, text in _reflections(build):
        if day < today:
            by_date.setdefault(day, _Entry(head={"date": day}, lines=[], notes=[])).notes.append(
                {"activity": None, "text": text})
    return [by_date[day] for day in sorted(by_date)]


def _session_lines(build: _Build, bounds: tuple[int, int | None], open_only: bool) -> list[_Line]:
    """@brief 区间内的 settled 句（open_only 时只取未了结事项），按发生时刻与事件序号排列；结束提交在区间内的活动各一句。"""
    lower, upper = bounds

    def within(event: Event) -> bool:
        """@brief 活动的结束事件落在区间 (lower, upper] 内（活动在本 session 结束）。"""
        return event.event_seq > lower and (upper is None or event.event_seq <= upper)

    lines = [] if open_only else [_Line(text, (event.occurred_at, event.event_seq))
                                  for event, text in ending_lines(build.context, build.facts, within)]
    for request in _slice(build.cached, "action_requested", bounds):
        result = build.results.get(request.action_id)
        if result is not None and not (open_only and not (result.data.get("data") or {}).get("orders")):
            lines.extend(_request_lines(build, request, result))
    for event in _slice(build.cached, "environment_event", bounds):
        if event.data.get("kind") in {"message_arrival", "delivery_arrival"} or not open_only:
            lines.extend(_world_lines(build, event))
    return sorted((line for line in lines if line.open or not open_only), key=lambda line: line.key)


def _request_lines(build: _Build, request: Event, result: Event) -> list[_Line]:
    """@brief 一个世界请求的 settled 句；下单逐订单一句，订单仍 pending 或 ready 时标未了结。"""
    context, key = build.context, (request.occurred_at, request.event_seq)
    orders = (result.data.get("data") or {}).get("orders", [])
    if (request.data["tool_name"] == "start_activity" and orders and result.data.get("status") in SETTLED_STATUSES
            and not request.data.get("reused")):
        deliveries = context.state.deliveries
        data = result.data.get("data") or {}
        moment = tool_record(context, request)["occurred_at"]
        return [*(_Line(purchase_sentence(context, order, data.get("answering")), key,
                        open=deliveries[order["order_id"]].status
                        in progress_layer.OPEN_ORDER_STATUSES) for order in orders),
                *(_Line(give_sentence(context, moment, item), key) for item in data.get("given", []))]
    return [_Line(text, key) for text in record_lines(context, tool_record(context, request), [request, result])]


def _world_lines(build: _Build, event: Event) -> list[_Line]:
    """@brief 世界事件的 settled 句：直接消息（全文，未回复时标未了结）、邮件（主题）、送达与新工作；交谈邀请不写。"""
    context, kind, key = build.context, event.data.get("kind"), (event.occurred_at, event.event_seq)
    if kind == "work_available":
        return [_Line(work_line(context, event), key)]
    if kind == "delivery_arrival":
        deliveries = context.state.deliveries
        return [_Line(record_lines(context, item.record, [event])[0], key,
                      open=deliveries[item.orders[0]].status in progress_layer.OPEN_ORDER_STATUSES)
                for item in progress_layer._delivery_records(context, event)]
    if kind not in {"message_arrival", "mail_arrival"}:
        return []
    item = progress_layer._message_record(context, event)
    if item.record["kind"] == "chat_invite":
        return []
    text = record_lines(context, item.record, [event])[0]
    if item.message_id is None:
        return [_Line(text, key)]
    sender = context.state.agents[context.agent_id].inbox[item.message_id].sender_id
    answered = any(seq > event.event_seq and recipient == sender for seq, recipient in build.replies)
    return [_Line(text, key, open=not answered, message_id=item.message_id)]


def _self_notes(build: _Build, bounds: tuple[int, int | None]) -> tuple[list[dict], dict[str, str]]:
    """@brief 区间内成功的 pause_activity 与 conclude_activity 写下的 summary 与普通记忆原文（经 control_items 读出，
    看法与偏好不写），按活动名标注；另返回各标注 → 完整活动名。"""
    requests = sorted(_successful(build.cached, build.results, "pause_activity", bounds)
                      + _successful(build.cached, build.results, "conclude_activity", bounds),
                      key=lambda event: event.event_seq)
    notes: list[tuple[str, str]] = []
    for request in requests:
        for item in control_items(request.data["tool_name"], request.data["request"]):
            texts = [item.summary, *(memory["text"] for memory in item.plain_memories)]
            notes.extend((item.activity_name, text) for text in texts if text.strip())
    labels = _labels([task for task, _ in notes])
    return [{"activity": labels[task], "text": text} for task, text in notes], {
        label: task for task, label in labels.items()}


def _labels(names: list[str]) -> dict[str, str]:
    """@brief 条目内的活动标注：去掉开始时刻后缀后不与条目内其他活动重名时用短名，否则保留原名。"""
    short = {name: START_SUFFIX.sub("", name, count=1) for name in dict.fromkeys(names)}
    counts = list(short.values())
    return {name: value if counts.count(value) == 1 else name for name, value in short.items()}


def _reflections(build: _Build) -> list[tuple[str, str]]:
    """@brief 就寝反思及其归属日期：含 wake_from_sleep 的那一轮写入的来源为 reflection 的快照条目，由那一刻刚结束的
    睡眠的开始时刻推出日期（宿主为反思条目标注来源）。"""
    context, rows = build.context, []
    sleeps = sorted(((item.ended_at, item.started_at) for item in context.state.activities.values()
                     if item.agent_id == context.agent_id and item.kind == ActivityKind.SLEEP
                     and item.ended_at is not None))
    for event in build.cached.events["memory_updated"]:
        entries = [item for item in event.data.get("episodes", []) if item.get("source") == "reflection"]
        if not entries or "reflection" not in event.data.get("trigger", []):
            continue
        index = bisect_right(sleeps, (event.occurred_at, float("inf"))) - 1
        if index < 0:
            logging.error("Bedtime reflection has no ended sleep")
            raise IntegrityError("Bedtime reflection has no ended sleep")
        rows.extend((reflection_date(sleeps[index][1], context.scenario.timezone), item["text"]) for item in entries)
    return rows


def _fit(days: list[_Entry], today: list[_Entry]) -> dict:
    """@brief 按 8000 字符窗口截取：先删最旧日期的反思，再把最旧的今日条目缩成只剩未了结句；豁免项不动。"""
    layer = _layer(days, today)
    while _size(layer) > MEMORY_WINDOW_CHARS:
        victim = next((entry for entry in days if entry.notes), None)
        if victim is not None:
            victim.notes = []
        else:
            victim = next((entry for entry in today if not entry.exempt
                           and (entry.notes or any(not line.open for line in entry.lines))), None)
            if victim is None:
                logging.warning("Memory layer exceeds its %d-character window after trimming: %d characters",
                                MEMORY_WINDOW_CHARS, _size(layer))
                break
            victim.lines, victim.notes = [line for line in victim.lines if line.open], []
        layer = _layer(days, today)
    return layer


def _layer(days: list[_Entry], today: list[_Entry]) -> dict:
    """@brief 记忆层 JSON；没有内容的条目不出现。"""
    return {"layer": "记忆层",
            "earlier_days": [_render(entry) for entry in days if entry.lines or entry.notes],
            "earlier_today": [_render(entry) for entry in today if entry.lines or entry.notes]}


def _render(entry: _Entry) -> dict:
    """@brief 一条记忆条目：日期或结束时刻、settled 句与 self_notes。"""
    return {**entry.head, "settled": [line.text for line in entry.lines], "self_notes": list(entry.notes)}


def _size(layer: dict) -> int:
    """@brief 记忆层紧凑 JSON 的字符数（与渲染同一序列化）。"""
    return len(json.dumps(layer, ensure_ascii=False, separators=(",", ":")))
