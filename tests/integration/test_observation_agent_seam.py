"""引擎观察投影与 LifeAgent 控制合同的真实形状接缝测试。

背景（runs/deepseek-day-openrouter 一日模拟死因）：观察投影层构造
active_activity 控制行时丢失 status 键，LifeAgent 校验 status == "active"
缺键即抛 RuntimeError。迁移前两侧测试各自绿、接缝零覆盖是根因温床——
本文件用真实引擎跑出含非空 active_activity 的观察投影，直接喂给
LifeAgent.prepare_react_loop（_visible_activity_controls 调用链），
钉住 docs/CONTRACTS.md「观察投影字段」的控制行字段闭集。
"""

from __future__ import annotations

import copy
import os
import json
from pathlib import Path

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://openrouter.ai/api/v1")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.agent.base import ReactDecision, ReactLoopRequest  # noqa: E402
from agentsociety2.society import DirectToolCall  # noqa: E402

from custom.agents.life_agent import LifeAgent  # noqa: E402
from tests.life.conftest import (  # noqa: E402
    advance_next,
    advance_until_ready,
    wake_by_mail,
    context,
    engine_factory as _engine_factory,  # noqa: F401
)
from worldmock.life.models import (  # noqa: E402
    active_activities,
    PlanDayRequest,
    PlanGoalInput,
    StartBasicActivityRequest,
    StartTargetedActivityRequest,
)

#: CONTRACTS.md「观察投影字段」钉住的 active_activity 控制行字段闭集。
_ACTIVE_ACTIVITY_CONTROL_KEYS = {
    "activity_id", "kind", "status", "title", "progress",
    "elapsed_seconds", "planned_duration_seconds", "data",
}


def _submit(engine, round_id: str, tool_name: str, request) -> None:
    """按真实顺序观察、登记并提交单人物动作后结算本轮。"""
    ctx = context(engine, round_id)
    engine.begin_decision_round([ctx])
    engine.observe(ctx)
    engine.submit_action(DirectToolCall(
        ctx, f"action-{round_id}", tool_name, request))
    engine.commit_decision_round(round_id)


def _observe_active_wait(engine) -> tuple[dict, object]:
    """用真实邮件事件唤醒仍在等待的人物，验证非空活动控制行。"""
    _submit(engine, "seam-plan", "plan_day",
            PlanDayRequest(goals=(PlanGoalInput(intent_kind="休息", goal="稍作休息"),)))
    advance_next(engine)
    wake_by_mail(engine, offset=900)
    _submit(engine, "seam-wait", "start_basic_activity",
            StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=2700))
    assert advance_until_ready(engine)
    ctx = context(engine, "seam-observe")
    engine.begin_decision_round([ctx])
    return engine.observe(ctx), ctx


def _observation_records(observation: dict) -> list[dict]:
    """构造宿主观察记录的真实承载形状（turn/action/ok/data 四键）。"""
    return [{"turn": 0, "action": "observe", "ok": True, "data": observation}]


def test_engine_active_activity_control_feeds_life_agent(_engine_factory) -> None:
    """真实投影的非空 active_activity 控制行被 LifeAgent 控制合同直接接受。"""
    engine = _engine_factory()
    observation, ctx = _observe_active_wait(engine)

    control = observation["active_activities"][0]
    assert control is not None
    assert control["kind"] == "wait"
    assert control["status"] == "active"
    assert set(control) == _ACTIVE_ACTIVITY_CONTROL_KEYS

    agent = LifeAgent()
    agent.prepare_react_loop(
        ReactLoopRequest(0, ctx.observed_at, _observation_records(observation)))
    schemas = {item["function"]["name"]: item["function"]
               for item in agent.build_react_tool_schemas(readonly=False)}
    assert {"continue_activity", "set_activity_state"} <= set(schemas)
    state = schemas["set_activity_state"]["parameters"]["properties"]
    assert state["activity_id"]["enum"] == [control["activity_id"]]
    assert set(state["operation"]["enum"]) == {"pause", "cancel"}
    assert schemas["continue_activity"]["parameters"]["properties"][
        "activity_id"]["enum"] == [control["activity_id"]]
    control["data"]["observer_write"] = "must stay outside the world"
    assert "observer_write" not in engine.state.activities[control["activity_id"]].data


def test_life_agent_rejects_active_activity_without_active_status(
    _engine_factory,
) -> None:
    """status 键缺失（历史缺陷）或非 active 值时控制合同构建必须失败。"""
    engine = _engine_factory()
    observation, ctx = _observe_active_wait(engine)
    assert observation["active_activities"][0]["status"] == "active"

    def sabotage_drop_status(row: dict) -> None:
        row.pop("status")

    def sabotage_pause_status(row: dict) -> None:
        row["status"] = "paused"

    for sabotage in (sabotage_drop_status, sabotage_pause_status):
        damaged = copy.deepcopy(observation)
        sabotage(damaged["active_activities"][0])
        agent = LifeAgent()
        with pytest.raises(RuntimeError, match="active activity (control fields are|state or kind is) invalid"):
            agent.prepare_react_loop(ReactLoopRequest(
                0, ctx.observed_at, _observation_records(damaged)))


def test_observation_nested_content_data_cannot_change_world_progress(_engine_factory) -> None:
    """观察中已播放字幕的嵌套列表必须隔离，调用者修改它不能篡改世界进度。"""
    engine = _engine_factory()
    _submit(engine, "copy-plan", "plan_day", PlanDayRequest(goals=()))
    advance_next(engine)
    wake_by_mail(engine, offset=900)
    _submit(engine, "copy-watch", "start_targeted_activity", StartTargetedActivityRequest(
        activity_type="watch", target_id="sintel-en-subtitles",
        planned_duration_seconds=1260, playback_speed=0.5))
    assert advance_until_ready(engine)
    ctx = context(engine, "copy-observe")
    engine.begin_decision_round([ctx])
    observation = engine.observe(ctx)
    control = observation["active_activities"][0]
    actual = engine.state.activities[control["activity_id"]].data["revealed"]["cues"]
    assert len(actual) > 0
    count = len(actual)
    control["data"]["revealed"]["cues"].clear()
    assert len(actual) == count


async def _agent_action(engine, round_id: str, action: str, payload: dict):
    """以真实观察与环境执行人物工具，然后提交引擎事务。"""
    from custom.envs.life_env import LifeEnv

    ctx = context(engine, round_id)
    engine.begin_decision_round([ctx])
    observation = engine.observe(ctx)
    agent = LifeAgent()
    environment = LifeEnv()
    environment.engine = engine
    agent._env = environment
    agent._id = ctx.agent_id
    agent.bind_decision_context(ctx)
    agent.prepare_react_loop(ReactLoopRequest(0, ctx.observed_at, _observation_records(observation)))
    functions = {item["function"]["name"]: item["function"]
                 for item in agent.build_react_tool_schemas(readonly=False)}
    assert action in functions
    assert agent.validate_react_decisions([ReactDecision("", action, payload, "")], False) == ""
    receipt = await agent.dispatch_react_tool(action, payload)
    assert receipt.ok
    results = await environment.commit_decision_round(round_id)
    expected_status = "in_progress" if action == "start_basic_activity" else "completed"
    assert results[0].status.value == expected_status
    return observation, results[0], functions


@pytest.mark.asyncio
async def test_real_game_observation_accepts_and_executes_visible_command(_engine_factory) -> None:
    """真实游戏经观察与人物合同执行命令，持久化后仍可恢复同一会话。"""
    from worldmock.life.engine import LifeEngine

    engine = _engine_factory()
    _submit(engine, "game-plan", "plan_day", PlanDayRequest(goals=()))
    advance_next(engine)
    _submit(engine, "game-start", "start_targeted_activity", StartTargetedActivityRequest(
        activity_type="play_game", target_id="textworld-1201",
        planned_duration_seconds=3600, playback_speed=1))
    advance_next(engine)
    activity = active_activities(engine.state, 1)[0]
    session_id = activity.data["game_session_id"]
    command = engine.state.agents[1].game_sessions[session_id].admissible_commands[0]
    observation, result, schemas = await _agent_action(
        engine, "game-command", "game_action", {"game_session_id": session_id, "command": command})
    assert observation["active_activities"][0]["data"]["game_session_id"] == session_id
    assert schemas["game_action"]["parameters"]["properties"]["game_session_id"]["enum"] == [session_id]
    assert result.data["game"]["moves"] == 1
    observation["active_activities"][0]["data"]["game_session_id"] = "mutated-observation"
    assert engine.state.activities[activity.activity_id].data["game_session_id"] == session_id
    reference = engine.store.assets.put_json({"agent_id": 1}, {"kind": "game_seam_checkpoint"})
    engine.checkpoint({"agent:1": reference})
    run_dir = engine.store.run_dir
    engine.close()
    restored = LifeEngine.open(Path("scenarios/white-collar-12-weeks.json"), run_dir)
    try:
        assert restored.state.agents[1].game_sessions[session_id].moves == 1
        advance_next(restored)
        ctx = context(restored, "game-restored")
        restored.begin_decision_round([ctx])
        visible = restored.observe(ctx)
        agent = LifeAgent()
        agent.prepare_react_loop(ReactLoopRequest(0, ctx.observed_at, _observation_records(visible)))
        assert visible["active_activities"][0]["data"]["game_session_id"] == session_id
    finally:
        restored.close()


@pytest.mark.asyncio
async def test_native_goal_tools_persist_empty_revision_progress_and_restore(_engine_factory) -> None:
    """原生人物经过环境、SQLite和恢复观察，完整贯通空清单、追加与实际推进。"""
    from worldmock.life.engine import LifeEngine

    engine = _engine_factory()
    submitted_at = engine.state.current_time
    _, initial, initial_tools = await _agent_action(engine, "goals-empty", "plan_day", {"goals": []})
    assert set(initial_tools["plan_day"]["parameters"]["properties"]) == {"goals"}
    assert engine.state.current_time == submitted_at
    assert engine.state.agents[1].day_plan.goals == ()
    assert initial.data["day_plan"] == engine.state.agents[1].day_plan.model_dump(mode="json")
    advance_next(engine)
    before_revision, revision, tools = await _agent_action(
        engine, "goals-append", "revise_day_plan",
        {"goals": [{"intent_kind": "休息", "goal": "缓一缓"}]})
    assert "plan_day" not in tools and "revise_day_plan" in tools
    assert before_revision["facts"]["day_plan"]["goals"] == []
    assert before_revision["scene"]["day_plan"] == {"goals": []}
    assert revision.data["day_plan"] == engine.state.agents[1].day_plan.model_dump(mode="json")
    advance_next(engine)
    _, started, _ = await _agent_action(
        engine, "goals-wait", "start_basic_activity",
        {"activity_type": "wait", "planned_duration_seconds": 60})
    assert engine.state.agents[1].day_plan.goals[0].progressed_at is None
    assert advance_until_ready(engine)
    expected = engine.state.agents[1].day_plan.model_dump(mode="json")
    goal = expected["goals"][0]
    assert goal["goal"] == "缓一缓"
    assert goal["progressed_at"] > goal["registered_at"]
    assert goal["progress_activity_id"] == started.data["activity"]["activity_id"]
    records = [json.loads(row[0]) for row in engine.store.connection.execute("SELECT body FROM events")]
    assert any(row["event_type"] == "action_result" and row["data"].get("day_plan") == expected
               for row in records) or any(
        row["event_type"] == "activity_progress" and row["data"].get("day_plan") == expected for row in records)
    reference = engine.store.assets.put_json({"agent_id": 1}, {"kind": "goal_tools_restore_test"})
    checkpoint = engine.checkpoint({"agent:1": reference})
    assert checkpoint.last_event_seq > 0
    run_dir = engine.store.run_dir
    engine.close()
    restored = LifeEngine.open(Path("scenarios/white-collar-12-weeks.json"), run_dir)
    try:
        assert restored.state.agents[1].day_plan.model_dump(mode="json") == expected
        observation, _, restored_tools = await _agent_action(
            restored, "goals-preserve", "revise_day_plan", {"goals": []})
        assert observation["facts"]["day_plan"] == expected
        assert observation["scene"]["day_plan"] == {"goals": [{"intent_kind": "休息", "progressed": True}]}
        assert "plan_day" not in restored_tools
        assert restored.state.agents[1].day_plan.model_dump(mode="json") == expected
    finally:
        restored.close()
