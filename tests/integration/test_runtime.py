"""runtime 运行壳、代码身份与共同恢复测试。"""

from __future__ import annotations

import copy
import json
import hashlib
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com/beta")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from worldmock.errors import IntegrityError
from worldmock.footprints.checkpoint import snapshot_directory
from worldmock.footprints.models import canonical_json
from worldmock.footprints.store import FootprintStore
from worldmock.life.models import CounterfactualIntervention
from worldmock.runtime import (
    _agent_specs,
    _build_society,
    _execute,
    _intervention_sha256,
    _model_identity,
    _no_persona_compilation,
    _require_run_shell,
    _restore_branch,
    runtime_code_sha256,
    two_stage_model_identity,
)
from worldmock.validation import validate_two_stage_model_identity


def test_model_identity_freezes_complete_strict_endpoint_and_request() -> None:
    """运行身份记录完整 beta URL 和唯一 strict 请求参数。"""
    identity = _model_identity().model_dump(mode="json")

    assert identity["base_url"] == "https://api.deepseek.com/beta"
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
    """两阶段身份：独白/行动/工作验收参数与 LifeAgent 常量单一事实源一致。"""
    from custom.agents.life_agent import (
        ACTION_THINKING,
        ACTION_TOOL_CHOICE,
        MONOLOGUE_MAX_TOKENS,
        MONOLOGUE_TEMPERATURE,
    )

    identity = two_stage_model_identity()
    stages = identity["stages"]

    assert stages["monologue"] == {"thinking": "disabled", "temperature": MONOLOGUE_TEMPERATURE,
                                   "max_tokens": MONOLOGUE_MAX_TOKENS, "tools": None,
                                   "tool_choice": None}
    assert stages["action"] == {"thinking": ACTION_THINKING["thinking"]["type"],
                                "tool_choice": ACTION_TOOL_CHOICE}
    assert stages["work_review"] == {"thinking": "disabled", "tool_choice": "required"}
    assert identity["base_url"].endswith("/beta")
    assert identity["max_retries"] == 0 and identity["strict"] is True
    assert identity["reasoning_effort"] is None


def test_two_stage_model_identity_is_frozen_and_tamper_rejected() -> None:
    """两阶段身份运行中不变更；任何篡改被一致性校验拒绝。"""
    identity = two_stage_model_identity()

    assert two_stage_model_identity() == identity
    validate_two_stage_model_identity(identity)

    tampered = copy.deepcopy(identity)
    tampered["stages"]["monologue"]["temperature"] = 0.7
    with pytest.raises(IntegrityError, match="monologue"):
        validate_two_stage_model_identity(tampered)

    no_beta = copy.deepcopy(identity)
    no_beta["base_url"] = "https://api.deepseek.com"
    with pytest.raises(IntegrityError, match="beta"):
        validate_two_stage_model_identity(no_beta)

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
    assert compiled == [1]
    restored = _agent_specs(scenario, persona_compiler=_no_persona_compilation)
    assert "persona_card" not in restored[0]["profile"]


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
    (workspace / "config.json").write_text(json.dumps({"enable_memory": True}))


def test_runtime_code_identity_is_deterministic() -> None:
    """同一精确 checkout 重复计算得到相同代码身份。"""
    root = Path(__file__).resolve().parents[2]

    first = runtime_code_sha256(root)
    second = runtime_code_sha256(root)

    assert len(first) == 64
    assert first == second


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
    prepared = SimpleNamespace(project_root=tmp_path, run_dir=tmp_path / "run", env_kwargs={})

    with pytest.raises(RuntimeError, match="router construction failed"):
        _build_society(prepared)

    assert calls[0][0] == "remote"
    assert calls[1] == ("kill", actor, True)
