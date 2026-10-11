"""当前进度、记忆与个人事务只消费真实可见证据。"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from tests.unit.test_environment_engine_admission import running, world
from worldmock.environment import load_environment
from worldmock.errors import IntegrityError, ProtocolError
from worldmock.footprints.models import EventDraft
from worldmock.input_models import ModelMetadata
from worldmock.life.errand_state import ErrandState
from worldmock.life import errands, memory_layer, progress_layer
from worldmock.life.memory_evidence import memory_write_points
from worldmock.life.models import ActivityKind, ActivityStatus
from worldmock.life.prompt_schemas import validate_layer
from worldmock.life.result_projection import public_result
from worldmock.life.schedule import DayPlan, PlanGoal, revise_day_plan
from worldmock.life.settled_text import app_progress, experience_sentence, purchase_sentence, record_lines


@pytest.fixture
def inputs():
    path = Path(__file__).resolve().parents[2] / "fixtures/dataset-environment"
    metadata = lambda config: ModelMetadata(requested_model=config.requested_model,
        metadata_url=config.metadata_url, context_window=262144, max_output_tokens=8192)
    return load_environment(path, metadata_loader=metadata)


def layer_context(inputs, state=None):
    return SimpleNamespace(inputs=inputs, config=inputs.config, catalog=inputs.catalog,
                           persona=inputs.persona, state=state or world(), agent_id=1,
                           names={"activities": {}})


def event(kind, *, visible="agent", seq=1, **data):
    return SimpleNamespace(event_type="environment_event", event_id=f"event-{seq}", event_seq=seq,
                           agent_id=1, activity_id=None, occurred_at=100, visibility=visible,
                           source_kind="environment", data={"kind": kind, **data})


class VisibleStore:
    def __init__(self, events):
        self.events = events

    def read_agent_events(self, event_type, agent_id, after=0):
        return [row for row in self.events if row.event_type == event_type
                and row.agent_id == agent_id and row.event_seq > after]


def errand(quantity=1):
    return ErrandState(errand_id="buy:errand:0", name="备好饮料", product_id="coffee", quantity=quantity,
        progress=0.0, memo=None, registered_at=50, remind_at=90, reminded_at=None,
        origin_action_id="buy", origin_event_id="registered")


def with_errand(state):
    agent = state.agents[1].model_copy(update={"errands": {"buy:errand:0": errand()}})
    return state.model_copy(update={"agents": {1: agent}})


def test_overdue_personal_errand_is_delivered_once_when_awake():
    state = with_errand(world())
    delivered, drafts = errands.deliver_reminders(state, 1)
    assert [row.data["kind"] for row in drafts] == ["errand_reminded"]
    assert delivered.agents[1].errands["buy:errand:0"].reminded_at == 100
    again, repeated = errands.deliver_reminders(delivered, 1)
    assert again == delivered and repeated == []


def test_sleep_keeps_overdue_errand_pending_until_real_awake_boundary():
    state = with_errand(world())
    sleep = running("sleep", ActivityKind.SLEEP, "first")
    state = state.model_copy(update={"activities": {sleep.activity_id: sleep}})
    unchanged, drafts = errands.deliver_reminders(state, 1)
    assert unchanged == state and drafts == []
    assert errands.boundary_times(state) == []
    assert unchanged.agents[1].errands["buy:errand:0"].reminded_at is None


@pytest.mark.parametrize("kind", ["purchase_payment", "payment_notification", "callback_effect", "app_touch",
                                  "app_foreground", "order_notification", "location_entered", "location_left",
                                  "message_arrival", "mail_arrival", "work_content_completed"])
def test_progress_excludes_system_and_retired_facts(inputs, kind):
    ctx = layer_context(inputs)
    assert progress_layer._world_records(ctx, VisibleStore([event(kind, visible="system")]), 0) == []
    assert progress_layer._world_records(ctx, VisibleStore([event(kind)]), 0) == []


def test_balance_decrease_displays_only_declared_loss_and_never_enters_memory(inputs):
    ctx = layer_context(inputs)
    fact = event("balance_decreased", amount_fen=1234)
    records = progress_layer._world_records(ctx, VisibleStore([fact]), 0)
    assert records[0].record == {"occurred_at": "1970-01-01T08:01:40+08:00",
                                 "type": "balance_decreased", "message": "余额减少了 12.34 元"}
    assert memory_layer._world_lines(SimpleNamespace(context=ctx), fact) == []
    validate_layer({"layer": "进度层", "session_started_at": records[0].record["occurred_at"],
                    "events": [records[0].record], "rejected_today": []})


def test_real_receipt_completes_errand_without_order_or_self_report_credit():
    state = with_errand(world())
    claimed = EventDraft(event_type="action_result", occurred_at=100, agent_id=1,
                        data={"status": "completed", "data": {"orders": [{"goods_id": "coffee"}]}})
    unchanged, none = errands.settle(state, [claimed])
    assert unchanged == state and none == []
    receipt = EventDraft(event_type="environment_event", occurred_at=100, agent_id=1,
                         data={"kind": "goods_received", "goods_id": "coffee", "quantity": 1})
    completed, actual = errands.settle(state, [receipt])
    assert completed.agents[1].errands == {}
    assert actual[0].data["kind"] == "errand_completed"
    assert actual[0].caused_by == [receipt.event_id]


def test_step_tail_write_points_are_only_registered_conclusions():
    """步尾登记的写入点只可能是 activity_conclusion：写了条目而没有带记忆的收尾请求就是没有来源；
    就寝反思不在这里推导。"""
    with pytest.raises(ProtocolError, match="no registered write point"):
        memory_write_points([], "decision", True)
    assert memory_write_points([], "decision", False) == []
    item = {"activity_name": "休息", "summary": "收尾。", "views": [],
            "memories": [{"type": "routine", "text": "歇好了。", "importance": 0.3, "keywords": []}]}
    concluded = SimpleNamespace(decision_id="decision", data={
        "tool_name": "conclude_activity", "request": {"items": [item], "errands": []}})
    assert memory_write_points([concluded], "decision", True) == ["activity_conclusion"]
    with pytest.raises(ProtocolError, match="no registered write point"):
        memory_write_points([concluded], "another-decision", True)


def test_saved_reflection_is_filed_by_its_recorded_date_and_needs_one(inputs):
    """记忆层只读反思登记自带的归属日期；写入点含 reflection 而没有日期的登记被拒绝。"""
    ctx = layer_context(inputs)
    episodes = [{"source": "reflection", "text": "今天的观察"}]
    dated = SimpleNamespace(occurred_at=100, data={"trigger": ["reflection"], "episodes": episodes,
                                                   "reflection": {"date": "2026-01-04", "sleep_activity_id": "s"}})
    build = SimpleNamespace(context=ctx, cached=SimpleNamespace(events={"memory_updated": [dated]}))
    assert memory_layer._reflections(build) == [("2026-01-04", "今天的观察")]
    for filed in (None, {}, {"sleep_activity_id": "s"}, "2026-01-04"):
        undated = SimpleNamespace(occurred_at=100, data={"trigger": ["reflection"], "episodes": episodes,
                                                         "reflection": filed})
        build = SimpleNamespace(context=ctx, cached=SimpleNamespace(events={"memory_updated": [undated]}))
        with pytest.raises(IntegrityError, match="Bedtime reflection has no filing date"):
            memory_layer._reflections(build)
    tail = SimpleNamespace(occurred_at=100, data={"trigger": [], "episodes": [], "reflection": None})
    build = SimpleNamespace(context=ctx, cached=SimpleNamespace(events={"memory_updated": [tail]}))
    assert memory_layer._reflections(build) == []


def test_partial_content_requires_real_positive_progress():
    def progress(before, after, complete=False):
        return SimpleNamespace(event_type="activity_progress", source_kind="environment", visibility="agent",
            activity_id="activity", data={"activity": {"kind": "interact"}, "content": {
                "content_id": "content", "position_before_seconds": before,
                "position_after_seconds": after, "completed": complete}})
    assert app_progress([progress(0, 0)], {}) == {}
    assert app_progress([progress(0, 10)], {}) == {"activity": "content"}
    assert app_progress([progress(10, 20, True)], {"activity": "content"}) == {}


def test_purchase_fact_hides_automatic_app_and_account_details(inputs):
    goods_id = next(iter(inputs.catalog.goods))
    order = {"goods_id": goods_id, "quantity": 1, "created_at": 100, "paid_at": 150,
             "total_price_fen": 1234, "destination_id": "home", "expected_at": 200,
             "fulfillment": "delivery", "app_id": "private-auto-order-app",
             "payment_app_id": "private-payment-app", "group_key": "private-group"}
    ctx = layer_context(inputs)
    sentence = purchase_sentence(ctx, order)
    assert "买了" in sentence and "12.34 元" in sentence
    assert "private" not in sentence
    result = public_result({"occurred_at": 150, "status": "completed", "message": "Purchased",
        "data": {"orders": [order], "resource_changes": {"account:agent:1": -1234}}}, inputs)
    assert set(result["details"]["purchases"][0]) == {"product", "quantity", "paid", "destination", "expected_at"}
    assert "private" not in str(result) and "account" not in str(result)


def test_unpaid_order_is_a_quote_without_a_payment_claim(inputs):
    goods_id = next(iter(inputs.catalog.goods))
    result = public_result({"occurred_at": 100, "status": "in_progress", "message": "Accepted", "data": {
        "orders": [{"goods_id": goods_id, "quantity": 1, "paid_at": None, "total_price_fen": 100,
                    "destination_id": "home", "expected_at": 200}]}}, inputs)
    assert "price" in result["details"]["purchases"][0]
    assert "paid" not in result["details"]["purchases"][0]


def test_parcel_collected_at_the_station_is_carried_as_evidence_but_not_reported_as_a_purchase(inputs):
    bought = {"goods_id": "tissue", "quantity": 1, "paid_at": None, "total_price_fen": 600, "status": "pending",
              "destination_id": "store", "expected_at": 200, "fulfillment": "pickup"}
    collected = {"goods_id": "online-notebook", "quantity": 1, "paid_at": 60, "total_price_fen": 1200,
                 "status": "delivered", "destination_id": "parcel-station", "expected_at": 90,
                 "fulfillment": "station_pickup"}

    def started(*orders):
        return {"occurred_at": 100, "status": "in_progress", "message": "Activities started",
                "data": {"activities": [], "orders": list(orders)}}

    def lines(result):
        record = {"occurred_at": "1970-01-01T08:01:40+08:00", "type": "tool_call", "tool": "start_side_activity"}
        return record_lines(layer_context(inputs), record, [SimpleNamespace(event_type="action_result", data=result)])

    assert "purchases" not in public_result(started(collected), inputs)["details"]
    assert lines(started(collected)) == []
    mixed = public_result(started(bought, collected), inputs)["details"]["purchases"]
    assert [row["product"] for row in mixed] == ["纸巾"]
    in_transit = {**collected, "status": "pending"}
    assert [row["product"] for row in public_result(started(in_transit), inputs)["details"]["purchases"]] == ["平台笔记本"]
    (sentence,) = lines(started(in_transit))
    assert "买了平台笔记本" in sentence and "快递驿站" in sentence
    local = {**bought, "paid_at": 100, "status": "delivered"}
    assert len(lines(started(local, collected))) == 1 and "买了纸巾" in lines(started(local, collected))[0]


@pytest.mark.parametrize("retired,value", [("progressed_at", 100), ("progress_activity_id", "activity"),
                                           ("progress_order_id", "order"), ("progress_note", "I finished")])
def test_goal_completion_claims_are_not_persistent_world_fields(retired, value):
    with pytest.raises(ValidationError):
        PlanGoal.model_validate({"intent_kind": "娱乐", "goal": "放松一会", "registered_at": 100, retired: value})


def test_day_plan_revision_replaces_the_full_list_including_previously_reminded_goals():
    original = DayPlan(day_end=1000, goals=(PlanGoal(intent_kind="娱乐", goal="看一会节目", registered_at=100,
                                                   remind_at=200, reminded_at=200),))
    replacement = PlanGoal(intent_kind="休息", goal="散散心", registered_at=300)
    revised = revise_day_plan(original, 300, [replacement])
    assert revised.goals == (replacement,)
    assert revise_day_plan(revised, 400, []).goals == ()


def test_unchanged_goal_reminder_is_not_delivered_twice_and_changed_reminder_is_rearmed():
    old = PlanGoal(intent_kind="娱乐", goal="看一会节目", registered_at=100, remind_at=200, reminded_at=200)
    plan = DayPlan(day_end=1000, goals=(old,))
    unchanged = PlanGoal(intent_kind="娱乐", goal="看一会节目", registered_at=300, remind_at=200)
    assert revise_day_plan(plan, 300, [unchanged]).goals == (old,)
    later = unchanged.model_copy(update={"remind_at": 400})
    changed = revise_day_plan(plan, 300, [later]).goals[0]
    assert changed.registered_at == 100 and changed.remind_at == 400 and changed.reminded_at is None
