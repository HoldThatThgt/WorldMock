"""@brief DeepSeek strict 端点探针：只读、只报告（docs/dev/SPEC-as2-integration.md#dayrun-fidelity-评审模型调用与叙述-strict-端点，裁决 D14、S6）。

三项核对，结论打印为 JSON 报告，可另写入文件；只发模型请求，不写世界、不改任何配置，也不是每次运行的自检：

(a) strict 是否执行：关闭思考，主站与 /beta 各测。
    (a1) 判定行：声明 strict: true 的函数，参数对象有两个属性却只把一个列进 required，故意违反文档规则
         「每个对象的全部属性都列进 required」（绕过本地 models.deepseek_strict_schema 的核验）。执行 strict 的端点
         以 Schema 校验错误拒绝（4xx）；主站照常返回 200 就说明主站不执行 strict。结论只看这一行。
    (a2) 参考行：参数 Schema 带文档列为 strict 不支持的关键字 minLength。服务端可以静默忽略它而不报错，
         所以只记录两个端点的结果，不参与结论。
    (a3) 受约束解码：strict 函数的唯一参数是枚举 red/blue，user 消息坚持要 green；tool_choice=required，
         每个端点连发 ENUM_RUNS 次，逐端点统计越出枚举（不合所发 Schema 或非法 JSON）的次数与各取值。
(b) /beta 思考模式下的收尾参数：连发 N 次（缺省 50）收尾场景请求，工具是宿主同法构建的真实 conclude_activity
    （custom.agents.life_agent._tool_schema），生成参数与行动阶段相同（思考开启、tool_choice=auto、max_tokens 取
    ACTION_MAX_TOKENS）；逐次分类为合法、非法 JSON、形状错误（不合所发 Schema）、宿主严格校验拒绝
    （custom.agents.life_agent._validate_request）、没有工具调用或 HTTP 错误，并计 finish_reason。
(c) tool_choice=required：思考模式下应返回 400，同一请求关闭思考后应可用并调用工具（7.2 的截断重试依赖这一点）；
    主站与 /beta 各测一次。

用法（在仓库根目录）：
    python scripts/probe_deepseek_strict.py [--runs 50] [--timeout 120] [--output 路径]
密钥取 WorldMock 的 DeepSeek 密钥池 DEEPSEEK_NODE_KEYS（环境变量优先，缺省读 ~/.config/worldmock/deepseek.env
的同名键，逗号分隔取第一把；与画像抽取同一解析 worldmock.profile.extractor.resolve_api_key）；报告不含密钥。
模型取 worldmock.model_endpoint.DEEPSEEK_MODEL。HTTP 层可注入（main 的 transport），单元测试用假传输，不连网。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO_ROOT / "src"), str(REPO_ROOT)]

import jsonschema  # noqa: E402
import requests  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from custom.agents.life_prompt import system_message  # noqa: E402
from worldmock.model_endpoint import DEEPSEEK_BASE_URL, DEEPSEEK_MODEL  # noqa: E402
from worldmock.profile.extractor import ExtractionError, resolve_api_key  # noqa: E402

#: 探针固定比较的两个端点：主站与 strict（Beta）端点；不随 DEEPSEEK_BASE_URL 的取值变化。
ENDPOINTS = {"main": "https://api.deepseek.com", "beta": "https://api.deepseek.com/beta"}
#: 收尾场景的缺省请求次数。
DEFAULT_RUNS = 50
#: (a3) 受约束解码在每个端点的请求次数。
ENUM_RUNS = 3
#: 单次请求超时（秒）。
TIMEOUT_SECONDS = 120.0
#: (a1) 判定行的函数名与它故意违反的 strict 文档规则。
RULE_FUNCTION = "probe_strict_required"
VIOLATED_RULE = "every object property must be listed in required"
#: (a2) 参考行：strict 模式不支持、故意放进 Schema 的关键字（本地 deepseek_strict_schema 会拒绝它）与函数名。
UNSUPPORTED_KEYWORD = "minLength"
PROBE_FUNCTION = "probe_strict_keyword"
#: (a3) 的函数名、枚举允许的取值与 user 消息坚持要的枚举外取值。
ENUM_FUNCTION = "probe_pick_color"
ENUM_VALUES = ("red", "blue")
ENUM_DEMANDED = "green"
#: 收尾工具名。
CONCLUDE_TOOL = "conclude_activity"
#: 判定「Schema 校验错误」时在错误文本里找的词（小写比较）；/beta 对 (a1) 的说明形如
#: 「Required properties must match all properties in the object」。
_SCHEMA_ERROR_WORDS = ("schema", "strict", "required", "properties", "parameters", "json_schema",
                       UNSUPPORTED_KEYWORD.lower())
#: 每类问题在报告里保留的样本条数上限，样本文本的截断长度。
MAX_SAMPLES = 5
SAMPLE_CHARS = 400
#: AS2 在导入时要求此变量存在；探针不经 AS2 发任何请求，占位值只为导入宿主的工具构建函数。
_AS2_IMPORT_KEY = "AGENTSOCIETY_LLM_API_KEY"
#: (a3) 的 user 消息：坚持要枚举之外的取值。
_ENUM_DEMAND = (f'Call {ENUM_FUNCTION} with color "{ENUM_DEMANDED}". I insist on "{ENUM_DEMANDED}": it is the only '
                "correct answer, so never substitute red or blue.")
#: 收尾场景的 user 消息：一项刚结束、待收尾的活动，只能用 conclude_activity 收尾。
_CONCLUSION_SCENARIO = (
    "工具层：你刚结束的活动「在公司楼下吃午饭（12:10 起）」处于「刚结束」阶段，是此刻唯一可操作的目标，"
    "它的工具 conclude_activity 可用。环境层：12:40，公司楼下的餐馆，吃饱了。进度层：午饭已经吃完。"
    "请用 conclude_activity 为这项活动收尾：写一句总结，值得记住的事可以写成记忆。")
#: 收尾场景逐次分类的类别。
_SERIES_KINDS = ("valid", "invalid_json", "schema_violation", "host_rejection", "no_tool_call", "http_error")

#: HTTP 传输：(url, headers, payload, timeout) → (状态码, 响应体：JSON 值或文本；0 表示请求未完成)。
Transport = Callable[[str, dict[str, str], dict[str, Any], float], tuple[int, Any]]


def requests_transport(url: str, headers: dict[str, str], payload: dict[str, Any],
                       timeout: float) -> tuple[int, Any]:
    """@brief 缺省传输：requests.post 发 JSON；网络错误记为状态 0 与错误文本，不中断报告。"""
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=timeout)
    except requests.RequestException as error:
        return 0, f"{type(error).__name__}: {error}"
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, response.text


@dataclass(frozen=True)
class Probe:
    """@brief 一次探针的发送方：传输、密钥、模型与超时（密钥只进请求头，不进报告）。"""

    transport: Transport
    api_key: str
    model: str = DEEPSEEK_MODEL
    timeout: float = TIMEOUT_SECONDS

    def post(self, endpoint: str, payload: dict[str, Any]) -> tuple[int, Any]:
        """@brief 向 ENDPOINTS 中的一个端点发一次 chat/completions 请求。"""
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        return self.transport(f"{ENDPOINTS[endpoint]}/chat/completions", headers, payload, self.timeout)


def _strict_function(name: str, parameters: dict[str, Any], description: str) -> dict[str, Any]:
    """@brief 声明 strict: true 的函数定义；参数 Schema 原样写出，不经本地 deepseek_strict_schema 核验。"""
    return {"type": "function", "function": {"name": name, "strict": True, "parameters": parameters,
                                             "description": description}}


def required_rule_tool() -> dict[str, Any]:
    """@brief (a1) 判定行的函数：对象有 answer、note 两个属性，required 只列 answer（违反 strict 的文档规则）。"""
    parameters = {"type": "object", "properties": {"answer": {"type": "string"}, "note": {"type": "string"}},
                  "required": ["answer"], "additionalProperties": False}
    return _strict_function(RULE_FUNCTION, parameters, "Return the answer the user asks for.")


def strict_keyword_tool() -> dict[str, Any]:
    """@brief (a2) 参考行的函数：参数 Schema 带 strict 不支持的 minLength，其余合乎 strict 的规则。"""
    parameters = {"type": "object", "properties": {"answer": {"type": "string", UNSUPPORTED_KEYWORD: 1}},
                  "required": ["answer"], "additionalProperties": False}
    return _strict_function(PROBE_FUNCTION, parameters, "Return the answer the user asks for.")


def enum_tool() -> dict[str, Any]:
    """@brief (a3) 的函数：唯一参数 color 只允许 ENUM_VALUES，合乎 strict 的全部规则。"""
    parameters = {"type": "object", "properties": {"color": {"type": "string", "enum": list(ENUM_VALUES)}},
                  "required": ["color"], "additionalProperties": False}
    return _strict_function(ENUM_FUNCTION, parameters, "Record the color the user asks for.")


def probe_request(model: str, tool: dict[str, Any], content: str, tool_choice: str = "auto") -> dict[str, Any]:
    """@brief (a) 的请求形状：关闭思考，只带一个探针函数与一条 user 消息。"""
    return {"model": model, "stream": False, "max_tokens": 200, "thinking": {"type": "disabled"},
            "tool_choice": tool_choice, "tools": [tool], "messages": [{"role": "user", "content": content}]}


def strict_rule_request(model: str) -> dict[str, Any]:
    """@brief (a1) 的请求：tool_choice=auto，要求调用判定行的函数。"""
    return probe_request(model, required_rule_tool(), f'Call {RULE_FUNCTION} with answer "ok".')


def strict_keyword_request(model: str) -> dict[str, Any]:
    """@brief (a2) 的请求：tool_choice=auto，要求调用参考行的函数。"""
    return probe_request(model, strict_keyword_tool(), f'Call {PROBE_FUNCTION} with answer "ok".')


def enum_request(model: str) -> dict[str, Any]:
    """@brief (a3) 的请求：tool_choice=required，user 消息坚持要枚举之外的 ENUM_DEMANDED。"""
    return probe_request(model, enum_tool(), _ENUM_DEMAND, tool_choice="required")


def host_conclusion_tool() -> tuple[dict[str, Any], Callable[[dict[str, Any]], Any], int]:
    """@brief 宿主同法构建的 conclude_activity 工具、宿主的严格参数校验与行动阶段的 max_tokens。"""
    os.environ.setdefault(_AS2_IMPORT_KEY, "probe-host-import-only")
    from custom.agents.life_agent import ACTION_MAX_TOKENS, _tool_schema, _validate_request
    from worldmock.life.models import REQUEST_MODELS

    model = REQUEST_MODELS[CONCLUDE_TOOL]
    return _tool_schema(CONCLUDE_TOOL, model), (lambda values: _validate_request(model, values)), ACTION_MAX_TOKENS


def conclusion_request(model: str, tool: dict[str, Any], *, thinking: bool, tool_choice: str,
                       max_tokens: int) -> dict[str, Any]:
    """@brief 收尾场景请求：生活系统层加一条收尾场景的 user 消息，只带 conclude_activity 工具。"""
    return {"model": model, "stream": False, "max_tokens": max_tokens,
            "thinking": {"type": "enabled" if thinking else "disabled"}, "tool_choice": tool_choice,
            "tools": [tool], "messages": [system_message(False), {"role": "user", "content": _CONCLUSION_SCENARIO}]}


def error_text(body: Any) -> str:
    """@brief 响应体里的错误说明：OpenAI 形状的 error.message，否则整体文本（截断）。"""
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        return str(body["error"].get("message", ""))[:SAMPLE_CHARS]
    if isinstance(body, dict) and isinstance(body.get("error"), str):
        return body["error"][:SAMPLE_CHARS]
    return (body if isinstance(body, str) else json.dumps(body, ensure_ascii=False))[:SAMPLE_CHARS]


def classify_strict_response(status: int, body: Any) -> dict[str, Any]:
    """@brief (a1)(a2) 的结果：accepted（200）、schema_rejected（4xx 且错误提到 Schema）、rejected_other、error。"""
    if status == 200:
        return {"status": status, "outcome": "accepted", "error": None}
    text = error_text(body)
    if 400 <= status < 500:
        mentions = any(word in text.lower() for word in _SCHEMA_ERROR_WORDS)
        return {"status": status, "outcome": "schema_rejected" if mentions else "rejected_other", "error": text}
    return {"status": status, "outcome": "error", "error": text}


def _message(body: Any) -> dict[str, Any]:
    """@brief 第一条候选的 message 与 finish_reason（缺失时为空）。"""
    choices = body.get("choices") if isinstance(body, dict) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return {}
    message = choices[0].get("message")
    return {**(message if isinstance(message, dict) else {}), "finish_reason": choices[0].get("finish_reason")}


def _named_call(body: Any, name: str) -> dict[str, Any] | None:
    """@brief 回复里第一个调用函数 name 的工具调用；没有时为 None。"""
    calls = _message(body).get("tool_calls") or []
    return next((item for item in calls if isinstance(item, dict)
                 and (item.get("function") or {}).get("name") == name), None)


def classify_tool_call(body: Any, parameters: dict[str, Any], validate: Callable[[dict[str, Any]], Any],
                       name: str = CONCLUDE_TOOL) -> tuple[str, str | None]:
    """@brief 一次回复里 name 调用的类别与样本：valid、invalid_json、schema_violation、host_rejection、no_tool_call。

    形状错误按所发的参数 Schema（JSON Schema）判定；Schema 合法、宿主严格校验（含跨字段约束）仍拒绝的另计。
    """
    call = _named_call(body, name)
    if call is None:
        return "no_tool_call", None
    arguments = call["function"].get("arguments")
    if not isinstance(arguments, str):
        return "invalid_json", f"arguments are not JSON text: {str(arguments)[:SAMPLE_CHARS]}"
    try:
        values = json.loads(arguments)
    except json.JSONDecodeError as error:
        return "invalid_json", f"{error}: {arguments[:SAMPLE_CHARS]}"
    violation = next(iter(jsonschema.Draft202012Validator(parameters).iter_errors(values)), None)
    if violation is not None:
        return "schema_violation", f"{violation.json_path}: {violation.message}"[:SAMPLE_CHARS]
    try:
        validate(values)
    except ValidationError as error:
        return "host_rejection", str(error)[:SAMPLE_CHARS]
    return "valid", None


def argument_value(body: Any, name: str, key: str) -> str | None:
    """@brief name 调用的参数里 key 的取值：文本原样，其他值写成 JSON；参数不可解析或没有该键时为 None。"""
    arguments = ((_named_call(body, name) or {}).get("function") or {}).get("arguments")
    try:
        values = json.loads(arguments) if isinstance(arguments, str) else None
    except json.JSONDecodeError:
        return None
    if not isinstance(values, dict) or key not in values:
        return None
    return values[key] if isinstance(values[key], str) else json.dumps(values[key], ensure_ascii=False)


def _both_endpoints(probe: Probe, payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """@brief 同一请求在主站与 /beta 各发一次，按 classify_strict_response 分类。"""
    return {name: classify_strict_response(*probe.post(name, payload)) for name in ENDPOINTS}


def run_strict_enforcement(probe: Probe) -> dict[str, Any]:
    """@brief (a1) 判定行给出主站与 /beta 是否执行 strict 的结论；(a2) minLength 参考行只记录两个端点的结果。"""
    results = _both_endpoints(probe, strict_rule_request(probe.model))
    keyword = _both_endpoints(probe, strict_keyword_request(probe.model))
    return {**results, "violated_rule": VIOLATED_RULE,
            "main_ignores_strict": results["main"]["outcome"] == "accepted",
            "beta_enforces_strict": results["beta"]["outcome"] == "schema_rejected",
            "unsupported_keyword": {"keyword": UNSUPPORTED_KEYWORD, "informational": True, **keyword}}


def _enum_series(probe: Probe, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
    """@brief (a3) 一个端点的 ENUM_RUNS 次请求：枚举内、越界（不合 Schema 或非法 JSON）、没有调用、HTTP 错误各计数，并计各取值。"""
    parameters = payload["tools"][0]["function"]["parameters"]
    counts: Counter[str] = Counter()
    values: Counter[str] = Counter()
    samples: list[str] = []
    for _ in range(ENUM_RUNS):
        status, body = probe.post(endpoint, payload)
        if status != 200:
            kind, sample = "http_error", f"{status}: {error_text(body)}"
        else:
            kind, sample = classify_tool_call(body, parameters, lambda _values: None, name=ENUM_FUNCTION)
            value = argument_value(body, ENUM_FUNCTION, "color")
            values.update([] if value is None else [value])
        counts[kind] += 1
        if sample is not None and len(samples) < MAX_SAMPLES:
            samples.append(sample)
    return {"requests": ENUM_RUNS, "in_enum": counts["valid"],
            "violations": counts["schema_violation"] + counts["invalid_json"], "invalid_json": counts["invalid_json"],
            "no_tool_call": counts["no_tool_call"], "http_error": counts["http_error"],
            "values": dict(sorted(values.items())), "samples": samples}


def run_enum_constraint(probe: Probe) -> dict[str, Any]:
    """@brief (a3) 主站与 /beta 各连发 ENUM_RUNS 次枚举请求（关闭思考、required），逐端点报告越出枚举的次数。"""
    payload = enum_request(probe.model)
    rows = {name: _enum_series(probe, name, payload) for name in ENDPOINTS}
    return {"function": ENUM_FUNCTION, "allowed": list(ENUM_VALUES), "demanded": ENUM_DEMANDED,
            "thinking": "disabled", "tool_choice": "required", "requests_per_endpoint": ENUM_RUNS, **rows}


def run_conclusion_series(probe: Probe, runs: int) -> dict[str, Any]:
    """@brief (b) 在 /beta 思考模式下连发 runs 次收尾场景，按类别与 finish_reason 计数并留少量样本。"""
    tool, validate, max_tokens = host_conclusion_tool()
    payload = conclusion_request(probe.model, tool, thinking=True, tool_choice="auto", max_tokens=max_tokens)
    counts: Counter[str] = Counter()
    finishes: Counter[str] = Counter()
    samples: dict[str, list[str]] = {}
    for _ in range(runs):
        status, body = probe.post("beta", payload)
        if status != 200:
            kind, sample = "http_error", f"{status}: {error_text(body)}"
        else:
            finishes[str(_message(body).get("finish_reason"))] += 1
            kind, sample = classify_tool_call(body, tool["function"]["parameters"], validate)
        counts[kind] += 1
        if sample is not None and len(samples.setdefault(kind, [])) < MAX_SAMPLES:
            samples[kind].append(sample)
    return {"endpoint": "beta", "thinking": "enabled", "tool_choice": "auto", "max_tokens": max_tokens,
            "requests": runs, **{kind: counts[kind] for kind in _SERIES_KINDS},
            "tool_calls": runs - counts["no_tool_call"] - counts["http_error"],
            "finish_reasons": dict(sorted(finishes.items())), "samples": samples}


def run_required_choice(probe: Probe) -> dict[str, Any]:
    """@brief (c) 主站与 /beta 各测 tool_choice=required：思考开启应返回 400，关闭思考应 200 并调用工具。"""
    tool, _, max_tokens = host_conclusion_tool()
    result: dict[str, Any] = {}
    for name in ENDPOINTS:
        rows = {}
        for label, thinking in (("thinking_enabled", True), ("thinking_disabled", False)):
            payload = conclusion_request(probe.model, tool, thinking=thinking, tool_choice="required",
                                         max_tokens=max_tokens)
            status, body = probe.post(name, payload)
            called = status == 200 and _named_call(body, CONCLUDE_TOOL) is not None
            rows[label] = {"status": status, "tool_called": called,
                           "error": None if status == 200 else error_text(body)}
        rows["required_rejected_in_thinking"] = rows["thinking_enabled"]["status"] == 400
        rows["required_works_without_thinking"] = rows["thinking_disabled"]["tool_called"]
        result[name] = rows
    return result


def _conclusions(strict: dict[str, Any], enum: dict[str, Any], series: dict[str, Any],
                 required: dict[str, Any]) -> dict[str, Any]:
    """@brief 汇总结论：strict 是否执行只看 (a1)；枚举越界按端点计；/beta 思考模式的收尾错误；required 的可用性。"""
    return {
        "main_ignores_strict": strict["main_ignores_strict"],
        "beta_enforces_strict": strict["beta_enforces_strict"],
        "enum_violations": {name: enum[name]["violations"] for name in ENDPOINTS},
        "beta_constrains_enum": enum["beta"]["in_enum"] == enum["beta"]["requests"],
        "beta_thinking_invalid_json": series["invalid_json"],
        "beta_thinking_shape_errors": series["schema_violation"],
        "required_rejected_in_thinking": {name: rows["required_rejected_in_thinking"]
                                          for name, rows in required.items()},
        "required_works_without_thinking": {name: rows["required_works_without_thinking"]
                                            for name, rows in required.items()},
    }


def build_report(probe: Probe, runs: int) -> dict[str, Any]:
    """@brief 三项核对的完整报告（不含密钥）与汇总结论。"""
    strict = run_strict_enforcement(probe)
    enum = run_enum_constraint(probe)
    series = run_conclusion_series(probe, runs)
    required = run_required_choice(probe)
    return {
        "probe": "deepseek-strict", "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": probe.model, "endpoints": dict(ENDPOINTS), "configured_base_url": DEEPSEEK_BASE_URL,
        "strict_enforcement": strict, "enum_constraint": enum, "beta_thinking_conclusions": series,
        "required_tool_choice": required, "conclusions": _conclusions(strict, enum, series, required),
    }


def main(argv: list[str] | None = None, transport: Transport = requests_transport) -> int:
    """@brief 命令行入口：解析参数、读取密钥、发起三项核对，打印 JSON 报告并按需写入文件。"""
    parser = argparse.ArgumentParser(description="Read-only DeepSeek strict endpoint probe (report only)")
    parser.add_argument("--runs", type=int, default=DEFAULT_RUNS, help="conclusion requests sent to /beta")
    parser.add_argument("--timeout", type=float, default=TIMEOUT_SECONDS, help="seconds per request")
    parser.add_argument("--output", type=Path, default=None, help="also write the JSON report to this path")
    arguments = parser.parse_args(argv)
    if arguments.runs < 1:
        parser.error("--runs must be at least 1")
    try:
        key = resolve_api_key()
    except ExtractionError as error:
        print(f"DeepSeek key unavailable: {error}", file=sys.stderr)
        return 2
    report = build_report(Probe(transport=transport, api_key=key, timeout=arguments.timeout), arguments.runs)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
