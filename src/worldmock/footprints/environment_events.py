"""@brief 当前环境事实的严格正文与可见性验证。"""

import logging
from datetime import date

from worldmock.exchange.constants import WEATHER_FACTORS
from worldmock.footprints.callback_contract import StateDeltaResult, StateResetResult, TransferResult

RETIRED_KINDS = {
    "alarm_ring", "message_arrival", "mail_arrival", "model_npc_reply_pending", "model_npc_reply",
    "work_content_completed", "bill_due", "payroll", "goal_progressed",
}
TIME_KEYS = {"run_start", "run_stop", "hourly", "daily", "monthly"}
#: 一段停留怎样开始，以及各自要求的起因个数：运行开始时已在这里的那一段没有起因。
ENTRY_KIND_CAUSES = {"run_start": 0, "arrival": 1, "travel_ended_early": 1}


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
    """@brief 三种效果正文来自同一模型，且只有真实环境触发可产生。"""
    contracts = {"state_delta": StateDeltaResult, "reset_to_cap": StateResetResult, "transfer": TransferResult}
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
