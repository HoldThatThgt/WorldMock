"""开始调用的名称解析：五种结果的码、路径与文字，候选是新工具的完整调用，睡眠只读分钟数。"""

from __future__ import annotations

import pytest

from tests.life.conftest import MAIN_TOOL, SIDE_TOOL, context, do_at, purchase, sleep, travel, use_app
from worldmock.life.action_contract import binding_action
from worldmock.life.models import StartSideActivityRequest
from worldmock.life.name_resolution import ORDER_APP_KIND, NameBook, Option, Target, name_book, resolve_start

_OBJECT_EXPECTED = "A known object name in the selected action's category is required."
_DETAIL_EXPECTED = "A detail name listed for this object and action is required."
_ORDER_APP_EXPECTED = ("A target that the written action accepts is required; "
                       "an app that only takes orders is written in prefer.")


@pytest.fixture
def book(engine_factory) -> NameBook:
    """夹具环境运行开始时（人在家）真实冻结观察上的名称簿。"""
    engine = engine_factory()
    return name_book(engine.observe(context(engine, "names")))


def _option(action: str, detail: str | None = None, blocked: str | None = None, duration: str | None = None,
            aliases: tuple[str, ...] = ()) -> Option:
    """手写的一条动作行（用于夹具里造不出的重名情形）。"""
    return Option(action, detail, blocked, duration, aliases, {**binding_action(action, detail), "note": blocked})


def _target(label: str, kind: str, *options: Option) -> Target:
    """手写的一个对象及其动作行。"""
    row = {"object": label, "kind": kind, "object_id": f"id-{label}", "actions": [dict(item.row) for item in options]}
    return Target(label, kind, f"id-{label}", options, True, row)


def _order_app_why(name: str) -> str:
    """把只用于下单和查询的 app 写进 target 时读到的那一句，名称是目录登记的全名。"""
    return (f"Write {name} in prefer of purchase or query, not in target: "
            "this app only takes orders and has no content to open.")


def _order_app_item(name: str, written: str) -> dict:
    """这条退回的完整明细（未绑定调用下标时定位在第 0 条调用）。"""
    return {"path": ["tool_calls", 0, "function", "arguments", "action"], "written": written,
            "error_code": "ACTION_NOT_OFFERED", "allowed_values": [], "expected": _ORDER_APP_EXPECTED,
            "why": _order_app_why(name), "candidates": []}


def _single(outcome) -> dict:
    """唯一一条名称失败的完整明细。"""
    assert outcome.rows == () and len(outcome.failures) == 1
    failure = outcome.failures[0]
    assert failure.sentence == failure.detail == failure.item.why
    return failure.item.payload()


def test_resolved_main_call_selects_the_named_row_of_the_object(book):
    """出行解析到目的地的那条出行方式行，在地点做事解析到当前地点的那个单元行。"""
    outcome = resolve_start(book, MAIN_TOOL, travel("公司", "驾车"))
    row = outcome.rows[0]
    assert outcome.failures == () and outcome.resolved == ()
    assert (row["object"], row["kind"], row["object_id"]) == ("公司", "location", "office")
    assert row["selected_action"]["detail"] == "驾车" and row["selected_action"]["travel_method_id"] == "driving"
    unit = resolve_start(book, MAIN_TOOL, do_at("家", "测试休息")).rows[0]
    assert unit["object_id"] == "home" and unit["selected_action"]["content_ids"] == ["c-rest"]
    assert unit["selected_action"]["episode_titles"] == []


def test_side_call_expands_one_item_per_array_element_and_keeps_repeats(book):
    """副工具的 target 每个元素一项，购买的重复元素各自成项；未展示的名称交给指名商品入口。"""
    outcome = resolve_start(book, SIDE_TOOL, purchase("牛肉面", "柠檬茶", "柠檬茶", "平台笔记本"))
    assert outcome.failures == ()
    assert [row["object"] for row in outcome.rows] == ["牛肉面", "柠檬茶", "柠檬茶", "平台笔记本"]
    assert [row["object_id"] for row in outcome.rows] == ["beef-noodles", "lemon-tea", "lemon-tea", None]
    assert outcome.rows[3]["kind"] == "named_supply"
    app = resolve_start(book, SIDE_TOOL, use_app("哔哩哔哩")).rows
    assert [(row["object_id"], row["kind"]) for row in app] == [("bilibili", "app")]


def test_unknown_object_reports_name_not_found_with_complete_call_candidates(book):
    """对象名找不到：主工具定位 target，副工具定位数组里的那一项；候选是近名对象可以照抄的完整调用。"""
    main = _single(resolve_start(book, MAIN_TOOL, travel("公司大楼", "驾车")))
    assert main["error_code"] == "NAME_NOT_FOUND" and main["path"][-1:] == ["target"]
    assert main["written"] == "公司大楼" and main["allowed_values"] == [] and main["expected"] == _OBJECT_EXPECTED
    assert main["why"] == ("No known object named 公司大楼 supports this action; "
                           "changing spelling cannot create an object.")
    assert main["candidates"] == [{"tool": MAIN_TOOL, "note": "约 5 分钟",
                                   "arguments": {"action": "travel", "target": "公司", "detail": "驾车"}}]
    side = _single(resolve_start(book, SIDE_TOOL, use_app("哔哩")))
    assert side["error_code"] == "NAME_NOT_FOUND" and side["path"][-2:] == ["target", 0]
    assert [item["arguments"] for item in side["candidates"]] == [
        {"action": "interact", "target": ["哔哩哔哩"], "prefer": None}]
    assert all(item["tool"] == SIDE_TOOL for item in side["candidates"])


def test_ambiguous_object_lists_each_match_as_a_candidate():
    """归一后对应多个对象：NAME_AMBIGUOUS，候选是各匹配对象；购买候选只替换数组里出错的那个元素，prefer 照人物所写
    的原样带上，包括认不出的写法和 null。"""
    walk = _option("travel", "步行", duration="约 9 分钟")
    places = NameBook((_target("三杯 咖啡", "location", walk), _target("三杯  咖啡", "location", walk)), "家", "家")
    item = _single(resolve_start(places, MAIN_TOOL, travel("三杯咖啡", "步行")))
    assert item["error_code"] == "NAME_AMBIGUOUS" and item["path"][-1:] == ["target"]
    assert item["expected"] == "One unambiguous complete object name is required."
    assert item["why"] == "The name 三杯咖啡 identifies several known objects."
    assert [candidate["arguments"]["target"] for candidate in item["candidates"]] == ["三杯 咖啡", "三杯  咖啡"]
    buy = _option("purchase")
    goods = NameBook((_target("柠檬 茶", "supply", buy), _target("柠檬  茶", "supply", buy)), "家", "家")
    for prefer in ("饿了么", "饿 了 么", "美团外卖", None):
        item = _single(resolve_start(goods, SIDE_TOOL, purchase("牛肉面", "柠檬茶", "薯片", prefer=prefer)))
        assert item["error_code"] == "NAME_AMBIGUOUS" and item["path"][-2:] == ["target", 1]
        assert [candidate["arguments"] for candidate in item["candidates"]] == [
            {"action": "purchase", "target": ["牛肉面", "柠檬 茶", "薯片"], "prefer": prefer},
            {"action": "purchase", "target": ["牛肉面", "柠檬  茶", "薯片"], "prefer": prefer}]
        assert all(candidate["tool"] == SIDE_TOOL for candidate in item["candidates"])


def test_interact_at_another_location_only_yields_action_not_offered(book):
    """对不是当前地点的地点写 interact：该地点此刻只提供出行，候选是去那里的完整出行调用。"""
    item = _single(resolve_start(book, MAIN_TOOL, do_at("公司", "季度销售数据整理")))
    assert item["error_code"] == "ACTION_NOT_OFFERED" and item["path"][-1:] == ["action"]
    assert item["written"] == "interact" and item["allowed_values"] == ["travel"]
    assert item["expected"] == "An action offered by this object is required."
    assert item["why"] == "The known object 公司 does not support the written action."
    assert [candidate["arguments"] for candidate in item["candidates"]] == [
        {"action": "travel", "target": "公司", "detail": detail} for detail in ("步行", "公共交通", "驾车")]
    assert all(candidate["tool"] == MAIN_TOOL for candidate in item["candidates"])


def test_travel_to_the_current_location_only_yields_action_not_offered(book):
    """把当前地点写成出行目的地：当前地点的行没有出行；睡眠行不成候选，因为引擎不替人物定时长。"""
    item = _single(resolve_start(book, MAIN_TOOL, travel("家", "步行")))
    assert item["error_code"] == "ACTION_NOT_OFFERED" and item["allowed_values"] == ["interact", "sleep"]
    assert [candidate["arguments"] for candidate in item["candidates"]] == [
        {"action": "interact", "target": "家", "detail": "测试闲聊"},
        {"action": "interact", "target": "家", "detail": "测试休息"}]
    assert [candidate["note"] for candidate in item["candidates"]] == ["约 10 分钟", "约 15 分钟"]


def test_object_of_the_other_tool_offers_nothing_here_and_points_to_its_own_tool(book):
    """地点写进副工具、内容 app 写进购买：本工具下允许的动作可以为空，候选属于另一个工具或另一个动作。"""
    place = _single(resolve_start(book, SIDE_TOOL, use_app("家")))
    assert place["error_code"] == "ACTION_NOT_OFFERED" and place["allowed_values"] == []
    assert {candidate["tool"] for candidate in place["candidates"]} == {MAIN_TOOL}
    app = _single(resolve_start(book, SIDE_TOOL, purchase("牛肉面", "哔哩哔哩")))
    assert app["error_code"] == "ACTION_NOT_OFFERED" and app["allowed_values"] == ["interact"]
    assert app["candidates"][0]["arguments"] == {"action": "interact", "target": ["哔哩哔哩"], "prefer": None}


def test_side_tool_candidates_always_carry_prefer(book):
    """副工具的候选恒为 action、target、prefer 三个键并能通过请求模型：购买候选照抄被退回的购买所写的 prefer；
    购买写了 app 名、打开 app 写了商品名或近名、主工具写了商品名，候选的 prefer 都是 null。"""
    cases = [
        (SIDE_TOOL, purchase("牛肉面", "哔哩哔哩", prefer="饿了么"),
         [{"action": "interact", "target": ["哔哩哔哩"], "prefer": None}]),
        (SIDE_TOOL, use_app("柠檬茶"), [{"action": "purchase", "target": ["柠檬茶"], "prefer": None}]),
        (SIDE_TOOL, use_app("哔哩"), [{"action": "interact", "target": ["哔哩哔哩"], "prefer": None}]),
        (MAIN_TOOL, do_at("柠檬茶", "测试休息"), [{"action": "purchase", "target": ["柠檬茶"], "prefer": None}]),
    ]
    for tool, request, expected in cases:
        item = _single(resolve_start(book, tool, request))
        side = [candidate["arguments"] for candidate in item["candidates"] if candidate["tool"] == SIDE_TOOL]
        assert side == expected, request
    buy = _option("purchase")
    goods = NameBook((_target("柠檬 茶", "supply", buy), _target("柠檬  茶", "supply", buy)), "家", "家")
    item = _single(resolve_start(goods, SIDE_TOOL, purchase("柠檬茶", prefer="不存在的 app")))
    candidates = [candidate["arguments"] for candidate in item["candidates"]]
    assert [arguments["prefer"] for arguments in candidates] == ["不存在的 app", "不存在的 app"]
    for arguments in candidates + [row for _, _, rows in cases for row in rows]:
        assert list(arguments) == ["action", "target", "prefer"]
        assert StartSideActivityRequest.model_validate(arguments).model_dump(mode="json") == arguments


def test_unknown_detail_lists_every_row_name_and_ranks_near_rows(book):
    """detail 找不到：allowed_values 是该对象该动作全部行的 detail（按行序），顶层原因等于明细的 why。"""
    unit = _single(resolve_start(book, MAIN_TOOL, do_at("家", "测试睡觉")))
    assert unit["error_code"] == "NAME_NOT_FOUND" and unit["path"][-1:] == ["detail"]
    assert unit["written"] == "测试睡觉" and unit["allowed_values"] == ["测试闲聊", "测试休息"]
    assert unit["expected"] == _DETAIL_EXPECTED
    assert unit["why"] == "家 offers no interact option named 测试睡觉; changing spelling cannot create one."
    assert sorted(candidate["arguments"]["detail"] for candidate in unit["candidates"]) == ["测试休息", "测试闲聊"]
    method = _single(resolve_start(book, MAIN_TOOL, travel("公司", "骑车")))
    assert method["allowed_values"] == ["步行", "公共交通", "驾车", "打车"] and method["candidates"] == []
    assert method["why"] == "公司 offers no travel option named 骑车; changing spelling cannot create one."


def test_episode_title_is_not_a_name_and_ranks_its_series_first():
    """写了某一集的标题：不受理；所属系列凭各集标题排在候选最前，别的单元不进候选。"""
    series = _option("interact", "工作日晚饭", duration="约 20 分钟", aliases=("备菜", "吃饭", "洗碗"))
    home = _target("家", "location", series, _option("interact", "洗漱"), _option("interact", "睡前准备"))
    item = _single(resolve_start(NameBook((home,), "家", "家"), MAIN_TOOL, do_at("家", "备菜")))
    assert item["error_code"] == "NAME_NOT_FOUND" and item["path"][-1:] == ["detail"]
    assert item["allowed_values"] == ["工作日晚饭", "洗漱", "睡前准备"]
    assert item["why"] == "家 offers no interact option named 备菜; changing spelling cannot create one."
    assert item["candidates"] == [{"tool": MAIN_TOOL, "note": "约 20 分钟",
                                   "arguments": {"action": "interact", "target": "家", "detail": "工作日晚饭"}}]


def test_ambiguous_detail_lists_the_matching_rows():
    """归一后对应多个单元：NAME_AMBIGUOUS，allowed_values 仍是全部单元名，候选是这些单元。"""
    home = _target("家", "location", _option("interact", "晚 饭"), _option("interact", "晚  饭"),
                   _option("interact", "洗漱"))
    item = _single(resolve_start(NameBook((home,), "家", "家"), MAIN_TOOL, do_at("家", "晚饭")))
    assert item["error_code"] == "NAME_AMBIGUOUS" and item["path"][-1:] == ["detail"]
    assert item["allowed_values"] == ["晚 饭", "晚  饭", "洗漱"]
    assert item["expected"] == "One unambiguous complete detail name is required."
    assert item["why"] == "The name 晚饭 identifies several options of 家."
    assert [candidate["arguments"]["detail"] for candidate in item["candidates"]] == ["晚 饭", "晚  饭"]


def test_lossless_normalization_is_recorded_for_object_and_detail(book):
    """全半角与空白的无损归一受理，并把所写与解析结果各记一条；「住处」指向本人住处。"""
    outcome = resolve_start(book, MAIN_TOOL, travel("公 司", "驾 车"))
    assert outcome.failures == () and outcome.rows[0]["selected_action"]["detail"] == "驾车"
    assert outcome.resolved == ({"written": "公 司", "as": "公司"}, {"written": "驾 车", "as": "驾车"})
    home = resolve_start(book, MAIN_TOOL, do_at("住处", "测试休息"))
    assert home.failures == () and home.resolved == ({"written": "住处", "as": "家"},)
    goods = resolve_start(book, SIDE_TOOL, purchase("牛 肉 面"))
    assert goods.resolved == ({"written": "牛 肉 面", "as": "牛肉面"},)


@pytest.mark.parametrize("written,minutes,record", [
    ("470", 470, ()), ("４７０", 470, ({"written": "４７０", "as": "470"},)), ("060", 60, ({"written": "060", "as": "60"},)),
])
def test_sleep_resolves_without_any_binding_row(written, minutes, record):
    """睡眠不查名称：没有 sleep 行的地点、甚至一个对象都没有时，分钟数合格就成立，能不能睡由受理判定。"""
    outcome = resolve_start(NameBook((), None, None), MAIN_TOOL, sleep(written))
    assert outcome.failures == () and outcome.resolved == record
    assert [row["minutes"] for row in outcome.rows] == [minutes]
    assert outcome.rows[0]["object_id"] is None and outcome.rows[0]["kind"] == "sleep"


def test_order_only_app_in_target_is_refused_with_the_prefer_sentence(book):
    """真实观察的名称簿：打开美团、购买牛肉面和美团各得一条失败，定位到 action，written 是各自的动作，没有允许值和
    候选；名称簿把 prefer_apps 里还不能打开的饿了么、美团登记成 order_app 对象排在最后。购买美团和饿了么得两条，
    按数组先后。"""
    orders = [target for target in book.targets if target.kind == ORDER_APP_KIND]
    assert [(target.label, target.object_id, target.options, target.listed, target.row) for target in orders] == [
        ("饿了么", None, (), False, None), ("美团", None, (), False, None)]
    assert book.targets[-2:] == tuple(orders)
    assert _single(resolve_start(book, SIDE_TOOL, use_app("美团"))) == _order_app_item("美团", "interact")
    assert _single(resolve_start(book, SIDE_TOOL, purchase("牛肉面", "美团"))) == _order_app_item("美团", "purchase")
    outcome = resolve_start(book, SIDE_TOOL, purchase("美团", "饿了么"))
    assert outcome.rows == ()
    assert [failure.item.payload() for failure in outcome.failures] == [
        _order_app_item("美团", "purchase"), _order_app_item("饿了么", "purchase")]


@pytest.mark.parametrize("written", ["美 团", "美　团"])
def test_normalised_order_only_app_name_is_refused_in_every_side_action(book, written):
    """规整后才对上的写法（多一个半角或全角空格）与一字不差的写法结果相同，句子里写的是登记的全名「美团」。"""
    assert _single(resolve_start(book, SIDE_TOOL, use_app(written))) == _order_app_item("美团", "interact")
    assert _single(resolve_start(book, SIDE_TOOL, purchase(written))) == _order_app_item("美团", "purchase")


def test_near_miss_and_function_apps_keep_name_not_found(book):
    """打开「美团外卖」只是找不到，候选里没有只用于下单和查询的 app；功能 app 不在名称簿里，打开它们找不到、没有候选。"""
    near = _single(resolve_start(book, SIDE_TOOL, use_app("美团外卖")))
    assert near["error_code"] == "NAME_NOT_FOUND"
    assert not {"美团", "饿了么"} & {name for row in near["candidates"] for name in row["arguments"]["target"]}
    for name in ("支付宝", "高德地图"):
        item = _single(resolve_start(book, SIDE_TOOL, use_app(name)))
        assert (item["error_code"], item["candidates"]) == ("NAME_NOT_FOUND", [])
    assert not {"支付宝", "高德地图", "微信"} & {target.label for target in book.targets}


def test_main_tool_naming_an_order_only_app_gets_the_same_refusal(book):
    """出行的 target 一字不差写了美团：与副工具同一条退回，written 是 travel；主工具规整时只认地点，写「美 团」找不到。"""
    assert _single(resolve_start(book, MAIN_TOOL, travel("美团", "步行"))) == _order_app_item("美团", "travel")
    spaced = _single(resolve_start(book, MAIN_TOOL, travel("美 团", "步行")))
    assert (spaced["error_code"], spaced["path"][-1:]) == ("NAME_NOT_FOUND", ["target"])


def test_goods_sharing_a_name_with_an_order_only_app_is_ambiguous_while_listed():
    """手写的名称簿里一件展示中的商品与一个 order_app 对象同名：购买和打开都对上两个对象，得到 NAME_AMBIGUOUS，候选
    恰一条，是购买那件商品；去掉那件商品后两种动作都得到这条退回。"""
    order_app = Target("美团", ORDER_APP_KIND, None, (), False)
    shared = NameBook((_target("美团", "supply", _option("purchase")), order_app), "家", "家")
    for request in (purchase("美团"), use_app("美团")):
        item = _single(resolve_start(shared, SIDE_TOOL, request))
        assert item["error_code"] == "NAME_AMBIGUOUS"
        assert [(row["tool"], row["arguments"]) for row in item["candidates"]] == [
            (SIDE_TOOL, {"action": "purchase", "target": ["美团"], "prefer": None})]
    alone = NameBook((order_app,), "家", "家")
    assert _single(resolve_start(alone, SIDE_TOOL, purchase("美团"))) == _order_app_item("美团", "purchase")
    assert _single(resolve_start(alone, SIDE_TOOL, use_app("美团"))) == _order_app_item("美团", "interact")
