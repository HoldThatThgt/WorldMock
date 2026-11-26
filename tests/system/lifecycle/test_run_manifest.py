"""原生环境冻结完整模型档路由身份与输入身份，拒绝预算类型或值漂移。"""

import json

import pytest
from pydantic import ValidationError

from tests.data_paths import ENVIRONMENT_ROOT
from tests.integration.runtime.current_contract_fixtures import inputs, metadata_service
from tests.integration.runtime_fixtures import OfflineClient, new_run
from tests.life.conftest import StubPlanDayClient
from worldmock.runtime.framework.setup import model_identity, stage_profile_identity
from worldmock.runtime.lifecycle import midnight
from worldmock.runtime.lifecycle.runner import RunRequest, _prepare_new_run
from worldmock.state.identity import RUN_MANIFEST_SCHEMA_VERSION, StageProfileIdentity


@pytest.mark.parametrize("field", ["transport_max_retries", "max_react_turns", "reflection_validation_max_retries",
                                   "day_plan_validation_max_retries", "compact_trigger_tokens"])
@pytest.mark.parametrize("change", ["missing", "float", "string", "negative"])
def test_manifest_model_budgets_refuse_incomplete_or_invalid_values(field, change):
    frozen = inputs()
    profile_id = frozen.config.model_routing.action
    identity = stage_profile_identity(frozen.config.models[profile_id], frozen.model_metadata[profile_id])
    payload = identity.model_dump(mode="json")
    assert StageProfileIdentity.model_validate(payload) == identity
    if change == "missing":
        del payload[field]
    else:
        value = payload[field]
        payload[field] = {"float": float(value), "string": str(value), "negative": -1}[change]
    with pytest.raises(ValidationError):
        StageProfileIdentity.model_validate(payload)


def test_stage_profile_identity_freezes_prompt_cache_key_and_compact_trigger_bounds():
    """@brief StageProfileIdentity 严格冻结 prompt_cache_key 与 compact_trigger_tokens 上界。"""
    frozen = inputs()
    profile_id = frozen.config.model_routing.action
    identity = stage_profile_identity(frozen.config.models[profile_id], frozen.model_metadata[profile_id])
    assert identity.compact_trigger_tokens == frozen.config.models[profile_id].compact_trigger_tokens
    assert identity.prompt_cache_key is frozen.config.models[profile_id].prompt_cache_key
    payload = identity.model_dump(mode="json")
    with pytest.raises(ValidationError):
        StageProfileIdentity.model_validate({**payload, "prompt_cache_key": "yes"})
    with pytest.raises(ValidationError, match="compact_trigger_tokens"):
        StageProfileIdentity.model_validate({**payload, "compact_trigger_tokens": int(identity.context_window * 0.85)})


@pytest.mark.asyncio
async def test_native_environment_manifest_contains_full_declared_service_identity(tmp_path, monkeypatch):
    frozen = inputs()
    metadata_service(monkeypatch, frozen)
    monkeypatch.setattr(midnight, "PlanDayClient", lambda client, identity: StubPlanDayClient(identity))
    prepared = _prepare_new_run(RunRequest(ENVIRONMENT_ROOT, tmp_path / "run", "manifest", "manifest"))
    host = await new_run(prepared, OfflineClient())
    try:
        manifest = host.engine.context.store.manifest
        assert manifest["schema_version"] == RUN_MANIFEST_SCHEMA_VERSION == 9
        listed = [entry.path for entry in frozen.catalog.manifest.files]
        assert listed == ["app-catalog.jsonl", "content-catalog.jsonl", "location.jsonl", "merchant-catalog.jsonl",
                          "travel-method.jsonl"]
        assert set(manifest["environment_assets"]) == {
            "personas/persona_1.toml", "config.toml", "catalog/manifest.json", "map.json",
            *(f"catalog/{name}" for name in listed),
        }
        assert manifest["environment_identity"] == {
            "environment_id": "artificial-test", "environment_version": "4", "rules_version": 7,
            "catalog_content_sha256": frozen.catalog.manifest.catalog_content_sha256,
            "map_sha256": frozen.catalog.map_sha256,
        }
        expected_model = model_identity(frozen.config, frozen.model_metadata).model_dump(mode="json")
        assert manifest["model_identity"] == expected_model
        assert "model_metadata" not in manifest and "stage_model_identity" not in manifest
        assert "api_key_env" not in json.dumps(manifest["model_identity"])
        assert manifest["persona_sha256s"] == {"1": frozen.persona_sha256s[1]}
        assert manifest["agent_ids"] == [1]
    finally:
        await host.close()
