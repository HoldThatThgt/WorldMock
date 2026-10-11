"""@brief 业务拒绝的公开原因、改法与下一步（docs/design/06-agent.md「提示与工具」，
docs/design/04-simulation-engine.md「动作、名称与并行」）。

拒绝结果的 data.reason（一句原因）、data.fix（怎样改这次请求，没有可照做的改法时为 null）、data.next_step（世界里下一步
做什么：可读的活动名、地点名或做法，不含内部身份，没有时为 null）与逐项明细 data.items 由本模块从拒绝确定性生成：
引导性拒绝在抛出处用 guided() 或 Refusal.error() 写明原因与下一步（或以可读名称写 suggested），原因记在错误的
public_reason 上，改法与逐项明细写进 suggested；其余错误码按码取默认原因与改法，从不取链式底层异常的文字（其中可能是
请求模型的校验转储）。源码中会抛出的每个业务拒绝码都有具体的默认原因，行为由跨模块错误合同测试验证。
结果 message 就是 reason；这些键经公开结果投影进入进度层 tool_call.result.details（docs/design/06-agent.md
「进度、待办与记忆」）。工具层的可用性说明取同一原因：只有错误码的拒绝经 refusal_for_code 取得与拒绝一字不差的 Refusal。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from worldmock.errors import BusinessRuleError, PreconditionError
from worldmock.life.error_contract import ErrorItem, items_fix

#: 抛出处没有写明原因时按错误码取的原因（只写世界事实，不含内部身份）。
_DEFAULT_REASONS = {
    'ACTIVITY_DURATION_INVALID': 'The derived activity duration is outside the allowed range.',
    'ACTION_REQUEST_INVALID': 'The requested object or action does not satisfy this tool contract.',
    'ROUTE_DURATION_INVALID': 'The route duration exceeds the maximum travel duration.',
    'ACTIVITY_TOO_SHORT': 'An activity must last at least one minute.',
    'INSUFFICIENT_FUNDS': "The caller's available balance is below the actual purchase total.",
    'INSUFFICIENT_TRAVEL_FUNDS': "The caller's available balance is below the route fare.",
    'SAME_MOMENT_ACTION_RESTRICTED': 'This same-moment stage only accepts its offered action types.',
    'TASK_NOT_FOUND': 'No known task, goal or activity uniquely matches this name.',
    'SUPPLY_NOT_FOUND': 'No goods exist for the written name; changing spelling cannot create goods.',
    'ACTIVITY_NOT_FOUND': 'No known activity matches the written activity name.',
    'ACTIVITY_NOT_OWNED': 'The named activity belongs to another person.',
    'ACTIVITY_NOT_ACTIVE': 'Only an active activity can be paused.',
    'ACTIVITY_NOT_PAUSED': 'Only a paused activity can be resumed.',
    'ACTIVITY_ALREADY_TERMINAL': 'This activity has already ended.',
    'ACTIVITY_FEEDBACK_NOT_DUE': 'The current time has not reached the next decision boundary.',
    'ACTIVITY_CONCLUSION_REQUIRED': 'Conclude the finished activities before starting another action.',
    'ACTIVITY_PAUSE_UNAVAILABLE': 'This activity cannot be paused at its current phase or location.',
    'ACTIVITY_PRECONDITION_UNMET': 'The requested activity has unmet world prerequisites.',
    'SESSION_LIMIT_EXCEEDED': 'Active and pending-conclusion activities occupy both available slots.',
    'RESOURCE_CONFLICT': 'The requested activity conflicts with an occupied resource.',
    'ROUTE_DURATION_MISMATCH': 'The requested travel duration differs from the published graph.',
    'ORDER_NOT_FOUND': 'The order is unknown.',
    'ORDER_NOT_PAID': 'The order has not been paid.',
    'LOCATION_CLOSED': 'The location is closed at the current time.',
    'DELIVERY_NOT_DUE': 'The order has not reached its delivery time.',
    'DESTINATION_NOT_FOUND': 'The receiving place does not exist.',
    'EXCHANGE_INPUT_INVALID': 'The exchange input quantity is invalid.',
    'EXCHANGE_RESULT_INVALID': 'The exchange result is not a finite valid number.',
    'OUT_OF_BOUNDS': 'The quantity is outside its declared resource bounds.',
    'NEGATIVE_BALANCE': 'The available resource quantity cannot cover this exchange.',
    'UNKNOWN_RESOURCE': 'The resource is not declared.',
    'AMOUNT_NOT_POSITIVE': 'The exchange amount must be positive.',
    'CONVERT_IMBALANCED': 'The conversion does not balance its declared input and output.',
    'CAUSE_REQUIRED': 'The exchange requires an explicit cause.',
    'TIMESTAMP_NOT_INTEGER': 'The exchange timestamp must be an integer.',
    'FLOW_ON_CLOSED_DOMAIN': 'This resource cannot be created or destroyed in its closed domain.',
    'TRANSFER_NOT_CLOSED_DOMAIN': 'This resource cannot be transferred across people.',
    'TRANSFER_UNBALANCED': 'The transferred input and output amounts do not balance.',
    'PLAN_NOT_FOUND': 'No day plan exists for the current day.',
    'PLAN_REMIND_TIME_INVALID': 'The reminder must be after now and before the next local midnight.',
    'PLAN_GOALS_INVALID': 'The revised plan must preserve unique intents and contain at most eight goals.',
    'OBSERVATION_NOT_FOUND': 'The decision has no unique frozen observation.',
    'CONTENT_PREREQUISITE_UNMET': 'No content of this app can start at the current place and time.',
    'PARCEL_NOT_READY': 'No parcels are ready for you at this station.',
}

#: 按错误码给的改法：怎样改这次请求（世界条件造成的拒绝没有改法，只看下一步）。
_DEFAULT_FIXES = {
    'ACTION_REQUEST_INVALID': 'Use the published exact object name and offered action.',
    'ACTIVITY_NOT_FOUND': 'Use an exact known activity name.',
    'ACTIVITY_NOT_OWNED': "Name only the caller's activities.",
    'ACTIVITY_NOT_ACTIVE': 'Pause only an active activity.',
    'ACTIVITY_NOT_PAUSED': 'Resume only a paused activity.',
    'ACTIVITY_ALREADY_TERMINAL': 'Do not operate on an activity that has already ended.',
    'ACTIVITY_CONCLUSION_REQUIRED': 'Conclude the finished activity first.',
    'ACTIVITY_PAUSE_UNAVAILABLE': 'Conclude this activity instead.',
    'SUPPLY_NOT_FOUND': 'Use an exact known goods name.',
    'DESTINATION_NOT_FOUND': 'Use an exact known location name.',
    'TASK_NOT_FOUND': 'Use an exact known task, goal or activity name.',
    'PLAN_REMIND_TIME_INVALID': 'Use a local HH:MM after now and before midnight, or null.',
    'PLAN_GOALS_INVALID': 'Use unique intents and at most eight goals including preserved goals.',
}

#: 抛出处没有写下一步时按错误码给的做法。
_DEFAULT_NEXT_STEPS = {
    'SAME_MOMENT_ACTION_RESTRICTED': 'Choose an action offered in this stage.',
    'ACTION_REQUEST_INVALID': 'Choose a known object and an offered action.',
    'SUPPLY_NOT_FOUND': 'Choose a known product or candidate request.',
    'CONTENT_PREREQUISITE_UNMET': 'Choose another action.',
}

#: 原样进入公开结果的可读建议：逐项明细（带可照填候选）、名称归一与附带说明（都不含内部身份）。
_PUBLIC_SUGGESTIONS = ("items", "resolved", "notes")


@dataclass(frozen=True, slots=True)
class Refusal:
    """@brief 一条不成立的准入：错误码、一句原因、可读的下一步、改法、逐项明细与私有的未满足条件和建议（不抛出，供说明
    复用）。

    :param suggested: 私有 data.suggested 的可读建议（例如 {"conclude_or_pause": [活动名]}）；省略时写下一步。
    :param fix: 怎样改这次请求；空串时取该码的默认改法。
    :param items: 逐项明细（第四章 items[]），写进 suggested 后公开。
    """

    code: str
    reason: str
    next_step: str = ""
    unmet: tuple[str, ...] = ()
    suggested: dict | None = None
    fix: str = ""
    items: tuple[ErrorItem, ...] = ()

    def error(self) -> PreconditionError:
        """@brief 换成可抛出的业务拒绝：原因写在 public_reason 上（也作链式底层原因），建议或下一步、改法与逐项明细写进
        suggested。"""
        suggested = dict(self.suggested) if self.suggested is not None else (
            {"next_step": self.next_step} if self.next_step else {})
        if self.fix:
            suggested["fix"] = self.fix
        if self.items:
            suggested["items"] = [item.payload() for item in self.items]
        error = PreconditionError(self.code, unmet=list(self.unmet), suggested=suggested or None,
                                  public_reason=self.reason)
        error.__cause__ = ValueError(self.reason)
        return error


def default_reason(code: str) -> str:
    """@brief 错误码必须有已登记的原因；未知码记录错误并拒绝。"""
    if code not in _DEFAULT_REASONS:
        logging.error("Business rejection code has no declared reason: %s", code)
        raise ValueError(f"business rejection code has no declared reason: {code}")
    return _DEFAULT_REASONS[code]


def guided(code: str, reason: str, next_step: str = "", unmet: Sequence[str] = (), fix: str = "") -> PreconditionError:
    """@brief 构造带公开原因与下一步的业务拒绝：原因写在 public_reason 上，下一步与改法写进 suggested（可读，不含身份）。

    :param code: 业务错误码（接口合同登记的闭集）。
    :param reason: 一句原因。
    :param next_step: 可读的下一步（要先处理的活动、要去的地点或做法），没有时为空串。
    :param unmet: 未满足条件名称，写进私有 data.unmet。
    :param fix: 怎样改这次请求；空串时取该码的默认改法。
    """
    return Refusal(code, reason, next_step, tuple(unmet), fix=fix).error()


def refusal_for_code(code: str) -> Refusal:
    """@brief 只有错误码的业务拒绝对应的 Refusal：原因、下一步与改法取该码的默认说法，与 rejection_guidance 一字不差。

    :param code: 业务错误码（接口合同登记的闭集）。
    """
    return Refusal(code, default_reason(code), _DEFAULT_NEXT_STEPS.get(code, ""), fix=_DEFAULT_FIXES.get(code, ""))


def refusal_from_error(error: BusinessRuleError) -> Refusal:
    """@brief 已抛出的业务拒绝换回 Refusal：原因、下一步与改法取 rejection_guidance 的同一说法，未满足条件照带。"""
    guidance = rejection_guidance(error)
    return Refusal(str(error), guidance["reason"], guidance["next_step"] or "",
                   tuple(getattr(error, "unmet", ()) or ()), fix=guidance["fix"] or "")


def items_refusal(entries: Sequence[tuple[ErrorItem, str]], total: int) -> Refusal:
    """@brief 逐项的名称类拒绝合并成一次（docs/design/04-simulation-engine.md）：码取第一项的码；原因逐项写
    「第 N 项「原文」：该项原因」（请求只有一项时不写序号），以「；」相连；改法按码写「第 N 项<该码的默认改法>」；下一步
    取第一项的码的默认做法。

    :param entries: （明细，该项的一句原因），按请求中的先后排列。
    :param total: 请求的项数（决定原因与改法是否写序号）。
    """
    items = tuple(item for item, _ in entries)
    reason = "; ".join(sentence for _, sentence in entries)
    first = items[0].error_code
    return Refusal(first, reason, _DEFAULT_NEXT_STEPS.get(first, ""),
                   fix=items_fix(items, total, _DEFAULT_FIXES) or "", items=items)


def rejection_guidance(error: BusinessRuleError) -> dict:
    """@brief 从一次业务拒绝确定性生成公开的 reason、next_step 与 fix，另带逐项明细 items、
    名称归一 resolved 与说明 notes（有才写）。

    原因只取 guided() 或 Refusal.error() 写明的 public_reason，否则按错误码取默认原因（不读链式底层异常）；下一步取
    suggested 中的可读下一步、要去的地点或要先处理的活动名，否则按错误码取默认做法；改法取 suggested 中写明的，否则按
    错误码取默认改法。下一步与改法没有时为 None（公开结果写 null，docs/design/06-agent.md「提示与工具」：允许为空
    的写 null，不填编造的值）。逐项明细里的候选是可照填的请求 {target, action}（至多 3 个，
    docs/design/04-simulation-engine.md）。
    """
    code = str(error)
    suggested = getattr(error, "suggested", None) or {}
    guidance = {"reason": getattr(error, "public_reason", None) or default_reason(code),
                "next_step": _next_step(suggested) or _DEFAULT_NEXT_STEPS.get(code) or None,
                "fix": suggested.get("fix") or _DEFAULT_FIXES.get(code) or None}
    guidance.update({key: list(suggested[key]) for key in _PUBLIC_SUGGESTIONS if suggested.get(key)})
    return guidance


def _next_step(suggested: dict) -> str:
    """@brief 把可读的建议写成一句下一步：直接写明的下一步、要去的地点或要先结束或暂停的活动。"""
    if suggested.get("next_step"):
        return str(suggested["next_step"])
    if suggested.get("conclude_or_pause"):
        return f"Conclude or pause {', '.join(suggested['conclude_or_pause'])} first."
    return ""
