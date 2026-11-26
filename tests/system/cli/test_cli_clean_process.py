"""@brief 干净的进程里照手册敲命令：在任何目录启动、不预设任何进程变量，run、resume、validate、status 都能起。

只把真正跑决策循环、调模型的那一步（worldmock.runtime.lifecycle.runner._execute）与新建运行初始检查点之后补零点计划的
业务客户端（worldmock.runtime.lifecycle.midnight.PlanDayClient）换成替身，其余走真实的命令入口、准入、凭据与运行身份。
"""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.integration.runtime_fixtures import OfflineClient, new_run
from tests.life.conftest import StubPlanDayClient
from tests.system.cli.test_cli import published_environment  # noqa: F401
from tests.unit.engineering.test_framework_removal import REMOVED_PROCESS_VARIABLES
from worldmock.agents.memory.workspace import snapshot_workspaces
from worldmock.runtime.lifecycle import midnight
from worldmock.runtime.lifecycle.runner import RunRequest, _prepare_new_run
from worldmock.runtime.monitoring.status import read_status, write_status

ROOT = Path(__file__).resolve().parents[3]
#: 夹具 config.toml 的 model.api_key_env 指的变量与发布服务认的凭据。
API_KEY = ("WORLDMOCK_TEST_API_KEY", "test-only-key")
#: 入口：把 _execute 换成替身后调真实命令；替身向标准错误打一行证据，新建运行经运行宿主建好世界再关闭。
#: 运行宿主在初始检查点之后补零点计划，子进程里零点计划的业务客户端换成返回空计划的桩（同 tests.life.conftest 的
#: StubPlanDayClient，用量固定为 30/9/39），发布服务因此只需答模型元数据。
#: 名字表 __NAMES__ 在测试运行时由 repr(REMOVED_PROCESS_VARIABLES) 填入。
_STUB_ENTRY = """
import json, os, sys
import worldmock.runtime.lifecycle.runner as runtime
from worldmock.cli import main
from worldmock.runtime.framework.setup import project_root

NAMES = __NAMES__

class PlanDay:
    def __init__(self, client, identity):
        self.model_name = identity["requested_model"]

    async def call(self, request):
        call = {"id": "call-plan-day", "type": "function",
                "function": {"name": "submit_day_plan", "arguments": json.dumps({"goals": []})}}
        return {"model": self.model_name,
                "choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [call]}}],
                "usage": {"prompt_tokens": 30, "completion_tokens": 9, "total_tokens": 39}}

async def execute(prepared):
    evidence = {"written": sorted(name for name in NAMES if name in os.environ),
                "root_on_path": sys.path.count(str(project_root())),
                "pythonpath": os.environ.get("PYTHONPATH"), "project_root": str(project_root())}
    print("CLEAN-PROCESS " + json.dumps(evidence), file=sys.stderr)
    if prepared.run_identity is not None:
        from worldmock.runtime.lifecycle import midnight
        from worldmock.runtime.lifecycle.run_host import RunHost
        midnight.PlanDayClient = PlanDay
        host = RunHost(prepared.run_dir, prepared.inputs, runtime._model_registry(prepared), prepared.agents,
                       prepared.memory_records)
        try:
            await host.start_new(prepared.run_identity, runtime._creation_options(prepared))
        finally:
            await host.close()
    return runtime.RunResult(prepared.run_id, prepared.run_dir, prepared.start_time, 0)

runtime._execute = execute
raise SystemExit(main())
"""
_PLAIN_ENTRY = "from worldmock.cli import main; raise SystemExit(main())"
#: 入口：照脚本路径运行仓库 scripts/ 下的一个脚本，第一个参数是脚本路径，其余原样交给它的命令行。
_SCRIPT_ENTRY = "import runpy, sys; runpy.run_path(sys.argv.pop(1), run_name='__main__')"
#: 干净进程里不该出现的两种启动故障。
_STARTUP_FAILURES = ("No module named", "OUTPUT_TRUNCATED")
#: 让任何不写编码的文本读取都以 EncodingWarning 失败的解释器开关。
_ENCODING_FLAGS = ("-X", "warn_default_encoding", "-W", "error::EncodingWarning")


def _stub_entry():
    """填好退役进程变量名字表的替身入口源码。"""
    return _STUB_ENTRY.replace("__NAMES__", repr(REMOVED_PROCESS_VARIABLES))


def _clean(entry, *arguments, cwd, flags=()):
    """在临时目录起一个干净的子进程：删掉 11 个退役进程变量与本地价目表变量，PYTHONPATH 只留 src。"""
    removed = {*REMOVED_PROCESS_VARIABLES, "LITELLM_LOCAL_MODEL_COST_MAP"}
    environment = {key: value for key, value in os.environ.items() if key not in removed}
    # 只固定捕获管道的编码，文件读取仍由默认编码告警检查。
    environment.update({API_KEY[0]: API_KEY[1], "PYTHONPATH": str(ROOT / "src"), "PYTHONIOENCODING": "utf-8"})
    return subprocess.run([sys.executable, *flags, "-c", entry, *map(str, arguments)], cwd=cwd,
                          capture_output=True, text=True, encoding="utf-8", timeout=300, env=environment)


def _evidence(result):
    """替身打在标准错误里的那一行证据。"""
    [line] = [line for line in result.stderr.splitlines() if line.startswith("CLEAN-PROCESS ")]
    return json.loads(line.removeprefix("CLEAN-PROCESS "))


def _assert_started_cleanly(result, output):
    """退出码 0；进程里没有写出任何退役变量，项目根不在 sys.path 里，PYTHONPATH 原样，项目根由导入的包推出；回执与状态都写好。"""
    assert result.returncode == 0, result.stdout + result.stderr
    assert _evidence(result) == {"written": [], "root_on_path": 0, "pythonpath": str(ROOT / "src"),
                                 "project_root": str(ROOT)}
    assert not [failure for failure in _STARTUP_FAILURES if failure in result.stderr]
    assert json.loads((output / "status.json").read_text(encoding="utf-8"))["status"] == "completed"
    return json.loads((output / "result.json").read_text(encoding="utf-8"))


async def _parent_with_checkpoint(environment_root, run_dir, monkeypatch):
    """在 pytest 进程里用同一份环境经运行宿主建好父运行并存一个共同检查点，返回检查点身份；初始检查点之后补的零点计划
    由确定性空清单桩作答，离线客户端因此收不到请求。"""
    monkeypatch.setenv(*API_KEY)
    monkeypatch.setattr(midnight, "PlanDayClient", lambda client, identity: StubPlanDayClient(identity))
    prepared = _prepare_new_run(RunRequest(environment_root, run_dir, "clean-parent", "clean-parent"))
    host = await new_run(prepared, OfflineClient())
    try:
        snapshots = snapshot_workspaces(prepared.run_dir, (1,), host.engine.context.store.assets)
        return host.engine.checkpoint(snapshots).checkpoint_id
    finally:
        await host.close()


def test_run_starts_from_any_directory_without_preset_variables(published_environment, tmp_path):
    output = tmp_path / "run"
    result = _clean(_stub_entry(), "run", "--environment", published_environment, "--output", output, cwd=tmp_path)
    receipt = _assert_started_cleanly(result, output)
    # 回执用量由已落盘响应累计，含初始检查点之后补的那一次零点计划（替身桩的固定用量）。
    assert receipt["token_stats"] == [{"model": "test-model", "responses": 1, "responses_with_usage": 1,
                                       "input_tokens": 30, "output_tokens": 9, "total_tokens": 39,
                                       "cached_input_tokens": None, "uncached_input_tokens": None,
                                       "cache_hit_ratio": None}]


@pytest.mark.asyncio
async def test_resume_starts_from_any_directory_without_preset_variables(
        published_environment, tmp_path, monkeypatch):
    parent = tmp_path / "parent"
    checkpoint = await _parent_with_checkpoint(published_environment, parent, monkeypatch)
    output = tmp_path / "child"
    result = _clean(_stub_entry(), "resume", "--environment", published_environment, "--output", output,
                    "--parent", parent, "--checkpoint", checkpoint, cwd=tmp_path)
    receipt = _assert_started_cleanly(result, output)
    assert receipt["token_stats"] == read_status(output)["observed_model_usage"]


def test_validate_is_unaffected_in_a_clean_process(published_environment, tmp_path):
    result = _clean(_PLAIN_ENTRY, "validate", "--environment", published_environment, cwd=tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["valid"] is True


@pytest.mark.asyncio
async def test_status_reads_a_run_directory_with_a_chinese_manifest_without_the_default_encoding(
        published_environment, tmp_path, monkeypatch):
    parent = tmp_path / "parent"
    await _parent_with_checkpoint(published_environment, parent, monkeypatch)
    assert any(byte > 127 for byte in (parent / "manifest.json").read_bytes())
    result = _clean(_PLAIN_ENTRY, "status", "--run-dir", parent, cwd=tmp_path, flags=_ENCODING_FLAGS)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["run_id"] == "clean-parent"


@pytest.mark.asyncio
async def test_inspect_run_summary_reads_a_run_directory_without_the_default_encoding(
        published_environment, tmp_path, monkeypatch):
    """同一开关下 scripts/inspect_run.py <运行目录> summary 退出码 0：清单与状态两份 JSON 都按 UTF-8 读。"""
    parent = tmp_path / "parent"
    await _parent_with_checkpoint(published_environment, parent, monkeypatch)
    write_status(parent, "running", "deciding")
    assert any(byte > 127 for byte in (parent / "manifest.json").read_bytes())
    result = _clean(_SCRIPT_ENTRY, ROOT / "scripts" / "inspect_run.py", parent, "summary", cwd=tmp_path,
                    flags=_ENCODING_FLAGS)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.splitlines()[:2] == ["run_id     : clean-parent", "状态       : running (deciding)"]


def test_replay_reads_its_intervention_file_without_the_default_encoding(published_environment, tmp_path):
    intervention = tmp_path / "intervention.json"
    intervention.write_text(json.dumps({"kind": "set_balance_fen", "agent_id": 1, "balance_fen": 100,
                                        "hypothesis": "余额变了，花钱的选择也会变。",
                                        "source_event_ids": ["observation-source"]}, ensure_ascii=False),
                            encoding="utf-8")
    result = _clean(_PLAIN_ENTRY, "replay", "--environment", published_environment, "--output", tmp_path / "child",
                    "--parent", tmp_path / "missing-parent", "--checkpoint", "checkpoint",
                    "--intervention", intervention, cwd=tmp_path, flags=_ENCODING_FLAGS)
    assert "Footprint database does not exist" in result.stderr
    assert "EncodingWarning" not in result.stderr
