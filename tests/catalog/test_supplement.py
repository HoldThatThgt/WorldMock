"""@brief 加油站、驿站、完整运输时长与远程社交通信方式的严格目录补订。"""

import pytest

from tests.catalog.conftest import edit_row, read_payload, write_payload
from worldmock.catalog.errors import RegistrationError
from worldmock.catalog.loader import load_catalog
from worldmock.catalog.mapgen import generate_map


def add_stations(catalog_dir):
    """@brief 在隔离目录变体登记两个站点与燃油，不改七节点人工图。"""
    payload = read_payload(catalog_dir, "locations.json")
    payload["locations"].extend([
        {"location_id": "gas", "name": "测试加油站", "kind": "gas_station", "app_ids": [],
         "shipping_seconds": None},
        {"location_id": "parcel", "name": "测试驿站", "kind": "parcel_station", "app_ids": [],
         "shipping_seconds": None},
    ])
    payload["locations"].sort(key=lambda row: row["location_id"])
    write_payload(catalog_dir, "locations.json", payload)
    graph = generate_map([row["location_id"] for row in payload["locations"]], 7)
    write_payload(catalog_dir, "map.json", graph.model_dump(mode="json"))
    payload = read_payload(catalog_dir, "goods.json")
    payload["goods"].append({"goods_id": "fuel-pack", "name": "测试燃油", "category": "fuel",
                             "price_fen": 20000, "store_ids": "all"})
    payload["goods"].sort(key=lambda row: row["goods_id"])
    write_payload(catalog_dir, "goods.json", payload)


def test_gas_sells_only_fuel_and_parcel_has_no_goods_or_app(catalog_dir):
    """@brief 所有绑定按新增固定供给表展开，原四类店铺的供给不扩到加油站。"""
    add_stations(catalog_dir)
    catalog = load_catalog(catalog_dir)
    assert catalog.goods_by_store["gas"] == ("fuel-pack",)
    assert catalog.goods_by_store["parcel"] == ()
    assert catalog.apps_by_store["parcel"] == ()
    assert all("fuel-pack" not in goods for place, goods in catalog.goods_by_store.items() if place != "gas")
    assert catalog.location_kinds("gas_station") == catalog.location_kinds("parcel_station") == ("venue",)
    assert catalog.goods_by_store["cafe"] == ("chips", "lemon-tea")


@pytest.mark.parametrize("goods,stores", [("fuel-pack", ["store"]), ("fuel-pack", ["parcel"]),
                                          ("chips", ["gas"]), ("playing-cards", ["gas"])])
def test_goods_cannot_cross_gas_supply_boundary(catalog_dir, goods, stores):
    """@brief 燃油不能绑定普通店铺或驿站，其他商品不能绑定加油站。"""
    add_stations(catalog_dir)
    edit_row(catalog_dir, "goods.json", goods, {"store_ids": stores})
    with pytest.raises(RegistrationError, match="GOODS_STORE_KIND_INVALID"):
        load_catalog(catalog_dir)


@pytest.mark.parametrize("place,updates,code", [
    ("parcel", {"app_ids": ["meituan"]}, "ENVIRONMENT_KIND_INVALID"),
    ("parcel", {"shipping_seconds": 100}, "ENVIRONMENT_KIND_INVALID"),
    ("gas", {"shipping_seconds": 100}, "ENVIRONMENT_KIND_INVALID"),
    ("home", {"shipping_seconds": 100}, "ENVIRONMENT_KIND_INVALID"),
    ("cafe", {"shipping_seconds": 0}, "ENVIRONMENT_NUMBER_INVALID"),
    ("cafe", {"shipping_seconds": -1}, "ENVIRONMENT_NUMBER_INVALID"),
    ("cafe", {"shipping_seconds": True}, "ENVIRONMENT_NUMBER_INVALID"),
    ("cafe", {"shipping_seconds": 100.0}, "ENVIRONMENT_NUMBER_INVALID"),
    ("cafe", {"shipping_seconds": "100"}, "ENVIRONMENT_NUMBER_INVALID"),
])
def test_shipping_and_station_types_are_strict(catalog_dir, place, updates, code):
    """@brief 运输事实只属于普通店铺，严格正整数不能由布尔或其他数字形状冒充。"""
    add_stations(catalog_dir)
    edit_row(catalog_dir, "locations.json", place, updates)
    with pytest.raises(RegistrationError, match=code):
        load_catalog(catalog_dir)


def test_shipping_seconds_is_required_and_long_transport_is_registered(catalog_dir):
    """@brief 显式空值选择原图配送算子，完整长途时长可在店铺登记但不能省略。"""
    edit_row(catalog_dir, "locations.json", "cafe", {"shipping_seconds": 86401})
    assert load_catalog(catalog_dir).locations["cafe"].shipping_seconds == 86401
    payload = read_payload(catalog_dir, "locations.json")
    payload["locations"][0].pop("shipping_seconds")
    write_payload(catalog_dir, "locations.json", payload)
    with pytest.raises(RegistrationError, match="ENVIRONMENT_NUMBER_INVALID"):
        load_catalog(catalog_dir)


@pytest.mark.parametrize("action,mode", [("voice-call", None), ("voice-call", "video"), ("voice-call", True),
                                        ("run", "voice"), ("small-talk", "text"), ("browse-feed", "text")])
def test_action_communication_mode_has_only_explicit_remote_social_values(catalog_dir, action, mode):
    """@brief 应用社交必须写文字或语音，其他动作与无应用社交必须写空。"""
    edit_row(catalog_dir, "actions.json", action, {"communication_mode": mode})
    with pytest.raises(RegistrationError, match="ACTION_COMMUNICATION_MODE_INVALID"):
        load_catalog(catalog_dir)


def test_communication_mode_is_required_and_does_not_guess_from_name(catalog_dir):
    """@brief 相同名字的动作可以明示文字路径；缺字段不能从语音名字补齐。"""
    edit_row(catalog_dir, "actions.json", "voice-call", {"communication_mode": "text"})
    assert load_catalog(catalog_dir).actions["voice-call"].communication_mode == "text"
    payload = read_payload(catalog_dir, "actions.json")
    payload["actions"][-1].pop("communication_mode")
    write_payload(catalog_dir, "actions.json", payload)
    with pytest.raises(RegistrationError, match="ACTION_COMMUNICATION_MODE_INVALID"):
        load_catalog(catalog_dir)
