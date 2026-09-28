"""@brief 业务拒绝的公开原因与下一步（docs/dev/SPEC-time-and-energy.md 第五章「动作照常列出，做错了再说明」）。

拒绝结果的 data.reason（一句中文原因）与 data.next_step（可读的活动名、地点名或做法，不含内部身份）由本模块从拒绝
确定性生成：引导性拒绝在抛出处用 guided() 或 Refusal.error() 写明原因与下一步（或以可读名称写 suggested），原因记在
错误的 public_reason 上；其余错误码按码取中文默认原因，从不取链式底层异常的文字（其中可能是请求模型的校验转储）。
两者经公开结果投影进入进度层 tool_call.result.details（docs/dev/SPEC-persona-prompt.md「进度层」）。工具层的可用性
说明取同一原因：只有错误码的拒绝经 refusal_for_code 取得与拒绝一字不差的 Refusal。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from worldmock.errors import BusinessRuleError, PreconditionError

#: 没有专门说明的业务拒绝的通用原因。
GENERIC_REASON = "这件事现在做不了"
#: 抛出处没有写明原因时按错误码取的中文原因（只写世界事实，不含内部身份）。
_DEFAULT_REASONS = {
    "ACTION_REQUEST_INVALID": "所选的对象或方式不在这一轮列出的范围内",
    "INTERACTION_MENU_DENIED": "这里不能这样做",
    "ACTIVITY_DURATION_INVALID": "按现在的情况算出的时长不合适，做不了",
    "ROUTE_DURATION_INVALID": "这段路太长，一次走不完",
    "ACTIVITY_TOO_SHORT": "时长不到一分钟，做不了",
    "PRODUCT_NOT_HELD": "手边没有这件东西",
    "PRODUCT_USE_LOCATION_REQUIRED": "这件东西只能在它所在的地方用",
    "FULFILLMENT_NOT_SUPPORTED": "这件商品不能用这种方式买",
    "DELIVERY_DESTINATION_NOT_SUPPORTED": "暂时送不到这里",
    "DELIVERY_ROUTE_NOT_FOUND": "暂时送不到这里",
    "DELIVERY_ROUTE_UNREACHABLE": "暂时送不到这里",
    "DELIVERY_ROUTE_INFORMATION_UNKNOWN": "暂时送不到这里",
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
}
#: 抛出处没有写下一步时按错误码给的做法。
_DEFAULT_NEXT_STEPS = {
    "WORK_REQUIRES_REWORK": "按评审意见修改后再提交",
    "WORK_INCOMPLETE": "接着做这项工作，做够了再提交",
    "SAME_MOMENT_ACTION_RESTRICTED": "只从这一轮开放的动作里选",
    "ACTION_REQUEST_INVALID": "从本轮列出的对象和方式里选",
    "SOCIAL_ACTIVITY_CANNOT_PAUSE": "可以直接结束",
}


@dataclass(frozen=True, slots=True)
class Refusal:
    """@brief 一条不成立的准入：错误码、一句中文原因、可读的下一步与私有的未满足条件和建议（不抛出，供说明复用）。

    :param suggested: 私有 data.suggested 的可读建议（例如 {"conclude_or_pause": [活动名]}）；省略时写下一步。
    """

    code: str
    reason: str
    next_step: str = ""
    unmet: tuple[str, ...] = ()
    suggested: dict | None = None

    def error(self) -> PreconditionError:
        """@brief 换成可抛出的业务拒绝：原因写在 public_reason 上（也作链式底层原因进入结果消息），建议或下一步写进
        suggested。"""
        suggested = self.suggested if self.suggested is not None else (
            {"next_step": self.next_step} if self.next_step else None)
        error = PreconditionError(self.code, unmet=list(self.unmet), suggested=suggested, public_reason=self.reason)
        error.__cause__ = ValueError(self.reason)
        return error


def guided(code: str, reason: str, next_step: str = "", unmet: Sequence[str] = ()) -> PreconditionError:
    """@brief 构造带公开原因与下一步的业务拒绝：原因写在 public_reason 上，下一步写进 suggested（可读，不含身份）。

    :param code: 业务错误码（接口合同登记的闭集）。
    :param reason: 一句中文原因。
    :param next_step: 可读的下一步（要先处理的活动、要去的地点或做法），没有时为空串。
    :param unmet: 未满足条件名称，写进私有 data.unmet。
    """
    return Refusal(code, reason, next_step, tuple(unmet)).error()


def refusal_for_code(code: str) -> Refusal:
    """@brief 只有错误码的业务拒绝对应的 Refusal：原因与下一步取该码的默认说法，与 rejection_guidance 一字不差。

    :param code: 业务错误码（接口合同登记的闭集）。
    """
    return Refusal(code, _DEFAULT_REASONS.get(code, GENERIC_REASON), _DEFAULT_NEXT_STEPS.get(code, ""))


def rejection_guidance(error: BusinessRuleError) -> dict[str, str]:
    """@brief 从一次业务拒绝确定性生成公开的 reason 与 next_step。

    原因只取 guided() 或 Refusal.error() 写明的 public_reason，否则按错误码取默认原因（不读链式底层异常）；下一步取
    suggested 中的可读下一步、要去的地点或要先处理的活动名，否则按错误码取默认做法，没有则为空串。
    """
    code = str(error)
    reason = getattr(error, "public_reason", None) or _DEFAULT_REASONS.get(code, GENERIC_REASON)
    suggested = getattr(error, "suggested", None) or {}
    return {"reason": reason, "next_step": _next_step(suggested) or _DEFAULT_NEXT_STEPS.get(code, "")}


def _next_step(suggested: dict) -> str:
    """@brief 把可读的建议写成一句下一步：直接写明的下一步、要去的地点或要先结束或暂停的活动。"""
    if suggested.get("next_step"):
        return str(suggested["next_step"])
    if suggested.get("travel_to"):
        return f"去{suggested['travel_to']}"
    if suggested.get("conclude_or_pause"):
        return f"先结束或暂停{'、'.join(suggested['conclude_or_pause'])}"
    return ""
