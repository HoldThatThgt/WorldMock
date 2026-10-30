"""零点计划的回顾时段是左闭右开的本地日窗口：零点时是刚过去的一天，启动时是当天零点到生成时刻；只有一个窗口判定。"""

import inspect
from datetime import datetime
from types import SimpleNamespace

from tests.life.conftest import conclude_request
from worldmock.simulation_engine.observation.memory.days import DayWindow
from worldmock.simulation_engine.scheduling.plans import day_plan_inputs
from worldmock.simulation_engine.scheduling.plans.day_plan_inputs import _AgentHistory, _review_window

ZONE = "Asia/Shanghai"


def at(text: str) -> int:
    """上海时区的本地时刻「YYYY-MM-DD HH:MM:SS」写成纪元秒。"""
    return int(datetime.fromisoformat(f"{text}+08:00").timestamp())


def test_review_window_is_yesterday_at_midnight_and_today_so_far_at_startup():
    midnight = at("2026-10-10 00:00:00")
    assert _review_window(midnight, ZONE, True) == DayWindow("2026-10-09", at("2026-10-09 00:00:00"), midnight, ZONE)
    morning = at("2026-10-10 08:00:00")
    assert _review_window(morning, ZONE, False) == DayWindow("2026-10-10", midnight, morning, ZONE)
    empty = _review_window(midnight, ZONE, False)
    assert empty == DayWindow("2026-10-10", midnight, midnight, ZONE)
    assert not any(empty.contains(moment) for moment in (midnight - 1, midnight, midnight + 1))


def _conclusion(number: int, moment: int) -> tuple[SimpleNamespace, SimpleNamespace]:
    """一条受理的 conclude_activity 请求与它的结果，收尾活动名写着第几条。"""
    request = SimpleNamespace(event_id=f"request-{number}", action_id=f"action-{number}", occurred_at=moment,
                              data={"tool_name": "conclude_activity",
                                    "request": conclude_request(f"第{number}件事").model_dump(mode="json")})
    result = SimpleNamespace(event_id=f"result-{number}", action_id=f"action-{number}", occurred_at=moment,
                             data={"status": "completed"})
    return request, result


def test_conclusion_records_follow_the_half_open_day():
    """10-10 零点的计划取 10-09 00:00:00 与 23:59:59 两条收尾，恰在 10-10 00:00:00 的一条属于新的一天。"""
    moments = (at("2026-10-09 00:00:00"), at("2026-10-09 23:59:59"), at("2026-10-10 00:00:00"))
    pairs = [_conclusion(number, moment) for number, moment in enumerate(moments, start=1)]
    history = _AgentHistory((), (), tuple(result for _, result in pairs), tuple(request for request, _ in pairs))
    window = _review_window(at("2026-10-10 00:00:00"), ZONE, True)
    records, sources = day_plan_inputs._conclusion_records(history, window)
    assert [(row["时间"], row["活动"]) for row in records] == [("00:00", "第1件事"), ("23:59", "第2件事")]
    assert sources == ["request-1", "request-2"]


def test_previous_goals_still_match_the_plan_ending_at_this_midnight():
    """回顾时段结束于这次零点：取 day_end 等于这次零点的计划快照，不取 day_end 是下一个零点的。"""
    midnight = at("2026-10-10 00:00:00")
    plans = [(1, midnight, "写完周报"), (2, at("2026-10-11 00:00:00"), "明天的事")]
    environment = tuple(SimpleNamespace(event_seq=seq, event_id=f"plan-{seq}", data={"day_plan": {
        "day_end": day_end, "goals": [{"intent_kind": "工作", "goal": goal}]}}) for seq, day_end, goal in plans)
    goals, sources = day_plan_inputs._previous_goals(_AgentHistory((), environment, (), ()),
                                                     _review_window(midnight, ZONE, True))
    assert (goals, sources) == ([{"意图": "工作", "目标": "写完周报"}], ["plan-1"])


def test_day_plan_inputs_keeps_one_window_predicate():
    assert not hasattr(day_plan_inputs, "_Window")
    assert isinstance(_review_window(at("2026-10-10 08:00:00"), ZONE, False), DayWindow)
    source = inspect.getsource(day_plan_inputs)
    assert "covers(" not in source and "ends_within(" not in source
