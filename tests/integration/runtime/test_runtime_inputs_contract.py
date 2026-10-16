"""@brief 正式新建、共同恢复与反事实输入身份通过真实宿主和冻结工作区执行。"""

import shutil

import pytest

from tests.data_paths import ENVIRONMENT_ROOT
from tests.integration.runtime.current_contract_fixtures import ROOT, aligned_process, inputs, metadata_service
from tests.integration.runtime_fixtures import write_agent_workspace
from worldmock import run_setup
import worldmock.runtime as runtime_module
from worldmock.errors import EnvironmentInputError, IntegrityError
from worldmock.footprints.checkpoint import snapshot_directory
from worldmock.footprints.store import FootprintStore
from worldmock.runtime import RunRequest, ResumeRequest, _prepare_new_run, _prepare_resume, _society_specs

#: 进程配置失败的替身抛出的错误原文。
REFUSED = "AgentSociety2 was imported before the required model configuration"


def test_missing_actual_stage_identity_rejects_world_before_creating_run(tmp_path):
    from worldmock.life.engine import LifeEngine
    from tests.integration.runtime_fixtures import current_run_identity

    frozen = inputs()
    with pytest.raises(IntegrityError, match="Stage model identity must be an object"):
        LifeEngine.create(frozen, tmp_path / "run", current_run_identity(frozen, "missing-stage"), None, {})
    assert not (tmp_path / "run").exists()


async def prepared_parent(tmp_path, monkeypatch, *, continuation=True):
    from custom.envs.life_env import LifeEnv

    frozen = inputs()
    metadata_service(monkeypatch, frozen)
    aligned_process(monkeypatch, frozen)
    prepared = _prepare_new_run(RunRequest(ROOT, ENVIRONMENT_ROOT, tmp_path / "parent",
                                         "contract-parent", "contract-parent", continuation))
    specs = _society_specs(prepared)
    env = LifeEnv(**prepared.env_kwargs)
    await env.init(prepared.start_time)
    workspace = write_agent_workspace(tmp_path / "workspace", agent_document={"schema_version": 1,
        "agent_class": "LifeAgent", "agent_id": 1, "initialized_at": None, "profile": specs[0]["profile"]},
        config_document=specs[0]["config"])
    snapshot = snapshot_directory(workspace, env.engine.context.store.assets)
    checkpoint = env.engine.checkpoint({"agent:1": snapshot})
    return frozen, prepared, env, checkpoint


def _record(monkeypatch, calls, owner, name, label):
    """给 owner 上的一个函数包一层按调用先后记下 label 的包装，行为不变。"""
    original = getattr(owner, name)

    def recorded(*args, **kwargs):
        calls.append(label)
        return original(*args, **kwargs)

    monkeypatch.setattr(owner, name, recorded)


def _refuse_configuration(*args):
    """进程配置的替身：照真实的第三种失败抛出。"""
    raise RuntimeError(REFUSED)


def test_new_run_configures_the_process_after_the_environment_and_before_the_model_identity(tmp_path, monkeypatch):
    """新建运行读完环境之后、读模型身份之前配置进程：先后恰为「配置、身份」。"""
    frozen = inputs()
    metadata_service(monkeypatch, frozen)
    aligned_process(monkeypatch, frozen)
    calls = []
    _record(monkeypatch, calls, run_setup, "_configure_process", "configure")
    _record(monkeypatch, calls, run_setup, "_model_identity", "identity")
    prepared = _prepare_new_run(RunRequest(ROOT, ENVIRONMENT_ROOT, tmp_path / "run", "ordered", "ordered"))
    assert calls == ["configure", "identity"]
    assert prepared.run_dir == (tmp_path / "run").resolve()


def test_new_run_with_a_refused_environment_neither_configures_nor_reads_the_model_identity(tmp_path, monkeypatch):
    """环境不合格时报环境的拒绝原因，进程配置与模型身份都没有被调用。"""
    root = tmp_path / "environment"
    shutil.copytree(ENVIRONMENT_ROOT, root)
    (root / "persona.toml").write_text("schema_version = 2\n", encoding="utf-8", newline="\n")
    metadata_service(monkeypatch)
    calls = []
    _record(monkeypatch, calls, run_setup, "_configure_process", "configure")
    _record(monkeypatch, calls, run_setup, "_model_identity", "identity")
    with pytest.raises(EnvironmentInputError) as refused:
        _prepare_new_run(RunRequest(ROOT, root, tmp_path / "run", "refused", "refused"))
    assert (refused.value.path, refused.value.code) == ("persona.toml", "PERSONA_INVALID")
    assert calls == []
    assert not (tmp_path / "run").exists()


def test_new_run_whose_process_configuration_fails_leaves_no_run_directory(tmp_path, monkeypatch):
    """进程配置失败时抛出同一个错，输出目录没有被创建。"""
    metadata_service(monkeypatch)
    monkeypatch.setattr(run_setup, "_configure_process", _refuse_configuration)
    with pytest.raises(RuntimeError, match=f"^{REFUSED}$"):
        _prepare_new_run(RunRequest(ROOT, ENVIRONMENT_ROOT, tmp_path / "run", "refused", "refused"))
    assert not (tmp_path / "run").exists()


@pytest.mark.asyncio
async def test_resume_configures_the_process_after_the_checkpoint_and_before_the_runtime_identity(
        tmp_path, monkeypatch):
    """恢复在取到检查点之后、核对运行身份之前配置进程；配置失败时抛出同一个错，不建分支目录。"""
    _, parent, env, checkpoint = await prepared_parent(tmp_path, monkeypatch)
    await env.close()
    calls = []
    _record(monkeypatch, calls, FootprintStore, "get_checkpoint", "checkpoint")
    _record(monkeypatch, calls, run_setup, "_configure_process", "configure")
    _record(monkeypatch, calls, runtime_module, "_require_runtime_identity", "runtime identity")
    prepared = _prepare_resume(ResumeRequest(ROOT, ENVIRONMENT_ROOT, parent.run_dir,
                                             checkpoint.checkpoint_id, tmp_path / "child"))
    assert calls[:3] == ["checkpoint", "configure", "runtime identity"]
    assert calls.count("configure") == 1
    assert prepared.run_dir.exists()
    monkeypatch.setattr(run_setup, "_configure_process", _refuse_configuration)
    with pytest.raises(RuntimeError, match=f"^{REFUSED}$"):
        _prepare_resume(ResumeRequest(ROOT, ENVIRONMENT_ROOT, parent.run_dir,
                                      checkpoint.checkpoint_id, tmp_path / "refused"))
    assert not (tmp_path / "refused").exists()


def test_resume_without_a_parent_database_fails_before_configuring_the_process(tmp_path, monkeypatch):
    """父库不存在先于进程配置报出，没有配置进程，也不建分支目录。"""
    metadata_service(monkeypatch)
    calls = []
    _record(monkeypatch, calls, run_setup, "_configure_process", "configure")
    with pytest.raises(IntegrityError, match="Footprint database does not exist"):
        _prepare_resume(ResumeRequest(ROOT, ENVIRONMENT_ROOT, tmp_path / "missing-parent", "checkpoint",
                                      tmp_path / "child"))
    assert calls == []
    assert not (tmp_path / "child").exists()


@pytest.mark.asyncio
async def test_new_run_freezes_complete_inputs_and_the_card_built_from_the_persona_table(tmp_path, monkeypatch):
    frozen, prepared, env, _ = await prepared_parent(tmp_path, monkeypatch)
    try:
        manifest = env.engine.context.store.manifest
        assert manifest["schema_version"] == 7
        assert manifest["persona_sha256"] == frozen.persona_sha256
        assert manifest["config_sha256"] == frozen.config_sha256
        assert manifest["model_metadata"] == frozen.model_metadata.model_dump(mode="json")
        assert set(manifest["environment_assets"]) == {"persona.toml", "config.toml", "catalog/manifest.json",
            "catalog/app-catalog.jsonl", "catalog/location.jsonl", "catalog/merchant-catalog.jsonl",
            "catalog/content-catalog.jsonl", "catalog/travel-method.jsonl", "map.json"}
        assert "scenario_sha256" not in manifest and "scenario_path" not in prepared.env_kwargs
        assert set(env.engine.context.state.agents) == {1}
        assert set(env.engine.context.state.accounts) == {"agent:1", "external:scenario"}
    finally:
        await env.close()


@pytest.mark.asyncio
async def test_resume_restores_exact_world_card_and_full_model_from_the_frozen_workspace(tmp_path, monkeypatch):
    frozen, parent, env, checkpoint = await prepared_parent(tmp_path, monkeypatch, continuation=False)
    before = env.engine.context.store.state
    await env.close()
    prepared = _prepare_resume(ResumeRequest(ROOT, ENVIRONMENT_ROOT, parent.run_dir,
                                           checkpoint.checkpoint_id, tmp_path / "child"))
    assert prepared.workspace_mode == "restored" and prepared.same_second_continuation is False
    specs = _society_specs(prepared)
    assert specs[0]["profile"]["same_second_continuation"] is False
    assert specs[0]["profile"]["model_identity"]["model_metadata"] == frozen.model_metadata.model_dump(mode="json")
    store = FootprintStore(prepared.run_dir)
    try:
        assert {k: v for k, v in store.state.items() if k not in {"run_id", "version"}} == {
            k: v for k, v in before.items() if k not in {"run_id", "version"}}
        assert store.state["version"] == before["version"] + 1
        for reference in store.manifest["environment_assets"].values():
            from worldmock.footprints.models import AssetRef
            asset = AssetRef.model_validate(reference)
            assert len(store.assets.read(asset)) == asset.size_bytes
    finally:
        store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("relative", ["persona.toml", "config.toml"])
async def test_resume_rejects_changed_original_bytes_before_publishing_child(tmp_path, monkeypatch, relative):
    _, parent, env, checkpoint = await prepared_parent(tmp_path, monkeypatch)
    await env.close()
    root = tmp_path / "changed-environment"
    shutil.copytree(ENVIRONMENT_ROOT, root)
    path = root / relative
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(IntegrityError):
        _prepare_resume(ResumeRequest(ROOT, root, parent.run_dir, checkpoint.checkpoint_id, tmp_path / "child"))
    assert not (tmp_path / "child").exists()


@pytest.mark.asyncio
async def test_resume_rejects_actual_metadata_drift_before_publishing_child(tmp_path, monkeypatch):
    _, parent, env, checkpoint = await prepared_parent(tmp_path, monkeypatch)
    await env.close()
    import requests
    from types import SimpleNamespace

    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: SimpleNamespace(raise_for_status=lambda: None,
        json=lambda: {"data": [{"id": "test-model", "context_window": 524289, "max_output_tokens": 32768}]}))
    with pytest.raises(IntegrityError, match="model identity"):
        _prepare_resume(ResumeRequest(ROOT, ENVIRONMENT_ROOT, parent.run_dir,
                                     checkpoint.checkpoint_id, tmp_path / "child"))
    assert not (tmp_path / "child").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["rhythm", "relationship", "missing"])
async def test_resume_rejects_a_frozen_card_that_differs_from_the_persona_table(tmp_path, monkeypatch, change):
    """恢复的真实路径：检查点里人物工作区的结构化卡片与用冻结的 persona.toml 重新构造出的卡片不相等（作息或关系被改过，
    或卡片缺失）时，在发布子目录之前以完整性错误停止。"""
    import copy
    from worldmock.run_setup import creation_inputs

    frozen, parent, env, _ = await prepared_parent(tmp_path, monkeypatch)
    spec = copy.deepcopy(creation_inputs(frozen)[0][0])
    card = spec["profile"]["persona_card_struct"]
    if change == "rhythm":
        card["time_anchors"]["lunch"]["start_hour"] = 11.5
    elif change == "relationship":
        card["relationships"].pop()
    else:
        del spec["profile"]["persona_card_struct"]
    workspace = write_agent_workspace(tmp_path / "tampered", agent_document={
        "schema_version": 1, "agent_class": "LifeAgent", "agent_id": 1, "initialized_at": None,
        "profile": spec["profile"]}, config_document=spec["config"])
    checkpoint = env.engine.checkpoint({"agent:1": snapshot_directory(workspace, env.engine.context.store.assets)})
    await env.close()
    with pytest.raises(IntegrityError, match="Restored persona card differs from persona.toml"):
        _prepare_resume(ResumeRequest(ROOT, ENVIRONMENT_ROOT, parent.run_dir, checkpoint.checkpoint_id,
                                      tmp_path / "child"))
    assert not (tmp_path / "child").exists()
