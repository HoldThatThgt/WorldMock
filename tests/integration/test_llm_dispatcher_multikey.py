"""LLM Router 多 key 负载分摊合同：comma-joined key 池 -> N deployment。

只测 Router 构造（无网络）：deployment 数、model_name 分组、openai/ 前缀、
api_base 透传与空段剔除。模型身份记录 openai_compatible 的覆盖在
test_runtime.py（_model_identity / two_stage_model_identity）。
"""

from __future__ import annotations

import os

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.config.llm_dispatcher import _build_router

NODE_BASE = "https://api.deepseek.com"
NODE_MODEL = "deepseek-flash"


def _deployments(api_key: str):
    router = _build_router(NODE_BASE, api_key, NODE_MODEL)
    return list(router.model_list)


def test_key_pool_builds_one_deployment_per_key():
    """N 把 key -> 同一 model_name 的 N 个 deployment（litellm 原生负载分摊）。"""
    deployments = _deployments("key-a,key-b,key-c")
    assert len(deployments) == 3
    assert {dep["model_name"] for dep in deployments} == {NODE_MODEL}
    keys = [dep["litellm_params"]["api_key"] for dep in deployments]
    assert keys == ["key-a", "key-b", "key-c"]


def test_single_key_builds_single_deployment():
    """单 key（runtime 已 comma-join）仍构造恰好一个 deployment。"""
    deployments = _deployments("key-a")
    assert len(deployments) == 1
    assert deployments[0]["litellm_params"]["api_key"] == "key-a"


def test_deployments_use_openai_prefix_and_passthrough_base():
    """每个 deployment 走 openai 兼容路径：openai/ 前缀 + api_base 原样透传。"""
    for dep in _deployments("key-a,key-b"):
        params = dep["litellm_params"]
        assert params["model"] == f"openai/{NODE_MODEL}"
        assert params["api_base"] == NODE_BASE


def test_deployments_have_distinct_generated_ids():
    """同组 deployment 的生成 id 互不相同（distinct api_key -> distinct hash）。"""
    ids = [dep["model_info"]["id"] for dep in _deployments("key-a,key-b,key-c")]
    assert len(ids) == len(set(ids)) == 3
