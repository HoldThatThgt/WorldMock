"""runtime 运行壳、代码身份与共同恢复测试。"""

from __future__ import annotations

from tests.data_paths import ENVIRONMENT_ROOT
from worldmock.footprints.checkpoint import restore_branch

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

import worldmock.runtime as runtime_module
from tests.integration.as2_fixtures import (
    FAKE_AS2_FILES,
    expected_as2_sha256,
    use_fake_as2,
    write_fake_as2,
    write_project,
)
from worldmock.errors import IntegrityError
from worldmock.footprints.checkpoint import snapshot_directory
from worldmock.footprints.models import canonical_json
from worldmock.footprints.store import FootprintStore
from worldmock.life.models import CounterfactualIntervention
from worldmock.runtime import CounterfactualRequest, ResumeRequest, RunRequest, _build_society, _execute, _intervention_sha256, _no_persona_compilation, _prepare_new_run, _require_run_shell, _restore_branch, replay_world, resume_world, run_world
from worldmock.validation import validate_project

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ENVIRONMENT_ROOT / "scenarios" / "single-person.json"
#: 规格第八章「运行身份」规定的 AS2 身份拒绝文案。
_AS2_REFUSAL = "AS2 runtime identity differs from the locked source"


def test_model_identity_freezes_complete_strict_endpoint_and_request() -> None:
    """运行身份记录完整节点 URL 和唯一 strict 请求参数。"""
    from worldmock.run_setup import AS2_LOCKED_SOURCE_SHA256, _agent_specs, _git_state, _model_identity, require_locked_as2, runtime_code_sha256, stage_model_identity

    identity = _model_identity().model_dump(mode="json")

    assert identity["base_url"] == "https://api.deepseek.com/beta"
    assert identity["provider"] == "openai_compatible"
    assert identity["requested_model"] == "deepseek-flash"
    assert identity["tool_choice"] == "auto"
    assert identity["thinking"] == "enabled"
    assert identity["max_retries"] == 0
    assert "base_url_host" not in identity


def test_model_identity_top_level_tracks_life_agent_action_constants() -> None:
    """顶层 thinking/tool_choice 与 B2 导出的行动阶段常量同源。"""
    from worldmock.run_setup import AS2_LOCKED_SOURCE_SHA256, _agent_specs, _git_state, _model_identity, require_locked_as2, runtime_code_sha256, stage_model_identity

    from custom.agents.life_agent import ACTION_THINKING, ACTION_TOOL_CHOICE

    identity = _model_identity()

    assert identity.thinking == ACTION_THINKING["thinking"]["type"]
    assert identity.tool_choice == ACTION_TOOL_CHOICE


def test_stage_model_identity_shape_matches_life_agent_constants() -> None:
    """阶段身份只有行动与工作验收两个阶段，行动参数与 LifeAgent 常量单一事实源一致。"""
    from worldmock.run_setup import AS2_LOCKED_SOURCE_SHA256, _agent_specs, _git_state, _model_identity, require_locked_as2, runtime_code_sha256, stage_model_identity

    from custom.agents.life_agent import (
        ACTION_THINKING,
        ACTION_TOOL_CHOICE,
        ACTION_MAX_TOKENS,
    )

    identity = stage_model_identity()
    stages = identity["stages"]

    assert set(stages) == {"action", "work_review"}
    assert stages["action"] == {"thinking": ACTION_THINKING["thinking"]["type"],
                                "tool_choice": ACTION_TOOL_CHOICE, "max_tokens": ACTION_MAX_TOKENS,
                                "same_second_continuation": True}
    assert stage_model_identity(False)["stages"]["action"]["same_second_continuation"] is False
    assert stages["work_review"] == {"thinking": "disabled", "tool_choice": "required"}
    assert identity["base_url"] == "https://api.deepseek.com/beta"
    assert identity["max_retries"] == 0 and identity["strict"] is True
    assert identity["reasoning_effort"] is None


def test_stage_model_identity_is_frozen_and_tamper_rejected() -> None:
    """阶段身份运行中不变更；任何篡改被一致性校验拒绝。"""
    from worldmock.run_setup import AS2_LOCKED_SOURCE_SHA256, _agent_specs, _git_state, _model_identity, require_locked_as2, runtime_code_sha256, stage_model_identity
    from worldmock.run_setup import validate_stage_model_identity

    identity = stage_model_identity()

    assert stage_model_identity() == identity
    validate_stage_model_identity(identity)

    tampered = copy.deepcopy(identity)
    tampered["stages"]["action"]["max_tokens"] = 4000
    with pytest.raises(IntegrityError, match="stage action"):
        validate_stage_model_identity(tampered)

    wrong_endpoint = copy.deepcopy(identity)
    wrong_endpoint["base_url"] = "https://elsewhere.example/v1"
    with pytest.raises(IntegrityError, match="base_url"):
        validate_stage_model_identity(wrong_endpoint)

    reasoning = copy.deepcopy(identity)
    reasoning["reasoning_effort"] = "high"
    with pytest.raises(IntegrityError, match="reasoning_effort"):
        validate_stage_model_identity(reasoning)


def test_agent_specs_inject_compiled_persona_card_text() -> None:
    """新建运行注入 PersonaCard 渲染文本与结构化字段（去掉当下处境），两者都取经场景作息覆盖后的卡片
    （docs/dev/SPEC-life-realism.md#dayrun-fidelity-人设与系统层-场景作息覆盖卡片抽取值 ）；恢复模式不重新编译。"""
    from worldmock.run_setup import AS2_LOCKED_SOURCE_SHA256, _agent_specs, _git_state, _model_identity, require_locked_as2, runtime_code_sha256, stage_model_identity

    from tests.profile.conftest import make_card
    from worldmock.life.world_setup import load_scenario
    from worldmock.profile.injection import apply_scenario_rhythm
    from worldmock.profile.models import persona_card_struct
    from worldmock.profile.textgen import render_card

    scenario = load_scenario(ENVIRONMENT_ROOT / "scenarios/single-person.json")[0]
    compiled: list[int] = []
    card = make_card()

    def fake_compiler(agent):
        compiled.append(agent.agent_id)
        return card if agent.background_story else None

    specs = _agent_specs(scenario, persona_compiler=fake_compiler)

    overridden = apply_scenario_rhythm(card, scenario.agents[0]).card
    assert overridden != card
    assert specs[0]["profile"]["persona_card"] == render_card(overridden)
    assert specs[0]["profile"]["persona_card_struct"] == persona_card_struct(overridden)
    assert specs[0]["config"]["max_react_turns"] == 16
    assert compiled == [1]
    restored = _agent_specs(scenario, persona_compiler=_no_persona_compilation)
    assert "persona_card" not in restored[0]["profile"] and "persona_card_struct" not in restored[0]["profile"]
    assert restored[0]["config"]["max_react_turns"] == 16


def _write_agent_workspace(workspace: Path, *, initialized: bool = False) -> None:
    """写入 runtime 恢复验证使用的最小严格 LifeAgent 工作区。"""
    workspace.mkdir()
    agent = {
        "schema_version": 1,
        "agent_class": "LifeAgent",
        "agent_id": 1,
        "initialized_at": "2026-01-01T00:00:00+00:00" if initialized else None,
        "profile": {"id": 1, "same_second_continuation": True},
    }
    (workspace / "AGENT.json").write_text(json.dumps(agent))
    (workspace / "config.json").write_text(json.dumps({"enable_memory": True, "max_react_turns": 16}))


def test_imported_as2_matches_rebuildable_locked_source() -> None:
    """实际导入的 AS2 与固定基线加补丁重建的唯一源码身份一致。"""
    from worldmock.run_setup import AS2_LOCKED_SOURCE_SHA256, _agent_specs, _git_state, _model_identity, require_locked_as2, runtime_code_sha256, stage_model_identity

    files = require_locked_as2()

    assert files and all(label.startswith("agentsociety2/") for label, _ in files)
    assert "agentsociety2/__init__.py" in {label for label, _ in files}


def test_runtime_code_identity_is_deterministic(monkeypatch, tmp_path, tmp_path_factory) -> None:
    """同一工程与同一 AS2 包重复计算得到相同代码身份（AS2 取临时包，不依赖本机安装）。"""
    from worldmock.run_setup import AS2_LOCKED_SOURCE_SHA256, _agent_specs, _git_state, _model_identity, require_locked_as2, runtime_code_sha256, stage_model_identity

    project = write_project(tmp_path / "project")
    use_fake_as2(monkeypatch, tmp_path_factory.mktemp("as2-site"))

    first = runtime_code_sha256(project)
    second = runtime_code_sha256(project)

    assert len(first) == 64
    assert first == second


def test_code_identity_covers_every_imported_as2_file_except_pycache(
        monkeypatch, tmp_path, tmp_path_factory) -> None:
    """AS2 包内任一文件（含非 Python 文件与新增文件）字节变化都改变 code_sha256，__pycache__ 不计入；
    未改锁定摘要时字节变化即按摘要不符拒绝。"""
    from worldmock.run_setup import AS2_LOCKED_SOURCE_SHA256, _agent_specs, _git_state, _model_identity, require_locked_as2, runtime_code_sha256, stage_model_identity
    import worldmock.run_setup as worldmock_run_setup_module

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
    from worldmock.run_setup import AS2_LOCKED_SOURCE_SHA256, _agent_specs, _git_state, _model_identity, require_locked_as2, runtime_code_sha256, stage_model_identity
    import worldmock.run_setup as worldmock_run_setup_module

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
    import worldmock.run_setup as worldmock_run_setup_module

    if condition == "missing":
        monkeypatch.setattr(worldmock_run_setup_module, '_AS2_PACKAGE', f'worldmock_absent_as2_{uuid4().hex}')
    elif condition == "empty":
        use_fake_as2(monkeypatch, site, {"__pycache__/stale.cpython-312.pyc": b"\x00"}, locked=False)
    else:
        use_fake_as2(monkeypatch, site, locked=False)


@pytest.mark.parametrize("condition", ["missing", "empty", "mismatch"])
async def test_unlocked_as2_refuses_run_resume_replay_and_validate(
        condition, monkeypatch, tmp_path, tmp_path_factory) -> None:
    """AS2 包目录缺失、为空或摘要不符时，四个入口在读凭据、改进程环境与写运行目录之前以 IntegrityError 拒绝。"""
    _break_as2(condition, monkeypatch, tmp_path_factory.mktemp("as2-site"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    environment = dict(os.environ)
    resume = ResumeRequest(ROOT, SCENARIO, tmp_path / "parent", "checkpoint", tmp_path / "child")
    intervention = CounterfactualIntervention(
        kind="set_balance_fen", agent_id=1, balance_fen=0, hypothesis="h", source_event_ids=("e",))
    entries = (lambda: run_world(RunRequest(ROOT, SCENARIO, tmp_path / "run", "identity", "identity")),
               lambda: resume_world(resume),
               lambda: replay_world(CounterfactualRequest(resume, intervention)))

    for entry in entries:
        with pytest.raises(IntegrityError, match=_AS2_REFUSAL):
            await entry()
    with pytest.raises(IntegrityError, match=_AS2_REFUSAL):
        validate_project(ROOT, SCENARIO)

    assert not (tmp_path / "run").exists() and not (tmp_path / "child").exists()
    assert dict(os.environ) == environment


@pytest.mark.parametrize("variant", ["blocked", "no_spec", "not_a_package"])
def test_as2_lookup_failures_are_identity_refusals(variant, monkeypatch, tmp_path, tmp_path_factory) -> None:
    """导入被阻断、已导入对象没有 spec、同名对象不是包：都按 AS2 身份不符拒绝，也不给出代码身份。"""
    from worldmock.run_setup import AS2_LOCKED_SOURCE_SHA256, _agent_specs, _git_state, _model_identity, require_locked_as2, runtime_code_sha256, stage_model_identity
    import worldmock.run_setup as worldmock_run_setup_module

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
    from worldmock.run_setup import AS2_LOCKED_SOURCE_SHA256, _agent_specs, _git_state, _model_identity, require_locked_as2, runtime_code_sha256, stage_model_identity
    import worldmock.run_setup as worldmock_run_setup_module

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


def test_experiment_registers_the_locked_as2_source_digest_verbatim() -> None:
    """EXPERIMENT「AS2 基线与框架依赖」登记的 AS2 源码摘要与代码常量逐字相同（EX3）。"""
    from worldmock.run_setup import AS2_LOCKED_SOURCE_SHA256, _agent_specs, _git_state, _model_identity, require_locked_as2, runtime_code_sha256, stage_model_identity

    text = (ROOT / "docs" / "EXPERIMENT.md").read_text(encoding="utf-8")
    section = text.split("### AS2 基线与框架依赖", 1)[1].split("\n### ", 1)[0]
    rows = [[cell.strip() for cell in line.strip().strip("|").split("|")]
            for line in section.splitlines() if line.startswith("| AS2 源码摘要 |")]

    assert len(rows) == 1
    assert rows[0][1] == f"`{AS2_LOCKED_SOURCE_SHA256}`"
    assert "`SERIES`" in rows[0][2]


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
    from worldmock.run_setup import AS2_LOCKED_SOURCE_SHA256, _agent_specs, _git_state, _model_identity, require_locked_as2, runtime_code_sha256, stage_model_identity

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
    from worldmock.run_setup import AS2_LOCKED_SOURCE_SHA256, _agent_specs, _git_state, _model_identity, require_locked_as2, runtime_code_sha256, stage_model_identity
    import worldmock.run_setup as worldmock_run_setup_module

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

    use_fake_as2(monkeypatch, tmp_path_factory.mktemp("as2-site"))
    _hermetic_git(monkeypatch, tmp_path_factory)
    checkout, head = _committed_project(tmp_path_factory.mktemp("checkout"))
    (checkout / "notes.txt").write_text("uncommitted\n", encoding="utf-8")
    plain = write_project(tmp_path_factory.mktemp("plain"))

    manifests = []
    for project, run_dir in ((checkout, tmp_path / "git-run"), (plain, tmp_path / "plain-run")):
        prepared = _prepare_new_run(RunRequest(project, SCENARIO, run_dir, "identity", "identity"))
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


def test_run_shell_allows_only_status_file(tmp_path) -> None:
    """CLI 状态壳可用，任何已有运行工件都被拒绝。"""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "status.json").write_text("{}")
    _require_run_shell(run_dir)

    (run_dir / "manifest.json").write_text("{}")
    with pytest.raises(FileExistsError, match="artifacts"):
        _require_run_shell(run_dir)


def test_restore_publishes_database_agents_and_status_together(tmp_path) -> None:
    """子分支发布时包含同一检查点的人物工作区和 CLI 状态。"""
    parent_dir = tmp_path / "parent"
    manifest = {"schema_version": 1, "run_id": "parent", "agent_ids": [1]}
    state = {"version": 0, "current_time": 1, "run_id": "parent"}
    parent = FootprintStore.create(parent_dir, manifest, state)
    workspace = tmp_path / "agent"
    _write_agent_workspace(workspace)
    snapshots = {
        "agent:1": snapshot_directory(workspace, parent.assets),
        "life": parent.assets.put_json({"state_version": 0}, {"kind": "life_checkpoint"}),
    }
    checkpoint = parent.checkpoint(state, snapshots)
    destination = tmp_path / "child"
    destination.mkdir()
    status = {"status": "created", "phase": "stopped"}
    (destination / "status.json").write_text(json.dumps(status))

    child_manifest, child_state = _restore_branch(parent, checkpoint, destination)
    parent.close()

    restored = destination / "as2" / "agents" / "agent_0001" / "AGENT.json"
    assert json.loads(restored.read_text())["agent_id"] == 1
    assert json.loads((destination / "status.json").read_text()) == status
    assert child_manifest["run_id"] != "parent"
    assert child_state["run_id"] == child_manifest["run_id"]
    child = FootprintStore(destination)
    try:
        assert child.state == child_state
    finally:
        child.close()


def test_counterfactual_branch_freezes_intervention_before_publish(tmp_path) -> None:
    """反事实分支发布时已绑定规范干预身份且父运行保持不变。"""
    parent_dir = tmp_path / "parent"
    manifest = {"schema_version": 1, "run_id": "parent", "agent_ids": [1]}
    state = {"version": 0, "current_time": 1, "run_id": "parent"}
    parent = FootprintStore.create(parent_dir, manifest, state)
    workspace = tmp_path / "agent"
    _write_agent_workspace(workspace)
    snapshots = {
        "agent:1": snapshot_directory(workspace, parent.assets),
        "life": parent.assets.put_json({"state_version": 0}, {"kind": "life_checkpoint"}),
    }
    checkpoint = parent.checkpoint(state, snapshots)
    intervention = CounterfactualIntervention(
        kind="set_balance_fen",
        agent_id=1,
        balance_fen=0,
        hypothesis="No available money changes the next purchase decision.",
        source_event_ids=("source-event",),
    )
    payload = {"tool_name": "counterfactual_intervention",
               "request": intervention.model_dump(mode="json")}
    expected = hashlib.sha256(canonical_json(payload)).hexdigest()
    destination = tmp_path / "child"

    child_manifest, _ = _restore_branch(
        parent,
        checkpoint,
        destination,
        {"branch_kind": "counterfactual", "intervention_sha256": expected},
    )

    assert _intervention_sha256(intervention) == expected
    assert child_manifest["branch_kind"] == "counterfactual"
    assert child_manifest["intervention_sha256"] == expected
    assert parent.manifest == manifest
    assert parent.state == state
    parent.close()


def test_restore_rejects_corrupt_used_memory_before_branch_publish(tmp_path) -> None:
    """已执行人物的记忆损坏时不发布子目录且父运行不变。"""
    parent_dir = tmp_path / "parent"
    manifest = {"schema_version": 1, "run_id": "parent", "agent_ids": [1]}
    state = {"version": 0, "current_time": 1, "run_id": "parent"}
    parent = FootprintStore.create(parent_dir, manifest, state)
    workspace = tmp_path / "agent"
    _write_agent_workspace(workspace, initialized=True)
    (workspace / "memory").mkdir()
    (workspace / "MEMORY.md").write_text("# Memory\n")
    (workspace / "memory" / "episodes.jsonl").write_text("{damaged}\n")
    (workspace / "memory" / "state.json").write_text("{}")
    snapshots = {
        "agent:1": snapshot_directory(workspace, parent.assets),
        "life": parent.assets.put_json({"state_version": 0}, {"kind": "life_checkpoint"}),
    }
    checkpoint = parent.checkpoint(state, snapshots)
    destination = tmp_path / "child"

    with pytest.raises(IntegrityError, match="memory is invalid"):
        _restore_branch(parent, checkpoint, destination)

    assert not destination.exists()
    assert parent.manifest == manifest
    assert parent.state == state
    parent.close()


@pytest.mark.asyncio
async def test_execute_applies_counterfactual_before_running(monkeypatch, tmp_path) -> None:
    """runtime 初始化后先提交干预，只有成功才进入人物运行。"""
    calls = []

    class Society:
        current_time = None
        step_count = 0
        token_stats = {}

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
    import worldmock.runtime as runtime

    monkeypatch.setattr(llm_dispatcher, "init_dispatchers", init_dispatchers)
    monkeypatch.setattr(runtime, "_build_society", lambda prepared: (None, Society()))
    intervention = CounterfactualIntervention(
        kind="set_balance_fen", agent_id=1, balance_fen=0,
        hypothesis="No money changes behavior.", source_event_ids=("event",),
    )
    digest = _intervention_sha256(intervention)
    prepared = SimpleNamespace(
        run_id="child", run_dir=tmp_path, intervention=intervention,
        intervention_sha256=digest, scenario=SimpleNamespace(timezone="UTC"),
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
    import worldmock.runtime as runtime

    monkeypatch.setattr(llm_dispatcher, "init_dispatchers", init_dispatchers)
    monkeypatch.setattr(runtime, "_build_society", lambda prepared: (None, Society()))
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
    import worldmock.runtime as runtime

    monkeypatch.setattr(llm_dispatcher, "init_dispatchers", init_dispatchers)
    monkeypatch.setattr(runtime, "_build_society", lambda prepared: (None, Society()))
    prepared = SimpleNamespace(
        run_id="run", run_dir=tmp_path, intervention=None, intervention_sha256=None,
    )

    with pytest.raises(BaseExceptionGroup) as caught:
        await _execute(prepared)

    assert [type(item) for item in caught.value.exceptions] == [ValueError, OSError]
    assert [str(item) for item in caught.value.exceptions] == ["run failed", "close failed"]


def test_society_build_failure_terminates_created_actor(monkeypatch, tmp_path) -> None:
    """Society 连接失败时立即释放尚未交给外层清理的环境 actor。"""
    calls = []
    actor = object()

    class ActorClass:
        @staticmethod
        def remote(spec):
            calls.append(("remote", spec))
            return actor

    def reject_router(*args, **kwargs):
        raise RuntimeError("router construction failed")

    import ray
    import worldmock.runtime as runtime
    from agentsociety2.config import llm_dispatcher
    from worldmock.life import read_query_runtime

    monkeypatch.setattr(runtime, "_require_custom_modules", lambda root: None)
    monkeypatch.setattr(llm_dispatcher, "build_client_for_role", lambda role: role)
    from worldmock.life import read_query_runtime

    monkeypatch.setattr(read_query_runtime, "life_env_actor_class", lambda: ActorClass)
    monkeypatch.setattr(read_query_runtime, "LifeEnvProxy", reject_router)
    monkeypatch.setattr(ray, "kill", lambda target, no_restart: calls.append(("kill", target, no_restart)))
    from worldmock.life.world_setup import load_scenario

    scenario_path = ENVIRONMENT_ROOT / "scenarios/single-person.json"
    scenario, _ = load_scenario(scenario_path)
    prepared = SimpleNamespace(project_root=tmp_path, run_dir=tmp_path / "run", env_kwargs={},
                               scenario_path=scenario_path, scenario=scenario,
                               workspace_mode="restore", start_time=scenario.start_at,
                               same_second_continuation=True)

    with pytest.raises(RuntimeError, match="router construction failed"):
        _build_society(prepared)

    assert calls[0][0] == "remote"
    assert calls[1] == ("kill", actor, True)


class _StubContactReplyClient:
    """NPC 送达集成测试的联系人回复客户端桩（记录请求、返回脚本化响应）。"""

    model_name = "deepseek-flash"

    def __init__(self, model_identity):
        self.calls = []

    async def call(self, request):
        self.calls.append(request)
        return {"model": self.model_name,
                "choices": [{"message": {"content": "好啊，老地方见。"}}],
                "usage": {"prompt_tokens": 21, "completion_tokens": 7}}


def _npc_delivery_engine(tmp_path: Path):
    """建立隔离运行的 LifeEngine 与裸 LifeEnv 包装（采纳点=commit 后结算）。"""
    from worldmock.run_setup import AS2_LOCKED_SOURCE_SHA256, _agent_specs, _git_state, _model_identity, require_locked_as2, runtime_code_sha256, stage_model_identity

    from agentsociety2.society import DecisionContext, DirectToolCall
    from tests.life.conftest import context, start_request
    from worldmock.life.engine import LifeEngine

    scenario_path = ENVIRONMENT_ROOT / "scenarios/single-person.json"
    from worldmock.life.world_setup import load_scenario

    scenario, _ = load_scenario(scenario_path)
    run_id = "npc-delivery-test"
    from worldmock.life.models import RunIdentity

    identity = RunIdentity(
        run_id=run_id, source_group_id=f"group-{run_id}",
        agent_ids=tuple(item.agent_id for item in scenario.agents),
        start_at=scenario.start_at.isoformat(), end_at=scenario.end_at.isoformat(),
        timezone=scenario.timezone, code_sha256="4" * 64,
        model_identity=_model_identity())
    engine = LifeEngine.create(scenario_path, tmp_path / "run", identity)
    return engine, context, DecisionContext, DirectToolCall


@pytest.mark.asyncio
async def test_life_env_commit_settles_model_reply_into_inbox(tmp_path, monkeypatch) -> None:
    """生产采纳点（C1 接缝二）：model 联系人消息在本轮 commit 后生成回复并登记预排送达，
    CONTACT_REPLY_DELAY_SECONDS 之后才进入收件箱（裁决 #21）。"""
    from worldmock.life.models import SendMessageActionRequest
    from worldmock.life.social import CONTACT_REPLY_DELAY_SECONDS

    engine, context, _DecisionContext, DirectToolCall = _npc_delivery_engine(tmp_path)
    try:
        from custom.envs.life_env import LifeEnv

        env = LifeEnv()
        env.engine = engine
        ctx = context(engine, "npc-deliver")
        await env.begin_decision_round((ctx,))
        await env.observe(ctx)
        await env.submit_action(DirectToolCall(
            ctx, "action-npc-deliver", "send_message",
            SendMessageActionRequest(recipient_name="联系人：沈昊",
                                     text="周五晚上一起吃饭？")))

        import worldmock.runtime as runtime_module

        monkeypatch.setattr(runtime_module, "ContactReplyClient", _StubContactReplyClient)
        results = await env.commit_decision_round("npc-deliver")

        assert results[0].status == "completed"
        assert not [item for item in engine.context.state.agents[1].inbox.values()
                    if item.sender_id == "contact-chenxi-oldfriend"]
        scheduled = [item for item in engine.context.state.pending_events if item.kind == "message_arrival"]
        assert len(scheduled) == 1 and scheduled[0].agent_id == 1
        assert scheduled[0].due_at == engine.context.state.current_time + CONTACT_REPLY_DELAY_SECONDS
        reply = scheduled[0].data["message"]
        assert reply["sender_id"] == "contact-chenxi-oldfriend" and reply["text"] == "好啊，老地方见。"
        events = engine.context.store.read_events(engine.context.store.last_event_seq)
        assert not [item for item in events if item.data.get("kind") == "message_arrival"
                    and item.data.get("message_id") == reply["message_id"]]
        evidence = [item for item in events if item.data.get("kind") == "model_npc_reply"]
        assert len(evidence) == 1
    finally:
        engine.close()


@pytest.mark.asyncio
async def test_life_env_commit_without_pending_never_builds_reply_client(
        tmp_path, monkeypatch) -> None:
    """反例：无待回复消息时 commit 不构造回复客户端（结算循环零副作用）。"""
    engine, context, _DecisionContext, DirectToolCall = _npc_delivery_engine(tmp_path)
    try:
        from custom.envs.life_env import LifeEnv
        from tests.life.conftest import start_request

        env = LifeEnv()
        env.engine = engine
        ctx = context(engine, "npc-noop")
        await env.begin_decision_round((ctx,))
        await env.observe(ctx)
        await env.submit_action(DirectToolCall(
            ctx, "action-npc-noop", "start_activity",
            start_request(targets=["自己"], actions=["interact"], ways=["等待"])))

        import worldmock.runtime as runtime_module

        def _forbidden(model_identity):
            raise AssertionError("reply client must not be built without pending replies")

        monkeypatch.setattr(runtime_module, "ContactReplyClient", _forbidden)
        results = await env.commit_decision_round("npc-noop")

        assert results[0].status == "in_progress"
        assert engine.context.state.agents[1].inbox == {}
    finally:
        engine.close()


class _RecordingPlanDayClient:
    """零点计划客户端桩：记录构造身份与请求，返回脚本化的合法计划（替换 worldmock.runtime.PlanDayClient）。"""

    model_name = "deepseek-flash"
    built: list = []
    goals: list = []

    def __init__(self, model_identity):
        type(self).built.append(model_identity)
        self.calls = []

    async def call(self, request):
        from tests.life.conftest import plan_day_response

        self.calls.append(request)
        return plan_day_response(type(self).goals)


def _plan_day_client(monkeypatch, goals: list | None = None) -> type:
    """把零点计划客户端类替换为记录桩（同替换 ContactReplyClient），返回该类。"""
    client = type("PlanDayClientStub", (_RecordingPlanDayClient,), {"built": [], "goals": goals or []})
    monkeypatch.setattr(runtime_module, "PlanDayClient", client)
    return client


def _forbid_plan_day_client(monkeypatch) -> None:
    """断言零点计划任务不构造客户端。"""
    def forbidden(model_identity):
        raise AssertionError("plan day client must not be built")

    monkeypatch.setattr(runtime_module, "PlanDayClient", forbidden)


def _checkpoint_request(engine, tmp_path: Path, round_id: str):
    """全部人物工作区的共同检查点请求（工作区只放一个档案文件）。"""
    from agentsociety2.society import SocietyCheckpointRequest

    workspaces = []
    for agent_id in engine.context.store.manifest["agent_ids"]:
        workspace = tmp_path / f"ws-{round_id}-{agent_id}"
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "AGENT.json").write_text('{"agent_id": %d}' % agent_id, encoding="utf-8")
        workspaces.append((agent_id, str(workspace)))
    now = datetime.fromtimestamp(engine.context.state.current_time, ZoneInfo(engine.context.scenario.timezone))
    return SocietyCheckpointRequest(engine.context.state.run_id, round_id, now, tuple(workspaces))


@pytest.mark.asyncio
async def test_life_env_checkpoint_plans_the_start_day_after_the_initial_checkpoint(tmp_path, monkeypatch) -> None:
    """启动路径：新建运行的初始检查点之后、首次决策之前生成当日计划；工作区字节不变，不产生人物事件。"""
    from worldmock.run_setup import AS2_LOCKED_SOURCE_SHA256, _agent_specs, _git_state, _model_identity, require_locked_as2, runtime_code_sha256, stage_model_identity

    from custom.envs.life_env import LifeEnv

    engine, context, _DecisionContext, _DirectToolCall = _npc_delivery_engine(tmp_path)
    try:
        env = LifeEnv()
        env.engine = engine
        client = _plan_day_client(monkeypatch, [{"intent_kind": "工作", "goal": "上午把周报写完",
                                                 "remind_time": "09:30", "around_time": "09:30"}])
        request = _checkpoint_request(engine, tmp_path, "initial")
        before = {path: (Path(path) / "AGENT.json").read_bytes() for _, path in request.agent_workspaces}

        checkpoint = await env.checkpoint(request)

        assert checkpoint.state["agents"]["1"]["day_plan"] is None
        assert engine.context.state.agents[1].day_plan.goals[0].goal == "上午把周报写完"
        assert client.built == [engine.context.store.manifest["model_identity"]]
        events = engine.context.store.read_events(engine.context.store.last_event_seq)
        order = [event.event_type if event.event_type != "environment_event" else event.data["kind"]
                 for event in events]
        assert order.index("checkpoint_created") < order.index("day_plan_generated")
        assert not {"observation", "decision_started", "memory_updated"} & set(order)
        assert {path: (Path(path) / "AGENT.json").read_bytes() for _, path in request.agent_workspaces} == before
        assert set(stage_model_identity()["stages"]) == {"action", "work_review"}
        _forbid_plan_day_client(monkeypatch)
        await env.checkpoint(_checkpoint_request(engine, tmp_path, "again"))
    finally:
        engine.close()


@pytest.mark.asyncio
async def test_life_env_step_plans_midnight_before_the_step_checkpoint(tmp_path, monkeypatch) -> None:
    """零点：LifeEnv.step 在 engine.advance 之后 await 零点计划任务、再执行零点节律入口；计划在本步检查点之前提交。"""
    from custom.envs.life_env import LifeEnv
    from tests.life.conftest import settle_day_plans, start_activity, submit_round
    from tests.life.test_day_planning import _late_engine

    engine = _late_engine(tmp_path, "2026-01-05T23:45:00+08:00", "env-midnight")
    try:
        settle_day_plans(engine)
        submit_round(engine, {1: ("start_activity", start_activity("睡觉", "自己"))}, "bedtime")
        env = LifeEnv()
        env.engine = engine
        client = _plan_day_client(monkeypatch, [{"intent_kind": "晨间例程", "goal": "起床洗漱",
                                                 "remind_time": "07:00", "around_time": "07:00"}])
        state = await env.scheduling_state()

        await env.step(int(state.next_event_at.timestamp()) - engine.context.state.current_time, state.next_event_at)

        assert env.t == state.next_event_at and not engine.midnight_rhythm_pending
        assert engine.context.state.agents[1].plan_submitted_dates == {"2026-01-05", "2026-01-06"}
        assert len(client.built) == 1 and not (await env.scheduling_state()).ready_agent_ids
        checkpoint = await env.checkpoint(_checkpoint_request(engine, tmp_path, "advance"))
        assert checkpoint.state["agents"]["1"]["day_plan"]["goals"][0]["goal"] == "起床洗漱"
        assert len(client.built) == 1
    finally:
        engine.close()


def _resume_branch(tmp_path: Path, planned: bool, metadata: dict | None = None) -> Path:
    """从新建运行的检查点恢复子分支（planned 为假时取计划尚未生成的初始检查点），返回子运行目录。"""
    from tests.life.conftest import settle_day_plans

    engine, _context, _DecisionContext, _DirectToolCall = _npc_delivery_engine(tmp_path)
    try:
        if planned:
            settle_day_plans(engine)
        workspace = tmp_path / "parent-ws"
        workspace.mkdir(exist_ok=True)
        (workspace / "AGENT.json").write_text("{}", encoding="utf-8")
        checkpoint = engine.checkpoint({"agent:1": snapshot_directory(workspace, engine.context.store.assets)})
        child = tmp_path / ("planned-child" if planned else "child")
        restore_branch(engine.context.store, checkpoint.checkpoint_id, child, metadata).close()
        return child
    finally:
        engine.close()


@pytest.mark.asyncio
async def test_plain_resume_plans_the_missing_day_in_init_once(tmp_path, monkeypatch) -> None:
    """从计划尚未生成的初始检查点普通恢复：LifeEnv.init 在首次决策之前生成一次；已生成的检查点恢复时不重复。"""
    from custom.envs.life_env import LifeEnv

    scenario = ENVIRONMENT_ROOT / "scenarios/single-person.json"
    child = _resume_branch(tmp_path, planned=False)
    client = _plan_day_client(monkeypatch)
    env = LifeEnv(scenario_path=str(scenario), run_dir=str(child), open_existing=True)
    await env.init(datetime.fromisoformat("2026-01-05T00:00:00+08:00"))
    try:
        assert env.engine.context.store.manifest["branch_kind"] == "resume"
        assert env.engine.context.state.agents[1].plan_submitted_dates == {"2026-01-05"} and len(client.built) == 1
        events = env.engine.context.store.read_events(env.engine.context.store.last_event_seq)
        assert not [event for event in events if event.event_type == "decision_started"]
    finally:
        await env.close()
    (tmp_path / "second").mkdir()
    planned_child = _resume_branch(tmp_path / "second", planned=True)
    _forbid_plan_day_client(monkeypatch)
    again = LifeEnv(scenario_path=str(scenario), run_dir=str(planned_child), open_existing=True)
    await again.init(datetime.fromisoformat("2026-01-05T00:00:00+08:00"))
    await again.close()


@pytest.mark.asyncio
async def test_counterfactual_init_waits_for_the_intervention_checkpoint(tmp_path, monkeypatch) -> None:
    """反事实分支的 init 不执行零点计划（在干预检查点之后执行），不构造客户端。"""
    from custom.envs.life_env import LifeEnv

    child = _resume_branch(tmp_path, planned=False,
                           metadata={"branch_kind": "counterfactual", "intervention_sha256": "a" * 64})
    _forbid_plan_day_client(monkeypatch)
    env = LifeEnv(scenario_path=str(ENVIRONMENT_ROOT / "scenarios/single-person.json"), run_dir=str(child), open_existing=True)
    await env.init(datetime.fromisoformat("2026-01-05T00:00:00+08:00"))
    try:
        assert env.engine.context.state.agents[1].day_plan is None
    finally:
        await env.close()


@pytest.mark.asyncio
async def test_plan_day_failure_during_resume_init_is_registered_before_raising(tmp_path, monkeypatch) -> None:
    """普通恢复中零点计划失败：init 先经 engine.fail_run 登记运行故障再抛出（AS2 的故障登记范围之外）。"""
    from custom.envs.life_env import LifeEnv

    child = _resume_branch(tmp_path, planned=False)

    class FailingClient(_RecordingPlanDayClient):
        async def call(self, request):
            raise ConnectionError("plan day transport down")

    monkeypatch.setattr(runtime_module, "PlanDayClient", FailingClient)
    env = LifeEnv(scenario_path=str(ENVIRONMENT_ROOT / "scenarios/single-person.json"), run_dir=str(child), open_existing=True)
    with pytest.raises(ConnectionError, match="transport down"):
        await env.init(datetime.fromisoformat("2026-01-05T00:00:00+08:00"))
    try:
        events = env.engine.context.store.read_events(env.engine.context.store.last_event_seq)
        assert events[-1].event_type == "run_failed" and "transport down" in events[-1].data["error"]
        assert [event.data["phase"] for event in events if event.data.get("purpose") == "plan_day"] == [
            "request", "failure"]
    finally:
        await env.close()


def _failing_plan_day_client(monkeypatch) -> None:
    """零点计划客户端桩：按清单身份照常构造，每次请求都以传输失败抛出（替换 worldmock.runtime.PlanDayClient）。"""
    class FailingClient(_RecordingPlanDayClient):
        built: list = []

        async def call(self, request):
            raise ConnectionError("plan day transport down")

    monkeypatch.setattr(runtime_module, "PlanDayClient", FailingClient)


def _society_config(env, identity, run_dir: Path, window: tuple[datetime, datetime], mode: str):
    """真实 AgentSociety 接真实 LifeEnv 的运行配置（同 test_life_agent_protocol 的 Society 装配，不经 Ray 与模型）。

    环境路由是生产装配所用的 LifeEnvActor（AS2 DirectEnvActor 的 Life 子类，带只读查询转发）与它持有的 DirectRouter
    （生产链路去掉 Ray 代理与模块扫描）；identity 带 run_id 与 agent_ids；window 为（起点, 终点）。
    """
    from agentsociety2.env.router_direct import DirectRouter
    from agentsociety2.society import AgentSocietyConfig
    from worldmock.life.read_query_runtime import LifeEnvActor

    actor = object.__new__(LifeEnvActor)
    actor._router = DirectRouter(env_modules=[env], llm_clients_spec={"coder": "unused", "default": "unused"})
    actor._replay_proxy = None
    return AgentSocietyConfig(
        run_id=identity.run_id, agent_class_name="LifeAgent", env_router=actor, service_proxy=None,
        agent_specs=tuple({"id": agent_id, "profile": {}, "config": {}} for agent_id in identity.agent_ids),
        start_time=window[0], end_time=window[1], run_dir=run_dir,
        custom_root=Path(__file__).resolve().parents[2], workspace_mode=mode)


@pytest.mark.asyncio
async def test_plan_day_failure_at_a_midnight_step_is_one_run_failure_through_society_run(
        tmp_path, monkeypatch) -> None:
    """零点推进中零点计划失败：异常经 LifeEnv.step 传到 Society.run，登记唯一 run_failed 后原样抛出，不成异常组；
    零点的请求与失败证据先于 run_failed 保留。"""
    from agentsociety2.society import AgentSociety
    from custom.envs.life_env import LifeEnv
    from tests.life.conftest import settle_day_plans, start_activity, submit_round
    from tests.life.test_day_planning import _late_engine

    engine = _late_engine(tmp_path, "2026-01-05T23:45:00+08:00", "society-midnight")
    try:
        settle_day_plans(engine)
        submit_round(engine, {1: ("start_activity", start_activity("睡觉", "自己"))}, "bedtime")
        env = LifeEnv()
        env.engine = engine
        _failing_plan_day_client(monkeypatch)
        zone = ZoneInfo(engine.context.scenario.timezone)
        window = (datetime.fromtimestamp(engine.context.state.current_time, zone),
                  datetime.fromtimestamp(engine.context.state.end_time, zone))
        identity = SimpleNamespace(run_id=engine.context.state.run_id, agent_ids=tuple(sorted(engine.context.state.agents)))
        society = AgentSociety(_society_config(env, identity, tmp_path, window, "restored"))
        society._initialized = True
        society._environment_started = True
        midnight = int(datetime(2026, 1, 6, tzinfo=zone).timestamp())
        seq = engine.context.store.last_event_seq

        with pytest.raises(ConnectionError, match="transport down") as caught:
            await society.run()

        assert not isinstance(caught.value, BaseExceptionGroup) and society.current_time == window[0]
        events = [event for event in engine.context.store.read_events(engine.context.store.last_event_seq) if event.event_seq > seq]
        assert events[-1].event_type == "run_failed" and "transport down" in events[-1].data["error"]
        assert [event.event_type for event in events].count("run_failed") == 1
        assert [(event.data["phase"], event.occurred_at) for event in events
                if event.data.get("purpose") == "plan_day"] == [("request", midnight), ("failure", midnight)]
    finally:
        engine.close()


@pytest.mark.asyncio
async def test_plan_day_failure_at_the_initial_checkpoint_is_one_run_failure_through_society_init(
        tmp_path, monkeypatch) -> None:
    """新建运行的启动路径：初始检查点之后零点计划失败，在 Society.init 登记故障的范围内登记唯一 run_failed 后原样抛出；
    检查点、零点计划的请求与失败证据依次保留。"""
    from worldmock.run_setup import AS2_LOCKED_SOURCE_SHA256, _agent_specs, _git_state, _model_identity, require_locked_as2, runtime_code_sha256, stage_model_identity

    import agentsociety2.society.society as society_module
    from agentsociety2.config import llm_dispatcher
    from agentsociety2.society import AgentSociety
    from custom.envs.life_env import LifeEnv
    from worldmock.life.models import RunIdentity
    from worldmock.life.world_setup import load_scenario

    async def no_dispatchers():
        return None

    async def create_workspaces(request):
        for item in request.items:
            workspace = Path(request.workspace_root) / f"agent_{int(item['id']):04d}"
            workspace.mkdir(parents=True)
            (workspace / "AGENT.json").write_text('{"agent_id": %d}' % int(item["id"]), encoding="utf-8")

    monkeypatch.setattr(llm_dispatcher, "init_dispatchers", no_dispatchers)
    monkeypatch.setattr(society_module, "create_direct_agents_batch", SimpleNamespace(remote=create_workspaces))
    _failing_plan_day_client(monkeypatch)
    scenario_path = ENVIRONMENT_ROOT / "scenarios/single-person.json"
    scenario, _ = load_scenario(scenario_path)
    identity = RunIdentity(
        run_id="society-init", source_group_id="group-society-init",
        agent_ids=tuple(item.agent_id for item in scenario.agents),
        start_at=scenario.start_at.isoformat(), end_at=scenario.end_at.isoformat(),
        timezone=scenario.timezone, code_sha256="4" * 64, model_identity=_model_identity())
    run_dir = tmp_path / "run"
    env = LifeEnv(scenario_path=str(scenario_path), run_dir=str(run_dir),
                  run_identity=identity.model_dump(mode="json"))
    zone = ZoneInfo(scenario.timezone)
    window = (scenario.start_at.astimezone(zone), scenario.end_at.astimezone(zone))
    society = AgentSociety(_society_config(env, identity, run_dir, window, "create"))

    with pytest.raises(ConnectionError, match="transport down") as caught:
        await society.init()

    try:
        assert not isinstance(caught.value, BaseExceptionGroup)
        events = env.engine.context.store.read_events(env.engine.context.store.last_event_seq)
        order = [event.event_type if event.data.get("purpose") != "plan_day" else event.data["phase"]
                 for event in events]
        assert order[-4:] == ["checkpoint_created", "request", "failure", "run_failed"]
        assert "transport down" in events[-1].data["error"] and order.count("run_failed") == 1
    finally:
        await env.close()






def test_agent_specs_restore_mode_never_recompiles_personas(monkeypatch) -> None:
    """恢复装配不重新编译画像；人物档案由检查点工作区原样恢复。"""
    from worldmock.run_setup import AS2_LOCKED_SOURCE_SHA256, _agent_specs, _git_state, _model_identity, require_locked_as2, runtime_code_sha256, stage_model_identity
    import worldmock.run_setup as worldmock_run_setup_module

    import worldmock.runtime as runtime_module
    from worldmock.life.world_setup import load_scenario

    def forbidden(agent):
        raise AssertionError("restore path must not compile personas")

    monkeypatch.setattr(worldmock_run_setup_module, '_compile_persona', forbidden)
    scenario = load_scenario(ROOT / "tests/fixtures/environment/scenarios/single-person.json")[0]

    specs = _agent_specs(scenario, persona_compiler=_no_persona_compilation)

    assert "persona_card" not in specs[0]["profile"]
    assert "persona_card_struct" not in specs[0]["profile"]
    assert "taste_weights" not in specs[0]["profile"]
