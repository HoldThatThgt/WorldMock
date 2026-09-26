"""@brief 在发送模型请求前计量完整上下文，并仅浓缩进度层历史投影。

浓缩单位是会话（地点停留或一段路）中已收尾的活动（docs/dev/SPEC-time-and-energy.md 第七章「会话与活动的字段」）：
超过窗口 85% 时先按会话合并较旧会话里已收尾的活动，仍超限再合并全部已收尾活动；进行中、暂停中与待收尾的活动、
当前结果与提醒完整保留。
"""

from __future__ import annotations

import copy
import logging
from typing import Any

from litellm import token_counter

from worldmock.life.activity_control import PHASE_ACTIVE, PHASE_PAUSED, PHASE_PENDING, SELF_REPORT_MARK
from worldmock.life.narration import render_facts
from worldmock.model_endpoint import DEEPSEEK_CONTEXT_LENGTH, DEEPSEEK_MODEL


CONTEXT_THRESHOLD = 0.85
MODEL_ALIAS = DEEPSEEK_MODEL
TOKENIZER_MODEL = f"deepseek/{DEEPSEEK_MODEL}"


def model_context_length(model: str) -> int:
    """@brief 返回冻结模型合同中的官方上下文长度，拒绝身份不符。"""
    if model != MODEL_ALIAS:
        logging.error("Model context metadata is unavailable for %s", model)
        raise RuntimeError("model context metadata is unavailable")
    return DEEPSEEK_CONTEXT_LENGTH


def prompt_token_count(request: dict[str, Any]) -> int:
    """@brief 计量实际发送的消息与工具定义。"""
    try:
        return token_counter(model=TOKENIZER_MODEL, messages=request["messages"],
                             tools=request.get("tools"))
    except Exception as error:
        logging.error("Model request token count failed: %s", error)
        raise RuntimeError("model request token count failed") from error


def request_token_count(request: dict[str, Any]) -> int:
    """@brief 容量预检计入输入和明确预留的最大输出 token。"""
    reserve = request.get("max_tokens")
    if not isinstance(reserve, int) or reserve <= 0:
        logging.error("Model request output reserve is missing")
        raise RuntimeError("model request output reserve is missing")
    return prompt_token_count(request) + reserve


#: 浓缩时完整保留的活动阶段：进行中、已暂停与刚结束（待收尾）；只合并已收尾（或截止结束）的活动。
LIVE_PHASES = frozenset({PHASE_ACTIVE, PHASE_PAUSED, PHASE_PENDING})


def _split_activities(rows: list[dict[str, Any]]) -> tuple[list[dict], list[dict]]:
    """@brief 分开完整保留的进行中、暂停与待收尾活动行，以及可浓缩的已收尾活动行（含上一阶段的摘要行）。"""
    live, done = [], []
    for row in rows:
        (live if row["phase"] in LIVE_PHASES else done).append(row)
    return live, done


def _history_row(done: list[dict[str, Any]]) -> dict[str, Any]:
    """@brief 把已收尾的活动行合并为一行摘要：保留条数与最近一条的客观进度及注明来源的自述。"""
    latest = done[-1]
    count = sum(row.get("activity_count", 1) for row in done)
    row = {"name": f"较早收尾的{count}项活动", "phase": "历史摘要", "activity_count": count,
           "latest_objective_progress": latest.get("objective_progress", latest.get("latest_objective_progress"))}
    note = latest.get("subjective_progress", latest.get("latest_subjective_progress"))
    if note:
        row.update(latest_subjective_progress=note, subjective_status=SELF_REPORT_MARK)
    return row


def compact_by_session(progress: dict[str, Any]) -> dict[str, Any]:
    """@brief 第一阶段：较旧会话（最近一段之外）里已收尾的活动按会话合并为一行摘要，原样保留当前结果和提醒。"""
    compacted = copy.deepcopy(progress)
    for row in compacted["sessions"][:-1]:
        live, done = _split_activities(row["activities"])
        row["activities"] = live + ([_history_row(done)] if done else [])
    return compacted


def compact_all_history(progress: dict[str, Any]) -> dict[str, Any]:
    """@brief 第二阶段：全部已收尾活动合并为一行「历史活动」；进行中、暂停与待收尾的活动和最近一段会话保留。"""
    compacted = copy.deepcopy(progress)
    sessions, done = [], []
    for index, row in enumerate(compacted["sessions"]):
        live, finished = _split_activities(row["activities"])
        done.extend(finished)
        if live or index == len(compacted["sessions"]) - 1:
            sessions.append({**row, "activities": live})
    if done:
        sessions.insert(0, {"name": "历史活动", "activities": [_history_row(done)]})
    compacted["sessions"] = sessions
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
        for compact in (compact_by_session, compact_all_history):
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
    estimate = prompt_token_count(request)
    logging.info("Model prompt token estimate checked: estimated=%s actual=%s difference=%s",
                 estimate, actual, actual - estimate)
