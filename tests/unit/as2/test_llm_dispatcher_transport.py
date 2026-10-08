"""传输错误重试与普通重试预算相互独立，全部使用本地脚本化 Router。"""

from contextlib import nullcontext
from functools import partial
from types import SimpleNamespace

import pytest
from litellm.exceptions import (
    APIConnectionError,
    AuthenticationError,
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


def _client(monkeypatch, script):
    """构造真实 LLMClient，仅替换 Router、并发许可及等待。"""
    client = llm_dispatcher.LLMClient("offline-model", "https://example.invalid", "test-key")
    client._router = _Router(script)
    client._sem = _Semaphore()
    monkeypatch.setattr(client, "_ensure_runtime", lambda: None)
    delays = []

    async def sleep(delay):
        """记录退避秒数，并验证许可已经释放。"""
        assert client._sem.acquired == 0
        delays.append(delay)

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
async def test_persistent_transport_failure_stops_after_four_attempts(monkeypatch, kind):
    """持续暂时错误耗尽三次传输重试，准确报告四次外部请求。"""
    failure = _error(kind)
    client, delays = _client(monkeypatch, [failure] * 5)
    with pytest.raises(llm_dispatcher.LLMDispatchError, match="after 4 attempts") as captured:
        await client.call(messages=[], max_retries=0)
    assert llm_dispatcher.TRANSPORT_MAX_RETRIES == 3
    assert captured.value.__cause__ is failure
    assert not captured.value.rate_limit_like
    assert len(client._router.requests) == len(client._sem.releases) == 4
    assert delays == [2.0, 4.0, 8.0]
    assert client.snapshot_token_stats() == {}


@pytest.mark.parametrize("kind", [AuthenticationError, BadRequestError, RateLimitError])
@pytest.mark.parametrize("after_transport", [False, True])
async def test_non_transport_provider_errors_do_not_use_transport_budget(monkeypatch, kind, after_transport):
    """认证、无效请求与限流不能使用剩余的传输重试预算。"""
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
    """传输预算独立耗尽后终止，即使普通预算尚未使用。"""
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
    """供应商错误可能带密钥或请求片段，调度器日志和摘要只记录错误类型。"""
    sensitive = "fake-provider-key-in-error"
    failure = APIConnectionError(message=sensitive, llm_provider="openai", model="offline-model")
    client, _ = _client(monkeypatch, [failure] * 4)
    messages = []
    logger = SimpleNamespace(
        error=lambda template, *args: messages.append(template % args),
        warning=lambda template, *args: messages.append(template % args),
    )
    monkeypatch.setattr(llm_dispatcher, "logger", logger)
    with pytest.raises(llm_dispatcher.LLMDispatchError) as captured:
        await client.call(messages=[], max_retries=0)
    assert sensitive not in str(captured.value)
    assert all(sensitive not in message for message in messages)
    assert "APIConnectionError" in str(captured.value)


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
