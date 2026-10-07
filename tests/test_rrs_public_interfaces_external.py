"""@brief 真实目录、交换与足迹消费入口的拒绝分支和派生结果证明。"""

import json
import shutil
from pathlib import Path

import pytest

from tests.catalog.conftest import read_payload, write_payload
from tests.data_paths import ARTIFICIAL_ENVIRONMENT_ROOT
from tests.footprints.test_validation import complete_export  # noqa: F401
from worldmock.catalog.admission import validate_scenario_against_catalog
from worldmock.catalog.affordances import engine_affordances
from worldmock.catalog.errors import RegistrationError
from worldmock.catalog.identity import read_catalog_file, read_manifest_payload
from worldmock.catalog.loader import load_catalog
from worldmock.errors import IntegrityError
from worldmock.exchange.operators import evaluate_goods_receipt
from worldmock.footprints import checkpoint
from worldmock.footprints.assets import AssetStore
from worldmock.footprints.export import write_jsonl
from worldmock.footprints.models import canonical_json
from worldmock.footprints.validation import validate_export
from worldmock.life.scenario import LifeScenario
from worldmock.validation import validate_environment


@pytest.mark.parametrize("change,code", [
    ("duplicate", "ENVIRONMENT_ID_DUPLICATE"), ("unsorted", "ENVIRONMENT_KIND_INVALID"),
])
def test_catalog_manifest_rejects_duplicate_or_unsorted_file_registration(catalog_dir, change, code):
    """@brief 文件集合相同也必须保持唯一、规范发布顺序。"""
    payload = read_payload(catalog_dir, "manifest.json")
    if change == "duplicate":
        payload["files"].append(dict(payload["files"][0]))
    else:
        payload["files"].reverse()
    write_payload(catalog_dir, "manifest.json", payload, refresh=False)
    with pytest.raises(RegistrationError) as caught:
        load_catalog(catalog_dir)
    assert caught.value.code == code


def test_scenario_catalog_admission_rechecks_home_after_document_changes(loaded_catalog):
    """@brief 已解析文档的副本被改成未选住处时，公共登记入口仍拒绝。"""
    path = ARTIFICIAL_ENVIRONMENT_ROOT / "scenarios/single-person.json"
    document = LifeScenario.model_validate_json(path.read_bytes())
    validate_scenario_against_catalog(document, loaded_catalog)
    changed_agent = document.agents[0].model_copy(update={"home_location_id": "unselected-home"})
    changed = document.model_copy(update={"agents": (changed_agent, *document.agents[1:])})
    with pytest.raises(RegistrationError) as caught:
        validate_scenario_against_catalog(changed, loaded_catalog)
    assert caught.value.code == "ENVIRONMENT_REF_UNKNOWN"
    assert document.agents[0].home_location_id == "home"


def test_affordance_views_keep_fixed_met_and_action_bindings():
    view=engine_affordances()
    assert set(view.kinds)=={"wait","sleep","travel","action"}
    assert view.met_closed_keys == {"wait","sleep","travel:driving","travel:walking"}
    assert view.relief_keys == {"action"}
    assert view.relief_channel("action")=="content_rate"
    assert view.target_catalog_fields == {"action":("actions","action_id")}
    assert view.resource_claims("action",category="exercise",manner="offline_assisted")=={"exercise"}
    with pytest.raises(TypeError):
        view.kinds["action"]=view.kinds["wait"]




def test_affordance_met_lookup_requires_explicit_mode_and_fixed_category():
    view=engine_affordances()
    assert view.met_lookup_key("travel","walking")=="travel:walking"
    with pytest.raises(IntegrityError,match="registered travel mode"):
        view.met_lookup_key("travel")
    with pytest.raises(IntegrityError,match="fixed category table"):
        view.met_lookup_key("action")


@pytest.mark.parametrize("endpoint", ["origin", "destination"])
def test_catalog_graph_rejects_unknown_query_endpoint(loaded_catalog, endpoint):
    """@brief 完整图查询不能用未登记端点产生假路线。"""
    with pytest.raises(RegistrationError) as caught:
        if endpoint == "origin":
            loaded_catalog.graph.shortest_paths("unknown-place")
        else:
            loaded_catalog.graph.path("home", "unknown-place")
    assert caught.value.code == "ENVIRONMENT_REF_UNKNOWN"
    assert loaded_catalog.graph.path("home", "office") == ("home", "noodle-1", "office")


def test_catalog_file_read_reports_real_byte_read_failure(catalog_dir, monkeypatch):
    """@brief 安全路径已核对但文件打开失败时，保留故障及稳定登记码。"""
    path = catalog_dir / "goods.json"
    original = Path.read_bytes
    failure = PermissionError("Catalog file read denied")

    def read(selected):
        if selected == path:
            raise failure
        return original(selected)

    monkeypatch.setattr(Path, "read_bytes", read)
    with pytest.raises(RegistrationError) as caught:
        read_catalog_file(catalog_dir, "goods.json")
    assert caught.value.code == "ENVIRONMENT_IDENTITY_MISMATCH"
    assert caught.value.__cause__ is failure
    assert original(path)


@pytest.mark.parametrize("raw,code", [
    (b"{", "ENVIRONMENT_IDENTITY_MISMATCH"),
    (b"\xff", "ENVIRONMENT_IDENTITY_MISMATCH"),
    (b"[]", "ENVIRONMENT_KIND_INVALID"),
])
def test_catalog_manifest_reader_refuses_malformed_or_nonobject_bytes(catalog_dir, raw, code):
    """@brief 原始发布清单必须是可解码 JSON 对象，不能改变结构后继续登记。"""
    (catalog_dir / "manifest.json").write_bytes(raw)
    with pytest.raises(RegistrationError) as caught:
        read_manifest_payload(catalog_dir)
    assert caught.value.code == code
    assert (catalog_dir / "manifest.json").read_bytes() == raw




@pytest.mark.parametrize("quantity",[0,-1,0.5,True,float("inf")])
def test_goods_receipt_refuses_non_positive_integer_quantity(quantity,caplog):
    assert evaluate_goods_receipt("meal",1)==600
    with pytest.raises(ValueError,match="quantity must be a positive integer"):
        evaluate_goods_receipt("meal",quantity)
    assert "quantity must be a positive integer" in caplog.text


def test_workspace_restore_refuses_a_file_created_after_destination_preflight(tmp_path, monkeypatch):
    """@brief 展开中出现真实目标文件时，恢复不能覆盖另一写入者的内容。"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a.json").write_bytes(b"first snapshot")
    (workspace / "b.json").write_bytes(b"second snapshot")
    assets = AssetStore(tmp_path / "assets")
    reference = checkpoint.snapshot_directory(workspace, assets)
    destination = tmp_path / "restored"
    write = checkpoint.atomic_write

    def concurrent_write(path, content):
        write(path, content)
        if path.name == "a.json":
            (destination / "b.json").write_bytes(b"external write")

    monkeypatch.setattr(checkpoint, "atomic_write", concurrent_write)
    with pytest.raises(IntegrityError, match="unsafe member"):
        checkpoint.restore_directory(reference, assets, destination)
    assert (destination / "a.json").read_bytes() == b"first snapshot"
    assert (destination / "b.json").read_bytes() == b"external write"
    assert assets.read(reference)


@pytest.mark.parametrize("artifact,filename,message", [
    ("verified_experiences", "verified-experiences.jsonl", "verified experiences disagree"),
    ("purchases", "purchases.jsonl", "purchase facts disagree"),
])
def test_export_rejects_rehashed_fact_rows_without_matching_committed_events(
    complete_export, artifact, filename, message,
):
    """@brief 重写派生事实和合法摘要也不能伪造已经发生的体验或购买。"""
    _, events, _ = validate_export(complete_export)
    manifest_path = complete_export / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["artifacts"][artifact] = write_jsonl(
        complete_export / filename, [{"source_event_id": events[0]["event_id"], "forged": True}],
    )
    manifest_path.write_bytes(canonical_json(manifest) + b"\n")
    with pytest.raises(IntegrityError, match=message):
        validate_export(complete_export)


@pytest.fixture
def selected_environment(tmp_path):
    """@brief 全部入口反例使用独立的真实人工环境副本。"""
    destination = tmp_path / "environment"
    shutil.copytree(ARTIFICIAL_ENVIRONMENT_ROOT, destination)
    return destination


def test_environment_validation_returns_all_authentic_scenario_and_corpus_identities(selected_environment):
    """@brief 直接消费者入口返回真实目录身份、语料数量和完整场景集合。"""
    result = validate_environment(selected_environment)
    expected = sorted(path.relative_to(selected_environment).as_posix()
                      for path in (selected_environment / "scenarios").rglob("*.json"))
    assert result["valid"] is True
    assert result["environment_id"] == "artificial-test"
    assert result["scenarios"] == expected and expected
    assert set(result)=={"valid","environment_id","environment_version","scenarios"}


def test_environment_validation_reports_catalog_failure_for_all_scenarios(selected_environment):
    (selected_environment/"catalog/apps.json").unlink()
    result=validate_environment(selected_environment)
    assert result["valid"] is False
    catalog_errors=[error for error in result["errors"] if error["path"]=="catalog"]
    assert catalog_errors and all(error["code"]=="ENVIRONMENT_IDENTITY_MISMATCH" for error in catalog_errors)
    expected={path.relative_to(selected_environment).as_posix()
              for path in (selected_environment/"scenarios").rglob("*.json")}
    assert expected <= {error["path"] for error in result["errors"]}
    assert result["scenarios"]==[]


def test_environment_validation_keeps_valid_scenarios_after_bad_pin(selected_environment):
    """@brief 单个场景的环境钉失败不能抹掉其余真实有效场景。"""
    scenario = selected_environment / "scenarios/single-person.json"
    document = json.loads(scenario.read_bytes())
    document["environment"]["environment_version"] = "wrong-published-version"
    scenario.write_bytes(canonical_json(document) + b"\n")
    result = validate_environment(selected_environment)
    assert result["valid"] is False
    assert result["errors"] == [{"path": "scenarios/single-person.json", "code": "ENVIRONMENT_PIN_MISMATCH",
                                 "reason": "ENVIRONMENT_PIN_MISMATCH: The scenario pins an environment "
                                           "that is not the one being loaded."}]
    expected = sorted(path.relative_to(selected_environment).as_posix()
                      for path in (selected_environment / "scenarios").rglob("*.json") if path != scenario)
    assert result["scenarios"] == expected and expected


def test_environment_validation_refuses_a_publish_directory_without_scenarios(selected_environment):
    """@brief 只有资源没有场景的目录不能被登记为可运行环境。"""
    shutil.rmtree(selected_environment / "scenarios")
    result = validate_environment(selected_environment)
    assert result == {"valid": False, "errors": [{"path": "scenarios", "code": "IntegrityError",
                                                "reason": "The environment contains no scenarios"}],
                      "scenarios": []}
