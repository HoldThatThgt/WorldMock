"""当前目录发布拒绝错侧容器、无效回调与损坏地图，错误原因保持可观察。"""

import json
from dataclasses import replace

import pytest

from tests.life.environment_inputs import catalog_rows, copy_environment, write_catalog_rows
from worldmock.catalog.admission import validate_manifest, validate_map
from worldmock.catalog.errors import RegistrationError
from worldmock.catalog.identity import read_manifest_payload, refresh_manifest_identity
from worldmock.catalog.loader import load_catalog, validate_catalog_files
from worldmock.catalog.models import MapGraph
from worldmock.footprints.models import canonical_json


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
    ({"rules_version": 3}, "RULES_VERSION_MISMATCH"),
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


EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
MAP_ROW = b'{"a":"home","b":"office","length_m":12600}\n'


def rewrite_manifest(directory, change):
    """只改清单原文，不刷新，用来构造清单与实际目录不一致的环境包。"""
    payload = json.loads((directory / "manifest.json").read_bytes())
    change(payload)
    (directory / "manifest.json").write_bytes(canonical_json(payload) + b"\n")


def record(path, sha256=EMPTY_SHA256, size_bytes=0):
    return {"path": path, "sha256": sha256, "size_bytes": size_bytes}


def listed(payload, *extra):
    payload["files"] = sorted([*payload["files"], *extra], key=lambda entry: entry["path"])


def refused(directory):
    """加载和纯校验共用同一段清单检查，必须给出同一个登记失败。"""
    with pytest.raises(RegistrationError) as loaded:
        load_catalog(directory, 7)
    with pytest.raises(RegistrationError) as checked:
        validate_catalog_files(directory)
    assert (loaded.value.code, loaded.value.detail) == (checked.value.code, checked.value.detail)
    return loaded.value


@pytest.mark.parametrize("missing", ["app-catalog.jsonl", "content-catalog.jsonl", "location.jsonl",
                                     "merchant-catalog.jsonl"])
def test_manifest_must_list_every_required_file(directory, missing):
    rewrite_manifest(directory, lambda payload: payload.update(
        files=[entry for entry in payload["files"] if entry["path"] != missing]))
    failure = refused(directory)
    assert (failure.code, failure.detail) == ("ENVIRONMENT_FILE_UNLISTED", missing)
    assert failure.message == "The manifest does not list a catalog file that is required or present."


@pytest.mark.parametrize("name,content", [("callbacks.jsonl", b""), ("map.jsonl", b""), ("map.jsonl", MAP_ROW)])
def test_present_optional_file_must_be_listed(directory, name, content):
    """可选文件放进目录而没有刷新清单：不被忽略，也不当作身份不符，详情是这个文件名。"""
    (directory / name).write_bytes(content)
    failure = refused(directory)
    assert (failure.code, failure.detail) == ("ENVIRONMENT_FILE_UNLISTED", name)
    refresh_manifest_identity(directory)
    assert name in [entry.path for entry in load_catalog(directory, 7).manifest.files]


@pytest.mark.parametrize("name", ["callbacks.jsonl", "map.jsonl"])
def test_listed_optional_file_that_does_not_exist_is_an_identity_mismatch(directory, name):
    rewrite_manifest(directory, lambda payload: listed(payload, record(name)))
    assert refused(directory).code == "ENVIRONMENT_IDENTITY_MISMATCH"


@pytest.mark.parametrize("name", ["travel-method.jsonl", "travel_method.jsonl", "map.json", "obsolete.jsonl"])
def test_file_table_path_outside_the_closed_set_is_stale(directory, name):
    (directory / name).write_bytes(b"")
    rewrite_manifest(directory, lambda payload: listed(payload, record(name)))
    failure = refused(directory)
    assert (failure.code, failure.detail) == ("ENVIRONMENT_STALE_FILE", str(directory.resolve() / name))


@pytest.mark.parametrize("name", ["travel-method.jsonl", "callback.jsonl", "notes.txt"])
def test_unlisted_directory_entry_outside_the_closed_set_is_stale(directory, name):
    (directory / name).write_bytes(b"")
    failure = refused(directory)
    assert (failure.code, failure.detail) == ("ENVIRONMENT_STALE_FILE", str(directory.resolve() / name))


@pytest.mark.parametrize("change,code", [
    (lambda payload: payload["files"].append(dict(payload["files"][0])), "ENVIRONMENT_ID_DUPLICATE"),
    (lambda payload: payload["files"].reverse(), "ENVIRONMENT_KIND_INVALID"),
    (lambda payload: payload["files"][0].update(size_bytes=-1), "ENVIRONMENT_NUMBER_INVALID"),
    (lambda payload: payload["files"][0].update(size_bytes=True), "ENVIRONMENT_NUMBER_INVALID"),
    (lambda payload: payload["files"][0].pop("size_bytes"), "ENVIRONMENT_NUMBER_INVALID"),
    (lambda payload: payload["files"][0].update(size_bytes=-1, note="x"), "ENVIRONMENT_KIND_INVALID"),
    (lambda payload: payload["files"][0].update(sha256="0" * 63), "ENVIRONMENT_KIND_INVALID"),
    (lambda payload: payload.update(note="x"), "ENVIRONMENT_KIND_INVALID"),
], ids=["duplicate", "unsorted", "negative-size", "boolean-size", "missing-size", "unknown-field-first",
        "bad-digest", "unknown-manifest-field"])
def test_file_table_shape_errors_use_their_fixed_codes(directory, change, code):
    rewrite_manifest(directory, change)
    assert refused(directory).code == code


@pytest.mark.parametrize("changes,code,detail", [
    (("old_rules", "bad_shape", "duplicate"), "RULES_VERSION_MISMATCH", None),
    (("bad_shape", "duplicate"), "ENVIRONMENT_KIND_INVALID", None),
    (("duplicate", "stale_listed", "required_unlisted"), "ENVIRONMENT_ID_DUPLICATE", None),
    (("unsorted", "stale_listed"), "ENVIRONMENT_KIND_INVALID", None),
    (("stale_listed", "required_unlisted", "stale_entry"), "ENVIRONMENT_STALE_FILE", "obsolete.jsonl"),
    (("required_unlisted", "stale_entry", "optional_unlisted"), "ENVIRONMENT_FILE_UNLISTED", "location.jsonl"),
    (("stale_entry", "optional_unlisted", "drift"), "ENVIRONMENT_STALE_FILE", "notes.txt"),
    (("optional_unlisted", "drift"), "ENVIRONMENT_FILE_UNLISTED", "map.jsonl"),
    (("drift",), "ENVIRONMENT_IDENTITY_MISMATCH", None),
])
def test_manifest_checks_report_the_earliest_failing_step(directory, changes, code, detail):
    """八步清单检查先遇到的先报：后面的错误同时存在也不改变报出的登记码。"""
    def change(payload):
        if "stale_listed" in changes:
            listed(payload, record("obsolete.jsonl"))
        if "required_unlisted" in changes:
            payload["files"] = [entry for entry in payload["files"] if entry["path"] != "location.jsonl"]
        if "duplicate" in changes:
            payload["files"].append(dict(payload["files"][0]))
        if "unsorted" in changes:
            payload["files"].reverse()
        if "bad_shape" in changes:
            payload["format_version"] = 3
        if "old_rules" in changes:
            payload["rules_version"] = 3

    if "stale_entry" in changes:
        (directory / "notes.txt").write_bytes(b"")
    if "optional_unlisted" in changes:
        (directory / "map.jsonl").write_bytes(MAP_ROW)
    if "drift" in changes:
        path = directory / "merchant-catalog.jsonl"
        path.write_bytes(path.read_bytes() + b" ")
    rewrite_manifest(directory, change)
    failure = refused(directory)
    assert failure.code == code
    if detail is not None:
        assert failure.detail in {detail, str(directory.resolve() / detail)}


@pytest.mark.parametrize("name", ["callbacks.jsonl", "map.jsonl"])
def test_listed_optional_file_must_be_a_regular_file_not_a_link(directory, monkeypatch, name):
    from pathlib import Path

    (directory / name).write_bytes(b"")
    refresh_manifest_identity(directory)
    load_catalog(directory, 7)
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path.name == name or original(path))
    assert refused(directory).code == "ENVIRONMENT_IDENTITY_MISMATCH"


@pytest.mark.parametrize("name", ["callbacks.jsonl", "map.jsonl"])
def test_optional_name_occupied_by_a_directory_is_an_identity_mismatch(directory, name):
    """可选文件名下只要有条目就算存在；条目不是普通文件时登记了也读不出身份，刷新同样失败。"""
    (directory / name).mkdir()
    assert refused(directory).code == "ENVIRONMENT_FILE_UNLISTED"
    rewrite_manifest(directory, lambda payload: listed(payload, record(name)))
    assert refused(directory).code == "ENVIRONMENT_IDENTITY_MISMATCH"
    with pytest.raises(RegistrationError) as failure:
        refresh_manifest_identity(directory)
    assert failure.value.code == "ENVIRONMENT_IDENTITY_MISMATCH"
