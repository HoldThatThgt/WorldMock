"""@brief 实际模型服务的只读权威容量核验，不使用静态容量表。"""

from __future__ import annotations

import copy

import logging
import os
from urllib.parse import urlparse

import requests
from pydantic import ValidationError

from worldmock.input_models import ModelConfig, ModelMetadata

_LOGGER = logging.getLogger(__name__)


def credential_value(config: ModelConfig) -> str:
    """@brief 只按配置声明的进程环境变量读取凭据，不记录凭据正文。"""
    value = os.environ.get(config.api_key_env)
    if not isinstance(value, str) or not value.strip():
        _LOGGER.error("Model credential environment variable is missing or empty: %s", config.api_key_env)
        raise ValueError("Model credential environment variable is missing or empty")
    return value.strip()


def load_model_metadata(config: ModelConfig) -> ModelMetadata:
    """@brief 精确匹配实际模型服务返回的模型身份与输入输出容量。"""
    from worldmock.errors import EnvironmentInputError

    try:
        _require_service_urls(config)
        secret = credential_value(config)
        response = requests.get(config.metadata_url, headers={"Authorization": f"Bearer {secret}"}, timeout=30)
        response.raise_for_status()
        metadata = parse_model_metadata(config, response.json())
    except (requests.RequestException, ValueError, ValidationError) as error:
        reason = "model context metadata is unavailable"
        if str(error).startswith("Model context length "):
            reason = str(error)
        raise EnvironmentInputError("config.toml", "RUN_CONFIG_INVALID", reason) from error
    return metadata


def _require_service_urls(config: ModelConfig) -> None:
    """@brief 身份端点与显式元数据端点必须指向同一 HTTP 模型服务。"""
    base, metadata = urlparse(config.base_url), urlparse(config.metadata_url)
    if (base.scheme not in {"http", "https"} or not base.hostname or base.username or base.password
            or (base.scheme, base.netloc) != (metadata.scheme, metadata.netloc)
            or metadata.username or metadata.password):
        _LOGGER.error("Model metadata URL does not identify the configured model service")
        raise ValueError("Model metadata URL does not identify the configured model service")


def parse_model_metadata(config: ModelConfig, payload: object) -> ModelMetadata:
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
    """@brief 业务模型请求共用已配置客户端，身份与预算来自冻结运行配置。"""

    def __init__(self, model_identity: dict, purpose: str):
        """@brief 建立与冻结模型身份一致的客户端，不寻找第二份配置。"""
        from agentsociety2.config import build_client_for_role
        from worldmock.errors import ProtocolError

        client = build_client_for_role("default")
        if (client.model_name != model_identity["requested_model"]
                or client.base_url != model_identity["base_url"]):
            raise ProtocolError(f"{purpose} client identity disagrees with the run manifest")
        self.client = client
        self.model_name = model_identity["requested_model"]
        self.purpose = purpose
        self.model_identity = model_identity

    async def call(self, request: dict) -> dict:
        """@brief 请求使用同一 dispatcher 的两个独立重试计数器，响应必须可持久化。"""
        from worldmock.errors import ProtocolError

        arguments = copy.deepcopy(request)
        arguments["max_retries"] = self.model_identity["max_retries"]
        arguments["transport_max_retries"] = self.model_identity["transport_max_retries"]
        metadata = ModelMetadata.model_validate(self.model_identity["model_metadata"])
        if "max_tokens" in arguments:
            arguments["max_tokens"] = min(arguments["max_tokens"], metadata.max_output_tokens)
        response = await self.client.call(**arguments)
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
