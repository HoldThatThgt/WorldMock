"""同一可读动作请求启动两条独立生活活动的真实验收。"""

from __future__ import annotations

from agentsociety2.society import DirectToolCall

from tests.life.conftest import (advance_next, conclude_request, context, pending_names, stage_day_plan, start_activity,
                                 start_request, report_request)
from worldmock.life.activity_control import PHASE_ACTIVE
from worldmock.life.vocabulary import CONTENT_WAYS

#: 播客的续听方式：只占耳朵，本文件的「观看」只取占眼睛和耳朵的内容（第五章占用表）。
_LISTEN_WAYS = frozenset({CONTENT_WAYS[(True, False)], CONTENT_WAYS[(True, True)]})


def _met(note: str | None) -> bool:
    """@brief 动作说明里没有未满足的条件：说明为空，或内容对象只写了时长（「全长…」「还剩…」，第五章）。"""
    return note is None or note.startswith(("全长", "还剩"))


def _choice(observation: dict, action: str) -> tuple[dict, dict]:
    """@brief 从真实观察中取一个条件已满足的对象行与所选动作（内部标准动作为 action，出行只取步行）。"""
    return next((row, choice)
                for row in observation["interaction_bindings"]
                for choice in row["actions"] if choice["canonical_action"] == action and _met(choice["note"])
                and (action != "travel_to" or choice["travel_mode"] == "walking")
                and (action != "watch" or choice["label"] not in _LISTEN_WAYS))


def _start_pair_with_choices(engine, first: str, second: str, round_id: str):
    """@brief 在同一冻结观察中逐对象选择（对象，动词，方式）并结算一次决策；返回结果与所选的对象行和动作。"""
    decision = context(engine, round_id)
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    choices = [_choice(observed, action) for action in (first, second)]
    request = start_request(targets=[row["object"] for row, _ in choices],
                                   actions=[choice["verb"] for _, choice in choices],
                                   ways=[choice["label"] for _, choice in choices])
    engine.submit_action(DirectToolCall(decision, f"action-{round_id}", "start_activity", request))
    return engine.commit_decision_round(round_id)[0], choices


def _start_pair(engine, first: str, second: str, round_id: str):
    """@brief 在同一冻结观察中逐对象选择动作并结算一次决策。"""
    return _start_pair_with_choices(engine, first, second, round_id)[0]


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


def test_walk_and_watch_start_atomically_with_distinct_activity_ids(engine_factory) -> None:
    """一条决策允许步行与观看，各有独立活动身份；出发结束运行开始的停留会话，两项同归一段路上会话（第七章）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    stay = next(iter(engine.context.state.sessions.values()))
    result = _start_pair(engine, "travel_to", "watch", "walk-watch")
    assert result.status == "in_progress", result.error_code
    assert result.activity_id is None
    activities = [item for item in engine.context.state.activities.values()
                  if item.origin_action_id == result.action_id]
    assert len(activities) == 2
    assert len({item.activity_id for item in activities}) == 2
    assert {item.kind.value for item in activities} == {"travel", "watch"}
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
    result = _start_pair(engine, "watch", "sleep", "watch-sleep-conflict")
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
    result = _start_pair(engine, "watch", "wait", "parallel-terminal")
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
    watch = next(engine.context.state.activities[item] for item in ids if item != first[0].activity_id)
    assert watch.status == "active"
    assert engine.context.state.processed_actions[action_id].result["status"] == "in_progress"
    assert engine.scheduling_state().ready_agent_ids == (1,)
    now = engine.context.state.current_time
    _advance_with_decisions(engine, 100)
    assert engine.context.state.current_time == now
    assert engine.context.state.activities[first[0].activity_id].concluded_at == now
    assert engine.context.state.activities[watch.activity_id].status == "active"
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
