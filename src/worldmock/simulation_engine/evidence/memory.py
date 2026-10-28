"""@brief 按职责保存现有定义与纯计算。"""


from __future__ import annotations
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo
from worldmock.state.world.kinds import ActivityKind, ActivityStatus




def due_reflection(state: Any, agent_id: int) -> dict[str, str] | None:
    """@brief 就寝反思此刻该不该做：本人恰在此刻睡醒、这是当天第一次睡醒、这一秒还没有提交过决策时，给出归属日期与所属睡眠。

    只读世界状态里已有的事实。归属日期是睡醒所在本地日的前一天；恰在本地零点结束的睡眠属于新的一天，
    被运行终点截断的睡眠不是睡醒。

    :param state: 世界状态（读 current_time、timezone、agents 与 activities）。
    :param agent_id: 人物身份。
    :returns: 不该做时为 None，否则为 {date, sleep_activity_id}。
    """
    now, zone = state.current_time, ZoneInfo(state.timezone)
    if state.agents[agent_id].last_decision_time == now:
        return None
    sleeps = [item for item in state.activities.values() if item.agent_id == agent_id
              and item.kind == ActivityKind.SLEEP and item.status == ActivityStatus.COMPLETED]
    woke = next((item for item in sleeps if item.ended_at == now), None)
    if woke is None:
        return None
    today = datetime.fromtimestamp(now, zone).date()
    if any(item is not woke and datetime.fromtimestamp(item.ended_at, zone).date() == today for item in sleeps):
        return None
    return {"date": (today - timedelta(days=1)).isoformat(), "sleep_activity_id": woke.activity_id}
