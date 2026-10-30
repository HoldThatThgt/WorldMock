"""当前进度、记忆与个人事务只消费真实可见证据。"""

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from tests.unit.test_environment_engine_admission import running, world
from worldmock.configuration.inputs.environment import load_environment
from worldmock.foundation.errors import ProtocolError
from worldmock.recording_engine.events.models import EventDraft
from worldmock.recording_engine.storage.store import FootprintStore
from worldmock.configuration.inputs.models import ModelMetadata
from worldmock.state.planning.errands import ErrandState
from worldmock.simulation_engine.scheduling.plans import errands
from worldmock.simulation_engine.observation.layers import progress_layer
from worldmock.recording_engine.events.memory_evidence import memory_write_points
from worldmock.state.world.kinds import ActivityKind, ActivityStatus
from worldmock.agents.prompt.prompt_schemas import validate_layer
from worldmock.simulation_engine.observation.layers.result_projection import public_result
from worldmock.state.planning.schedule import DayPlan, PlanGoal, revise_day_plan
from worldmock.simulation_engine.observation.wording.settled_text import (
    app_progress,
    experience_sentence,
    purchase_sentence,
    record_lines,
)
from worldmock.agents.prompt import prompt_capacity
from worldmock.foundation.errors import IntegrityError
from worldmock.simulation_engine.observation.layers.progress_layer import progress_view, tool_call_record
from worldmock.simulation_engine.observation.memory.entries import AgentEvents, Decision
from worldmock.state.interaction import control_items


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
    assert progress_layer._world_records(ctx, VisibleStore([event(kind, visible="system")]), (0, 0)) == []
    assert progress_layer._world_records(ctx, VisibleStore([event(kind)]), (0, 0)) == []


def test_balance_decrease_displays_only_declared_loss_and_never_enters_memory(inputs):
    ctx = layer_context(inputs)
    fact = event("balance_decreased", amount_fen=1234)
    records = progress_layer._world_records(ctx, VisibleStore([fact]), (0, 0))
    assert records[0].record == {"occurred_at": "1970-01-01T08:01:40+08:00",
                                 "type": "balance_decreased", "message": "余额减少了 12.34 元"}
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
    """步尾登记的写入点只可能是 activity_conclusion：写了条目而没有带记忆的收尾请求就是没有来源。"""
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


def test_partial_content_requires_real_positive_progress():
    def progress(before, after, complete=False):
        return SimpleNamespace(event_type="activity_progress", source_kind="environment", visibility="agent",
            activity_id="activity", data={"activity": {"kind": "interact"}, "content": {
                "content_id": "content", "position_before_seconds": before,
                "position_after_seconds": after, "completed": complete}})
    assert app_progress([progress(0, 0)], {}) == {}
    assert app_progress([progress(0, 10)], {}) == {"activity": "content"}
    assert app_progress([progress(10, 20, True)], {"activity": "content"}) == {}


def test_purchase_fact_names_the_ordering_app_but_not_the_payment_app_or_account(inputs):
    """购买明细一行写明下单 app 的完整名称（channel），到店写「到店」，键的次序固定；支付 app 的名称、账户与配送组键
    不出现。记忆层与零点计划读到的购买句不写渠道。"""
    goods_id = next(iter(inputs.catalog.goods))
    order = {"goods_id": goods_id, "quantity": 1, "created_at": 100, "paid_at": 150,
             "total_price_fen": 1234, "destination_id": "home", "expected_at": 200,
             "fulfillment": "delivery", "app_id": "eleme",
             "payment_app_id": "alipay", "group_key": "private-group"}
    ctx = layer_context(inputs)
    sentence = purchase_sentence(ctx, order)
    assert "买了" in sentence and "12.34 元" in sentence
    assert "饿了么" not in sentence and "eleme" not in sentence and "支付宝" not in sentence
    result = public_result({"occurred_at": 150, "status": "completed", "message": "Purchased",
        "data": {"orders": [order], "resource_changes": {"account:agent:1": -1234}}}, inputs, {})
    (row,) = result["details"]["purchases"]
    assert list(row) == ["product", "quantity", "channel", "destination", "expected_at", "paid"]
    assert row["channel"] == "饿了么"
    for hidden in ("支付宝", "alipay", "eleme", "private", "account", "agent:1"):
        assert hidden not in str(result), hidden
    local = {**order, "app_id": None, "fulfillment": "pickup", "destination_id": "store"}
    assert public_result({"occurred_at": 150, "status": "completed", "message": "Purchased",
        "data": {"orders": [local]}}, inputs, {})["details"]["purchases"][0]["channel"] == "到店"
    assert "到店" not in purchase_sentence(ctx, local)


def test_unpaid_order_is_a_quote_without_a_payment_claim(inputs):
    goods_id = next(iter(inputs.catalog.goods))
    result = public_result({"occurred_at": 100, "status": "in_progress", "message": "Accepted", "data": {
        "orders": [{"goods_id": goods_id, "quantity": 1, "paid_at": None, "total_price_fen": 100,
                    "destination_id": "home", "expected_at": 200, "app_id": "meituan"}]}}, inputs, {})
    assert "price" in result["details"]["purchases"][0]
    assert "paid" not in result["details"]["purchases"][0]
    assert result["details"]["purchases"][0]["channel"] == "美团"


def test_parcel_collected_at_the_station_is_carried_as_evidence_but_not_reported_as_a_purchase(inputs):
    bought = {"goods_id": "tissue", "quantity": 1, "paid_at": None, "total_price_fen": 600, "status": "pending",
              "destination_id": "store", "expected_at": 200, "fulfillment": "pickup", "app_id": None}
    collected = {"goods_id": "online-notebook", "quantity": 1, "paid_at": 60, "total_price_fen": 1200,
                 "status": "delivered", "destination_id": "parcel-station", "expected_at": 90,
                 "fulfillment": "station_pickup", "app_id": "bilibili"}

    def started(*orders):
        return {"occurred_at": 100, "status": "in_progress", "message": "Activities started",
                "data": {"activities": [], "orders": list(orders)}}

    def lines(result):
        record = {"occurred_at": "1970-01-01T08:01:40+08:00", "type": "tool_call", "tool": "start_side_activity"}
        return record_lines(layer_context(inputs), record, [SimpleNamespace(event_type="action_result", data=result)])

    assert "purchases" not in public_result(started(collected), inputs, {})["details"]
    assert lines(started(collected)) == []
    mixed = public_result(started(bought, collected), inputs, {})["details"]["purchases"]
    assert [row["product"] for row in mixed] == ["纸巾"]
    in_transit = {**collected, "status": "pending"}
    transit = public_result(started(in_transit), inputs, {})["details"]["purchases"]
    assert [row["product"] for row in transit] == ["平台笔记本"]
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


def test_rejected_today_counts_only_evidence_from_local_midnight_to_now(tmp_path):
    """当日拒绝只取场景本地零点到此刻的持久证据：前一日最后一秒的拒绝不计，零点那一秒、清早与此刻那一秒的计入，晚于此刻的
    不计。此刻取本地 13:00：按 UTC（本地 08:00）或美东（本地 12:00）算出的零点会漏掉清早的拒绝。"""
    midnight = int(datetime(2026, 10, 10, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())
    now = midnight + 13 * 3600
    store = FootprintStore.create(tmp_path / "run", {"run_id": "run", "agent_ids": [1]},
                                  {"version": 0, "current_time": midnight - 1})

    def rejected(moment: int, code: str) -> EventDraft:
        item = {"path": ["tool_calls", 0, "arguments", code], "written": code, "why": code}
        return EventDraft(event_type="action_rejected", occurred_at=moment, agent_id=1,
                          decision_id=f"decision-{code}", action_id=f"action-{code}",
                          data={"error_code": code, "reason": code, "items": [item]})

    moments = {"YESTERDAY": midnight - 1, "MIDNIGHT": midnight, "MORNING": midnight + 7 * 3600, "NOW": now,
               "LATER": now + 60}
    for version, (code, moment) in enumerate(moments.items(), start=1):
        store.commit(store.state, {"version": version, "current_time": moment}, [rejected(moment, code)])
    context = SimpleNamespace(state=SimpleNamespace(current_time=now), agent_id=1,
                              config=SimpleNamespace(timezone="Asia/Shanghai"))
    rows = progress_layer.rejected_today(context, store)
    store.close()
    assert sorted((row["why"], row["times"]) for row in rows) == [("MIDNIGHT", 1), ("MORNING", 1), ("NOW", 1)]


def test_world_records_take_facts_after_the_published_bound_and_notices_after_the_notice_bound(inputs):
    """记忆事实（办成）只取序号大于已发布界的，非记忆提示（事务提醒、余额减少）只取序号大于提示界的。"""
    ctx = layer_context(inputs)
    store = VisibleStore([event("errand_reminded", seq=4, errand={"name": "备好饮料"}),
                          event("errand_completed", seq=6, errand={"name": "备好饮料"}),
                          event("balance_decreased", seq=7, amount_fen=100)])

    def kinds(bounds):
        return [item.record["type"] for item in progress_layer._world_records(ctx, store, bounds)]

    assert kinds((5, 3)) == ["errand_reminder", "errand_completed", "balance_decreased"]
    assert kinds((6, 4)) == ["balance_decreased"]
    assert kinds((0, 7)) == ["errand_completed"]


class _DecisionStore:
    """上一次决策的假存储：最后一个事件序号与这次决策读到的观察。"""

    last_event_seq = 9

    def __init__(self, observations):
        self.observations = observations

    def read_decision_observations(self, agent_id, decision_id, observed_at):
        assert (agent_id, decision_id, observed_at) == (1, "decision-x", 100)
        return self.observations


def test_a_previous_decision_without_its_observation_is_an_integrity_fault(monkeypatch, caplog):
    """有上一次决策而读不到它的观察是完整性故障；没有决策时为 None；有两条观察时取序号大的一条与决策时刻。"""
    decisions = [(Decision("decision-x", 100, 7),)]
    monkeypatch.setattr(progress_layer, "decision_commits", lambda store, cached, cutoff: decisions[0])
    with pytest.raises(IntegrityError, match="^Previous decision has no observation$"):
        progress_layer._previous_decision(_DecisionStore([]), AgentEvents(), 1)
    assert "Previous decision decision-x has no observation" in caplog.text
    decisions[0] = ()
    assert progress_layer._previous_decision(_DecisionStore([]), AgentEvents(), 1) is None
    decisions[0] = (Decision("decision-x", 100, 7),)
    observed = [SimpleNamespace(event_seq=3), SimpleNamespace(event_seq=5)]
    assert progress_layer._previous_decision(_DecisionStore(observed), AgentEvents(), 1) == \
        progress_layer._PreviousDecision(5, 100)


class _EndedStore:
    """结束那一秒之后只有别的动作结果的假存储。"""

    def last_event_seq_before(self, occurred_at):
        assert occurred_at == 500
        return 10

    def read_agent_events(self, event_type, agent_id, after=0):
        assert (event_type, agent_id, after) == ("action_result", 1, 10)
        return [SimpleNamespace(event_seq=11, action_id="another-action")]


def test_an_ended_activity_without_its_terminal_result_is_an_integrity_fault(caplog):
    """已结束的活动在结束那一秒之后读不到开始动作的结果：完整性故障，错误日志点名活动。"""
    activity = SimpleNamespace(activity_id="activity-ended", agent_id=1, ended_at=500, origin_action_id="start")
    with pytest.raises(IntegrityError, match="^Ended activity has no terminal result$"):
        progress_layer._terminal_result(_EndedStore(), activity, SimpleNamespace(event_seq=3))
    assert "Ended activity activity-ended has no terminal result" in caplog.text


def test_the_session_record_path_is_deleted():
    """按段取记录的旧路径与控制请求参数投影已删除；进度层的入口只收已发布界与食材包，浓缩只剩折叠。"""
    import inspect

    for name in ("records_after", "_session_records", "_tool_records", "_session_calls", "_carried_calls",
                 "_arguments", "_derived_records"):
        assert not hasattr(progress_layer, name), name
    assert not hasattr(control_items, "control_request") and not hasattr(control_items, "_MEMORY_KEYS")
    assert list(inspect.signature(record_lines).parameters) == ["context", "record", "sources"]
    assert list(inspect.signature(progress_view).parameters) == ["context", "store", "published", "kits"]
    assert list(inspect.signature(tool_call_record).parameters) == ["context", "request", "result", "kits"]
    assert "self_note" not in inspect.getsource(prompt_capacity._condensation_steps)
    record = {"occurred_at": "2026-01-05T08:00:00+08:00", "type": "tool_call", "tool": "start_main_activity",
              "arguments": {}, "result": {}, "self_note": "写下的进展。"}
    records, _ = next(prompt_capacity._condensation_steps([record], [{"protected": False, "lines": ["…"]}]))
    assert records == [{"occurred_at": record["occurred_at"], "type": "condensed",
                        "until_at": record["occurred_at"], "lines": ["…"]}]


def test_progress_tool_call_schema_takes_only_start_requests_without_self_note():
    """进度层的 tool_call 记录只有两个开始工具、没有 self_note。"""
    result = {"occurred_at": "2026-01-05T08:00:00+08:00", "status": "in_progress", "error_code": None,
              "message": "Activity started", "details": {"planned": [{"name": "测试休息", "duration": "约 15 分钟"}]}}
    record = {"occurred_at": "2026-01-05T08:00:00+08:00", "type": "tool_call", "tool": "start_main_activity",
              "arguments": {"action": "interact", "target": "家", "detail": "测试休息"}, "result": result}

    def layer(item):
        return {"layer": "进度层", "session_started_at": "2026-01-05T08:00:00+08:00", "events": [item],
                "rejected_today": []}

    validate_layer(layer(record))
    for broken in ({**record, "self_note": "写下的进展。"}, {**record, "tool": "conclude_activity"}):
        with pytest.raises(ValueError):
            validate_layer(layer(broken))

