"""@brief 记忆层与看法偏好：由已提交事件与世界状态在构建提示时派生（docs/dev/SPEC-persona-prompt.md 第一章「记忆层」、
第三章「进度与记忆的更新」，裁决 #32–#34）。

- session 区间：与进度层同一下界口径（由决策引起的切换取引起切换的那条结果的事件序号，运行开始与到达取开始时刻），一个
  session 的区间止于下一个 session 的下界。于是出发请求整体归旧 session，到达那一秒的收尾调用归新 session，路程中收到的
  消息、送达与发出的消息归路程 session；同一事件只在进度层或记忆层之一出现。
- earlier_today：结束时刻落在今天（场景本地日期）的已结束 session 各一条，recorded_at 为结束时刻；settled 取
  settled_text 的句子，self_notes 取成功的 pause_activity、conclude_activity 写下的 summary 与记忆原文（relationship 与
  preference 只在人设层），按活动名标注，条目内不重名时去掉开始时刻后缀；没有内容的条目不出现。
- earlier_days：今天之前结束的 session 中此刻仍未了结的事项（本人 pending 或 ready 的订单；收到后没有成功 send_message
  回复发件人的直接消息，通话不算回复），按起源日期归入；另加归属该日的就寝反思（睡眠开始时刻前推 6 小时的本地日期）。
- 窗口：记忆层 JSON 不超过 8000 字符；超出时依次删去最旧日期的反思、再把最旧的今日条目缩成只剩未了结的 settled 句；
  未了结事项与最近一个 session 的条目永不删去，仍超出时写英文日志，交给整次请求的阈值处理。
- 零点不切断 session，也不需要钩子：按当前时刻的本地日期分区。
- 不新增持久状态；按 read_agent_events 的 after 增量读取，进程内缓存只作加速（按存储对象区分，恢复后的新存储首次全量读取）。
全文出现在记忆层的直接消息记为已呈现（presented_message_ids）。
"""

from __future__ import annotations

import json
import logging
import re
from bisect import bisect_right
from dataclasses import dataclass, field
from weakref import WeakKeyDictionary

from worldmock.errors import IntegrityError
from worldmock.footprints.models import Event
from worldmock.footprints.store import FootprintStore
from worldmock.life import progress_layer
from worldmock.life.models import ActivityKind, SessionState, SessionStatus
from worldmock.life.readable import iso_time, local_date
from worldmock.life.settled_text import pause_intervals, purchase_sentence, record_lines, tool_record, work_line
from worldmock.life.tool_layer import LayerContext

#: 记忆层 JSON 的字符上限（新常量，docs/dev/SPEC-persona-prompt.md 第三章「窗口」）。
MEMORY_WINDOW_CHARS = 8000
#: 反思归属日期：睡眠开始时刻前推的秒数（裁决 #34）。
REFLECTION_SHIFT_SECONDS = 6 * 3600
#: 只在人设层出现的记忆类型（不进记忆层与进度层参数）。
VIEW_MEMORY_TYPES = frozenset({"relationship", "preference"})
#: 派生记忆层读取的本人事件类型。
_EVENT_TYPES = ("action_requested", "action_result", "environment_event", "memory_updated")
#: 成功的即时结果状态与未了结的订单状态。
_SETTLED_STATUSES = frozenset({"completed", "in_progress"})
_OPEN_ORDER_STATUSES = frozenset({"pending", "ready"})
#: 活动名中的开始时刻后缀「（M月D日 HH:MM 起）」。
_START_SUFFIX = re.compile(r"（\d{1,2}月\d{1,2}日 \d{2}:\d{2} 起）")


@dataclass(frozen=True, slots=True)
class MemoryView:
    """@brief 一次观察的记忆层与私有配套：layer 为记忆层 JSON，presented_message_ids 为全文在其中的直接消息，
    pauses 为本人活动的已恢复暂停区间（进度层结束活动的句子共用）。"""

    layer: dict
    presented_message_ids: list[str]
    pauses: dict[str, list[tuple[int, int]]]


@dataclass(slots=True)
class _AgentEvents:
    """@brief 一个人物已读取的事件（进程内加速缓存）：读到的末事件序号、各类型事件及其序号、已算出的 session 下界。"""

    last_seq: int = 0
    events: dict[str, list[Event]] = field(default_factory=lambda: {name: [] for name in _EVENT_TYPES})
    seqs: dict[str, list[int]] = field(default_factory=lambda: {name: [] for name in _EVENT_TYPES})
    lowers: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _Build:
    """@brief 一次派生共用的只读输入：层上下文、本人事件、各动作的即时结果、成功回复与暂停区间。"""

    context: LayerContext
    cached: _AgentEvents
    results: dict[str, Event]
    replies: tuple[tuple[int, str], ...]
    pauses: dict[str, list[tuple[int, int]]]


@dataclass(frozen=True, slots=True)
class _Line:
    """@brief 一条 settled 句：文字、排序键（发生时刻、事件序号）、是否未了结与全文在其中的直接消息身份。"""

    text: str
    key: tuple[int, int]
    open: bool = False
    message_id: str | None = None


@dataclass(slots=True)
class _Entry:
    """@brief 组装中的一条记忆条目：日期或结束时刻、settled 句与 self_notes；exempt 为最近一个 session 的条目。"""

    head: dict
    lines: list[_Line]
    notes: list[dict]
    exempt: bool = False


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
    kept = [line.message_id for entry in (*days, *entries) for line in entry.lines if line.message_id is not None]
    return MemoryView(layer=layer, presented_message_ids=list(dict.fromkeys(kept)), pauses=build.pauses)


def persona_views(context: LayerContext, store: FootprintStore) -> dict:
    """@brief 人设层的看法与偏好：成功的 conclude_activity 写下的带 subject 的 relationship 与 preference 记忆，
    同一对象按事件顺序取最后一条（事件照旧只追加，两条都保留，裁决 #33）。"""
    build = _build(context, store)
    relationships: dict[str, str] = {}
    preferences: dict[str, dict] = {}
    for request in _successful(build.cached, build.results, "conclude_activity", (0, None)):
        noted_on = local_date(request.occurred_at, context.scenario.timezone)
        for group in request.data["request"]["memories"]:
            for memory in group:
                if memory.get("subject") is None or memory.get("type") not in VIEW_MEMORY_TYPES:
                    continue
                if memory["type"] == "relationship":
                    relationships[memory["subject"]] = memory["text"]
                else:
                    preferences[memory["subject"]] = {"subject": memory["subject"], "preference": memory["text"],
                                                      "noted_on": noted_on}
    return {"relationships": [{"person": person, "view": view} for person, view in relationships.items()],
            "preferences": list(preferences.values())}


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
    return _Build(context, cached, results, replies, pause_intervals(cached.events["environment_event"]))


def _agent_events(store: FootprintStore, agent_id: int) -> _AgentEvents:
    """@brief 按 after 增量读取本人事件；缓存只作加速，读到的内容与一次全量读取相同。"""
    cached = _CACHE.setdefault(store, {}).setdefault(agent_id, _AgentEvents())
    last = store.last_event_seq
    if last > cached.last_seq:
        for name in _EVENT_TYPES:
            fresh = store.read_agent_events(name, agent_id, after=cached.last_seq)
            cached.events[name].extend(fresh)
            cached.seqs[name].extend(event.event_seq for event in fresh)
        cached.last_seq = last
    return cached


def _session_ranges(context: LayerContext, store: FootprintStore,
                    cached: _AgentEvents) -> list[tuple[SessionState, int, int | None]]:
    """@brief 本人全部 session 按开始顺序及其事件序号区间（下界不含、上界含；当前 session 没有上界）。"""
    ordered = sorted((item for item in context.state.sessions.values() if item.agent_id == context.agent_id),
                     key=lambda item: (item.started_at, item.session_id))
    lowers = [_lower(store, cached, item) for item in ordered]
    return [(item, lowers[index], lowers[index + 1] if index + 1 < len(ordered) else None)
            for index, item in enumerate(ordered)]


def _lower(store: FootprintStore, cached: _AgentEvents, session: SessionState) -> int:
    """@brief session 的事件序号下界（与进度层同一口径），计算一次后缓存（已提交的下界不再改变）。"""
    if session.session_id in cached.lowers:
        return cached.lowers[session.session_id]
    if session.opened_by_action_id is None:
        lower = store.last_event_seq_before(session.started_at)
    else:
        seqs = [event.event_seq for event in cached.events["action_result"]
                if event.action_id == session.opened_by_action_id]
        if not seqs:
            logging.error("Session opening action has no committed result")
            raise IntegrityError("Session opening action has no committed result")
        lower = seqs[0]
    cached.lowers[session.session_id] = lower
    return lower


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
    return _Entry(head=head, lines=lines, notes=_self_notes(build, (lower, upper)))


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
    """@brief 区间内的 settled 句（open_only 时只取未了结事项），按发生时刻与事件序号排列。"""
    lines: list[_Line] = []
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
    if (request.data["tool_name"] == "start_activity" and orders and result.data.get("status") in _SETTLED_STATUSES
            and not request.data.get("reused")):
        deliveries = context.state.deliveries
        return [_Line(purchase_sentence(context, order), key,
                      open=deliveries[order["order_id"]].status in _OPEN_ORDER_STATUSES) for order in orders]
    return [_Line(text, key) for text in record_lines(context, tool_record(context, request), [request, result],
                                                      build.pauses)]


def _world_lines(build: _Build, event: Event) -> list[_Line]:
    """@brief 世界事件的 settled 句：直接消息（全文，未回复时标未了结）、邮件（主题）、送达与新工作；交谈邀请不写。"""
    context, kind, key = build.context, event.data.get("kind"), (event.occurred_at, event.event_seq)
    if kind == "work_available":
        return [_Line(work_line(context, event), key)]
    if kind == "delivery_arrival":
        deliveries = context.state.deliveries
        return [_Line(record_lines(context, item.record, [event])[0], key,
                      open=deliveries[item.orders[0]].status in _OPEN_ORDER_STATUSES)
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


def _self_notes(build: _Build, bounds: tuple[int, int | None]) -> list[dict]:
    """@brief 区间内成功的 pause_activity 与 conclude_activity 写下的 summary 与记忆原文，按活动名标注。"""
    requests = sorted(_successful(build.cached, build.results, "pause_activity", bounds)
                      + _successful(build.cached, build.results, "conclude_activity", bounds),
                      key=lambda event: event.event_seq)
    notes: list[tuple[str, str]] = []
    for request in requests:
        body = request.data["request"]
        memories = body.get("memories", [])
        for index, task in enumerate(body["task"]):
            group = memories[index] if index < len(memories) else []
            texts = [body["summary"][index], *(item["text"] for item in group
                                               if item.get("type") not in VIEW_MEMORY_TYPES)]
            notes.extend((task, text) for text in texts if text.strip())
    labels = _labels([task for task, _ in notes])
    return [{"activity": labels[task], "text": text} for task, text in notes]


def _labels(names: list[str]) -> dict[str, str]:
    """@brief 条目内的活动标注：去掉开始时刻后缀后不与条目内其他活动重名时用短名，否则保留原名。"""
    short = {name: _START_SUFFIX.sub("", name, count=1) for name in dict.fromkeys(names)}
    counts = list(short.values())
    return {name: value if counts.count(value) == 1 else name for name, value in short.items()}


def _reflections(build: _Build) -> list[tuple[str, str]]:
    """@brief 就寝反思及其归属日期：含 wake_from_sleep 的那一轮写入的来源为 reflection 的快照条目，由那一刻刚结束的
    睡眠的开始时刻推出日期（宿主为反思条目标注来源）。"""
    context, rows = build.context, []
    for event in build.cached.events["memory_updated"]:
        entries = [item for item in event.data.get("episodes", []) if item.get("source") == "reflection"]
        if not entries or "reflection" not in event.data.get("trigger", []):
            continue
        sleeps = [item for item in context.state.activities.values()
                  if item.agent_id == context.agent_id and item.kind == ActivityKind.SLEEP
                  and item.ended_at is not None and item.ended_at <= event.occurred_at]
        if not sleeps:
            logging.error("Bedtime reflection has no ended sleep")
            raise IntegrityError("Bedtime reflection has no ended sleep")
        started = max(sleeps, key=lambda item: (item.ended_at, item.started_at)).started_at
        rows.extend((reflection_date(started, context.scenario.timezone), item["text"]) for item in entries)
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
