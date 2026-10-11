"""@brief 正式新建、共同恢复与反事实输入身份通过真实宿主和冻结工作区执行。"""

import shutil

import pytest

from tests.data_paths import ENVIRONMENT_ROOT
from tests.integration.runtime.current_contract_fixtures import ROOT, compiled_persona, inputs, metadata_service
from tests.integration.runtime_fixtures import write_agent_workspace
from worldmock.errors import IntegrityError
from worldmock.footprints.checkpoint import snapshot_directory
from worldmock.footprints.store import FootprintStore
from worldmock.runtime import RunRequest, ResumeRequest, _prepare_new_run, _prepare_resume, _society_specs


def test_missing_actual_stage_identity_rejects_world_before_creating_run(tmp_path):
    from worldmock.life.engine import LifeEngine
    from tests.integration.runtime_fixtures import current_run_identity

    frozen = inputs()
    with pytest.raises(IntegrityError, match="Stage model identity must be an object"):
        LifeEngine.create(frozen, tmp_path / "run", current_run_identity(frozen, "missing-stage"), None,
                          {"world_dynamics": compiled_persona(frozen).world_dynamics.model_dump()})
    assert not (tmp_path / "run").exists()


async def prepared_parent(tmp_path, monkeypatch, *, continuation=True):
    import worldmock.run_setup as setup
    from custom.envs.life_env import LifeEnv

    frozen = inputs()
    metadata_service(monkeypatch, frozen)

    async def compile_card(*arguments):
        return compiled_persona(frozen)

    monkeypatch.setattr(setup, "_compile_persona", compile_card)
    prepared = _prepare_new_run(RunRequest(ROOT, ENVIRONMENT_ROOT, tmp_path / "parent",
                                         "contract-parent", "contract-parent", continuation))
    specs = await _society_specs(prepared)
    env = LifeEnv(**prepared.env_kwargs)
    await env.init(prepared.start_time)
    workspace = write_agent_workspace(tmp_path / "workspace", agent_document={"schema_version": 1,
        "agent_class": "LifeAgent", "agent_id": 1, "initialized_at": None, "profile": specs[0]["profile"]},
        config_document=specs[0]["config"])
    snapshot = snapshot_directory(workspace, env.engine.context.store.assets)
    checkpoint = env.engine.checkpoint({"agent:1": snapshot})
    return frozen, prepared, env, checkpoint


@pytest.mark.asyncio
async def test_new_run_freezes_complete_inputs_and_mandatory_compiled_card(tmp_path, monkeypatch):
    frozen, prepared, env, _ = await prepared_parent(tmp_path, monkeypatch)
    try:
        manifest = env.engine.context.store.manifest
        assert manifest["schema_version"] == 5
        assert manifest["persona_sha256"] == frozen.persona_sha256
        assert manifest["config_sha256"] == frozen.config_sha256
        assert manifest["model_metadata"] == frozen.model_metadata.model_dump(mode="json")
        assert set(manifest["environment_assets"]) == {"persona.toml", "config.toml", "catalog/manifest.json",
            "catalog/app-catalog.jsonl", "catalog/location.jsonl", "catalog/merchant-catalog.jsonl",
            "catalog/content-catalog.jsonl", "map.json"}
        assert "scenario_sha256" not in manifest and "scenario_path" not in prepared.env_kwargs
        assert set(env.engine.context.state.agents) == {1}
        assert set(env.engine.context.state.accounts) == {"agent:1", "external:scenario"}
    finally:
        await env.close()


@pytest.mark.asyncio
async def test_resume_restores_exact_world_card_and_full_model_without_recompiling(tmp_path, monkeypatch):
    frozen, parent, env, checkpoint = await prepared_parent(tmp_path, monkeypatch, continuation=False)
    before = env.engine.context.store.state
    await env.close()
    prepared = _prepare_resume(ResumeRequest(ROOT, ENVIRONMENT_ROOT, parent.run_dir,
                                           checkpoint.checkpoint_id, tmp_path / "child"))
    assert prepared.workspace_mode == "restored" and prepared.same_second_continuation is False
    specs = await _society_specs(prepared)
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
