"""@brief 统一动作的真实恢复身份与内容消费文字，不通过生活引擎。"""

import os
import subprocess
import sys
from types import SimpleNamespace
from dataclasses import replace

import pytest

from tests.life.action_worlds import action_world, advance, begin, pin
from worldmock.errors import IntegrityError
from worldmock.footprints.models import Event
from worldmock.life.activity_validation import validate_activity_targets
from worldmock.life.rules import LifeRuleEngine
from worldmock.life.models import WorldState
from worldmock.exchange.types import CommunicationMode
from worldmock.life.settled_text import activity_sentence, app_progress, experience_sentence, record_lines


def context(semantics, state, activity):
    """@brief 取同一严格发布目录和真实活动，名称模拟已冻结的活动名称。"""
    rules = LifeRuleEngine(semantics.scenario, semantics.catalog)
    action = rules.actions[activity.data["action_id"]].name
    app_id = activity.data["app_id"]
    app = "" if app_id is None else f"用{rules.apps[app_id].name}"
    return SimpleNamespace(state=state, scenario=semantics.scenario, semantics=semantics, rules=rules,
                           agent_id=activity.agent_id,
                           names={"activities": {activity.activity_id: f"{app}{action}（1月5日 08:00 起）"}})


def progress_event(state, activity, evidence, **changes):
    """@brief 已认证真实活动推进事件，不把随机抽中的下一条当作消费。"""
    fields = dict(event_id="actual-progress", event_seq=1, run_id=state.run_id, event_type="activity_progress",
                  occurred_at=state.current_time, agent_id=1, activity_id=activity.activity_id,
                  source_kind="environment", visibility="agent", recorded_at="2026-01-05T00:00:00Z",
                  wall_elapsed_ms=0, state_version_before=1, state_version_after=2,
                  data={"activity": activity.model_dump(mode="json"), "content": evidence})
    return Event(**{**fields, **changes})


@pytest.mark.parametrize("action,app,counterpart", [
    ("browse-feed", "douyin", None), ("quiet-rest", None, None),
    ("small-talk", None, "agent:2"), ("voice-call", "wechat", "contact-colleague"),
])
def test_real_action_restores_from_registered_identity(action, app, counterpart):
    semantics, state = action_world(multiple=True)
    state, activity = begin(semantics, state, action, app, counterpart)
    validate_activity_targets(state, LifeRuleEngine(semantics.scenario, semantics.catalog), semantics.scenario)
    assert activity.data["action_id"] == action


@pytest.mark.parametrize("key,value", [
    ("action_id", "retired-action"), ("category", "exercise"), ("manner", "phone_background"),
    ("app_id", "wechat"), ("content_id", "c-rest"), ("counterpart", "contact-colleague"),
    ("draw_index", -1), ("draw_index", True), ("pinned_by_push_id", ""),
    ("start_position_seconds", -1), ("start_position_seconds", 121), ("start_position_seconds", True),
    ("played", [{"content_id": "c-rest", "from_position": 0, "to_position": 900}]),
    ("played", [{"content_id": "c-short", "from_position": 0, "to_position": 119}]),
    ("played", [{"content_id": "c-short", "from_position": 120, "to_position": 120}]),
    ("summary", "retired snapshot"), ("communication_mode", "voice"),
    ("communication_mode", "text"), ("communication_mode", "other"),
])
def test_persisted_action_binding_drift_is_rejected(key, value):
    semantics, state = action_world()
    pin(state)
    state, activity = begin(semantics, state)
    activity.data[key] = value
    with pytest.raises(IntegrityError, match="Persisted activity target"):
        validate_activity_targets(state, LifeRuleEngine(semantics.scenario, semantics.catalog), semantics.scenario)


@pytest.mark.parametrize("action,app,counterpart", [
    ("small-talk", None, "agent:1"), ("small-talk", None, "contact-colleague"),
    ("voice-call", "wechat", "agent:2"), ("voice-call", "wechat", "unknown-contact"),
])
def test_persisted_social_requires_actual_other_person_or_own_contact(action, app, counterpart):
    semantics, state = action_world(multiple=True)
    state, activity = begin(semantics, state, action, app, counterpart)
    with pytest.raises(IntegrityError, match="Persisted activity target"):
        validate_activity_targets(state, LifeRuleEngine(semantics.scenario, semantics.catalog), semantics.scenario)


def test_silent_next_content_is_not_described_until_actual_consumption():
    semantics, state = action_world()
    pin(state)
    state, activity = begin(semantics, state)
    state, evidence = advance(semantics, state, activity, 120)
    following = state.activities[activity.activity_id]
    event = progress_event(state, following, evidence)
    progress = app_progress([event], {activity.activity_id: "c-short"})
    assert progress == {}
    sentence = activity_sentence(context(semantics, state, following), following, app_progress=progress)
    assert f"《{semantics.catalog.contents['c-short'].title}》" in sentence
    assert semantics.catalog.contents[following.data["content_id"]].title not in sentence
    assert sentence.count("用抖音") == 1 and "看了" not in sentence
    state, evidence = advance(semantics, state, following, 1)
    actual = state.activities[activity.activity_id]
    progress = app_progress([progress_event(state, actual, evidence)], progress)
    assert progress == {activity.activity_id: actual.data["content_id"]}
    sentence = activity_sentence(context(semantics, state, actual), actual, app_progress=progress)
    assert f"（《{semantics.catalog.contents[actual.data['content_id']].title}》未完）" in sentence


def test_no_app_action_has_actual_partial_content_without_app_language():
    semantics, state = action_world()
    state, activity = begin(semantics, state, "quiet-rest", None)
    original = activity_sentence(context(semantics, state, activity), activity, app_progress={})
    assert "《" not in original and "用" not in original
    state, evidence = advance(semantics, state, activity, 1)
    actual = state.activities[activity.activity_id]
    event = progress_event(state, actual, evidence)
    sentence = activity_sentence(context(semantics, state, actual), actual, app_progress=app_progress([event], {}))
    assert "安静休息" in sentence and "未完" in sentence and "用" not in sentence and "看了" not in sentence
    assert app_progress([event.model_copy(update={"source_kind": "agent"})], {}) == {}
    assert app_progress([event.model_copy(update={"visibility": "system"})], {}) == {}
    with pytest.raises(IntegrityError, match="authenticated progress evidence"):
        activity_sentence(context(semantics, state, actual), actual)


@pytest.mark.parametrize("action,app", [("quiet-rest", None), ("browse-feed", "douyin")])
def test_daily_plan_played_sentence_uses_actual_action_and_optional_app(action, app):
    semantics, state = action_world()
    state, activity = begin(semantics, state, action, app)
    fact = {"kind": "played", "content_id": activity.data["content_id"], "app_id": app,
            "position_before_seconds": 0, "position_after_seconds": 1,
            "interval_start": state.current_time, "interval_end": state.current_time + 1}
    sentence = experience_sentence(context(semantics, state, activity), fact)
    assert semantics.catalog.actions[action].name in sentence
    assert f"《{semantics.catalog.contents[fact['content_id']].title}》" in sentence
    assert ("用抖音" in sentence) is (app is not None)


def test_completed_work_sentence_uses_actual_content_title():
    semantics, state = action_world("office")
    state, activity = begin(semantics, state, "office-work", None)
    record = {"type": "work_completed", "occurred_at": "2026-01-05T09:00:00+08:00", "topic": "本月销售总结"}
    assert record_lines(context(semantics, state, activity), record, []) == ["09:00 完成「本月销售总结」"]


def test_action_modules_import_in_fresh_process_without_life_engine():
    modules = ("action_run", "activities", "content", "social_actions", "app_push", "activity_validation", "settled_text")
    script = ("import importlib, sys\n" +
              f"for name in {modules!r}: importlib.import_module('worldmock.life.'+name)\n" +
              "assert 'worldmock.life.engine' not in sys.modules\n")
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join(("src", ".")))
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                            env=environment, timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("selection", ["absent", None, "navigation", "unknown", "payment"])
def test_persisted_navigation_is_optional_but_nonempty_identity_requires_navigation_purpose(selection):
    from worldmock.life.activities import ActivityOrigin, start_activity
    from worldmock.life.models import ActivityStartParams
    from worldmock.life.travel import plan_trip, travel_activity_data
    semantics, state = action_world()
    catalog = semantics.catalog
    trip = plan_trip(catalog.graph, "home", "office", "walking")
    request = ActivityStartParams(activity_type="travel", planned_duration_seconds=trip.duration_seconds,
                                  destination_id="office", travel_mode="walking")
    agent, travel = start_activity(state.agents[1], request, state.current_time,
                                   ActivityOrigin("travel", "decision", "request"), semantics.body)
    travel.data = travel_activity_data(trip)
    if selection != "absent":
        identity = next((item.app_id for item in catalog.apps.values() if selection in item.purposes), selection)
        travel.data["navigation_app_id"] = identity
    state.activities = {travel.activity_id: travel}
    state.agents[1] = agent
    rules = LifeRuleEngine(semantics.scenario, catalog)
    if selection in {"absent", None, "navigation"}:
        validate_activity_targets(state, rules, semantics.scenario)
    else:
        with pytest.raises(IntegrityError, match="Persisted activity target"):
            validate_activity_targets(state, rules, semantics.scenario)


def test_completed_action_played_identity_survives_real_continuation():
    semantics, state = action_world()
    pin(state)
    state, activity = begin(semantics, state)
    state, _ = advance(semantics, state, activity, 120)
    validate_activity_targets(state, LifeRuleEngine(semantics.scenario, semantics.catalog), semantics.scenario)
    state.activities[activity.activity_id].media_position_seconds = 1e9
    with pytest.raises(IntegrityError, match="Persisted activity target"):
        validate_activity_targets(state, LifeRuleEngine(semantics.scenario, semantics.catalog), semantics.scenario)


@pytest.mark.parametrize("fulfillment,app,payment,location", [
    ("pickup", None, None, "noodle-1"), ("delivery", "meituan", "wechat", "home"),
])
def test_real_purchase_sentence_uses_goods_apps_and_actual_fulfillment(fulfillment, app, payment, location):
    from worldmock.life.basket import purchase_order
    from worldmock.life.models import PurchaseItem, PurchaseOrderRequest
    from worldmock.life.settled_text import purchase_sentence
    semantics, state = action_world(location)
    state, activity = begin(semantics, state, "quiet-rest", None)
    request = PurchaseOrderRequest(items=(PurchaseItem(store_id="noodle-1", goods_id="beef-noodles", quantity=1,
        recipient_id="agent:1"),), fulfillment=fulfillment, destination_id=location,
        app_id=app, payment_app_id=payment)
    outcome = purchase_order(state, semantics, 1, request)
    order = outcome.orders[0].model_dump(mode="json")
    actual = context(semantics, outcome.state, activity)
    sentence = purchase_sentence(actual, order, None)
    assert "购买牛肉面 1 份，付" in sentence and "远程下单" not in sentence and "到店" not in sentence
    if app is None:
        assert "经" not in sentence and "预计" not in sentence
    else:
        assert "经美团购买" in sentence and "（微信）" in sentence and "送到家" in sentence
    fact = {"kind": "received", "occurred_at": state.current_time, "goods_id": order["goods_id"], "quantity": 1}
    assert "收到牛肉面 1 份" in experience_sentence(actual, fact)


def test_real_completed_action_source_endings_and_summary_have_identical_content():
    from worldmock.life.settled_text import ActivityFacts, activity_endings, ending_lines
    semantics, state = action_world()
    state, activity = begin(semantics, state, "quiet-rest", None)
    state, evidence = advance(semantics, state, activity, 900)
    ended = state.activities[activity.activity_id]
    event = progress_event(state, ended, evidence)
    later = event.model_copy(update={"event_id": "later", "event_seq": 2,
                                    "event_type": "action_result", "data": {"data": {"activities": [ended.model_dump()]}}})
    assert activity_endings([later, event]) == {activity.activity_id: event}
    facts = ActivityFacts({}, activity_endings([event]), app_progress([event], {}))
    actual = context(semantics, state, ended)
    assert ending_lines(actual, facts, lambda row: row.event_id == "actual-progress") == [
        (event, activity_sentence(actual, ended, app_progress={}))]
    assert ending_lines(actual, facts, lambda row: False) == []
    assert "《" in ending_lines(actual, facts, lambda row: True)[0][1]


@pytest.mark.parametrize("action, app, counterpart, expected", [
    ("browse-feed", "douyin", None, None), ("small-talk", None, "agent:2", None),
    ("voice-call", "wechat", "contact-colleague", "voice"),
    ("voice-call", "wechat", "contact-colleague", "text"),
])
def test_persisted_communication_mode_is_real_declared_fact_and_restore_rejects_drift(action, app, counterpart, expected):
    semantics, state = action_world(multiple=True)
    if expected == "text":
        actions = dict(semantics.catalog.actions)
        actions[action] = actions[action].model_copy(update={"communication_mode": CommunicationMode.TEXT})
        semantics = replace(semantics, catalog=replace(semantics.catalog, actions=actions))
    state, activity = begin(semantics, state, action, app, counterpart)
    assert activity.data["communication_mode"] == expected and len(activity.data) == 11
    restored = WorldState.model_validate(state.model_dump(mode="json"))
    rules = LifeRuleEngine(semantics.scenario, semantics.catalog)
    validate_activity_targets(restored, rules, semantics.scenario)
    restored.activities[activity.activity_id].data["communication_mode"] = "text" if expected != "text" else "voice"
    with pytest.raises(IntegrityError, match="Persisted activity target"):
        validate_activity_targets(restored, rules, semantics.scenario)
