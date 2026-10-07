"""已提交进度到人物已核验经历的真实存储边界。"""

import pytest

from worldmock.errors import IntegrityError
from worldmock.footprints.experiences import (
    project_verified_experiences,
    recent_verified_experiences,
    validate_verified_experiences,
)
from worldmock.footprints.models import EventDraft
from worldmock.footprints.store import FootprintStore


@pytest.fixture
def store(tmp_path):
    value = FootprintStore.create(
        tmp_path / "run", {"run_id": "run", "agent_ids": [1, 2]},
        {"version": 0, "current_time": 100},
    )
    yield value
    value.close()


def _progress(kind, activity_id, agent_id=1, **fields):
    activity = {"activity_id": activity_id, "kind": kind, **fields.pop("activity", {})}
    return EventDraft(
        event_type="activity_progress", occurred_at=100, agent_id=agent_id,
        activity_id=activity_id,
        data={"interval_start": 90, "interval_end": 100, "activity": activity, **fields},
    )


def _receipt(order_id, agent_id=1, **fields):
    """@brief 在真实事件存储中提交环境收货载荷，保留订单、收货人与固定效果证据。"""
    data = {"kind": "goods_received", "order_id": order_id, "payer_id": 1,
            "goods_id": "rice", "category": "meal", "quantity": 1,
            "destination_id": "restaurant", "recipient_location_id": "restaurant", "recipient_asleep": False,
            "provided": 600, "applied": 540, "efficiency": 0.9, "resource_flows": []}
    return EventDraft(event_type="environment_event", occurred_at=100, agent_id=agent_id,
                      source_kind="environment", visibility="agent", data={**data, **fields})


def test_recent_window_only_projects_own_committed_progress_before_observation(store, monkeypatch):
    drafts = [
        _progress("wait", "wait-1"),
        _progress("wait", "other-wait", agent_id=2),
        _receipt("order-1"),
        _progress("use_app", "app-1", activity={"target_id": "bilibili"}, content={
            "content_id": "c-article", "app_id": "bilibili",
            "position_before_seconds": 0, "position_after_seconds": 10,
            "completed": False, "completed_rounds_after": 0,
        }),
        _progress("travel", "travel-1", activity={
            "status": "completed", "destination_id": "office", "location_id": "home",
            "data": {"arrival": {"location_id": "office", "access_status": "allowed"}},
        }),
    ]
    committed = store.commit(store.state, drafts)
    observation = store.commit(store.state, [EventDraft(
        event_type="observation", occurred_at=100, agent_id=1,
    )])[0]
    expected = project_verified_experiences(committed, agent_id=1, cutoff_seq=observation.event_seq - 1)
    monkeypatch.setattr(store, "read_events", lambda _: pytest.fail("observation scanned all events"))
    recent = recent_verified_experiences(store, 1, observation.event_seq - 1)
    assert recent == expected
    assert [row["kind"] for row in recent] == ["waited", "received", "played", "arrived"]
    assert [row["source_event_id"] for row in recent] == [event.event_id for event in committed if event.agent_id == 1]
    assert validate_verified_experiences(recent, 1, 100) == recent
    received = next(row for row in recent if row["kind"] == "received")
    assert (received["order_id"], received["goods_id"], received["category"], received["quantity"]) == (
        "order-1", "rice", "meal", 1)
    assert received["activity_id"] is None
    played = next(row for row in recent if row["kind"] == "played")
    assert (played["content_id"], played["app_id"], played["position_before_seconds"],
            played["position_after_seconds"]) == ("c-article", "bilibili", 0, 10)
    assert "revealed" not in played and "target_id" not in played


def test_received_orders_are_distinct_and_projection_is_idempotent(store):
    """@brief 同一商品的两笔真实收货分别保留，重复读取同一事件不重复增加事实。"""
    events = store.commit(store.state, [_receipt("first"), _receipt("second")])
    facts = project_verified_experiences(events, agent_id=1, cutoff_seq=2)
    assert [row["order_id"] for row in facts] == ["first", "second"]
    assert [row["quantity"] for row in facts] == [1, 1]
    assert sum(row["quantity"] for row in facts) == 2
    assert project_verified_experiences(events + events[-1:], agent_id=1, cutoff_seq=2) == facts


def test_real_goods_receipt_commit_projects_only_its_received_order(tmp_path):
    """@brief 真实人工环境购买、收货结算与 SQLite 提交给出订单绑定的收到经历。"""
    from tests.life.purchase_worlds import registered_world
    from tests.life.test_commerce import order_request
    from worldmock.life.basket import purchase_order

    semantics, state = registered_world("store")
    outcome = purchase_order(state, semantics, 1, order_request(
        "store", "store", None, "pickup", ("rice-ball",), payment=None))
    store = FootprintStore.create(tmp_path / "real-receipt", {"run_id": state.run_id, "agent_ids": [1]},
                                  state.model_dump(mode="json"))
    try:
        committed_state = outcome.state.model_copy(update={"version": state.version + 1})
        payment = EventDraft(event_type="action_result", occurred_at=state.current_time, agent_id=1,
                             data={"resource_changes": {f"account:{key}": value - state.accounts[key]
                                   for key, value in committed_state.accounts.items()
                                   if value != state.accounts[key]}})
        events = store.commit(committed_state.model_dump(mode="json"), [payment, *outcome.receipts])
        facts = recent_verified_experiences(store, 1, store.last_event_seq)
        assert facts == project_verified_experiences(events, agent_id=1, cutoff_seq=store.last_event_seq)
        assert facts == [{"kind": "received", "agent_id": 1, "activity_id": None,
                          "source_event_id": events[1].event_id, "event_seq": events[1].event_seq,
                          "occurred_at": state.current_time, "order_id": outcome.orders[0].order_id,
                          "goods_id": "rice-ball", "category": "meal", "quantity": 1}]
        assert validate_verified_experiences(facts, 1, state.current_time) == facts
    finally:
        store.close()


@pytest.mark.parametrize("field", ["order_id", "goods_id", "category", "quantity"])
def test_missing_settlement_evidence_is_an_error_not_a_guessed_fact(store, field):
    draft = _receipt("order")
    del draft.data[field]
    event = store.commit(store.state, [draft])[0]
    with pytest.raises(IntegrityError, match="receipt evidence"):
        project_verified_experiences([event], agent_id=1, cutoff_seq=1)


def test_reference_window_rejects_other_person_and_future_time(store):
    event = store.commit(store.state, [_progress("wait", "wait-1")])[0]
    fact = project_verified_experiences([event], agent_id=1, cutoff_seq=1)[0]
    with pytest.raises(IntegrityError):
        validate_verified_experiences([{**fact, "agent_id": 2}], 1, 100)
    with pytest.raises(IntegrityError):
        validate_verified_experiences([{**fact, "occurred_at": 101}], 1, 100)


def test_observed_window_rejects_invalid_boundary_duplicates_and_reordering(store):
    events = store.commit(store.state, [_progress("wait", "first"), _progress("wait", "second")])
    facts = project_verified_experiences(events, agent_id=1, cutoff_seq=2)
    with pytest.raises(IntegrityError, match="window"):
        validate_verified_experiences(facts, 0, 100)
    with pytest.raises(IntegrityError, match="duplicated"):
        validate_verified_experiences(facts + facts[:1], 1, 100)
    with pytest.raises(IntegrityError, match="order"):
        validate_verified_experiences(list(reversed(facts)), 1, 100)


def test_projection_rejects_uncommitted_origin_conflict_and_invalid_cutoff(store):
    events = store.commit(store.state, [_progress("wait", "first"), _progress("wait", "second")])
    with pytest.raises(IntegrityError, match="boundary"):
        project_verified_experiences(events, agent_id=0, cutoff_seq=2)
    with pytest.raises(IntegrityError, match="cutoff"):
        project_verified_experiences(events, agent_id=1, cutoff_seq=1)
    with pytest.raises(IntegrityError, match="ordered"):
        project_verified_experiences(list(reversed(events)), agent_id=1, cutoff_seq=2)
    changed = events[0].model_copy(update={"data": {"activity": {"kind": "use_app"}}})
    with pytest.raises(IntegrityError, match="conflicts"):
        project_verified_experiences([events[0], changed], agent_id=1, cutoff_seq=2)
    untrusted = events[0].model_copy(update={"source_kind": "model"})
    with pytest.raises(IntegrityError, match="provenance"):
        project_verified_experiences([untrusted], agent_id=1, cutoff_seq=2)


def test_recent_experience_reader_rejects_uncommitted_cutoff_and_bad_query(store):
    store.commit(store.state, [_progress("wait", "first")])
    with pytest.raises(IntegrityError, match="cutoff"):
        recent_verified_experiences(store, 1, store.last_event_seq + 1)
    with pytest.raises(IntegrityError, match="boundary"):
        store.read_recent_experience_sources(1, store.last_event_seq + 1, 0)


def test_recent_reader_returns_no_experience_from_another_person(store):
    store.commit(store.state, [_progress("wait", "other", agent_id=2)])
    assert recent_verified_experiences(store, 1, store.last_event_seq) == []


def test_received_window_ignores_payment_delivery_and_retired_consumption(store):
    """@brief 付款与送达订单不是付款人的收货，旧进食载荷也不能合成为经历。"""
    events = store.commit(store.state, [
        EventDraft(event_type="action_result", occurred_at=100, agent_id=1,
                   data={"orders": [{"order_id": "other", "status": "delivered", "recipient_id": "agent:2"}]}),
        EventDraft(event_type="environment_event", occurred_at=100, agent_id=1,
                   data={"kind": "delivery_arrival", "scheduled_id": "arrival", "orders": []}),
        _progress("consume", "retired", consumption={"items": [{"food_id": "rice", "interval_portion": 1}]}),
        _receipt("other", agent_id=2),
    ])
    assert project_verified_experiences(events, agent_id=1, cutoff_seq=4) == []
    assert recent_verified_experiences(store, 1, 4) == []
    received = project_verified_experiences(events, agent_id=2, cutoff_seq=4)
    assert len(received) == 1 and received[0]["source_event_id"] == events[-1].event_id
    assert received[0]["kind"] == "received" and received[0]["activity_id"] is None
    assert validate_verified_experiences(received, 2, 100) == received
    with pytest.raises(IntegrityError, match="kind"):
        validate_verified_experiences([{**received[0], "kind": "consumed"}], 2, 100)


@pytest.mark.parametrize("change,error", [
    ({"source_kind": "model"}, "provenance"), ({"visibility": "system"}, "visibility"),
    ({"activity_id": "unrelated"}, "activity identity"),
])
def test_received_source_requires_environment_recipient_visibility_and_no_activity(store, change, error):
    """@brief 收货事实不能借用模型来源、系统可见事件或其他活动身份。"""
    event = store.commit(store.state, [_receipt("order")])[0]
    with pytest.raises(IntegrityError, match=error):
        project_verified_experiences([event.model_copy(update=change)], agent_id=1, cutoff_seq=1)


@pytest.mark.parametrize("quantity", [True, 0, -1, float("nan"), float("inf")])
def test_received_quantity_requires_positive_finite_actual_evidence(store, quantity):
    """@brief 份数不能由缺失、布尔、非有限数或非正数猜出。"""
    event = store.commit(store.state, [_receipt("order")])[0]
    changed = event.model_copy(update={"data": {**event.data, "quantity": quantity}})
    with pytest.raises(IntegrityError, match="receipt evidence"):
        project_verified_experiences([changed], agent_id=1, cutoff_seq=1)


def test_received_recent_window_pages_past_nonprojected_activity_sources(store, monkeypatch):
    """@brief 收货走本人截止前的索引分页，越过无经历的活动页，不扫描所有事件。"""
    own = store.commit(store.state, [_receipt(f"order-{index}") for index in range(8)])
    store.commit(store.state, [_progress("sleep", f"sleep-{index}") for index in range(40)])
    store.commit(store.state, [_receipt("other", agent_id=2)])
    cutoff = store.last_event_seq
    store.commit(store.state, [_receipt("future")])
    original = store.read_recent_experience_sources
    calls = []

    def read_page(agent_id, before_seq, limit):
        calls.append((agent_id, before_seq, limit))
        return original(agent_id, before_seq, limit)

    monkeypatch.setattr(store, "read_recent_experience_sources", read_page)
    monkeypatch.setattr(store, "read_events", lambda _: pytest.fail("receipt window scanned all events"))
    facts = recent_verified_experiences(store, 1, cutoff)
    assert len(calls) == 2 and all(agent_id == 1 and limit == 32 for agent_id, _, limit in calls)
    assert calls[0][1] == cutoff + 1 and calls[1][1] < calls[0][1]
    assert [row["order_id"] for row in facts] == [f"order-{index}" for index in range(3, 8)]
    assert [row["source_event_id"] for row in facts] == [event.event_id for event in own[-5:]]
    assert validate_verified_experiences(facts, 1, 100) == facts


def test_zero_playback_and_unfinished_travel_do_not_become_completed_facts(store):
    events = store.commit(store.state, [
        _progress("use_app", "app", activity={"target_id": "bilibili"}, content={
            "content_id": "c-article", "app_id": "bilibili",
            "position_before_seconds": 10, "position_after_seconds": 10,
            "completed": False, "completed_rounds_after": 0,
        }),
        _progress("travel", "route", activity={"status": "active", "destination_id": "office"}),
    ])
    assert project_verified_experiences(events, agent_id=1, cutoff_seq=2) == []
    backwards = events[0].model_copy(update={"data": {
        **events[0].data, "content": {**events[0].data["content"], "position_after_seconds": 9},
    }})
    with pytest.raises(IntegrityError, match="backwards"):
        project_verified_experiences([backwards], agent_id=1, cutoff_seq=2)
    missing_arrival = events[1].model_copy(update={"data": {
        **events[1].data, "activity": {"kind": "travel", "status": "completed",
                                      "destination_id": "office", "location_id": "home"},
    }})
    with pytest.raises(IntegrityError, match="arrival"):
        project_verified_experiences([missing_arrival], agent_id=1, cutoff_seq=2)


def test_arrival_snapshot_at_origin_still_projects_arrived(store):
    """出行快照 location_id 是出发地；到达事实只取 arrival.location_id，且须等于目的地。"""
    arrived = _progress("travel", "route", activity={
        "status": "completed", "destination_id": "office", "location_id": "home",
        "data": {"arrival": {"location_id": "office", "access_status": "allowed"}},
    })
    event = store.commit(store.state, [arrived])[0]
    facts = project_verified_experiences([event], agent_id=1, cutoff_seq=event.event_seq)
    assert [(row["kind"], row["location_id"], row["access_status"]) for row in facts] == [
        ("arrived", "office", "allowed")]
    elsewhere = event.model_copy(update={"data": {**event.data, "activity": {
        **event.data["activity"],
        "data": {"arrival": {"location_id": "cafe", "access_status": "allowed"}},
    }}})
    with pytest.raises(IntegrityError, match="arrival location"):
        project_verified_experiences([elsewhere], agent_id=1, cutoff_seq=event.event_seq)


@pytest.mark.parametrize("count", [8, 40])
def test_recent_window_returns_latest_five_before_cutoff_with_more_history(store, count):
    """最近窗口明确排除旧经历、其他人物以及截止之后的新经历。"""
    own = store.commit(store.state, [_progress("wait", f"own-{index}") for index in range(count)])
    store.commit(store.state, [_progress("wait", "other", agent_id=2)])
    cutoff = store.last_event_seq
    store.commit(store.state, [_progress("wait", "after-cutoff")])
    facts = recent_verified_experiences(store, 1, cutoff)
    assert [row["activity_id"] for row in facts] == [f"own-{index}" for index in range(count - 5, count)]
    assert [row["source_event_id"] for row in facts] == [event.event_id for event in own[-5:]]
    assert [row["event_seq"] for row in facts] == list(range(count - 4, count + 1))


def test_actual_engine_app_progress_projects_the_consumed_content_and_app(engine_factory):
    """真实宿主自动接续内容后，经历仍引用该区间消费的内容而不是已抽出的下一项。"""
    from tests.life.conftest import stage_day_plan, start_activity
    from tests.life.test_location_sessions import _round, _until_ended

    engine = engine_factory()
    stage_day_plan(engine)
    _, started = _round(engine, "verified-app", "start_activity", start_activity("打开", "应用：抖音"))
    assert started.status.value == "in_progress"
    _until_ended(engine, started.activity_id, "verified-app")
    events = engine.context.store.read_events(engine.context.store.last_event_seq)
    sources = [event for event in events if event.event_type == "activity_progress"
               and event.activity_id == started.activity_id and event.data["content"]["position_after_seconds"]
               > event.data["content"]["position_before_seconds"]]
    assert sources and any(source.data["content"]["completed"] for source in sources)
    facts = project_verified_experiences(events, agent_id=1, cutoff_seq=engine.context.store.last_event_seq)
    played = [fact for fact in facts if fact["kind"] == "played"]
    assert len(played) == len(sources)
    for fact, source in zip(played, sources, strict=True):
        content = source.data["content"]
        assert fact["source_event_id"] == source.event_id and fact["app_id"] == "douyin"
        assert fact["content_id"] == content["content_id"]
        assert fact["position_before_seconds"] == content["position_before_seconds"]
        assert fact["position_after_seconds"] == content["position_after_seconds"]
    assert any(source.data["activity"]["data"]["content_id"] != source.data["content"]["content_id"]
               for source in sources)
