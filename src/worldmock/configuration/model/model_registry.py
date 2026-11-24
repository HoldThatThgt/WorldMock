"""@brief 阶段与人物双维异构模型路由器与连接池复用注册表。"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from worldmock.configuration.inputs.models import (
    MODEL_STAGES,
    ModelMetadata,
    ModelProfileConfig,
    ModelRoutingConfig,
    RunConfig,
    referenced_profile_ids,
)
from worldmock.configuration.model.model_client import ModelClient
from worldmock.configuration.model.model_metadata import credential_value, verify_active_credentials
from worldmock.foundation.errors import IntegrityError

_LOGGER = logging.getLogger(__name__)

ModelStage = Literal["action", "search", "plan_day", "digest"]
_PoolKey = tuple[str, str, int, int]


@dataclass(frozen=True, slots=True)
class ResolvedModelBinding:
    """@brief 按阶段与人物解析出的冻结模型绑定与有效输出上限。"""

    profile_id: str
    profile: ModelProfileConfig
    metadata: ModelMetadata
    client: ModelClient
    effective_max_tokens: int


def _pool_key(profile: ModelProfileConfig) -> _PoolKey:
    """@brief 提取用于复用底层 HTTP 连接池的四元组键。"""
    return (profile.base_url, profile.api_key_env, profile.max_retries, profile.transport_max_retries)


def _default_client(profile: ModelProfileConfig) -> ModelClient:
    """@brief 按档位端点、环境变量凭据与两类重试预算构造真实模型客户端。"""
    return ModelClient(
        base_url=profile.base_url,
        api_key=credential_value(profile),
        max_retries=profile.max_retries,
        transport_max_retries=profile.transport_max_retries,
    )


class ModelRegistry:
    """@brief 按阶段与人物路由模型档位，并按端点与凭据键复用 ModelClient 连接池。"""

    def __init__(
        self,
        config: RunConfig,
        model_metadata: Mapping[str, ModelMetadata],
        client_factory: Callable[[ModelProfileConfig], ModelClient] | None = None,
    ) -> None:
        """@brief 核验全部激活档位元数据并建立共享连接池映射。"""
        self._routing: ModelRoutingConfig = config.model_routing
        active_ids = referenced_profile_ids(self._routing)
        self._require_metadata(active_ids, model_metadata)
        if client_factory is None:
            verify_active_credentials(config)
        factory = client_factory or _default_client
        self._clients: dict[_PoolKey, ModelClient] = {}
        self._bindings: dict[str, ResolvedModelBinding] = self._build_bindings(
            config, active_ids, model_metadata, factory,
        )

    @property
    def bindings(self) -> Mapping[str, ResolvedModelBinding]:
        """@brief 返回全部激活模型档位的只读解析映射。"""
        return MappingProxyType(self._bindings)

    def resolve(self, stage: ModelStage, agent_id: int | None = None) -> ResolvedModelBinding:
        """@brief 按认知阶段与可选人物编号解析对应模型绑定（人物覆盖优先于阶段默认）。"""
        if stage not in MODEL_STAGES:
            _LOGGER.error("Unknown model routing stage: %s", stage)
            raise ValueError(f"Unknown model stage: {stage}")
        profile_id = getattr(self._routing, stage)
        if agent_id is not None:
            override = self._routing.agent_overrides.get(str(agent_id))
            if override is not None:
                stage_profile = getattr(override, stage)
                if stage_profile is not None:
                    profile_id = stage_profile
        return self._bindings[profile_id]

    async def close(self) -> None:
        """@brief 依次关闭全部唯一连接池客户端，保留所有关闭错误。"""
        errors: list[Exception] = []
        for client in self._clients.values():
            try:
                await client.close()
            except Exception as error:
                _LOGGER.exception("ModelRegistry client close failed")
                errors.append(error)
        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise ExceptionGroup("ModelRegistry close failed", errors)

    def _require_metadata(
        self, active_ids: tuple[str, ...], model_metadata: Mapping[str, ModelMetadata],
    ) -> None:
        """@brief 核对每个被路由引用的激活档位均已具备核验后的元数据。"""
        missing = [profile_id for profile_id in active_ids if profile_id not in model_metadata]
        if missing:
            _LOGGER.error("Missing model metadata for active profiles: %s", ", ".join(missing))
            raise IntegrityError(f"Missing model metadata for active profile: {missing[0]}")

    def _build_bindings(
        self,
        config: RunConfig,
        active_ids: tuple[str, ...],
        model_metadata: Mapping[str, ModelMetadata],
        factory: Callable[[ModelProfileConfig], ModelClient],
    ) -> dict[str, ResolvedModelBinding]:
        """@brief 按连接池键去重创建客户端并生成每个激活档位的只读绑定。"""
        bindings: dict[str, ResolvedModelBinding] = {}
        for profile_id in active_ids:
            profile = config.models[profile_id]
            metadata = model_metadata[profile_id]
            key = _pool_key(profile)
            if key not in self._clients:
                self._clients[key] = factory(profile)
            bindings[profile_id] = ResolvedModelBinding(
                profile_id=profile_id,
                profile=profile,
                metadata=metadata,
                client=self._clients[key],
                effective_max_tokens=min(profile.max_output_tokens, metadata.max_output_tokens),
            )
        return bindings
