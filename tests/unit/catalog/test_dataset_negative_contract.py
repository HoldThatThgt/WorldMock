"""当前目录发布拒绝错侧容器、无效回调与损坏地图，错误原因保持可观察。"""

import json
from dataclasses import replace

import pytest

from tests.life.environment_inputs import (
    catalog_rows, copy_environment, travel_method_row, walking_only, write_catalog_rows,
)
from worldmock.catalog.admission import validate_manifest, validate_map
from worldmock.catalog.errors import RegistrationError
from worldmock.catalog.identity import read_manifest_payload, refresh_manifest_identity
from worldmock.catalog.loader import load_catalog, validate_catalog_files
from worldmock.catalog.models import App, MapGraph
from worldmock.footprints.models import canonical_json


@pytest.fixture
def directory(tmp_path):
    return copy_environment(tmp_path / "input") / "catalog"


@pytest.fixture
def bare(tmp_path):
    """没有任何可选文件的夹具副本：只有引擎自带的步行，导航 app 只绑定步行。"""
    return walking_only(copy_environment(tmp_path / "bare")) / "catalog"


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
    ("app-catalog.jsonl", "amap", {"travel_method_ids": []}, "APP_UNBOUND"),
    ("app-catalog.jsonl", "amap", {"actions": [], "travel_method_ids": []}, "APP_UNBOUND"),
    ("app-catalog.jsonl", "alipay", {"actions": []}, "APP_UNBOUND"),
    ("app-catalog.jsonl", "alipay", {"actions": [], "travel_method_ids": []}, "APP_UNBOUND"),
    ("app-catalog.jsonl", "amap", {"actions": ["interact"]}, "ENVIRONMENT_KIND_INVALID"),
    ("app-catalog.jsonl", "amap", {"actions": ["travel"]}, "ENVIRONMENT_KIND_INVALID"),
    ("app-catalog.jsonl", "amap", {"actions": ["sleep"]}, "ENVIRONMENT_KIND_INVALID"),
    ("app-catalog.jsonl", "amap", {"actions": ["walk"]}, "ENVIRONMENT_KIND_INVALID"),
    ("app-catalog.jsonl", "amap", {"actions": ["drive"]}, "ENVIRONMENT_KIND_INVALID"),
    ("app-catalog.jsonl", "amap", {"actions": ["take_taxi"]}, "ENVIRONMENT_KIND_INVALID"),
    ("app-catalog.jsonl", "amap", {"actions": ["take_bus"]}, "ENVIRONMENT_KIND_INVALID"),
    ("app-catalog.jsonl", "amap", {"actions": ["drive", "purchase"]}, "ENVIRONMENT_KIND_INVALID"),
    ("app-catalog.jsonl", "amap", {"actions": ["purchase", "purchase"]}, "ENVIRONMENT_ID_DUPLICATE"),
    ("app-catalog.jsonl", "amap", {"travel_method_ids": ["driving", "bus"]}, "ENVIRONMENT_KIND_INVALID"),
    ("app-catalog.jsonl", "amap", {"travel_method_ids": ["walking", "taxi"]}, "ENVIRONMENT_KIND_INVALID"),
    ("app-catalog.jsonl", "amap", {"travel_method_ids": ["bus", "bus"]}, "ENVIRONMENT_ID_DUPLICATE"),
    ("app-catalog.jsonl", "amap", {"travel_method_ids": [""]}, "ENVIRONMENT_KIND_INVALID"),
    ("app-catalog.jsonl", "amap", {"travel_method_ids": "walking"}, "ENVIRONMENT_KIND_INVALID"),
    ("app-catalog.jsonl", "amap", {"browse_duration_seconds": 60}, "ENVIRONMENT_KIND_INVALID"),
    ("app-catalog.jsonl", "bilibili", {"actions": []}, "APP_ROLE_BINDING_INVALID"),
    ("app-catalog.jsonl", "bilibili", {"actions": ["purchase"]}, "APP_ROLE_BINDING_INVALID"),
    ("app-catalog.jsonl", "bilibili", {"travel_method_ids": []}, "APP_ROLE_BINDING_INVALID"),
    ("app-catalog.jsonl", "bilibili", {"travel_method_ids": ["walking"]}, "APP_ROLE_BINDING_INVALID"),
    ("app-catalog.jsonl", "bilibili", {"browse_duration_seconds": 60}, "ENVIRONMENT_KIND_INVALID"),
    ("app-catalog.jsonl", "eleme", {"browse_duration_seconds": 60}, "ENVIRONMENT_KIND_INVALID"),
])
def test_registration_enforces_actual_roles_and_exclusive_bindings(directory, filename, identity, update, code):
    change_rows(directory, filename, identity, update)
    with pytest.raises(RegistrationError) as failure:
        load_catalog(directory, 7)
    assert failure.value.code == code


def test_removed_browse_duration_is_refused_as_an_unknown_field_with_its_line(directory):
    """已删除的浏览时长对没有内容的内容 app 也只是一个未知字段：行模型拒绝，详情指到那一行并带模型原文。"""
    assert "browse_duration_seconds" not in App.model_fields
    change_rows(directory, "app-catalog.jsonl", "eleme", {"browse_duration_seconds": 60})
    failure = refused(directory)
    assert (failure.code, failure.message) == ("ENVIRONMENT_KIND_INVALID", "A declared kind is outside the fixed set.")
    assert failure.detail.startswith("app-catalog.jsonl:5 1 validation error for App\n")
    lines = failure.detail.splitlines()
    assert lines[1] == "browse_duration_seconds"
    assert "Extra inputs are not permitted" in lines[2]


def test_app_binding_refusals_keep_their_rewritten_fixed_reasons(directory):
    """功能 app 两种绑定都为空、内容 app 带任一绑定字段，各用改写后的固定英文原因。"""
    change_rows(directory, "app-catalog.jsonl", "amap", {"travel_method_ids": []})
    with pytest.raises(RegistrationError) as unbound:
        validate_catalog_files(directory)
    assert str(unbound.value) == (
        "APP_UNBOUND: An app must have content, or an action or travel method binding, for its role.")
    change_rows(directory, "app-catalog.jsonl", "amap", {"travel_method_ids": ["walking"]})
    change_rows(directory, "app-catalog.jsonl", "bilibili", {"travel_method_ids": []})
    with pytest.raises(RegistrationError) as content:
        validate_catalog_files(directory)
    assert str(content.value) == (
        "APP_ROLE_BINDING_INVALID: Only function apps may bind actions or travel methods, "
        "and only content apps may be referenced by locations, goods or contents.")


@pytest.mark.parametrize("update,purchase,methods", [
    ({"travel_method_ids": ["walking"]}, ("alipay", "wechat"), {"walking": ("amap",)}),
    ({"travel_method_ids": ["bus", "walking"]}, ("alipay", "wechat"), {"walking": ("amap",), "bus": ("amap",)}),
    ({"actions": ["purchase"], "travel_method_ids": ["driving", "taxi"]}, ("alipay", "amap", "wechat"),
     {"driving": ("amap",), "taxi": ("amap",)}),
    ({"actions": ["purchase"], "travel_method_ids": []}, ("alipay", "amap", "wechat"), {}),
])
def test_function_app_may_bind_purchase_travel_methods_or_both(directory, update, purchase, methods):
    """功能 app 只有出行方式、只有购买、两者都有均合法；两个索引各按 app 身份升序，没有绑定的方式是空元组。"""
    change_rows(directory, "app-catalog.jsonl", "amap", update)
    validate_catalog_files(directory)
    catalog = load_catalog(directory, 7)
    assert dict(catalog.apps_by_action) == {"purchase": purchase}
    assert dict(catalog.apps_by_travel_method) == {
        key: methods.get(key, ()) for key in ("walking", "bus", "driving", "taxi")}


def test_travel_method_index_lists_every_binding_app_in_identity_order(directory):
    """一种出行方式绑定多个功能 app 时，索引值是这些 app 的身份升序元组；键是 walking 加每个数据集身份。"""
    change_rows(directory, "app-catalog.jsonl", "wechat", {"travel_method_ids": ["taxi", "walking"]})
    change_rows(directory, "app-catalog.jsonl", "alipay", {"travel_method_ids": ["taxi"]})
    catalog = load_catalog(directory, 7)
    assert list(catalog.apps_by_travel_method.items()) == [
        ("walking", ("amap", "wechat")), ("bus", ("amap",)), ("driving", ("amap",)),
        ("taxi", ("alipay", "amap", "wechat"))]


@pytest.mark.parametrize("value", ["boat", "Walking", "步行", "驾车", "walk", "drive"])
def test_function_app_travel_method_must_be_registered_or_walking(directory, value):
    """功能 app 只能引用文件里的出行方式身份或 walking；写名称、旧动作名或没登记的身份都指出文件、行号和所写的值。"""
    change_rows(directory, "app-catalog.jsonl", "amap", {"travel_method_ids": [value]})
    failure = refused(directory)
    assert (failure.code, failure.detail) == (
        "ENVIRONMENT_REF_UNKNOWN", f"app-catalog.jsonl:2 Unknown travel_method_id: {value}.")


def test_function_app_without_the_travel_method_file_may_only_bind_walking(bare):
    """没有出行方式文件时，夹具三种方式的身份都没有登记，walking 仍可引用。"""
    assert dict(load_catalog(bare, 7).apps_by_travel_method) == {"walking": ("amap",)}
    change_rows(bare, "app-catalog.jsonl", "amap", {"travel_method_ids": ["driving", "walking"]})
    failure = refused(bare)
    assert (failure.code, failure.detail) == (
        "ENVIRONMENT_REF_UNKNOWN", "app-catalog.jsonl:2 Unknown travel_method_id: driving.")


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
    ({"rules_version": 4}, "RULES_VERSION_MISMATCH"),
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


METHOD_ROW = ('{"exertion":"light","name":"骑车","price_per_km_fen":0,"speed_meters_per_second":4,'
              '"start_price_fen":0,"travel_method_id":"bike"}\n').encode("utf-8")
OPTIONAL_NAMES = ["callbacks.jsonl", "map.jsonl", "travel-method.jsonl"]


@pytest.mark.parametrize("name,content", [
    ("callbacks.jsonl", b""), ("map.jsonl", b""), ("map.jsonl", MAP_ROW), ("travel-method.jsonl", b""),
    ("travel-method.jsonl", METHOD_ROW)])
def test_present_optional_file_must_be_listed(bare, name, content):
    """可选文件放进目录而没有刷新清单：不被忽略，也不当作身份不符，详情是这个文件名。"""
    (bare / name).write_bytes(content)
    failure = refused(bare)
    assert (failure.code, failure.detail) == ("ENVIRONMENT_FILE_UNLISTED", name)
    refresh_manifest_identity(bare)
    assert name in [entry.path for entry in load_catalog(bare, 7).manifest.files]


@pytest.mark.parametrize("name", OPTIONAL_NAMES)
def test_listed_optional_file_that_does_not_exist_is_an_identity_mismatch(bare, name):
    rewrite_manifest(bare, lambda payload: listed(payload, record(name)))
    assert refused(bare).code == "ENVIRONMENT_IDENTITY_MISMATCH"


def test_deleting_the_listed_travel_method_file_is_an_identity_mismatch(directory):
    """夹具登记了出行方式文件；删掉文件而不刷新清单，按登记的文件不存在拒绝。"""
    (directory / "travel-method.jsonl").unlink()
    assert refused(directory).code == "ENVIRONMENT_IDENTITY_MISMATCH"


@pytest.mark.parametrize("name", ["travel_method.jsonl", "travel-methods.jsonl", "map.json", "obsolete.jsonl"])
def test_file_table_path_outside_the_closed_set_is_stale(directory, name):
    (directory / name).write_bytes(b"")
    rewrite_manifest(directory, lambda payload: listed(payload, record(name)))
    failure = refused(directory)
    assert (failure.code, failure.detail) == ("ENVIRONMENT_STALE_FILE", str(directory.resolve() / name))


@pytest.mark.parametrize("name", ["travel_method.jsonl", "callback.jsonl", "notes.txt"])
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
            payload["format_version"] = 4
        if "old_rules" in changes:
            payload["rules_version"] = 4

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


@pytest.mark.parametrize("name", OPTIONAL_NAMES)
def test_listed_optional_file_must_be_a_regular_file_not_a_link(bare, monkeypatch, name):
    from pathlib import Path

    (bare / name).write_bytes(b"")
    refresh_manifest_identity(bare)
    load_catalog(bare, 7)
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path.name == name or original(path))
    assert refused(bare).code == "ENVIRONMENT_IDENTITY_MISMATCH"


@pytest.mark.parametrize("name", OPTIONAL_NAMES)
def test_optional_name_occupied_by_a_directory_is_an_identity_mismatch(bare, name):
    """可选文件名下只要有条目就算存在；条目不是普通文件时登记了也读不出身份，刷新同样失败。"""
    (bare / name).mkdir()
    assert refused(bare).code == "ENVIRONMENT_FILE_UNLISTED"
    rewrite_manifest(bare, lambda payload: listed(payload, record(name)))
    assert refused(bare).code == "ENVIRONMENT_IDENTITY_MISMATCH"
    with pytest.raises(RegistrationError) as failure:
        refresh_manifest_identity(bare)
    assert failure.value.code == "ENVIRONMENT_IDENTITY_MISMATCH"


def methods_refusal(root, *rows):
    """把出行方式行写进夹具副本并刷新清单，加载与纯校验必须给出同一个登记失败。"""
    write_catalog_rows(root, "travel-method.jsonl", list(rows))
    return refused(root / "catalog")


@pytest.mark.parametrize("rows,code,detail", [
    ([travel_method_row("walking", "散步", 1)], "ENVIRONMENT_ID_DUPLICATE",
     "travel-method.jsonl:1 uses the reserved identity walking."),
    ([travel_method_row("bike", "骑车", 4), travel_method_row("walking", "散步", 1)], "ENVIRONMENT_ID_DUPLICATE",
     "travel-method.jsonl:2 uses the reserved identity walking."),
    ([travel_method_row("bike", "骑车", 4), travel_method_row("bike", "单车", 5)], "ENVIRONMENT_ID_DUPLICATE",
     "travel-method.jsonl:1 and travel-method.jsonl:2 share the identity bike."),
    ([travel_method_row("bike", "骑车", 4), travel_method_row("car", "开车", 9), travel_method_row("bike", "单车", 5)],
     "ENVIRONMENT_ID_DUPLICATE", "travel-method.jsonl:1 and travel-method.jsonl:3 share the identity bike."),
    ([travel_method_row("car", "开车", 9), travel_method_row("bike", "骑车", 4)], "ENVIRONMENT_KIND_INVALID",
     "travel-method.jsonl:2 Travel method rows must be sorted by travel_method_id."),
    ([travel_method_row("bike", "步行", 4)], "ENVIRONMENT_ID_DUPLICATE",
     "travel-method.jsonl:1 uses the built-in name 步行."),
    ([travel_method_row("bike", " 步 行 ", 4)], "ENVIRONMENT_ID_DUPLICATE",
     "travel-method.jsonl:1 uses the built-in name 步行."),
    ([travel_method_row("bike", "骑车", 4), travel_method_row("car", "骑车", 9)], "ENVIRONMENT_ID_DUPLICATE",
     "travel-method.jsonl:1 and travel-method.jsonl:2 share the name 骑车."),
    ([travel_method_row("bike", "City Bike", 4), travel_method_row("car", "city  bike", 9)],
     "ENVIRONMENT_ID_DUPLICATE", "travel-method.jsonl:1 and travel-method.jsonl:2 share the name City Bike."),
    ([travel_method_row("bike", "骑 车", 4), travel_method_row("car", "开车", 9), travel_method_row("ebike", "骑车", 6)],
     "ENVIRONMENT_ID_DUPLICATE", "travel-method.jsonl:1 and travel-method.jsonl:3 share the name 骑 车."),
])
def test_travel_method_rows_are_registered_with_line_numbered_refusals(bare, rows, code, detail):
    """保留身份、身份重复、未排序、保留名称、重名（只差空白或大小写也算）各报规定的登记码与带行号的详情。"""
    failure = methods_refusal(bare.parent, *rows)
    assert (failure.code, failure.detail) == (code, detail)


@pytest.mark.parametrize("rows,code,detail", [
    ([travel_method_row("walking", "步行", 1), travel_method_row("walking", "步行", 1)], "ENVIRONMENT_ID_DUPLICATE",
     "travel-method.jsonl:1 uses the reserved identity walking."),
    ([travel_method_row("car", "步行", 9), travel_method_row("bike", "骑车", 4), travel_method_row("car", "开车", 9)],
     "ENVIRONMENT_ID_DUPLICATE", "travel-method.jsonl:1 and travel-method.jsonl:3 share the identity car."),
    ([travel_method_row("car", "步行", 9), travel_method_row("bike", "步行", 4)], "ENVIRONMENT_KIND_INVALID",
     "travel-method.jsonl:2 Travel method rows must be sorted by travel_method_id."),
    ([travel_method_row("bike", "骑车", 4), travel_method_row("car", "骑车", 9), travel_method_row("ebike", "步行", 6)],
     "ENVIRONMENT_ID_DUPLICATE", "travel-method.jsonl:3 uses the built-in name 步行."),
])
def test_travel_method_checks_run_in_the_documented_order(bare, rows, code, detail):
    """保留身份先于身份重复，身份重复先于行次序，行次序先于保留名称，保留名称先于重名。"""
    failure = methods_refusal(bare.parent, *rows)
    assert (failure.code, failure.detail) == (code, detail)


def test_travel_method_name_may_equal_a_place_goods_or_app_name(bare):
    """出行方式的名称只在 detail 里解析，不要求与地点、商品、app 的名称不同。"""
    write_catalog_rows(bare.parent, "travel-method.jsonl", [
        travel_method_row("a", "公司", 4), travel_method_row("b", "薯片", 4), travel_method_row("c", "高德地图", 4)])
    validate_catalog_files(bare)
    assert [row.name for row in load_catalog(bare, 7).travel_methods.values()] == ["公司", "薯片", "高德地图"]


def test_absent_and_empty_travel_method_file_both_leave_only_walking(bare):
    """零行文件与不放文件都只有引擎自带的步行：目录里没有数据集方式，索引只有 walking 一个键。"""
    absent = load_catalog(bare, 7)
    write_catalog_rows(bare.parent, "travel-method.jsonl", [])
    assert (bare / "travel-method.jsonl").read_bytes() == b""
    empty = load_catalog(bare, 7)
    for catalog in (absent, empty):
        assert dict(catalog.travel_methods) == {}
        assert dict(catalog.apps_by_travel_method) == {"walking": ("amap",)}
    assert empty.map_sha256 == absent.map_sha256
    assert empty.manifest.catalog_content_sha256 != absent.manifest.catalog_content_sha256


@pytest.mark.parametrize("trigger,code,detail", [
    ({"class": "action", "key": "travel"}, "ENVIRONMENT_KIND_INVALID", None),
    ({"class": "action", "key": "travel", "travel_method_id": ""}, "ENVIRONMENT_KIND_INVALID", None),
    ({"class": "action", "key": "travel", "travel_method_id": None}, "ENVIRONMENT_KIND_INVALID", None),
    ({"class": "action", "key": "travel", "travel_method_id": "driving", "note": "x"}, "ENVIRONMENT_KIND_INVALID",
     None),
    ({"class": "action", "key": "purchase", "travel_method_id": "driving"}, "ENVIRONMENT_KIND_INVALID", None),
    ({"class": "action", "key": "sleep", "travel_method_id": "walking"}, "ENVIRONMENT_KIND_INVALID", None),
    ({"class": "action", "key": "interact", "travel_method_id": "walking"}, "ENVIRONMENT_KIND_INVALID", None),
    ({"class": "action", "key": "drive"}, "ENVIRONMENT_KIND_INVALID", None),
    ({"class": "action", "key": "walk"}, "ENVIRONMENT_KIND_INVALID", None),
    ({"class": "action", "key": "take_taxi"}, "ENVIRONMENT_KIND_INVALID", None),
    ({"class": "action", "key": "take_bus"}, "ENVIRONMENT_KIND_INVALID", None),
    ({"class": "action", "key": "query"}, "ENVIRONMENT_KIND_INVALID", None),
    ({"class": "action", "key": "drive", "travel_method_id": "driving"}, "ENVIRONMENT_KIND_INVALID", None),
    ({"class": "action", "key": "travel", "travel_method_id": "boat"}, "ENVIRONMENT_REF_UNKNOWN",
     "callbacks.jsonl:2 Unknown travel_method_id: boat."),
    ({"class": "action", "key": "travel", "travel_method_id": "驾车"}, "ENVIRONMENT_REF_UNKNOWN",
     "callbacks.jsonl:2 Unknown travel_method_id: 驾车."),
])
def test_callback_travel_trigger_names_exactly_one_registered_travel_method(directory, trigger, code, detail):
    """出行触发必须带出行方式，其他动作触发不得带；旧出行动作名和查询（query）都不是合法的键；未登记的身份指出文件、行号
    和值。"""
    write_catalog_rows(directory.parent, "callbacks.jsonl", [
        {"state": "stock", "initial": 1, "cap": 3, "reset_on_goods": []},
        {"rule_id": "rule", "trigger": trigger, "effect": {"type": "state_delta", "state": "stock", "delta": -1}}])
    failure = refused(directory)
    assert failure.code == code
    if detail is None:
        assert failure.detail.startswith("callbacks.jsonl:2 ")
    else:
        assert failure.detail == detail


@pytest.mark.parametrize("method", ["walking", "bus", "driving", "taxi"])
def test_callback_travel_trigger_may_name_walking_or_any_registered_method(directory, method):
    write_catalog_rows(directory.parent, "callbacks.jsonl", [
        {"state": "stock", "initial": 1, "cap": 3, "reset_on_goods": []},
        {"rule_id": "rule", "trigger": {"class": "action", "key": "travel", "travel_method_id": method},
         "effect": {"type": "state_delta", "state": "stock", "delta": -1, "once_per": "local_day"}}])
    validate_catalog_files(directory)
    trigger = load_catalog(directory, 7).callback_rules["rule"].trigger
    assert (trigger.trigger_class, trigger.key, trigger.travel_method_id) == ("action", "travel", method)


def test_callback_rule_line_number_counts_the_state_rows_before_it(bare):
    """回调规则的行号是文件里的真实行号：两条状态行之后的第二条规则在第四行。"""
    write_catalog_rows(bare.parent, "callbacks.jsonl", [
        {"state": "fuel", "initial": 1, "cap": 3, "reset_on_goods": []},
        {"state": "stock", "initial": 1, "cap": 3, "reset_on_goods": []},
        {"rule_id": "a", "trigger": {"class": "action", "key": "travel", "travel_method_id": "walking"},
         "effect": {"type": "state_delta", "state": "stock", "delta": -1}},
        {"rule_id": "b", "trigger": {"class": "action", "key": "travel", "travel_method_id": "driving"},
         "effect": {"type": "state_delta", "state": "fuel", "delta": -1}}])
    failure = refused(bare)
    assert (failure.code, failure.detail) == (
        "ENVIRONMENT_REF_UNKNOWN", "callbacks.jsonl:4 Unknown travel_method_id: driving.")


KIND_INVALID = "ENVIRONMENT_KIND_INVALID"
PREREQUISITE_REASON = ("A content prerequisite must belong to an app content and declare location kinds, "
                       "opening hours, or both.")


def set_prerequisite(directory, content_id, prerequisite, **fields):
    change_rows(directory, "content-catalog.jsonl", content_id, {"prerequisite": prerequisite, **fields})


@pytest.mark.parametrize("prerequisite,detail", [
    (None, "A declared content prerequisite must be an object."),
    ("office", "prerequisite\n"),
    (["office"], "prerequisite\n"),
    ({"location_kinds": ["office"], "place": "office"}, "prerequisite.place\n"),
    ({"location_kinds": "office"}, "prerequisite.location_kinds\n"),
    ({"location_kinds": ["office", " "]}, "prerequisite.location_kinds.1\n"),
    ({"location_kinds": ["office"], "opens": None}, "A declared opening schedule must be an object."),
    ({"opens": {}}, "prerequisite.opens.weekly\n"),
    ({"opens": [["08:00", "10:00"]]}, "prerequisite.opens\n"),
], ids=["null", "string", "array", "unknown-key", "kinds-not-array", "blank-kind", "null-opens", "opens-shape",
        "opens-not-object"])
def test_prerequisite_shape_errors_are_reported_on_their_content_line(directory, prerequisite, detail):
    """行解析阶段：前提及其成员的形状由严格行模型检查，详情以文件名和行号开头。"""
    set_prerequisite(directory, "c-article", prerequisite)
    failure = refused(directory)
    assert failure.code == KIND_INVALID
    assert failure.detail.startswith("content-catalog.jsonl:1 ") and detail in failure.detail


@pytest.mark.parametrize("content_id,prerequisite,code,detail", [
    ("c-rest", {"location_kinds": ["home"]}, "CONTENT_PREREQUISITE_INVALID", "c-rest is a location content."),
    ("c-article", {}, "CONTENT_PREREQUISITE_INVALID", "c-article declares neither location kinds nor opening hours."),
    ("c-article", {"location_kinds": []}, "CONTENT_PREREQUISITE_INVALID",
     "c-article declares neither location kinds nor opening hours."),
    ("c-article", {"location_kinds": ["office", "office"]}, "ENVIRONMENT_ID_DUPLICATE", None),
    ("c-article", {"location_kinds": ["office", "home"]}, KIND_INVALID, None),
    ("c-article", {"location_kinds": ["Office"]}, "CONTENT_BINDING_DANGLING", "c-article prerequisite names Office."),
    ("c-article", {"location_kinds": ["home", "school"]}, "CONTENT_BINDING_DANGLING",
     "c-article prerequisite names school."),
    ("c-article", {"opens": {"weekly": {"monday": []}}}, KIND_INVALID, "Opening weekdays must use mon through sun."),
    ("c-article", {"opens": {"weekly": {}, "exceptions": {"next monday": []}}}, KIND_INVALID,
     "Opening exceptions must use ISO dates."),
    ("c-article", {"opens": {"weekly": {}, "exceptions": {"20261012": []}}}, KIND_INVALID,
     "Opening exceptions must use canonical ISO dates."),
    ("c-article", {"opens": {"weekly": {"mon": [["09:00", "12:00"], ["11:00", "13:00"]]}}}, KIND_INVALID,
     "Opening intervals must be sorted and disjoint."),
    ("c-article", {"opens": {"weekly": {"mon": [["9:00", "10:00"]]}}}, KIND_INVALID,
     "Time must use a valid HH:MM value."),
], ids=["location-content", "empty-object", "empty-kinds-without-opens", "duplicate-kinds", "unsorted-kinds",
        "dangling-kind", "second-kind-dangling", "weekday-name", "exception-not-a-date", "exception-not-canonical",
        "intervals-overlap", "time-not-hhmm"])
def test_prerequisite_registration_failures_carry_their_code_and_detail(directory, content_id, prerequisite, code,
                                                                       detail):
    set_prerequisite(directory, content_id, prerequisite)
    failure = refused(directory)
    assert (failure.code, failure.detail) == (code, detail)
    if code == "CONTENT_PREREQUISITE_INVALID":
        assert failure.message == PREREQUISITE_REASON
    if code == "CONTENT_BINDING_DANGLING":
        assert failure.message == "A content names a location kind that no registered location declares."


@pytest.mark.parametrize("content_id,prerequisite,code,detail", [
    ("c-rest", {}, "CONTENT_PREREQUISITE_INVALID", "c-rest is a location content."),
    ("c-rest", {"location_kinds": ["home", "home"]}, "CONTENT_PREREQUISITE_INVALID", "c-rest is a location content."),
    ("c-article", {"location_kinds": ["school", "school"]}, "ENVIRONMENT_ID_DUPLICATE", None),
    ("c-article", {"location_kinds": ["school", "home"]}, KIND_INVALID, None),
    ("c-article", {"location_kinds": ["school"], "opens": {"weekly": {"monday": []}}}, "CONTENT_BINDING_DANGLING",
     "c-article prerequisite names school."),
], ids=["location-content-before-neither", "location-content-before-duplicate", "duplicate-before-dangling",
        "unsorted-before-dangling", "dangling-before-opening-hours"])
def test_prerequisite_checks_run_in_the_declared_order(directory, content_id, prerequisite, code, detail):
    """一个前提同时违反两条规则时报排在前面的那条：写在地点内容上、两项都没写、location_kinds 的重复与排序、
    location_kinds 悬空、opens 的时段写法。两项都没写的前提没有可查的成员，只可能与「写在地点内容上」同时出现。"""
    set_prerequisite(directory, content_id, prerequisite)
    failure = refused(directory)
    assert (failure.code, failure.detail) == (code, detail)


def test_top_level_dangling_kind_shares_the_reason_without_a_prerequisite_detail(directory):
    change_rows(directory, "content-catalog.jsonl", "c-rest", {"location_kinds": ["school"]})
    failure = refused(directory)
    assert (failure.code, failure.detail) == ("CONTENT_BINDING_DANGLING", None)
    assert failure.message == "A content names a location kind that no registered location declares."


@pytest.mark.parametrize("update,code", [
    ({"app_ids": [], "location_kinds": []}, "CONTENT_CONTAINER_MISSING"),
    ({"location_kinds": ["home"]}, "CONTENT_CONTAINER_SIDE_EXCLUSIVE"),
    ({"app_ids": ["amap"]}, "APP_ROLE_BINDING_INVALID"),
    ({"summary": "x" * 501}, "CONTENT_SUMMARY_TOO_LONG"),
    ({"summary_source": {"kind": "real", "url": None, "notice": None}}, "CONTENT_SOURCE_URL_MISSING"),
])
def test_container_reference_summary_and_source_checks_precede_the_prerequisite(directory, update, code):
    """容器缺失、两侧互斥、引用、摘要和来源都先于前提：同一行同时带着一个不成立的前提，报的仍是前面的检查。"""
    set_prerequisite(directory, "c-article", {}, **update)
    assert refused(directory).code == code


def test_prerequisite_check_precedes_the_series_check(directory):
    """同一行的前提不成立、集号又与前一集重复：先报前提。"""
    set_prerequisite(directory, "c-drama-2", {}, series={"episode": 1, "title": "测试剧集"})
    failure = refused(directory)
    assert (failure.code, failure.detail) == ("CONTENT_PREREQUISITE_INVALID",
                                              "c-drama-2 declares neither location kinds nor opening hours.")


SERIES_HOURS = {"weekly": {"mon": [["08:00", "10:00"]], "tue": [["08:00", "10:00"]]}}


@pytest.mark.parametrize("first,second", [
    ({"location_kinds": ["office"]}, None),
    (None, {"location_kinds": ["office"]}),
    ({"location_kinds": ["office"]}, {"location_kinds": ["home"]}),
    ({"location_kinds": ["office"], "opens": SERIES_HOURS}, {"location_kinds": ["office"]}),
    ({"opens": SERIES_HOURS}, {"opens": {"weekly": {"mon": [["08:00", "10:00"]]}}}),
    ({"opens": SERIES_HOURS}, {"opens": {**SERIES_HOURS, "exceptions": {"2026-10-12": []}}}),
], ids=["second-omits", "first-omits", "kinds-differ", "opens-missing", "weekly-differs", "exceptions-differ"])
def test_series_episodes_with_different_prerequisites_are_refused(directory, first, second):
    rows = catalog_rows(directory.parent, "content-catalog.jsonl")
    for content_id, prerequisite in (("c-drama-1", first), ("c-drama-2", second), ("c-drama-3", first)):
        if prerequisite is not None:
            next(row for row in rows if row["content_id"] == content_id)["prerequisite"] = prerequisite
    write_catalog_rows(directory.parent, "content-catalog.jsonl", rows)
    failure = refused(directory)
    assert (failure.code, failure.detail) == ("CONTENT_SERIES_INVALID",
                                              "测试剧集 episodes declare different prerequisites.")
    assert failure.message == ("Episodes of a series must share one container binding and one prerequisite "
                               "and have unique episode numbers.")


def test_series_episodes_with_the_same_prerequisite_in_another_spelling_load(directory):
    """键的先后不同、location_kinds 省略与空数组、exceptions 省略与写成空对象都不算差异。"""
    spellings = {
        "c-drama-1": {"location_kinds": [], "opens": SERIES_HOURS},
        "c-drama-2": {"opens": {"exceptions": {}, "weekly": dict(reversed(SERIES_HOURS["weekly"].items()))}},
        "c-drama-3": {"opens": {"weekly": SERIES_HOURS["weekly"], "exceptions": {}}, "location_kinds": []},
    }
    rows = catalog_rows(directory.parent, "content-catalog.jsonl")
    path = directory / "content-catalog.jsonl"
    for row in rows:
        if row["content_id"] in spellings:
            row["prerequisite"] = spellings[row["content_id"]]
    path.write_bytes(b"".join(json.dumps(row, ensure_ascii=False).encode("utf-8") + b"\n" for row in rows))
    refresh_manifest_identity(directory)
    assert '"exceptions": {}, "weekly": {"tue"' in path.read_text(encoding="utf-8")
    contents = load_catalog(directory, 7).contents
    assert contents["c-drama-1"].prerequisite == contents["c-drama-2"].prerequisite == contents["c-drama-3"].prerequisite
    assert contents["c-drama-1"].prerequisite.opens.weekly["tue"] == (("08:00", "10:00"),)


def test_series_binding_and_episode_number_checks_still_have_no_detail(directory):
    change_rows(directory, "content-catalog.jsonl", "c-drama-2", {"app_ids": ["douyin"]})
    assert (refused(directory).code, refused(directory).detail) == ("CONTENT_SERIES_INVALID", None)
    change_rows(directory, "content-catalog.jsonl", "c-drama-2",
                {"app_ids": ["bilibili"], "series": {"episode": 1, "title": "测试剧集"}})
    assert (refused(directory).code, refused(directory).detail) == ("CONTENT_SERIES_INVALID", None)
