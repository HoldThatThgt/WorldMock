"""@brief 场景材料到时间有序预排表的确定性编译（docs/design/04-simulation-engine.md「预排事件」）。

加载时把邮件、任务、日历、资金、供给报价边界与补货编译为确定事件；
引擎按模拟时刻、序号与稳定身份依次处理，不通过周期轮询更新供给。
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from worldmock.life.scenario import RuntimeScenario
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
    for task in scenario.tasks:
        due = int(task.available_at.timestamp())
        if start < due < end:
            agent_id = int(task.assignee_agent_id.removeprefix("agent:"))
            rows.append((due, agent_id, ScheduledKind.WORK_AVAILABLE,
                         {"task_id": task.task_id}, f"work-{task.task_id}-{agent_id}"))
        deadline = int(task.due_at.timestamp())
        if start < deadline <= end:
            rows.append((deadline, None, ScheduledKind.TASK_EXPIRED,
                         {"task_id": task.task_id}, f"expired-{task.task_id}"))
    rows.extend(_calendar_rows(scenario, start, end))
    rows.extend(_bill_rows(scenario, start, end))
    rows.extend(_payroll_rows(scenario, start, end))
    rows.extend(_quote_change_rows(scenario, start, end))
    rows.extend(_price_change_rows(scenario, start, end))
    rows.extend(_product_release_rows(scenario, start, end))
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


def _quote_change_rows(scenario: RuntimeScenario, start: int, end: int):
    """@brief 每个报价生效或失效边界只编译一条完整声明替换事件。"""
    rows = []
    for supply in scenario.supplies:
        boundaries = {int(quote.effective_from.timestamp()) for quote in supply.quotes}
        boundaries.update(int(quote.effective_to.timestamp()) for quote in supply.quotes
                          if quote.effective_to is not None)
        for due in sorted(boundaries):
            if start < due <= end:
                rows.append((due, None, ScheduledKind.SUPPLY_QUOTE_CHANGE,
                             {"supply_id": supply.supply_id}, f"boundary-quote-{supply.supply_id}-{due}"))
    return rows


def _price_change_rows(scenario: RuntimeScenario, start: int, end: int):
    """@brief 每次显式调价携带供给身份、完整版本和自己的假设来源。"""
    return [(int(change.due_at.timestamp()), None, ScheduledKind.PRICE_CHANGE,
             {"supply_id": change.supply_id, "price_version": change.price_version,
              "amount_fen": change.amount_fen, "source": change.source.model_dump(mode="json")},
             f"price-{change.change_id}")
            for change in scenario.price_changes if start < int(change.due_at.timestamp()) < end]


def _product_release_rows(scenario: RuntimeScenario, start: int, end: int):
    """@brief 每次补货只将目标供给库存置为声明数量，并保留假设来源。"""
    return [(int(release.due_at.timestamp()), None, ScheduledKind.PRODUCT_RELEASE,
             {"supply_id": release.supply_id, "stock": release.stock,
              "source": release.source.model_dump(mode="json")},
             f"release-{release.release_id}")
            for release in scenario.product_releases
            if start < int(release.due_at.timestamp()) < end]


def datetime_midnight(date_text: str, zone: ZoneInfo) -> datetime:
    """@brief 把规范 ISO 自然日解析为场景时区的当日 00:00 时刻。"""
    return datetime.fromisoformat(f"{date_text}T00:00:00").replace(tzinfo=zone)
