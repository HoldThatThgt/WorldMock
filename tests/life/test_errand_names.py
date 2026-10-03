"""个人事务变更的名称与错误在宿主与环境两侧一致（docs/dev/SPEC-errands-workday.md 2.2、2.3）。

经真实引擎：正式场景与目录建立的 LifeEngine 上，人物开始一段等待；每一轮先由同一次冻结观察（存进 SQLite 的观察事件）
准备的 LifeAgent 在宿主一侧本地校验（validate_react_decisions，与生产的 ReAct 循环同一入口），再把同一请求原样登记给
引擎并结算（begin_decision_round → observe → submit_action → commit_decision_round），即使宿主已经纠错——这正是绕过宿主
的请求。两侧调用同一纯函数 errands.change_issues、读同一份私有事务绑定：更新与取消按待办层名称解析（精确或无损归一后
唯一，从不替换），名称错误的合法值列本人全部未完成事务名；商品名错误只在 why 写最接近的至多三个商品名；新增重名、
同结果同商品与超过八条都是 ARGUMENTS_INVALID；全部不带候选。环境把全部问题合并成一次 ACTION_REQUEST_INVALID（照常计入
同刻轮数），世界不变。
"""

from __future__ import annotations

import random

from agentsociety2.agent.base import ReactDecision, ReactLoopRequest
from agentsociety2.society import DirectToolCall

from custom.agents.life_agent import LifeAgent
from custom.agents.life_feedback import feedback_report
from tests.life.conftest import context, errand_add, errand_cancel, errand_update, stage_errands
from tests.life.test_errand_requests import _constructed, _errand_events, _next_chance, _one, _waiting
from tests.life.test_errand_todo_layer import _errand
from worldmock.life.errand_state import MAX_OPEN_ERRANDS
from worldmock.life.errands import remind_epoch
from worldmock.life.models import REQUEST_MODELS, ActivityStatus

#: 正式目录里的真实商品：食材包、素炒什锦与名称只差一个空格的两种冰红茶（归一后同名）。
_KIT, _MEAL = "ingredient-vegetable-kit", "meal-0030"
_TEA, _TEA_JOINED = "food-6922507005019", "food-6944839902385"
_KIT_ERRAND, _MEAL_ERRAND = "补一份素炒什锦食材包", "晚饭吃素炒什锦"
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
    now, zone = engine.state.current_time, engine.scenario.timezone
    kit = _errand("action-a:errand:0", _KIT_ERRAND, _KIT, now - 120, memo="下班前下单")
    tea = _errand("action-a:errand:1", "囤冰红茶", _TEA, now - 60, quantity=3)
    stage_errands(engine, 1, [kit, tea])
    update = errand_update("补一份 素炒什锦食材包", memo="周末去超市买", remind_time="18:30")
    observed, error, updated = _round(engine, "update", "report_status", _arguments("report_status", name, [update]))
    assert [row["name"] for row in observed["todo_layer"]["errands"]] == [_KIT_ERRAND, "囤冰红茶"]
    assert error == "" and updated.status == "completed"
    assert updated.data["errands"] == [{"errand": _KIT_ERRAND, "change": "更新"}]
    renewed = engine.state.agents[1].errands[kit.errand_id]
    assert renewed == kit.model_copy(update={"memo": "周末去超市买", "remind_at": remind_epoch("18:30", now, zone)})
    _next_chance(engine)
    cancel = errand_cancel(_KIT_ERRAND, "公司楼下买到了")
    observed, error, cancelled = _round(engine, "cancel", "pause_activity", _arguments("pause_activity", name, [cancel]))
    assert observed["todo_layer"]["errands"][0]["memo"] == "周末去超市买"
    assert error == "" and cancelled.data["errands"] == [{"errand": _KIT_ERRAND, "change": "取消"}]
    events = _errand_events(engine)
    assert [event.data["kind"] for event in events] == ["errand_updated", "errand_cancelled"]
    assert (events[1].data["errand"], events[1].data["reason"]) == (renewed.model_dump(mode="json"), "公司楼下买到了")
    assert engine.state.agents[1].errands == {tea.errand_id: tea}
    after = engine.observe(context(engine, "after"))
    assert [row["name"] for row in after["todo_layer"]["errands"]] == ["囤冰红茶"]


def test_unknown_or_ambiguous_errand_name_lists_every_open_name_and_substitutes_nothing(engine_factory) -> None:
    """更新所写的事务名找不到（NAME_NOT_FOUND）、取消所写的事务名归一后对应两条（只差一个空格的两条事务，只能经预置
    状态得到：新增时重名即被拒）为 NAME_AMBIGUOUS：路径到 errands[j].name，合法值按待办层顺序列本人全部未完成事务名，
    没有候选。宿主一次纠错给出两条；绕过宿主的同一请求在环境被合并成一次拒绝、明细逐项相同，从不替换成近似的那一条——
    事务原样（备注没被更新、没有事务被取消），没有事务事件，暂停也没有发生。"""
    engine, wait_id, name = _waiting(engine_factory)
    now = engine.state.current_time
    staged = [_errand("action-a:errand:0", _KIT_ERRAND, _KIT, now - 120),
              _errand("action-a:errand:1", "囤 冰红茶", _TEA, now - 60),
              _errand("action-a:errand:2", "囤冰红茶", _TEA_JOINED, now - 60)]
    stage_errands(engine, 1, staged)
    open_names = [_KIT_ERRAND, "囤 冰红茶", "囤冰红茶"]
    changes = [errand_update("补一份食材包", memo="下班前下单"), errand_cancel("囤　冰红茶", "不想喝了")]
    observed, error, result = _round(engine, "names", "pause_activity", _arguments("pause_activity", name, changes))
    assert [row["name"] for row in observed["errand_bindings"]] == open_names
    expected = [
        _row((0, "name"), "补一份食材包", "NAME_NOT_FOUND", "The errand name 「补一份食材包」 is not known.", open_names),
        _row((1, "name"), "囤　冰红茶", "NAME_AMBIGUOUS", "The errand name 「囤　冰红茶」 matches several open errands.",
             open_names)]
    assert _host_rows(error) == expected and feedback_report(error).error_code == "NAME_NOT_FOUND"
    assert _refused(result) == expected
    assert engine.state.agents[1].errands == {errand.errand_id: errand for errand in staged}
    assert _errand_events(engine) == [] and engine.state.activities[wait_id].status == ActivityStatus.ACTIVE


def test_unknown_item_reports_nearest_product_names_in_why_without_candidates(engine_factory) -> None:
    """新增的商品名找不到时 NAME_NOT_FOUND 的 why 写最接近的至多三个商品名；写成全角空格的「康师傅　冰红茶」归一后对应
    正式目录里的两种冰红茶，NAME_AMBIGUOUS 的 why 列出两者。两者合法值都为空——私有绑定里全部商品名（一百多个）从不列进
    allowed_values——也都没有候选；宿主与环境逐项相同。两种冰红茶各自照目录的精确写法新增都被受理、解析到各自的商品。"""
    engine, _, name = _waiting(engine_factory)
    changes = [errand_add("买牛奶", "牛奶"), errand_add("囤茶", "康师傅　冰红茶")]
    observed, error, result = _round(engine, "items", "conclude_activity",
                                     _arguments("conclude_activity", name, changes))
    assert len(observed["errand_item_bindings"]) > 100
    expected = [
        _row((0, "item"), "牛奶", "NAME_NOT_FOUND",
             "No product is named 「牛奶」. Nearest product names: 「纯牛奶」、「臻浓牛奶」、「高钙牛奶」."),
        _row((1, "item"), "康师傅　冰红茶", "NAME_AMBIGUOUS",
             "The product name 「康师傅　冰红茶」 matches several products: 「康师傅 冰红茶」、「康师傅冰红茶」.")]
    assert _host_rows(error) == expected and _refused(result) == expected
    assert engine.state.agents[1].errands == {} and _errand_events(engine) == []
    exact = [errand_add("一瓶冰红茶", "康师傅 冰红茶"), errand_add("另一瓶冰红茶", "康师傅冰红茶")]
    _, error, accepted = _round(engine, "exact", "report_status", _arguments("report_status", name, exact))
    assert error == "" and accepted.status == "completed"
    held = engine.state.agents[1].errands
    assert [held[f"action-exact:errand:{index}"].product_id for index in (0, 1)] == [_TEA, _TEA_JOINED]


def test_cancel_requires_a_reason(engine_factory) -> None:
    """取消必带原因：原因为 null 时报「reason is required when change is 取消」，只有空白时按非空白格式报，路径都到
    errands[0].reason；宿主按参数错误纠正（ARGUMENTS_INVALID），绕过宿主时环境以 ACTION_REQUEST_INVALID 逐项给出同样的
    明细，事务原样留着。写了原因的取消被受理，取消事件带这条原因。"""
    engine, _, name = _waiting(engine_factory)
    kit = _errand("action-a:errand:0", _KIT_ERRAND, _KIT, engine.state.current_time - 120)
    stage_errands(engine, 1, [kit])
    whys = []
    for index, reason in enumerate((None, "  ")):
        changes = [{**errand_cancel(_KIT_ERRAND, "占位"), "reason": reason}]
        arguments = _arguments("pause_activity", name, changes)
        _, error, result = _round(engine, f"bare-{index}", "pause_activity", arguments,
                                  _constructed("pause_activity", name, changes))
        [host] = _host_rows(error)
        assert feedback_report(error).error_code == "ARGUMENTS_INVALID"
        assert host[:4] == ([*_ROOT, 0, "reason"], reason, "ARGUMENTS_INVALID", []) and host[5] == []
        assert _refused(result) == [(host[0], reason, "ACTION_REQUEST_INVALID", *host[3:])]
        assert engine.state.agents[1].errands == {kit.errand_id: kit} and _errand_events(engine) == []
        whys.append(host[4])
    assert whys[0] == "reason is required when change is 取消" and whys[1] != whys[0]
    _, error, result = _round(engine, "reasoned", "pause_activity",
                              _arguments("pause_activity", name, [errand_cancel(_KIT_ERRAND, "已经买到了")]))
    assert error == "" and result.status == "completed" and engine.state.agents[1].errands == {}
    [event] = _errand_events(engine)
    assert (event.data["kind"], event.data["reason"]) == ("errand_cancelled", "已经买到了")


def test_duplicate_added_names_same_outcome_and_product_and_a_ninth_errand_are_rejected(engine_factory) -> None:
    """新增的三种状态相关冲突都是 ARGUMENTS_INVALID、why 写明冲突的事务名或上限，宿主与环境逐项相同：结果种类与商品都与
    一条未完成事务相同（errands[j].item）；名称与一条未完成事务归一后相同、与同请求较早一条新增归一后相同
    （errands[j].name，只报较晚的一条）；已有八条未完成事务时再新增（errands[j].change，上限只写在 why）。"""
    engine, _, name = _waiting(engine_factory)
    now = engine.state.current_time
    stage_errands(engine, 1, [_errand("action-a:errand:0", _KIT_ERRAND, _KIT, now - 120)])
    changes = [errand_add("再买一份食材包", "素炒什锦食材包"),
               errand_add("补一份 素炒什锦食材包", "素炒什锦", outcome="吃掉"),
               errand_add("囤茶", "康师傅 冰红茶"), errand_add("囤 茶", "康师傅冰红茶")]
    _, error, result = _round(engine, "conflicts", "report_status", _arguments("report_status", name, changes))
    expected = [
        _row((0, "item"), "素炒什锦食材包", "ARGUMENTS_INVALID",
             f"The open errand 「{_KIT_ERRAND}」 already tracks this outcome and product."),
        _row((1, "name"), "补一份 素炒什锦食材包", "ARGUMENTS_INVALID", f"An open errand is already named 「{_KIT_ERRAND}」."),
        _row((3, "name"), "囤 茶", "ARGUMENTS_INVALID", "Two added errands share the name 「囤茶」.")]
    assert _host_rows(error) == expected and feedback_report(error).error_code == "ARGUMENTS_INVALID"
    assert _refused(result) == expected
    products = [product for product in sorted(engine.rules.items) if product not in {_KIT, _MEAL, _TEA, _TEA_JOINED}]
    full = [_errand(f"action-s:errand:{index}", f"事务{index}", product, now - 600)
            for index, product in enumerate(products[:MAX_OPEN_ERRANDS])]
    stage_errands(engine, 1, full)
    _, error, result = _round(engine, "ninth", "conclude_activity",
                              _arguments("conclude_activity", name, [errand_add(_KIT_ERRAND, "素炒什锦食材包")]))
    expected = [_row((0, "change"), "新增", "ARGUMENTS_INVALID", "This addition would exceed the limit of 8 open errands.")]
    assert _host_rows(error) == expected and _refused(result) == expected
    assert feedback_report(error).payload()["items"][0]["expected"] == (
        "An addition that the open errands can still take is required.")
    assert engine.state.agents[1].errands == {errand.errand_id: errand for errand in full}
    assert _errand_events(engine) == []


def test_cancelling_and_adding_again_with_the_original_name_and_a_new_quantity_is_accepted(engine_factory) -> None:
    """完成依据记下后不可改：同一请求取消旧事务并以原名、同一商品、新数量新增，按「取消、更新、新增」的次序求值，宿主与
    环境都受理；旧事务移出（取消事件带它移出前的累计量），新事务以「<动作身份>:errand:<条目下标>」为身份、零累计量记下，
    回执与事件按请求顺序。终结之后名称可以再用：随后取消这条新事务，后来的一次请求再以原名新增照样受理；完成也把事务
    移出未完成集合（2.1），以完成之后留下的状态（只剩别的事务）预置，原名同样可以新增。"""
    engine, _, name = _waiting(engine_factory)
    now = engine.state.current_time
    old = _errand("action-a:errand:0", _KIT_ERRAND, _KIT, now - 120, quantity=1, progress=0.5)
    stage_errands(engine, 1, [old])
    changes = [errand_cancel(_KIT_ERRAND, "改成两份"), errand_add(_KIT_ERRAND, "素炒什锦食材包", quantity=2, memo="多买一份")]
    _, error, result = _round(engine, "again", "report_status", _arguments("report_status", name, changes))
    assert error == "" and result.status == "completed"
    assert result.data["errands"] == [{"errand": _KIT_ERRAND, "change": "取消"}, {"errand": _KIT_ERRAND, "change": "新增"}]
    [renewed] = engine.state.agents[1].errands.values()
    assert (renewed.errand_id, renewed.name, renewed.quantity, renewed.progress, renewed.memo) == (
        "action-again:errand:1", _KIT_ERRAND, 2, 0.0, "多买一份")
    cancelled, registered = _errand_events(engine)
    assert (cancelled.data["kind"], cancelled.data["errand"]) == ("errand_cancelled", old.model_dump(mode="json"))
    assert registered.data == {"kind": "errand_registered", "errand": renewed.model_dump(mode="json")}
    _next_chance(engine)
    _, error, result = _round(engine, "drop", "pause_activity",
                              _arguments("pause_activity", name, [errand_cancel(_KIT_ERRAND, "不买了")]))
    assert error == "" and result.status == "completed" and engine.state.agents[1].errands == {}
    reused = [errand_add(_KIT_ERRAND, "素炒什锦食材包")]
    _, error, result = _round(engine, "reuse", "report_status", _arguments("report_status", name, reused))
    assert error == "" and result.data["errands"] == [{"errand": _KIT_ERRAND, "change": "新增"}]
    _next_chance(engine)
    meal = _errand("action-b:errand:0", _MEAL_ERRAND, _MEAL, now, outcome="eat")
    stage_errands(engine, 1, [meal])
    _, error, result = _round(engine, "after-completion", "report_status", _arguments("report_status", name, reused))
    assert error == "" and result.status == "completed"
    assert sorted(engine.state.agents[1].errands) == ["action-after-completion:errand:0", meal.errand_id]


def test_a_request_bypassing_the_host_is_refused_once_and_the_world_is_unchanged(engine_factory) -> None:
    """绕过宿主的请求（宿主已在本轮纠错，请求仍原样到达环境）：三个处理器在活动名解析之后用同一函数复核，全部事务问题
    合并成一次 ACTION_REQUEST_INVALID——一条拒绝结果、一个 action_rejected，原因逐条以「; 」相连，明细按（条目下标, 键）
    排列、路径到 errands[j].<键>，与宿主那一次纠错的明细逐项相同。它不是免计的名称类拒绝：照常计入同刻轮数，人物在同一秒
    再得到一次机会。世界不变：事务、等待活动（没有被播报）与资源都原样，没有事务事件。"""
    engine, wait_id, name = _waiting(engine_factory)
    now = engine.state.current_time
    kit = _errand("action-a:errand:0", _KIT_ERRAND, _KIT, now - 120)
    stage_errands(engine, 1, [kit])
    before, rounds = engine.state.activities[wait_id], engine.state.agents[1].same_moment_rounds
    changes = [errand_add("囤茶", "康师傅 冰红茶"), errand_update("补一份食材包", memo="晚点"),
               errand_add("买牛奶", "钢琴"), errand_add("囤 茶", "康师傅冰红茶")]
    _, error, result = _round(engine, "bypass", "report_status", _arguments("report_status", name, changes))
    expected = [
        _row((1, "name"), "补一份食材包", "NAME_NOT_FOUND", "The errand name 「补一份食材包」 is not known.", [_KIT_ERRAND]),
        _row((2, "item"), "钢琴", "NAME_NOT_FOUND", "No product is named 「钢琴」."),
        _row((3, "name"), "囤 茶", "ARGUMENTS_INVALID", "Two added errands share the name 「囤茶」.")]
    assert _host_rows(error) == expected and _refused(result) == expected
    assert result.message == "; ".join(row[4] for row in expected)
    assert [event.event_type for event in engine.store.read_agent_events("action_rejected", 1)
            if event.action_id == "action-bypass"] == ["action_rejected"]
    assert _one(engine, "action_result", "action-bypass").data["error_code"] == "ACTION_REQUEST_INVALID"
    assert result.data["same_moment_round"] == engine.state.agents[1].same_moment_rounds == rounds + 1
    assert engine.state.agents[1].next_decision_at == now and 1 in engine.scheduling_state().ready_agent_ids
    assert engine.state.agents[1].errands == {kit.errand_id: kit} and engine.state.activities[wait_id] == before
    assert _errand_events(engine) == []


#: 性质测试的词表：新增的名称、更新与取消点名的写法（含无损归一的变体、全角空格的歧义写法与不存在的名称）、商品名
#: （含找不到的、有歧义的）、两种结果、备注与提醒钟点。
_ADD_NAMES = ("补一份食材包", "买牛奶", _MEAL_ERRAND, "囤冰红茶", "囤 冰红茶", "事务0", "囤茶")
_TARGETS = (_KIT_ERRAND, "补一份 素炒什锦食材包", "囤 冰红茶", "囤冰红茶", "囤　冰红茶", _MEAL_ERRAND, "事务0", "事务 1",
            "不存在的事务")
_ITEMS = ("素炒什锦食材包", "素炒什锦", "康师傅 冰红茶", "康师傅冰红茶", "康师傅　冰红茶", "牛奶", "纯牛奶")
_PROPERTY_ROUNDS = 60


def _states(engine) -> dict[str, list]:
    """性质测试依次预置的四种未完成事务：没有；三条（取得食材包、取得冰红茶、吃掉素炒什锦）；名称只差一个空格的两条；
    已满八条。"""
    now = engine.state.current_time
    kit = _errand("action-p:errand:0", _KIT_ERRAND, _KIT, now - 300)
    tea = _errand("action-p:errand:1", "囤 冰红茶", _TEA, now - 200)
    products = [product for product in sorted(engine.rules.items) if product not in {_KIT, _MEAL, _TEA, _TEA_JOINED}]
    return {
        "none": [],
        "three": [kit, tea, _errand("action-p:errand:2", _MEAL_ERRAND, _MEAL, now - 100, outcome="eat", quantity=2)],
        "twins": [kit, tea, _errand("action-p:errand:2", "囤冰红茶", _TEA_JOINED, now - 100)],
        "full": [_errand(f"action-f:errand:{index}", f"事务{index}", product, now - 600)
                 for index, product in enumerate(products[:MAX_OPEN_ERRANDS])],
    }


def _random_changes(rng: random.Random) -> list[dict]:
    """从固定词表确定性地抽一组结构合法的变更（零至四条，新增占多数）。"""
    changes = []
    for _ in range(rng.choice((0, 1, 1, 2, 2, 3, 4))):
        kind = rng.choice(("新增", "新增", "更新", "取消"))
        if kind == "新增":
            changes.append(errand_add(rng.choice(_ADD_NAMES), rng.choice(_ITEMS), outcome=rng.choice(("取得", "吃掉")),
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
    return engine._resolve(engine.state.model_copy(deep=True), probe)[1]


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
        real = engine._round.pending_actions[0]
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
