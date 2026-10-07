"""@brief 环境验证真实场景、发布地图、包资源和目录身份的命令验收。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from datetime import datetime, timedelta

import pytest

from tests.catalog.conftest import OBJECT_COUNTEREXAMPLES, edit_row, read_payload, write_payload
from tests.data_paths import ARTIFICIAL_ENVIRONMENT_ROOT, ENVIRONMENT_ROOT
from tests.test_cli import _command
from worldmock.catalog.errors import REGISTRATION_REASONS


@pytest.fixture
def published_environment(tmp_path) -> Path:
    """@brief 复制实际人工发布环境，各个反例只改自己的隔离副本。"""
    target = tmp_path / "environment"
    shutil.copytree(ARTIFICIAL_ENVIRONMENT_ROOT, target)
    return target


def _write_document(path: Path, document: dict) -> None:
    """@brief 反例保持与生产发布相同的公共 JSON 编码和换行。"""
    from worldmock.footprints.models import canonical_json

    path.write_bytes(canonical_json(document) + b"\n")


def _refresh_catalog(root: Path) -> None:
    """@brief 用真实发布入口重新钉文件身份，让反例触达目标行为校验。"""
    from worldmock.catalog.identity import refresh_manifest_identity

    refresh_manifest_identity(root / "catalog")



def _assert_registration_error(result, code: str, path: str = "catalog") -> None:
    """@brief 命令必须非零退出并公开真实路径、稳定码和对应英文原因。"""
    assert result.returncode == 1, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["valid"] is False
    matching = [error for error in report["errors"] if error["path"] == path and error["code"] == code]
    assert len(matching) == 1, report
    error = matching[0]
    assert set(error) == {"path", "code", "reason"}
    assert error["reason"].startswith(f"{code}: {REGISTRATION_REASONS[code]}")


def test_validate_environment_command_accepts_the_actual_artificial_environment() -> None:
    """@brief 人工环境的真实目录、地图和全部场景完整加载后命令成功。"""
    result = _command("validate", "--environment", ENVIRONMENT_ROOT)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    expected = sorted(path.relative_to(ENVIRONMENT_ROOT).as_posix()
                      for path in (ENVIRONMENT_ROOT / "scenarios").rglob("*.json"))
    assert report["valid"] is True and report["scenarios"] == expected and expected
    assert set(report) == {"valid", "environment_id", "environment_version", "scenarios"}


def test_validate_environment_rejects_map_bytes_even_when_manifest_is_republished(
    published_environment, capsys,
) -> None:
    """@brief 运行加载允许结构有效的图，但环境验证拒绝无法重生成的发布字节。"""
    root = published_environment
    path = root / "catalog/map.json"
    document = json.loads(path.read_bytes())
    document["edges"][0]["length_m"] += 1
    _write_document(path, document)
    _refresh_catalog(root)
    from worldmock.catalog.loader import load_catalog

    load_catalog(root / "catalog")
    from worldmock.cli import main

    assert main(["validate", "--environment", str(root)]) == 1
    report = json.loads(capsys.readouterr().out)
    error = next(error for error in report["errors"] if error["path"] == "catalog")
    assert set(error) == {"path", "code", "reason"}
    assert error["code"] == "MAP_NOT_REGENERATED"
    assert error["reason"] == f"MAP_NOT_REGENERATED: {REGISTRATION_REASONS['MAP_NOT_REGENERATED']}"


@pytest.mark.parametrize("relative", ["affordances.toml", "rules/motivation.toml", "rules/persona-mapping.toml"])
def test_validate_environment_rejects_retired_rule_copies(published_environment, relative) -> None:
    """@brief 发布目录不接受已经退役的规则副本。"""
    path = published_environment / "catalog" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("retired = true\n", encoding="utf-8")
    result = _command("validate", "--environment", published_environment)
    assert result.returncode == 1 and "ENVIRONMENT_STALE_FILE" in result.stdout + result.stderr


def test_validate_environment_ignores_unread_extra_root_directories(published_environment) -> None:
    """@brief 环境根的其他目录不被读取或校验。"""
    path = published_environment / "corpora"
    path.mkdir()
    (path / "manifest.json").write_bytes(b"invalid catalog-independent bytes")
    result = _command("validate", "--environment", published_environment)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "corpus_entries" not in json.loads(result.stdout)


def test_validate_environment_checks_every_scenario_after_a_scenario_failure(published_environment) -> None:
    """@brief 两个独立场景的环境钉错误均被报告，其余合法场景继续完成加载。"""
    root = published_environment
    scenarios = sorted((root / "scenarios").glob("*.json"))
    assert len(scenarios) >= 3
    for path in scenarios[:2]:
        document = json.loads(path.read_bytes())
        document["environment"]["environment_version"] = "changed"
        _write_document(path, document)
    result = _command("validate", "--environment", root)
    assert result.returncode == 1, result.stdout + result.stderr
    report = json.loads(result.stdout)
    for path in scenarios[:2]:
        relative = path.relative_to(root).as_posix()
        assert any(error["path"] == relative and error["code"] == "ENVIRONMENT_PIN_MISMATCH"
                   for error in report["errors"])
    assert scenarios[2].relative_to(root).as_posix() in report["scenarios"]


def test_validate_environment_rejects_no_scenarios(published_environment) -> None:
    """@brief 缺少真实场景的发布目录不能作为有效环境通过。"""
    root = published_environment
    shutil.rmtree(root / "scenarios")
    result = _command("validate", "--environment", root)
    assert result.returncode == 1 and "The environment contains no scenarios" in result.stdout + result.stderr


@pytest.mark.parametrize("filename,identity,updates,code", OBJECT_COUNTEREXAMPLES)
def test_validate_environment_command_rejects_real_registration_counterexamples(
    published_environment, filename, identity, updates, code,
) -> None:
    """@brief 对象登记的共享真实反例在完整命令边界同样非零退出并打印稳定码。"""
    edit_row(published_environment / "catalog", filename, identity, updates)
    result = _command("validate", "--environment", published_environment)
    _assert_registration_error(result, code)


@pytest.mark.parametrize("case,code", [
    ("rules_version", "RULES_VERSION_MISMATCH"), ("retired", "ENVIRONMENT_STALE_FILE"),
    ("unlisted", "ENVIRONMENT_FILE_UNLISTED"), ("identity", "ENVIRONMENT_IDENTITY_MISMATCH"),
    ("unbound", "APP_UNBOUND"), ("small_map", "MAP_TOO_SMALL"),
    ("self_edge", "MAP_EDGE_INVALID"), ("disconnected", "MAP_DISCONNECTED"),
])
def test_validate_environment_command_rejects_manifest_and_graph_counterexamples(
    published_environment, case, code,
) -> None:
    """@brief 发布身份、旧文件和图结构反例通过真实 CLI 逐项拒绝。"""
    directory = published_environment / "catalog"
    if case in {"rules_version", "unlisted"}:
        manifest = read_payload(directory, "manifest.json")
        if case == "rules_version":
            manifest["rules_version"] = 1
        else:
            manifest["files"].pop()
        write_payload(directory, "manifest.json", manifest, refresh=False)
    elif case == "retired":
        (directory / "category-contract.json").write_text("{}\n")
    elif case == "identity":
        path = directory / "goods.json"
        path.write_bytes(path.read_bytes() + b" ")
    elif case == "unbound":
        apps = read_payload(directory, "apps.json")
        apps["apps"].append({"app_id": "z-unbound", "name": "空应用", "purposes": [], "payment_app_ids": []})
        write_payload(directory, "apps.json", apps)
    else:
        graph = read_payload(directory, "map.json")
        if case == "small_map":
            graph.update(nodes=["home"], edges=[])
        elif case == "self_edge":
            graph["edges"][0]["b"] = graph["edges"][0]["a"]
        else:
            graph["edges"] = graph["edges"][:1]
        write_payload(directory, "map.json", graph)
    result = _command("validate", "--environment", published_environment)
    _assert_registration_error(result, code)


@pytest.mark.parametrize("case,code", [
    ("pin", "ENVIRONMENT_PIN_MISMATCH"), ("topic", "WORK_CONTENT_UNKNOWN"),
    ("merchant", "MERCHANT_ACCOUNT_MISSING"), ("schedule", "WORK_SCHEDULE_REQUIRED"),
])
def test_validate_environment_command_rejects_real_scenario_references(published_environment, case, code) -> None:
    """@brief 场景钉、工作内容、商家和工作安排由真实场景加载器拒绝。"""
    path = published_environment / "scenarios/single-person.json"
    document = json.loads(path.read_bytes())
    if case == "pin":
        document["environment"]["environment_id"] = "changed"
    elif case == "topic":
        document["agents"][0]["work_content_ids"] = ["unknown"]
    elif case == "merchant":
        document["accounts"] = [row for row in document["accounts"] if row["role"] != "merchant:cafe"]
    else:
        document["agents"][0]["work_schedule"] = None
    _write_document(path, document)
    result = _command("validate", "--environment", published_environment)
    _assert_registration_error(result, code, "scenarios/single-person.json")


@pytest.mark.parametrize("case,code", [
    ("no_action_content", "ACTION_CONTENT_EMPTY"),
    ("no_work_content", "WORK_CONTENT_REQUIRED"),
    ("short_work_content", "WORK_CONTENT_INSUFFICIENT"),
])
def test_validate_environment_command_rejects_missing_action_and_work_content(published_environment, case, code):
    """@brief 内容供给与完整班次不足的剩余登记码也必须通过真实命令边界拒绝。"""
    if case == "no_action_content":
        directory = published_environment / "catalog"
        document = read_payload(directory, "contents.json")
        document["contents"] = [row for row in document["contents"] if row["content_id"] != "c-run"]
        write_payload(directory, "contents.json", document)
        path = "catalog"
    else:
        path = "scenarios/single-person.json"
        destination = published_environment / path
        document = json.loads(destination.read_bytes())
        if case == "no_work_content":
            document["agents"][0]["work_content_ids"] = []
        else:
            document["end_at"] = (datetime.fromisoformat(document["end_at"]) + timedelta(days=1)).isoformat()
        _write_document(destination, document)
    result = _command("validate", "--environment", published_environment)
    _assert_registration_error(result, code, path)
