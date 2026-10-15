"""原生环境冻结完整模型能力和输入身份，拒绝预算类型或值漂移。"""
import copy
import pytest
from tests.integration.runtime.current_contract_fixtures import inputs, metadata_service
from tests.integration.runtime_fixtures import current_run_identity
from tests.data_paths import ENVIRONMENT_ROOT
from worldmock.errors import IntegrityError
from worldmock.run_setup import stage_model_identity, validate_stage_model_identity
from custom.envs.life_env import LifeEnv

@pytest.mark.parametrize("field", ["transport_max_retries", "max_react_turns", "reflection_validation_max_retries",
                                   "day_plan_validation_max_retries"])
@pytest.mark.parametrize("change", ["missing", "float", "string", "negative"])
def test_manifest_model_budgets_refuse_incomplete_or_invalid_values(field, change):
    frozen = inputs()
    identity = stage_model_identity(frozen.config.model, frozen.model_metadata)
    assert validate_stage_model_identity(identity) == identity
    if change == "missing":
        del identity[field]
    else:
        value = identity[field]
        identity[field] = {"float": float(value), "string": str(value), "negative": -1}[change]
    with pytest.raises(IntegrityError):
        validate_stage_model_identity(identity)

@pytest.mark.asyncio
async def test_native_environment_manifest_contains_full_declared_service_identity(tmp_path, monkeypatch):
    frozen = inputs()
    metadata_service(monkeypatch, frozen)
    env = LifeEnv(environment_root=str(ENVIRONMENT_ROOT), run_dir=str(tmp_path / "run"),
                  run_identity=current_run_identity(frozen, "manifest").model_dump(mode="json"))
    await env.init(frozen.config.start_time)
    try:
        manifest = env.engine.context.store.manifest
        assert manifest["schema_version"] == 6
        listed = [entry.path for entry in frozen.catalog.manifest.files]
        assert listed == ["app-catalog.jsonl", "content-catalog.jsonl", "location.jsonl", "merchant-catalog.jsonl",
                          "travel-method.jsonl"]
        assert set(manifest["environment_assets"]) == {
            "persona.toml", "config.toml", "catalog/manifest.json", "map.json", *(f"catalog/{name}" for name in listed)}
        assert manifest["environment_identity"] == {
            "environment_id": "artificial-test", "environment_version": "4", "rules_version": 6,
            "catalog_content_sha256": frozen.catalog.manifest.catalog_content_sha256,
            "map_sha256": frozen.catalog.map_sha256}
        assert manifest["model_metadata"] == frozen.model_metadata.model_dump(mode="json")
        assert manifest["stage_model_identity"] == stage_model_identity(frozen.config.model, frozen.model_metadata)
        assert "api_key_env" not in manifest["stage_model_identity"]
        assert manifest["agent_ids"] == [1]
    finally:
        await env.close()
