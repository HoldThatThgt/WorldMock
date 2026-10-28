"""当前冻结环境的真实提交、逐边界推进与只读观察的帮助函数，以及几种常用的运行配置。"""

from datetime import timedelta

from agentsociety2.society import DirectToolCall
from tests.life.conftest import advance_next, conclude_request, context, report_request, sleep, tool_of, travel


def act(engine, label, request, tool=None, action_id=None):
    """从当前观察登记一项真实请求并原子结算；工具名缺省由请求模型给出。"""
    decision = context(engine, label)
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    identity = action_id or f"{decision.decision_id}:action:0001"
    engine.submit_action(DirectToolCall(decision, identity, tool or tool_of(request), request))
    return observed, engine.commit_decision_round(label)[0]


def act_group(engine, label, *requests):
    """同一决策里按给定先后登记一个开始组（两个开始工具各一条）并整组结算。

    返回观察与各动作的最终存档结果（按登记次序）；环境向框架只返回第一条动作的结果，两条结果都已落库。
    """
    decision = context(engine, label)
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    identities = [f"{decision.decision_id}:action:{index:04d}" for index, _ in enumerate(requests, start=1)]
    for identity, request in zip(identities, requests, strict=True):
        engine.submit_action(DirectToolCall(decision, identity, tool_of(request), request))
    returned = engine.commit_decision_round(label)
    assert [item.action_id for item in returned] == identities[:1]
    results = [action_result(engine, identity) for identity in identities]
    return observed, results


def action_result(engine, action_id):
    """一条动作第一次写下的结果事件（受理或拒绝那一刻）的结果对象。"""
    from worldmock.state.decisions.results import ActionResult

    row = next(event for event in events(engine, "action_result") if event.action_id == action_id)
    return ActionResult.model_validate({key: row.data[key] for key in ActionResult.model_fields if key in row.data})


def events(engine, kind=None):
    """只读取已提交的真实事实。"""
    rows = list(engine.context.store.iter_events(engine.context.store.last_event_seq))
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


def until_ended(engine, activity_id):
    """推进到这项活动终结：其间的决策机会只播报进行中的活动、给别的已结束活动收尾，不替这项活动收尾。

    不要用在会自动接续的 app 使用上，它会把整个可推送的内容放完；那种用 conclude_app_use。
    """
    while engine.context.state.activities[activity_id].status == "active":
        if engine.scheduling_state().ready_agent_ids:
            settle_decision(engine, f"settle-{engine.context.state.current_time}")
        else:
            advance_next(engine)


def conclude_app_use(engine, activity_id, label, items=1):
    """推进一次 app 使用，等它放到至少 items 条、并且人物就绪时收尾它，返回这次收尾的动作结果。

    其间人物就绪而条数不够时用 settle_decision 处理这次决策（照旧收尾别的待收尾活动、播报进行中的活动，包括这次使用），
    不就绪时 advance_next。条数不够而这项活动已不在进行中（自己停下了或被暂停），或这项活动已经收尾，都断言失败并点名
    活动。条件满足后在同一次 conclude_activity 里点名这项活动和此刻全部别的待收尾活动：同一秒还有活动在进行时不会再有
    同秒决策，所以要一次点全。

    :param items: 收尾之前至少放过的条数（data.played 的项数）。
    """
    while True:
        activity = engine.context.state.activities[activity_id]
        played = len(activity.data["played"])
        assert activity.concluded_at is None, f"{activity.name} was already concluded"
        assert played >= items or activity.status == "active", f"{activity.name} stopped after {played} items"
        ready = engine.scheduling_state().ready_agent_ids
        if played >= items and ready:
            break
        if ready:
            settle_decision(engine, f"{label}-report-{engine.context.state.current_time}")
        else:
            advance_next(engine)
    others = [item.name for item in engine.context.state.activities.values() if item.activity_id != activity_id
              and item.status not in {"active", "paused"} and item.concluded_at is None]
    return act(engine, f"{label}-conclude", conclude_request(activity.name, *others), "conclude_activity")[1]


def observe_only(engine, label):
    """人物此刻的一次观察（只读，不登记请求）；轮次身份带上此刻，同一标签可以在不同时刻重复使用。"""
    return engine.observe(context(engine, f"{label}-{engine.context.state.current_time}"))


def check_every_step(monkeypatch, engine, check) -> None:
    """在引擎实例上包住 commit_decision_round、advance 与 decide_midnight_rhythm：调用原方法之后（零点判定未完成时除外）
    执行 check(engine, origins)；origins 是最近一次 advance 调用之前进行中或暂停活动的 origin_action_id 集合。"""
    origins = [frozenset()]

    def wrapped(name):
        """包住引擎实例上的一个方法。"""
        original = getattr(engine, name)

        def step(*args, **kwargs):
            """推进之前记下 origins，调用原方法后核对。"""
            if name == "advance":
                origins[0] = _unfinished_origins(engine.context.state)
            result = original(*args, **kwargs)
            if not engine.midnight_rhythm_pending:
                check(engine, origins[0])
            return result

        return step

    for name in ("commit_decision_round", "advance", "decide_midnight_rhythm"):
        monkeypatch.setattr(engine, name, wrapped(name))


def ledger_holds_only_readable_entries(state, origins: frozenset[str]) -> None:
    """processed_actions 只含规则还会读的条目：本秒结算的结果与最近一次推进前未终结活动的开始动作。"""
    keys = set(state.processed_actions)
    for key in keys:
        assert key in origins or state.processed_actions[key].result["occurred_at"] == state.current_time, key
    assert origins <= keys, sorted(origins - keys)
    live = _unfinished_origins(state)
    assert live <= keys, sorted(live - keys)


def _unfinished_origins(state) -> frozenset[str]:
    """此刻进行中或暂停的活动的 origin_action_id。"""
    return frozenset(activity.origin_action_id for activity in state.activities.values()
                     if activity.status in {"active", "paused"})


def every_five_minutes(persona, config):
    """运行配置：每五分钟到一次重估间隔，人物在路上、在一件事做到一半时都有决策机会。"""
    config["reconsideration_interval_seconds"] = 300


def one_hour(persona, config):
    """运行配置：一小时的运行（运行窗口必须落在整点上）。"""
    config["stop_time"] = config["start_time"] + timedelta(hours=1)


def one_hour_every_five_minutes(persona, config):
    """运行配置：一小时的运行，每五分钟到一次重估间隔。"""
    one_hour(persona, config)
    every_five_minutes(persona, config)
