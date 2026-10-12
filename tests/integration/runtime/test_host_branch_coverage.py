"""C4e 宿主簇分支覆盖：runtime 恢复/失败传播、cli 命令出口、status 只读视图、
validation 拒绝分支与 opslog 边界。模型客户端全部打桩，零真实调用。
"""

from __future__ import annotations

from tests.data_paths import ENVIRONMENT_ROOT
import worldmock.run_setup as worldmock_run_setup_module

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com/beta")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from worldmock.errors import IntegrityError, ProtocolError
from worldmock.footprints.checkpoint import snapshot_directory
from worldmock.footprints.models import EventDraft
from worldmock.footprints.store import FootprintStore
from worldmock.life.models import CounterfactualIntervention
from worldmock.life.engine import LifeEngine
from tests.integration.runtime_fixtures import current_run_identity, write_agent_workspace
from worldmock.runtime import (CounterfactualRequest, ResumeRequest, _cleanup_runtime, _execute,
    _ops_crash_log, _require_custom_modules, _require_run_shell, _terminate_failed_actor)
from worldmock.run_setup import _prepend_pythonpath, runtime_code_sha256
from worldmock.status import read_status, write_status
from worldmock.run_setup import validate_stage_model_identity
from tests.integration.as2_fixtures import use_fake_as2, write_project

ROOT = Path(__file__).resolve().parents[3]
SCENARIO = ENVIRONMENT_ROOT


@pytest.fixture(autouse=True)
def _locked_fake_as2(monkeypatch, tmp_path_factory):
    """本文件的运行身份一律解析到临时 AS2 包并以其摘要为锁定值，不依赖本机实际安装的 AS2。"""
    return use_fake_as2(monkeypatch, tmp_path_factory.mktemp("as2-site"))


# ---------------------------------------------------------------------------
# runtime 入口包装与反事实请求合同
# ---------------------------------------------------------------------------


def test_counterfactual_request_rejects_foreign_payloads():
    """反事实请求拒绝绕开严格模型的输入。"""
    with pytest.raises(TypeError, match="ResumeRequest"):
        CounterfactualRequest(SimpleNamespace(), CounterfactualIntervention(
            kind="set_balance_fen", agent_id=1, balance_fen=0, hypothesis="h",
            source_event_ids=("e",)))
    request = ResumeRequest(ROOT, SCENARIO, ROOT, "checkpoint", ROOT / "elsewhere")
    with pytest.raises(TypeError, match="strict life model"):
        CounterfactualRequest(request, "not-an-intervention")


def _prepared_stub(tmp_path, **overrides):
    """构造 _execute 使用的最小启动输入。"""
    base = dict(
        run_id="child", run_dir=tmp_path, intervention=None, intervention_sha256=None,
        inputs=SimpleNamespace(config=SimpleNamespace(timezone="UTC")),
        start_time=datetime(2026, 1, 5, tzinfo=ZoneInfo("UTC")),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class _Society:
    """记录调用顺序并支持脚本化失败的 Society 桩。"""

    current_time = datetime(2026, 1, 5, tzinfo=ZoneInfo("UTC"))
    step_count = 1

    def __init__(self, fail_run=None, fail_close=None):
        self.calls = []
        self._fail_run = fail_run
        self._fail_close = fail_close

    async def init(self):
        self.calls.append("init")

    async def run(self):
        self.calls.append("run")
        if self._fail_run:
            raise self._fail_run

    async def close(self):
        self.calls.append("close")
        if self._fail_close:
            raise self._fail_close


async def _no_dispatchers():
    return None




# ---------------------------------------------------------------------------
# 进程配置与凭据
# ---------------------------------------------------------------------------












def test_prepend_pythonpath_is_idempotent(tmp_path, monkeypatch):
    """PYTHONPATH 前插保持唯一且幂等；项目根进本进程 sys.path 的最前，只出现一次，src 不加；
    项目根本来就在 sys.path 中间时位置不变。"""
    monkeypatch.setenv("PYTHONPATH", f"/already{os.pathsep}/present")
    monkeypatch.setattr(sys, "path", list(sys.path))
    _prepend_pythonpath(tmp_path)
    once = os.environ["PYTHONPATH"]
    _prepend_pythonpath(tmp_path)
    assert os.environ["PYTHONPATH"] == once
    assert once.split(os.pathsep)[:2] == [str(tmp_path), str(tmp_path / "src")]
    assert once.endswith("/present")
    assert sys.path[0] == str(tmp_path) and sys.path.count(str(tmp_path)) == 1
    assert str(tmp_path / "src") not in sys.path
    monkeypatch.setattr(sys, "path", ["first", str(tmp_path), "last"])
    _prepend_pythonpath(tmp_path)
    assert sys.path == ["first", str(tmp_path), "last"]


# ---------------------------------------------------------------------------
# 新建/恢复准备路径
# ---------------------------------------------------------------------------


















def test_runtime_code_sha256_requires_complete_sources(tmp_path, monkeypatch):
    """代码身份源缺失或不安全时拒绝；实际导入的 AS2 无法解析时同样拒绝给出代码身份。"""
    import worldmock.runtime as runtime

    with pytest.raises(IntegrityError, match="missing or unsafe"):
        runtime_code_sha256(tmp_path)
    project = write_project(tmp_path / "project")
    assert len(runtime_code_sha256(project)) == 64
    monkeypatch.setattr(worldmock_run_setup_module, '_AS2_PACKAGE', 'worldmock_absent_as2_package')
    with pytest.raises(IntegrityError, match="AS2 runtime identity differs from the locked source"):
        runtime_code_sha256(project)


def test_run_shell_rejects_non_directory_and_foreign_files(tmp_path):
    """运行壳只允许不存在目录或仅含 status.json 的目录。"""
    (tmp_path / "occupied").write_text("not a directory")
    with pytest.raises(FileExistsError, match="not a safe directory"):
        _require_run_shell(tmp_path / "occupied")
    empty = tmp_path / "fresh"
    _require_run_shell(empty)
    assert not empty.exists() or empty.is_dir()


# ---------------------------------------------------------------------------
# 崩溃日志、执行清理与 Society 装配
# ---------------------------------------------------------------------------


def test_ops_crash_log_records_and_never_masks_original_error(tmp_path):
    """崩溃日志写 RUN_CRASHED；自身故障只记日志不掩盖原始异常。"""
    prepared = _prepared_stub(tmp_path)
    society = SimpleNamespace(current_time=1767225600)
    _ops_crash_log(prepared, society, ValueError("original failure"))
    entry = json.loads(
        sorted((tmp_path / "logs").glob("ops-*.jsonl"))[0].read_text().splitlines()[0])
    assert entry["code"] == "RUN_CRASHED" and entry["level"] == "error"
    assert "original failure" in entry["detail"]

    broken = _prepared_stub(tmp_path / "no-matter",
                            inputs=SimpleNamespace(config=SimpleNamespace(timezone="Bogus/Zone")))
    with pytest.raises(ZoneInfoNotFoundError):
        ZoneInfo("Bogus/Zone")
    _ops_crash_log(broken, None, ValueError("swallowed original"))


@pytest.mark.asyncio
async def test_execute_requires_intervention_identity(monkeypatch, tmp_path):
    """干预已设置但身份缺失时拒绝进入运行。"""
    import worldmock.runtime as runtime
    from agentsociety2.config import llm_dispatcher

    monkeypatch.setattr(llm_dispatcher, "init_dispatchers", _no_dispatchers)
    async def build(prepared):
        return None, _Society()
    monkeypatch.setattr(runtime, "_build_society", build)
    with pytest.raises(RuntimeError, match="identity is unavailable"):
        await _execute(_prepared_stub(tmp_path, intervention=object(),
                                      intervention_sha256=None))
    entry = json.loads(
        sorted((tmp_path / "logs").glob("ops-*.jsonl"))[0].read_text().splitlines()[0])
    assert entry["code"] == "RUN_CRASHED" and entry["level"] == "error"


@pytest.mark.asyncio
async def test_execute_cleanup_failures_surface_without_run_failure(monkeypatch, tmp_path):
    """运行成功但清理失败：单项错误单独上抛，多项错误合并异常组。"""
    import ray

    import worldmock.runtime as runtime
    from agentsociety2.config import llm_dispatcher

    monkeypatch.setattr(llm_dispatcher, "init_dispatchers", _no_dispatchers)
    single = _Society(fail_close=OSError("close failed"))
    async def build(prepared):
        return None, single
    monkeypatch.setattr(runtime, "_build_society", build)
    monkeypatch.setattr(ray, "is_initialized", lambda: False)
    with pytest.raises(OSError, match="close failed"):
        await _execute(_prepared_stub(tmp_path))

    both = _Society(fail_close=OSError("close failed"))
    async def build(prepared):
        return None, both
    monkeypatch.setattr(runtime, "_build_society", build)
    calls = {"count": 0}

    def flip_initialized():
        calls["count"] += 1
        return calls["count"] > 1  # 启动时未初始化（owned_ray=True），清理时已初始化

    monkeypatch.setattr(ray, "is_initialized", flip_initialized)
    monkeypatch.setattr(ray, "shutdown", lambda: (_ for _ in ()).throw(RuntimeError("ray down")))
    with pytest.raises(BaseExceptionGroup) as caught:
        await _execute(_prepared_stub(tmp_path))
    assert [type(item) for item in caught.value.exceptions] == [OSError, RuntimeError]


@pytest.mark.asyncio
async def test_cleanup_runtime_releases_actor_and_owned_ray(monkeypatch):
    """清理路径释放 actor 与自有 Ray，并保留各项失败。"""
    import ray

    released = []
    monkeypatch.setattr(ray, "is_initialized", lambda: True)
    monkeypatch.setattr(ray, "kill",
                        lambda target, no_restart: released.append(("kill", target, no_restart)))
    monkeypatch.setattr(ray, "shutdown", lambda: released.append("shutdown"))
    errors = await _cleanup_runtime(None, "actor", True)
    assert errors == [] and released == [("kill", "actor", True), "shutdown"]

    released.clear()
    monkeypatch.setattr(ray, "kill",
                        lambda target, no_restart: (_ for _ in ()).throw(OSError("kill failed")))
    monkeypatch.setattr(ray, "shutdown", lambda: (_ for _ in ()).throw(OSError("down failed")))
    errors = await _cleanup_runtime(None, "actor", True)
    assert [type(item) for item in errors] == [OSError, OSError]
    assert released == []


def test_terminate_failed_actor_preserves_double_fault(monkeypatch):
    """actor 回滚失败时与原始异常合并为异常组。"""
    import ray

    cause = ValueError("society failed")
    monkeypatch.setattr(ray, "kill",
                        lambda target, no_restart: (_ for _ in ()).throw(OSError("kill boom")))
    with pytest.raises(BaseExceptionGroup) as caught:
        _terminate_failed_actor("actor", cause)
    assert [type(item) for item in caught.value.exceptions] == [ValueError, OSError]


def test_require_custom_modules_accepts_registered_life_modules(monkeypatch):
    """scanner 真实通过 LifeAgent 与 LifeEnv；错误结果拒绝。"""
    monkeypatch.setenv("WORKSPACE_PATH", str(ROOT))
    _require_custom_modules(ROOT)
    from agentsociety2.registry import scan_and_register_custom_modules

    original = scan_and_register_custom_modules
    monkeypatch.setattr("agentsociety2.registry.scan_and_register_custom_modules",
                        lambda root: {"errors": ["boom"], "agents": [], "envs": []})
    with pytest.raises(RuntimeError, match="registration failed"):
        _require_custom_modules(ROOT)
    monkeypatch.setattr("agentsociety2.registry.scan_and_register_custom_modules", original)




# ---------------------------------------------------------------------------
# 联系人回复客户端与请求结算
# ---------------------------------------------------------------------------




















# ---------------------------------------------------------------------------
# 零点计划客户端与后台任务（docs/design/04-simulation-engine.md 第四章「执行位置」）
# ---------------------------------------------------------------------------








# ---------------------------------------------------------------------------
# opslog 边界
# ---------------------------------------------------------------------------


def test_opslog_accepts_naive_datetime_and_reports_close_failures(tmp_path, capsys):
    """朴素 datetime 按 UTC 解释；关闭失败只记 stderr。"""
    from worldmock.opslog import OpsLogger

    writer = OpsLogger(tmp_path, "Asia/Shanghai")
    writer.log(datetime(2026, 1, 5, 16, 30), "runtime", "info", {"code": "PROBE_NAIVE", "detail": "probe"})
    day = "20260106"  # UTC 16:30+8 = 次日 00:30 上海
    writer._handles[day] = _FailingHandle()
    writer.close()
    output = capsys.readouterr().err
    assert "opslog close failed" in output
    logger = OpsLogger(tmp_path, "UTC")
    logger.log(datetime(2026, 1, 5, 1, 0), "runtime", "info", {"code": "PROBE_NAIVE_UTC", "detail": "probe"})
    logger.close()
    assert (tmp_path / "logs" / "ops-20260105.jsonl").is_file()


class _FailingHandle:
    """关闭即失败的句柄桩。"""

    def close(self):
        raise OSError("handle broken")


# ---------------------------------------------------------------------------
# status 只读视图
# ---------------------------------------------------------------------------


def test_write_status_rejects_unknown_status_and_phase(tmp_path):
    """状态写入只接受封闭集合。"""
    write_status(tmp_path, "created", "stopped")
    document = json.loads((tmp_path / "status.json").read_text())
    assert document == {"status": "created", "phase": "stopped", "error_type": None}
    with pytest.raises(IntegrityError, match="Unknown run status"):
        write_status(tmp_path, "exploded", "stopped")
    with pytest.raises(IntegrityError, match="Unknown run phase"):
        write_status(tmp_path, "running", "daydreaming")


def _status_store(tmp_path, agents_state=None, current_time=100):
    """建立带人物状态的只读视图数据库（以最终状态直接建库）。"""
    manifest = {"schema_version": 1, "run_id": "probe", "source_group_id": "probe",
                "agent_ids": [1], "model_identity": {"name": "fixture"},
                "window": {"start": 100, "end": 200, "timezone": "UTC"}}
    state = {"version": 0, "current_time": current_time, "end_time": 200,
             "activities": {}, "agents": agents_state or {}}
    store = FootprintStore.create(tmp_path / "run", manifest, state)
    return store, tmp_path / "run"


def test_read_status_requires_existing_database(tmp_path):
    """数据库缺失时拒绝查询。"""
    with pytest.raises(IntegrityError, match="database does not exist"):
        read_status(tmp_path)


def test_read_status_reports_empty_world_and_wall_time(tmp_path):
    """空事件世界：墙钟为空、人物与检查点为空列表。"""
    store, run_dir = _status_store(tmp_path)
    store.close()
    result = read_status(run_dir)
    assert result["run_id"] == "probe" and result["state_version"] == 0
    assert result["wall_time"] == {"started_at": None, "last_event_at": None,
                                   "elapsed_seconds": None}
    assert result["people"] == [] and result["recent_checkpoints"] == []
    assert result["observed_model_usage"] == []
    assert result["token_stats"] is None and result["runtime"] is None


def test_read_status_decision_boundaries(tmp_path):
    """决策边界：终态返回 None、非法持久时间拒绝、未来边界截到终点。"""
    agent = {"agent_id": 1, "location_id": "home",
             "next_decision_at": 100}
    store, run_dir = _status_store(tmp_path, {1: agent})
    store.close()
    result = read_status(run_dir)
    assert result["people"][0]["next_decision_at"] == 100

    finished, finished_dir = _status_store(
        tmp_path / "finished", {1: dict(agent, next_decision_at=250)}, current_time=200)
    finished.close()
    assert read_status(finished_dir)["people"][0]["next_decision_at"] is None

    invalid, invalid_dir = _status_store(
        tmp_path / "invalid", {1: dict(agent, next_decision_at=50)})
    invalid.close()
    with pytest.raises(IntegrityError, match="decision time is invalid"):
        read_status(invalid_dir)


# ---------------------------------------------------------------------------
# validation 拒绝分支
# ---------------------------------------------------------------------------












# ---------------------------------------------------------------------------
# cli 命令出口
# ---------------------------------------------------------------------------


def test_replay_reads_its_intervention_file_as_utf8(tmp_path, monkeypatch, caplog):
    """进程内经真实命令入口重放：带中文假设的干预文件按 UTF-8 读，期间没有依赖系统默认编码的文本读取；
    凭据未设置时环境加载失败，日志里是环境的拒绝原因。"""
    from tests.text_encoding import default_encoding_reads
    from worldmock.cli import main

    intervention = tmp_path / "intervention.json"
    intervention.write_text(json.dumps({"kind": "set_balance_fen", "agent_id": 1, "balance_fen": 100,
                                        "hypothesis": "余额变了，花钱的选择也会变。",
                                        "source_event_ids": ["observation-source"]}, ensure_ascii=False),
                            encoding="utf-8")
    monkeypatch.delenv("WORLDMOCK_TEST_API_KEY", raising=False)
    calls = default_encoding_reads(monkeypatch)
    with caplog.at_level(logging.ERROR):
        code = main(["replay", "--environment", str(ENVIRONMENT_ROOT), "--output", str(tmp_path / "child"),
                     "--parent", str(tmp_path / "parent"), "--checkpoint", "checkpoint",
                     "--intervention", str(intervention)])
    assert code == 1 and calls == []
    assert "model context metadata is unavailable" in caplog.text
    assert not (tmp_path / "child").exists()



















# ---------------------------------------------------------------------------
# 恢复发布前的严格工作区校验与状态墙钟
# ---------------------------------------------------------------------------










def test_read_status_reports_event_wall_time_and_runtime(tmp_path):
    """有事件的世界：墙钟由事件落盘时刻计算，status.json 决定终止时刻。"""
    manifest = {"schema_version": 1, "run_id": "probe", "source_group_id": "probe",
                "agent_ids": [1], "model_identity": {"name": "fixture"},
                "window": {"start": 100, "end": 200, "timezone": "UTC"}}
    store = FootprintStore.create(tmp_path / "run", manifest,
                                  {"version": 0, "current_time": 100, "end_time": 200,
                                   "activities": {}, "agents": {}})
    store.commit(store.state, [EventDraft(event_type="observation", occurred_at=100,
                                          agent_id=1, decision_id="d1",
                                          data={"balance_fen": 800})])
    store.close()
    write_status(tmp_path / "run", "completed", "stopped")
    result = read_status(tmp_path / "run")
    assert result["runtime"] == {"status": "completed", "phase": "stopped",
                                 "error_type": None}
    assert result["wall_time"]["started_at"] == result["wall_time"]["last_event_at"]
    assert result["wall_time"]["elapsed_seconds"] == 0.0
    write_status(tmp_path / "run", "running", "deciding")
    running = read_status(tmp_path / "run")
    assert running["wall_time"]["elapsed_seconds"] >= 0.0
    assert running["event_counts"] == {"observation": 1}
