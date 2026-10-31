"""@brief 当前环境事实的严格正文与可见性验证。"""

import logging
import re
from datetime import date

from worldmock.exchange_engine.definitions.constants import WEATHER_FACTORS
from worldmock.recording_engine.events.callback_contract import (
    ConsumeOnStartResult,
    StateDeltaResult,
    StateResetResult,
    TransferResult,
)
from worldmock.state.model.memory import (
    MEMORY_DIGEST_KEYS,
    MEMORY_DIGEST_KIND,
    MEMORY_DIGEST_TEXT_LIMITS,
    MEMORY_DIGEST_TIERS,
)

RETIRED_KINDS = {
    "alarm_ring", "message_arrival", "mail_arrival", "model_npc_reply_pending", "model_npc_reply",
    "work_content_completed", "bill_due", "payroll", "goal_progressed",
}
TIME_KEYS = {"run_start", "run_stop", "hourly", "daily", "monthly"}
#: 一段停留怎样开始，以及各自要求的起因个数：运行开始时已在这里的那一段没有起因。
ENTRY_KIND_CAUSES = {"run_start": 0, "arrival": 1, "travel_ended_early": 1}
#: 回顾登记事件 data 的键集与每类项的键集。
_DIGEST_FIELDS = frozenset({"kind", "tier", "period", "digests", "fallback"})
_DIGEST_ITEM_FIELDS = frozenset({"text", "source_event_ids", "cited_event_ids"})
#: 月回顾与年回顾的期写法（ASCII 数字，fullmatch 不接受末尾换行）。
_PERIOD_PATTERNS = {"month": re.compile(r"[0-9]{4}-(0[1-9]|1[0-2])"), "year": re.compile(r"[0-9]{4}")}


def validate_environment_event(event) -> None:
    """@brief 写入和读取均拒绝退役事件及当前闭集正文以外的字段。"""
    data = event.data
    kind = data.get("kind")
    if kind in RETIRED_KINDS:
        _invalid("Environment event uses a retired kind")
    if kind == "callback_effect":
        _callback_effect(event)
    elif kind == "time_trigger":
        _system_source(event, {"kind", "key", "scheduled_id"})
        if data["key"] not in TIME_KEYS or not _text(data["scheduled_id"]) or event.caused_by:
            _invalid("Time trigger must be an independent actual scheduled fact")
    elif kind == "weather":
        _system_source(event, {"kind", "local_date", "condition", "speed_factor", "scheduled_id"})
        _weather(data)
    elif kind == "callback_reminder":
        if (set(data) != {"kind", "rule_id", "app_id", "text"} or event.source_kind != "environment"
                or event.visibility != "agent" or event.resource_changes
                or any(not _text(data[key]) for key in ("rule_id", "app_id", "text"))):
            _invalid("Callback reminder fields or source are invalid")
    elif kind in {"location_entered", "location_left"}:
        _location_stay(event)
    elif kind == MEMORY_DIGEST_KIND:
        _memory_digest_generated(event)


def _memory_digest_generated(event) -> None:
    """@brief 回顾登记事件只核对形状：回顾正文是模型写的主观回忆，不是核验事实；生成时的「依据归属」依赖这一期的输入行，
    这里不重跑。

    信封是来源 model、可见性 system、无资金变化、属于一个人物、起因恰一项；级与期对应；三类各为 null 或带正文与两种身份
    列表的对象，至少一类非 null 或 fallback 非空；fallback 里的类按 food、work、life 的相对次序、不重复且都为 null。
    """
    data = event.data
    if (set(data) != _DIGEST_FIELDS or event.source_kind != "model" or event.visibility != "system"
            or event.resource_changes or event.agent_id is None or len(event.caused_by) != 1):
        _invalid("Memory digest event fields or source are invalid")
    tier, period = data["tier"], data["period"]
    if tier not in MEMORY_DIGEST_TIERS or not isinstance(period, str) or not _period_matches(tier, period):
        _invalid("Memory digest event tier or period is invalid")
    if not _digests_valid(data["digests"], MEMORY_DIGEST_TEXT_LIMITS[tier], data["fallback"]):
        _invalid("Memory digest event digests are invalid")
    fallback = data["fallback"]
    if (not isinstance(fallback, list) or fallback != [key for key in MEMORY_DIGEST_KEYS if key in fallback]
            or any(data["digests"][key] is not None for key in fallback)):
        _invalid("Memory digest event fallback is invalid")


def _period_matches(tier: str, period: str) -> bool:
    """@brief 期与级对应：日是往返相等的 ISO 日期，月是 YYYY-MM（01–12），年是四位 ASCII 数字。"""
    if tier != "day":
        return _PERIOD_PATTERNS[tier].fullmatch(period) is not None
    try:
        return date.fromisoformat(period).isoformat() == period
    except ValueError:
        return False


def _digests_valid(digests, limit: int, fallback) -> bool:
    """@brief 三类恰为 food、work、life；每类为 null 或正文非空白且不超过该级上限、两种身份列表非空且不重复、依据是
    来源的子集；至少一类非 null 或 fallback 非空。"""
    if not isinstance(digests, dict) or set(digests) != set(MEMORY_DIGEST_KEYS):
        return False
    for item in digests.values():
        if item is None:
            continue
        if not isinstance(item, dict) or set(item) != _DIGEST_ITEM_FIELDS or not _text(item["text"]):
            return False
        sources, cited = item["source_event_ids"], item["cited_event_ids"]
        if (len(item["text"]) > limit or not _identities(sources) or not _identities(cited)
                or not set(cited) <= set(sources)):
            return False
    return any(item is not None for item in digests.values()) or (isinstance(fallback, list) and bool(fallback))


def _identities(value) -> bool:
    """@brief 身份列表：非空、元素都是非空白字符串且不重复。"""
    return (isinstance(value, list) and bool(value) and all(_text(item) for item in value)
            and len(set(value)) == len(value))


def _location_stay(event) -> None:
    """@brief 停留进出是无资金变更的系统可见环境事实：字段闭集，属于一个人物，起因个数随进入方式而定，离开恰一个。"""
    data = event.data
    entered = data["kind"] == "location_entered"
    fields = {"kind", "session_id", "location_id", "entry_kind"} if entered else {"kind", "session_id", "location_id"}
    causes = ENTRY_KIND_CAUSES.get(data.get("entry_kind")) if entered else 1
    if (set(data) != fields or event.source_kind != "environment" or event.visibility != "system"
            or event.resource_changes or event.agent_id is None or causes is None
            or len(event.caused_by) != causes or not _text(data["session_id"]) or not _text(data["location_id"])):
        _invalid("Location stay event fields or source are invalid")


def _callback_effect(event) -> None:
    """@brief 四种效果正文来自同一模型，且只有真实环境触发可产生；三种状态效果不改账户。"""
    contracts = {"state_delta": StateDeltaResult, "reset_to_cap": StateResetResult,
                 "consume_on_start": ConsumeOnStartResult, "transfer": TransferResult}
    contract = contracts.get(event.data.get("type"))
    if contract is None:
        _invalid("Callback effect type is invalid")
    try:
        contract.model_validate(event.data, strict=True)
    except ValueError:
        logging.exception("Callback effect fields are invalid")
        raise
    if (event.source_kind != "environment" or event.visibility != "system"
            or event.agent_id is None or len(event.caused_by) != 1):
        _invalid("Callback effect requires one actual environment trigger")
    if event.data["type"] == "transfer":
        _transfer(event)
    elif event.resource_changes:
        _invalid("Callback state effect cannot change account balances")


def _transfer(event) -> None:
    """@brief 转账仅允许本人账户与签名外部锚点，未付不能夹带资金差额。"""
    data = event.data
    agent = f"agent:{event.agent_id}"
    source, target = (("external:scenario", agent) if data["direction"] == "income"
                      else (agent, "external:scenario"))
    if (data["from_account_id"], data["to_account_id"]) != (source, target):
        _invalid("Callback transfer accounts disagree with its direction")
    expected = {} if data["status"] == "unpaid" else {
        f"account:{source}": -data["amount_fen"], f"account:{target}": data["amount_fen"]}
    if event.resource_changes != expected:
        _invalid("Callback transfer balances disagree with its actual payment status")


def _system_source(event, fields: set[str]) -> None:
    """@brief 时间和天气事实是无资金变更的系统可见环境事件。"""
    if (set(event.data) != fields or event.source_kind != "environment"
            or event.visibility != "system" or event.resource_changes):
        _invalid("System environment fact fields or source are invalid")


def _weather(data: dict) -> None:
    """@brief 天气日期、枚举和速度系数只能表达引擎真实天气。"""
    try:
        valid_date = date.fromisoformat(data["local_date"]).isoformat() == data["local_date"]
    except (ValueError, TypeError):
        valid_date = False
    factor = data["speed_factor"]
    if (not valid_date or data["condition"] not in WEATHER_FACTORS or type(factor) not in (int, float)
            or factor != WEATHER_FACTORS[data["condition"]] or not _text(data["scheduled_id"])):
        _invalid("Weather fact disagrees with the actual environment weather")


def _text(value) -> bool:
    """@brief 身份与文本必须是非空白字符串。"""
    return isinstance(value, str) and bool(value.strip())


def _invalid(message: str) -> None:
    """@brief 非法录制数据写错误日志并拒绝。"""
    logging.error(message)
    raise ValueError(message)
