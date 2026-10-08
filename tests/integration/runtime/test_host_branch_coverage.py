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
from worldmock.life.world_setup import load_runtime_scenario
from worldmock.runtime import CounterfactualRequest, ResumeRequest, RunRequest, _build_society, _cleanup_runtime, _contact_profile, _execute, _intervention_sha256, _ops_crash_log, _pending_reply_requests, _prepare_new_run, _prepare_resume, _require_custom_modules, _require_runtime_identity, _require_run_shell, _terminate_failed_actor, parse_contact_reply, prepare_contact_reply, resume_world, replay_world, run_world
from worldmock.run_setup import _configure_process, _model_identity, _prepend_pythonpath, _read_deepseek_key, _require_loaded_config, runtime_code_sha256
from worldmock.status import read_status, write_status
from worldmock.run_setup import validate_stage_model_identity
from tests.integration.as2_fixtures import use_fake_as2, write_project

ROOT = Path(__file__).resolve().parents[3]
SCENARIO = ENVIRONMENT_ROOT / "scenarios" / "single-person.json"


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
        scenario=SimpleNamespace(timezone="UTC"),
        start_time=datetime(2026, 1, 5, tzinfo=ZoneInfo("UTC")),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class _Society:
    """记录调用顺序并支持脚本化失败的 Society 桩。"""

    current_time = datetime(2026, 1, 5, tzinfo=ZoneInfo("UTC"))
    step_count = 1
    token_stats = {"deepseek-flash": {"calls": 1, "input": 10, "output": 5}}

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


@pytest.mark.asyncio
async def test_entry_wrappers_delegate_and_propagate_failures(monkeypatch, tmp_path):
    """run/resume/replay 三个入口委托内部阶段，失败路径记录后原样传播。"""
    import worldmock.runtime as runtime

    seen = []

    async def capture_execute(prepared):
        seen.append(prepared)
        return SimpleNamespace(ok=True)

    monkeypatch.setattr(worldmock_run_setup_module, '_configure_process', lambda root: seen.append('configure'))
    monkeypatch.setattr(runtime, "_prepare_new_run", lambda request: "prepared-new")
    monkeypatch.setattr(runtime, "_prepare_resume", lambda request, intervention=None: (
        "prepared-resume" if intervention is None else "prepared-replay"))
    monkeypatch.setattr(runtime, "_execute", capture_execute)

    request = RunRequest(ROOT, SCENARIO, tmp_path / "run", "identity", "identity")
    await run_world(request)
    resume = ResumeRequest(ROOT, SCENARIO, tmp_path / "parent", "checkpoint", tmp_path / "child")
    await resume_world(resume)
    intervention = CounterfactualIntervention(
        kind="set_balance_fen", agent_id=1, balance_fen=0, hypothesis="h",
        source_event_ids=("e",))
    await replay_world(CounterfactualRequest(resume, intervention))
    assert seen == ["configure", "prepared-new", "configure", "prepared-resume",
                    "configure", "prepared-replay"]

    async def failing(prepared):
        raise ValueError("stage failed")

    monkeypatch.setattr(runtime, "_execute", failing)
    with pytest.raises(ValueError, match="stage failed"):
        await run_world(request)
    with pytest.raises(ValueError, match="stage failed"):
        await resume_world(resume)
    with pytest.raises(ValueError, match="stage failed"):
        await replay_world(CounterfactualRequest(resume, intervention))


# ---------------------------------------------------------------------------
# 进程配置与凭据
# ---------------------------------------------------------------------------


def _write_secret(home: Path, body: str) -> None:
    """在隔离 HOME 下写入 DeepSeek 私密配置。"""
    config = home / ".config" / "worldmock"
    config.mkdir(parents=True, exist_ok=True)
    (config / "deepseek.env").write_text(body)


def test_read_deepseek_key_enforces_private_schema(tmp_path, monkeypatch):
    """凭据文件缺失、symlink、未知键、空文件与空段各自拒绝。"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    with pytest.raises(FileNotFoundError, match="missing or unsafe"):
        _read_deepseek_key()
    _write_secret(tmp_path, "DEEPSEEK_NODE_KEYS=  \n")
    with pytest.raises(IntegrityError, match="invalid schema"):
        _read_deepseek_key()
    _write_secret(tmp_path, "DEEPSEEK_NODE_KEYS=node-secret-1\nOTHER=1\n")
    with pytest.raises(IntegrityError, match="invalid schema"):
        _read_deepseek_key()
    _write_secret(tmp_path, "DEEPSEEK_API_KEY=legacy-secret\n")
    with pytest.raises(IntegrityError, match="invalid schema"):
        _read_deepseek_key()
    _write_secret(tmp_path, "")
    with pytest.raises(IntegrityError, match="invalid schema"):
        _read_deepseek_key()
    _write_secret(tmp_path, "DEEPSEEK_NODE_KEYS=node-secret-1,,node-secret-2\n")
    with pytest.raises(IntegrityError, match="invalid schema"):
        _read_deepseek_key()
    config = tmp_path / ".config" / "worldmock"
    link = config / "deepseek.env"
    real = config / "real.env"
    real.write_text("DEEPSEEK_NODE_KEYS=node-secret-1\n")
    link.unlink()
    try:
        link.symlink_to(real)
    except OSError as error:
        if getattr(error, 'winerror', None) != 1314:
            raise
        # 没有符号链接权限的 Windows：以同名普通文件加 is_symlink 桩呈现同一形态，拒绝分支照样执行。
        link.write_text("DEEPSEEK_NODE_KEYS=node-secret-1\n")
        is_symlink = Path.is_symlink
        monkeypatch.setattr(Path, "is_symlink", lambda path: path == link or is_symlink(path))
    with pytest.raises(FileNotFoundError, match="missing or unsafe"):
        _read_deepseek_key()


def test_read_deepseek_key_strips_dedupes_and_joins_multi_key_pool(tmp_path, monkeypatch):
    """多 key 池：逐段 strip、按序去重、逗号 join（单 key 也 join）。"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    _write_secret(tmp_path, "DEEPSEEK_NODE_KEYS=node-secret-1\n")
    assert _read_deepseek_key() == "node-secret-1"
    _write_secret(tmp_path, "DEEPSEEK_NODE_KEYS= node-secret-1 , node-secret-2 \n")
    assert _read_deepseek_key() == "node-secret-1,node-secret-2"
    _write_secret(tmp_path,
                  "DEEPSEEK_NODE_KEYS=node-secret-2,node-secret-1,node-secret-2\n")
    assert _read_deepseek_key() == "node-secret-2,node-secret-1"


def test_require_loaded_config_rejects_diverged_as2_configuration(monkeypatch):
    """进程早先导入的 AS2 配置偏离时拒绝启动。"""
    from agentsociety2.config.config import Config

    saved = {name: getattr(Config, name) for name in (
        "LLM_API_BASE", "LLM_MODEL", "CODER_LLM_API_BASE", "CODER_LLM_MODEL",
        "EMBEDDING_MODEL")}
    try:
        monkeypatch.setattr(Config, "LLM_API_BASE", "https://api.deepseek.com/beta")
        monkeypatch.setattr(Config, "LLM_MODEL", "deepseek-flash")
        monkeypatch.setattr(Config, "CODER_LLM_API_BASE", "https://api.deepseek.com/beta")
        monkeypatch.setattr(Config, "CODER_LLM_MODEL", "deepseek-flash")
        monkeypatch.setattr(Config, "EMBEDDING_MODEL", "")
        _require_loaded_config()
        monkeypatch.setattr(Config, "LLM_API_BASE", "https://elsewhere.example")
        with pytest.raises(RuntimeError, match="imported before"):
            _require_loaded_config()
    finally:
        for name, value in saved.items():
            monkeypatch.setattr(Config, name, value)


def test_configure_process_sets_worker_environment(tmp_path, monkeypatch):
    """完整配置写入 worker 环境与 PYTHONPATH；工程根不完整即拒绝。"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    _write_secret(tmp_path, "DEEPSEEK_NODE_KEYS=secret-a,secret-b\n")
    with pytest.raises(FileNotFoundError, match="incomplete"):
        _configure_process(tmp_path)
    from agentsociety2.config.config import Config

    saved = {name: getattr(Config, name) for name in (
        "LLM_API_BASE", "LLM_MODEL", "CODER_LLM_API_BASE", "CODER_LLM_MODEL",
        "EMBEDDING_MODEL")}
    saved_path = os.environ.get("PYTHONPATH")
    touched = ("WORKSPACE_PATH", "AGENTSOCIETY_LLM_API_KEY", "AGENTSOCIETY_LLM_API_BASE",
               "AGENTSOCIETY_LLM_MODEL", "AGENTSOCIETY_CODER_LLM_API_KEY",
               "AGENTSOCIETY_CODER_LLM_API_BASE", "AGENTSOCIETY_CODER_LLM_MODEL",
               "AGENTSOCIETY_EMBEDDING_MODEL", "LITELLM_LOCAL_MODEL_COST_MAP",
               "AGENTSOCIETY_LLM_REQUEST_TIMEOUT", "MEM0_TELEMETRY", "ANONYMIZED_TELEMETRY")
    saved_env = {name: os.environ.get(name) for name in touched}
    try:
        monkeypatch.setattr(Config, "LLM_API_BASE", "https://api.deepseek.com/beta")
        monkeypatch.setattr(Config, "LLM_MODEL", "deepseek-flash")
        monkeypatch.setattr(Config, "CODER_LLM_API_BASE", "https://api.deepseek.com/beta")
        monkeypatch.setattr(Config, "CODER_LLM_MODEL", "deepseek-flash")
        monkeypatch.setattr(Config, "EMBEDDING_MODEL", "")
        _configure_process(ROOT)
        assert os.environ["AGENTSOCIETY_LLM_MODEL"] == "deepseek-flash"
        # AS2 的默认与 CODER 角色都指向 strict 所在的 /beta（docs/design/10-runtime.md ）。
        assert (os.environ["AGENTSOCIETY_LLM_API_BASE"], os.environ["AGENTSOCIETY_CODER_LLM_API_BASE"]) == (
            "https://api.deepseek.com/beta", "https://api.deepseek.com/beta")
        assert os.environ["AGENTSOCIETY_LLM_API_KEY"] == "secret-a,secret-b"
        entries = os.environ["PYTHONPATH"].split(os.pathsep)
        assert entries[0] == str(ROOT) and entries[1] == str(ROOT / "src")
    finally:
        for name, value in saved.items():
            monkeypatch.setattr(Config, name, value)
        if saved_path is None:
            os.environ.pop("PYTHONPATH", None)
        else:
            os.environ["PYTHONPATH"] = saved_path
        for name, value in saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def test_prepend_pythonpath_is_idempotent(tmp_path, monkeypatch):
    """PYTHONPATH 前插保持唯一且幂等。"""
    monkeypatch.setenv("PYTHONPATH", f"/already{os.pathsep}/present")
    _prepend_pythonpath(tmp_path)
    once = os.environ["PYTHONPATH"]
    _prepend_pythonpath(tmp_path)
    assert os.environ["PYTHONPATH"] == once
    assert once.split(os.pathsep)[:2] == [str(tmp_path), str(tmp_path / "src")]
    assert once.endswith("/present")


# ---------------------------------------------------------------------------
# 新建/恢复准备路径
# ---------------------------------------------------------------------------


def test_prepare_new_run_admits_scenario_and_logs_start(tmp_path):
    """新建准备产出统一启动输入并落 RUN_STARTED 操作日志。"""
    run_dir = tmp_path / "run"
    prepared = _prepare_new_run(RunRequest(ROOT, SCENARIO, run_dir, "identity", "identity"))
    assert prepared.run_id == "identity" and prepared.workspace_mode == "create"
    assert prepared.env_kwargs["open_existing"] is False
    assert prepared.env_kwargs["run_identity"]["run_id"] == "identity"
    assert prepared.scenario.scenario_id
    entry = json.loads(
        sorted((run_dir / "logs").glob("ops-*.jsonl"))[0].read_text().splitlines()[0])
    assert entry["code"] == "RUN_STARTED" and entry["run_id"] == "identity"
    assert entry["stage_model_identity"]["stages"]["action"]["thinking"] == "enabled"
    assert "two_stage_model_identity" not in entry


def test_prepare_new_run_rejects_identity_mismatch(tmp_path):
    """新建要求 run_id 与 source_group_id 一致且非空。"""
    request = RunRequest(ROOT, SCENARIO, tmp_path / "run", "identity", "other")
    with pytest.raises(ValueError, match="matching run_id"):
        _prepare_new_run(request)
    blank = RunRequest(ROOT, SCENARIO, tmp_path / "run", "  ", "  ")
    with pytest.raises(ValueError, match="matching run_id"):
        _prepare_new_run(blank)


def test_prepare_new_run_rejects_catalog_admission(tmp_path, monkeypatch):
    """目录准入七条规则任一违例即拒绝启动。"""
    import worldmock.runtime as runtime

    def reject(scenario, catalog):
        """@brief 唯一登记器的拒绝必须向真实启动准备入口传播。"""
        raise IntegrityError("Scenario rejected by catalog")

    monkeypatch.setattr("worldmock.catalog.admission.validate_scenario_against_catalog", reject)
    request = RunRequest(ROOT, SCENARIO, tmp_path / "run", "identity", "identity")
    with pytest.raises(IntegrityError, match="rejected by catalog"):
        _prepare_new_run(request)


def _parent_store(tmp_path, workspace_builder=None, snapshots_override=None):
    """建立带可恢复检查点的父运行（当前模型/代码身份）。"""
    semantics, _ = load_runtime_scenario(SCENARIO)
    identity = current_run_identity(semantics.scenario, "parent")
    engine = LifeEngine.create(SCENARIO, tmp_path / "parent", identity,
                               worldmock_run_setup_module.stage_model_identity())
    parent = engine.context.store
    state = engine.context.state.model_dump(mode="json")
    workspace = workspace_builder(tmp_path) if workspace_builder else write_agent_workspace(tmp_path / "agent")
    snapshots = snapshots_override if snapshots_override is not None else {
        "agent:1": snapshot_directory(workspace, parent.assets),
        "life": parent.assets.put_json({"state_version": state["version"]}, {"kind": "life_checkpoint"}),
    }
    return parent, parent.checkpoint(state, snapshots)


def test_prepare_resume_restores_branch_and_logs_resume(tmp_path):
    """恢复准备产出 restored 输入并落 RUN_RESUMED 操作日志。"""
    parent, checkpoint = _parent_store(tmp_path)
    try:
        destination = tmp_path / "child"
        prepared = _prepare_resume(
            ResumeRequest(ROOT, SCENARIO, tmp_path / "parent", checkpoint.checkpoint_id,
                          destination))
        assert prepared.workspace_mode == "restored" and prepared.run_id != "parent"
        assert prepared.env_kwargs["open_existing"] is True
        entry = json.loads(
            sorted((destination / "logs").glob("ops-*.jsonl"))[0].read_text().splitlines()[0])
        assert entry["code"] == "RUN_RESUMED"
        assert entry["checkpoint_id"] == checkpoint.checkpoint_id
    finally:
        parent.close()


def test_restore_directory_fails_closed_when_member_content_missing(tmp_path, monkeypatch):
    """恢复快照时成员内容缺位即 fail-closed 拒绝（防御分支取证）。

    直调原因：restore_directory 展开前已拒绝一切非普通文件成员
    （checkpoint.py 的 member.isfile() 门），tarfile.extractfile 对通过
    该门的成员恒返回非空对象——内容缺位分支无法用真实归档触发，只能
    以桩取 extractfile=None 验证 fail-closed 契约（与 body.py 空档守卫
    的 NaN 取证同型）。
    """
    import tarfile

    from worldmock.footprints.assets import AssetStore
    from worldmock.footprints.checkpoint import restore_directory

    assets = AssetStore(tmp_path / "assets")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "note.txt").write_text("hello")
    reference = snapshot_directory(workspace, assets)
    monkeypatch.setattr(tarfile.TarFile, "extractfile", lambda archive, member: None)
    with pytest.raises(IntegrityError, match="content is missing"):
        restore_directory(reference, assets, tmp_path / "restore")


def test_prepare_resume_counterfactual_binds_intervention(tmp_path):
    """反事实恢复发布前绑定干预身份到子清单。"""
    parent, checkpoint = _parent_store(tmp_path)
    try:
        intervention = CounterfactualIntervention(
            kind="set_balance_fen", agent_id=1, balance_fen=0,
            hypothesis="No money changes behavior.", source_event_ids=("event",))
        prepared = _prepare_resume(
            ResumeRequest(ROOT, SCENARIO, tmp_path / "parent", checkpoint.checkpoint_id,
                          tmp_path / "child"), intervention)
        assert prepared.intervention_sha256 == _intervention_sha256(intervention)
        manifest = json.loads((tmp_path / "child" / "manifest.json").read_text())
        assert manifest["branch_kind"] == "counterfactual"
    finally:
        parent.close()


def test_require_runtime_identity_rejects_diverged_manifest(tmp_path, monkeypatch):
    """父清单模型或代码身份（含实际导入的 AS2）偏离时拒绝恢复；git_commit 与 git_dirty 只记录，不参与比对。"""
    from worldmock.run_setup import stage_model_identity

    parent, _checkpoint = _parent_store(tmp_path)
    parent.close()
    wrong_model = {"model_identity": {"provider": "other"}, "stage_model_identity": stage_model_identity(),
                   "code_sha256": "0" * 64}
    with pytest.raises(IntegrityError, match="model identity differs"):
        _require_runtime_identity(wrong_model, ROOT)
    current = {"model_identity": _model_identity().model_dump(mode="json"),
               "stage_model_identity": stage_model_identity()}
    with pytest.raises(IntegrityError, match="code identity differs"):
        _require_runtime_identity({**current, "code_sha256": "0" * 64}, ROOT)
    parent_code = runtime_code_sha256(ROOT)
    for git_commit, git_dirty in ((None, None), ("f" * 40, True), ("0" * 40, False)):
        _require_runtime_identity(
            {**current, "code_sha256": parent_code, "git_commit": git_commit, "git_dirty": git_dirty}, ROOT)
    other_as2 = {"__init__.py": b"VERSION = 'other'\n", "society/society.py": b"def step():\n    return 2\n"}
    use_fake_as2(monkeypatch, tmp_path / "other-as2-site", other_as2)
    with pytest.raises(IntegrityError, match="code identity differs"):
        _require_runtime_identity({**current, "code_sha256": parent_code}, ROOT)


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
                            scenario=SimpleNamespace(timezone="Bogus/Zone"))
    with pytest.raises(ZoneInfoNotFoundError):
        ZoneInfo("Bogus/Zone")
    _ops_crash_log(broken, None, ValueError("swallowed original"))


@pytest.mark.asyncio
async def test_execute_requires_intervention_identity(monkeypatch, tmp_path):
    """干预已设置但身份缺失时拒绝进入运行。"""
    import worldmock.runtime as runtime
    from agentsociety2.config import llm_dispatcher

    monkeypatch.setattr(llm_dispatcher, "init_dispatchers", _no_dispatchers)
    monkeypatch.setattr(runtime, "_build_society", lambda prepared: (None, _Society()))
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
    monkeypatch.setattr(runtime, "_build_society", lambda prepared: (None, single))
    monkeypatch.setattr(ray, "is_initialized", lambda: False)
    with pytest.raises(OSError, match="close failed"):
        await _execute(_prepared_stub(tmp_path))

    both = _Society(fail_close=OSError("close failed"))
    monkeypatch.setattr(runtime, "_build_society", lambda prepared: (None, both))
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


def test_persona_compile_skips_and_renders(tmp_path, monkeypatch):
    """无背景故事不编译；有故事经编译得到编译产物（卡片与编译警告一起返回，警告不再丢弃），渲染文本取自同一张卡片。"""
    quiet = SimpleNamespace(background_story=None)
    assert worldmock_run_setup_module._compile_persona(quiet) is None
    card = SimpleNamespace()
    compiled = SimpleNamespace(card=card, warnings=("field_agreement_below_two_thirds: ['coping']",))
    monkeypatch.setattr("worldmock.profile.compile.compile_persona", lambda story: compiled)
    monkeypatch.setattr("worldmock.profile.textgen.render_card", lambda value: "rendered-card")
    story_agent = SimpleNamespace(background_story="陈曦，产品经理。")
    assert worldmock_run_setup_module._compile_persona(story_agent) is compiled
    assert worldmock_run_setup_module._compiled_parts(compiled) == (card, compiled.warnings)
    assert worldmock_run_setup_module._render_card(card) == "rendered-card"


# ---------------------------------------------------------------------------
# 联系人回复客户端与请求结算
# ---------------------------------------------------------------------------


class _StubAS2Client:
    """AS2 客户端桩：返回脚本化响应形态。"""

    model_name = "deepseek-flash"
    base_url = "https://api.deepseek.com/beta"

    def __init__(self, responses):
        self._responses = list(responses)

    async def call(self, **_request):
        return self._responses.pop(0)


def test_contact_reply_client_enforces_manifest_identity(monkeypatch):
    """回复客户端身份必须与运行清单完全一致。"""
    from worldmock.runtime import ContactReplyClient

    identity = {"requested_model": "deepseek-flash",
                "base_url": "https://api.deepseek.com/beta", "max_retries": 0}
    monkeypatch.setattr("agentsociety2.config.build_client_for_role", lambda role: SimpleNamespace(
        model_name="deepseek-flash", base_url="https://api.deepseek.com/beta",
        call=None))
    client = ContactReplyClient(identity)
    assert client.model_name == "deepseek-flash"
    monkeypatch.setattr("agentsociety2.config.build_client_for_role", lambda role: SimpleNamespace(
        model_name="other-model", base_url="https://api.deepseek.com/beta", call=None))
    with pytest.raises(ProtocolError, match="identity disagrees"):
        ContactReplyClient(identity)
    monkeypatch.setattr("agentsociety2.config.build_client_for_role", lambda role: SimpleNamespace(
        model_name="deepseek-flash", base_url="https://api.deepseek.com/beta", call=None))
    with pytest.raises(ProtocolError, match="parameters disagree"):
        ContactReplyClient({**identity, "max_retries": 3})


@pytest.mark.asyncio
async def test_contact_reply_client_serializes_model_responses(monkeypatch):
    """模型响应按 pydantic/dict 形态序列化，异常形态拒绝。"""
    from worldmock.runtime import ContactReplyClient

    identity = {"requested_model": "deepseek-flash",
                "base_url": "https://api.deepseek.com/beta", "max_retries": 0}
    as2_client = _StubAS2Client([
        SimpleNamespace(model_dump=lambda mode: {"choices": []}),
        {"choices": []},
        12345,
        SimpleNamespace(model_dump=lambda mode: ["not-an-object"]),
    ])
    monkeypatch.setattr("agentsociety2.config.build_client_for_role",
                        lambda role: as2_client)
    client = ContactReplyClient(identity)
    assert await client.call({"model": "deepseek-flash"}) == {"choices": []}
    assert await client.call({}) == {"choices": []}
    with pytest.raises(ProtocolError, match="not serializable"):
        await client.call({})
    with pytest.raises(ProtocolError, match="not a serialized object"):
        await client.call({})


def test_parse_contact_reply_accepts_single_choice_text_only():
    """回复解析只接受唯一 choice 且文本非空。"""
    good = {"choices": [{"message": {"content": "  好啊，老地方见。  "}}]}
    assert parse_contact_reply(good) == "好啊，老地方见。"
    for bad in ({}, {"choices": []}, {"choices": [{}, {}]},
                {"choices": ["not-a-dict"]},
                {"choices": [{"message": {"content": "   "}}]},
                {"choices": [{"message": "not-a-dict"}]},
                {"choices": [{"message": {"content": None}}]}):
        with pytest.raises(ProtocolError):
            parse_contact_reply(bad)


def test_prepare_contact_reply_builds_disabled_thinking_request():
    """联系人回复请求独立成调用：thinking 关闭、无工具、无重试、reasoning 上限；载荷带来信事实（发件人姓名、场景本地
    时刻、presence=remote、收货地名）、双方往来与对方收到的物品；system 提示写明联系人只通过消息来往。"""
    from worldmock.life.social import ReceivedItem, ReplyHistoryEntry
    from worldmock.runtime import _CONTACT_REPLY_PROMPT

    history = (ReplyHistoryEntry(speaker="陈曦", time="10:00", text="在吗"),)
    received = (ReceivedItem(item="牛肉炒面", quantity="1 份", time="11:05"),)
    request = SimpleNamespace(contact_id="contact-1", incoming_text="周五晚上一起吃饭？",
                              sender_name="陈曦", now="2026-01-05T11:10:00+08:00", presence="remote",
                              receive_location="公司", max_chars=200, history=history, received=received)
    payload = prepare_contact_reply(request, None, "deepseek-flash")
    assert payload["model"] == "deepseek-flash"
    assert payload["extra_body"] == {"thinking": {"type": "disabled"}}
    assert payload["max_retries"] == 0 and payload["stream"] is False
    assert payload["max_tokens"] == 8000
    assert [message["role"] for message in payload["messages"]] == ["system", "user"]
    assert payload["messages"][0]["content"] == _CONTACT_REPLY_PROMPT
    assert "只通过消息" in _CONTACT_REPLY_PROMPT and "不约当面" in _CONTACT_REPLY_PROMPT
    body = json.loads(payload["messages"][1]["content"])
    assert body["contact"] == {"contact_id": "contact-1"}
    assert (body["sender_name"], body["now"], body["presence"], body["receive_location"]) == (
        "陈曦", "2026-01-05T11:10:00+08:00", "remote", "公司")
    assert body["incoming_message"] == "周五晚上一起吃饭？"
    assert body["history"] == [{"speaker": "陈曦", "time": "10:00", "text": "在吗"}]
    assert body["received"] == [{"item": "牛肉炒面", "quantity": "1 份", "time": "11:05"}]
    assert body["reply_char_limit"] == 200
    profile = {"contact_id": "contact-1", "salutation": "老陈", "relation": "old_friend",
               "closeness": 0.8, "channels": ["wechat"]}
    with_contact = prepare_contact_reply(request, profile, "deepseek-flash")
    assert json.loads(with_contact["messages"][1]["content"])["contact"] == profile


def test_contact_profile_resolves_declared_contacts_only():
    """联系人档案只在场景声明中查找，未声明返回 None。"""
    contact = SimpleNamespace(contact_id="contact-1", salutation="老陈", relation="old_friend",
                              closeness=0.8, channels=["wechat"])
    engine = SimpleNamespace(context=SimpleNamespace(semantics=SimpleNamespace(
        scenario=SimpleNamespace(agents=[SimpleNamespace(contacts=[contact])]))))
    request = SimpleNamespace(contact_id="contact-1")
    found = _contact_profile(engine, request)
    assert found == {"contact_id": "contact-1", "salutation": "老陈",
                     "relation": "old_friend", "closeness": 0.8, "channels": ["wechat"]}
    assert _contact_profile(engine, SimpleNamespace(contact_id="unknown")) is None


class _ReplyEvent:
    """足迹事件桩：按 data 形态参与 pending 扫描。"""

    def __init__(self, kind, data):
        self.event_type = "environment_event"
        self.data = {"kind": kind, **data}


def test_pending_reply_requests_deduplicate_settled_replies():
    """pending 扫描只保留尚未回复的请求（幂等去重）。"""
    request = {"contact_id": "contact-1", "reply_to_message_id": "message-1",
               "recipient_id": "agent:1", "incoming_text": "在吗？", "sender_name": "陈曦",
               "now": "2026-01-05T10:00:00+08:00", "receive_location": None, "history": [], "received": []}
    second = dict(request, reply_to_message_id="message-2")
    events = [
        _ReplyEvent("model_npc_reply_pending", {"request": request}),
        _ReplyEvent("model_npc_reply_pending", {"request": second}),
        _ReplyEvent("model_npc_reply", {"reply_to_message_id": "message-2"}),
        _ReplyEvent("message_arrival", {"message_id": "other"}),
        SimpleNamespace(event_type="model_response", data={}),
    ]
    engine = SimpleNamespace(context=SimpleNamespace(store=SimpleNamespace(read_events=lambda seq: events,
                                                                           last_event_seq=len(events))))
    pending = _pending_reply_requests(engine)
    assert [item[1].reply_to_message_id for item in pending] == ["message-1"]


@pytest.mark.asyncio
async def test_settle_pending_model_replies_builds_client_from_manifest(tmp_path, monkeypatch):
    """无待回复时零副作用；有待回复且未注入客户端时按清单构造客户端。"""
    import worldmock.runtime as runtime

    empty_engine = SimpleNamespace(context=SimpleNamespace(store=SimpleNamespace(
        read_events=lambda seq: [], last_event_seq=0)))
    assert await runtime.settle_pending_model_replies(empty_engine) == []

    built = []

    class RecordingClient:
        model_name = "deepseek-flash"

        def __init__(self, model_identity):
            built.append(model_identity)

        async def call(self, request):
            return {"choices": [{"message": {"content": "好啊，老地方见。"}}]}

    from tests.life.conftest import context
    from worldmock.life.engine import LifeEngine
    from worldmock.life.models import RunIdentity, SendMessageActionRequest
    from worldmock.life.world_setup import load_scenario

    from tests.engine_fixture import _mutated_scenario
    contact = {"contact_id": "reply-colleague", "salutation": "沈昊", "relation": "colleague",
               "closeness": 15, "channels": ["message", "phone"], "reply_policy": "model",
               "receive_location_id": "office"}
    scenario_path = _mutated_scenario(SCENARIO, lambda document: document["agents"][0].update(contacts=[contact]),
                                     tmp_path / "scenario")
    scenario, _ = load_scenario(scenario_path)
    identity = RunIdentity(
        run_id="settle-direct", source_group_id="group-settle-direct",
        agent_ids=tuple(item.agent_id for item in scenario.agents),
        start_at=scenario.start_at.isoformat(), end_at=scenario.end_at.isoformat(),
        timezone=scenario.timezone, code_sha256="4" * 64,
        model_identity=_model_identity())
    engine = LifeEngine.create(scenario_path, tmp_path / "run", identity)
    try:
        from agentsociety2.society import DecisionContext, DirectToolCall
        from custom.envs.life_env import LifeEnv

        env = LifeEnv()
        env.engine = engine
        ctx = context(engine, "settle-direct")
        await env.begin_decision_round((ctx,))
        await env.observe(ctx)
        await env.submit_action(DirectToolCall(
            ctx, "action-settle", "send_message",
            SendMessageActionRequest(recipient_name="联系人：沈昊",
                                     text="周五晚上一起吃饭？")))
        monkeypatch.setattr(runtime, "ContactReplyClient", RecordingClient)
        results = await env.commit_decision_round("settle-direct")
        assert results[0].status == "completed"
        # 回复登记为预排送达（裁决 #21）：本轮提交后尚未进入收件箱。
        assert not [item for item in engine.context.state.agents[1].inbox.values()
                    if item.sender_id == "reply-colleague"]
        replies = [item.data["message"] for item in engine.context.state.pending_events if item.kind == "message_arrival"]
        assert len(replies) == 1 and replies[0]["text"] == "好啊，老地方见。"
        assert replies[0]["sender_id"] == "reply-colleague"
        assert built and built[0]["requested_model"] == "deepseek-flash"
    finally:
        engine.close()


# ---------------------------------------------------------------------------
# 零点计划客户端与后台任务（docs/design/04-simulation-engine.md 第四章「执行位置」）
# ---------------------------------------------------------------------------


def test_plan_day_client_enforces_manifest_identity(monkeypatch):
    """零点计划客户端身份与参数必须与运行清单完全一致（核对同联系人回复，类名独立）。"""
    from worldmock.runtime import PlanDayClient

    identity = {"requested_model": "deepseek-flash",
                "base_url": "https://api.deepseek.com/beta", "max_retries": 0}
    monkeypatch.setattr("agentsociety2.config.build_client_for_role", lambda role: SimpleNamespace(
        model_name="deepseek-flash", base_url="https://api.deepseek.com/beta", call=None))
    assert PlanDayClient(identity).model_name == "deepseek-flash"
    monkeypatch.setattr("agentsociety2.config.build_client_for_role", lambda role: SimpleNamespace(
        model_name="deepseek-flash", base_url="https://example.invalid", call=None))
    with pytest.raises(ProtocolError, match="Plan day client identity disagrees"):
        PlanDayClient(identity)
    monkeypatch.setattr("agentsociety2.config.build_client_for_role", lambda role: SimpleNamespace(
        model_name="deepseek-flash", base_url="https://api.deepseek.com/beta", call=None))
    with pytest.raises(ProtocolError, match="Plan day request parameters disagree"):
        PlanDayClient({**identity, "max_retries": 3})


@pytest.mark.asyncio
async def test_plan_day_client_serializes_model_responses(monkeypatch):
    """零点计划模型响应按 pydantic/dict 形态序列化，异常形态拒绝。"""
    from worldmock.runtime import PlanDayClient

    identity = {"requested_model": "deepseek-flash",
                "base_url": "https://api.deepseek.com/beta", "max_retries": 0}
    as2_client = _StubAS2Client([
        SimpleNamespace(model_dump=lambda mode: {"choices": []}),
        {"choices": []},
        12345,
        SimpleNamespace(model_dump=lambda mode: ["not-an-object"]),
    ])
    monkeypatch.setattr("agentsociety2.config.build_client_for_role", lambda role: as2_client)
    client = PlanDayClient(identity)
    assert await client.call({"model": "deepseek-flash"}) == {"choices": []}
    assert await client.call({}) == {"choices": []}
    with pytest.raises(ProtocolError, match="not serializable"):
        await client.call({})
    with pytest.raises(ProtocolError, match="not a serialized object"):
        await client.call({})


@pytest.mark.asyncio
async def test_settle_pending_day_plans_builds_client_from_manifest_only_when_needed(tmp_path, monkeypatch):
    """有人缺当日计划且未注入客户端时按清单构造客户端；运行到终点或无人缺计划时不构造、零副作用。"""
    import worldmock.runtime as runtime
    from tests.life.conftest import StubPlanDayClient
    from worldmock.life.engine import LifeEngine
    from worldmock.life.models import RunIdentity
    from worldmock.life.world_setup import load_scenario

    built = []

    class RecordingClient(StubPlanDayClient):
        def __init__(self, model_identity):
            super().__init__()
            built.append(model_identity)

    scenario, _ = load_scenario(SCENARIO)
    identity = RunIdentity(
        run_id="plan-direct", source_group_id="group-plan-direct",
        agent_ids=tuple(item.agent_id for item in scenario.agents),
        start_at=scenario.start_at.isoformat(), end_at=scenario.end_at.isoformat(),
        timezone=scenario.timezone, code_sha256="4" * 64, model_identity=_model_identity())
    engine = LifeEngine.create(SCENARIO, tmp_path / "run", identity)
    try:
        monkeypatch.setattr(runtime, "PlanDayClient", RecordingClient)
        assert await runtime.settle_pending_day_plans(engine) == [{"agent_id": 1, "date": "2026-01-05", "goals": 0}]
        assert built == [engine.context.store.manifest["model_identity"]]
        assert await runtime.settle_pending_day_plans(engine) == []
        ended = SimpleNamespace(context=SimpleNamespace(
            state=engine.context.state.model_copy(update={"current_time": engine.context.state.end_time}),
            scenario=engine.context.scenario))
        assert await runtime.settle_pending_day_plans(ended) == []
        assert len(built) == 1
    finally:
        engine.close()


# ---------------------------------------------------------------------------
# opslog 边界
# ---------------------------------------------------------------------------


def test_opslog_accepts_naive_datetime_and_reports_close_failures(tmp_path, capsys):
    """朴素 datetime 按 UTC 解释；关闭失败只记 stderr。"""
    from worldmock.opslog import OpsLogger

    writer = OpsLogger(tmp_path, "Asia/Shanghai")
    writer.log(datetime(2026, 1, 5, 16, 30), "runtime", "info", "PROBE_NAIVE", "probe")
    day = "20260106"  # UTC 16:30+8 = 次日 00:30 上海
    writer._handles[day] = _FailingHandle()
    writer.close()
    output = capsys.readouterr().err
    assert "opslog close failed" in output
    logger = OpsLogger(tmp_path, "UTC")
    logger.log(datetime(2026, 1, 5, 1, 0), "runtime", "info", "PROBE_NAIVE_UTC", "probe")
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


def test_stage_identity_rejects_retries_and_reasoning_effort():
    """阶段身份一致性：重试非零与发送 reasoning_effort 均拒绝。"""
    from worldmock.run_setup import stage_model_identity

    actual = stage_model_identity()
    retries = copy_dict(actual)
    retries["max_retries"] = 2
    with pytest.raises(IntegrityError, match="max_retries"):
        validate_stage_model_identity(retries)
    reasoning = copy_dict(actual)
    reasoning["reasoning_effort"] = "high"
    with pytest.raises(IntegrityError, match="reasoning_effort"):
        validate_stage_model_identity(reasoning)


def copy_dict(value):
    """测试内使用的浅拷贝。"""
    import copy as _copy

    return _copy.deepcopy(value)




def test_validate_persona_coverage_uses_registered_catalog_and_skips_plain_agents(tmp_path):
    """真实登记目录可用；未声明背景故事的人物没有卡片，不产生画像报告与输入警告。"""
    from worldmock.life.world_setup import load_scenario
    from worldmock.validation import validate_persona_coverage

    source = load_scenario(SCENARIO)[0]
    scenario = source.model_copy(update={"agents": tuple(agent.model_copy(update={"background_story": None})
                                                          for agent in source.agents)})
    document = validate_persona_coverage(
        SCENARIO, _scenario=scenario,
        coverage_out=tmp_path / "persona_coverage.json")
    assert document["agents"] == [] and document["input_warnings"] == []
    assert json.loads((tmp_path / "persona_coverage.json").read_text())["threshold"] == 0.8


def test_persona_coverage_targets_read_real_registered_goods_apps_and_action_names():
    """@brief 画像覆盖直接取真实登记目录，食品、应用和动作名称与运行一致。"""
    from worldmock.exchange.types import GoodsCategory
    from worldmock.profile.coverage_targets import build_coverage_targets

    semantics, _ = load_runtime_scenario(SCENARIO)
    targets = build_coverage_targets(semantics.scenario, semantics.catalog)
    assert targets.food_kinds == tuple(sorted(goods.name for goods in semantics.catalog.goods.values()
                                              if goods.category in {GoodsCategory.MEAL, GoodsCategory.SNACK, GoodsCategory.DRINK}))
    assert targets.app_names == tuple(sorted(app.name for app in semantics.catalog.apps.values()))
    assert targets.action_names == tuple(sorted(action.name for action in semantics.catalog.actions.values()))
    assert "media_kinds" not in type(targets).model_fields
    assert set(targets.action_names) <= set(targets.habit_response_keywords)
    assert set(place.display_name for place in semantics.catalog.locations.values()) <= set(targets.habit_response_keywords)


# ---------------------------------------------------------------------------
# cli 命令出口
# ---------------------------------------------------------------------------


def _run_result(run_id="identity", run_dir=None):
    """构造可序列化的运行结果。"""
    from worldmock.runtime import RunResult

    return RunResult(run_id=run_id, run_dir=run_dir or Path("/tmp/run"),
                     current_time=datetime(2026, 1, 5, tzinfo=timezone.utc),
                     step_count=2, token_stats={"deepseek-flash": {
                         "calls": 1, "input": 10, "output": 5}})


def test_cli_main_exit_codes_for_success_interrupt_and_failure(monkeypatch, capsys):
    """main 成功打印 JSON 返回 0；中断 130；失败 1。"""
    from worldmock import cli

    monkeypatch.setattr(cli, "_dispatch", lambda arguments: {"status": "ok"})
    assert cli.main(["status", "--run-dir", "/tmp/x"]) == 0
    assert json.loads(capsys.readouterr().out) == {"status": "ok"}

    def interrupt(arguments):
        raise KeyboardInterrupt()

    monkeypatch.setattr(cli, "_dispatch", interrupt)
    assert cli.main(["status", "--run-dir", "/tmp/x"]) == 130

    def broken(arguments):
        raise ValueError("boom")

    monkeypatch.setattr(cli, "_dispatch", broken)
    assert cli.main(["status", "--run-dir", "/tmp/x"]) == 1


def test_cli_dispatch_reads_status_and_exports_run(tmp_path, capsys):
    """status 与 export 命令走真实只读视图与导出边界。"""
    from worldmock import cli

    store, run_dir = _status_store(tmp_path)
    try:
        result = cli._dispatch(cli.build_parser().parse_args(
            ["status", "--run-dir", str(run_dir)]))
        assert result["run_id"] == "probe"
    finally:
        store.close()

    from datetime import timedelta
    from tests.life.conftest import advance_next, stage_day_plan, start_activity, submit_round
    from tests.life.scenario_fixtures import read_scenario, write_scenario

    document = read_scenario("single-person.json")
    document["end_at"] = (datetime.fromisoformat(document["start_at"]) + timedelta(seconds=900)).isoformat()
    document["messages"], document["payrolls"], document["bills"] = [], [], []
    scenario_path = write_scenario(document, tmp_path / "export-environment")
    semantics, _ = load_runtime_scenario(scenario_path)
    export_dir = tmp_path / "export-run"
    engine = LifeEngine.create(scenario_path, export_dir, current_run_identity(semantics.scenario, "export-run"))
    try:
        stage_day_plan(engine, 1)
        _, started = submit_round(engine, {1: ("start_activity", start_activity("等待", "自己"))}, "export-wait")
        assert started[0].status == "in_progress"
        advance_next(engine)
        assert engine.context.state.current_time == engine.context.state.end_time
        events = engine.context.store.read_events(engine.context.store.last_event_seq)
        ended = next(event for event in events if event.event_type == "run_ended")
        assert ended.source_kind == "environment" and ended.visibility == "system"
        requested = next(event for event in events if event.event_type == "action_requested")
        assert requested.data["tool_name"] == "start_activity"
        assert requested.data["request"]["items"][0]["way"] == "等待"
        workspace = write_agent_workspace(tmp_path / "export-agent", initialized=True)
        reference = snapshot_directory(workspace, engine.context.store.assets)
        point = engine.checkpoint({"agent:1": reference})
        output = tmp_path / "exported"
        result = cli._dispatch(cli.build_parser().parse_args(
            ["export", "--run-dir", str(export_dir), "--cutoff", str(point.last_event_seq),
             "--output", str(output)]))
        assert result["run_id"] == "export-run"
        assert (output / "events.jsonl").is_file()
    finally:
        engine.close()


def test_cli_dispatch_validates_and_rejects_dataset_processing(monkeypatch, tmp_path):
    """@brief 模拟校验仍连接真实入口，数据集加工命令不属于引擎。"""
    from worldmock import cli
    from worldmock import validation as validation_module
    monkeypatch.setattr(validation_module, "validate_project",
                        lambda root, scenario, coverage_out=None: {"valid": True})
    result = cli._dispatch(cli.build_parser().parse_args(
        ["validate", "--scenario", str(SCENARIO)]))
    assert result == {"valid": True}

    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["prepare-corpora", "--output", str(tmp_path / "corpora")])

    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["deliver", "--export", str(tmp_path / "export"),
                                      "--output", str(tmp_path / "dataset")])

def test_cli_run_command_writes_receipt_and_final_status(monkeypatch, tmp_path):
    """run 命令：成功写 result.json 与 completed 状态；失败写 failed 状态。"""
    from worldmock import cli
    import worldmock.runtime as runtime

    async def succeed(request):
        assert request.run_id == request.source_group_id
        return _run_result(request.run_id, request.run_dir)

    monkeypatch.setattr(runtime, "run_world", succeed)
    output = tmp_path / "run-output"
    receipt = cli._run_command(cli.build_parser().parse_args(
        ["run", "--scenario", str(SCENARIO), "--output", str(output)]))
    assert len(receipt["run_id"]) == 32 and receipt["step_count"] == 2
    assert json.loads((output / "result.json").read_text())["token_stats"] == receipt["token_stats"]
    assert json.loads((output / "status.json").read_text())["status"] == "completed"

    occupied = tmp_path / "occupied"
    occupied.mkdir()
    with pytest.raises(IntegrityError, match="already exists"):
        cli._run_command(cli.build_parser().parse_args(
            ["run", "--scenario", str(SCENARIO), "--output", str(occupied)]))

    async def failing(request):
        raise ValueError("engine exploded")

    monkeypatch.setattr(runtime, "run_world", failing)
    failed = tmp_path / "run-failed"
    with pytest.raises(ValueError, match="engine exploded"):
        cli._run_command(cli.build_parser().parse_args(
            ["run", "--scenario", str(SCENARIO), "--output", str(failed)]))
    status = json.loads((failed / "status.json").read_text())
    assert status == {"status": "failed", "phase": "stopped", "error_type": "ValueError"}

    blocked = tmp_path / "blocked"
    blocked.write_text("occupies the parent path")
    unwritable = blocked / "run-unwritable"
    with pytest.raises(Exception):
        cli._run_command(cli.build_parser().parse_args(
            ["run", "--scenario", str(SCENARIO), "--output", str(unwritable)]))
    assert not unwritable.exists()

    async def interrupted(request):
        raise KeyboardInterrupt()

    monkeypatch.setattr(runtime, "run_world", interrupted)
    stopped = tmp_path / "run-interrupted"
    with pytest.raises(KeyboardInterrupt):
        cli._run_command(cli.build_parser().parse_args(
            ["run", "--scenario", str(SCENARIO), "--output", str(stopped)]))
    assert json.loads((stopped / "status.json").read_text())["status"] == "interrupted"


def test_cli_resume_and_replay_commands_restore_explicitly(monkeypatch, tmp_path):
    """resume/replay 命令走真实恢复入口并读取干预文件。"""
    from worldmock import cli
    import worldmock.runtime as runtime

    parent = tmp_path / "parent"
    parent.mkdir()
    seen = {}

    async def resumed(request):
        seen["resume"] = (request.parent_run_dir, request.checkpoint_id)
        return _run_result("branch", request.run_dir)

    monkeypatch.setattr(runtime, "resume_world", resumed)
    output = tmp_path / "branch"
    receipt = cli._run_command(cli.build_parser().parse_args(
        ["resume", "--scenario", str(SCENARIO), "--output", str(output),
         "--parent", str(parent), "--checkpoint", "checkpoint-1"]))
    assert seen["resume"] == (parent.resolve(), "checkpoint-1")
    assert receipt["run_id"] == "branch"
    assert json.loads((output / "status.json").read_text())["status"] == "completed"

    async def replayed(request):
        seen["replay"] = (request.base.checkpoint_id, request.intervention.hypothesis)
        return _run_result("counterfactual", request.base.run_dir)

    monkeypatch.setattr(runtime, "replay_world", replayed)
    intervention = tmp_path / "intervention.json"
    intervention.write_text(json.dumps({
        "kind": "set_balance_fen", "agent_id": 1, "balance_fen": 0,
        "hypothesis": "No money changes the next purchase.",
        "source_event_ids": ["event-1"]}))
    replay_output = tmp_path / "counterfactual"
    cli._run_command(cli.build_parser().parse_args(
        ["replay", "--scenario", str(SCENARIO), "--output", str(replay_output),
         "--parent", str(parent), "--checkpoint", "checkpoint-1",
         "--intervention", str(intervention)]))
    assert seen["replay"] == ("checkpoint-1", "No money changes the next purchase.")


def test_cli_evaluate_command_branches(monkeypatch, tmp_path):
    """evaluate：reviews 专属；reviews 与他参互斥；缺参拒绝；全参走准备。"""
    from worldmock import cli
    from worldmock.evaluation import runner

    monkeypatch.setattr(runner, "finalize_evaluation",
                        lambda output, reviews: {"finalized": len(reviews)})
    result = cli._dispatch(cli.build_parser().parse_args(
        ["evaluate", "--output", str(tmp_path / "o"), "--reviews", "a.jsonl", "b.jsonl"]))
    assert result == {"finalized": 2}
    with pytest.raises(IntegrityError, match="accepts only"):
        cli._dispatch(cli.build_parser().parse_args(
            ["evaluate", "--output", str(tmp_path / "o"), "--reviews", "a.jsonl",
             "--worldmock", "wm"]))
    with pytest.raises(IntegrityError, match="requires"):
        cli._dispatch(cli.build_parser().parse_args(
            ["evaluate", "--output", str(tmp_path / "o")]))
    monkeypatch.setattr(runner, "prepare_evaluation",
                        lambda legacy, worldmock, scenario, output, human: {"prepared": True})
    result = cli._dispatch(cli.build_parser().parse_args(
        ["evaluate", "--worldmock", "wm", "--legacy", "lg", "--scenario", str(SCENARIO),
         "--output", str(tmp_path / "o")]))
    assert result == {"prepared": True}


def test_cli_profile_reads_stdin_or_reports_missing_file(monkeypatch, capsys):
    """profile 文本可来自 stdin；文件缺失返回失败退出。"""
    import io
    import sys

    from worldmock import cli
    from worldmock.profile import extractor
    from tests.profile.conftest import make_card

    def fake_extraction(text, n_runs):
        from tests.integration.footprints.test_validation import _fake_extraction

        return _fake_extraction(make_card())

    monkeypatch.setattr(extractor, "extract_persona", fake_extraction)
    original = sys.stdin
    sys.stdin = io.StringIO("陈曦，32岁，产品经理，住在上海张江。")
    try:
        assert cli.main(["profile", "compile", "--text", "-"]) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["persona_card_text"].startswith("产品经理")
    finally:
        sys.stdin = original
    assert cli.main(["profile", "compile", "--text", "/absent/story.txt"]) == 1


def test_cli_profile_compile_writes_output_file(tmp_path, monkeypatch, capsys):
    """profile compile --out 落盘结果并返回写入回执。"""
    from worldmock import cli
    from worldmock.profile import extractor
    from tests.integration.footprints.test_validation import _fake_extraction
    from tests.profile.conftest import make_card

    story = tmp_path / "story.txt"
    story.write_text("陈曦，32岁，产品经理，住在上海张江。", encoding="utf-8")
    monkeypatch.setattr(extractor, "extract_persona",
                        lambda text, n_runs: _fake_extraction(make_card()))
    out = tmp_path / "card.json"
    assert cli.main(["profile", "compile", "--text", str(story), "--out", str(out)]) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["written"] == str(out) and receipt["warnings"] == []
    assert json.loads(out.read_text(encoding="utf-8"))["schema_version"] == 3


# ---------------------------------------------------------------------------
# 恢复发布前的严格工作区校验与状态墙钟
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("workspace_builder, message", [
    (lambda tmp: write_agent_workspace(tmp / "agent", missing=("AGENT.json",)),
     "artifact is missing or unsafe"),
    (lambda tmp: write_agent_workspace(tmp / "agent", agent_document="{damaged", config_document={}),
     "artifact is invalid"),
    (lambda tmp: write_agent_workspace(tmp / "agent", agent_document={"schema_version": 2,
                                                       "agent_class": "LifeAgent",
                                                       "agent_id": 1, "initialized_at": None},
                                   config_document={}),
     "does not match the manifest"),
    (lambda tmp: write_agent_workspace(tmp / "agent", config_document={"enable_memory": False}),
     "must keep memory enabled"),
])
def test_restore_rejects_invalid_agent_workspaces(tmp_path, workspace_builder, message):
    """身份工件缺失/损坏、身份不符与记忆关闭都阻止分支发布。"""
    parent, checkpoint = _parent_store(tmp_path, workspace_builder=workspace_builder)
    try:
        with pytest.raises(IntegrityError, match=message):
            _prepare_resume(ResumeRequest(ROOT, SCENARIO, tmp_path / "parent",
                                          checkpoint.checkpoint_id, tmp_path / "child"))
        assert not (tmp_path / "child").exists()
    finally:
        parent.close()


@pytest.mark.parametrize("budget", [None, 10, 17, 16.0, "16", True, False])
def test_restore_rejects_missing_changed_or_wrong_type_agent_budget(tmp_path, budget):
    """恢复清单虽正确，人物工作区缺失、错值或错类型的预算仍阻止分支发布。"""
    config = {"enable_memory": True}
    if budget is not None:
        config["max_react_turns"] = budget
    parent, checkpoint = _parent_store(
        tmp_path, workspace_builder=lambda path: write_agent_workspace(path / "agent", config_document=config))
    try:
        with pytest.raises(IntegrityError, match="max_react_turns must match"):
            _prepare_resume(ResumeRequest(ROOT, SCENARIO, tmp_path / "parent",
                                          checkpoint.checkpoint_id, tmp_path / "child"))
        assert not (tmp_path / "child").exists()
    finally:
        parent.close()


@pytest.mark.asyncio
async def test_restored_life_agent_reads_the_frozen_budget_from_its_real_workspace(tmp_path):
    """生产配置经检查点恢复后，由实际 LifeAgent.restore 读文件得到十六回合预算。"""
    from custom.agents.life_agent import LifeAgent
    from worldmock.life.world_setup import load_scenario
    from worldmock.run_setup import _agent_specs, stage_model_identity

    scenario, _ = load_scenario(SCENARIO)
    spec = _agent_specs(scenario, persona_compiler=lambda _: None)[0]

    def workspace_builder(path):
        """用正式人物 create 写入将被打包进检查点的配置文件。"""
        workspace = path / "agent"
        LifeAgent.create(workspace, spec["profile"], spec["config"])
        return workspace

    parent, checkpoint = _parent_store(tmp_path, workspace_builder=workspace_builder)
    try:
        destination = tmp_path / "child"
        _prepare_resume(ResumeRequest(ROOT, SCENARIO, tmp_path / "parent",
                                      checkpoint.checkpoint_id, destination))
        workspace = destination / "as2" / "agents" / "agent_0001"
        service = SimpleNamespace(env=SimpleNamespace(), trace=None,
                                  llm=SimpleNamespace(default=SimpleNamespace(model_name="offline-model")))
        agent = await LifeAgent.from_workspace(workspace, service)
        assert agent._max_react_turns == stage_model_identity()["max_react_turns"] == 16
        assert agent._config["max_react_turns"] == 16
        assert json.loads((workspace / "config.json").read_text())["max_react_turns"] == 16
    finally:
        parent.close()


def test_restore_agents_requires_every_agent_snapshot(tmp_path):
    """检查点缺少人物工作区快照即拒绝（store 层已挡不完整检查点，直击装配分支）。"""
    from worldmock.runtime import _restore_agents

    parent, _ = _parent_store(tmp_path)
    try:
        crafted = SimpleNamespace(snapshots={"life": "reference"})
        with pytest.raises(IntegrityError, match="missing an agent workspace snapshot"):
            _restore_agents(crafted, parent, tmp_path)
    finally:
        parent.close()


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


def test_cli_dispatch_routes_run_command(tmp_path, monkeypatch):
    """run 命令经统一分发进入运行路径。"""
    from worldmock import cli
    import worldmock.runtime as runtime

    async def succeed(request):
        return _run_result(request.run_id, request.run_dir)

    monkeypatch.setattr(runtime, "run_world", succeed)
    output = tmp_path / "dispatched"
    receipt = cli._dispatch(cli.build_parser().parse_args(
        ["run", "--scenario", str(SCENARIO), "--output", str(output)]))
    assert len(receipt["run_id"]) == 32
    assert json.loads((output / "status.json").read_text())["status"] == "completed"


def test_stage_identity_requires_strict_declarations():
    """阶段身份一致性：strict 声明缺失即拒绝。"""
    from worldmock.run_setup import stage_model_identity

    relaxed = copy_dict(stage_model_identity())
    relaxed["strict"] = False
    with pytest.raises(IntegrityError, match="strict=true"):
        validate_stage_model_identity(relaxed)
