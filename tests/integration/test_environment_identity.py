"""@brief 品类身份经真实运行、恢复和导出保持冻结的集成证据。"""

from __future__ import annotations

from tests.data_paths import ENVIRONMENT_ROOT

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from worldmock.catalog.identity import refresh_manifest_identity
from worldmock.errors import IntegrityError
from worldmock.life.models import CounterfactualIntervention, RunIdentity
from worldmock.life.world_setup import load_runtime_scenario
from worldmock.runtime import ResumeRequest, _prepare_resume
from worldmock.run_setup import _model_identity, runtime_code_sha256, stage_model_identity
from tests.integration.as2_fixtures import use_fake_as2
from tests.life.conftest import advance_next, stage_day_plan, submit_round, start_request

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _locked_fake_as2(monkeypatch, tmp_path_factory):
    """运行与恢复的代码身份解析到临时 AS2 包并以其摘要为锁定值，不依赖本机实际安装的 AS2。"""
    return use_fake_as2(monkeypatch, tmp_path_factory.mktemp("as2-site"))


def _fixture(tmp_path):
    """隔离完整人工环境，场景只使用兄弟目录的相对引用。"""
    project = tmp_path / "project"
    shutil.copytree(ENVIRONMENT_ROOT, project)
    return project / "scenarios/multi-person-half-day.json", project / "catalog"


def _create(path, destination):
    """创建具备正式模型与阶段身份的运行，允许恢复继续验证品类身份。"""
    from worldmock.life.engine import LifeEngine
    semantics, _ = load_runtime_scenario(path)
    scenario = semantics.scenario
    identity = RunIdentity(
        run_id="environment-identity", source_group_id="environment-identity-group",
        agent_ids=tuple(agent.agent_id for agent in scenario.agents),
        start_at=scenario.start_at.isoformat(), end_at=scenario.end_at.isoformat(),
        timezone=scenario.timezone, model_identity=_model_identity(), code_sha256=runtime_code_sha256(ROOT),
    )
    return LifeEngine.create(path, destination, identity, stage_identity=stage_model_identity())


def _change_environment(directory):
    path = directory / "foods.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["foods"][-1]["name"] += " revised"
    path.write_text(json.dumps(document), encoding="utf-8")
    refresh_manifest_identity(directory)


def _hash_files(directory):
    return {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in directory.rglob("*") if path.is_file()}


def _expected_identity(directory):
    """直接对照环境清单及真实发布图的字节，不复用运行身份提取函数。"""
    manifest = json.loads((directory / "manifest.json").read_bytes())
    return {
        "environment_id": manifest["environment_id"],
        "environment_version": manifest["environment_version"],
        "rules_version": manifest["rules_version"],
        "catalog_content_sha256": manifest["catalog_content_sha256"],
        "map_sha256": hashlib.sha256((directory / "map.json").read_bytes()).hexdigest(),
    }


def _checkpoint(engine, tmp_path):
    from worldmock.footprints.checkpoint import snapshot_directory
    snapshots = {"life": engine.context.store.assets.put_json(
        {"state_version": engine.context.state.version}, {"kind": "life_checkpoint"})}
    for agent in engine.context.scenario.agents:
        workspace = tmp_path / f"agent-{agent.agent_id}"
        workspace.mkdir()
        (workspace / "AGENT.json").write_text(json.dumps({
            "schema_version": 1, "agent_class": "LifeAgent", "agent_id": agent.agent_id,
            "initialized_at": None, "profile": {"id": agent.agent_id, "same_second_continuation": True},
        }), encoding="utf-8")
        (workspace / "config.json").write_text(json.dumps({
            "enable_memory": True, "max_react_turns": 16,
        }), encoding="utf-8")
        snapshots[f"agent:{agent.agent_id}"] = snapshot_directory(workspace, engine.context.store.assets)
    return engine.context.store.checkpoint(engine.context.store.state, snapshots)


def test_running_semantics_and_export_keep_original_environment_identity(tmp_path):
    from worldmock.life.engine import LifeEngine
    from worldmock.footprints.export import export_run
    path, directory = _fixture(tmp_path)
    engine = _create(path, tmp_path / "run")
    try:
        expected = _expected_identity(directory)
        assert engine.context.store.manifest["environment_identity"] == expected
        reopened = LifeEngine.open(path, tmp_path / "run")
        try:
            assert reopened.context.store.manifest["environment_identity"] == expected
        finally:
            reopened.close()
        names = {key: item.name for key, item in engine.context.semantics.catalog.foods.items()}
        for agent_id in engine.context.state.agents:
            stage_day_plan(engine, agent_id)
        submit_round(engine, {agent_id: ("start_activity", start_request(
            targets=["自己"], actions=["interact"], ways=["等待"])) for agent_id in engine.context.state.agents}, "wait")
        advance_next(engine)
        cutoff = engine.context.store.last_event_seq
        first = export_run(engine.context.store, cutoff, tmp_path / "first-export")
        assert first["frozen_run_manifest"]["environment_identity"] == expected
        frozen_bytes = _hash_files(tmp_path / "first-export")
        _change_environment(directory)
        load_runtime_scenario(path)
        assert _expected_identity(directory) != expected
        assert {key: item.name for key, item in engine.context.semantics.catalog.foods.items()} == names
        second = export_run(engine.context.store, cutoff, tmp_path / "second-export")
        assert first["frozen_run_manifest"] == second["frozen_run_manifest"] == engine.context.store.manifest
        assert _hash_files(tmp_path / "first-export") == frozen_bytes
    finally:
        engine.close()


@pytest.mark.parametrize("refresh", [False, True])
def test_open_rejects_same_version_changed_content_without_mutating_run(tmp_path, refresh):
    from worldmock.life.engine import LifeEngine
    path, directory = _fixture(tmp_path)
    run = tmp_path / "run"
    _create(path, run).close()
    contract = directory / "foods.json"
    contract.write_text(contract.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    if refresh:
        refresh_manifest_identity(directory)
    before = _hash_files(run)
    error = "Runtime environment identity mismatch" if refresh else "ENVIRONMENT_IDENTITY_MISMATCH"
    with pytest.raises(IntegrityError, match=error):
        LifeEngine.open(path, run)
    assert _hash_files(run) == before


@pytest.mark.parametrize("counterfactual", [False, True])
@pytest.mark.parametrize("changed", [False, True])
def test_resume_and_replay_bind_original_identity_before_publishing_branch(tmp_path, counterfactual, changed):
    from worldmock.life.engine import LifeEngine
    from worldmock.footprints.export import export_run
    path, directory = _fixture(tmp_path)
    run, destination = tmp_path / "run", tmp_path / "branch"
    engine = _create(path, run)
    try:
        expected = _expected_identity(directory)
        assert engine.context.store.manifest["environment_identity"] == expected
        checkpoint = _checkpoint(engine, tmp_path)
        parent_manifest = engine.context.store.manifest
    finally:
        engine.close()
    if changed:
        _change_environment(directory)
    request = ResumeRequest(ROOT, path, run, checkpoint.checkpoint_id, destination)
    intervention = CounterfactualIntervention(
        kind="set_balance_fen", agent_id=1, balance_fen=0,
        hypothesis="Available funds influence purchase decisions.", source_event_ids=("source-event",),
    ) if counterfactual else None
    if changed:
        before = _hash_files(run)
        with pytest.raises(IntegrityError, match="Runtime environment identity mismatch"):
            _prepare_resume(request, intervention)
        assert not destination.exists()
        assert not list(tmp_path.glob(".worldmock-resume-*"))
        assert _hash_files(run) == before
    else:
        prepared = _prepare_resume(request, intervention)
        manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
        assert prepared.workspace_mode == "restored"
        assert manifest["environment_identity"] == expected
        assert manifest["parent_checkpoint_id"] == checkpoint.checkpoint_id
        assert manifest["run_id"] != parent_manifest["run_id"]
        branch = LifeEngine.open(path, destination)
        try:
            assert branch.context.store.manifest["environment_identity"] == expected
            for agent_id in branch.context.state.agents:
                stage_day_plan(branch, agent_id)
            submit_round(branch, {agent_id: ("start_activity", start_request(
                targets=["自己"], actions=["interact"], ways=["等待"])) for agent_id in branch.context.state.agents},
                "branch-wait")
            advance_next(branch)
            exported = export_run(branch.context.store, branch.context.store.last_event_seq, tmp_path / "branch-export")
            assert exported["frozen_run_manifest"]["environment_identity"] == expected
        finally:
            branch.close()


@pytest.mark.parametrize("field", ["environment_id", "environment_version", "rules_version",
                                   "catalog_content_sha256", "map_sha256"])
def test_open_rejects_missing_environment_identity_fields(tmp_path, field):
    from worldmock.life.engine import LifeEngine
    path, _ = _fixture(tmp_path)
    run = tmp_path / "run"
    _create(path, run).close()
    manifest_path = run / "manifest.json"
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    document["environment_identity"].pop(field)
    manifest_path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(IntegrityError, match="Runtime environment identity mismatch"):
        LifeEngine.open(path, run)




def test_loaded_environment_identity_is_five_exact_file_bound_values():
    """阶段一真实场景加载所冻结的五环境身份与文件字节一致。"""
    from worldmock.life.run_identity import environment_identity

    semantics, _ = load_runtime_scenario(ENVIRONMENT_ROOT / "scenarios/single-person.json")
    assert environment_identity(semantics) == _expected_identity(ENVIRONMENT_ROOT / "catalog")


@pytest.mark.parametrize("field", ["environment_id", "environment_version", "rules_version",
                                   "catalog_content_sha256", "map_sha256"])
@pytest.mark.parametrize("change", ["missing", "changed"])
def test_environment_identity_requires_every_frozen_value(field, change):
    """缺失任一环境身份或同版本异内容都在读取状态前拒绝。"""
    from worldmock.life.run_identity import require_environment_identity

    semantics, _ = load_runtime_scenario(ENVIRONMENT_ROOT / "scenarios/single-person.json")
    manifest = {"schema_version": 3, "environment_identity": _expected_identity(ENVIRONMENT_ROOT / "catalog")}
    require_environment_identity(manifest, semantics)
    if change == "missing":
        del manifest["environment_identity"][field]
    else:
        manifest["environment_identity"][field] = "changed"
    with pytest.raises(IntegrityError, match="Runtime environment identity mismatch"):
        require_environment_identity(manifest, semantics)


@pytest.mark.parametrize("version", [1, 2])
def test_environment_identity_rejects_retired_run_manifest_versions(version):
    """旧运行结构即使带当前五环境身份仍被拒绝。"""
    from worldmock.life.run_identity import require_environment_identity

    semantics, _ = load_runtime_scenario(ENVIRONMENT_ROOT / "scenarios/single-person.json")
    manifest = {"schema_version": version, "environment_identity": _expected_identity(ENVIRONMENT_ROOT / "catalog")}
    with pytest.raises(IntegrityError, match="Runtime environment identity mismatch"):
        require_environment_identity(manifest, semantics)
