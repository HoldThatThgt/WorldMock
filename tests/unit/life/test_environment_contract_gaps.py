"""验证真实环境区间、夏令时及按日回调的可观察合同。"""

from dataclasses import replace
from datetime import datetime
import json
import shutil
from types import SimpleNamespace

import pytest

from tests.data_paths import ENVIRONMENT_ROOT
from tests.frozen_inputs import fixture_metadata
from tests.life.conftest import do_at, purchase, travel
from tests.life.environment_runtime import act, reach
from worldmock.catalog.callback_models import CallbackRule, CallbackState
from worldmock.catalog.identity import refresh_manifest_identity
from worldmock.environment import load_environment
from worldmock.errors import IntegrityError
from worldmock.footprints.models import EventDraft
from worldmock.input_models import RunConfig
from worldmock.life.activity_validation import validate_activity_targets
from worldmock.life.callbacks import settle_callback_dates
from worldmock.life.rhythm import RhythmBoundary, settle_engine_rhythm
from worldmock.life.world_setup import _initial_state
from worldmock.scheduling.compile import compile_schedule


@pytest.fixture
def inputs():
    return load_environment(ENVIRONMENT_ROOT, metadata_loader=fixture_metadata)


def configured(inputs, start, stop, rules):
    values = {**{name: getattr(inputs.config, name) for name in RunConfig.model_fields}, "timezone": "America/New_York",
              "start_time": datetime.fromisoformat(start), "stop_time": datetime.fromisoformat(stop)}
    return replace(inputs, config=RunConfig.model_validate(values),
                   catalog=replace(inputs.catalog, callback_rules={rule.rule_id: rule for rule in rules}))


def callback(identity, trigger, effect):
    return CallbackRule.model_validate({"rule_id": identity, "trigger": trigger, "effect": effect})


def test_initial_hunger_bills_only_the_awake_time_beyond_two_hours(engine_factory):
    def mutate(persona, config):
        persona["initial_location"] = "公司"
        persona["initial_relaxation_need"] = 0.0
        persona["body"]["initial_energy_kcal"] = 0.0

    engine = engine_factory(mutate=mutate)
    started = engine.context.state.current_time
    _, result = act(engine, "hungry-work", do_at("公司", "季度销售数据整理"))
    assert result.status == "in_progress"
    reach(engine, started + 3 * 3600)
    assert engine.context.state.agents[1].body.relaxation_need == pytest.approx(0.34)


def test_closed_local_store_is_observable_and_refuses_purchase_without_world_failure(engine_factory, tmp_path):
    root = tmp_path / "closed-store"
    shutil.copytree(ENVIRONMENT_ROOT, root)
    path = root / "catalog/location.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        if row["location_id"] == "store":
            row["opens"] = {"weekly": {}}
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                    encoding="utf-8", newline="\n")
    refresh_manifest_identity(root / "catalog")
    persona = (root / "persona.toml").read_text(encoding="utf-8")
    (root / "persona.toml").write_text(persona.replace('initial_location = "家"', 'initial_location = "四季便利"'),
                                       encoding="utf-8", newline="\n")
    engine = engine_factory(environment_root=root)
    before = dict(engine.context.state.accounts)
    _, result = act(engine, "closed-purchase", purchase("饭团"))
    assert result.status == "rejected"
    assert result.error_code == "LOCATION_CLOSED"
    assert result.message == "The location is closed at the current time."
    assert engine.context.state.accounts == before


@pytest.mark.parametrize("navigation", [None, "amap"])
def test_restore_rejects_retired_navigation_app_activity_field(engine_factory, navigation):
    engine = engine_factory()
    _, result = act(engine, "valid-walk", travel("公司"))
    assert result.status == "in_progress"
    state = engine.context.state.model_copy(deep=True)
    entry = state.activities[result.activity_id]
    state.activities[result.activity_id] = entry.model_copy(
        update={"data": {**entry.data, "navigation_app_id": navigation}})
    with pytest.raises(IntegrityError, match="Persisted activity target is invalid"):
        validate_activity_targets(state, engine.context.catalog, engine.context.persona)


def test_relief_at_interval_end_still_bills_the_preceding_actual_discomfort(inputs):
    state = _initial_state(inputs, "discomfort")
    now = state.current_time
    person = state.agents[1]
    person.body = person.body.model_copy(update={"energy_kcal": 0.0, "sleep_pressure": 0.0})
    person.discomfort_started_at = now - 3 * 3600
    person.discomfort_pulse_hours = 1.0
    staged = state.model_copy(deep=True)
    staged.current_time += 900
    staged.agents[1].body.energy_kcal = 1800.0
    context = SimpleNamespace(state=state, persona=inputs.persona, config=inputs.config,
                              body=engine_body(inputs))
    settled = staged.agents[1].body.model_dump(mode="json")
    record = EventDraft(event_type="environment_event", occurred_at=staged.current_time, agent_id=1,
                        data={"kind": "body_settlement", "interval_start": now, "interval_end": staged.current_time,
                              "activity_ids": [], "resource_flows": [],
                              "body_settlement": {"before": dict(settled), "after": dict(settled)}})
    result = settle_engine_rhythm(context, staged, RhythmBoundary(staged.current_time, 900, {}, [record]))
    assert result.agents[1].body.relaxation_need == pytest.approx(person.body.relaxation_need + 0.025)
    assert record.data["body_settlement"]["after"]["relaxation_need"] == pytest.approx(
        settled["relaxation_need"] + 0.025)
    assert [(flow["resource"], flow["direction"], flow["cause"]) for flow in record.data["resource_flows"]] == [
        ("relaxation_need", "source", "relaxation:event")]
    assert result.agents[1].discomfort_started_at is None
    assert result.agents[1].discomfort_pulse_hours == 0.0


def engine_body(inputs):
    from worldmock.life.body_config import assemble_body_config

    return assemble_body_config(inputs.persona)


@pytest.mark.parametrize("start,stop,hours", [
    ("2026-03-08T00:00:00-05:00", "2026-03-09T00:00:00-04:00", 23),
    ("2026-11-01T00:00:00-04:00", "2026-11-02T00:00:00-05:00", 25),
])
def test_hourly_schedule_preserves_real_dst_instants_and_excludes_the_start(inputs, start, stop, hours):
    hourly = callback("hourly", {"class": "time", "key": "hourly"},
                      {"type": "transfer", "direction": "income", "amount_fen": 1})
    daily = callback("daily", {"class": "time", "key": "daily"},
                     {"type": "transfer", "direction": "income", "amount_fen": 1})
    current = configured(inputs, start, stop, [hourly, daily])
    schedule = compile_schedule(current)
    hourly_events = [event for event in schedule if event.data.get("key") == "hourly"]
    assert len(hourly_events) == hours
    assert len({event.due_at for event in hourly_events}) == hours
    assert hourly_events[0].due_at == int(current.config.start_time.timestamp()) + 3600
    assert hourly_events[-1].due_at == int(current.config.stop_time.timestamp())
    assert len([event for event in schedule if event.data.get("key") == "daily"]) == 1
    assert len([event for event in schedule if event.kind.value == "weather"]) == 1


@pytest.mark.parametrize("start,stop,clock,count", [
    ("2026-03-08T00:00:00-05:00", "2026-03-09T00:00:00-04:00", "02:30", 0),
    ("2026-11-01T00:00:00-04:00", "2026-11-02T00:00:00-05:00", "01:30", 2),
])
def test_monthly_schedule_uses_existing_local_times_and_both_distinct_fold_instants(
        inputs, start, stop, clock, count):
    monthly = callback("month", {"class": "time", "key": "monthly", "day": datetime.fromisoformat(start).day,
                                  "time": clock},
                       {"type": "transfer", "direction": "income", "amount_fen": 1})
    current = configured(inputs, start, stop, [monthly])
    rows = [event for event in compile_schedule(current) if event.data.get("key") == "monthly"]
    assert len(rows) == count
    assert len({event.scheduled_id for event in rows}) == count
    if count == 2:
        assert rows[1].due_at - rows[0].due_at == 3600


@pytest.mark.parametrize("start,stop", [
    ("2026-03-08T00:00:00-05:00", "2026-03-09T00:00:00-04:00"),
    ("2026-11-01T00:00:00-04:00", "2026-11-02T00:00:00-05:00"),
])
def test_daily_action_callback_counts_actual_dates_across_short_and_long_dst_days(inputs, start, stop):
    declaration = CallbackState(state="driving_days", initial=3, cap=3, reset_on_goods=())
    daily = callback("drive-day", {"class": "action", "key": "travel", "travel_method_id": "driving"},
                     {"type": "state_delta", "state": "driving_days", "delta": -1, "once_per": "local_day"})
    current = configured(inputs, start, stop, [daily])
    current = replace(current, catalog=replace(current.catalog, callback_states={"driving_days": declaration}))
    state = _initial_state(current, "dst")
    activity = SimpleNamespace(agent_id=1, origin_action_id="actual-trip", travel_method_id="driving",
                               data={"action": "travel", "accepted_event_id": "accepted-trip"})
    first, end = int(current.config.start_time.timestamp()), int(current.config.stop_time.timestamp())
    result = settle_callback_dates(state, current.catalog, activity, (first, end))
    assert result.state.agents[1].callback_states["driving_days"] == 2
    assert len(result.drafts) == 1
    repeated = settle_callback_dates(result.state, current.catalog, activity, (first + 1, end))
    assert not repeated.drafts
    next_day = settle_callback_dates(result.state, current.catalog, activity, (end, end + 1))
    assert next_day.state.agents[1].callback_states["driving_days"] == 1
