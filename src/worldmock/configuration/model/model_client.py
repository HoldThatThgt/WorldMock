"""@brief WorldMock 唯一的模型客户端：经 litellm.acompletion 发送生成请求，承担两类重试预算、停放、单次发送整次期限、
失败发送记录与错误链（docs/design/08-configuration.md「模型配置与凭据」）。

本模块只依赖标准库与 litellm、openai、httpx，可以在别的环境里按文件路径加载；它是 src 里唯一导入 litellm 的模块，
导入之前设置 LITELLM_LOCAL_MODEL_COST_MAP。最终失败的类名 LLMDispatchError 与发送循环的骨架沿用上游的模型调度器。

来源：本模块部分代码改写自 AgentSociety2 2.8.7（上游提交 670c94ff），按 Apache-2.0 使用，许可全文见 LICENSES/AgentSociety2-Apache-2.0.txt。
"""

from __future__ import annotations

import asyncio
import email.utils
import logging
import os
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"

import litellm
from litellm import token_counter
from litellm.exceptions import (
    APIConnectionError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout,
    UnprocessableEntityError,
)
from litellm.types.utils import ModelResponse
from openai import AsyncOpenAI

__all__ = ["LLMDispatchError", "ModelClient", "REQUEST_KEYS", "collect_transport_attempts", "error_chain",
           "token_counter"]

#: 交给模型客户端的请求恰好这六个键；这个次序是错误文字与三条路径底稿的构造次序，call 只比集合。
REQUEST_KEYS = ("model", "messages", "tools", "tool_choice", "max_tokens", "extra_body")
_REQUEST_KEY_SET = frozenset(REQUEST_KEYS)
_REQUEST_KEYS_ERROR = "Model request keys must be exactly model, messages, tools, tool_choice, max_tokens, extra_body"
_RETRY_BUDGET_ERROR = "Model retry budgets must be integers in [0, 10]"
#: 每次发送交给 litellm 与网络客户端的请求超时（秒）；引擎常量，不在配置里。
_REQUEST_TIMEOUT_SECONDS = 300.0
#: 只接受 LiteLLM 明确的连接、超时及服务暂时不可用异常，不按错误文本猜测。
_TRANSPORT_ERRORS = (APIConnectionError, Timeout, InternalServerError, ServiceUnavailableError)
#: 凭据、权限和请求参数错误不会因服务重试预算而重复发送。
_FATAL_ERRORS = (AuthenticationError, BadRequestError, NotFoundError, PermissionDeniedError,
                 UnprocessableEntityError)
#: 计数用尽后停放而不失败的类别：传输错误与限流 429（按类型判定，不按文本猜测）。
_PARKED_ERRORS = (*_TRANSPORT_ERRORS, RateLimitError)
#: 停放的总时长（秒），从停放开始计；引擎常量，不是配置字段或命令行参数。
_PARK_BUDGET_SECONDS = 12 * 60 * 60
#: 停放期间两次发送之间的等待上限（秒）。
_PARK_MAX_WAIT_SECONDS = 120.0
#: 单次发送的整次期限：基础秒数加请求 max_tokens 每个的秒数（16000 时 240 秒）。
_DEADLINE_BASE_SECONDS = 120.0
_DEADLINE_SECONDS_PER_TOKEN = 0.0075
#: 当前 asyncio 任务里登记失败发送的收集器；只由 collect_transport_attempts 设置，不是客户端上的共享状态。
_TRANSPORT_ATTEMPTS: ContextVar[list[dict[str, Any]] | None] = ContextVar("transport_attempts", default=None)

_LOGGER = logging.getLogger(__name__)


class LLMDispatchError(RuntimeError):
    """@brief 重试或停放之后仍失败的模型请求；文字含发送次数、耗时、最后的错误类型与根因异常链。"""


def error_chain(error: BaseException) -> str:
    """@brief 异常及其原因链的类型名，自外向内以「 <- 」连接：显式原因优先，未抑制时取隐式上下文，至多 16 层；
    只含类型名，不含可能带凭据或请求片段的异常正文。"""
    names: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen and len(names) < 16:
        seen.add(id(current))
        names.append(type(current).__name__)
        if current.__cause__ is not None:
            current = current.__cause__
        else:
            current = None if current.__suppress_context__ else current.__context__
    return " <- ".join(names)


@contextmanager
def collect_transport_attempts() -> Iterator[list[dict[str, Any]]]:
    """@brief 在当前任务上下文收集其间模型调用的失败发送，供证据信封写入 transport_attempts；退出时恢复外层收集器。

    收集器属于调用方所在的 asyncio 任务；并发的任务各有各的，调用方每次只包住一次模型调用。
    """
    attempts: list[dict[str, Any]] = []
    token = _TRANSPORT_ATTEMPTS.set(attempts)
    try:
        yield attempts
    finally:
        _TRANSPORT_ATTEMPTS.reset(token)


def _attempt_record(attempt: int, chain: str, elapsed: float, wait: float | None) -> dict[str, Any]:
    """@brief 一次失败发送的证据：发送序号、错误链、距第一次发送的毫秒数、到下一次发送的等待毫秒数（结束调用时为空）。"""
    return {"attempt": attempt, "error": chain, "failed_after_ms": int(elapsed * 1000),
            "wait_ms": None if wait is None else int(wait * 1000)}


def _retry_after_seconds(error: Exception) -> float | None:
    """@brief 429 响应头 Retry-After 给出的等待秒数（整数秒或 HTTP 日期，不小于 0）；没有或无法解析时为空。

    供应商响应头只在 LiteLLM 映射异常时设置的 litellm_response_headers 上（litellm_core_utils/exception_mapping_utils.py）；
    error.response 是 LiteLLM 另造的不带头的替身。耦合点随锁定的 LiteLLM 版本核对。
    """
    headers = getattr(error, "litellm_response_headers", None)
    value = headers.get("retry-after") if headers is not None else None
    if value is None:
        return None
    text = value.strip()
    if text.isdigit():
        return float(text)
    try:
        moment = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError):
        _LOGGER.warning("Ignoring unparsable Retry-After header: %r", value)
        return None
    moment = moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    return max(0.0, (moment - datetime.now(timezone.utc)).total_seconds())


def _backoff(error: Exception, index: int, counted: bool) -> float:
    """@brief 同类第 index 次失败后的等待秒数：传输 2·2^n，限流 429（只按 RateLimitError 类型）2^n（计数内封顶 60），
    其余 0；停放时一律封顶 120；429 不短于 Retry-After。计数内的值与原规则相同。"""
    exponent = min(index, 16)
    if isinstance(error, _TRANSPORT_ERRORS):
        wait = 2.0 * 2.0 ** exponent
    elif isinstance(error, RateLimitError):
        wait = min(2.0 ** exponent, 60.0) if counted else 2.0 ** exponent
    else:
        wait = 0.0
    if not counted:
        wait = min(wait, _PARK_MAX_WAIT_SECONDS)
    retry_after = _retry_after_seconds(error) if isinstance(error, RateLimitError) else None
    return wait if retry_after is None else max(wait, retry_after)


def _attempt_deadline(request: Mapping[str, Any]) -> float:
    """@brief 单次发送的整次期限：120 秒加 max_tokens 每个 7.5 毫秒；没有正整数 max_tokens 时为 120 秒。"""
    tokens = request.get("max_tokens")
    tokens = tokens if type(tokens) is int and tokens > 0 else 0
    return _DEADLINE_BASE_SECONDS + _DEADLINE_SECONDS_PER_TOKEN * tokens


def _dispatch_error(error: Exception, attempts: int, elapsed: float) -> LLMDispatchError:
    """@brief 记录并构造最终失败：发送次数、耗时、最后错误类型与根因异常链，不含供应商错误正文。"""
    chain = error_chain(error)
    _LOGGER.error("Model request failed after %d attempts in %.1f s: %s", attempts, elapsed, chain)
    return LLMDispatchError(
        f"Failed to get valid response after {attempts} attempts in {elapsed:.1f} s. "
        f"Last error type: {type(error).__name__}. Error chain: {chain}")


@dataclass
class _Retries:
    """@brief 一次 call 的两类重试计数与停放起点；只在这次调用内存在，不跨调用共享。"""

    max_retries: int
    transport_max_retries: int
    ordinary: int = 0
    transport: int = 0
    parked_since: float | None = None

    def next_wait(self, error: Exception, now: float) -> float | None:
        """@brief 下一次发送前的等待秒数；None 表示这次调用失败。计数用尽后传输与限流停放，十二小时为限；
        任何一次等待长于十二小时也立即失败。"""
        if isinstance(error, _FATAL_ERRORS):
            return None
        transport = isinstance(error, _TRANSPORT_ERRORS)
        index = self.transport if transport else self.ordinary
        counted = index < (self.transport_max_retries if transport else self.max_retries)
        if transport:
            self.transport += 1
        else:
            self.ordinary += 1
        if not counted and not isinstance(error, _PARKED_ERRORS):
            return None
        wait = _backoff(error, index, counted)
        if counted:
            return None if wait > _PARK_BUDGET_SECONDS else wait
        if self.parked_since is None:
            self.parked_since = now
        return None if now + wait - self.parked_since > _PARK_BUDGET_SECONDS else wait


class ModelClient:
    """@brief 整次运行共用的模型客户端：持有一个 OpenAI SDK 异步网络客户端，每次发送都显式关闭底层重试并给出
    请求超时；两类重试次数在构造时校验一次，请求只接受六个键；不累计用量，不保留跨调用的状态。"""

    def __init__(self, *, base_url: str, api_key: str, max_retries: int, transport_max_retries: int) -> None:
        """@brief 校验两类重试次数（整数且在 0 到 10 之间），再建且只建一个网络客户端；凭据原样交给它。"""
        if (type(max_retries) is not int or type(transport_max_retries) is not int
                or not 0 <= max_retries <= 10 or not 0 <= transport_max_retries <= 10):
            _LOGGER.error("%s", _RETRY_BUDGET_ERROR)
            raise ValueError(_RETRY_BUDGET_ERROR)
        self.base_url = base_url
        self.max_retries = max_retries
        self.transport_max_retries = transport_max_retries
        self._api_key = api_key
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url, max_retries=0,
                                   timeout=_REQUEST_TIMEOUT_SECONDS)

    async def call(self, request: Mapping[str, Any]) -> ModelResponse:
        """@brief 发送六键请求并原样返回响应；普通与传输重试分别计数，计数内退避不变；计数用尽后传输失败与限流 429
        原样重发同一请求停放至多十二小时；每次失败发送登记到当前收集器；失败报告发送次数、耗时、最后错误类型与
        根因异常链。请求不被修改或复制。"""
        if set(request) != _REQUEST_KEY_SET:
            _LOGGER.error("%s", _REQUEST_KEYS_ERROR)
            raise ValueError(_REQUEST_KEYS_ERROR)
        retries, collector = _Retries(self.max_retries, self.transport_max_retries), _TRANSPORT_ATTEMPTS.get()
        first_send, attempts = time.monotonic(), 0
        while True:
            attempts += 1
            try:
                return await self._send_within(request)
            except Exception as error:
                now = time.monotonic()
                wait, chain = retries.next_wait(error, now), error_chain(error)
                if collector is not None:
                    collector.append(_attempt_record(attempts, chain, now - first_send, wait))
                if wait is None:
                    raise _dispatch_error(error, attempts, now - first_send) from error
                parked = "" if retries.parked_since is None else f", parked {now - retries.parked_since:.0f}s"
                _LOGGER.warning("Request failed for '%s' (attempt %d%s). Backoff %.1fs. Error: %s",
                                request["model"], attempts, parked, wait, chain)
                if wait:
                    await asyncio.sleep(wait)

    async def _send_within(self, request: Mapping[str, Any]) -> ModelResponse:
        """@brief 整次期限包住一次底层发送；期限触发时改抛 LiteLLM 传输超时（以原 TimeoutError 为原因），别处的
        TimeoutError 原样传播；日志由调用层统一记录。"""
        deadline = _attempt_deadline(request)
        scope = asyncio.timeout(deadline)
        try:
            async with scope:
                return await litellm.acompletion(
                    model=f"openai/{request['model']}", messages=request["messages"], tools=request["tools"],
                    tool_choice=request["tool_choice"], max_tokens=request["max_tokens"],
                    extra_body=request["extra_body"], api_base=self.base_url, api_key=self._api_key,
                    client=self._client, max_retries=0, timeout=_REQUEST_TIMEOUT_SECONDS, stream=False)
        except TimeoutError as error:
            if not scope.expired():
                raise
            raise Timeout(message=f"Model call exceeded its {deadline:.1f} s deadline", model=request["model"],
                          llm_provider="openai") from error

    async def close(self) -> None:
        """@brief 关闭持有的网络客户端一次；不另外捕获异常。"""
        await self._client.close()
