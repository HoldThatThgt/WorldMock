"""@brief 新环境目录的真实加载、容器索引、营业安排和回调登记。"""

from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from worldmock.resource_engine.registration.errors import RegistrationError
from worldmock.resource_engine.catalog.identity import refresh_manifest_identity
from worldmock.resource_engine.catalog.loader import load_catalog, validate_catalog_files
from worldmock.resource_engine.map.mapgen import generate_map
from worldmock.foundation.randomness import random_stream


FIXTURE = Path(__file__).parents[2] / "fixtures" / "dataset-environment" / "catalog"


@pytest.fixture
def catalog_directory(tmp_path):
    shutil.copytree(FIXTURE, tmp_path / "catalog")
    return tmp_path / "catalog"


def replace_rows(directory, filename, change):
    source = directory / filename
    rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines()]
    change(rows)
    source.write_bytes(b"".join(json.dumps(row, ensure_ascii=False).encode("utf-8") + b"\n" for row in rows))
    refresh_manifest_identity(directory)


def write_callbacks(directory, *rows):
    (directory / "callbacks.jsonl").write_bytes(b"".join(
        json.dumps(row, ensure_ascii=False).encode("utf-8") + b"\n" for row in rows))
    refresh_manifest_identity(directory)


def assert_refused(directory, code):
    with pytest.raises(RegistrationError) as captured:
        load_catalog(directory, 7)
    assert captured.value.code == code


def test_content_less_ordering_app_is_registered_without_any_duration(catalog_directory):
    """没有内容、只接单的内容 app 一行只写身份、名称和角色就能登记；去掉它唯一的接单绑定后没有任何绑定。"""
    catalog = load_catalog(catalog_directory, 7)
    assert catalog.contents_by_app["eleme"] == ()
    assert catalog.stores_by_app["eleme"]
    assert catalog.apps["eleme"].model_fields_set == {"app_id", "name", "role"}

    def unbind(rows):
        cafe = next(row for row in rows if row["location_id"] == "cafe")
        cafe["app_ids"] = [key for key in cafe["app_ids"] if key != "eleme"]

    replace_rows(catalog_directory, "location.jsonl", unbind)
    assert_refused(catalog_directory, "APP_UNBOUND")


def test_new_fixture_loads_complete_world_and_seeded_memory_map():
    catalog = load_catalog(FIXTURE, 7)
    assert len(catalog.locations) == 8
    assert len(catalog.apps) == 8
    assert len(catalog.goods) == 7
    assert len(catalog.contents) == 15
    assert catalog.locations["home"].is_home
    assert {key for key, place in catalog.locations.items() if place.sleepable} == {"home", "office"}
    assert catalog.locations["office"].display_name == "公司"
    assert catalog.nearest_parcel_station("home") == "parcel-station"
    state = hashlib.sha256(b"random:7").hexdigest()
    expected = generate_map(list(catalog.locations), random_stream(state, "map").getrandbits(64))
    assert catalog.map_data == expected
    assert load_catalog(FIXTURE, 7).map_sha256 == catalog.map_sha256
    assert load_catalog(FIXTURE, 8).map_sha256 != catalog.map_sha256
    assert not (FIXTURE / "map.json").exists()
    with pytest.raises(TypeError):
        catalog.locations["invented"] = catalog.locations["home"]


def test_catalog_indexes_expand_exact_kinds_and_keep_app_goods_separate():
    catalog = load_catalog(FIXTURE, 7)
    assert catalog.goods_by_store["noodle-1"] == catalog.goods_by_store["noodle-2"]
    assert catalog.goods_by_app["bilibili"] == ("online-notebook",)
    assert all("online-notebook" not in items for items in catalog.goods_by_store.values())
    assert catalog.contents_by_location_kind["parcel_station"] == ("parcel-service",)
    assert dict(catalog.apps_by_action) == {"purchase": ("alipay", "wechat")}
    assert list(catalog.apps_by_travel_method.items()) == [
        ("walking", ("amap",)), ("bus", ("amap",)), ("driving", ("amap",)), ("taxi", ("amap",))]
    assert [(row.travel_method_id, row.name, row.speed_meters_per_second, row.start_price_fen,
             row.price_per_km_fen, row.exertion) for row in catalog.travel_methods.values()] == [
        ("bus", "公共交通", 5, 200, 0, "light"), ("driving", "驾车", 7, 500, 200, "light"),
        ("taxi", "打车", 7, 1400, 300, "light")]
    assert catalog.contents_by_app["eleme"] == ()
    assert catalog.stores_by_app["eleme"]
    assert any(len(items) >= 2 for items in catalog.contents_by_series.values())


def test_pure_catalog_validation_does_not_require_or_generate_a_map(monkeypatch):
    def forbidden(*args):
        raise AssertionError("Pure catalog validation must not generate a map")
    monkeypatch.setattr('worldmock.resource_engine.catalog.loader.generate_map', forbidden)
    validate_catalog_files(FIXTURE)


@pytest.mark.parametrize("seed", [True, False, "7", 7.0, None])
def test_runtime_seed_requires_strict_integer(seed):
    with pytest.raises(RegistrationError, match="ENVIRONMENT_NUMBER_INVALID"):
        load_catalog(FIXTURE, seed)


def test_weekly_opening_hours_use_local_half_open_intervals_and_full_date_override(catalog_directory):
    def change(rows):
        rows[0]["opens"] = {"weekly": {"mon": [["09:00", "12:00"], ["13:00", "24:00"]]},
                             "exceptions": {"2026-10-12": [], "2026-10-13": [["10:00", "11:00"]]}}
    replace_rows(catalog_directory, "location.jsonl", change)
    place = load_catalog(catalog_directory, 7).locations["cafe"]
    assert not place.is_open(datetime(2026, 10, 5, 8, 59))
    assert place.is_open(datetime(2026, 10, 5, 9))
    assert not place.is_open(datetime(2026, 10, 5, 12))
    assert place.is_open(datetime(2026, 10, 5, 23, 59, 59))
    assert not place.is_open(datetime(2026, 10, 6, 10))
    assert not place.is_open(datetime(2026, 10, 12, 10))
    assert place.is_open(datetime(2026, 10, 13, 10))
    assert not place.is_open(datetime(2026, 10, 13, 11))


@pytest.mark.parametrize("opens", [None, {}, {"weekly": {"monday": []}},
    {"weekly": {"mon": [["24:00", "24:00"]]}}, {"weekly": {"mon": [["23:00", "01:00"]]}},
    {"weekly": {"mon": [["09:00", "12:00"], ["11:00", "13:00"]]}},
    {"weekly": {}, "exceptions": {"20261005": []}}, {"weekly": {"mon": [["9:00", "10:00"]]}},
])
def test_invalid_opening_declarations_are_rejected(catalog_directory, opens):
    replace_rows(catalog_directory, "location.jsonl", lambda rows: rows[0].update(opens=opens))
    assert_refused(catalog_directory, "ENVIRONMENT_KIND_INVALID")


@pytest.mark.parametrize("filename,fields,code", [
    ("merchant-catalog.jsonl", {"store_kinds": [], "app_ids": []}, "GOODS_BINDING_MISSING"),
    ("merchant-catalog.jsonl", {"app_ids": ["bilibili"]}, "GOODS_BINDING_SIDE_EXCLUSIVE"),
    ("merchant-catalog.jsonl", {"store_kinds": ["Restaurant"]}, "GOODS_BINDING_DANGLING"),
    ("merchant-catalog.jsonl", {"store_kinds": [], "app_ids": ["alipay"]}, "APP_ROLE_BINDING_INVALID"),
    ("content-catalog.jsonl", {"location_kinds": [], "app_ids": []}, "CONTENT_CONTAINER_MISSING"),
    ("content-catalog.jsonl", {"location_kinds": ["home"]}, "CONTENT_CONTAINER_SIDE_EXCLUSIVE"),
    ("content-catalog.jsonl", {"app_ids": [], "location_kinds": ["Home"]}, "CONTENT_BINDING_DANGLING"),
    ("content-catalog.jsonl", {"app_ids": ["alipay"]}, "APP_ROLE_BINDING_INVALID"),
    ("content-catalog.jsonl", {"relaxation_relief": "arbitrary"}, "ENVIRONMENT_KIND_INVALID"),
])
def test_container_registration_rejects_missing_conflicting_and_dangling_bindings(
        catalog_directory, filename, fields, code):
    replace_rows(catalog_directory, filename, lambda rows: rows[0].update(fields))
    assert_refused(catalog_directory, code)


STATE = {"state": "fuel", "initial": 2, "cap": 3, "reset_on_goods": []}


def test_callback_registration_loads_typed_states_rules_transfer_and_alias(catalog_directory):
    write_callbacks(catalog_directory, STATE,
        {"rule_id": "charge", "trigger": {"class": "time", "key": "monthly", "day": 31, "time": "09:00"},
         "effect": {"type": "transfer", "direction": "expense", "amount_fen": 100}},
        {"rule_id": "drive", "trigger": {"class": "action", "key": "travel", "travel_method_id": "driving"},
         "effect": {"type": "state_delta", "state": "fuel", "delta": -1, "once_per": "local_day"}},
        {"rule_id": "nap", "trigger": {"class": "action", "key": "sleep"},
         "effect": {"type": "transfer", "direction": "income", "amount_fen": 1}})
    catalog = load_catalog(catalog_directory, 7)
    assert catalog.callback_states["fuel"].cap == 3
    assert catalog.callback_rules["charge"].effect.direction == "expense"
    assert catalog.callback_rules["drive"].trigger.trigger_class == "action"
    assert catalog.callback_rules["drive"].model_dump(mode="json")["trigger"] == {
        "class": "action", "key": "travel", "travel_method_id": "driving"}
    assert catalog.callback_rules["nap"].model_dump(mode="json")["trigger"] == {"class": "action", "key": "sleep"}


@pytest.mark.parametrize("effect,code", [
    ({"type": "state_delta", "state": "unknown", "delta": -1}, "CALLBACK_STATE_UNREGISTERED"),
    ({"type": "state_delta", "state": "fuel", "delta": 0}, "ENVIRONMENT_NUMBER_INVALID"),
    ({"type": "state_delta", "state": "fuel", "delta": True}, "ENVIRONMENT_NUMBER_INVALID"),
    ({"type": "state_delta", "state": "fuel", "delta": -1, "once_per": None}, "ENVIRONMENT_KIND_INVALID"),
    ({"type": "footprint_chain", "steps": []}, "CALLBACK_FOOTPRINT_EMPTY"),
    ({"type": "footprint_chain", "steps": [{"kind": "browse", "app_pool": [], "pick": "random"}]},
     "CALLBACK_FOOTPRINT_EMPTY"),
    ({"type": "transfer", "direction": "income", "amount_fen": 0}, "ENVIRONMENT_NUMBER_INVALID"),
    ({"type": "transfer", "direction": "income", "amount_fen": True}, "ENVIRONMENT_NUMBER_INVALID"),
    ({"type": "transfer", "direction": "income", "amount_fen": 100, "account": "merchant:x"},
     "ENVIRONMENT_KIND_INVALID"),
])
def test_callback_effects_refuse_invalid_state_number_pool_and_retired_account(catalog_directory, effect, code):
    write_callbacks(catalog_directory, STATE,
        {"rule_id": "rule", "trigger": {"class": "action", "key": "purchase"}, "effect": effect})
    assert_refused(catalog_directory, code)


def test_callback_state_rows_must_precede_rules(catalog_directory):
    write_callbacks(catalog_directory,
        {"rule_id": "rule", "trigger": {"class": "time", "key": "run_start"},
         "effect": {"type": "transfer", "direction": "income", "amount_fen": 1}}, STATE)
    assert_refused(catalog_directory, "ENVIRONMENT_KIND_INVALID")


PREREQUISITE_HOURS = {"weekly": {"mon": [["08:00", "10:00"], ["17:00", "24:00"]]},
                      "exceptions": {"2026-10-19": [], "2026-10-20": [["12:00", "13:00"]]}}


def set_prerequisites(directory, **prerequisites):
    """给夹具副本里指定内容写上前提（键是 content_id 里的连字符换成下划线），刷新清单。"""
    wanted = {key.replace("_", "-"): value for key, value in prerequisites.items()}

    def change(rows):
        for row in rows:
            if row["content_id"] in wanted:
                row["prerequisite"] = wanted[row["content_id"]]
    replace_rows(directory, "content-catalog.jsonl", change)


def test_legal_prerequisite_rows_load_with_their_parsed_values(catalog_directory):
    """只写地点、只写时段、两项都写、省略四种合法写法都能加载；location_kinds 省略与空数组等价。"""
    set_prerequisites(catalog_directory, c_article={"location_kinds": ["home", "office"]},
                      c_course={"opens": PREREQUISITE_HOURS},
                      c_image={"location_kinds": ["office"], "opens": {"weekly": {}}},
                      c_live={"location_kinds": [], "opens": PREREQUISITE_HOURS})
    contents = load_catalog(catalog_directory, 7).contents
    validate_catalog_files(catalog_directory)
    place_only, time_only, both = (contents[key].prerequisite for key in ("c-article", "c-course", "c-image"))
    assert place_only.location_kinds == ("home", "office") and place_only.opens is None
    assert time_only.location_kinds == () and time_only.opens.weekly["mon"] == (("08:00", "10:00"), ("17:00", "24:00"))
    assert time_only.opens.exceptions == {"2026-10-19": (), "2026-10-20": (("12:00", "13:00"),)}
    assert both.location_kinds == ("office",) and both.opens.weekly == {} and both.opens.exceptions == {}
    assert contents["c-live"].prerequisite == time_only
    assert contents["c-long"].prerequisite is None
    assert all(item.prerequisite is None for item in load_catalog(FIXTURE, 7).contents.values())


def test_opening_hours_object_evaluates_half_open_local_intervals_for_locations_and_prerequisites(catalog_directory):
    """营业时段的求值只在 OpeningHours 上：含头不含尾、24:00 结束、没写的星期关闭、特殊日期整日替换（含空数组）；
    地点行写同一份时段时，地点的两个接口给出同样的结果。"""
    set_prerequisites(catalog_directory, c_course={"opens": PREREQUISITE_HOURS})
    replace_rows(catalog_directory, "location.jsonl", lambda rows: rows[0].update(opens=PREREQUISITE_HOURS))
    catalog = load_catalog(catalog_directory, 7)
    hours, place = catalog.contents["c-course"].prerequisite.opens, catalog.locations["cafe"]
    expected = [
        (datetime(2026, 10, 12, 7, 59, 59), False), (datetime(2026, 10, 12, 8), True),
        (datetime(2026, 10, 12, 9, 59, 59), True), (datetime(2026, 10, 12, 10), False),
        (datetime(2026, 10, 12, 17), True), (datetime(2026, 10, 12, 23, 59, 59), True),
        (datetime(2026, 10, 13, 0), False), (datetime(2026, 10, 13, 9), False),
        (datetime(2026, 10, 19, 9), False), (datetime(2026, 10, 20, 9), False),
        (datetime(2026, 10, 20, 12), True), (datetime(2026, 10, 20, 13), False),
    ]
    for moment, opened in expected:
        assert hours.is_open(moment) is opened, moment
        assert place.is_open(moment) is opened, moment
    for day, intervals in ((12, (("08:00", "10:00"), ("17:00", "24:00"))), (13, ()), (19, ()),
                           (20, (("12:00", "13:00"),))):
        assert hours.opening_intervals(datetime(2026, 10, day).date()) == intervals
        assert place.opening_intervals(datetime(2026, 10, day).date()) == intervals


def test_location_without_declared_hours_is_open_all_day(catalog_directory):
    place = load_catalog(catalog_directory, 7).locations["cafe"]
    assert place.opens is None
    assert place.opening_intervals(datetime(2026, 10, 13).date()) == (("00:00", "24:00"),)
    assert place.is_open(datetime(2026, 10, 13, 0)) and place.is_open(datetime(2026, 10, 13, 23, 59, 59))


def test_null_opening_schedule_is_refused_with_one_sentence_for_locations_and_prerequisites(catalog_directory, tmp_path):
    """地点行与内容前提的 opens 写 null 是同一个前置校验，详情是同一句。"""
    sentence = "A declared opening schedule must be an object."
    other = tmp_path / "other"
    shutil.copytree(FIXTURE, other)
    replace_rows(catalog_directory, "location.jsonl", lambda rows: rows[0].update(opens=None))
    set_prerequisites(other, c_article={"location_kinds": ["office"], "opens": None})
    for directory, line in ((catalog_directory, "location.jsonl:1 "), (other, "content-catalog.jsonl:1 ")):
        with pytest.raises(RegistrationError) as captured:
            load_catalog(directory, 7)
        assert captured.value.code == "ENVIRONMENT_KIND_INVALID"
        assert captured.value.detail.startswith(line) and sentence in captured.value.detail
        assert "location opening schedule" not in captured.value.detail
