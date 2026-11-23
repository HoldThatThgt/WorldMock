"""@brief app 内容前提的唯一判定处，以及一个 app 的内容此刻全都不合格时的拒绝文字与目标行的开放时间。

内容前提（Content.prerequisite）声明一条绑定到 app 的内容只有人在某类地点、或当地时间落在某些时段内才能开始。
判定的两个输入：停留地点是人物当前停留会话所在的地点，人在路上时没有；本地时刻是世界当前时刻按运行时区换算的结果。
合格内容的筛选、工具层的可用性说明与整组核验的拒绝都从这里取结论，所以说明与拒绝一字不差。时间部分只有一种写法
（time_phrase）：当天有时段时写当天的时段，没有时写之后七天里的下次开放；拒绝的分句与工具层内容 app 目标的 state
（app_time_state）都用它，两者对同一刻、同一批全都不合格的内容下结论。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import date, datetime, timedelta

from worldmock.resource_engine.catalog.models import Catalog, Content, Location, OpeningHours
from worldmock.simulation_engine.observation.wording.guidance import Refusal, refusal_for_code
from worldmock.foundation.time.readable import interval_phrases
from worldmock.state.world.agent import AgentState

#: 登记了内容的 app 此刻没有任何合格内容时的业务拒绝码。
PREREQUISITE_UNMET_CODE = "CONTENT_PREREQUISITE_UNMET"
#: 不满足的前提成员名：差地点、差时段；拒绝的 unmet 按这个固定次序写。
LOCATION_UNMET, TIME_UNMET = "content_location", "content_time"
#: 当天没有时段时往后找下次开放的本地日数：明天到第七天（第七天与今天同一个星期几），含第七天。
NEXT_OPENING_DAYS = 7
#: 下次开放的星期写法，按 date.weekday() 取；不用 %a，它随区域设置变。
_DAY_ABBREVIATIONS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def prerequisite_unmet(content: Content, stay: Location | None, local_moment: datetime) -> tuple[str, ...]:
    """@brief 一条内容此刻不满足的前提成员，按 content_location、content_time 的固定次序；空即满足。

    :param stay: 人物的停留地点；人在路上时为 None，既不算在出发地也不算在目的地。
    :param local_moment: 已换算为运行时区的本地时刻。
    """
    prerequisite = content.prerequisite
    if prerequisite is None:
        return ()
    unmet = []
    if prerequisite.city_ids and (stay is None or stay.city_id not in prerequisite.city_ids):
        unmet.append(LOCATION_UNMET)
    elif prerequisite.location_kinds and (stay is None or stay.kind not in prerequisite.location_kinds):
        unmet.append(LOCATION_UNMET)
    if prerequisite.opens is not None and not prerequisite.opens.is_open(local_moment):
        unmet.append(TIME_UNMET)
    return tuple(unmet)


def content_units(contents: Sequence[Content]) -> tuple[tuple[Content, ...], ...]:
    """@brief 将内容按完整单元分组：独立内容各自成单元，系列内各集按集号升序归为一单元。"""
    series: dict[str, list[Content]] = {}
    singles: list[tuple[Content, ...]] = []
    for item in sorted(contents, key=lambda row: row.content_id):
        if item.series is None:
            singles.append((item,))
        else:
            series.setdefault(item.series.title, []).append(item)
    grouped = [tuple(sorted(items, key=lambda row: (row.series.episode, row.content_id)))
               for _, items in sorted(series.items())]
    return (*singles, *grouped)


def _completed_rounds(agent: AgentState | None, item: Content) -> int:
    """@brief 读取一条内容已完成的完整轮数；未提供人物状态或尚无进度记录时为零。"""
    if agent is None:
        return 0
    progress = agent.content_progress.get(item.content_id)
    return 0 if progress is None else progress.completed_rounds


def unit_target_episode(unit: Sequence[Content], agent: AgentState | None) -> Content | None:
    """@brief 返回一个单元当前的唯一代表集；一次性单元最小完成轮数不少于一轮时已耗尽，返回 None。"""
    if not unit:
        return None
    rounds = {item.content_id: _completed_rounds(agent, item) for item in unit}
    if not unit[0].repeatable and min(rounds.values()) >= 1:
        return None
    return min(unit, key=lambda item: (rounds[item.content_id],
                                       0 if item.series is None else item.series.episode, item.content_id))


def unexhausted_units(contents: Sequence[Content],
                      agent: AgentState | None) -> tuple[tuple[tuple[Content, ...], Content], ...]:
    """@brief 在完整单元全集上剔除已耗尽的一次性单元，返回每个剩余单元及其代表集。"""
    pairs: list[tuple[tuple[Content, ...], Content]] = []
    for unit in content_units(contents):
        target = unit_target_episode(unit, agent)
        if target is not None:
            pairs.append((unit, target))
    return tuple(pairs)


def time_phrase(opens: OpeningHours, local_date: date) -> str:
    """@brief 内容开放时间的唯一写法：当天有时段时写「available <时段> today」；当天没有时在之后 NEXT_OPENING_DAYS 个
    本地日里取最早有时段的一天，写「not available today (next available <Day> <MM-DD> <时段>)」；都没有时写
    「not available today or in the next seven days」。

    每一天的时段都按 OpeningHours.opening_intervals 求，特殊日期整日替换照旧生效；下次开放写在括号里，因为分句之间以
    「; or 」相连。<Day> 取固定的 Mon 至 Sun，<MM-DD> 是补零的月和日，不写年。当天的时段都已过去时仍写当天的时段。

    :param opens: 内容前提声明的开放时段。
    :param local_date: 运行时区的本地日期。
    """
    hours = _hours(opens, local_date)
    if hours:
        return f"available {hours} today"
    for offset in range(1, NEXT_OPENING_DAYS + 1):
        day = local_date + timedelta(days=offset)
        hours = _hours(opens, day)
        if hours:
            return f"not available today (next available {_DAY_ABBREVIATIONS[day.weekday()]} {day:%m-%d} {hours})"
    return "not available today or in the next seven days"


def app_prerequisite_refusal(catalog: Catalog, app_id: str, stay: Location | None, local_moment: datetime,
                             agent: AgentState | None = None) -> Refusal | None:
    """@brief 登记了内容的 app 此刻没有任何合格内容或全已耗尽时的拒绝；没有内容或至少一条合格时为 None。

    未耗尽代表集全都不合格时与 app_time_state 取同一批结果，逐条（按内容身份升序）写分句并以「; or 」相连。
    """
    exhausted, rows = _unmet_contents(catalog, app_id, stay, local_moment, agent)
    default = refusal_for_code(PREREQUISITE_UNMET_CODE)
    if exhausted:
        return replace(default, reason=f"All content in {catalog.apps[app_id].name} has already been finished.")
    if not rows:
        return None
    clauses, places = [], []
    for item, unmet in rows:
        clause, place = _clause(catalog, item, unmet, stay, local_moment.date())
        if clause not in clauses:
            clauses.append(clause)
        if place is not None:
            places.append(place)
    here = "here " if places else ""
    reason = f"Nothing in {catalog.apps[app_id].name} can start {here}now: {'; or '.join(clauses)}."
    unmet = tuple(member for member in (LOCATION_UNMET, TIME_UNMET)
                  if any(member in failure for _, failure in rows))
    return replace(default, reason=reason, unmet=unmet,
                   next_step=f"Go to {places[0]}." if places else default.next_step)


def app_time_state(catalog: Catalog, app_id: str, stay: Location | None, local_moment: datetime,
                   agent: AgentState | None = None) -> str | None:
    """@brief 工具层内容 app 目标的 state：全部单元已耗尽时写「无未读内容」，全被阻时写各代表集开放时间。"""
    exhausted, rows = _unmet_contents(catalog, app_id, stay, local_moment, agent)
    if exhausted:
        return "无未读内容"
    phrases = []
    for item, _ in rows:
        if item.prerequisite is not None and item.prerequisite.opens is not None:
            phrase = time_phrase(item.prerequisite.opens, local_moment.date())
            if phrase not in phrases:
                phrases.append(phrase)
    return "; or ".join(phrases) or None


def _unmet_contents(catalog: Catalog, app_id: str, stay: Location | None, local_moment: datetime,
                    agent: AgentState | None = None) -> tuple[bool, list[tuple[Content, tuple[str, ...]]]]:
    """@brief 在完整单元上剔除已耗尽单元并求剩余代表集；返回是否全耗尽与全被阻时的代表集行。"""
    contents = [catalog.contents[key] for key in sorted(catalog.contents_by_app.get(app_id, ()))]
    if not contents:
        return False, []
    remaining = unexhausted_units(contents, agent)
    if not remaining:
        return True, []
    targets = sorted((target for _, target in remaining), key=lambda item: item.content_id)
    rows = [(item, prerequisite_unmet(item, stay, local_moment)) for item in targets]
    return (False, rows) if all(unmet for _, unmet in rows) else (False, [])


def _hours(opens: OpeningHours, day: date) -> str:
    """@brief 一天的开放时段文字，各时段以「, 」相连，没有时段时为空串；time_phrase 与 _clause 共用这一处写法。"""
    return ", ".join(interval_phrases(opens.opening_intervals(day)))


def _clause(catalog: Catalog, content: Content, unmet: tuple[str, ...], stay: Location | None,
            local_date: date) -> tuple[str, str | None]:
    """@brief 一条不合格内容的分句与它要去的地点；差地点时优先取当前城市匹配地点，否则列合格城市匹配地点。"""
    prerequisite = content.prerequisite
    place = None
    if LOCATION_UNMET in unmet:
        candidates = [item for _, item in sorted(catalog.locations.items())
                      if (not prerequisite.city_ids or item.city_id in prerequisite.city_ids)
                      and (not prerequisite.location_kinds or item.kind in prerequisite.location_kinds)]
        local_matches = [] if stay is None else [item for item in candidates if item.city_id == stay.city_id]
        shown = local_matches or candidates
        place = " or ".join(item.display_name for item in shown)
    if TIME_UNMET not in unmet:
        return f"it requires presence at {place}", place
    if place is None:
        return f"it is {time_phrase(prerequisite.opens, local_date)}", None
    hours = _hours(prerequisite.opens, local_date)
    if hours:
        return f"it requires presence at {place} during {hours} today", place
    return f"it requires presence at {place} and is {time_phrase(prerequisite.opens, local_date)}", place
