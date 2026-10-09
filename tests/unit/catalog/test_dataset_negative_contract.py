"""当前目录发布拒绝错侧容器、无效回调与损坏地图，错误原因保持可观察。"""

import json
from dataclasses import replace

import pytest

from tests.life.environment_inputs import catalog_rows, copy_environment, write_catalog_rows
from worldmock.catalog.admission import validate_manifest, validate_map
from worldmock.catalog.errors import RegistrationError
from worldmock.catalog.identity import read_manifest_payload
from worldmock.catalog.loader import load_catalog
from worldmock.catalog.models import MapGraph


@pytest.fixture
def directory(tmp_path):
    return copy_environment(tmp_path / "input") / "catalog"


def change_rows(directory, filename, identity, update):
    root = directory.parent
    rows = catalog_rows(root, filename)
    row = next(item for item in rows if identity in item.values())
    row.update(update)
    write_catalog_rows(root, filename, rows)


@pytest.mark.parametrize("filename,identity,update,code", [
    ("location.jsonl", "home", {"app_ids": ["amap"]}, "APP_ROLE_BINDING_INVALID"),
    ("merchant-catalog.jsonl", "online-notebook", {"app_ids": [], "store_kinds": []}, "GOODS_BINDING_MISSING"),
    ("merchant-catalog.jsonl", "online-notebook", {"store_kinds": ["store"]}, "GOODS_BINDING_SIDE_EXCLUSIVE"),
    ("merchant-catalog.jsonl", "online-notebook", {"app_ids": ["amap"]}, "APP_ROLE_BINDING_INVALID"),
    ("merchant-catalog.jsonl", "online-notebook", {"app_ids": [], "store_kinds": ["不存在"]},
     "GOODS_BINDING_DANGLING"),
    ("content-catalog.jsonl", "c-article", {"app_ids": [], "location_kinds": []}, "CONTENT_CONTAINER_MISSING"),
    ("content-catalog.jsonl", "c-article", {"location_kinds": ["home"]}, "CONTENT_CONTAINER_SIDE_EXCLUSIVE"),
    ("content-catalog.jsonl", "c-article", {"app_ids": ["amap"]}, "APP_ROLE_BINDING_INVALID"),
    ("content-catalog.jsonl", "c-article", {"app_ids": [], "location_kinds": ["不存在"]},
     "CONTENT_BINDING_DANGLING"),
    ("content-catalog.jsonl", "c-article", {"summary": "x" * 501}, "CONTENT_SUMMARY_TOO_LONG"),
    ("content-catalog.jsonl", "c-article", {"summary_source": {"kind": "real", "url": None, "notice": None}},
     "CONTENT_SOURCE_URL_MISSING"),
    ("app-catalog.jsonl", "amap", {"actions": []}, "APP_UNBOUND"),
    ("app-catalog.jsonl", "amap", {"actions": ["interact"]}, "ENVIRONMENT_KIND_INVALID"),
    ("app-catalog.jsonl", "amap", {"browse_duration_seconds": 60}, "ENVIRONMENT_KIND_INVALID"),
    ("app-catalog.jsonl", "bilibili", {"actions": []}, "APP_ROLE_BINDING_INVALID"),
    ("app-catalog.jsonl", "bilibili", {"browse_duration_seconds": 60}, "ENVIRONMENT_KIND_INVALID"),
])
def test_registration_enforces_actual_roles_and_exclusive_bindings(directory, filename, identity, update, code):
    change_rows(directory, filename, identity, update)
    with pytest.raises(RegistrationError) as failure:
        load_catalog(directory, 7)
    assert failure.value.code == code


@pytest.mark.parametrize("row,code", [
    ({"state": "stock", "initial": 4, "cap": 3, "reset_on_goods": []}, "ENVIRONMENT_NUMBER_INVALID"),
    ({"state": "stock", "initial": 1, "cap": 3, "reset_on_goods": ["unknown"]}, "ENVIRONMENT_REF_UNKNOWN"),
])
def test_callback_state_declarations_must_match_domain_and_real_goods(directory, row, code):
    write_catalog_rows(directory.parent, "callbacks.jsonl", [row])
    with pytest.raises(RegistrationError) as failure:
        load_catalog(directory, 7)
    assert failure.value.code == code


@pytest.mark.parametrize("trigger,effect,notify,code", [
    ({"class": "state", "key": "unknown", "below": 1},
     {"type": "transfer", "direction": "income", "amount_fen": 1}, None, "CALLBACK_STATE_UNREGISTERED"),
    ({"class": "state", "key": "stock", "below": 4},
     {"type": "transfer", "direction": "income", "amount_fen": 1}, None, "ENVIRONMENT_NUMBER_INVALID"),
    ({"class": "time", "key": "monthly", "day": 31, "time": "24:00"},
     {"type": "transfer", "direction": "income", "amount_fen": 1}, None, "ENVIRONMENT_KIND_INVALID"),
    ({"class": "time", "key": "run_start"},
     {"type": "footprint_chain", "steps": [{"kind": "browse", "app_pool": ["unknown"], "pick": "random"}]},
     None, "ENVIRONMENT_REF_UNKNOWN"),
    ({"class": "time", "key": "run_start"},
     {"type": "transfer", "direction": "income", "amount_fen": 1}, {"visible": True}, "CALLBACK_NOTIFY_MISMATCH"),
    ({"class": "time", "key": "run_start"},
     {"type": "footprint_chain", "steps": [{"kind": "browse", "app_pool": ["bilibili"], "pick": "random"}]},
     {"when": {"state": "stock", "below": 1}, "channel": "app_push", "app_id": "bilibili", "text": "提醒"},
     "CALLBACK_NOTIFY_MISMATCH"),
    ({"class": "time", "key": "run_start"}, {"type": "state_delta", "state": "stock", "delta": -1},
     {"when": {"state": "unknown", "below": 1}, "channel": "app_push", "app_id": "bilibili", "text": "提醒"},
     "CALLBACK_STATE_UNREGISTERED"),
    ({"class": "time", "key": "run_start"}, {"type": "state_delta", "state": "stock", "delta": -1},
     {"when": {"state": "stock", "below": 1}, "channel": "app_push", "app_id": "amap", "text": "提醒"},
     "APP_ROLE_BINDING_INVALID"),
])
def test_callback_subscription_and_notification_contracts(directory, trigger, effect, notify, code):
    declaration = {"state": "stock", "initial": 1, "cap": 3, "reset_on_goods": []}
    rule = {"rule_id": "rule", "trigger": trigger, "effect": effect}
    if notify is not None:
        rule["notify"] = notify
    write_catalog_rows(directory.parent, "callbacks.jsonl", [declaration, rule])
    with pytest.raises(RegistrationError) as failure:
        load_catalog(directory, 7)
    assert failure.value.code == code


@pytest.mark.parametrize("payload,code", [
    (b"{}\r\n", "ENVIRONMENT_KIND_INVALID"), (b"broken JSON", "ENVIRONMENT_IDENTITY_MISMATCH"),
    (b"\xff", "ENVIRONMENT_IDENTITY_MISMATCH"), (b"[]\n", "ENVIRONMENT_KIND_INVALID"),
])
def test_manifest_reader_rejects_noncanonical_or_nonobject_document(directory, payload, code):
    (directory / "manifest.json").write_bytes(payload)
    with pytest.raises(RegistrationError) as failure:
        read_manifest_payload(directory)
    assert failure.value.code == code


@pytest.mark.parametrize("change,code", [
    ({"rules_version": 0}, "RULES_VERSION_MISMATCH"),
    ({"files": ()}, "ENVIRONMENT_FILE_UNLISTED"),
])
def test_manifest_validator_checks_version_and_complete_file_table(directory, change, code):
    manifest = load_catalog(directory, 7).manifest.model_copy(update=change)
    with pytest.raises(RegistrationError) as failure:
        validate_manifest(directory, manifest)
    assert failure.value.code == code


@pytest.mark.parametrize("nodes,edges,code", [
    (["a"], [], "MAP_TOO_SMALL"),
    (["a", "b", "c"], [{"a": "b", "b": "a", "length_m": 1}], "MAP_EDGE_INVALID"),
    (["a", "b", "c"], [{"a": "a", "b": "b", "length_m": 1}], "MAP_DISCONNECTED"),
])
def test_public_map_validation_requires_all_nodes_normal_edges_and_connectivity(nodes, edges, code):
    graph = MapGraph.model_validate({"nodes": nodes, "edges": edges})
    with pytest.raises(RegistrationError) as failure:
        validate_map(graph, set(nodes))
    assert failure.value.code == code


def test_manifest_extra_entry_is_rejected_before_reading_undeclared_file(directory):
    catalog = load_catalog(directory, 7)
    extra = catalog.manifest.files[0].model_copy(update={"path": "obsolete.json"})
    files = tuple(sorted((*catalog.manifest.files, extra), key=lambda entry: entry.path))
    manifest = catalog.manifest.model_copy(update={"files": files})
    with pytest.raises(RegistrationError) as failure:
        validate_manifest(directory, manifest)
    assert failure.value.code == "ENVIRONMENT_STALE_FILE" and "obsolete.json" in str(failure.value)


def test_duplicate_normalized_display_names_fail_registration(directory):
    rows = catalog_rows(directory.parent, "app-catalog.jsonl")
    rows[1]["name"] = " " + rows[0]["name"] + " "
    write_catalog_rows(directory.parent, "app-catalog.jsonl", rows)
    with pytest.raises(RegistrationError) as failure:
        load_catalog(directory, 7)
    assert failure.value.code == "ENVIRONMENT_ID_DUPLICATE"


def test_graph_rejects_unknown_origin_and_missing_station_has_explicit_error(directory):
    catalog = load_catalog(directory, 7)
    with pytest.raises(RegistrationError) as failure:
        catalog.graph.shortest_paths("unknown")
    assert failure.value.code == "ENVIRONMENT_REF_UNKNOWN"
    locations = {key: item.model_copy(update={"accepts_parcels": False})
                 for key, item in catalog.locations.items()}
    without_station = replace(catalog, locations=locations)
    with pytest.raises(RegistrationError) as failure:
        without_station.nearest_parcel_station("home")
    assert failure.value.code == "PARCEL_STATION_REQUIRED"


def test_catalog_file_read_reports_real_filesystem_failure(directory, monkeypatch):
    from pathlib import Path
    from worldmock.catalog.identity import read_catalog_file

    original = Path.read_bytes

    def read(path):
        if path == directory / "manifest.json":
            raise PermissionError("Catalog file cannot be read")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    with pytest.raises(RegistrationError) as failure:
        read_catalog_file(directory, "manifest.json")
    assert failure.value.code == "ENVIRONMENT_IDENTITY_MISMATCH"
    assert "Catalog file cannot be read" in str(failure.value)
