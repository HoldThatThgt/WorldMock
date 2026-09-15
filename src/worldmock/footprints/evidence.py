"""@brief 保存模型上下文前移除连接凭据，同时保留实际任务内容。"""

import os
from typing import Any

SECRET_FIELDS = {"api_key", "authorization", "password", "access_token", "refresh_token", "secret"}
SECRET_ENVIRONMENTS = ("DEEPSEEK_API_KEY", "AGENTSOCIETY_LLM_API_KEY", "AGENTSOCIETY_CODER_LLM_API_KEY")


def redact_evidence(value: Any) -> Any:
    """@brief 递归去除凭据字段和当前使用的密钥，不修改调用方对象。"""
    secrets = tuple(os.environ[name] for name in SECRET_ENVIRONMENTS if os.environ.get(name))
    return _redact(value, secrets)


def _redact(value: Any, secrets: tuple[str, ...]) -> Any:
    """@brief 对明确 JSON 类型做确定性脱敏，数组和任务文本保持顺序。"""
    if isinstance(value, dict):
        return {key: "[REDACTED]" if key.lower() in SECRET_FIELDS else _redact(item, secrets)
                for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, "[REDACTED]")
    return value
