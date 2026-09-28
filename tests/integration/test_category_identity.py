"""@brief 品类身份经真实运行、恢复和导出保持冻结的集成证据。"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from worldmock.categories import refresh_manifest_identity
from worldmock.errors import IntegrityError
from worldmock.footprints.checkpoint import snapshot_directory
from worldmock.footprints.export import export_run
from worldmock.life.engine import LifeEngine
from worldmock.life.models import CounterfactualIntervention, RunIdentity
from worldmock.life.semantics import load_runtime_scenario
from worldmock.runtime import (
    ResumeRequest,
    _model_identity,
    _prepare_resume,
    runtime_code_sha256,
    stage_model_identity,
)
from tests.integration.as2_fixtures import use_fake_as2
from tests.life.conftest import advance_next, stage_day_plan, submit_round, start_request

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _locked_fake_as2(monkeypatch, tmp_path_factory):
    """运行与恢复的代码身份解析到临时 AS2 包并以其摘要为锁定值，不依赖本机实际安装的 AS2。"""
    return use_fake_as2(monkeypatch, tmp_path_factory.mktemp("as2-site"))


def _fixture(tmp_path):
    project = tmp_path / "project"
    shutil.copytree(ROOT / "catalog", project / "catalog")
    scenarios = project / "scenarios"
    scenarios.mkdir()
    document = json.loads((ROOT / "scenarios/multi-person-half-day.json").read_text(encoding="utf-8"))
    document["corpus_root"] = str(ROOT / "corpora")
    path = scenarios / "scene.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path, project / "catalog"


def _create(path, destination):
    """创建具备正式模型与阶段身份的运行，允许恢复继续验证品类身份。"""
    semantics, _ = load_runtime_scenario(path)
    scenario = semantics.scenario
    identity = RunIdentity(
        run_id="category-identity", source_group_id="category-identity-group",
        agent_ids=tuple(agent.agent_id for agent in scenario.agents),
        start_at=scenario.start_at.isoformat(), end_at=scenario.end_at.isoformat(),
        timezone=scenario.timezone, model_identity=_model_identity(), code_sha256=runtime_code_sha256(ROOT),
    )
    return LifeEngine.create(path, destination, identity, stage_identity=stage_model_identity())


def _change_category(directory):
    path = directory / "category-contract.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["categories"][-1]["name"] += " revised"
    path.write_text(json.dumps(document), encoding="utf-8")
    refresh_manifest_identity(directory)


def _hash_files(directory):
    return {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in directory.rglob("*") if path.is_file()}


def _expected_identity(directory):
    """直接对照实际文件字节与已经加载验证的目录清单，不复用运行身份提取函数。"""
    contract_bytes = (directory / "category-contract.json").read_bytes()
    rule_bytes = (directory / "exchange-rules.json").read_bytes()
    manifest = json.loads((directory / "manifest.json").read_bytes())
    return {
        "category_contract_version": json.loads(contract_bytes)["contract_version"],
        "category_contract_sha256": hashlib.sha256(contract_bytes).hexdigest(),
        "exchange_rules_sha256": hashlib.sha256(rule_bytes).hexdigest(),
        "catalog_content_sha256": manifest["catalog_content_sha256"],
    }


def _checkpoint(engine, tmp_path):
    snapshots = {"life": engine.store.assets.put_json(
        {"state_version": engine.state.version}, {"kind": "life_checkpoint"})}
    for agent in engine.scenario.agents:
        workspace = tmp_path / f"agent-{agent.agent_id}"
        workspace.mkdir()
        (workspace / "AGENT.json").write_text(json.dumps({
            "schema_version": 1, "agent_class": "LifeAgent", "agent_id": agent.agent_id,
            "initialized_at": None, "profile": {"id": agent.agent_id, "same_second_continuation": True},
        }), encoding="utf-8")
        (workspace / "config.json").write_text(json.dumps({
            "enable_memory": True, "max_react_turns": 16,
        }), encoding="utf-8")
        snapshots[f"agent:{agent.agent_id}"] = snapshot_directory(workspace, engine.store.assets)
    return engine.store.checkpoint(engine.store.state, snapshots)


def test_running_semantics_and_export_keep_original_category_identity(tmp_path):
    path, directory = _fixture(tmp_path)
    engine = _create(path, tmp_path / "run")
    try:
        expected = _expected_identity(directory)
        assert {key: engine.store.manifest[key] for key in expected} == expected
        reopened = LifeEngine.open(path, tmp_path / "run")
        try:
            assert {key: reopened.store.manifest[key] for key in expected} == expected
        finally:
            reopened.close()
        names = {key: item.name for key, item in engine.semantics.catalog.category_contract.categories.items()}
        for agent_id in engine.state.agents:
            stage_day_plan(engine, agent_id)
        submit_round(engine, {agent_id: ("start_activity", start_request(
            targets=["自己"], actions=["interact"], ways=["等待"])) for agent_id in engine.state.agents}, "wait")
        advance_next(engine)
        cutoff = engine.store.last_event_seq
        first = export_run(engine.store, cutoff, tmp_path / "first-export")
        assert {key: first["frozen_run_manifest"][key] for key in expected} == expected
        frozen_bytes = _hash_files(tmp_path / "first-export")
        _change_category(directory)
        load_runtime_scenario(path)
        assert _expected_identity(directory) != expected
        assert {key: item.name for key, item in engine.semantics.catalog.category_contract.categories.items()} == names
        second = export_run(engine.store, cutoff, tmp_path / "second-export")
        assert first["frozen_run_manifest"] == second["frozen_run_manifest"] == engine.store.manifest
        assert _hash_files(tmp_path / "first-export") == frozen_bytes
    finally:
        engine.close()


@pytest.mark.parametrize("refresh", [False, True])
def test_open_rejects_same_version_changed_content_without_mutating_run(tmp_path, refresh):
    path, directory = _fixture(tmp_path)
    run = tmp_path / "run"
    _create(path, run).close()
    contract = directory / "category-contract.json"
    contract.write_text(contract.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    if refresh:
        refresh_manifest_identity(directory)
    before = _hash_files(run)
    error = "Runtime category contract identity mismatch" if refresh else "CATALOG_CONTENT_IDENTITY_MISMATCH"
    with pytest.raises(IntegrityError, match=error):
        LifeEngine.open(path, run)
    assert _hash_files(run) == before


@pytest.mark.parametrize("counterfactual", [False, True])
@pytest.mark.parametrize("changed", [False, True])
def test_resume_and_replay_bind_original_identity_before_publishing_branch(tmp_path, counterfactual, changed):
    path, directory = _fixture(tmp_path)
    run, destination = tmp_path / "run", tmp_path / "branch"
    engine = _create(path, run)
    try:
        expected = _expected_identity(directory)
        assert {key: engine.store.manifest[key] for key in expected} == expected
        checkpoint = _checkpoint(engine, tmp_path)
        parent_manifest = engine.store.manifest
    finally:
        engine.close()
    if changed:
        _change_category(directory)
    request = ResumeRequest(ROOT, path, run, checkpoint.checkpoint_id, destination)
    intervention = CounterfactualIntervention(
        kind="set_balance_fen", agent_id=1, balance_fen=0,
        hypothesis="Available funds influence purchase decisions.", source_event_ids=("source-event",),
    ) if counterfactual else None
    if changed:
        before = _hash_files(run)
        with pytest.raises(IntegrityError, match="Runtime category contract identity mismatch"):
            _prepare_resume(request, intervention)
        assert not destination.exists()
        assert not list(tmp_path.glob(".worldmock-resume-*"))
        assert _hash_files(run) == before
    else:
        prepared = _prepare_resume(request, intervention)
        manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
        assert prepared.workspace_mode == "restored"
        assert {key: manifest[key] for key in expected} == expected
        assert manifest["parent_checkpoint_id"] == checkpoint.checkpoint_id
        assert manifest["run_id"] != parent_manifest["run_id"]
        branch = LifeEngine.open(path, destination)
        try:
            assert {key: branch.store.manifest[key] for key in expected} == expected
            for agent_id in branch.state.agents:
                stage_day_plan(branch, agent_id)
            submit_round(branch, {agent_id: ("start_activity", start_request(
                targets=["自己"], actions=["interact"], ways=["等待"])) for agent_id in branch.state.agents},
                "branch-wait")
            advance_next(branch)
            exported = export_run(branch.store, branch.store.last_event_seq, tmp_path / "branch-export")
            assert {key: exported["frozen_run_manifest"][key] for key in expected} == expected
        finally:
            branch.close()


@pytest.mark.parametrize("field", ["category_contract_version", "category_contract_sha256",
                                   "exchange_rules_sha256", "catalog_content_sha256"])
def test_open_rejects_missing_category_identity_fields(tmp_path, field):
    path, _ = _fixture(tmp_path)
    run = tmp_path / "run"
    _create(path, run).close()
    manifest_path = run / "manifest.json"
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    document.pop(field)
    manifest_path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(IntegrityError, match="Runtime category contract identity mismatch"):
        LifeEngine.open(path, run)
