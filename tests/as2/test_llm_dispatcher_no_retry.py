"""正式模型调用的零普通重试测试；传输异常另有固定预算。"""

import pytest

from agentsociety2.config.llm_dispatcher import LLMClient, LLMDispatchError


class _FailingRouter:
    """记录底层模型请求次数并返回确定性参数错误。"""

    def __init__(self) -> None:
        self.calls = 0

    async def acompletion(self, **request):
        """抛出不会因重试而变化的请求错误。"""
        del request
        self.calls += 1
        raise ValueError("unsupported parameter")


class _Semaphore:
    """提供调度器测试所需的最小并发接口。"""

    async def acquire(self) -> None:
        """允许立即进入。"""

    def record_latency(self, latency_ms: float, *, is_error: bool) -> None:
        """接受时延记录。"""
        del latency_ms, is_error

    async def release(self, *, overloaded: bool) -> None:
        """接受释放记录。"""
        del overloaded


@pytest.mark.asyncio
async def test_zero_retries_makes_exactly_one_external_attempt() -> None:
    """显式零普通重试时，确定性参数错误只产生一个外部请求。"""
    client = LLMClient("model", "https://example.invalid", "secret")
    router = _FailingRouter()
    client._router = router
    client._sem = _Semaphore()
    client._ensure_runtime = lambda: None

    with pytest.raises(LLMDispatchError, match="after 1 attempts"):
        await client.call(messages=[], max_retries=0)

    assert router.calls == 1
