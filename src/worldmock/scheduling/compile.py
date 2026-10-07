"""@brief 场景材料到时间有序预排表的确定性编译（docs/design/04-simulation-engine.md#simulation-engine-worldmock-产品设计说明书-模拟引擎「预排事件」）。

加载时把邮件、日历、账单、工资与应用推送编译为确定事件；
引擎按模拟时刻、序号与稳定身份依次处理，不通过周期轮询生成事实。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from worldmock.life.scenario import RuntimeScenario
from worldmock.exchange.constants import PUSH_HOURS_LOCAL
from worldmock.scheduling.events import ScheduledEvent, ScheduledKind

#: 日历旗标名 → 场景日历字段（与观察侧 _calendar_context 的键保持一致）。
_CALENDAR_FLAG_FIELDS = (
    ("spring_festival", "spring_festival_dates"),
    ("makeup_workday", "makeup_work_dates"),
    ("overtime_opportunity", "overtime_opportunity_dates"),
    ("sick_leave", "sick_leave_date"),
    ("personal_leave", "personal_leave_date"),
    ("social_dinner_opportunity", "social_dinner_date"),
)


def compile_schedule(scenario: RuntimeScenario, start: int, end: int) -> list[ScheduledEvent]:
    """@brief 将全部场景材料编译为时间有序、序号连续的后台事件表。"""
    rows: list[tuple[int, int | None, ScheduledKind, dict, str]] = []
    for message in scenario.messages:
        due = int(message.sent_at.timestamp())
        if start < due < end:
            rows.append((due, message.recipient_agent_id, ScheduledKind.MAIL_ARRIVAL,
                         {"message_id": message.message_id}, f"mail-{message.message_id}"))
    rows.extend(_calendar_rows(scenario, start, end))
    rows.extend(_bill_rows(scenario, start, end))
    rows.extend(_payroll_rows(scenario, start, end))
    rows.extend(_push_rows(scenario, start, end))
    rows.sort(key=lambda row: (row[0], row[4]))
    return [ScheduledEvent(scheduled_id=row[4], kind=row[2], due_at=row[0], sequence=index,
                           agent_id=row[1], data=row[3]) for index, row in enumerate(rows, start=1)]


def _calendar_rows(scenario: RuntimeScenario, start: int, end: int):
    """@brief 每个有旗标的自然日在本地 00:00 各编译一条零状态变更事件。"""
    zone = ZoneInfo(scenario.timezone)
    flags_by_date: dict[str, list[str]] = {}
    for flag, field in _CALENDAR_FLAG_FIELDS:
        values = getattr(scenario.calendar, field)
        for value in (values if isinstance(values, tuple) else (values,)):
            flags_by_date.setdefault(value, []).append(flag)
    rows = []
    for date_text in sorted(flags_by_date):
        due = int(datetime_midnight(date_text, zone).timestamp())
        if start < due < end:
            rows.append((due, None, ScheduledKind.CALENDAR_FLAG,
                         {"date": date_text, "flags": sorted(flags_by_date[date_text])},
                         f"calendar-{date_text}"))
    return rows


def _bill_rows(scenario: RuntimeScenario, start: int, end: int):
    """@brief 每张账单一条到期扣款事件（余额不足的拒付在效果侧裁决）。"""
    return [(int(bill.due_at.timestamp()), None, ScheduledKind.BILL_DUE,
             {"bill_id": bill.bill_id, "account_id": bill.account_id,
              "payee_account_id": bill.payee_account_id, "amount_fen": bill.amount_fen},
             f"bill-{bill.bill_id}")
            for bill in scenario.bills if start < int(bill.due_at.timestamp()) < end]


def _payroll_rows(scenario: RuntimeScenario, start: int, end: int):
    """@brief 每笔工资一条到期入账事件（固定金额，失败在效果侧拒付）。"""
    return [(int(payroll.due_at.timestamp()), payroll.agent_id, ScheduledKind.PAYROLL,
             {"payroll_id": payroll.payroll_id, "employer_account_id": payroll.employer_account_id,
              "agent_id": payroll.agent_id, "amount_fen": payroll.amount_fen},
             f"payroll-{payroll.payroll_id}")
            for payroll in scenario.payrolls if start < int(payroll.due_at.timestamp()) < end]








def _push_rows(scenario: RuntimeScenario, start: int, end: int):
    """@brief 每个当地日声明整点推送机会，重复与缺失小时沿用本地零点加小时的语义。"""
    zone = ZoneInfo(scenario.timezone)
    day = datetime.fromtimestamp(start, zone).date()
    final_day = datetime.fromtimestamp(end - 1, zone).date()
    rows = []
    while day <= final_day:
        midnight = datetime_midnight(day.isoformat(), zone)
        for hour in PUSH_HOURS_LOCAL:
            due = _push_due_at(midnight, hour)
            if start < due < end:
                for agent in scenario.agents:
                    identity = f"push-{agent.agent_id}-{day.isoformat()}T{hour:02d}"
                    rows.append((due, agent.agent_id, ScheduledKind.APP_PUSH, {}, identity))
        day += timedelta(days=1)
    return rows


def _push_due_at(midnight: datetime, hour: int) -> int:
    """@brief 从当地零点声明一个小时；缺失小时用跳变前偏移，重复小时取第一次。"""
    return int((midnight + timedelta(hours=hour)).timestamp())


def datetime_midnight(date_text: str, zone: ZoneInfo) -> datetime:
    """@brief 把规范 ISO 自然日解析为场景时区的当日 00:00 时刻。"""
    return datetime.fromisoformat(f"{date_text}T00:00:00").replace(tzinfo=zone)
