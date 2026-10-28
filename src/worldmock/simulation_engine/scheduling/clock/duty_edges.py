"""@brief 班次与休息时段起止的唤醒事实（docs/design/04-simulation-engine.md）。

起止时刻由人物在场景里声明的工作安排（经 world_ops.agent_config 取得）与世界时钟直接求值，不存交付状态：日期例外
优先于每周声明（work_schedule.shift_on），区间半开，按当地墙钟换算（work_schedule.shift_span、break_spans：夏令时
跳过的当地时刻按跳变前的偏移、重复的取第一次，换算后落到班次之外的休息时段起止照样记）；写作 1440 的止点落在次日
当地零点。不当班的日子本身没有起止，没有工作安排的人物没有这些时刻。

预测与唤醒共用同一事实生成器 duty_edges：edges_at 给恰在某一秒成立的起止成员（唤醒判定），edge_times_after 给严格
晚于某一秒的起止时刻（下一次决策机会与下一时钟边界的候选）。人物醒睡的取舍不在本模块：睡眠中的过滤与剔除由
schedule.evaluate_wake_reasons、schedule.next_decision_time 与 rhythm.awake_duty_edge_times 负责。
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from worldmock.configuration.persona.work_schedule import break_spans, shift_on, shift_span


def duty_edges(inputs: Any, agent_id: int, moment: int) -> Iterator[tuple[int, str]]:
    """@brief 生成器：moment 所在当地日的前一日、当日与次日里，该人物班次与休息时段的起止（时刻, 成员）。

    扫描前一日使前一日写到 1440 的止点在当地零点上可见，扫描次日使次日 00:00 的起点在零点之前就进入候选
    （docs/design/04-simulation-engine.md）。按日期、班次开始、各休息时段、班次结束的次序产出，
    同一时刻可以出现多次；没有工作安排或三天都不当班时什么都不产出。

    @param inputs 完整环境输入（读 timezone 与唯一人物声明）
    @param agent_id 人物身份
    @param moment 查询时刻（UTC epoch 秒）
    """
    schedule = inputs.persona.work_schedule
    if schedule is None:
        return
    zone = ZoneInfo(inputs.config.timezone)
    today = datetime.fromtimestamp(moment, zone).date()
    for day in (today - timedelta(days=1), today, today + timedelta(days=1)):
        shift = shift_on(schedule, day)
        if shift is None:
            continue
        start, end = shift_span(shift, day, zone)
        yield start, "shift_start"
        for begin, finish in break_spans(shift, day, zone):
            yield begin, "break_start"
            yield finish, "break_end"
        yield end, "shift_end"


def edges_at(inputs: Any, agent_id: int, now: int) -> frozenset[str]:
    """@brief 恰在 now 这一秒成立的起止成员（唤醒判定的输入）：同一秒重合的多个起止各记一次，并列的次序由
    schedule.evaluate_wake_reasons 按声明次序给出（docs/design/04-simulation-engine.md）。

    @param inputs 完整环境输入
    @param agent_id 人物身份
    @param now 结算边界时刻（UTC epoch 秒）
    """
    return frozenset(member for moment, member in duty_edges(inputs, agent_id, now) if moment == now)


def edge_times_after(inputs: Any, agent_id: int, now: int) -> tuple[int, ...]:
    """@brief 严格晚于 now 的起止时刻（升序去重）：下一次决策机会与下一时钟边界的候选，本边界已判定的起止不重复计
    （docs/design/04-simulation-engine.md）。

    @param inputs 完整环境输入
    @param agent_id 人物身份
    @param now 当前时刻（UTC epoch 秒）
    """
    return tuple(sorted({moment for moment, _member in duty_edges(inputs, agent_id, now) if moment > now}))
