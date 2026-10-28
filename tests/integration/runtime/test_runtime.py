"""runtime 运行壳、代码身份与共同恢复测试。"""

from __future__ import annotations

from tests.data_paths import ENVIRONMENT_ROOT
from worldmock.recording_engine.storage.checkpoint import restore_branch

import copy
import importlib.util
import json
import hashlib
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com/beta")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import worldmock.runtime.lifecycle.runner as runtime_module
from tests.integration.runtime_fixtures import write_agent_workspace
from tests.integration.as2_fixtures import (
    FAKE_AS2_FILES,
    expected_as2_sha256,
    use_fake_as2,
    write_fake_as2,
    write_project,
)
from worldmock.foundation.errors import IntegrityError
from worldmock.recording_engine.storage.checkpoint import snapshot_directory
from worldmock.foundation.serialization import canonical_json
from worldmock.recording_engine.storage.store import FootprintStore
from worldmock.state.decisions.results import CounterfactualIntervention
from worldmock.runtime.lifecycle.runner import (
    CounterfactualRequest,
    ResumeRequest,
    RunRequest,
    _build_society,
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
from tests.integration.runtime.current_contract_fixtures import aligned_process, inputs, metadata_service

ROOT = Path(__file__).resolve().parents[3]
SCENARIO = ENVIRONMENT_ROOT
#: 规格第八章「运行身份」规定的 AS2 身份拒绝文案。
_AS2_REFUSAL = "AS2 runtime identity differs from the locked source"














def test_imported_as2_matches_rebuildable_locked_source() -> None:
    """实际导入的 AS2 与固定基线加补丁重建的唯一源码身份一致。"""
    from worldmock.runtime.framework.setup import (
        AS2_LOCKED_SOURCE_SHA256,
        _git_state,
        _model_identity,
        require_locked_as2,
        runtime_code_sha256,
        stage_model_identity,
    )

    files = require_locked_as2()

    assert files and all(label.startswith("agentsociety2/") for label, _ in files)
    assert "agentsociety2/__init__.py" in {label for label, _ in files}


def test_runtime_code_identity_is_deterministic(monkeypatch, tmp_path, tmp_path_factory) -> None:
    """同一工程与同一 AS2 包重复计算得到相同代码身份（AS2 取临时包，不依赖本机安装）。"""
    from worldmock.runtime.framework.setup import (
        AS2_LOCKED_SOURCE_SHA256,
        _git_state,
        _model_identity,
        require_locked_as2,
        runtime_code_sha256,
        stage_model_identity,
    )

    project = write_project(tmp_path / "project")
    use_fake_as2(monkeypatch, tmp_path_factory.mktemp("as2-site"))

    first = runtime_code_sha256(project)
    second = runtime_code_sha256(project)

    assert len(first) == 64
    assert first == second


def test_code_identity_covers_production_python_and_the_two_dependency_files_only(
        monkeypatch, tmp_path, tmp_path_factory) -> None:
    """引擎自己的代码身份是 src 与 custom 下的全部 Python 源码加根目录的 pyproject.toml 与 uv.lock：改其中任何一份的字节、
    或在任一目录里新增一份 Python 源码都改变 code_sha256；src 下其他类型的文件不进代码身份，引擎包里没有数据目录。"""
    from worldmock.runtime.framework.setup import runtime_code_sha256

    project = write_project(tmp_path / "project")
    use_fake_as2(monkeypatch, tmp_path_factory.mktemp("as2-site"))
    seen = {runtime_code_sha256(project)}
    for relative in ("src/worldmock/probe.py", "custom/agents/life_agent.py", "pyproject.toml", "uv.lock",
                     "src/worldmock/life/added.py", "custom/envs/added.py"):
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((path.read_bytes() if path.exists() else b"") + b"# changed\n")
        changed = runtime_code_sha256(project)
        assert changed not in seen, relative
        seen.add(changed)
    for relative in ("src/worldmock/data/rules.toml", "src/worldmock/data/tables/extra.json", "src/worldmock/notes.md",
                     "custom/agents/prompt.txt"):
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"limit = 1\n")
        assert runtime_code_sha256(project) == changed, relative
    assert not (ROOT / "src/worldmock/data").exists()


def test_code_identity_covers_every_imported_as2_file_except_pycache(
        monkeypatch, tmp_path, tmp_path_factory) -> None:
    """AS2 包内任一文件（含非 Python 文件与新增文件）字节变化都改变 code_sha256，__pycache__ 不计入；
    未改锁定摘要时字节变化即按摘要不符拒绝。"""
    from worldmock.runtime.framework.setup import (
        AS2_LOCKED_SOURCE_SHA256,
        _git_state,
        _model_identity,
        require_locked_as2,
        runtime_code_sha256,
        stage_model_identity,
    )
    import worldmock.runtime.framework.setup as worldmock_run_setup_module

    project = write_project(tmp_path / "project")
    package = use_fake_as2(monkeypatch, tmp_path_factory.mktemp("as2-site"))
    seen = {runtime_code_sha256(project)}

    (package / "__pycache__" / "__init__.cpython-312.pyc").write_bytes(b"\x00recompiled")
    (package / "__pycache__" / "extra.cpython-312.pyc").write_bytes(b"\x00new")
    assert runtime_code_sha256(project) in seen

    for relative in ("society/society.py", "agent/skills/guide/SKILL.md", "data/added.json"):
        path = package / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((path.read_bytes() if path.exists() else b"") + b"#")
        with pytest.raises(IntegrityError, match=_AS2_REFUSAL):
            runtime_code_sha256(project)
        monkeypatch.setattr(worldmock_run_setup_module, 'AS2_LOCKED_SOURCE_SHA256', expected_as2_sha256(package))
        changed = runtime_code_sha256(project)
        assert changed not in seen
        seen.add(changed)


def test_as2_identity_is_independent_of_install_location(monkeypatch, tmp_path, tmp_path_factory) -> None:
    """同一份 AS2 源码装在不同位置：find_spec 解析到哪个目录都得到相同标签、通过同一锁定摘要、code_sha256 相同。"""
    from worldmock.runtime.framework.setup import (
        AS2_LOCKED_SOURCE_SHA256,
        _git_state,
        _model_identity,
        require_locked_as2,
        runtime_code_sha256,
        stage_model_identity,
    )
    import worldmock.runtime.framework.setup as worldmock_run_setup_module

    project = write_project(tmp_path / "project")
    name = f"worldmock_fake_as2_{uuid4().hex}"
    first = write_fake_as2(tmp_path_factory.mktemp("site-a"), name)
    second = write_fake_as2(tmp_path_factory.mktemp("site-b") / "deeper" / "install", name)
    monkeypatch.setattr(worldmock_run_setup_module, '_AS2_PACKAGE', name)
    monkeypatch.setattr(worldmock_run_setup_module, 'AS2_LOCKED_SOURCE_SHA256', expected_as2_sha256(first))

    identities = []
    for package in (first, second):
        monkeypatch.syspath_prepend(str(package.parent))
        assert Path(importlib.util.find_spec(name).origin).resolve().parent == package.resolve()
        labels = sorted(label for label, _ in require_locked_as2())
        identities.append((labels, runtime_code_sha256(project)))

    assert identities[0] == identities[1]
    assert identities[0][0] == sorted(
        f"agentsociety2/{relative}" for relative in FAKE_AS2_FILES if "__pycache__" not in relative)


def _break_as2(condition: str, monkeypatch, site: Path) -> None:
    """把运行身份解析到的 AS2 置为缺失（导入失败）、为空或与锁定摘要不符。"""
    import worldmock.runtime.framework.setup as worldmock_run_setup_module

    if condition == "missing":
        monkeypatch.setattr(worldmock_run_setup_module, '_AS2_PACKAGE', f'worldmock_absent_as2_{uuid4().hex}')
    elif condition == "empty":
        use_fake_as2(monkeypatch, site, {"__pycache__/stale.cpython-312.pyc": b"\x00"}, locked=False)
    else:
        use_fake_as2(monkeypatch, site, locked=False)


@pytest.mark.parametrize("condition", ["missing", "empty", "mismatch"])
def test_unlocked_as2_refuses_current_runtime_identity(condition, monkeypatch, tmp_path, tmp_path_factory):
    """当前运行代码身份必须通过完整 AS2 源码校验。"""
    from worldmock.runtime.framework.setup import runtime_code_sha256
    _break_as2(condition, monkeypatch, tmp_path_factory.mktemp("as2-site"))
    with pytest.raises(IntegrityError, match=_AS2_REFUSAL):
        runtime_code_sha256(ROOT)
    assert not (tmp_path / "run").exists()


@pytest.mark.parametrize("variant", ["blocked", "no_spec", "not_a_package"])
def test_as2_lookup_failures_are_identity_refusals(variant, monkeypatch, tmp_path, tmp_path_factory) -> None:
    """导入被阻断、已导入对象没有 spec、同名对象不是包：都按 AS2 身份不符拒绝，也不给出代码身份。"""
    from worldmock.runtime.framework.setup import (
        AS2_LOCKED_SOURCE_SHA256,
        _git_state,
        _model_identity,
        require_locked_as2,
        runtime_code_sha256,
        stage_model_identity,
    )
    import worldmock.runtime.framework.setup as worldmock_run_setup_module

    project = write_project(tmp_path / "project")
    name = f"worldmock_fake_as2_{uuid4().hex}"
    monkeypatch.setattr(worldmock_run_setup_module, '_AS2_PACKAGE', name)
    if variant == "blocked":
        monkeypatch.setitem(sys.modules, name, None)
    elif variant == "no_spec":
        monkeypatch.setitem(sys.modules, name, ModuleType(name))
    else:
        site = tmp_path_factory.mktemp("as2-site")
        (site / f"{name}.py").write_text("VALUE = 1\n", encoding="utf-8")
        monkeypatch.syspath_prepend(str(site))

    with pytest.raises(IntegrityError, match=_AS2_REFUSAL):
        require_locked_as2()
    with pytest.raises(IntegrityError, match=_AS2_REFUSAL):
        runtime_code_sha256(project)


@pytest.mark.parametrize("failure", ["vanished", "unreadable"])
def test_as2_file_read_failure_is_an_identity_refusal(failure, monkeypatch, tmp_path) -> None:
    """核对时 AS2 文件已消失或不可读：按 AS2 身份不符拒绝，只用规格的唯一拒绝文案。"""
    from worldmock.runtime.framework.setup import (
        AS2_LOCKED_SOURCE_SHA256,
        _git_state,
        _model_identity,
        require_locked_as2,
        runtime_code_sha256,
        stage_model_identity,
    )
    import worldmock.runtime.framework.setup as worldmock_run_setup_module

    path = tmp_path / "agentsociety2" / "society.py"
    if failure == "unreadable":
        path.parent.mkdir()
        path.write_bytes(b"def step():\n    return 1\n")
        original = Path.read_bytes

        def deny(self):
            if self == path:
                raise PermissionError(13, "Permission denied", str(self))
            return original(self)

        monkeypatch.setattr(Path, "read_bytes", deny)
    monkeypatch.setattr(worldmock_run_setup_module, '_as2_source_files', lambda: [('agentsociety2/society.py', path)])

    with pytest.raises(IntegrityError, match=f"^{_AS2_REFUSAL}$") as caught:
        require_locked_as2()

    assert isinstance(caught.value.__cause__, IntegrityError if failure == "vanished" else PermissionError)


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
    from worldmock.runtime.framework.setup import (
        AS2_LOCKED_SOURCE_SHA256,
        _git_state,
        _model_identity,
        require_locked_as2,
        runtime_code_sha256,
        stage_model_identity,
    )

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
    from worldmock.runtime.framework.setup import (
        AS2_LOCKED_SOURCE_SHA256,
        _git_state,
        _model_identity,
        require_locked_as2,
        runtime_code_sha256,
        stage_model_identity,
    )
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


async def test_run_manifest_records_git_commit_and_dirty_flag(monkeypatch, tmp_path, tmp_path_factory) -> None:
    """新建运行经 runtime、LifeEnv 与引擎写入清单：Git 检出记 HEAD 与未提交标志，非 Git 检出二者为 null；
    Git 状态不进入 code_sha256。"""
    from custom.envs.life_env import LifeEnv

    metadata_service(monkeypatch)
    aligned_process(monkeypatch, inputs())
    use_fake_as2(monkeypatch, tmp_path_factory.mktemp("as2-site"))
    _hermetic_git(monkeypatch, tmp_path_factory)
    checkout, head = _committed_project(tmp_path_factory.mktemp("checkout"))
    (checkout / "notes.txt").write_text("uncommitted\n", encoding="utf-8")
    plain = write_project(tmp_path_factory.mktemp("plain"))

    manifests = []
    for project, run_dir in ((checkout, tmp_path / "git-run"), (plain, tmp_path / "plain-run")):
        prepared = _prepare_new_run(RunRequest(project, SCENARIO, run_dir, "identity", "identity"))
        runtime_module._society_specs(prepared)
        env = LifeEnv(**prepared.env_kwargs)
        await env.init(prepared.start_time)
        try:
            manifests.append(env.engine.context.store.manifest)
        finally:
            await env.close()

    assert (manifests[0]["git_commit"], manifests[0]["git_dirty"]) == (head, True)
    assert {"git_commit", "git_dirty"} <= manifests[1].keys()
    assert (manifests[1]["git_commit"], manifests[1]["git_dirty"]) == (None, None)
    assert manifests[0]["code_sha256"] == manifests[1]["code_sha256"]










@pytest.mark.asyncio
async def test_execute_applies_counterfactual_before_running(monkeypatch, tmp_path) -> None:
    """runtime 初始化后先提交干预，只有成功才进入人物运行。"""
    calls = []

    class Society:
        current_time = None
        step_count = 0

        async def init(self):
            calls.append("init")

        async def apply_counterfactual(self, intervention, digest):
            calls.append(("intervention", intervention, digest))

        async def run(self):
            calls.append("run")

        async def close(self):
            calls.append("close")

    async def init_dispatchers():
        return None

    from agentsociety2.config import llm_dispatcher
    import worldmock.runtime.lifecycle.runner as runtime

    monkeypatch.setattr(llm_dispatcher, "init_dispatchers", init_dispatchers)
    async def build(prepared):
        return None, Society()

    monkeypatch.setattr(runtime, "_build_society", build)
    intervention = CounterfactualIntervention(
        kind="set_balance_fen", agent_id=1, balance_fen=0,
        hypothesis="No money changes behavior.", source_event_ids=("event",),
    )
    digest = _intervention_sha256(intervention)
    prepared = SimpleNamespace(
        run_id="child", run_dir=tmp_path, intervention=intervention,
        intervention_sha256=digest, inputs=SimpleNamespace(config=SimpleNamespace(timezone="UTC")),
        start_time=datetime(2026, 1, 5, tzinfo=ZoneInfo("UTC")),
    )

    await _execute(prepared)

    assert calls == ["init", ("intervention", intervention, digest), "run", "close"]


@pytest.mark.asyncio
async def test_execute_never_runs_after_counterfactual_failure(monkeypatch, tmp_path) -> None:
    """干预失败时关闭 Society 且不退化为普通恢复运行。"""
    calls = []

    class Society:
        async def init(self):
            calls.append("init")

        async def apply_counterfactual(self, intervention, digest):
            calls.append("intervention")
            raise RuntimeError("intervention failed")

        async def run(self):
            calls.append("run")

        async def close(self):
            calls.append("close")

    async def init_dispatchers():
        return None

    from agentsociety2.config import llm_dispatcher
    import worldmock.runtime.lifecycle.runner as runtime

    monkeypatch.setattr(llm_dispatcher, "init_dispatchers", init_dispatchers)
    async def build(prepared):
        return None, Society()

    monkeypatch.setattr(runtime, "_build_society", build)
    prepared = SimpleNamespace(
        run_id="child", run_dir=tmp_path, intervention=object(),
        intervention_sha256="a" * 64,
    )

    with pytest.raises(RuntimeError, match="intervention failed"):
        await _execute(prepared)

    assert calls == ["init", "intervention", "close"]


@pytest.mark.asyncio
async def test_execute_preserves_run_and_cleanup_failures(monkeypatch, tmp_path) -> None:
    """运行和关闭同时失败时两项根因都进入异常组。"""
    class Society:
        async def init(self):
            return None

        async def run(self):
            raise ValueError("run failed")

        async def close(self):
            raise OSError("close failed")

    async def init_dispatchers():
        return None

    from agentsociety2.config import llm_dispatcher
    import worldmock.runtime.lifecycle.runner as runtime

    monkeypatch.setattr(llm_dispatcher, "init_dispatchers", init_dispatchers)
    async def build(prepared):
        return None, Society()

    monkeypatch.setattr(runtime, "_build_society", build)
    prepared = SimpleNamespace(
        run_id="run", run_dir=tmp_path, intervention=None, intervention_sha256=None,
    )

    with pytest.raises(BaseExceptionGroup) as caught:
        await _execute(prepared)

    assert [type(item) for item in caught.value.exceptions] == [ValueError, OSError]
    assert [str(item) for item in caught.value.exceptions] == ["run failed", "close failed"]
