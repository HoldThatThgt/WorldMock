"""查询的答案：按做完那一刻的世界算，每家店一行，到店、外卖、快递与六种不可下单的原因，限定意向 app 时怎样筛，
次序与 20 件上限，机器形式与人物读到的 details.query，留多久，答完这次决策就能买（docs/design/04-simulation-engine.md
「出行、内容与查询」）。全部经真实请求、引擎与 SQLite。

夹具环境 2026 年 1 月 5 日（周一）08:00 人在家：一心面馆（2,524 米）只登记了美团，三杯咖啡（4,729 米）登记了饿了么
和美团，四季便利与二味小炒没有登记 app；从公司到一心面馆 991 米。
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from tests.life.conftest import (
    SIDE_TOOL,
    action_identity,
    advance_next,
    advance_until_ready,
    conclude_pending,
    context,
    do_at,
    progress_calls,
    purchase,
    query,
    travel,
)
from tests.life.environment_inputs import catalog_rows, copy_environment, write_catalog_rows
from tests.life.environment_runtime import act, act_group, events, settle_decision
from worldmock.foundation.randomness import random_stream

ZONE = ZoneInfo("Asia/Shanghai")
APP_NAMES = {"bilibili": "哔哩哔哩", "eleme": "饿了么", "meituan": "美团"}
NO_APP = "这家店没有上任何 app，要到店里买"
#: 机器形式里不可下单的六个原因码。
REASON_CODES = {"location_closed", "present_at_seller", "store_without_app", "no_open_store",
                "no_open_store_on_app", "not_sold_on_app"}


def _at(location):
    """人物从给定地点开始。"""
    def mutate(persona, config):
        persona["initial_location"] = location
    return mutate


def _edited(tmp_path, name, change, *files):
    """夹具副本：给定目录文件的每一行交给 change 改写（就地修改），改完按生产入口刷新清单。"""
    root = copy_environment(tmp_path / name)
    for relative in files:
        rows = catalog_rows(root, relative)
        for row in rows:
            change(row)
        write_catalog_rows(root, relative, rows)
    return root


def _hours(hours: dict):
    """改写函数：给定地点在 1 月 5 日（周一）只在给定时段营业。"""
    def change(row):
        if row.get("location_id") in hours:
            row["opens"] = {"weekly": {"mon": [list(hours[row["location_id"]])]}}
    return change


def _closed(*location_ids):
    """改写函数：给定地点只在周二营业，1 月 5 日周一整天打烊。"""
    def change(row):
        if row.get("location_id") in location_ids:
            row["opens"] = {"weekly": {"tue": [["00:00", "24:00"]]}}
    return change


def _finish(engine, activity_id):
    """逐边界推进到这项活动不再进行；途中人物有决策机会时照实播报。"""
    while engine.context.state.activities[activity_id].status == "active":
        if engine.scheduling_state().ready_agent_ids:
            settle_decision(engine, f"report-{engine.context.state.current_time}")
        else:
            advance_next(engine)
    return engine.context.state.activities[activity_id]


def _asked(engine, label, *targets, prefer=None):
    """提交一次查询并推进到它做完，返回做完的活动与人物读到的 details.query。"""
    _, result = act(engine, label, query(*targets, prefer=prefer))
    assert result.status == "in_progress", (result.error_code, result.message)
    done = _finish(engine, result.activity_id)
    assert done.status == "completed"
    observed = engine.observe(context(engine, f"{label}-read"))
    result = progress_calls(observed, SIDE_TOOL)[-1]["result"]
    assert result["status"] == "completed" and "query" in result["details"], result
    return done, result["details"]["query"]


def _iso(epoch: int) -> str:
    """带时区的 ISO 8601 时刻。"""
    return datetime.fromtimestamp(epoch, ZONE).isoformat()


def _line(product, route, store, apps, price, fee=None, expected=None, reason=None) -> dict:
    """人物读到的答案的一行：九个键；reason 为空时是「可以下单」。"""
    return {"product": product, "route": route, "store": store, "apps": apps, "price": price, "delivery_fee": fee,
            "expected_at": expected, "now": "可以下单" if reason is None else "不可下单", "reason": reason}


def test_goods_sold_only_in_an_app_can_be_queried_by_name(engine_factory):
    """「平台笔记本」不在工具层的商品窗口里，查询照样受理，答案里有它的快递行。"""
    _, answer = _asked(engine_factory(), "book", "平台笔记本")
    assert [(row["product"], row["route"], row["store"]) for row in answer["rows"]] == [("平台笔记本", "快递", None)]


def test_answer_reads_the_world_at_the_finishing_second(engine_factory, tmp_path):
    """开始时刻离一心面馆关门不到 5 分钟：受理时营业、做完时已打烊的它不在答案里；受理时还没开门、做完时已开门的
    三杯咖啡在答案里。"""
    root = _edited(tmp_path, "hours", _hours({"noodle-1": ("07:00", "08:05"), "cafe": ("08:05", "22:00")}),
                   "location.jsonl")
    done, answer = _asked(engine_factory(root), "tea", "柠檬茶")
    assert done.ended_at >= int(datetime(2026, 1, 5, 8, 5, tzinfo=ZONE).timestamp())
    assert [(row["store"], row["route"], row["now"]) for row in answer["rows"]] == [
        ("三杯咖啡", "外卖", "可以下单"), ("二味小炒", "到店", "不可下单"), ("四季便利", "到店", "不可下单")]


def test_every_open_store_selling_the_goods_gives_one_row(engine_factory):
    """人在家查询「柠檬茶」、prefer 为 null：恰四行。一心面馆与三杯咖啡各一条外卖行，可以下单，可用的 app 是各自登记的
    全部 app，运费 5 元，预计送达是做完时刻加 1,105 秒与 1,546 秒，较近的排前；二味小炒与四季便利各一条到店行排在后面，
    不可下单，原因逐字，没有 app、运费与送达。"""
    done, answer = _asked(engine_factory(), "tea", "柠檬茶")
    assert answer["scope"] == "列出全部途径" and "omitted" not in answer
    assert answer["rows"] == [
        _line("柠檬茶", "外卖", "一心面馆", ["美团"], "12 元", "5 元", _iso(done.ended_at + 1105)),
        _line("柠檬茶", "外卖", "三杯咖啡", ["饿了么", "美团"], "12 元", "5 元", _iso(done.ended_at + 1546)),
        _line("柠檬茶", "到店", "二味小炒", [], "12 元", reason=NO_APP),
        _line("柠檬茶", "到店", "四季便利", [], "12 元", reason=NO_APP)]


def test_orderability_ignores_the_balance_and_the_slots(engine_factory):
    """余额为 0、槽位 1 做着「测试休息」：一心面馆的外卖行照样可以下单，原因为 null。"""
    def broke(persona, config):
        persona["initial_balance_fen"] = 0

    engine = engine_factory(mutate=broke)
    _, (rest, side) = act_group(engine, "pair", do_at("家", "测试休息"), query("柠檬茶"))
    assert (rest.status, side.status) == ("in_progress", "in_progress")
    _finish(engine, side.activity_id)
    answer = progress_calls(engine.observe(context(engine, "read")), SIDE_TOOL)[0]["result"]["details"]["query"]
    noodle = next(row for row in answer["rows"] if row["store"] == "一心面馆")
    assert (noodle["now"], noodle["reason"]) == ("可以下单", None)


def test_store_where_the_person_stands_comes_first_and_other_stores_cannot_deliver(engine_factory, tmp_path):
    """人在三杯咖啡查询「柠檬茶」：第一行是三杯咖啡的到店行，可以下单，没有 app、运费与送达；一心面馆的外卖行照列但
    不可下单，原因点出三杯咖啡。三杯咖啡做完时已打烊：到店行仍在，原因是已打烊，一心面馆的外卖行仍不可下单。"""
    present = "你所在的三杯咖啡就卖这件商品，此刻购买只在所在地点到店完成，不从这家店送"
    _, answer = _asked(engine_factory(mutate=_at("三杯咖啡")), "tea", "柠檬茶")
    assert answer["rows"][:2] == [_line("柠檬茶", "到店", "三杯咖啡", [], "12 元"),
                                  _line("柠檬茶", "外卖", "一心面馆", ["美团"], "12 元", reason=present)]
    assert [row["store"] for row in answer["rows"][2:]] == ["二味小炒", "四季便利"]
    root = _edited(tmp_path, "cafe-closed", _closed("cafe"), "location.jsonl")
    _, closed = _asked(engine_factory(root, mutate=_at("三杯咖啡")), "tea", "柠檬茶")
    assert _line("柠檬茶", "到店", "三杯咖啡", [], "12 元", reason="这家店此刻已打烊") in closed["rows"]
    assert _line("柠檬茶", "外卖", "一心面馆", ["美团"], "12 元", reason=present) in closed["rows"]


def _two_app_notebook(tmp_path):
    """夹具副本：平台笔记本登记哔哩哔哩和美团两个 app。"""
    def change(row):
        if row["goods_id"] == "online-notebook":
            row["app_ids"] = ["bilibili", "meituan"]
    return _edited(tmp_path, "two-apps", change, "merchant-catalog.jsonl")


def test_courier_goods_give_one_row_quoted_like_the_purchase_of_the_same_second(engine_factory, tmp_path):
    """平台笔记本登记两个 app。prefer 为 null：恰一行快递，没有店，可用的 app 是两个，运费与到站时刻等于同一秒不写意向
    购买它冻结在订单上的数。prefer 写当天没被抽到的那个：可用的 app 只有它，运费与到站时刻等于同一秒带同一个意向购买
    冻结的数。"""
    root = _two_app_notebook(tmp_path)
    state = engine_factory(root).context.state
    drawn = random_stream(state.random_state, "order-app:online-notebook:courier:2026-01-05").choice(
        ["bilibili", "meituan"])
    (other,) = {"bilibili", "meituan"} - {drawn}
    for prefer, apps in ((None, ["哔哩哔哩", "美团"]), (APP_NAMES[other], [APP_NAMES[other]])):
        engine = engine_factory(root)
        _, answer = _asked(engine, "book", "平台笔记本", prefer=prefer)
        _, bought = act(engine, "buy", purchase("平台笔记本", prefer=prefer))
        assert bought.status == "in_progress", bought.error_code
        (order,) = bought.data["orders"]
        assert order["app_id"] == (drawn if prefer is None else other)
        fee = f"{order['delivery_fee_fen'] // 100} 元"
        assert answer["rows"] == [_line("平台笔记本", "快递", None, apps, "12 元", fee, _iso(order["expected_at"]))]


def test_goods_without_any_open_store_keep_one_row_with_the_reason(engine_factory, tmp_path):
    """卖牛肉面的两家餐馆都打烊、人在家：恰一行，没有途径、没有店，原因是此刻没有营业的店卖这件商品。"""
    root = _edited(tmp_path, "noodles-closed", _closed("noodle-1", "noodle-2"), "location.jsonl")
    _, answer = _asked(engine_factory(root), "noodles", "牛肉面")
    assert answer["rows"] == [_line("牛肉面", None, None, [], "28 元", reason="此刻没有营业的店卖这件商品")]


def test_usable_prefer_lists_only_what_its_app_can_deliver(engine_factory, tmp_path):
    """人在公司查询「食品」、prefer 写「美团」：app 是美团，只列经它能买的：只有牛肉面在一心面馆的外卖行，可用的 app 只有
    美团；没有二味小炒的到店行，也没有饭团。另指名饭团时多一行「这个 app 里没有」。一心面馆做完时已打烊：牛肉面只剩一行，
    原因是上了这个 app、卖这件商品的店此刻都没有营业。"""
    engine = engine_factory(mutate=_at("公司"))
    done, answer = _asked(engine, "meal", "食品", prefer="美团")
    assert (answer["app"], answer["scope"]) == ("美团", "只列经这个 app 能买的")
    assert answer["rows"] == [_line("牛肉面", "外卖", "一心面馆", ["美团"], "28 元", "5 元", _iso(done.ended_at + 799))]
    done, named = _asked(engine, "meal-and-rice", "食品", "饭团", prefer="美团")
    assert named["rows"] == [_line("牛肉面", "外卖", "一心面馆", ["美团"], "28 元", "5 元", _iso(done.ended_at + 799)),
                             _line("饭团", None, None, [], "10 元", reason="这个 app 里没有")]
    root = _edited(tmp_path, "noodle-closed", _closed("noodle-1"), "location.jsonl")
    _, closed = _asked(engine_factory(root, mutate=_at("公司")), "meal", "食品", prefer="美团")
    assert closed["rows"] == [_line("牛肉面", None, None, [], "28 元", reason="上了这个 app、卖这件商品的店此刻都没有营业")]


def test_prefer_never_filters_out_the_store_where_the_person_stands(engine_factory):
    """人在三杯咖啡查询「柠檬茶」、prefer 写「美团」：三杯咖啡的到店行可以下单；一心面馆的外卖行只写美团、不可下单；
    没有二味小炒和四季便利。人在四季便利查询「牛肉面」「纸巾」：纸巾先是四季便利的到店行，再是「这个 app 里没有」。
    人在四季便利查询「食品」：类别只展开美团卖的商品，没有饭团。"""
    present = "你所在的三杯咖啡就卖这件商品，此刻购买只在所在地点到店完成，不从这家店送"
    _, cafe = _asked(engine_factory(mutate=_at("三杯咖啡")), "tea", "柠檬茶", prefer="美团")
    assert cafe["rows"] == [_line("柠檬茶", "到店", "三杯咖啡", [], "12 元"),
                            _line("柠檬茶", "外卖", "一心面馆", ["美团"], "12 元", reason=present)]
    _, store = _asked(engine_factory(mutate=_at("四季便利")), "two", "牛肉面", "纸巾", prefer="美团")
    assert [row for row in store["rows"] if row["product"] == "纸巾"] == [
        _line("纸巾", "到店", "四季便利", [], "6 元"), _line("纸巾", None, None, [], "6 元", reason="这个 app 里没有")]
    _, meal = _asked(engine_factory(mutate=_at("四季便利")), "meal", "食品", prefer="美团")
    assert {row["product"] for row in meal["rows"]} == {"牛肉面"}


def _many_snacks(tmp_path):
    """夹具副本：零食类再加 22 件，10 件只在餐馆卖，8 件只在咖啡店卖，4 件只在便利店卖，连薯片共 23 件。"""
    root = copy_environment(tmp_path / "snacks")
    rows = catalog_rows(root, "merchant-catalog.jsonl")
    for prefix, kind, count in (("r", "restaurant", 10), ("c", "cafe", 8), ("s", "convenience_store", 4)):
        rows.extend({"category": "snack", "goods_id": f"snack-{prefix}-{index:02d}", "name": f"零食{prefix}{index:02d}",
                     "price_fen": 500, "store_kinds": [kind]} for index in range(count))
    write_catalog_rows(root, "merchant-catalog.jsonl", sorted(rows, key=lambda row: row["goods_id"]))
    return root


def test_answer_orders_goods_and_rows_and_lists_at_most_twenty_goods(engine_factory, tmp_path):
    """零食 23 件：恰列 20 件，「还有 3 件没列」；有可下单行的在前，其间按最早送达（一心面馆早于三杯咖啡）、再按
    goods_id，只在便利店卖的排后；一件之内可下单的在前、按送达，不可下单的按店铺身份。另指名排序靠后的纸巾时，它照
    排序键落在 20 件之外，不因指名提前；件数不超过 20 时没有 omitted。"""
    root = _many_snacks(tmp_path)
    _, answer = _asked(engine_factory(root), "snacks", "零食")
    listed = list(dict.fromkeys(row["product"] for row in answer["rows"]))
    assert answer["omitted"] == "还有 3 件没列" and len(listed) == 20
    assert listed == ["薯片", *(f"零食r{index:02d}" for index in range(10)), *(f"零食c{index:02d}" for index in range(8)),
                      "零食s00"]
    chips = [(row["store"], row["now"]) for row in answer["rows"] if row["product"] == "薯片"]
    assert chips == [("一心面馆", "可以下单"), ("三杯咖啡", "可以下单"), ("二味小炒", "不可下单"), ("四季便利", "不可下单")]
    _, named = _asked(engine_factory(root), "snacks-and-tissue", "零食", "纸巾")
    assert named["omitted"] == "还有 4 件没列" and "纸巾" not in {row["product"] for row in named["rows"]}
    _, few = _asked(engine_factory(root), "drink", "饮料")
    assert "omitted" not in few and {row["product"] for row in few["rows"]} == {"柠檬茶"}


def test_category_without_goods_is_accepted_with_an_empty_answer(engine_factory, tmp_path):
    """副本里去掉全部饮料：查询「饮料」照常受理，手机上没有 app，答案没有行。"""
    root = copy_environment(tmp_path / "no-drinks")
    rows = [row for row in catalog_rows(root, "merchant-catalog.jsonl") if row["category"] != "drink"]
    write_catalog_rows(root, "merchant-catalog.jsonl", rows)
    done, answer = _asked(engine_factory(root), "drink", "饮料")
    assert done.data["app_id"] is None
    assert answer == {"app": None, "scope": "列出全部途径", "rows": []}


def test_answer_is_stored_in_machine_form(engine_factory):
    """做完的查询，活动数据的键集恰为六个；answer 恰有 rows 与 omitted，每行九个键，身份、整数分与纪元秒，没有显示名和
    中文；原因只取六个码或 null。"""
    done, _ = _asked(engine_factory(mutate=_at("三杯咖啡")), "mixed", "零食", "饮料", "商品", "牛肉面")
    assert set(done.data) == {"action", "targets", "app_id", "scoped", "accepted_event_id", "answer"}
    answer = done.data["answer"]
    assert set(answer) == {"rows", "omitted"} and answer["omitted"] == 0
    keys = {"goods_id", "route", "store_id", "app_ids", "price_fen", "delivery_fee_fen", "expected_at", "available",
            "reason"}
    for row in answer["rows"]:
        assert set(row) == keys and row["goods_id"] in {"chips", "lemon-tea", "online-notebook", "playing-cards",
                                                         "tissue", "beef-noodles"}
        assert row["route"] in {"pickup", "delivery", "station_pickup", None} and type(row["price_fen"]) is int
        assert all(app in APP_NAMES for app in row["app_ids"]) and row["app_ids"] == sorted(row["app_ids"])
        assert row["reason"] is None if row["available"] else row["reason"] in REASON_CODES
        for key in ("delivery_fee_fen", "expected_at"):
            assert row[key] is None or type(row[key]) is int
    assert {row["reason"] for row in answer["rows"]} >= {None, "present_at_seller", "store_without_app"}
    assert not any("一" <= char <= "鿿" for char in str(answer))


def test_answer_is_the_second_result(engine_factory):
    """做完后这条请求的 result.status 是 completed，details.query.rows 非空；这条动作最后一条 action_result 事件就是
    第二条结果，带着做完时的答案。"""
    engine = engine_factory()
    done, answer = _asked(engine, "tea", "柠檬茶")
    assert answer["rows"]
    last = [row for row in events(engine, "action_result") if row.action_id == action_identity("tea")][-1]
    assert last.data["status"] == "completed"
    assert last.data["activity"]["data"]["answer"] == done.data["answer"]


def test_answer_stays_for_the_stay_and_leaves_a_settled_line_after_departure(engine_factory):
    """同一段停留里之后两次观察都读得到这条答案；出发后进度层没有它，到达后记忆层有这项查询的结束句，不带答案。"""
    engine = engine_factory()
    done, answer = _asked(engine, "tea", "柠檬茶")
    _, chat = act(engine, "chat", do_at("家", "测试闲聊"))
    assert chat.status == "in_progress"
    _finish(engine, chat.activity_id)
    observed = engine.observe(context(engine, "later"))
    assert progress_calls(observed, SIDE_TOOL)[0]["result"]["details"]["query"] == answer
    conclude_pending(engine, "conclude-chat")
    assert progress_calls(engine.observe(context(engine, "still")), SIDE_TOOL)[0]["result"]["details"]["query"] == answer
    _, trip = act(engine, "leave", travel("公司"))
    assert trip.status == "in_progress"
    _finish(engine, trip.activity_id)
    arrived = engine.observe(context(engine, "arrived"))
    assert not [row for row in progress_calls(arrived) if row["arguments"].get("action") == "query"]
    start, end = (datetime.fromtimestamp(moment, ZONE).strftime("%H:%M") for moment in (done.started_at, done.ended_at))
    sentence = f"{done.name}：{start}–{end}，按计划结束"
    assert sentence in str(arrived["memory_layer"]) and "一心面馆" not in str(arrived["memory_layer"])


def test_goods_only_in_the_answer_are_not_an_exposure(engine_factory):
    """只出现在答案里、从未进过工具层窗口的平台笔记本，做完后不在 presentation_state 里。"""
    engine = engine_factory()
    _, answer = _asked(engine, "goods", "商品")
    assert "平台笔记本" in {row["product"] for row in answer["rows"]}
    assert "online-notebook" not in engine.context.state.presentation_state.get(1, {})


def test_person_buys_in_the_deciding_round_the_query_finished(engine_factory):
    """人物做着「测试休息」：查询做完的那次决策里直接购买被受理，没有先收尾；购买的开始时刻等于查询的结束时刻，
    订单上冻结的预计送达与运费与答案里那家店的行逐值相同。"""
    engine = engine_factory()
    _, (rest, side) = act_group(engine, "pair", do_at("家", "测试休息"), query("柠檬茶"))
    done = _finish(engine, side.activity_id)
    assert engine.context.state.current_time == done.ended_at and engine.scheduling_state().ready_agent_ids == (1,)
    answer = progress_calls(engine.observe(context(engine, "read")), SIDE_TOOL)[0]["result"]["details"]["query"]
    _, bought = act(engine, "buy", purchase("柠檬茶"))
    assert bought.status == "in_progress", bought.error_code
    purchase_activity = engine.context.state.activities[bought.activity_id]
    assert purchase_activity.started_at == done.ended_at
    assert engine.context.state.activities[rest.activity_id].status == "active"
    (order,) = bought.data["orders"]
    store = engine.context.catalog.locations[order["store_id"]].display_name
    row = next(item for item in answer["rows"] if item["store"] == store)
    assert (row["now"], row["expected_at"], row["delivery_fee"]) == (
        "可以下单", _iso(order["expected_at"]), f"{order['delivery_fee_fen'] // 100} 元")
    assert advance_until_ready(engine)
