"""工具层与可用性单一来源（docs/dev/SPEC-persona-prompt.md「工具层」）。

性质测试：在真实引擎状态上，工具层（与待办层）列出的每个动作与工具单独提交时——available 为 false 的由环境以同一原因业务
拒绝（action_result.data.reason 与 reason 一字不差），available 为 true 的被受理。候选按（目标类别，工具，方式，可用性，
原因）分成等价类，每类在同一份已提交状态的独立副本上单独提交，互不影响。覆盖运行开始、待收尾、同刻受限轮、路上、他处持有物与别处暂停的活动、工作进行中、
距截止不足一分钟暂停的工作七种状态；另验证名称唯一、他处持有物的名称带地点、路上的活动不能暂停、正在做的工作提交写
「先结束正在做的工作」、截止前一分钟内不能恢复工作。
"""

from __future__ import annotations

import shutil

from agentsociety2.society import DirectToolCall

from tests.life.conftest import (activity_name, advance_next, advance_until_ready, conclude_request, context, drive,
                                 stage_day_plan, start_activity, submit_round, target_actions, target_tools,
                                 tool_targets, wake_by_mail, start_request)
from tests.life import test_work_contracts as work_contracts
from tests.life.routing_fixtures import HOME, OFFICE, food_product, open_world, place, stage
from tests.life.test_scheduling import _custom_engine
from tests.life.test_work_review import StaticReviewer, _at_task_start, _paraphrased_submission
from worldmock.footprints.models import EventDraft
from worldmock.footprints.store import FootprintStore
from worldmock.life import engine as engine_module
from worldmock.life.engine import LifeEngine
from worldmock.life.models import (PauseActivityRequest, ReportStatusRequest, SameMomentStage,
                                   SendMessageActionRequest, SubmitWorkActionRequest)


def _branch(engine: LifeEngine, tmp_path, monkeypatch, label: str) -> LifeEngine:
    """已提交状态的独立副本：复制运行目录，复用同一冻结规则目录与生活活动目录打开新的引擎。"""
    target = tmp_path / "branches" / label
    shutil.copytree(engine.store.run_dir, target)
    with monkeypatch.context() as patch:
        patch.setattr(engine_module, "LifeRuleEngine", lambda scenario, root: engine.rules)
        patch.setattr(engine_module, "load_lifestyle_items", lambda root, met: engine.lifestyle_items)
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


def _request(tool: str, name: str, item: dict, recipient: str | None):
    """单独提交一项候选的请求：start_activity 按目标、动词与方式（给予交给 recipient），名称工具按目标名。"""
    if tool == "start_activity" and item["way"] == "给予":
        return start_request(targets=[name], actions=[item["action"]], ways=[item["way"]], to=[recipient])
    if tool == "start_activity":
        return start_request(targets=[name], actions=[item["action"]], ways=[item["way"]])
    if tool == "report_status":
        return ReportStatusRequest(task=[name], summary=["继续。"])
    if tool == "pause_activity":
        return PauseActivityRequest(task=[name], summary=["先停一下。"])
    if tool == "conclude_activity":
        return conclude_request(name)
    if tool == "submit_work":
        return SubmitWorkActionRequest(task_name=name, deliverable=_paraphrased_submission().deliverable)
    return SendMessageActionRequest(recipient_name=name, text="你好")


def _candidates(observed: dict) -> list[tuple[str, str, dict]]:
    """工具层每个目标的每个动作与工具、待办层每项暂停活动的工具：（工具名，目标名，可用性行）。"""
    rows = []
    for target in observed["tool_layer"]["targets"]:
        rows.extend(("start_activity", target["name"], item) for item in target["actions"])
        rows.extend((item["tool"], target["name"], item) for item in target["tools"])
    for item in observed["todo_layer"]["paused_activities"]:
        rows.extend((tool["tool"], item["name"], tool) for tool in item["tools"])
    return rows


def _classes(observed: dict) -> list[tuple[str, str, dict]]:
    """按等价类取候选：目标类别、工具、方式、可用性与原因都相同的只取第一项（同一判定路径的同一结论）。"""
    kinds = {row["name"]: row["kind"] for row in observed["tool_layer"]["targets"]}
    chosen: dict[tuple, tuple[str, str, dict]] = {}
    for tool, name, item in _candidates(observed):
        key = (kinds.get(name, "paused"), tool, item.get("way"), item["available"], item["reason"])
        chosen.setdefault(key, (tool, name, item))
    return list(chosen.values())


def _listed(observed: dict, tool: str, name: str, item: dict) -> dict:
    """在另一份观察里找到同一候选的可用性行。"""
    return next(row for candidate, target, row in _candidates(observed)
                if (candidate, target) == (tool, name) and row.get("way") == item.get("way"))


def _assert_admission_matches(engine: LifeEngine, tmp_path, monkeypatch, label: str) -> list[dict]:
    """单独提交当前观察列出的每个候选等价类，断言不可用的以同一原因被拒、可用的被受理；返回候选可用性行。"""
    observed = engine.observe(context(engine, f"{label}-listing"))
    names = [row["name"] for row in observed["tool_layer"]["targets"]]
    assert len(names) == len(set(names))
    recipient = _give_recipient(engine, observed)
    checked = []
    for index, (tool, name, item) in enumerate(_classes(observed)):
        branch = _branch(engine, tmp_path, monkeypatch, f"{label}-{index}")
        try:
            decision = context(branch, f"{label}-{index}")
            branch.begin_decision_round([decision])
            assert _listed(branch.observe(decision), tool, name, item) == item
            action_id = f"action-{label}-{index}"
            branch.submit_action(DirectToolCall(decision, action_id, tool, _request(tool, name, item, recipient)))
            if tool == "submit_work" and item["available"]:
                branch._work_reviewer = StaticReviewer()
                drive(branch.prepare_pending_work_review(action_id))
            result = branch.commit_decision_round(decision.round_id)[0]
        finally:
            branch.close()
        described = (tool, name, item.get("way"), result.error_code, result.data.get("reason"))
        if item["available"]:
            assert result.status != "rejected", described
        else:
            assert result.status == "rejected" and result.data["reason"] == item["reason"], (described, item)
        checked.append(item)
    return checked


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
