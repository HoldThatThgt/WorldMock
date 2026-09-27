"""@brief 发送模型请求前计量完整上下文，超过阈值时只结构化浓缩 user 中的进度层（docs/dev/SPEC-persona-prompt.md 第四章）。

计量覆盖原生 tools、系统层、六层 JSON、此后的原生消息与预留输出。达到上下文 85% 时依次：
1. 从旧到新逐条去掉未受保护记录的 self_note；
2. 把最旧一段连续的未受保护记录（紧邻其前的 condensed 记录并入）折成一条 condensed 记录，重复到阈值以下或无可折叠；
每步之后重新计数。受保护记录从不改变；工具层、人设层、记忆层、待办层与环境层不浓缩。仍超限时按层记录 token 用量
并以容量故障停止，不静默删减。
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
from typing import Any

from litellm import token_counter

from worldmock.life.narration import render_layer_json
from worldmock.model_endpoint import DEEPSEEK_CONTEXT_LENGTH, DEEPSEEK_MODEL


CONTEXT_THRESHOLD = 0.85
MODEL_ALIAS = DEEPSEEK_MODEL
TOKENIZER_MODEL = f"deepseek/{DEEPSEEK_MODEL}"
#: 进度层的层名与折叠记录的类型。
_PROGRESS_LAYER = "进度层"
_CONDENSED = "condensed"


@dataclass(frozen=True, slots=True)
class ProgressBudget:
    """@brief 可浓缩的请求部分：user 消息中的层数组与进度层记录的私有元数据（受保护标记与 settled 句）。"""

    layers: tuple[dict, ...]
    meta: tuple[dict, ...]
    user_index: int = 1


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


def fit_request(request: dict[str, Any], budget: ProgressBudget | None = None) -> dict[str, Any]:
    """@brief 请求低于阈值时原样返回；否则按第四章步骤浓缩进度层，每步重计，仍超限即容量故障。"""
    limit = model_context_length(request["model"]) * CONTEXT_THRESHOLD
    measured = request_token_count(request)
    if measured < limit:
        return request
    layers = list(budget.layers) if budget is not None else []
    index = next((i for i, layer in enumerate(layers) if layer.get("layer") == _PROGRESS_LAYER), None)
    if index is not None:
        records, meta = _progress_records(layers[index], budget.meta)
        for records, meta in _condensation_steps(records, meta):
            layers[index] = {**layers[index], "events": records}
            request = _with_user_layers(request, layers, budget.user_index)
            measured = request_token_count(request)
            if measured < limit:
                return request
    _log_capacity_failure(request, layers, measured, limit)
    raise RuntimeError("model request capacity exceeded")


def _progress_records(layer: dict[str, Any], meta: tuple[dict, ...]) -> tuple[list[dict], list[dict]]:
    """@brief 复制进度层记录与逐条对齐的私有元数据；两者条数不一致是损坏的宿主观察。"""
    records = copy.deepcopy(layer["events"])
    if len(records) != len(meta):
        logging.error("Progress metadata does not align with progress records")
        raise RuntimeError("progress metadata does not align with progress records")
    return records, [dict(item) for item in meta]


def _condensation_steps(records: list[dict], meta: list[dict]):
    """@brief 逐步产出浓缩后的（记录, 元数据）：先从旧到新逐条去 self_note，再逐段折叠为 condensed 记录。"""
    for position, record in enumerate(records):
        if not meta[position]["protected"] and "self_note" in record:
            del record["self_note"]
            yield records, meta
    while (run := _oldest_foldable_run(records, meta)) is not None:
        start, end = run
        folded = records[start:end]
        lines = [line for item in meta[start:end] for line in item["lines"]]
        condensed = {"occurred_at": folded[0]["occurred_at"], "type": _CONDENSED,
                     "until_at": folded[-1]["occurred_at"], "lines": lines}
        records = [*records[:start], condensed, *records[end:]]
        meta = [*meta[:start], {"protected": False, "lines": lines}, *meta[end:]]
        yield records, meta


def _oldest_foldable_run(records: list[dict], meta: list[dict]) -> tuple[int, int] | None:
    """@brief 最旧一段连续的未受保护、非 condensed 记录的区间 [start, end)，紧邻其前的 condensed 记录并入；没有时为 None。"""
    for start, record in enumerate(records):
        if meta[start]["protected"] or record["type"] == _CONDENSED:
            continue
        end = start
        while end < len(records) and not meta[end]["protected"] and records[end]["type"] != _CONDENSED:
            end += 1
        if start > 0 and records[start - 1]["type"] == _CONDENSED:
            start -= 1
        return start, end
    return None


def _with_user_layers(request: dict[str, Any], layers: list[dict], user_index: int) -> dict[str, Any]:
    """@brief 只替换 user 消息中的层数组，不改其他消息与观察事件。"""
    candidate = copy.deepcopy(request)
    candidate["messages"][user_index] = {**candidate["messages"][user_index],
                                         "content": render_layer_json(layers)}
    return candidate


def _log_capacity_failure(request: dict[str, Any], layers: list[dict], measured: int, limit: float) -> None:
    """@brief 按系统层、工具定义、各 user 层、此后的原生消息与预留输出分层记录 token 用量。"""
    counts: dict[str, int] = {
        "system": token_counter(model=TOKENIZER_MODEL, messages=request["messages"][:1]),
        "tools": token_counter(model=TOKENIZER_MODEL, messages=[], tools=request.get("tools")),
    }
    for layer in layers:
        counts[str(layer.get("layer"))] = token_counter(model=TOKENIZER_MODEL, text=render_layer_json(layer))
    counts["continuation_messages"] = token_counter(model=TOKENIZER_MODEL, messages=request["messages"][2:])
    counts["reserved_output"] = request["max_tokens"]
    logging.error("Model request capacity exceeded: total=%s threshold=%s parts=%s", measured, limit, counts)


def compare_usage(request: dict[str, Any], response: dict[str, Any] | None) -> None:
    """@brief 用供应商返回的实际输入 token 校核发送前估算。"""
    usage = response.get("usage") if isinstance(response, dict) else None
    actual = usage.get("prompt_tokens") if isinstance(usage, dict) else None
    if not isinstance(actual, int):
        return
    estimate = prompt_token_count(request)
    logging.info("Model prompt token estimate checked: estimated=%s actual=%s difference=%s",
                 estimate, actual, actual - estimate)
