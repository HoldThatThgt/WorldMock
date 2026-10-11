"""@brief 同一模拟秒的三个开放轮和三个受限轮，预算耗尽立即终止。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from worldmock.footprints.models import EventDraft
import logging
from worldmock.errors import ProtocolError
from worldmock.life.action_contract import START_TOOLS
from worldmock.life.error_contract import NAME_REJECTION_CODES
from worldmock.life.guidance import Refusal, refusal_for_code
from worldmock.life.models import (ActionStatus, ActivityState, ActivityStatus, AgentState,
                                   SameMomentStage, WorldState, active_activities, CONCLUDABLE_TERMINAL_STATUSES)
from worldmock.life.schedule import WAKE_TRIGGERS

#: 当前业务拒绝都产生服务拒绝脉冲。
SERVICE_REFUSED = "service_refused"
#: 每人每模拟秒至多几次名称类拒绝不计同刻轮数、不施加情绪脉冲（docs/design/04-simulation-engine.md；实验假设，登记在
#: docs/design/04-simulation-engine.md「原子性与恢复」）；超出的照常计轮、照常施加脉冲。
FREE_NAME_REJECTIONS_PER_SECOND = 2

#: 各阶段接受的请求类（引擎按请求派生 start、resume、conclude、conclude_pending、other）：
#: conclude 指结束能结束的活动（进行中、暂停中或待收尾），conclude_pending 指只点名待收尾活动（全部或其中几项；开放
#: 结束的阶段同样接受它），受限轮的收尾因此不会被业务拒绝；收尾阶段一直持续到没有待收尾活动。resume_or_conclude 与
#: conclude_only 留在阶段闭集与本表中，现行阶梯不再进入它们（docs/design/04-simulation-engine.md）。
_STAGE_REQUEST_KINDS = {
    SameMomentStage.OPEN: frozenset({"start", "resume", "conclude", "conclude_pending", "other"}),
    SameMomentStage.CONCLUDE_PENDING: frozenset({"conclude_pending"}),
    SameMomentStage.START_OR_RESUME: frozenset({"start", "resume"}),
    SameMomentStage.RESUME_OR_CONCLUDE: frozenset({"resume", "conclude", "conclude_pending"}),
    SameMomentStage.CONCLUDE_ONLY: frozenset({"conclude", "conclude_pending"}),
}

#: 达到同刻上限后至多再给的受限轮数（规则 3）；用完仍要同一秒再决策即跨决策预算耗尽。
OPEN_ROUNDS = 3
#: 开放轮之后的受限轮数。
RESTRICTED_ROUNDS = 3
#: 跨决策预算耗尽时写入的 environment_event 种类（docs/design/06-agent.md）。
BUDGET_EXHAUSTED_KIND = "decision_budget_exhausted"


@dataclass(frozen=True, slots=True)
class RoundOutcome:
    """@brief 一轮结算后的同刻排期输入。

    :param now: 当前模拟时刻（epoch 秒）
    :param agent: 已计入本轮的人物状态
    :param active: 本轮结算后人物是否有进行中活动
    :param rejected: 本轮是否为业务拒绝（重放按存档结果）
    :param limit: 场景的同刻决策上限
    :param pending: 本轮结算后人物是否有待收尾活动
    """

    now: int
    agent: AgentState
    active: bool
    rejected: bool
    pending: bool = False


def count_round(agent: AgentState) -> AgentState:
    """@brief 受理、业务拒绝与重放各使本秒的同刻轮数加一（第一章「实现约定」）；免计的名称类拒绝不经这里
    （rejection_counts）。"""
    return agent.model_copy(update={"same_moment_rounds": agent.same_moment_rounds + 1}, deep=True)


def free_rejection_flags(codes: Sequence[str | None]) -> list[bool]:
    """@brief 同一人物同一模拟秒内按结算先后的业务拒绝码逐个是否免计（docs/design/04-simulation-engine.md）：名称
    类拒绝码
    的前 FREE_NAME_REJECTIONS_PER_SECOND 次免计（不计同刻轮数、不施加情绪脉冲），此后的名称类拒绝与其余拒绝一样照常。"""
    flags, seen = [], 0
    for code in codes:
        named = code in NAME_REJECTION_CODES
        flags.append(named and seen < FREE_NAME_REJECTIONS_PER_SECOND)
        seen += named
    return flags


def rejection_counts(state: WorldState, agent_id: int, error_code: str) -> bool:
    """@brief 这次业务拒绝是否计入同刻轮数并照常施加情绪脉冲：本人本秒此前已结算的拒绝（依据已存的动作结果）之后，它是
    本秒第 3 次起的名称类拒绝或不是名称类拒绝时计入。"""
    return not free_rejection_flags([*_second_rejections(state, agent_id), error_code])[-1]


def rejection_pulse(state: WorldState, agent_id: int, error_code: str) -> str | None:
    """@brief 这次业务拒绝施加的情绪脉冲键：免计的名称类拒绝不施加；其余按 refusal_pulse_key，其中 service_refused 每人
    每秒至多一次（「拒绝的情绪代价与重估间隔」）。"""
    if not rejection_counts(state, agent_id, error_code):
        return None
    key = refusal_pulse_key(error_code)
    return None if key == SERVICE_REFUSED and refusal_pulse_applied(state, agent_id) else key


def round_wake_reasons(active: bool, rejected: bool) -> list[str]:
    """@brief 同刻再决策轮的本轮唤醒原因（规则 6）：没有进行中活动记 no_active_activity，业务拒绝记 rejection_feedback。

    两者同时成立时按 WakeReason 枚举顺序记两项；上一轮原因不带入。
    """
    hits = set()
    if rejected:
        hits.add("rejection_feedback")
    if not active:
        hits.add("no_active_activity")
    return [trigger.name for trigger in WAKE_TRIGGERS if trigger.name in hits]


def next_stage(outcome: RoundOutcome) -> SameMomentStage:
    """@brief 本轮之后的受限轮阶段（规则 3）。

    未达上限保持 open；达到上限后有待收尾活动只开放收尾（conclude_pending）；有进行中活动且没有待收尾时不再进入受限轮，
    阶段保持不变直到时钟推进复位；否则（没有进行中与待收尾的活动，两个槽位都空着）一律开始或恢复（start_or_resume），
    开始或恢复被拒后留在这一阶段。
    """
    agent = outcome.agent
    if agent.same_moment_rounds < OPEN_ROUNDS:
        return SameMomentStage.OPEN
    if outcome.pending:
        return SameMomentStage.CONCLUDE_PENDING
    if outcome.active:
        return agent.same_moment_stage
    return SameMomentStage.START_OR_RESUME


def schedule_after_round(outcome: RoundOutcome,
                         rhythm_next: Callable[[], int]) -> tuple[int, list[str], SameMomentStage]:
    """@brief 安排本轮之后的下一次决策，返回 (next_decision_at, pending_wake_reasons, same_moment_stage)。

    没有进行中活动、有待收尾活动，或有进行中活动而业务拒绝且未达上限：同一秒再决策，原因按本轮替换（规则 2、4、6）；
    其余情形（有进行中活动且无待收尾时的成功，或达到上限的拒绝）按决策节律，只在此时调用 rhythm_next，原因清空。
    """
    stage = next_stage(outcome)
    below_limit = outcome.agent.same_moment_rounds < OPEN_ROUNDS
    if not outcome.active or outcome.pending or outcome.rejected and below_limit:
        return outcome.now, round_wake_reasons(outcome.active, outcome.rejected), stage
    return rhythm_next(), [], stage


def budget_exhausted(outcome: RoundOutcome) -> bool:
    """@brief 跨决策预算是否耗尽：本秒轮数已达上限加三（受限轮用完），本轮之后仍没有进行中活动或仍有待收尾活动。

    单次决策的回合预算（宿主的 ReAct 回合）与此分开计（docs/design/06-agent.md「宿主模型协议」）。
    """
    used_up = outcome.agent.same_moment_rounds >= OPEN_ROUNDS + RESTRICTED_ROUNDS
    return used_up and (not outcome.active or outcome.pending)


def require_budget_available(state: WorldState) -> None:
    """@brief 检查当前及恢复后的真实预算，故障后不能再决策或推进。"""
    for agent in state.agents.values():
        outcome = RoundOutcome(
            now=state.current_time, agent=agent, active=bool(active_activities(state, agent.agent_id)),
            rejected=False, pending=any(item.agent_id == agent.agent_id and item.status in CONCLUDABLE_TERMINAL_STATUSES
                        and item.concluded_at is None for item in state.activities.values()),
        )
        if budget_exhausted(outcome):
            logging.error("Decision budget exhausted at world time %s", state.current_time)
            raise ProtocolError("decision_budget_exhausted")


def exhausted_event(outcome: RoundOutcome, error_code: str | None) -> EventDraft:
    """@brief 记录真实预算故障，不创建下一次决策或跳过模拟时间。"""
    return EventDraft(
        event_type="environment_event", occurred_at=outcome.now, agent_id=outcome.agent.agent_id,
        visibility="system", source_kind="environment",
        data={"kind": BUDGET_EXHAUSTED_KIND, "error_code": error_code},
    )


def request_kind(tool_name: str, named: Sequence[ActivityState], pending_ids: frozenset[str]) -> str:
    """@brief 受限轮复核的请求类（引擎解析名称与对象后传入，宿主收窄与引擎复核用同一口径）。

    :param tool_name: 世界请求工具名；两个开始工具的请求类都是 start。
    :param named: report_status、conclude_activity 点名的权威活动（其他工具为空）。
    :param pending_ids: 人物当前全部待收尾活动身份。
    :returns: start（开始持续活动）、resume（只恢复暂停活动）、
              conclude_pending（只点名待收尾活动，全部或其中几项）、conclude（只结束能结束的活动）或 other。
    """
    if tool_name in START_TOOLS:
        return "start"
    if tool_name == "report_status":
        return "resume" if named and all(item.status == ActivityStatus.PAUSED for item in named) else "other"
    if tool_name == "conclude_activity":
        if named and {item.activity_id for item in named} <= pending_ids:
            return "conclude_pending"
        allowed = named and all(item.kind != "sleep" or item.status != ActivityStatus.ACTIVE for item in named)
        return "conclude" if allowed else "other"
    return "other"


def stage_refusal(stage: SameMomentStage, request_kind: str) -> Refusal | None:
    """@brief 受限轮复核的判定本身：请求类不属于本阶段开放的动作类时返回 SAME_MOMENT_ACTION_RESTRICTED，否则 None。

    工具层的可用性说明与受理共用这一判定，所以说明与拒绝原因一字不差（docs/design/06-agent.md「提示与工具」）。

    :param stage: 人物当前的受限轮阶段
    :param request_kind: 引擎派生的请求类：start（开始持续活动）、resume（恢复暂停活动）、
                         conclude（结束能结束的活动）、conclude_pending（只点名待收尾活动）、other
    """
    if request_kind in _STAGE_REQUEST_KINDS.get(stage, frozenset()):
        return None
    return refusal_for_code("SAME_MOMENT_ACTION_RESTRICTED")


def require_stage_action(stage: SameMomentStage, request_kind: str) -> None:
    """@brief 受限轮复核（第一章「实现约定」）：请求类不属于本阶段开放的动作类即业务拒绝。

    :raises PreconditionError: SAME_MOMENT_ACTION_RESTRICTED（stage_refusal 的原因），计入同刻轮数
    """
    refusal = stage_refusal(stage, request_kind)
    if refusal is not None:
        raise refusal.error()


def refusal_pulse_key(error_code: str) -> str | None:
    """@brief 业务拒绝码对应的情绪脉冲键：品类配对说明不施加，其余记 service_refused；免计的
    名称类拒绝（3.4）是否施加由 rejection_pulse 判定。"""
    return SERVICE_REFUSED


def _second_rejections(state: WorldState, agent_id: int) -> list[str | None]:
    """@brief 本人在当前模拟秒已结算的业务拒绝码（依据已存的动作结果，按结算先后；重放不另存结果）。

    按组计：同一决策的两条开始动作被拒时内容相同，只取一次。
    """
    codes: dict[str, str | None] = {}
    for stored in state.processed_actions.values():
        result = stored.result
        if (result is not None and result.get("agent_id") == agent_id
                and result.get("occurred_at") == state.current_time
                and result.get("status") == ActionStatus.REJECTED.value):
            codes.setdefault(stored.receipt["decision_id"], result.get("error_code"))
    return list(codes.values())


def latest_rejection_code(state: WorldState, agent_id: int) -> str | None:
    """@brief 本人在当前模拟秒最近一次计入同刻轮数的业务拒绝的错误码（依据已存的动作结果，按结算先后；免计的名称类拒绝
    不算，3.4），没有时为 None。"""
    codes = _second_rejections(state, agent_id)
    counted = [code for code, free in zip(codes, free_rejection_flags(codes)) if not free]
    return counted[-1] if counted else None


def refusal_pulse_applied(state: WorldState, agent_id: int) -> bool:
    """@brief 本人在当前模拟秒是否已施加过 service_refused 脉冲（「拒绝的情绪代价与重估间隔」：每人每秒至多一次）。

    依据已存的动作结果判定：本人、发生于当前时刻、状态为拒绝、不是免计的名称类拒绝且错误码的脉冲键为 service_refused；
    不另设世界字段。
    """
    codes = _second_rejections(state, agent_id)
    return any(not free and refusal_pulse_key(str(code)) == SERVICE_REFUSED
               for code, free in zip(codes, free_rejection_flags(codes)))


def reset_same_moment(state: WorldState) -> WorldState:
    """@brief 时钟推进时每个人物的同刻轮数归零、受限轮阶段复位为 open（第一章「实现约定」）。"""
    agents = {agent_id: agent.model_copy(update={"same_moment_rounds": 0,
                                                 "same_moment_stage": SameMomentStage.OPEN}, deep=True)
              for agent_id, agent in state.agents.items()}
    return state.model_copy(update={"agents": agents}, deep=True)
