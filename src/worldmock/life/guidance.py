"""@brief 业务拒绝的公开原因、改法与下一步（docs/dev/SPEC-time-and-energy.md 第五章「动作照常列出，做错了再说明」，
docs/dev/SPEC-dayrun-fidelity.md 第四章「错误合同」）。

拒绝结果的 data.reason（一句原因）、data.fix（怎样改这次请求，没有可照做的改法时为 null）、data.next_step（世界里下一步
做什么：可读的活动名、地点名或做法，不含内部身份，没有时为 null）与逐项明细 data.items 由本模块从拒绝确定性生成：
引导性拒绝在抛出处用 guided() 或 Refusal.error() 写明原因与下一步（或以可读名称写 suggested），原因记在错误的
public_reason 上，改法与逐项明细写进 suggested；其余错误码按码取默认原因与改法，从不取链式底层异常的文字（其中可能是
请求模型的校验转储）。源码中会抛出的每个业务拒绝码都有具体的默认原因（tests/life/test_error_contract.py 机械核对）。
结果 message 就是 reason；这些键经公开结果投影进入进度层 tool_call.result.details（docs/dev/SPEC-persona-prompt.md
「进度层」）。工具层的可用性说明取同一原因：只有错误码的拒绝经 refusal_for_code 取得与拒绝一字不差的 Refusal。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from worldmock.errors import BusinessRuleError, PreconditionError
from worldmock.life.error_contract import ErrorItem, items_fix, numbered

#: 没有专门说明的业务拒绝的通用原因（兜底句；会被抛出的码都另有默认原因）。
GENERIC_REASON = "这件事现在做不了"
#: 远程下单的送达地不在该供给的配送范围内（unmet=delivery_destination，docs/dev/SPEC-dayrun-fidelity.md 5.3）。
DELIVERY_DESTINATION_REASON = "这里不在这家店的配送范围内"
#: 送达地在配送范围内、门店到送达地的配送路线却缺失（未知或不可达，unmet=delivery_route）。
DELIVERY_ROUTE_REASON = "这家店到这里没有能走的配送路线"
#: 抛出处没有写明原因时按错误码取的原因（只写世界事实，不含内部身份）。
_DEFAULT_REASONS = {
    "ACTION_REQUEST_INVALID": "所选的对象或方式不在这一轮列出的范围内",
    "INTERACTION_MENU_DENIED": "这里不能这样做",
    "ACTIVITY_DURATION_INVALID": "按现在的情况算出的时长不合适，做不了",
    "ROUTE_DURATION_INVALID": "这段路太长，一次走不完",
    "ACTIVITY_TOO_SHORT": "时长不到一分钟，做不了",
    "PRODUCT_NOT_HELD": "手边没有这件东西",
    "PRODUCT_USE_LOCATION_REQUIRED": "这件东西只能在它所在的地方用",
    "FULFILLMENT_NOT_SUPPORTED": "这件商品不能用这种方式买",
    "DELIVERY_DESTINATION_NOT_SUPPORTED": DELIVERY_DESTINATION_REASON,
    "DELIVERY_ROUTE_NOT_FOUND": DELIVERY_ROUTE_REASON,
    "DELIVERY_ROUTE_UNREACHABLE": DELIVERY_ROUTE_REASON,
    "DELIVERY_ROUTE_INFORMATION_UNKNOWN": DELIVERY_ROUTE_REASON,
    "INSUFFICIENT_FUNDS": "钱不够",
    "INSUFFICIENT_TRAVEL_FUNDS": "钱不够付这段路费",
    "OUT_OF_STOCK": "卖完了",
    "MERCHANT_CLOSED": "店没开门",
    "CHAT_PARTNER_NOT_AVAILABLE": "对方现在不方便交谈",
    "CHAT_ALREADY_ACTIVE": "你们已经在交谈了",
    "SOCIAL_ACTIVITY_CANNOT_PAUSE": "交谈与通话不能暂停",
    "RECIPIENT_NOT_FOUND": "找不到这个收件人",
    "MESSAGE_TEXT_TOO_LONG": "消息太长了",
    "WORK_INCOMPLETE": "这项工作还没做够，不能提交",
    "WORK_REQUIRES_REWORK": "提交的成果还需要返工",
    "WORK_TASK_IN_PROGRESS": "先结束正在做的工作",
    "WORK_TASK_ALREADY_PAID": "这项工作已经结算过了",
    "WORK_SUBMISSION_LATE": "已经过了截止时间",
    "ELIGIBILITY_LOCATION_KIND_MISMATCH": "这里不适合做这件事",
    "SAME_MOMENT_ACTION_RESTRICTED": "已经想了好几轮，这一轮只能先定下一件事",
    "TASK_NOT_FOUND": "找不到这个名称",
    "SUPPLY_NOT_FOUND": "没有找到这个名字的商品",
    "SUPPLY_NAME_AMBIGUOUS": "好几家店都卖这个名字的商品",
    "REQUEST_ALREADY_HANDLED": "这条消息要的东西已经下过单了",
    # 活动的开始、控制与名额
    "ACTION_NOT_SUPPORTED": "这种做法这里不支持",
    "ACTIVITY_NOT_FOUND": "找不到这项活动",
    "ACTIVITY_NOT_OWNED": "这项活动不是你的",
    "ACTIVITY_NOT_ACTIVE": "这项活动没有在进行，不能暂停",
    "ACTIVITY_NOT_PAUSED": "这项活动没有暂停，不用恢复",
    "ACTIVITY_ALREADY_TERMINAL": "这项活动已经结束了",
    "ACTIVITY_FEEDBACK_NOT_DUE": "还没到可以改动这项安排的时候",
    "ACTIVITY_CONCLUSION_REQUIRED": "先给刚结束的活动收尾",
    "ACTIVITY_PAUSE_UNAVAILABLE": "这项活动不能暂停",
    "ACTIVITY_PRECONDITION_UNMET": "还有前提条件没有满足",
    "SESSION_LIMIT_EXCEEDED": "手上的事已经占满了两个名额",
    "RESOURCE_CONFLICT": "和正在做的事不能同时进行",
    "PLAYBACK_SPEED_NOT_ALLOWED": "只有正在看的内容才能改播放速度",
    # 交谈、通话与消息
    "SOCIAL_PARTNER_NOT_FOUND": "找不到要交谈的人",
    "SOCIAL_ACTIVITY_OPERATION_UNKNOWN": "交谈与通话不能这样改动",
    "CONTACT_NOT_FOUND": "找不到这位联系人",
    "MESSAGE_NOT_VISIBLE": "这封邮件你还看不到",
    # 内容、游戏、浏览与生活活动
    "CONTENT_NOT_FOUND": "找不到这个内容",
    "CONTENT_TIMELINE_NOT_FOUND": "这个内容缺少播放时间轴，现在放不了",
    "CONTENT_END_EXCEEDED": "剩下的内容不够播放这么久",
    "GAME_NOT_FOUND": "找不到这个游戏",
    "BROWSE_TARGET_NOT_FOUND": "这里没有可以浏览的商品目录",
    "LIFESTYLE_ACTIVITY_NOT_FOUND": "找不到这项生活活动",
    # 出行
    "FROZEN_ROUTE_NOT_FOUND": "没有用这种方式去那里的路线",
    "ROUTE_UNREACHABLE": "那里现在去不了",
    "ROUTE_INFORMATION_UNKNOWN": "不清楚去那里的路，现在去不了",
    "ROUTE_DURATION_MISMATCH": "路上要用的时间算得不一致，这次出发不成立",
    "LOCATION_TRACK_WINDOW_INVALID": "这段行程的起止时刻不成立",
    # 工作
    "WORK_TASK_NOT_FOUND": "找不到这项工作",
    "WORK_TASK_NOT_VISIBLE": "你还不知道这项工作",
    "WORK_TASK_NOT_AVAILABLE": "这项工作现在不在可以做的时间里",
    "WORK_DURATION_EXCEEDS_CONTRACT": "这项工作累计的时长会超出合同",
    "WORK_WOULD_MISS_DEADLINE": "做这么久会超过截止时间",
    "EMPLOYER_FUNDS_INSUFFICIENT": "雇主的账上钱不够付这笔报酬",
    # 购买、订单与送达
    "SUPPLY_NOT_VISIBLE": "这件商品你还没有见过",
    "SUPPLY_QUOTE_UNKNOWN": "这件商品现在没有报价",
    "SUPPLY_QUALIFICATION_REQUIRED": "买这件商品需要的资格你还没有",
    "PRICE_VERSION_MISMATCH": "价格已经变了",
    "ORDER_ITEM_COUNT_OUT_OF_RANGE": "一单只能买一到五样",
    "ORDER_DUPLICATE_SUPPLY": "同一件商品给同一个人只能写一次",
    "ORDER_SELLER_MISMATCH": "一单只能买同一个卖家的东西",
    "ORDER_LOCATION_MISMATCH": "一单只能买同一个地方的东西",
    "ORDER_NOT_FOUND": "找不到这笔订单",
    "DELIVERY_NOT_DUE": "订单还没到送达的时候",
    "DESTINATION_NOT_FOUND": "收货的地点不存在",
    "PICKUP_LOCATION_REQUIRED": "到店取货要人在店里",
    "LOCATION_ACCESS_DENIED": "你没有进这个地方的资格",
    "MERCHANT_OPENING_UNKNOWN": "不清楚这家店现在开不开门",
    # 食用、使用与品类
    "PRODUCT_NOT_FOUND": "找不到这件东西",
    "PRODUCT_USE_MODE_NOT_ALLOWED": "这件东西不能这样用",
    "PRODUCT_USE_DURATION_MISMATCH": "使用时长和这件东西对不上",
    "MEAL_DURATION_MUST_BE_LONGEST": "用餐时长和份量对不上",
    "MEAL_ITEM_COUNT_OUT_OF_RANGE": "一次最多吃三样东西",
    "NON_FOOD_USE_SINGLE_ITEM": "吃的以外的东西一次只能用一件",
    "CATEGORY_PAIRING_NO_EFFECT": "对这件东西这样做没有效果",
    "CATEGORY_PAIRING_UNDEFINED": "这件东西没有这种用法",
    # 制作（库存配方）与份量换算
    "INVENTORY_RECIPE_OUTPUT_UNBOUND": "这里做不出这样东西",
    "INVENTORY_RECIPE_RULE_MISMATCH": "做法和开始时的不一致",
    "INVENTORY_RECIPE_RESOURCE_UNKNOWN": "这种材料没有登记，做不了",
    "INVENTORY_RECIPE_QUANTITY_UNREPRESENTABLE": "这次的份量太少，换算不出来",
    "INVENTORY_RECIPE_INPUT_CATEGORY_MISMATCH": "手边的材料和这种做法对不上",
    "INVENTORY_RECIPE_OUTPUT_CATEGORY_MISMATCH": "做出来的东西和这种做法对不上",
    "INVENTORY_RECIPE_UNIT_MISMATCH": "材料的计量单位对不上",
    "EXCHANGE_INPUT_INVALID": "这次的份量换算不成立",
    "EXCHANGE_RESULT_INVALID": "换算的结果不是一个有效的数",
    "EXCHANGE_PIECE_COUNT_MISSING": "缺少件数，换算不了",
    "CATEGORY_NUMBER_INVALID": "份量不是一个有效的数",
    "OUT_OF_BOUNDS": "数量超出了能记下的范围",
    "NEGATIVE_BALANCE": "手边的数量不够这次换算",
    "UNKNOWN_RESOURCE": "这种东西没有登记，换算不了",
    "AMOUNT_NOT_POSITIVE": "换算的数量要大于零",
    "CONVERT_IMBALANCED": "换算前后的数量对不上",
    "CAUSE_REQUIRED": "这次换算缺少来由",
    "TIMESTAMP_NOT_INTEGER": "换算的时刻不成立",
    "FLOW_ON_CLOSED_DOMAIN": "这种东西不能凭空增减",
    "TRANSFER_NOT_CLOSED_DOMAIN": "这种东西不能在人与人之间转移",
    "TRANSFER_UNBALANCED": "转出与转入的数量对不上",
    # 今日计划与决策
    "PLAN_NOT_FOUND": "今天没有可以修订的计划",
    "PLAN_REMIND_TIME_INVALID": "提醒时刻要晚于现在、早于明天零点",
    "PLAN_GOAL_ALREADY_PROGRESSED": "这个意图的目标今天已经推进过了",
    "PLAN_GOALS_INVALID": "修订后的今日计划不成立",
    "OBSERVATION_NOT_FOUND": "这次决定依据的情况已经对不上了",
}
#: 按错误码给的改法：怎样改这次请求（世界条件造成的拒绝没有改法，只看下一步）。
_DEFAULT_FIXES = {
    "ACTION_REQUEST_INVALID": "照工具层列出的名称、动词与方式重填这个请求",
    "ACTION_NOT_SUPPORTED": "改用对象列出的动词与方式",
    "INTERACTION_MENU_DENIED": "只选这里列出的动作",
    "ACTIVITY_NOT_FOUND": "照工具层、待办层中的活动名填写",
    "ACTIVITY_NOT_OWNED": "只点名自己的活动",
    "ACTIVITY_NOT_ACTIVE": "暂停只点名正在进行的活动",
    "ACTIVITY_NOT_PAUSED": "恢复只点名已暂停的活动",
    "ACTIVITY_ALREADY_TERMINAL": "不再点名已经结束的活动",
    "ACTIVITY_CONCLUSION_REQUIRED": "这次改为给刚结束的活动收尾",
    "ACTIVITY_PAUSE_UNAVAILABLE": "改为直接结束这项活动",
    "SOCIAL_ACTIVITY_CANNOT_PAUSE": "改为直接结束交谈或通话",
    "SOCIAL_ACTIVITY_OPERATION_UNKNOWN": "交谈与通话只能结束",
    "SOCIAL_PARTNER_NOT_FOUND": "照工具层写人物的名称",
    "CONTACT_NOT_FOUND": "照工具层写联系人的名称",
    "RECIPIENT_NOT_FOUND": "收件人或收货人照工具层写人物或联系人的名称",
    "MESSAGE_TEXT_TOO_LONG": "把消息缩短到 300 字以内",
    "MESSAGE_NOT_VISIBLE": "只选已经收到的邮件",
    "CONTENT_NOT_FOUND": "照工具层写内容的名称",
    "GAME_NOT_FOUND": "照工具层写游戏的名称",
    "BROWSE_TARGET_NOT_FOUND": "改选工具层列出的浏览对象",
    "LIFESTYLE_ACTIVITY_NOT_FOUND": "照工具层写生活活动的名称",
    "FROZEN_ROUTE_NOT_FOUND": "改选工具层列出的地点与出行方式",
    "WORK_TASK_NOT_FOUND": "照工具层写工作任务的名称",
    "WORK_TASK_NOT_VISIBLE": "只选工具层列出的工作",
    "FULFILLMENT_NOT_SUPPORTED": "改用这件商品支持的取得方式",
    "SUPPLY_NOT_FOUND": "改用候选里的完整标签，或者照本轮列出的写法写商品",
    "SUPPLY_NAME_AMBIGUOUS": "改用候选里写明门店的完整标签",
    "SUPPLY_NOT_VISIBLE": "写商品名或完整标签按名下单",
    "ORDER_ITEM_COUNT_OUT_OF_RANGE": "一单只写一到五样",
    "ORDER_DUPLICATE_SUPPLY": "去掉重复的一项",
    "ORDER_SELLER_MISMATCH": "不同卖家的商品分开下单",
    "ORDER_LOCATION_MISMATCH": "不同地方的商品分开下单",
    "DESTINATION_NOT_FOUND": "收货人写自己或能收货的人",
    "PRODUCT_NOT_FOUND": "照工具层写持有物或商品的名称",
    "PRODUCT_USE_MODE_NOT_ALLOWED": "改用这件东西支持的方式",
    "MEAL_ITEM_COUNT_OUT_OF_RANGE": "一次只选一到三样吃的",
    "NON_FOOD_USE_SINGLE_ITEM": "一次只选一件",
    "CATEGORY_PAIRING_NO_EFFECT": "换一件能起作用的东西",
    "CATEGORY_PAIRING_UNDEFINED": "换一种这件东西支持的做法",
    "TASK_NOT_FOUND": "照工具层、待办层或记忆层中的写法填写任务、目标或活动的名称",
    "PLAN_REMIND_TIME_INVALID": "把 remind_time 改到现在之后、今天结束之前，或者写 null",
    "PLAN_GOAL_ALREADY_PROGRESSED": "修订时不再写已经推进过的目标",
    "PLAN_GOALS_INVALID": "每种意图只写一条，连同已推进的目标不超过八条",
}
#: 抛出处没有写下一步时按错误码给的做法。
_DEFAULT_NEXT_STEPS = {
    "WORK_REQUIRES_REWORK": "按评审意见修改后再提交",
    "WORK_INCOMPLETE": "接着做这项工作，做够了再提交",
    "WORK_SUBMISSION_LATE": "这项工作不再提交，改做别的事",
    "SAME_MOMENT_ACTION_RESTRICTED": "只从这一轮开放的动作里选",
    "ACTION_REQUEST_INVALID": "从本轮列出的对象和方式里选",
    "SOCIAL_ACTIVITY_CANNOT_PAUSE": "可以直接结束",
    "SUPPLY_NOT_FOUND": "从候选或本轮列出的商品里选",
    "SUPPLY_NAME_AMBIGUOUS": "写明是哪家店出售的",
    "REQUEST_ALREADY_HANDLED": "等已下的订单送到",
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
    return _DEFAULT_REASONS.get(code, GENERIC_REASON)


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
    """@brief 逐项的名称类拒绝合并成一次（docs/dev/SPEC-dayrun-fidelity.md 第四章）：码取第一项的码；原因逐项写
    「第 N 项「原文」：该项原因」（请求只有一项时不写序号），以「；」相连；改法按码写「第 N 项<该码的默认改法>」；下一步
    取第一项的码的默认做法。

    :param entries: （明细，该项的一句原因），按请求中的先后排列。
    :param total: 请求的项数（决定原因与改法是否写序号）。
    """
    items = tuple(item for item, _ in entries)
    reason = "；".join(f"{numbered(item.index, total)}「{item.written}」：{sentence}" for item, sentence in entries)
    first = items[0].error_code
    return Refusal(first, reason, _DEFAULT_NEXT_STEPS.get(first, ""),
                   fix=items_fix(items, total, _DEFAULT_FIXES) or "", items=items)


def rejection_guidance(error: BusinessRuleError) -> dict:
    """@brief 从一次业务拒绝确定性生成公开的 reason、next_step 与 fix，另带逐项明细 items、已关联的订单 linked_orders、
    名称归一 resolved 与说明 notes（有才写）。

    原因只取 guided() 或 Refusal.error() 写明的 public_reason，否则按错误码取默认原因（不读链式底层异常）；下一步取
    suggested 中的可读下一步、要去的地点或要先处理的活动名，否则按错误码取默认做法；改法取 suggested 中写明的，否则按
    错误码取默认改法。下一步与改法没有时为 None（公开结果写 null，docs/dev/SPEC-persona-prompt.md「通用格式」：允许为空
    的写 null，不填编造的值）。逐项明细里的候选是可照填的请求 {target, action, way, from, to}（至多 3 个，
    docs/dev/SPEC-dayrun-fidelity.md 第四章）。
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
        return f"去{suggested['travel_to']}"
    if suggested.get("conclude_or_pause"):
        return f"先结束或暂停{'、'.join(suggested['conclude_or_pause'])}"
    return ""
