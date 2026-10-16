"""完整运行、共同恢复和反事实重放走真实原生 Society、工作区、LifeAgent 和 LifeEnv。"""
import json
import pytest
from tests.integration.runtime.current_contract_fixtures import ROOT
from tests.integration.runtime.native_runtime_fixtures import CurrentDecisionModel, native_runtime  # noqa: F401
from worldmock.footprints.store import FootprintStore
from worldmock.life.models import CounterfactualIntervention
from worldmock.runtime import CounterfactualRequest, RunRequest, ResumeRequest, run_world, resume_world, replay_world


@pytest.mark.asyncio
@pytest.mark.parametrize("branch", [None, "resume", "counterfactual"])
async def test_native_dataset_run_resume_and_replay_finish_with_real_frozen_workspaces(
        tmp_path, native_runtime, monkeypatch, branch):
    from worldmock import run_setup

    root, frozen, models = native_runtime
    configure, configured = run_setup._configure_process, []

    def counted(*args):
        configured.append(args)
        return configure(*args)

    monkeypatch.setattr(run_setup, "_configure_process", counted)
    result = await run_world(RunRequest(ROOT, root, tmp_path / "parent", "native-parent", "native-parent"))
    assert result.current_time == frozen.config.stop_time
    assert len(configured) == 1
    store = FootprintStore(result.run_dir)
    try:
        events = list(store.iter_events(store.last_event_seq))
        assert any(event.event_type == "run_ended" for event in events)
        assert not any(event.event_type == "run_failed" for event in events)
        observed = next(event for event in events if event.event_type == "observation")
        observation = observed.event_id
        checkpoint = next(event.data["checkpoint_id"] for event in events
                          if event.event_type == "checkpoint_created" and event.event_seq > observed.event_seq)
        manifest = store.manifest
        assert manifest["schema_version"] == 7
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
    assert len(configured) == 2
    child_store = FootprintStore(restored.run_dir)
    try:
        events = list(child_store.iter_events(child_store.last_event_seq))
        assert child_store.manifest["branch_kind"] == branch
        assert sum(event.event_type == "environment_event" and event.data.get("kind") == "counterfactual_intervention"
                   for event in events) == int(branch == "counterfactual")
        assert not any(event.event_type == "run_failed" for event in events)
    finally:
        child_store.close()


def _persisted_responses(run_dir, until=None):
    """运行库里已落盘的模型响应（phase=response）条数；until 给出时只数序号不晚于它的。"""
    store = FootprintStore(run_dir)
    try:
        events = list(store.iter_events(store.last_event_seq if until is None else until))
    finally:
        store.close()
    return sum(event.event_type == "model_response" and event.data.get("phase") == "response" for event in events)


def _shared_checkpoint(run_dir):
    """父运行第一次观察之后的第一个共同检查点事件，以及那次观察的事件身份。"""
    store = FootprintStore(run_dir)
    try:
        events = list(store.iter_events(store.last_event_seq))
    finally:
        store.close()
    observed = next(event for event in events if event.event_type == "observation")
    return observed.event_id, next(event for event in events if event.event_type == "checkpoint_created"
                                   and event.event_seq > observed.event_seq)


def test_receipts_count_every_persisted_model_response_including_the_day_plan(tmp_path, native_runtime):
    """经真实命令入口跑完：回执的 token_stats 等于 status 的实测用量，含零点日计划桩的用量；
    从检查点恢复与重放的回执覆盖分支库整段历史，含继承自父运行的响应。"""
    from worldmock.cli import main
    from worldmock.status import read_status

    root, _, _ = native_runtime
    parent = tmp_path / "parent"
    assert main(["--project-root", str(ROOT), "run", "--environment", str(root), "--output", str(parent)]) == 0
    receipt = json.loads((parent / "result.json").read_text(encoding="utf-8"))["token_stats"]
    assert receipt == read_status(parent)["observed_model_usage"] == read_status(parent)["token_stats"]
    rows = {row["model"]: row for row in receipt}
    assert list(rows) == [None, "test-model"]
    plan, decisions = rows["test-model"], rows[None]
    assert plan["responses"] > 0
    assert (plan["input_tokens"], plan["output_tokens"], plan["total_tokens"]) == tuple(
        value * plan["responses_with_usage"] for value in (30, 9, 39))
    assert decisions["responses"] > 0 and decisions["responses_with_usage"] == 0
    assert (decisions["input_tokens"], decisions["output_tokens"], decisions["total_tokens"]) == (None, None, None)
    observation, point = _shared_checkpoint(parent)
    inherited = _persisted_responses(parent, point.event_seq)
    assert inherited > 0
    intervention = tmp_path / "intervention.json"
    intervention.write_text(json.dumps({"kind": "set_balance_fen", "agent_id": 1, "balance_fen": 123456,
                                        "hypothesis": "Different funds change spending choices.",
                                        "source_event_ids": [observation]}), encoding="utf-8")
    for command, extra in (("resume", []), ("replay", ["--intervention", str(intervention)])):
        child = tmp_path / command
        assert main(["--project-root", str(ROOT), command, "--environment", str(root), "--output", str(child),
                     "--parent", str(parent), "--checkpoint", point.data["checkpoint_id"], *extra]) == 0
        branch = json.loads((child / "result.json").read_text(encoding="utf-8"))["token_stats"]
        assert branch == read_status(child)["observed_model_usage"]
        assert sum(row["responses"] for row in branch) == _persisted_responses(child) >= inherited
