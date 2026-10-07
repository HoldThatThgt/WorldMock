"""@brief 业务拒绝的公开原因、改法与下一步（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第五章「动作照常列出，做错了再说明」，
docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要 第四章「错误合同」）。

拒绝结果的 data.reason（一句原因）、data.fix（怎样改这次请求，没有可照做的改法时为 null）、data.next_step（世界里下一步
做什么：可读的活动名、地点名或做法，不含内部身份，没有时为 null）与逐项明细 data.items 由本模块从拒绝确定性生成：
引导性拒绝在抛出处用 guided() 或 Refusal.error() 写明原因与下一步（或以可读名称写 suggested），原因记在错误的
public_reason 上，改法与逐项明细写进 suggested；其余错误码按码取默认原因与改法，从不取链式底层异常的文字（其中可能是
请求模型的校验转储）。源码中会抛出的每个业务拒绝码都有具体的默认原因（tests/life/test_error_contract.py 机械核对）。
结果 message 就是 reason；这些键经公开结果投影进入进度层 tool_call.result.details（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求
「进度层」）。工具层的可用性说明取同一原因：只有错误码的拒绝经 refusal_for_code 取得与拒绝一字不差的 Refusal。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from worldmock.errors import BusinessRuleError, PreconditionError
from worldmock.life.error_contract import ErrorItem, items_fix

#: 没有专门说明的业务拒绝的通用原因（兜底句；会被抛出的码都另有默认原因）。

#: 抛出处没有写明原因时按错误码取的原因（只写世界事实，不含内部身份）。
_DEFAULT_REASONS = {
    "ROUTE_FIELDS_INVALID": "The route fields do not match the way.",
    'APP_NOT_BOUND': 'This store cannot be ordered through that app.',
    'ORDER_DUPLICATE_ITEM': 'The same goods for the same recipient appear twice in one order.',
    'ACTIVITY_DURATION_INVALID': 'The derived activity duration is outside the allowed range.',
    'ACTION_REQUEST_INVALID': 'The requested object or way does not satisfy this tool contract.',
    'INTERACTION_MENU_DENIED': 'The current location does not admit this action.',
    'ROUTE_DURATION_INVALID': 'The route duration exceeds the maximum travel duration.',
    'ACTIVITY_TOO_SHORT': 'An activity must last at least one minute.',
    'INSUFFICIENT_FUNDS': "The caller's available balance is below the actual purchase total.",
    'INSUFFICIENT_TRAVEL_FUNDS': "The caller's available balance is below the route fare.",
    'CHAT_PARTNER_NOT_AVAILABLE': "The conversation partner is not available at the caller's place.",
    'CHAT_ALREADY_ACTIVE': 'A conversation with this partner is already active.',
    'SOCIAL_ACTIVITY_CANNOT_PAUSE': 'Conversations and calls cannot be paused.',
    'RECIPIENT_NOT_FOUND': 'The recipient is not a known other person; use null for the caller.',
    'MESSAGE_TEXT_TOO_LONG': 'A message must contain at most 300 characters.',
    'ELIGIBILITY_LOCATION_KIND_MISMATCH': 'The current location category does not admit this activity.',
    'SAME_MOMENT_ACTION_RESTRICTED': 'This same-moment stage only accepts its offered action types.',
    'TASK_NOT_FOUND': 'No known task, goal or activity uniquely matches this name.',
    'SUPPLY_NOT_FOUND': 'No goods exist for the written name; changing spelling cannot create goods.',
    'SUPPLY_NOT_AT_STORE': 'The named app does not sell these goods.',
    'REQUEST_ALREADY_HANDLED': 'An order already fulfills this message request.',
    'ACTION_NOT_SUPPORTED': 'This object does not support the requested action.',
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
    'SOCIAL_PARTNER_NOT_FOUND': 'The named conversation partner is unknown.',
    'CONTACT_NOT_FOUND': 'The named contact is unknown.',
    'ROUTE_DURATION_MISMATCH': 'The requested travel duration differs from the published graph.',
    'ORDER_ITEM_COUNT_OUT_OF_RANGE': 'An order requires one to five items.',
    'ORDER_LOCATION_MISMATCH': 'An order must use one store location.',
    'ORDER_NOT_FOUND': 'The order is unknown.',
    'DELIVERY_NOT_DUE': 'The order has not reached its delivery time.',
    'DESTINATION_NOT_FOUND': 'The receiving place does not exist.',
    'EXCHANGE_INPUT_INVALID': 'The exchange input quantity is invalid.',
    'EXCHANGE_RESULT_INVALID': 'The exchange result is not a finite valid number.',
    'EXCHANGE_PIECE_COUNT_MISSING': 'The exchange requires a piece count.',
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
    'PLAN_GOAL_ALREADY_PROGRESSED': 'This goal intent has already progressed today.',
    'PLAN_GOALS_INVALID': 'The revised plan must preserve unique intents and contain at most eight goals.',
    'OBSERVATION_NOT_FOUND': 'The decision has no unique frozen observation.',
    'APP_REQUIRED': 'This action is done through an app and none was named.',
    'APP_NOT_OFFERED': 'The named app is not offered for this target.',
    'PAYMENT_APP_REQUIRED': 'An order placed through a shopping app must name a payment app.',
    'PAYMENT_APP_NOT_BOUND': 'The named payment app cannot be used for this order.',
    'COUNTERPART_REQUIRED': 'This action needs the other person to be named.',
    'CONTENT_EXHAUSTED': 'Nothing is left to do for this action.',
    'CAR_REQUIRED': 'Driving requires a car owned by this persona.',
    'DRIVING_DAYS_EXHAUSTED': 'No driving days are available for this trip.',
    'FUEL_PURCHASE_INVALID': 'Fuel must be bought for yourself while present at a gas station.',
    'PARCEL_NOT_READY': 'No parcels are ready for you at this station.',
    'ALARM_TIME_INVALID': 'Alarm time must be a future whole second before the run ends.',
}

#: 按错误码给的改法：怎样改这次请求（世界条件造成的拒绝没有改法，只看下一步）。
_DEFAULT_FIXES = {
    "ROUTE_FIELDS_INVALID": "Correct app, to, pay_app and answering.",
    'ORDER_DUPLICATE_ITEM': 'Keep one line for that goods and recipient.',
    'ACTION_REQUEST_INVALID': 'Use the published exact object name and offered way.',
    'ACTION_NOT_SUPPORTED': "Use one of the object's offered ways.",
    'INTERACTION_MENU_DENIED': 'Use an action admitted at the current location.',
    'ACTIVITY_NOT_FOUND': 'Use an exact known activity name.',
    'ACTIVITY_NOT_OWNED': "Name only the caller's activities.",
    'ACTIVITY_NOT_ACTIVE': 'Pause only an active activity.',
    'ACTIVITY_NOT_PAUSED': 'Resume only a paused activity.',
    'ACTIVITY_ALREADY_TERMINAL': 'Do not operate on an activity that has already ended.',
    'ACTIVITY_CONCLUSION_REQUIRED': 'Conclude the finished activity first.',
    'ACTIVITY_PAUSE_UNAVAILABLE': 'Conclude this activity instead.',
    'SOCIAL_ACTIVITY_CANNOT_PAUSE': 'Conclude the conversation or call.',
    'SOCIAL_PARTNER_NOT_FOUND': 'Use an exact known person name.',
    'CONTACT_NOT_FOUND': 'Use an exact known contact name.',
    'RECIPIENT_NOT_FOUND': 'Use null for the caller, or the exact name of a known other person.',
    'MESSAGE_TEXT_TOO_LONG': 'Shorten the message to at most 300 characters.',
    'SUPPLY_NOT_FOUND': 'Use an exact known goods name.',
    'SUPPLY_NOT_AT_STORE': 'Set app to a shopping app that sells these goods, or buy them in a store.',
    'ORDER_ITEM_COUNT_OUT_OF_RANGE': 'Submit one or two order items.',
    'ORDER_LOCATION_MISMATCH': 'Order from each store separately.',
    'DESTINATION_NOT_FOUND': 'Use null for the caller or a person with a receiving place.',
    'TASK_NOT_FOUND': 'Use an exact known task, goal or activity name.',
    'PLAN_REMIND_TIME_INVALID': 'Use a local HH:MM after now and before midnight, or null.',
    'PLAN_GOAL_ALREADY_PROGRESSED': 'Submit only intents that have not progressed today.',
    'PLAN_GOALS_INVALID': 'Use unique intents and at most eight goals including preserved goals.',
    'APP_REQUIRED': 'Set app to one of the apps offered for this action.',
    'APP_NOT_OFFERED': 'Use one of the offered apps, or null where none is offered.',
    'PAYMENT_APP_REQUIRED': 'Set pay_app to a payment app offered by that shopping app.',
    'PAYMENT_APP_NOT_BOUND': 'Use one of the offered payment apps, or null for an in-store purchase.',
    'COUNTERPART_REQUIRED': 'Name a person who is here, or a contact when the action uses an app.',
}

#: 抛出处没有写下一步时按错误码给的做法。
_DEFAULT_NEXT_STEPS = {
    'SAME_MOMENT_ACTION_RESTRICTED': 'Choose an action offered in this stage.',
    'ACTION_REQUEST_INVALID': 'Choose a known object and an offered way.',
    'SOCIAL_ACTIVITY_CANNOT_PAUSE': 'Conclude the activity.',
    'SUPPLY_NOT_FOUND': 'Choose a known product or candidate request.',
    'REQUEST_ALREADY_HANDLED': 'Wait for the existing order to arrive.',
    'CONTENT_EXHAUSTED': 'Choose another action.',
}

#: 原样进入公开结果的可读建议：逐项明细（带可照填候选）、重复的代点请求已关联的订单、名称归一与附带说明（都不含内部身份）。
_PUBLIC_SUGGESTIONS = ("items", "linked_orders", "resolved", "notes")


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
    """@brief 错误码的默认原因；没有登记的码取兜底句（会被抛出的码都已登记，见模块说明）。"""
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


def items_refusal(entries: Sequence[tuple[ErrorItem, str]], total: int) -> Refusal:
    """@brief 逐项的名称类拒绝合并成一次（docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要 第四章）：码取第一项的码；原因逐项写
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
    """@brief 从一次业务拒绝确定性生成公开的 reason、next_step 与 fix，另带逐项明细 items、已关联的订单 linked_orders、
    名称归一 resolved 与说明 notes（有才写）。

    原因只取 guided() 或 Refusal.error() 写明的 public_reason，否则按错误码取默认原因（不读链式底层异常）；下一步取
    suggested 中的可读下一步、要去的地点或要先处理的活动名，否则按错误码取默认做法；改法取 suggested 中写明的，否则按
    错误码取默认改法。下一步与改法没有时为 None（公开结果写 null，docs/dev/SPEC-life-realism.md#persona-prompt-七层请求「通用格式」：允许为空
    的写 null，不填编造的值）。逐项明细里的候选是可照填的请求 {target, action, way, from, to}（至多 3 个，
    docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要 第四章）。
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
    if suggested.get("travel_to"):
        return f"Travel to {suggested['travel_to']}."
    if suggested.get("conclude_or_pause"):
        return f"Conclude or pause {', '.join(suggested['conclude_or_pause'])} first."
    return ""
