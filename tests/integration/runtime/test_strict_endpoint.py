"""运行模型和完整模型路由档位身份取显式配置与权威服务元数据，不保留已退役布尔与续接键。"""

import pytest
from pydantic import ValidationError

from tests.integration.runtime.current_contract_fixtures import inputs
from worldmock.runtime.framework.setup import model_identity, stage_profile_identity
from worldmock.state.identity import ModelIdentity, StageProfileIdentity


def test_current_identity_freezes_actual_endpoint_and_separate_retry_budgets():
    frozen = inputs()
    pid = frozen.config.model_routing.action
    profile = frozen.config.models[pid]
    metadata = frozen.model_metadata[pid]
    identity = stage_profile_identity(profile, metadata)
    assert identity.base_url == profile.base_url
    assert identity.requested_model == profile.requested_model
    assert identity.max_retries == 0 and identity.transport_max_retries == 3
    assert identity.reflection_validation_max_retries == identity.day_plan_validation_max_retries == 3
    assert identity.model_metadata == metadata
    dumped = identity.model_dump(mode="json")
    assert "persona_validation_max_retries" not in dumped
    assert "api_key_env" not in dumped


@pytest.mark.parametrize("field", [
    "provider", "base_url", "requested_model", "metadata_url", "context_window",
    "thinking", "max_output_tokens", "max_retries", "transport_max_retries",
    "max_react_turns", "reflection_validation_max_retries", "day_plan_validation_max_retries",
    "model_metadata", "effective_max_tokens",
])
def test_stage_profile_identity_requires_every_current_field(field):
    frozen = inputs()
    pid = frozen.config.model_routing.action
    payload = stage_profile_identity(frozen.config.models[pid], frozen.model_metadata[pid]).model_dump(mode="json")
    del payload[field]
    with pytest.raises(ValidationError):
        StageProfileIdentity.model_validate(payload)


def test_model_identity_freezes_routing_and_stage_sha256s():
    frozen = inputs()
    identity = model_identity(frozen.config, frozen.model_metadata)
    payload = identity.model_dump(mode="json")
    assert ModelIdentity.model_validate(payload) == identity
    assert set(identity.profiles) == {"reasoner", "fast_search"}
    assert set(identity.prompt_sha256s) == {"action", "search"}
    assert set(identity.tool_schema_sha256s) == {"action", "search"}
