"""scripts/probe_deepseek_strict.py 的请求构造与结果分类（docs/dev/SPEC-dayrun-fidelity.md 7.5，裁决 S6）。

探针只读、只报告：用假传输替代 HTTP，不连网、不读真实密钥。核对三项请求的端点、请求头与载荷（strict 函数带本地核验
会拒绝的关键字、收尾工具与宿主逐字节相同、思考与 tool_choice 组合），以及 200/400、非法 JSON、形状错误、宿主拒绝、
没有工具调用与传输失败的分类和汇总结论；报告不含密钥。
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")

from custom.agents.life_agent import ACTION_MAX_TOKENS, _tool_schema  # noqa: E402
from custom.agents.life_prompt import system_message  # noqa: E402
from tests.life.conftest import conclude_request  # noqa: E402
from worldmock.life.models import REQUEST_MODELS, deepseek_strict_schema  # noqa: E402
from worldmock.model_endpoint import DEEPSEEK_MODEL  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
_KEY = "sk-probe-test-0001"


def _load_probe():
    """按文件路径加载 scripts/probe_deepseek_strict.py（scripts/ 不是包，沿用项目先例）。"""
    path = REPO_ROOT / "scripts" / "probe_deepseek_strict.py"
    spec = importlib.util.spec_from_file_location("probe_deepseek_strict", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["probe_deepseek_strict"] = module
    spec.loader.exec_module(module)
    return module


probe = _load_probe()


def _tool_reply(arguments: str | None, finish: str = "tool_calls") -> dict:
    """一条 OpenAI 形状的回复：带一次 conclude_activity 调用，arguments 为 None 时只有正文。"""
    calls = [] if arguments is None else [{"id": "call-1", "type": "function",
                                          "function": {"name": "conclude_activity", "arguments": arguments}}]
    return {"choices": [{"index": 0, "finish_reason": finish,
                         "message": {"role": "assistant", "content": "" if calls else "先想想。",
                                     "tool_calls": calls or None}}]}


def _valid_arguments() -> str:
    """宿主当前形状下合法的收尾参数（取共享测试助手构造的真实请求）。"""
    return json.dumps(conclude_request("在公司楼下吃午饭（12:10 起）", summary="午饭吃完了。").model_dump(mode="json"),
                      ensure_ascii=False)


class FakeTransport:
    """按请求内容回放：探针函数请求主站 200、/beta 400；收尾请求依次取 replies；required 按思考开关给 400 或 200。"""

    def __init__(self, replies: list[tuple[int, object]]):
        """收尾请求的回复按序取用；每次请求都记录下来。"""
        self.replies = list(replies)
        self.calls: list[dict] = []

    def __call__(self, url: str, headers: dict, payload: dict, timeout: float) -> tuple[int, object]:
        """记录请求并按请求内容回放一条回复。"""
        self.calls.append({"url": url, "headers": dict(headers), "payload": payload, "timeout": timeout})
        names = [tool["function"]["name"] for tool in payload["tools"]]
        if names == [probe.PROBE_FUNCTION]:
            if url.startswith("https://api.deepseek.com/beta/"):
                return 400, {"error": {"message": "Invalid schema for function 'probe_strict_keyword': "
                                                  "'minLength' is not supported in strict mode", "type": "invalid"}}
            return 200, {"choices": [{"finish_reason": "tool_calls", "message": {"tool_calls": [
                {"id": "c", "type": "function", "function": {"name": probe.PROBE_FUNCTION, "arguments": "{}"}}]}}]}
        if payload["tool_choice"] == "required":
            if payload["thinking"]["type"] == "enabled":
                return 400, {"error": {"message": "Thinking mode does not support this tool_choice"}}
            return 200, _tool_reply(_valid_arguments())
        return self.replies.pop(0)


def _series_replies() -> list[tuple[int, object]]:
    """六次收尾回复：合法两次、裸字符串的非法 JSON、不合 Schema 的形状（参数不是对象）、截断而没有工具调用、HTTP 500。"""
    return [(200, _tool_reply(_valid_arguments())),
            (200, _tool_reply('{"summary": 吃完了}')),
            (200, _tool_reply(json.dumps(["吃完了"], ensure_ascii=False))),
            (200, _tool_reply(None, finish="length")),
            (500, "upstream unavailable"),
            (200, _tool_reply(_valid_arguments()))]


def test_strict_probe_function_bypasses_the_local_schema_check() -> None:
    """探针函数声明 strict: true，参数 Schema 带 minLength：本地 deepseek_strict_schema 的核验会拒绝它，探针不经核验照发。"""
    tool = probe.strict_keyword_tool()["function"]
    assert tool["strict"] is True and tool["name"] == "probe_strict_keyword"
    assert tool["parameters"]["properties"]["answer"] == {"type": "string", "minLength": 1}
    from worldmock.life.models import _validate_deepseek_schema_node

    with pytest.raises(ValueError, match="unsupported keys"):
        _validate_deepseek_schema_node(tool["parameters"], "$")


def test_report_sends_every_probe_request_and_classifies_the_results(monkeypatch) -> None:
    """一次完整探针：(a) 主站 200、/beta 400（提到 minLength）→ 主站不执行 strict、/beta 执行；(b) /beta 思考模式
    六次收尾按类别计数；(c) 两个端点思考开启 + required 为 400、关闭思考后 200 并调用工具。请求头带密钥，报告不含。"""
    transport = FakeTransport(_series_replies())
    report = probe.build_report(probe.Probe(transport=transport, api_key=_KEY), runs=6)
    strict = report["strict_enforcement"]
    assert strict["main"] == {"status": 200, "outcome": "accepted", "error": None}
    assert strict["beta"]["status"] == 400 and strict["beta"]["outcome"] == "schema_rejected"
    series = report["beta_thinking_conclusions"]
    assert {key: series[key] for key in ("requests", "valid", "invalid_json", "schema_violation", "host_rejection",
                                         "no_tool_call", "http_error", "tool_calls")} == {
        "requests": 6, "valid": 2, "invalid_json": 1, "schema_violation": 1, "host_rejection": 0,
        "no_tool_call": 1, "http_error": 1, "tool_calls": 4}
    assert series["finish_reasons"] == {"length": 1, "tool_calls": 4}
    assert series["samples"]["schema_violation"][0].startswith("$: ")
    assert series["samples"]["invalid_json"][0].startswith("Expecting value")
    assert series["samples"]["http_error"] == ["500: upstream unavailable"]
    assert report["conclusions"] == {
        "main_ignores_strict": True, "beta_enforces_strict": True, "beta_thinking_invalid_json": 1,
        "beta_thinking_shape_errors": 1,
        "required_rejected_in_thinking": {"main": True, "beta": True},
        "required_works_without_thinking": {"main": True, "beta": True}}
    assert report["model"] == DEEPSEEK_MODEL and report["endpoints"] == {
        "main": "https://api.deepseek.com", "beta": "https://api.deepseek.com/beta"}
    assert len(transport.calls) == 2 + 6 + 4
    assert all(call["headers"]["Authorization"] == f"Bearer {_KEY}" for call in transport.calls)
    assert _KEY not in json.dumps(report, ensure_ascii=False)


def test_requests_use_the_host_conclude_tool_and_stage_parameters() -> None:
    """(a) 关闭思考、auto；(b) 发到 /beta、思考开启、auto、max_tokens 取行动阶段常量；(c) 两端点各一对 required 请求；
    收尾工具与宿主 _tool_schema 逐字节相同，消息为生活系统层加收尾场景。"""
    transport = FakeTransport(_series_replies()[:1])
    probe.build_report(probe.Probe(transport=transport, api_key=_KEY, timeout=7.5), runs=1)
    host_tool = _tool_schema("conclude_activity", REQUEST_MODELS["conclude_activity"])
    first, second, series, *required = transport.calls
    assert [first["url"], second["url"]] == ["https://api.deepseek.com/chat/completions",
                                             "https://api.deepseek.com/beta/chat/completions"]
    assert first["payload"]["thinking"] == {"type": "disabled"} and first["payload"]["tool_choice"] == "auto"
    assert series["url"] == "https://api.deepseek.com/beta/chat/completions"
    assert series["payload"]["tools"] == [host_tool]
    assert series["payload"]["messages"][0] == system_message(False)
    assert (series["payload"]["thinking"], series["payload"]["tool_choice"], series["payload"]["max_tokens"]) == (
        {"type": "enabled"}, "auto", ACTION_MAX_TOKENS)
    assert all(call["payload"]["model"] == DEEPSEEK_MODEL and call["timeout"] == 7.5 for call in transport.calls)
    assert [(call["url"].split("/chat")[0], call["payload"]["thinking"]["type"], call["payload"]["tool_choice"])
            for call in required] == [
        ("https://api.deepseek.com", "enabled", "required"), ("https://api.deepseek.com", "disabled", "required"),
        ("https://api.deepseek.com/beta", "enabled", "required"),
        ("https://api.deepseek.com/beta", "disabled", "required")]
    assert all(call["payload"]["tools"] == [host_tool] for call in required)
    assert host_tool["function"]["strict"] is True
    assert host_tool["function"]["parameters"] == deepseek_strict_schema(REQUEST_MODELS["conclude_activity"])


@pytest.mark.parametrize(("status", "body", "outcome"), [
    (200, {"choices": []}, "accepted"),
    (400, {"error": {"message": "Invalid function schema: unsupported keyword"}}, "schema_rejected"),
    (400, {"error": {"message": "Model Not Exist"}}, "rejected_other"),
    (503, "Service Unavailable", "error"),
    (0, "ConnectionError: refused", "error"),
])
def test_strict_response_outcomes(status: int, body: object, outcome: str) -> None:
    """(a) 的分类：200 为照常受理；4xx 且错误提到 Schema 才算 Schema 校验错误；其他 4xx、5xx 与传输失败分开记。"""
    result = probe.classify_strict_response(status, body)
    assert result["status"] == status and result["outcome"] == outcome
    assert (result["error"] is None) == (status == 200)


def test_host_rejection_is_separate_from_shape_errors() -> None:
    """合乎所发 JSON Schema、但宿主严格校验（跨字段约束）拒绝的参数记为 host_rejection，不算形状错误；合法参数经
    宿主真实的严格校验记为 valid。"""
    from pydantic import BaseModel, model_validator

    class Picky(BaseModel):
        """只接受 summary 以句号结尾的参数（代表 JSON Schema 表达不了的宿主约束）。"""

        summary: str

        @model_validator(mode="after")
        def ends_with_period(self) -> "Picky":
            """跨字段约束的替身。"""
            if not self.summary.endswith("。"):
                raise ValueError("summary must end with a period")
            return self

    parameters = {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"],
                  "additionalProperties": False}
    kind, sample = probe.classify_tool_call(_tool_reply('{"summary": "吃完了"}'), parameters,
                                            lambda values: Picky.model_validate(values))
    assert kind == "host_rejection" and "summary must end with a period" in sample
    tool, validate, max_tokens = probe.host_conclusion_tool()
    assert tool == _tool_schema("conclude_activity", REQUEST_MODELS["conclude_activity"])
    assert max_tokens == ACTION_MAX_TOKENS
    assert probe.classify_tool_call(_tool_reply(_valid_arguments()), tool["function"]["parameters"], validate) == (
        "valid", None)


def test_main_reads_the_worldmock_key_pool_prints_and_writes_the_report(monkeypatch, tmp_path, capsys) -> None:
    """命令行：密钥取 DEEPSEEK_NODE_KEYS 的第一把，打印 JSON 报告并写入 --output；报告与输出都不含密钥。"""
    monkeypatch.setenv("DEEPSEEK_NODE_KEYS", f"{_KEY}, sk-probe-test-0002")
    transport = FakeTransport(_series_replies()[:2])
    output = tmp_path / "reports" / "probe.json"
    assert probe.main(["--runs", "2", "--output", str(output)], transport=transport) == 0
    printed = capsys.readouterr().out
    assert json.loads(printed) == json.loads(output.read_text(encoding="utf-8"))
    assert _KEY not in printed and "sk-probe-test-0002" not in printed
    assert {call["headers"]["Authorization"] for call in transport.calls} == {f"Bearer {_KEY}"}
    assert json.loads(printed)["beta_thinking_conclusions"]["requests"] == 2


def test_main_reports_a_missing_key_without_sending_requests(monkeypatch, tmp_path, capsys) -> None:
    """没有密钥时不发任何请求，以退出码 2 报告缺少密钥。"""
    monkeypatch.delenv("DEEPSEEK_NODE_KEYS", raising=False)
    monkeypatch.setattr("worldmock.profile.extractor.CREDENTIALS_PATH", tmp_path / "missing.env")
    transport = FakeTransport([])
    assert probe.main(["--runs", "1"], transport=transport) == 2
    assert transport.calls == []
    assert "DEEPSEEK_NODE_KEYS" in capsys.readouterr().err
