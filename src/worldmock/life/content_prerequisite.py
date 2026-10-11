"""@brief app 内容前提的唯一判定处，以及一个 app 的内容此刻全都不合格时的拒绝文字。

内容前提（Content.prerequisite）声明一条绑定到 app 的内容只有人在某类地点、或当地时间落在某些时段内才能开始。
判定的两个输入：停留地点是人物当前停留会话所在的地点，人在路上时没有；本地时刻是世界当前时刻按运行时区换算的结果。
合格内容的筛选、工具层的可用性说明与整组核验的拒绝都从这里取结论，所以说明与拒绝一字不差。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime

from worldmock.catalog.models import Catalog, Content, Location
from worldmock.life.guidance import Refusal, refusal_for_code
from worldmock.life.readable import interval_phrases

#: 登记了内容的 app 此刻没有任何合格内容时的业务拒绝码。
PREREQUISITE_UNMET_CODE = "CONTENT_PREREQUISITE_UNMET"
#: 不满足的前提成员名：差地点、差时段；拒绝的 unmet 按这个固定次序写。
LOCATION_UNMET, TIME_UNMET = "content_location", "content_time"


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


def app_prerequisite_refusal(catalog: Catalog, app_id: str, stay: Location | None,
                             local_moment: datetime) -> Refusal | None:
    """@brief 登记了内容的 app 此刻没有任何合格内容时的拒绝；没有登记内容或至少一条合格时为 None。

    原因逐条内容（按内容身份升序）写一个分句，文字相同的只留第一次出现的，以「; or」相连；下一步指向第一个差地点的
    分句的地点，都只差时间时取该码的默认下一步。没有改法：这是世界条件造成的拒绝。
    """
    contents = [catalog.contents[key] for key in sorted(catalog.contents_by_app.get(app_id, ()))]
    failures = [prerequisite_unmet(item, stay, local_moment) for item in contents]
    if not contents or not all(failures):
        return None
    clauses, places = [], []
    for item, unmet in zip(contents, failures, strict=True):
        clause, place = _clause(catalog, item, unmet, local_moment.date())
        if clause not in clauses:
            clauses.append(clause)
        if place is not None:
            places.append(place)
    here = "here " if places else ""
    reason = f"Nothing in {catalog.apps[app_id].name} can start {here}now: {'; or '.join(clauses)}."
    default = refusal_for_code(PREREQUISITE_UNMET_CODE)
    unmet = tuple(member for member in (LOCATION_UNMET, TIME_UNMET)
                  if any(member in failure for failure in failures))
    return replace(default, reason=reason, unmet=unmet,
                   next_step=f"Go to {places[0]}." if places else default.next_step)


def _clause(catalog: Catalog, content: Content, unmet: tuple[str, ...], local_date: date) -> tuple[str, str | None]:
    """@brief 一条不合格内容的分句与它要去的地点；不差地点时地点为 None。

    地点写 kind 属于该内容前提的全部登记地点的显示名，按地点身份升序以「or」相连；时段写前提当天的时段，以「, 」相连。
    """
    prerequisite = content.prerequisite
    place = None
    if LOCATION_UNMET in unmet:
        place = " or ".join(item.display_name for _, item in sorted(catalog.locations.items())
                            if item.kind in prerequisite.location_kinds)
    if TIME_UNMET not in unmet:
        return f"it requires presence at {place}", place
    hours = ", ".join(interval_phrases(prerequisite.opens.opening_intervals(local_date)))
    if place is None:
        return (f"it is available {hours} today" if hours else "it is not available today"), None
    tail = f" during {hours} today" if hours else " and is not available today"
    return f"it requires presence at {place}{tail}", place
