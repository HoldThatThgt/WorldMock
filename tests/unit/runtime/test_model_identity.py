"""真实环境配置、权威容量和独立预算的冻结身份。"""

import copy

import pytest

from tests.integration.runtime.current_contract_fixtures import inputs
from worldmock.errors import IntegrityError
from worldmock.run_setup import _model_identity, stage_model_identity, validate_stage_model_identity


def test_model_identity_freezes_explicit_endpoint_and_separate_retry_budgets():
    """运行身份来自声明，凭据名称与凭据值不进入身份。"""
    frozen = inputs()
    config = frozen.config.model
    identity = _model_identity(config).model_dump(mode="json")
    assert identity["base_url"] == config.base_url
    assert identity["requested_model"] == config.requested_model
    assert identity["provider"] == config.provider
    assert identity["max_retries"] == config.max_retries
    assert identity["transport_max_retries"] == config.transport_max_retries
    assert "api_key_env" not in identity
    assert identity["thinking"] == "enabled" and identity["tool_choice"] == "auto"


def test_stage_identity_freezes_actual_capacity_and_all_validation_budgets():
    """只有行动阶段，真实输出上限约束请求；全部独立预算保留原值。"""
    from custom.agents.life_agent import ACTION_MAX_TOKENS

    frozen = inputs()
    identity = stage_model_identity(frozen.config.model, frozen.model_metadata)
    assert identity["model_metadata"] == frozen.model_metadata.model_dump(mode="json")
    assert set(identity["stages"]) == {"action"}
    assert identity["stages"]["action"] == {
        "thinking": "enabled", "tool_choice": "auto",
        "max_tokens": min(ACTION_MAX_TOKENS, frozen.model_metadata.max_output_tokens),
        "same_second_continuation": True,
    }
    for field in ("persona_validation_max_retries", "reflection_validation_max_retries",
                  "day_plan_validation_max_retries", "max_react_turns"):
        assert identity[field] == getattr(frozen.config.model, field)
    disabled = stage_model_identity(frozen.config.model, frozen.model_metadata, False)
    assert disabled["stages"]["action"]["same_second_continuation"] is False
    assert validate_stage_model_identity(identity) == identity


@pytest.mark.parametrize("change", [
    lambda row: row["stages"]["action"].update(max_tokens=4000),
    lambda row: row.update(reasoning_effort="high"),
    lambda row: row.update(strict=False),
    lambda row: row.update(api_key_env="SECRET"),
    lambda row: row["model_metadata"].update(requested_model="different-model"),
    lambda row: row["model_metadata"].update(context_window=262143),
    lambda row: row.update(stages={}),
    lambda row: row.pop("model_metadata"),
])
def test_tampered_identity_is_rejected(change):
    """冻结阶段身份中的参数、容量、额外字段或缺失声明都明确拒绝。"""
    frozen = inputs()
    identity = copy.deepcopy(stage_model_identity(frozen.config.model, frozen.model_metadata))
    change(identity)
    with pytest.raises(IntegrityError):
        validate_stage_model_identity(identity)


def test_endpoint_change_is_valid_only_with_matching_declared_metadata():
    """服务端点可以配置，身份校验拒绝端点与实际元数据来源不一致。"""
    frozen = inputs()
    identity = stage_model_identity(frozen.config.model, frozen.model_metadata)
    identity["base_url"] = "https://elsewhere.example/v1"
    identity["metadata_url"] = "https://elsewhere.example/v1/models"
    with pytest.raises(IntegrityError, match="metadata"):
        validate_stage_model_identity(identity)
    identity["model_metadata"]["metadata_url"] = identity["metadata_url"]
    assert validate_stage_model_identity(identity)["base_url"] == "https://elsewhere.example/v1"
