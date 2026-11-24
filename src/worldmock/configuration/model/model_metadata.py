"""@brief 实际模型服务的只读权威容量核验，不使用静态容量表。"""

from __future__ import annotations

import copy

import logging
import os
import time
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import requests
from pydantic import ValidationError

from worldmock.configuration.inputs.models import (
    ModelMetadata,
    ModelProfileConfig,
    RunConfig,
    referenced_profile_ids,
)

if TYPE_CHECKING:
    from worldmock.configuration.model.model_client import ModelClient

_LOGGER = logging.getLogger(__name__)
#: 元数据读取整次重试的等待秒数（至多五次读取）。
_METADATA_FETCH_WAITS = (2.0, 4.0, 8.0, 16.0)
#: 视为服务暂时不可用、整次重试的 HTTP 状态。
_METADATA_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


def credential_value(config: ModelProfileConfig) -> str:
    """@brief 只按配置声明的进程环境变量读取凭据，不记录凭据正文。"""
    value = os.environ.get(config.api_key_env)
    if not isinstance(value, str) or not value.strip():
        _LOGGER.error("Model credential environment variable is missing or empty: %s", config.api_key_env)
        raise ValueError("Model credential environment variable is missing or empty")
    return value.strip()


def load_model_metadata(config: ModelProfileConfig) -> ModelMetadata:
    """@brief 精确匹配实际模型服务返回的模型身份与输入输出容量；暂时性失败整次重试，至多五次。"""
    from worldmock.foundation.errors import EnvironmentInputError

    try:
        _require_service_urls(config)
        secret = credential_value(config)
        payload = _fetch_metadata(config.metadata_url, secret)
        metadata = parse_model_metadata(config, payload)
    except (requests.RequestException, ValueError, ValidationError) as error:
        reason = "model context metadata is unavailable"
        if str(error).startswith("Model context length "):
            reason = str(error)
        raise EnvironmentInputError("config.toml", "RUN_CONFIG_INVALID", reason) from error
    return metadata


def _get_metadata(url: str, secret: str) -> object:
    """@brief 一次读取元数据：GET、核对状态并解析 JSON，任何一步的 requests 异常原样抛出。"""
    response = requests.get(url, headers={"Authorization": f"Bearer {secret}"}, timeout=30)
    response.raise_for_status()
    return response.json()


def _fetch_metadata(url: str, secret: str) -> object:
    """@brief 整次读取元数据（GET、状态与 JSON）；连接、超时、429 与 5xx 按固定间隔重试，至多五次；用尽或其他失败时
    写英文错误日志并抛出最后一次错误。"""
    for tries, wait in enumerate((*_METADATA_FETCH_WAITS, None), start=1):
        try:
            return _get_metadata(url, secret)
        except requests.RequestException as error:
            if wait is None or not _transient_metadata_error(error):
                _LOGGER.error("Model metadata fetch failed after %d tries: %s", tries, type(error).__name__)
                raise
            _LOGGER.warning("Model metadata fetch failed (%s); retrying in %.0f s", type(error).__name__, wait)
            time.sleep(wait)


def _transient_metadata_error(error: requests.RequestException) -> bool:
    """@brief 连接（含 TLS 与代理）、超时、响应体中断及 429/5xx 视为暂时性失败。"""
    if isinstance(error, (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError)):
        return True
    response = error.response if isinstance(error, requests.HTTPError) else None
    return response is not None and response.status_code in _METADATA_RETRY_STATUSES


def frozen_metadata_loader(source: Mapping[str, Any]) -> Callable[[ModelProfileConfig], ModelMetadata]:
    """@brief 恢复、重放与打开已有运行的元数据来源：读运行清单冻结的档案元数据，不访问模型服务；键缺失或值不合法时拒绝。"""
    from worldmock.foundation.errors import IntegrityError

    try:
        frozen = _extract_frozen_metadata(source)
    except (KeyError, TypeError, ValueError, ValidationError) as error:
        _LOGGER.error("Frozen model metadata is missing or invalid")
        raise IntegrityError("Frozen model metadata is missing or invalid") from error
    if isinstance(frozen, ModelMetadata):
        return lambda _config: frozen

    def _lookup(config: ModelProfileConfig) -> ModelMetadata:
        """@brief 按模型标识与元数据端点匹配清单中冻结的对应档案元数据。"""
        key = (config.requested_model, config.metadata_url)
        if key not in frozen:
            _LOGGER.error("Frozen model metadata is missing or invalid for %s", config.requested_model)
            raise IntegrityError("Frozen model metadata is missing or invalid")
        return frozen[key]

    return _lookup


def _extract_frozen_metadata(source: Mapping[str, Any]) -> ModelMetadata | dict[tuple[str, str], ModelMetadata]:
    """@brief 从 v8 多档案运行清单或单档案元数据字典提取冻结元数据映射。"""
    identity = source.get("model_identity")
    if isinstance(identity, Mapping) and "profiles" in identity:
        profiles = identity["profiles"]
        if not isinstance(profiles, Mapping) or not profiles:
            raise ValueError("empty frozen model profiles")
        entries = [ModelMetadata.model_validate(item["model_metadata"]) for item in profiles.values()]
        return {(meta.requested_model, meta.metadata_url): meta for meta in entries}
    raw = source["model_metadata"]
    if isinstance(raw, Mapping) and "requested_model" not in raw:
        if not raw:
            raise ValueError("empty model metadata map")
        entries = [ModelMetadata.model_validate(item) for item in raw.values()]
        return {(meta.requested_model, meta.metadata_url): meta for meta in entries}
    return ModelMetadata.model_validate(raw)


def _require_service_urls(config: ModelProfileConfig) -> None:
    """@brief 身份端点与显式元数据端点必须指向同一 HTTP 模型服务。"""
    base, metadata = urlparse(config.base_url), urlparse(config.metadata_url)
    if (base.scheme not in {"http", "https"} or not base.hostname or base.username or base.password
            or (base.scheme, base.netloc) != (metadata.scheme, metadata.netloc)
            or metadata.username or metadata.password):
        _LOGGER.error("Model metadata URL does not identify the configured model service")
        raise ValueError("Model metadata URL does not identify the configured model service")


def parse_model_metadata(config: ModelProfileConfig, payload: object) -> ModelMetadata:
    """@brief 服务必须给出唯一匹配模型、总容量与输出上限，缺失能力直接拒绝。"""
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        _metadata_invalid()
    matching = [row for row in payload["data"] if isinstance(row, dict) and row.get("id") == config.requested_model]
    if len(matching) != 1:
        _metadata_invalid()
    row = matching[0]
    try:
        metadata = ModelMetadata(requested_model=config.requested_model, metadata_url=config.metadata_url,
                                 context_window=row.get("context_window"),
                                 max_output_tokens=row.get("max_output_tokens"))
    except ValidationError:
        _metadata_invalid()
    if metadata.context_window < config.context_window:
        reason = (f"Model context length {metadata.context_window} is below the declared "
                  f"context_window {config.context_window}")
        _LOGGER.error("%s", reason)
        raise ValueError(reason)
    return metadata


def _metadata_invalid() -> None:
    """@brief 元数据不完整时记录统一原因，不估算或查询另一份容量表。"""
    _LOGGER.error("Model context metadata is unavailable")
    raise ValueError("model context metadata is unavailable")


class IndependentModelClient:
    """@brief 零点计划与回顾的业务入口：经整次运行共用的模型客户端发送；请求隔离，响应序列化为可持久化字典。"""

    def __init__(self, model_client: ModelClient, model_identity: Mapping[str, Any], purpose: str):
        """@brief 记下整次运行共用的模型客户端、冻结身份里的模型名与用途；不核对、不保存身份。"""
        self.client = model_client
        self.model_name = model_identity["requested_model"]
        self.purpose = purpose

    async def call(self, request: dict) -> dict:
        """@brief 把请求的深拷贝交给模型客户端发送，调用方的请求不被改动；响应必须可持久化。"""
        from worldmock.foundation.errors import ProtocolError

        response = await self.client.call(copy.deepcopy(request))
        if hasattr(response, "model_dump"):
            payload = response.model_dump(mode="json")
        elif isinstance(response, dict):
            payload = dict(response)
        else:
            raise ProtocolError(f"{self.purpose} model response is not serializable")
        if not isinstance(payload, dict):
            raise ProtocolError(f"{self.purpose} model response is not a serialized object")
        return payload


def model_context_length(model: str, metadata: ModelMetadata | dict) -> int:
    """@brief 发送与后台规划共用冻结的实际服务总容量，拒绝模型身份不符。"""
    checked = metadata if isinstance(metadata, ModelMetadata) else ModelMetadata.model_validate(metadata)
    if checked.requested_model != model:
        _LOGGER.error("Frozen model metadata does not match the requested model")
        raise ValueError("model context metadata is unavailable")
    return checked.context_window


def active_profile_ids(config: RunConfig) -> tuple[str, ...]:
    """@brief 返回被 model_routing（含 agent_overrides）实际引用的全部模型档位名称。"""
    return referenced_profile_ids(config.model_routing)


def verify_active_credentials(config: RunConfig) -> dict[str, str]:
    """@brief 核验全部被 model_routing 引用的模型档位环境变量凭据均已非空配置。"""
    return {profile_id: credential_value(config.models[profile_id]) for profile_id in active_profile_ids(config)}


def load_active_metadata(
    config: RunConfig,
    metadata_loader: Callable[[ModelProfileConfig], ModelMetadata] | None = None,
) -> dict[str, ModelMetadata]:
    """@brief 为 model_routing（含 agent_overrides）实际引用的全部模型档加载并核验元数据，未引用档位不请求。"""
    loader = metadata_loader or load_model_metadata
    loaded: dict[str, ModelMetadata] = {}
    for profile_id in active_profile_ids(config):
        profile = config.models[profile_id]
        metadata = loader(profile)
        _verify_metadata_matches_profile(profile, metadata)
        loaded[profile_id] = metadata
    return loaded


def _verify_metadata_matches_profile(profile: ModelProfileConfig, metadata: ModelMetadata) -> None:
    """@brief 校验元数据返回的模型名、端点与最小上下文长度满足配置档声明。"""
    if metadata.requested_model != profile.requested_model or metadata.metadata_url != profile.metadata_url:
        _LOGGER.error("Model metadata identity disagrees with profile %s", profile.requested_model)
        raise ValueError("model context metadata is unavailable")
    if metadata.context_window < profile.context_window:
        reason = (f"Model context length {metadata.context_window} is below the declared "
                  f"context_window {profile.context_window}")
        _LOGGER.error("%s", reason)
        raise ValueError(reason)
