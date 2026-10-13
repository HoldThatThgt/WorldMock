"""打开 app 只看内容；没有登记内容、只用于下单和查询的 app 不能打开，只列在 prefer_apps 里
（docs/design/04-simulation-engine.md「动作、名称与并行」「出行、内容与查询」）。

夹具环境 2026 年 1 月 5 日 08:00 人在家：哔哩哔哩、抖音、Keep 登记了内容，哔哩哔哩另外直接卖平台笔记本；饿了么、美团
没有登记内容，只被卖东西的店登记，是只用于下单和查询的 app。
"""

from __future__ import annotations

from tests.life.conftest import context, purchase, query, target_actions, tool_targets, travel, use_app
from tests.life.current_world import stage_moment
from tests.life.environment_runtime import act, act_group, events, finish_activity, reach
from tests.life.phone_records import foreground, moment, paying_label, phone_environment, phone_window
from worldmock.life.activity_validation import _CONTENT_FIELDS
from worldmock.life.name_resolution import name_book
from worldmock.life.prompt_schemas import validate_layer
from worldmock.life.result_projection import public_result

#: 夹具里能打开的 app，按 app_id 升序。
APP_TARGETS = ["哔哩哔哩", "抖音", "Keep"]
#: 夹具里可以写进 prefer 的 app，按 app_id 升序。
PREFER_APPS = ["哔哩哔哩", "饿了么", "美团"]
#: 退回明细的 expected，三种动作共用。
ORDER_APP_EXPECTED = ("A target that the written action accepts is required; "
                      "an app that only takes orders is written in prefer.")
#: ACTION_NOT_OFFERED 现有的默认修正句。
OFFERED_FIX = "Use one of the object's offered actions."
NEITHER = " Neither start request was started."


def order_app_why(name: str) -> str:
    """把只用于下单和查询的 app 写进 target 时读到的那一句，名称是目录登记的全名。"""
    return (f"Write {name} in prefer of purchase or query, not in target: "
            "this app only takes orders and has no content to open.")


def order_app_item(name: str, written: str, call: int = 0) -> dict:
    """这条退回的一条完整明细：定位到所写调用的 action，没有允许值和候选。"""
    return {"path": ["tool_calls", call, "function", "arguments", "action"], "written": written,
            "error_code": "ACTION_NOT_OFFERED", "allowed_values": [], "expected": ORDER_APP_EXPECTED,
            "why": order_app_why(name), "candidates": []}


def app_lists(observed: dict) -> tuple[list[str], list[str]]:
    """观察里的两份名单：工具层 kind 为 app 的目标名，与 callable_index.prefer_apps。"""
    targets = [row["name"] for row in observed["tool_layer"]["targets"] if row["kind"] == "app"]
    return targets, observed["tool_layer"]["callable_index"]["prefer_apps"]


def test_opening_an_app_returns_content_only(engine_factory):
    """打开既有内容又卖平台笔记本的哔哩哔哩：受理结果的数据只有活动与订单，公开明细恰为 planned 与 content，
    从存储读回的这条 action_result 正文里也没有 goods。"""
    engine = engine_factory()
    _, result = act(engine, "open", use_app("哔哩哔哩"))
    assert result.status == "in_progress", result.error_code
    assert set(result.data) == {"activities", "orders"}
    details = public_result(result.model_dump(mode="json"), engine.context.inputs)["details"]
    assert set(details) == {"planned", "content"}
    [stored] = [row for row in events(engine, "action_result") if row.action_id == result.action_id]
    assert "goods" not in stored.data and set(stored.data["data"]) == {"activities", "orders"}


def test_app_with_content_and_goods_opens_and_still_sells(engine_factory):
    """哔哩哔哩照常打开、只看内容，占槽位 2，活动数据是带内容的那一组键；它既是 app 目标又在 prefer_apps，名称簿里
    这个名字只有一个 app 对象；用完之后它卖的平台笔记本仍能按名购买，订单的下单 app 是它。"""
    engine = engine_factory()
    observed, result = act(engine, "open", use_app("哔哩哔哩"))
    assert result.status == "in_progress", result.error_code
    activity = engine.context.state.activities[result.activity_id]
    assert activity.resource_claims == {"second"} and activity.data["content_id"] is not None
    assert set(activity.data) == _CONTENT_FIELDS
    assert tool_targets(observed)["哔哩哔哩"]["kind"] == "app"
    assert "哔哩哔哩" in observed["tool_layer"]["callable_index"]["prefer_apps"]
    assert [target.kind for target in name_book(observed).targets if target.label == "哔哩哔哩"] == ["app"]
    finish_activity(engine, result, "open")
    _, bought = act(engine, "notebook", purchase("平台笔记本"))
    assert bought.status == "in_progress", bought.error_code
    assert [(order.goods_id, order.app_id) for order in engine.context.state.deliveries.values()] == [
        ("online-notebook", "bilibili")]


def test_order_only_apps_are_not_targets_and_appear_only_in_prefer_apps(engine_factory):
    """在家与到了公司各观察一次：app 目标恰为哔哩哔哩、抖音、Keep，prefer_apps 是哔哩哔哩、饿了么、美团；私有的
    两份绑定里没有饿了么、美团；工具层通过层校验。"""
    engine = engine_factory()
    home = engine.observe(context(engine, "home"))
    _, trip = act(engine, "away", travel("公司", "驾车"))
    finish_activity(engine, trip, "away")
    assert engine.context.state.agents[1].location_id == "office"
    office = engine.observe(context(engine, "office"))
    for observed in (home, office):
        assert app_lists(observed) == (APP_TARGETS, PREFER_APPS)
        bound = {row["object_id"] for row in [*observed["interaction_bindings"], *observed["catalog_bindings"]]}
        assert not {"eleme", "meituan"} & bound
        validate_layer(observed["tool_layer"])


def test_environment_refuses_opening_an_order_only_app(engine_factory):
    """绕过宿主直接登记「打开美团」：环境用同一份名称解析复核，以 ACTION_REQUEST_INVALID 拒绝，明细恰一条；没有活动，
    停留那一段、余额、内容进度与时刻都与请求前相同，存储里有这条 action_rejected。"""
    engine = engine_factory()
    before = engine.context.state.model_copy(deep=True)
    _, result = act(engine, "open", use_app("美团"))
    assert (result.status, result.error_code) == ("rejected", "ACTION_REQUEST_INVALID")
    assert result.data["reason"] == result.message == order_app_why("美团")
    assert result.data["fix"] == OFFERED_FIX
    assert result.data["items"] == [order_app_item("美团", "interact")]
    after = engine.context.state
    assert after.activities == before.activities == {} and after.sessions == before.sessions
    assert after.accounts == before.accounts and after.current_time == before.current_time
    assert after.agents[1].content_progress == before.agents[1].content_progress
    assert [row.action_id for row in events(engine, "action_rejected")] == [result.action_id]


def test_purchase_naming_an_order_only_app_buys_nothing(engine_factory):
    """一条购买里写了牛肉面又写了美团：整条调用退回，明细恰一条、written 是 purchase；牛肉面也不买，余额不变。"""
    engine = engine_factory()
    balance = engine.context.state.accounts["agent:1"]
    _, result = act(engine, "buy", purchase("牛肉面", "美团"))
    assert (result.status, result.error_code) == ("rejected", "ACTION_REQUEST_INVALID")
    assert result.data["items"] == [order_app_item("美团", "purchase")]
    assert result.data["reason"] == order_app_why("美团")
    assert engine.context.state.deliveries == {} and engine.context.state.activities == {}
    assert engine.context.state.accounts["agent:1"] == balance
    assert not [row for row in events(engine, "environment_event") if row.data.get("kind") == "goods_received"]


def test_query_naming_an_order_only_app_starts_no_query(engine_factory):
    """经副工具查询「美团」、再查询「零食」「美团」，prefer 都是 null：两次都整条退回，明细恰一条、written 是 query，
    不是找不到商品的 SUPPLY_NOT_FOUND；零食那一项也不查。没有查询活动，时刻不动。"""
    engine = engine_factory()
    before, refused = engine.context.state.current_time, []
    for label, request in (("look", query("美团")), ("look-snacks", query("零食", "美团"))):
        _, result = act(engine, label, request)
        refused.append(result.action_id)
        assert (result.status, result.error_code) == ("rejected", "ACTION_REQUEST_INVALID")
        assert result.data["items"] == [order_app_item("美团", "query")]
        assert result.data["reason"] == result.message == order_app_why("美团")
        assert result.data["fix"] == OFFERED_FIX
    state = engine.context.state
    assert state.activities == {} and state.current_time == before
    assert [row.action_id for row in events(engine, "action_rejected")] == refused


def test_departure_with_an_order_only_app_starts_neither(engine_factory):
    """与打车出发同一次回复里「打开饿了么」：整组拒绝，两条动作读到同一份拒绝，明细只有副调用的那一条，原因后面
    追加整组没有开始的一句；没有路上那一段，没有扣车费，没有活动，人仍在家。"""
    engine = engine_factory()
    before = engine.context.state.model_copy(deep=True)
    _, (trip, opened) = act_group(engine, "leave", travel("公司", "打车"), use_app("饿了么"))
    for result in (trip, opened):
        assert (result.status, result.error_code) == ("rejected", "ACTION_REQUEST_INVALID")
        assert result.data["items"] == [order_app_item("饿了么", "interact", 1)]
        assert result.data["reason"] == order_app_why("饿了么") + NEITHER
    after = engine.context.state
    assert after.activities == {} and after.sessions == before.sessions
    assert after.accounts == before.accounts and after.agents[1].location_id == "home"


def test_every_content_app_either_starts_with_content_or_is_refused(engine_factory):
    """目录里每个内容 app 各用一个新引擎打开：登记了内容的开始后内容身份不为空，没有登记内容的被退回，没有第三种结果。"""
    catalog = engine_factory().context.catalog
    outcomes = {}
    for app_id, app in sorted(catalog.apps.items()):
        if app.role != "content":
            continue
        engine = engine_factory()
        _, result = act(engine, f"open-{app_id}", use_app(app.name))
        if catalog.contents_by_app[app_id]:
            assert result.status == "in_progress", result.error_code
            assert engine.context.state.activities[result.activity_id].data["content_id"] is not None
            outcomes[app_id] = "started"
        else:
            assert (result.status, result.data["items"]) == ("rejected", [order_app_item(app.name, "interact")])
            assert engine.context.state.activities == {}
            outcomes[app_id] = "refused"
    assert outcomes == {"bilibili": "started", "douyin": "started", "eleme": "refused", "keep": "started",
                        "meituan": "refused"}


def test_coffee_decision_is_refused_then_bought_with_prefer(engine_factory, tmp_path):
    """规格算例在夹具副本上的确定数字：10 月 12 日 08:46:00 人在家，打开瑞幸咖啡被退回、什么都没变；同一刻经瑞幸
    咖啡购买冰美式，从三杯咖啡下单，09:01:00 送达收货；全程瑞幸咖啡只有购买名下的下单前台，没有 interact 活动。"""
    engine = engine_factory(phone_environment(tmp_path / "input"), mutate=phone_window((8, 0), 2))
    # 运行配置只接受整点开始，所以从 08:00 开始，把世界时刻放到 08:46:00，人仍在家
    start = moment(8, 46)
    stage_moment(engine, start)
    balance = engine.context.state.accounts["agent:1"]
    observed, refused = act(engine, "coffee-open", use_app("瑞幸咖啡"))
    assert app_lists(observed) == (APP_TARGETS, ["哔哩哔哩", "瑞幸咖啡", "美团"])
    assert target_actions(observed, "冰美式")["purchase"]["note"] == "运费5 元，预计10月12日 09:01到达"
    assert (refused.status, refused.data["items"]) == ("rejected", [order_app_item("瑞幸咖啡", "interact")])
    assert refused.data["reason"] == order_app_why("瑞幸咖啡")
    state = engine.context.state
    assert state.activities == {} and state.accounts["agent:1"] == balance and state.current_time == start
    label = paying_label(engine, "coffee-buy", {"luckin": "alipay"})
    _, bought = act(engine, label, purchase("冰美式", prefer="瑞幸咖啡"))
    assert bought.status == "in_progress", bought.error_code
    [order] = engine.context.state.deliveries.values()
    assert (order.goods_id, order.store_id, order.app_id, order.expected_at) == (
        "iced-americano", "cafe", "luckin", moment(9, 1))
    details = public_result(bought.model_dump(mode="json"), engine.context.inputs)["details"]
    assert [row["channel"] for row in details["purchases"]] == ["瑞幸咖啡"]
    reach(engine, moment(9, 1))
    assert engine.context.state.deliveries[order.order_id].received_at == moment(9, 1)
    assert not [item for item in engine.context.state.activities.values()
                if item.kind == "interact" and item.data["app_id"] == "luckin"]
    luckin = [row for row in foreground(engine) if row[0] == "luckin"]
    assert [row[1] for row in luckin] == ["ordering"]
    assert foreground(engine, bought.activity_id)[0] == luckin[0]
