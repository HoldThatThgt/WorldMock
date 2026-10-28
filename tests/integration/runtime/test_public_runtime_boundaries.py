"""当前公共运行入口、人物提示和真实服务边界的失败行为。"""

import copy
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
import requests

from tests.data_paths import ENVIRONMENT_ROOT
from tests.integration.runtime.current_contract_fixtures import ROOT, inputs, profile, metadata_service
from tests.life.conftest import StubPlanDayClient, stage_day_plan
from tests.life.environment_runtime import settle_decision
from worldmock.agents.prompt.life_context import persona_card_text
from worldmock.agents.protocol.life_continuation import tool_message
from worldmock.agents.prompt.life_persona import persona_layer, readonly_layers, write_prompt_snapshot
from worldmock.foundation.errors import EnvironmentInputError, IntegrityError, ProtocolError
from worldmock.agents.prompt.narration import RenderBudgetExceededError
from worldmock.configuration.model import model_metadata as model_endpoint
from worldmock.configuration.model.model_metadata import credential_value, load_model_metadata, model_context_length
from worldmock.runtime.framework.setup import validate_manifest_stage_identity, validate_stage_model_identity
from worldmock.runtime.lifecycle.runner import CounterfactualRequest, PlanDayClient, ResumeRequest, RunRequest
from worldmock.runtime.lifecycle.runner import run_world, resume_world, replay_world
from worldmock.state.decisions.results import CounterfactualIntervention


@pytest.mark.parametrize("value", [None, "", "  ", 17])
def test_persona_text_requires_the_card_text(value):
    with pytest.raises(IntegrityError, match="Persona card text is required"):
        persona_card_text({"persona_card": value}, "林舟")
    assert persona_card_text({"persona_card": "  人物卡片文本  "}, "林舟") == "人物卡片文本"


def test_persona_layer_rejects_actual_overlong_card_without_truncation():
    prepared = profile()
    prepared["persona_card_struct"]["tastes"]["content_genres"] = ["纪" * 601]
    original = copy.deepcopy(prepared)
    with pytest.raises(RenderBudgetExceededError, match="input-side problem, not truncated"):
        persona_layer(prepared, "林舟", {"relationships": [], "preferences": []})
    assert prepared == original


def test_readonly_prompt_uses_saved_snapshot_and_requires_the_persona_card(tmp_path):
    prepared = profile()
    initial, memory = readonly_layers(None, prepared, "林舟")
    assert initial["identity"]["name"] == "林舟"
    write_prompt_snapshot(tmp_path, initial, memory)
    assert readonly_layers(tmp_path, {}, "不同的姓名") == (initial, memory)
    with pytest.raises(IntegrityError, match="Persona card structure is required"):
        readonly_layers(tmp_path / "without-snapshot", {}, "林舟")


def test_continuation_tool_message_preserves_actual_events_and_omits_empty_changes():
    result = {"status": "completed"}
    event = {"text": "计划提醒到了。"}
    message = tool_message("call-report", result, {}, [event])
    assert message["tool_call_id"] == "call-report"
    assert json.loads(message["content"]) == {"result": result, "events": [event]}


@pytest.mark.parametrize("manifest,reason", [
    ({}, "decision stage identity is missing"),
    ({"two_stage_model_identity": {}}, "two_stage_model_identity is retired"),
])
def test_manifest_stage_identity_requires_the_current_actual_stage(manifest, reason):
    with pytest.raises(IntegrityError, match=reason):
        validate_manifest_stage_identity(manifest)


@pytest.mark.parametrize("field,value", [
    ("requested_model", "different-model"),
    ("metadata_url", "https://different.example.invalid/v1/models"),
    ("context_window", 262143),
])
def test_stage_identity_rejects_a_different_actual_service_capability(field, value):
    identity = profile()["model_identity"]
    identity["model_metadata"][field] = value
    with pytest.raises(IntegrityError, match="inconsistent model metadata"):
        validate_stage_model_identity(identity)


def test_stage_identity_cannot_include_a_credential_reference():
    identity = profile()["model_identity"]
    identity["api_key_env"] = "MODEL_SECRET"
    with pytest.raises(IntegrityError, match="unexpected fields"):
        validate_stage_model_identity(identity)


def test_frozen_context_capacity_cannot_be_used_for_a_different_model():
    metadata = inputs().model_metadata
    with pytest.raises(ValueError, match="model context metadata is unavailable"):
        model_context_length("different-model", metadata)


@pytest.mark.parametrize("value", [None, "", "  "])
def test_model_credential_is_required_and_never_included_in_the_error(monkeypatch, value):
    config = inputs().config.model
    if value is None:
        monkeypatch.delenv(config.api_key_env, raising=False)
    else:
        monkeypatch.setenv(config.api_key_env, value)
    with pytest.raises(ValueError, match="Model credential environment variable is missing or empty"):
        credential_value(config)


@pytest.mark.parametrize("failure", ["transport", "capacity", "foreign-service"])
def test_actual_model_metadata_rejects_transport_capacity_and_service_identity(monkeypatch, failure):
    frozen = inputs()
    metadata_service(monkeypatch, frozen)
    config = frozen.config.model
    gets, waits = [], []
    if failure == "transport":
        def unavailable(*args, **kwargs):
            gets.append(args)
            raise requests.ConnectionError("Service is offline")
        monkeypatch.setattr(requests, "get", unavailable)
        monkeypatch.setattr(model_endpoint.time, "sleep", waits.append)
        reason = "model context metadata is unavailable"
    elif failure == "capacity":
        response = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"data": [
            {"id": config.requested_model, "context_window": 262143, "max_output_tokens": 32768}]})
        monkeypatch.setattr(requests, "get", lambda *args, **kwargs: response)
        reason = "Model context length 262143 is below the declared"
    else:
        config = config.model_copy(update={"metadata_url": "https://another.example.invalid/v1/models"})
        reason = "model context metadata is unavailable"
    with pytest.raises(EnvironmentInputError, match=reason) as caught:
        load_model_metadata(config)
    assert caught.value.path == "config.toml" and caught.value.code == "RUN_CONFIG_INVALID"
    assert "test-only-key" not in str(caught.value)
    if failure == "transport":
        assert len(gets) == 5 and waits == [2.0, 4.0, 8.0, 16.0]


@pytest.mark.asyncio
async def test_day_plan_client_caps_output_using_actual_frozen_metadata(monkeypatch):
    import agentsociety2.config
    identity = profile()["model_identity"]
    received = []

    async def call(**request):
        received.append(request)
        return {"choices": []}

    transport = SimpleNamespace(model_name=identity["requested_model"], base_url=identity["base_url"], call=call)
    monkeypatch.setattr(agentsociety2.config, "build_client_for_role", lambda role: transport)
    request = {"messages": [], "max_tokens": 1000000}
    assert await PlanDayClient(identity).call(request) == {"choices": []}
    assert received[0]["max_tokens"] == identity["model_metadata"]["max_output_tokens"]
    assert received[0]["max_retries"] == identity["max_retries"]
    assert received[0]["transport_max_retries"] == identity["transport_max_retries"]
    assert request == {"messages": [], "max_tokens": 1000000}


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["run", "resume", "replay"])
async def test_public_runtime_entries_log_real_admission_failures(tmp_path, monkeypatch, caplog, entry):
    metadata_service(monkeypatch)
    destination = tmp_path / "run"
    if entry == "run":
        request = RunRequest(ROOT, ENVIRONMENT_ROOT, destination, "probe", "different-source-group")
        operation, reason = run_world(request), "matching run_id and source_group_id"
    else:
        request = ResumeRequest(ROOT, ENVIRONMENT_ROOT, tmp_path / "missing-parent", "checkpoint", destination)
        intervention = CounterfactualIntervention(kind="set_balance_fen", agent_id=1, balance_fen=10000,
            hypothesis="Changed funds alter the next spending choice.", source_event_ids=("observation-source",))
        operation = resume_world(request) if entry == "resume" else replay_world(CounterfactualRequest(request, intervention))
        reason = "Footprint database does not exist"
    with pytest.raises((IntegrityError, ValueError), match=reason):
        await operation
    assert "WorldMock" in caplog.text and "failed" in caplog.text
    assert not destination.exists()


@pytest.mark.asyncio
async def test_environment_confirmed_clock_cannot_advance_world_time(engine_factory):
    from custom.envs.life_env import LifeEnv
    engine = engine_factory()
    environment = LifeEnv()
    environment.engine = engine
    before = engine.context.state.model_dump_json()
    now = engine.context.config.start_time
    with pytest.raises(ProtocolError, match="Society current time disagrees with the environment"):
        await environment.set_current_time(now + timedelta(seconds=1))
    assert engine.context.state.model_dump_json() == before
    await environment.set_current_time(now)
    assert environment.t == now


@pytest.mark.asyncio
async def test_real_environment_midnight_step_registers_next_plan_before_rhythm(engine_factory, monkeypatch):
    import worldmock.runtime.lifecycle.runner as runtime
    from custom.envs.life_env import LifeEnv

    def overnight(persona, config):
        config["start_time"] = config["start_time"].replace(hour=23)
        config["stop_time"] = config["start_time"] + timedelta(hours=2)

    engine = engine_factory(mutate=overnight)
    stage_day_plan(engine)
    environment = LifeEnv()
    environment.engine = engine
    monkeypatch.setattr(runtime, "PlanDayClient", StubPlanDayClient)
    for index in range(100):
        state = engine.scheduling_state()
        if state.current_time.hour == 0:
            break
        if state.ready_agent_ids:
            _, result = settle_decision(engine, f"midnight-{index}")
            assert result.status in {"in_progress", "completed"}
        else:
            await environment.step(int(state.next_event_at.timestamp()) - engine.context.state.current_time,
                                   state.next_event_at)
    else:
        raise AssertionError("The actual scheduling boundaries did not reach midnight")
    assert not engine.midnight_rhythm_pending
    assert state.current_time.date().isoformat() in engine.context.state.agents[1].plan_submitted_dates
    assert engine.context.state.agents[1].day_plan is not None
