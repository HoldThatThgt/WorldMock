"""同一可读动作请求启动两条独立生活活动的真实验收。"""

from __future__ import annotations

from agentsociety2.society import DirectToolCall

from tests.life.conftest import (advance_next, conclude_request, context, pending_names, stage_day_plan, start_activity,
                                 start_request, report_request)
from tests.integration.life.test_location_sessions import _pick
from worldmock.life.activity_control import PHASE_ACTIVE

def _start_pair_with_choices(engine, first: str, second: str, round_id: str, action_id="browse-feed"):
    """@brief 同一冻结观察选择完整当前项，原子提交后返回回执与实际选择。"""
    decision = context(engine, round_id)
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    choices = [_pick(observed, action, mode="walking" if action == "travel_to" else None,
                     kind="action" if action == "do" else None, place=action_id if action == "do" else None)
               for action in (first, second)]
    request = start_request(items=choices, answering=None)
    engine.submit_action(DirectToolCall(decision, f"action-{round_id}", "start_activity", request))
    return engine.commit_decision_round(round_id)[0], choices


def _start_pair(engine, first: str, second: str, round_id: str, action_id="browse-feed"):
    """@brief 逐项明确选择动作与应用并结算一次真实决策。"""
    return _start_pair_with_choices(engine, first, second, round_id, action_id)[0]


def _control_request(observed: dict) -> tuple[str, object]:
    """@brief 按本轮观察选控制：刚结束的活动当秒收尾，否则按活动名播报一项进行中活动，没有活动时等待。"""
    pending = pending_names(observed)
    if pending:
        return "conclude_activity", conclude_request(*pending)
    running = [row["name"] for row in observed["activity_bindings"] if row["phase"] == PHASE_ACTIVE]
    if running:
        return "report_status", report_request(*running[:1], summary="仍在继续")
    return "start_activity", start_activity("等待", "自己")


def _advance_with_decisions(engine, index: int) -> None:
    """@brief 人物获得决策机会时按本轮观察真实决策（第七章：活动一结束就要结束它），否则推进下一环境边界。"""
    ready = engine.scheduling_state().ready_agent_ids
    if not ready:
        advance_next(engine)
        return
    round_id = f"parallel-feedback-{index}"
    contexts = [context(engine, round_id, agent_id) for agent_id in ready]
    engine.begin_decision_round(contexts)
    requests = {item.agent_id: _control_request(engine.observe(item)) for item in contexts}
    for item in reversed(contexts):
        tool_name, request = requests[item.agent_id]
        engine.submit_action(DirectToolCall(item, f"action-{round_id}-{item.agent_id}", tool_name, request))
    engine.commit_decision_round(round_id)


def test_walk_and_app_start_atomically_with_distinct_activity_ids(engine_factory) -> None:
    """一条决策允许步行与使用 app，各有独立活动身份；出发结束运行开始的停留会话，两项同归一段路上会话（第七章）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    stay = next(iter(engine.context.state.sessions.values()))
    result = _start_pair(engine, "travel_to", "do", "walk-app")
    assert result.status == "in_progress", result.error_code
    assert result.activity_id is None
    activities = [item for item in engine.context.state.activities.values()
                  if item.origin_action_id == result.action_id]
    assert len(activities) == 2
    assert len({item.activity_id for item in activities}) == 2
    assert {item.kind.value for item in activities} == {"travel", "action"}
    ids = {item.activity_id for item in activities}
    ended = engine.context.state.sessions[stay.session_id]
    assert ended.kind == "stay" and ended.status == "ended" and ended.activity_ids == []
    assert ended.ended_at == engine.context.state.current_time
    road = [item for item in engine.context.state.sessions.values() if set(item.activity_ids) & ids]
    assert len(road) == 1 and road[0].kind == "transit" and road[0].status == "active"
    travel = next(item for item in activities if item.kind.value == "travel")
    assert (road[0].origin_id, road[0].destination_id) == (stay.location_id, travel.destination_id)
    assert set(road[0].activity_ids) == ids
    assert all(item.session_ids == [road[0].session_id] for item in activities)


def test_pair_failure_rejects_all_without_partial_activity(engine_factory) -> None:
    """后项资源冲突时，先项也不进入权威世界；会话只剩运行开始的停留会话且未改动。"""
    engine = engine_factory()
    stage_day_plan(engine)
    before = engine.context.state.model_dump(mode="json")
    result = _start_pair(engine, "do", "sleep", "app-sleep-conflict")
    assert result.status == "rejected", result.error_code
    assert result.error_code == "RESOURCE_CONFLICT"
    assert engine.context.state.activities == {}
    sessions = list(engine.context.state.sessions.values())
    assert [(item.kind, item.status, item.activity_ids) for item in sessions] == [("stay", "active", [])]
    assert engine.context.state.model_dump(mode="json")["sessions"] == before["sessions"]
    assert engine.context.state.accounts == before["accounts"]


def test_parallel_origin_stays_in_progress_until_both_finish(engine_factory) -> None:
    """先结束的子项当秒收尾而不把原动作提前标记为完成，另一项照常继续；全部结束才写唯一终态（第七章）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    result = _start_pair(engine, "do", "wait", "parallel-terminal", "study-course")
    assert result.status == "in_progress", result.error_code
    action_id = result.action_id
    ids = sorted(item.activity_id for item in engine.context.state.activities.values()
                 if item.origin_action_id == action_id)
    for index in range(100):
        if any(engine.context.state.activities[item].status == "completed" for item in ids):
            break
        _advance_with_decisions(engine, index)
    first = [engine.context.state.activities[item] for item in ids if engine.context.state.activities[item].status == "completed"]
    assert [item.kind.value for item in first] == ["wait"] and first[0].concluded_at is None
    app = next(engine.context.state.activities[item] for item in ids if item != first[0].activity_id)
    assert app.status == "active"
    assert engine.context.state.processed_actions[action_id].result["status"] == "in_progress"
    assert engine.scheduling_state().ready_agent_ids == (1,)
    now = engine.context.state.current_time
    _advance_with_decisions(engine, 100)
    assert engine.context.state.current_time == now
    assert engine.context.state.activities[first[0].activity_id].concluded_at == now
    assert engine.context.state.activities[app.activity_id].status == "active"
    assert engine.context.state.processed_actions[action_id].result["status"] == "in_progress"
    for index in range(101, 300):
        if all(engine.context.state.activities[item].status == "completed" for item in ids):
            break
        _advance_with_decisions(engine, index)
    assert all(engine.context.state.activities[item].status == "completed" for item in ids)
    assert engine.context.state.processed_actions[action_id].result["status"] == "completed"
    events = [event for event in engine.context.store.read_agent_events("action_result", 1)
              if event.action_id == action_id and event.data["status"] != "in_progress"]
    assert len(events) == 1
