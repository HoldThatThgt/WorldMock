"""@brief 模型客户端用例共用的替身：只换掉 litellm 发送的真实客户端、假时钟与等待，以及本机 OpenAI 兼容服务。"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from typing import Any

from litellm.exceptions import RateLimitError

from worldmock.configuration.model import model_client
from worldmock.configuration.model.model_client import ModelClient

OFFLINE_MODEL = "offline-model"
OFFLINE_BASE = "https://example.invalid"
OFFLINE_KEY = "test-key"

#: 本机服务回 200 时的完整补全正文。
_COMPLETION = {"id": "local", "object": "chat.completion", "created": 0, "model": "test-model",
               "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
               "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}
#: 本机服务回非 200 状态时的错误正文。
_BUSY = {"error": {"message": "busy", "type": "rate_limit"}}


def draft_request(messages=(), max_tokens=8000) -> dict:
    """@brief 交给模型客户端的六键请求，键序同 REQUEST_KEYS。"""
    return {"model": OFFLINE_MODEL, "messages": list(messages), "tools": [], "tool_choice": "required",
            "max_tokens": max_tokens, "extra_body": {"thinking": {"type": "disabled"}}}


def provider_error(kind):
    """@brief 构造 LiteLLM 类型化异常，文本不包含任何传输关键词。"""
    return kind(message="busy", llm_provider="openai", model=OFFLINE_MODEL)


def scripted_client(monkeypatch, script, clock=None, max_retries=0,
                    transport_max_retries=3) -> tuple[ModelClient, list[float], list[dict]]:
    """@brief 真实 ModelClient，只换掉 litellm 发送与等待：发送按脚本依次抛出异常或返回结果，记下每次的关键字参数
    浅拷贝（client 是原对象）；等待记下秒数，给出 clock 时把秒数加到它上面，单调时钟也读它。

    @return (客户端, 等待秒数列表, 关键字参数列表)
    """
    client = ModelClient(base_url=OFFLINE_BASE, api_key=OFFLINE_KEY, max_retries=max_retries,
                         transport_max_retries=transport_max_retries)
    items, delays, sent = iter(script), [], []

    async def acompletion(**arguments):
        """@brief 记下这次发送，返回或抛出脚本的下一项，禁止隐式联网。"""
        sent.append(dict(arguments))
        item = next(items)
        if isinstance(item, BaseException):
            raise item
        return item

    async def sleep(delay):
        """@brief 记下等待秒数，给出 clock 时推进它。"""
        delays.append(delay)
        if clock is not None:
            clock[0] += delay

    monkeypatch.setattr(model_client.litellm, "acompletion", acompletion)
    monkeypatch.setattr(model_client, "asyncio", SimpleNamespace(sleep=sleep, timeout=asyncio.timeout))
    if clock is not None:
        monkeypatch.setattr(model_client, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    return client, delays, sent


class _QuietServer(ThreadingHTTPServer):
    """@brief 客户端已断开时写回失败不打印。"""

    def handle_error(self, request, client_address):
        """@brief 不打印处理请求时的异常。"""


@contextmanager
def _local_server(answers):
    """@brief 本机 OpenAI 兼容服务，在守护线程里运行：第 n 个 POST 按 answers[min(n, len(answers) - 1)] 回答，每项是
    (状态, 响应头字典, 先等待的秒数, 正文)；正文为 None 时 200 回 _COMPLETION、其余状态回 _BUSY。记下每个请求的
    (方法, 路径, 按到达次序的 [名, 值] 列表, 原始正文字节)。

    @return 进入时给出 (f"http://127.0.0.1:{端口}/v1", 记录列表)
    """
    records: list[tuple[str, str, list[list[str]], bytes]] = []
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        """@brief 按到达次序记下请求并按脚本回答。"""

        def do_POST(self) -> None:  # noqa: N802 - 标准库回调名
            """@brief 读完请求体，记下请求，按脚本等待后回答。"""
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            with lock:
                records.append(("POST", self.path, [list(item) for item in self.headers.items()], body))
                status, headers, wait, payload = answers[min(len(records) - 1, len(answers) - 1)]
            if wait:
                time.sleep(wait)
            if payload is None:
                payload = _COMPLETION if status == 200 else _BUSY
            encoded = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            for name, value in headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, *arguments: Any) -> None:
            """@brief 不写访问日志。"""

    server = _QuietServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/v1", records
    finally:
        server.shutdown()
        server.server_close()


async def _rate_limit(headers=None):
    """@brief 真实 ModelClient 向本机服务发送一次、服务回带给定响应头的 429 时抛出的 RateLimitError，与运行里的同形：
    供应商响应头只在 litellm_response_headers 上。不给头时没有 Retry-After。须在 scripted_client 换掉发送之前构造。"""
    with _local_server([(429, headers or {}, 0, None)]) as (url, _):
        client = ModelClient(base_url=url, api_key=OFFLINE_KEY, max_retries=0, transport_max_retries=0)
        try:
            await client._send_within(draft_request())
        except RateLimitError as error:
            return error
        finally:
            await client.close()
    raise AssertionError("the local server did not rate-limit the request")


def _fake_time(monkeypatch):
    """@brief 模型客户端的单调时钟与等待换成假时钟：非零等待记下秒数并推进时钟，零秒等待照常让出事件循环。

    @return (时钟, 等待秒数列表)
    """
    clock, delays, real_sleep = [0.0], [], asyncio.sleep

    async def sleep(delay):
        """@brief 非零等待只推进假时钟；零秒等待照常让出。"""
        if not delay:
            await real_sleep(0)
            return
        delays.append(delay)
        clock[0] += delay

    monkeypatch.setattr(model_client, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(model_client, "asyncio", SimpleNamespace(sleep=sleep, timeout=asyncio.timeout))
    return clock, delays
