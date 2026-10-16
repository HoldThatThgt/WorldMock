"""传输错误重试与普通重试预算相互独立，计数用尽后传输与限流停放重发同一请求，单次发送有整次期限；
除最后一个真实 Router 用例外全部使用本地脚本化 Router。"""

import asyncio
import email.utils
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from functools import partial
from types import SimpleNamespace

import httpx
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
from pydantic import BaseModel, ValidationError

from agentsociety2.config import llm_dispatcher
from agentsociety2.env.router_base import RouterBase


class _Router:
    """按脚本返回异常或结果，并保留发往同一模型的请求。"""

    def __init__(self, script):
        self.script = iter(script)
        self.requests = []

    async def acompletion(self, **request):
        """返回下一项，禁止隐式联网。"""
        self.requests.append(request)
        item = next(self.script)
        if isinstance(item, BaseException):
            raise item
        return item


class _Semaphore:
    """跟踪许可释放，保证退避时不会占用并发槽位。"""

    def __init__(self):
        self.acquired = 0
        self.releases = []

    async def acquire(self):
        """领取一次许可。"""
        self.acquired += 1

    async def release(self, *, overloaded):
        """释放许可并记录过载状态。"""
        self.acquired -= 1
        self.releases.append(overloaded)

    def record_latency(self, *args, **kwargs):
        """接收时延观测。"""


def _client(monkeypatch, script, clock: list[float] | None = None):
    """构造真实 LLMClient，仅替换 Router、并发许可及等待；给出 clock 时调度器的单调时钟读它，等待把秒数加到它上面。"""
    client = llm_dispatcher.LLMClient("offline-model", "https://example.invalid", "test-key")
    client._router = _Router(script)
    client._sem = _Semaphore()
    monkeypatch.setattr(client, "_ensure_runtime", lambda: None)
    delays = []
    if clock is not None:
        monkeypatch.setattr(llm_dispatcher, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    async def sleep(delay):
        """记录退避秒数，并验证许可已经释放。"""
        assert client._sem.acquired == 0
        delays.append(delay)
        if clock is not None:
            clock[0] += delay

    monkeypatch.setattr(llm_dispatcher.asyncio, "sleep", sleep)
    return client, delays


def _error(kind):
    """构造 LiteLLM 类型化异常，文本不包含任何传输关键词。"""
    return kind(message="busy", llm_provider="openai", model="offline-model")


def _success():
    """返回有用量的模型响应，核对重试成功只结算一次已知用量。"""
    return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=10, completion_tokens=20))


@pytest.mark.parametrize("kind", [APIConnectionError, Timeout, InternalServerError, ServiceUnavailableError])
async def test_typed_transient_failure_recovers_with_zero_ordinary_retries(monkeypatch, kind):
    """显式零普通重试仍允许类型化暂时错误恢复，模型与请求保持相同。"""
    response = _success()
    client, delays = _client(monkeypatch, [_error(kind), response])
    result = await client.call(messages=[{"role": "user", "content": "hello"}], max_retries=0)
    assert result is response
    assert delays == [2.0]
    assert client._router.requests[0] == client._router.requests[1]
    assert client._router.requests[0]["model"] == "offline-model"
    assert client.snapshot_token_stats() == {"offline-model": {"calls": 1, "input": 10, "output": 20}}
    assert len(client._sem.releases) == 2


@pytest.mark.parametrize("kind", [APIConnectionError, Timeout, InternalServerError, ServiceUnavailableError])
async def test_persistent_transport_failure_ends_when_the_park_budget_is_spent(monkeypatch, kind):
    """不停放时（停放类别为空），持续暂时错误耗尽三次传输重试后本该停放的第一次失败即结束，准确报告四次外部请求。"""
    monkeypatch.setattr(llm_dispatcher, "_PARKED_ERRORS", ())
    failure = _error(kind)
    client, delays = _client(monkeypatch, [failure] * 5)
    with pytest.raises(llm_dispatcher.LLMDispatchError, match="after 4 attempts") as captured:
        await client.call(messages=[], max_retries=0)
    assert captured.value.__cause__ is failure
    assert not captured.value.rate_limit_like
    assert len(client._router.requests) == len(client._sem.releases) == 4
    assert delays == [2.0, 4.0, 8.0]
    assert client.snapshot_token_stats() == {}


@pytest.mark.parametrize("kind", [AuthenticationError, BadRequestError])
@pytest.mark.parametrize("after_transport", [False, True])
async def test_non_transport_provider_errors_do_not_use_transport_budget(monkeypatch, kind, after_transport):
    """认证与无效请求不能使用剩余的传输重试预算（限流 429 的停放见下方用例）。"""
    script = [_error(APIConnectionError)] if after_transport else []
    script.append(_error(kind))
    client, delays = _client(monkeypatch, script + [_success()])
    attempts = 2 if after_transport else 1
    with pytest.raises(llm_dispatcher.LLMDispatchError, match=f"after {attempts} attempts") as captured:
        await client.call(messages=[], max_retries=0)
    assert len(client._router.requests) == attempts
    assert delays == ([2.0] if after_transport else [])
    assert captured.value.rate_limit_like is (kind is RateLimitError)


class _Payload(BaseModel):
    """制造真实结构校验错误。"""

    value: int


def _invalid_payload():
    """返回结构校验错误对象。"""
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
    script = [_error(Timeout)] if after_transport else []
    script.append(failure)
    client, delays = _client(monkeypatch, script + [_success()])
    attempts = 2 if after_transport else 1
    with pytest.raises(llm_dispatcher.LLMDispatchError, match=f"after {attempts} attempts"):
        await client.call(messages=[], max_retries=0)
    assert len(client._router.requests) == attempts
    assert delays == ([2.0] if after_transport else [])


async def test_mixed_failures_spend_separate_budgets(monkeypatch):
    """传输失败不消耗普通预算，普通失败也不推进传输退避。"""
    response = _success()
    client, delays = _client(monkeypatch, [
        _error(APIConnectionError), ValueError("invalid payload"), _error(Timeout),
        _error(RateLimitError), _error(ServiceUnavailableError), response,
    ])
    assert await client.call(messages=[], max_retries=2) is response
    assert len(client._router.requests) == 6
    assert delays == [2.0, 4.0, 2.0, 8.0]


async def test_exhausted_ordinary_budget_does_not_borrow_transport_retries(monkeypatch):
    """普通预算独立耗尽后终止，即使仍有传输预算。"""
    client, delays = _client(monkeypatch, [
        ValueError("invalid payload"), _error(Timeout), ValueError("invalid payload"), _success(),
    ])
    with pytest.raises(llm_dispatcher.LLMDispatchError, match="after 3 attempts"):
        await client.call(messages=[], max_retries=1)
    assert len(client._router.requests) == 3
    assert delays == [2.0]


async def test_exhausted_transport_budget_does_not_borrow_ordinary_retries(monkeypatch):
    """传输预算用尽后停放而不借普通预算：五次超时后成功，退避 2、4、8、16、32；不停放时第四次即结束，
    普通预算始终未用。"""
    response = _success()
    client, delays = _client(monkeypatch, [_error(Timeout)] * 5 + [response])
    assert await client.call(messages=[], max_retries=2) is response
    assert delays == [2.0, 4.0, 8.0, 16.0, 32.0]
    monkeypatch.setattr(llm_dispatcher, "_PARKED_ERRORS", ())
    client, delays = _client(monkeypatch, [_error(Timeout)] * 5)
    with pytest.raises(llm_dispatcher.LLMDispatchError, match="after 4 attempts"):
        await client.call(messages=[], max_retries=2)
    assert len(client._router.requests) == 4
    assert delays == [2.0, 4.0, 8.0]


async def test_cancellation_is_not_retried_and_releases_semaphore(monkeypatch):
    """取消保持控制信号语义，立即退出并释放许可。"""
    client, delays = _client(monkeypatch, [llm_dispatcher.asyncio.CancelledError(), _success()])
    with pytest.raises(llm_dispatcher.asyncio.CancelledError):
        await client.call(messages=[], max_retries=0)
    assert len(client._router.requests) == len(client._sem.releases) == 1
    assert delays == []


async def test_rate_limit_uses_fixed_one_second_backoff_with_sixty_second_cap(monkeypatch):
    """普通限流保持一秒指数退避，六十秒封顶，不引入可调参数层。"""
    response = _success()
    client, delays = _client(monkeypatch, [_error(RateLimitError)] * 8 + [response])
    assert await client.call(messages=[], max_retries=8) is response
    assert delays == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0]
    assert len(client._router.requests) == 9


async def test_explicit_non_streaming_request_reaches_router(monkeypatch):
    """生产调用透传的 stream=False 被显式处理且只发送一次。"""
    response = _success()
    client, delays = _client(monkeypatch, [response])
    assert await client.call(messages=[], stream=False, max_retries=0) is response
    assert client._router.requests[0]["stream"] is False
    assert delays == []


async def test_streaming_is_rejected_before_any_external_request(monkeypatch):
    """不支持的流式请求在外部调用前明确拒绝。"""
    client, delays = _client(monkeypatch, [_success()])
    with pytest.raises(NotImplementedError, match="streaming is not supported"):
        await client.call(messages=[], stream=True, max_retries=0)
    assert client._router.requests == []
    assert delays == []


async def test_dispatcher_does_not_log_provider_error_body(monkeypatch):
    """供应商错误可能带密钥或请求片段，调度器日志和摘要只记录错误类型；停放中的重发警告写明已停放的秒数。"""
    sensitive = "fake-provider-key-in-error"
    failure = APIConnectionError(message=sensitive, llm_provider="openai", model="offline-model")
    warnings, errors = [], []
    logger = SimpleNamespace(
        error=lambda template, *args: errors.append(template % args),
        warning=lambda template, *args: warnings.append(template % args),
    )
    monkeypatch.setattr(llm_dispatcher, "logger", logger)
    client, _ = _client(monkeypatch, [failure] * 5 + [_success()], clock=[0.0])
    await client.call(messages=[], max_retries=0)
    assert len(warnings) == 5
    assert ", parked" not in "".join(warnings[:3])
    assert "(attempt 4, parked 0s)" in warnings[3] and "(attempt 5, parked 16s)" in warnings[4]
    assert all(sensitive not in message for message in warnings)
    monkeypatch.setattr(llm_dispatcher, "_PARKED_ERRORS", ())
    client, _ = _client(monkeypatch, [failure] * 4)
    with pytest.raises(llm_dispatcher.LLMDispatchError) as captured:
        await client.call(messages=[], max_retries=0)
    assert sensitive not in str(captured.value) and "APIConnectionError" in str(captured.value)
    assert errors and all(sensitive not in message for message in errors)
    assert "APIConnectionError" in errors[-1]


def _environment_router(client):
    """为环境包装器提供真实调度器和最小本地上下文。"""
    router = SimpleNamespace(
        max_llm_call_retry=0, _coder_dispatcher=client, _summary_dispatcher=client,
        codegen_model_name="offline-model", summary_model_name="offline-model",
        _llm_completion_span=lambda *args: nullcontext(), get_system_prompt=lambda: "Return JSON.",
    )
    router.acompletion = partial(RouterBase.acompletion, router)
    return router


async def test_environment_router_does_not_forward_retired_retry_parameters(monkeypatch):
    """环境普通补全包装器通过真实 dispatcher 发出供应商可接受的请求。"""
    response = _success()
    client, _ = _client(monkeypatch, [response])
    router = _environment_router(client)
    assert await router.acompletion("coder", [], stream=False) is response
    assert set(client._router.requests[0]) == {"model", "messages", "stream", "timeout"}


async def test_environment_payload_validation_does_not_forward_retired_retry_parameters(monkeypatch):
    """带结构校验的环境补全包装器保持可执行，不把退避参数发送给供应商。"""
    response = _success()
    response.choices = [SimpleNamespace(message=SimpleNamespace(content='{"value": 7}'))]
    client, _ = _client(monkeypatch, [response])
    router = _environment_router(client)
    result = await RouterBase.acompletion_with_pydantic_validation(
        router, "coder", _Payload, [], max_retries=0,
    )
    assert result.value == 7
    assert set(client._router.requests[0]) == {"model", "messages", "stream", "timeout"}


async def test_real_router_makes_only_one_provider_attempt(monkeypatch):
    """真实 LiteLLM Router 不得在调度器之外重复请求供应商。"""
    router = llm_dispatcher._build_router("http://127.0.0.1:1/v1", "test-only", "test-model")
    failure = InternalServerError(message="busy", llm_provider="openai", model="test-model")
    models = []

    async def fail_provider_call(**request):
        """替换最后一层供应商调用，保留真实 Router 的重试判定与路由流程。"""
        models.append(request["model"])
        raise failure

    monkeypatch.setattr(router, "_acompletion", fail_provider_call)
    try:
        with pytest.raises(InternalServerError) as captured:
            await router.acompletion(model="test-model", messages=[{"role": "user", "content": "hello"}])
        assert captured.value is failure
        assert models == ["test-model"]
    finally:
        router.discard()


def test_real_router_has_no_fallback_models():
    """实际 Router 的普通、上下文窗口与内容政策备用模型集合都必须为空。"""
    router = llm_dispatcher._build_router("http://127.0.0.1:1/v1", "test-only", "test-model")
    try:
        assert not router.fallbacks
        assert not router.context_window_fallbacks
        assert not router.content_policy_fallbacks
    finally:
        router.discard()


@pytest.mark.parametrize("budget", [0, 1, 2, 5, 10])
async def test_configured_transport_budget_controls_actual_attempts(monkeypatch, budget):
    """不停放时，声明预算直接限定实际发送次数，首次请求不算重试。"""
    monkeypatch.setattr(llm_dispatcher, "_PARKED_ERRORS", ())
    client, delays = _client(monkeypatch, [_error(Timeout)] * (budget + 2))
    with pytest.raises(llm_dispatcher.LLMDispatchError, match=f"after {budget + 1} attempts"):
        await client.call(messages=[], max_retries=10, transport_max_retries=budget)
    assert len(client._router.requests) == budget + 1
    assert len(delays) == budget
    assert all("transport_max_retries" not in request for request in client._router.requests)


@pytest.mark.parametrize("kind", [AuthenticationError, BadRequestError])
async def test_authentication_and_parameters_fail_with_unspent_retry_budgets(monkeypatch, kind):
    """认证及参数错误立即失败，不消耗两个服务重试预算。"""
    client, delays = _client(monkeypatch, [_error(kind), _success()])
    with pytest.raises(llm_dispatcher.LLMDispatchError, match="after 1 attempts"):
        await client.call(messages=[], max_retries=10, transport_max_retries=10)
    assert len(client._router.requests) == 1
    assert delays == []


@pytest.mark.parametrize("budget", [True, -1, 11, 1.5, "3"])
async def test_invalid_retry_budgets_do_not_send_request(monkeypatch, budget):
    """不合法预算在请求前失败，不能转型或修补。"""
    client, delays = _client(monkeypatch, [_success()])
    with pytest.raises(ValueError, match="retry budgets"):
        await client.call(messages=[], transport_max_retries=budget)
    assert client._router.requests == []
    assert delays == []


def test_router_disables_underlying_retries_and_preserves_declared_credential(monkeypatch):
    """成熟 Router 仅承担原生发送与并发，重试预算只由一个循环负责。"""
    import litellm

    calls = []
    monkeypatch.setattr(litellm, "Router", lambda **kwargs: calls.append(kwargs) or object())
    llm_dispatcher._build_router("https://model.example/v1", "literal,credential", "configured-model")
    assert calls[0]["num_retries"] == 0
    assert calls[0]["cache_responses"] is False
    assert calls[0]["model_list"] == [{"model_name": "configured-model", "litellm_params": {
        "model": "openai/configured-model", "api_key": "literal,credential",
        "api_base": "https://model.example/v1", "max_retries": 0}}]


# ---------------------------------------------------------------------------
# 停放、整次期限与失败发送收集（长时运行成本第五章）
# ---------------------------------------------------------------------------


def _rate_limit(headers=None):
    """带给定响应头的 429；不给头时没有 Retry-After。"""
    response = httpx.Response(429, headers=headers or {}, request=httpx.Request("POST", "https://example.invalid"))
    return RateLimitError(message="busy", llm_provider="openai", model="offline-model", response=response)


def _rooted_failure():
    """一次以 ConnectionResetError 为显式原因的 InternalServerError（与运行里断网的根因同形）。"""
    try:
        try:
            raise ConnectionResetError("reset by peer")
        except ConnectionResetError as cause:
            raise _error(InternalServerError) from cause
    except InternalServerError as error:
        return error


def _recording_logger(monkeypatch):
    """把调度器日志换成记录器，返回 (级别, 文本) 列表。"""
    records = []

    def writer(level):
        return lambda template, *args: records.append((level, template % args))

    monkeypatch.setattr(llm_dispatcher, "logger", SimpleNamespace(
        warning=writer("warning"), error=writer("error"), exception=writer("exception")))
    return records


async def test_spent_transport_count_parks_and_resends_the_identical_request(monkeypatch):
    """十次连接失败后成功、传输计数 3：计数内 2、4、8 秒，之后停放按 2·2ⁿ 封顶 120 秒；十一次发送的请求逐字相同，
    计数与失败发送都不上线路。"""
    response = _success()
    client, delays = _client(monkeypatch, [_error(APIConnectionError)] * 10 + [response], clock=[0.0])
    result = await client.call(messages=[{"role": "user", "content": "hello"}], max_retries=0,
                               transport_max_retries=3, max_tokens=16000)
    assert result is response
    assert delays == [2, 4, 8, 16, 32, 64, 120, 120, 120, 120]
    requests = client._router.requests
    assert len(requests) == 11 and all(request == requests[0] for request in requests)
    assert all("transport_max_retries" not in request and "transport_attempts" not in request for request in requests)


async def test_park_gives_up_after_twelve_hours_with_the_root_cause(monkeypatch):
    """一直断网：停放十二小时后第 366 次发送失败，耗时 43206 秒，错误写明最后的类型与根因链。"""
    failure = _rooted_failure()
    client, delays = _client(monkeypatch, [failure] * 400, clock=[0.0])
    with pytest.raises(llm_dispatcher.LLMDispatchError) as captured:
        await client.call(messages=[], max_retries=0, transport_max_retries=3)
    assert captured.value.args[0] == (
        "Failed to get valid response after 366 attempts in 43206.0 s. Last error type: InternalServerError. "
        "Error chain: InternalServerError <- ConnectionResetError")
    assert captured.value.__cause__ is failure
    assert len(client._router.requests) == 366
    assert sum(delays) == 43206


async def test_rate_limit_parks_after_the_ordinary_count_and_honours_retry_after(monkeypatch):
    """普通计数为零的 429 立即停放：Retry-After 30 秒等 30 秒；HTTP 日期 45 秒后等约 45 秒；不带头时 1、2、4… 封顶 120。"""
    client, delays = _client(monkeypatch, [_rate_limit({"retry-after": "30"})] * 3 + [_success()], clock=[0.0])
    await client.call(messages=[], max_retries=0)
    assert delays == [30, 30, 30]
    moment = email.utils.format_datetime(datetime.now(timezone.utc) + timedelta(seconds=45), usegmt=True)
    client, delays = _client(monkeypatch, [_rate_limit({"retry-after": moment})] * 3 + [_success()], clock=[0.0])
    await client.call(messages=[], max_retries=0)
    assert len(delays) == 3 and all(44 <= delay <= 45 for delay in delays)
    client, delays = _client(monkeypatch, [_rate_limit()] * 9 + [_success()], clock=[0.0])
    await client.call(messages=[], max_retries=0)
    assert delays == [1, 2, 4, 8, 16, 32, 64, 120, 120]


@pytest.mark.parametrize("max_retries", [0, 2], ids=["parked", "counted"])
async def test_retry_after_beyond_the_budget_fails_at_once(monkeypatch, max_retries):
    """Retry-After 五万秒长于十二小时：停放中与计数内都在第一次发送后失败，不等待。"""
    client, delays = _client(monkeypatch, [_rate_limit({"retry-after": "50000"}), _success()], clock=[0.0])
    with pytest.raises(llm_dispatcher.LLMDispatchError, match="after 1 attempts"):
        await client.call(messages=[], max_retries=max_retries)
    assert delays == [] and len(client._router.requests) == 1


async def test_unparsable_retry_after_is_ignored_with_a_warning(monkeypatch):
    """Retry-After 无法解析：按退避等待，写一次警告。"""
    records = _recording_logger(monkeypatch)
    client, delays = _client(monkeypatch, [_rate_limit({"retry-after": "soon"}), _success()], clock=[0.0])
    await client.call(messages=[], max_retries=0)
    assert delays == [1]
    assert [text for level, text in records if level == "warning"].count(
        "Ignoring unparsable Retry-After header: 'soon'") == 1


async def test_counted_rate_limit_waits_honour_a_longer_retry_after(monkeypatch):
    """计数内的 429 也不短于 Retry-After：两次各等 5 秒后成功。"""
    response = _success()
    client, delays = _client(monkeypatch, [_rate_limit({"retry-after": "5"})] * 2 + [response], clock=[0.0])
    assert await client.call(messages=[], max_retries=2) is response
    assert delays == [5, 5]


@pytest.mark.parametrize("failure", [_error(AuthenticationError), _error(BadRequestError),
                                     _error(BadGatewayError), ValueError("bad payload")],
                         ids=["authentication", "bad-request", "bad-gateway", "ordinary"])
async def test_fatal_and_ordinary_failures_never_park(monkeypatch, failure):
    """鉴权、参数与普通失败不停放：普通计数为零时第一次就失败；像限流的文本只在计数内等待，用尽即失败。"""
    client, delays = _client(monkeypatch, [failure] * 3, clock=[0.0])
    with pytest.raises(llm_dispatcher.LLMDispatchError, match="after 1 attempts"):
        await client.call(messages=[], max_retries=0)
    assert delays == []
    client, delays = _client(monkeypatch, [ValueError("try again in 3s")] * 3, clock=[0.0])
    with pytest.raises(llm_dispatcher.LLMDispatchError, match="after 2 attempts"):
        await client.call(messages=[], max_retries=1)
    assert delays == [1]


class _StallingRouter(_Router):
    """第一次发送永远等不到回答，之后按脚本回答。"""

    async def acompletion(self, **request):
        """第一次挂起在不会被设置的事件上。"""
        if not self.requests:
            self.requests.append(request)
            await asyncio.Event().wait()
        return await super().acompletion(**request)


async def test_deadline_expiry_resends_the_same_request_as_a_transport_retry(monkeypatch):
    """服务卡住超过整次期限：这次发送按传输超时放弃，2 秒后重发同一请求并拿到回答；失败发送记下超时链。"""
    monkeypatch.setattr(llm_dispatcher, "_DEADLINE_BASE_SECONDS", 0.05)
    monkeypatch.setattr(llm_dispatcher, "_DEADLINE_SECONDS_PER_TOKEN", 0)
    response = _success()
    client, delays = _client(monkeypatch, [])
    client._router = _StallingRouter([response])
    with llm_dispatcher.collect_transport_attempts() as attempts:
        result = await client.call(messages=[{"role": "user", "content": "hello"}], max_tokens=16000)
    assert result is response
    assert len(client._router.requests) == 2 and client._router.requests[0] == client._router.requests[1]
    assert len(attempts) == 1 and attempts[0]["error"].startswith("Timeout <- TimeoutError")
    assert attempts[0]["wait_ms"] == 2000 and delays == [2.0]


def test_deadline_scales_with_max_tokens():
    """整次期限是 120 秒加每个输出 token 7.5 毫秒；没有正整数 max_tokens 时为 120 秒。"""
    assert llm_dispatcher._attempt_deadline({"max_tokens": 16000}) == 240.0
    assert llm_dispatcher._attempt_deadline({"max_tokens": 8000}) == 180.0
    assert llm_dispatcher._attempt_deadline({}) == 120.0
    assert llm_dispatcher._attempt_deadline({"max_tokens": True}) == 120.0


async def test_foreign_timeout_error_is_not_relabelled(monkeypatch):
    """期限未到时别处抛出的 TimeoutError 不改成传输超时，按普通失败结束。"""
    client, delays = _client(monkeypatch, [TimeoutError(), _success()])
    with pytest.raises(llm_dispatcher.LLMDispatchError, match="after 1 attempts") as captured:
        await client.call(messages=[], max_retries=0)
    assert "Last error type: TimeoutError" in str(captured.value) and delays == []


async def test_collector_records_each_failed_send_outside_the_request(monkeypatch):
    """收集器按顺序记下每次失败发送的序号、错误链、距第一次发送的毫秒数与等待毫秒数；线路上没有这个键。"""
    response = _success()
    client, _ = _client(monkeypatch, [_error(APIConnectionError), _error(Timeout), response], clock=[0.0])
    with llm_dispatcher.collect_transport_attempts() as attempts:
        assert await client.call(messages=[], max_retries=0) is response
    assert attempts == [
        {"attempt": 1, "error": "APIConnectionError", "failed_after_ms": 0, "wait_ms": 2000},
        {"attempt": 2, "error": "Timeout", "failed_after_ms": 2000, "wait_ms": 4000}]
    assert all("transport_attempts" not in request for request in client._router.requests)


async def test_failed_call_records_its_final_send_with_a_null_wait(monkeypatch):
    """结束调用的那次失败也记下，等待为空。"""
    client, _ = _client(monkeypatch, [_error(AuthenticationError)], clock=[0.0])
    with llm_dispatcher.collect_transport_attempts() as attempts:
        with pytest.raises(llm_dispatcher.LLMDispatchError):
            await client.call(messages=[], max_retries=0)
    assert attempts == [{"attempt": 1, "error": "AuthenticationError", "failed_after_ms": 0, "wait_ms": None}]


class _PerMessageRouter:
    """按消息正文各自计数：前 failures[正文] 次连接失败，之后回答；每次都先让出事件循环。"""

    def __init__(self, failures, real_sleep):
        self.failures, self.real_sleep, self.sent = dict(failures), real_sleep, {}

    async def acompletion(self, **request):
        """先让出循环使两个任务交错，再按正文决定失败或回答。"""
        await self.real_sleep(0)
        text = request["messages"][0]["content"]
        self.sent[text] = self.sent.get(text, 0) + 1
        if self.sent[text] <= self.failures[text]:
            raise _error(APIConnectionError)
        return _success()


async def test_collectors_are_isolated_between_concurrent_tasks(monkeypatch):
    """同一 LLMClient 上两个并发任务各有收集器（分别失败 1 次与 2 次）：各自只记下自己的失败发送。"""
    real_sleep = asyncio.sleep
    client, _ = _client(monkeypatch, [])
    client._router = _PerMessageRouter({"a": 1, "b": 2}, real_sleep)

    async def yielding(delay):
        await real_sleep(0)

    monkeypatch.setattr(llm_dispatcher.asyncio, "sleep", yielding)

    async def one(text):
        with llm_dispatcher.collect_transport_attempts() as attempts:
            await client.call(messages=[{"role": "user", "content": text}], max_retries=0)
        return attempts

    first, second = await asyncio.gather(one("a"), one("b"))
    assert [item["attempt"] for item in first] == [1]
    assert [item["attempt"] for item in second] == [1, 2]
    assert client._router.sent == {"a": 2, "b": 3}


async def test_without_a_collector_nothing_is_recorded(monkeypatch):
    """没有收集器时失败后照常重发成功，不留下任何收集器。"""
    response = _success()
    client, _ = _client(monkeypatch, [_error(APIConnectionError), response])
    assert await client.call(messages=[]) is response
    assert llm_dispatcher._TRANSPORT_ATTEMPTS.get() is None


def _raised(build):
    """执行一段会抛出异常的代码并返回那个异常。"""
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

    assert llm_dispatcher.error_chain(_raised(explicit)) == "RuntimeError <- ValueError"
    assert llm_dispatcher.error_chain(_raised(implicit)) == "OSError <- KeyError"
    assert llm_dispatcher.error_chain(_raised(suppressed)) == "OSError"
    cycle = ValueError("self")
    cycle.__cause__ = cycle
    assert llm_dispatcher.error_chain(cycle) == "ValueError"
    deep = ValueError("0")
    for index in range(1, 20):
        outer = KeyError(str(index))
        outer.__cause__ = deep
        deep = outer
    assert llm_dispatcher.error_chain(deep).split(" <- ") == ["KeyError"] * 16


async def _closing_real_client(client):
    """真实客户端用完后在本循环关闭 LiteLLM 缓存的客户端并释放 Router。"""
    await llm_dispatcher.close_cached_async_clients()
    client.close()


def _fake_time(monkeypatch):
    """调度器的单调时钟与等待换成假时钟，返回时钟与等待记录；零秒等待（网络库让出循环）照常让出，不记录。"""
    clock, delays, real_sleep = [0.0], [], asyncio.sleep
    monkeypatch.setattr(llm_dispatcher, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    async def sleep(delay, *arguments):
        if not delay:
            return await real_sleep(0, *arguments)
        delays.append(delay)
        clock[0] += delay

    monkeypatch.setattr(llm_dispatcher.asyncio, "sleep", sleep)
    return clock, delays


async def _always_429_server():
    """本地服务：每个补全请求都回 HTTP 429 且不带 Retry-After；返回 (runner, 端口)。"""
    from aiohttp import web

    async def busy(request):
        return web.json_response({"error": {"message": "busy", "type": "rate_limit"}}, status=429)

    app = web.Application()
    app.router.add_post("/v1/chat/completions", busy)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    return runner, site._server.sockets[0].getsockname()[1]


async def test_real_router_keeps_accepting_parked_resends(monkeypatch):
    """真实 Router 在停放速率下不冷却：连到关闭的端口每次都是传输错误，十次发送后用完 600 秒停放预算；本地服务一律回
    429 时每次都是 RateLimitError（不是路由层的限流），十一次发送后用完预算。"""
    monkeypatch.setattr(llm_dispatcher, "_PARK_BUDGET_SECONDS", 600)
    clock, delays = _fake_time(monkeypatch)
    closed = llm_dispatcher.LLMClient("test-model", "http://127.0.0.1:9/v1", "test-only")
    try:
        with llm_dispatcher.collect_transport_attempts() as attempts:
            with pytest.raises(llm_dispatcher.LLMDispatchError, match="after 10 attempts"):
                await closed.call(messages=[{"role": "user", "content": "hello"}], max_retries=0,
                                  transport_max_retries=0)
    finally:
        await _closing_real_client(closed)
    assert delays == [2, 4, 8, 16, 32, 64, 120, 120, 120]
    assert all(item["error"].split(" <- ")[0] in {"APIConnectionError", "InternalServerError", "Timeout"}
               and "RouterRateLimitError" not in item["error"] for item in attempts)
    runner, port = await _always_429_server()
    clock[0], delays[:] = 0.0, []
    limited = llm_dispatcher.LLMClient("test-model", f"http://127.0.0.1:{port}/v1", "test-only")
    try:
        with llm_dispatcher.collect_transport_attempts() as attempts:
            with pytest.raises(llm_dispatcher.LLMDispatchError, match="after 11 attempts"):
                await limited.call(messages=[{"role": "user", "content": "hello"}], max_retries=0)
    finally:
        await _closing_real_client(limited)
        await runner.cleanup()
    assert delays == [1, 2, 4, 8, 16, 32, 64, 120, 120, 120]
    assert [item["error"].split(" <- ")[0] for item in attempts] == ["RateLimitError"] * 11
