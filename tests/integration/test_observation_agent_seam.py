"""真实引擎观察与 LifeAgent 的可读会话、活动和目标接缝。"""

from __future__ import annotations

import copy
import os
import json
from pathlib import Path

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.agent.base import ReactDecision, ReactLoopRequest  # noqa: E402
from agentsociety2.society import DirectToolCall  # noqa: E402

from custom.agents.life_agent import LifeAgent  # noqa: E402
from tests.life.conftest import (  # noqa: E402
    advance_until_ready,
    wake_by_mail,
    context,
    engine_factory as _engine_factory,  # noqa: F401
)
from worldmock.life.models import (  # noqa: E402
    PlanDayRequest,
    PlanGoalInput,
    StartActivityRequest,
)

#: 引擎事件级活动控制行字段闭集，模型只读取 facts.progress 的可读会话。
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
    # 登记计划是瞬时动作：没有进行中活动，同一秒再决策（时间与能量规格第一章规则 2）。
    wake_by_mail(engine, offset=300)
    _submit(engine, "seam-wait", "start_activity",
            StartActivityRequest(targets=["自己"], actions=["等待"]))
    assert advance_until_ready(engine)
    ctx = context(engine, "seam-observe")
    engine.begin_decision_round([ctx])
    return engine.observe(ctx), ctx


def _observation_records(observation: dict) -> list[dict]:
    """构造宿主观察记录的真实承载形状（turn/action/ok/data 四键）。"""
    return [{"turn": 0, "action": "observe", "ok": True, "data": observation}]


def test_engine_active_activity_control_feeds_life_agent(_engine_factory) -> None:
    """真实活动与会话投影形成可读控制名，人物工具只读取会话。"""
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
    assert {"report_status", "pause_activity", "conclude_activity"} <= set(schemas)
    [session] = observation["facts"]["progress"]["sessions"]
    assert session["phase"] == "进行中"
    assert session["session_name"] in schemas["report_status"]["parameters"]["properties"][
        "task"]["items"]["enum"] or session["task_name"] in schemas["report_status"]["parameters"][
            "properties"]["task"]["items"]["enum"]
    assert control["activity_id"] not in str(schemas)
    control["data"]["observer_write"] = "must stay outside the world"
    assert "observer_write" not in engine.state.activities[control["activity_id"]].data


def test_life_agent_rejects_session_without_valid_phase(
    _engine_factory,
) -> None:
    """模型可见会话阶段缺失或非法时，人物工具构建失败关闭。"""
    engine = _engine_factory()
    observation, ctx = _observe_active_wait(engine)
    assert observation["facts"]["progress"]["sessions"][0]["phase"] == "进行中"

    for invalid in (None, "未知阶段"):
        damaged = copy.deepcopy(observation)
        damaged["facts"]["progress"]["sessions"][0]["phase"] = invalid
        agent = LifeAgent()
        with pytest.raises(RuntimeError, match="session status is invalid"):
            agent.prepare_react_loop(ReactLoopRequest(
                0, ctx.observed_at, _observation_records(damaged)))


def test_observation_nested_content_data_cannot_change_world_progress(_engine_factory) -> None:
    """观察中已播放字幕的嵌套列表必须隔离，调用者修改它不能篡改世界进度。"""
    engine = _engine_factory()
    _submit(engine, "copy-plan", "plan_day", PlanDayRequest(goals=()))
    wake_by_mail(engine, offset=600)
    shown = engine._observation_data(engine.state.agents[1])
    subtitle = next(row for row in shown["interaction_bindings"]
                    if row["kind"] == "content" and row["object_id"] == "sintel-en-subtitles")
    _submit(engine, "copy-watch", "start_activity", StartActivityRequest(
        targets=[subtitle["object"]], actions=["观看"]))
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
    expected_status = "in_progress" if action == "start_activity" else "completed"
    assert results[0].status.value == expected_status
    return observation, results[0], functions


@pytest.mark.asyncio
async def test_real_game_observation_starts_visible_game_and_restores_activity(_engine_factory) -> None:
    """模型用可读游戏名启动活动，私有游戏身份随检查点恢复。"""
    from worldmock.life.engine import LifeEngine

    engine = _engine_factory()
    _submit(engine, "game-plan", "plan_day", PlanDayRequest(goals=()))
    shown = engine._observation_data(engine.state.agents[1])
    game = next(row for row in shown["interaction_bindings"]
                if row["kind"] == "game" and any(item["label"] == "游玩" for item in row["actions"]))
    observation, result, schemas = await _agent_action(
        engine, "game-start", "start_activity",
        {"targets": [game["object"]], "actions": ["游玩"]})
    assert game["object"] in schemas["start_activity"]["parameters"]["properties"][
        "targets"]["items"]["enum"]
    assert game["object_id"] not in str(schemas["start_activity"])
    activity = engine.state.activities[result.activity_id]
    assert activity.target_id == game["object_id"]
    observation["active_activities"].append({"observer_write": "must stay outside the world"})
    assert all("observer_write" not in item.data for item in engine.state.activities.values())
    reference = engine.store.assets.put_json({"agent_id": 1}, {"kind": "game_seam_checkpoint"})
    engine.checkpoint({"agent:1": reference})
    run_dir = engine.store.run_dir
    engine.close()
    restored = LifeEngine.open(Path("scenarios/white-collar-12-weeks.json"), run_dir)
    try:
        assert restored.state.activities[activity.activity_id].target_id == game["object_id"]
        visible = restored._observation_data(restored.state.agents[1])
        assert any(row["session_name"] for row in visible["facts"]["progress"]["sessions"])
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
    before_revision, revision, tools = await _agent_action(
        engine, "goals-append", "revise_day_plan",
        {"goals": [{"intent_kind": "休息", "goal": "缓一缓"}]})
    assert "plan_day" not in tools and "revise_day_plan" in tools
    assert before_revision["facts"]["day_plan"]["goals"] == []
    assert before_revision["scene"]["day_plan"] == {"goals": []}
    assert revision.data["day_plan"] == engine.state.agents[1].day_plan.model_dump(mode="json")
    assert engine.state.current_time == submitted_at
    _, started, _ = await _agent_action(
        engine, "goals-wait", "start_activity",
        {"targets": ["自己"], "actions": ["等待"]})
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
        public_goal = observation["facts"]["day_plan"]["goals"][0]
        assert public_goal["task_name"] == "目标：休息：缓一缓"
        assert public_goal["progressed"] is True
        assert "progress_activity_id" not in public_goal
        assert observation["scene"]["day_plan"] == {"goals": [{"intent_kind": "休息", "progressed": True}]}
        assert "plan_day" not in restored_tools
        assert restored.state.agents[1].day_plan.model_dump(mode="json") == expected
    finally:
        restored.close()
