"""@brief 干净的进程里照手册敲命令：在任何目录启动、不预设任何 AGENTSOCIETY_* 变量，run、resume、validate、status 都能起。

只把真正起 Ray 和调模型的那一步（worldmock.runtime.lifecycle.runner._execute）换成替身，其余走真实的命令入口、准入、进程配置与运行身份。
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib

import pytest

from tests.integration.runtime.current_contract_fixtures import aligned_process
from tests.integration.runtime_fixtures import write_agent_workspace
from tests.system.cli.test_cli import published_environment  # noqa: F401
from worldmock.configuration.inputs.environment import load_environment
from worldmock.recording_engine.storage.checkpoint import snapshot_directory
from worldmock.runtime.lifecycle.runner import RunRequest, _prepare_new_run, _society_specs
from worldmock.runtime.monitoring.status import read_status, write_status

ROOT = Path(__file__).resolve().parents[3]
#: 夹具 config.toml 的 model.api_key_env 指的变量与发布服务认的凭据。
API_KEY = ("WORLDMOCK_TEST_API_KEY", "test-only-key")
#: 进程配置会写、干净终端里没有的另外四个变量。
_PROCESS_ONLY = ("WORKSPACE_PATH", "LITELLM_LOCAL_MODEL_COST_MAP", "MEM0_TELEMETRY", "ANONYMIZED_TELEMETRY")
#: 入口：把 _execute 换成替身后调真实命令；替身向标准错误打一行证据，新建运行照环境自己的建库路径建好世界再关闭。
_STUB_ENTRY = """
import json, sys
import worldmock.runtime.lifecycle.runner as runtime
from worldmock.cli import main

async def execute(prepared):
    from agentsociety2.config.config import Config
    evidence = {"base_url": Config.LLM_API_BASE, "model": Config.LLM_MODEL,
                "life_agent": "custom.agents.life_agent" in sys.modules,
                "root_on_path": sys.path.count(str(prepared.project_root))}
    print("CLEAN-PROCESS " + json.dumps(evidence), file=sys.stderr)
    if prepared.workspace_mode == "create":
        from custom.envs.life_env import LifeEnv
        env = LifeEnv(**prepared.env_kwargs)
        await env.init(prepared.start_time)
        await env.close()
    return runtime.RunResult(prepared.run_id, prepared.run_dir, prepared.start_time, 0)

runtime._execute = execute
raise SystemExit(main())
"""
_PLAIN_ENTRY = "from worldmock.cli import main; raise SystemExit(main())"
#: 入口：照脚本路径运行仓库 scripts/ 下的一个脚本，第一个参数是脚本路径，其余原样交给它的命令行。
_SCRIPT_ENTRY = "import runpy, sys; runpy.run_path(sys.argv.pop(1), run_name='__main__')"
#: 干净进程里不该出现的四种启动故障。
_STARTUP_FAILURES = ("No module named 'custom'", "AGENTSOCIETY_LLM_API_KEY is required",
                     "imported before the required model configuration", "OUTPUT_TRUNCATED")
#: 让任何不写编码的文本读取都以 EncodingWarning 失败的解释器开关。
_ENCODING_FLAGS = ("-X", "warn_default_encoding", "-W", "error::EncodingWarning")


def _clean(entry, *arguments, cwd, flags=()):
    """在临时目录起一个干净的子进程：删掉全部 AGENTSOCIETY_ 变量与进程配置会写的四个变量，PYTHONPATH 只留 src。"""
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("AGENTSOCIETY_") and key not in _PROCESS_ONLY}
    # 只固定捕获管道的编码，文件读取仍由默认编码告警检查。
    environment.update({API_KEY[0]: API_KEY[1], "PYTHONPATH": str(ROOT / "src"), "PYTHONIOENCODING": "utf-8"})
    return subprocess.run([sys.executable, *flags, "-c", entry, *map(str, arguments)], cwd=cwd,
                          capture_output=True, text=True, encoding="utf-8", timeout=300, env=environment)


def _evidence(result):
    """替身打在标准错误里的那一行证据。"""
    [line] = [line for line in result.stderr.splitlines() if line.startswith("CLEAN-PROCESS ")]
    return json.loads(line.removeprefix("CLEAN-PROCESS "))


def _assert_started_cleanly(result, environment_root, output):
    """退出码 0，进程按 config.toml 的端点与模型配置，custom 已导入，项目根在 sys.path 里恰一次，回执与状态都写好。"""
    assert result.returncode == 0, result.stdout + result.stderr
    declared = tomllib.loads((environment_root / "config.toml").read_text(encoding="utf-8"))["model"]
    assert _evidence(result) == {"base_url": declared["base_url"], "model": declared["requested_model"],
                                 "life_agent": True, "root_on_path": 1}
    assert not [failure for failure in _STARTUP_FAILURES if failure in result.stderr]
    assert json.loads((output / "status.json").read_text(encoding="utf-8"))["status"] == "completed"
    return json.loads((output / "result.json").read_text(encoding="utf-8"))


async def _parent_with_checkpoint(environment_root, run_dir, monkeypatch):
    """在 pytest 进程里用同一份环境建好父运行并存一个共同检查点，返回检查点身份。"""
    from custom.envs.life_env import LifeEnv

    monkeypatch.setenv(*API_KEY)
    aligned_process(monkeypatch, load_environment(environment_root))
    prepared = _prepare_new_run(RunRequest(ROOT, environment_root, run_dir, "clean-parent", "clean-parent"))
    specs = _society_specs(prepared)
    env = LifeEnv(**prepared.env_kwargs)
    await env.init(prepared.start_time)
    try:
        workspace = write_agent_workspace(run_dir.parent / "workspace", agent_document={
            "schema_version": 1, "agent_class": "LifeAgent", "agent_id": 1, "initialized_at": None,
            "profile": specs[0]["profile"]}, config_document=specs[0]["config"])
        snapshot = snapshot_directory(workspace, env.engine.context.store.assets)
        return env.engine.checkpoint({"agent:1": snapshot}).checkpoint_id
    finally:
        await env.close()


def test_run_starts_from_any_directory_without_agentsociety_variables(published_environment, tmp_path):
    output = tmp_path / "run"
    result = _clean(_STUB_ENTRY, "run", "--environment", published_environment, "--output", output, cwd=tmp_path)
    receipt = _assert_started_cleanly(result, published_environment, output)
    assert receipt["token_stats"] == []


@pytest.mark.asyncio
async def test_resume_starts_from_any_directory_without_agentsociety_variables(
        published_environment, tmp_path, monkeypatch):
    parent = tmp_path / "parent"
    checkpoint = await _parent_with_checkpoint(published_environment, parent, monkeypatch)
    output = tmp_path / "child"
    result = _clean(_STUB_ENTRY, "resume", "--environment", published_environment, "--output", output,
                    "--parent", parent, "--checkpoint", checkpoint, cwd=tmp_path)
    receipt = _assert_started_cleanly(result, published_environment, output)
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
