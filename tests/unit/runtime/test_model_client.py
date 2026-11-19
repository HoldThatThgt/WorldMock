"""@brief 模型客户端：两类重试预算相互独立，计数用尽后传输与限流停放重发同一请求，单次发送有整次期限；每次发送都
显式关闭底层重试、给出 300 秒请求超时，只接受六键请求；失败发送进入收集器，最终失败的类名与文字不变。

除真实本机服务的用例外全部使用 scripted_client：真实 ModelClient 只换掉 litellm 发送与等待；脚本里带响应头的 429
取自真实客户端对本机服务的回答。"""

import asyncio
import email.utils
import gc
import logging
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from litellm.exceptions import (
    APIConnectionError,
    AuthenticationError,
    BadGatewayError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout,
)
from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from tests.unit.runtime.model_client_fixtures import (
    OFFLINE_BASE,
    OFFLINE_KEY,
    OFFLINE_MODEL,
    _fake_time,
    _local_server,
    _rate_limit,
    draft_request,
    provider_error,
    scripted_client,
)
from worldmock.configuration.model import model_client
from worldmock.configuration.model.model_client import REQUEST_KEYS, LLMDispatchError, ModelClient

#: 每次发送交给 litellm.acompletion 的十二个关键字参数。
_SEND_KEYS = {"model", "messages", "tools", "tool_choice", "max_tokens", "extra_body", "api_base", "api_key", "client",
              "max_retries", "timeout", "stream"}
#: 已删除的限流标记属性名（由片段拼出）。
_RATE_LIMIT_FLAG = "rate_limit" + "_like"


def _success():
    """@brief 有用量的模型响应。"""
    return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=10, completion_tokens=20))


@pytest.fixture
def network_clients(monkeypatch):
    """@brief 把模型客户端模块里的 AsyncOpenAI 换成记录构造参数与关闭次数的真实子类；返回 (实例, 构造参数) 列表。"""
    built = []

    class Recording(AsyncOpenAI):
        """@brief 记下构造参数与 close 次数的 OpenAI SDK 异步客户端。"""

        def __init__(self, **arguments):
            super().__init__(**arguments)
            self.closes = 0
            built.append((self, arguments))

        async def close(self):
            """@brief 记一次关闭，再真正关闭。"""
            self.closes += 1
            await super().close()

    monkeypatch.setattr(model_client, "AsyncOpenAI", Recording)
    return built


async def test_one_client_serves_every_call_over_one_network_client(monkeypatch, network_clients):
    """一个 ModelClient 依次发人物回合、零点计划与回顾三种形状的请求：网络客户端恰构造一次、不带 SDK 缺省，三次发送
    交给 litellm 的都是这一个对象。"""
    client, _, sent = scripted_client(monkeypatch, [_success(), _success(), _success()])
    turn = {"model": OFFLINE_MODEL,
            "messages": [{"role": "system", "content": "系统层"}, {"role": "user", "content": "[]"}],
            "tools": [{"type": "function", "function": {"name": "start_main_activity", "parameters": {}}}],
            "tool_choice": "auto", "max_tokens": 16000, "extra_body": {"thinking": {"type": "enabled"}}}
    plan = {**draft_request([{"role": "user", "content": "plan"}]),
            "tools": [{"type": "function", "function": {"name": "submit_day_plan", "parameters": {}}}]}
    digest = {**draft_request([{"role": "user", "content": "digest"}]),
              "tools": [{"type": "function", "function": {"name": "submit_memory_digest", "parameters": {}}}]}
    for request in (turn, plan, digest):
        assert tuple(request) == REQUEST_KEYS
        await client.call(request)
    ((network, arguments),) = network_clients
    assert arguments == {"api_key": "test-key", "base_url": "https://example.invalid", "max_retries": 0,
                         "timeout": 300.0}
    assert len(sent) == 3 and all(item["client"] is network for item in sent)


@pytest.mark.parametrize("kind", [APIConnectionError, Timeout, InternalServerError, ServiceUnavailableError])
async def test_typed_transient_failure_recovers_with_zero_ordinary_retries(monkeypatch, kind):
    """显式零普通重试仍允许类型化暂时错误恢复，模型与请求保持相同。"""
    response = _success()
    client, delays, requests = scripted_client(monkeypatch, [provider_error(kind), response], max_retries=0)
    result = await client.call(draft_request([{"role": "user", "content": "hello"}]))
    assert result is response
    assert delays == [2.0]
    assert requests[0] == requests[1]
    assert requests[0]["model"] == "openai/offline-model"


@pytest.mark.parametrize("kind", [APIConnectionError, Timeout, InternalServerError, ServiceUnavailableError])
async def test_persistent_transport_failure_ends_when_the_park_budget_is_spent(monkeypatch, kind):
    """不停放时（停放类别为空），持续暂时错误耗尽三次传输重试后本该停放的第一次失败即结束，准确报告四次外部请求。"""
    monkeypatch.setattr(model_client, "_PARKED_ERRORS", ())
    failure = provider_error(kind)
    client, delays, requests = scripted_client(monkeypatch, [failure] * 5, max_retries=0)
    with pytest.raises(LLMDispatchError, match="after 4 attempts") as captured:
        await client.call(draft_request())
    assert captured.value.__cause__ is failure
    assert len(requests) == 4
    assert delays == [2.0, 4.0, 8.0]


@pytest.mark.parametrize("kind", [AuthenticationError, BadRequestError])
@pytest.mark.parametrize("after_transport", [False, True])
async def test_non_transport_provider_errors_do_not_use_transport_budget(monkeypatch, kind, after_transport):
    """认证与无效请求不能使用剩余的传输重试预算（限流 429 的停放见下方用例）。"""
    script = [provider_error(APIConnectionError)] if after_transport else []
    script.append(provider_error(kind))
    client, delays, requests = scripted_client(monkeypatch, script + [_success()], max_retries=0)
    attempts = 2 if after_transport else 1
    with pytest.raises(LLMDispatchError, match=f"after {attempts} attempts"):
        await client.call(draft_request())
    assert len(requests) == attempts
    assert delays == ([2.0] if after_transport else [])


class _Payload(BaseModel):
    """制造真实结构校验错误。"""

    value: int


def _invalid_payload():
    """@brief 返回结构校验错误对象。"""
    try:
        _Payload(value="invalid")
    except ValidationError as error:
        return error
    raise AssertionError("Expected validation failure")


@pytest.mark.parametrize("failure", [
    ValueError("connection error in user text"),
    RuntimeError("business rule rejected: timeout"),
    _invalid_payload(),
])
@pytest.mark.parametrize("after_transport", [False, True])
async def test_payload_and_business_errors_do_not_gain_transport_retries(monkeypatch, failure, after_transport):
    """错误文案含传输关键词也不重试无效载荷、协议或业务错误。"""
    script = [provider_error(Timeout)] if after_transport else []
    script.append(failure)
    client, delays, requests = scripted_client(monkeypatch, script + [_success()], max_retries=0)
    attempts = 2 if after_transport else 1
    with pytest.raises(LLMDispatchError, match=f"after {attempts} attempts"):
        await client.call(draft_request())
    assert len(requests) == attempts
    assert delays == ([2.0] if after_transport else [])


async def test_mixed_failures_spend_separate_budgets(monkeypatch):
    """传输失败不消耗普通预算，普通失败也不推进传输退避。"""
    response = _success()
    client, delays, requests = scripted_client(monkeypatch, [
        provider_error(APIConnectionError), ValueError("invalid payload"), provider_error(Timeout),
        provider_error(RateLimitError), provider_error(ServiceUnavailableError), response,
    ], max_retries=2)
    assert await client.call(draft_request()) is response
    assert len(requests) == 6
    assert delays == [2.0, 4.0, 2.0, 8.0]


async def test_exhausted_ordinary_budget_does_not_borrow_transport_retries(monkeypatch):
    """普通预算独立耗尽后终止，即使仍有传输预算。"""
    client, delays, requests = scripted_client(monkeypatch, [
        ValueError("invalid payload"), provider_error(Timeout), ValueError("invalid payload"), _success(),
    ], max_retries=1)
    with pytest.raises(LLMDispatchError, match="after 3 attempts"):
        await client.call(draft_request())
    assert len(requests) == 3
    assert delays == [2.0]


async def test_exhausted_transport_budget_does_not_borrow_ordinary_retries(monkeypatch):
    """传输预算用尽后停放而不借普通预算：五次超时后成功，退避 2、4、8、16、32；不停放时第四次即结束，
    普通预算始终未用。"""
    response = _success()
    client, delays, _ = scripted_client(monkeypatch, [provider_error(Timeout)] * 5 + [response], max_retries=2)
    assert await client.call(draft_request()) is response
    assert delays == [2.0, 4.0, 8.0, 16.0, 32.0]
    monkeypatch.setattr(model_client, "_PARKED_ERRORS", ())
    client, delays, requests = scripted_client(monkeypatch, [provider_error(Timeout)] * 5, max_retries=2)
    with pytest.raises(LLMDispatchError, match="after 4 attempts"):
        await client.call(draft_request())
    assert len(requests) == 4
    assert delays == [2.0, 4.0, 8.0]


async def test_cancellation_is_not_retried(monkeypatch):
    """取消保持控制信号语义，立即退出，不重发。"""
    client, delays, requests = scripted_client(monkeypatch, [asyncio.CancelledError(), _success()])
    with pytest.raises(asyncio.CancelledError):
        await client.call(draft_request())
    assert len(requests) == 1
    assert delays == []


async def test_rate_limit_uses_fixed_one_second_backoff_with_sixty_second_cap(monkeypatch):
    """普通限流保持一秒指数退避，六十秒封顶，不引入可调参数层。"""
    response = _success()
    client, delays, requests = scripted_client(monkeypatch, [provider_error(RateLimitError)] * 8 + [response],
                                               max_retries=8)
    assert await client.call(draft_request()) is response
    assert delays == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0]
    assert len(requests) == 9


async def test_rate_limit_wording_on_other_errors_does_not_change_the_wait(monkeypatch):
    """限流只按 RateLimitError 类型判定：文字像限流的 502 在计数内立即重发，不等待。"""
    texts = ("try again in 20s", "RouterRateLimitError", "No deployments available for selected model")
    failures = [BadGatewayError(message=text, llm_provider="openai", model=OFFLINE_MODEL) for text in texts]
    response = _success()
    client, delays, _ = scripted_client(monkeypatch, [*failures, response], clock=[0.0], max_retries=3)
    with model_client.collect_transport_attempts() as attempts:
        assert await client.call(draft_request()) is response
    assert delays == []
    assert [item["wait_ms"] for item in attempts] == [0, 0, 0]


async def test_counted_transport_waits_are_two_times_two_to_the_n(monkeypatch):
    """计数内的传输失败等 2·2ⁿ 秒：传输计数 3、三次连接失败后成功，等待依次为 2、4、8 秒。"""
    client, delays, _ = scripted_client(monkeypatch, [provider_error(APIConnectionError)] * 3 + [_success()],
                                        clock=[0.0], transport_max_retries=3)
    await client.call(draft_request())
    assert delays == [2.0, 4.0, 8.0]


async def test_dispatcher_does_not_log_provider_error_body(monkeypatch):
    """供应商错误可能带密钥或请求片段，客户端日志和摘要只记录错误类型；停放中的重发警告写明已停放的秒数。"""
    sensitive = "fake-provider-key-in-error"
    failure = APIConnectionError(message=sensitive, llm_provider="openai", model=OFFLINE_MODEL)
    warnings_seen, errors = [], []
    logger = SimpleNamespace(
        error=lambda template, *args: errors.append(template % args),
        warning=lambda template, *args: warnings_seen.append(template % args),
    )
    monkeypatch.setattr(model_client, "_LOGGER", logger)
    client, _, _ = scripted_client(monkeypatch, [failure] * 5 + [_success()], clock=[0.0])
    await client.call(draft_request())
    assert len(warnings_seen) == 5
    assert warnings_seen[0] == "Request failed for 'offline-model' (attempt 1). Backoff 2.0s. Error: APIConnectionError"
    assert ", parked" not in "".join(warnings_seen[:3])
    assert "(attempt 4, parked 0s)" in warnings_seen[3] and "(attempt 5, parked 16s)" in warnings_seen[4]
    assert all(sensitive not in message for message in warnings_seen)
    monkeypatch.setattr(model_client, "_PARKED_ERRORS", ())
    client, _, _ = scripted_client(monkeypatch, [failure] * 4)
    with pytest.raises(LLMDispatchError) as captured:
        await client.call(draft_request())
    assert sensitive not in str(captured.value) and "APIConnectionError" in str(captured.value)
    assert errors and all(sensitive not in message for message in errors)
    assert "APIConnectionError" in errors[-1]


@pytest.mark.parametrize("budget", [0, 1, 2, 5, 10])
async def test_configured_transport_budget_controls_actual_attempts(monkeypatch, budget):
    """不停放时，声明预算直接限定实际发送次数，首次请求不算重试；每次发送的关键字恰为十二个。"""
    monkeypatch.setattr(model_client, "_PARKED_ERRORS", ())
    client, delays, requests = scripted_client(monkeypatch, [provider_error(Timeout)] * (budget + 2),
                                               max_retries=10, transport_max_retries=budget)
    with pytest.raises(LLMDispatchError, match=f"after {budget + 1} attempts"):
        await client.call(draft_request())
    assert len(requests) == budget + 1
    assert len(delays) == budget
    assert all(set(request) == _SEND_KEYS for request in requests)


@pytest.mark.parametrize("kind", [AuthenticationError, BadRequestError])
async def test_authentication_and_parameters_fail_with_unspent_retry_budgets(monkeypatch, kind):
    """认证及参数错误立即失败，不消耗两个服务重试预算。"""
    client, delays, requests = scripted_client(monkeypatch, [provider_error(kind), _success()],
                                               max_retries=10, transport_max_retries=10)
    with pytest.raises(LLMDispatchError, match="after 1 attempts"):
        await client.call(draft_request())
    assert len(requests) == 1
    assert delays == []


async def test_zero_retries_makes_exactly_one_external_attempt(monkeypatch):
    """显式零普通重试时，确定性参数错误只产生一个外部请求。"""
    client, _, requests = scripted_client(monkeypatch, [provider_error(BadRequestError)] * 2, max_retries=0)
    with pytest.raises(LLMDispatchError, match="after 1 attempts"):
        await client.call(draft_request())
    assert len(requests) == 1


@pytest.mark.parametrize("budget", [True, -1, 11, 1.5, "3"])
@pytest.mark.parametrize("field", ["max_retries", "transport_max_retries"])
def test_invalid_retry_budgets_are_refused_when_the_client_is_built(monkeypatch, caplog, field, budget):
    """两类重试次数在构造客户端时校验一次：不合法时写同文错误日志并拒绝，不能转型或修补，不建网络客户端。"""
    built = []
    monkeypatch.setattr(model_client, "AsyncOpenAI", lambda **arguments: built.append(arguments))
    caplog.set_level(logging.ERROR, logger=model_client.__name__)
    arguments = {"max_retries": 0, "transport_max_retries": 3, field: budget}
    with pytest.raises(ValueError) as captured:
        ModelClient(base_url=OFFLINE_BASE, api_key=OFFLINE_KEY, **arguments)
    assert str(captured.value) == "Model retry budgets must be integers in [0, 10]"
    assert [record.getMessage() for record in caplog.records if record.name == model_client.__name__] == [
        "Model retry budgets must be integers in [0, 10]"]
    assert built == []


async def test_every_send_passes_zero_retries_and_the_three_hundred_second_timeout(monkeypatch):
    """一次连接失败再成功：两次发送的关键字恰为十二个，五个请求值原样，端点与凭据、构造时的网络客户端、SDK 重试 0 次、
    请求超时 300 秒与非流式每次都显式给出。"""
    request = draft_request([{"role": "user", "content": "hello"}])
    client, _, sent = scripted_client(monkeypatch, [provider_error(APIConnectionError), _success()])
    await client.call(request)
    assert len(sent) == 2
    for arguments in sent:
        assert set(arguments) == _SEND_KEYS
        assert arguments["model"] == "openai/offline-model"
        assert {key: arguments[key] for key in REQUEST_KEYS[1:]} == {key: request[key] for key in REQUEST_KEYS[1:]}
        assert arguments["api_base"] == "https://example.invalid" and arguments["api_key"] == "test-key"
        assert arguments["client"] is client._client
        assert arguments["max_retries"] == 0 and arguments["timeout"] == 300.0 and arguments["stream"] is False


async def test_client_sends_the_declared_credential_as_given(monkeypatch, network_clients):
    """声明的凭据原样交给网络客户端与每次发送，不去空白、不切分。"""
    _, _, sent = scripted_client(monkeypatch, [provider_error(APIConnectionError), _success()])
    client = ModelClient(base_url=OFFLINE_BASE, api_key="literal,credential", max_retries=0, transport_max_retries=3)
    await client.call(draft_request())
    assert network_clients[-1][1]["api_key"] == "literal,credential"
    assert len(sent) == 2 and all(arguments["api_key"] == "literal,credential" for arguments in sent)


async def test_client_sends_through_the_openai_compatible_route_with_the_configured_base(monkeypatch):
    """每次发送走 openai 兼容路径：模型名加 openai/ 前缀，端点原样透传。"""
    base = "https://api.deepseek.com/beta"
    _, _, sent = scripted_client(monkeypatch, [_success()])
    client = ModelClient(base_url=base, api_key=OFFLINE_KEY, max_retries=0, transport_max_retries=3)
    await client.call({**draft_request(), "model": "deepseek-flash"})
    assert sent[0]["model"].startswith("openai/") and sent[0]["model"] == "openai/deepseek-flash"
    assert sent[0]["api_base"] == base


@pytest.mark.parametrize("draft", [
    {**draft_request(), "stream": False},
    {**draft_request(), "max_retries": 0},
    {**draft_request(), "transport_max_retries": 3},
    {key: value for key, value in draft_request().items() if key != "tools"},
], ids=["stream", "max_retries", "transport_max_retries", "missing-tools"])
async def test_request_keys_must_be_exactly_the_six_draft_keys(monkeypatch, draft):
    """请求的键集合必须恰为六个：多一个或少一个都在发送之前拒绝，不登记失败发送。"""
    client, delays, sent = scripted_client(monkeypatch, [_success()])
    with model_client.collect_transport_attempts() as attempts:
        with pytest.raises(ValueError) as captured:
            await client.call(draft)
    assert str(captured.value) == (
        "Model request keys must be exactly model, messages, tools, tool_choice, max_tokens, extra_body")
    assert sent == [] and attempts == [] and delays == []


async def test_close_closes_the_network_client_once(network_clients):
    """一次 close 恰好关闭它持有的网络客户端一次，之后网络客户端已关闭。"""
    client = ModelClient(base_url=OFFLINE_BASE, api_key=OFFLINE_KEY, max_retries=0, transport_max_retries=3)
    ((network, _),) = network_clients
    assert not network.is_closed()
    await client.close()
    assert network.closes == 1
    assert network.is_closed()


class _LogRecords(logging.Handler):
    """@brief 记下经过的每条日志文字。"""

    def __init__(self):
        super().__init__(logging.DEBUG)
        self.messages = []

    def emit(self, record):
        """@brief 记下一条日志的文字。"""
        self.messages.append(record.getMessage())


async def test_a_closed_client_leaves_no_unclosed_session_records():
    """真实 ModelClient 对本机服务完成五次调用后关闭：全部回答 ok，垃圾回收后没有含 Unclosed 的告警或日志记录（asyncio、
    litellm 两个日志器，另加 litellm 自己的详细日志器 LiteLLM）。"""
    records = _LogRecords()
    loggers = [logging.getLogger("asyncio"), logging.getLogger("litellm"), logging.getLogger("LiteLLM")]
    levels = [logger.level for logger in loggers]
    for logger in loggers:
        logger.addHandler(records)
        logger.setLevel(logging.DEBUG)
    answers = []
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            with _local_server([(200, {}, 0, None)]) as (url, hits):
                client = ModelClient(base_url=url, api_key="test-only", max_retries=0, transport_max_retries=0)
                for _ in range(5):
                    response = await client.call(draft_request([{"role": "user", "content": "hello"}]))
                    answers.append(response.choices[0].message.content)
                await client.close()
            gc.collect()
    finally:
        for logger, level in zip(loggers, levels):
            logger.removeHandler(records)
            logger.setLevel(level)
    assert answers == ["ok"] * 5 and len(hits) == 5
    assert not [item for item in caught if "Unclosed" in str(item.message)]
    assert not [message for message in records.messages if "Unclosed" in message]


def _rooted_failure():
    """@brief 一次以 ConnectionResetError 为显式原因的 InternalServerError（与运行里断网的根因同形）。"""
    try:
        try:
            raise ConnectionResetError("reset by peer")
        except ConnectionResetError as cause:
            raise provider_error(InternalServerError) from cause
    except InternalServerError as error:
        return error


def _recording_logger(monkeypatch):
    """@brief 把模型客户端日志换成记录器，返回 (级别, 文本) 列表。"""
    records = []

    def writer(level):
        return lambda template, *args: records.append((level, template % args))

    monkeypatch.setattr(model_client, "_LOGGER", SimpleNamespace(
        warning=writer("warning"), error=writer("error"), exception=writer("exception")))
    return records


async def test_spent_transport_count_parks_and_resends_the_identical_request(monkeypatch):
    """十次连接失败后成功、传输计数 3：计数内 2、4、8 秒，之后停放按 2·2ⁿ 封顶 120 秒；十一次发送的参数逐值相同，
    每次的关键字恰为十二个，计数与失败发送都不上线路。"""
    response = _success()
    client, delays, requests = scripted_client(monkeypatch, [provider_error(APIConnectionError)] * 10 + [response],
                                               clock=[0.0], max_retries=0, transport_max_retries=3)
    result = await client.call(draft_request([{"role": "user", "content": "hello"}], max_tokens=16000))
    assert result is response
    assert delays == [2, 4, 8, 16, 32, 64, 120, 120, 120, 120]
    assert len(requests) == 11 and all(request == requests[0] for request in requests)
    assert all(set(request) == _SEND_KEYS for request in requests)


async def test_park_gives_up_after_twelve_hours_with_the_root_cause(monkeypatch):
    """一直断网：停放十二小时后第 366 次发送失败，耗时 43206 秒，错误写明最后的类型与根因链。"""
    failure = _rooted_failure()
    client, delays, requests = scripted_client(monkeypatch, [failure] * 400, clock=[0.0], max_retries=0,
                                               transport_max_retries=3)
    with pytest.raises(LLMDispatchError) as captured:
        await client.call(draft_request())
    assert captured.value.args[0] == (
        "Failed to get valid response after 366 attempts in 43206.0 s. Last error type: InternalServerError. "
        "Error chain: InternalServerError <- ConnectionResetError")
    assert captured.value.__cause__ is failure
    assert len(requests) == 366
    assert sum(delays) == 43206


async def test_rate_limit_parks_after_the_ordinary_count_and_honours_retry_after(monkeypatch):
    """普通计数为零的 429 立即停放：Retry-After 30 秒等 30 秒；HTTP 日期约 45 秒后时等到那一刻；不带头时 1、2、4… 封顶 120。"""
    target = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=45)
    seconds, dated, bare = (await _rate_limit({"retry-after": "30"}),
                            await _rate_limit({"retry-after": email.utils.format_datetime(target, usegmt=True)}),
                            await _rate_limit())
    client, delays, _ = scripted_client(monkeypatch, [seconds] * 3 + [_success()], clock=[0.0], max_retries=0)
    await client.call(draft_request())
    assert delays == [30, 30, 30]
    client, delays, _ = scripted_client(monkeypatch, [dated] * 3 + [_success()], clock=[0.0], max_retries=0)
    before = datetime.now(timezone.utc)
    await client.call(draft_request())
    after = datetime.now(timezone.utc)
    assert len(delays) == 3
    assert all((target - after).total_seconds() <= delay <= (target - before).total_seconds() for delay in delays)
    client, delays, _ = scripted_client(monkeypatch, [bare] * 9 + [_success()], clock=[0.0], max_retries=0)
    await client.call(draft_request())
    assert delays == [1, 2, 4, 8, 16, 32, 64, 120, 120]


@pytest.mark.parametrize("max_retries", [0, 2], ids=["parked", "counted"])
async def test_retry_after_beyond_the_budget_fails_at_once(monkeypatch, max_retries):
    """Retry-After 五万秒长于十二小时：停放中与计数内都在第一次发送后失败，不等待。"""
    limited = await _rate_limit({"retry-after": "50000"})
    client, delays, requests = scripted_client(monkeypatch, [limited, _success()], clock=[0.0],
                                               max_retries=max_retries)
    with pytest.raises(LLMDispatchError, match="after 1 attempts"):
        await client.call(draft_request())
    assert delays == [] and len(requests) == 1


async def test_unparsable_retry_after_is_ignored_with_a_warning(monkeypatch):
    """Retry-After 无法解析：按退避等待，写一次警告。"""
    limited = await _rate_limit({"retry-after": "soon"})
    records = _recording_logger(monkeypatch)
    client, delays, _ = scripted_client(monkeypatch, [limited, _success()], clock=[0.0], max_retries=0)
    await client.call(draft_request())
    assert delays == [1]
    assert [text for level, text in records if level == "warning"].count(
        "Ignoring unparsable Retry-After header: 'soon'") == 1


async def test_retry_after_date_without_a_zone_is_read_as_utc(monkeypatch):
    """Retry-After 的 HTTP 日期以 -0000 结尾（解析结果不带时区）：按 UTC 读，停放中等到约 45 秒后的那一刻。"""
    target = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=45)
    limited = await _rate_limit({"retry-after": email.utils.format_datetime(target.replace(tzinfo=None))})
    client, delays, _ = scripted_client(monkeypatch, [limited, _success()], clock=[0.0], max_retries=0)
    before = datetime.now(timezone.utc)
    await client.call(draft_request())
    after = datetime.now(timezone.utc)
    assert len(delays) == 1
    assert (target - after).total_seconds() <= delays[0] <= (target - before).total_seconds()


async def test_counted_rate_limit_waits_honour_a_longer_retry_after(monkeypatch):
    """计数内的 429 也不短于 Retry-After：两次各等 5 秒后成功。"""
    response, limited = _success(), await _rate_limit({"retry-after": "5"})
    client, delays, _ = scripted_client(monkeypatch, [limited] * 2 + [response], clock=[0.0], max_retries=2)
    assert await client.call(draft_request()) is response
    assert delays == [5, 5]


@pytest.mark.parametrize("failure", [provider_error(AuthenticationError), provider_error(BadRequestError),
                                     provider_error(BadGatewayError), ValueError("bad payload")],
                         ids=["authentication", "bad-request", "bad-gateway", "ordinary"])
async def test_fatal_and_ordinary_failures_never_park(monkeypatch, failure):
    """鉴权、参数与普通失败不停放：普通计数为零时第一次就失败；像限流的文本只在计数内立即重发，用尽即失败。"""
    client, delays, _ = scripted_client(monkeypatch, [failure] * 3, clock=[0.0], max_retries=0)
    with pytest.raises(LLMDispatchError, match="after 1 attempts"):
        await client.call(draft_request())
    assert delays == []
    client, delays, _ = scripted_client(monkeypatch, [ValueError("try again in 3s")] * 3, clock=[0.0], max_retries=1)
    with pytest.raises(LLMDispatchError, match="after 2 attempts"):
        await client.call(draft_request())
    assert delays == []


async def test_deadline_expiry_resends_the_same_request_as_a_transport_retry(monkeypatch):
    """服务卡住超过整次期限：这次发送按传输超时放弃，2 秒后重发同一请求并拿到回答；失败发送记下超时链。"""
    monkeypatch.setattr(model_client, "_DEADLINE_BASE_SECONDS", 0.05)
    monkeypatch.setattr(model_client, "_DEADLINE_SECONDS_PER_TOKEN", 0)
    response = _success()
    client, delays, requests = scripted_client(monkeypatch, [response])
    scripted = model_client.litellm.acompletion

    async def stalling(**arguments):
        """第一次挂起在不会被设置的事件上，之后交给脚本。"""
        if not requests:
            requests.append(dict(arguments))
            await asyncio.Event().wait()
        return await scripted(**arguments)

    monkeypatch.setattr(model_client.litellm, "acompletion", stalling)
    with model_client.collect_transport_attempts() as attempts:
        result = await client.call(draft_request([{"role": "user", "content": "hello"}], max_tokens=16000))
    assert result is response
    assert len(requests) == 2 and requests[0] == requests[1]
    assert len(attempts) == 1 and attempts[0]["error"].startswith("Timeout <- TimeoutError")
    assert attempts[0]["wait_ms"] == 2000 and delays == [2.0]


def test_deadline_scales_with_max_tokens():
    """整次期限是 120 秒加每个输出 token 7.5 毫秒；没有正整数 max_tokens 时为 120 秒。"""
    assert model_client._attempt_deadline({"max_tokens": 16000}) == 240.0
    assert model_client._attempt_deadline({"max_tokens": 8000}) == 180.0
    assert model_client._attempt_deadline({}) == 120.0
    assert model_client._attempt_deadline({"max_tokens": True}) == 120.0


async def test_foreign_timeout_error_is_not_relabelled(monkeypatch):
    """期限未到时别处抛出的 TimeoutError 不改成传输超时，按普通失败结束。"""
    client, delays, _ = scripted_client(monkeypatch, [TimeoutError(), _success()], max_retries=0)
    with pytest.raises(LLMDispatchError, match="after 1 attempts") as captured:
        await client.call(draft_request())
    assert "Last error type: TimeoutError" in str(captured.value) and delays == []


async def test_collector_records_each_failed_send_outside_the_request(monkeypatch):
    """收集器按顺序记下每次失败发送的序号、错误链、距第一次发送的毫秒数与等待毫秒数；每次发送的关键字恰为十二个。"""
    response = _success()
    client, _, requests = scripted_client(monkeypatch, [provider_error(APIConnectionError), provider_error(Timeout),
                                                        response], clock=[0.0], max_retries=0)
    with model_client.collect_transport_attempts() as attempts:
        assert await client.call(draft_request()) is response
    assert attempts == [
        {"attempt": 1, "error": "APIConnectionError", "failed_after_ms": 0, "wait_ms": 2000},
        {"attempt": 2, "error": "Timeout", "failed_after_ms": 2000, "wait_ms": 4000}]
    assert all(set(request) == _SEND_KEYS for request in requests)


async def test_failed_call_records_its_final_send_with_a_null_wait(monkeypatch):
    """结束调用的那次失败也记下，等待为空。"""
    client, _, _ = scripted_client(monkeypatch, [provider_error(AuthenticationError)], clock=[0.0], max_retries=0)
    with model_client.collect_transport_attempts() as attempts:
        with pytest.raises(LLMDispatchError):
            await client.call(draft_request())
    assert attempts == [{"attempt": 1, "error": "AuthenticationError", "failed_after_ms": 0, "wait_ms": None}]


async def test_collectors_are_isolated_between_concurrent_tasks(monkeypatch):
    """同一 ModelClient 上两个并发任务各有收集器（分别失败 1 次与 2 次）：各自只记下自己的失败发送。"""
    real_sleep = asyncio.sleep
    client, _, _ = scripted_client(monkeypatch, [])
    failures, sent = {"a": 1, "b": 2}, {}

    async def per_message(**arguments):
        """先让出循环使两个任务交错，再按消息正文决定失败或回答。"""
        await real_sleep(0)
        text = arguments["messages"][0]["content"]
        sent[text] = sent.get(text, 0) + 1
        if sent[text] <= failures[text]:
            raise provider_error(APIConnectionError)
        return _success()

    async def yielding(delay):
        """等待只让出循环。"""
        await real_sleep(0)

    monkeypatch.setattr(model_client.litellm, "acompletion", per_message)
    monkeypatch.setattr(model_client, "asyncio", SimpleNamespace(sleep=yielding, timeout=asyncio.timeout))

    async def one(text):
        with model_client.collect_transport_attempts() as attempts:
            await client.call(draft_request([{"role": "user", "content": text}]))
        return attempts

    first, second = await asyncio.gather(one("a"), one("b"))
    assert [item["attempt"] for item in first] == [1]
    assert [item["attempt"] for item in second] == [1, 2]
    assert sent == {"a": 2, "b": 3}


async def test_without_a_collector_nothing_is_recorded(monkeypatch):
    """没有收集器时失败后照常重发成功，不留下任何收集器。"""
    response = _success()
    client, _, _ = scripted_client(monkeypatch, [provider_error(APIConnectionError), response])
    assert await client.call(draft_request()) is response
    assert model_client._TRANSPORT_ATTEMPTS.get() is None


async def test_nested_collectors_restore_the_outer_one(monkeypatch):
    """内层收集器退出后恢复外层：内层期间的一次失败只进内层，之后的一次失败只进外层；外层退出后不留收集器。"""
    script = [provider_error(APIConnectionError), {"ok": 1}, provider_error(APIConnectionError), {"ok": 2}]
    client, _, _ = scripted_client(monkeypatch, script, clock=[0.0])
    with model_client.collect_transport_attempts() as outer:
        with model_client.collect_transport_attempts() as inner:
            await client.call(draft_request())
        await client.call(draft_request())
    assert len(inner) == 1 and len(outer) == 1
    assert model_client._TRANSPORT_ATTEMPTS.get() is None


def _raised(build):
    """@brief 执行一段会抛出异常的代码并返回那个异常。"""
    try:
        build()
    except Exception as error:
        return error
    raise AssertionError("expected an exception")


def test_error_chain_follows_cause_then_unsuppressed_context():
    """异常链取显式原因、再取未抑制的隐式上下文；from None 截断；自环与过深的链止于重复与 16 层。"""
    def explicit():
        raise RuntimeError("outer") from ValueError("inner")

    def implicit():
        try:
            raise KeyError("first")
        except KeyError:
            raise OSError("second")

    def suppressed():
        try:
            raise KeyError("first")
        except KeyError:
            raise OSError("second") from None

    assert model_client.error_chain(_raised(explicit)) == "RuntimeError <- ValueError"
    assert model_client.error_chain(_raised(implicit)) == "OSError <- KeyError"
    assert model_client.error_chain(_raised(suppressed)) == "OSError"
    cycle = ValueError("self")
    cycle.__cause__ = cycle
    assert model_client.error_chain(cycle) == "ValueError"
    deep = ValueError("0")
    for index in range(1, 20):
        outer = KeyError(str(index))
        outer.__cause__ = deep
        deep = outer
    assert model_client.error_chain(deep).split(" <- ") == ["KeyError"] * 16


async def test_final_failure_class_and_text_are_unchanged(monkeypatch, caplog):
    """最终失败的类是 RuntimeError 的直接子类，只带一条文字、以最后一次异常为原因，repr 与基线逐字相同；抛出前写一条
    同义错误日志。"""
    failure = provider_error(BadRequestError)
    client, _, _ = scripted_client(monkeypatch, [failure], clock=[0.0])
    caplog.set_level(logging.ERROR, logger=model_client.__name__)
    with pytest.raises(LLMDispatchError) as captured:
        await client.call(draft_request())
    error = captured.value
    assert repr(error) == ("LLMDispatchError('Failed to get valid response after 1 attempts in 0.0 s. "
                           "Last error type: BadRequestError. Error chain: BadRequestError')")
    assert type(error).__mro__[1] is RuntimeError
    assert error.__cause__ is failure and len(error.args) == 1
    assert not hasattr(error, _RATE_LIMIT_FLAG)
    assert [record.getMessage() for record in caplog.records if record.name == model_client.__name__] == [
        "Model request failed after 1 attempts in 0.0 s: BadRequestError"]


async def test_success_returns_the_response_unchanged(monkeypatch):
    """成功时原样返回脚本给出的响应对象；两次成功调用前后实例属性的键与取值都不变。"""
    first, second = _success(), _success()
    client, _, _ = scripted_client(monkeypatch, [first, second])
    before = dict(vars(client))
    assert await client.call(draft_request()) is first
    assert await client.call(draft_request()) is second
    assert vars(client) == before


def test_module_defines_none_of_the_removed_dispatcher_parts():
    """模块里没有上游调度器的路由、并发阀、缓存清理、文字限流判断、用量统计、角色与进程初始化，也没有它们的痕迹。"""
    names = ("LLMClient", "AdaptiveSemaphore", "close_cached_async_clients", "is_rate_limit_like_error",
             "merge_token_stats", "build_client_for_role", "init_dispatchers", "shutdown_dispatchers", "_build_router")
    assert [name for name in names if hasattr(model_client, name)] == []
    source = Path(model_client.__file__).read_text(encoding="utf-8")
    banned = ("AGENT" + "SOCIETY_", "Router" + "(", _RATE_LIMIT_FLAG, "stream" + "=True", "__get" + "state__",
              "in_memory_llm" + "_clients_cache")
    assert [text for text in banned if text in source] == []


async def test_parked_resends_keep_reaching_the_local_server(monkeypatch):
    """真实客户端在停放速率下持续发送：连到关闭的端口每次都是传输错误，十次发送后用完 600 秒停放预算；本机服务一律回
    429 时每次都是 RateLimitError，十一次发送后用完预算。"""
    monkeypatch.setattr(model_client, "_PARK_BUDGET_SECONDS", 600)
    clock, delays = _fake_time(monkeypatch)
    closed = ModelClient(base_url="http://127.0.0.1:9/v1", api_key="test-only", max_retries=0,
                         transport_max_retries=0)
    try:
        with model_client.collect_transport_attempts() as attempts:
            with pytest.raises(LLMDispatchError, match="after 10 attempts"):
                await closed.call(draft_request([{"role": "user", "content": "hello"}]))
    finally:
        await closed.close()
    assert delays == [2, 4, 8, 16, 32, 64, 120, 120, 120]
    assert all(item["error"].split(" <- ")[0] in {"APIConnectionError", "InternalServerError", "Timeout"}
               and "RouterRateLimitError" not in item["error"] for item in attempts)
    clock[0], delays[:] = 0.0, []
    with _local_server([(429, {}, 0, None)]) as (url, _):
        limited = ModelClient(base_url=url, api_key="test-only", max_retries=0, transport_max_retries=3)
        try:
            with model_client.collect_transport_attempts() as attempts:
                with pytest.raises(LLMDispatchError, match="after 11 attempts"):
                    await limited.call(draft_request([{"role": "user", "content": "hello"}]))
        finally:
            await limited.close()
    assert delays == [1, 2, 4, 8, 16, 32, 64, 120, 120, 120]
    assert [item["error"].split(" <- ")[0] for item in attempts] == ["RateLimitError"] * 11


@pytest.mark.parametrize("max_retries", [0, 2], ids=["parked", "counted"])
async def test_waits_exactly_the_providers_retry_after(monkeypatch, max_retries):
    """真实客户端对本机服务：前三次回 429 带 Retry-After: 30，之后回答；每次等待正好是服务给出的 30 秒（计数内与停放中
    都一样），第四次发送拿到回答。"""
    _, delays = _fake_time(monkeypatch)
    with _local_server([(429, {"Retry-After": "30"}, 0, None)] * 3 + [(200, {}, 0, None)]) as (url, hits):
        client = ModelClient(base_url=url, api_key="test-only", max_retries=max_retries, transport_max_retries=3)
        try:
            with model_client.collect_transport_attempts() as attempts:
                result = await client.call(draft_request([{"role": "user", "content": "hello"}]))
        finally:
            await client.close()
    assert result.choices[0].message.content == "ok"
    assert delays == [30, 30, 30] and len(hits) == 4
    assert [(item["error"].split(" <- ")[0], item["wait_ms"]) for item in attempts] == [("RateLimitError", 30000)] * 3


@pytest.mark.parametrize("max_retries", [0, 2], ids=["parked", "counted"])
async def test_retry_after_beyond_the_park_budget_ends_the_call(monkeypatch, max_retries):
    """真实客户端对本机服务：429 带 Retry-After: 50000，长于十二小时停放预算；停放中与计数内都在第一次发送后失败，
    不等待也不重发。"""
    _, delays = _fake_time(monkeypatch)
    with _local_server([(429, {"Retry-After": "50000"}, 0, None)]) as (url, hits):
        client = ModelClient(base_url=url, api_key="test-only", max_retries=max_retries, transport_max_retries=3)
        try:
            with pytest.raises(LLMDispatchError, match="after 1 attempts") as captured:
                await client.call(draft_request([{"role": "user", "content": "hello"}]))
        finally:
            await client.close()
    assert isinstance(captured.value.__cause__, RateLimitError)
    assert delays == [] and len(hits) == 1
