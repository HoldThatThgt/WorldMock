"""@brief 发送模型请求前计量完整上下文，超过阈值时先结束本秒续接，再结构化浓缩 user 中的进度层（docs/design/06-agent.md）。

计量覆盖原生 tools、系统层、六层 JSON、续接消息（含回传的 reasoning_content）、此后的原生消息与预留输出。达到上下文 85% 时依次：
0. 提前结束本秒续接：链前缀换成新快照（系统层与此刻的六层），思考不再出现；
1. 从旧到新逐条去掉未受保护记录的 self_note；
2. 把最旧一段连续的未受保护记录（紧邻其前的 condensed 记录并入）折成一条 condensed 记录，重复到阈值以下或无可折叠；
每步之后重新计数。受保护记录从不改变；工具层、人设层、记忆层、待办层与环境层不浓缩。仍超限时按层记录 token 用量
并以容量故障停止，不静默删减。

工具层的可调用索引 callable_index 在 user 消息里，照样计入整次请求的容量计数（阈值与浓缩步骤不变），也从不被浓缩或删减；
它不计入单次呈现上限（docs/design/04-simulation-engine.md）。容量故障的分层记录另单列它的 token 用量。
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
from typing import Any

from litellm import token_counter

from worldmock.life.narration import render_layer_json
from worldmock.model_endpoint import model_context_length
from worldmock.input_models import ModelMetadata


CONTEXT_THRESHOLD = 0.85
#: 进度层的层名与折叠记录的类型。
_PROGRESS_LAYER = "进度层"
_CONDENSED = "condensed"
#: 工具层中可调用索引的键（容量故障记录单列它的用量）。
_INDEX_KEY = "callable_index"


@dataclass(frozen=True, slots=True)
class ProgressBudget:
    """@brief 可浓缩的请求部分：user 消息中的层数组与进度层记录的私有元数据（受保护标记与 settled 句）。

    续接的请求另带 fresh_messages（不带续接的新快照 system 与 user，layers 即其中的层）与 chain_length（请求开头属于
    续接链的消息条数），供第 0 步把链前缀换成新快照；不续接时两者为 None 与 0。
    """

    layers: tuple[dict, ...]
    meta: tuple[dict, ...]
    user_index: int = 1
    fresh_messages: tuple[dict, ...] | None = None
    chain_length: int = 0


@dataclass(frozen=True, slots=True)
class FittedRequest:
    """@brief 容量预检通过、实际要发送的请求，与发送前为它计量的输入 token 数。"""

    #: 实际发送的请求；低于阈值时就是传入的那个对象。
    request: dict[str, Any]
    #: request 的消息与工具定义的 token 数（不含预留输出），等于 prompt_token_count(request)。
    prompt_tokens: int


def prompt_token_count(request: dict[str, Any]) -> int:
    """@brief 计量实际发送的消息与工具定义。"""
    try:
        return token_counter(model=request["model"], messages=request["messages"],
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


def fit_request(request: dict[str, Any], metadata: ModelMetadata | dict,
                budget: ProgressBudget | None = None) -> FittedRequest:
    """@brief 请求低于阈值时原样放进结果；否则先结束本秒续接，再按第四章步骤浓缩进度层，每步重计，仍超限即容量故障。
    结果带实际发送的请求与它的输入 token 数（最后一次计量减去预留输出），用量日志直接复用。"""
    frozen = metadata if isinstance(metadata, ModelMetadata) else ModelMetadata.model_validate(metadata)
    reserve = request.get("max_tokens")
    if type(reserve) is not int or not 0 < reserve <= frozen.max_output_tokens:
        logging.error("Model request output reserve exceeds verified service capacity")
        raise RuntimeError("model request output reserve exceeds verified service capacity")
    limit = model_context_length(request["model"], frozen) * CONTEXT_THRESHOLD
    measured = request_token_count(request)
    if measured < limit:
        return FittedRequest(request, measured - reserve)
    if budget is not None and budget.fresh_messages is not None:
        request = {**request, "messages": [*budget.fresh_messages, *request["messages"][budget.chain_length:]]}
        measured = request_token_count(request)
        if measured < limit:
            return FittedRequest(request, measured - reserve)
    layers = list(budget.layers) if budget is not None else []
    index = next((i for i, layer in enumerate(layers) if layer.get("layer") == _PROGRESS_LAYER), None)
    if index is not None:
        records, meta = _progress_records(layers[index], budget.meta)
        for records, meta in _condensation_steps(records, meta):
            layers[index] = {**layers[index], "events": records}
            request = _with_user_layers(request, layers, budget.user_index)
            measured = request_token_count(request)
            if measured < limit:
                return FittedRequest(request, measured - reserve)
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
    """@brief 按系统层、工具定义、各 user 层（工具层的可调用索引另单列，它已含在工具层之内）、此后的原生消息与预留输出
    分层记录 token 用量。"""
    counts: dict[str, int] = {
        "system": token_counter(model=request["model"], messages=request["messages"][:1]),
        "tools": token_counter(model=request["model"], messages=[], tools=request.get("tools")),
    }
    for layer in layers:
        counts[str(layer.get("layer"))] = token_counter(model=request["model"], text=render_layer_json(layer))
        if isinstance(layer.get(_INDEX_KEY), dict):
            counts[f"{layer['layer']}.{_INDEX_KEY}"] = token_counter(model=request["model"],
                                                                     text=render_layer_json(layer[_INDEX_KEY]))
    counts["continuation_messages"] = token_counter(model=request["model"], messages=request["messages"][2:])
    counts["reserved_output"] = request["max_tokens"]
    logging.error("Model request capacity exceeded: total=%s threshold=%s parts=%s", measured, limit, counts)


def compare_usage(estimate: int, response: dict[str, Any] | None) -> None:
    """@brief 用供应商返回的实际输入 token 校核发送前的估算；估算是容量预检为同一请求计量的数（FittedRequest.prompt_tokens），
    这里不再重数。"""
    usage = response.get("usage") if isinstance(response, dict) else None
    actual = usage.get("prompt_tokens") if isinstance(usage, dict) else None
    if not isinstance(actual, int):
        return
    logging.info("Model prompt token estimate checked: estimated=%s actual=%s difference=%s",
                 estimate, actual, actual - estimate)
