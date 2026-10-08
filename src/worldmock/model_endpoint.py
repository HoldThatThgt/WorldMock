"""@brief WorldMock 与画像编译共用的 DeepSeek 官方模型端点。"""

from __future__ import annotations

import copy
from worldmock.errors import ProtocolError

#: AgentSociety2 与独立画像请求均使用官方 OpenAI 格式接口的 strict（Beta）端点：探针实测主站不执行函数的 strict 声明，
#: /beta 在请求时校验 strict 函数的 Schema（不合规的 Schema 返回 400），输出取值不保证受约束（关闭思考时枚举仍可能越界），
#: 宿主本地的严格校验仍是权威
#: （docs/design/06-agent.md ；探针 scripts/probe_deepseek_strict.py）。
DEEPSEEK_BASE_URL = "https://api.deepseek.com/beta"
DEEPSEEK_MODEL = "deepseek-flash"
#: DeepSeek 官方模型与价格文档声明的上下文长度。
DEEPSEEK_CONTEXT_LENGTH = 1_000_000


class IndependentModelClient:
    """@brief 独立业务模型请求共享身份校验、单次传输和响应序列化。"""

    def __init__(self, model_identity: dict, purpose: str):
        """@brief 建立与冻结身份一致的客户端，业务名决定错误文案。"""
        from agentsociety2.config import build_client_for_role

        client = build_client_for_role("default")
        if (client.model_name != model_identity["requested_model"]
                or client.base_url != model_identity["base_url"]):
            raise ProtocolError(f"{purpose} client identity disagrees with the run manifest")
        if model_identity["max_retries"] != 0:
            raise ProtocolError(f"{purpose} request parameters disagree with the run manifest")
        self.client = client
        self.model_name = model_identity["requested_model"]
        self.purpose = purpose

    async def call(self, request: dict) -> dict:
        """@brief 发出单次请求并返回实际可持久化的对象响应。"""

        response = await self.client.call(**copy.deepcopy(request))
        if hasattr(response, "model_dump"):
            payload = response.model_dump(mode="json")
        elif isinstance(response, dict):
            payload = dict(response)
        else:
            raise ProtocolError(f"{self.purpose} model response is not serializable")
        if not isinstance(payload, dict):
            raise ProtocolError(f"{self.purpose} model response is not a serialized object")
        return payload
