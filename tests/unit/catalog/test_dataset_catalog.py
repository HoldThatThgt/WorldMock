"""@brief 新环境目录的真实加载、容器索引、营业安排和回调登记。"""

from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from worldmock.catalog.errors import RegistrationError
from worldmock.catalog.identity import refresh_manifest_identity
from worldmock.catalog.loader import load_catalog, validate_catalog_files
from worldmock.catalog.mapgen import generate_map
from worldmock.life.randomness import random_stream


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
    assert catalog.apps_by_action["purchase"] == ("alipay", "wechat")
    for action in ("walk", "drive", "take_taxi", "take_bus"):
        assert catalog.apps_by_action[action] == ("amap",)
    assert catalog.apps_by_action["sleep"] == ()
    assert catalog.apps_by_action["interact"] == ()
    assert catalog.contents_by_app["eleme"] == ()
    assert catalog.stores_by_app["eleme"]
    assert any(len(items) >= 2 for items in catalog.contents_by_series.values())


def test_pure_catalog_validation_does_not_require_or_generate_a_map(monkeypatch):
    def forbidden(*args):
        raise AssertionError("Pure catalog validation must not generate a map")
    monkeypatch.setattr("worldmock.catalog.loader.generate_map", forbidden)
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
        {"rule_id": "drive", "trigger": {"class": "action", "key": "drive"},
         "effect": {"type": "state_delta", "state": "fuel", "delta": -1, "once_per": "local_day"}})
    catalog = load_catalog(catalog_directory, 7)
    assert catalog.callback_states["fuel"].cap == 3
    assert catalog.callback_rules["charge"].effect.direction == "expense"
    assert catalog.callback_rules["drive"].trigger.trigger_class == "action"
    assert catalog.callback_rules["drive"].model_dump(mode="json")["trigger"]["class"] == "action"


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
        {"rule_id": "rule", "trigger": {"class": "action", "key": "drive"}, "effect": effect})
    assert_refused(catalog_directory, code)


def test_callback_state_rows_must_precede_rules(catalog_directory):
    write_callbacks(catalog_directory,
        {"rule_id": "rule", "trigger": {"class": "time", "key": "run_start"},
         "effect": {"type": "transfer", "direction": "income", "amount_fen": 1}}, STATE)
    assert_refused(catalog_directory, "ENVIRONMENT_KIND_INVALID")
