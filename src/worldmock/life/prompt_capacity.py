"""@brief 在发送模型请求前计量完整上下文，并仅浓缩进度层历史投影。"""

from __future__ import annotations

import copy
import logging
from functools import lru_cache
from typing import Any

import requests
from litellm import token_counter

from worldmock.life.narration import render_facts


CONTEXT_THRESHOLD = 0.85
MODEL_METADATA_URL = "https://openrouter.ai/api/v1/model/deepseek/deepseek-v4.1-flash"
MODEL_ALIAS = "deepseek-v4.1-flash"
TOKENIZER_MODEL = "openrouter/deepseek/deepseek-v4.1-flash"


@lru_cache(maxsize=1)
def model_context_length(model: str) -> int:
    """@brief 从当前模型供应商读取实际上下文窗口，拒绝身份不符。"""
    if model != MODEL_ALIAS:
        logging.error("Model context metadata is unavailable for %s", model)
        raise RuntimeError("model context metadata is unavailable")
    try:
        response = requests.get(MODEL_METADATA_URL, timeout=15)
        response.raise_for_status()
        metadata = response.json()["data"]
        if metadata["id"] != "deepseek/deepseek-v4.1-flash":
            raise ValueError("model metadata identity differs from the request")
        context_length = metadata["context_length"]
        if not isinstance(context_length, int) or context_length <= 0:
            raise ValueError("model context length is invalid")
    except (requests.RequestException, KeyError, TypeError, ValueError) as error:
        logging.error("Model context metadata lookup failed: %s", error)
        raise RuntimeError("model context metadata lookup failed") from error
    return context_length


def request_token_count(request: dict[str, Any]) -> int:
    """@brief 计入最终消息、工具定义和明确预留的最大输出 token。"""
    try:
        prompt = token_counter(model=TOKENIZER_MODEL, messages=request["messages"],
                               tools=request.get("tools"))
    except Exception as error:
        logging.error("Model request token count failed: %s", error)
        raise RuntimeError("model request token count failed") from error
    reserve = request.get("max_tokens")
    if not isinstance(reserve, int) or reserve <= 0:
        logging.error("Model request output reserve is missing")
        raise RuntimeError("model request output reserve is missing")
    return prompt + reserve


def _terminal_sessions(progress: dict[str, Any]) -> tuple[list[dict], list[dict]]:
    """@brief 分开保留进行中和暂停会话，以及可浓缩的终态会话。"""
    live, terminal = [], []
    for row in progress["sessions"]:
        (live if row["phase"] in {"进行中", "已暂停"} else terminal).append(row)
    return live, terminal


def _history_row(task_name: str, sessions: list[dict[str, Any]]) -> dict[str, Any]:
    """@brief 以最新一条为依据，保留历史数量及客观和主观归属。"""
    latest = sessions[0]
    return {"task_name": task_name, "phase": "历史摘要", "session_count": len(sessions),
            "latest_objective_progress": latest["objective_progress"],
            "latest_subjective_progress": latest["subjective_progress"],
            "subjective_status": "未经核验"}


def compact_by_task(progress: dict[str, Any]) -> dict[str, Any]:
    """@brief 将终态会话按可读任务名分组，原样保留当前结果和提醒。"""
    live, terminal = _terminal_sessions(progress)
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in terminal:
        groups.setdefault(row["task_name"], []).append(row)
    compacted = copy.deepcopy(progress)
    compacted["sessions"] = live + [_history_row(name, rows) for name, rows in groups.items()]
    return compacted


def compact_all_history(progress: dict[str, Any]) -> dict[str, Any]:
    """@brief 合并所有终态摘要，保留最新事实、自述和会话总数。"""
    live, historical = _terminal_sessions(progress)
    if not historical:
        return copy.deepcopy(progress)
    latest = historical[0]
    total = sum(row.get("session_count", 1) for row in historical)
    aggregate = {"task_name": "历史活动", "phase": "历史摘要", "session_count": total,
                 "latest_objective_progress": latest["latest_objective_progress"],
                 "latest_subjective_progress": latest["latest_subjective_progress"],
                 "subjective_status": "未经核验"}
    compacted = copy.deepcopy(progress)
    compacted["sessions"] = live + [aggregate]
    return compacted


def _replace_progress(request: dict[str, Any], facts: dict[str, Any],
                      progress: dict[str, Any]) -> dict[str, Any]:
    """@brief 仅替换本轮 user 消息中的进度层，不改观察事件或其他消息。"""
    original = render_facts(facts)
    updated_facts = {**facts, "progress": progress}
    replacement = render_facts(updated_facts)
    candidate = copy.deepcopy(request)
    for message in candidate["messages"]:
        if message["role"] == "user" and original in message.get("content", ""):
            message["content"] = message["content"].replace(original, replacement, 1)
            return candidate
    logging.error("Current facts are absent from the model request")
    raise RuntimeError("current facts are absent from the model request")


def _layer_counts(layers: dict[str, str], request: dict[str, Any]) -> dict[str, int]:
    """@brief 给容量故障日志提供各提示层的 token 诊断值。"""
    counts = {name: token_counter(model=TOKENIZER_MODEL, text=value)
              for name, value in layers.items()}
    counts["system"] = token_counter(model=TOKENIZER_MODEL, messages=request["messages"][:1])
    counts["conversation_history"] = token_counter(
        model=TOKENIZER_MODEL, messages=request["messages"][2:])
    counts["tools"] = token_counter(model=TOKENIZER_MODEL, messages=[], tools=request.get("tools"))
    counts["reserved_output"] = request["max_tokens"]
    return counts


def fit_request(request: dict[str, Any], facts: dict[str, Any] | None = None,
                layers: dict[str, str] | None = None) -> dict[str, Any]:
    """@brief 超过窗口 85% 时仅浓缩历史进度，并在每阶段重计完整请求。"""
    context = model_context_length(request["model"])
    measured = request_token_count(request)
    if measured < context * CONTEXT_THRESHOLD:
        return request
    if facts is not None:
        original_request = request
        progress = facts["progress"]
        for compact in (compact_by_task, compact_all_history):
            progress = compact(progress)
            request = _replace_progress(original_request, facts, progress)
            measured = request_token_count(request)
            if measured < context * CONTEXT_THRESHOLD:
                return request
    breakdown = _layer_counts(layers or {}, request)
    logging.error("Model request capacity exceeded: total=%s context=%s layers=%s",
                  measured, context, breakdown)
    raise RuntimeError("model request capacity exceeded")


def compare_usage(request: dict[str, Any], response: dict[str, Any] | None) -> None:
    """@brief 用供应商返回的实际输入 token 校核发送前估算。"""
    usage = response.get("usage") if isinstance(response, dict) else None
    actual = usage.get("prompt_tokens") if isinstance(usage, dict) else None
    if not isinstance(actual, int):
        return
    estimate = request_token_count(request) - request["max_tokens"]
    logging.info("Model prompt token estimate checked: estimated=%s actual=%s difference=%s",
                 estimate, actual, actual - estimate)
