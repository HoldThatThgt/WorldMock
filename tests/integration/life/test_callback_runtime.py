"""真实开始、结算、收货和结束边界只执行相应声明回调。"""

import pytest

from tests.life.conftest import advance_next, start_activity
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
    rules = [rule("drive-day", {"class": "action", "key": "drive"},
                  {"type": "state_delta", "state": "driving_stock", "delta": -1, "once_per": "local_day"})]
    engine = engine_factory(callback_environment(tmp_path, rules))
    _, started = act(engine, "drive", start_activity("drive", "公司"))
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
    _, next_trip = act(engine, "return", start_activity("drive", "家"))
    finish_activity(engine, next_trip, "return")
    assert engine.context.state.agents[1].callback_states == {"driving_stock": 2}
    assert len(changes(engine)) == 1


def test_unpaid_callback_expense_does_not_block_accepted_activity(engine_factory, tmp_path):
    rules = [rule("unaffordable", {"class": "action", "key": "sleep"},
                  {"type": "transfer", "direction": "expense", "amount_fen": 200001})]
    engine = engine_factory(callback_environment(tmp_path, rules))
    _, result = act(engine, "sleep", start_activity("sleep", "家"))
    assert result.status == "in_progress"
    assert engine.context.state.accounts == {"agent:1": 200000, "external:scenario": -200000}
    effect = changes(engine)[0]
    assert effect.data["status"] == "unpaid" and effect.resource_changes == {}


def test_threshold_fires_from_original_effect_once_without_cascading(engine_factory, tmp_path):
    rules = [rule("drive-stock", {"class": "action", "key": "drive"},
                  {"type": "state_delta", "state": "driving_stock", "delta": -3}),
             rule("stock-fee", {"class": "state", "key": "driving_stock", "below": 1},
                  {"type": "transfer", "direction": "expense", "amount_fen": 100})]
    engine = engine_factory(callback_environment(tmp_path, rules))
    _, result = act(engine, "drive", start_activity("drive", "公司"))
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
    _, result = act(engine, "sleep", start_activity("sleep", "家"))
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
