"""@brief 目录的文件集合与夹具字节：固定规则只在引擎代码里，不随目录发布。"""

from worldmock.resource_engine.catalog.identity import OPTIONAL_CATALOG_FILES, REQUIRED_CATALOG_FILES
from worldmock.resource_engine.catalog.loader import load_catalog


def test_catalog_has_four_required_files_three_optional_names_and_no_engine_rule_copies(catalog_dir):
    """目录必需四个作者文件，回调、距离声明和出行方式可以不放；夹具恰为清单加五个文件，生成的地图与固定规则不进入发布清单。"""
    catalog = load_catalog(catalog_dir, 7)
    assert REQUIRED_CATALOG_FILES == {
        "app-catalog.jsonl", "content-catalog.jsonl", "location.jsonl", "merchant-catalog.jsonl"}
    assert OPTIONAL_CATALOG_FILES == {"callbacks.jsonl", "map.jsonl", "travel-method.jsonl"}
    assert {entry.path for entry in catalog.manifest.files} == REQUIRED_CATALOG_FILES | {"travel-method.jsonl"}
    assert {path.name for path in catalog_dir.iterdir()} == REQUIRED_CATALOG_FILES | {
        "manifest.json", "travel-method.jsonl"}
    assert catalog.callback_states == {} and catalog.callback_rules == {}
    assert catalog.apps_by_travel_method["walking"] == ("amap",)
    assert catalog.apps_by_action["purchase"] == ("alipay", "wechat")


def test_fixture_travel_method_file_is_written_byte_for_byte(catalog_dir):
    """夹具的出行方式文件与导航 app 一行逐字节固定：键按升序、不带空格、每行以 LF 结尾。"""
    assert (catalog_dir / "travel-method.jsonl").read_bytes() == (
        '{"exertion":"light","name":"公共交通","price_per_km_fen":0,"speed_meters_per_second":5,'
        '"start_price_fen":200,"travel_method_id":"bus"}\n'
        '{"exertion":"light","name":"驾车","price_per_km_fen":200,"speed_meters_per_second":7,'
        '"start_price_fen":500,"travel_method_id":"driving"}\n'
        '{"exertion":"light","name":"打车","price_per_km_fen":300,"speed_meters_per_second":7,'
        '"start_price_fen":1400,"travel_method_id":"taxi"}\n').encode("utf-8")
    assert ('{"app_id":"amap","name":"高德地图","role":"function",'
            '"travel_method_ids":["bus","driving","taxi","walking"]}\n').encode("utf-8") in (
        catalog_dir / "app-catalog.jsonl").read_bytes()
