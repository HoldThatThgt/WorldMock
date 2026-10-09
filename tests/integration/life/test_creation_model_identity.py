"""公开引擎创建只接受从真实输入派生的模型身份与阶段预算，拒绝时不产生运行文件。"""

import copy
import logging

import pytest

from tests.integration.runtime.current_contract_fixtures import inputs
from tests.integration.runtime_fixtures import current_run_identity
from worldmock.errors import IntegrityError
from worldmock.life.engine import LifeEngine
from worldmock.profile.models import WorldDynamics
from worldmock.run_setup import _model_identity, stage_model_identity


def _dynamics():
    return {"world_dynamics": WorldDynamics(social_need_growth_per_hour=0.06).model_dump(mode="json")}


def _foreign_stages(frozen, change):
    """构造自身完全合法、但与真实配置或权威元数据不同的声明。"""
    config = frozen.config.model
    metadata = frozen.model_metadata
    if change == "requested_model":
        config = config.model_copy(update={change: "another-model"})
        metadata = metadata.model_copy(update={change: config.requested_model})
    elif change == "base_url":
        config = config.model_copy(update={"base_url": "https://another.example/v1",
                                           "metadata_url": "https://another.example/v1/models"})
        metadata = metadata.model_copy(update={"metadata_url": config.metadata_url})
    elif change == "context_window":
        metadata = metadata.model_copy(update={change: metadata.context_window * 2})
    elif change == "max_output_tokens":
        metadata = metadata.model_copy(update={change: metadata.max_output_tokens - 1})
    else:
        config = config.model_copy(update={change: getattr(config, change) + 1})
    return stage_model_identity(config, metadata)


@pytest.mark.parametrize("change", [
    "requested_model", "base_url", "context_window", "max_output_tokens", "max_retries",
    "transport_max_retries", "max_react_turns", "persona_validation_max_retries",
    "reflection_validation_max_retries", "day_plan_validation_max_retries",
])
def test_creation_rejects_self_consistent_foreign_stage_identity_without_writing(frozen_inputs, tmp_path, caplog, change):
    """即使自洽并通过阶段结构校验，模型、端点、实际容量或预算不同源也拒绝。"""
    frozen = frozen_inputs.record(inputs().root)["inputs"]
    identity = current_run_identity(frozen, "stage-refusal")
    stages = _foreign_stages(frozen, change)
    untouched = copy.deepcopy(stages)
    destination = tmp_path / "rejected-run"
    with caplog.at_level(logging.ERROR), pytest.raises(IntegrityError, match="Stage model identity disagrees"):
        LifeEngine.create(frozen, destination, identity, stages, _dynamics())
    assert not destination.exists()
    assert stages == untouched
    assert "Stage model identity disagrees with environment inputs" in caplog.text


@pytest.mark.parametrize("field,value", [
    ("requested_model", "another-model"), ("base_url", "https://another.example/v1"),
    ("max_retries", 1), ("transport_max_retries", 4), ("thinking", "disabled"),
    ("tool_choice", "required"), ("prompt_sha256", "a" * 64), ("tool_schema_sha256", "b" * 64),
])
def test_creation_rejects_foreign_run_model_identity_without_writing(tmp_path, caplog, field, value):
    """运行身份也逐项使用当前宿主合同，不能保留另一套模型、重试或提示工具指纹。"""
    frozen = inputs()
    identity = current_run_identity(frozen, "run-refusal")
    identity = identity.model_copy(update={"model_identity": identity.model_identity.model_copy(update={field: value})})
    stages = stage_model_identity(frozen.config.model, frozen.model_metadata)
    destination = tmp_path / "rejected-run"
    with caplog.at_level(logging.ERROR), pytest.raises(IntegrityError, match="Run model identity disagrees"):
        LifeEngine.create(frozen, destination, identity, stages, _dynamics())
    assert not destination.exists()
    assert "Run model identity disagrees with environment inputs" in caplog.text


@pytest.mark.parametrize("continuation", [False, True])
def test_creation_accepts_actual_config_metadata_and_explicit_continuation(tmp_path, continuation):
    """真实身份可以新建运行，两种显式续接选择均完整冻结，不产生身份替代。"""
    frozen = inputs()
    identity = current_run_identity(frozen, "actual-model")
    stages = stage_model_identity(frozen.config.model, frozen.model_metadata, continuation)
    engine = LifeEngine.create(frozen, tmp_path / "actual-run", identity, stages, _dynamics())
    try:
        manifest = engine.context.store.manifest
        assert manifest["model_identity"] == _model_identity(frozen.config.model).model_dump(mode="json")
        assert manifest["stage_model_identity"] == stages
        assert manifest["model_metadata"] == frozen.model_metadata.model_dump(mode="json")
        assert manifest["stage_model_identity"]["stages"]["action"]["same_second_continuation"] is continuation
        assert engine.context.state.agents[frozen.persona.agent_id].name == frozen.persona.name
    finally:
        engine.close()


def test_creation_requires_compiled_dynamics_before_writing_any_files(tmp_path, caplog):
    """合法身份也不能绕过必需的真实画像动力学，不创建半份运行。"""
    frozen = inputs()
    destination = tmp_path / "missing-dynamics"
    with caplog.at_level(logging.ERROR), pytest.raises(IntegrityError, match="Compiled persona dynamics are required"):
        LifeEngine.create(frozen, destination, current_run_identity(frozen, "missing-dynamics"),
                          stage_model_identity(frozen.config.model, frozen.model_metadata), {})
    assert not destination.exists()
    assert "Compiled persona dynamics are required" in caplog.text


def test_creation_rejects_a_foreign_agent_before_writing_any_files(tmp_path, caplog):
    """模型身份合法仍须匹配实际唯一人物，拒绝不能产生运行目录。"""
    frozen = inputs()
    identity = current_run_identity(frozen, "foreign-agent").model_copy(update={"agent_ids": (999,)})
    destination = tmp_path / "foreign-agent"
    with caplog.at_level(logging.ERROR), pytest.raises(IntegrityError, match="Run identity disagrees"):
        LifeEngine.create(frozen, destination, identity,
                          stage_model_identity(frozen.config.model, frozen.model_metadata), _dynamics())
    assert not destination.exists()
    assert "Run identity disagrees with environment inputs" in caplog.text
