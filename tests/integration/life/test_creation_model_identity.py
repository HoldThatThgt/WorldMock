"""公开引擎创建只接受从真实输入派生的 v8 模型身份，拒绝时不产生运行文件。

恢复同样核对：父运行冻结的提示指纹、工具指纹或代码身份与当前不同时按原文拒绝，不留下新运行目录。
"""

from __future__ import annotations

import logging

import pytest

from tests.data_paths import ENVIRONMENT_ROOT
from tests.integration.runtime.current_contract_fixtures import inputs, metadata_service
from tests.integration.runtime_fixtures import current_run_identity, write_agent_workspace, write_project
from worldmock.agents.prompt import life_prompt
from worldmock.foundation.errors import IntegrityError
from worldmock.recording_engine.storage.checkpoint import snapshot_directory
from worldmock.runtime.framework import setup as run_setup
from worldmock.runtime.framework.setup import model_identity, stage_profile_identity
from worldmock.runtime.lifecycle.runner import ResumeRequest, _prepare_resume
from worldmock.simulation_engine.engine import LifeEngine


def _foreign_model_identity(frozen, change: str):
    """构造自身完全合法、但与真实配置或权威元数据某一项不同的 ModelIdentity。"""
    base = model_identity(frozen.config, frozen.model_metadata)
    profile = frozen.config.models["reasoner"]
    metadata = frozen.model_metadata["reasoner"]
    if change == "requested_model":
        profile = profile.model_copy(update={change: "another-model"})
        metadata = metadata.model_copy(update={change: profile.requested_model})
    elif change == "base_url":
        profile = profile.model_copy(update={"base_url": "https://another.example/v1",
                                             "metadata_url": "https://another.example/v1/models"})
        metadata = metadata.model_copy(update={"metadata_url": profile.metadata_url})
    elif change == "context_window":
        metadata = metadata.model_copy(update={change: metadata.context_window * 2})
    elif change == "max_output_tokens":
        metadata = metadata.model_copy(update={change: metadata.max_output_tokens - 1})
    else:
        profile = profile.model_copy(update={change: getattr(profile, change) + 1})
    altered = stage_profile_identity(profile, metadata)
    return base.model_copy(update={"profiles": {**base.profiles, "reasoner": altered}})


def _parent_with_checkpoint(tmp_path):
    """按当前契约创建父运行并提交首次检查点后关闭。"""
    frozen = inputs()
    identity = current_run_identity(frozen, "contract-parent")
    parent_dir = tmp_path / "parent"
    engine = LifeEngine.create(frozen, parent_dir, identity, identity.model_identity, {})
    try:
        workspace = write_agent_workspace(tmp_path / "agent-1")
        checkpoint = engine.checkpoint({"agent:1": snapshot_directory(workspace, engine.context.store.assets)})
    finally:
        engine.close()
    return frozen, parent_dir, checkpoint


@pytest.mark.parametrize("change", [
    "requested_model", "base_url", "context_window", "max_output_tokens", "max_retries",
    "transport_max_retries", "max_react_turns",
    "reflection_validation_max_retries", "day_plan_validation_max_retries",
])
def test_creation_rejects_self_consistent_foreign_stage_identity_without_writing(frozen_inputs, tmp_path, caplog, change):
    """即使自洽并通过结构校验，模型、端点、实际容量或预算与环境配置或 run_identity 不一致也拒绝，不写目录。"""
    frozen = frozen_inputs.record(inputs().root)["inputs"]
    foreign_mi = _foreign_model_identity(frozen, change)
    # 同时构造 identity 与 supplied 均带同一外部模型身份的场景，触发 environment inputs 校验拒绝
    identity = current_run_identity(frozen, "stage-refusal").model_copy(update={"model_identity": foreign_mi})
    destination = tmp_path / "rejected-run"
    with caplog.at_level(logging.ERROR), pytest.raises(IntegrityError, match="Run model identity disagrees"):
        LifeEngine.create(frozen, destination, identity, foreign_mi, {})
    assert not destination.exists()
    assert "Run model identity disagrees with environment inputs" in caplog.text


@pytest.mark.parametrize("field,value", [
    ("prompt_sha256s", {"action": "a" * 64, "search": "b" * 64}),
    ("tool_schema_sha256s", {"action": "c" * 64, "search": "d" * 64}),
    ("routing", {"plan_day": "other", "action": "reasoner", "search": "fast_search",
                 "memory_digest": "reasoner", "agent_overrides": {}}),
])
def test_creation_rejects_foreign_run_model_identity_without_writing(tmp_path, caplog, field, value):
    """supplied 与 identity.model_identity 不一致时按原文拒绝，不创建运行目录。"""
    frozen = inputs()
    identity = current_run_identity(frozen, "run-refusal")
    foreign = identity.model_identity.model_copy(update={field: value})
    identity = identity.model_copy(update={"model_identity": foreign})
    actual = model_identity(frozen.config, frozen.model_metadata)
    destination = tmp_path / "rejected-run"
    with caplog.at_level(logging.ERROR), pytest.raises(
            IntegrityError, match="Run model identity disagrees with supplied model identity"):
        LifeEngine.create(frozen, destination, identity, actual, {})
    assert not destination.exists()
    assert "Run model identity disagrees with supplied model identity" in caplog.text


def test_creation_accepts_actual_config_metadata_and_rejects_retired_keys(tmp_path):
    """真实 v8 模型身份可以新建运行并冻结全部档案，已退役 stage_model_identity 选项键被直接拒绝。"""
    frozen = inputs()
    identity = current_run_identity(frozen, "actual-model")
    actual = model_identity(frozen.config, frozen.model_metadata)
    with pytest.raises(IntegrityError, match="Retired model identity option keys are rejected"):
        LifeEngine.create(frozen, tmp_path / "retired-opt", identity, actual, {"stage_model_identity": {}})
    engine = LifeEngine.create(frozen, tmp_path / "actual-run", identity, actual, {})
    try:
        manifest = engine.context.store.manifest
        assert manifest["schema_version"] == 8
        assert "stage_model_identity" not in manifest
        assert manifest["model_identity"] == actual.model_dump(mode="json")
        assert manifest["persona_sha256s"] == {str(k): v for k, v in frozen.persona_sha256s.items()}
        assert engine.context.state.agents[frozen.persona_by_id[1].agent_id].name == frozen.persona_by_id[1].name
    finally:
        engine.close()


def test_creation_rejects_a_foreign_agent_before_writing_any_files(tmp_path, caplog):
    """模型身份合法仍须匹配实际人物集合，拒绝不能产生运行目录。"""
    frozen = inputs()
    identity = current_run_identity(frozen, "foreign-agent").model_copy(update={"agent_ids": (999,)})
    destination = tmp_path / "foreign-agent"
    with caplog.at_level(logging.ERROR), pytest.raises(IntegrityError, match="Run identity disagrees"):
        LifeEngine.create(frozen, destination, identity, model_identity(frozen.config, frozen.model_metadata), {})
    assert not destination.exists()
    assert "Run identity disagrees with environment inputs" in caplog.text


def _edit_tool_description(monkeypatch):
    """改一个原生工具的用途说明：工具 Schema 随之改变。"""
    name = "start_main_activity"
    monkeypatch.setitem(life_prompt.TOOL_DESCRIPTIONS, name, life_prompt.TOOL_DESCRIPTIONS[name] + "改过一句。")


def _edit_system_rule(monkeypatch):
    """改系统层的一条规则：提示随之改变。"""
    first, *others = life_prompt.LIFE_SYSTEM_LAYER["rules"]
    monkeypatch.setitem(life_prompt.LIFE_SYSTEM_LAYER, "rules", [first + "改过一句。", *others])


@pytest.mark.parametrize("edit,fingerprint", [
    pytest.param(_edit_tool_description, "tool_schema_sha256s", id="tool-description"),
    pytest.param(_edit_system_rule, "prompt_sha256s", id="system-layer-rule"),
])
def test_resume_rejects_a_changed_prompt_or_tool_fingerprint_without_writing(
        tmp_path, monkeypatch, caplog, edit, fingerprint):
    """父运行按当前提示与工具建好并存下检查点；此后系统层规则或工具用途说明变了，
    当前模型身份只在对应指纹上与父清单不同：恢复按原文拒绝，不留下新运行目录。"""
    frozen = inputs()
    metadata_service(monkeypatch, frozen)
    _, parent_dir, checkpoint = _parent_with_checkpoint(tmp_path)
    recorded = model_identity(frozen.config, frozen.model_metadata).model_dump(mode="json")
    edit(monkeypatch)
    current = model_identity(frozen.config, frozen.model_metadata).model_dump(mode="json")
    assert {key for key in current if current[key] != recorded[key]} == {fingerprint}
    child = tmp_path / "child"
    with caplog.at_level(logging.ERROR), pytest.raises(
            IntegrityError, match=r"^Checkpoint model identity differs from the current LifeAgent contract$"):
        _prepare_resume(ResumeRequest(ENVIRONMENT_ROOT, parent_dir, checkpoint.checkpoint_id, child))
    assert not child.exists()
    assert "Checkpoint model identity differs from the current contract" in caplog.text


def test_resume_rejects_a_different_code_identity_without_writing(tmp_path, monkeypatch, caplog):
    """提示与工具都没有变、项目根里的生产源码不是父运行那一份：恢复报代码身份不符，不留下新运行目录。

    对照：退出替换项目根的上下文之后，同一个检查点用真实的项目根可以恢复。
    """
    frozen = inputs()
    metadata_service(monkeypatch, frozen)
    _, parent_dir, checkpoint = _parent_with_checkpoint(tmp_path)
    other = write_project(tmp_path / "other-project")
    refused = tmp_path / "refused"
    with monkeypatch.context() as patched:
        patched.setattr(run_setup, "project_root", lambda: other)
        with caplog.at_level(logging.ERROR), pytest.raises(
                IntegrityError, match=r"^Checkpoint code identity differs from the current runtime$"):
            _prepare_resume(ResumeRequest(ENVIRONMENT_ROOT, parent_dir, checkpoint.checkpoint_id, refused))
    assert not refused.exists()
    assert "Checkpoint code identity differs from the current runtime" in caplog.text
    accepted = _prepare_resume(ResumeRequest(ENVIRONMENT_ROOT, parent_dir, checkpoint.checkpoint_id,
                                             tmp_path / "child"))
    assert accepted.run_dir == (tmp_path / "child").resolve() and accepted.run_dir.exists()
