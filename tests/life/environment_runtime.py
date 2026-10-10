"""当前冻结环境的真实提交与逐边界推进帮助函数。"""

from agentsociety2.society import DirectToolCall
from tests.life.conftest import (
    advance_next, conclude_request, context, pause_request, report_request, start_activity,
)


def act(engine, label, request, tool="start_activity", action_id=None):
    """从当前观察登记一项真实请求并原子结算。"""
    decision = context(engine, label)
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    engine.submit_action(DirectToolCall(decision, action_id or f"action-{label}", tool, request))
    return observed, engine.commit_decision_round(label)[0]


def events(engine, kind=None):
    """只读取已提交的真实事实。"""
    rows = engine.context.store.read_events(engine.context.store.last_event_seq)
    return rows if kind is None else [row for row in rows if row.event_type == kind]


def settle_decision(engine, label):
    """按本轮实际活动完成收尾、播报，或选择真实家中睡眠。"""
    state = engine.context.state
    pending = [item.name for item in state.activities.values()
               if item.status not in {"active", "paused"} and item.concluded_at is None]
    if pending:
        return act(engine, label, conclude_request(*pending), "conclude_activity")
    active = [item.name for item in state.activities.values() if item.status == "active"]
    if active:
        return act(engine, label, report_request(*active), "report_status")
    person = state.agents[1]
    home = engine.context.catalog.locations[engine.context.inputs.home_location_id].display_name
    return act(engine, label, start_activity("sleep" if person.location_id == engine.context.inputs.home_location_id
        else "walk", home))


def reach(engine, target):
    """逐边界推进并处理实际决策机会，不跳过付款或内容边界。"""
    counter = 0
    while engine.context.state.current_time < target:
        if engine.scheduling_state().ready_agent_ids:
            counter += 1
            settle_decision(engine, f"reach-report-{counter}-{engine.context.state.current_time}")
            continue
        next_event = engine.scheduling_state().next_event_at
        assert next_event is not None
        assert int(next_event.timestamp()) <= target
        advance_next(engine)


def finish_activity(engine, result, label):
    """推进原始活动到真实终局，再按本人活动名称收尾。"""
    identity = result.activity_id
    while engine.context.state.activities[identity].status == "active":
        if engine.scheduling_state().ready_agent_ids:
            settle_decision(engine, f"{label}-report-{engine.context.state.current_time}")
        else:
            advance_next(engine)
    current = engine.context.state.activities[identity]
    if current.concluded_at is None and engine.scheduling_state().ready_agent_ids:
        return act(engine, f"{label}-conclude", conclude_request(current.name), "conclude_activity")[1]
    return current


def run_to_end(engine):
    """一直处理真实活动和决策，直到运行正常截止。"""
    counter = 0
    while engine.context.state.current_time < engine.context.state.end_time:
        counter += 1
        assert counter < 2000
        if engine.scheduling_state().ready_agent_ids:
            _, result = settle_decision(engine, f"finish-{counter}")
            assert result.status in {"in_progress", "completed"}, result.error_code
        else:
            assert engine.scheduling_state().next_event_at is not None
            advance_next(engine)
    return events(engine)
