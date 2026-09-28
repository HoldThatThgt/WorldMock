"""人物动作只提交可读选择；引擎使用冻结观察和领域合同生成执行参数。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from worldmock.errors import BusinessRuleError
from worldmock.life.activities import change_activity
from worldmock.life.activity_control import PHASE_ACTIVE, PHASE_CONCLUDED, PHASE_PAUSED, PHASE_PENDING, activity_phase
from worldmock.life.interaction_menu import (
    LIFE_VERBS, PURCHASE_WAYS, TRAVEL_WAYS, USE_WAYS, WAYS, binding_action,
)
from worldmock.life.models import (
    ActivityKind, ActivityState, ActivityStatus, ConcludeActivityRequest, PauseActivityRequest,
    ReportStatusRequest, SessionTaskKind, StartActivityRequest,
)
from worldmock.life.schedule import DayPlan, PlanGoal
from worldmock.life.session_actions import SessionActionMixin


class _ActionHarness(SessionActionMixin):
    """仅为动作解析与派生参数测试提供被调用的领域边界。"""

    def __init__(self, observation: dict | None = None):
        """@brief 绑定一条已提交观察，捕获不改状态的领域调用。"""
        event = SimpleNamespace(decision_id="decision", occurred_at=100, data=observation)
        self.store = SimpleNamespace(read_agent_events=lambda *_: [event])
        self.scenario = SimpleNamespace(locations=())  # 控制判定的地点名表；本组用例都在同一停留内
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

    def _change(self, state, pending, request):
        """@brief 经真实领域转换（activities.change_activity）改变所选活动，与引擎同一路径。"""
        agent, activity = change_activity(state.agents[pending.agent_id], state.activities[request.activity_id],
                                          request, state.current_time, 60)
        return state.model_copy(update={"activities": {**state.activities, activity.activity_id: activity},
                                        "agents": {pending.agent_id: agent}}), None


class _Agent:
    """仅提供身份与当日计划的可复制人物。"""

    agent_id = 1

    def __init__(self, day_plan=None):
        """@brief 保存人物明确登记的目标。"""
        self.day_plan = day_plan

    def model_copy(self, update=None, deep=False):
        """@brief 模拟人物状态的不可变更新。"""
        return _Agent((update or {}).get("day_plan", self.day_plan))


class _State:
    """供活动控制测试使用的可复制状态：人物的活动、当日计划与当前时刻。"""

    def __init__(self, activities, agent=None):
        """@brief 保存权威活动及当前时刻。"""
        self.activities = activities
        self.current_time = 200
        self.agents = {1: agent or _Agent()}

    def model_copy(self, update, deep=False):
        """@brief 模拟 Pydantic 状态不可变更新。"""
        return _State(update.get("activities", self.activities), update.get("agents", self.agents)[1])


def _pending():
    """@brief 返回与冻结观察对应的 start_activity 动作身份。"""
    return SimpleNamespace(agent_id=1, decision_id="decision", observed_at=100, action_id="action",
                           tool_name="start_activity")


def _activity(identity, name, status=ActivityStatus.ACTIVE, **fields):
    """@brief 建立带可读名称的真实活动模型：100 开始，已结算到 150，当前时刻为 200。"""
    ended = status not in {ActivityStatus.ACTIVE, ActivityStatus.PAUSED}
    return ActivityState(activity_id=identity, agent_id=1, location_id="home", kind=ActivityKind.WAIT,
                         status=status, started_at=100, ended_at=150 if ended else None, planned_end_at=400,
                         last_settled_at=150, elapsed_seconds=50, planned_duration_seconds=300,
                         origin_action_id=f"origin-{identity}", origin_decision_id="origin-decision",
                         origin_request_event_id=f"request-{identity}", session_ids=["session-1-0001"],
                         name=name, **fields)


def _bindings(*activities, **extra):
    """@brief 冻结观察中本人可见活动的私有绑定（名称、活动身份与阶段）。"""
    return {"activity_bindings": [{"name": item.name, "activity_id": item.activity_id,
                                   "phase": activity_phase(item)} for item in activities], **extra}


#: 第五章「请求形状与菜单」的方式短语闭集，按动词分组（只有一种方式的对象也要照填）。
SPEC_WAYS_BY_VERB = {
    "travel": {"步行", "驾车"},
    "interact": {"工作", "观看", "游玩", "交谈", "通话", "阅读", "浏览", "进行", "等待",
                 "接着看", "从头再看", "接着听", "从头再听"},
    "obtain": {"到店", "远程下单", "制作"},
    "consume": {"食用", "使用"},
    "sleep": {"睡觉"},
}


def _office_and_self_bindings() -> list[dict]:
    """@brief 本轮私有绑定：可步行前往的城西办公室与「自己」（等待）。"""
    return [{"object": "城西办公室", "object_id": "office", "kind": "location",
             "actions": [binding_action("步行", "travel_to", travel_mode="walking")]},
            {"object": "自己", "object_id": "agent:1", "kind": "self",
             "actions": [binding_action("等待", "wait")]}]


def test_start_activity_schema_rejects_model_supplied_calculation():
    """人物只提交对象、动词与方式三个数组；方式必填，不能回填金额、版本、精确时长或内部目的地。"""
    request = StartActivityRequest(targets=["城西办公室"], actions=["travel"], ways=["步行"])
    assert set(request.model_dump()) == {"targets", "actions", "ways"}
    with pytest.raises(ValidationError):
        StartActivityRequest(targets=["城西办公室"], actions=["travel"])
    for field, value in {"price_version": "v1", "planned_duration_seconds": 600,
                         "destination_id": "office", "quantity": 2}.items():
        with pytest.raises(ValidationError):
            StartActivityRequest(targets=["城西办公室"], actions=["travel"], ways=["步行"], **{field: value})


def test_ways_vocabulary_maps_each_way_to_exactly_one_life_verb():
    """五个生活动词与方式短语闭集逐项对应第五章；旧的整句短语（步行前往、到店购买、配送到当前位置）不再是方式。"""
    assert LIFE_VERBS == ("travel", "interact", "obtain", "consume", "sleep")
    assert {verb: {way for way, (owner, _) in WAYS.items() if owner == verb} for verb in LIFE_VERBS} == (
        SPEC_WAYS_BY_VERB)
    assert not {"步行前往", "驾车前往", "到店购买", "配送到当前位置"} & set(WAYS)
    assert {WAYS[way] for way in TRAVEL_WAYS.values()} == {("travel", "travel_to")}
    assert {WAYS[way] for way in PURCHASE_WAYS.values()} == {("obtain", "purchase")}
    assert {WAYS[way] for way in USE_WAYS.values()} == {("consume", "consume")}
    assert WAYS["制作"] == ("obtain", "consume")


@pytest.mark.parametrize("targets,actions,ways", [
    (["城西办公室"], ["travel", "travel"], ["步行"]),
    (["城西办公室"], ["travel"], ["步行", "步行"]),
    (["城西办公室", "城西办公室"], ["travel", "travel"], ["步行", "步行"]),
    ([], [], []),
    (["城西办公室", "自己", "城东公园"], ["travel", "interact", "travel"], ["步行", "等待", "步行"]),
    (["城东公园"], ["travel"], ["步行"]),
    (["城西办公室"], ["interact"], ["步行"]),
    (["城西办公室"], ["travel"], ["驾车"]),
    (["城西办公室"], ["travel"], ["步行前往"]),
    (["自己"], ["sleep"], ["等待"]),
], ids=["fewer-ways", "fewer-verbs", "duplicate-target", "empty", "three-items", "unlisted-object",
        "verb-not-offered", "way-not-offered", "retired-phrase", "verb-way-mismatch"])
def test_unresolvable_selection_is_rejected_as_invalid_request(targets, actions, ways):
    """三数组不等长、目标重复、项数不在一至二、对象未列出或（动词，方式）不在本轮闭集，都报 ACTION_REQUEST_INVALID。"""
    harness = _ActionHarness({"interaction_bindings": _office_and_self_bindings()})
    selection = harness._start_selection(_pending(), StartActivityRequest(targets=targets, actions=actions, ways=ways))
    assert selection is None
    with pytest.raises(BusinessRuleError, match="ACTION_REQUEST_INVALID"):
        harness._start_activity(None, _pending(), selection)


def test_two_offered_selections_resolve_item_by_item_to_internal_actions():
    """两项逐项映射到各自的绑定行与内部标准动作，顺序与请求一致。"""
    harness = _ActionHarness({"interaction_bindings": _office_and_self_bindings()})
    selected = harness._start_selection(_pending(), StartActivityRequest(
        targets=["自己", "城西办公室"], actions=["interact", "travel"], ways=["等待", "步行"]))
    assert [(row["object_id"], row["selected_action"]["verb"], row["selected_action"]["label"],
             row["selected_action"]["canonical_action"]) for row in selected] == [
        ("agent:1", "interact", "等待", "wait"), ("office", "travel", "步行", "travel_to")]


def test_route_choice_preserves_mode_but_engine_derives_exact_seconds(monkeypatch):
    """所选方式经私有绑定决定出行方式；展示给人物的分钟数不能成为执行时长。"""
    row = {"object": "城西办公室", "object_id": "office", "kind": "location",
           "actions": [binding_action("步行", "travel_to", travel_mode="walking"),
                       binding_action("驾车", "travel_to", travel_mode="driving")]}
    harness = _ActionHarness({"interaction_bindings": [row]})
    harness.scenario = SimpleNamespace()
    harness.semantics = SimpleNamespace(travel_planned_seconds=lambda *_: 673)
    monkeypatch.setattr("worldmock.life.session_actions.route_for", lambda *_: SimpleNamespace(duration_seconds=610))
    state = SimpleNamespace(agents={1: SimpleNamespace(location_id="home")}, current_time=100)
    selected = harness._start_selection(
        _pending(), StartActivityRequest(targets=["城西办公室"], actions=["travel"], ways=["步行"]))
    assert selected[0]["selected_action"]["canonical_action"] == "travel_to"
    harness._start_travel_action(state, _pending(), selected[0]["object_id"], selected[0])
    assert harness.calls[0].planned_duration_seconds == 673
    assert harness.calls[0].destination_id == "office"
    assert harness.calls[0].travel_mode == "walking"
    assert TRAVEL_WAYS["walking"] == "步行" and TRAVEL_WAYS["driving"] == "驾车"


def test_purchase_uses_each_observed_quote_and_single_unit():
    """两种供给可有不同报价版本，人物无需猜测或回填。"""
    harness = _ActionHarness()
    supply = SimpleNamespace(location_id="shop", fulfillment_modes=("delivery",),
                             delivery_destination_ids=("home",))
    harness.rules = SimpleNamespace(supplies={"first": supply, "second": supply})
    state = SimpleNamespace(agents={1: SimpleNamespace(location_id="home")}, activities={})
    selected = binding_action(PURCHASE_WAYS["delivery"], "purchase", fulfillment="delivery")
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
    assert selected["label"] == "远程下单" and selected["verb"] == "obtain"


def test_purchase_preserves_chosen_pickup_when_delivery_is_also_available():
    """同一供给支持两种履约时，执行不擅自改成人物未选择的方式。"""
    harness = _ActionHarness()
    supply = SimpleNamespace(location_id="home", fulfillment_modes=("pickup", "delivery"),
                             delivery_destination_ids=("home",))
    harness.rules = SimpleNamespace(supplies={"offer": supply})
    state = SimpleNamespace(agents={1: SimpleNamespace(location_id="home")}, activities={})
    binding = {"object_id": "offer", "selected_action": binding_action("到店", "purchase", fulfillment="pickup"),
               "quote": {"price_version": "current-v3", "price_fen": 350}}
    harness._start_purchase_action(state, _pending(), [binding])
    assert harness.calls[0].fulfillment == "pickup"
    assert harness.calls[0].items[0].price_version == "current-v3"


def test_nonfood_object_uses_catalog_mode_and_one_unit():
    """非食品的“使用”选择沿目录合同进入 use，而非被误作进食。"""
    harness = _ActionHarness()
    harness.rules = SimpleNamespace(items={
        "umbrella": SimpleNamespace(use_modes=("use",), use_duration_seconds=300)})
    bindings = [{"selected_action": binding_action(USE_WAYS["use"], "consume", use_mode="use")}]
    harness._start_consume_action(_State({}), _pending(), ["umbrella"], bindings)
    request = harness.calls[0]
    assert request.use_mode == "use"
    assert request.items[0].quantity == 1
    assert bindings[0]["selected_action"]["label"] == "使用"


def test_report_two_activities_validates_all_before_updating_anything():
    """一条播报逐项覆盖两项进行中活动并重置各自播报锚点；任一项已收尾时整条拒绝，不会部分写入。

    时间与能量规格第七章「活动的生命周期」：report_status 的 task[] 逐项点名活动；接口合同「请求字段与结算检查」：
    名称须对应本人进行中、暂停中或待收尾的活动，所有状态转换先验证再原子更新。
    """
    first = _activity("morning", "工作写会议纪要（1月5日 09:00 起）")
    second = _activity("afternoon", "工作核对决议（1月5日 09:00 起）")
    harness = _ActionHarness(_bindings(first, second))
    request = ReportStatusRequest(task=[first.name, second.name], summary=["先整理议题", "再核对决议"])
    changed, result = harness._report_status(
        _State({first.activity_id: first, second.activity_id: second}), _pending(), request)
    assert result.status == "completed"
    assert result.data == {"task": [first.name, second.name], "activity_ids": ["morning", "afternoon"]}
    assert [(changed.activities[item.activity_id].progress_note,
             changed.activities[item.activity_id].report_anchor_at) for item in (first, second)] == [
        ("先整理议题", 200), ("再核对决议", 200)]

    concluded = _activity("afternoon", second.name, ActivityStatus.COMPLETED, concluded_at=180)
    original = _State({first.activity_id: first, concluded.activity_id: concluded})
    with pytest.raises(BusinessRuleError, match="ACTION_REQUEST_INVALID"):
        _ActionHarness(_bindings(first, concluded))._report_status(original, _pending(), request)
    assert (original.activities[first.activity_id].progress_note,
            original.activities[first.activity_id].report_anchor_at) == ("", None)


def test_control_requests_pair_each_task_with_its_summary():
    """三条控制工具都要求任务与人物摘要逐项对应；conclude_activity 另要求等长的 memories（可为空组）。"""
    with pytest.raises(ValidationError):
        ReportStatusRequest(task=["工作", "休息"], summary=["先写完提纲"])
    with pytest.raises(ValidationError):
        PauseActivityRequest(task=["工作"], summary=[])
    assert set(ConcludeActivityRequest(task=["工作"], summary=["今天已完成"], memories=[[]]).model_dump()) == {
        "task", "summary", "memories"}
    with pytest.raises(ValidationError):
        ConcludeActivityRequest(task=["工作", "休息"], summary=["只写一个摘要"], memories=[[], []])
    with pytest.raises(ValidationError):
        ConcludeActivityRequest(task=["工作", "休息"], summary=["先停下", "也停下"], memories=[[]])
    with pytest.raises(ValidationError):
        ConcludeActivityRequest(task=["工作"], summary=["今天已完成"])


def test_report_writes_original_summary_to_independent_day_goal_note():
    """主观目标进展沿用人物原文，不改登记与客观活动证据；播报按活动名点名，写到活动及其关联目标。"""
    goal = PlanGoal(intent_kind="休息", goal="午后稍作休息", registered_at=100,
                    progressed_at=150, progress_activity_id="completed-activity")
    plan = DayPlan(day_end=500, goals=(goal,))
    waiting = _activity("rest", "等待（1月5日 13:00 起）", task_kind=SessionTaskKind.GOAL, task_ref="休息",
                        plan_goal_intent_kind="休息")
    harness = _ActionHarness(_bindings(waiting))
    changed, _ = harness._report_status(
        _State({waiting.activity_id: waiting}, _Agent(plan)), _pending(),
        ReportStatusRequest(task=[waiting.name], summary=["还想再歇一会儿"]))
    result = changed.agents[1].day_plan.goals[0]
    assert result.progress_note == "还想再歇一会儿"
    assert changed.activities[waiting.activity_id].progress_note == "还想再歇一会儿"
    assert (result.registered_at, result.progressed_at, result.progress_activity_id) == (
        goal.registered_at, goal.progressed_at, goal.progress_activity_id)


def test_conclude_ends_only_selected_activities_with_own_summaries():
    """结束只触及逐项点名的活动，未点名的活动保持原状（时间与能量规格第七章「conclude_activity 的效果」）。

    待收尾活动不改终态，只写总结并置 concluded_at；暂停的活动先转为 cancelled（agent_cancelled）再收尾；
    记忆来源由系统逐项登记（活动、所属会话与本轮观察中该活动的已核验经历），模型不填身份。
    """
    ended = _activity("ended", "等待（1月5日 00:00 起）", ActivityStatus.COMPLETED)
    running = _activity("running", "观看午间新闻（1月5日 00:01 起）")
    paused = _activity("paused", "工作写会议纪要（1月5日 00:02 起）", ActivityStatus.PAUSED)
    experiences = [{"source_event_id": "event-waited", "activity_id": "ended"},
                   {"source_event_id": "event-watched", "activity_id": "running"}]
    harness = _ActionHarness(_bindings(ended, running, paused, verified_experiences=experiences))
    state = _State({item.activity_id: item for item in (ended, running, paused)})
    request = ConcludeActivityRequest(task=[ended.name, paused.name], summary=["等完了", "先不写了"],
                                      memories=[[], []])
    changed, result = harness._conclude_activity(state, _pending(), request)
    done, stopped = changed.activities["ended"], changed.activities["paused"]
    assert (done.status, done.concluded_at, done.progress_note) == (ActivityStatus.COMPLETED, 200, "等完了")
    assert (stopped.status, stopped.interruption_reason, stopped.concluded_at, stopped.progress_note) == (
        ActivityStatus.CANCELLED, "agent_cancelled", 200, "先不写了")
    assert (activity_phase(done), activity_phase(stopped)) == (PHASE_CONCLUDED, PHASE_CONCLUDED)
    assert changed.activities["running"] == running and activity_phase(running) == PHASE_ACTIVE
    assert result.data["task"] == [ended.name, paused.name]
    assert result.data["activity_ids"] == ["ended", "paused"]
    assert result.data["memory_sources"] == [
        {"task": ended.name, "activity_id": "ended", "session_id": "session-1-0001",
         "verified_event_ids": ["event-waited"]},
        {"task": paused.name, "activity_id": "paused", "session_id": "session-1-0001", "verified_event_ids": []}]


def test_conclude_naming_none_of_the_just_ended_activities_is_rejected():
    """有刚结束、待收尾的活动时只结束别的活动，以 ACTIVITY_CONCLUSION_REQUIRED 拒绝并点名待收尾活动，不改任何活动。"""
    ended = _activity("ended", "等待（1月5日 00:00 起）", ActivityStatus.COMPLETED)
    paused = _activity("paused", "工作写会议纪要（1月5日 00:02 起）", ActivityStatus.PAUSED)
    assert (activity_phase(ended), activity_phase(paused)) == (PHASE_PENDING, PHASE_PAUSED)
    state = _State({item.activity_id: item for item in (ended, paused)})
    with pytest.raises(BusinessRuleError, match="ACTIVITY_CONCLUSION_REQUIRED") as raised:
        _ActionHarness(_bindings(ended, paused))._conclude_activity(
            state, _pending(), ConcludeActivityRequest(task=[paused.name], summary=["先不写了"], memories=[[]]))
    assert str(raised.value.__cause__) == f"先给刚结束的{ended.name}收尾"
    assert state.activities == {"ended": ended, "paused": paused}
