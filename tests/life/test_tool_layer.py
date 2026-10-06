"""工具层与可用性单一来源（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求「工具层」、验收台账「可用性」）。

性质测试：在真实引擎状态上，工具层（与待办层）列出的每一项动作与工具（逐项，不按等价类抽样）各在同一份已提交状态的独立
副本上单独提交一次，互不影响：宿主（绑定真实 LifeEnv 的 LifeAgent，按该副本本轮观察冻结六层与私有绑定）对每一项的本地校验
validate_react_decisions 都返回空串（列出即可提交），并经宿主的真实派发路径交给环境；available 为 false 的由环境以同一原因
业务拒绝（action_result.data.reason 与 reason 一字不差），available 为 true 的被环境受理。副本的本轮观察与列出时的可用性
逐项相同。覆盖运行开始、待收尾、同刻受限轮的三种阶段（开始或恢复、恢复或结束、只收尾，只收尾另有两项待收尾各自可收尾的一例）、
路上、他处持有物与别处暂停的活动、工作进行中、距截止不足一分钟暂停的工作、驾车途中、门店没开门/卖完了/余额不够、
身边有人（对方睡着）、逾期任务、给予有无对象等状态；另验证永久不能满足的动作（没有路线、技能不够）不列出且宿主按协议纠错，名称唯一、他处持有物的名称带
地点、路上的活动不能暂停、正在做的工作提交写「先结束正在做的工作」、截止前一分钟内不能恢复工作、驾车时发消息与提交成果写
「开车时不能…」。
"""

from __future__ import annotations


import shutil
from dataclasses import replace

import pytest
from agentsociety2.agent.base import ReactDecision, ReactLoopRequest

from custom.agents.life_agent import LifeAgent
from custom.envs.life_env import LifeEnv
from tests.life.conftest import (activity_name, advance_next, advance_until_ready, conclude_request, context, drive,
                                 stage_day_plan, start_activity, start_arguments, submit_round, target_actions,
                                 target_tools, tool_targets, wake_by_mail, pause_request)
from tests.life import test_parallel_admission as parallel_admission
from datetime import datetime
from zoneinfo import ZoneInfo

HOME, OFFICE, PERSON_TARGET = "home", "office", "人物：李凡"
from tests.life.test_work_review import StaticReviewer, _at_task_start, _paraphrased_submission
from worldmock.errors import IntegrityError
from worldmock.footprints.models import EventDraft
from worldmock.footprints.store import FootprintStore
from worldmock.life.engine import LifeEngine
from worldmock.life.guidance import refusal_for_code
from worldmock.life.models import SameMomentStage
from worldmock.life.tool_layer import tool_layer



def _branch(engine: LifeEngine, tmp_path, monkeypatch, label: str) -> LifeEngine:
    """@brief 复制已提交运行目录，再由真实规则与存储构造独立引擎。"""
    target = tmp_path / "branches" / label
    shutil.copytree(engine.context.store.run_dir, target)
    return LifeEngine(engine.context.semantics, engine.context.scenario_sha256,
                      engine.context.corpus_root, FootprintStore(target))


def _give_recipient(engine: LifeEngine, observed: dict) -> str | None:
    """给予的去向：工具层里此刻能给的人物目标（同处的同世界人物，或收货地点就是人物所在地点的联系人），没有时取第一个
    人物目标（身边没有能给的人，给予标不可用）。"""
    config = next(item for item in engine.context.scenario.agents if item.agent_id == 1)
    places = {item.contact_id: item.receive_location_id for item in config.contacts}
    rows = [row for row in observed["interaction_bindings"] if row["kind"] in {"person", "contact"}]
    here = [row["object"] for row in rows
            if row["kind"] == "person" or places[row["object_id"]] == engine.context.state.agents[1].location_id]
    return next(iter([*here, *(row["object"] for row in rows)]), None)


def _arguments(tool: str, name: str, item: dict, recipient: str | None) -> dict:
    """单独提交一项候选时模型写出的工具参数（原生 JSON 形状）：start_activity 按目标、动词与方式（给予交给 recipient，
    其余来源与去向取缺省），名称工具按目标名。"""
    if tool == "start_activity":
        routing = {"to": [recipient]} if item["way"] == "给予" else {}
        return start_arguments([name], [item["action"]], [item["way"]], **routing)
    if tool in {"report_status", "pause_activity"}:
        return {"items": [{"activity_name": name, "summary": "继续。" if tool == "report_status" else "先停一下。"}],
                "errands": []}
    if tool == "conclude_activity":
        return {"items": [{"activity_name": name, "summary": "收尾。", "memories": [], "views": []}], "errands": []}
    if tool == "submit_work":
        return {"task_name": name, "deliverable": _paraphrased_submission().deliverable.model_dump(mode="json")}
    return {"recipient_name": name, "text": "你好"}


def _candidates(observed: dict) -> list[tuple[str, str, dict]]:
    """工具层每个目标的每个动作与工具、待办层每项暂停活动的工具：（工具名，目标名，可用性行）。"""
    rows = []
    for target in observed["tool_layer"]["targets"]:
        rows.extend(("start_activity", target["name"], item) for item in target["actions"])
        rows.extend((item["tool"], target["name"], item) for item in target["tools"])
    for item in observed["todo_layer"]["paused_activities"]:
        rows.extend((tool["tool"], item["name"], tool) for tool in item["tools"])
    return rows


def _host(engine: LifeEngine, decision, observed: dict) -> LifeAgent:
    """宿主：绑定真实 LifeEnv 与本轮宿主身份的 LifeAgent，按本轮观察冻结六层、菜单、私有绑定与工具面（与 run_react_loop
    开头同一准备）。"""
    person = LifeAgent()
    name = engine.context.state.agents[decision.agent_id].name
    person._id, person._name, person._profile = decision.agent_id, name, {"id": decision.agent_id, "name": name}
    environment = LifeEnv()
    environment.engine = engine
    person._env, person._decision_context, person._max_react_turns = environment, decision, 3
    person.prepare_react_loop(ReactLoopRequest(0, decision.observed_at, [
        {"turn": 0, "action": "observe", "ok": True, "data": observed}]))
    return person


def _submitted(branch: LifeEngine, label: str, listing: list, candidate: tuple, recipient: str | None):
    """在副本上开一轮：本轮观察的可用性与列出时逐项相同；宿主本地校验放行这一项，经宿主派发交给环境，结算后返回结果。"""
    tool, name, item = candidate
    decision = context(branch, label)
    branch.begin_decision_round([decision])
    observed = branch.observe(decision)
    assert _candidates(observed) == listing
    person = _host(branch, decision, observed)
    arguments = _arguments(tool, name, item, recipient)
    assert person.validate_react_decisions([ReactDecision("", tool, arguments, "")], False) == "", candidate
    branch.reviews.reviewer = StaticReviewer()
    drive(person.dispatch_react_tool(tool, arguments))
    return branch.commit_decision_round(decision.round_id)[0]


def _assert_admission_matches(engine: LifeEngine, tmp_path, monkeypatch, label: str) -> list[dict]:
    """逐项单独提交当前观察列出的每一个候选（各在一份独立副本上），断言宿主对每一项都放行、环境对不可用的以同一原因拒绝、
    对可用的受理；返回全部候选可用性行。"""
    observed = engine.observe(context(engine, f"{label}-listing"))
    names = [row["name"] for row in observed["tool_layer"]["targets"]]
    assert len(names) == len(set(names))
    listing = _candidates(observed)
    recipient = _give_recipient(engine, observed)
    for index, candidate in enumerate(listing):
        branch = _branch(engine, tmp_path, monkeypatch, f"{label}-{index}")
        try:
            result = _submitted(branch, f"{label}-{index}", listing, candidate, recipient)
        finally:
            branch.close()
            shutil.rmtree(branch.context.store.run_dir)
        tool, name, item = candidate
        described = (tool, name, item.get("way"), result.error_code, result.data.get("reason"))
        if item["available"]:
            assert result.status != "rejected", described
        else:
            assert result.status == "rejected" and result.data["reason"] == item["reason"], (described, item)
    return [item for _, _, item in listing]


def _commit(engine: LifeEngine, staged, data: dict) -> None:
    """把改写后的世界副本连同一条系统事件经存储提交（版本加一）。"""
    staged.version += 1
    engine.context.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time, visibility="system", data=data)])
    engine.context.state = staged


def _stage(engine: LifeEngine, change) -> None:
    """@brief 在实际世界副本应用测试所需事实，通过真实存储连续提交。"""
    staged = engine.context.state.model_copy(deep=True)
    change(staged)
    _commit(engine, staged, {"kind": "test_tool_layer_position"})


def _place(engine: LifeEngine, agent_id: int, location: str, holdings: dict | None = None) -> None:
    """@brief 设置真实人物位置与按登记食品身份记录的持有物。"""
    def change(state):
        state.agents[agent_id].location_id = location
        if holdings is not None:
            state.agents[agent_id].inventory = {location: dict(holdings)}
    _stage(engine, change)


def _food_id(engine: LifeEngine) -> str:
    """@brief 从实际登记食品菜单选一个可消费食品身份。"""
    return next(iter(engine.context.semantics.catalog.foods))


def _open_world(engine_factory) -> LifeEngine:
    """@brief 打开实际多人环境，设置日间时刻、空计划与当前身体结算时间。"""
    engine = engine_factory("multi-person.json")
    for agent_id in engine.context.state.agents:
        stage_day_plan(engine, agent_id)
    moment = int(datetime(2026, 1, 5, 10, tzinfo=ZoneInfo(engine.context.scenario.timezone)).timestamp())
    def change(state):
        state.current_time = moment
        state.pending_events = [event for event in state.pending_events if event.due_at > moment]
        for agent in state.agents.values():
            agent.body_settled_at = moment
            agent.next_decision_at = moment
    _stage(engine, change)
    return engine


def _reach_limit(engine: LifeEngine) -> None:
    """@brief 通过真实无效名称请求到达同刻决策上限。"""
    while engine.context.state.agents[1].same_moment_rounds < engine.context.scenario.max_same_moment_decisions:
        label = f"reach-limit-{engine.context.state.agents[1].same_moment_rounds}"
        _, [result] = submit_round(engine, {1: ("start_activity", start_activity("打开", "不存在的应用"))}, label)
        assert result.status == "rejected"


def _finish(engine: LifeEngine, activity_id: str, label: str) -> None:
    """@brief 经真实时间边界完成活动，就绪的继续请求使用实际播报入口。"""
    from tests.life.conftest import report_request
    for step in range(64):
        if engine.context.state.activities[activity_id].status != "active":
            return
        if engine.scheduling_state().ready_agent_ids:
            submit_round(engine, {1: ("report_status", report_request(
                activity_name(engine, activity_id), summary="继续。"))}, f"{label}-{step}")
        else:
            advance_next(engine)
    pytest.fail("Activity did not end at its declared boundaries")


def test_every_listed_item_matches_admission_at_run_start(engine_factory, tmp_path, monkeypatch) -> None:
    """运行开始（深夜、在家）：每个列出的动作与工具单独提交，可用性与受理一致，不可用的原因与拒绝一字不差。"""
    engine = engine_factory()
    stage_day_plan(engine)
    checked = _assert_admission_matches(engine, tmp_path, monkeypatch, "start")
    assert any(item["available"] for item in checked) and any(not item["available"] for item in checked)


def test_give_availability_follows_whether_someone_can_be_handed_to(engine_factory, tmp_path, monkeypatch) -> None:
    """多人场景白天、人物 1 在家手边有吃的：李凡在办公室时身边没有能给的人，给予动作标不可用并以同一原因被拒；李凡回家
    后给予动作标可用、交给身边的人被受理；两种状态下全部候选与受理一致。"""
    engine = _open_world(engine_factory)
    _place(engine, 1, HOME, {_food_id(engine): 1.0})
    _place(engine, 2, OFFICE)
    _stage(engine, lambda state: setattr(state.agents[2], "next_decision_at", state.current_time + 3600))
    alone = [item for item in _assert_admission_matches(engine, tmp_path, monkeypatch, "alone")
             if item.get("way") == "给予"]
    assert alone and not any(item["available"] for item in alone)
    _place(engine, 2, HOME)
    together = [item for item in _assert_admission_matches(engine, tmp_path, monkeypatch, "together")
                if item.get("way") == "给予"]
    assert together and all(item["available"] for item in together)


def test_pending_conclusion_marks_everything_but_conclusion_unavailable(engine_factory, tmp_path,
                                                                         monkeypatch) -> None:
    """活动刚结束待收尾时，除了结束它以外的动作与工具都标不可用，原因是「先给刚结束的<活动>收尾」，与受理一致。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _, results = submit_round(engine, {1: ("start_activity", start_activity("等待", "自己"))}, "pending-wait")
    assert advance_until_ready(engine)
    name = activity_name(engine, results[0].activity_id)
    observed = engine.observe(context(engine, "pending-preview"))
    assert target_tools(observed, name)["conclude_activity"]["available"] is True
    assert target_actions(observed, "自己")["等待"]["reason"] == f"Conclude the finished activities {name} first."
    checked = _assert_admission_matches(engine, tmp_path, monkeypatch, "pending")
    assert [item["tool"] for item in checked if item["available"]] == ["conclude_activity"]


def test_restricted_round_marks_closed_items_with_the_same_moment_reason(engine_factory, tmp_path,
                                                                          monkeypatch) -> None:
    """达到同刻上限后的受限轮（只开放开始或恢复）：购买、发消息等不开放的动作标同刻限制原因，与受理一致。"""
    engine = engine_factory()
    stage_day_plan(engine)
    staged = engine.context.state.model_copy(deep=True)
    staged.agents[1] = staged.agents[1].model_copy(update={
        "same_moment_rounds": engine.context.scenario.max_same_moment_decisions,
        "same_moment_stage": SameMomentStage.START_OR_RESUME}, deep=True)
    staged.version += 1
    engine.context.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time, visibility="system",
        data={"kind": "test_restricted_staged"})])
    engine.context.state = staged
    checked = _assert_admission_matches(engine, tmp_path, monkeypatch, "restricted")
    assert "This same-moment stage only accepts its offered action types." in {item["reason"] for item in checked}


def test_road_activity_cannot_be_paused_and_matches_admission(engine_factory, tmp_path, monkeypatch) -> None:
    """路上的出行不能暂停：暂停标不可用「路上的<活动>不能暂停」，结束照常可用；在路上的全部候选与受理一致。"""
    engine = engine_factory()
    stage_day_plan(engine)
    preview = engine.observe(context(engine, "road-preview"))
    office = next(row["object"] for row in preview["interaction_bindings"] if row["object_id"] == "office")
    _, results = submit_round(engine, {1: ("start_activity", start_activity("步行", office))}, "road-walk")
    wake_by_mail(engine, offset=120)
    advance_next(engine)
    name = activity_name(engine, results[0].activity_id)
    observed = engine.observe(context(engine, "road-seen"))
    assert target_tools(observed, name)["pause_activity"] == {
        "tool": "pause_activity", "available": False, "reason": f"The activity {name} cannot be paused while travelling."}
    assert target_tools(observed, name)["conclude_activity"]["available"] is True
    assert observed["environment_layer"]["location"]["session_kind"] == "transit"
    _assert_admission_matches(engine, tmp_path, monkeypatch, "road")


def _stage_holding(engine, place: str, product_id: str) -> None:
    """在指定地点放一份持有物并持久提交。"""
    staged = engine.context.state.model_copy(deep=True)
    agent = staged.agents[1]
    inventory = {key: dict(value) for key, value in agent.inventory.items()}
    inventory.setdefault(place, {})[product_id] = 1.0
    staged.agents[1] = agent.model_copy(update={"inventory": inventory}, deep=True)
    staged.version += 1
    engine.context.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time, visibility="system",
        data={"kind": "test_holding_staged"})])
    engine.context.state = staged


def test_held_elsewhere_and_paused_elsewhere_are_listed_with_reasons(engine_factory, tmp_path, monkeypatch) -> None:
    """到办公室后：家里的持有物作为「持有物：<商品>（<地点>）」列出，食用标不可用并写明只能在当地用；在家暂停的活动在待办层，
    恢复标不可用并点名家，结束可用；全部候选与受理一致。"""
    engine = engine_factory()
    stage_day_plan(engine)
    food = next(iter(engine.context.semantics.catalog.foods.values()))
    _stage_holding(engine, "home", food.food_id)
    _, results = submit_round(engine, {1: ("start_activity", start_activity("等待", "自己"))}, "away-wait")
    waiting = results[0].activity_id
    wake_by_mail(engine, offset=300)
    advance_next(engine)
    submit_round(engine, {1: ("pause_activity", pause_request(
        activity_name(engine, waiting), summary="先出门。"))}, "away-pause")
    preview = engine.observe(context(engine, "away-preview"))
    office = next(row["object"] for row in preview["interaction_bindings"] if row["object_id"] == "office")
    _, walking = submit_round(engine, {1: ("start_activity", start_activity("步行", office))}, "away-walk")
    assert advance_until_ready(engine)
    submit_round(engine, {1: ("conclude_activity", conclude_request(activity_name(engine, walking[0].activity_id)))},
                 "away-arrive")
    home = next(item.display_name for item in engine.context.scenario.locations if item.location_id == "home")
    observed = engine.observe(context(engine, "away-seen"))
    eat = target_actions(observed, f"持有物：{food.name}（{home}）")["食用"]
    assert (eat["available"], eat["reason"]) == (False, f"This item is at {home} and must be used there.")
    name = activity_name(engine, waiting)
    assert name not in tool_targets(observed)
    resume = target_tools(observed, name)["report_status"]
    assert (resume["available"], resume["reason"]) == (False, f"Resume {name} at its start location {home}; it can be concluded elsewhere.")
    assert target_tools(observed, name)["conclude_activity"]["available"] is True
    _assert_admission_matches(engine, tmp_path, monkeypatch, "away")




def test_tool_layer_fails_closed_when_an_activity_takes_a_target_name(engine_factory) -> None:
    """名称全局唯一：活动名由 visible_names 避开绑定目标名；名称表若把进行中的活动叫成某个绑定目标的名称，工具层以
    IntegrityError 失败关闭，不输出无法唯一解析的目标。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _, results = submit_round(engine, {1: ("start_activity", start_activity("等待", "自己"))}, "clash-wait")
    waiting = results[0].activity_id
    observed = engine.observe(context(engine, "clash-seen"))
    layer_context = engine.observations._layer_context(engine.context.state.agents[1], (), observed["interaction_bindings"])
    assert activity_name(engine, waiting) in tool_targets(observed)
    clashing = {**layer_context.names, "activities": {**layer_context.names["activities"], waiting: "自己"}}
    with pytest.raises(IntegrityError, match="Tool layer target names must be unique"):
        tool_layer(replace(layer_context, names=clashing), observed["tool_layer"]["callable_index"])


def test_tool_layer_carries_the_callable_index_beside_the_targets(engine_factory) -> None:
    """工具层 JSON 依次是 layer、targets 与只列名称的可调用索引 callable_index（docs/dev/SPEC-interaction-menu.md#dayrun-fidelity-呈现与浏览-只列名称的可调用索引 ）：
    观察里的索引就是工具层附上的那一份，目标照旧；索引不是目标，不参与目标名称的唯一性判定——索引里写上与目标同名的
    名称，工具层也不失败关闭。"""
    engine = engine_factory()
    stage_day_plan(engine)
    observed = engine.observe(context(engine, "index-beside"))
    assert list(observed["tool_layer"]) == ["layer", "targets", "callable_index"]
    layer_context = engine.observations._layer_context(engine.context.state.agents[1], (), observed["interaction_bindings"])
    clashing = {"lifestyle": list(tool_targets(observed)), "goods": [], "query_categories": []}
    layer = tool_layer(layer_context, clashing)
    assert layer["callable_index"] is clashing and layer["targets"] == observed["tool_layer"]["targets"]


def test_submit_while_working_says_finish_the_work_first(engine_factory, tmp_path, monkeypatch) -> None:
    """正在做这项工作时：任务目标的 submit_work 标不可用「先结束正在做的工作」，工作活动是进行中的活动目标，合同写达标情况；
    全部候选与受理一致（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第六章「工时达标与提交状态」）。"""
    engine = engine_factory()
    _at_task_start(engine)
    stage_day_plan(engine)
    task = engine.context.scenario.tasks[0]
    target = f"工作：{task.title}"
    submit_round(engine, {1: ("start_activity", start_activity("工作", target))}, "busy-work")
    wake_by_mail(engine, offset=600)
    advance_next(engine)
    observed = engine.observe(context(engine, "busy-seen"))
    assert target_tools(observed, target)["submit_work"] == {
        "tool": "submit_work", "available": False, "reason": "This work activity is still in progress."}
    assert tool_targets(observed)[target]["contract"]["remaining"].startswith("有效工时还差")
    assert any(row["kind"] == "activity" and row["phase"] == "进行中" for row in observed["tool_layer"]["targets"])
    _assert_admission_matches(engine, tmp_path, monkeypatch, "busy")


def test_driving_marks_messages_and_submission_unavailable_with_the_admission_reason(engine_factory, tmp_path,
                                                                                   monkeypatch) -> None:
    """驾车途中：人物目标的 send_message 与任务目标的 submit_work 标不可用，原因取受理拒绝的原文「Cannot send a message while driving.」
    「Cannot submit work while driving.」；车上的全部候选单独提交，可用性与受理一致（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求「工具层」
    可用性只有一个来源）。"""
    engine = parallel_admission._engine(engine_factory, location="office", task=True)
    parallel_admission._driving(engine, "drive")
    observed = engine.observe(context(engine, "drive-seen"))
    assert observed["environment_layer"]["location"]["place_type"] == "驾车"
    task = f"工作：{engine.context.scenario.tasks[0].title}"
    assert target_tools(observed, task)["submit_work"] == {
        "tool": "submit_work", "available": False, "reason": "Cannot submit work while driving."}
    people = [row["name"] for row in observed["tool_layer"]["targets"] if row["kind"] == "person"]
    assert people and all(target_tools(observed, name)["send_message"] == {
        "tool": "send_message", "available": False, "reason": "Cannot send a message while driving."} for name in people)
    checked = _assert_admission_matches(engine, tmp_path, monkeypatch, "driving")
    assert {"Cannot send a message while driving.", "Cannot submit work while driving."} <= {item["reason"] for item in checked}




def _start_then_pause(engine: LifeEngine, label: str, request) -> str:
    """开始一项活动，两分钟后（邮件叫醒）暂停它，返回活动名；暂停后没有进行中的活动，人物同一秒再决策。"""
    _, [started] = submit_round(engine, {1: ("start_activity", request)}, f"{label}-start")
    assert started.status == "in_progress", (started.error_code, started.message)
    wake_by_mail(engine, offset=120)
    advance_next(engine)
    name = activity_name(engine, started.activity_id)
    _, [paused] = submit_round(engine, {1: ("pause_activity", pause_request(name, summary="先停一下。"))}, f"{label}-pause")
    assert paused.status == "completed", (paused.error_code, paused.message)
    return name


def _refuse(engine: LifeEngine, label: str, times: int) -> list:
    """同一秒连续提交 times 次开始等待（此刻都会被业务拒绝），返回各次结果。"""
    results = []
    for index in range(times):
        _, [result] = submit_round(engine, {1: ("start_activity", start_activity("等待", "自己"))},
                                   f"{label}-{index}")
        assert result.status == "rejected", (result.error_code, result.message)
        results.append(result)
    return results


def test_both_paused_at_the_limit_open_starting_and_resuming_not_ending(engine_factory, tmp_path, monkeypatch) -> None:
    """两项都暂停到同刻上限（真实到达，不改计数）：等待与使用应用先后在家暂停，暂停的活动不占名额；同一秒再被拒到上限后阶段
    为开始或恢复（START_OR_RESUME），不再进入恢复或结束。此时待办层两项暂停活动的恢复可用、结束标同刻限制原因，开始等待
    可用；逐项提交与受理一致。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _start_then_pause(engine, "full-wait", start_activity("等待", "自己"))
    preview = engine.observe(context(engine, "full-preview"))
    app, way = next((row["object"], action["label"]) for row in preview["interaction_bindings"]
                        if row["kind"] == "app" for action in row["actions"]
                        if action["canonical_action"] == "use_app")
    _start_then_pause(engine, "full-app", start_activity(way, app))
    _reach_limit(engine)
    assert engine.context.state.agents[1].same_moment_stage == SameMomentStage.START_OR_RESUME
    observed = engine.observe(context(engine, "full-stage"))
    paused = [tool for item in observed["todo_layer"]["paused_activities"] for tool in item["tools"]]
    restricted = refusal_for_code("SAME_MOMENT_ACTION_RESTRICTED").reason
    assert sorted((tool["tool"], tool["available"], tool.get("reason")) for tool in paused) == [
        ("conclude_activity", False, restricted), ("conclude_activity", False, restricted),
        ("report_status", True, None), ("report_status", True, None)]
    checked = _assert_admission_matches(engine, tmp_path, monkeypatch, "full")
    assert [item["available"] for item in checked if item.get("way") == "等待"] == [True]


def test_conclude_pending_stage_opens_only_the_conclusion(engine_factory, tmp_path, monkeypatch) -> None:
    """同刻受限轮「只收尾」（真实到达）：等待到点、待收尾，同一秒三次提交开始等待都以「先给刚结束的<活动>收尾」被拒，
    本秒轮数达到上限且仍有待收尾活动，阶段为 CONCLUDE_PENDING。此时只有收尾刚结束的等待可用，其余每一项都标同刻限制原因
    （阶段判定先于待收尾判定）；逐项提交与受理一致。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _, [waited] = submit_round(engine, {1: ("start_activity", start_activity("等待", "自己"))}, "only-wait")
    assert advance_until_ready(engine)
    name = activity_name(engine, waited.activity_id)
    refused = _refuse(engine, "only-refused", engine.context.scenario.max_same_moment_decisions)
    assert {item.data["reason"] for item in refused} == {f"Conclude the finished activities {name} first."}
    assert engine.context.state.agents[1].same_moment_stage == SameMomentStage.CONCLUDE_PENDING
    checked = _assert_admission_matches(engine, tmp_path, monkeypatch, "only")
    assert [item["tool"] for item in checked if item["available"]] == ["conclude_activity"]
    assert {item["reason"] for item in checked if not item["available"]} == {
        refusal_for_code("SAME_MOMENT_ACTION_RESTRICTED").reason}


def test_conclude_pending_stage_with_two_pending_opens_each_conclusion(engine_factory, tmp_path, monkeypatch) -> None:
    """同刻受限轮「只收尾」有两项待收尾（真实到达：出行与路上的使用应用一起结束）：两项活动的 conclude_activity 都标可用，
    单独收尾其中一项也被受理（收尾阶段持续到没有待收尾活动），其余每一项都标同刻限制原因；逐项提交与受理一致（「列出即可
    提交」，docs/dev/SPEC-life-realism.md#persona-prompt-七层请求「工具层」）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    preview = engine.observe(context(engine, "two-preview"))
    destination = next(row["object"] for row in preview["interaction_bindings"] if row["object_id"] == "office")
    app = next(row["object"] for row in preview["interaction_bindings"] if row["kind"] == "app"
               and any(action["note"] is None for action in row["actions"]))
    _, [started] = submit_round(engine, {1: ("start_activity", start_arguments(
        [destination, app], ["travel", "interact"], ["步行", "打开"]))}, "two-depart")
    assert started.status == "in_progress"
    travel_id, app_id = (item["activity_id"] for item in started.data["activities"])
    _finish(engine, travel_id, "two-walk")
    names = [activity_name(engine, travel_id), activity_name(engine, app_id)]
    refused = _refuse(engine, "two-refused", engine.context.scenario.max_same_moment_decisions)
    assert {item.error_code for item in refused} == {"ACTIVITY_CONCLUSION_REQUIRED"}
    assert engine.context.state.agents[1].same_moment_stage == SameMomentStage.CONCLUDE_PENDING
    observed = engine.observe(context(engine, "two-preview"))
    assert [target_tools(observed, name)["conclude_activity"]["available"] for name in names] == [True, True]
    checked = _assert_admission_matches(engine, tmp_path, monkeypatch, "two")
    assert [item["tool"] for item in checked if item["available"]] == ["conclude_activity", "conclude_activity"]
    assert {item["reason"] for item in checked if not item["available"]} == {
        refusal_for_code("SAME_MOMENT_ACTION_RESTRICTED").reason}








def test_a_co_present_person_asleep_can_be_messaged_but_not_talked_to(engine_factory, tmp_path, monkeypatch) -> None:
    """身边有人：10:00 两人都在家，李凡睡着、人物 1 在等待且手边有吃的。工具层的人物目标写「正在睡觉」，交谈标「对方现在
    不方便交谈」，发消息可用；逐项提交（含把手边的食物给李凡）与受理一致。"""
    engine = _open_world(engine_factory)
    _place(engine, 1, HOME, {_food_id(engine): 1.0})
    submit_round(engine, {1: ("start_activity", start_activity("等待", "自己")),
                          2: ("start_activity", start_activity("睡觉", "自己"))}, "company")
    wake_by_mail(engine, offset=120)
    advance_next(engine)
    assert engine.scheduling_state().ready_agent_ids == (1,)
    observed = engine.observe(context(engine, "company-seen"))
    assert tool_targets(observed)[PERSON_TARGET]["state"] == "正在睡觉"
    assert target_actions(observed, PERSON_TARGET)["交谈"]["reason"] == refusal_for_code(
        "CHAT_PARTNER_NOT_AVAILABLE").reason
    assert target_tools(observed, PERSON_TARGET)["send_message"]["available"] is True
    _assert_admission_matches(engine, tmp_path, monkeypatch, "company")
