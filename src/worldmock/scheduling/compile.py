"""@brief 场景材料到时间有序预排表的确定性编译（设计文档 §7.1）。

编译保持"编译-执行"结构：加载时把邮件/任务/日历旗标与四个可选预排段展开成
按（时刻、稳定身份）排序的 ScheduledEvent 表，引擎按（时刻、序号、身份）确定
性点火。缺省场景（无可选段）的非日历条目与旧 engine._initial_events 逐条一致。
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
            for agent in scenario.agents:
                rows.append((due, agent.agent_id, ScheduledKind.WORK_AVAILABLE,
                             {"task_id": task.task_id}, f"work-{task.task_id}-{agent.agent_id}"))
        deadline = int(task.due_at.timestamp())
        if start < deadline <= end:
            rows.append((deadline, None, ScheduledKind.TASK_EXPIRED,
                         {"task_id": task.task_id}, f"expired-{task.task_id}"))
    rows.extend(_calendar_rows(scenario, start, end))
    rows.extend(_bill_rows(scenario, start, end))
    rows.extend(_payroll_rows(scenario, start, end))
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


def _price_change_rows(scenario: RuntimeScenario, start: int, end: int):
    """@brief 每次调价一条到点替换商品 transfer 边金额的事件。"""
    return [(int(change.due_at.timestamp()), None, ScheduledKind.PRICE_CHANGE,
             {"object_id": change.object_id, "object_version": change.object_version,
              "amount_fen": change.amount_fen},
             f"price-{change.change_id}")
            for change in scenario.price_changes if start < int(change.due_at.timestamp()) < end]


def _product_release_rows(scenario: RuntimeScenario, start: int, end: int):
    """@brief 每次上架一条到点把商家该对象库存置为给定值的事件。"""
    return [(int(release.due_at.timestamp()), None, ScheduledKind.PRODUCT_RELEASE,
             {"object_id": release.object_id, "merchant": release.merchant,
              "stock": release.stock},
             f"release-{release.release_id}")
            for release in scenario.product_releases
            if start < int(release.due_at.timestamp()) < end]


def datetime_midnight(date_text: str, zone: ZoneInfo) -> datetime:
    """@brief 把规范 ISO 自然日解析为场景时区的当日 00:00 时刻。"""
    return datetime.fromisoformat(f"{date_text}T00:00:00").replace(tzinfo=zone)
