"""个人事务变更的名称与错误在宿主与环境两侧一致（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-记下更新取消 、2.3）。

经真实引擎：正式场景与目录建立的 LifeEngine 上，人物开始一段等待；每一轮先由同一次冻结观察（存进 SQLite 的观察事件）
准备的 LifeAgent 在宿主一侧本地校验（validate_react_decisions，与生产的 ReAct 循环同一入口），再把同一请求原样登记给
引擎并结算（begin_decision_round → observe → submit_action → commit_decision_round），即使宿主已经纠错——这正是绕过宿主
的请求。两侧调用同一纯函数 errands.change_issues、读同一份私有事务绑定：更新与取消按待办层名称解析（精确或无损归一后
唯一，从不替换），名称错误的合法值列本人全部未完成事务名；商品名错误只在 why 写最接近的至多三个商品名；新增重名、
同结果同商品、超过八条与同一事务在一次请求里被两条更新或取消点名都是 ARGUMENTS_INVALID；全部不带候选。环境把全部问题合并成一次 ACTION_REQUEST_INVALID（照常计入
同刻轮数），世界不变。
"""

from __future__ import annotations

import random

import pytest
from agentsociety2.agent.base import ReactDecision, ReactLoopRequest
from agentsociety2.society import DirectToolCall

from custom.agents.life_agent import LifeAgent
from worldmock.agents.life_feedback import feedback_report
from tests.life.conftest import context, errand_add, errand_cancel, errand_update, stage_errands
from tests.life.test_errand_changes import engine_factory
from tests.life.test_errand_requests import _constructed, _errand_events, _next_chance, _one, _waiting
from tests.life.test_errand_todo_layer import _errand
from worldmock.life.errand_state import MAX_OPEN_ERRANDS
from worldmock.life.errands import remind_epoch
from worldmock.life.models import REQUEST_MODELS, ActivityStatus

#: 当前人工目录的真实食品：零食、牛肉面与名称只差一个空格的两种柠檬茶（归一后同名）。
_SNACK, _MEAL = "chips", "beef-noodles"
_TEA, _TEA_JOINED = "lemon-tea", "lemon-tea-test"
_SNACK_ERRAND, _MEAL_ERRAND = "补一份薯片", "晚饭吃牛肉面"
#: 明细的路径前缀：宿主绑定到本次调用、环境按同一参数根投影，两侧相同。
_ROOT = ["tool_calls", 0, "function", "arguments", "errands"]
_CONTROL_TOOLS = ("report_status", "pause_activity", "conclude_activity")


def _arguments(tool: str, name: str, changes: list[dict]) -> dict:
    """控制工具的参数：点名一项活动（结束工具另带空的记忆与看法）与请求级的个人事务变更。"""
    item = {"activity_name": name, "summary": "先这样。"}
    if tool == "conclude_activity":
        item = {**item, "memories": [], "views": []}
    return {"items": [item], "errands": changes}


def _host(ctx, observed: dict) -> LifeAgent:
    """由同一次冻结观察准备的真实生活人物（宿主一侧的本地校验用它的私有绑定）。"""
    host = LifeAgent()
    host.prepare_react_loop(ReactLoopRequest(0, ctx.observed_at, [{"turn": 0, "action": "observe", "ok": True,
                                                                  "data": observed}]))
    host.build_react_tool_schemas(readonly=False)
    return host


def _round(engine, round_id: str, tool: str, arguments: dict, request=None):
    """同一次冻结观察先经宿主本地校验，再把同一请求原样登记给真实引擎并结算（宿主纠错也照样登记，即绕过宿主）：
    （观察，宿主纠错串，环境结果）。request 缺省为按参数校验出的请求模型；结构不合法的参数另给不经校验构造的请求。"""
    ctx = context(engine, round_id)
    engine.begin_decision_round([ctx])
    observed = engine.observe(ctx)
    error = _host(ctx, observed).validate_react_decisions([ReactDecision("", tool, arguments, "")], False)
    typed = request if request is not None else REQUEST_MODELS[tool].model_validate(arguments)
    engine.submit_action(DirectToolCall(ctx, f"action-{round_id}", tool, typed))
    [result] = engine.commit_decision_round(round_id)
    return observed, error, result


def _shape(rows: list[dict]) -> list[tuple]:
    """明细的可比形状（宿主与环境共用）：（完整路径，原值，明细码，合法值，why，候选）。"""
    return [(row["path"], row["written"], row["error_code"], row["allowed_values"], row["why"], row["candidates"])
            for row in rows]


def _host_rows(error: str) -> list[tuple]:
    """宿主纠错的明细（一次纠错的全部明细，路径已绑定到本次调用）；宿主受理（空串）时断言失败。"""
    assert error, "the host accepted the request"
    return _shape(feedback_report(error).payload()["items"])


def _refused(result) -> list[tuple]:
    """环境的合并拒绝：顶层码 ACTION_REQUEST_INVALID，返回明细；被拒结果没有回执键。"""
    assert (result.status, result.error_code) == ("rejected", "ACTION_REQUEST_INVALID")
    assert "errands" not in result.data
    return _shape(result.data["items"])


def _row(at: tuple[int, str], written, code: str, why: str, allowed: list[str] | None = None) -> tuple:
    """一条期望的明细：路径 errands[j].<键>，不带候选。"""
    return [*_ROOT, *at], written, code, allowed or [], why, []


def test_update_and_cancel_use_the_todo_layer_name(engine_factory) -> None:
    """更新与取消照待办层的写法点名：无损归一后唯一的写法（多一个空格）解析到待办层那一条，宿主与环境都受理，回执写待办层
    里的名称，名称不因所写的写法改变（不可改名），只替换备注与提醒；随后照待办层原样写名称取消它，取消事件带移出前的
    快照与原因，下一次观察的待办层不再列出它，另一条事务原样保留。"""
    engine, _, name = _waiting(engine_factory)
    now, zone = engine.context.state.current_time, engine.context.scenario.timezone
    kit = _errand("action-a:errand:0", _SNACK_ERRAND, _SNACK, now - 120, memo="下班前下单")
    tea = _errand("action-a:errand:1", "囤柠檬茶", _TEA, now - 60, quantity=3)
    stage_errands(engine, 1, [kit, tea])
    update = errand_update("补一份 薯片", memo="周末去超市买", remind_time="18:30")
    observed, error, updated = _round(engine, "update", "report_status", _arguments("report_status", name, [update]))
    assert [row["name"] for row in observed["todo_layer"]["errands"]] == [_SNACK_ERRAND, "囤柠檬茶"]
    assert error == "" and updated.status == "completed"
    assert updated.data["errands"] == [{"errand": _SNACK_ERRAND, "change": "更新"}]
    renewed = engine.context.state.agents[1].errands[kit.errand_id]
    assert renewed == kit.model_copy(update={"memo": "周末去超市买", "remind_at": remind_epoch("18:30", now, zone)})
    _next_chance(engine)
    cancel = errand_cancel(_SNACK_ERRAND, "公司楼下买到了")
    observed, error, cancelled = _round(engine, "cancel", "pause_activity", _arguments("pause_activity", name, [cancel]))
    assert observed["todo_layer"]["errands"][0]["memo"] == "周末去超市买"
    assert error == "" and cancelled.data["errands"] == [{"errand": _SNACK_ERRAND, "change": "取消"}]
    events = _errand_events(engine)
    assert [event.data["kind"] for event in events] == ["errand_updated", "errand_cancelled"]
    assert (events[1].data["errand"], events[1].data["reason"]) == (renewed.model_dump(mode="json"), "公司楼下买到了")
    assert engine.context.state.agents[1].errands == {tea.errand_id: tea}
    after = engine.observe(context(engine, "after"))
    assert [row["name"] for row in after["todo_layer"]["errands"]] == ["囤柠檬茶"]


def test_unknown_or_ambiguous_errand_name_lists_every_open_name_and_substitutes_nothing(engine_factory) -> None:
    """更新所写的事务名找不到（NAME_NOT_FOUND）、取消所写的事务名归一后对应两条（只差一个空格的两条事务，只能经预置
    状态得到：新增时重名即被拒）为 NAME_AMBIGUOUS：路径到 errands[j].name，合法值按待办层顺序列本人全部未完成事务名，
    没有候选。宿主一次纠错给出两条；绕过宿主的同一请求在环境被合并成一次拒绝、明细逐项相同，从不替换成近似的那一条——
    事务原样（备注没被更新、没有事务被取消），没有事务事件，暂停也没有发生。"""
    engine, wait_id, name = _waiting(engine_factory)
    now = engine.context.state.current_time
    staged = [_errand("action-a:errand:0", _SNACK_ERRAND, _SNACK, now - 120),
              _errand("action-a:errand:1", "囤 柠檬茶", _TEA, now - 60),
              _errand("action-a:errand:2", "囤柠檬茶", _TEA_JOINED, now - 60)]
    stage_errands(engine, 1, staged)
    open_names = [_SNACK_ERRAND, "囤 柠檬茶", "囤柠檬茶"]
    changes = [errand_update("补一份零食", memo="下班前下单"), errand_cancel("囤　柠檬茶", "不想喝了")]
    observed, error, result = _round(engine, "names", "pause_activity", _arguments("pause_activity", name, changes))
    assert [row["name"] for row in observed["errand_bindings"]] == open_names
    expected = [
        _row((0, "name"), "补一份零食", "NAME_NOT_FOUND", "The errand name 「补一份零食」 is not known.", open_names),
        _row((1, "name"), "囤　柠檬茶", "NAME_AMBIGUOUS", "The errand name 「囤　柠檬茶」 matches several open errands.",
             open_names)]
    assert _host_rows(error) == expected and feedback_report(error).error_code == "NAME_NOT_FOUND"
    assert _refused(result) == expected
    assert engine.context.state.agents[1].errands == {errand.errand_id: errand for errand in staged}
    assert _errand_events(engine) == [] and engine.context.state.activities[wait_id].status == ActivityStatus.ACTIVE


def test_unknown_item_reports_nearest_product_names_in_why_without_candidates(engine_factory) -> None:
    """新增的商品名找不到时 NAME_NOT_FOUND 的 why 写最接近的至多三个商品名；写成全角空格的「柠　檬茶」归一后对应
    正式目录里的两种柠檬茶，NAME_AMBIGUOUS 的 why 列出两者。两者合法值都为空——私有绑定里全部商品名（一百多个）从不列进
    allowed_values——也都没有候选；宿主与环境逐项相同。两种柠檬茶各自照目录的精确写法新增都被受理、解析到各自的商品。"""
    engine, _, name = _waiting(engine_factory)
    changes = [errand_add("买牛肉", "牛肉"), errand_add("囤茶", "柠　檬茶")]
    observed, error, result = _round(engine, "items", "conclude_activity",
                                     _arguments("conclude_activity", name, changes))
    assert {row["product_id"] for row in observed["errand_item_bindings"]} == set(engine.context.rules.goods)
    expected = [
        _row((0, "item"), "牛肉", "NAME_NOT_FOUND",
             "No product is named 「牛肉」. Nearest product names: 「牛肉面」."),
        _row((1, "item"), "柠　檬茶", "NAME_AMBIGUOUS",
             "The product name 「柠　檬茶」 matches several products: 「柠檬茶」、「柠檬茶（模拟）」.")]
    assert _host_rows(error) == expected and _refused(result) == expected
    assert engine.context.state.agents[1].errands == {} and _errand_events(engine) == []
    exact = [errand_add("一瓶柠檬茶", "柠檬茶"), errand_add("另一瓶柠檬茶", "柠檬茶（模拟）")]
    _, error, accepted = _round(engine, "exact", "report_status", _arguments("report_status", name, exact))
    assert error == "" and accepted.status == "completed"
    held = engine.context.state.agents[1].errands
    assert [held[f"action-exact:errand:{index}"].product_id for index in (0, 1)] == [_TEA, _TEA_JOINED]


def test_cancel_requires_a_reason(engine_factory) -> None:
    """取消必带原因：原因为 null 时报「reason is required when change is 取消」，只有空白时按非空白格式报，路径都到
    errands[0].reason；宿主按参数错误纠正（ARGUMENTS_INVALID），绕过宿主时环境以 ACTION_REQUEST_INVALID 逐项给出同样的
    明细，事务原样留着。写了原因的取消被受理，取消事件带这条原因。"""
    engine, _, name = _waiting(engine_factory)
    kit = _errand("action-a:errand:0", _SNACK_ERRAND, _SNACK, engine.context.state.current_time - 120)
    stage_errands(engine, 1, [kit])
    whys = []
    for index, reason in enumerate((None, "  ")):
        changes = [{**errand_cancel(_SNACK_ERRAND, "占位"), "reason": reason}]
        arguments = _arguments("pause_activity", name, changes)
        _, error, result = _round(engine, f"bare-{index}", "pause_activity", arguments,
                                  _constructed("pause_activity", name, changes))
        [host] = _host_rows(error)
        assert feedback_report(error).error_code == "ARGUMENTS_INVALID"
        assert host[:4] == ([*_ROOT, 0, "reason"], reason, "ARGUMENTS_INVALID", []) and host[5] == []
        assert _refused(result) == [(host[0], reason, "ACTION_REQUEST_INVALID", *host[3:])]
        assert engine.context.state.agents[1].errands == {kit.errand_id: kit} and _errand_events(engine) == []
        whys.append(host[4])
    assert whys[0] == "reason is required when change is 取消" and whys[1] != whys[0]
    _, error, result = _round(engine, "reasoned", "pause_activity",
                              _arguments("pause_activity", name, [errand_cancel(_SNACK_ERRAND, "已经买到了")]))
    assert error == "" and result.status == "completed" and engine.context.state.agents[1].errands == {}
    [event] = _errand_events(engine)
    assert (event.data["kind"], event.data["reason"]) == ("errand_cancelled", "已经买到了")


def test_duplicate_added_names_same_outcome_and_product_and_a_ninth_errand_are_rejected(engine_factory) -> None:
    """新增的三种状态相关冲突都是 ARGUMENTS_INVALID、why 写明冲突的事务名或上限，宿主与环境逐项相同：结果种类与商品都与
    一条未完成事务相同（errands[j].item）；名称与一条未完成事务归一后相同、与同请求较早一条新增归一后相同
    （errands[j].name，只报较晚的一条）；已有八条未完成事务时再新增（errands[j].change，上限只写在 why）。"""
    engine, _, name = _waiting(engine_factory)
    now = engine.context.state.current_time
    stage_errands(engine, 1, [_errand("action-a:errand:0", _SNACK_ERRAND, _SNACK, now - 120)])
    changes = [errand_add("再买一份零食", "薯片"),
               errand_add("补一份 薯片", "牛肉面"),
               errand_add("囤茶", "柠檬茶"), errand_add("囤 茶", "柠檬茶（模拟）")]
    _, error, result = _round(engine, "conflicts", "report_status", _arguments("report_status", name, changes))
    expected = [
        _row((0, "item"), "薯片", "ARGUMENTS_INVALID",
             f"The open errand 「{_SNACK_ERRAND}」 already tracks this product."),
        _row((1, "name"), "补一份 薯片", "ARGUMENTS_INVALID", f"An open errand is already named 「{_SNACK_ERRAND}」."),
        _row((3, "name"), "囤 茶", "ARGUMENTS_INVALID", "Two added errands share the name 「囤茶」.")]
    assert _host_rows(error) == expected and feedback_report(error).error_code == "ARGUMENTS_INVALID"
    assert _refused(result) == expected
    full = _full_errands(engine, now, "action-s")
    stage_errands(engine, 1, full)
    _, error, result = _round(engine, "ninth", "conclude_activity",
                              _arguments("conclude_activity", name, [errand_add(_SNACK_ERRAND, "薯片")]))
    expected = [_row((0, "change"), "新增", "ARGUMENTS_INVALID", "This addition would exceed the limit of 8 open errands.")]
    assert _host_rows(error) == expected and _refused(result) == expected
    assert feedback_report(error).payload()["items"][0]["expected"] == (
        "An addition that the open errands can still take is required.")
    assert engine.context.state.agents[1].errands == {errand.errand_id: errand for errand in full}
    assert _errand_events(engine) == []


def test_one_errand_named_twice_and_two_additions_with_one_outcome_and_product_are_rejected(engine_factory) -> None:
    """同一请求内的两种重复（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-记下更新取消 「同一条未完成事务在一次请求里至多被一条更新或取消
    点名」、2.3 表第 1 与第 4 行）：先更新、再以无损归一后同一条的写法取消同一条未完成事务；两条名称不同的新增结果种类
    与商品都相同。两处都是 ARGUMENTS_INVALID、不带候选、只报较晚的一条（errands[1].name 与 errands[3].item），why 写明
    冲突；宿主一次纠错与环境的合并拒绝逐项相同。整请求被拒：事务原样（备注没被更新、没被取消），没有事务事件，等待
    活动没有被播报。"""
    engine, wait_id, name = _waiting(engine_factory)
    kit = _errand("action-a:errand:0", _SNACK_ERRAND, _SNACK, engine.context.state.current_time - 120)
    stage_errands(engine, 1, [kit])
    before = engine.context.state.activities[wait_id]
    changes = [errand_update(_SNACK_ERRAND, memo="周末去超市买"), errand_cancel("补一份 薯片", "不买了"),
               errand_add("茶一", "柠檬茶"), errand_add("茶二", "柠檬茶", quantity=2)]
    _, error, result = _round(engine, "twice", "report_status", _arguments("report_status", name, changes))
    expected = [
        _row((1, "name"), "补一份 薯片", "ARGUMENTS_INVALID",
             f"The open errand 「{_SNACK_ERRAND}」 is named by more than one update or cancel in this request."),
        _row((3, "item"), "柠檬茶", "ARGUMENTS_INVALID", "Two added errands track the same product.")]
    assert _host_rows(error) == expected and feedback_report(error).error_code == "ARGUMENTS_INVALID"
    assert _refused(result) == expected
    assert engine.context.state.agents[1].errands == {kit.errand_id: kit} and _errand_events(engine) == []
    assert engine.context.state.activities[wait_id] == before


def test_cancelling_and_adding_again_with_the_original_name_and_a_new_quantity_is_accepted(engine_factory) -> None:
    """完成依据记下后不可改：同一请求取消旧事务并以原名、同一商品、新数量新增，按「取消、更新、新增」的次序求值，宿主与
    环境都受理；旧事务移出（取消事件带它移出前的累计量），新事务以「<动作身份>:errand:<条目下标>」为身份、零累计量记下，
    回执与事件按请求顺序。终结之后名称可以再用：随后取消这条新事务，后来的一次请求再以原名新增照样受理；完成也把事务
    移出未完成集合（2.1），以完成之后留下的状态（只剩别的事务）预置，原名同样可以新增。"""
    engine, _, name = _waiting(engine_factory)
    now = engine.context.state.current_time
    old = _errand("action-a:errand:0", _SNACK_ERRAND, _SNACK, now - 120, quantity=1, progress=0.5)
    stage_errands(engine, 1, [old])
    changes = [errand_cancel(_SNACK_ERRAND, "改成两份"), errand_add(_SNACK_ERRAND, "薯片", quantity=2, memo="多买一份")]
    _, error, result = _round(engine, "again", "report_status", _arguments("report_status", name, changes))
    assert error == "" and result.status == "completed"
    assert result.data["errands"] == [{"errand": _SNACK_ERRAND, "change": "取消"}, {"errand": _SNACK_ERRAND, "change": "新增"}]
    [renewed] = engine.context.state.agents[1].errands.values()
    assert (renewed.errand_id, renewed.name, renewed.quantity, renewed.progress, renewed.memo) == (
        "action-again:errand:1", _SNACK_ERRAND, 2, 0.0, "多买一份")
    cancelled, registered = _errand_events(engine)
    assert (cancelled.data["kind"], cancelled.data["errand"]) == ("errand_cancelled", old.model_dump(mode="json"))
    assert registered.data == {"kind": "errand_registered", "errand": renewed.model_dump(mode="json")}
    _next_chance(engine)
    _, error, result = _round(engine, "drop", "pause_activity",
                              _arguments("pause_activity", name, [errand_cancel(_SNACK_ERRAND, "不买了")]))
    assert error == "" and result.status == "completed" and engine.context.state.agents[1].errands == {}
    reused = [errand_add(_SNACK_ERRAND, "薯片")]
    _, error, result = _round(engine, "reuse", "report_status", _arguments("report_status", name, reused))
    assert error == "" and result.data["errands"] == [{"errand": _SNACK_ERRAND, "change": "新增"}]
    _next_chance(engine)
    meal = _errand("action-b:errand:0", _MEAL_ERRAND, _MEAL, now)
    stage_errands(engine, 1, [meal])
    _, error, result = _round(engine, "after-completion", "report_status", _arguments("report_status", name, reused))
    assert error == "" and result.status == "completed"
    assert sorted(engine.context.state.agents[1].errands) == ["action-after-completion:errand:0", meal.errand_id]


def test_a_request_bypassing_the_host_is_refused_once_and_the_world_is_unchanged(engine_factory) -> None:
    """绕过宿主的请求（宿主已在本轮纠错，请求仍原样到达环境）：三个处理器在活动名解析之后用同一函数复核，全部事务问题
    合并成一次 ACTION_REQUEST_INVALID——一条拒绝结果、一个 action_rejected，原因逐条以「; 」相连，明细按（条目下标, 键）
    排列、路径到 errands[j].<键>，与宿主那一次纠错的明细逐项相同。它不是免计的名称类拒绝：照常计入同刻轮数，人物在同一秒
    再得到一次机会。世界不变：事务、等待活动（没有被播报）与资源都原样，没有事务事件。"""
    engine, wait_id, name = _waiting(engine_factory)
    now = engine.context.state.current_time
    kit = _errand("action-a:errand:0", _SNACK_ERRAND, _SNACK, now - 120)
    stage_errands(engine, 1, [kit])
    before, rounds = engine.context.state.activities[wait_id], engine.context.state.agents[1].same_moment_rounds
    changes = [errand_add("囤茶", "柠檬茶"), errand_update("补一份零食", memo="晚点"),
               errand_add("买牛肉", "钢琴"), errand_add("囤 茶", "柠檬茶（模拟）")]
    _, error, result = _round(engine, "bypass", "report_status", _arguments("report_status", name, changes))
    expected = [
        _row((1, "name"), "补一份零食", "NAME_NOT_FOUND", "The errand name 「补一份零食」 is not known.", [_SNACK_ERRAND]),
        _row((2, "item"), "钢琴", "NAME_NOT_FOUND", "No product is named 「钢琴」."),
        _row((3, "name"), "囤 茶", "ARGUMENTS_INVALID", "Two added errands share the name 「囤茶」.")]
    assert _host_rows(error) == expected and _refused(result) == expected
    assert result.message == "; ".join(row[4] for row in expected)
    assert [event.event_type for event in engine.context.store.read_agent_events("action_rejected", 1)
            if event.action_id == "action-bypass"] == ["action_rejected"]
    assert _one(engine, "action_result", "action-bypass").data["error_code"] == "ACTION_REQUEST_INVALID"
    assert result.data["same_moment_round"] == engine.context.state.agents[1].same_moment_rounds == rounds + 1
    assert engine.context.state.agents[1].next_decision_at == now and 1 in engine.scheduling_state().ready_agent_ids
    assert engine.context.state.agents[1].errands == {kit.errand_id: kit} and engine.context.state.activities[wait_id] == before
    assert _errand_events(engine) == []


#: 性质测试的词表：新增的名称、更新与取消点名的写法（含无损归一的变体、全角空格的歧义写法与不存在的名称）、商品名
#: （含找不到的、有歧义的）、两种结果、备注与提醒钟点。
_ADD_NAMES = ("补一份零食", "买牛肉", _MEAL_ERRAND, "囤柠檬茶", "囤 柠檬茶", "事务0", "囤茶")
_TARGETS = (_SNACK_ERRAND, "补一份 薯片", "囤 柠檬茶", "囤柠檬茶", "囤　柠檬茶", _MEAL_ERRAND, "事务0", "事务 1",
            "不存在的事务")
_ITEMS = ("薯片", "牛肉面", "柠檬茶", "柠檬茶（模拟）", "柠　檬茶", "牛肉", "饭团")
_PROPERTY_ROUNDS = 60


def _full_errands(engine, now, origin):
    """@brief 八条完整状态由当前食品与取得/吃掉依据构成，不依赖已移除的大商品库。"""
    pairs = [food for food in sorted(engine.context.rules.goods) if food != _SNACK][:MAX_OPEN_ERRANDS]
    assert len(pairs) == MAX_OPEN_ERRANDS and len(set(pairs)) == MAX_OPEN_ERRANDS
    return [_errand(f"{origin}:errand:{index}", f"事务{index}", food, now - 600)
            for index, food in enumerate(pairs)]


def _states(engine) -> dict[str, list]:
    """性质测试依次预置的四种未完成事务：没有；三条（取得零食、取得柠檬茶、吃掉牛肉面）；名称只差一个空格的两条；
    已满八条。"""
    now = engine.context.state.current_time
    kit = _errand("action-p:errand:0", _SNACK_ERRAND, _SNACK, now - 300)
    tea = _errand("action-p:errand:1", "囤 柠檬茶", _TEA, now - 200)
    return {
        "none": [],
        "three": [kit, tea, _errand("action-p:errand:2", _MEAL_ERRAND, _MEAL, now - 100, quantity=2)],
        "twins": [kit, tea, _errand("action-p:errand:2", "囤柠檬茶", _TEA_JOINED, now - 100)],
        "full": _full_errands(engine, now, "action-f"),
    }


def _random_changes(rng: random.Random) -> list[dict]:
    """从固定词表确定性地抽一组结构合法的变更（零至四条，新增占多数）。"""
    changes = []
    for _ in range(rng.choice((0, 1, 1, 2, 2, 3, 4))):
        kind = rng.choice(("新增", "新增", "更新", "取消"))
        if kind == "新增":
            changes.append(errand_add(rng.choice(_ADD_NAMES), rng.choice(_ITEMS),
                                      quantity=rng.randint(1, 9), memo=rng.choice((None, "下班前去买")),
                                      remind_time=rng.choice((None, "17:00", "08:30"))))
        elif kind == "更新":
            changes.append(errand_update(rng.choice(_TARGETS), memo=rng.choice((None, "周末再说")),
                                         remind_time=rng.choice((None, "21:00"))))
        else:
            changes.append(errand_cancel(rng.choice(_TARGETS), "用不着了"))
    return changes


def _probe(engine, real, index: int, tool: str, arguments: dict):
    """环境对同一冻结观察上的另一组参数的判定：与本轮真实登记的动作同一决策、观察时刻与请求事件，只换工具、参数与动作
    身份，由引擎结算每个动作的同一步骤（_resolve：分派、受理后的记账与事件草稿）在世界副本上判定，不提交。"""
    request = REQUEST_MODELS[tool].model_validate(arguments).model_dump(mode="json")
    probe = real.model_copy(update={"action_id": f"probe-{index}", "tool_name": tool, "request": request})
    return engine.decisions._resolve(engine.context.state.model_copy(deep=True), probe)[1]


def test_whatever_the_host_accepts_the_environment_accepts(engine_factory) -> None:
    """性质测试（宿主受理的环境也受理）：四种未完成事务的状态下各开一轮决策，在同一次冻结观察上从固定词表以固定种子
    确定性地枚举变更组合（共二百四十组，三个控制工具轮换）。每组先经宿主本地校验；宿主受理的，环境对同一观察的判定也
    受理，回执按请求顺序逐条对应。每轮第一组宿主受理的变更再经真实登记、结算与 SQLite 提交，结果与该组的判定相同。
    受理与纠错两种结局在枚举里都出现。"""
    rng = random.Random(20261003)
    engine, _, name = _waiting(engine_factory)
    verdicts = {"accepted": 0, "corrected": 0}
    for state_name, staged in _states(engine).items():
        stage_errands(engine, 1, staged)
        ctx = context(engine, f"property-{state_name}")
        engine.begin_decision_round([ctx])
        host = _host(ctx, engine.observe(ctx))
        cases = [(rng.choice(_CONTROL_TOOLS), _random_changes(rng)) for _ in range(_PROPERTY_ROUNDS)]
        accepted = [(tool, changes) for tool, changes in cases if host.validate_react_decisions(
            [ReactDecision("", tool, _arguments(tool, name, changes), "")], False) == ""]
        verdicts["accepted"] += len(accepted)
        verdicts["corrected"] += len(cases) - len(accepted)
        real_changes = next(changes for _, changes in accepted)
        engine.submit_action(DirectToolCall(ctx, f"action-property-{state_name}", "report_status", REQUEST_MODELS[
            "report_status"].model_validate(_arguments("report_status", name, real_changes))))
        real = engine.decisions.round.pending_actions[0]
        receipts = []
        for index, (tool, changes) in enumerate(accepted):
            result = _probe(engine, real, index, tool, _arguments(tool, name, changes))
            assert result.status == "completed", (state_name, tool, changes, result.data)
            assert [row["change"] for row in result.data["errands"]] == [change["change"] for change in changes]
            receipts.append(result.data["errands"])
        [committed] = engine.commit_decision_round(ctx.round_id)
        assert committed.status == "completed" and committed.data["errands"] == receipts[0]
        _next_chance(engine)
    assert verdicts["accepted"] + verdicts["corrected"] == 4 * _PROPERTY_ROUNDS
    assert min(verdicts.values()) >= 40, verdicts


def test_a_name_of_30_characters_texts_of_120_characters_and_9_portions_are_accepted(engine_factory) -> None:
    """@brief 上限都含端点（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-范围与状态 「名称至多 30 字符；备注与取消原因各至多 120 字符；数量 1 至
    9」）：恰好 30 个字符的名称、120 个字符的备注、9 份的新增被宿主与环境受理并照所写的记下；下一次观察的待办层原样列出
    名称、「取得薯片 9 份」与备注。随后以恰好 120 个字符的原因取消它同样受理，取消事件带这条原因。多一个字符与
    第 10 份的拒绝见 test_errand_requests.py 的结构错误用例。"""
    engine, _, name = _waiting(engine_factory)
    long_name, memo, reason = "名" * 30, "备" * 120, "因" * 120
    added = [errand_add(long_name, "薯片", quantity=9, memo=memo)]
    _, error, noted = _round(engine, "limits", "report_status", _arguments("report_status", name, added))
    assert error == "" and noted.status == "completed"
    assert noted.data["errands"] == [{"errand": long_name, "change": "新增"}]
    [held] = engine.context.state.agents[1].errands.values()
    assert (held.name, held.product_id, held.quantity, held.memo) == (long_name, _SNACK, 9, memo)
    _next_chance(engine)
    dropped = [errand_cancel(long_name, reason)]
    observed, error, result = _round(engine, "drop", "pause_activity", _arguments("pause_activity", name, dropped))
    [row] = observed["todo_layer"]["errands"]
    assert (row["name"], row["done_when"], row["memo"]) == (long_name, "取得薯片 9 份", memo)
    assert error == "" and result.status == "completed" and engine.context.state.agents[1].errands == {}
    registered, cancelled = _errand_events(engine)
    assert (registered.data["kind"], registered.data["errand"]["quantity"]) == ("errand_registered", 9)
    assert (cancelled.data["kind"], cancelled.data["reason"]) == ("errand_cancelled", reason)


def test_an_errand_that_a_request_only_updates_still_counts_as_open(engine_factory) -> None:
    """@brief 只扣除本请求取消的事务（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-记下更新取消 「重名、同结果同商品与上限都按扣除本请求取消之后的
    未完成集合判定」）：同一请求更新一条未完成事务、又新增一条，被更新的那条仍在未完成集合里。新增的名称与它归一后相同
    （errands[1].name）、结果种类与商品都与它相同（errands[1].item）、已有八条时再新增（errands[1].change）都是
    ARGUMENTS_INVALID，宿主与环境逐项相同；整请求被拒，备注没有被更新，没有事务事件。把更新换成取消，同一条新增就被受理。"""
    engine, _, name = _waiting(engine_factory)
    kit = _errand("action-a:errand:0", _SNACK_ERRAND, _SNACK, engine.context.state.current_time - 120)
    stage_errands(engine, 1, [kit])
    update = errand_update(_SNACK_ERRAND, memo="晚点买")
    cases = {
        "renamed": (errand_add("补一份 薯片", "牛肉面"), _row(
            (1, "name"), "补一份 薯片", "ARGUMENTS_INVALID", f"An open errand is already named 「{_SNACK_ERRAND}」.")),
        "doubled": (errand_add("再买一份零食", "薯片"), _row(
            (1, "item"), "薯片", "ARGUMENTS_INVALID",
            f"The open errand 「{_SNACK_ERRAND}」 already tracks this product.")),
    }
    for round_id, (added, expected) in cases.items():
        _, error, result = _round(engine, round_id, "report_status", _arguments("report_status", name, [update, added]))
        assert _host_rows(error) == [expected] and _refused(result) == [expected]
    assert engine.context.state.agents[1].errands == {kit.errand_id: kit} and _errand_events(engine) == []
    full = _states(engine)["full"]
    stage_errands(engine, 1, full)
    added = errand_add(_SNACK_ERRAND, "薯片")
    ninth = [errand_update("事务0", memo="晚点办"), added]
    _, error, result = _round(engine, "ninth", "pause_activity", _arguments("pause_activity", name, ninth))
    expected = _row((1, "change"), "新增", "ARGUMENTS_INVALID", "This addition would exceed the limit of 8 open errands.")
    assert _host_rows(error) == [expected] and _refused(result) == [expected]
    assert engine.context.state.agents[1].errands == {errand.errand_id: errand for errand in full}
    assert _errand_events(engine) == []
    _next_chance(engine)
    freed = [errand_cancel("事务0", "不办了"), added]
    _, error, result = _round(engine, "freed", "pause_activity", _arguments("pause_activity", name, freed))
    assert error == "" and result.status == "completed"
    assert sorted(engine.context.state.agents[1].errands) == sorted(
        ["action-freed:errand:1", *(errand.errand_id for errand in full[1:])])


def test_different_names_cannot_add_the_same_product_twice(engine_factory) -> None:
    """@brief 同一种商品只允许一条未完成事务，名称不同及请求次序不改变这条合同。"""
    engine, _, name = _waiting(engine_factory)
    for label, names in (("both", ("买一份牛肉面", "晚饭牛肉面")), ("reverse", ("晚饭牛肉面", "买一份牛肉面"))):
        both = [errand_add(item, "牛肉面") for item in names]
        _, error, result = _round(engine, label, "report_status", _arguments("report_status", name, both))
        expected = [_row((1, "item"), "牛肉面", "ARGUMENTS_INVALID", "Two added errands track the same product.")]
        assert _host_rows(error) == expected and _refused(result) == expected
        assert engine.context.state.agents[1].errands == {} and _errand_events(engine) == []
