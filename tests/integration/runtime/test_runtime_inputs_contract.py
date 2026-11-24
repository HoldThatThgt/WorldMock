"""@brief 正式新建、共同恢复与反事实输入身份通过真实运行宿主和冻结工作区执行。"""

import json
import logging
import shutil

import pytest

from tests.data_paths import ENVIRONMENT_ROOT
from tests.integration.runtime.current_contract_fixtures import inputs, metadata_service
from tests.integration.runtime_fixtures import OfflineClient, new_run, write_project
from worldmock.runtime.framework.setup import model_identity
from worldmock.runtime.framework import setup as run_setup
import worldmock.configuration.model.model_registry as model_registry_module
import worldmock.runtime.lifecycle.runner as runtime_module
from worldmock.agents.life_agent import person_inputs
from worldmock.agents.memory.workspace import snapshot_workspaces
from worldmock.foundation.errors import EnvironmentInputError, IntegrityError
from worldmock.recording_engine.storage.checkpoint import snapshot_directory
from worldmock.recording_engine.storage.store import FootprintStore
from worldmock.state.identity import ModelIdentity
from worldmock.runtime.lifecycle.runner import (
    RunRequest,
    ResumeRequest,
    _prepare_new_run,
    _prepare_resume,
)


def test_missing_actual_stage_identity_rejects_world_before_creating_run(tmp_path):
    from worldmock.simulation_engine.engine import LifeEngine
    from tests.integration.runtime_fixtures import current_run_identity

    frozen = inputs()
    with pytest.raises(IntegrityError, match="Explicit model identity is required when creating a run"):
        LifeEngine.create(frozen, tmp_path / "run", current_run_identity(frozen, "missing-stage"), None, {})
    assert not (tmp_path / "run").exists()


def _offline_day_plans(monkeypatch) -> None:
    """新建运行的初始检查点之后会补零点计划：零点计划的业务客户端换成确定性空清单桩，离线客户端因此收不到请求。"""
    from tests.life.conftest import StubPlanDayClient
    from worldmock.runtime.lifecycle import midnight

    monkeypatch.setattr(midnight, "PlanDayClient", lambda client, identity: StubPlanDayClient(identity))


async def prepared_parent(tmp_path, monkeypatch):
    """经真实新建入口与运行宿主建一个父运行，再为空的人物工作区写一个共同检查点；返回的宿主仍开着。"""
    frozen = inputs()
    metadata_service(monkeypatch, frozen)
    _offline_day_plans(monkeypatch)
    prepared = _prepare_new_run(RunRequest(ENVIRONMENT_ROOT, tmp_path / "parent",
                                           "contract-parent", "contract-parent"))
    host = await new_run(prepared, OfflineClient())
    checkpoint = host.engine.checkpoint(snapshot_workspaces(prepared.run_dir, (1,), host.engine.context.store.assets))
    return frozen, prepared, host, checkpoint


def _record(monkeypatch, calls, owner, name, label):
    """给 owner 上的一个函数包一层按调用先后记下 label 的包装，行为不变。"""
    original = getattr(owner, name)

    def recorded(*args, **kwargs):
        calls.append(label)
        return original(*args, **kwargs)

    monkeypatch.setattr(owner, name, recorded)


def _logged(run_dir, code: str) -> list[dict]:
    """运行目录运维日志里某个记录码的全部记录。"""
    return [entry for path in sorted((run_dir / "logs").glob("ops-*.jsonl"))
            for entry in map(json.loads, path.read_text(encoding="utf-8").splitlines()) if entry["code"] == code]


def test_new_run_with_a_refused_environment_does_not_read_the_model_identity(tmp_path, monkeypatch):
    """环境不合格时报环境的拒绝原因，模型身份没有被读取，不留运行目录。"""
    root = tmp_path / "environment"
    shutil.copytree(ENVIRONMENT_ROOT, root)
    (root / "personas" / "persona_1.toml").write_text("schema_version = 2\n", encoding="utf-8", newline="\n")
    metadata_service(monkeypatch)
    calls = []
    _record(monkeypatch, calls, run_setup, "model_identity", "identity")
    with pytest.raises(EnvironmentInputError) as refused:
        _prepare_new_run(RunRequest(root, tmp_path / "run", "refused", "refused"))
    assert (refused.value.path, refused.value.code) == ("personas/persona_1.toml", "PERSONA_INVALID")
    assert calls == []
    assert not (tmp_path / "run").exists()


async def test_prepared_runs_carry_the_frozen_agent_inputs_and_the_model_identity(tmp_path, monkeypatch):
    """新建：清单里的模型身份等于从配置和元数据构造的那一份，人物冻结输入恰为人设表的唯一人物，带冻结的运行身份；
    恢复：恢复出同一模型身份与人物冻结输入，没有运行身份。"""
    frozen, parent, host, checkpoint = await prepared_parent(tmp_path, monkeypatch)
    await host.close()
    (started,) = _logged(parent.run_dir, "RUN_STARTED")
    expected_identity = model_identity(parent.inputs.config, parent.inputs.model_metadata).model_dump(mode="json")
    assert parent.run_identity.model_identity.model_dump(mode="json") == expected_identity
    assert tuple(parent.agents) == (1,)
    assert parent.agents[1] == person_inputs(parent.inputs.persona_by_id[1])
    assert parent.run_identity is not None and parent.run_identity.run_id == "contract-parent"
    assert parent.run_identity.code_sha256 == started["code_sha256"]
    child = _prepare_resume(ResumeRequest(ENVIRONMENT_ROOT, parent.run_dir, checkpoint.checkpoint_id,
                                          tmp_path / "child"))
    frozen_identity = json.loads((parent.run_dir / "manifest.json").read_text(encoding="utf-8"))["model_identity"]
    assert frozen_identity == expected_identity
    assert tuple(child.agents) == (1,)
    assert child.agents[1] == person_inputs(child.inputs.persona_by_id[1])
    assert child.run_identity is None


async def test_resume_reads_the_credential_before_the_runtime_identity_and_the_branch(tmp_path, monkeypatch, caplog):
    """恢复在核对运行身份之前读取凭据：凭据变量缺失时以凭据缺失拒绝（即使代码身份也不符），不建分支目录。"""
    frozen, parent, host, checkpoint = await prepared_parent(tmp_path, monkeypatch)
    await host.close()
    monkeypatch.delenv(frozen.config.models["reasoner"].api_key_env)
    other = write_project(tmp_path / "other-project")
    monkeypatch.setattr(run_setup, "project_root", lambda: other)
    with caplog.at_level(logging.ERROR), pytest.raises(Exception) as raised:
        _prepare_resume(ResumeRequest(ENVIRONMENT_ROOT, parent.run_dir, checkpoint.checkpoint_id,
                                      tmp_path / "child"))
    assert type(raised.value) is ValueError
    assert str(raised.value) == "Model credential environment variable is missing or empty"
    assert "Model credential environment variable is missing or empty: WORLDMOCK_TEST_API_KEY" in caplog.text
    assert not (tmp_path / "child").exists()


def test_resume_without_a_parent_database_fails_before_reading_the_credential(tmp_path, monkeypatch):
    """父库不存在先于凭据报出，没有读取凭据，也不建分支目录。"""
    metadata_service(monkeypatch)
    monkeypatch.delenv(inputs().config.models["reasoner"].api_key_env)
    calls = []
    _record(monkeypatch, calls, model_registry_module, "credential_value", "credential")
    with pytest.raises(IntegrityError, match="Footprint database does not exist"):
        _prepare_resume(ResumeRequest(ENVIRONMENT_ROOT, tmp_path / "missing-parent", "checkpoint",
                                      tmp_path / "child"))
    assert calls == []
    assert not (tmp_path / "child").exists()


@pytest.mark.asyncio
async def test_new_run_freezes_complete_inputs_and_the_card_built_from_the_persona_table(tmp_path, monkeypatch):
    frozen, prepared, host, _ = await prepared_parent(tmp_path, monkeypatch)
    try:
        manifest = host.engine.context.store.manifest
        assert manifest["schema_version"] == 8
        assert manifest["persona_sha256s"] == {"1": frozen.persona_sha256s[1]}
        assert manifest["config_sha256"] == frozen.config_sha256
        assert manifest["model_identity"] == model_identity(frozen.config, frozen.model_metadata).model_dump(mode="json")
        assert set(manifest["environment_assets"]) == {"personas/persona_1.toml", "config.toml",
            "catalog/manifest.json", "catalog/app-catalog.jsonl", "catalog/location.jsonl",
            "catalog/merchant-catalog.jsonl", "catalog/content-catalog.jsonl", "catalog/travel-method.jsonl",
            "map.json"}
        assert "scenario_sha256" not in manifest and not hasattr(prepared, "scenario_path")
        assert set(host.engine.context.state.agents) == {1}
        assert set(host.engine.context.state.accounts) == {"agent:1", "external:scenario"}
    finally:
        await host.close()


@pytest.mark.asyncio
async def test_resume_restores_exact_world_card_and_full_model_from_the_frozen_workspace(tmp_path, monkeypatch):
    frozen, parent, host, checkpoint = await prepared_parent(tmp_path, monkeypatch)
    before = host.engine.context.store.state
    await host.close()
    prepared = _prepare_resume(ResumeRequest(ENVIRONMENT_ROOT, parent.run_dir,
                                             checkpoint.checkpoint_id, tmp_path / "child"))
    assert prepared.run_identity is None
    assert prepared.memory_records == {1: ()}
    assert dict(prepared.inputs.model_metadata) == dict(frozen.model_metadata)
    store = FootprintStore(prepared.run_dir)
    try:
        assert ModelIdentity.model_validate(store.manifest["model_identity"]).model_dump(mode="json") == (
            model_identity(frozen.config, frozen.model_metadata).model_dump(mode="json"))
        assert {k: v for k, v in store.state.items() if k not in {"run_id", "version"}} == {
            k: v for k, v in before.items() if k not in {"run_id", "version"}}
        assert store.state["version"] == before["version"] + 1
        for reference in store.manifest["environment_assets"].values():
            from worldmock.recording_engine.events.models import AssetRef
            asset = AssetRef.model_validate(reference)
            assert len(store.assets.read(asset)) == asset.size_bytes
    finally:
        store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("relative", ["personas/persona_1.toml", "config.toml"])
async def test_resume_rejects_changed_original_bytes_before_publishing_child(tmp_path, monkeypatch, relative):
    _, parent, host, checkpoint = await prepared_parent(tmp_path, monkeypatch)
    await host.close()
    root = tmp_path / "changed-environment"
    shutil.copytree(ENVIRONMENT_ROOT, root)
    path = root / relative
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(IntegrityError):
        _prepare_resume(ResumeRequest(root, parent.run_dir, checkpoint.checkpoint_id, tmp_path / "child"))
    assert not (tmp_path / "child").exists()


@pytest.mark.asyncio
async def test_resume_rejects_a_corrupt_memory_workspace_before_publishing(tmp_path, monkeypatch):
    """恢复的真实路径：检查点里人物工作区的条目文件读不回时，在发布子目录之前以完整性错误停止。"""
    _, parent, host, _ = await prepared_parent(tmp_path, monkeypatch)
    workspace = tmp_path / "tampered"
    (workspace / "memory").mkdir(parents=True)
    (workspace / "MEMORY.md").write_text("# 记忆\n", encoding="utf-8", newline="\n")
    (workspace / "memory" / "episodes.jsonl").write_text("{}\n", encoding="utf-8", newline="\n")
    checkpoint = host.engine.checkpoint({"agent:1": snapshot_directory(workspace, host.engine.context.store.assets)})
    await host.close()
    with pytest.raises(IntegrityError, match="^Restored agent memory is invalid$"):
        _prepare_resume(ResumeRequest(ENVIRONMENT_ROOT, parent.run_dir, checkpoint.checkpoint_id,
                                      tmp_path / "child"))
    assert not (tmp_path / "child").exists()
