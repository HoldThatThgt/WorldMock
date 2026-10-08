"""scripts/probe_deepseek_strict.py 的请求构造与结果分类（docs/design/10-runtime.md ，裁决 S6）。

探针只读、只报告：用假传输替代 HTTP，不连网、不读真实密钥。核对各项请求的端点、请求头与载荷（判定行的 strict 函数
违反「全部属性列进 required」、参考行带 minLength，二者都绕过本地核验；枚举函数合乎 strict 规则、user 消息坚持要枚举外
的取值；收尾工具与宿主逐字节相同；思考与 tool_choice 组合），以及 200/400、枚举越界、非法 JSON、形状错误、宿主拒绝、
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
from worldmock.agents.life_prompt import system_message  # noqa: E402
from tests.life.conftest import conclude_request  # noqa: E402
from worldmock.life.models import REQUEST_MODELS, _validate_deepseek_schema_node, deepseek_strict_schema  # noqa: E402
from worldmock.model_endpoint import DEEPSEEK_MODEL  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
_KEY = "sk-probe-test-0001"
_MAIN = "https://api.deepseek.com"
_BETA = "https://api.deepseek.com/beta"
#: /beta 对违反「全部属性列进 required」的 strict 函数给出的错误说明。
_RULE_ERROR = "Required properties must match all properties in the object"


def _load_probe():
    """按文件路径加载 scripts/probe_deepseek_strict.py（scripts/ 不是包，沿用项目先例）。"""
    path = REPO_ROOT / "scripts" / "probe_deepseek_strict.py"
    spec = importlib.util.spec_from_file_location("probe_deepseek_strict", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["probe_deepseek_strict"] = module
    spec.loader.exec_module(module)
    return module


probe = _load_probe()


def _call_reply(name: str, arguments: object | None, finish: str = "tool_calls") -> dict:
    """一条 OpenAI 形状的回复：带一次 name 调用，arguments 为 None 时只有正文。"""
    calls = [] if arguments is None else [{"id": "call-1", "type": "function",
                                          "function": {"name": name, "arguments": arguments}}]
    return {"choices": [{"index": 0, "finish_reason": finish,
                         "message": {"role": "assistant", "content": "" if calls else "先想想。",
                                     "tool_calls": calls or None}}]}


def _tool_reply(arguments: str | None, finish: str = "tool_calls") -> dict:
    """一条带 conclude_activity 调用的回复（arguments 为 None 时只有正文）。"""
    return _call_reply("conclude_activity", arguments, finish)


def _color(value: str) -> tuple[int, dict]:
    """(a3) 的一次 200 回复：probe_pick_color 的参数为 {"color": value}。"""
    return 200, _call_reply(probe.ENUM_FUNCTION, json.dumps({"color": value}))


def _valid_arguments() -> str:
    """宿主当前形状下合法的收尾参数（取共享测试助手构造的真实请求）。"""
    return json.dumps(conclude_request("在公司楼下吃午饭（12:10 起）", summary="午饭吃完了。").model_dump(mode="json"),
                      ensure_ascii=False)


#: (a3) 的缺省回放：主站三次里一次照用户要求返回 green，/beta 三次都在枚举内。
_COLORS = {"main": [_color("green"), _color("red"), _color("blue")], "beta": [_color("red")] * 3}


class FakeTransport:
    """按请求内容回放：(a1) 主站 200、/beta 400；(a2) 两个端点都 200；(a3) 按端点依次取 colors；收尾请求依次取 replies；
    required 按思考开关给 400 或 200。"""

    def __init__(self, replies: list[tuple[int, object]], colors: dict[str, list] | None = None):
        """收尾请求与各端点的枚举请求的回复按序取用；每次请求都记录下来。"""
        self.replies = list(replies)
        self.colors = {name: list(items) for name, items in (colors or _COLORS).items()}
        self.calls: list[dict] = []

    def __call__(self, url: str, headers: dict, payload: dict, timeout: float) -> tuple[int, object]:
        """记录请求并按请求内容回放一条回复。"""
        self.calls.append({"url": url, "headers": dict(headers), "payload": payload, "timeout": timeout})
        endpoint = "beta" if url.startswith(f"{_BETA}/") else "main"
        name = payload["tools"][0]["function"]["name"]
        if name == probe.RULE_FUNCTION and endpoint == "beta":
            return 400, {"error": {"message": _RULE_ERROR, "type": "invalid_request_error"}}
        if name in (probe.RULE_FUNCTION, probe.PROBE_FUNCTION):
            return 200, _call_reply(name, '{"answer": "ok"}')
        if name == probe.ENUM_FUNCTION:
            return self.colors[endpoint].pop(0)
        if payload["tool_choice"] == "required":
            if payload["thinking"]["type"] == "enabled":
                return 400, {"error": {"message": "Thinking mode does not support this tool_choice"}}
            return 200, _tool_reply(_valid_arguments())
        return self.replies.pop(0)


class _UnrelatedFailures(FakeTransport):
    """(a1) 两个端点都不是以 Schema 校验错误作答：主站 503，/beta 以与 Schema 无关的原因 400（模型不存在）；(c) 思考开启的
    required 请求两个端点都 503；其余请求同 FakeTransport。"""

    def __call__(self, url: str, headers: dict, payload: dict, timeout: float) -> tuple[int, object]:
        """先拦下这两类请求（同样记录），其余交给 FakeTransport 回放。"""
        name = payload["tools"][0]["function"]["name"]
        thinking_required = name == "conclude_activity" and payload["tool_choice"] == "required" and (
            payload["thinking"]["type"] == "enabled")
        if name != probe.RULE_FUNCTION and not thinking_required:
            return super().__call__(url, headers, payload, timeout)
        self.calls.append({"url": url, "headers": dict(headers), "payload": payload, "timeout": timeout})
        if name == probe.RULE_FUNCTION and url.startswith(f"{_BETA}/"):
            return 400, {"error": {"message": "Model Not Exist", "type": "invalid_request_error"}}
        return 503, {"error": {"message": "Service Unavailable"}}


def _series_replies() -> list[tuple[int, object]]:
    """六次收尾回复：合法两次、裸字符串的非法 JSON、不合 Schema 的形状（参数不是对象）、截断而没有工具调用、HTTP 500。"""
    return [(200, _tool_reply(_valid_arguments())),
            (200, _tool_reply('{"summary": 吃完了}')),
            (200, _tool_reply(json.dumps(["吃完了"], ensure_ascii=False))),
            (200, _tool_reply(None, finish="length")),
            (500, "upstream unavailable"),
            (200, _tool_reply(_valid_arguments()))]


def test_strict_probe_functions_bypass_the_local_schema_check() -> None:
    """三个探针函数都声明 strict: true。判定行的对象有两个属性、required 只列一个，参考行带 minLength，本地
    deepseek_strict_schema 的核验会分别拒绝它们，探针不经核验照发；枚举函数合乎 strict 的全部规则，本地核验接受。"""
    rule, keyword, enum = (tool()["function"] for tool in (probe.required_rule_tool, probe.strict_keyword_tool,
                                                            probe.enum_tool))
    assert [(item["name"], item["strict"]) for item in (rule, keyword, enum)] == [
        ("probe_strict_required", True), ("probe_strict_keyword", True), ("probe_pick_color", True)]
    assert set(rule["parameters"]["properties"]) == {"answer", "note"} and rule["parameters"]["required"] == ["answer"]
    with pytest.raises(ValueError, match="not all required"):
        _validate_deepseek_schema_node(rule["parameters"], "$")
    assert keyword["parameters"]["properties"]["answer"] == {"type": "string", "minLength": 1}
    with pytest.raises(ValueError, match="unsupported keys"):
        _validate_deepseek_schema_node(keyword["parameters"], "$")
    assert enum["parameters"]["properties"]["color"] == {"type": "string", "enum": ["red", "blue"]}
    assert _validate_deepseek_schema_node(enum["parameters"], "$") is None


def test_report_sends_every_probe_request_and_classifies_the_results() -> None:
    """一次完整探针：(a1) 主站 200、/beta 以 required 规则的 Schema 错误 400 → 主站不执行 strict、/beta 执行；(a2) minLength
    两个端点都 200，只作参考行、不进结论；(a3) 主站三次里一次返回枚举外的 green、/beta 三次都在枚举内；(b) /beta 思考模式
    六次收尾按类别计数；(c) 两个端点思考开启 + required 为 400、关闭思考后 200 并调用工具。请求头带密钥，报告不含。"""
    transport = FakeTransport(_series_replies())
    report = probe.build_report(probe.Probe(transport=transport, api_key=_KEY), runs=6)
    strict = report["strict_enforcement"]
    accepted = {"status": 200, "outcome": "accepted", "error": None}
    assert strict["main"] == accepted
    assert strict["beta"] == {"status": 400, "outcome": "schema_rejected", "error": _RULE_ERROR}
    assert strict["violated_rule"] == "every object property must be listed in required"
    assert strict["unsupported_keyword"] == {"keyword": "minLength", "informational": True,
                                             "main": accepted, "beta": accepted}
    enum = report["enum_constraint"]
    assert {key: enum[key] for key in ("function", "allowed", "demanded", "thinking", "tool_choice",
                                       "requests_per_endpoint")} == {
        "function": "probe_pick_color", "allowed": ["red", "blue"], "demanded": "green", "thinking": "disabled",
        "tool_choice": "required", "requests_per_endpoint": 3}
    assert enum["main"] == {"requests": 3, "in_enum": 2, "violations": 1, "invalid_json": 0, "no_tool_call": 0,
                            "http_error": 0, "values": {"blue": 1, "green": 1, "red": 1},
                            "samples": ["$.color: 'green' is not one of ['red', 'blue']"]}
    assert enum["beta"] == {"requests": 3, "in_enum": 3, "violations": 0, "invalid_json": 0, "no_tool_call": 0,
                            "http_error": 0, "values": {"red": 3}, "samples": []}
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
        "main_ignores_strict": True, "beta_enforces_strict": True,
        "enum_violations": {"main": 1, "beta": 0}, "beta_constrains_enum": True,
        "beta_thinking_invalid_json": 1, "beta_thinking_shape_errors": 1,
        "required_rejected_in_thinking": {"main": True, "beta": True},
        "required_works_without_thinking": {"main": True, "beta": True}}
    assert report["model"] == DEEPSEEK_MODEL and report["endpoints"] == {"main": _MAIN, "beta": _BETA}
    assert report["configured_base_url"] == _BETA
    assert len(transport.calls) == 2 + 2 + 3 * 2 + 6 + 4
    assert all(call["headers"]["Authorization"] == f"Bearer {_KEY}" for call in transport.calls)
    assert _KEY not in json.dumps(report, ensure_ascii=False)


def test_conclusions_need_the_specific_outcomes_not_just_any_failure() -> None:
    """汇总结论只认规格要求的具体结果（7.5）：主站 5xx 不等于「主站不执行 strict」，/beta 以与 Schema 无关的原因拒绝不等于
    「/beta 执行 strict」，思考模式下 required 请求 5xx 也不等于「返回 400」；这些失败照样按原样记在对应的行里。"""
    report = probe.build_report(probe.Probe(transport=_UnrelatedFailures(_series_replies()[:1]), api_key=_KEY), runs=1)
    strict, required = report["strict_enforcement"], report["required_tool_choice"]
    assert (strict["main"]["outcome"], strict["beta"]["outcome"]) == ("error", "rejected_other")
    assert strict["beta"]["error"] == "Model Not Exist"
    assert [required[name]["thinking_enabled"]["status"] for name in ("main", "beta")] == [503, 503]
    conclusions = report["conclusions"]
    assert (conclusions["main_ignores_strict"], conclusions["beta_enforces_strict"]) == (False, False)
    assert conclusions["required_rejected_in_thinking"] == {"main": False, "beta": False}
    assert conclusions["required_works_without_thinking"] == {"main": True, "beta": True}


def test_requests_use_the_host_conclude_tool_and_stage_parameters() -> None:
    """(a1)(a2) 关闭思考、auto，主站与 /beta 各一次；(a3) 关闭思考、required，user 消息坚持要 green，主站与 /beta 各三次；
    (b) 发到 /beta、思考开启、auto、max_tokens 取行动阶段常量；(c) 两端点各一对 required 请求；收尾工具与宿主 _tool_schema
    逐字节相同，消息为生活系统层加收尾场景。"""
    transport = FakeTransport(_series_replies()[:1])
    probe.build_report(probe.Probe(transport=transport, api_key=_KEY, timeout=7.5), runs=1)
    host_tool = _tool_schema("conclude_activity", REQUEST_MODELS["conclude_activity"])
    rule, keyword, enum, series, required = (transport.calls[:2], transport.calls[2:4], transport.calls[4:10],
                                             transport.calls[10], transport.calls[11:])
    assert [call["url"] for call in rule + keyword] == [f"{_MAIN}/chat/completions", f"{_BETA}/chat/completions"] * 2
    assert all(call["payload"]["tools"] == [probe.required_rule_tool()] for call in rule)
    assert all(call["payload"]["tools"] == [probe.strict_keyword_tool()] for call in keyword)
    assert all((call["payload"]["thinking"], call["payload"]["tool_choice"]) == ({"type": "disabled"}, "auto")
               for call in rule + keyword)
    assert [call["url"] for call in enum] == [f"{_MAIN}/chat/completions"] * 3 + [f"{_BETA}/chat/completions"] * 3
    assert all(call["payload"]["tools"] == [probe.enum_tool()] and call["payload"]["tool_choice"] == "required"
               and call["payload"]["thinking"] == {"type": "disabled"} for call in enum)
    demand = enum[0]["payload"]["messages"]
    assert [message["role"] for message in demand] == ["user"] and 'with color "green"' in demand[0]["content"]
    assert series["url"] == f"{_BETA}/chat/completions"
    assert series["payload"]["tools"] == [host_tool]
    assert series["payload"]["messages"][0] == system_message(False)
    assert (series["payload"]["thinking"], series["payload"]["tool_choice"], series["payload"]["max_tokens"]) == (
        {"type": "enabled"}, "auto", ACTION_MAX_TOKENS)
    assert all(call["payload"]["model"] == DEEPSEEK_MODEL and call["timeout"] == 7.5 for call in transport.calls)
    assert [(call["url"].split("/chat")[0], call["payload"]["thinking"]["type"], call["payload"]["tool_choice"])
            for call in required] == [(_MAIN, "enabled", "required"), (_MAIN, "disabled", "required"),
                                      (_BETA, "enabled", "required"), (_BETA, "disabled", "required")]
    assert all(call["payload"]["tools"] == [host_tool] for call in required)
    assert host_tool["function"]["strict"] is True
    assert host_tool["function"]["parameters"] == deepseek_strict_schema(REQUEST_MODELS["conclude_activity"])


def test_enum_violations_count_invalid_json_and_keep_missing_calls_and_failures_apart() -> None:
    """(a3) 的计数：枚举外取值与非法 JSON 都算越界，没有工具调用与 HTTP 错误另计、不算越界；/beta 有一次传输失败时
    「/beta 约束了枚举」的结论不成立（三次里只有两次拿到枚举内的取值）。"""
    colors = {"main": [_color("green"), (200, _call_reply(probe.ENUM_FUNCTION, '{"color": green}')),
                       (200, _call_reply(probe.ENUM_FUNCTION, None, finish="stop"))],
              "beta": [(503, {"error": {"message": "Service Unavailable"}}), _color("red"), _color("blue")]}
    report = probe.build_report(probe.Probe(transport=FakeTransport(_series_replies()[:1], colors), api_key=_KEY),
                                runs=1)
    enum = report["enum_constraint"]
    assert {key: enum["main"][key] for key in ("in_enum", "violations", "invalid_json", "no_tool_call", "http_error",
                                               "values")} == {
        "in_enum": 0, "violations": 2, "invalid_json": 1, "no_tool_call": 1, "http_error": 0, "values": {"green": 1}}
    assert enum["main"]["samples"][1].startswith("Expecting value")
    assert {key: enum["beta"][key] for key in ("in_enum", "violations", "no_tool_call", "http_error", "values",
                                               "samples")} == {
        "in_enum": 2, "violations": 0, "no_tool_call": 0, "http_error": 1, "values": {"blue": 1, "red": 1},
        "samples": ["503: Service Unavailable"]}
    assert (report["conclusions"]["enum_violations"], report["conclusions"]["beta_constrains_enum"]) == (
        {"main": 2, "beta": 0}, False)


@pytest.mark.parametrize(("arguments", "value"), [
    ('{"color": "red"}', "red"),
    ('{"color": 3}', "3"),
    ('{"colour": "red"}', None),
    ('["red"]', None),
    ('{"color": red}', None),
    ({"color": "red"}, None),
], ids=["text", "number-as-json", "missing-key", "not-an-object", "invalid-json", "arguments-not-text"])
def test_argument_value_reads_one_key_of_the_named_call(arguments: object, value: str | None) -> None:
    """取值计数只读指定函数调用参数里的一个键：文本原样、其他值写成 JSON；缺键、不是对象、非法 JSON 或参数不是文本都没有取值。"""
    assert probe.argument_value(_call_reply(probe.ENUM_FUNCTION, arguments), probe.ENUM_FUNCTION, "color") == value
    assert probe.argument_value(_call_reply("conclude_activity", arguments), probe.ENUM_FUNCTION, "color") is None


@pytest.mark.parametrize(("status", "body", "outcome"), [
    (200, {"choices": []}, "accepted"),
    (400, {"error": {"message": _RULE_ERROR}}, "schema_rejected"),
    (400, {"error": {"message": "Invalid function schema: unsupported keyword"}}, "schema_rejected"),
    (400, {"error": {"message": "Model Not Exist"}}, "rejected_other"),
    (503, "Service Unavailable", "error"),
    (0, "ConnectionError: refused", "error"),
], ids=["ok", "required-rule-400", "schema-400", "other-400", "server-503", "transport-failure"])
def test_strict_response_outcomes(status: int, body: object, outcome: str) -> None:
    """(a1)(a2) 的分类：200 为照常受理；4xx 且错误提到 Schema（含 required 规则的说明）才算 Schema 校验错误；其他 4xx、
    5xx 与传输失败分开记。"""
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
    assert json.loads(printed)["conclusions"]["enum_violations"] == {"main": 1, "beta": 0}


@pytest.mark.parametrize(("reply", "kind"), [
    (_tool_reply("null"), "schema_violation"),
    ({"choices": [{"finish_reason": "tool_calls", "message": {"tool_calls": [
        {"id": "c", "type": "function", "function": {"name": "conclude_activity", "arguments": {"summary": "x"}}}]}}]},
     "invalid_json"),
    ({"choices": [{"finish_reason": "tool_calls", "message": {"tool_calls": [
        {"id": "c", "type": "function", "function": {"name": "report_status", "arguments": "{}"}}]}}]},
     "no_tool_call"),
    ({"error": {"message": "overloaded"}}, "no_tool_call"),
], ids=["json-null", "arguments-not-text", "other-tool", "no-choices"])
def test_tool_call_classification_edges(reply: dict, kind: str) -> None:
    """合法 JSON 但不是对象记形状错误；参数不是 JSON 文本记非法 JSON；调了别的工具或没有候选都记没有收尾调用。"""
    tool, validate, _ = probe.host_conclusion_tool()
    assert probe.classify_tool_call(reply, tool["function"]["parameters"], validate)[0] == kind


def test_default_transport_posts_json_and_records_failures(monkeypatch) -> None:
    """缺省传输用 requests.post 发 JSON：JSON 回复解析成对象、非 JSON 回复保留文本、网络错误记为状态 0（不连网）。"""
    import requests

    sent = []

    class Reply:
        """requests 回复的替身。"""

        def __init__(self, status: int, body: object):
            """状态码与回复体。"""
            self.status_code, self.body = status, body
            self.text = body if isinstance(body, str) else json.dumps(body)

        def json(self):
            """JSON 回复体；文本回复按 requests 的行为抛 ValueError。"""
            if isinstance(self.body, str):
                raise ValueError("not JSON")
            return self.body

    replies = [Reply(400, {"error": {"message": "bad schema"}}), Reply(502, "Bad Gateway")]

    def post(url, headers, json, timeout):
        """记录请求并依次回放；第三次模拟连接失败。"""
        sent.append((url, headers, json, timeout))
        if not replies:
            raise requests.ConnectionError("refused")
        return replies.pop(0)

    monkeypatch.setattr(requests, "post", post)
    headers = {"Authorization": f"Bearer {_KEY}"}
    assert probe.requests_transport("https://example.test/chat/completions", headers, {"a": 1}, 3.0) == (
        400, {"error": {"message": "bad schema"}})
    assert probe.requests_transport("https://example.test/chat/completions", headers, {"a": 2}, 3.0) == (
        502, "Bad Gateway")
    status, body = probe.requests_transport("https://example.test/chat/completions", headers, {"a": 3}, 3.0)
    assert status == 0 and body.startswith("ConnectionError")
    assert [item[2] for item in sent] == [{"a": 1}, {"a": 2}, {"a": 3}] and all(item[3] == 3.0 for item in sent)


def test_main_reports_a_missing_key_without_sending_requests(monkeypatch, tmp_path, capsys) -> None:
    """没有密钥时不发任何请求，以退出码 2 报告缺少密钥。"""
    monkeypatch.delenv("DEEPSEEK_NODE_KEYS", raising=False)
    monkeypatch.setattr("worldmock.profile.extractor.CREDENTIALS_PATH", tmp_path / "missing.env")
    transport = FakeTransport([])
    assert probe.main(["--runs", "1"], transport=transport) == 2
    assert transport.calls == []
    assert "DEEPSEEK_NODE_KEYS" in capsys.readouterr().err
