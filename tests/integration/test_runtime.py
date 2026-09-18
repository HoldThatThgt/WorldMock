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
    from worldmock.life.models import SendMessageRequest

    engine, context, _DecisionContext, DirectToolCall = _npc_delivery_engine(tmp_path)
    try:
        from custom.envs.life_env import LifeEnv

        env = LifeEnv()
        env.engine = engine
        ctx = context(engine, "npc-deliver")
        await env.begin_decision_round((ctx,))
        await env.submit_action(DirectToolCall(
            ctx, "action-npc-deliver", "send_message",
            SendMessageRequest(recipient_id="contact-chenxi-oldfriend",
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
    from worldmock.life.models import StartBasicActivityRequest

    engine, context, _DecisionContext, DirectToolCall = _npc_delivery_engine(tmp_path)
    try:
        from custom.envs.life_env import LifeEnv

        env = LifeEnv()
        env.engine = engine
        ctx = context(engine, "npc-noop")
        await env.begin_decision_round((ctx,))
        await env.submit_action(DirectToolCall(
            ctx, "action-npc-noop", "start_basic_activity",
            StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=60)))

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
    """装配侧由同一次编译的卡片口味计算确定性权重（修订合同 L1661-1662）。"""
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
    assert "sintel-en-subtitles" not in genres
    joined = {item.item_id for item in scenario.contents} & set(genres)
    assert joined
