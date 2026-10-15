"""离这一集结束不足三分钟时暂停在地点做的系列：做完这一集还会接下一集时，工具层 pause_activity 一行与提交后的拒绝都说
这一集快完了、之后接着做下一集；做到最后一集时照旧说这件事快做完了（docs/design/04-simulation-engine.md「动作、名称与
并行」）。拒绝不改世界、不写暂停事件，人物同一刻再决策。

都经真实请求、引擎与 SQLite：meal_environment 副本，运行从 2026 年 1 月 5 日（周一）08:00 开始，人在家，08:00:00 点名
「做晚饭」（《备菜做饭》1800 秒、《吃晚饭》1500 秒、《洗碗收拾厨房》900 秒）。
"""

from __future__ import annotations

from tests.life.conftest import advance_until_ready, do_at, pause_request, target_tools
from tests.life.environment_inputs import meal_environment
from tests.life.environment_runtime import act, events, reach

_NAME = "进行做晚饭（1月5日 08:00 起）"
#: 会接下一集时的原因、下一步与改法。
CONTINUING = (f"{_NAME} cannot be paused while its current episode ends in under three minutes; "
              "it then continues with the next episode.",
              f"Conclude {_NAME}, or keep it running.", "Conclude this activity or keep it running instead.")
#: 不再接续时今天的原因、下一步与改法。
ENDING = (f"The activity {_NAME} ends in under three minutes and cannot be paused.",
          f"Conclude {_NAME} or let it finish.", "Conclude this activity or let it finish instead.")


def _decides_after(seconds: int):
    """运行配置：重估间隔改成给定秒数，决策落在开始后的这些秒数上。"""
    def mutate(persona, config):
        """只改重估间隔。"""
        config["reconsideration_interval_seconds"] = seconds
    return mutate


def _cooking(engine_factory, tmp_path, seconds: int):
    """08:00:00 点名「做晚饭」的真实引擎；返回引擎、活动身份与开始时刻。"""
    engine = engine_factory(meal_environment(tmp_path / "meal"), mutate=_decides_after(seconds))
    start = engine.context.state.current_time
    _, result = act(engine, "dinner", do_at("家", "做晚饭"))
    assert result.status == "in_progress", result.error_code
    assert engine.context.state.activities[result.activity_id].name == _NAME
    return engine, result.activity_id, start


def _refused_pause(engine, activity_id: str, wording: tuple[str, str, str], state: str) -> None:
    """此刻点名这项活动暂停：工具层这一行不可用、原因就是拒绝的原因，结束与播报可用，目标 state 如给定；提交被拒，原因、
    下一步与改法如给定；活动快照不变，没有暂停事件，人物同一刻再决策。"""
    reason, next_step, fix = wording
    before = engine.context.state.activities[activity_id].model_dump(mode="json")
    observed, refused = act(engine, "pause", pause_request(_NAME), "pause_activity")
    tools = target_tools(observed, _NAME)
    assert tools["pause_activity"] == {"tool": "pause_activity", "available": False, "reason": reason}
    assert tools["conclude_activity"] == {"tool": "conclude_activity", "available": True, "reason": None}
    assert tools["report_status"] == {"tool": "report_status", "available": True, "reason": None}
    target = next(row for row in observed["tool_layer"]["targets"] if row["name"] == _NAME)
    assert target["state"] == state
    assert refused.status == "rejected" and refused.error_code == "ACTIVITY_PAUSE_UNAVAILABLE"
    assert refused.message == refused.data["reason"] == reason
    assert (refused.data["next_step"], refused.data["fix"]) == (next_step, fix)
    assert engine.context.state.activities[activity_id].model_dump(mode="json") == before
    assert not [row for row in events(engine, "environment_event") if row.data.get("kind") == "activity_paused"]
    assert engine.scheduling_state().ready_agent_ids == (1,)


def test_pause_179_seconds_before_a_non_last_episode_ends_says_the_series_continues(engine_factory, tmp_path):
    """重估间隔 1621 秒，开始后第一次决策在 08:27:01，第一集还剩 179 秒：拒绝是续接的三句；不收尾的话 08:30:00 接上
    《吃晚饭》，活动仍在进行中。"""
    engine, activity_id, start = _cooking(engine_factory, tmp_path, 1621)
    assert advance_until_ready(engine)
    activity = engine.context.state.activities[activity_id]
    assert engine.context.state.current_time == start + 1621
    assert activity.data["content_id"] == "dinner-1-cook"
    assert activity.planned_end_at - engine.context.state.current_time == 179
    _refused_pause(engine, activity_id, CONTINUING, "第 1 集，共 3 集；第 2 集供一餐；已做约 27 分钟，还剩约 3 分钟")
    reach(engine, start + 1800)
    assert engine.context.state.current_time == start + 1800
    activity = engine.context.state.activities[activity_id]
    assert (activity.status, activity.data["content_id"]) == ("active", "dinner-2-eat")


def test_pause_179_seconds_before_the_last_episode_ends_keeps_the_ending_wording(engine_factory, tmp_path):
    """重估间隔 421 秒，推进到 09:07:01，最后一集《洗碗收拾厨房》还剩 179 秒：拒绝仍是今天的三句；09:10:00 做完待收尾。"""
    engine, activity_id, start = _cooking(engine_factory, tmp_path, 421)
    reach(engine, start + 3600 + 421)
    activity = engine.context.state.activities[activity_id]
    assert engine.context.state.current_time == start + 3600 + 421
    assert activity.data["content_id"] == "dinner-3-wash"
    assert activity.planned_end_at - engine.context.state.current_time == 179
    _refused_pause(engine, activity_id, ENDING, "第 3 集，共 3 集；第 2 集供一餐；已做约 1 小时，还剩约 3 分钟")
    reach(engine, start + 4200)
    activity = engine.context.state.activities[activity_id]
    assert (activity.status, activity.ended_at) == ("completed", start + 4200)
