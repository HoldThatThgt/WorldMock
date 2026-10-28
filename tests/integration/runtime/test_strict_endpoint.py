"""运行模型和完整阶段身份取显式配置与权威服务元数据。"""
import copy
import pytest
from tests.integration.runtime.current_contract_fixtures import inputs
from worldmock.foundation.errors import IntegrityError
from worldmock.runtime.framework.setup import stage_model_identity, validate_stage_model_identity


def test_current_identity_freezes_actual_endpoint_and_separate_retry_budgets():
    frozen = inputs()
    identity = stage_model_identity(frozen.config.model, frozen.model_metadata)
    assert identity["base_url"] == frozen.config.model.base_url
    assert identity["requested_model"] == frozen.config.model.requested_model
    assert identity["max_retries"] == 0 and identity["transport_max_retries"] == 3
    assert "persona_validation_max_retries" not in identity
    assert identity["reflection_validation_max_retries"] == identity["day_plan_validation_max_retries"] == 3
    assert identity["model_metadata"] == frozen.model_metadata.model_dump(mode="json")
    assert "api_key_env" not in identity


@pytest.mark.parametrize("field", ["provider", "base_url", "requested_model", "metadata_url", "context_window",
    "model_metadata", "max_retries", "transport_max_retries", "max_react_turns", "strict", "stages"])
def test_stage_identity_requires_every_current_field(field):
    frozen = inputs()
    identity = stage_model_identity(frozen.config.model, frozen.model_metadata)
    del identity[field]
    with pytest.raises(IntegrityError):
        validate_stage_model_identity(identity)


@pytest.mark.parametrize("value", [None, 1, "on"])
def test_continuation_switch_is_exactly_boolean(value):
    frozen = inputs()
    identity = stage_model_identity(frozen.config.model, frozen.model_metadata)
    identity["stages"]["action"]["same_second_continuation"] = value
    with pytest.raises(IntegrityError):
        validate_stage_model_identity(identity)
