"""多模型档注册表、双维路由解析、连接池复用、凭据核验与冻结元数据边界契约。"""

from __future__ import annotations

import ast
import asyncio
import copy
from pathlib import Path
import tomllib

import pytest

from tests.frozen_inputs import fixture_metadata
from worldmock.configuration.inputs.models import ModelMetadata, RunConfig
from worldmock.configuration.model.model_metadata import (
    active_profile_ids,
    credential_value,
    frozen_metadata_loader,
    load_active_metadata,
    verify_active_credentials,
)
from worldmock.configuration.model.model_registry import ModelRegistry, ResolvedModelBinding
from worldmock.foundation.errors import IntegrityError

SOURCE = Path(__file__).resolve().parents[2] / "fixtures/dataset-environment"


class _FakeClient:
    """只记录关闭次数的轻量模型客户端替身。"""

    def __init__(self, profile, fail_with: Exception | None = None) -> None:
        self.profile = profile
        self.fail_with = fail_with
        self.closed = 0

    async def close(self) -> None:
        self.closed += 1
        if self.fail_with is not None:
            raise self.fail_with


def _sample_config_dict() -> dict:
    payload = tomllib.loads((SOURCE / "config.toml").read_text(encoding="utf-8"))
    payload["models"]["search_dedicated"] = {
        **payload["models"]["fast_search"],
        "base_url": "https://search.example.invalid/v1",
        "metadata_url": "https://search.example.invalid/v1/models",
        "requested_model": "search-dedicated-model",
        "max_output_tokens": 8192,
    }
    payload["models"]["unused_extra"] = {
        **payload["models"]["fast_search"],
        "api_key_env": "WORLDMOCK_UNUSED_EXTRA_KEY",
        "requested_model": "unused-extra-model",
    }
    payload["model_routing"]["agent_overrides"] = {"2": {"search": "search_dedicated"}}
    return payload


def _build_metadata_map(config: RunConfig, cap_overrides: dict[str, int] | None = None) -> dict[str, ModelMetadata]:
    caps = cap_overrides or {}
    loaded: dict[str, ModelMetadata] = {}
    for profile_id in active_profile_ids(config):
        profile = config.models[profile_id]
        base_meta = fixture_metadata(profile)
        if profile_id in caps:
            base_meta = base_meta.model_copy(update={"max_output_tokens": caps[profile_id]})
        loaded[profile_id] = base_meta
    return loaded


def test_model_registry_reuses_pools_resolves_overrides_and_caps_tokens() -> None:
    """相同连接池键复用客户端，不同键新建客户端；人物覆盖只作用于所覆盖阶段，未引用档位不实例化。"""
    config = RunConfig.model_validate(_sample_config_dict())
    meta_map = _build_metadata_map(config, {"search_dedicated": 4096})
    created: list[_FakeClient] = []

    def factory(profile) -> _FakeClient:
        client = _FakeClient(profile)
        created.append(client)
        return client

    registry = ModelRegistry(config, meta_map, client_factory=factory)
    assert len(created) == 2
    assert set(registry.bindings) == {"reasoner", "fast_search", "search_dedicated"}
    assert "unused_extra" not in registry.bindings
    with pytest.raises(TypeError):
        registry.bindings["extra"] = registry.bindings["reasoner"]  # type: ignore[index]

    action_default = registry.resolve("action")
    search_default = registry.resolve("search")
    assert isinstance(action_default, ResolvedModelBinding)
    assert action_default.profile_id == "reasoner"
    assert search_default.profile_id == "fast_search"
    assert action_default.client is search_default.client
    assert action_default.effective_max_tokens == 16000
    assert search_default.effective_max_tokens == 4096

    assert registry.resolve("search", agent_id=1).profile_id == "fast_search"
    search_agent2 = registry.resolve("search", agent_id=2)
    assert search_agent2.profile_id == "search_dedicated"
    assert search_agent2.client is not action_default.client
    assert search_agent2.effective_max_tokens == 4096
    assert registry.resolve("action", agent_id=2).profile_id == "reasoner"

    with pytest.raises(ValueError, match="Unknown model stage"):
        registry.resolve("reflection")  # type: ignore[arg-type]


def test_model_registry_pool_key_splits_on_retry_and_env_differences() -> None:
    """max_retries、transport_max_retries 或 api_key_env 任一不同时分配独立连接池。"""
    raw = _sample_config_dict()
    raw["models"]["fast_search"]["transport_max_retries"] = 5
    raw["models"]["search_dedicated"]["api_key_env"] = "WORLDMOCK_SEARCH_API_KEY"
    config = RunConfig.model_validate(raw)
    created: list[_FakeClient] = []
    registry = ModelRegistry(config, _build_metadata_map(config), client_factory=lambda p: created.append(c := _FakeClient(p)) or c)
    assert len(created) == 3
    assert len({b.client for b in registry.bindings.values()}) == 3


def test_model_registry_missing_metadata_and_default_client_verification(monkeypatch) -> None:
    """缺少激活档元数据报 IntegrityError；默认工厂校验激活凭据并能创建真实 ModelClient。"""
    config = RunConfig.model_validate(_sample_config_dict())
    meta_map = _build_metadata_map(config)
    partial = {k: v for k, v in meta_map.items() if k != "search_dedicated"}
    with pytest.raises(IntegrityError, match="Missing model metadata for active profile: search_dedicated"):
        ModelRegistry(config, partial, client_factory=_FakeClient)

    monkeypatch.setenv("WORLDMOCK_TEST_API_KEY", "active-secret")
    monkeypatch.delenv("WORLDMOCK_UNUSED_EXTRA_KEY", raising=False)
    creds = verify_active_credentials(config)
    assert creds == {"reasoner": "active-secret", "fast_search": "active-secret", "search_dedicated": "active-secret"}

    monkeypatch.delenv("WORLDMOCK_TEST_API_KEY", raising=False)
    with pytest.raises(ValueError, match="missing or empty"):
        credential_value(config.models["reasoner"])
    with pytest.raises(ValueError, match="missing or empty"):
        ModelRegistry(config, meta_map)

    monkeypatch.setenv("WORLDMOCK_TEST_API_KEY", "active-secret")
    real_registry = ModelRegistry(config, meta_map)
    assert len(real_registry.bindings) == 3
    asyncio.run(real_registry.close())


@pytest.mark.asyncio
async def test_model_registry_close_handles_success_single_and_multiple_failures() -> None:
    """close 按去重客户端关闭；单错误原样抛出，多错误聚合为 ExceptionGroup。"""
    config = RunConfig.model_validate(_sample_config_dict())
    meta_map = _build_metadata_map(config)

    ok_clients: list[_FakeClient] = []
    registry_ok = ModelRegistry(
        config, meta_map, client_factory=lambda p: ok_clients.append(c := _FakeClient(p)) or c,
    )
    await registry_ok.close()
    assert [c.closed for c in ok_clients] == [1, 1]

    def one_fail(profile) -> _FakeClient:
        err = RuntimeError("search close boom") if "search.example" in profile.base_url else None
        return _FakeClient(profile, fail_with=err)

    registry_one = ModelRegistry(config, meta_map, client_factory=one_fail)
    with pytest.raises(RuntimeError, match="search close boom"):
        await registry_one.close()

    registry_many = ModelRegistry(
        config, meta_map, client_factory=lambda p: _FakeClient(p, fail_with=OSError(f"boom:{p.requested_model}")),
    )
    with pytest.raises(ExceptionGroup) as group:
        await registry_many.close()
    assert len(group.value.exceptions) == 2


def test_frozen_metadata_loader_and_litellm_import_boundary() -> None:
    """frozen_metadata_loader 支持 v8 多档与单档映射；model_registry 模块禁止导入 litellm。"""
    config = RunConfig.model_validate(_sample_config_dict())
    meta_map = _build_metadata_map(config)
    v8_source = {
        "model_identity": {
            "profiles": {
                pid: {"model_metadata": meta.model_dump(mode="json")}
                for pid, meta in meta_map.items()
            },
        },
    }
    loaded_v8 = load_active_metadata(config, metadata_loader=frozen_metadata_loader(copy.deepcopy(v8_source)))
    assert loaded_v8 == meta_map

    dict_source = {"model_metadata": {pid: meta.model_dump(mode="json") for pid, meta in meta_map.items()}}
    assert load_active_metadata(config, metadata_loader=frozen_metadata_loader(dict_source)) == meta_map

    single_meta = fixture_metadata(config.models["reasoner"]).model_dump(mode="json")
    assert frozen_metadata_loader({"model_metadata": single_meta})(config.models["reasoner"]).requested_model == "test-model"

    lookup_v8 = frozen_metadata_loader(v8_source)
    unknown_profile = config.models["unused_extra"].model_copy(update={"requested_model": "not-frozen"})
    with pytest.raises(IntegrityError, match="Frozen model metadata is missing or invalid"):
        lookup_v8(unknown_profile)

    for bad_source in ({}, {"model_identity": {"profiles": {}}}, {"model_metadata": {}}):
        with pytest.raises(IntegrityError, match="Frozen model metadata is missing or invalid"):
            frozen_metadata_loader(bad_source)

    registry_source = Path(__file__).resolve().parents[3] / "src/worldmock/configuration/model/model_registry.py"
    tree = ast.parse(registry_source.read_text(encoding="utf-8"))
    imported = {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    } | {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert "litellm" not in imported
