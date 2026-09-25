"""runtime 运行壳、代码身份与共同恢复测试。"""

from __future__ import annotations

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
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com")
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
from worldmock.life.models import CounterfactualIntervention, StartActivityRequest
from worldmock.runtime import (
    AS2_LOCKED_SOURCE_SHA256,
    CounterfactualRequest,
    ResumeRequest,
    RunRequest,
    _agent_specs,
    _build_society,
    _execute,
    _git_state,
    _intervention_sha256,
    _model_identity,
    _no_persona_compilation,
    _prepare_new_run,
    _require_run_shell,
    _restore_branch,
    replay_world,
    require_locked_as2,
    resume_world,
    run_world,
    runtime_code_sha256,
    two_stage_model_identity,
)
from worldmock.validation import validate_project, validate_two_stage_model_identity

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "scenarios" / "white-collar-12-weeks.json"
#: 规格第八章「运行身份」规定的 AS2 身份拒绝文案。
_AS2_REFUSAL = "AS2 runtime identity differs from the locked source"


def test_model_identity_freezes_complete_strict_endpoint_and_request() -> None:
    """运行身份记录完整节点 URL 和唯一 strict 请求参数。"""
    identity = _model_identity().model_dump(mode="json")

    assert identity["base_url"] == "https://api.deepseek.com"
    assert identity["provider"] == "openai_compatible"
    assert identity["requested_model"] == "deepseek-flash"
    assert identity["tool_choice"] == "auto"
    assert identity["thinking"] == "enabled"
    assert identity["max_retries"] == 0
    assert "base_url_host" not in identity


def test_model_identity_top_level_tracks_life_agent_action_constants() -> None:
    """顶层 thinking/tool_choice 与 B2 导出的行动阶段常量同源。"""
    from custom.agents.life_agent import ACTION_THINKING, ACTION_TOOL_CHOICE

    identity = _model_identity()

    assert identity.thinking == ACTION_THINKING["thinking"]["type"]
    assert identity.tool_choice == ACTION_TOOL_CHOICE


def test_two_stage_model_identity_shape_matches_life_agent_constants() -> None:
    """两阶段身份：决策自述/行动/工作验收参数与 LifeAgent 常量单一事实源一致。"""
    from custom.agents.life_agent import (
        ACTION_THINKING,
        ACTION_TOOL_CHOICE,
        ACTION_MAX_TOKENS,
        DECISION_STATEMENT_MAX_TOKENS,
        DECISION_STATEMENT_TEMPERATURE,
    )

    identity = two_stage_model_identity()
    stages = identity["stages"]

    assert stages["decision_statement"] == {"thinking": "disabled", "temperature": DECISION_STATEMENT_TEMPERATURE,
                                   "max_tokens": DECISION_STATEMENT_MAX_TOKENS, "tools": None,
                                   "tool_choice": None}
    assert stages["action"] == {"thinking": ACTION_THINKING["thinking"]["type"],
                                "tool_choice": ACTION_TOOL_CHOICE, "max_tokens": ACTION_MAX_TOKENS}
    assert stages["work_review"] == {"thinking": "disabled", "tool_choice": "required"}
    assert identity["base_url"] == "https://api.deepseek.com"
    assert identity["max_retries"] == 0 and identity["strict"] is True
    assert identity["reasoning_effort"] is None


def test_two_stage_model_identity_is_frozen_and_tamper_rejected() -> None:
    """两阶段身份运行中不变更；任何篡改被一致性校验拒绝。"""
    identity = two_stage_model_identity()

    assert two_stage_model_identity() == identity
    validate_two_stage_model_identity(identity)

    tampered = copy.deepcopy(identity)
    tampered["stages"]["decision_statement"]["temperature"] = 0.7
    with pytest.raises(IntegrityError, match="decision_statement"):
        validate_two_stage_model_identity(tampered)

    wrong_endpoint = copy.deepcopy(identity)
    wrong_endpoint["base_url"] = "https://elsewhere.example/v1"
    with pytest.raises(IntegrityError, match="base_url"):
        validate_two_stage_model_identity(wrong_endpoint)

    reasoning = copy.deepcopy(identity)
    reasoning["reasoning_effort"] = "high"
    with pytest.raises(IntegrityError, match="reasoning_effort"):
        validate_two_stage_model_identity(reasoning)


def test_agent_specs_inject_compiled_persona_card_text() -> None:
    """新建运行注入 PersonaCard 渲染文本；恢复模式不重新编译。"""
    from worldmock.life.scenario import load_scenario

    scenario = load_scenario(Path("scenarios/white-collar-12-weeks.json"))[0]
    compiled: list[int] = []

    def fake_compiler(agent):
        compiled.append(agent.agent_id)
        return "卡片文本" if agent.background_story else None

    specs = _agent_specs(scenario, persona_compiler=fake_compiler)

    assert specs[0]["profile"]["persona_card"] == "卡片文本"
    assert specs[0]["config"]["max_react_turns"] == 16
    assert compiled == [1]
    restored = _agent_specs(scenario, persona_compiler=_no_persona_compilation)
    assert "persona_card" not in restored[0]["profile"]
    assert restored[0]["config"]["max_react_turns"] == 16


def _write_agent_workspace(workspace: Path, *, initialized: bool = False) -> None:
    """写入 runtime 恢复验证使用的最小严格 LifeAgent 工作区。"""
    workspace.mkdir()
    agent = {
        "schema_version": 1,
        "agent_class": "LifeAgent",
        "agent_id": 1,
        "initialized_at": "2026-01-01T00:00:00+00:00" if initialized else None,
    }
    (workspace / "AGENT.json").write_text(json.dumps(agent))
    (workspace / "config.json").write_text(json.dumps({"enable_memory": True, "max_react_turns": 16}))


def test_runtime_code_identity_is_deterministic(monkeypatch, tmp_path, tmp_path_factory) -> None:
    """同一工程与同一 AS2 包重复计算得到相同代码身份（AS2 取临时包，不依赖本机安装）。"""
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
        monkeypatch.setattr(runtime_module, "AS2_LOCKED_SOURCE_SHA256", expected_as2_sha256(package))
        changed = runtime_code_sha256(project)
        assert changed not in seen
        seen.add(changed)


def test_as2_identity_is_independent_of_install_location(monkeypatch, tmp_path, tmp_path_factory) -> None:
    """同一份 AS2 源码装在不同位置：find_spec 解析到哪个目录都得到相同标签、通过同一锁定摘要、code_sha256 相同。"""
    project = write_project(tmp_path / "project")
    name = f"worldmock_fake_as2_{uuid4().hex}"
    first = write_fake_as2(tmp_path_factory.mktemp("site-a"), name)
    second = write_fake_as2(tmp_path_factory.mktemp("site-b") / "deeper" / "install", name)
    monkeypatch.setattr(runtime_module, "_AS2_PACKAGE", name)
    monkeypatch.setattr(runtime_module, "AS2_LOCKED_SOURCE_SHA256", expected_as2_sha256(first))

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
    if condition == "missing":
        monkeypatch.setattr(runtime_module, "_AS2_PACKAGE", f"worldmock_absent_as2_{uuid4().hex}")
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
    project = write_project(tmp_path / "project")
    name = f"worldmock_fake_as2_{uuid4().hex}"
    monkeypatch.setattr(runtime_module, "_AS2_PACKAGE", name)
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


def test_experiment_registers_the_locked_as2_source_digest_verbatim() -> None:
    """EXPERIMENT「AS2 基线与框架依赖」登记的 AS2 源码摘要与代码常量逐字相同（EX3）。"""
    text = (ROOT / "docs" / "EXPERIMENT.md").read_text(encoding="utf-8")
    section = text.split("### AS2 基线与框架依赖", 1)[1].split("\n### ", 1)[0]
    rows = [[cell.strip() for cell in line.strip().strip("|").split("|")]
            for line in section.splitlines() if line.startswith("| AS2 源码摘要 |")]

    assert len(rows) == 1
    assert rows[0][1] == f"`{AS2_LOCKED_SOURCE_SHA256}`"
    assert "`8768e14`" in rows[0][2]


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


def test_git_state_records_head_and_dirty_flag_only_at_checkout_top_level(
        monkeypatch, tmp_path_factory) -> None:
    """git_commit 取 HEAD；git_dirty 取 git status --porcelain 是否非空（含未跟踪文件、不含忽略文件）；
    非检出顶层或非 Git 目录时二者为 null。"""
    _hermetic_git(monkeypatch, tmp_path_factory)
    repository, head = _committed_project(tmp_path_factory.mktemp("checkout"))
    assert _git_state(repository) == (head, False)

    (repository / "debug.log").write_text("ignored\n", encoding="utf-8")
    assert _git_state(repository) == (head, False)
    _git(repository, "config", "status.showUntrackedFiles", "no")
    (repository / "notes.txt").write_text("untracked\n", encoding="utf-8")
    assert _git_state(repository) == (head, True)
    (repository / "notes.txt").unlink()
    (repository / "uv.lock").write_text("version = 2\n", encoding="utf-8")
    assert _git_state(repository) == (head, True)

    assert _git_state(repository / "src") == (None, None)
    assert _git_state(tmp_path_factory.mktemp("plain")) == (None, None)


def test_git_state_is_null_when_git_is_unavailable_or_status_fails(monkeypatch, tmp_path) -> None:
    """git 不可执行，或 HEAD 可读而 status 失败时，二者为 null，不阻断启动。"""
    commands = []

    def missing_git(command, **kwargs):
        commands.append(command)
        raise FileNotFoundError("git")

    monkeypatch.setattr(runtime_module.subprocess, "run", missing_git)
    assert _git_state(tmp_path) == (None, None)

    def status_fails(command, **kwargs):
        commands.append(command)
        head = "rev-parse" in command
        return SimpleNamespace(returncode=0 if head else 128, stdout=b"\n" + b"a" * 40 + b"\n" if head else b"")

    monkeypatch.setattr(runtime_module.subprocess, "run", status_fails)
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
            manifests.append(env.engine.store.manifest)
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
    from agentsociety2.env import direct_env_actor, env_router_proxy

    monkeypatch.setattr(runtime, "_require_custom_modules", lambda root: None)
    monkeypatch.setattr(llm_dispatcher, "build_client_for_role", lambda role: role)
    monkeypatch.setattr(direct_env_actor, "get_direct_env_actor_class", lambda: ActorClass)
    monkeypatch.setattr(env_router_proxy, "EnvRouterProxy", reject_router)
    monkeypatch.setattr(ray, "kill", lambda target, no_restart: calls.append(("kill", target, no_restart)))
    from worldmock.life.scenario import load_scenario

    scenario_path = Path("scenarios/white-collar-12-weeks.json")
    scenario, _ = load_scenario(scenario_path)
    prepared = SimpleNamespace(project_root=tmp_path, run_dir=tmp_path / "run", env_kwargs={},
                               scenario_path=scenario_path, scenario=scenario,
                               workspace_mode="restore", start_time=scenario.start_at)

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
    from agentsociety2.society import DecisionContext, DirectToolCall
    from tests.life.conftest import context
    from worldmock.life.engine import LifeEngine

    scenario_path = Path("scenarios/white-collar-12-weeks.json")
    from worldmock.life.scenario import load_scenario

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
    """生产采纳点（C1 接缝二）：model 联系人消息在下一轮 commit 后送达收件箱。"""
    from worldmock.life.models import SendMessageActionRequest

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
        inbox = engine.state.agents[1].inbox
        replies = [item for item in inbox.values()
                   if item.sender_id == "contact-chenxi-oldfriend"]
        assert len(replies) == 1 and replies[0].text == "好啊，老地方见。"
        events = engine.store.read_events(engine.store.last_event_seq)
        arrivals = [item for item in events if item.data.get("kind") == "message_arrival"
                    and item.data.get("message_id") == replies[0].message_id]
        assert len(arrivals) == 1 and arrivals[0].agent_id == 1
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

        env = LifeEnv()
        env.engine = engine
        ctx = context(engine, "npc-noop")
        await env.begin_decision_round((ctx,))
        await env.observe(ctx)
        await env.submit_action(DirectToolCall(
            ctx, "action-npc-noop", "start_activity",
            StartActivityRequest(targets=["自己"], actions=["等待"])))

        import worldmock.runtime as runtime_module

        def _forbidden(model_identity):
            raise AssertionError("reply client must not be built without pending replies")

        monkeypatch.setattr(runtime_module, "ContactReplyClient", _forbidden)
        results = await env.commit_decision_round("npc-noop")

        assert results[0].status == "in_progress"
        assert engine.state.agents[1].inbox == {}
    finally:
        engine.close()


def test_agent_specs_computes_taste_weights_from_the_compiled_card(monkeypatch) -> None:
    """装配侧由同一次编译的卡片口味计算确定性权重（docs/dev/SPEC-life-realism.md「画像编译」）。"""
    import worldmock.runtime as runtime_module
    from worldmock.life.scenario import load_scenario
    from worldmock.profile.taste import compute_taste_weights

    scenario = load_scenario(Path("scenarios/white-collar-12-weeks.json"))[0]
    card = SimpleNamespace(tastes=SimpleNamespace(content_genres=("剧情", "科幻")))
    compiled: list[int] = []

    def fake_payload(agent):
        compiled.append(agent.agent_id)
        return "卡片文本", card

    monkeypatch.setattr(runtime_module, "_persona_payload", fake_payload)
    genres = {item.item_id: ("剧情",) for item in scenario.contents}
    full, half, empty = (item.item_id for item in scenario.contents[:3])
    genres[full], genres[half], genres[empty] = ("剧情", "科幻"), ("剧情", "历史"), ()

    specs = _agent_specs(scenario, content_genres=genres)

    weights = specs[0]["profile"]["taste_weights"]
    expected = compute_taste_weights(
        ("剧情", "科幻"),
        [{"item_id": item.item_id, "genre": genres[item.item_id]}
         for item in scenario.contents])
    assert compiled == [1]
    assert specs[0]["profile"]["persona_card"] == "卡片文本"
    assert weights == expected
    assert weights[full] == 1.0 and weights[half] == 0.5 and weights[empty] == 0.0


def test_agent_specs_card_without_tastes_yields_all_zero_weights(monkeypatch) -> None:
    """无口味卡片：全内容目录权重为 0（无缺省抬升），键集覆盖全部条目。"""
    import worldmock.runtime as runtime_module
    from worldmock.life.scenario import load_scenario

    scenario = load_scenario(Path("scenarios/white-collar-12-weeks.json"))[0]
    card = SimpleNamespace(tastes=SimpleNamespace(content_genres=()))
    monkeypatch.setattr(runtime_module, "_persona_payload", lambda agent: ("卡片文本", card))

    specs = _agent_specs(scenario, content_genres={item.item_id: ("剧情",)
                                                   for item in scenario.contents})

    weights = specs[0]["profile"]["taste_weights"]
    assert set(weights) == {item.item_id for item in scenario.contents}
    assert weights and all(value == 0.0 for value in weights.values())


def test_agent_specs_restore_mode_never_recompiles_tastes(monkeypatch) -> None:
    """恢复模式不重算口味权重：人物档案随检查点工作区原样恢复。"""
    import worldmock.runtime as runtime_module
    from worldmock.life.scenario import load_scenario

    def forbidden(agent):
        raise AssertionError("restore path must not compile personas or tastes")

    monkeypatch.setattr(runtime_module, "_persona_payload", forbidden)
    scenario = load_scenario(Path("scenarios/white-collar-12-weeks.json"))[0]

    specs = _agent_specs(scenario, persona_compiler=_no_persona_compilation)

    assert "persona_card" not in specs[0]["profile"]
    assert "taste_weights" not in specs[0]["profile"]


def test_content_genre_catalog_joins_contents_to_corpus_genres() -> None:
    """装配侧 genre 表：场景内容经 item_id 连接语料条目 genre（无 id 条目缺省空）。"""
    from worldmock.runtime import _content_genre_catalog
    from worldmock.life.scenario import load_scenario

    scenario_path = Path("scenarios/white-collar-12-weeks.json")
    scenario, _ = load_scenario(scenario_path)

    genres = _content_genre_catalog(scenario_path, scenario)

    assert genres["movie-0001"] == ("剧情", "历史")
    # sintel 的身份文件（single 装载）在补入 item_id 与 genre 后成为一条真实条目：
    # 可观看内容本就该带题材，故它进表而非缺席（旧断言以"无 item_id 即缺席"为前提，已失效）。
    assert genres["sintel-en-subtitles"] == ("动画", "奇幻")
    joined = {item.item_id for item in scenario.contents} & set(genres)
    assert joined
