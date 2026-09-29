"""@brief 宿主协议纠错与环境业务拒绝共用的错误结构（docs/dev/SPEC-dayrun-fidelity.md 第四章「错误合同」）。

一次纠错或拒绝写成 {error_code, reason, fix, items, unmet}：error_code 为错误码，reason 一句原因，fix 一句可以照做的
改法（没有可照做的改法时为 null），items 逐项明细，unmet 沿用 ACTIVITY_PRECONDITION_UNMET 的未满足条件名称。逐项明细
{index, field, written, error_code, candidates}：index 从 0 起是请求中的第几项，field 为出错的参数名，written 为人物所写
的原文，candidates 至多 3 个可以照填的请求 {target, action, way, from, to}（可另带 note，如「约 20 分钟」）。键名英文，
值是人物能照着改的自然语言（语言不作要求，裁决 D18），不含内部身份、校验库原文与网址。受理时名称经归一的写进结果
details.resolved（[{written, as}]），附带的说明写进 details.notes（字符串列表）。

宿主（custom/agents/life_feedback.py）把本地校验失败写成 HOST_ERROR_CODES 闭集中的码；环境（life.guidance）把业务拒绝
写成同一结构，公开结果的 message 就是 reason。本模块只有数据与文字，不读世界状态，宿主与环境共用。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

_LOGGER = logging.getLogger(__name__)

#: 错误结构的键（输出顺序）。
ERROR_KEYS = ("error_code", "reason", "fix", "items", "unmet")
#: 逐项明细的键。
ITEM_KEYS = ("index", "field", "written", "error_code", "candidates")
#: 可照填候选的请求键（另可带 note）。
CANDIDATE_KEYS = ("target", "action", "way", "from", "to")
#: 每一项至多给出的候选数。
MAX_CANDIDATES = 3
#: 受理结果里名称归一与附带说明的公开键（details.resolved 为 [{written, as}]，details.notes 为字符串列表）。
ACCEPTANCE_KEYS = ("resolved", "notes")

#: 宿主协议纠错码 → （默认原因，默认改法）；键的集合就是宿主错误码闭集（接口合同「工具返回通道」）。
_HOST_DEFAULTS: dict[str, tuple[str, str]] = {
    "ARGUMENTS_NOT_JSON": ("工具参数不是一个完整的 JSON 对象",
                           "参数写成一个完整的 JSON 对象：键和字符串用双引号，括号成对，数组各项用逗号隔开"),
    "ARGUMENTS_INVALID": ("参数不符合这个工具的参数定义", "按 reason 逐项改正参数，其余保持不变"),
    "TOOL_CALL_REQUIRED": ("这次回复没有可以执行的工具调用", "从这次提供的工具里只调用一个，提交这次的决定"),
    "TOO_MANY_TARGETS": ("一次最多选两个目标", "只保留一至两个目标，其余的下一次再做"),
    "NAME_NOT_FOUND": ("写的名称不是可以操作的对象", "改用候选里的名称，或者照工具层、待办层中的写法填写，或者去掉这一项"),
    "NAME_AMBIGUOUS": ("写的名称对应不止一个对象", "改用候选中的完整名称"),
    "WAY_NOT_OFFERED": ("这个对象没有列出这种动词与方式", "改用该对象列出的动词与方式（见候选），或者去掉这一项"),
    "ROUTE_FIELDS_INVALID": ("from、to 或 answering 与动词、方式不相配", "按动词与方式重填 from、to 与 answering"),
    "SUPPLY_SOURCE_MISMATCH": ("目标已经写明了门店，from 与它不一致", "from 写 null"),
    "SUBJECT_NOT_KNOWN": ("关系看法写的对象不是认识的人", "about 改写认识的人的名字，或者把这条看法改写成普通记忆放进 memories"),
    "OUTPUT_TRUNCATED": ("输出在思考阶段用尽，尚未调用工具", "只调用一个工具"),
}
#: 宿主协议纠错码闭集（docs/dev/SPEC-dayrun-fidelity.md 第四章）。
HOST_ERROR_CODES: tuple[str, ...] = tuple(_HOST_DEFAULTS)
#: 宿主的名称类纠错码：多项请求逐项报全部，其余条件仍报第一条（第三章 3.1 第 5 条、3.4）。
HOST_NAME_CODES = frozenset({"NAME_NOT_FOUND", "NAME_AMBIGUOUS", "WAY_NOT_OFFERED", "SUPPLY_SOURCE_MISMATCH",
                             "SUBJECT_NOT_KNOWN"})
#: 环境的名称类拒绝码（3.4）：指名购买找不到商品、那家店不卖、不支持所写方式、来源不是门店或渠道、目标已写明门店而 from
#: 是另一家（SUPPLY_SOURCE_MISMATCH 也是宿主码：列出的标签由宿主纠正，按名写的标签由环境拒绝，3.2），以及回看找不到名称；
#: SUPPLY_NAME_AMBIGUOUS 对商品已不再抛出（3.5「收敛」第 5 条：多于一个可服务的匹配按 prefer 选定），留在闭集里按码归类
#: 旧运行的拒绝。
ENVIRONMENT_NAME_CODES = frozenset({"SUPPLY_NOT_FOUND", "SUPPLY_NOT_AT_STORE", "SUPPLY_NAME_AMBIGUOUS",
                                    "FULFILLMENT_NOT_SUPPORTED", "SOURCE_NOT_FOUND", "SUPPLY_SOURCE_MISMATCH",
                                    "TASK_NOT_FOUND"})
#: 名称类拒绝码闭集（宿主与环境同一口径，3.4）：宿主的只耗本次决策的回合、不进引擎；环境的每人每秒至多 2 次不计同刻
#: 轮数、不施加情绪脉冲（worldmock.life.same_moment），第 3 次起照常；质量报告 name_rejections 按它分列。
NAME_REJECTION_CODES = HOST_NAME_CODES | ENVIRONMENT_NAME_CODES


@dataclass(frozen=True, slots=True)
class Candidate:
    """@brief 一个可以照填的候选请求：目标、动词、方式、来源与去向，可另带一句说明。

    :param source: 请求的 from（没有时为 None）。
    :param destination: 请求的 to（没有时为 None）。
    :param note: 附带的说明，如「约 20 分钟」；没有时为 None，不写进结构。
    """

    target: str
    action: str
    way: str
    source: str | None = None
    destination: str | None = None
    note: str | None = None

    def payload(self) -> dict[str, Any]:
        """@brief 写进结构的形状 {target, action, way, from, to}，有说明时另带 note。"""
        row: dict[str, Any] = {"target": self.target, "action": self.action, "way": self.way,
                               "from": self.source, "to": self.destination}
        if self.note:
            row["note"] = self.note
        return row


@dataclass(frozen=True, slots=True)
class ErrorItem:
    """@brief 一项明细：请求中的第几项（从 0 起）、出错的参数名、人物所写的原文、该项的错误码与至多 3 个候选。"""

    index: int
    field: str
    written: str
    error_code: str
    candidates: tuple[Candidate, ...] = ()

    def __post_init__(self) -> None:
        """@brief 序号从 0 起，候选至多 3 个（超出是构造方的程序错误）。"""
        if self.index < 0 or len(self.candidates) > MAX_CANDIDATES:
            _LOGGER.error("Error item index or candidate count is out of range")
            raise ValueError("error item index must be non-negative and carry at most three candidates")

    def payload(self) -> dict[str, Any]:
        """@brief 写进结构的形状 {index, field, written, error_code, candidates}。"""
        return {"index": self.index, "field": self.field, "written": self.written, "error_code": self.error_code,
                "candidates": [candidate.payload() for candidate in self.candidates]}


@dataclass(frozen=True, slots=True)
class ErrorReport:
    """@brief 一次纠错或拒绝的完整结构：错误码、原因、改法（没有时为 None）、逐项明细与未满足条件。"""

    error_code: str
    reason: str
    fix: str | None = None
    items: tuple[ErrorItem, ...] = ()
    unmet: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """@brief 错误码与原因都不能为空（空原因会让人物无从改起）。"""
        if not self.error_code or not self.reason:
            _LOGGER.error("Error report has no code or no reason")
            raise ValueError("error report requires a code and a reason")

    def payload(self) -> dict[str, Any]:
        """@brief 写进结构的形状：ERROR_KEYS 五个键都写，没有改法时 fix 为 null，没有明细时 items 为 []。"""
        return {"error_code": self.error_code, "reason": self.reason, "fix": self.fix or None,
                "items": [item.payload() for item in self.items], "unmet": list(self.unmet)}


def host_defaults(code: str) -> tuple[str, str]:
    """@brief 宿主协议纠错码的默认（原因，改法）；不在闭集中的码是宿主的程序错误。"""
    if code not in _HOST_DEFAULTS:
        _LOGGER.error("Host protocol error code is outside the closed set: %s", code)
        raise ValueError(f"host protocol error code is outside the closed set: {code}")
    return _HOST_DEFAULTS[code]


def host_report(code: str, reason: str | None = None, fix: str | None = None,
                items: Sequence[ErrorItem] = ()) -> ErrorReport:
    """@brief 一次宿主协议纠错：码须在 HOST_ERROR_CODES 闭集中，原因与改法缺省取该码的默认说法；宿主没有未满足条件。"""
    default_reason, default_fix = host_defaults(code)
    return ErrorReport(code, reason or default_reason, fix or default_fix, tuple(items))


def numbered(index: int, total: int) -> str:
    """@brief 多项请求中第 index 项（从 0 起）的说法「第 N 项」；请求只有一项时为空串。"""
    return f"第 {index + 1} 项" if total > 1 else ""


def numbered_items(indices: Sequence[int], total: int) -> str:
    """@brief 多项请求中几项的合称「第 1、2 项」；请求只有一项时为空串。"""
    return f"第 {'、'.join(str(index + 1) for index in indices)} 项" if total > 1 else ""


def items_fix(items: Sequence[ErrorItem], total: int, fixes: Mapping[str, str | None]) -> str | None:
    """@brief 逐项改法：按错误码首次出现的先后，每个码写一句「第 N 项<该码的改法>」，以「；」相连；没有改法的码不写，
    全都没有时为 None。

    :param fixes: 错误码 → 改法（宿主取默认改法表，环境取拒绝码的默认改法）。
    """
    order = list(dict.fromkeys(item.error_code for item in items))
    sentences = [f"{numbered_items([item.index for item in items if item.error_code == code], total)}{fixes[code]}"
                 for code in order if fixes.get(code)]
    return "；".join(sentences) or None
