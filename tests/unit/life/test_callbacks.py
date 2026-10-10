"""验证回调只对真实来源执行，按实际日期结算且资金守恒。"""

from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from worldmock.catalog.callback_models import CallbackRule, CallbackState
from worldmock.environment import load_environment
from worldmock.errors import IntegrityError
from worldmock.footprints.models import EventDraft
from worldmock.input_models import ModelMetadata
from worldmock.life.callbacks import (
    CallbackCause, compare_callback_states, evaluate_callbacks, issue_callback_reminders,
    reset_on_receipt, settle_callback_dates, validate_callback_states,
)
from worldmock.life.world_setup import _initial_state
from worldmock.profile.models import WorldDynamics
from worldmock.scheduling.compile import compile_schedule


SOURCE = Path(__file__).resolve().parents[2] / "fixtures/dataset-environment"


@pytest.fixture
def world():
    inputs = load_environment(SOURCE, metadata_loader=lambda config: ModelMetadata(
        requested_model=config.requested_model, metadata_url=config.metadata_url,
        context_window=524288, max_output_tokens=32768))
    state = _initial_state(inputs, "callback-tests", WorldDynamics(social_need_growth_per_hour=0.06))
    return inputs, state


def catalog_with(inputs, rules, initial=3, cap=3):
    declaration = CallbackState(state="driving_days", initial=initial, cap=cap, reset_on_goods=("fuel",))
    return replace(inputs.catalog, callback_states={"driving_days": declaration},
                   callback_rules={rule.rule_id: rule for rule in rules})


def rule(identity, trigger, effect, notify=None):
    payload = {"rule_id": identity, "trigger": trigger, "effect": effect}
    if notify is not None:
        payload["notify"] = notify
    return CallbackRule.model_validate(payload)


def prepared(world, rules, initial=3, cap=3):
    inputs, state = world
    state.agents[1].callback_states = {"driving_days": initial}
    return catalog_with(inputs, rules, initial, cap), state


def cause(key="drive"):
    return CallbackCause("action", key, 1, "actual-source", "accepted-action:0")


def delta_rule(once=False):
    effect = {"type": "state_delta", "state": "driving_days", "delta": -1}
    if once:
        effect["once_per"] = "local_day"
    return rule("drive-day", {"class": "action", "key": "drive"}, effect)


def test_only_matching_action_applies_and_preserves_actual_cause(world):
    catalog, state = prepared(world, [delta_rule()])
    assert not evaluate_callbacks(state, catalog, cause("walk")).drafts
    outcome = evaluate_callbacks(state, catalog, cause())
    assert outcome.state.agents[1].callback_states == {"driving_days": 2}
    assert outcome.drafts[0].caused_by == ["actual-source"]
    assert outcome.drafts[0].data == {"kind": "callback_effect", "type": "state_delta",
                                     "rule_id": "drive-day", "state": "driving_days", "delta": -1,
                                     "before": 3, "after": 2}


@pytest.mark.parametrize("initial,delta,expected", [(0, -2, 0), (2, 10, 3), (2, -10, 0), (1, 1, 2)])
def test_state_delta_clamps_without_blocking_action(world, initial, delta, expected):
    declared = rule("delta", {"class": "action", "key": "drive"},
                    {"type": "state_delta", "state": "driving_days", "delta": delta})
    catalog, state = prepared(world, [declared], initial=initial)
    outcome = evaluate_callbacks(state, catalog, cause())
    assert outcome.state.agents[1].callback_states["driving_days"] == expected
    assert outcome.drafts[0].data["after"] - outcome.drafts[0].data["before"] == expected - initial


def test_local_day_delta_waits_for_positive_actual_time(world):
    catalog, state = prepared(world, [delta_rule(True)])
    assert evaluate_callbacks(state, catalog, cause()).state.agents[1].callback_states["driving_days"] == 3
    activity = SimpleNamespace(agent_id=1, origin_action_id="accepted-action",
                               data={"action": "drive", "accepted_event_id": "actual-source"})
    assert not settle_callback_dates(state, catalog, activity, (state.current_time, state.current_time)).drafts
    outcome = settle_callback_dates(state, catalog, activity, (state.current_time, state.current_time + 1))
    assert outcome.state.agents[1].callback_states["driving_days"] == 2
    repeated = settle_callback_dates(outcome.state, catalog, activity,
                                     (state.current_time + 1, state.current_time + 3600))
    assert not repeated.drafts


@pytest.mark.parametrize("end,expected", [("2026-01-06T00:00:00+08:00", 2),
                                         ("2026-01-06T00:00:01+08:00", 1)])
def test_actual_half_open_midnight_interval(world, end, expected):
    catalog, state = prepared(world, [delta_rule(True)])
    start = int(datetime.fromisoformat("2026-01-05T23:59:59+08:00").timestamp())
    stop = int(datetime.fromisoformat(end).timestamp())
    activity = SimpleNamespace(agent_id=1, origin_action_id="actual-trip",
                               data={"action": "drive", "accepted_event_id": "actual-source"})
    outcome = settle_callback_dates(state, catalog, activity, (start, stop))
    assert outcome.state.agents[1].callback_states["driving_days"] == expected


def test_receipt_resets_to_cap_without_clearing_daily_identity(world):
    catalog, state = prepared(world, [delta_rule(True)], initial=1)
    state.agents[1].callback_once_keys = {"drive-day:2026-01-05"}
    receipt = EventDraft(event_type="environment_event", occurred_at=state.current_time, agent_id=1,
                         data={"kind": "goods_received", "goods_id": "fuel", "order_id": "order"})
    outcome = reset_on_receipt(state, catalog, receipt)
    assert outcome.state.agents[1].callback_states["driving_days"] == 3
    assert outcome.state.agents[1].callback_once_keys == state.agents[1].callback_once_keys
    assert outcome.drafts[0].caused_by == [receipt.event_id]
    assert outcome.drafts[0].data["type"] == "reset_to_cap"
    assert outcome.drafts[0].data["rule_id"] is None


@pytest.mark.parametrize("direction,amount,paid", [("expense", 500, True), ("expense", 200001, False),
                                                 ("income", 500000, True)])
def test_transfer_is_full_or_unpaid_and_conserves_accounts(world, direction, amount, paid):
    declared = rule("transfer", {"class": "action", "key": "drive"},
                    {"type": "transfer", "direction": direction, "amount_fen": amount})
    catalog, state = prepared(world, [declared])
    outcome = evaluate_callbacks(state, catalog, cause())
    assert sum(outcome.state.accounts.values()) == 0
    assert set(outcome.state.accounts) == {"agent:1", "external:scenario"}
    assert outcome.drafts[0].data["status"] == ("paid" if paid else "unpaid")
    actual = outcome.state.accounts["agent:1"] - state.accounts["agent:1"]
    expected = (amount if direction == "income" else -amount) if paid else 0
    assert actual == expected
    assert outcome.drafts[0].resource_changes == ({"account:agent:1": expected,
                                                 "account:external:scenario": -expected} if paid else {})


def test_visible_hint_uses_only_trigger_net_decrease_once(world):
    rules = [rule("expense", {"class": "action", "key": "drive"},
                  {"type": "transfer", "direction": "expense", "amount_fen": 700}),
             rule("income", {"class": "action", "key": "drive"},
                  {"type": "transfer", "direction": "income", "amount_fen": 200}),
             rule("trace", {"class": "action", "key": "drive"},
                  {"type": "footprint_chain", "steps": [{"kind": "payment", "app_pool": ["alipay"],
                                                          "pick": "random"}]}, {"visible": True})]
    catalog, state = prepared(world, rules)
    outcome = evaluate_callbacks(state, catalog, cause(), balance_before=200300)
    hints = [draft for draft in outcome.drafts if draft.data["kind"] == "balance_decreased"]
    assert [draft.data for draft in hints] == [{"kind": "balance_decreased", "amount_fen": 800}]
    assert all(not draft.resource_changes for draft in outcome.drafts if draft.data["kind"] == "app_touch")


def test_threshold_edge_fires_once_and_does_not_cascade(world):
    crossed = rule("edge", {"class": "state", "key": "driving_days", "below": 3},
                   {"type": "state_delta", "state": "driving_days", "delta": -2})
    lower = rule("lower", {"class": "state", "key": "driving_days", "below": 2},
                 {"type": "transfer", "direction": "expense", "amount_fen": 500})
    catalog, state = prepared(world, [delta_rule(), crossed, lower])
    accepted = evaluate_callbacks(state, catalog, cause())
    outcome = compare_callback_states(state, accepted.state, catalog, accepted.drafts, accepted.causes)
    assert outcome.state.agents[1].callback_states["driving_days"] == 0
    assert outcome.state.accounts == state.accounts
    assert len(outcome.drafts) == 1
    assert outcome.drafts[0].caused_by == ["actual-source"]
    unchanged = compare_callback_states(outcome.state, outcome.state, catalog, outcome.drafts, {})
    assert not unchanged.drafts


def test_reminder_is_silent_daily_and_contains_no_content_identity(world):
    declared = rule("reminder", {"class": "action", "key": "drive"},
                    {"type": "state_delta", "state": "driving_days", "delta": -1},
                    {"when": {"state": "driving_days", "below": 2}, "channel": "app_push",
                     "app_id": "wechat", "text": "记得购买燃油"})
    catalog, state = prepared(world, [declared], initial=1)
    outcome = issue_callback_reminders(state, catalog)
    assert len(outcome.drafts) == 1
    assert outcome.drafts[0].data == {"kind": "callback_reminder", "rule_id": "reminder",
                                     "app_id": "wechat", "text": "记得购买燃油"}
    assert not issue_callback_reminders(outcome.state, catalog).drafts


@pytest.mark.parametrize("seconds", [-3600, 60, 14 * 3600])
def test_reminder_outside_awake_push_hour_does_not_consume_the_local_date(world, seconds):
    declared = rule("reminder", {"class": "action", "key": "drive"},
                    {"type": "state_delta", "state": "driving_days", "delta": -1},
                    {"when": {"state": "driving_days", "below": 2}, "channel": "app_push",
                     "app_id": "wechat", "text": "记得购买燃油"})
    catalog, state = prepared(world, [declared], initial=1)
    state = state.model_copy(update={"current_time": state.current_time + seconds}, deep=True)
    before = state.model_dump(mode="json")
    outcome = issue_callback_reminders(state, catalog)
    assert outcome.state is state and outcome.drafts == []
    assert state.model_dump(mode="json") == before and state.agents[1].callback_reminder_days == {}


def test_receipt_of_unrelated_goods_does_not_reset_other_registered_state(world):
    catalog, state = prepared(world, [delta_rule()], initial=1)
    receipt = EventDraft(event_type="environment_event", occurred_at=state.current_time, agent_id=1,
                         visibility="agent", data={"kind": "goods_received", "goods_id": "tissue"})
    before = state.model_dump(mode="json")
    outcome = reset_on_receipt(state, catalog, receipt)
    assert outcome.state is state and outcome.drafts == [] and state.model_dump(mode="json") == before


@pytest.mark.parametrize("invalid", [{}, {"driving_days": -1}, {"driving_days": 4},
                                    {"driving_days": True}, {"other": 1}])
def test_corrupt_callback_state_is_rejected(world, invalid):
    catalog, state = prepared(world, [delta_rule()])
    state.agents[1].callback_states = invalid
    with pytest.raises(IntegrityError):
        validate_callback_states(state, catalog)


def test_time_schedule_excludes_start_and_includes_stop(world):
    inputs, _ = world
    declared = rule("hour", {"class": "time", "key": "hourly"},
                    {"type": "transfer", "direction": "income", "amount_fen": 1})
    inputs = replace(inputs, catalog=catalog_with(inputs, [declared]))
    start, end = int(inputs.config.start_time.timestamp()), int(inputs.config.stop_time.timestamp())
    rows = [event for event in compile_schedule(inputs) if event.kind.value == "time_trigger"]
    assert len(rows) == 24
    assert rows[0].due_at == start + 3600
    assert rows[-1].due_at == end
    assert len({event.scheduled_id for event in rows}) == len(rows)
