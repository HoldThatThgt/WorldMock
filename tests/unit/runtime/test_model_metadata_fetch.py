"""模型元数据读取的整次重试，以及恢复与重放读取运行清单冻结元数据的加载器。"""

import logging
from types import SimpleNamespace

import pytest
import requests

from tests.integration.runtime.current_contract_fixtures import inputs
from worldmock import model_endpoint
from worldmock.errors import EnvironmentInputError, IntegrityError
from worldmock.model_endpoint import frozen_metadata_loader, load_model_metadata


def _answer(frozen):
    """实际服务给出的元数据响应正文：唯一匹配模型的容量与输出上限。"""
    return {"data": [{"id": frozen.config.model.requested_model,
                      "context_window": frozen.model_metadata.context_window,
                      "max_output_tokens": frozen.model_metadata.max_output_tokens}]}


def _http_error(status):
    """raise_for_status 抛出的 HTTP 错误，带响应状态。"""
    return requests.HTTPError(f"HTTP {status}", response=SimpleNamespace(status_code=status))


def _outcome(config):
    """读取一次元数据：成功为元数据，失败时返回输入错误，交给断言报告。"""
    try:
        return load_model_metadata(config)
    except EnvironmentInputError as error:
        return error


def _service(monkeypatch, frozen, script):
    """按脚本逐次回答 GET：异常从 GET 抛出，("status", n) 由 raise_for_status 抛出，("json", e) 由 json() 抛出，
    "ok" 返回合法元数据；返回 GET 记录与等待记录。"""
    calls, waits, steps = [], [], iter(script)

    def get(url, **arguments):
        calls.append(url)
        step = next(steps)
        if isinstance(step, BaseException):
            raise step

        def check():
            if isinstance(step, tuple) and step[0] == "status":
                raise _http_error(step[1])

        def body():
            if isinstance(step, tuple) and step[0] == "json":
                raise step[1]
            return _answer(frozen)

        return SimpleNamespace(raise_for_status=check, json=body)

    monkeypatch.setenv(frozen.config.model.api_key_env, "offline-metadata-secret")
    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(model_endpoint.time, "sleep", waits.append)
    return calls, waits


@pytest.mark.parametrize("failure", [
    requests.ConnectionError("offline"), requests.exceptions.SSLError("handshake"), requests.Timeout("slow"),
    requests.exceptions.ChunkedEncodingError("cut"),
    ("status", 429), ("status", 500), ("status", 502), ("status", 503), ("status", 504),
], ids=["connection", "ssl", "timeout", "chunked", "429", "500", "502", "503", "504"])
def test_transient_failures_are_retried_whole_with_fixed_waits(monkeypatch, failure):
    """连接、TLS、超时、响应体中断与 429/5xx 各失败两次后成功：整次读取三次，等待 2、4 秒，元数据等于成功的那一份。"""
    frozen = inputs()
    calls, waits = _service(monkeypatch, frozen, [failure, failure, "ok"])
    assert _outcome(frozen.config.model) == frozen.model_metadata
    assert len(calls) == 3
    assert waits == [2.0, 4.0]


def test_five_transient_failures_end_as_the_unchanged_input_error(monkeypatch, caplog):
    """五次 SSLError：等待 2、4、8、16 秒后以原来的输入错误结束，并写出读取次数与错误类型。"""
    frozen = inputs()
    calls, waits = _service(monkeypatch, frozen, [requests.exceptions.SSLError("handshake")] * 5)
    with caplog.at_level(logging.WARNING), pytest.raises(EnvironmentInputError,
                                                         match="^model context metadata is unavailable$") as caught:
        load_model_metadata(frozen.config.model)
    assert caught.value.code == "RUN_CONFIG_INVALID" and caught.value.path == "config.toml"
    assert len(calls) == 5 and waits == [2.0, 4.0, 8.0, 16.0]
    assert "Model metadata fetch failed after 5 tries: SSLError" in caplog.text
    assert caplog.text.count("Model metadata fetch failed (SSLError); retrying in") == 4


@pytest.mark.parametrize("failure", [
    ("status", 401), ("status", 404), ("json", requests.exceptions.JSONDecodeError("Expecting value", "<html>", 0)),
], ids=["401", "404", "json"])
def test_authentication_and_payload_failures_are_not_retried(monkeypatch, failure, caplog):
    """鉴权失败、找不到与内容不是 JSON 只读一次、不等待，以原来的输入错误结束。"""
    frozen = inputs()
    calls, waits = _service(monkeypatch, frozen, [failure, "ok"])
    with caplog.at_level(logging.ERROR), pytest.raises(EnvironmentInputError,
                                                       match="^model context metadata is unavailable$"):
        load_model_metadata(frozen.config.model)
    assert len(calls) == 1 and waits == []
    assert "Model metadata fetch failed after 1 tries:" in caplog.text


def test_frozen_metadata_loader_returns_the_frozen_value_without_network(monkeypatch):
    """冻结元数据加载器不访问网络，对任何配置都返回清单冻结的那一份。"""
    frozen = inputs()

    def offline(*arguments, **options):
        raise requests.exceptions.SSLError("must not fetch")

    monkeypatch.setattr(requests, "get", offline)
    loader = frozen_metadata_loader({"model_metadata": frozen.model_metadata.model_dump(mode="json")})
    assert loader(frozen.config.model) == frozen.model_metadata
    assert loader(None) == frozen.model_metadata


@pytest.mark.parametrize("source", [{}, {"model_metadata": None}, "manifest"], ids=["missing", "none", "partial"])
def test_frozen_metadata_loader_rejects_missing_or_invalid_metadata(source, caplog):
    """清单没有 model_metadata、值为 null 或缺 context_window 时拒绝，并写英文错误日志。"""
    if source == "manifest":
        value = inputs().model_metadata.model_dump(mode="json")
        del value["context_window"]
        source = {"model_metadata": value}
    with caplog.at_level(logging.ERROR), pytest.raises(IntegrityError,
                                                       match="^Frozen model metadata is missing or invalid$"):
        frozen_metadata_loader(source)
    assert "Frozen model metadata is missing or invalid" in caplog.text
