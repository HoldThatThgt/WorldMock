"""@brief app 内容前提的唯一判定处，以及一个 app 的内容此刻全都不合格时的拒绝文字与目标行的开放时间。

内容前提（Content.prerequisite）声明一条绑定到 app 的内容只有人在某类地点、或当地时间落在某些时段内才能开始。
判定的两个输入：停留地点是人物当前停留会话所在的地点，人在路上时没有；本地时刻是世界当前时刻按运行时区换算的结果。
合格内容的筛选、工具层的可用性说明与整组核验的拒绝都从这里取结论，所以说明与拒绝一字不差。时间部分只有一种写法
（time_phrase）：当天有时段时写当天的时段，没有时写之后七天里的下次开放；拒绝的分句与工具层内容 app 目标的 state
（app_time_state）都用它，两者对同一刻、同一批全都不合格的内容下结论。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta

from worldmock.catalog.models import Catalog, Content, Location, OpeningHours
from worldmock.life.guidance import Refusal, refusal_for_code
from worldmock.life.readable import interval_phrases

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
    if prerequisite.location_kinds and (stay is None or stay.kind not in prerequisite.location_kinds):
        unmet.append(LOCATION_UNMET)
    if prerequisite.opens is not None and not prerequisite.opens.is_open(local_moment):
        unmet.append(TIME_UNMET)
    return tuple(unmet)


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


def app_prerequisite_refusal(catalog: Catalog, app_id: str, stay: Location | None,
                             local_moment: datetime) -> Refusal | None:
    """@brief 登记了内容的 app 此刻没有任何合格内容时的拒绝；没有登记内容或至少一条合格时为 None。

    原因逐条内容（按内容身份升序）写一个分句，文字相同的只留第一次出现的，以「; or」相连；下一步指向第一个差地点的
    分句的地点，都只差时间时取该码的默认下一步。没有改法：这是世界条件造成的拒绝。
    """
    rows = _unmet_contents(catalog, app_id, stay, local_moment)
    if not rows:
        return None
    clauses, places = [], []
    for item, unmet in rows:
        clause, place = _clause(catalog, item, unmet, local_moment.date())
        if clause not in clauses:
            clauses.append(clause)
        if place is not None:
            places.append(place)
    here = "here " if places else ""
    reason = f"Nothing in {catalog.apps[app_id].name} can start {here}now: {'; or '.join(clauses)}."
    default = refusal_for_code(PREREQUISITE_UNMET_CODE)
    unmet = tuple(member for member in (LOCATION_UNMET, TIME_UNMET)
                  if any(member in failure for _, failure in rows))
    return replace(default, reason=reason, unmet=unmet,
                   next_step=f"Go to {places[0]}." if places else default.next_step)


def app_time_state(catalog: Catalog, app_id: str, stay: Location | None, local_moment: datetime) -> str | None:
    """@brief 工具层内容 app 目标的 state：这个 app 的内容此刻全都不合格时，写了 opens 的每条内容（按内容身份升序）的
    time_phrase，文字相同的只留第一次出现的，以「; or 」相连；没有登记内容、至少一条合格、或没有一条写了 opens 时为
    None。

    写的是写了 opens 的内容的开放时间，不只写此刻差时间的内容：只差地点时也写当天的时段。它不看槽位、待收尾与同刻
    限制，所以动作行的原因被别的消息占着时开放时间照样在目标上。

    :param stay: 人物的停留地点；人在路上时为 None。
    :param local_moment: 已换算为运行时区的本地时刻。
    """
    rows = _unmet_contents(catalog, app_id, stay, local_moment)
    phrases = []
    for item, _ in rows:
        if item.prerequisite is not None and item.prerequisite.opens is not None:
            phrase = time_phrase(item.prerequisite.opens, local_moment.date())
            if phrase not in phrases:
                phrases.append(phrase)
    return "; or ".join(phrases) or None


def _unmet_contents(catalog: Catalog, app_id: str, stay: Location | None,
                    local_moment: datetime) -> list[tuple[Content, tuple[str, ...]]]:
    """@brief 「全都不合格」的唯一判定：这个 app 的内容按内容身份升序，每条配上此刻不满足的前提成员；全都不合格时返回
    这些行，否则（含没有登记内容）返回空列表。拒绝的原因与目标行的 state 都从这里取同一批内容。"""
    contents = [catalog.contents[key] for key in sorted(catalog.contents_by_app.get(app_id, ()))]
    rows = [(item, prerequisite_unmet(item, stay, local_moment)) for item in contents]
    return rows if rows and all(unmet for _, unmet in rows) else []


def _hours(opens: OpeningHours, day: date) -> str:
    """@brief 一天的开放时段文字，各时段以「, 」相连，没有时段时为空串；time_phrase 与 _clause 共用这一处写法。"""
    return ", ".join(interval_phrases(opens.opening_intervals(day)))


def _clause(catalog: Catalog, content: Content, unmet: tuple[str, ...], local_date: date) -> tuple[str, str | None]:
    """@brief 一条不合格内容的分句与它要去的地点；不差地点时地点为 None。

    地点写 kind 属于该内容前提的全部登记地点的显示名，按地点身份升序以「or」相连。只差时间写「it is <时间短语>」；
    两项都差时，当天有时段写「it requires presence at <地点> during <时段> today」，当天没有时段写
    「it requires presence at <地点> and is <时间短语>」；时间短语即 time_phrase。
    """
    prerequisite = content.prerequisite
    place = None
    if LOCATION_UNMET in unmet:
        place = " or ".join(item.display_name for _, item in sorted(catalog.locations.items())
                            if item.kind in prerequisite.location_kinds)
    if TIME_UNMET not in unmet:
        return f"it requires presence at {place}", place
    if place is None:
        return f"it is {time_phrase(prerequisite.opens, local_date)}", None
    hours = _hours(prerequisite.opens, local_date)
    if hours:
        return f"it requires presence at {place} during {hours} today", place
    return f"it requires presence at {place} and is {time_phrase(prerequisite.opens, local_date)}", place
