"""完整运行、共同恢复和反事实重放走真实原生 Society、工作区、LifeAgent 和 LifeEnv。"""
import copy
import json
import shutil
from datetime import timedelta
from types import SimpleNamespace
import pytest
import tomlkit
from tests.data_paths import ENVIRONMENT_ROOT
from tests.frozen_inputs import fixture_metadata
from tests.integration.runtime.current_contract_fixtures import ROOT, compiled_persona, inputs, metadata_service, ToolResponse
from tests.life.conftest import MAIN_TOOL, StubPlanDayClient, main_arguments
from worldmock.environment import load_environment
from worldmock.footprints.store import FootprintStore
from worldmock.life.models import CounterfactualIntervention
from worldmock.runtime import CounterfactualRequest, RunRequest, ResumeRequest, run_world, resume_world, replay_world


class CurrentDecisionModel:
    model_name = "test-model"

    def __init__(self, env):
        self.env, self.requests = env, []

    async def call(self, **request):
        self.requests.append(copy.deepcopy(request))
        state = self.env.engine.context.state
        ended = [item.name for item in state.activities.values()
                 if item.status not in {"active", "paused"} and item.concluded_at is None]
        if ended:
            return ToolResponse("conclude_activity", {"items": [{"activity_name": name, "summary": "聊完了。",
                "memories": [], "views": []} for name in ended], "errands": []}, f"call-{len(self.requests)}")
        active = [item.name for item in state.activities.values() if item.status == "active"]
        if active:
            return ToolResponse("report_status", {"items": [{"activity_name": name, "summary": "继续聊天。"}
                for name in active], "errands": []}, f"call-{len(self.requests)}")
        return ToolResponse(MAIN_TOOL, main_arguments("interact", "家", "测试闲聊"), f"call-{len(self.requests)}")


@pytest.fixture
def native_runtime(tmp_path, monkeypatch):
    import worldmock.runtime as runtime
    import worldmock.run_setup as setup
    import agentsociety2.society.society as society_module
    from agentsociety2.agent.runner import _step_agent_batch_async, _create_direct_agents_batch_async
    from agentsociety2.config import llm_dispatcher
    from agentsociety2.config.config import Config
    from agentsociety2.society import AgentSociety, AgentSocietyConfig
    from custom.envs.life_env import LifeEnv

    root = tmp_path / "environment"
    shutil.copytree(ENVIRONMENT_ROOT, root)
    path = root / "config.toml"
    document = tomlkit.parse(path.read_text(encoding="utf-8"))
    document["stop_time"] = document["start_time"] + timedelta(hours=1)
    path.write_text(tomlkit.dumps(document), encoding="utf-8", newline="\n")
    frozen = load_environment(root, metadata_loader=fixture_metadata)
    metadata_service(monkeypatch, frozen)
    for name in ("LLM_API_BASE", "CODER_LLM_API_BASE"):
        monkeypatch.setattr(Config, name, frozen.config.model.base_url)
    for name in ("LLM_MODEL", "CODER_LLM_MODEL"):
        monkeypatch.setattr(Config, name, frozen.config.model.requested_model)
    monkeypatch.setattr(Config, "EMBEDDING_MODEL", "")
    import os
    for name in ("WORKSPACE_PATH", "AGENTSOCIETY_LLM_API_KEY", "AGENTSOCIETY_LLM_API_BASE", "AGENTSOCIETY_LLM_MODEL",
                 "AGENTSOCIETY_CODER_LLM_API_KEY", "AGENTSOCIETY_CODER_LLM_API_BASE", "AGENTSOCIETY_CODER_LLM_MODEL",
                 "AGENTSOCIETY_EMBEDDING_MODEL", "LITELLM_LOCAL_MODEL_COST_MAP", "AGENTSOCIETY_LLM_REQUEST_TIMEOUT",
                 "MEM0_TELEMETRY", "ANONYMIZED_TELEMETRY", "PYTHONPATH"):
        monkeypatch.setenv(name, os.environ.get(name, ""))
    models = []

    async def compile_card(*args):
        return compiled_persona(frozen)

    async def initialize():
        return None

    async def step(request, service):
        return {"results": await _step_agent_batch_async(request, service), "token_stats": {}}

    async def build(prepared):
        specs = await runtime._society_specs(prepared)
        env = LifeEnv(**prepared.env_kwargs)
        model = CurrentDecisionModel(env)
        models.append(model)
        service = SimpleNamespace(env=env, llm=SimpleNamespace(default=model), trace=None)
        return None, AgentSociety(AgentSocietyConfig(prepared.run_id, specs, "LifeAgent", env, service,
            prepared.start_time, prepared.inputs.config.stop_time, prepared.run_dir, ROOT, prepared.workspace_mode))

    monkeypatch.setattr(setup, "_compile_persona", compile_card)
    monkeypatch.setattr(llm_dispatcher, "init_dispatchers", initialize)
    monkeypatch.setattr(runtime, "PlanDayClient", StubPlanDayClient)
    monkeypatch.setattr(runtime, "_build_society", build)
    monkeypatch.setattr(society_module, "create_direct_agents_batch", SimpleNamespace(remote=_create_direct_agents_batch_async))
    monkeypatch.setattr(society_module, "step_agent_batch", SimpleNamespace(remote=step))
    monkeypatch.setenv("WORKSPACE_PATH", str(ROOT))
    return root, frozen, models


@pytest.mark.asyncio
@pytest.mark.parametrize("branch", [None, "resume", "counterfactual"])
async def test_native_dataset_run_resume_and_replay_finish_with_real_frozen_workspaces(tmp_path, native_runtime, branch):
    root, frozen, models = native_runtime
    result = await run_world(RunRequest(ROOT, root, tmp_path / "parent", "native-parent", "native-parent"))
    assert result.current_time == frozen.config.stop_time
    store = FootprintStore(result.run_dir)
    try:
        events = store.read_events(store.last_event_seq)
        assert any(event.event_type == "run_ended" for event in events)
        assert not any(event.event_type == "run_failed" for event in events)
        observed = next(event for event in events if event.event_type == "observation")
        observation = observed.event_id
        checkpoint = next(event.data["checkpoint_id"] for event in events
                          if event.event_type == "checkpoint_created" and event.event_seq > observed.event_seq)
        manifest = store.manifest
        assert manifest["schema_version"] == 5
        assert set(manifest["environment_assets"]) == {
            "persona.toml", "config.toml", "map.json", "catalog/manifest.json",
            *(f"catalog/{entry.path}" for entry in frozen.catalog.manifest.files)}
        assert manifest["persona_sha256"] == frozen.persona_sha256
        assert manifest["config_sha256"] == frozen.config_sha256
    finally:
        store.close()
    profile = json.loads((result.run_dir / "as2/agents/agent_0001/AGENT.json").read_text(encoding="utf-8"))["profile"]
    assert profile["persona_card_struct"] and profile["model_identity"]["model_metadata"] == frozen.model_metadata.model_dump(mode="json")
    assert all(request["transport_max_retries"] == 3 for request in models[0].requests)
    if branch is None:
        return
    request = ResumeRequest(ROOT, root, result.run_dir, checkpoint, tmp_path / "child")
    intervention = None if branch == "resume" else CounterfactualIntervention(kind="set_balance_fen", agent_id=1,
        balance_fen=123456, hypothesis="Different funds change spending choices.", source_event_ids=(observation,))
    restored = (await resume_world(request) if branch == "resume"
                else await replay_world(CounterfactualRequest(request, intervention)))
    assert restored.current_time == frozen.config.stop_time
    child_store = FootprintStore(restored.run_dir)
    try:
        events = child_store.read_events(child_store.last_event_seq)
        assert child_store.manifest["branch_kind"] == branch
        assert sum(event.event_type == "environment_event" and event.data.get("kind") == "counterfactual_intervention"
                   for event in events) == int(branch == "counterfactual")
        assert not any(event.event_type == "run_failed" for event in events)
    finally:
        child_store.close()
