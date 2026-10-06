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
    'APP_NOT_BOUND': 'This store cannot be ordered through that app.',
    'ORDER_DUPLICATE_ITEM': 'The same food from the same store appears twice in one order.',
    'ACTIVITY_DURATION_INVALID': 'The derived activity duration is outside the allowed range.',
    'ACTION_REQUEST_INVALID': 'The requested object or way does not satisfy this tool contract.',
    'INTERACTION_MENU_DENIED': 'The current location does not admit this action.',
    'ROUTE_DURATION_INVALID': 'The route duration exceeds the maximum travel duration.',
    'ACTIVITY_TOO_SHORT': 'An activity must last at least one minute.',
    'PRODUCT_NOT_HELD': 'The caller has no available quantity of this object at the required location.',
    'PRODUCT_USE_LOCATION_REQUIRED': 'This object can only be used at its inventory location.',
    'FULFILLMENT_NOT_SUPPORTED': 'This store has no delivery app.',
    'INSUFFICIENT_FUNDS': "The caller's available balance is below the actual purchase total.",
    'INSUFFICIENT_TRAVEL_FUNDS': "The caller's available balance is below the route fare.",
    'CHAT_PARTNER_NOT_AVAILABLE': "The conversation partner is not available at the caller's place.",
    'CHAT_ALREADY_ACTIVE': 'A conversation with this partner is already active.',
    'SOCIAL_ACTIVITY_CANNOT_PAUSE': 'Conversations and calls cannot be paused.',
    'RECIPIENT_NOT_FOUND': 'The recipient is not a known other person; use null for the caller.',
    'MESSAGE_TEXT_TOO_LONG': 'A message must contain at most 300 characters.',
    'WORK_INCOMPLETE': 'The work has not reached its required effective progress.',
    'WORK_REQUIRES_REWORK': 'The submitted work needs revision before acceptance.',
    'WORK_TASK_IN_PROGRESS': 'End the running work activity before submitting its result.',
    'WORK_TASK_ALREADY_PAID': 'This work task has already been paid.',
    'WORK_SUBMISSION_LATE': 'The work submission deadline has passed.',
    'ELIGIBILITY_LOCATION_KIND_MISMATCH': 'The current location category does not admit this activity.',
    'SAME_MOMENT_ACTION_RESTRICTED': 'This same-moment stage only accepts its offered action types.',
    'TASK_NOT_FOUND': 'No known task, goal or activity uniquely matches this name.',
    'SUPPLY_NOT_FOUND': 'No food exists for the written name; changing spelling cannot create a food.',
    'SUPPLY_NOT_AT_STORE': 'The named source does not sell this food.',
    'SUPPLY_SOURCE_MISMATCH': 'The source differs from the store named in the target.',
    'SOURCE_NOT_FOUND': 'The source is not a known store or app name.',
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
    'SOCIAL_ACTIVITY_OPERATION_UNKNOWN': 'This operation is not allowed for conversations or calls.',
    'CONTACT_NOT_FOUND': 'The named contact is unknown.',
    'MESSAGE_NOT_VISIBLE': 'The mail has not arrived for this person.',
    'APP_CONTENT_EMPTY': 'This app has no content to use.',
    'LIFESTYLE_ACTIVITY_NOT_FOUND': 'The named lifestyle activity is unknown.',
    'ROUTE_DURATION_MISMATCH': 'The requested travel duration differs from the published graph.',
    'WORK_TASK_NOT_FOUND': 'The named work task is unknown.',
    'WORK_TASK_NOT_VISIBLE': 'The work task has not been disclosed to this person.',
    'WORK_TASK_NOT_AVAILABLE': 'The work task has not opened or its contract window has ended.',
    'WORK_DURATION_EXCEEDS_CONTRACT': 'The accumulated work duration would exceed the contract.',
    'WORK_WOULD_MISS_DEADLINE': 'The derived work duration would pass the submission deadline.',
    'EMPLOYER_FUNDS_INSUFFICIENT': "The employer's balance cannot cover the contractual payment.",
    'ORDER_ITEM_COUNT_OUT_OF_RANGE': 'An order requires one to five items.',
    'ORDER_LOCATION_MISMATCH': 'An order must use one store location.',
    'ORDER_NOT_FOUND': 'The order is unknown.',
    'DELIVERY_NOT_DUE': 'The order has not reached its delivery time.',
    'DESTINATION_NOT_FOUND': 'The receiving place does not exist.',
    'PICKUP_LOCATION_REQUIRED': 'Pickup requires the caller to be at the store.',
    'PRODUCT_NOT_FOUND': 'The product is unknown.',
    'PRODUCT_USE_MODE_NOT_ALLOWED': 'The product does not support the requested use way.',
    'PRODUCT_USE_DURATION_MISMATCH': 'The derived use duration does not match the product contract.',
    'MEAL_DURATION_MUST_BE_LONGEST': 'The meal duration must equal the longest portion consumption duration.',
    'MEAL_ITEM_COUNT_OUT_OF_RANGE': 'A meal requires one to three food items.',
    'NON_FOOD_USE_SINGLE_ITEM': 'Non-food use requires exactly one item.',
    'EXCHANGE_INPUT_INVALID': 'The exchange input quantity is invalid.',
    'EXCHANGE_RESULT_INVALID': 'The exchange result is not a finite valid number.',
    'EXCHANGE_PIECE_COUNT_MISSING': 'The exchange requires a piece count.',
    'OUT_OF_BOUNDS': 'The quantity is outside its declared resource bounds.',
    'NEGATIVE_BALANCE': 'The available inventory quantity cannot cover this exchange.',
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
}

#: 按错误码给的改法：怎样改这次请求（世界条件造成的拒绝没有改法，只看下一步）。
_DEFAULT_FIXES = {
    'ORDER_DUPLICATE_ITEM': 'Keep one line for that food and combine the quantity.',
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
    'SOCIAL_ACTIVITY_OPERATION_UNKNOWN': 'Conclude the conversation or call.',
    'SOCIAL_PARTNER_NOT_FOUND': 'Use an exact known person name.',
    'CONTACT_NOT_FOUND': 'Use an exact known contact name.',
    'RECIPIENT_NOT_FOUND': 'Use null for the caller, or the exact name of a known other person.',
    'MESSAGE_TEXT_TOO_LONG': 'Shorten the message to at most 300 characters.',
    'MESSAGE_NOT_VISIBLE': 'Select a mail message that has arrived.',
    'LIFESTYLE_ACTIVITY_NOT_FOUND': 'Use an exact published lifestyle activity name.',
    'WORK_TASK_NOT_FOUND': 'Use an exact known work task name.',
    'WORK_TASK_NOT_VISIBLE': 'Select a disclosed work task.',
    'FULFILLMENT_NOT_SUPPORTED': 'Use an offered purchase way or 购买.',
    'SUPPLY_NOT_FOUND': 'Use an exact known food name.',
    'SUPPLY_NOT_AT_STORE': 'Set from to null or a store that sells this product.',
    'SUPPLY_SOURCE_MISMATCH': 'Use the source named by the target and its supported way.',
    'SOURCE_NOT_FOUND': 'Use an exact known store or app name.',
    'ORDER_ITEM_COUNT_OUT_OF_RANGE': 'Submit one to five order items.',
    'ORDER_LOCATION_MISMATCH': 'Order from each store separately.',
    'DESTINATION_NOT_FOUND': 'Use null for the caller or a person with a receiving place.',
    'PRODUCT_NOT_FOUND': 'Use an exact known product name.',
    'PRODUCT_USE_MODE_NOT_ALLOWED': 'Use an offered way for this product.',
    'MEAL_ITEM_COUNT_OUT_OF_RANGE': 'Select one to three held food items.',
    'NON_FOOD_USE_SINGLE_ITEM': 'Select exactly one item.',
    'TASK_NOT_FOUND': 'Use an exact known task, goal or activity name.',
    'PLAN_REMIND_TIME_INVALID': 'Use a local HH:MM after now and before midnight, or null.',
    'PLAN_GOAL_ALREADY_PROGRESSED': 'Submit only intents that have not progressed today.',
    'PLAN_GOALS_INVALID': 'Use unique intents and at most eight goals including preserved goals.',
}

#: 抛出处没有写下一步时按错误码给的做法。
_DEFAULT_NEXT_STEPS = {
    'APP_CONTENT_EMPTY': 'Choose another app.',
    'WORK_REQUIRES_REWORK': 'Revise the work according to the review before resubmission.',
    'WORK_INCOMPLETE': 'Continue the work until its effective progress reaches the contract.',
    'WORK_SUBMISSION_LATE': 'Choose another action; the submission deadline has passed.',
    'SAME_MOMENT_ACTION_RESTRICTED': 'Choose an action offered in this stage.',
    'ACTION_REQUEST_INVALID': 'Choose a known object and an offered way.',
    'SOCIAL_ACTIVITY_CANNOT_PAUSE': 'Conclude the activity.',
    'SUPPLY_NOT_FOUND': 'Choose a known product or candidate request.',
    'SUPPLY_NOT_AT_STORE': 'Choose a store that sells this product.',
    'REQUEST_ALREADY_HANDLED': 'Wait for the existing order to arrive.',
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
