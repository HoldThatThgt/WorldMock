"""runtime 运行壳、代码身份与执行装配测试。"""

from __future__ import annotations

from tests.data_paths import ENVIRONMENT_ROOT
from worldmock.recording_engine.storage.checkpoint import restore_branch

import copy
import json
import hashlib
import os
import subprocess
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import worldmock.runtime.lifecycle.runner as runtime_module
from tests.integration.runtime_fixtures import OfflineClient, new_run, write_agent_workspace, write_project
from worldmock.foundation.errors import IntegrityError
from worldmock.recording_engine.storage.checkpoint import snapshot_directory
from worldmock.foundation.serialization import canonical_json
from worldmock.recording_engine.storage.store import FootprintStore
from worldmock.state.decisions.results import CounterfactualIntervention
from worldmock.runtime.lifecycle.runner import (
    CounterfactualRequest,
    ResumeRequest,
    RunRequest,
    _execute,
    _intervention_sha256,
    _prepare_new_run,
    _require_run_shell,
    _restore_branch,
    replay_world,
    resume_world,
    run_world,
)
from worldmock.configuration.inputs.validation import validate_environment
from tests.integration.runtime.current_contract_fixtures import inputs, metadata_service

ROOT = Path(__file__).resolve().parents[3]
SCENARIO = ENVIRONMENT_ROOT


def test_runtime_code_identity_is_deterministic(monkeypatch, tmp_path) -> None:
    """同一临时工程根重复计算得到相同代码身份，是 64 位十六进制。"""
    from worldmock.runtime.framework import setup as run_setup

    project = write_project(tmp_path / "project")
    monkeypatch.setattr(run_setup, "project_root", lambda: project)

    first = run_setup.runtime_code_sha256()
    second = run_setup.runtime_code_sha256()

    assert len(first) == 64 and all(character in "0123456789abcdef" for character in first)
    assert first == second


def test_project_root_is_derived_from_the_imported_worldmock_package(monkeypatch, tmp_path) -> None:
    """项目根由已导入的 worldmock 包推出：换到空的临时目录启动，项目根仍是这份检出的根，代码身份不变。"""
    import worldmock
    from worldmock.runtime.framework.setup import project_root, runtime_code_sha256

    monkeypatch.chdir(ROOT)
    before = runtime_code_sha256()
    monkeypatch.chdir(tmp_path)
    assert project_root() == Path(worldmock.__file__).resolve().parents[2] == ROOT
    assert runtime_code_sha256() == before


def test_code_identity_covers_production_python_and_the_two_dependency_files_only(monkeypatch, tmp_path) -> None:
    """代码身份是项目根 src 下的全部 Python 源码加根目录的 pyproject.toml 与 uv.lock：改其中任何一份的字节、或在 src 里
    新增一份 Python 源码都改变 code_sha256；src 下其他类型的文件与 src 之外的目录都不进代码身份，引擎包里没有数据目录。"""
    from worldmock.runtime.framework import setup as run_setup

    project = write_project(tmp_path / "project")
    monkeypatch.setattr(run_setup, "project_root", lambda: project)
    seen = {run_setup.runtime_code_sha256()}
    for relative in ("src/worldmock/probe.py", "pyproject.toml", "uv.lock", "src/worldmock/life/added.py"):
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((path.read_bytes() if path.exists() else b"") + b"# changed\n")
        changed = run_setup.runtime_code_sha256()
        assert changed not in seen, relative
        seen.add(changed)
    for relative in ("src/worldmock/data/rules.toml", "src/worldmock/data/tables/extra.json", "src/worldmock/notes.md",
                     "custom/probe.py", "tests/test_probe.py", "scripts/probe.py", "tools/analysis/probe.py"):
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"limit = 1\n")
        assert run_setup.runtime_code_sha256() == changed, relative
    assert not (ROOT / "src/worldmock/data").exists()


def _hermetic_git(monkeypatch, tmp_path_factory) -> None:
    """本测试内的 git 只读一份空的全局配置、不读系统配置，并使用固定的提交身份。"""
    config = tmp_path_factory.mktemp("git-config") / "gitconfig"
    config.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "WorldMock Test")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "test@example.invalid")


def _git(repository: Path, *arguments: str) -> str:
    """在测试仓库执行一条 git 命令并返回去掉首尾空白的标准输出。"""
    result = subprocess.run(["git", "-C", str(repository), *arguments],
                            capture_output=True, text=True, check=True)
    return result.stdout.strip()


def _committed_project(root: Path) -> tuple[Path, str]:
    """建一个已提交的最小工程仓库（忽略 *.log），返回仓库目录与 HEAD 提交。"""
    write_project(root)
    (root / ".gitignore").write_text("*.log\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "fixture")
    return root, _git(root, "rev-parse", "HEAD")


def test_git_state_records_head_and_dirty_flag_of_the_checkout(monkeypatch, tmp_path_factory) -> None:
    """git_commit 取 HEAD；git_dirty 取 git status --porcelain 是否非空（含未跟踪文件、不含忽略文件）；
    项目根在检出的子目录中时同样记录该检出，非 Git 目录时二者为 null。"""
    from worldmock.runtime.framework.setup import _git_state

    _hermetic_git(monkeypatch, tmp_path_factory)
    repository, head = _committed_project(tmp_path_factory.mktemp("checkout"))
    assert _git_state(repository) == (head, False)
    assert _git_state(repository / "src") == (head, False)

    (repository / "debug.log").write_text("ignored\n", encoding="utf-8")
    assert _git_state(repository) == (head, False)
    _git(repository, "config", "status.showUntrackedFiles", "no")
    (repository / "notes.txt").write_text("untracked\n", encoding="utf-8")
    assert _git_state(repository) == (head, True)
    (repository / "notes.txt").unlink()
    (repository / "uv.lock").write_text("version = 2\n", encoding="utf-8")
    assert _git_state(repository) == (head, True)
    assert _git_state(repository / "src") == (head, True)

    assert _git_state(tmp_path_factory.mktemp("plain")) == (None, None)


def test_git_state_is_null_when_git_is_unavailable_or_status_fails(monkeypatch, tmp_path) -> None:
    """git 不可执行，或 HEAD 可读而 status 失败时，二者为 null，不阻断启动。"""
    from worldmock.runtime.framework.setup import _git_state
    import worldmock.runtime.framework.setup as worldmock_run_setup_module

    commands = []

    def missing_git(command, **kwargs):
        commands.append(command)
        raise FileNotFoundError("git")

    monkeypatch.setattr(worldmock_run_setup_module.subprocess, "run", missing_git)
    assert _git_state(tmp_path) == (None, None)

    def status_fails(command, **kwargs):
        commands.append(command)
        head = "rev-parse" in command
        return SimpleNamespace(returncode=0 if head else 128, stdout=b"a" * 40 + b"\n" if head else b"")

    monkeypatch.setattr(worldmock_run_setup_module.subprocess, "run", status_fails)
    assert _git_state(tmp_path) == (None, None)
    assert commands[-1][3:] == ["status", "--porcelain", "--untracked-files=normal"]


def _offline_day_plans(monkeypatch) -> None:
    """新建运行的初始检查点之后会补零点计划：零点计划的业务客户端换成确定性空清单桩，离线客户端因此收不到请求。"""
    from tests.life.conftest import StubPlanDayClient
    from worldmock.runtime.lifecycle import midnight

    monkeypatch.setattr(midnight, "PlanDayClient", lambda client, identity: StubPlanDayClient(identity))


async def test_run_manifest_records_git_commit_and_dirty_flag(monkeypatch, tmp_path, tmp_path_factory) -> None:
    """新建运行经 runtime、运行宿主与引擎写入清单：每个工程根由项目根替身指定；Git 检出记 HEAD 与未提交标志，
    非 Git 检出二者为 null；Git 状态不进入 code_sha256。"""
    from worldmock.runtime.framework import setup as run_setup

    metadata_service(monkeypatch)
    _offline_day_plans(monkeypatch)
    _hermetic_git(monkeypatch, tmp_path_factory)
    checkout, head = _committed_project(tmp_path_factory.mktemp("checkout"))
    (checkout / "notes.txt").write_text("uncommitted\n", encoding="utf-8")
    plain = write_project(tmp_path_factory.mktemp("plain"))

    manifests = []
    for project, run_dir in ((checkout, tmp_path / "git-run"), (plain, tmp_path / "plain-run")):
        monkeypatch.setattr(run_setup, "project_root", lambda project=project: project)
        prepared = _prepare_new_run(RunRequest(SCENARIO, run_dir, "identity", "identity"))
        host = await new_run(prepared, OfflineClient())
        try:
            manifests.append(host.engine.context.store.manifest)
        finally:
            await host.close()

    assert (manifests[0]["git_commit"], manifests[0]["git_dirty"]) == (head, True)
    assert {"git_commit", "git_dirty"} <= manifests[1].keys()
    assert (manifests[1]["git_commit"], manifests[1]["git_dirty"]) == (None, None)
    assert manifests[0]["code_sha256"] == manifests[1]["code_sha256"]


#: 执行装配用例里宿主替身给出的模拟时刻与步数。
_HOST_TIME = datetime(2026, 1, 5, 9, tzinfo=ZoneInfo("UTC"))
_HOST_STEPS = 3


def _host_stub(monkeypatch, *, start_error=None, run_error=None, close_error=None) -> list:
    """把运行宿主换成记录型替身：记下构造参数与每次调用（方法名与参数），可让启动、运行或关闭抛出给定异常；
    返回构造出的替身列表。"""
    from worldmock.runtime.lifecycle import run_host

    built: list = []

    class _Host:
        current_time = _HOST_TIME
        step_count = _HOST_STEPS
        engine = None

        def __init__(self, *arguments):
            self.arguments, self.calls = arguments, []
            built.append(self)

        async def _call(self, name, *arguments, error=None):
            self.calls.append((name, arguments))
            if error is not None:
                raise error

        async def start_new(self, *arguments):
            await self._call("start_new", *arguments, error=start_error)

        async def start_resume(self):
            await self._call("start_resume", error=start_error)

        async def start_counterfactual(self, *arguments):
            await self._call("start_counterfactual", *arguments, error=start_error)

        async def run(self):
            await self._call("run", error=run_error)

        async def close(self):
            await self._call("close", error=close_error)

    monkeypatch.setattr(run_host, "RunHost", _Host)
    return built


def _prepared(tmp_path, **overrides) -> SimpleNamespace:
    """_execute 读取的启动输入：缺省是一个普通恢复（没有运行身份、没有干预）。"""
    model = SimpleNamespace(api_key_env="WORLDMOCK_TEST_API_KEY")
    values = dict(run_id="run", run_dir=tmp_path, start_time=datetime(2026, 1, 5, tzinfo=ZoneInfo("UTC")),
                  inputs=SimpleNamespace(config=SimpleNamespace(timezone="UTC", model=model)),
                  stage_identity={"base_url": "https://example.invalid", "max_retries": 1,
                                  "transport_max_retries": 2},
                  agents={1: "frozen-inputs"}, memory_records={1: ()},
                  run_identity=None, intervention=None, intervention_sha256=None)
    values.update(overrides)
    return SimpleNamespace(**values)


def _offline_model_client(monkeypatch) -> object:
    """把运行装配构造模型客户端的唯一一处换成返回哨兵对象的函数，返回那个哨兵。"""
    sentinel = object()
    monkeypatch.setattr(runtime_module, "_model_client", lambda prepared: sentinel)
    return sentinel


@pytest.mark.asyncio
async def test_execute_builds_one_model_client_from_the_frozen_identity(monkeypatch, tmp_path) -> None:
    """_execute 恰构造一次模型客户端：关键字参数恰为端点、凭据与两类重试次数，取自冻结的阶段身份与配置声明的
    凭据变量；运行宿主收到的就是这一个对象。"""
    from worldmock.configuration.model import model_client

    constructed = []

    class _Client:
        def __init__(self, **options):
            constructed.append((self, options))

    monkeypatch.setattr(model_client, "ModelClient", _Client)
    monkeypatch.setenv("WORLDMOCK_TEST_API_KEY", "frozen-identity-secret")
    hosts = _host_stub(monkeypatch)
    prepared = _prepared(tmp_path)

    await _execute(prepared)

    ((client, options),) = constructed
    assert options == {"base_url": "https://example.invalid", "api_key": "frozen-identity-secret",
                       "max_retries": 1, "transport_max_retries": 2}
    (host,) = hosts
    assert host.arguments[2] is client


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["new", "resume", "counterfactual"])
async def test_execute_starts_each_run_kind_with_its_own_start(monkeypatch, tmp_path, kind) -> None:
    """有运行身份走新建、两者皆空走普通恢复、带干预走反事实；之后运行再关闭。"""
    hosts = _host_stub(monkeypatch)
    sentinel = _offline_model_client(monkeypatch)
    options = {"same_second_continuation": True}
    if kind == "new":
        monkeypatch.setattr(runtime_module, "_creation_options", lambda prepared: options)
    overrides = {"new": {"run_identity": object()}, "resume": {},
                 "counterfactual": {"intervention": object(), "intervention_sha256": "a" * 64}}[kind]
    prepared = _prepared(tmp_path, **overrides)

    await _execute(prepared)

    expected = {"new": ("start_new", (prepared.run_identity, prepared.stage_identity, options)),
                "resume": ("start_resume", ()),
                "counterfactual": ("start_counterfactual", (prepared.intervention, prepared.intervention_sha256))}
    (host,) = hosts
    assert host.calls == [expected[kind], ("run", ()), ("close", ())]
    assert host.arguments[2] is sentinel


@pytest.mark.asyncio
async def test_execute_applies_counterfactual_before_running(monkeypatch, tmp_path) -> None:
    """反事实分支先由宿主施加干预，只有成功才进入人物运行。"""
    hosts = _host_stub(monkeypatch)
    _offline_model_client(monkeypatch)
    intervention = CounterfactualIntervention(
        kind="set_balance_fen", agent_id=1, balance_fen=0,
        hypothesis="No money changes behavior.", source_event_ids=("event",),
    )
    digest = _intervention_sha256(intervention)
    prepared = _prepared(tmp_path, run_id="child", intervention=intervention, intervention_sha256=digest)

    await _execute(prepared)

    (host,) = hosts
    assert [name for name, _ in host.calls] == ["start_counterfactual", "run", "close"]
    assert host.calls[0][1] == (intervention, digest)


@pytest.mark.asyncio
async def test_execute_never_runs_after_counterfactual_failure(monkeypatch, tmp_path) -> None:
    """干预失败时关闭宿主且不退化为普通恢复运行。"""
    hosts = _host_stub(monkeypatch, start_error=RuntimeError("intervention failed"))
    _offline_model_client(monkeypatch)
    prepared = _prepared(tmp_path, run_id="child", intervention=object(), intervention_sha256="a" * 64)

    with pytest.raises(RuntimeError, match="intervention failed"):
        await _execute(prepared)

    (host,) = hosts
    assert [name for name, _ in host.calls] == ["start_counterfactual", "close"]


@pytest.mark.asyncio
async def test_execute_preserves_run_and_cleanup_failures(monkeypatch, tmp_path) -> None:
    """运行和关闭同时失败时两项根因都进入异常组。"""
    _host_stub(monkeypatch, run_error=ValueError("run failed"), close_error=OSError("close failed"))
    _offline_model_client(monkeypatch)

    with pytest.raises(BaseExceptionGroup) as caught:
        await _execute(_prepared(tmp_path))

    assert caught.value.message == "WorldMock run and cleanup failed"
    assert [type(item) for item in caught.value.exceptions] == [ValueError, OSError]
    assert [str(item) for item in caught.value.exceptions] == ["run failed", "close failed"]


@pytest.mark.asyncio
async def test_execute_logs_run_ended_with_the_host_clock_and_steps(monkeypatch, tmp_path) -> None:
    """RUN_ENDED 的模拟时刻与步数取自运行宿主，记录的键依次为 code、detail、run_id、steps；返回值同样取自宿主。"""
    _host_stub(monkeypatch)
    _offline_model_client(monkeypatch)

    result = await _execute(_prepared(tmp_path))

    entries = [json.loads(line) for path in sorted((tmp_path / "logs").glob("ops-*.jsonl"))
               for line in path.read_text(encoding="utf-8").splitlines()]
    (ended,) = [entry for entry in entries if entry["code"] == "RUN_ENDED"]
    assert ended["sim_time"] == _HOST_TIME.isoformat() and ended["steps"] == _HOST_STEPS
    assert [key for key in ended if key not in {"at", "sim_time", "component", "level"}] == [
        "code", "detail", "run_id", "steps"]
    assert (result.current_time, result.step_count) == (_HOST_TIME, _HOST_STEPS)
