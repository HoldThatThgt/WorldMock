"""当前冻结环境的真实提交与逐边界推进帮助函数。"""

from agentsociety2.society import DirectToolCall
from tests.life.conftest import (
    advance_next, conclude_request, context, report_request, sleep, tool_of, travel,
)


def act(engine, label, request, tool=None, action_id=None):
    """从当前观察登记一项真实请求并原子结算；工具名缺省由请求模型给出。"""
    decision = context(engine, label)
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    engine.submit_action(DirectToolCall(decision, action_id or f"action-{label}", tool or tool_of(request), request))
    return observed, engine.commit_decision_round(label)[0]


def act_group(engine, label, *requests):
    """同一决策里按给定先后登记一个开始组（两个开始工具各一条）并整组结算。

    返回观察与各动作的最终存档结果（按登记次序）；环境向框架只返回第一条动作的结果，两条结果都已落库。
    """
    decision = context(engine, label)
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    identities = [f"action-{label}-{index}" for index, _ in enumerate(requests, start=1)]
    for identity, request in zip(identities, requests, strict=True):
        engine.submit_action(DirectToolCall(decision, identity, tool_of(request), request))
    returned = engine.commit_decision_round(label)
    assert [item.action_id for item in returned] == identities[:1]
    results = [action_result(engine, identity) for identity in identities]
    return observed, results


def action_result(engine, action_id):
    """一条动作第一次写下的结果事件（受理或拒绝那一刻）的结果对象。"""
    from worldmock.life.models import ActionResult

    row = next(event for event in events(engine, "action_result") if event.action_id == action_id)
    return ActionResult.model_validate({key: row.data[key] for key in ActionResult.model_fields if key in row.data})


def events(engine, kind=None):
    """只读取已提交的真实事实。"""
    rows = engine.context.store.read_events(engine.context.store.last_event_seq)
    return rows if kind is None else [row for row in rows if row.event_type == kind]


def settle_decision(engine, label):
    """按本轮实际活动完成收尾、播报；没有活动时在本人的家睡一夜，不在家就步行回家。"""
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
    at_home = person.location_id == engine.context.inputs.home_location_id
    return act(engine, label, sleep() if at_home else travel(home))


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
