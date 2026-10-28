"""恢复、重放与打开已有运行读运行清单冻结的模型元数据，不访问模型服务；父运行不对时在加载环境与复制之前失败。"""

import json

import pytest
import requests

from tests.integration.runtime.current_contract_fixtures import ROOT
from tests.integration.runtime.native_runtime_fixtures import native_runtime  # noqa: F401
from worldmock.configuration.model import model_metadata as model_endpoint
from worldmock.foundation.errors import IntegrityError
from worldmock.recording_engine.storage.store import FootprintStore
from worldmock.state.decisions.results import CounterfactualIntervention
from worldmock.runtime.lifecycle.runner import (
    CounterfactualRequest,
    ResumeRequest,
    RunRequest,
    replay_world,
    resume_world,
    run_world,
)


@pytest.fixture
def sleeps(monkeypatch):
    """元数据重试的等待只记录不真睡：再次在线读取的构建在这里失败，而不是每次读取睡 30 秒。"""
    waits = []
    monkeypatch.setattr(model_endpoint.time, "sleep", waits.append)
    return waits


def _offline(monkeypatch):
    """把元数据服务换成记录调用并以断言失败的替身（不被元数据读取的 requests 异常处理吞掉），返回调用记录。"""
    calls = []

    def get(url, **arguments):
        calls.append(url)
        raise AssertionError("metadata service must not be read")

    monkeypatch.setattr(requests, "get", get)
    return calls


async def _parent(tmp_path, root):
    """跑完一小时的父运行，返回运行目录、第一次观察之后的共同检查点身份与那次观察的事件身份。"""
    result = await run_world(RunRequest(ROOT, root, tmp_path / "parent", "frozen-parent", "frozen-parent"))
    store = FootprintStore(result.run_dir)
    try:
        events = list(store.iter_events(store.last_event_seq))
    finally:
        store.close()
    observed = next(event for event in events if event.event_type == "observation")
    checkpoint = next(event.data["checkpoint_id"] for event in events
                      if event.event_type == "checkpoint_created" and event.event_seq > observed.event_seq)
    return result.run_dir, checkpoint, observed.event_id


def _manifest(run_dir):
    """运行目录里的运行清单。"""
    return json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_resume_and_replay_make_no_metadata_request(tmp_path, native_runtime, monkeypatch, sleeps):
    """父运行之后元数据服务不可读：恢复与重放照常跑完，没有一次读取，子运行清单的模型元数据等于父运行的。"""
    root, _, _ = native_runtime
    parent, checkpoint, observation = await _parent(tmp_path, root)
    calls = _offline(monkeypatch)
    intervention = CounterfactualIntervention(kind="set_balance_fen", agent_id=1, balance_fen=123456,
                                              hypothesis="Different funds change spending choices.",
                                              source_event_ids=(observation,))
    resumed = await resume_world(ResumeRequest(ROOT, root, parent, checkpoint, tmp_path / "resume"))
    replayed = await replay_world(CounterfactualRequest(
        ResumeRequest(ROOT, root, parent, checkpoint, tmp_path / "replay"), intervention))
    assert calls == [] and sleeps == []
    for child in (resumed.run_dir, replayed.run_dir):
        assert _manifest(child)["model_metadata"] == _manifest(parent)["model_metadata"]


@pytest.mark.asyncio
async def test_resume_uses_the_frozen_capacity_even_when_the_live_service_differs(
        tmp_path, native_runtime, monkeypatch, sleeps):
    """父运行之后服务改报更小的输出上限：恢复照常跑完，子运行第一次决策请求的输出上限仍取冻结值。"""
    root, frozen, models = native_runtime
    parent, checkpoint, _ = await _parent(tmp_path, root)
    smaller = {"data": [{"id": frozen.config.model.requested_model,
                         "context_window": frozen.model_metadata.context_window, "max_output_tokens": 12000}]}
    calls = []

    def changed(url, **arguments):
        calls.append(url)
        return type("Response", (), {"raise_for_status": lambda self: None, "json": lambda self: smaller})()

    monkeypatch.setattr(requests, "get", changed)
    await resume_world(ResumeRequest(ROOT, root, parent, checkpoint, tmp_path / "child"))
    assert calls == []
    first = models[-1].requests[0]
    assert first["max_tokens"] == min(16000, frozen.model_metadata.max_output_tokens) != 12000


def _forbidden(name):
    """被调用即让用例失败的替身：证明失败发生在这一步之前。"""
    def call(*arguments, **options):
        raise AssertionError(f"{name} must not run before the parent run is accepted")
    return call


def _no_branch_left(tmp_path, destination):
    """目标目录与恢复暂存目录都不存在。"""
    assert not destination.exists()
    assert not list(destination.parent.glob(".worldmock-resume-*"))


@pytest.mark.asyncio
async def test_bad_checkpoint_id_fails_before_environment_load_or_copy(tmp_path, native_runtime, monkeypatch, sleeps):
    """空检查点身份：在加载环境与配置进程之前报检查点缺失，不留目标目录或暂存目录。"""
    import worldmock.configuration.inputs.environment
    from worldmock.runtime.framework import setup as run_setup

    root, _, _ = native_runtime
    parent, _, _ = await _parent(tmp_path, root)
    calls = _offline(monkeypatch)
    monkeypatch.setattr(worldmock.configuration.inputs.environment, "load_environment", _forbidden("load_environment"))
    monkeypatch.setattr(run_setup, "_configure_process", _forbidden("_configure_process"))
    destination = tmp_path / "branches" / "child"
    with pytest.raises(IntegrityError, match="^Checkpoint is missing or its record is damaged$"):
        await resume_world(ResumeRequest(ROOT, root, parent, "", destination))
    assert calls == []
    _no_branch_left(tmp_path, destination)


@pytest.mark.asyncio
async def test_parent_with_an_edited_manifest_fails_before_environment_load_or_copy(
        tmp_path, native_runtime, monkeypatch, sleeps):
    """父运行清单被改写去掉 model_metadata：检查点按清单摘要拒绝，不加载环境、不读元数据、不留分支目录。"""
    import worldmock.configuration.inputs.environment

    root, _, _ = native_runtime
    parent, checkpoint, _ = await _parent(tmp_path, root)
    manifest = _manifest(parent)
    del manifest["model_metadata"]
    (parent / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    calls = _offline(monkeypatch)
    monkeypatch.setattr(worldmock.configuration.inputs.environment, "load_environment", _forbidden("load_environment"))
    destination = tmp_path / "branches" / "child"
    with pytest.raises(IntegrityError, match="^Checkpoint and frozen manifest disagree$"):
        await resume_world(ResumeRequest(ROOT, root, parent, checkpoint, destination))
    assert calls == []
    _no_branch_left(tmp_path, destination)


@pytest.mark.asyncio
async def test_engine_open_reads_the_store_manifest_metadata(tmp_path, native_runtime, monkeypatch, sleeps):
    """从环境目录打开已有运行：元数据服务不可读也能打开，输入里的模型元数据就是清单冻结的那一份。"""
    from worldmock.simulation_engine.engine import LifeEngine

    root, _, _ = native_runtime
    parent, _, _ = await _parent(tmp_path, root)
    calls = _offline(monkeypatch)
    engine = LifeEngine.open(root, parent)
    try:
        assert engine.context.inputs.model_metadata.model_dump(mode="json") == _manifest(parent)["model_metadata"]
    finally:
        engine.close()
    assert calls == [] and sleeps == []


@pytest.mark.asyncio
async def test_fresh_run_fetch_retries_and_freezes_the_same_metadata(tmp_path, native_runtime, monkeypatch, sleeps):
    """新运行的元数据读取先两次 TLS 失败：整次重试后运行照常开始并跑完，清单冻结的元数据与没有失败时相同。"""
    root, frozen, _ = native_runtime
    answer, failures = requests.get, []

    def flaky(url, **arguments):
        if len(failures) < 2:
            failures.append(url)
            raise requests.exceptions.SSLError("handshake")
        return answer(url, **arguments)

    monkeypatch.setattr(requests, "get", flaky)
    result = await run_world(RunRequest(ROOT, root, tmp_path / "fresh", "fresh-run", "fresh-run"))
    assert len(failures) == 2 and sleeps == [2.0, 4.0]
    assert result.current_time == frozen.config.stop_time
    assert _manifest(result.run_dir)["model_metadata"] == frozen.model_metadata.model_dump(mode="json")
