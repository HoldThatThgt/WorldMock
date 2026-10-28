"""完整原生运行里停车后重发不会改变提示、结算事件或最终世界；等待证据和运维记录仍完整保留。"""

import asyncio
import copy
import json
import re
from types import SimpleNamespace

import pytest

from agentsociety2.config import llm_dispatcher
from tests.dayrun_baseline import deterministic_day_runtime
from tests.integration.runtime import native_runtime_fixtures as fixtures
from tests.integration.runtime.current_contract_fixtures import ROOT
from tests.integration.runtime.native_runtime_fixtures import native_runtime  # noqa: F401
from worldmock.foundation.serialization import canonical_json
from worldmock.recording_engine.storage.store import FootprintStore
from worldmock.runtime.lifecycle.runner import RunRequest, run_world


def _read_run(run_dir):
    """读取运行已提交事件、最终世界与运维记录，离开前关闭存储。"""
    store = FootprintStore(run_dir)
    try:
        events = [event.model_dump(mode="json") for event in store.iter_events(store.last_event_seq)]
        state = store.state
    finally:
        store.close()
    ops = [json.loads(line) for path in sorted((run_dir / "logs").glob("ops-*.jsonl"))
           for line in path.read_text(encoding="utf-8").splitlines()]
    return events, state, ops


def _normalize(events, state):
    """只去墙钟和失败发送；事件与检查点的随机身份按首次出现映射，内容摘要保持原字节。"""
    records = copy.deepcopy(events)
    for event in records:
        event.pop("recorded_at")
        event.pop("wall_elapsed_ms")
        if event["event_type"] == "model_response":
            event["data"].pop("transport_attempts")
    identities = {}

    def replace(match):
        value = match.group()
        return identities.setdefault(value, f"random-id-{len(identities)}")

    payload = canonical_json({"events": records, "state": state}).decode("utf-8")
    return json.loads(re.sub(r"(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])", replace, payload))


def _unique_requests(requests):
    """折叠连续重复的线路请求；每个其他请求的规范字节必须原样保留。"""
    result = []
    for request in requests:
        encoded = canonical_json(request)
        if not result or result[-1] != encoded:
            result.append(encoded)
    return result


@pytest.fixture
async def paired_runs(tmp_path, native_runtime, monkeypatch):
    """相同运行身份执行一小时两次，仅第二次的第二次模型调用先失败六次，越过三次计数进入停车。

    MEMORY.md 的来源事件身份参与内容摘要，因此每次复用完整日录制的固定 UUID 与记录时钟，内容摘要仍逐字比较。
    """
    root, frozen, models = native_runtime
    clock, waits = [0.0], []

    async def sleep(delay):
        assert all(model._sem.acquired == 0 for model in models)
        waits.append(delay)
        clock[0] += delay
        await asyncio.sleep(0)

    monkeypatch.setattr(llm_dispatcher, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(llm_dispatcher, "asyncio", SimpleNamespace(
        sleep=sleep, timeout=asyncio.timeout, get_running_loop=asyncio.get_running_loop))
    runs = []
    for name, failures in (("clean", {}), ("parked", {2: 6})):
        # MEMORY.md 的来源事件身份参与内容摘要，复用完整日录制的固定身份与记录时钟，不屏蔽内容摘要。
        deterministic_day_runtime(monkeypatch)
        monkeypatch.setattr(fixtures, "MODEL_FACTORY", lambda env: fixtures.scripted_dispatcher(env, failures))
        result = await run_world(RunRequest(ROOT, root, tmp_path / name, "park", "park"))
        assert result.current_time == frozen.config.stop_time
        runs.append(_read_run(result.run_dir))
    assert waits == [2, 4, 8, 16, 32, 64]
    return runs, [model._router.requests for model in models]


async def test_parked_run_settles_like_an_uninterrupted_run(paired_runs):
    """六次连接失败后继续完成：请求逐字相同，事件与最终世界除明确允许的记录差异外相同，没有恢复分支。"""
    runs, requests = paired_runs
    for events, _, _ in runs:
        assert any(event["event_type"] == "run_ended" for event in events)
        assert not any(event["event_type"] in {"branch_created", "run_failed"} for event in events)
    clean_requests, parked_requests = [_unique_requests(items) for items in requests]
    assert len(clean_requests) == len(parked_requests)
    for before, after in zip(clean_requests, parked_requests, strict=True):
        assert before == after
    clean_run, parked_run = [_normalize(*run[:2]) for run in runs]
    assert len(clean_run["events"]) == len(parked_run["events"])
    for before, after in zip(clean_run["events"], parked_run["events"], strict=True):
        assert before == after
    assert clean_run["state"] == parked_run["state"]
    clean = [event for event in runs[0][0] if event["event_type"] == "model_response"]
    faulted = [event for event in runs[1][0] if event["event_type"] == "model_response"]
    assert all(event["data"]["transport_attempts"] == [] for event in clean)
    attempts = [event["data"]["transport_attempts"] for event in faulted if event["data"]["transport_attempts"]]
    assert len(attempts) == 1
    assert [item["attempt"] for item in attempts[0]] == [1, 2, 3, 4, 5, 6]
    assert [item["wait_ms"] for item in attempts[0]] == [2000, 4000, 8000, 16000, 32000, 64000]
    assert [item["failed_after_ms"] for item in attempts[0]] == [0, 2000, 6000, 14000, 30000, 62000]


async def test_parked_run_ops_log_names_the_wait(paired_runs):
    """只有有失败发送的运行写一条等待记录，累计六次失败、126 秒等待及最后错误类型。"""
    runs, _ = paired_runs
    clean = [record for record in runs[0][2] if record.get("code") == "MODEL_TRANSPORT_WAIT"]
    parked = [record for record in runs[1][2] if record.get("code") == "MODEL_TRANSPORT_WAIT"]
    assert clean == []
    assert len(parked) == 1
    assert parked[0]["failed_sends"] == 6 and parked[0]["waited_ms"] == 126000
    assert parked[0]["last_error"] == "APIConnectionError"
