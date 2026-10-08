"""活动控制与公开结果合同：活动目标的人读客观进度、播报暂停与结束的控制、回看、公开结果投影与内部身份隔离。

docs/design/06-agent.md「工具层」「待办层」「进度层」：进行中与刚结束的活动是工具层的活动目标（名称、阶段、
客观进度与三个控制工具），暂停的活动只在待办层；播报、暂停与结束逐项点名活动；回看可按任务、当日目标或活动名，
结果不带身份并进入进度层该调用的结果。第八章「活动」：客观进度从活动累计读取，写成人读短语并随结算增长，工作不写「还差」。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from tests.life.conftest import (activity_name, advance_next, advance_until_ready, conclude_request, context,
                                 progress_calls, stage_day_plan, start_activity, submit_round, wake_by_mail,
                                 pause_request, report_request)
from worldmock.errors import BusinessRuleError
from worldmock.life.activity_control import activity_phase, objective_progress, visible_names
from worldmock.life.models import (
    ActivityKind,
    ActivityState,
    ActivityStatus,
    AgentState,
    ConcludeActivityRequest,
    PauseActivityRequest,
    PendingAction,
    RecallTaskSummaryRequest,
    ReportStatusRequest,
    SameMomentStage,
    SessionKind,
    SessionState,
    SessionStatus,
    SessionTaskKind,
    WorldState,
    active_activities,
)
from worldmock.life.result_projection import public_result
from worldmock.life.schedule import DayPlan, PlanGoal
from worldmock.life.session_actions import ActionHandlers
from worldmock.life.sessions import moment_label
from worldmock.life.tool_layer import LayerContext, tool_layer

_ZONE = "Asia/Shanghai"
#: 2026-01-05 00:00（场景时区）。
_START = 1767542400










#: 控制动作与工具层只需要的场景切片：地点名与冻结路线（没有路线：下一步提示按可达性写，这里用不到）。
_PLACES = SimpleNamespace(timezone=_ZONE, locations=(SimpleNamespace(location_id="private-office", name="办公室", display_name="办公室"),),
                          routes=())




@pytest.mark.parametrize("request_type", [ReportStatusRequest, PauseActivityRequest, ConcludeActivityRequest])
def test_activity_control_takes_one_object_per_activity(request_type) -> None:
    """每项点名的活动是一个对象，活动名与自述写在同一对象里，结束另带该项的记忆与看法；模型接口不接受内部会话或活动定位
    字段（docs/design/04-simulation-engine.md 第六章）；请求级另有个人事务变更 errands（docs/design/04-simulation-engine.md ）。"""
    concluding = request_type is ConcludeActivityRequest
    extra = {"memories": [], "views": []} if concluding else {}
    assert set(request_type.model_fields) == {"items", "errands"}
    names = ["观看Space Habitat（1月5日 09:00 起）", "等待（1月5日 09:00 起）"]
    items = [{"activity_name": name, "summary": summary, **extra} for name, summary in zip(names, ["看到一半", "还在等"])]
    request = request_type(items=items, errands=[])
    assert [(item.activity_name, item.summary) for item in request.items] == list(zip(names, ["看到一半", "还在等"]))
    with pytest.raises(ValidationError):
        request_type(items=[], errands=[])
    for leaked in ("session_id", "activity_id"):
        with pytest.raises(ValidationError):
            request_type(items=items, errands=[], **{leaked: "internal-7"})
        with pytest.raises(ValidationError):
            request_type(items=[{**items[0], leaked: "internal-7"}], errands=[])
    if concluding:
        with pytest.raises(ValidationError):
            request_type(items=[{"activity_name": names[0], "summary": "看到一半"}], errands=[])












def _act(engine, round_id: str, tool: str, request):
    """单人物按真实顺序观察、登记并结算一轮。"""
    _, results = submit_round(engine, {1: (tool, request)}, round_id)
    return results[0]


def _observe(engine, round_id: str) -> dict:
    """读取一次当前观察（只追加观察事件，不开轮次）。"""
    return engine.observe(context(engine, round_id))


def _activity_target(observed: dict, name: str) -> dict | None:
    """工具层中按活动名找到的活动目标（不在工具层时为 None）。"""
    return next((row for row in observed["tool_layer"]["targets"]
                 if row["kind"] == "activity" and row["name"] == name), None)


def _advance_until(engine, done, round_id: str) -> None:
    """推进到条件成立；途中的决策机会以播报继续全部进行中活动。"""
    for step in range(96):
        if done():
            return
        if engine.scheduling_state().ready_agent_ids:
            running = active_activities(engine.context.state, 1)
            _act(engine, f"{round_id}-{step}", "report_status", report_request(
                *(item.name for item in running), summary="继续。"))
        else:
            advance_next(engine)
    pytest.fail("Condition was not reached at the declared boundaries")


def test_progress_rows_follow_pause_resume_end_and_conclusion(engine_factory) -> None:
    """真实运行中活动依次出现在：工具层（进行中）、待办层（暂停，带暂停时刻与恢复地点）、工具层（进行中）、工具层（刚结束）、
    两层都不在（已收尾）；写下的 summary 只作为进度层该调用的参数出现，不进工具层与待办层（第七章「进度层」，
    docs/design/06-agent.md「唯一出现原则」）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    started = _act(engine, "phase-wait", "start_activity", start_activity("等待", "自己"))
    activity_id = started.activity_id
    name = activity_name(engine, activity_id)
    opened = _observe(engine, "phase-active")
    assert _activity_target(opened, name)["phase"] == "进行中"
    assert _activity_target(opened, name)["state"] == "已做不到 1 分钟，还剩约 15 分钟"
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine)
    paused_result = _act(engine, "phase-pause", "pause_activity", pause_request(name, summary="先处理邮件"))
    assert paused_result.status == "completed"
    paused = _observe(engine, "phase-paused")
    assert _activity_target(paused, name) is None
    (item,) = paused["todo_layer"]["paused_activities"]
    assert (item["name"], item["progress"]) == (name, "已做约 1 分钟，还剩约 14 分钟")
    assert "先处理邮件" not in json.dumps([paused["tool_layer"], paused["todo_layer"]], ensure_ascii=False)
    assert progress_calls(paused, "pause_activity")[0]["arguments"] == {
        "items": [{"activity_name": name, "summary": "先处理邮件"}]}
    resumed_result = _act(engine, "phase-resume", "report_status", report_request(name, summary="回到等待"))
    assert resumed_result.status == "completed"
    assert engine.context.state.activities[activity_id].note_history[-1]["note"] == "先处理邮件"
    resumed = _observe(engine, "phase-resumed")
    assert _activity_target(resumed, name)["phase"] == "进行中" and not resumed["todo_layer"]["paused_activities"]
    assert "回到等待" not in json.dumps(resumed["tool_layer"], ensure_ascii=False)
    _advance_until(engine, lambda: engine.context.state.activities[activity_id].status != ActivityStatus.ACTIVE, "phase")
    ended = _observe(engine, "phase-ended")
    assert _activity_target(ended, name)["phase"] == "刚结束"
    assert _activity_target(ended, name)["state"] == "按计划结束，已做约 15 分钟"
    concluded_result = _act(engine, "phase-conclude", "conclude_activity", conclude_request(name, summary="等完了"))
    assert concluded_result.status == "completed"
    concluded = _observe(engine, "phase-concluded")
    assert _activity_target(concluded, name) is None and not concluded["todo_layer"]["paused_activities"]
    for tool, result, observed in (("pause_activity", paused_result, paused),
                                  ("report_status", resumed_result, resumed),
                                  ("conclude_activity", concluded_result, concluded)):
        assert result.data["activity_ids"] == [activity_id]
        assert "task" not in result.data and "items" not in result.data
        [stored] = [event for event in engine.context.store.read_agent_events("action_result", 1)
                    if event.action_id == result.action_id]
        assert stored.data["data"] == result.data
        details = progress_calls(observed, tool)[-1]["result"]["details"]
        assert "task" not in details and "items" not in details and "activity_ids" not in details
        assert details["errands"] == []
    assert progress_calls(resumed, "report_status")[-1]["result"]["details"]["planned"] == [
        {"name": name, "duration": "约 14 分钟"}]
    assert progress_calls(concluded, "conclude_activity")[-1]["result"]["details"]["outcome"] == concluded_result.data[
        "outcome"]




def _scenario(products: tuple = (), locations: tuple = ()) -> SimpleNamespace:
    """公开结果投影所需的场景切片：商品名、地点名与时区。"""
    agents = (SimpleNamespace(agent_id=1, name="林悦",
                              contacts=(SimpleNamespace(contact_id="contact-wanglei", salutation="王磊"),)),)
    return SimpleNamespace(goods=products, locations=locations, timezone=_ZONE, agents=agents)


def _result(data: dict | None = None) -> dict:
    """构造 action_result 事件正文，公开投影应主动删掉内部身份。"""
    return {"action_id": "internal-action-42", "agent_id": 1, "occurred_at": _START + 120, "status": "rejected",
            "error_code": "ACTIVITY_DURATION_INVALID", "message": "The activity duration is invalid.", "data": data or {}}


def test_fresh_result_preserves_feedback_without_action_id() -> None:
    """公开结果写带时区的时刻、状态、错误码、原文说明与 details（没有新事实时为 {}），不暴露事件或动作定位字段。"""
    public = public_result(_result(), _scenario())
    assert public == {"occurred_at": "2026-01-05T00:02:00+08:00", "status": "rejected",
                      "error_code": "ACTIVITY_DURATION_INVALID", "message": "The activity duration is invalid.", "details": {}}
    assert "internal-action-42" not in str(public)


def test_purchase_result_replaces_product_and_supply_ids_with_names() -> None:
    """多商品结果各自写商品名、份数、付款、送达地点、预计时刻（带时区 ISO，docs/design/06-agent.md「通用格式」）、
    收货人与所回应的消息（没有关联时为 null），不复述请求的方式，删除报价版本、订单、供给、收货人与消息身份。"""
    products = (SimpleNamespace(goods_id="p1", name="牛肉面"),
                SimpleNamespace(goods_id="p2", name="豆浆"))
    places = (SimpleNamespace(location_id="private-office", name="办公室", display_name="办公室"),)
    orders = [
        {"order_id": "order-1", "goods_id": "p1", "store_id": "store-1", "quantity": 1, "fulfillment": "delivery",
         "destination_id": "private-office", "expected_at": _START + 900,
         "total_price_fen": 2800, "recipient_id": "contact-wanglei", "request_message_id": "reply-private", "app_id": None, "payment_app_id": None},
        {"order_id": "order-2", "goods_id": "p2", "store_id": "store-2", "quantity": 1, "fulfillment": "pickup",
         "destination_id": "private-office", "expected_at": _START + 86_400,
         "total_price_fen": 550, "recipient_id": "agent:1", "request_message_id": None, "app_id": None, "payment_app_id": None},
    ]
    payload = {"orders": orders, "answering": "王磊 00:00 的消息"}
    public = public_result({**_result(payload), "status": "completed", "error_code": None},
                           _scenario(products, places))
    assert public["details"]["purchases"] == [
        {"product": "牛肉面", "quantity": "1 份", "paid": "28 元", "destination": "办公室",
         "expected_at": "2026-01-05T00:15:00+08:00", "recipient": "王磊", "request": "王磊 00:00 的消息"},
        {"product": "豆浆", "quantity": "1 份", "paid": "5.5 元", "destination": "办公室",
         "expected_at": "2026-01-06T00:00:00+08:00", "recipient": "林悦", "request": None},
    ]
    assert all(key not in str(public) for key in ("order-1", "supply_id", "private-v1", "s1", "contact-wanglei",
                                                   "reply-private", "agent:1"))


def test_result_details_keep_only_new_readable_facts() -> None:
    """details 的闭集键：开始活动写计划时长，到达写地点与能否进入，浏览只列名称（完整标签「<门店>出售的<商品>」），
    回看、原因、下一步、逐项明细、名称
    归一、说明与终局原样（私有的候选标签不进入）；读完的邮件写主题与正文（正文只在阅读动作的结果中），恢复的活动写恢复
    后的计划时长；不复述参数、不含内部身份；工作结果另带只含公开字段的 work_review。"""
    products = (SimpleNamespace(goods_id="p1", name="牛肉面"),)
    places = (SimpleNamespace(location_id="private-office", name="办公室", display_name="办公室"),)
    activity = {"activity_id": "internal-activity-1", "name": "步行前往办公室（1月5日 00:00 起）",
                "planned_duration_seconds": 750, "data": {"arrival": {"location_id": "private-office",
                                                                     "access_status": "denied"}}}
    item = {"path": ["tool_calls", 0, "function", "arguments", "items", 0, "target"],
            "written": "牛肉", "error_code": "SUPPLY_NOT_FOUND", "allowed_values": [],
            "expected": "An exact known product name is required.", "why": "No product has this name.",
            "candidates": [{"tool": "start_activity", "arguments": {
                "items": [{"target": "办公室出售的牛肉面", "way": "到店", "from": None, "to": None}],
                "answering": None, "prefer": "最快拿到"}, "note": None}]}
    resolved = [{"written": "办公室", "as": "地点：办公室"}]
    started = {**_result({"activity": activity, "browse_result": {"items": [{"goods_id": "p1", "store_id": "store-1",
                                                                              "location_id": "private-office",
                                                                              "price_fen": 1200}]},
                          "recall": {"name": "工作：会议纪要"}, "candidates": ["办公室出售的牛肉面"], "items": [item],
                          "resolved": resolved, "notes": ["from 可以写 null"],
                          "outcome": [{"activity": "吃牛肉面", "outcome": "完成", "items": [], "note": None}],
                          "request": {"items": [{"target": "地点：办公室", "way": "步行", "from": None, "to": None}],
                                      "answering": None, "prefer": None}}),
               "status": "in_progress", "error_code": None}
    details = public_result(started, _scenario(products, places))["details"]
    assert details == {"planned": [{"name": activity["name"], "duration": "约 12 分钟"}],
                       "arrival": {"place": "办公室", "access": "没能进去"},
                       "recall": {"name": "工作：会议纪要"}, "items": [item], "resolved": resolved,
                       "notes": ["from 可以写 null"],
                       "outcome": [{"activity": "吃牛肉面", "outcome": "完成", "items": [], "note": None}]}
    allowed = {**activity, "data": {"arrival": {"location_id": "private-office", "access_status": "allowed"}}}
    arrived = public_result({**_result({"activity": allowed}), "status": "completed", "error_code": None},
                            _scenario(products, places))["details"]
    assert arrived == {"arrival": {"place": "办公室", "access": "进入"}}
    rejected = public_result(_result({"reason": "钱不够", "next_step": "换一件便宜的", "unmet": ["funds"]}),
                             _scenario())["details"]
    assert rejected == {"reason": "钱不够", "next_step": "换一件便宜的"}
    resumed = {"activity_ids": ["internal-activity-3"],
               "resumed": [{"name": "工作会议纪要（1月5日 00:00 起）", "planned_duration_seconds": 22_409}]}
    assert public_result({**_result(resumed), "status": "completed", "error_code": None}, _scenario())["details"] == {
        "planned": [{"name": "工作会议纪要（1月5日 00:00 起）", "duration": "约 6 小时"}]}




def _begin_pair(engine, label: str) -> tuple[str, str]:
    """真实等待与应用活动共享一个请求，各自产生独立活动身份。"""
    from tests.life.conftest import start_request
    stage_day_plan(engine)
    _, rows = submit_round(engine, {1: ("start_activity", start_request(
        targets=["自己", "刷推荐"], actions=["interact", "interact"], ways=["等待", "进行"], app=[None,"抖音"]))}, label)
    assert rows[0].status == "in_progress"
    from tests.life.conftest import wake_by_mail
    def wake_for_control(current):
        wake_by_mail(current, offset=60)
        assert advance_until_ready(current)
    wake_for_control(engine)
    activities = rows[0].data["activities"]
    return tuple(activity_name(engine, row["activity_id"]) for row in activities)


def test_report_status_maps_each_activity_to_its_own_note_and_goal_note(engine_factory) -> None:
    """真实播报逐项写入各自活动历史，不改另一个活动的叙述。"""
    engine = engine_factory()
    first, second = _begin_pair(engine, "pair-report-start")
    request = report_request(first, second, summary=["还在等。", "刚看了一段视频。"])
    _act(engine, "pair-report", "report_status", request)
    data = _observe(engine, "pair-report-view")
    by_name = {item.name: item for item in engine.context.state.activities.values()}
    assert by_name[first].progress_note == "还在等。"
    assert by_name[second].progress_note == "刚看了一段视频。"
    assert all(not item.note_history for item in by_name.values())
    assert len(data["tool_layer"]["targets"]) >= 2


def test_control_names_resolve_to_one_own_activity_of_this_round(engine_factory) -> None:
    """真实轮只接受本人的已绑定活动名，找不到时整个控制请求拒绝且活动不变。"""
    engine = engine_factory()
    first, second = _begin_pair(engine, "pair-control-start")
    before = {key: value.model_dump(mode="json") for key, value in engine.context.state.activities.items()}
    result = _act(engine, "pair-control-unknown", "report_status", report_request(first, "未知活动"))
    assert result.status == "rejected"
    assert result.data["items"][0]["error_code"] == "NAME_NOT_FOUND"
    assert {key: value.model_dump(mode="json") for key, value in engine.context.state.activities.items()} == before


def test_conclude_marks_named_just_ended_activities_and_keeps_their_status(engine_factory) -> None:
    """真实活动按计划结束后收尾只补叙述，保留完成状态和结束时刻。"""
    engine = engine_factory()
    stage_day_plan(engine)
    started = _act(engine, "ending-wait-start", "start_activity", start_activity("等待", "自己"))
    key = started.activity_id
    name = activity_name(engine, key)
    for step in range(64):
        if engine.context.state.activities[key].status != "active":
            break
        if engine.scheduling_state().ready_agent_ids:
            _act(engine, f"ending-wait-report-{step}", "report_status", report_request(name))
        advance_next(engine)
    ended = engine.context.state.activities[key]
    assert ended.status == "completed"
    result = _act(engine, "ending-wait-conclude", "conclude_activity", conclude_request(name))
    assert result.status == "completed"
    after = engine.context.state.activities[key]
    assert (after.status, after.ended_at) == (ended.status, ended.ended_at)
    assert after.concluded_at is not None


def test_recall_reads_task_and_activity_without_identities(engine_factory) -> None:
    """真实主题和活动回看含历史叙述，读取不产生状态或事件变化，不泄漏内部身份。"""
    from tests.life.work_runtime import at_workplace
    def wake_for_control(current):
        wake_by_mail(current, offset=60)
        assert advance_until_ready(current)
    engine = engine_factory()
    at_workplace(engine)
    topic = engine.context.semantics.catalog.contents[engine.context.scenario.agents[0].work_content_ids[0]]
    started = _act(engine, "recall-work-start", "start_activity", start_activity("进行", engine.context.semantics.catalog.actions[topic.action_id].name))
    name = activity_name(engine, started.activity_id)
    wake_for_control(engine)
    _act(engine, "recall-work-first", "report_status", report_request(name, summary="先核对日期。"))
    wake_for_control(engine)
    _act(engine, "recall-work-second", "report_status", report_request(name, summary="正在核对金额。"))
    data = _observe(engine, "recall-work-view")
    before = engine.context.state.model_dump_json(), engine.context.store.last_event_seq
    for wanted in (name,):
        result = engine.read_task_summary(1, RecallTaskSummaryRequest(task_name=wanted), data)
        assert result["recall"]["progress_note"] == "正在核对金额。"
        assert result["recall"]["note_history"][0]["note"] == "先核对日期。"
        assert "topic_id" not in json.dumps(result) and started.activity_id not in json.dumps(result)
    assert (engine.context.state.model_dump_json(), engine.context.store.last_event_seq) == before


def test_activity_targets_carry_only_readable_objective_progress(engine_factory) -> None:
    """真实工作目标显示跨活动实际工时和上一段叙述，不泄漏内部主题身份。"""
    from tests.life.work_runtime import at_workplace
    def wake_for_control(current):
        wake_by_mail(current, offset=60)
        assert advance_until_ready(current)
    engine = engine_factory()
    at_workplace(engine)
    topic = engine.context.semantics.catalog.contents[engine.context.scenario.agents[0].work_content_ids[0]]
    started = _act(engine, "target-work-start", "start_activity", start_activity("进行", engine.context.semantics.catalog.actions[topic.action_id].name))
    name = activity_name(engine, started.activity_id)
    wake_for_control(engine)
    _act(engine, "target-work-report", "report_status", report_request(name, summary="已核对起始金额。"))
    target = _activity_target(_observe(engine, "target-work-view"), name)
    assert target["state"] == f"已做约 1 分钟，《{topic.title}》还剩约 9 小时"
    assert target["last_report"] == "已核对起始金额。"
    assert [row["tool"] for row in target["tools"]] == ["report_status", "pause_activity", "conclude_activity"]
    assert topic.content_id not in json.dumps(target) and "有效" not in target["state"]


def test_work_objective_progress_grows_with_settlement(engine_factory) -> None:
    """真实工作结算六百秒后，工具目标的已做和剩余与主题累计工时一致。"""
    from tests.life.work_runtime import at_workplace
    def wake_for_control(current):
        wake_by_mail(current, offset=60)
        assert advance_until_ready(current)
    engine = engine_factory()
    at_workplace(engine)
    topic = engine.context.semantics.catalog.contents[engine.context.scenario.agents[0].work_content_ids[0]]
    started = _act(engine, "progress-work-start", "start_activity", start_activity("进行", engine.context.semantics.catalog.actions[topic.action_id].name))
    name = activity_name(engine, started.activity_id)
    wake_by_mail(engine, offset=600)
    advance_next(engine)
    activity = engine.context.state.activities[started.activity_id]
    assert activity.elapsed_seconds == 600
    target = _activity_target(_observe(engine, "progress-work-view"), name)
    assert target["state"] == f"已做约 10 分钟，《{topic.title}》还剩约 9 小时"
    assert "有效" not in target["state"]
