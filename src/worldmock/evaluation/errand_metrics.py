"""@brief 个人事务与收尾记忆的只报告指标（docs/design/05-recording-engine.md 、3.4 第 3 项）。

质量报告的两个顶层分组 errand_metrics 与 conclusion_memories 都只读同批已认证事件（与 day_plan_metrics 同一份事件字典），
可逐值重算，全部只报告：不参与任何完成判定与门禁，也不把人物写下的文字当作已发生事实的依据。

- errand_metrics：事务身份取事务快照 data.errand.errand_id，所有人取事件的人物字段；分母是有 errand_registered 的不同
  事务（不是当日目标数，键名也不借用当日目标指标），终态是同一事务的 errand_completed 或 errand_cancelled，截止是导出
  的末一个事件。没有任何新增事件时整组为 None，有事务时各计数没有命中记 0；办成、取消与截止时未完成三者之和恒等于分母。
- conclusion_memories：非重放的 conclude_activity 请求（不论之后是否被受理：记忆在登记时写入）里经 control_items 读出的
  items[].memories（收尾记忆；views 是看法与偏好，不计）；errand_overlap_count 按文字包含同一决策观察里某条未完成事务的
  名称（待办层）或其商品名称（私有绑定）计，不做相似度。没有收尾记忆时整组为 None。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from worldmock.life.control_items import control_items
from worldmock.life.errand_state import (
    CANCELLED_KIND,
    COMPLETED_KIND,
    ERRAND_EVENT_KINDS,
    REGISTERED_KIND,
    REMINDED_KIND,
)
from worldmock.life.errands import evidence_rows
from worldmock.life.readable import local_date

#: 一条事务的两种终态事件。
_TERMINAL_KINDS = (COMPLETED_KIND, CANCELLED_KIND)
#: 没有终态的事务，其观察与行动的事件序号上界（到截止为止）。
_OPEN = float("inf")


@dataclass(slots=True)
class _Errand:
    """@brief 一条已记下的事务在导出中的经历：记下事件、终态事件（办成或取消，没有为 None）与各次提醒事件。"""

    registered: Mapping[str, Any]
    terminal: Mapping[str, Any] | None = None
    reminders: list[Mapping[str, Any]] = field(default_factory=list)

    @property
    def snapshot(self) -> Mapping[str, Any]:
        """@brief 记下时的事务快照（身份、名称、商品与记下时刻）。"""
        return self.registered["data"]["errand"]

    @property
    def owner(self) -> int:
        """@brief 事务所有人：记下事件的人物字段。"""
        return self.registered["agent_id"]

    @property
    def upper(self) -> float:
        """@brief 终态事件的事件序号；仍未完成时没有上界。"""
        return self.terminal["event_seq"] if self.terminal is not None else _OPEN


def errand_metrics(events: Sequence[Mapping[str, Any]], timezone: str) -> dict[str, int] | None:
    """@brief 质量报告的 errand_metrics 分组（docs/design/05-recording-engine.md ）。

    - registered_count：有新增事件的不同事务数，是办成、取消、截止时未完成与跨夜四项的分母；
    - completed_count、cancelled_count、open_at_cutoff_count：按终态分（三者之和恒等于分母）；
    - carried_overnight_count：记下后跨过至少一个本地零点仍未完成（终态恰在零点不算，截止恰在零点算）；
    - visible_observation_count、expected_observation_count：未完成期间（记下事件之后、终态事件之前，没有终态到截止）
      本人各次观察中待办层含该事务的次数与同期观察数，二者应相等；
    - reminded_errand_count、acted_after_reminder_count：至少被提醒过一次的事务数；其中提醒后到下一个本地零点或终态
      之前，本人有被受理（action_accepted，重放的不算）且指向同一商品的购买请求的事务数；
    - completed_without_evidence_count：办成事件缺证据引用或证据不可复核的条数（完整性计数，应恒为 0）。

    :param events: 已认证、按事件序号排列的事件字典。
    :param timezone: 场景冻结的本地时区（本地零点）。
    :returns: 十个整数计数；导出里没有任何 errand_registered 时为 None。
    """
    lives = _errands(events)
    if not lives:
        return None
    registered = len(lives)
    completed = sum(_ended(life, COMPLETED_KIND) for life in lives.values())
    cancelled = sum(_ended(life, CANCELLED_KIND) for life in lives.values())
    visible, expected = _observation_counts(events, lives)
    reminded = [life for life in lives.values() if life.reminders]
    accepted = _accepted_products(events)
    return {
        "registered_count": registered,
        "completed_count": completed,
        "cancelled_count": cancelled,
        "open_at_cutoff_count": registered - completed - cancelled,
        "carried_overnight_count": sum(_carried(life, events[-1]["occurred_at"], timezone) for life in lives.values()),
        "visible_observation_count": visible,
        "expected_observation_count": expected,
        "reminded_errand_count": len(reminded),
        "acted_after_reminder_count": sum(_acted(life, accepted.get(life.owner, []), timezone) for life in reminded),
        "completed_without_evidence_count": _unverified_completions(events),
    }


def conclusion_memories(events: Sequence[Mapping[str, Any]]) -> dict[str, int] | None:
    """@brief 质量报告的 conclusion_memories 分组（docs/design/05-recording-engine.md 、3.4 第 3 项）
    ：对自述文字本身的
    计数，只报告。

    收尾记忆是非重放的 conclude_activity 请求（不论之后是否被受理）经 control_items 读出的 items[].memories；看法与偏好
    （views）不计。routine_count 是类型为 routine 的条数；errand_overlap_count 是文字包含同一决策观察里某条未完成事务的
    名称（待办层）或其商品名称（同一观察的私有绑定）的条数，纯子串判定。

    :param events: 已认证、按事件序号排列的事件字典。
    :returns: {total_count, routine_count, errand_overlap_count}；没有收尾记忆时为 None。
    """
    observed: dict[tuple[int, str], Mapping[str, Any]] = {}
    total = routine = overlap = 0
    for event in events:
        if event["event_type"] == "observation":
            observed[(event["agent_id"], event["decision_id"])] = event["data"]
            continue
        if not _conclusion(event):
            continue
        texts = _errand_texts(observed.get((event["agent_id"], event["decision_id"]), {}))
        for item in control_items("conclude_activity", event["data"]["request"]):
            for memory in item.plain_memories:
                total += 1
                routine += memory["type"] == "routine"
                overlap += any(text in memory["text"] for text in texts)
    if not total:
        return None
    return {"total_count": total, "routine_count": routine, "errand_overlap_count": overlap}


def _errand_kind(event: Mapping[str, Any]) -> str | None:
    """@brief 事务事件（environment_event 下的六种 kind 之一）的 kind；其余事件为 None。"""
    if event["event_type"] != "environment_event":
        return None
    kind = event["data"].get("kind")
    return kind if kind in ERRAND_EVENT_KINDS else None


def _errands(events: Sequence[Mapping[str, Any]]) -> dict[str, _Errand]:
    """@brief 导出里每条已记下事务的经历，按记下的先后：终态取该事务首个办成或取消事件，提醒按事件顺序；导出里没有记下
    事件的事务（其事件不计）不在其中，更新与累计事件不影响计数。"""
    lives: dict[str, _Errand] = {}
    for event in events:
        kind = _errand_kind(event)
        if kind is None:
            continue
        errand_id = event["data"]["errand"]["errand_id"]
        if kind == REGISTERED_KIND:
            lives.setdefault(errand_id, _Errand(event))
        elif errand_id not in lives:
            continue
        elif kind == REMINDED_KIND:
            lives[errand_id].reminders.append(event)
        elif kind in _TERMINAL_KINDS and lives[errand_id].terminal is None:
            lives[errand_id].terminal = event
    return lives


def _ended(life: _Errand, kind: str) -> bool:
    """@brief 事务的终态是否为该 kind（办成或取消）。"""
    return life.terminal is not None and life.terminal["data"]["kind"] == kind


def _carried(life: _Errand, cutoff: int, timezone: str) -> bool:
    """@brief 记下后是否跨过至少一个本地零点仍未完成：记下的本地日期早于终态前一秒的本地日期（终态恰在零点时已在零点前
    结束，不算跨夜），仍未完成的早于截止时刻的本地日期（截止恰在零点时它在零点仍未完成，算跨夜）。"""
    end = life.terminal["occurred_at"] - 1 if life.terminal is not None else cutoff
    return local_date(life.snapshot["registered_at"], timezone) < local_date(end, timezone)


def _observation_counts(events: Sequence[Mapping[str, Any]], lives: Mapping[str, _Errand]) -> tuple[int, int]:
    """@brief （待办层含该事务的次数，同期观察数）：每条事务在记下事件之后、终态事件之前（没有终态到截止）本人的各次
    观察，待办层 errands 有一行名称相同即算可见。"""
    observations: dict[int, list[tuple[int, frozenset[str]]]] = defaultdict(list)
    for event in events:
        if event["event_type"] == "observation":
            rows = event["data"].get("todo_layer", {}).get("errands", [])
            observations[event["agent_id"]].append((event["event_seq"], frozenset(row["name"] for row in rows)))
    visible = expected = 0
    for life in lives.values():
        lower, upper = life.registered["event_seq"], life.upper
        for sequence, names in observations[life.owner]:
            if lower < sequence < upper:
                expected += 1
                visible += life.snapshot["name"] in names
    return visible, expected


def _accepted_products(events: Sequence[Mapping[str, Any]]) -> dict[int, list[tuple[int, int, frozenset[str]]]]:
    """@brief 各人物被受理的请求（action_accepted，重放的不算；不读之后才写的动作结果）：（事件序号，时刻，指向的商品）。"""
    accepted: dict[int, list[tuple[int, int, frozenset[str]]]] = defaultdict(list)
    for event in events:
        if event["event_type"] == "action_accepted" and not event["data"].get("reused"):
            accepted[event["agent_id"]].append((event["event_seq"], event["occurred_at"], _referred(event["data"])))
    return accepted


def _referred(data: Mapping[str, Any]) -> frozenset[str]:
    """@brief 一次受理的购买所指向的商品，直接读取其订单；在途订单也表示购买已受理。"""
    payload = data.get("data") or {}
    return frozenset(order["goods_id"] for order in payload.get("orders", []))


def _acted(life: _Errand, accepted: Sequence[tuple[int, int, frozenset[str]]], timezone: str) -> bool:
    """@brief 某次提醒之后、下一个本地零点与终态事件之前，本人有被受理且指向该事务商品的请求。受理事件排在提醒之后、时刻
    不早于提醒，所以「零点之前」即与提醒同一本地日期。"""
    product = life.snapshot["product_id"]
    for reminder in life.reminders:
        day = local_date(reminder["occurred_at"], timezone)
        if any(reminder["event_seq"] < sequence < life.upper and local_date(moment, timezone) == day
               and product in products for sequence, moment, products in accepted):
            return True
    return False


def _unverified_completions(events: Sequence[Mapping[str, Any]]) -> int:
    """@brief 办成事件缺证据引用、或引用的都不是本导出里更早的、能（经 errands.evidence_rows，与结算同一读取）给出同一
    所有人与商品的收货证据行的事件的条数。"""
    indexed = {event["event_id"]: event for event in events}
    return sum(not _evidenced(event, indexed) for event in events if _errand_kind(event) == COMPLETED_KIND)


def _evidenced(completed: Mapping[str, Any], indexed: Mapping[str, Mapping[str, Any]]) -> bool:
    """@brief 办成事件的某个原因是本导出里更早的事件，且它的收货证据行有同一所有人与商品。"""
    errand = completed["data"]["errand"]
    wanted = (completed["agent_id"], errand["product_id"])
    for cause in completed["caused_by"]:
        source = indexed.get(cause)
        if source is None or source["event_seq"] >= completed["event_seq"]:
            continue
        rows = evidence_rows(source["event_type"], source["agent_id"], source["data"])
        if any((row.owner_id, row.product_id) == wanted for row in rows):
            return True
    return False


def _conclusion(event: Mapping[str, Any]) -> bool:
    """@brief 非重放的 conclude_activity 请求事件（action_requested）。"""
    data = event["data"]
    return (event["event_type"] == "action_requested" and data.get("tool_name") == "conclude_activity"
            and not data.get("reused"))


def _errand_texts(observation: Mapping[str, Any]) -> tuple[str, ...]:
    """@brief 一次观察里未完成事务的名称（待办层 errands）与其商品名称（私有绑定 errand_bindings 的商品身份经
    errand_item_bindings 取名）；没有观察时为空。"""
    names = [row["name"] for row in observation.get("todo_layer", {}).get("errands", [])]
    items = {row["product_id"]: row["name"] for row in observation.get("errand_item_bindings", [])}
    return tuple(names + [items[row["product_id"]] for row in observation.get("errand_bindings", [])])
