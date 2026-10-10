"""@brief 声明式回调的唯一求值器，效果与真实原因同次提交。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from worldmock.catalog.callback_models import FootprintChain, MoneyTransfer, StateDelta, StateReminder, VisibleHint
from worldmock.errors import IntegrityError
from worldmock.footprints.models import EventDraft
from worldmock.footprints.callback_contract import StateDeltaResult, StateResetResult, TransferResult
from worldmock.life.models import ActivityKind, WorldState, active_activities
from worldmock.life.randomness import random_stream
from worldmock.life.settlement import settle_money_transfer


@dataclass(frozen=True)
class CallbackCause:
    """@brief 真实触发类、键、人物、直接原因和稳定随机锚点。"""

    #: 目录声明的触发类。
    trigger_class: str
    #: 具体动作、时间键、状态身份或事件类别。
    key: str
    #: 唯一人物身份。
    agent_id: int
    #: 本次提交的真实触发事件身份。
    source_event_id: str
    #: 不使用事件随机身份的确定性锚点。
    anchor: str


@dataclass(frozen=True)
class CallbackOutcome:
    """@brief 唯一新状态与必须和它共同提交的证据。"""

    #: 已实际应用效果的世界。
    state: WorldState
    #: 按规则与步骤次序产生的真实证据。
    drafts: list[EventDraft]
    #: 本次提交内各效果沿用的真实触发原因，不写入持久事件正文。
    causes: dict[str, CallbackCause] = field(default_factory=dict)


def validate_callback_states(state: WorldState, catalog) -> None:
    """@brief 恢复和写入拒绝状态集合、范围、规则身份或本地日期损坏。"""
    for agent in state.agents.values():
        if set(agent.callback_states) != set(catalog.callback_states):
            _invalid("Callback state set differs from the registered environment")
        for name, value in agent.callback_states.items():
            if type(value) is not int or not 0 <= value <= catalog.callback_states[name].cap:
                _invalid("Callback state value is outside its registered domain")
        for value in agent.callback_once_keys:
            rule_id, separator, local_date = value.rpartition(":")
            rule = catalog.callback_rules.get(rule_id)
            if (not separator or rule is None or not isinstance(rule.effect, StateDelta)
                    or rule.effect.once_per != "local_day" or not _is_date(local_date)):
                _invalid("Callback daily application identity or date is invalid")
        for rule_id, value in agent.callback_reminder_days.items():
            rule = catalog.callback_rules.get(rule_id)
            if rule is None or not isinstance(rule.notify, StateReminder) or not _is_date(value):
                _invalid("Callback reminder identity or date is invalid")


def _is_date(value: str) -> bool:
    """@brief 日期只接受规范 ISO 日历格式。"""
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        logging.error("Callback local date is invalid")
        return False


def _invalid(reason: str) -> None:
    """@brief 持久化回调损坏必须有英文日志并立即拒绝。"""
    logging.error("%s", reason)
    raise IntegrityError(reason)


def evaluate_callbacks(state: WorldState, catalog, cause: CallbackCause,
                       balance_before: int | None = None) -> CallbackOutcome:
    """@brief 同一真实触发按规则排序执行，净减少提示包含该动作已收车费。"""
    account = state.agents[cause.agent_id].balance_account_id
    before = state.accounts[account] if balance_before is None else balance_before
    drafts, visible, causes = [], False, {}
    for rule in sorted(catalog.callback_rules.values(), key=lambda value: value.rule_id):
        if not _matches(rule, cause, state):
            continue
        if (cause.trigger_class == "action" and isinstance(rule.effect, StateDelta)
                and rule.effect.once_per == "local_day"):
            continue
        outcome = _apply_rule(state, catalog, rule, cause)
        state = outcome.state
        drafts.extend(outcome.drafts)
        causes.update(outcome.causes)
        visible |= isinstance(rule.notify, VisibleHint) and rule.notify.visible
    decreased = before - state.accounts[account]
    if visible and decreased > 0:
        drafts.append(_draft(state, cause, {"kind": "balance_decreased", "amount_fen": decreased}, "agent"))
    return CallbackOutcome(state, drafts, causes)


def _matches(rule, cause: CallbackCause, state: WorldState) -> bool:
    """@brief 月度规则在共用时间事实之后仍核对自身日期与时刻。"""
    trigger = rule.trigger
    if trigger.trigger_class != cause.trigger_class or trigger.key != cause.key:
        return False
    if cause.trigger_class == "time" and cause.key == "monthly":
        local = datetime.fromtimestamp(state.current_time, ZoneInfo(state.timezone))
        return local.day == trigger.day and local.strftime("%H:%M") == trigger.time
    return True


def _apply_rule(state: WorldState, catalog, rule, cause: CallbackCause) -> CallbackOutcome:
    """@brief 只按效果类型派发，不按规则身份或应用名称新增专用行为。"""
    if isinstance(rule.effect, StateDelta):
        if rule.effect.once_per == "local_day":
            day = datetime.fromtimestamp(state.current_time, ZoneInfo(state.timezone)).date().isoformat()
            return _apply_daily_delta(state, catalog, rule, cause, day)
        return _apply_delta(state, catalog, rule, cause)
    if isinstance(rule.effect, MoneyTransfer):
        return _apply_transfer(state, rule, cause)
    if isinstance(rule.effect, FootprintChain):
        return _apply_chain(state, rule, cause)
    _invalid("Callback effect type is outside the registered contract")


def _apply_delta(state: WorldState, catalog, rule, cause: CallbackCause) -> CallbackOutcome:
    """@brief 增量钳位后写真实前后值，零实际差额仍保留已应用请求。"""
    agent = state.agents[cause.agent_id]
    effect = rule.effect
    before = agent.callback_states[effect.state]
    after = min(catalog.callback_states[effect.state].cap, max(0, before + effect.delta))
    agent = agent.model_copy(update={"callback_states": {**agent.callback_states, effect.state: after}})
    state = _put_agent(state, agent)
    payload = StateDeltaResult(rule_id=rule.rule_id, state=effect.state,
                               delta=effect.delta, before=before, after=after)
    draft = _draft(state, cause, payload.model_dump(mode="json"))
    return CallbackOutcome(state, [draft], {draft.event_id: cause})


def _apply_daily_delta(state: WorldState, catalog, rule, cause: CallbackCause, local_date: str) -> CallbackOutcome:
    """@brief 每规则同一本地日最多实际执行一次，重置不清除已执行日期。"""
    agent = state.agents[cause.agent_id]
    once = f"{rule.rule_id}:{local_date}"
    if once in agent.callback_once_keys:
        return CallbackOutcome(state, [])
    outcome = _apply_delta(state, catalog, rule, cause)
    agent = outcome.state.agents[cause.agent_id]
    agent = agent.model_copy(update={"callback_once_keys": {*agent.callback_once_keys, once}})
    state = _put_agent(outcome.state, agent)
    return CallbackOutcome(state, outcome.drafts, outcome.causes)


def _apply_transfer(state: WorldState, rule, cause: CallbackCause) -> CallbackOutcome:
    """@brief 收入与支出逐项守恒，支出不足全额未付且不阻断触发。"""
    effect = rule.effect
    account = state.agents[cause.agent_id].balance_account_id
    source, target = ("external:scenario", account) if effect.direction == "income" else (account, "external:scenario")
    paid = effect.direction == "income" or state.accounts[source] >= effect.amount_fen
    changes = {}
    if paid:
        state = settle_money_transfer(state, source, target, effect.amount_fen,
                                      f"callback:{rule.rule_id}:{cause.anchor}")
        changes = {f"account:{source}": -effect.amount_fen, f"account:{target}": effect.amount_fen}
    else:
        logging.error("Callback expense cannot be paid in full: %s", rule.rule_id)
    payload = TransferResult(rule_id=rule.rule_id, direction=effect.direction, amount_fen=effect.amount_fen,
                             from_account_id=source, to_account_id=target, status="paid" if paid else "unpaid")
    draft = _draft(state, cause, payload.model_dump(mode="json"), changes=changes)
    return CallbackOutcome(state, [draft], {draft.event_id: cause})


def _apply_chain(state: WorldState, rule, cause: CallbackCause) -> CallbackOutcome:
    """@brief 各步骤独立抽取一个实际应用，支付足迹不移动资金。"""
    drafts = []
    for index, step in enumerate(rule.effect.steps):
        stream = random_stream(state.random_state, f"callback:{rule.rule_id}:{cause.anchor}:{index}")
        app_id = stream.choice(tuple(sorted(step.app_pool)))
        action = cause.key if cause.trigger_class == "action" else None
        payload = {"kind": "app_touch", "app_id": app_id, "action": action,
                   "step": step.kind, "rule_id": rule.rule_id}
        drafts.append(_draft(state, cause, payload))
    return CallbackOutcome(state, drafts, {draft.event_id: cause for draft in drafts})


def _draft(state: WorldState, cause: CallbackCause, payload: dict, visibility: str = "system",
           changes: dict | None = None) -> EventDraft:
    """@brief 所有效果只引用该触发的真实直接原因。"""
    return EventDraft(event_type="environment_event", occurred_at=state.current_time, agent_id=cause.agent_id,
                      source_kind="environment", visibility=visibility, caused_by=[cause.source_event_id],
                      data=payload, resource_changes={} if changes is None else changes)


def _put_agent(state: WorldState, agent) -> WorldState:
    """@brief 效果只替换唯一状态中的人物，不维护另一份簿记。"""
    return state.model_copy(update={"agents": {**state.agents, agent.agent_id: agent}})


def settle_callback_dates(state: WorldState, catalog, activity, interval: tuple[int, int]) -> CallbackOutcome:
    """@brief 按实际半开区间中的本地日期执行一次增量，不按受理日或固定日秒数。"""
    start, end = interval
    if end <= start:
        return CallbackOutcome(state, [])
    drafts, causes = [], {}
    for local_date in _interval_dates(start, end, state.timezone):
        for rule in sorted(catalog.callback_rules.values(), key=lambda value: value.rule_id):
            if (rule.trigger.trigger_class != "action" or rule.trigger.key != activity.data["action"]
                    or not isinstance(rule.effect, StateDelta) or rule.effect.once_per != "local_day"):
                continue
            agent = state.agents[activity.agent_id]
            once = f"{rule.rule_id}:{local_date}"
            if once in agent.callback_once_keys:
                continue
            cause = CallbackCause("action", rule.trigger.key, activity.agent_id,
            activity.data["accepted_event_id"], f"{activity.origin_action_id}:{local_date}")
            outcome = _apply_daily_delta(state, catalog, rule, cause, local_date)
            state, drafts = outcome.state, [*drafts, *outcome.drafts]
            causes.update(outcome.causes)
    return CallbackOutcome(state, drafts, causes)


def _interval_dates(start: int, end: int, timezone: str) -> list[str]:
    """@brief 半开终点减一秒避免午夜停下时误计次日，并保留夏令时日长。"""
    zone = ZoneInfo(timezone)
    current = datetime.fromtimestamp(start, zone).date()
    final = datetime.fromtimestamp(end - 1, zone).date()
    dates = []
    while current <= final:
        dates.append(current.isoformat())
        current += timedelta(days=1)
    return dates


def reset_on_receipt(state: WorldState, catalog, receipt: EventDraft) -> CallbackOutcome:
    """@brief 真实收货只将匹配状态置为上限，不清除当天生效记录。"""
    goods_id = receipt.data["goods_id"]
    agent, drafts = state.agents[receipt.agent_id], []
    cause = CallbackCause("event", "goods_received", agent.agent_id, receipt.event_id, receipt.event_id)
    for name, declaration in sorted(catalog.callback_states.items()):
        if goods_id not in declaration.reset_on_goods:
            continue
        before, after = agent.callback_states[name], declaration.cap
        agent = agent.model_copy(update={"callback_states": {**agent.callback_states, name: after}})
        state = _put_agent(state, agent)
        payload = StateResetResult(state=name, goods_id=goods_id, before=before, after=after)
        drafts.append(_draft(state, cause, payload.model_dump(mode="json")))
    return CallbackOutcome(state, drafts)


def compare_callback_states(before: WorldState, state: WorldState, catalog, drafts: list[EventDraft],
                            causes: dict[str, CallbackCause]) -> CallbackOutcome:
    """@brief 每次提交对最终状态只比较一次，阈值效果不递归或延迟追补。"""
    pending = []
    for rule in sorted(catalog.callback_rules.values(), key=lambda value: value.rule_id):
        trigger = rule.trigger
        if trigger.trigger_class != "state":
            continue
        identity = next(iter(state.agents))
        old = before.agents[identity].callback_states[trigger.key]
        new = state.agents[identity].callback_states[trigger.key]
        if old >= trigger.below > new:
            source = _crossing_source(drafts, trigger.key, trigger.below)
            pending.append((rule, CallbackCause("state", trigger.key, identity,
                                                source.caused_by[0], causes[source.event_id].anchor)))
    emitted = []
    for rule, cause in pending:
        outcome = _apply_rule(state, catalog, rule, cause)
        state, emitted = outcome.state, [*emitted, *outcome.drafts]
    return CallbackOutcome(state, emitted)


def _crossing_source(drafts: list[EventDraft], name: str, below: int) -> EventDraft:
    """@brief 直接原因取最后一次造成越阈的真实效果对应的触发事件。"""
    candidates = [draft for draft in drafts if draft.data.get("kind") == "callback_effect"
                  and draft.data.get("state") == name and draft.data.get("before", -1) >= below
                  and draft.data.get("after", below) < below]
    if not candidates:
        _invalid("Callback threshold crossing has no actual source effect")
    return candidates[-1]


def issue_callback_reminders(state: WorldState, catalog) -> CallbackOutcome:
    """@brief 清醒推送整点投递每天一次静默阈值提醒，睡眠不消耗日期。"""
    identity = next(iter(state.agents))
    if any(activity.kind == ActivityKind.SLEEP for activity in active_activities(state, identity)):
        return CallbackOutcome(state, [])
    local = datetime.fromtimestamp(state.current_time, ZoneInfo(state.timezone))
    if local.minute or local.second or not 8 <= local.hour <= 21:
        return CallbackOutcome(state, [])
    agent, drafts = state.agents[identity], []
    today = local.date().isoformat()
    for rule in sorted(catalog.callback_rules.values(), key=lambda value: value.rule_id):
        reminder = rule.notify
        if (not isinstance(reminder, StateReminder) or agent.callback_reminder_days.get(rule.rule_id) == today
                or agent.callback_states[reminder.when.state] >= reminder.when.below):
            continue
        drafts.append(EventDraft(event_type="environment_event", occurred_at=state.current_time, agent_id=identity,
                                  source_kind="environment", visibility="agent",
                                  data={"kind": "callback_reminder", "rule_id": rule.rule_id,
                                        "app_id": reminder.app_id, "text": reminder.text}))
        agent = agent.model_copy(update={"callback_reminder_days": {**agent.callback_reminder_days,
                                                                   rule.rule_id: today}})
    return CallbackOutcome(_put_agent(state, agent), drafts)
