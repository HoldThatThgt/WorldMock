"""工具层与可用性单一来源（docs/dev/SPEC-persona-prompt.md「工具层」、验收台账「可用性」）。

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

import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from agentsociety2.agent.base import ReactDecision, ReactLoopRequest

from custom.agents.life_agent import LifeAgent
from custom.envs.life_env import LifeEnv
from tests.life.conftest import (activity_name, advance_next, advance_until_ready, conclude_request, context, drive,
                                 stage_day_plan, start_activity, start_arguments, submit_round, target_actions,
                                 target_tools, tool_targets, wake_by_mail)
from tests.life import test_parallel_admission as parallel_admission
from tests.life import test_same_moment_decisions as same_moment_decisions
from tests.life import test_work_contracts as work_contracts
from tests.life.routing_fixtures import HOME, MARKET, OFFICE, PERSON_TARGET, food_product, open_world, place, stage
from tests.life.test_scheduling import _custom_engine
from tests.life.test_work_review import StaticReviewer, _at_task_start, _paraphrased_submission
from worldmock.errors import IntegrityError
from worldmock.footprints.models import EventDraft
from worldmock.footprints.store import FootprintStore
from worldmock.life import engine as engine_module
from worldmock.life.engine import LifeEngine
from worldmock.life.guidance import refusal_for_code
from worldmock.life.map_models import RouteConfig
from worldmock.life.models import PauseActivityRequest, SameMomentPresentation, SameMomentStage
from worldmock.life.storefront_state import current_quote
from worldmock.life.tool_layer import tool_layer

#: 1 月 5 日 10:00 还没开门、只能到店取餐的饭店（地图中其余门店此刻都开着）。
_CLOSED_AT_TEN = "poi-3538682404"


def _branch(engine: LifeEngine, tmp_path, monkeypatch, label: str) -> LifeEngine:
    """已提交状态的独立副本：复制运行目录，复用同一冻结规则目录、生活活动目录与父引擎已校验过的路线与游戏打开新的引擎
    （副本与父运行同一场景、同一内容，校验结论相同）。"""
    target = tmp_path / "branches" / label
    shutil.copytree(engine.store.run_dir, target)
    with monkeypatch.context() as patch:
        patch.setattr(engine_module, "LifeRuleEngine", lambda scenario, root: engine.rules)
        patch.setattr(engine_module, "load_lifestyle_items", lambda root, met: engine.lifestyle_items)
        patch.setattr(engine_module, "validate_routes", lambda scenario, root: None)
        patch.setattr(engine_module, "validate_game", lambda config, root: None)
        return LifeEngine(engine.semantics, engine.scenario_sha256, engine.corpus_root, FootprintStore(target))


def _give_recipient(engine: LifeEngine, observed: dict) -> str | None:
    """给予的去向：工具层里此刻能给的人物目标（同处的同世界人物，或收货地点就是人物所在地点的联系人），没有时取第一个
    人物目标（身边没有能给的人，给予标不可用）。"""
    config = next(item for item in engine.scenario.agents if item.agent_id == 1)
    places = {item.contact_id: item.receive_location_id for item in config.contacts}
    rows = [row for row in observed["interaction_bindings"] if row["kind"] in {"person", "contact"}]
    here = [row["object"] for row in rows
            if row["kind"] == "person" or places[row["object_id"]] == engine.state.agents[1].location_id]
    return next(iter([*here, *(row["object"] for row in rows)]), None)


def _arguments(tool: str, name: str, item: dict, recipient: str | None) -> dict:
    """单独提交一项候选时模型写出的工具参数（原生 JSON 形状）：start_activity 按目标、动词与方式（给予交给 recipient，
    其余来源与去向取缺省），名称工具按目标名。"""
    if tool == "start_activity":
        routing = {"to": [recipient]} if item["way"] == "给予" else {}
        return start_arguments([name], [item["action"]], [item["way"]], **routing)
    if tool in {"report_status", "pause_activity"}:
        return {"task": [name], "summary": ["继续。" if tool == "report_status" else "先停一下。"]}
    if tool == "conclude_activity":
        return {"task": [name], "summary": ["收尾。"], "memories": [[]]}
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
    name = engine.state.agents[decision.agent_id].name
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
    branch._work_reviewer = StaticReviewer()
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
            shutil.rmtree(branch.store.run_dir)
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
    engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time, visibility="system", data=data)])
    engine.state = staged


def test_every_listed_item_matches_admission_at_run_start(engine_factory, tmp_path, monkeypatch) -> None:
    """运行开始（深夜、在家）：每个列出的动作与工具单独提交，可用性与受理一致，不可用的原因与拒绝一字不差。"""
    engine = engine_factory()
    stage_day_plan(engine)
    checked = _assert_admission_matches(engine, tmp_path, monkeypatch, "start")
    assert any(item["available"] for item in checked) and any(not item["available"] for item in checked)


def test_give_availability_follows_whether_someone_can_be_handed_to(engine_factory, tmp_path, monkeypatch) -> None:
    """多人场景白天、人物 1 在家手边有吃的：李凡在办公室时身边没有能给的人，给予动作标不可用并以同一原因被拒；李凡回家
    后给予动作标可用、交给身边的人被受理；两种状态下全部候选与受理一致。"""
    engine = open_world(engine_factory)
    place(engine, 1, HOME, {food_product(engine): 1.0})
    place(engine, 2, OFFICE)
    stage(engine, lambda state: setattr(state.agents[2], "next_decision_at", state.current_time + 3600))
    alone = [item for item in _assert_admission_matches(engine, tmp_path, monkeypatch, "alone")
             if item.get("way") == "给予"]
    assert alone and not any(item["available"] for item in alone)
    place(engine, 2, HOME)
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
    assert target_actions(observed, "自己")["等待"]["reason"] == f"先给刚结束的{name}收尾"
    checked = _assert_admission_matches(engine, tmp_path, monkeypatch, "pending")
    assert [item["tool"] for item in checked if item["available"]] == ["conclude_activity"]


def test_restricted_round_marks_closed_items_with_the_same_moment_reason(engine_factory, tmp_path,
                                                                          monkeypatch) -> None:
    """达到同刻上限后的受限轮（只开放开始或恢复）：购买、发消息等不开放的动作标同刻限制原因，与受理一致。"""
    engine = engine_factory()
    stage_day_plan(engine)
    staged = engine.state.model_copy(deep=True)
    staged.agents[1] = staged.agents[1].model_copy(update={
        "same_moment_rounds": engine.scenario.max_same_moment_decisions,
        "same_moment_stage": SameMomentStage.START_OR_RESUME}, deep=True)
    staged.version += 1
    engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time, visibility="system",
        data={"kind": "test_restricted_staged"})])
    engine.state = staged
    checked = _assert_admission_matches(engine, tmp_path, monkeypatch, "restricted")
    assert "已经想了好几轮，这一轮只能先定下一件事" in {item["reason"] for item in checked}


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
        "tool": "pause_activity", "available": False, "reason": f"路上的{name}不能暂停"}
    assert target_tools(observed, name)["conclude_activity"]["available"] is True
    assert observed["environment_layer"]["location"]["session_kind"] == "transit"
    _assert_admission_matches(engine, tmp_path, monkeypatch, "road")


def _stage_holding(engine, place: str, product_id: str) -> None:
    """在指定地点放一份持有物并持久提交。"""
    staged = engine.state.model_copy(deep=True)
    agent = staged.agents[1]
    inventory = {key: dict(value) for key, value in agent.inventory.items()}
    inventory.setdefault(place, {})[product_id] = 1.0
    staged.agents[1] = agent.model_copy(update={"inventory": inventory}, deep=True)
    staged.version += 1
    engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time, visibility="system",
        data={"kind": "test_holding_staged"})])
    engine.state = staged


def test_held_elsewhere_and_paused_elsewhere_are_listed_with_reasons(engine_factory, tmp_path, monkeypatch) -> None:
    """到办公室后：家里的持有物作为「持有物：<商品>（<地点>）」列出，食用标不可用并写明只能在当地用；在家暂停的活动在待办层，
    恢复标不可用并点名家，结束可用；全部候选与受理一致。"""
    engine = engine_factory()
    stage_day_plan(engine)
    food = next(item for item in engine.scenario.products if "eat" in item.use_modes)
    _stage_holding(engine, "home", food.product_id)
    _, results = submit_round(engine, {1: ("start_activity", start_activity("等待", "自己"))}, "away-wait")
    waiting = results[0].activity_id
    wake_by_mail(engine, offset=300)
    advance_next(engine)
    submit_round(engine, {1: ("pause_activity", PauseActivityRequest(
        task=[activity_name(engine, waiting)], summary=["先出门。"]))}, "away-pause")
    preview = engine.observe(context(engine, "away-preview"))
    office = next(row["object"] for row in preview["interaction_bindings"] if row["object_id"] == "office")
    _, walking = submit_round(engine, {1: ("start_activity", start_activity("步行", office))}, "away-walk")
    assert advance_until_ready(engine)
    submit_round(engine, {1: ("conclude_activity", conclude_request(activity_name(engine, walking[0].activity_id)))},
                 "away-arrive")
    home = next(item.name for item in engine.scenario.locations if item.location_id == "home")
    observed = engine.observe(context(engine, "away-seen"))
    eat = target_actions(observed, f"持有物：{food.name}（{home}）")["食用"]
    assert (eat["available"], eat["reason"]) == (False, f"这件东西在{home}，只能在当地用")
    name = activity_name(engine, waiting)
    assert name not in tool_targets(observed)
    resume = target_tools(observed, name)["report_status"]
    assert (resume["available"], resume["reason"]) == (False, f"{name}要回到{home}才能接着做，在别处可以直接结束")
    assert target_tools(observed, name)["conclude_activity"]["available"] is True
    _assert_admission_matches(engine, tmp_path, monkeypatch, "away")


def test_repeated_readable_object_names_fail_closed_before_any_binding(tmp_path) -> None:
    """名称唯一：两款游戏同名的场景在加载时就被拒绝，原因写明重复的名称与两处来源（docs/dev/SPEC-dayrun-fidelity.md
    8.1，不再等到运行中途）；绕过加载直接把运行时场景改成同名时，观察投影仍在给出任何绑定之前以 ValueError 失败关闭，
    不让人物用一个名称指向两个对象。"""
    def mutate(document: dict) -> None:
        """第二款游戏改用第一款的名称。"""
        document["games"][1]["title"] = document["games"][0]["title"]

    games = json.loads(Path("scenarios/white-collar-12-weeks.json").read_text(encoding="utf-8"))["games"]
    with pytest.raises(IntegrityError) as failure:
        _custom_engine("white-collar-12-weeks.json", mutate, tmp_path / "load", "repeated-names")
    assert str(failure.value) == (f"Agent 1 would see non-unique object labels: '游戏：{games[0]['title']}' from "
                                  f"game:{games[0]['game_id']}, game:{games[1]['game_id']}")
    engine = _custom_engine("white-collar-12-weeks.json", lambda document: None, tmp_path / "runtime", "runtime-names")
    try:
        stage_day_plan(engine)
        # 两款都在本轮游戏窗口里的游戏改成同名（窗口顺序由排序键与轮转决定，改名不改变顺序）。
        shown = [row["game_id"] for row in engine._menu_windows(engine.state.agents[1])["game"].items
                 if "game_id" in row]
        assert len(shown) >= 2
        first = next(game for game in engine.scenario.games if game.game_id == shown[0])
        renamed = tuple(game.model_copy(update={"title": first.title}) if game.game_id == shown[1] else game
                        for game in engine.scenario.games)
        engine.scenario = engine.scenario.model_copy(update={"games": renamed})
        with pytest.raises(ValueError, match="interactive object labels must be unique"):
            engine.observe(context(engine, "repeated-names"))
    finally:
        engine.close()


def test_tool_layer_fails_closed_when_an_activity_takes_a_target_name(engine_factory) -> None:
    """名称全局唯一：活动名由 visible_names 避开绑定目标名；名称表若把进行中的活动叫成某个绑定目标的名称，工具层以
    IntegrityError 失败关闭，不输出无法唯一解析的目标。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _, results = submit_round(engine, {1: ("start_activity", start_activity("等待", "自己"))}, "clash-wait")
    waiting = results[0].activity_id
    observed = engine.observe(context(engine, "clash-seen"))
    layer_context = engine._layer_context(engine.state.agents[1], (), observed["interaction_bindings"])
    assert activity_name(engine, waiting) in tool_targets(observed)
    clashing = {**layer_context.names, "activities": {**layer_context.names["activities"], waiting: "自己"}}
    with pytest.raises(IntegrityError, match="Tool layer target names must be unique"):
        tool_layer(replace(layer_context, names=clashing))


def test_submit_while_working_says_finish_the_work_first(engine_factory, tmp_path, monkeypatch) -> None:
    """正在做这项工作时：任务目标的 submit_work 标不可用「先结束正在做的工作」，工作活动是进行中的活动目标，合同写达标情况；
    全部候选与受理一致（docs/dev/SPEC-persona-prompt.md 第六章「工时达标与提交状态」）。"""
    engine = engine_factory()
    _at_task_start(engine)
    stage_day_plan(engine)
    task = engine.scenario.tasks[0]
    target = f"工作：{task.title}"
    submit_round(engine, {1: ("start_activity", start_activity("工作", target))}, "busy-work")
    wake_by_mail(engine, offset=600)
    advance_next(engine)
    observed = engine.observe(context(engine, "busy-seen"))
    assert target_tools(observed, target)["submit_work"] == {
        "tool": "submit_work", "available": False, "reason": "先结束正在做的工作"}
    assert tool_targets(observed)[target]["contract"]["remaining"].startswith("有效工时还差")
    assert any(row["kind"] == "activity" and row["phase"] == "进行中" for row in observed["tool_layer"]["targets"])
    _assert_admission_matches(engine, tmp_path, monkeypatch, "busy")


def test_driving_marks_messages_and_submission_unavailable_with_the_admission_reason(engine_factory, tmp_path,
                                                                                   monkeypatch) -> None:
    """驾车途中：人物目标的 send_message 与任务目标的 submit_work 标不可用，原因取受理拒绝的原文「开车时不能发消息」
    「开车时不能提交成果」；车上的全部候选单独提交，可用性与受理一致（docs/dev/SPEC-persona-prompt.md「工具层」
    可用性只有一个来源）。"""
    engine = parallel_admission._engine(engine_factory, location="office", task=True, market=True)
    parallel_admission._driving(engine, "drive")
    observed = engine.observe(context(engine, "drive-seen"))
    assert observed["environment_layer"]["location"]["place_type"] == "驾车"
    task = f"工作：{engine.scenario.tasks[0].title}"
    assert target_tools(observed, task)["submit_work"] == {
        "tool": "submit_work", "available": False, "reason": "开车时不能提交成果"}
    people = [row["name"] for row in observed["tool_layer"]["targets"] if row["kind"] == "person"]
    assert people and all(target_tools(observed, name)["send_message"] == {
        "tool": "send_message", "available": False, "reason": "开车时不能发消息"} for name in people)
    checked = _assert_admission_matches(engine, tmp_path, monkeypatch, "driving")
    assert {"开车时不能发消息", "开车时不能提交成果"} <= {item["reason"] for item in checked}


def test_resume_within_a_minute_of_the_deadline_is_unavailable_with_the_admission_reason(tmp_path,
                                                                                         monkeypatch) -> None:
    """17:29:01 同秒暂停的工作距截止不足 60 秒：待办层恢复（report_status）标不可用，原因与开工同一截止约束
    「离截止不到一分钟，来不及开始」，结束照常可用；在开始地点、占用无冲突时截止先于占用判定；全部候选与受理一致
    （docs/dev/SPEC-persona-prompt.md 第六章「开工与恢复使用同一截止约束」）。"""

    def mutate(document: dict) -> None:
        """首个任务的截止改为 17:30。"""
        document["tasks"][0]["due_at"] = work_contracts._DEADLINE

    engine = _custom_engine("white-collar-12-weeks.json", mutate, tmp_path, "late-resume")
    try:
        work_contracts._stage_office(engine, work_contracts._clock("17:20:00"))
        work_id = work_contracts._start_work(engine, "late-start")
        work_contracts._decide_at(engine, work_contracts._clock("17:29:01"))
        assert work_contracts._pause(engine, "late-pause", work_id).status == "completed"
        observed = engine.observe(context(engine, "late-seen"))
        [paused] = observed["todo_layer"]["paused_activities"]
        tools = {item["tool"]: item for item in paused["tools"]}
        assert tools["report_status"] == {"tool": "report_status", "available": False,
                                          "reason": "离截止不到一分钟，来不及开始"}
        assert tools["conclude_activity"]["available"] is True
        checked = _assert_admission_matches(engine, tmp_path, monkeypatch, "late")
        assert tools["report_status"] in checked
    finally:
        engine.close()


def _start_then_pause(engine: LifeEngine, label: str, request) -> str:
    """开始一项活动，两分钟后（邮件叫醒）暂停它，返回活动名；暂停后没有进行中的活动，人物同一秒再决策。"""
    _, [started] = submit_round(engine, {1: ("start_activity", request)}, f"{label}-start")
    assert started.status == "in_progress", (started.error_code, started.message)
    wake_by_mail(engine, offset=120)
    advance_next(engine)
    name = activity_name(engine, started.activity_id)
    _, [paused] = submit_round(engine, {1: ("pause_activity", PauseActivityRequest(
        task=[name], summary=["先停一下。"]))}, f"{label}-pause")
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


def test_resume_or_conclude_stage_opens_only_resuming_and_ending(engine_factory, tmp_path, monkeypatch) -> None:
    """同刻受限轮「恢复或结束」（真实到达，不改计数）：等待与观看先后在家暂停，两个名额都被暂停的活动占着；同一秒再被拒
    两次后本秒轮数达到上限，阶段为 RESUME_OR_CONCLUDE。此时待办层两项暂停活动的恢复与结束（共四项）可用，其余每一项都标
    同刻限制原因；逐项提交与受理一致。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _start_then_pause(engine, "full-wait", start_activity("等待", "自己"))
    preview = engine.observe(context(engine, "full-preview"))
    content, way = next((row["object"], action["label"]) for row in preview["interaction_bindings"]
                        if row["kind"] == "content" for action in row["actions"]
                        if action["canonical_action"] == "watch")
    _start_then_pause(engine, "full-watch", start_activity(way, content))
    _refuse(engine, "full-refused", engine.scenario.max_same_moment_decisions - 1)
    assert engine.state.agents[1].same_moment_stage == SameMomentStage.RESUME_OR_CONCLUDE
    checked = _assert_admission_matches(engine, tmp_path, monkeypatch, "full")
    assert sorted(item["tool"] for item in checked if item["available"]) == [
        "conclude_activity", "conclude_activity", "report_status", "report_status"]
    assert {item["reason"] for item in checked if not item["available"]} == {
        refusal_for_code("SAME_MOMENT_ACTION_RESTRICTED").reason}


def test_conclude_pending_stage_opens_only_the_conclusion(engine_factory, tmp_path, monkeypatch) -> None:
    """同刻受限轮「只收尾」（真实到达）：等待到点、待收尾，同一秒三次提交开始等待都以「先给刚结束的<活动>收尾」被拒，
    本秒轮数达到上限且仍有待收尾活动，阶段为 CONCLUDE_PENDING。此时只有收尾刚结束的等待可用，其余每一项都标同刻限制原因
    （阶段判定先于待收尾判定）；逐项提交与受理一致。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _, [waited] = submit_round(engine, {1: ("start_activity", start_activity("等待", "自己"))}, "only-wait")
    assert advance_until_ready(engine)
    name = activity_name(engine, waited.activity_id)
    refused = _refuse(engine, "only-refused", engine.scenario.max_same_moment_decisions)
    assert {item.data["reason"] for item in refused} == {f"先给刚结束的{name}收尾"}
    assert engine.state.agents[1].same_moment_stage == SameMomentStage.CONCLUDE_PENDING
    checked = _assert_admission_matches(engine, tmp_path, monkeypatch, "only")
    assert [item["tool"] for item in checked if item["available"]] == ["conclude_activity"]
    assert {item["reason"] for item in checked if not item["available"]} == {
        refusal_for_code("SAME_MOMENT_ACTION_RESTRICTED").reason}


def test_conclude_pending_stage_with_two_pending_opens_each_conclusion(engine_factory, tmp_path, monkeypatch) -> None:
    """同刻受限轮「只收尾」有两项待收尾（真实到达：出行与路上的观看一起结束）：两项活动的 conclude_activity 都标可用，
    单独收尾其中一项也被受理（收尾阶段持续到没有待收尾活动），其余每一项都标同刻限制原因；逐项提交与受理一致（「列出即可
    提交」，docs/dev/SPEC-persona-prompt.md「工具层」）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    travel_id, watch_id = same_moment_decisions._depart(
        engine, "two-depart", same_moment_decisions._content(engine, "two-depart"))
    same_moment_decisions._finish(engine, travel_id, "two-walk")
    names = [activity_name(engine, travel_id), activity_name(engine, watch_id)]
    refused = _refuse(engine, "two-refused", engine.scenario.max_same_moment_decisions)
    assert {item.error_code for item in refused} == {"ACTIVITY_CONCLUSION_REQUIRED"}
    assert engine.state.agents[1].same_moment_stage == SameMomentStage.CONCLUDE_PENDING
    observed = engine.observe(context(engine, "two-preview"))
    assert [target_tools(observed, name)["conclude_activity"]["available"] for name in names] == [True, True]
    checked = _assert_admission_matches(engine, tmp_path, monkeypatch, "two")
    assert [item["tool"] for item in checked if item["available"]] == ["conclude_activity", "conclude_activity"]
    assert {item["reason"] for item in checked if not item["available"]} == {
        refusal_for_code("SAME_MOMENT_ACTION_RESTRICTED").reason}


def test_each_store_product_target_carries_its_source_marker_once(engine_factory) -> None:
    """门店商品目标的来源标记（类型、来源引用与事实截止日，取供给声明的来源）写在该目标第一个动作的 note 中，每个目标
    恰好一次，不含内部身份；远程下单的送达估计照写在它自己的 note；持有物与其他目标没有来源标记（地图与门店规格「地点
    发现与候选呈现」，docs/dev/SPEC-persona-prompt.md 冻结条款修订 #24「来源标记落在工具层商品目标的 note」）。"""
    engine = parallel_admission._engine(engine_factory, location="market", market=True)
    observed = engine.observe(context(engine, "source-marker"))
    supplies = {row["object"]: engine.rules.supplies[row["object_id"]] for row in observed["interaction_bindings"]
                if row["kind"] == "supply"}
    targets = tool_targets(observed)
    assert supplies and any("delivery" in supply.fulfillment_modes for supply in supplies.values())
    kinds = {"fact": "事实", "scenario": "场景假设"}
    for name, supply in supplies.items():
        source = supply.source
        marker = f"来源：{kinds[source.kind]}（{source.url}），事实截止 {source.fact_cutoff.isoformat()}"
        notes = [action["note"] or "" for action in targets[name]["actions"]]
        assert marker in notes[0] and sum(note.count("来源：") for note in notes) == 1, (name, notes)
        assert supply.supply_id not in json.dumps(targets[name], ensure_ascii=False)
        assert all(action["note"].startswith("预计 ") for action in targets[name]["actions"]
                   if action["way"] == "远程下单" and action["available"])
    assert not any("来源：" in (action["note"] or "") for target in targets.values() if target["name"] not in supplies
                   for action in target["actions"])


def _stage_store(engine: LifeEngine) -> dict[str, str]:
    """人物此刻在只能到店取餐、还没开门的饭店里，看过它的餐与集市三件能送到这里的食品（本秒置顶呈现）：其中一件卖完了，
    余额改为最便宜与最贵那两件的价钱之间（余额变动随同一事件记账）。返回 {closed, cheap, sold, dear: 供给身份}。"""
    state = engine.state

    def amount(supply) -> int:
        """@brief 按当前报价买一份的付款。"""
        return engine.semantics.purchase_amount(supply.product_id, current_quote(state, supply.supply_id).price_fen, 1)

    closed = next(item for item in engine.scenario.supplies if item.location_id == _CLOSED_AT_TEN)
    foods = sorted((item for item in engine.scenario.supplies if item.location_id == MARKET
                    and _CLOSED_AT_TEN in item.delivery_destination_ids
                    and "eat" in engine.rules.items[item.product_id].use_modes
                    and current_quote(state, item.supply_id) is not None), key=amount)
    chosen = {"closed": closed, "cheap": foods[0], "sold": foods[1], "dear": foods[-1]}
    balance = (amount(foods[0]) + amount(foods[-1])) // 2
    staged = state.model_copy(deep=True)
    agent = staged.agents[1]
    visible = {item.supply_id for item in chosen.values()}
    agent.visible_supply_ids = visible
    agent.supply_browsed_at = {key: staged.current_time for key in visible}
    agent.supply_browsed_location = {key: agent.location_id for key in visible}
    staged.same_moment_presentation[1] = SameMomentPresentation(stamps={}, pinned_supply_ids=tuple(sorted(visible)))
    staged.supply_stock[chosen["sold"].supply_id] = 0
    spent = staged.accounts[agent.balance_account_id] - balance
    staged.accounts[agent.balance_account_id] = balance
    _commit(engine, staged, {"kind": "test_store_staged", "resource_changes": {
        f"account:{agent.balance_account_id}": -spent, "account:external:scenario": spent}})
    return {key: item.supply_id for key, item in chosen.items()}


def test_closed_sold_out_and_unaffordable_supplies_are_listed_and_refused_alike(engine_factory, tmp_path,
                                                                                monkeypatch) -> None:
    """运行内可能满足的门店条件照常列出并标原因：10:00 人物在还没开门的饭店里，它的餐「到店」标「店没开门」；集市送到这里的
    三件食品「到店」都标「到店<商品>需要在<集市>」（人不在集市），「远程下单」时卖完的一件标「卖完了」、比余额贵的一件标
    「钱不够」、买得起的一件可用；逐项提交与受理一致。"""
    engine = parallel_admission._engine(engine_factory, location=_CLOSED_AT_TEN)
    chosen = _stage_store(engine)
    observed = engine.observe(context(engine, "store-seen"))
    names = {row["object_id"]: row["object"] for row in observed["interaction_bindings"] if row["kind"] == "supply"}
    assert set(names) == set(chosen.values())

    def reason(key: str, way: str) -> str | None:
        """@brief 某件供给某种方式的不可用原因。"""
        return target_actions(observed, names[chosen[key]])[way]["reason"]

    market = next(item.name for item in engine.scenario.locations if item.location_id == MARKET)
    assert reason("closed", "到店") == refusal_for_code("MERCHANT_CLOSED").reason
    assert all(reason(key, "到店") == f"到店{names[chosen[key]]}需要在{market}" for key in ("cheap", "sold", "dear"))
    assert reason("sold", "远程下单") == refusal_for_code("OUT_OF_STOCK").reason
    assert reason("dear", "远程下单") == refusal_for_code("INSUFFICIENT_FUNDS").reason
    assert reason("cheap", "远程下单") is None
    _assert_admission_matches(engine, tmp_path, monkeypatch, "store")


def test_a_co_present_person_asleep_can_be_messaged_but_not_talked_to(engine_factory, tmp_path, monkeypatch) -> None:
    """身边有人：10:00 两人都在家，李凡睡着、人物 1 在等待且手边有吃的。工具层的人物目标写「正在睡觉」，交谈标「对方现在
    不方便交谈」，发消息可用；逐项提交（含把手边的食物给李凡）与受理一致。"""
    engine = open_world(engine_factory)
    place(engine, 1, HOME, {food_product(engine): 1.0})
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


def test_overdue_task_stays_listed_and_its_submission_is_refused_alike(tmp_path, monkeypatch) -> None:
    """逾期任务保留（「工具层」第六章）：17:30 截止的任务在 17:30:01 仍是任务目标，合同写「已过截止」、不列开工，提交标
    「已经过了截止时间」；逐项提交与受理一致。"""

    def mutate(document: dict) -> None:
        """首个任务的截止改为 17:30。"""
        document["tasks"][0]["due_at"] = work_contracts._DEADLINE

    engine = _custom_engine("white-collar-12-weeks.json", mutate, tmp_path, "overdue")
    try:
        work_contracts._stage_office(engine, work_contracts._clock("17:30:01"))
        observed = engine.observe(context(engine, "overdue-seen"))
        target = tool_targets(observed)[f"工作：{engine.scenario.tasks[0].title}"]
        assert (target["contract"]["outlook"], target["actions"]) == ("已过截止", [])
        assert target["tools"] == [{"tool": "submit_work", "available": False, "reason": "已经过了截止时间"}]
        checked = _assert_admission_matches(engine, tmp_path, monkeypatch, "overdue")
        assert target["tools"][0] in checked
    finally:
        engine.close()


def _without_routes(engine: LifeEngine, origin: str, destination: str) -> None:
    """地图从 origin 到 destination 的每种方式都没有路线（NoRoute，冻结地图中没有这样的记录）：替换引擎的运行场景中这几条
    路线的状态，其余不变。"""
    routes = tuple(RouteConfig.model_validate({
        **route.model_dump(), "status": "unreachable", "distance_meters": None, "duration_seconds": None,
        "fare_fen": None, "reason": "NoRoute"}) if (route.origin_id, route.destination_id) == (origin, destination)
        else route for route in engine.scenario.routes)
    scenario = engine.scenario.model_copy(update={"routes": routes})
    engine.scenario = scenario
    engine.semantics = replace(engine.semantics, scenario=scenario)


def test_permanently_impossible_actions_are_not_listed_and_the_host_corrects_them(engine_factory) -> None:
    """永久不能满足的不列（「工具层」「列出即可提交」）：人物在办公室，到一家饭店没有路线、技能够不上当日任务时，这家饭店与
    这项任务都不是工具层目标，名称不出现在工具层中；按这两个名称提交时宿主以协议纠错（本轮纠正，不交给环境）。此前两者
    照常列出（饭店有步行与驾车），其余地点目标不受影响。"""
    engine = parallel_admission._engine(engine_factory, location=OFFICE, task=True)
    restaurant = next(item for item in engine.scenario.locations if item.location_id == _CLOSED_AT_TEN)
    task = engine.scenario.tasks[0]
    place_name, task_name = f"地点：{restaurant.name}", f"工作：{task.title}"
    before = engine.observe(context(engine, "impossible-before"))
    assert {row["way"] for row in tool_targets(before)[place_name]["actions"]} == {"步行", "驾车"}
    assert task_name in tool_targets(before)
    _without_routes(engine, OFFICE, restaurant.location_id)
    stage(engine, lambda state: state.agents[1].skill_levels.update({task.required_skill: task.minimum_skill - 0.1}))
    decision = context(engine, "impossible-after")
    after = engine.observe(decision)
    places = {row["name"] for row in after["tool_layer"]["targets"] if row["kind"] == "location"}
    assert places == {row["name"] for row in before["tool_layer"]["targets"] if row["kind"] == "location"} - {
        place_name}
    assert task_name not in tool_targets(after)
    assert restaurant.name not in json.dumps(after["tool_layer"], ensure_ascii=False)
    assert task.title not in json.dumps(after["tool_layer"], ensure_ascii=False)
    person = _host(engine, decision, after)
    travel = start_arguments([place_name], ["travel"], ["步行"])
    assert person.validate_react_decisions([ReactDecision("", "start_activity", travel, "")], False) == (
        "NAME_NOT_FOUND: start_activity target is not a visible interactive object (targets[0])")
    submit = _arguments("submit_work", task_name, {}, None)
    assert person.validate_react_decisions([ReactDecision("", "submit_work", submit, "")], False) == (
        "NAME_NOT_FOUND: task_name is not visible")
