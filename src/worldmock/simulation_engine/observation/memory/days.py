"""@brief 归日与「近三天」：事件按发生时刻的场景本地日期归日，零点整属于新的一天；一天的窗口左闭右开，零点界是本地零点
之前最后一个事件的序号；「近三天」饮食和生活取日历上的前三天，工作取最近三个有工作记录的日子。

规则见 docs/design/04-simulation-engine.md「归日」与 docs/design/06-agent.md「近三天」。记忆层、零点计划、当日拒绝与检索
都经这里归日，全仓只有这一条规则。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any, Mapping
from zoneinfo import ZoneInfo

from worldmock.configuration.inputs.environment import EnvironmentInputs
from worldmock.configuration.persona.work_schedule import DAY_REST, DAY_WORK, WEEKDAY_NAMES, shift_on
from worldmock.foundation.errors import IntegrityError
from worldmock.foundation.time.readable import local_date
from worldmock.recording_engine.storage.store import FootprintStore
from worldmock.simulation_engine.observation.memory.categories import (
    FOOD_GOODS_CATEGORIES,
    activity_category,
    memory_book,
    purchase_goods,
)
from worldmock.simulation_engine.observation.memory.digests import registered_digests
from worldmock.simulation_engine.observation.memory.entries import (
    AgentEvents,
    Decision,
    TodayEntry,
    activity_facts,
    agent_events,
    build_closing_entry,
    build_entry,
    decision_commits,
    short_name,
)
from worldmock.state.model.memory import MEMORY_CATEGORIES, MEMORY_DIGEST_KEYS, MemoryCategory
from worldmock.state.world.kinds import ActivityStatus

if TYPE_CHECKING:
    from worldmock.simulation_engine.observation.targets.tool_layer import LayerContext

_FOOD, _WORK = MEMORY_CATEGORIES[0], MEMORY_CATEGORIES[1]
#: 次数统计计入的工作、生活活动终态：自然结束或人物收尾。
_COUNTED_STATUSES = frozenset({ActivityStatus.COMPLETED, ActivityStatus.CANCELLED})


@dataclass(frozen=True, slots=True)
class DayWindow:
    """@brief 一个本地日的时刻窗口 [start, end)：start 是这一天本地零点，end 是下一个本地零点或部分窗口的截止时刻。"""

    day: str
    start: int
    end: int
    timezone: str

    def contains(self, epoch: int) -> bool:
        """@brief 时刻是否落在窗口里（左闭右开：零点整属于新的一天）。"""
        return self.start <= epoch < self.end


@dataclass(frozen=True, slots=True)
class DayDigest:
    """@brief 一天的日回顾在「近三天」里用到的部分：有正文的类别 → 正文，以及「未整理」的类别。"""

    texts: Mapping[MemoryCategory, str]
    unsettled: frozenset[MemoryCategory]

    def __post_init__(self) -> None:
        """@brief 一类不能既有正文又未整理。"""
        if set(self.texts) & self.unsettled:
            logging.error("Day digest marks a category both written and unsettled")
            raise IntegrityError("Day digest marks a category both written and unsettled")


@dataclass(frozen=True, slots=True)
class PublishedToday:
    """@brief 一次观察的今天：已发布的决策条目（按决策次序）与已发布界 P。"""

    entries: tuple[TodayEntry, ...]
    published: int


def shift_date(day: str, days: int) -> str:
    """@brief ISO 日期加减整天。"""
    return (date.fromisoformat(day) + timedelta(days=days)).isoformat()


def day_window(day: str, timezone: str) -> DayWindow:
    """@brief 一整天的窗口：从这一天的本地零点到下一个本地日的零点（夏令时日不强定为二十四小时）。"""
    return DayWindow(day, _local_midnight(day, timezone), _local_midnight(shift_date(day, 1), timezone), timezone)


def _local_midnight(day: str, timezone: str) -> int:
    """@brief 一个本地日期的零点时刻（纪元秒）。"""
    return int(datetime.combine(date.fromisoformat(day), time(0), ZoneInfo(timezone)).timestamp())


def day_bound(store: FootprintStore, day: str, timezone: str, now: int) -> int:
    """@brief 零点界 B_D：D 日本地零点之前最后一个事件的序号，没有时为 0；序号落在 (B_D, B_{D+1}] 的恰是 D 日的事件。

    全仓零点界只有这一个定义。B_D 只有时钟到达 D 日零点之后才确定，所以 now 早于那一刻时是完整性故障。
    """
    start = day_window(day, timezone).start
    if now < start:
        logging.error("Day bound is read before the day starts")
        raise IntegrityError("Day bound is read before the day starts")
    return store.last_event_seq_before(start)


def recent_dates(today: str) -> tuple[str, str, str]:
    """@brief 「近三天」里饮食与生活取的日子：today 之前日历上的三天，按日期升序。"""
    return shift_date(today, -3), shift_date(today, -2), shift_date(today, -1)


def day_digests(store: FootprintStore, agent_id: int, cutoff: int) -> dict[str, DayDigest]:
    """@brief 序号不超过 cutoff 的日回顾，按日期升序：日期 → 有正文的类别与「未整理」的类别；月、年回顾不读。"""
    rows = sorted((key[1], event) for key, event in registered_digests(store, agent_id).items()
                  if key[0] == "day" and event.event_seq <= cutoff)
    digests = {}
    for period, event in rows:
        data = event.data
        texts = {MEMORY_CATEGORIES[index]: data["digests"][key]["text"]
                 for index, key in enumerate(MEMORY_DIGEST_KEYS) if data["digests"][key] is not None}
        digests[period] = DayDigest(texts, frozenset(MEMORY_CATEGORIES[MEMORY_DIGEST_KEYS.index(key)]
                                                     for key in data["fallback"]))
    return digests


def recent_work_dates(digests: Mapping[str, DayDigest], today: str) -> tuple[str, ...]:
    """@brief 「近三天」里工作取的日子：日期早于 today、工作类有回顾或未整理的日子中最晚的三个，按日期升序。"""
    return tuple(sorted(day for day, digest in digests.items()
                        if day < today and (_WORK in digest.texts or _WORK in digest.unsettled))[-3:])


def recent_block(digests: Mapping[str, DayDigest], today: str, categories: tuple[MemoryCategory, ...],
                 schedule: Any | None) -> dict[str, list[dict]]:
    """@brief 记忆层的「近三天」：键是启用的类别（按饮食、工作、生活的次序），值是这一类按日期升序的格子；
    那天这一类没有记录时不写这一格。

    :param digests: 日期 → 已登记的日回顾（day_digests 的结果）。
    :param schedule: 人物的工作安排；工作格的「安排」按它取，没有时写「休息日」。
    """
    block = {}
    for category in (name for name in MEMORY_CATEGORIES if name in categories):
        days = recent_work_dates(digests, today) if category == _WORK else recent_dates(today)
        cells = (_cell(digests[day], day, category, schedule) for day in days if day in digests)
        block[category] = [cell for cell in cells if cell is not None]
    return block


def _cell(digest: DayDigest, day: str, category: MemoryCategory, schedule: Any | None) -> dict | None:
    """@brief 一天一类的格子：有回顾时带正文，未整理时回顾为 null 并带「未整理」，没有记录时为 None。"""
    if category not in digest.texts and category not in digest.unsettled:
        return None
    moment = date.fromisoformat(day)
    cell: dict[str, Any] = {"日期": day, "星期": WEEKDAY_NAMES[moment.weekday()]}
    if category == _WORK:
        cell["安排"] = DAY_WORK if schedule is not None and shift_on(schedule, moment) is not None else DAY_REST
    if category in digest.texts:
        cell["回顾"] = digest.texts[category]
    else:
        cell.update({"回顾": None, "未整理": True})
    return cell


def published_today(inputs: EnvironmentInputs, store: FootprintStore, agent_id: int, moment: int,
                    cutoff: int) -> PublishedToday:
    """@brief 一次观察的今天：日期为 moment 的本地日期、C 不超过 cutoff 的各次决策的条目，与已发布界
    P = max(C ≤ cutoff 的最后一次决策的 C，B_today)。

    :param moment: 观察时刻；只读检索时是冻结观察的时刻。
    :param cutoff: 构建观察时是 store.last_event_seq，只读检索时是冻结观察的事件序号。
    """
    book = memory_book(store, inputs)
    cached = agent_events(store, agent_id, inputs.catalog.kit_states())
    today = local_date(moment, inputs.config.timezone)
    bound = _bound(store, cached, today, inputs.config.timezone, moment)
    decisions = [decision for decision in decision_commits(store, cached, cutoff) if decision.commit <= cutoff]
    own = [decision for decision in decisions if local_date(decision.occurred_at, book.timezone) == today]
    entries = _decision_entries(store, cached, own, bound, inputs)
    return PublishedToday(tuple(entries), max(decisions[-1].commit if decisions else 0, bound))


def past_day_entries(inputs: EnvironmentInputs, store: FootprintStore, agent_id: int, day: str,
                     now: int) -> tuple[TodayEntry, ...]:
    """@brief 某一往日的全部条目：那天各次决策的条目按决策次序，最后接那天的零点收尾条目（有时）。

    :param now: 此刻；早于 day 的下一个本地零点时那天还没结束，是完整性故障。
    """
    zone = inputs.config.timezone
    following = shift_date(day, 1)
    if now < day_window(following, zone).start:
        logging.error("Day entries requested before the day ended")
        raise IntegrityError("Day entries requested before the day ended")
    cached = agent_events(store, agent_id, inputs.catalog.kit_states())
    bound = _bound(store, cached, day, zone, now)
    upper = _bound(store, cached, following, zone, now)
    own = [decision for decision in decision_commits(store, cached, upper)
           if local_date(decision.occurred_at, zone) == day]
    entries = _decision_entries(store, cached, own, bound, inputs)
    if day not in cached.closings:
        lower = own[-1].commit if own else bound
        cached.closings[day] = build_closing_entry(memory_book(store, inputs), cached, day, (lower, upper))
    closing = cached.closings[day]
    return tuple(entries) if closing is None else (*entries, closing)


def _bound(store: FootprintStore, cached: AgentEvents, day: str, timezone: str, now: int) -> int:
    """@brief 某日的零点界：缓存里已有时直接取，否则经 day_bound 求出并记下。"""
    if day not in cached.day_bounds:
        cached.day_bounds[day] = day_bound(store, day, timezone, now)
    return cached.day_bounds[day]


def _decision_entries(store: FootprintStore, cached: AgentEvents, decisions: list[Decision], bound: int,
                      inputs: EnvironmentInputs) -> list[TodayEntry]:
    """@brief 同一天各次决策的条目：第一条的窗口下界是零点界，此后是上一次决策的提交末序号；已建的从缓存取。"""
    entries, lower = [], bound
    for decision in decisions:
        if decision.decision_id not in cached.entries:
            build_entry(memory_book(store, inputs), store, cached, decision, lower)
        entries.append(cached.entries[decision.decision_id])
        lower = max(decision.commit, bound)
    return entries


def count_lines(context: LayerContext, first_day: str, last_day: str) -> dict[str, tuple[str, ...]]:
    """@brief 月、年回顾的次数统计：闭区间 [first_day, last_day] 内（按归日）各类的「〈名称〉 N 次」行，键是中文类别名。

    饮食类是本人收到的 meal、snack、drink 商品各计一次，以及区间内结束、用了收到的食材包的本人活动所用的食材包各计一次
    （食材包按做饭计，收货不另计）；工作类、生活类是区间内结束、终态为完成或人物收尾、判给这一类的本人活动按短名各计一次
    （失败的与运行截止截断的不计）。活动的快照取最后一份，类别按快照与开始结果推导，不读世界状态。
    """
    store, zone, catalog = context.engine.store, context.config.timezone, context.catalog
    cached = agent_events(store, context.agent_id, catalog.kit_states())
    book = memory_book(store, context.inputs)
    tallies: dict[str, dict[str, list[int]]] = {category: {} for category in MEMORY_CATEGORIES}
    for event in cached.events["environment_event"]:
        if (event.data.get("kind") == "goods_received" and event.data["category"] in FOOD_GOODS_CATEGORIES
                and first_day <= local_date(event.occurred_at, zone) <= last_day):
            _tally(tallies[_FOOD], catalog.goods[event.data["goods_id"]].name, event.event_seq)
    kits = activity_facts(cached).kits
    for activity_id, ending in cached.endings.items():
        if not first_day <= local_date(ending.occurred_at, zone) <= last_day:
            continue
        use = kits.get(activity_id)
        if use is not None and use.goods_id is not None:
            _tally(tallies[_FOOD], catalog.goods[use.goods_id].name, ending.event_seq)
        activity = cached.snapshots[activity_id][-1][1]
        if activity.status not in _COUNTED_STATUSES:
            continue
        start = cached.first_results[activity.origin_action_id]
        category = activity_category(book, activity, purchase_goods(activity, start))
        if category != _FOOD:
            _tally(tallies[category], short_name(activity.name), ending.event_seq)
    return {category: _count_rows(tally) for category, tally in tallies.items()}


def _tally(tally: dict[str, list[int]], name: str, seq: int) -> None:
    """@brief 记一次：名称 → [次数, 首次计入那条事件的序号]。"""
    counted = tally.setdefault(name, [0, seq])
    counted[0] += 1
    counted[1] = min(counted[1], seq)


def _count_rows(tally: Mapping[str, list[int]]) -> tuple[str, ...]:
    """@brief 「〈名称〉 N 次」各行（名称与数字、数字与「次」之间各一个半角空格）：按次数从多到少，次数相同按首次计入那条
    事件的序号从小到大。"""
    ordered = sorted(tally.items(), key=lambda item: (-item[1][0], item[1][1]))
    return tuple(f"{name} {count} 次" for name, (count, _) in ordered)
