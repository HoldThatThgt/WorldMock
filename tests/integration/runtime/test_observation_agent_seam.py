"""真实引擎观察与 LifeAgent 的接缝：工具层的活动目标、待办层的今日计划、私有绑定核对名称、观察事件与返回体分工。"""

from __future__ import annotations

from tests.data_paths import ARTIFICIAL_ENVIRONMENT_ROOT

import copy
import os
import json
from pathlib import Path

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com/beta")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.agent.base import ReactDecision, ReactLoopRequest  # noqa: E402
from agentsociety2.society import DirectToolCall  # noqa: E402

from custom.agents.life_agent import LifeAgent  # noqa: E402
from tests.engine_fixture import (engine_factory as _engine_factory)  # noqa: F401
from tests.life.conftest import (StubPlanDayClient, activity_name, advance_until_ready, conclude_pending, settle_day_plans, stage_day_plan, wake_by_mail, context, start_request, start_arguments)  # noqa: F401
from tests.integration.life import test_memory_layer as memory_tests  # noqa: E402
from tests.life.routing_fixtures import (  # noqa: E402
    OFFICE, PERSON_TARGET, act, observe, open_world, place,
)
from worldmock.life.models import RecallTaskSummaryRequest  # noqa: E402

#: 引擎事件级活动控制行字段闭集；模型只读取工具层的活动目标。
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
    # 启动路径的零点计划后台任务（确定性桩）登记当日计划，不占决策；首个决策仍带 run_start（时间与能量规格第四章）。
    settle_day_plans(engine, StubPlanDayClient(goals=[{"intent_kind": "休息", "goal": "稍作休息", "remind_time": None,
                                                       "around_time": None}]))
    wake_by_mail(engine, offset=300)
    _submit(engine, "seam-wait", "start_activity",
            start_request(targets=["自己"], actions=["interact"], ways=["等待"]))
    assert advance_until_ready(engine)
    ctx = context(engine, "seam-observe")
    engine.begin_decision_round([ctx])
    return engine.observe(ctx), ctx


def _observation_records(observation: dict) -> list[dict]:
    """构造宿主观察记录的真实承载形状（turn/action/ok/data 四键）。"""
    return [{"turn": 0, "action": "observe", "ok": True, "data": observation}]


def _functions(agent: LifeAgent) -> dict[str, dict]:
    """人物本次请求的原生工具。"""
    return {item["function"]["name"]: item["function"] for item in agent.build_react_tool_schemas(readonly=False)}


def test_engine_active_activity_control_feeds_life_agent(_engine_factory) -> None:
    """真实活动投影成工具层的「进行中」活动目标；控制工具以它的可读名为参数，宿主按私有活动绑定核对，不见内部身份；
    观察返回体带进度层交给宿主，观察事件只存来源引用。"""
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
    schemas = _functions(agent)
    [activity] = [target for target in observation["tool_layer"]["targets"] if target["kind"] == "activity"]
    assert (activity["phase"], activity["actions"]) == ("进行中", [])
    assert activity["name"] == activity_name(engine, control["activity_id"])
    assert [item["tool"] for item in activity["tools"]] == ["report_status", "pause_activity", "conclude_activity"]
    for tool in ("report_status", "pause_activity"):
        assert "enum" not in schemas[tool]["parameters"]["properties"]["items"]["items"]["properties"]["activity_name"]
        decision = ReactDecision("", tool, {"items": [{"activity_name": activity["name"], "summary": "继续。"}],
                                            "errands": []}, "")
        assert agent.validate_react_decisions([decision], False) == ""
    user = agent.build_react_messages(ReactLoopRequest(0, ctx.observed_at, _observation_records(observation)))[1]
    session_id = next(iter(engine.context.state.sessions))
    for private in (control["activity_id"], session_id):
        assert private not in str(schemas) and private not in user["content"]
    [stored] = [event for event in engine.context.store.read_agent_events("observation", 1)
                if event.event_id == observation["observation_event_id"]]
    assert "progress_layer" in observation and "progress_meta" in observation
    assert not {"progress_layer", "progress_meta"} & set(stored.data)
    assert stored.data["tool_layer"] == observation["tool_layer"]
    control["data"]["observer_write"] = "must stay outside the world"
    assert "observer_write" not in engine.context.state.activities[control["activity_id"]].data


def test_life_agent_checks_activity_names_against_private_bindings(_engine_factory) -> None:
    """活动名只按私有活动绑定核对：不在绑定里的名称按协议错误纠正，绑定缺失是损坏的宿主观察。"""
    engine = _engine_factory()
    observation, ctx = _observe_active_wait(engine)
    agent = LifeAgent()
    agent.prepare_react_loop(ReactLoopRequest(0, ctx.observed_at, _observation_records(observation)))
    decision = ReactDecision("", "conclude_activity", {"items": [
        {"activity_name": "不存在的活动", "summary": "收尾。", "memories": [], "views": []}], "errands": []}, "")
    assert agent.validate_react_decisions([decision], False) == (
        "NAME_NOT_FOUND: The name 不存在的活动 is not known.")

    damaged = copy.deepcopy(observation)
    damaged.pop("activity_bindings")
    with pytest.raises(RuntimeError, match="activity_bindings are invalid"):
        LifeAgent().prepare_react_loop(ReactLoopRequest(0, ctx.observed_at, _observation_records(damaged)))


def _without_same_name_colleague(document: dict) -> None:
    """场景副本里保持本人通讯录为空，让另一位人物只通过同世界人物绑定为本人所知。"""
    agent = next(item for item in document["agents"] if item["agent_id"] == 1)
    agent["contacts"] = []


def test_relationship_memory_may_name_a_same_world_person_who_is_elsewhere(_engine_factory) -> None:
    """relationship 记忆的对象可以是不在身边的同世界人物（docs/design/06-agent.md「人设层」：已知人物含同世界
    人物名）：另一位人物在办公室、本人在家，本人的通讯录里没有他，本轮工具层也没有他，只有私有绑定 person_bindings
    认得他；宿主接受写他的 relationship 记忆，不认识的名字照常按协议错误纠正。"""
    engine = open_world(_engine_factory, mutate=_without_same_name_colleague)
    place(engine, 2, OFFICE)
    act(engine, "relationship-wait", start_request(targets=["自己"], actions=["interact"], ways=["等待"]))
    observation = observe(engine, "relationship")
    assert PERSON_TARGET not in {row["name"] for row in observation["tool_layer"]["targets"]}
    assert [row["name"] for row in observation["person_bindings"]] == [PERSON_TARGET]
    other_name = PERSON_TARGET.split("：", 1)[1]
    config = next(item for item in engine.context.scenario.agents if item.agent_id == 1)
    assert other_name not in {contact.salutation for contact in config.contacts}
    agent = LifeAgent()
    agent._name, agent._profile = config.name, {**config.model_dump(mode="json"), "id": 1}
    agent.prepare_react_loop(ReactLoopRequest(0, context(engine, "relationship").observed_at,
                                              _observation_records(observation)))
    [waiting] = [row["name"] for row in observation["activity_bindings"]]

    def conclude(about: str) -> ReactDecision:
        """收尾等待并写一条关于 about 的 relationship 看法（写在 views）。"""
        view = {"type": "relationship", "about": about, "text": "他最近在办公室很忙。", "importance": 0.5,
                "keywords": ["同事"]}
        return ReactDecision("", "conclude_activity", {"items": [
            {"activity_name": waiting, "summary": "先收个尾。", "memories": [], "views": [view]}], "errands": []}, "")

    assert agent.validate_react_decisions([conclude(other_name)], False) == ""
    unknown = agent.validate_react_decisions([conclude("陌生人")], False)
    assert unknown == "SUBJECT_NOT_KNOWN: The relationship subject 陌生人 is not a known person."
    assert [(item.path, item.written) for item in unknown.report.items] == [(
        ("tool_calls", 0, "function", "arguments", "items", 0, "views", 0, "about"), "陌生人")]




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
async def test_real_action_observation_starts_visible_action_and_restores_activity(_engine_factory) -> None:
    """模型点名可读动作和应用，真实动作与应用身份随检查点恢复。"""
    from worldmock.life.engine import LifeEngine

    engine = _engine_factory()
    settle_day_plans(engine)
    shown = engine.observations._observation_data(engine.context.state.agents[1])
    app = next(row for row in shown["interaction_bindings"]
                if row["kind"] == "action" and row["object"] == "刷推荐")
    observation, result, schemas = await _agent_action(
        engine, "app-start", "start_activity",
        start_arguments([app["object"]], ["interact"], ["进行"], app=["抖音"]))
    assert [target["kind"] for target in observation["tool_layer"]["targets"]
            if target["name"] == app["object"]] == ["action"]
    assert "enum" not in schemas["start_activity"]["parameters"]["properties"]["items"]["items"]["properties"]["target"]
    assert app["object_id"] not in str(schemas["start_activity"])
    activity = engine.context.state.activities[result.activity_id]
    assert activity.data["action_id"] == app["object_id"]
    assert activity.data["app_id"] == "douyin"
    observation["active_activities"].append({"observer_write": "must stay outside the world"})
    assert all("observer_write" not in item.data for item in engine.context.state.activities.values())
    reference = engine.context.store.assets.put_json({"agent_id": 1}, {"kind": "app_seam_checkpoint"})
    engine.checkpoint({"agent:1": reference})
    run_dir = engine.context.store.run_dir
    engine.close()
    restored = LifeEngine.open(ARTIFICIAL_ENVIRONMENT_ROOT / "scenarios/single-person.json", run_dir)
    try:
        assert restored.context.state.activities[activity.activity_id].data["action_id"] == app["object_id"]
        assert restored.context.state.activities[activity.activity_id].data["app_id"] == "douyin"
        visible = restored.observations._observation_data(restored.context.state.agents[1])
        names = [target["name"] for target in visible["tool_layer"]["targets"] if target["kind"] == "activity"]
        assert activity.name in names
    finally:
        restored.close()


@pytest.mark.asyncio
async def test_native_goal_tools_persist_empty_revision_progress_and_restore(_engine_factory) -> None:
    """零点计划登记的空清单经原生人物修订、环境、SQLite和恢复观察，完整贯通追加与实际推进（时间与能量规格第四章）；
    修订的条目带大致时刻 around_time，登记为 around_at，恢复后的待办层写「约 HH:MM」（docs/design/04-simulation-engine.md ）。"""
    from worldmock.life.engine import LifeEngine

    engine = _engine_factory()
    submitted_at = engine.context.state.current_time
    # 启动路径：零点计划后台任务（确定性桩）经系统登记入口写入空清单，不是人物动作，不推进时间。
    assert settle_day_plans(engine) == [{"agent_id": 1, "date": "2026-01-05", "goals": 0}]
    assert engine.context.state.current_time == submitted_at
    assert engine.context.state.agents[1].day_plan.goals == ()
    generated = [event.data["day_plan"] for event in engine.context.store.read_events(engine.context.store.last_event_seq)
                 if event.event_type == "environment_event" and event.data.get("kind") == "day_plan_generated"]
    assert generated == [engine.context.state.agents[1].day_plan.model_dump(mode="json")]
    before_revision, revision, tools = await _agent_action(
        engine, "goals-append", "revise_day_plan",
        {"goals": [{"intent_kind": "休息", "goal": "缓一缓", "remind_time": None, "around_time": "00:30"}]})
    # 运行开始的首个决策照常开放生活工具与修订，没有先登记计划的门控。
    assert "run_start" in before_revision["wake_reasons"]
    assert "plan_day" not in tools and {"start_activity", "revise_day_plan"} <= set(tools)
    assert set(tools["revise_day_plan"]["parameters"]["properties"]) == {"goals"}
    assert before_revision["todo_layer"]["day_plan"]["goals"] == []
    assert revision.data["day_plan"] == engine.context.state.agents[1].day_plan.model_dump(mode="json")
    assert engine.context.state.agents[1].day_plan.goals[0].around_at == submitted_at + 1800
    assert engine.context.state.current_time == submitted_at
    _, started, _ = await _agent_action(
        engine, "goals-wait", "start_activity",
        start_arguments(["自己"], ["interact"], ["等待"]))
    assert engine.context.state.agents[1].day_plan.goals[0].progressed_at is None
    assert advance_until_ready(engine)
    progressed = engine.context.state.agents[1].day_plan.model_dump(mode="json")
    goal = progressed["goals"][0]
    assert goal["goal"] == "缓一缓"
    assert goal["progressed_at"] > goal["registered_at"]
    assert goal["progress_activity_id"] == started.data["activity"]["activity_id"]
    records = [json.loads(row[0]) for row in engine.context.store.connection.execute("SELECT body FROM events")]
    assert any(row["event_type"] == "action_result" and row["data"].get("day_plan") == progressed
               for row in records) or any(
        row["event_type"] == "activity_progress" and row["data"].get("day_plan") == progressed for row in records)
    # 等待到点即刚结束，人物当秒收尾（第七章「活动一结束就要结束它」），收尾自述写入关联目标。
    assert conclude_pending(engine, "goals-conclude").status.value == "completed"
    expected = engine.context.state.agents[1].day_plan.model_dump(mode="json")
    assert expected["goals"][0] == {**goal, "progress_note": "收尾。"}
    reference = engine.context.store.assets.put_json({"agent_id": 1}, {"kind": "goal_tools_restore_test"})
    checkpoint = engine.checkpoint({"agent:1": reference})
    assert checkpoint.last_event_seq > 0
    run_dir = engine.context.store.run_dir
    engine.close()
    restored = LifeEngine.open(ARTIFICIAL_ENVIRONMENT_ROOT / "scenarios/single-person.json", run_dir)
    try:
        assert restored.context.state.agents[1].day_plan.model_dump(mode="json") == expected
        observation, _, restored_tools = await _agent_action(
            restored, "goals-preserve", "revise_day_plan", {"goals": []})
        assert observation["todo_layer"]["day_plan"]["goals"] == [
            {"intent_kind": "休息", "goal": "缓一缓", "progressed": True, "remind_time": None,
             "around_time": "00:30"}]
        assert [row["task_name"] for row in observation["goal_bindings"]] == ["缓一缓"]
        assert "plan_day" not in restored_tools
        assert restored.context.state.agents[1].day_plan.model_dump(mode="json") == expected
    finally:
        restored.close()


def _recall_round(engine, round_id: str, name: str) -> tuple[str, dict]:
    """同一冻结观察完成宿主校验和纯回看，查询不登记世界请求。"""
    from worldmock.errors import BusinessRuleError
    from worldmock.life.guidance import rejection_guidance

    ctx = context(engine, round_id)
    observation = engine.observe(ctx)
    agent = LifeAgent()
    agent.prepare_react_loop(ReactLoopRequest(0, ctx.observed_at, _observation_records(observation)))
    checked = agent.validate_react_decisions(
        [ReactDecision("", "recall_task_summary", {"task_name": name}, "")], False)
    before, sequence = engine.context.state.model_dump(mode="json"), engine.context.store.last_event_seq
    try:
        data = engine.read_task_summary(1, RecallTaskSummaryRequest(task_name=name), observation)
    except BusinessRuleError as error:
        guidance = rejection_guidance(error)
        data = {key: guidance[key] for key in ("reason", "fix", "items")}
        data.update(error_code=str(error), unmet=list(error.unmet))
    assert engine.context.state.model_dump(mode="json") == before and engine.context.store.last_event_seq == sequence
    return checked, data


def test_goal_recall_names_are_the_goal_texts_written_in_the_todo_layer(_engine_factory) -> None:
    """当日目标的回看名称就是待办层写出的目标文字（系统层规则 12「名称照工具层、待办层中的写法填写」，回看的名称只取
    七层中出现的名称）：私有目标绑定与待办层逐字相同；宿主放行照抄的目标文字，旧拼法「目标：<意图>：<文字>」不在七层中，
    按不可见的名称纠正；环境按目标文字回看到那个目标。两个目标文字相同时名称不唯一，环境以 TASK_NOT_FOUND 业务拒绝，
    不猜是哪一个。"""
    engine = _engine_factory()
    settle_day_plans(engine, StubPlanDayClient(goals=[
        {"intent_kind": "用餐", "goal": "好好吃顿午饭", "remind_time": None, "around_time": "12:00"},
        {"intent_kind": "休息", "goal": "歇一歇", "remind_time": None, "around_time": None},
        {"intent_kind": "娱乐", "goal": "歇一歇", "remind_time": None, "around_time": None}]))
    assert advance_until_ready(engine)
    observation = engine.observe(context(engine, "goal-names-preview"))
    shown = [goal["goal"] for goal in observation["todo_layer"]["day_plan"]["goals"]]
    assert [row["task_name"] for row in observation["goal_bindings"]] == shown == ["好好吃顿午饭", "歇一歇", "歇一歇"]
    old, _ = _recall_round(engine, "goal-names-old", "目标：用餐：好好吃顿午饭")
    assert "NAME_NOT_FOUND" in old and "not known" in old
    checked, found = _recall_round(engine, "goal-names-shown", "好好吃顿午饭")
    assert checked == "" and "error_code" not in found, found
    assert found["recall"] == {"kind": "goal", "goal": "好好吃顿午饭", "intent_kind": "用餐", "progressed": False}
    checked, shared = _recall_round(engine, "goal-names-shared", "歇一歇")
    assert checked == "" and shared["error_code"] == "TASK_NOT_FOUND"


def test_recall_accepts_the_activity_name_as_written_in_self_notes(_engine_factory) -> None:
    """更早的 summary 可按记忆层 self_notes 所写的活动名回看（docs/design/06-agent.md 第三章「窗口」）：条目内不重名时
    活动名去掉了开始时刻后缀，宿主放行这个名称，环境把它解析为那项活动，结果写它在记忆层中的活动名与「已在记忆层」；
    之后另一个条目里又有一项同名的活动，这个名称就指两项活动，环境以 TASK_NOT_FOUND 业务拒绝。"""
    engine = _engine_factory()
    stage_day_plan(engine)
    home = memory_tests._concluding_wait(engine, "label-home", "在家歇好了。", [])
    memory_tests._arrive(engine, "label-office", memory_tests._walk(engine, "label-walk").activity_id)
    label = memory_tests._short(home)
    notes = [note for entry in engine.observe(context(engine, "label-seen"))["memory_layer"]["earlier_today"]
             for note in entry["self_notes"]]
    assert label != home and {"activity": label, "text": "在家歇好了。"} in notes
    checked, recalled = _recall_round(engine, "label-recall", label)
    assert checked == "" and "error_code" not in recalled, recalled
    assert recalled["recall"] == {
        "kind": "activity", "activity": label, "progress_note": "已在记忆层", "note_history": []}
    office = memory_tests._concluding_wait(engine, "label-again", "在公司也歇了。", [])
    memory_tests._arrive(engine, "label-back", memory_tests._walk(engine, "label-return", "home").activity_id)
    assert memory_tests._short(office) == label and office != home
    checked, shared = _recall_round(engine, "label-shared", label)
    assert checked == "" and shared["error_code"] == "TASK_NOT_FOUND"
