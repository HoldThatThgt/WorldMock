"""@brief 今天条目：每次决策一条，从已提交事件与冻结目录逐条生成（docs/design/06-agent.md「今天条目」）。

第 j 次决策的提交末序号 C_j 由 store.commit_last_seq 求出，第 k 条的窗口是 (max(C_{k-1}, B_D), C_k]；条目文字只依赖窗口
上界以内的事件与冻结目录，发布后当天不再改变（记忆层兜底的改写见 layout.py）。核验短句、短名与标注、自述前缀、120 字
以内的取舍（规则 B）、核验超过 120 字时的兜底、条目全文与按类别切分、回看标注都只在这里定义。本人事件的增量缓存也在
这里，按存储对象与人物持有，换成新的存储对象时从头读取。
"""

from __future__ import annotations

import logging
from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from weakref import WeakKeyDictionary

from worldmock.foundation.errors import IntegrityError
from worldmock.foundation.time.readable import compact_money_phrase, compact_portion_phrase, entry_clock, local_date
from worldmock.recording_engine.events.models import Event
from worldmock.recording_engine.projections.commerce.action_orders import placed_orders
from worldmock.recording_engine.projections.presentation.display_wording import TITLE_LIST_LIMIT
from worldmock.recording_engine.storage.store import FootprintStore
from worldmock.simulation_engine.activities.lifecycle import activity_control
from worldmock.simulation_engine.observation.memory.categories import (
    CategoryBook,
    activity_category,
    category_label,
    goods_category,
    primary_request,
    purchase_goods,
    rejected_category,
    request_category,
)
from worldmock.simulation_engine.observation.memory.layout import PublishedEntry
from worldmock.simulation_engine.observation.targets.object_labels import START_SUFFIX
from worldmock.simulation_engine.observation.wording.settled_text import (
    EMPTY_KIT_TRACK,
    ActivityFacts,
    KitTrack,
    activity_endings,
    app_progress,
    kit_phrase,
    pause_intervals,
    track_kits,
)
from worldmock.state.interaction.actions import START_TOOLS
from worldmock.state.interaction.control_items import CONTROL_TOOLS, control_items
from worldmock.state.model.memory import MEMORY_CATEGORIES, MemoryCategory
from worldmock.state.planning.errands import CANCELLED_KIND, COMPLETED_KIND, REGISTERED_KIND, UPDATED_KIND
from worldmock.state.world.activity import ActivityState
from worldmock.state.world.kinds import ActivityKind, ActivityStatus

#: 每条条目「核验」与「自述」各字符串 len 之和的上限。
ENTRY_CHARS = 120
#: 规则 B 第一轮每项先给的字数。
ENTRY_HEAD_CHARS = 40
#: 第一轮整项照给的长度上限。
ENTRY_WHOLE_CHARS = 50
#: 截给一项的最少字数（含「…」）。
ENTRY_MIN_CUT_CHARS = 8
#: 截断处向前退到标点时至多舍去的字数（含标点）。
ENTRY_BACKOFF_CHARS = 10
#: 标点回补的遍数上限。
ENTRY_REFILL_PASSES = 3
#: 截断处可以退到的标点。
ENTRY_BREAK_MARKS = "，。；！？、"
#: 截断与省略的记号（U+2026）。
ELLIPSIS = "…"
#: 零点收尾条目的「时刻」。
CLOSING_CLOCK = "24:00"
#: 四种自述前缀。
PROGRESS_PREFIX, VIEW_PREFIX, MEMORY_PREFIX, BODY_PREFIX = "进展·", "看法·", "记住：", "正文："
#: 核验行的种类闭集。
LINE_KINDS = ("ending", "purchase", "delivery", "pickup", "errand_done", "query", "pause", "start", "rejection",
              "errand_change", "revise", "fallback", "crossing")
#: 自述项的种类闭集与渲染次序档位（routine 只进全文）。
SELF_RANKS = {"summary": 0, "progress": 0, "view": 1, "memory": 2, "routine": 2, "body": 3}
#: 缓存读的本人事件类型。
AGENT_EVENT_TYPES = ("action_requested", "action_result", "environment_event")

#: 受理的第一条结果状态。
_ACCEPTED = frozenset({"accepted", "in_progress", "completed"})
#: 活动快照的终态。
_TERMINAL = frozenset({ActivityStatus.COMPLETED, ActivityStatus.CANCELLED, ActivityStatus.FAILED,
                       ActivityStatus.TRUNCATED})
#: 点名活动（用标注写出活动）的行种类。
_NAMING = frozenset({"ending", "pause", "start", "crossing", "fallback"})
#: 本次决策引起的三种个人事务变更事件 → 行首动词。
_ERRAND_CHANGES = {REGISTERED_KIND: "新增", UPDATED_KIND: "更新", CANCELLED_KIND: "取消"}
#: 被拒行与兜底行里控制工具的动词。
_CONTROL_VERBS = {"report_status": "播报", "pause_activity": "暂停", "conclude_activity": "收尾"}
_LIFE = MEMORY_CATEGORIES[2]


@dataclass(slots=True)
class AgentEvents:
    """@brief 一个人物已读取的事件（进程内加速缓存）与按决策缓存的今天条目。"""

    last_seq: int = 0
    events: dict[str, list[Event]] = field(default_factory=lambda: {name: [] for name in AGENT_EVENT_TYPES})
    seqs: dict[str, list[int]] = field(default_factory=lambda: {name: [] for name in AGENT_EVENT_TYPES})
    endings: dict[str, Event] = field(default_factory=dict)
    #: 当前部分观看只由本人活动的认证进度事件派生，完成后清除。
    app_progress: dict[str, str] = field(default_factory=dict)
    #: 活动身份 → [(事件序号, 快照)]，取自本人 action_result 携带的活动快照，按序号升序。
    snapshots: dict[str, list[tuple[int, ActivityState]]] = field(default_factory=dict)
    #: 动作身份 → 这条动作的第一条 action_result（受理、拒绝或中止那一刻）。
    first_results: dict[str, Event] = field(default_factory=dict)
    #: 决策身份 → C_j。
    commits: dict[str, int] = field(default_factory=dict)
    #: 决策身份 → 已建的今天条目。
    entries: dict[str, "TodayEntry"] = field(default_factory=dict)
    #: 日期 → B_D（day_bound 的返回值，03 章）。
    day_bounds: dict[str, int] = field(default_factory=dict)
    #: 日期 → 零点收尾条目（没有时为 None）。
    closings: dict[str, "TodayEntry | None"] = field(default_factory=dict)
    #: 食材包的增量推导（08 章）。
    kits: KitTrack = EMPTY_KIT_TRACK


#: 存储对象 → 人物 → 已读事件与已建条目；换成恢复后的新存储对象时从头读取。
_CACHE: WeakKeyDictionary[FootprintStore, dict[int, AgentEvents]] = WeakKeyDictionary()


@dataclass(frozen=True, slots=True)
class Decision:
    """@brief 一次决策：身份、决策时刻（首条世界请求的发生时刻）与提交末序号 C_j。"""

    decision_id: str
    occurred_at: int
    commit: int


@dataclass(frozen=True, slots=True)
class EntryLine:
    """@brief 一行核验：种类、短句、完整行、去标题行、类别与来源事件身份；查完行另带「当时查到」。"""

    kind: str
    text: str
    full: str
    bare: str
    category: str
    event_ids: tuple[str, ...]
    answers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SelfItem:
    """@brief 一项自述：种类、带前缀的全文、类别、来源事件身份与所指活动（正文没有）。"""

    kind: str
    text: str
    category: str
    event_ids: tuple[str, ...]
    activity_id: str | None


@dataclass(frozen=True, slots=True)
class TodayEntry:
    """@brief 一条今天条目或零点收尾条目：日期、决策、时刻、类别串、各行、各项、主请求、点名活动与渲染形状。"""

    day: str
    decision_id: str | None
    clock: str
    category: str
    lines: tuple[EntryLine, ...]
    items: tuple[SelfItem, ...]
    #: 主请求的工具名；零点收尾条目为 None。
    tool: str | None
    #: 受理的控制请求按项次序点名的活动身份；其余为空。
    activity_ids: tuple[str, ...]
    #: 条目内的（标注, 活动身份），按首次出现的先后。
    labels: tuple[tuple[str, str], ...]
    #: 各行（按行序）再各项（按项序）的来源事件身份依次相连、按首次出现去重（【E7】）。
    event_ids: tuple[str, ...]
    #: 渲染形状（【E8】）；零点收尾条目为 None。
    shown: dict | None


@dataclass(frozen=True, slots=True)
class _Scope:
    """@brief 一条条目的构建输入：推导输入、存储（只为被拒请求读决策观察，零点收尾条目为 None）、缓存、窗口
    (lower, upper]、条目日期与条目「时刻」。"""

    book: CategoryBook
    store: FootprintStore | None
    cached: AgentEvents
    window: tuple[int, int]
    day: str
    clock: str


@dataclass(frozen=True, slots=True)
class _Draft:
    """@brief 一行核验的草稿：种类、排序键、来源事件身份、所涉活动（首项定这一行的类别）与写法要读的数据；
    标注定下之后才写成文字。"""

    kind: str
    key: tuple
    event_ids: tuple[str, ...]
    activities: tuple[str, ...]
    data: Any = None


@dataclass(frozen=True, slots=True)
class _Note:
    """@brief 一项自述的草稿：种类、请求里的原文、看法的对象、来源事件身份与所指活动（正文没有）。"""

    kind: str
    text: str
    subject: str | None
    event_ids: tuple[str, ...]
    activity_id: str | None


@dataclass(frozen=True, slots=True)
class _Head:
    """@brief 条目的决策部分：决策身份、主请求与它的第一条结果、受理的控制请求点名的活动；零点收尾条目全为空。"""

    decision_id: str | None
    primary: Event | None
    result: Event | None
    activity_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Facts:
    """@brief 条目所涉各活动的快照（窗口上界以内最后一份）、类别与条目内的标注。"""

    snapshots: Mapping[str, ActivityState]
    categories: Mapping[str, MemoryCategory]
    labels: Mapping[str, str]


def _fail(message: str) -> IntegrityError:
    """@brief 写同文的英文错误日志，给出要抛的完整性错误。"""
    logging.error(message)
    return IntegrityError(message)


def agent_events(store: FootprintStore, agent_id: int, states: frozenset[str]) -> AgentEvents:
    """@brief 按 after 增量读取本人事件：结束事件、部分观看、食材包、各动作的第一条结果与各活动的快照随读随记，
    缓存与全量读取相同。

    :param states: 冻结目录的食材包状态（catalog.kit_states()）；同一足迹库对象上各次调用传同一份。
    """
    cached = _CACHE.setdefault(store, {}).setdefault(agent_id, AgentEvents())
    last = store.last_event_seq
    if last > cached.last_seq:
        fresh = {name: store.read_agent_events(name, agent_id, after=cached.last_seq)
                 for name in (*AGENT_EVENT_TYPES, "activity_progress")}
        for name in AGENT_EVENT_TYPES:
            cached.events[name].extend(fresh[name])
            cached.seqs[name].extend(event.event_seq for event in fresh[name])
        endings = activity_endings([*fresh["activity_progress"], *fresh["environment_event"], *fresh["action_result"]])
        for activity_id, event in endings.items():
            cached.endings.setdefault(activity_id, event)
        cached.app_progress = app_progress(fresh["activity_progress"], cached.app_progress)
        cached.kits = track_kits(fresh["environment_event"], fresh["action_result"], cached.kits, states)
        for event in fresh["action_result"]:
            cached.first_results.setdefault(event.action_id, event)
            for snapshot in _result_snapshots(event):
                cached.snapshots.setdefault(snapshot.activity_id, []).append((event.event_seq, snapshot))
        cached.last_seq = last
    return cached


def _result_snapshots(event: Event) -> list[ActivityState]:
    """@brief 一条 action_result 携带的活动快照（data.activity 与 data.data.activities 各项，去掉 content_title），
    同一活动只取一份。"""
    payload = event.data.get("data")
    rows = payload.get("activities", []) if isinstance(payload, dict) else []
    found: dict[str, ActivityState] = {}
    for row in (event.data.get("activity"), *rows):
        if isinstance(row, dict):
            fields = {key: value for key, value in row.items() if key != "content_title"}
            snapshot = ActivityState.model_validate(fields)
            found[snapshot.activity_id] = snapshot
    return list(found.values())


def successful_requests(cached: AgentEvents, tool: str) -> list[Event]:
    """@brief 本人这个工具的全部受理请求：最新一条结果是 completed 的 action_requested，按事件序号。"""
    latest = {event.action_id: event for event in cached.events["action_result"]}
    return [event for event in cached.events["action_requested"]
            if event.data.get("tool_name") == tool and event.action_id in latest
            and latest[event.action_id].data.get("status") == "completed"]


def activity_facts(cached: AgentEvents) -> ActivityFacts:
    """@brief 本人活动的事实：已恢复的暂停区间、结束事件、部分观看与所用食材包；记忆层、续接与次数统计共用。"""
    return ActivityFacts(pause_intervals(cached.events["environment_event"]), cached.endings, cached.app_progress,
                         cached.kits.uses)


def decision_commits(store: FootprintStore, cached: AgentEvents, cutoff: int) -> tuple[Decision, ...]:
    """@brief 组内最小事件序号不超过 cutoff 的各次决策，按次序；C_j 是这次决策第一条结果所在提交的末序号，算出后缓存。

    序号大于 cutoff 的 action_requested 不构成决策，也不查它的结果。
    """
    groups: dict[str, list[Event]] = {}
    for event in cached.events["action_requested"]:
        if event.event_seq > cutoff:
            break
        groups.setdefault(event.decision_id, []).append(event)
    decisions = []
    for decision_id, requests in groups.items():
        if decision_id not in cached.commits:
            results = [cached.first_results[item.action_id] for item in requests
                       if item.action_id in cached.first_results]
            if not results:
                raise _fail("Decision world request has no committed result")
            cached.commits[decision_id] = store.commit_last_seq(min(event.event_seq for event in results))
        decisions.append(Decision(decision_id, requests[0].occurred_at, cached.commits[decision_id]))
    return tuple(decisions)


def build_entry(book: CategoryBook, store: FootprintStore, cached: AgentEvents, decision: Decision,
                lower: int) -> TodayEntry:
    """@brief 窗口 (lower, decision.commit] 上的决策条目，存进 cached.entries；只读事件与冻结输入，不读世界状态。

    :param lower: 窗口下界 max(C_{k-1}, B_D)，由调用方给出（【E4】）。
    """
    day = local_date(decision.occurred_at, book.timezone)
    scope = _Scope(book, store, cached, (lower, decision.commit), day,
                   entry_clock(decision.occurred_at, day, book.timezone))
    requests = [event for event in _between(cached, "action_requested", scope.window)
                if event.decision_id == decision.decision_id]
    results = [_first_result(cached, request) for request in requests]
    accepted = _accepted(results)
    drafts = [*_settled_drafts(scope), *_own_drafts(scope, requests, results, accepted)]
    notes = _notes(requests, results, accepted)
    if not drafts and all(note.kind == "routine" for note in notes):
        drafts = [_fallback_draft(requests[0], results[0])]
    primary = primary_request(requests)
    controls = (tuple(_control_ids(requests[0], results[0]))
                if accepted and requests[0].data["tool_name"] in CONTROL_TOOLS else ())
    head = _Head(decision.decision_id, primary, results[requests.index(primary)], controls)
    entry = _assemble(scope, drafts, notes, head)
    cached.entries[decision.decision_id] = entry
    return entry


def build_closing_entry(book: CategoryBook, cached: AgentEvents, day: str,
                        window: tuple[int, int]) -> TodayEntry | None:
    """@brief D 日的零点收尾条目：窗口里的已结算事实，加零点时仍在进行的每项本人活动一句「跨过零点」；没有行时为 None。

    :param window: (max(C_last(D), B_D), B_{D+1})。
    """
    scope = _Scope(book, None, cached, window, day, CLOSING_CLOCK)
    drafts = [*_settled_drafts(scope), *_crossing_drafts(scope)]
    if not drafts:
        return None
    return _assemble(scope, drafts, [], _Head(None, None, None, ()))


def _between(cached: AgentEvents, name: str, window: tuple[int, int]) -> list[Event]:
    """@brief 某类本人事件中序号落在 (lower, upper] 内的部分（按序号二分）。"""
    lower, upper = window
    seqs = cached.seqs[name]
    return cached.events[name][bisect_right(seqs, lower):bisect_right(seqs, upper)]


def _first_result(cached: AgentEvents, request: Event) -> Event:
    """@brief 一条世界请求的第一条 action_result（F_i）；没有时这次决策没有结算。"""
    result = cached.first_results.get(request.action_id)
    if result is None:
        raise _fail("Decision world request has no committed result")
    return result


def _accepted(results: Sequence[Event]) -> bool:
    """@brief 这次决策的各条请求是否受理：中止、受理与拒绝以外的状态、受理与拒绝混杂都是完整性故障。"""
    statuses = [result.data.get("status") for result in results]
    if any(status == "failed" and result.data.get("error_code") == "DECISION_ROUND_ABORTED"
           for status, result in zip(statuses, results)):
        raise _fail("Aborted decision has no today entry")
    if any(status not in _ACCEPTED and status != "rejected" for status in statuses):
        raise _fail("Decision result status cannot form a today entry")
    verdicts = {status in _ACCEPTED for status in statuses}
    if len(verdicts) != 1:
        raise _fail("Decision results disagree")
    return verdicts.pop()


def _control_ids(request: Event, result: Event) -> list[str]:
    """@brief 受理的控制请求逐项所指的活动身份（结果 data.data.activity_ids 与请求各项同位）。"""
    items = control_items(request.data["tool_name"], request.data["request"])
    identities = list(result.data["data"]["activity_ids"])
    if len(identities) != len(items):
        raise _fail("Control result activities disagree with its items")
    return identities


def _event_ids(*events: Event) -> tuple[str, ...]:
    """@brief 几条事件的身份，按事件序号升序、去重。"""
    return tuple(dict.fromkeys(event.event_id for event in sorted(events, key=lambda event: event.event_seq)))


def _snapshot(cached: AgentEvents, activity_id: str, upper: int) -> tuple[int, ActivityState]:
    """@brief 这项活动序号不超过 upper 的最后一份快照及其所在 action_result 的序号。"""
    rows = cached.snapshots.get(activity_id, [])
    index = bisect_right(rows, upper, key=lambda row: row[0])
    if index == 0:
        raise _fail("Activity has no snapshot within the entry window")
    return rows[index - 1]


def _result_at(cached: AgentEvents, seq: int) -> Event:
    """@brief 序号为 seq 的本人 action_result。"""
    return cached.events["action_result"][bisect_left(cached.seqs["action_result"], seq)]


def _settled_drafts(scope: _Scope) -> list[_Draft]:
    """@brief 窗口里的已结算事实（活动结束、购买、送达与办成），按来源事件序号；同一事件的结束行按（开始时刻，身份），
    送达行在待取行之前。"""
    purchases = _purchase_drafts(scope)
    bought = {draft.data[0] for draft in purchases}
    drafts = [*purchases, *_ending_drafts(scope, bought), *_world_drafts(scope)]
    return sorted(drafts, key=lambda draft: draft.key)


def _purchase_drafts(scope: _Scope) -> list[_Draft]:
    """@brief 本人开始请求的 completed 结果里有已付款订单的，每个结果一行购买行，只写已付款的订单。"""
    found = []
    for event in _between(scope.cached, "action_result", scope.window):
        paid = tuple(order for order in placed_orders(event.data.get("data") or {}) if order.get("paid_at") is not None)
        if event.data.get("status") == "completed" and paid:
            found.append((event, paid))
    if not found:
        return []
    starts = {request.action_id for request in scope.cached.events["action_requested"]
              if request.data.get("tool_name") in START_TOOLS}
    return [_Draft("purchase", (event.event_seq, 0, 0, ""), (event.event_id,), (), (event.action_id, paid))
            for event, paid in found if event.action_id in starts]


def _ending_drafts(scope: _Scope, bought: set[str]) -> list[_Draft]:
    """@brief 结束事件落在窗口里的本人活动：查询做完一行查完行，有购买行的购买活动不写，其余一行结束行。"""
    lower, upper = scope.window
    drafts = []
    for activity_id, ending in scope.cached.endings.items():
        if not lower < ending.event_seq <= upper:
            continue
        seq, activity = _snapshot(scope.cached, activity_id, upper)
        if activity.status not in _TERMINAL:
            raise _fail("Ended activity has no terminal snapshot")
        if activity.kind == ActivityKind.PURCHASE and activity.origin_action_id in bought:
            continue
        done = activity.kind == ActivityKind.QUERY and activity.status == ActivityStatus.COMPLETED
        drafts.append(_Draft("query" if done else "ending", (ending.event_seq, 0, activity.started_at, activity_id),
                             _event_ids(ending, _result_at(scope.cached, seq)), (activity_id,)))
    return drafts


def _world_drafts(scope: _Scope) -> list[_Draft]:
    """@brief 窗口里人物可见的送达与办成事件各自的行。"""
    drafts = []
    for event in _between(scope.cached, "environment_event", scope.window):
        kind = event.data.get("kind")
        if kind == "delivery_arrival" and event.visibility == "agent":
            drafts.extend(_arrival_drafts(event))
        elif kind == COMPLETED_KIND and event.visibility == "agent":
            drafts.append(_Draft("errand_done", (event.event_seq, 0, 0, ""), (event.event_id,), (), event))
    return drafts


def _arrival_drafts(event: Event) -> list[_Draft]:
    """@brief 一次送达事件里本人订单的送达行与待取行；订单状态只能是 delivered 或 ready_for_pickup。"""
    orders = [order for order in event.data.get("orders", []) if order["agent_id"] == event.agent_id]
    if any(order["status"] not in {"delivered", "ready_for_pickup"} for order in orders):
        raise _fail("Delivery arrival order status is not delivered or ready_for_pickup")
    drafts = []
    for group, (kind, status) in enumerate((("delivery", "delivered"), ("pickup", "ready_for_pickup")), start=1):
        chosen = tuple(order for order in orders if order["status"] == status)
        if chosen:
            drafts.append(_Draft(kind, (event.event_seq, group, 0, ""), (event.event_id,), (), (event, chosen)))
    return drafts


def _crossing_drafts(scope: _Scope) -> list[_Draft]:
    """@brief 零点时仍在进行的本人活动各一行跨过零点行：零点前已开始、结束事件不在窗口上界以内、此刻没有暂停着。"""
    upper = scope.window[1]
    paused = _paused_at(scope.cached, upper)
    drafts = []
    for activity_id, rows in scope.cached.snapshots.items():
        ending = scope.cached.endings.get(activity_id)
        if rows[0][0] > upper or (ending is not None and ending.event_seq <= upper) or activity_id in paused:
            continue
        seq, activity = _snapshot(scope.cached, activity_id, upper)
        drafts.append(_Draft("crossing", (activity.started_at, activity_id),
                             (_result_at(scope.cached, seq).event_id,), (activity_id,)))
    return sorted(drafts, key=lambda draft: draft.key)


def _paused_at(cached: AgentEvents, upper: int) -> set[str]:
    """@brief 序号不超过 upper 时暂停着的活动：它最后一条暂停或恢复事件是 activity_paused。"""
    last: dict[str, str] = {}
    for event in _between(cached, "environment_event", (0, upper)):
        if event.data.get("kind") in {"activity_paused", "activity_resumed"}:
            last[event.data.get("activity_id")] = event.data["kind"]
    return {activity_id for activity_id, kind in last.items() if kind == "activity_paused"}


def _own_drafts(scope: _Scope, requests: Sequence[Event], results: Sequence[Event], accepted: bool) -> list[_Draft]:
    """@brief 本次决策自己的行：被拒时一行被拒行；受理时依次是暂停行、开始行、个人事务变更行与改计划行。"""
    pairs = sorted(zip(requests, results), key=lambda pair: _slot_rank(pair[0]))
    sources = _event_ids(*requests, *results)
    if not accepted:
        return [_Draft("rejection", (), sources, (), tuple(request for request, _ in pairs))]
    drafts = [_Draft("pause", (), _event_ids(request, result), (activity_id,))
              for request, result in zip(requests, results) if request.data["tool_name"] == "pause_activity"
              for activity_id in _control_ids(request, result)]
    starts = [result.activity_id for request, result in pairs if request.data["tool_name"] in START_TOOLS]
    if starts:
        if any(identity is None for identity in starts):
            raise _fail("Accepted start has no started activity")
        drafts.append(_Draft("start", (), sources, tuple(starts)))
    drafts.extend(_errand_change_drafts(scope, requests))
    if requests[0].data["tool_name"] == "revise_day_plan":
        drafts.append(_Draft("revise", (), _event_ids(requests[0], results[0]), ()))
    return drafts


def _slot_rank(request: Event) -> int:
    """@brief 开始组里 start_main_activity 那条在前；其余请求保持原次序。"""
    tool = request.data["tool_name"]
    return START_TOOLS.index(tool) if tool in START_TOOLS else 0


def _errand_change_drafts(scope: _Scope, requests: Sequence[Event]) -> list[_Draft]:
    """@brief 窗口里由本次控制请求引起的个人事务新增、更新与取消事件，按事件序号每个事件一行。"""
    causes = {request.event_id for request in requests if request.data["tool_name"] in CONTROL_TOOLS}
    if not causes:
        return []
    return [_Draft("errand_change", (event.event_seq,), (event.event_id,), (), event)
            for event in _between(scope.cached, "environment_event", scope.window)
            if event.data.get("kind") in _ERRAND_CHANGES and causes & set(event.caused_by)]


def _fallback_draft(request: Event, result: Event) -> _Draft:
    """@brief 兜底行：受理的播报或收尾各项总结都是空白、条目没有别的行和可渲染的自述时，写它点名的各项活动。"""
    return _Draft("fallback", (), _event_ids(request, result), tuple(_control_ids(request, result)),
                  request.data["tool_name"])


def _notes(requests: Sequence[Event], results: Sequence[Event], accepted: bool) -> list[_Note]:
    """@brief 本次决策的各项自述，按总结与进展、看法、记住、正文四档排列；同一档按请求里的先后。"""
    notes: list[_Note] = []
    if accepted:
        for request, result in zip(requests, results):
            if request.data["tool_name"] in CONTROL_TOOLS:
                notes.extend(_control_notes(request, result))
    body = requests[0].data.get("self_note")
    if body is not None:
        notes.append(_Note("body", body, None, (requests[0].event_id,), None))
    return sorted(notes, key=lambda note: SELF_RANKS[note.kind])


def _control_notes(request: Event, result: Event) -> list[_Note]:
    """@brief 受理的控制请求逐项写下的总结或进展、看法与普通记忆；空白文字和与总结逐字相同的记忆不产生项。"""
    tool = request.data["tool_name"]
    source = (request.event_id,)
    notes = []
    for item, activity_id in zip(control_items(tool, request.data["request"]), _control_ids(request, result)):
        if item.summary.strip():
            notes.append(_Note("progress" if tool == "report_status" else "summary", item.summary, None, source,
                               activity_id))
        notes.extend(_Note("view", memory["text"], memory["subject"], source, activity_id)
                     for memory in item.views if memory["text"].strip())
        notes.extend(_Note("routine" if memory["type"] == "routine" else "memory", memory["text"], None, source,
                           activity_id) for memory in item.plain_memories
                     if memory["text"].strip() and memory["text"] != item.summary)
    return notes


def _assemble(scope: _Scope, drafts: Sequence[_Draft], notes: Sequence[_Note], head: _Head) -> TodayEntry:
    """@brief 由行与项的草稿写出条目：读快照与类别、定标注、写各行各项、类别串与渲染形状。"""
    named = [identity for draft in drafts if draft.kind in _NAMING for identity in draft.activities]
    named += [note.activity_id for note in notes if note.kind in {"summary", "progress"}]
    involved = [*(identity for draft in drafts for identity in draft.activities),
                *(note.activity_id for note in notes if note.activity_id is not None), *head.activity_ids]
    facts = _facts(scope, involved, named)
    main = None if head.primary is None else request_category(scope.book, scope.store, head.primary, head.result,
                                                              facts.categories)
    lines = tuple(_line(scope, facts, draft) for draft in drafts)
    items = tuple(_item(facts, note, main) for note in notes)
    category = category_label(main, [*(line.category for line in lines), *(item.category for item in items)])
    shown = None if head.decision_id is None else _shown(scope.clock, category, lines, items)
    return TodayEntry(scope.day, head.decision_id, scope.clock, category, lines, items,
                      None if head.primary is None else head.primary.data["tool_name"], head.activity_ids,
                      tuple((facts.labels[identity], identity) for identity in dict.fromkeys(named)),
                      tuple(dict.fromkeys(event_id for part in (*lines, *items) for event_id in part.event_ids)), shown)


def _facts(scope: _Scope, involved: Sequence[str], named: Sequence[str]) -> _Facts:
    """@brief 条目所涉各活动的快照与类别，以及点名活动的标注（短名重复时两项都用冻结全名）。"""
    snapshots = {identity: _snapshot(scope.cached, identity, scope.window[1])[1]
                 for identity in dict.fromkeys(involved)}
    categories = {identity: _activity_category(scope, snapshot) for identity, snapshot in snapshots.items()}
    shorts = {identity: short_name(snapshots[identity].name) for identity in dict.fromkeys(named)}
    counts = Counter(shorts.values())
    labels = {identity: short if counts[short] == 1 else snapshots[identity].name for identity, short in shorts.items()}
    return _Facts(snapshots, categories, labels)


def _activity_category(scope: _Scope, activity: ActivityState) -> MemoryCategory:
    """@brief 一项活动的类别：按快照与这项活动开始请求的第一条结果推导。"""
    start = scope.cached.first_results.get(activity.origin_action_id)
    if start is None:
        raise _fail("Activity has no start result")
    return activity_category(scope.book, activity, purchase_goods(activity, start))


def _shown(clock: str, category: str, lines: Sequence[EntryLine], items: Sequence[SelfItem]) -> dict:
    """@brief 渲染形状：核验经核验兜底，自述只取非 routine 的项经规则 B；空列表的键省略。"""
    verified = fit_verified(lines)
    notes = fit_items(sum(len(text) for text in verified), [item.text for item in items if item.kind != "routine"])
    return {"时刻": clock, "类别": category, **({"核验": verified} if verified else {}),
            **({"自述": notes} if notes else {})}


def _item(facts: _Facts, note: _Note, main: MemoryCategory | None) -> SelfItem:
    """@brief 一项自述的带前缀全文与类别：正文取主请求的类别，其余取所指活动的类别。"""
    if note.kind == "summary":
        text = f"{facts.labels[note.activity_id]}：{note.text}"
    elif note.kind == "progress":
        text = f"{PROGRESS_PREFIX}{facts.labels[note.activity_id]}：{note.text}"
    elif note.kind == "view":
        text = f"{VIEW_PREFIX}{note.subject}：{note.text}"
    elif note.kind == "body":
        text = f"{BODY_PREFIX}{note.text}"
    else:
        text = f"{MEMORY_PREFIX}{note.text}"
    category = main if note.activity_id is None else facts.categories[note.activity_id]
    return SelfItem(note.kind, text, category, note.event_ids, note.activity_id)


def _line(scope: _Scope, facts: _Facts, draft: _Draft) -> EntryLine:
    """@brief 一行核验的三种文字、类别与来源事件身份。"""
    text, full, bare, answers = _WRITERS[draft.kind](scope, facts, draft)
    return EntryLine(draft.kind, text, full, bare, _line_category(scope, facts, draft), draft.event_ids, answers)


def _line_category(scope: _Scope, facts: _Facts, draft: _Draft) -> MemoryCategory:
    """@brief 一行核验的类别（docs/design/06-agent.md「记忆的类别」的核验行表）。"""
    if draft.activities:
        return facts.categories[draft.activities[0]]
    if draft.kind in {"purchase", "delivery", "pickup"}:
        return goods_category(scope.book, [order["goods_id"] for order in draft.data[1]])
    if draft.kind in {"errand_done", "errand_change"}:
        return goods_category(scope.book, [draft.data.data["errand"]["product_id"]])
    if draft.kind == "rejection":
        return rejected_category(scope.book, scope.store, draft.data[0])
    return _LIFE


def _same(text: str) -> tuple[str, str, str, tuple[str, ...]]:
    """@brief 三种文字相同、没有「当时查到」的一行。"""
    return text, text, text, ()


def _prefix(scope: _Scope, moment: int) -> str:
    """@brief 句首时刻前缀：时刻按条目日期写出后与条目「时刻」不同时写它加一个空格，相同时为空。"""
    clock = entry_clock(moment, scope.day, scope.book.timezone)
    return "" if clock == scope.clock else f"{clock} "


def _ending_line(scope: _Scope, facts: _Facts, draft: _Draft) -> tuple[str, str, str, tuple[str, ...]]:
    """@brief 结束行：标注、起止（含已恢复的暂停）、终局、标题段与食材段；完整行列全标题，去标题行没有标题段。"""
    activity = facts.snapshots[draft.activities[0]]
    zone, day = scope.book.timezone, scope.day
    label = facts.labels[activity.activity_id]
    if activity.kind == ActivityKind.TRAVEL and activity.status == ActivityStatus.COMPLETED:
        label = label.replace("前往", "到", 1)
    pauses = pause_intervals(scope.cached.events["environment_event"]).get(activity.activity_id, ())
    paused = "、".join(f"{entry_clock(begin, day, zone)}–{entry_clock(until, day, zone)}" for begin, until in pauses)
    head = (f"{label} {entry_clock(activity.started_at, day, zone)}–{entry_clock(activity.ended_at, day, zone)}"
            f"{f'（暂停 {paused}）' if paused else ''}{_outcome(activity)}")
    kit = scope.cached.kits.uses.get(activity.activity_id)
    tail = "" if kit is None else "，" + kit_phrase(kit, scope.book.catalog, zone, True)
    short, full = _title_segments(scope, activity)
    return (head + (f"，{short}" if short else "") + tail, head + (f"，{full}" if full else "") + tail,
            head + tail, ())


def _outcome(activity: ActivityState) -> str:
    """@brief 终局段：到达、按计划结束与结束使用为空，提前结束、失败与睡过头各有写法。"""
    phrase = activity_control.ending_phrase(activity)
    if phrase in {"提前结束", "失败"}:
        return f"，{phrase}"
    if phrase.startswith("比打算的多睡了"):
        return f"，多睡{activity.data['oversleep_seconds'] // 60}分钟"
    return ""


def _title_segments(scope: _Scope, activity: ActivityState) -> tuple[str, str]:
    """@brief interact 结束行的标题段（短句与完整行两种，都不带前面的「，」）；其余种类为空。"""
    if activity.kind != ActivityKind.INTERACT:
        return "", ""
    contents = scope.book.catalog.contents
    titles = [contents[row["content_id"]].title for row in activity.data["played"]]
    listed = "".join(f"《{title}》" for title in titles)
    if activity.data["app_id"] is None:
        if len(titles) == 1 and titles[0] == short_name(activity.name):
            return "", ""
        more = f"等{len(titles)}条" if len(titles) > TITLE_LIST_LIMIT else ""
        return "".join(f"《{title}》" for title in titles[:TITLE_LIST_LIMIT]) + more, listed
    short = f"《{titles[0]}》{f'等{len(titles)}条' if len(titles) > 1 else ''}" if titles else ""
    current = activity.data["content_id"]
    partial = scope.cached.app_progress.get(activity.activity_id) == current
    return short, listed + (f"（《{contents[current].title}》未完）" if partial else "")


def _purchase_line(scope: _Scope, facts: _Facts, draft: _Draft) -> tuple[str, str, str, tuple[str, ...]]:
    """@brief 购买行：时刻前缀、商品与份数、（共）金额与预计送达；只写这一行的已付款订单，不写渠道。"""
    del facts
    orders = draft.data[1]
    names = [scope.book.catalog.goods[order["goods_id"]].name for order in orders]
    if len(orders) == 1:
        goods = names[0] + compact_portion_phrase(orders[0]["quantity"])
    elif all(order["quantity"] == 1 for order in orders):
        goods = "、".join(names) + "各1份"
    else:
        goods = "、".join(name + compact_portion_phrase(order["quantity"]) for name, order in zip(names, orders))
    total = compact_money_phrase(sum(order["total_price_fen"] for order in orders))
    text = (f"{_prefix(scope, orders[0]['paid_at'])}买{goods}，{'共' if len(orders) > 1 else ''}{total}"
            f"{_delivery_segment(scope, orders)}")
    return _same(text)


def _delivery_segment(scope: _Scope, orders: Sequence[dict]) -> str:
    """@brief 送达段：外卖与快递订单的预计时刻（去重、按时刻升序）与目的地（按订单次序去重），全是到店时为空。"""
    shipped = [order for order in orders if order["fulfillment"] in {"delivery", "station_pickup"}]
    if not shipped:
        return ""
    zone = scope.book.timezone
    times = dict.fromkeys(entry_clock(moment, scope.day, zone) for moment in sorted(o["expected_at"] for o in shipped))
    places = dict.fromkeys(order["destination_id"] for order in shipped)
    names = ["家" if place == scope.book.home_id else scope.book.catalog.locations[place].display_name
             for place in places]
    return f"，预计{'、'.join(times)}送到{'、'.join(names)}"


def _arrival_line(scope: _Scope, facts: _Facts, draft: _Draft) -> tuple[str, str, str, tuple[str, ...]]:
    """@brief 送达行「送达A、B」与待取行「A已到取货点待取」：商品名按事件里的次序去重，时刻不同时带前缀。"""
    del facts
    event, orders = draft.data
    names = "、".join(dict.fromkeys(scope.book.catalog.goods[order["goods_id"]].name for order in orders))
    prefix = _prefix(scope, event.occurred_at)
    return _same(f"{prefix}送达{names}" if draft.kind == "delivery" else f"{prefix}{names}已到取货点待取")


def _query_line(scope: _Scope, facts: _Facts, draft: _Draft) -> tuple[str, str, str, tuple[str, ...]]:
    """@brief 查完行「查完〈app〉·〈范围〉：N项可买（另M项未列），P–Q元」与它的「当时查到」。"""
    activity = facts.snapshots[draft.activities[0]]
    answer, app_id = activity.data["answer"], activity.data["app_id"]
    zone, catalog = scope.book.timezone, scope.book.catalog
    target = short_name(activity.name).removeprefix("查询")
    app = None if app_id is None else catalog.apps[app_id].name
    available = [row for row in answer["rows"] if row["available"]]
    count = len({row["goods_id"] for row in available})
    omitted = answer["omitted"]
    prices = [row["price_fen"] for row in available]
    price = (f"，{compact_money_phrase(min(prices)).removesuffix('元')}–"
             f"{compact_money_phrase(max(prices)).removesuffix('元')}元") if count else ""
    text = (f"查完{target if app is None else f'{app}·{target}'}：{count}项可买"
            f"{f'（另{omitted}项未列）' if omitted > 0 else ''}{price}")
    clock = entry_clock(activity.ended_at, scope.day, zone)
    answers = [f"{clock} 查{target}" if app is None else f"{clock} 在{app}查{target}"]
    answers += [f"{catalog.goods[row['goods_id']].name} {compact_money_phrase(row['price_fen'])}"
                f"{'' if row['available'] else '，当时不可买'}" for row in answer["rows"]]
    answers += [f"另{omitted}项未列"] if omitted > 0 else []
    return text, text, text, tuple(answers)


def _errand_done_line(scope: _Scope, facts: _Facts, draft: _Draft) -> tuple[str, str, str, tuple[str, ...]]:
    """@brief 办成行「办成「事务名」」。"""
    del scope, facts
    return _same(f"办成「{draft.data.data['errand']['name']}」")


def _errand_change_line(scope: _Scope, facts: _Facts, draft: _Draft) -> tuple[str, str, str, tuple[str, ...]]:
    """@brief 个人事务变更行「新增、更新或取消个人事务「事务名」」。"""
    del scope, facts
    return _same(f"{_ERRAND_CHANGES[draft.data.data['kind']]}个人事务「{draft.data.data['errand']['name']}」")


def _pause_line(scope: _Scope, facts: _Facts, draft: _Draft) -> tuple[str, str, str, tuple[str, ...]]:
    """@brief 暂停行「暂停〈标注〉」。"""
    del scope
    return _same(f"暂停{facts.labels[draft.activities[0]]}")


def _start_line(scope: _Scope, facts: _Facts, draft: _Draft) -> tuple[str, str, str, tuple[str, ...]]:
    """@brief 开始行「开始A、B」：各条开始请求开始的活动的标注，start_main_activity 那条在前。"""
    del scope
    return _same("开始" + "、".join(facts.labels[identity] for identity in draft.activities))


def _rejection_line(scope: _Scope, facts: _Facts, draft: _Draft) -> tuple[str, str, str, tuple[str, ...]]:
    """@brief 被拒行「被拒：〈动作对象〉」：各条请求的动作与所写对象，start_main_activity 那条在前，不写原因。"""
    del scope, facts
    return _same("被拒：" + "、".join(_action_object(request) for request in draft.data))


def _action_object(request: Event) -> str:
    """@brief 被拒请求的动作与对象，名称照请求原文；控制请求所写的活动名取短名。"""
    tool, arguments = request.data["tool_name"], request.data["request"]
    if tool == START_TOOLS[0]:
        action = arguments["action"]
        return "睡觉" if action == "sleep" else (f"前往{arguments['target']}" if action == "travel"
                                                 else f"开始{arguments['detail']}")
    if tool == START_TOOLS[1]:
        if arguments["action"] == "interact":
            return f"开始{arguments['target'][0]}"
        return ("购买" if arguments["action"] == "purchase" else "查询") + "、".join(arguments["target"])
    if tool in CONTROL_TOOLS:
        names = (short_name(item.activity_name) for item in control_items(tool, arguments))
        return _CONTROL_VERBS[tool] + "、".join(names)
    return "改今日计划"


def _revise_line(scope: _Scope, facts: _Facts, draft: _Draft) -> tuple[str, str, str, tuple[str, ...]]:
    """@brief 改计划行「改了今日计划」。"""
    del scope, facts, draft
    return _same("改了今日计划")


def _fallback_line(scope: _Scope, facts: _Facts, draft: _Draft) -> tuple[str, str, str, tuple[str, ...]]:
    """@brief 兜底行「播报A、B」或「收尾A、B」。"""
    del scope
    return _same(_CONTROL_VERBS[draft.data] + "、".join(facts.labels[identity] for identity in draft.activities))


def _crossing_line(scope: _Scope, facts: _Facts, draft: _Draft) -> tuple[str, str, str, tuple[str, ...]]:
    """@brief 跨过零点行「〈起〉 开始〈标注〉，跨过零点」，只在零点收尾条目里。"""
    activity = facts.snapshots[draft.activities[0]]
    start = entry_clock(activity.started_at, scope.day, scope.book.timezone)
    return _same(f"{start} 开始{facts.labels[activity.activity_id]}，跨过零点")


#: 各种核验行的写法，按 LINE_KINDS 的次序逐一对应。
_WRITERS: dict[str, Callable[[_Scope, _Facts, _Draft], tuple[str, str, str, tuple[str, ...]]]] = dict(zip(LINE_KINDS, (
    _ending_line, _purchase_line, _arrival_line, _arrival_line, _errand_done_line, _query_line, _pause_line,
    _start_line, _rejection_line, _errand_change_line, _revise_line, _fallback_line, _crossing_line), strict=True))


def fit_verified(lines: Sequence[EntryLine]) -> list[str]:
    """@brief 核验本身超过 120 字时的兜底：先换成去标题行，再整行保留并以「另N项…」计其余，第一行本身放不下时截它。"""
    texts = [line.text for line in lines]
    if sum(len(text) for text in texts) <= ENTRY_CHARS:
        return texts
    bare = [line.bare for line in lines]
    if sum(len(text) for text in bare) <= ENTRY_CHARS:
        return bare
    count = len(bare)
    for kept in range(count - 1, 0, -1):
        tail = f"另{count - kept}项{ELLIPSIS}"
        if sum(len(text) for text in bare[:kept]) + len(tail) <= ENTRY_CHARS:
            return [*bare[:kept], tail]
    if count == 1:
        return [bare[0][:ENTRY_CHARS - 1] + ELLIPSIS]
    tail = f"另{count - 1}项{ELLIPSIS}"
    return [bare[0][:ENTRY_CHARS - len(tail) - 1] + ELLIPSIS, tail]


def fit_items(verified_chars: int, items: Sequence[str]) -> list[str]:
    """@brief 120 字以内的取舍（规则 B）：先给每项开头，首轮每项 40 字、50 字以内整项给；余量回补，标点回补；
    截断处以「…」结尾，有项省略时末尾补「…」；超出时从后往前缩短，核验从不为补「…」缩短。

    :param verified_chars: 核验已占的字数（k ≤ 120）。
    :param items: 按档位排好的可渲染自述项。
    """
    given, room = [0] * len(items), ENTRY_CHARS - verified_chars
    for index, item in enumerate(items):
        head = len(item) if len(item) <= ENTRY_WHOLE_CHARS else ENTRY_HEAD_CHARS
        if head <= room:
            given[index], room = head, room - head
            continue
        if room >= ENTRY_MIN_CUT_CHARS:
            given[index], room = room, 0
        break
    for index, item in enumerate(items):
        if 0 < given[index] < len(item) and room > 0:
            extra = min(room, len(item) - given[index])
            given[index], room = given[index] + extra, room - extra
    for _ in range(ENTRY_REFILL_PASSES):
        spare = ENTRY_CHARS - verified_chars - sum(len(text) for text in _displayed(items, given))
        cut = next((index for index, item in enumerate(items) if 0 < given[index] < len(item)), None)
        if spare < 2 or cut is None:
            break
        given[cut] = min(len(items[cut]), len(_cut(items[cut], given[cut])) + spare)
    shown = _displayed(items, given)
    while verified_chars + sum(len(text) for text in shown) > ENTRY_CHARS:
        if not any(given):
            return []
        last = max(index for index, value in enumerate(given) if value)
        over = verified_chars + sum(len(text) for text in shown) - ENTRY_CHARS
        given[last] = given[last] - over if given[last] - over >= 2 else 0
        shown = _displayed(items, given)
    return shown


def _cut(text: str, given: int) -> str:
    """@brief 把一项截成给定长度：保留前 given−1 字，最后一个可截标点在后一半且至多舍去 10 字时退到它之前，末尾接「…」。"""
    if given >= len(text):
        return text
    body = text[:given - 1]
    mark = max(body.rfind(char) for char in ENTRY_BREAK_MARKS)
    if mark >= 0 and mark >= len(body) // 2 and len(body) - mark <= ENTRY_BACKOFF_CHARS:
        body = body[:mark]
    return body + ELLIPSIS


def _displayed(items: Sequence[str], given: Sequence[int]) -> list[str]:
    """@brief 按给定长度写出的显示列表；有项省略而显示列表为空或末项不以「…」结尾时末尾补一项「…」。"""
    shown = [_cut(item, value) for item, value in zip(items, given) if value > 0]
    if any(value == 0 for value in given) and (not shown or not shown[-1].endswith(ELLIPSIS)):
        shown.append(ELLIPSIS)
    return shown


def short_name(name: str) -> str:
    """@brief 活动的短名：冻结名称去掉第一个开始时刻后缀，再去掉开头的一个「进行」；名称为空是完整性故障。"""
    if not name:
        raise _fail("Activity has no frozen name")
    return START_SUFFIX.sub("", name, count=1).removeprefix("进行")


def entry_full(entry: TodayEntry, category: str | None = None) -> dict | None:
    """@brief 条目全文（完整行、当时查到与全部自述，不截断）；给出类别时是这一类的部分，这一类没有行和项时为 None。"""
    lines = [line for line in entry.lines if category is None or line.category == category]
    items = [item for item in entry.items if category is None or item.category == category]
    if category is not None and not lines and not items:
        return None
    full: dict[str, Any] = {"时刻": entry.clock, "类别": entry.category if category is None else category}
    parts = (("核验", [line.full for line in lines]), ("当时查到", [text for line in lines for text in line.answers]),
             ("自述", [item.text for item in items]))
    full.update((key, values) for key, values in parts if values)
    return full


def published_entry(entry: TodayEntry) -> PublishedEntry:
    """@brief 记忆层组装用到的部分：渲染形状、是否只交了受理的 report_status、点名与收尾的活动身份。"""
    report_only = entry.tool == "report_status" and bool(entry.activity_ids)
    return PublishedEntry(entry.shown, report_only, frozenset(entry.activity_ids) if report_only else frozenset(),
                          frozenset(entry.activity_ids) if entry.tool == "conclude_activity" else frozenset())


def note_labels(entries: Sequence[TodayEntry], today: Sequence[dict],
                names: Mapping[str, str]) -> dict[str, tuple[str, ...]]:
    """@brief 回看标注：渲染出的「今天」里仍以「标注：」或「进展·标注：」开头的自述所写的标注 → 这些活动此刻的可见名称。

    :param today: 这次观察渲染出的「今天」列表（兜底改写之后）。
    :param names: 本次观察的活动可见名称 context.names["activities"]。
    """
    texts = [text for item in today for text in item.get("自述", ())]
    labels: dict[str, list[str]] = {}
    for entry in entries:
        for label, activity_id in entry.labels:
            visible = names[activity_id]
            if visible == label or not any(
                    text.startswith((f"{label}：", f"{PROGRESS_PREFIX}{label}：")) for text in texts):
                continue
            if visible not in labels.setdefault(label, []):
                labels[label].append(visible)
    return {label: tuple(visible) for label, visible in labels.items()}
