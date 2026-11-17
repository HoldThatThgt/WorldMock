"""@brief 模型客户端的线路：真实 ModelClient 对本机服务发出的正文等于基线环境里线路探针冻结的正文（固定样例
tests/fixtures/model-client-wire.json，缺失时用例失败）；每次发送恰好一个 HTTP 请求；九个故障场景的请求数、等待序列、
失败发送与结局等于基线客户端与新客户端在故障注入比较里一致的期望值。"""

import json
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.unit.runtime.model_client_fixtures import _fake_time, _local_server
from worldmock.configuration.model import model_client
from worldmock.configuration.model.model_client import LLMDispatchError, ModelClient
from worldmock.foundation.serialization import canonical_json

ROOT = Path(__file__).resolve().parents[3]
#: 线路探针在基线环境里写出的固定样例。
FIXTURE = ROOT / "tests" / "fixtures" / "model-client-wire.json"
#: 固定样例的五个用例，次序固定。
CASES = ("action", "continuation", "truncation_retry", "plan_day", "memory_digest")
#: 没有监听的本机端点。
_REFUSED = "http://127.0.0.1:9/v1"
#: 一律传输失败时（传输计数 1、停放预算 600 秒）每次失败发送后的等待；最后一次结束调用。
_PARKED = [2, 4, 8, 16, 32, 64, 120, 120, 120, None]
#: 本机服务回非 200 状态时的错误正文，同线路探针服务：文字里没有限流字样，LiteLLM 只按状态码映射异常类型。
_FAILURE = {"error": {"message": "probe failure", "type": "probe_error"}}


def _probe_request() -> dict:
    """@brief 故障注入比较的请求：两边相同的六键请求。"""
    return {"model": "probe-model", "messages": [{"role": "user", "content": "probe"}],
            "tools": [{"type": "function", "function": {
                "name": "submit_probe", "strict": True, "description": "Probe tool.",
                "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}}}],
            "tool_choice": "required", "max_tokens": 8000, "extra_body": {"thinking": {"type": "disabled"}}}


def _rate_limited(retry_after: str) -> list:
    """@brief 前三次 429 带给定的 Retry-After，之后 200。"""
    return [(429, {"Retry-After": retry_after}, 0, _FAILURE)] * 3 + [(200, {}, 0, None)]


#: 场景 → (本机服务的回答，None 表示端点没有监听；服务收到的请求数；每条失败发送之后的等待秒数，None 表示结束调用；
#: 每条失败发送的错误链；调用是否成功)。预算 max_retries=2、transport_max_retries=1。
_SCENARIOS = {
    "400": ([(400, {}, 0, _FAILURE)], 1, [None], "BadRequestError <- OpenAIError <- BadRequestError", False),
    "401": ([(401, {}, 0, _FAILURE)], 1, [None], "AuthenticationError <- OpenAIError <- AuthenticationError", False),
    "429-seconds": (_rate_limited("30"), 4, [30, 30, 30], "RateLimitError <- OpenAIError <- RateLimitError", True),
    "429-date": (_rate_limited("Thu, 01 Jan 2026 00:00:45 GMT"), 4, [45, 45, 45],
                 "RateLimitError <- OpenAIError <- RateLimitError", True),
    "500": ([(500, {}, 0, _FAILURE)], 10, _PARKED, "InternalServerError <- OpenAIError <- InternalServerError",
            False),
    "502": ([(502, {}, 0, _FAILURE)], 3, [0, 0, None], "BadGatewayError <- OpenAIError <- InternalServerError", False),
    "503": ([(503, {}, 0, _FAILURE)] * 2 + [(200, {}, 0, None)], 3, [2, 4],
            "ServiceUnavailableError <- OpenAIError <- InternalServerError", True),
    "connection-refused": (None, 0, _PARKED,
                           "InternalServerError <- OpenAIError <- APIConnectionError <- ConnectError <- ConnectError",
                           False),
    "slow": ([(200, {}, 2, None), (200, {}, 0, None)], 2, [2], "Timeout <- TimeoutError <- CancelledError", True),
}


class _FixedNow(datetime):
    """@brief 「此刻」恒为 2026-01-01T00:00:00+00:00。"""

    @classmethod
    def now(cls, tz=None):
        """@brief 固定的此刻。"""
        return datetime(2026, 1, 1, tzinfo=timezone.utc)


def _expected_attempts(chain: str, waits: list) -> list[dict]:
    """@brief 由每条失败发送之后的等待推出的失败发送记录：序号连续，距第一次发送的毫秒数是此前等待之和。"""
    elapsed, records = 0, []
    for number, wait in enumerate(waits, start=1):
        records.append({"attempt": number, "error": chain, "failed_after_ms": elapsed * 1000,
                        "wait_ms": None if wait is None else wait * 1000})
        elapsed += wait or 0
    return records


def _failure_text(chain: str, waits: list) -> str:
    """@brief 最终失败的文字：发送次数、耗时（假时钟上的等待之和）、最后的错误类型与错误链。"""
    elapsed = sum(wait for wait in waits if wait is not None)
    return (f"Failed to get valid response after {len(waits)} attempts in {elapsed:.1f} s. "
            f"Last error type: {chain.split(' <- ')[0]}. Error chain: {chain}")


@pytest.mark.parametrize("name", CASES)
async def test_request_bodies_match_the_frozen_probe_bodies(name):
    """固定样例的每个用例经真实 ModelClient 发到本机服务：恰好一个 HTTP 请求，正文字节等于探针冻结的正文，请求头的读
    超时为 300.0、重试计数为 0，响应序列化的规范 JSON 等于冻结的结果。"""
    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]
    assert [case["name"] for case in cases] == list(CASES)
    case = next(item for item in cases if item["name"] == name)
    with _local_server([(200, {}, 0, case["response"])]) as (url, hits):
        client = ModelClient(base_url=url, api_key="probe-key", max_retries=0, transport_max_retries=0)
        try:
            response = await client.call(case["request"])
        finally:
            await client.close()
    assert len(hits) == 1
    method, path, headers, body = hits[0]
    assert (method, path) == ("POST", "/v1/chat/completions")
    assert body == case["body"].encode("utf-8")
    named = {key.lower(): value for key, value in headers}
    assert named["x-stainless-read-timeout"] == "300.0" and named["x-stainless-retry-count"] == "0"
    assert canonical_json(response.model_dump(mode="json")).decode("utf-8") == case["serialized"]


async def test_each_send_is_exactly_one_http_request(monkeypatch):
    """SDK 不在客户端之外重发：一律 400 时服务收到 1 个请求；一律 500、停放预算 600 秒时服务收到的请求数等于失败发送的
    条数 10。"""
    monkeypatch.setattr(model_client, "_PARK_BUDGET_SECONDS", 600)
    _fake_time(monkeypatch)
    for status, expected in ((400, 1), (500, 10)):
        with _local_server([(status, {}, 0, _FAILURE)]) as (url, hits):
            client = ModelClient(base_url=url, api_key="probe-key", max_retries=2, transport_max_retries=1)
            try:
                with model_client.collect_transport_attempts() as attempts:
                    with pytest.raises(LLMDispatchError):
                        await client.call(_probe_request())
            finally:
                await client.close()
        assert len(hits) == len(attempts) == expected


@pytest.mark.parametrize("name", list(_SCENARIOS))
async def test_fault_scenarios_match_the_baseline_client(monkeypatch, name):
    """故障场景（假时钟从 0 起，此刻固定，停放预算 600 秒，慢响应场景的单次期限 0.5 秒）：服务收到的请求数、等待序列、
    失败发送的四个字段与结局都等于期望值表。"""
    answers, served, waits, chain, succeeds = _SCENARIOS[name]
    monkeypatch.setattr(model_client, "_PARK_BUDGET_SECONDS", 600)
    monkeypatch.setattr(model_client, "datetime", _FixedNow)
    if name == "slow":
        monkeypatch.setattr(model_client, "_DEADLINE_BASE_SECONDS", 0.5)
        monkeypatch.setattr(model_client, "_DEADLINE_SECONDS_PER_TOKEN", 0.0)
    _, delays = _fake_time(monkeypatch)
    server = nullcontext((_REFUSED, [])) if answers is None else _local_server(answers)
    with server as (url, hits):
        client = ModelClient(base_url=url, api_key="probe-key", max_retries=2, transport_max_retries=1)
        try:
            with model_client.collect_transport_attempts() as attempts:
                if succeeds:
                    response = await client.call(_probe_request())
                else:
                    with pytest.raises(LLMDispatchError) as captured:
                        await client.call(_probe_request())
        finally:
            await client.close()
    assert len(hits) == served
    assert delays == [wait for wait in waits if wait]
    assert attempts == _expected_attempts(chain, waits)
    if succeeds:
        assert response.choices[0].message.content == "ok"
    else:
        assert str(captured.value) == _failure_text(chain, waits)
