"""同一可读动作请求启动两条独立生活活动的真实验收。"""

from __future__ import annotations

from agentsociety2.society import DirectToolCall

from tests.life.conftest import advance_next, context, stage_day_plan, submit_round
from worldmock.life.models import ReportStatusRequest, StartActivityRequest


def _choice(observation: dict, action: str) -> tuple[str, str]:
    """@brief 从真实观察中取一个人类可读对象与动作短语。"""
    return next((row["object"], choice["label"])
                for row in observation["interaction_bindings"]
                for choice in row["actions"] if choice["canonical_action"] == action
                and (action != "travel_to" or choice["travel_mode"] == "walking"))


def _start_pair(engine, first: str, second: str, round_id: str):
    """@brief 在同一冻结观察中逐对象选择动作并结算一次决策。"""
    decision = context(engine, round_id)
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    choices = [_choice(observed, action) for action in (first, second)]
    request = StartActivityRequest(targets=[item[0] for item in choices],
                                   actions=[item[1] for item in choices])
    engine.submit_action(DirectToolCall(decision, f"action-{round_id}", "start_activity", request))
    return engine.commit_decision_round(round_id)[0]


def _advance_with_decisions(engine, index: int) -> None:
    """@brief 活动反馈唤醒人物时先真实播报，再推进下一环境边界。"""
    ready = engine.scheduling_state().ready_agent_ids
    if ready:
        requests = {}
        for agent_id in ready:
            sessions = [item for item in engine.state.sessions.values()
                        if item.agent_id == agent_id and item.status == "active"]
            if sessions:
                requests[agent_id] = ("report_status", ReportStatusRequest(
                    task=[sessions[0].session_name], summary=["仍在继续"]))
            else:
                requests[agent_id] = ("start_activity", StartActivityRequest(
                    targets=["自己"], actions=["等待"]))
        submit_round(engine, requests, f"parallel-feedback-{index}")
        return
    advance_next(engine)


def test_walk_and_watch_start_atomically_with_distinct_activity_ids(engine_factory) -> None:
    """一条决策允许步行与观看，各有独立活动和会话身份。"""
    engine = engine_factory()
    stage_day_plan(engine)
    result = _start_pair(engine, "travel_to", "watch", "walk-watch")
    assert result.status == "in_progress", result.error_code
    assert result.activity_id is None
    activities = [item for item in engine.state.activities.values()
                  if item.origin_action_id == result.action_id]
    assert len(activities) == 2
    assert len({item.activity_id for item in activities}) == 2
    assert {item.kind.value for item in activities} == {"travel", "watch"}
    ids = {item.activity_id for item in activities}
    sessions = [item for item in engine.state.sessions.values() if set(item.activity_ids) & ids]
    assert len(sessions) == 2
    assert all(len(item.activity_ids) == 1 for item in sessions)


def test_pair_failure_rejects_all_without_partial_activity(engine_factory) -> None:
    """后项资源冲突时，先项也不进入权威世界。"""
    engine = engine_factory()
    stage_day_plan(engine)
    before = engine.state.model_dump(mode="json")
    result = _start_pair(engine, "watch", "sleep", "watch-sleep-conflict")
    assert result.status == "rejected", result.error_code
    assert engine.state.activities == {}
    assert engine.state.sessions == {}
    assert engine.state.accounts == before["accounts"]


def test_parallel_origin_stays_in_progress_until_both_finish(engine_factory) -> None:
    """先结束的子项不将原动作提前标记为完成。"""
    engine = engine_factory()
    stage_day_plan(engine)
    result = _start_pair(engine, "travel_to", "watch", "parallel-terminal")
    assert result.status == "in_progress", result.error_code
    action_id = result.action_id
    for _ in range(100):
        activities = [item for item in engine.state.activities.values()
                      if item.origin_action_id == action_id]
        if any(item.status == "completed" for item in activities):
            break
        _advance_with_decisions(engine, _)
    assert any(item.status == "completed" for item in activities)
    if any(item.status == "active" for item in activities):
        assert engine.state.processed_actions[action_id].result["status"] == "in_progress"
    for _ in range(100):
        if all(item.status == "completed" for item in activities):
            break
        _advance_with_decisions(engine, _ + 100)
        activities = [engine.state.activities[item.activity_id] for item in activities]
    assert all(item.status == "completed" for item in activities)
    assert engine.state.processed_actions[action_id].result["status"] == "completed"
    events = [event for event in engine.store.read_agent_events("action_result", 1)
              if event.action_id == action_id and event.data["status"] != "in_progress"]
    assert len(events) == 1
