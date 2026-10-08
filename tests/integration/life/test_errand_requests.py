"""三个控制工具请求级的个人事务变更经真实请求、引擎与 SQLite 受理（docs/design/04-simulation-engine.md 、2.3、2.7、2.9）。

经真实引擎：正式场景与目录建立的 LifeEngine 上，人物开始一段等待，再以 report_status、pause_activity、conclude_activity
带上请求级 errands 逐轮登记并结算（begin_decision_round → observe → submit_action → commit_decision_round）。受理时事务变更
与活动转换同一事务提交：每条被受理的变更恰好一条事务事件（environment_event、系统可见、人物为事务所有人，决策、动作与活动
身份为空，只经 caused_by 关联请求事件），成功结果恒带按请求顺序的回执 details.errands，被拒结果没有该键、世界不变。结构
错误（缺键、类型、枚举、交叉约束、长度、份数、条数）在环境的 _dispatch 以 ACTION_REQUEST_INVALID 拒绝，逐项路径完整到
errands[j].<键>，与宿主的 argument_report 逐项相同；请求里没有「完成」；重放的请求不再记下。
"""

from __future__ import annotations

import pytest
from agentsociety2.society import DirectToolCall
from pydantic import ValidationError

from tests.life.conftest import (activity_name, advance_next, advance_until_ready, conclude_request, context,
                                 errand_add, errand_cancel, errand_update, last_result, pause_request, report_request,
                                 stage_day_plan, stage_errands, start_activity, wake_by_mail)
from tests.integration.life.test_errand_todo_layer import _errand
from tests.integration.life.test_location_sessions import _conclude, _depart, _until_ended
from worldmock.life.errand_state import ERRAND_EVENT_KINDS
from worldmock.life.errands import remind_epoch
from worldmock.life.models import (REQUEST_MODELS, ActivityConclusionItem, ActivityProgressItem, ActivityStatus,
                                   deepseek_strict_schema)
from worldmock.life.request_errors import argument_report

#: 当前人工目录的真实商品：零食、牛肉面与一种饮料，事务只累计收货事实。
_SNACK, _MEAL, _TEA = "chips", "beef-noodles", "lemon-tea"
_SNACK_NAME, _MEAL_NAME, _TEA_NAME = "薯片", "牛肉面", "柠檬茶"
_SNACK_ERRAND, _MEAL_ERRAND, _TEA_ERRAND = "补一份零食", "晚饭吃牛肉面", "囤柠檬茶"
#: 每条变更只声明新增、更新或取消，完成由真实收货事件结算。
_CHANGES = ["新增", "更新", "取消"]


def _act(engine, round_id: str, tool: str, request):
    """单个就绪人物按真实顺序观察、登记并结算一轮，返回（本轮观察，结果）。"""
    decision = context(engine, round_id)
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    engine.submit_action(DirectToolCall(decision, f"action-{round_id}", tool, request))
    return observed, engine.commit_decision_round(round_id)[0]


def _waiting(engine_factory):
    """人物开始一段等待，以一封邮件把下一次决策机会钉在两分钟后（等待仍在进行）：（引擎，等待身份，等待名）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _, started = _act(engine, "wait", "start_activity", start_activity("等待", "自己"))
    assert started.status == "in_progress"
    _next_chance(engine)
    return engine, started.activity_id, activity_name(engine, started.activity_id)


def _next_chance(engine) -> None:
    """以一封两分钟后到达的邮件给人物下一次决策机会（进行中活动播报之后按决策节律排期，不在同一秒）。"""
    wake_by_mail(engine, offset=120)
    advance_next(engine)
    assert 1 in engine.scheduling_state().ready_agent_ids


def _errand_events(engine) -> list:
    """人物 1 的全部事务事件（六种 kind），按事件顺序。"""
    return [event for event in engine.context.store.read_agent_events("environment_event", 1)
            if event.data.get("kind") in ERRAND_EVENT_KINDS]


def _one(engine, event_type: str, action_id: str):
    """本动作唯一一条给定类型的事件。"""
    (event,) = [item for item in engine.context.store.read_agent_events(event_type, 1) if item.action_id == action_id]
    return event


def _assert_request_events(engine, result, events: list, kinds: list[str]) -> None:
    """请求驱动的事务事件：按请求顺序的 kind；environment_event、来源为环境、系统可见、人物为事务所有人，决策、动作与活动
    身份为空，caused_by 只有本动作的请求事件，时刻是受理时刻，与动作结果同一提交（同一 state_version_after）；载荷只有
    kind 与事务快照（取消另带原因），不含 activity 与 resource_changes。"""
    requested = _one(engine, "action_requested", result.action_id)
    outcome = _one(engine, "action_result", result.action_id)
    assert [event.data["kind"] for event in events] == kinds
    for event in events:
        assert (event.event_type, event.source_kind, event.visibility, event.agent_id) == (
            "environment_event", "environment", "system", 1)
        assert (event.decision_id, event.action_id, event.activity_id) == (None, None, None)
        assert event.caused_by == [requested.event_id] and event.occurred_at == result.occurred_at
        assert event.state_version_after == outcome.state_version_after
        cancelled = event.data["kind"] == "errand_cancelled"
        assert set(event.data) == {"kind", "errand"} | ({"reason"} if cancelled else set())


def _resources(engine) -> tuple:
    """@brief 事务变更不改变余额、身体、驾车可用日与订单的世界事实。"""
    agent = engine.context.state.agents[1]
    return engine.context.state.accounts, agent.body, agent.driving_days, engine.context.state.deliveries


def test_each_control_tool_records_errand_changes_with_one_event_per_change_and_an_ordered_receipt(
        engine_factory) -> None:
    """三个控制工具都带请求级 errands 并在受理时记下变更：播报新增两条、暂停写一条内容没有变化的更新（照样写
    errand_updated）、点名暂停的活动播报即恢复它并同时取消一条、更新一条（回执按请求里的顺序：先取消后更新）、收尾再新增
    一条。每条被受理的变更恰好一条事务事件，与动作结果同一提交；新增的事务以「<动作身份>:errand:<条目下标>」为身份、
    零累计量、按受理时刻换算提醒钟点；更新只替换备注与提醒；取消移出状态、事件带移出前的快照与原因；余额、身体与订单不变。
    下一次观察的待办层列出这些事务，进度层里该次调用的公开结果带同样的回执 details.errands。"""
    engine, wait_id, name = _waiting(engine_factory)
    zone, noted_at, facts = engine.context.scenario.timezone, engine.context.state.current_time, _resources(engine)
    kit = errand_add(_SNACK_ERRAND, _SNACK_NAME, memo="下班前下单送到家", remind_time="17:00")
    meal = errand_add(_MEAL_ERRAND, _MEAL_NAME, quantity=2)
    _, reported = _act(engine, "report", "report_status", report_request(name, summary="还在等。", errands=[kit, meal]))
    assert reported.status == "completed"
    assert reported.data["errands"] == [{"errand": _SNACK_ERRAND, "change": "新增"},
                                        {"errand": _MEAL_ERRAND, "change": "新增"}]
    held = engine.context.state.agents[1].errands
    assert list(held) == ["action-report:errand:0", "action-report:errand:1"]
    requested = _one(engine, "action_requested", "action-report")
    first, second = held.values()
    assert first.model_dump() == {
        "errand_id": "action-report:errand:0", "name": _SNACK_ERRAND, "product_id": _SNACK,
        "quantity": 1, "progress": 0.0, "memo": "下班前下单送到家", "registered_at": noted_at,
        "remind_at": remind_epoch("17:00", noted_at, zone), "reminded_at": None,
        "origin_action_id": "action-report", "origin_event_id": requested.event_id}
    assert (second.product_id, second.quantity, second.memo, second.remind_at) == (_MEAL, 2, None, None)
    registered = _errand_events(engine)
    _assert_request_events(engine, reported, registered, ["errand_registered", "errand_registered"])
    assert [event.data["errand"] for event in registered] == [item.model_dump(mode="json") for item in held.values()]
    assert _resources(engine) == facts

    _next_chance(engine)
    facts = _resources(engine)
    unchanged = errand_update(_SNACK_ERRAND, memo="下班前下单送到家", remind_time="17:00")
    observed, paused = _act(engine, "pause", "pause_activity", pause_request(name, errands=[unchanged]))
    rows = observed["todo_layer"]["errands"]
    assert [(row["name"], row["done_when"], row["progress"]) for row in rows] == [
        (_SNACK_ERRAND, "取得薯片 1 份", "已取得 0 份"), (_MEAL_ERRAND, "取得牛肉面 2 份", "已取得 0 份")]
    assert (paused.status, paused.data["errands"]) == ("completed", [{"errand": _SNACK_ERRAND, "change": "更新"}])
    assert engine.context.state.activities[wait_id].status == ActivityStatus.PAUSED
    updated = _errand_events(engine)[2:]
    _assert_request_events(engine, paused, updated, ["errand_updated"])
    assert updated[0].data["errand"] == registered[0].data["errand"]
    assert _resources(engine) == facts

    changes = [errand_cancel(_MEAL_ERRAND, "改在公司吃了"), errand_update(_SNACK_ERRAND, memo="周末去超市买")]
    _, resumed = _act(engine, "resume", "report_status", report_request(name, summary="接着等。", errands=changes))
    assert resumed.status == "completed" and [row["name"] for row in resumed.data["resumed"]] == [name]
    assert engine.context.state.activities[wait_id].status == ActivityStatus.ACTIVE
    assert resumed.data["errands"] == [{"errand": _MEAL_ERRAND, "change": "取消"},
                                       {"errand": _SNACK_ERRAND, "change": "更新"}]
    cancelled, renewed = _errand_events(engine)[3:]
    _assert_request_events(engine, resumed, [cancelled, renewed], ["errand_cancelled", "errand_updated"])
    assert (cancelled.data["errand"], cancelled.data["reason"]) == (registered[1].data["errand"], "改在公司吃了")
    kept = engine.context.state.agents[1].errands["action-report:errand:0"]
    assert renewed.data["errand"] == kept.model_dump(mode="json")
    assert (kept.memo, kept.remind_at, kept.registered_at, kept.quantity) == ("周末去超市买", None, noted_at, 1)
    assert list(engine.context.state.agents[1].errands) == ["action-report:errand:0"]
    assert _resources(engine) == facts

    _next_chance(engine)
    facts = _resources(engine)
    tea = errand_add(_TEA_ERRAND, _TEA_NAME, quantity=3)
    _, concluded = _act(engine, "conclude", "conclude_activity", conclude_request(name, errands=[tea]))
    assert concluded.status == "completed" and concluded.data["errands"] == [{"errand": _TEA_ERRAND, "change": "新增"}]
    added = _errand_events(engine)[5:]
    _assert_request_events(engine, concluded, added, ["errand_registered"])
    assert added[0].data["errand"]["product_id"] == _TEA and added[0].data["errand"]["errand_id"] == (
        "action-conclude:errand:0")
    assert _resources(engine) == facts
    after = engine.observe(context(engine, "after"))
    assert [row["name"] for row in after["todo_layer"]["errands"]] == [_SNACK_ERRAND, _TEA_ERRAND]
    assert last_result(after)["details"]["errands"] == concluded.data["errands"]


def test_empty_errands_is_valid_and_the_receipt_is_an_empty_list(engine_factory) -> None:
    """errands 写空列表合法：三个控制工具照常受理，结果与进度层公开结果的回执 details.errands 都是空列表，没有事务事件，
    人物没有未完成事务，待办层 errands 为空列表。"""
    engine, wait_id, name = _waiting(engine_factory)
    _, reported = _act(engine, "report", "report_status", report_request(name, errands=[]))
    _next_chance(engine)
    observed, paused = _act(engine, "pause", "pause_activity", pause_request(name, errands=[]))
    assert last_result(observed)["details"]["errands"] == []
    observed, concluded = _act(engine, "conclude", "conclude_activity", conclude_request(name, errands=[]))
    assert last_result(observed)["details"]["errands"] == []
    for result in (reported, paused, concluded):
        assert result.status == "completed" and result.data["errands"] == []
    assert engine.context.state.activities[wait_id].concluded_at == engine.context.state.current_time
    after = engine.observe(context(engine, "after"))
    assert last_result(after)["details"]["errands"] == [] and after["todo_layer"]["errands"] == []
    assert engine.context.state.agents[1].errands == {} and _errand_events(engine) == []


def _add(**fields) -> dict:
    """一条合法的新增变更，按 fields 改动键。"""
    return {**errand_add(_SNACK_ERRAND, _SNACK_NAME), **fields}


def _update(**fields) -> dict:
    """一条合法的更新变更，按 fields 改动键。"""
    return {**errand_update(_SNACK_ERRAND), **fields}


def _cancel(**fields) -> dict:
    """一条合法的取消变更，按 fields 改动键。"""
    return {**errand_cancel(_SNACK_ERRAND, "不买了"), **fields}


_TYPE = "A value satisfying one of the declared JSON types is required."
_OUTSIDE = "The value is outside the declared allowed values."
_BLANK = "A non-blank string matching the declared format is required."
#: 互不冲突的五条新增（五个名称、五种不同的（结果种类，商品），都解析得到）：这样的请求只有条数超限这一处不合法。
_FIVE = [errand_add("事务一", _SNACK_NAME), errand_add("事务二", _MEAL_NAME), errand_add("事务三", "扑克牌"),
         errand_add("事务四", _TEA_NAME), errand_add("事务五", "饭团")]
#: 结构错误一例一行：（工具，请求级 errands，逐项（条目下标或 None，键，原值，合法值，why））；条目下标为 None 指 errands
#: 本身。上限只写在 why，expected 只有类型与格式。
_STRUCTURAL = {
    "missing_key": ("report_status", [{key: value for key, value in _add().items() if key != "memo"}],
                    [(0, "memo", None, [], "This required field was omitted.")]),
    "wrong_type": ("pause_activity", [_add(quantity="1")], [(0, "quantity", "1", [], "A JSON integer is required.")]),
    "change_outside_the_enum": ("conclude_activity", [_add(change="登记")], [(0, "change", "登记", _CHANGES, _OUTSIDE)]),
    "retired_outcome_is_forbidden": ("report_status", [_add(outcome="取得")],
                                     [(0, "outcome", "取得", [], "This field is not declared and must be removed.")]),
    "add_without_its_basis_and_with_a_reason": ("pause_activity", [_add(item=None, quantity=None,
                                                                        reason="不买了")], [
        (0, "item", None, [], "item is required when change is 新增"),
        (0, "quantity", None, [], "quantity is required when change is 新增"),
        (0, "reason", "不买了", [], "reason must be null when change is 新增")]),
    "update_touching_the_basis": ("conclude_activity", [_update(item=_SNACK_NAME, quantity=2,
                                                                reason="换一种")], [
        (0, "item", _SNACK_NAME, [], "item must be null when change is 更新"),
        (0, "quantity", 2, [], "quantity must be null when change is 更新"),
        (0, "reason", "换一种", [], "reason must be null when change is 更新")]),
    "cancel_without_a_reason_and_with_other_keys": ("report_status", [_cancel(
        item=_MEAL_NAME, quantity=1, memo="算了", remind_time="18:00", reason=None)], [
        (0, "item", _MEAL_NAME, [], "item must be null when change is 取消"),
        (0, "quantity", 1, [], "quantity must be null when change is 取消"),
        (0, "memo", "算了", [], "memo must be null when change is 取消"),
        (0, "remind_time", "18:00", [], "remind_time must be null when change is 取消"),
        (0, "reason", None, [], "reason is required when change is 取消")]),
    "blank_name": ("pause_activity", [_add(name=" ")], [(0, "name", " ", [], _BLANK)]),
    "blank_memo_in_a_later_entry": ("conclude_activity", [_add(), _update(memo="  ")],
                                    [(1, "memo", "  ", [], _BLANK)]),
    "blank_item": ("report_status", [_add(item="")], [(0, "item", "", [], _BLANK)]),
    "blank_reason": ("pause_activity", [_cancel(reason=" ")], [(0, "reason", " ", [], _BLANK)]),
    "clock_out_of_format": ("conclude_activity", [_add(remind_time="25:00")],
                            [(0, "remind_time", "25:00", [], _BLANK)]),
    "name_over_30_characters": ("report_status", [_add(name="长" * 31)],
                                [(0, "name", "长" * 31, [], "errand name must be within 30 characters")]),
    "memo_over_120_characters": ("pause_activity", [_add(memo="长" * 121)],
                                 [(0, "memo", "长" * 121, [], "errand memo must be within 120 characters")]),
    "reason_over_120_characters": ("conclude_activity", [_cancel(reason="长" * 121)], [
        (0, "reason", "长" * 121, [], "errand cancel reason must be within 120 characters")]),
    "quantity_zero": ("report_status", [_add(quantity=0)],
                      [(0, "quantity", 0, [], "errand quantity must be between 1 and 9")]),
    "quantity_ten": ("pause_activity", [_add(quantity=10)],
                     [(0, "quantity", 10, [], "errand quantity must be between 1 and 9")]),
    "five_entries": ("conclude_activity", [_add(name=f"事务{index}") for index in range(5)],
                     [(None, None, [_add(name=f"事务{index}") for index in range(5)], [],
                       "conclude_activity takes at most four errand changes")]),
    "five_unrelated_entries_in_a_report": ("report_status", _FIVE, [
        (None, None, _FIVE, [], "report_status takes at most four errand changes")]),
    "five_unrelated_entries_in_a_pause": ("pause_activity", _FIVE, [
        (None, None, _FIVE, [], "pause_activity takes at most four errand changes")]),
}


def _constructed(tool: str, name: str, errands: list[dict]):
    """不经校验构造的控制请求（模拟绕过宿主的请求）：一项合法的活动，errands 照写。"""
    if tool == "conclude_activity":
        item = ActivityConclusionItem(activity_name=name, summary="收尾。", memories=[], views=[])
    else:
        item = ActivityProgressItem(activity_name=name, summary="先这样。")
    return REQUEST_MODELS[tool].model_construct(items=[item], errands=errands)


@pytest.mark.parametrize("case", sorted(_STRUCTURAL))
def test_every_structural_error_reports_the_full_errand_path(engine_factory, case: str) -> None:
    """每种结构错误（缺键、类型、枚举外的值、各变更种类的交叉约束、空白与超长文字、钟点格式、份数 0 与 10、五条变更）：
    经环境的 _dispatch 以 ACTION_REQUEST_INVALID 拒绝，逐项路径完整到 errands[j].<键>（条数超限在 errands 本身），原值照写，
    枚举给全部合法变更，退役结果字段明确拒绝，上限写在 why；与宿主对同一参数的 argument_report
    逐项相同（只差明细码）。被拒结果没有回执键，人物没有记下事务、没有事务事件，等待没有被播报、暂停或结束。"""
    tool, errands, expected = _STRUCTURAL[case]
    engine, wait_id, name = _waiting(engine_factory)
    before = engine.context.state.activities[wait_id]
    request = _constructed(tool, name, errands)
    _, result = _act(engine, case, tool, request)
    assert (result.status, result.error_code) == ("rejected", "ACTION_REQUEST_INVALID")
    rows = result.data["items"]
    root = ["tool_calls", 0, "function", "arguments", "errands"]
    assert [(row["path"], row["written"], row["error_code"], row["allowed_values"], row["why"]) for row in rows] == [
        (root if index is None else [*root, index, key], written, "ACTION_REQUEST_INVALID", allowed, why)
        for index, key, written, allowed, why in expected]
    for row in rows:
        assert row["candidates"] == [] and not any(digit in row["expected"] for digit in "0123456789")
        assert row["expected"].startswith(("JSON type", _TYPE, "This field is required",
                                           "This field is not declared and must be removed."))
    arguments = request.model_dump(mode="json", warnings=False)
    with pytest.raises(ValidationError) as caught:
        REQUEST_MODELS[tool].model_validate(arguments)
    host = argument_report(tool, caught.value, REQUEST_MODELS[tool], arguments)
    assert [{**item.payload(), "error_code": "ACTION_REQUEST_INVALID"} for item in host.items] == rows
    assert "errands" not in result.data and engine.context.state.agents[1].errands == {} and _errand_events(engine) == []
    assert engine.context.state.activities[wait_id] == before


def _paused_app_and_pending_wait(engine_factory):
    """人物使用应用五分钟后暂停，接着等待到点：有暂停的应用使用与刚结束、待收尾的等待（只有后者占名额）。（引擎，观看名，等待名）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    shown = engine.observations._observation_data(engine.context.state.agents[1])
    _, opened = _act(engine, "app", "start_activity", start_activity("进行", "刷推荐", app=["抖音"]))
    assert opened.status == "in_progress"
    wake_by_mail(engine, offset=300)
    assert advance_until_ready(engine)
    app_name = activity_name(engine, opened.activity_id)
    assert _act(engine, "pause", "pause_activity", pause_request(app_name))[1].status == "completed"
    _, wait = _act(engine, "wait", "start_activity", start_activity("等待", "自己"))
    _until_ended(engine, wait.activity_id, "pending-wait")
    assert advance_until_ready(engine)
    assert engine.context.state.activities[wait.activity_id].status == ActivityStatus.COMPLETED
    return engine, app_name, activity_name(engine, wait.activity_id)


def test_a_rejected_request_registers_no_errand_and_writes_no_errand_event(engine_factory) -> None:
    """事务变更通过复核之后、收尾在活动检查处被业务拒绝（有刚结束的等待却只结束暂停的应用使用，ACTIVITY_CONCLUSION_REQUIRED）：
    事务变更随整请求一并不生效——世界里没有记下事务、没有事务事件，结果与下一次观察的公开结果都没有回执键，暂停的应用使用
    照旧暂停；同一组变更随一次合法的收尾就照常受理。"""
    engine, app_name, wait_name = _paused_app_and_pending_wait(engine_factory)
    noted = [errand_add(_SNACK_ERRAND, _SNACK_NAME, memo="下班前下单送到家")]
    _, rejected = _act(engine, "refused", "conclude_activity", conclude_request(app_name, errands=noted))
    assert (rejected.status, rejected.error_code) == ("rejected", "ACTIVITY_CONCLUSION_REQUIRED")
    assert "errands" not in rejected.data
    assert engine.context.state.agents[1].errands == {} and _errand_events(engine) == []
    assert _one(engine, "action_requested", "action-refused").data["request"]["errands"] == noted
    observed, concluded = _act(engine, "accepted", "conclude_activity", conclude_request(wait_name, errands=noted))
    shown = next(record for record in observed["progress_layer"]["events"]
                 if record["type"] == "tool_call" and record["tool"] == "conclude_activity")
    assert shown["result"]["status"] == "rejected" and "errands" not in shown["result"]["details"]
    assert observed["todo_layer"]["errands"] == [] and observed["todo_layer"]["paused_activities"][0]["name"] == (
        app_name)
    assert concluded.status == "completed" and concluded.data["errands"] == [{"errand": _SNACK_ERRAND, "change": "新增"}]
    assert list(engine.context.state.agents[1].errands) == ["action-accepted:errand:0"]
    assert [event.data["kind"] for event in _errand_events(engine)] == ["errand_registered"]


def test_a_request_cannot_mark_an_errand_completed(engine_factory) -> None:
    """请求里没有「标记完成」：change 的枚举只有新增、更新、取消三种。写「完成」的请求被宿主请求模型按枚举外的值拒绝，
    明细的合法值是这三种；绕过宿主到达环境时同样以 ACTION_REQUEST_INVALID 拒绝、给同样的合法值，预置的未完成事务原样
    留在状态里（累计量仍为零），没有任何事务事件。"""
    engine, _, name = _waiting(engine_factory)
    now = engine.context.state.current_time
    staged = _errand("action-a:errand:0", _SNACK_ERRAND, _SNACK, now - 60)
    stage_errands(engine, 1, [staged])
    done = {**errand_update(_SNACK_ERRAND), "change": "完成"}
    schema = deepseek_strict_schema(REQUEST_MODELS["report_status"])
    assert schema["properties"]["errands"]["items"]["properties"]["change"]["enum"] == _CHANGES
    arguments = {"items": [{"activity_name": name, "summary": "办完了。"}], "errands": [done]}
    with pytest.raises(ValidationError) as caught:
        REQUEST_MODELS["report_status"].model_validate(arguments)
    [host] = argument_report("report_status", caught.value, REQUEST_MODELS["report_status"], arguments).items
    assert (host.path, host.written, host.error_code, list(host.allowed_values)) == (
        ("errands", 0, "change"), "完成", "ARGUMENTS_INVALID", _CHANGES)
    _, result = _act(engine, "done", "report_status", _constructed("report_status", name, [done]))
    assert (result.status, result.error_code) == ("rejected", "ACTION_REQUEST_INVALID")
    [row] = result.data["items"]
    assert (row["path"][-3:], row["allowed_values"]) == (["errands", 0, "change"], _CHANGES)
    assert engine.context.state.agents[1].errands == {staged.errand_id: staged} and _errand_events(engine) == []


def test_a_replayed_request_does_not_register_twice(engine_factory) -> None:
    """同一动作身份与同一参数的重放只返回存档结果（含原回执），不重做受理：既不再记下一条事务，也不写事务事件；事务被
    取消之后重放当初记下它的请求，事务也不会回来。"""
    engine, _, name = _waiting(engine_factory)
    noted = report_request(name, errands=[errand_add(_SNACK_ERRAND, _SNACK_NAME)])
    _, first = _act(engine, "noted", "report_status", noted)
    assert first.data["errands"] == [{"errand": _SNACK_ERRAND, "change": "新增"}]
    held = dict(engine.context.state.agents[1].errands)
    _next_chance(engine)
    replay = context(engine, "replay")
    engine.begin_decision_round([replay])
    engine.observe(replay)
    engine.submit_action(DirectToolCall(replay, "action-noted", "report_status", noted))
    assert engine.commit_decision_round("replay")[0] == first
    assert engine.context.state.agents[1].errands == held
    assert [event.data["kind"] for event in _errand_events(engine)] == ["errand_registered"]
    _next_chance(engine)
    _, cancelled = _act(engine, "cancel", "pause_activity", pause_request(
        name, errands=[errand_cancel(_SNACK_ERRAND, "不买了")]))
    assert cancelled.status == "completed" and engine.context.state.agents[1].errands == {}
    again = context(engine, "again")
    engine.begin_decision_round([again])
    engine.observe(again)
    engine.submit_action(DirectToolCall(again, "action-noted", "report_status", noted))
    assert engine.commit_decision_round("again")[0] == first
    assert engine.context.state.agents[1].errands == {}
    assert [event.data["kind"] for event in _errand_events(engine)] == ["errand_registered", "errand_cancelled"]
    replayed = [event for event in engine.context.store.read_agent_events("action_result", 1)
                if event.action_id == "action-noted" and event.data.get("reused")]
    assert len(replayed) == 2 and all(event.data["data"]["errands"] == first.data["errands"] for event in replayed)


def _assert_nothing_noted(engine, rejected) -> None:
    """@brief 被拒的请求没有让任何事务变更生效：结果没有回执键，人物没有未完成事务、没有事务事件，此刻的观察里待办层与私有
    事务绑定都为空。"""
    assert rejected.status == "rejected" and "errands" not in rejected.data
    assert engine.context.state.agents[1].errands == {} and _errand_events(engine) == []
    shown = engine.observe(context(engine, f"after-{rejected.action_id}"))
    assert shown["todo_layer"]["errands"] == [] and shown["errand_bindings"] == []


def test_a_refused_pause_registers_no_errand_and_writes_no_errand_event(engine_factory) -> None:
    """@brief 「请求因任何原因被业务拒绝时事务变更一并不生效」对 pause_activity 成立（docs/design/04-simulation-engine.md
    「原子性」）：事务变更通过复核之后暂停在活动检查处被拒——点名已经暂停的等待再暂停一次（ACTION_REQUEST_INVALID，明细定位
    在活动名），步行去公司的路上暂停出行（ACTIVITY_PAUSE_UNAVAILABLE）。两次都带同一条合法的新增：结果没有回执键，世界里
    没有记下事务、没有事务事件，下一次观察的待办层与私有事务绑定都为空，活动状态照旧。同一条新增随路上一次合法的播报就
    照常受理。"""
    engine, wait_id, name = _waiting(engine_factory)
    noted = [errand_add(_SNACK_ERRAND, _SNACK_NAME, memo="下班前下单送到家")]
    assert _act(engine, "pause", "pause_activity", pause_request(name))[1].status == "completed"
    _, again = _act(engine, "pause-again", "pause_activity", pause_request(name, errands=noted))
    assert (again.status, again.error_code) == ("rejected", "ACTION_REQUEST_INVALID")
    assert [row["path"][-3:] for row in again.data["items"]] == [["items", 0, "activity_name"]]
    _assert_nothing_noted(engine, again)
    assert engine.context.state.activities[wait_id].status == ActivityStatus.PAUSED
    travel = _depart(engine, "to-office")
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine)
    road = activity_name(engine, travel.activity_id)
    _, refused = _act(engine, "pause-road", "pause_activity", pause_request(road, errands=noted))
    assert (refused.status, refused.error_code) == ("rejected", "ACTIVITY_PAUSE_UNAVAILABLE")
    _assert_nothing_noted(engine, refused)
    assert engine.context.state.activities[travel.activity_id].status == ActivityStatus.ACTIVE
    _, reported = _act(engine, "report-road", "report_status", report_request(road, errands=noted))
    assert reported.status == "completed" and reported.data["errands"] == [{"errand": _SNACK_ERRAND, "change": "新增"}]
    assert list(engine.context.state.agents[1].errands) == ["action-report-road:errand:0"]
    assert [event.data["kind"] for event in _errand_events(engine)] == ["errand_registered"]


def test_a_refused_resume_registers_no_errand_and_writes_no_errand_event(engine_factory) -> None:
    """@brief 同一条原子性对 report_status 成立，连同入口的边界（docs/design/04-simulation-engine.md 「入口」「原子性」）：
    暂停的等待留在家里，人物步行到公司后点名它播报——恢复只在开始地点，被 ACTIVITY_PRECONDITION_UNMET 拒绝（unmet 为
    activity_location）。请求带的合法新增一并不生效：结果没有回执键，世界里没有记下事务、没有事务事件，待办层与私有事务
    绑定都为空，等待照旧暂停。暂停的活动在任何地点都可用 conclude_activity 结束：同一条新增随这次收尾被受理。"""
    engine, wait_id, name = _waiting(engine_factory)
    assert _act(engine, "pause", "pause_activity", pause_request(name))[1].status == "completed"
    travel = _depart(engine, "to-office")
    _until_ended(engine, travel.activity_id, "to-office")
    assert _conclude(engine, "arrive-office", travel.activity_id).status == "completed"
    noted = [errand_add(_SNACK_ERRAND, _SNACK_NAME, memo="下班前下单送到家")]
    _, refused = _act(engine, "resume-elsewhere", "report_status",
                      report_request(name, summary="想接着等。", errands=noted))
    assert (refused.status, refused.error_code) == ("rejected", "ACTIVITY_PRECONDITION_UNMET")
    assert refused.data["unmet"] == ["activity_location"]
    _assert_nothing_noted(engine, refused)
    assert engine.context.state.activities[wait_id].status == ActivityStatus.PAUSED
    _, concluded = _act(engine, "conclude-elsewhere", "conclude_activity", conclude_request(name, errands=noted))
    assert concluded.status == "completed" and concluded.data["errands"] == [{"errand": _SNACK_ERRAND, "change": "新增"}]
    assert list(engine.context.state.agents[1].errands) == ["action-conclude-elsewhere:errand:0"]
    assert [event.data["kind"] for event in _errand_events(engine)] == ["errand_registered"]


#: 三个控制工具各自的请求构造（活动名，errands）。
_CONTROL_REQUESTS = {"report_status": report_request, "pause_activity": pause_request,
                     "conclude_activity": conclude_request}


def _state_refusing(engine_factory, tool: str):
    """@brief 各控制工具一种在活动状态检查处被拒的世界：（引擎，要点名的活动名，状态检查的拒绝码）。播报：点名暂停的等待，
    而另一段进行中的等待与它占用冲突（RESOURCE_CONFLICT）；暂停：点名的等待已经暂停（ACTION_REQUEST_INVALID）；结束：只
    点名暂停的应用使用，而另有刚结束、待收尾的等待（ACTIVITY_CONCLUSION_REQUIRED）。"""
    if tool == "conclude_activity":
        engine, app_name, _ = _paused_app_and_pending_wait(engine_factory)
        return engine, app_name, "ACTIVITY_CONCLUSION_REQUIRED"
    engine, _, name = _waiting(engine_factory)
    assert _act(engine, "pause", "pause_activity", pause_request(name))[1].status == "completed"
    if tool == "pause_activity":
        return engine, name, "ACTION_REQUEST_INVALID"
    assert _act(engine, "wait-again", "start_activity", start_activity("等待", "自己"))[1].status == "in_progress"
    _next_chance(engine)
    return engine, name, "RESOURCE_CONFLICT"


@pytest.mark.parametrize("tool", sorted(_CONTROL_REQUESTS))
def test_errand_changes_are_rechecked_before_the_activity_state_checks(engine_factory, tool: str) -> None:
    """@brief 环境受理的次序（docs/design/09-io-formats.md「原子受理」：先解析活动名，再在同一冻结观察上复核事务变更，活动状态转换
    之后才应用）：绕过宿主的请求点名一项此刻不能这样控制的活动，又点名一条不存在的事务——拒绝是事务复核的合并拒绝
    （ACTION_REQUEST_INVALID，唯一明细在 errands[0].name、NAME_NOT_FOUND），不是活动状态的拒绝。事务改成一条合法的新增再
    提交，才轮到活动状态的拒绝（各工具各自的码，明细定位在活动名），这条新增同样没有被记下。"""
    engine, name, state_code = _state_refusing(engine_factory, tool)
    request = _CONTROL_REQUESTS[tool]
    wrong = [errand_update("不存在的事务", memo="晚点办")]
    _, invalid = _act(engine, "errand-wrong", tool, request(name, errands=wrong))
    assert (invalid.status, invalid.error_code) == ("rejected", "ACTION_REQUEST_INVALID")
    assert [(row["path"][-3:], row["written"], row["error_code"]) for row in invalid.data["items"]] == [
        (["errands", 0, "name"], "不存在的事务", "NAME_NOT_FOUND")]
    _assert_nothing_noted(engine, invalid)
    noted = [errand_add(_SNACK_ERRAND, _SNACK_NAME, memo="下班前下单送到家")]
    _, refused = _act(engine, "state-wrong", tool, request(name, errands=noted))
    assert (refused.status, refused.error_code) == ("rejected", state_code)
    assert [(row["path"][-3:], row["written"]) for row in refused.data["items"]] == [
        (["items", 0, "activity_name"], name)]
    _assert_nothing_noted(engine, refused)
