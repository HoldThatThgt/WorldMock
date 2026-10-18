"""LiteLLM Router 生命周期回归测试。"""

from __future__ import annotations

import asyncio
import gc
import json
import logging
import threading
import weakref
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import litellm
import pytest

from agentsociety2.agent import runner
from agentsociety2.agent.service_proxy import LLMClients, ServiceProxy
from agentsociety2.config import llm_dispatcher
from agentsociety2.config.llm_dispatcher import LLMClient

_CALLBACK_LISTS = (
    "success_callback",
    "_async_success_callback",
    "failure_callback",
    "_async_failure_callback",
)


class _TaskProxy:
    """记录 worker 收尾顺序。"""

    def __init__(self) -> None:
        self.events: list[str] = []

    def take_token_stats(self) -> dict[str, dict[str, int]]:
        """记录统计提取并返回固定增量。"""
        self.events.append("stats")
        return {"model": {"calls": 1, "input": 2, "output": 3}}

    def close_llm_clients(self) -> None:
        """记录运行时清理。"""
        self.events.append("close")


class _StatsFailureProxy(_TaskProxy):
    """模拟统计提取失败。"""

    def take_token_stats(self) -> dict[str, dict[str, int]]:
        """记录统计阶段后抛出错误。"""
        self.events.append("stats")
        raise RuntimeError("stats failed")


class _BrokenRouter:
    """模拟 LiteLLM 清理失败。"""

    def discard(self) -> None:
        """抛出确定性清理错误。"""
        raise RuntimeError("discard failed")


class _RoleClient:
    """记录单个角色客户端的关闭次数。"""

    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        """记录一次关闭。"""
        self.close_calls += 1


def _task_function(task: Any) -> Any:
    """返回 Ray 包装器中的原始同步函数。"""
    return getattr(task, "_function", task)


def _router_callback_count(router: Any) -> int:
    """统计指定 Router 在四个全局列表中的绑定方法。"""
    return sum(
        getattr(callback, "__self__", None) is router
        for name in _CALLBACK_LISTS
        for callback in getattr(litellm, name)
    )


async def _ensure_router(client: LLMClient) -> weakref.ReferenceType[Any]:
    """在任务事件循环内构造一个真实 Router。"""
    client._ensure_runtime()
    return weakref.ref(client._router)


@pytest.mark.parametrize(
    ("task_name", "core_name", "args", "has_stats"),
    [
        ("step_agent_batch", "_step_agent_batch_async", (object(),), True),
        (
            "query_agent_task",
            "_query_agent_task_async",
            (1, "/tmp", "Agent", "ask", {}),
            False,
        ),
        (
            "questionnaire_agent_batch",
            "_questionnaire_agent_batch_async",
            ([1], "/tmp", "Agent", object(), object(), 1),
            True,
        ),
    ],
)
def test_worker_tasks_extract_stats_before_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    task_name: str,
    core_name: str,
    args: tuple[Any, ...],
    has_stats: bool,
) -> None:
    """三个 LLM worker 正常返回时先提取统计再关闭。"""

    async def _success(*unused: Any) -> list[dict[str, bool]]:
        del unused
        return [{"ok": True}]

    proxy = _TaskProxy()
    monkeypatch.setattr(runner, core_name, _success)
    result = _task_function(getattr(runner, task_name))(*args, proxy)
    assert proxy.events == ["stats", "close"]
    if has_stats:
        assert result["token_stats"]["model"]["calls"] == 1
    else:
        assert result == [{"ok": True}]


@pytest.mark.parametrize("error", [RuntimeError("failed"), asyncio.CancelledError()])
def test_worker_task_cleanup_covers_failure_and_cancellation(
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
) -> None:
    """失败和取消均执行相同的统计与关闭顺序。"""

    async def _fail(*unused: Any) -> None:
        del unused
        raise error

    proxy = _TaskProxy()
    monkeypatch.setattr(runner, "_step_agent_batch_async", _fail)
    with pytest.raises(type(error), match=str(error) or None):
        _task_function(runner.step_agent_batch)(object(), proxy)
    assert proxy.events == ["stats", "close"]


def test_stats_failure_still_closes_llm_clients() -> None:
    """统计提取异常仍执行 finally 中的 LLM 清理。"""
    proxy = _StatsFailureProxy()
    with pytest.raises(RuntimeError, match="stats failed"):
        runner._finish_llm_task(proxy)
    assert proxy.events == ["stats", "close"]


def test_more_than_one_hundred_task_loops_leave_no_router_callbacks(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """超过默认上限的任务循环不累计 Router 或产生上限警告。"""
    warmup = LLMClient("offline", "http://127.0.0.1:9", "redacted")
    asyncio.run(_ensure_router(warmup))
    warmup.close()
    baseline = {name: tuple(getattr(litellm, name)) for name in _CALLBACK_LISTS}
    caplog.set_level(logging.WARNING)
    references = []
    for _ in range(105):
        client = LLMClient("offline", "http://127.0.0.1:9", "redacted")
        references.append(asyncio.run(_ensure_router(client)))
        client.close()
    del client
    gc.collect()
    assert all(reference() is None for reference in references)
    assert {name: tuple(getattr(litellm, name)) for name in _CALLBACK_LISTS} == baseline
    assert "Cannot add callback" not in caplog.text


def test_worker_helper_closes_real_router_after_event_loop() -> None:
    """真实 ServiceProxy 在事件循环结束后保留统计并释放 Router。"""
    client = LLMClient("offline", "http://127.0.0.1:9", "redacted")
    clients = LLMClients(
        coder=LLMClient("coder", "http://127.0.0.1:9", "redacted"),
        default=client,
    )
    proxy = ServiceProxy(env=object(), llm=clients, trace=None, replay=None)
    references: list[weakref.ReferenceType[Any]] = []

    async def _use_router() -> str:
        client._ensure_runtime()
        references.append(weakref.ref(client._router))
        client._token_stats = {"offline": {"calls": 1, "input": 2, "output": 3}}
        return "done"

    result, stats = runner._run_worker_coroutine(_use_router(), proxy)
    gc.collect()
    assert result == "done"
    assert stats == {"offline": {"calls": 1, "input": 2, "output": 3}}
    assert references[0]() is None
    assert clients.default._router is None
    assert clients.coder._router is None


def test_cross_loop_rebuild_discards_only_previous_router() -> None:
    """同一客户端跨 loop 重建前关闭旧实例且不清理其他 Router。"""
    client = LLMClient("offline", "http://127.0.0.1:9", "redacted")
    other = LLMClient("offline", "http://127.0.0.1:9", "redacted")

    async def _ensure(target: LLMClient) -> weakref.ReferenceType[Any]:
        target._ensure_runtime()
        return weakref.ref(target._router)

    old = asyncio.run(_ensure(client))
    other_ref = asyncio.run(_ensure(other))
    current = asyncio.run(_ensure(client))
    gc.collect()
    assert old() is None
    assert current() is client._router
    assert _router_callback_count(client._router) == 4
    assert _router_callback_count(other._router) == 4
    client.close()
    assert other_ref() is other._router
    assert _router_callback_count(other._router) == 4
    other.close()


def test_closing_unused_roles_does_not_build_routers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """关闭未使用角色不会触发惰性 Router 构造。"""
    clients = LLMClients(
        coder=LLMClient("coder", "http://127.0.0.1:9", "redacted"),
        default=LLMClient("default", "http://127.0.0.1:9", "redacted"),
        embedding=LLMClient("embedding", "http://127.0.0.1:9", "redacted"),
    )
    proxy = ServiceProxy(env=object(), llm=clients, trace=None, replay=None)

    def _unexpected_build(*unused: Any) -> None:
        del unused
        raise AssertionError("unused role built a router")

    monkeypatch.setattr(llm_dispatcher, "_build_router", _unexpected_build)
    proxy.close_llm_clients()
    assert all(
        client._router is None
        for client in (clients.default, clients.coder, clients.embedding)
    )


def test_service_proxy_closes_every_configured_role() -> None:
    """ServiceProxy 对每个已配置角色恰好关闭一次。"""
    clients = LLMClients(
        coder=_RoleClient(), default=_RoleClient(), embedding=_RoleClient()
    )
    proxy = ServiceProxy(env=object(), llm=clients, trace=None, replay=None)
    proxy.close_llm_clients()
    assert clients.default.close_calls == 1
    assert clients.coder.close_calls == 1
    assert clients.embedding.close_calls == 1


def test_close_logs_discard_failure_and_clears_runtime(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """清理异常会记录错误且不留下可复用的旧运行时。"""
    client = LLMClient("offline", "http://127.0.0.1:9", "redacted")
    client._router = _BrokenRouter()
    client._sem = object()
    client._loop = object()
    caplog.set_level(logging.ERROR)
    with pytest.raises(RuntimeError, match="discard failed"):
        client.close()
    assert client._router is None
    assert client._sem is None
    assert client._loop is None
    assert "Failed to discard LiteLLM router" in caplog.text


# ---------------------------------------------------------------------------
# LiteLLM 缓存的异步客户端在本循环关闭并移出（长时运行成本第五章）
# ---------------------------------------------------------------------------


class _CachedClient:
    """假的缓存客户端：异步 close 记下运行它的事件循环，可按需抛出。"""

    def __init__(self, failure: Exception | None = None) -> None:
        self.closed_in: list[Any] = []
        self.failure = failure

    async def close(self) -> None:
        """记下当前循环；给定失败时抛出。"""
        self.closed_in.append(asyncio.get_running_loop())
        if self.failure is not None:
            raise self.failure


def _cache() -> Any:
    """LiteLLM 的进程级客户端缓存。"""
    return litellm.in_memory_llm_clients_cache


def _forget(*keys: str) -> None:
    """用例结束时移出放进缓存的条目。"""
    for key in keys:
        _cache().delete_cache(key)


def test_close_cached_async_clients_closes_and_evicts_only_this_loop() -> None:
    """只处理键以本循环 id 结尾的条目：在本循环里关闭并移出，返回 1；别的循环的条目留着、不关闭。"""
    other = _CachedClient()
    keys: list[str] = []

    async def scenario() -> tuple[int, Any, _CachedClient]:
        loop = asyncio.get_running_loop()
        own = _CachedClient()
        keys.extend([f"k-{id(loop)}", "k-1"])
        _cache().cache_dict[keys[0]] = own
        _cache().cache_dict[keys[1]] = other
        return await llm_dispatcher.close_cached_async_clients(), loop, own

    try:
        handled, loop, own = asyncio.run(scenario())
        assert handled == 1
        assert own.closed_in == [loop] and keys[0] not in _cache().cache_dict
        assert _cache().cache_dict.get("k-1") is other and other.closed_in == []
    finally:
        _forget(*keys)


def test_cached_client_close_failure_evicts_all_then_raises_the_first(caplog: pytest.LogCaptureFixture) -> None:
    """两个本循环条目关闭都失败：两个都移出，抛出第一个失败，每个失败一条错误日志。"""
    keys: list[str] = []

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        keys.extend([f"a-{id(loop)}", f"b-{id(loop)}"])
        _cache().cache_dict[keys[0]] = _CachedClient(RuntimeError("a"))
        _cache().cache_dict[keys[1]] = _CachedClient(RuntimeError("b"))
        await llm_dispatcher.close_cached_async_clients()

    caplog.set_level(logging.ERROR)
    try:
        with pytest.raises(RuntimeError, match="^a$"):
            asyncio.run(scenario())
        assert not any(key in _cache().cache_dict for key in keys)
        assert caplog.text.count("Failed to close cached LiteLLM client _CachedClient") == 2
    finally:
        _forget(*keys)


def test_worker_coroutine_closes_cached_clients_inside_its_loop() -> None:
    """worker 协程在自己的循环里登记的缓存客户端，在同一循环结束前关闭并移出；收尾仍是先统计再关闭。"""
    client, seen, keys = _CachedClient(), [], []

    async def task() -> str:
        loop = asyncio.get_running_loop()
        seen.append(loop)
        keys.append(f"worker-{id(loop)}")
        _cache().cache_dict[keys[0]] = client
        return "done"

    proxy = _TaskProxy()
    try:
        result, _ = runner._run_worker_coroutine(task(), proxy)
        assert result == "done"
        assert client.closed_in == seen and keys[0] not in _cache().cache_dict
        assert proxy.events == ["stats", "close"]
    finally:
        _forget(*keys)


def test_worker_task_failure_and_close_failure_are_both_kept() -> None:
    """任务失败且关闭缓存客户端也失败：异常组同时保留两者，收尾照常执行。"""
    keys: list[str] = []

    async def task() -> None:
        loop = asyncio.get_running_loop()
        keys.append(f"worker-{id(loop)}")
        _cache().cache_dict[keys[0]] = _CachedClient(RuntimeError("close"))
        raise RuntimeError("task")

    proxy = _TaskProxy()
    try:
        with pytest.raises(BaseExceptionGroup, match="Agent task and LiteLLM client close both failed") as caught:
            runner._run_worker_coroutine(task(), proxy)
        assert [str(item) for item in caught.value.exceptions] == ["task", "close"]
        assert proxy.events == ["stats", "close"]
    finally:
        _forget(*keys)


class _CompletionHandler(BaseHTTPRequestHandler):
    """本地 OpenAI 兼容服务：每个补全请求回一个内容为 ok 的回答。"""

    def do_POST(self) -> None:  # noqa: N802 - 标准库回调名
        """读完请求体后回答固定的补全响应。"""
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        body = json.dumps({"id": "chatcmpl-local", "object": "chat.completion", "created": 0, "model": "test-model",
                           "choices": [{"index": 0, "finish_reason": "stop",
                                        "message": {"role": "assistant", "content": "ok"}}],
                           "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *arguments: Any) -> None:
        """不写访问日志。"""


def test_five_real_worker_tasks_leave_no_unclosed_client_records(caplog: pytest.LogCaptureFixture) -> None:
    """五个 worker 任务各用真实 Router 请求本地服务：全部回答 ok，垃圾回收后没有 Unclosed 客户端记录。"""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CompletionHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
    caplog.set_level(logging.DEBUG)
    answers = []
    try:
        for _ in range(5):
            client = LLMClient("test-model", base_url, "test-only")
            proxy = ServiceProxy(env=object(), llm=LLMClients(coder=client, default=client), trace=None, replay=None)

            async def ask(target: LLMClient = client) -> str:
                response = await target.call(messages=[{"role": "user", "content": "hello"}], max_retries=0)
                return response.choices[0].message.content

            answers.append(runner._run_worker_coroutine(ask(), proxy)[0])
        gc.collect()
    finally:
        server.shutdown()
        server.server_close()
    assert answers == ["ok"] * 5
    assert not [record for record in caplog.records if "Unclosed" in record.getMessage()]
