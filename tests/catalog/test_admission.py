"""@brief 所有目录登记错误码的真实文件最小反例。"""

import json

import pytest

from tests.catalog.conftest import OBJECT_COUNTEREXAMPLES, edit_row, read_payload, write_payload
from worldmock.catalog.errors import REGISTRATION_REASONS, RegistrationError
from worldmock.catalog.identity import refresh_manifest_identity
from worldmock.catalog.loader import load_catalog


@pytest.mark.parametrize("filename,identity,updates,code", OBJECT_COUNTEREXAMPLES)

def test_registration_rejects_minimal_object_counterexample(catalog_dir, filename, identity, updates, code):
    """@brief 每条对象规则通过生产发布与加载边界给出稳定码及英文原因。"""
    edit_row(catalog_dir, filename, identity, updates)
    with pytest.raises(RegistrationError) as caught:
        load_catalog(catalog_dir)
    assert caught.value.code == code
    assert caught.value.message == REGISTRATION_REASONS[code]


def test_registration_rejects_missing_food_binding(catalog_dir):
    """@brief 没有绑定字段时不能补齐默认店铺。"""
    payload = read_payload(catalog_dir, "foods.json")
    payload["foods"][0].pop("store_ids")
    write_payload(catalog_dir, "foods.json", payload)
    with pytest.raises(RegistrationError, match="FOOD_BINDING_MISSING"):
        load_catalog(catalog_dir)


@pytest.mark.parametrize("field,value", [("rules_version", 2), ("size_bytes", -1)])
def test_registration_rejects_manifest_values(catalog_dir, field, value):
    """@brief 清单规则版本和数字域不能越界。"""
    payload = read_payload(catalog_dir, "manifest.json")
    if field == "size_bytes":
        payload["files"][0][field] = value
    else:
        payload[field] = value
    write_payload(catalog_dir, "manifest.json", payload, refresh=field == "rules_version")
    code = "RULES_VERSION_MISMATCH" if field == "rules_version" else "ENVIRONMENT_NUMBER_INVALID"
    with pytest.raises(RegistrationError, match=code):
        load_catalog(catalog_dir)


@pytest.mark.parametrize("name", [
    "category-contract.json", "exchange-rules.json", "objects.jsonl", "resources.jsonl", "rules/travel-time.toml",
])
def test_registration_rejects_every_retired_file(catalog_dir, name):
    """@brief 清单未列出的历史文件不能被加载器静默忽略。"""
    (catalog_dir / name).write_text("{}\n")
    with pytest.raises(RegistrationError, match="ENVIRONMENT_STALE_FILE"):
        load_catalog(catalog_dir)


def test_registration_rejects_missing_manifest_file_entry(catalog_dir):
    """@brief 清单必须恰好登记所有必需文件。"""
    payload = read_payload(catalog_dir, "manifest.json")
    payload["files"].pop()
    write_payload(catalog_dir, "manifest.json", payload, refresh=False)
    with pytest.raises(RegistrationError, match="ENVIRONMENT_FILE_UNLISTED"):
        load_catalog(catalog_dir)


@pytest.mark.parametrize("change", ["bytes", "digest", "size", "missing", "manifest_hash"])
def test_registration_rejects_file_identity_drift(catalog_dir, change):
    """@brief 真实字节、大小、摘要或文件集合漂移均不能继续登记。"""
    payload = read_payload(catalog_dir, "manifest.json")
    if change == "bytes":
        with (catalog_dir / "foods.json").open("ab") as handle:
            handle.write(b" ")
    elif change == "missing":
        (catalog_dir / "foods.json").unlink()
    else:
        if change == "manifest_hash":
            payload["catalog_content_sha256"] = "0" * 64
        elif change == "digest":
            payload["files"][0]["sha256"] = "0" * 64
        else:
            payload["files"][0]["size_bytes"] += 1
        write_payload(catalog_dir, "manifest.json", payload, refresh=False)
    with pytest.raises(RegistrationError, match="ENVIRONMENT_IDENTITY_MISMATCH"):
        load_catalog(catalog_dir)


def test_registration_rejects_unbound_app(catalog_dir):
    """@brief 应用必须有店铺或内容绑定。"""
    payload = read_payload(catalog_dir, "apps.json")
    payload["apps"].append({"app_id": "z-unbound", "name": "空应用"})
    write_payload(catalog_dir, "apps.json", payload)
    with pytest.raises(RegistrationError, match="APP_UNBOUND"):
        load_catalog(catalog_dir)


def test_registration_rejects_duplicate_identity(catalog_dir):
    """@brief 建索引前捕获重复身份，不能被字典构造覆盖。"""
    payload = read_payload(catalog_dir, "apps.json")
    payload["apps"].append(dict(payload["apps"][0]))
    write_payload(catalog_dir, "apps.json", payload)
    with pytest.raises(RegistrationError, match="ENVIRONMENT_ID_DUPLICATE"):
        load_catalog(catalog_dir)


@pytest.mark.parametrize("case", ["self", "duplicate", "unknown", "zero", "unsorted", "nodes", "disconnected"])
def test_registration_rejects_invalid_map(catalog_dir, case):
    """@brief 自环、重复、未知端点、零长、乱序与不连通各由真实加载拒绝。"""
    graph = read_payload(catalog_dir, "map.json")
    edge = graph["edges"][0]
    if case == "self":
        edge["b"] = edge["a"]
    elif case == "duplicate":
        graph["edges"].append(dict(edge))
    elif case == "unknown":
        edge["b"] = "unknown"
    elif case == "zero":
        edge["length_m"] = 0
    elif case == "unsorted":
        graph["edges"].reverse()
    elif case == "nodes":
        graph["nodes"].pop()
    else:
        graph["edges"] = [edge]
    write_payload(catalog_dir, "map.json", graph)
    code = "MAP_DISCONNECTED" if case == "disconnected" else "MAP_EDGE_INVALID"
    with pytest.raises(RegistrationError, match=code):
        load_catalog(catalog_dir)


def test_registration_rejects_too_small_map(catalog_dir):
    """@brief 单节点图不能登记为可出行地图。"""
    graph = read_payload(catalog_dir, "map.json")
    graph.update(nodes=["home"], edges=[])
    write_payload(catalog_dir, "map.json", graph)
    with pytest.raises(RegistrationError, match="MAP_TOO_SMALL"):
        load_catalog(catalog_dir)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_registration_rejects_nonfinite_work_target(catalog_dir, value):
    """@brief 即使 JSON 解码得到非有限浮点值，主题也不能登记。"""
    payload = read_payload(catalog_dir, "work_topics.json")
    payload["work_topics"][0]["target_hours"] = value
    (catalog_dir / "work_topics.json").write_text(json.dumps(payload) + "\n")
    refresh_manifest_identity(catalog_dir)
    with pytest.raises(RegistrationError, match="ENVIRONMENT_NUMBER_INVALID"):
        load_catalog(catalog_dir)
