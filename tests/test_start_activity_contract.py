"""人物动作只提交可读选择；引擎使用冻结观察和领域合同生成执行参数。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from worldmock.life.interaction_menu import action_label, canonical_action, purchase_action_label
from worldmock.life.models import (
    ConcludeActivityRequest, PauseActivityRequest, ReportStatusRequest, SessionState, SessionStatus,
    SessionTaskKind, StartActivityRequest,
)
from worldmock.life.schedule import DayPlan, PlanGoal
from worldmock.life.session_actions import SessionActionMixin


class _ActionHarness(SessionActionMixin):
    """仅为动作解析与派生参数测试提供被调用的领域边界。"""

    def __init__(self, observation: dict | None = None):
        """@brief 绑定一条已提交观察，捕获不改状态的领域调用。"""
        event = SimpleNamespace(decision_id="decision", occurred_at=100, data=observation)
        self.store = SimpleNamespace(read_agent_events=lambda *_: [event])
        self.calls = []

    def _purchase(self, state, pending, request):
        """@brief 捕获生成的整单请求。"""
        self.calls.append(request)
        return state, None

    def _consume(self, state, pending, request):
        """@brief 捕获生成的商品使用请求。"""
        self.calls.append(request)
        return state, None

    def _start(self, state, pending, request):
        """@brief 捕获生成的定时活动请求。"""
        self.calls.append(request)
        return state, None


class _Agent:
    """仅提供当日计划的可复制人物。"""

    def __init__(self, day_plan=None):
        """@brief 保存人物明确登记的目标。"""
        self.day_plan = day_plan

    def model_copy(self, update, deep):
        """@brief 模拟人物状态的不可变更新。"""
        return _Agent(update.get("day_plan", self.day_plan))


class _State:
    """供会话控制测试使用的可复制状态。"""

    def __init__(self, sessions, agent=None):
        """@brief 保存权威会话及当前时刻。"""
        self.sessions = sessions
        self.current_time = 200
        self.agents = {1: agent or _Agent()}
        self.activities = {}

    def model_copy(self, update, deep):
        """@brief 模拟 Pydantic 状态不可变更新。"""
        changed = _State(update.get("sessions", self.sessions), update.get("agents", self.agents)[1])
        changed.activities = update.get("activities", self.activities)
        return changed


def _pending():
    """@brief 返回与冻结观察对应的动作身份。"""
    return SimpleNamespace(agent_id=1, decision_id="decision", observed_at=100, action_id="action")


def _session(name, status=SessionStatus.ACTIVE):
    """@brief 建立含可读名称的真实会话模型。"""
    return SessionState(session_id=name, task_name="写会议纪要", session_name=name, agent_id=1,
                        task_kind=SessionTaskKind.NONE, status=status, started_at=100,
                        ended_at=200 if status == SessionStatus.CONCLUDED else None)


def test_start_activity_schema_rejects_model_supplied_calculation():
    """人物不能回填金额、版本、精确时长或内部目的地。"""
    request = StartActivityRequest(targets=["城西办公室"], actions=["步行前往"])
    assert set(request.model_dump()) == {"targets", "actions"}
    for field, value in {"price_version": "v1", "planned_duration_seconds": 600,
                         "destination_id": "office", "quantity": 2}.items():
        with pytest.raises(ValidationError):
            StartActivityRequest(targets=["城西办公室"], actions=["步行前往"], **{field: value})


def test_route_choice_preserves_mode_but_engine_derives_exact_seconds(monkeypatch):
    """路线短语决定方式；展示给人物的分钟数不能成为执行时长。"""
    row = {"object": "城西办公室", "object_id": "office", "kind": "location",
           "actions": [{"label": "步行前往", "canonical_action": "travel_to", "travel_mode": "walking"}]}
    harness = _ActionHarness({"interaction_bindings": [row]})
    harness.scenario = SimpleNamespace()
    harness.semantics = SimpleNamespace(travel_planned_seconds=lambda *_: 673)
    monkeypatch.setattr("worldmock.life.session_actions.route_for", lambda *_: SimpleNamespace(duration_seconds=610))
    state = SimpleNamespace(agents={1: SimpleNamespace(location_id="home")}, current_time=100)
    selected = harness._resolve_interaction_bindings(
        _pending(), StartActivityRequest(targets=["城西办公室"], actions=["步行前往"]))
    harness._start_travel_action(state, _pending(), selected[0]["object_id"], selected[0])
    assert harness.calls[0].planned_duration_seconds == 673
    assert harness.calls[0].destination_id == "office"
    assert harness.calls[0].travel_mode == "walking"
    assert canonical_action(action_label("travel_to", "walking")) == "travel_to"


def test_purchase_uses_each_observed_quote_and_single_unit():
    """两种供给可有不同报价版本，人物无需猜测或回填。"""
    harness = _ActionHarness()
    supply = SimpleNamespace(location_id="shop", fulfillment_modes=("delivery",),
                             delivery_destination_ids=("home",))
    harness.rules = SimpleNamespace(supplies={"first": supply, "second": supply})
    state = SimpleNamespace(agents={1: SimpleNamespace(location_id="home")})
    selected = {"label": "配送到当前位置", "canonical_action": "purchase", "fulfillment": "delivery"}
    bindings = [{"object_id": "first", "selected_action": selected,
                 "quote": {"price_version": "first-v2", "price_fen": 200}},
                {"object_id": "second", "selected_action": selected,
                 "quote": {"price_version": "second-v7", "price_fen": 500}}]
    harness._start_purchase_action(state, _pending(), bindings)
    request = harness.calls[0]
    assert [(item.supply_id, item.quantity, item.price_version) for item in request.items] == [
        ("first", 1, "first-v2"), ("second", 1, "second-v7")]
    assert request.fulfillment == "delivery"
    assert request.destination_id == "home"
    assert canonical_action(purchase_action_label("delivery")) == "purchase"


def test_purchase_preserves_chosen_pickup_when_delivery_is_also_available():
    """同一供给支持两种履约时，执行不擅自改成人物未选择的方式。"""
    harness = _ActionHarness()
    supply = SimpleNamespace(location_id="home", fulfillment_modes=("pickup", "delivery"),
                             delivery_destination_ids=("home",))
    harness.rules = SimpleNamespace(supplies={"offer": supply})
    state = SimpleNamespace(agents={1: SimpleNamespace(location_id="home")})
    binding = {"object_id": "offer", "selected_action": {"label": "到店购买",
               "canonical_action": "purchase", "fulfillment": "pickup"},
               "quote": {"price_version": "current-v3", "price_fen": 350}}
    harness._start_purchase_action(state, _pending(), [binding])
    assert harness.calls[0].fulfillment == "pickup"
    assert harness.calls[0].items[0].price_version == "current-v3"


def test_nonfood_object_uses_catalog_mode_and_one_unit():
    """非食品的“使用”选择沿目录合同进入 use，而非被误作进食。"""
    harness = _ActionHarness()
    harness.rules = SimpleNamespace(items={
        "umbrella": SimpleNamespace(use_modes=("use",), use_duration_seconds=300)})
    bindings = [{"selected_action": {"label": "使用", "canonical_action": "consume", "use_mode": "use"}}]
    harness._start_consume_action(_State({}), _pending(), ["umbrella"], bindings)
    request = harness.calls[0]
    assert request.use_mode == "use"
    assert request.items[0].quantity == 1
    assert canonical_action("使用") == "consume"


def test_report_two_sessions_validates_all_before_updating_anything():
    """一条播报分别覆盖两个任务；任一个已结束时不会部分写入。"""
    bindings = [{"task_name": "写会议纪要", "session_name": name, "session_id": name,
                 "status": "active"}
                for name in ("上午的写作", "下午的写作")]
    harness = _ActionHarness({"session_bindings": bindings})
    first, second = _session("上午的写作"), _session("下午的写作")
    request = ReportStatusRequest(task=[first.session_name, second.session_name],
                                  summary=["先整理议题", "再核对决议"])
    changed, result = harness._report_status(
        _State({first.session_id: first, second.session_id: second}), _pending(), request)
    assert result.status == "completed"
    assert [changed.sessions[item.session_id].progress_note for item in (first, second)] == [
        "先整理议题", "再核对决议"]

    concluded = _session("下午的写作", SessionStatus.CONCLUDED)
    original = _State({first.session_id: first, concluded.session_id: concluded})
    with pytest.raises(Exception, match="SESSION_NOT_ACTIVE"):
        harness._report_status(original, _pending(), request)
    assert original.sessions[first.session_id].progress_note == ""


def test_control_requests_pair_each_task_with_its_summary():
    """三条控制工具都要求任务与人物摘要逐项对应。"""
    with pytest.raises(ValidationError):
        ReportStatusRequest(task=["工作", "休息"], summary=["先写完提纲"])
    with pytest.raises(ValidationError):
        PauseActivityRequest(task=["工作"], summary=[])
    assert set(ConcludeActivityRequest(task=["工作"], summary=["今天已完成"]).model_dump()) == {
        "task", "summary"}
    with pytest.raises(ValidationError):
        ConcludeActivityRequest(task=["工作", "休息"], summary=["只写一个摘要"])


def test_report_writes_original_summary_to_independent_day_goal_note():
    """主观目标进展沿用人物原文，不改登记与客观活动证据。"""
    goal = PlanGoal(intent_kind="休息", goal="午后稍作休息", registered_at=100,
                    progressed_at=150, progress_activity_id="completed-activity")
    plan = DayPlan(day_end=500, goals=(goal,))
    session = _session("等待 自己").model_copy(update={"plan_goal_intent_kind": "休息"})
    harness = _ActionHarness({"session_bindings": [{"task_name": "午后稍作休息",
                                                     "session_name": session.session_name,
                                                     "session_id": session.session_id,
                                                     "status": "active"}]})
    changed, _ = harness._report_status(
        _State({session.session_id: session}, _Agent(plan)), _pending(),
        ReportStatusRequest(task=["午后稍作休息"], summary=["还想再歇一会儿"]))
    result = changed.agents[1].day_plan.goals[0]
    assert result.progress_note == "还想再歇一会儿"
    assert (result.registered_at, result.progressed_at, result.progress_activity_id) == (
        goal.registered_at, goal.progressed_at, goal.progress_activity_id)


def test_conclude_ends_only_selected_active_sessions_with_own_summaries():
    """结束只触及逐项选中的进行中会话，其他会话继续保留。"""
    first = _session("工作会话")
    second = _session("休息会话")
    paused = _session("稍后会话", SessionStatus.PAUSED)
    bindings = [{"task_name": session.task_name, "session_name": session.session_name,
                 "session_id": session.session_id, "status": session.status}
                for session in (first, second, paused)]
    harness = _ActionHarness({"session_bindings": bindings})
    changed, _ = harness._conclude_activity(
        _State({item.session_id: item for item in (first, second, paused)}),
        _pending(), ConcludeActivityRequest(task=[first.session_name], summary=["先结束工作"]))
    assert changed.sessions[first.session_name].status == SessionStatus.CONCLUDED
    assert changed.sessions[first.session_name].progress_note == "先结束工作"
    assert changed.sessions[second.session_name].status == SessionStatus.ACTIVE
    assert changed.sessions[paused.session_id].status == SessionStatus.PAUSED
