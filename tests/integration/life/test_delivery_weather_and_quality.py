"""在大雨天从查菜、下单走到收货，核对人物在两个时刻能看到的事实。"""

from datetime import datetime
from zoneinfo import ZoneInfo

from tests.life.conftest import SIDE_TOOL, context, progress_calls, purchase, query
from tests.life.current_world import stage_moment
from tests.life.environment_inputs import catalog_rows, copy_environment, write_catalog_rows
from tests.life.environment_runtime import act, events, reach, until_ended


def lunch_environment(path):
    root = copy_environment(path)
    menu = catalog_rows(root, "merchant-catalog.jsonl")
    next(row for row in menu if row["goods_id"] == "beef-noodles").update(name="信阳山笋炒腊肉", price_fen=4600)
    write_catalog_rows(root, "merchant-catalog.jsonl", menu)
    places = catalog_rows(root, "location.jsonl")
    next(row for row in places if row["location_id"] == "noodle-1").update(name="掌柜的店")
    write_catalog_rows(root, "location.jsonl", places)
    write_catalog_rows(root, "map.jsonl", [{"a": "home", "b": "noodle-1", "length_m": 4200}])
    return root


def test_rainy_lunch_from_menu_to_receipt(engine_factory, tmp_path):
    def heavy_rain(persona, config):
        config["seed"] = 32

    engine = engine_factory(lunch_environment(tmp_path / "lunch"), mutate=heavy_rain)
    noon = int(datetime(2026, 1, 5, 12, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())
    # 这个固定身份的查菜单用时 311 秒，使查完和受理购买同在中午十二点。
    stage_moment(engine, noon - 311)
    _, menu = act(engine, "menu", query("信阳山笋炒腊肉", prefer="美团"))
    until_ended(engine, menu.activity_id)
    observed, order = act(engine, "lunch", purchase("信阳山笋炒腊肉", prefer="美团"))
    assert engine.context.state.current_time == noon
    (row,) = progress_calls(observed, SIDE_TOOL)[-1]["result"]["details"]["query"]["rows"]
    assert (row["store"], row["rating"], row["expected_at"]) == ("掌柜的店", 4, "2026-01-05T12:27:00+08:00")
    assert order.status == "in_progress"
    until_ended(engine, order.activity_id)
    (pending,) = engine.context.state.deliveries.values()
    assert (pending.status, pending.total_price_fen, pending.expected_at) == ("pending", 5100, noon + 27 * 60)
    assert all(event.data.get("kind") != "goods_received" for event in events(engine))
    reach(engine, noon + 30 * 60)
    arrived = engine.observe(context(engine, "lunch-arrived"))["progress_layer"]["events"]
    (delivery,) = [row for row in arrived if row["type"] == "delivery"]
    assert "天气使配送多用了6分钟，比下单预计晚3分钟" in delivery["message"]
    (receipt,) = [row for row in arrived if row["type"] == "receipt"]
    assert (receipt["store"], receipt["product"], receipt["quality"]) == ("掌柜的店", "信阳山笋炒腊肉", "中")
    assert receipt["occurred_at"] == "2026-01-05T12:30:00+08:00"
