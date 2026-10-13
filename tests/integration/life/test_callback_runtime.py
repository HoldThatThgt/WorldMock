"""真实开始、结算、收货和结束边界只执行相应声明回调。"""

import pytest

from tests.life.conftest import advance_next, query, sleep, travel
from tests.life.environment_inputs import copy_environment, write_catalog_rows
from tests.life.environment_runtime import act, events, finish_activity, run_to_end


def callback_environment(tmp_path, rules, initial=3):
    root = copy_environment(tmp_path / "input")
    state = {"state": "driving_stock", "initial": initial, "cap": 3, "reset_on_goods": ["tissue"]}
    write_catalog_rows(root, "callbacks.jsonl", [state, *sorted(rules, key=lambda item: item["rule_id"])])
    return root


def rule(name, trigger, effect, **extra):
    return {"rule_id": name, "trigger": trigger, "effect": effect, **extra}


def changes(engine, kind="callback_effect"):
    return [row for row in events(engine, "environment_event") if row.data.get("kind") == kind]


def test_run_start_is_once_and_does_not_trigger_startup_wake_subscription(engine_factory, tmp_path):
    rules = [rule("initial-income", {"class": "time", "key": "run_start"},
                  {"type": "transfer", "direction": "income", "amount_fen": 500}),
             rule("wake-stock", {"class": "event", "key": "time_boundary"},
                  {"type": "state_delta", "state": "driving_stock", "delta": -1})]
    engine = engine_factory(callback_environment(tmp_path, rules))
    assert engine.context.state.accounts == {"agent:1": 200500, "external:scenario": -200500}
    assert engine.context.state.agents[1].callback_states == {"driving_stock": 3}
    effects = changes(engine)
    assert len(effects) == 1 and effects[0].data["rule_id"] == "initial-income"
    source = next(row for row in events(engine) if row.event_id == effects[0].caused_by[0])
    assert source.data["key"] == "run_start" and source.source_kind == "environment"


def test_action_daily_limit_waits_for_actual_time_and_is_persisted(engine_factory, tmp_path):
    rules = [rule("drive-day", {"class": "action", "key": "travel", "travel_method_id": "driving"},
                  {"type": "state_delta", "state": "driving_stock", "delta": -1, "once_per": "local_day"})]
    engine = engine_factory(callback_environment(tmp_path, rules))
    _, started = act(engine, "drive", travel("公司", "驾车"))
    assert started.status == "in_progress"
    assert engine.context.state.agents[1].callback_states == {"driving_stock": 3}
    advance_next(engine)
    assert engine.context.state.agents[1].callback_states == {"driving_stock": 2}
    assert engine.context.state.agents[1].callback_once_keys == {"drive-day:2026-01-05"}
    effects = changes(engine)
    assert len(effects) == 1
    source = next(row for row in events(engine) if row.event_id == effects[0].caused_by[0])
    assert source.event_type == "action_result" and source.data["status"] == "in_progress"
    finish_activity(engine, started, "drive")
    _, next_trip = act(engine, "return", travel("家", "驾车"))
    finish_activity(engine, next_trip, "return")
    assert engine.context.state.agents[1].callback_states == {"driving_stock": 2}
    assert len(changes(engine)) == 1


def test_unpaid_callback_expense_does_not_block_accepted_activity(engine_factory, tmp_path):
    rules = [rule("unaffordable", {"class": "action", "key": "sleep"},
                  {"type": "transfer", "direction": "expense", "amount_fen": 200001})]
    engine = engine_factory(callback_environment(tmp_path, rules))
    _, result = act(engine, "sleep", sleep())
    assert result.status == "in_progress"
    assert engine.context.state.accounts == {"agent:1": 200000, "external:scenario": -200000}
    effect = changes(engine)[0]
    assert effect.data["status"] == "unpaid" and effect.resource_changes == {}


def test_threshold_fires_from_original_effect_once_without_cascading(engine_factory, tmp_path):
    rules = [rule("drive-stock", {"class": "action", "key": "travel", "travel_method_id": "driving"},
                  {"type": "state_delta", "state": "driving_stock", "delta": -3}),
             rule("stock-fee", {"class": "state", "key": "driving_stock", "below": 1},
                  {"type": "transfer", "direction": "expense", "amount_fen": 100})]
    engine = engine_factory(callback_environment(tmp_path, rules))
    _, result = act(engine, "drive", travel("公司", "驾车"))
    assert result.status == "in_progress"
    effects = changes(engine)
    assert [row.data["rule_id"] for row in effects] == ["drive-stock", "stock-fee"]
    assert effects[1].caused_by == effects[0].caused_by
    assert engine.context.state.agents[1].callback_states["driving_stock"] == 0
    advance_next(engine)
    assert len(changes(engine)) == 2


def test_callback_reminder_has_no_content_selection_or_wake(engine_factory, tmp_path):
    rules = [rule("stock-reminder", {"class": "time", "key": "hourly"},
        {"type": "state_delta", "state": "driving_stock", "delta": -1},
        notify={"when": {"state": "driving_stock", "below": 3}, "channel": "app_push",
                "app_id": "bilibili", "text": "Remember your supplies."})]
    engine = engine_factory(callback_environment(tmp_path, rules))
    _, result = act(engine, "sleep", sleep())
    end = engine.context.state.activities[result.activity_id].planned_end_at
    while engine.context.state.current_time < end:
        advance_next(engine)
        if engine.context.state.current_time < end:
            assert engine.scheduling_state().ready_agent_ids == ()
    assert changes(engine, "callback_reminder") == []


def test_normal_stop_is_once_after_real_activity_settlement(engine_factory, tmp_path):
    rules = [rule("final-income", {"class": "time", "key": "run_stop"},
                  {"type": "transfer", "direction": "income", "amount_fen": 500})]
    engine = engine_factory(callback_environment(tmp_path, rules))
    run_to_end(engine)
    stop = [row for row in events(engine, "environment_event")
            if row.data.get("kind") == "time_trigger" and row.data["key"] == "run_stop"]
    assert len(stop) == 1
    effect = changes(engine)[0]
    assert effect.occurred_at == engine.context.state.end_time
    assert effect.caused_by == [stop[0].event_id]
    assert engine.context.state.accounts == {"agent:1": 200500, "external:scenario": -200500}
    assert engine.context.state.run_stop_callbacks_applied is True
    assert engine.scheduling_state().next_event_at is None


@pytest.mark.parametrize("method,fired", [("打车", []), ("公共交通", []), ("驾车", ["drive-fee"]),
                                          ("步行", ["walk-stock"])])
def test_travel_callback_fires_only_for_trips_by_the_subscribed_travel_method(engine_factory, tmp_path, method, fired):
    """回调按出行方式的身份触发：一条规则订阅一种方式，速度和价格与驾车相同的打车不触发驾车的规则。"""
    rules = [rule("drive-fee", {"class": "action", "key": "travel", "travel_method_id": "driving"},
                  {"type": "transfer", "direction": "expense", "amount_fen": 30}),
             rule("walk-stock", {"class": "action", "key": "travel", "travel_method_id": "walking"},
                  {"type": "state_delta", "state": "driving_stock", "delta": -1})]
    engine = engine_factory(callback_environment(tmp_path, rules))
    _, result = act(engine, "trip", travel("公司", method))
    assert result.status == "in_progress", result.error_code
    effects = changes(engine)
    assert [row.data["rule_id"] for row in effects] == fired
    assert engine.context.state.agents[1].callback_states == {"driving_stock": 2 if fired == ["walk-stock"] else 3}
    accepted = next(row for row in events(engine, "action_result") if row.action_id == "action-trip")
    assert all(row.caused_by == [accepted.event_id] for row in effects)
    advance_next(engine)
    assert [row.data["rule_id"] for row in changes(engine)] == fired


def test_daily_travel_callback_settles_only_on_days_with_a_trip_by_its_travel_method(engine_factory, tmp_path):
    """按本地日限次的出行增量只在订阅的出行方式实际走过时执行：打车去、驾车回，只有驾车那一趟结算时扣一次。"""
    rules = [rule("drive-day", {"class": "action", "key": "travel", "travel_method_id": "driving"},
                  {"type": "state_delta", "state": "driving_stock", "delta": -1, "once_per": "local_day"})]
    engine = engine_factory(callback_environment(tmp_path, rules))
    _, taxi = act(engine, "taxi", travel("公司", "打车"))
    assert taxi.status == "in_progress", taxi.error_code
    finish_activity(engine, taxi, "taxi")
    assert engine.context.state.agents[1].callback_states == {"driving_stock": 3}
    assert engine.context.state.agents[1].callback_once_keys == set() and changes(engine) == []
    _, drive = act(engine, "drive-back", travel("家", "驾车"))
    assert drive.status == "in_progress", drive.error_code
    finish_activity(engine, drive, "drive-back")
    assert engine.context.state.agents[1].callback_states == {"driving_stock": 2}
    assert engine.context.state.agents[1].callback_once_keys == {"drive-day:2026-01-05"}
    [effect] = changes(engine)
    source = next(row for row in events(engine) if row.event_id == effect.caused_by[0])
    assert source.action_id == "action-drive-back" and source.event_type == "action_result"


def _navigation_world(root):
    """三个导航 app 各绑定不同的出行方式：步行只有高德，自行车只有百度，驾车三个都有，轮渡没有 app 绑定。"""
    from tests.life.environment_inputs import catalog_rows, travel_method_row

    write_catalog_rows(root, "travel-method.jsonl", [
        travel_method_row("bike", "自行车", 4.5), travel_method_row("driving", "驾车", 7, 500, 200),
        travel_method_row("ferry", "轮渡", 3)])
    rows = catalog_rows(root, "app-catalog.jsonl")
    next(row for row in rows if row["app_id"] == "amap")["travel_method_ids"] = ["driving", "walking"]
    rows.append({"app_id": "baidu-map", "name": "百度地图", "role": "function", "travel_method_ids": ["bike", "driving"]})
    rows.append({"app_id": "didi", "name": "滴滴出行", "role": "function", "travel_method_ids": ["driving"]})
    write_catalog_rows(root, "app-catalog.jsonl", sorted(rows, key=lambda row: row["app_id"]))
    return root


def _navigation(engine):
    """导航 app 的前台区间事件。"""
    return [row for row in events(engine, "environment_event")
            if row.data.get("kind") == "app_foreground" and row.data["step"] == "navigation"]


@pytest.mark.parametrize("method,identity,candidates", [
    ("步行", "walking", ("amap",)), ("自行车", "bike", ("baidu-map",)),
    ("驾车", "driving", ("amap", "baidu-map", "didi")), ("轮渡", "ferry", ())])
def test_navigation_app_is_chosen_only_among_apps_bound_to_the_trips_travel_method(
        engine_factory, tmp_path, method, identity, candidates):
    """导航 app 只从绑定了这次出行方式的功能 app 里等概率选一个；同一种子得到同一个；没有绑定就没有导航记录。
    出发那一秒只把导航记在人物状态里，不再写时间点；到达时写一条从出发到到达的前台区间。"""
    from worldmock.life.randomness import random_stream

    chosen, seeds = [], (range(1, 7) if len(candidates) > 1 else (1,))
    for seed in seeds:
        for attempt in ("first", "second"):
            root = _navigation_world(copy_environment(tmp_path / f"{seed}-{attempt}"))
            engine = engine_factory(root, mutate=lambda persona, config, seed=seed: config.update(seed=seed))
            assert engine.context.catalog.apps_by_travel_method[identity] == candidates
            _, result = act(engine, "trip", travel("公司", method))
            assert result.status == "in_progress", result.error_code
            departed, opened = result.occurred_at, engine.context.state.agents[1].navigation_foreground
            assert _navigation(engine) == []
            assert not [row for row in events(engine, "environment_event") if row.data.get("kind") == "app_touch"]
            finish_activity(engine, result, "trip")
            arrived = engine.context.state.activities[result.activity_id].ended_at
            if not candidates:
                assert opened is None and _navigation(engine) == []
                continue
            stream = random_stream(engine.context.state.random_state, f"function-app:{result.activity_id}")
            assert opened.model_dump() == {"activity_id": result.activity_id, "app_id": stream.choice(candidates),
                                           "opened_at": departed}
            [interval] = _navigation(engine)
            assert interval.data == {"kind": "app_foreground", "app_id": opened.app_id, "step": "navigation",
                                     "interval_start": departed, "interval_end": arrived}
            assert interval.activity_id == result.activity_id and interval.visibility == "system"
            chosen.append((seed, opened.app_id))
    # 每个种子两次运行选到同一个 app；候选不止一个时，不同种子选到的不总是同一个
    assert len(set(chosen)) == (len(seeds) if candidates else 0)
    distinct = {app for _, app in chosen}
    assert distinct <= set(candidates) and (len(candidates) < 2 or len(distinct) > 1)


def test_accepted_query_fires_no_action_callback(engine_factory, tmp_path):
    """环境里登记订阅购买、interact、睡眠的三条动作规则：真实受理一次查询并走到做完，没有任何回调效果事件，回调状态、
    按日去重键与余额都不变（受理时以查询的动作 query 求值一次，没有规则能订阅它）。"""
    rules = [rule(f"on-{key}", {"class": "action", "key": key},
                  {"type": "state_delta", "state": "driving_stock", "delta": -1})
             for key in ("purchase", "interact", "sleep")]
    engine = engine_factory(callback_environment(tmp_path, rules))
    accounts = dict(engine.context.state.accounts)
    _, asked = act(engine, "ask", query("柠檬茶"))
    assert asked.status == "in_progress"
    activity = asked.activity_id
    while engine.context.state.activities[activity].status == "active":
        advance_next(engine)
    assert engine.context.state.activities[activity].status == "completed"
    person = engine.context.state.agents[1]
    assert (person.callback_states, person.callback_once_keys) == ({"driving_stock": 3}, set())
    assert engine.context.state.accounts == accounts and changes(engine) == []
