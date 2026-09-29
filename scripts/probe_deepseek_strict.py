"""@brief DeepSeek strict 端点探针：只读、只报告（docs/dev/SPEC-dayrun-fidelity.md 7.5，裁决 D14、S6）。

三项核对，结论打印为 JSON 报告，可另写入文件；只发模型请求，不写世界、不改任何配置，也不是每次运行的自检：

(a) strict 是否执行：同一个声明 strict: true 的函数，参数 Schema 故意带一个 strict 模式不支持的关键字 minLength
    （绕过本地 models.deepseek_strict_schema 的核验），分别发到主站与 /beta。/beta 按文档应返回 Schema 校验错误；
    主站照常返回 200 就说明主站不执行 strict。
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
#: 单次请求超时（秒）。
TIMEOUT_SECONDS = 120.0
#: strict 模式不支持、故意放进探针 Schema 的关键字（本地 deepseek_strict_schema 会拒绝它）。
UNSUPPORTED_KEYWORD = "minLength"
#: 探针函数名与收尾工具名。
PROBE_FUNCTION = "probe_strict_keyword"
CONCLUDE_TOOL = "conclude_activity"
#: 判定「Schema 校验错误」时在错误文本里找的词（小写比较）。
_SCHEMA_ERROR_WORDS = ("schema", "strict", UNSUPPORTED_KEYWORD.lower(), "parameters", "json_schema")
#: 每类问题在报告里保留的样本条数上限，样本文本的截断长度。
MAX_SAMPLES = 5
SAMPLE_CHARS = 400
#: AS2 在导入时要求此变量存在；探针不经 AS2 发任何请求，占位值只为导入宿主的工具构建函数。
_AS2_IMPORT_KEY = "AGENTSOCIETY_LLM_API_KEY"
#: 收尾场景的 user 消息：一项刚结束、待收尾的活动，只能用 conclude_activity 收尾。
_CONCLUSION_SCENARIO = (
    "工具层：你刚结束的活动「在公司楼下吃午饭（12:10 起）」处于「刚结束」阶段，是此刻唯一可操作的目标，"
    "它的工具 conclude_activity 可用。环境层：12:40，公司楼下的餐馆，吃饱了。进度层：午饭已经吃完。"
    "请用 conclude_activity 为这项活动收尾：写一句总结，值得记住的事可以写成记忆。")

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


def strict_keyword_tool() -> dict[str, Any]:
    """@brief 探针函数：strict: true，参数 Schema 带 strict 不支持的 minLength（不经本地核验，直接写出）。"""
    parameters = {"type": "object", "properties": {"answer": {"type": "string", UNSUPPORTED_KEYWORD: 1}},
                  "required": ["answer"], "additionalProperties": False}
    return {"type": "function", "function": {"name": PROBE_FUNCTION, "strict": True, "parameters": parameters,
                                             "description": "Return the answer the user asks for."}}


def strict_keyword_request(model: str) -> dict[str, Any]:
    """@brief (a) 的请求：关闭思考、tool_choice=auto，只带探针函数。"""
    return {"model": model, "stream": False, "max_tokens": 200, "thinking": {"type": "disabled"},
            "tool_choice": "auto", "tools": [strict_keyword_tool()],
            "messages": [{"role": "user", "content": f'Call {PROBE_FUNCTION} with answer "ok".'}]}


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
    """@brief (a) 的结论：accepted（200）、schema_rejected（4xx 且错误提到 Schema）、rejected_other、error。"""
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


def classify_tool_call(body: Any, parameters: dict[str, Any], validate: Callable[[dict[str, Any]], Any],
                       ) -> tuple[str, str | None]:
    """@brief 一次收尾回复的类别与样本：valid、invalid_json、schema_violation、host_rejection、no_tool_call。

    形状错误按所发的参数 Schema（JSON Schema）判定；Schema 合法、宿主严格校验（含跨字段约束）仍拒绝的另计。
    """
    calls = _message(body).get("tool_calls") or []
    call = next((item for item in calls if isinstance(item, dict)
                 and (item.get("function") or {}).get("name") == CONCLUDE_TOOL), None)
    if call is None:
        return "no_tool_call", None
    arguments = call["function"].get("arguments")
    try:
        values = json.loads(arguments) if isinstance(arguments, str) else None
    except json.JSONDecodeError as error:
        return "invalid_json", f"{error}: {arguments[:SAMPLE_CHARS]}"
    if values is None:
        return "invalid_json", f"arguments are not JSON text: {str(arguments)[:SAMPLE_CHARS]}"
    violation = next(iter(jsonschema.Draft202012Validator(parameters).iter_errors(values)), None)
    if violation is not None:
        return "schema_violation", f"{violation.json_path}: {violation.message}"[:SAMPLE_CHARS]
    try:
        validate(values)
    except ValidationError as error:
        return "host_rejection", str(error)[:SAMPLE_CHARS]
    return "valid", None


def run_strict_enforcement(probe: Probe) -> dict[str, Any]:
    """@brief (a) 主站与 /beta 各发一次带不支持关键字的 strict 函数，并给出两处是否执行 strict 的结论。"""
    results = {name: classify_strict_response(*probe.post(name, strict_keyword_request(probe.model)))
               for name in ENDPOINTS}
    return {**results, "unsupported_keyword": UNSUPPORTED_KEYWORD,
            "main_ignores_strict": results["main"]["outcome"] == "accepted",
            "beta_enforces_strict": results["beta"]["outcome"] == "schema_rejected"}


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
    kinds = ("valid", "invalid_json", "schema_violation", "host_rejection", "no_tool_call", "http_error")
    return {"endpoint": "beta", "thinking": "enabled", "tool_choice": "auto", "max_tokens": max_tokens,
            "requests": runs, **{kind: counts[kind] for kind in kinds},
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
            called = status == 200 and any((item.get("function") or {}).get("name") == CONCLUDE_TOOL
                                           for item in _message(body).get("tool_calls") or [])
            rows[label] = {"status": status, "tool_called": called,
                           "error": None if status == 200 else error_text(body)}
        rows["required_rejected_in_thinking"] = rows["thinking_enabled"]["status"] == 400
        rows["required_works_without_thinking"] = rows["thinking_disabled"]["tool_called"]
        result[name] = rows
    return result


def build_report(probe: Probe, runs: int) -> dict[str, Any]:
    """@brief 三项核对的完整报告（不含密钥）与汇总结论。"""
    strict = run_strict_enforcement(probe)
    series = run_conclusion_series(probe, runs)
    required = run_required_choice(probe)
    return {
        "probe": "deepseek-strict", "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": probe.model, "endpoints": dict(ENDPOINTS), "configured_base_url": DEEPSEEK_BASE_URL,
        "strict_enforcement": strict, "beta_thinking_conclusions": series, "required_tool_choice": required,
        "conclusions": {
            "main_ignores_strict": strict["main_ignores_strict"],
            "beta_enforces_strict": strict["beta_enforces_strict"],
            "beta_thinking_invalid_json": series["invalid_json"],
            "beta_thinking_shape_errors": series["schema_violation"],
            "required_rejected_in_thinking": {name: rows["required_rejected_in_thinking"]
                                              for name, rows in required.items()},
            "required_works_without_thinking": {name: rows["required_works_without_thinking"]
                                                for name, rows in required.items()},
        },
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
