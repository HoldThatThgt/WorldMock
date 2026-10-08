"""@brief 个人事务的全部逻辑（docs/design/04-simulation-engine.md）：私有绑定、待办层行、变更的校验与应用、请求驱动的
三种事务事件、完成依据与结算。

全部是纯函数：不读写存储、不访问引擎。状态模型与常量在 errand_state.py（状态与逻辑分两个模块）；本模块可依赖
models、名称解析与错误合同，models.py 从不导入本模块。

- 私有绑定（2.3）：`errand_bindings` 列本人未完成事务的名称、事务身份、商品身份（待办层顺序），
  `errand_item_bindings` 列全部商品名到商品身份（按商品身份排序）；两者与 `errand_id` 都是提示禁键，事务名不进回看名称。
- 待办层行（2.6）：按记下的时刻与事务身份排序，每行六键 name、done_when、progress、memo、remind_time、noted_on；
  不含内部身份、状态枚举与可用性。零点计划输入的只读键「个人事务」（2.8）由同一行构造给出五键（plan_rows）。
- 变更（2.2、2.3）：宿主与环境对同一份冻结观察调用 change_issues，判定一致；同一请求按「取消、更新、新增」的次序求值，
  重名、同商品与上限都按扣除本请求取消之后的未完成集合判定。校验与应用共用同一个私有解析（_plan），更新与取消
  按待办层名称解析（_open_name，与活动名同一函数，唯一才受理、从不替换）。apply_changes 只在校验通过后改人物状态并给出
  回执；request_drafts 为每条被受理的变更写一条事务事件，决策身份与动作身份都为空，只经 caused_by 关联请求事件（2.7）。
- 提醒钟点（2.5）：remind_epoch 把 HH:MM 换算为绝对时刻，该钟点的今日时刻严格晚于受理时刻取今天，否则取下一本地日。
  预测与唤醒共用同一事实生成器（_unreminded，未提醒的事务）：reminder_times 给严格晚于此刻的时刻（下一次决策机会的
  候选），boundary_times 只给清醒人物的这些时刻（下一边界的候选，睡眠中的人物不计入），reminders_due 给已到点的事务
  （唤醒原因 errand_reminder_due）；deliver_reminders 在为清醒人物提供决策机会的边界把全部已到点的事务一并交付，每条
  记下提醒时刻、写一条人物可见的 errand_reminded，事务的其余状态不变。
- 完成依据与结算：evidence_rows 只读事务所有人自己的 goods_received，按商品累加 quantity，达到数量减容差
  即移出并写办成事件，否则写进展事件；两种事件只经 caused_by 关联该收货草稿。

"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from worldmock.errors import BusinessRuleError, IntegrityError
from worldmock.footprints.models import EventDraft
from worldmock.life.control_items import CONTROL_TOOLS, control_errands
from worldmock.life.errand_state import (
    AGENT_VISIBLE_KINDS,
    CANCELLED_KIND,
    CHANGE_ADD,
    CHANGE_CANCEL,
    CHANGE_UPDATE,
    COMPLETED_KIND,
    ERRAND_PROGRESS_DIGITS,
    ERRAND_TOLERANCE,
    MAX_OPEN_ERRANDS,
    PROGRESSED_KIND,
    REGISTERED_KIND,
    REMINDED_KIND,
    UPDATED_KIND,
    ErrandState,
)
from worldmock.life.error_contract import ErrorItem, items_fix
from worldmock.life.guidance import Refusal, items_refusal, refusal_for_code
from worldmock.life.models import ActivityKind, AgentState, PendingAction, WorldState, active_activities
from worldmock.life.name_resolution import near_names
from worldmock.life.object_labels import resolve_name
from worldmock.life.readable import local_date, portion_phrase
from worldmock.life.schedule import clock_epoch, day_end_seconds, epoch_clock

_LOGGER = logging.getLogger(__name__)

#: 各类事务变更错误的 expected（只给要求的写法，上限写在 why，docs/design/04-simulation-engine.md ）。
_ERRAND_EXPECTED = "The exact name of an open errand in the todo layer is required."
_ITEM_EXPECTED = "An exact known product name is required."
_NAME_EXPECTED = "A name that no open errand uses is required."
_PAIR_EXPECTED = "A different product is required."
_CAPACITY_EXPECTED = "An addition that the open errands can still take is required."
#: 合并拒绝按明细码的改法。
_CHANGE_FIXES = {"NAME_NOT_FOUND": "Use an exact name.", "NAME_AMBIGUOUS": "Use an unambiguous complete name.",
                 "ARGUMENTS_INVALID": "Correct every reported errand change."}


@dataclass(frozen=True, slots=True)
class ErrandOrigin:
    """@brief 一次受理的来源：动作身份（新增事务的身份前缀）、请求事件身份、受理时刻与场景时区（换算提醒钟点）。"""

    action_id: str
    event_id: str
    now: int
    timezone: str


@dataclass(slots=True)
class _Plan:
    """@brief 一次请求的事务变更解析：逐条问题（明细，一句原因）、更新与取消点名的未完成事务绑定行（按条目下标）、新增
    解析出的商品身份（按条目下标）。"""

    issues: list[tuple[ErrorItem, str]] = field(default_factory=list)
    targets: dict[int, Mapping[str, Any]] = field(default_factory=dict)
    products: dict[int, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _Head:
    """@brief 事务事件草稿的公共部分：事务所有人、写入时刻与原因事件。"""

    owner: int
    now: int
    caused_by: tuple[str, ...]


class Evidence(NamedTuple):
    """@brief 本人的收货证据：事务所有人、商品身份与本次取得的份数。"""

    owner_id: int
    product_id: str
    quantity: float


def bindings(agent: AgentState, items: Mapping[str, Any]) -> dict:
    """@brief 本次观察的两类事务私有绑定：本人未完成事务（待办层顺序）与全部商品名（按商品身份排序）。

    items 是冻结目录的商品（商品身份 → 带 name 的对象，LifeRuleEngine.items）；商品名本就可按名调用，列全量不扩大
    权限。宿主与环境对同一份冻结观察里的这两张表做同一校验。
    """
    return {
        "errand_bindings": _errand_rows(agent),
        "errand_item_bindings": [{"name": items[product_id].name, "product_id": product_id}
                                 for product_id in sorted(items)],
    }


def todo_rows(agent: AgentState, product_names: Mapping[str, str], timezone_name: str) -> list[dict]:
    """@brief 待办层 errands：本人未完成事务按记下的时刻与事务身份排序，每条一行（product_names：商品身份 → 商品名）。"""
    return [_row(errand, product_names, timezone_name) for errand in _open_errands(agent)]


def plan_rows(agent: AgentState, product_names: Mapping[str, str], timezone_name: str) -> list[dict]:
    """@brief 零点计划输入的只读键「个人事务」（docs/design/04-simulation-engine.md ）：与待办层同一行构造、同一顺序，
    每条写名称、要办成什么、已办到哪、备注与提醒时刻（尚未提醒的钟点，没有或已提醒为 null）；不含内部身份与记下的
    日期，零点计划只读它，不创建、不更改、不终结事务。"""
    return [{"名称": row["name"], "要办成什么": row["done_when"], "已办到哪": row["progress"], "备注": row["memo"],
             "提醒时刻": row["remind_time"]} for row in todo_rows(agent, product_names, timezone_name)]


def remind_epoch(clock: str | None, now: int, timezone_name: str) -> int | None:
    """@brief 事务提醒钟点 HH:MM 换算为绝对时刻（docs/design/04-simulation-engine.md#simulation-engine-提醒时刻 ）：该钟点在受理时刻所在本地日的时刻
    严格晚于受理时刻时取它，否则取下一本地日的同一钟点（按本地日历换算，不加固定秒数），所以总在二十四小时之内；None
    原样返回（不设提醒）。"""
    if clock is None:
        return None
    today = clock_epoch(clock, now, timezone_name)
    if today is not None and today > now:
        return today
    return clock_epoch(clock, day_end_seconds(now, timezone_name), timezone_name)


def change_issues(changes: Sequence[Mapping[str, Any]], observed: Mapping[str, Any]) -> list[tuple[ErrorItem, str]]:
    """@brief 一次请求的个人事务变更在冻结观察上的全部状态相关问题（docs/design/04-simulation-engine.md 、2.3），宿主与
    环境调用同一函数、判定一致；没有问题时为空列表。

    结构（键、类型、枚举、交叉约束、长度、条数）由请求模型先校验，这里只判与未完成事务和商品名相关的四类：更新或取消的
    名称找不到、有歧义（NAME_NOT_FOUND、NAME_AMBIGUOUS，allowed_values 列本人全部未完成事务名）与被第二条更新或取消点名；
    新增的商品名解析不到唯一商品（最接近的至多三个商品名写在 why，不列全量）；新增的名称与未完成事务或同请求较早一条新增
    归一后相同、商品都相同；新增后未完成数超过上限。都不带候选，按（条目下标, 键）排序。

    :param changes: control_items.control_errands 读出的各条变更（七键字典），按请求顺序。
    :param observed: 带 errand_bindings 与 errand_item_bindings 的冻结观察（宿主一侧是同样两张表的名称簿）。
    :returns: （明细，这一条的一句原因）；原因与明细的 why 相同。
    """
    return _plan(changes, observed).issues


def change_refusal(issues: Sequence[tuple[ErrorItem, str]]) -> Refusal:
    """@brief 事务变更的全部问题合并成的一次拒绝（docs/design/04-simulation-engine.md 「环境复核」）：顶层码与控制工具活动名
    的防御拒绝一样取 ACTION_REQUEST_INVALID（不新增业务码，照常计入同刻轮数），原因逐条以「; 」相连，下一步取该码的默认
    做法，改法按明细码各写一句，明细原样带上。

    :param issues: change_issues 给出的非空问题列表。
    """
    merged = items_refusal(issues, len(issues))
    default = refusal_for_code("ACTION_REQUEST_INVALID")
    return Refusal("ACTION_REQUEST_INVALID", merged.reason, default.next_step,
                   fix=items_fix(merged.items, len(issues), _CHANGE_FIXES) or "", items=merged.items)


def apply_changes(agent: AgentState, changes: Sequence[Mapping[str, Any]], observed: Mapping[str, Any],
                  origin: ErrandOrigin) -> tuple[AgentState, list[dict]]:
    """@brief 把一次请求里已通过校验的个人事务变更应用到人物状态的副本（docs/design/04-simulation-engine.md 、2.2）。

    与 change_issues 用同一个私有解析：取消把事务移出未完成集合；更新只替换备注与提醒（null 即清空，写了钟点就按受理时刻
    重新换算并重新计一次提醒），完成依据与名称不可改；新增以 `<动作身份>:errand:<条目下标>` 为身份、零累计量记下。解析出的
    事务不在本人未完成集合（观察与世界对不上）时只报一条不带明细的 ACTION_REQUEST_INVALID。

    :param origin: 来源动作、请求事件、受理时刻与场景时区。
    :returns: （人物状态副本，回执），回执按请求顺序为 [{errand, change}]：更新与取消写待办层里的名称，新增写所写的名称。
    :raises BusinessRuleError: 变更不合法（与 change_refusal 相同的合并拒绝），或观察与世界对不上。
    """
    plan = _plan(changes, observed)
    if plan.issues:
        _LOGGER.error("Errand changes reached application with unresolved issues")
        raise change_refusal(plan.issues).error()
    if any(row["errand_id"] not in agent.errands for row in plan.targets.values()):
        _LOGGER.error("Resolved errand name is not an open errand of this person")
        raise BusinessRuleError("ACTION_REQUEST_INVALID")
    errands = dict(agent.errands)
    for index, change in enumerate(changes):
        _apply_one(errands, change, plan, (index, origin))
    receipt = [{"errand": plan.targets[index]["name"] if index in plan.targets else change["name"],
                "change": change["change"]} for index, change in enumerate(changes)]
    return agent.model_copy(update={"errands": errands}), receipt


def request_drafts(before: WorldState, after: WorldState, pending: PendingAction) -> list[EventDraft]:
    """@brief 一次被受理的控制请求里每条事务变更恰好一条事务事件（docs/design/04-simulation-engine.md 「原子性」、2.7），
    按请求顺序；不是控制工具时为空列表。只在成功分支调用，不经拒绝分支也会提交的暂存通道。

    变更只经 control_items.control_errands 读取（控制请求的唯一读取入口）。新增写 errand_registered（after 里新记下的
    事务）；更新写 errand_updated（after 里更新后的快照，内容没有变化也写）；取消写 errand_cancelled（before 里移出前的
    快照，另带原因）。更新与取消按所写名称在 before 里本人未完成事务的名称中解析，与受理时同一解析。三种事件都是
    environment_event、系统可见、人物为事务所有人，决策、动作与活动身份为空，caused_by 只有请求事件。

    :param before: 本动作受理前的暂存世界。
    :param after: 本动作受理后的暂存世界。
    :raises IntegrityError: 被受理的变更在世界里找不到对应的事务。
    """
    if pending.tool_name not in CONTROL_TOOLS:
        return []
    owner = pending.agent_id
    rows = _errand_rows(before.agents[owner])
    head = _Head(owner, after.current_time, (pending.requested_event_id,))
    drafts = []
    for index, change in enumerate(control_errands(pending.tool_name, pending.request)):
        if change["change"] == CHANGE_ADD:
            added = _held(after, owner, f"{pending.action_id}:errand:{index}")
            drafts.append(_draft(head, REGISTERED_KIND, added.model_dump(mode="json")))
        elif change["change"] == CHANGE_UPDATE:
            updated = _held(after, owner, _accepted_target(change["name"], rows))
            drafts.append(_draft(head, UPDATED_KIND, updated.model_dump(mode="json")))
        else:
            cancelled = _held(before, owner, _accepted_target(change["name"], rows))
            drafts.append(_draft(head, CANCELLED_KIND, cancelled.model_dump(mode="json"), {"reason": change["reason"]}))
    return drafts


def evidence_rows(event_type: str, agent_id: int | None, data: Mapping[str, Any]) -> list[Evidence]:
    """@brief 完成依据只读本人 goods_received，订单、自述与旧进食证据不推进事务。"""
    if event_type != "environment_event" or data.get("kind") != "goods_received":
        return []
    if type(agent_id) is not int or agent_id <= 0:
        raise IntegrityError("Goods receipt evidence has no simulated recipient")
    return [Evidence(agent_id, data["goods_id"], float(data["quantity"]))]


def settle(state: WorldState, drafts: Sequence[EventDraft]) -> tuple[WorldState, list[EventDraft]]:
    """@brief 一次提交的个人事务结算（docs/design/04-simulation-engine.md#simulation-engine-完成依据与结算 「结算函数」）：纯函数，每次提交恰好调用一次——
    决策轮在全部动作结算之后，时钟推进在后台事件并入之后、构造节律边界之前。

    按草稿先后逐条读证据行（evidence_rows），命中所有人同一商品的未完成事务（同一人至多一条）即累加：累计量
    达到数量减 1e-9 时把事务移出未完成集合并写 errand_completed，不足时存下累计量并写 errand_progressed。以往提交记下的
    事务对本次全部证据有效；本次提交新增的只认排在其 errand_registered 草稿之后的证据；本次提交里被取消的已不在状态里，
    不再接受任何证据。证据行只在订单由未送达变为已送达的那一次出现，
    所以状态里没有「已消费证据」集合。没有人有未完成事务、或没有证据命中时原样返回世界。

    :param drafts: 本次提交已排好的全部事件草稿。
    :returns: （结算后的世界，追加的事务事件草稿）；草稿按证据的先后，排在全部证据之后，各以其证据草稿为唯一原因。
    """
    if not any(agent.errands for agent in state.agents.values()):
        return state, []
    noted = {draft.data["errand"]["errand_id"]: index for index, draft in enumerate(drafts)
             if draft.event_type == "environment_event" and draft.data.get("kind") == REGISTERED_KIND}
    open_sets = {agent_id: dict(agent.errands) for agent_id, agent in state.agents.items()}
    settled: list[EventDraft] = []
    for index, draft in enumerate(drafts):
        for row in evidence_rows(draft.event_type, draft.agent_id, draft.data):
            errand = _matched(open_sets.get(row.owner_id, {}), row)
            if errand is None or noted.get(errand.errand_id, -1) > index:
                continue
            head = _Head(row.owner_id, state.current_time, (draft.event_id,))
            settled.append(_credit(open_sets[row.owner_id], errand, row, head))
    if not settled:
        return state, []
    agents = {agent_id: agent.model_copy(update={"errands": open_sets[agent_id]})
              for agent_id, agent in state.agents.items()}
    return state.model_copy(update={"agents": agents}), settled


def reminder_times(agent: AgentState, now: int) -> tuple[int, ...]:
    """@brief 本人未提醒事务的提醒时刻中严格晚于 now 的那些（升序去重），下一次决策机会的候选
    （docs/design/04-simulation-engine.md#simulation-engine-提醒时刻 「调度口径」）；人物在睡时由调用方剔除。"""
    return tuple(sorted({errand.remind_at for errand in _unreminded(agent) if errand.remind_at > now}))


def reminders_due(agent: AgentState, now: int) -> tuple[ErrandState, ...]:
    """@brief 本人已到点（提醒时刻不晚于 now）而未提醒的事务，待办层顺序；非空即唤醒原因 errand_reminder_due 成立，
    人物在睡时由唤醒判定过滤（它不在睡眠允许集合）。"""
    return tuple(errand for errand in _unreminded(agent) if errand.remind_at <= now)


def boundary_times(state: WorldState) -> list[int]:
    """@brief 下一边界的事务提醒候选：每个清醒人物（没有进行中的睡眠）严格晚于此刻的未提醒时刻；睡眠中的人物不计入，
    不形成静默边界，错过的提醒在醒来的边界交付（docs/design/04-simulation-engine.md#simulation-engine-提醒时刻 ）。"""
    return [moment for agent_id, agent in state.agents.items() if not _asleep(state, agent_id)
            for moment in reminder_times(agent, state.current_time)]


def deliver_reminders(state: WorldState, agent_id: int) -> tuple[WorldState, list[EventDraft]]:
    """@brief 在为该人物提供决策机会的边界交付他全部已到点未提醒的事务（docs/design/04-simulation-engine.md#simulation-engine-提醒时刻 、2.7）：
    每条记下提醒时刻 reminded_at（只提醒一次，名称、完成依据、累计量与备注都不变，事务仍未完成），各写一条
    errand_reminded（人物可见，人物为事务所有人，决策、动作与活动身份为空，没有原因事件，载荷为记下提醒后的快照）。
    没有到点的事务时事务不变、没有草稿。

    :returns: （交付后的世界，提醒事件草稿），草稿按待办层顺序。
    """
    agent = state.agents[agent_id]
    errands, drafts = dict(agent.errands), []
    head = _Head(agent_id, state.current_time, ())
    for errand in reminders_due(agent, state.current_time):
        reminded = errand.model_copy(update={"reminded_at": state.current_time})
        errands[errand.errand_id] = reminded
        drafts.append(_draft(head, REMINDED_KIND, reminded.model_dump(mode="json")))
    agents = {**state.agents, agent_id: agent.model_copy(update={"errands": errands})}
    return state.model_copy(update={"agents": agents}), drafts


def _open_errands(agent: AgentState) -> list[ErrandState]:
    """@brief 本人的未完成事务，按记下的时刻、再按事务身份排序（待办层与私有绑定的同一顺序）。"""
    return sorted(agent.errands.values(), key=lambda errand: (errand.registered_at, errand.errand_id))


def _unreminded(agent: AgentState) -> list[ErrandState]:
    """@brief 预测与唤醒共用的唯一事实生成器：本人设了提醒时刻而尚未提醒的未完成事务，待办层顺序（完成或取消的事务已不在
    状态里，不再提醒）。"""
    return [errand for errand in _open_errands(agent) if errand.remind_at is not None and errand.reminded_at is None]


def _asleep(state: WorldState, agent_id: int) -> bool:
    """@brief 人物此刻是否在睡：有进行中的睡眠活动（与节律判定的睡眠口径相同）。"""
    return any(item.kind == ActivityKind.SLEEP for item in active_activities(state, agent_id))


def _errand_rows(agent: AgentState) -> list[dict]:
    """@brief 本人未完成事务的私有绑定行 {name, errand_id, product_id}，待办层顺序。"""
    return [{"name": errand.name, "errand_id": errand.errand_id,
             "product_id": errand.product_id} for errand in _open_errands(agent)]


def _row(errand: ErrandState, product_names: Mapping[str, str], timezone_name: str) -> dict:
    """@brief 一条事务的待办层行：名称、要办成的事（「取得<商品名> N 份」）、已办到哪（「已取得 N 份」，尚无进展写 0 份）、
    备注、尚未提醒的提醒钟点（没有或已提醒为 null）与记下的本地日期。"""
    pending = errand.remind_at is not None and errand.reminded_at is None
    return {"name": errand.name,
            "done_when": f"取得{product_names[errand.product_id]} {portion_phrase(errand.quantity)}",
            "progress": f"已取得 {portion_phrase(errand.progress)}",
            "memo": errand.memo,
            "remind_time": epoch_clock(errand.remind_at, timezone_name) if pending else None,
            "noted_on": local_date(errand.registered_at, timezone_name)}


def _plan(changes: Sequence[Mapping[str, Any]], observed: Mapping[str, Any]) -> _Plan:
    """@brief 校验与应用共用的解析：先解析更新与取消点名的未完成事务，再按扣除本请求取消之后的未完成集合判定各条新增；
    问题按（条目下标, 键）排序。"""
    plan = _Plan()
    rows = list(observed["errand_bindings"])
    _plan_targets(plan, changes, rows)
    cancelled = {row["errand_id"] for index, row in plan.targets.items() if changes[index]["change"] == CHANGE_CANCEL}
    remaining = [row for row in rows if row["errand_id"] not in cancelled]
    _plan_additions(plan, changes, remaining, list(observed["errand_item_bindings"]))
    plan.issues.sort(key=lambda issue: issue[0].path[1:])
    return plan


def _plan_targets(plan: _Plan, changes: Sequence[Mapping[str, Any]], rows: list[Mapping[str, Any]]) -> None:
    """@brief 更新与取消按待办层名称逐条解析到未完成事务；解析不出唯一一条的报名称错误，同一事务被第二条更新或取消点名的
    只报较晚的那一条。"""
    named: set[str] = set()
    for index, change in enumerate(changes):
        if change["change"] == CHANGE_ADD:
            continue
        found, matches = _open_name(change["name"], rows)
        if len(found) != 1:
            plan.issues.append(_unknown_errand(change, index, [row["name"] for row in found] or list(matches), rows))
        elif found[0]["errand_id"] in named:
            why = (f"The open errand {_quoted([found[0]['name']])} is named by more than one update or cancel "
                   "in this request.")
            plan.issues.append(_issue(change, (index, "name"), "ARGUMENTS_INVALID", (_ERRAND_EXPECTED, why)))
        else:
            named.add(found[0]["errand_id"])
            plan.targets[index] = found[0]


def _plan_additions(plan: _Plan, changes: Sequence[Mapping[str, Any]], remaining: list[Mapping[str, Any]],
                    products: list[Mapping[str, Any]]) -> None:
    """@brief 各条新增依请求顺序判定：商品名解析到唯一商品；名称与扣除取消后的未完成事务、与同请求较早的每条新增都不同名；
    商品不与它们相同（只比商品已解析的）；连同此前各条新增不超过未完成事务的上限（每条新增都计数）。"""
    earlier: list[int] = []
    for index, change in enumerate(changes):
        if change["change"] != CHANGE_ADD:
            continue
        found, matches = _named_rows(change["item"], products)
        if len(found) == 1:
            plan.products[index] = found[0]["product_id"]
        else:
            plan.issues.append(_unknown_item(change, index, [row["name"] for row in found] or list(matches), products))
        plan.issues.extend(_added_conflicts(plan, changes, (index, earlier), remaining))
        if len(remaining) + len(earlier) + 1 > MAX_OPEN_ERRANDS:
            why = f"This addition would exceed the limit of {MAX_OPEN_ERRANDS} open errands."
            plan.issues.append(_issue(change, (index, "change"), "ARGUMENTS_INVALID", (_CAPACITY_EXPECTED, why)))
        earlier.append(index)


def _added_conflicts(plan: _Plan, changes: Sequence[Mapping[str, Any]], at: tuple[int, list[int]],
                     remaining: list[Mapping[str, Any]]) -> list[tuple[ErrorItem, str]]:
    """@brief 一条新增与未完成事务、与同请求较早各条新增的冲突：名称归一后相同（不论较早那条有没有别的问题），结果种类与
    商品都相同（只在本条商品已解析时比，较早的新增只比商品已解析的）。

    :param at: （本条下标，同请求较早各条新增的下标）。
    """
    index, earlier = at
    change, issues = changes[index], []
    taken = resolve_name(change["name"], [row["name"] for row in remaining]).matches
    twins = resolve_name(change["name"], [changes[other]["name"] for other in earlier]).matches
    if taken or twins:
        why = (f"An open errand is already named {_quoted(taken[:1])}." if taken
               else f"Two added errands share the name {_quoted(twins[:1])}.")
        issues.append(_issue(change, (index, "name"), "ARGUMENTS_INVALID", (_NAME_EXPECTED, why)))
    if index not in plan.products:
        return issues
    product_id = plan.products[index]
    held = [row["name"] for row in remaining if row["product_id"] == product_id]
    doubled = [other for other in earlier if other in plan.products
               and plan.products[other] == product_id]
    if held or doubled:
        why = (f"The open errand {_quoted(held[:1])} already tracks this product." if held
               else "Two added errands track the same product.")
        issues.append(_issue(change, (index, "item"), "ARGUMENTS_INVALID", (_PAIR_EXPECTED, why)))
    return issues


def _open_name(written: str, rows: Sequence[Mapping[str, Any]]) -> tuple[list[Mapping[str, Any]], tuple[str, ...]]:
    """@brief 更新或取消所写的事务名在本人未完成事务中的解析（与活动名同一函数：精确，再无损归一，唯一才受理）：（唯一
    解析出的名称对应的绑定行，同一级的全部匹配名称）；解析不出唯一一条时由调用方报名称错误，从不替换成近似的名称。"""
    return _named_rows(written, rows)


def _named_rows(written: str, rows: Sequence[Mapping[str, Any]]) -> tuple[list[Mapping[str, Any]], tuple[str, ...]]:
    """@brief 所写名称在一组绑定行（name → 身份）中的解析：resolve_name 唯一解析出的名称所对应的全部行（多于一行即同名有
    歧义）与同一级的全部匹配名称（没有即找不到）。"""
    match = resolve_name(written, [row["name"] for row in rows])
    return [row for row in rows if row["name"] == match.name], match.matches


def _unknown_errand(change: Mapping[str, Any], index: int, matched: list[str],
                    rows: Sequence[Mapping[str, Any]]) -> tuple[ErrorItem, str]:
    """@brief 更新或取消所写的事务名找不到（NAME_NOT_FOUND）或对应多条（NAME_AMBIGUOUS）：allowed_values 列本人全部未完成
    事务名（至多八个，名称字段不列全量的明示例外），不带候选。"""
    written = _quoted([change["name"]])
    why = (f"The errand name {written} matches several open errands." if matched
           else f"The errand name {written} is not known.")
    code = "NAME_AMBIGUOUS" if matched else "NAME_NOT_FOUND"
    return _issue(change, (index, "name"), code, (_ERRAND_EXPECTED, why), [row["name"] for row in rows])


def _unknown_item(change: Mapping[str, Any], index: int, matched: list[str],
                  products: Sequence[Mapping[str, Any]]) -> tuple[ErrorItem, str]:
    """@brief 新增所写的商品名解析不到唯一商品：找不到时 why 写最接近的至多三个商品名，对应多个商品时 why 列出它们（至多
    三个）；不把商品名放进 allowed_values，不带候选。"""
    written = change["item"]
    if matched:
        why = f"The product name {_quoted([written])} matches several products: {_quoted(matched[:3])}."
        return _issue(change, (index, "item"), "NAME_AMBIGUOUS", (_ITEM_EXPECTED, why))
    near = near_names(written, [row["name"] for row in products])
    closest = f" Nearest product names: {_quoted(near)}." if near else ""
    why = f"No product is named {_quoted([written])}.{closest}"
    return _issue(change, (index, "item"), "NAME_NOT_FOUND", (_ITEM_EXPECTED, why))


def _issue(change: Mapping[str, Any], at: tuple[int, str], code: str, texts: tuple[str, str],
           allowed: Sequence[str] = ()) -> tuple[ErrorItem, str]:
    """@brief 一条事务变更问题：路径 errands[j].<键>、该键所写的原值、明细码、（expected, why）与合法值；原因句就是 why。

    :param at: （条目下标, 键）。
    """
    index, key = at
    expected, why = texts
    return ErrorItem(("errands", index, key), change[key], code, tuple(allowed), expected, why), why


def _apply_one(errands: dict[str, ErrandState], change: Mapping[str, Any], plan: _Plan,
               at: tuple[int, ErrandOrigin]) -> None:
    """@brief 在未完成事务字典的副本上应用一条已解析的变更：取消即移出；更新只替换备注与提醒并清掉已提醒时刻；新增以来源
    动作与条目下标为身份记下，累计量为零。

    :param at: （条目下标，来源）。
    """
    index, origin = at
    remind_at = remind_epoch(change["remind_time"], origin.now, origin.timezone)
    if change["change"] == CHANGE_CANCEL:
        errands.pop(plan.targets[index]["errand_id"])
    elif change["change"] == CHANGE_UPDATE:
        errand_id = plan.targets[index]["errand_id"]
        errands[errand_id] = errands[errand_id].model_copy(
            update={"memo": change["memo"], "remind_at": remind_at, "reminded_at": None})
    else:
        errand_id = f"{origin.action_id}:errand:{index}"
        errands[errand_id] = ErrandState(
            errand_id=errand_id, name=change["name"],
            product_id=plan.products[index], quantity=change["quantity"], progress=0.0, memo=change["memo"],
            registered_at=origin.now, remind_at=remind_at, reminded_at=None,
            origin_action_id=origin.action_id, origin_event_id=origin.event_id)


def _accepted_target(written: str, rows: Sequence[Mapping[str, Any]]) -> str:
    """@brief 被受理的更新或取消所点名事务的身份（与受理时同一解析）；解析不出唯一一条即世界与已受理的请求不一致。"""
    found, _ = _open_name(written, rows)
    if len(found) != 1:
        _LOGGER.error("An accepted errand change names no single open errand")
        raise IntegrityError("An accepted errand change names no single open errand")
    return found[0]["errand_id"]


def _held(state: WorldState, owner: int, errand_id: str) -> ErrandState:
    """@brief 世界里人物持有的一条未完成事务；没有即与已受理的请求不一致。"""
    errand = state.agents[owner].errands.get(errand_id)
    if errand is None:
        _LOGGER.error("An accepted errand change has no errand in the world state")
        raise IntegrityError(f"Agent {owner} holds no errand {errand_id}")
    return errand


def _draft(head: _Head, kind: str, snapshot: dict, extra: Mapping[str, Any] | None = None) -> EventDraft:
    """@brief 唯一的事务事件草稿构造（docs/design/04-simulation-engine.md ）：environment_event，来源为环境，人物为事务所有
    人，决策、动作与活动身份都为空，只经 caused_by 关联；载荷为 {kind, errand}（事务快照）另加 extra；提醒与办成两种人物
    可见，其余系统可见。"""
    return EventDraft(event_type="environment_event", occurred_at=head.now, agent_id=head.owner, decision_id=None,
                      action_id=None, activity_id=None, caused_by=list(head.caused_by), source_kind="environment",
                      visibility="agent" if kind in AGENT_VISIBLE_KINDS else "system",
                      data={"kind": kind, "errand": snapshot, **(extra or {})})


def _matched(errands: Mapping[str, ErrandState], row: Evidence) -> ErrandState | None:
    """@brief 证据所有人的未完成事务中与证据同一商品的那一条（同一人至多一条），没有为 None。"""
    return next((errand for errand in errands.values()
                 if errand.product_id == row.product_id), None)


def _credit(errands: dict[str, ErrandState], errand: ErrandState, row: Evidence, head: _Head) -> EventDraft:
    """@brief 把一条命中的证据累加到事务（errands 为所有人未完成事务字典的副本，就地改写）：累计量（九位小数）达到数量减
    容差即移出并写 errand_completed——快照的 progress 改为最终累计量，因为未完成事务的模型不能持有达到数量的累计量；
    否则存下新累计量并写 errand_progressed。两种都另带本条证据的数量 quantity_added。"""
    added = round(row.quantity, ERRAND_PROGRESS_DIGITS)
    progress = round(errand.progress + added, ERRAND_PROGRESS_DIGITS)
    if progress >= errand.quantity - ERRAND_TOLERANCE:
        errands.pop(errand.errand_id)
        snapshot = {**errand.model_dump(mode="json"), "progress": progress}
        return _draft(head, COMPLETED_KIND, snapshot, {"quantity_added": added})
    errands[errand.errand_id] = ErrandState.model_validate({**errand.model_dump(), "progress": progress})
    return _draft(head, PROGRESSED_KIND, errands[errand.errand_id].model_dump(mode="json"), {"quantity_added": added})


def _quoted(names: Sequence[str]) -> str:
    """@brief 几个名称的说法「A」、「B」。"""
    return "、".join(f"「{name}」" for name in names)
