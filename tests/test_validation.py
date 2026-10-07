"""@brief 环境验证真实场景、发布地图、包资源和语料身份的命令验收。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tests.catalog.conftest import OBJECT_COUNTEREXAMPLES, edit_row, read_payload, write_payload
from tests.data_paths import ENVIRONMENT_ROOT
from tests.test_cli import _command


@pytest.fixture
def published_environment(tmp_path) -> Path:
    """@brief 复制实际人工发布环境，各个反例只改自己的隔离副本。"""
    target = tmp_path / "environment"
    shutil.copytree(ENVIRONMENT_ROOT, target)
    return target


def _write_document(path: Path, document: dict) -> None:
    """@brief 反例保持与生产发布相同的公共 JSON 编码和换行。"""
    from worldmock.footprints.models import canonical_json

    path.write_bytes(canonical_json(document) + b"\n")


def _refresh_catalog(root: Path) -> None:
    """@brief 用真实发布入口重新钉文件身份，让反例触达目标行为校验。"""
    from worldmock.catalog.identity import refresh_manifest_identity

    refresh_manifest_identity(root / "catalog")


def test_validate_environment_command_accepts_the_actual_artificial_environment() -> None:
    """@brief 人工环境的真实目录、地图、语料和全部场景完整加载后命令成功。"""
    result = _command("validate", "--environment", ENVIRONMENT_ROOT)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    expected = sorted(path.relative_to(ENVIRONMENT_ROOT).as_posix()
                      for path in (ENVIRONMENT_ROOT / "scenarios").rglob("*.json"))
    assert report["valid"] is True and report["scenarios"] == expected and expected
    assert report["corpus_entries"] > 0


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
    assert "MAP_NOT_REGENERATED" in capsys.readouterr().out


@pytest.mark.parametrize("relative", ["affordances.toml", "rules/motivation.toml", "rules/persona-mapping.toml"])
def test_validate_environment_rejects_republished_engine_rule_byte_changes(published_environment, relative) -> None:
    """@brief 重新发布清单也不能改变环境副本与引擎包资源的逐字节身份合同。"""
    root = published_environment
    path = root / "catalog" / relative
    path.write_bytes(path.read_bytes() + b"\n")
    _refresh_catalog(root)
    result = _command("validate", "--environment", root)
    assert result.returncode == 1 and "ENVIRONMENT_IDENTITY_MISMATCH" in result.stdout + result.stderr


def test_validate_environment_reports_corpus_failure_for_every_affected_scenario(published_environment) -> None:
    """@brief 语料漂移与受影响的全部场景都进入失败输出，不在第一处失败后停止。"""
    root = published_environment
    corpus = root / "corpora/lifestyle/items.json"
    corpus.write_bytes(corpus.read_bytes() + b" ")
    result = _command("validate", "--environment", root)
    assert result.returncode == 1, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert "Corpus entry identity mismatch" in result.stdout + result.stderr
    for path in sorted((root / "scenarios").glob("*.json")):
        relative = path.relative_to(root).as_posix()
        assert any(error["path"] == relative for error in report["errors"])


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
    assert result.returncode == 1 and code in result.stdout + result.stderr


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
            manifest["rules_version"] = 2
        else:
            manifest["files"].pop()
        write_payload(directory, "manifest.json", manifest, refresh=False)
    elif case == "retired":
        (directory / "category-contract.json").write_text("{}\n")
    elif case == "identity":
        path = directory / "foods.json"
        path.write_bytes(path.read_bytes() + b" ")
    elif case == "unbound":
        apps = read_payload(directory, "apps.json")
        apps["apps"].append({"app_id": "z-unbound", "name": "空应用"})
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
    assert result.returncode == 1 and code in result.stdout + result.stderr


@pytest.mark.parametrize("case,code", [
    ("pin", "ENVIRONMENT_PIN_MISMATCH"), ("topic", "WORK_TOPIC_UNKNOWN"),
    ("merchant", "MERCHANT_ACCOUNT_MISSING"), ("schedule", "WORK_SCHEDULE_REQUIRED"),
])
def test_validate_environment_command_rejects_real_scenario_references(published_environment, case, code) -> None:
    """@brief 场景钉、主题、商家和工作安排由真实场景加载器拒绝。"""
    path = published_environment / "scenarios/single-person.json"
    document = json.loads(path.read_bytes())
    if case == "pin":
        document["environment"]["environment_id"] = "changed"
    elif case == "topic":
        document["agents"][0]["work_topic_ids"] = ["unknown"]
    elif case == "merchant":
        document["accounts"] = [row for row in document["accounts"] if row["role"] != "merchant:cafe"]
    else:
        document["agents"][0]["work_schedule"] = None
    _write_document(path, document)
    result = _command("validate", "--environment", published_environment)
    assert result.returncode == 1 and code in result.stdout + result.stderr
