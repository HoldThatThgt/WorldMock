"""统一名称解析与名称类拒绝的同刻预算（docs/dev/SPEC-dayrun-fidelity.md 3.1、3.4，验收台账「名称解析」「名称类预算」）。

真实 LifeEngine、SQLite 与 LifeAgent 上：同一冻结观察交给宿主本地校验与环境受理，两边调用同一组解析函数
（worldmock.life.name_resolution）——精确匹配、无损归一（前缀、全半角、开始时刻、邮件时刻、「（模拟）」、「家」）唯一即受理并把
归一写进 details.resolved，方式同义只在目标列出的方式里恰好命中一个时受理，动词与方式不一致报 WAY_NOT_OFFERED 且不由方式
反推动词，找不到时只给按「此刻可行」排在前面、可以照填的候选，从不替换；宿主受理的，环境也受理（性质测试）。名称类拒绝每人
每秒前两次不计同刻轮数、不施加情绪脉冲，第三次照常；宿主与环境、质量报告用同一个名称类拒绝码闭集。
"""

from __future__ import annotations

import os
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")

from agentsociety2.agent.base import ReactDecision, ReactLoopRequest  # noqa: E402
from agentsociety2.society import DirectToolCall  # noqa: E402

from custom.agents.life_agent import LifeAgent  # noqa: E402
from custom.agents.life_feedback import feedback_report  # noqa: E402
from tests.integration.test_life_agent_protocol import (_add_objects, _observations, _offer, _prepared,  # noqa: E402
                                                        _visible_object)
from tests.life.conftest import (advance_until_ready, context, last_result, stage_day_plan,  # noqa: E402
                                 start_arguments)
from tests.life.routing_fixtures import place, stage  # noqa: E402
from worldmock.footprints.quality import build_quality_report  # noqa: E402
from worldmock.life import same_moment  # noqa: E402
from worldmock.life.error_contract import (ENVIRONMENT_NAME_CODES, HOST_ERROR_CODES, HOST_NAME_CODES,  # noqa: E402
                                           NAME_REJECTION_CODES)
from worldmock.life.guidance import GENERIC_REASON, default_reason  # noqa: E402
from worldmock.life.interaction_menu import WAYS, resolve_selection  # noqa: E402
from worldmock.life.models import REQUEST_MODELS, StartActivityRequest  # noqa: E402
from worldmock.life.name_resolution import (WAY_SYNONYMS, name_book, named_kind, resolve_name,  # noqa: E402
                                            resolve_start, way_options)
from worldmock.life.result_projection import public_result  # noqa: E402

#: 候选请求照填的键（另可带 note）。
_FILL_IN = ("target", "action", "way", "from", "to")
#: 分阶段世界的时刻：场景首日 09:30，任务已开放、第一封邮件已到。
_MORNING = datetime(2026, 1, 5, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
_TASK_ID, _MAIL_ID = "task-2026-01-05", "week-01-work-plan-1"
_MAIL_LABEL = "邮件：Work planning（2026年01月05日 08:00:00）"
_HOME_LABEL, _SHOP_LABEL = "地点：张江居所（冻结场景锚点）", "地点：好德"


def _morning(engine_factory):
    """white-collar 场景：登记空计划后把时间推到首日 09:30（人物在家），任务与第一封邮件可见，手边有一份能吃的东西。"""
    engine = engine_factory()
    stage_day_plan(engine)
    now = int(_MORNING.timestamp())
    food = next(item for item in engine.scenario.products if "eat" in item.use_modes)

    def change(state):
        """@brief 推到 09:30，开放当日任务与邮件，放一份食物在家。"""
        state.current_time = now
        state.pending_events = [event for event in state.pending_events if event.due_at > now]
        agent = state.agents[1]
        agent.body_settled_at = now
        agent.next_decision_at = now
        agent.visible_task_ids = {_TASK_ID}
        agent.visible_message_ids = {_MAIL_ID}
        agent.inventory = {"home": {food.product_id: 1.0}}
    stage(engine, change)
    return engine


def _host(ctx, observed: dict) -> LifeAgent:
    """按同一次冻结观察准备好的生活人物（宿主本地校验）。"""
    person = LifeAgent()
    person.prepare_react_loop(ReactLoopRequest(0, ctx.observed_at, [{"turn": 0, "action": "observe", "ok": True,
                                                                    "data": observed}]))
    person.build_react_tool_schemas(readonly=False)
    return person


def _open(engine, round_id: str):
    """开一个决策轮并冻结观察：（上下文，观察，宿主）。"""
    ctx = context(engine, round_id)
    engine.begin_decision_round([ctx])
    observed = engine.observe(ctx)
    return ctx, observed, _host(ctx, observed)


def _check(host: LifeAgent, tool: str, arguments: dict) -> str:
    """宿主本地校验一次工具调用（空串即受理）。"""
    return host.validate_react_decisions([ReactDecision("", tool, arguments, "")], False)


def _pending(engine, ctx, tool: str, request) -> SimpleNamespace:
    """与冻结观察对应的待结算动作（环境按它读同一次冻结观察）。"""
    return SimpleNamespace(agent_id=1, decision_id=ctx.decision_id, observed_at=engine.state.current_time,
                           action_id=f"probe-{tool}", tool_name=tool, request=request.model_dump(mode="json"))


def _decide(engine, round_id: str, tool: str, arguments: dict):
    """同一次冻结观察先经宿主本地校验，再原样登记给真实引擎并结算：（宿主纠错串，环境结果）。"""
    ctx, _, host = _open(engine, round_id)
    error = _check(host, tool, arguments)
    engine.submit_action(DirectToolCall(ctx, f"action-{round_id}", tool, REQUEST_MODELS[tool].model_validate(
        arguments)))
    [result] = engine.commit_decision_round(round_id)
    return error, result


def _start(targets, actions, ways, **fields) -> dict:
    """start_activity 参数（未写的来源、去向取缺省）。"""
    return start_arguments(list(targets), list(actions), list(ways), **fields)


def _binding(observed: dict, kind: str) -> dict:
    """本轮列出的第一个某类交互对象。"""
    return next(row for row in observed["interaction_bindings"] if row["kind"] == kind)


def _catalog(observed: dict, kind: str, doable: bool) -> dict:
    """目录绑定（本轮没有列出、可按名调用）里第一项此刻能做（或不能做）的某类对象。"""
    return next(row for row in observed["catalog_bindings"] if row["kind"] == kind and (row["reason"] is None) == doable)


def _selection(engine, ctx, arguments: dict):
    """环境对这次 start_activity 的名称解析（受理路径的同一函数）：绑定行，解析不出为 None。"""
    request = StartActivityRequest.model_validate(arguments)
    return engine._start_selection(_pending(engine, ctx, "start_activity", request), request)


def test_exact_normalised_and_candidate_paths_through_host_and_engine(engine_factory) -> None:
    """三段各一例：精确写联系人名照常受理；只写「沈昊」无损归一唯一，宿主与环境都受理，受理结果 details.resolved 记
    {written, as}；写错一个字（「沈浩」）不替换，宿主报 NAME_NOT_FOUND，候选里有可以照填的联系人请求。"""
    engine = _morning(engine_factory)
    ctx, observed, host = _open(engine, "paths")
    exact = _start(["联系人：沈昊"], ["interact"], ["通话"])
    assert _check(host, "start_activity", exact) == ""
    assert [row["object"] for row in _selection(engine, ctx, exact)] == ["联系人：沈昊"]
    normalised = _start(["沈昊"], ["interact"], ["通话"])
    assert _check(host, "start_activity", normalised) == ""
    assert [row["object"] for row in _selection(engine, ctx, normalised)] == ["联系人：沈昊"]
    outcome = resolve_start(name_book(observed), StartActivityRequest.model_validate(normalised))
    assert outcome.resolved == ({"written": "沈昊", "as": "联系人：沈昊"},)
    wrong = _check(host, "start_activity", _start(["沈浩"], ["interact"], ["通话"]))
    assert wrong.report.error_code == "NAME_NOT_FOUND" and _selection(engine, ctx, _start(
        ["沈浩"], ["interact"], ["通话"])) is None
    [item] = wrong.report.items
    assert {"target": "联系人：沈昊", "action": "interact", "way": "通话", "from": None, "to": None} in [
        {key: candidate.payload()[key] for key in _FILL_IN} for candidate in item.candidates]
    engine.submit_action(DirectToolCall(ctx, "action-paths", "start_activity",
                                        StartActivityRequest.model_validate(normalised)))
    [result] = engine.commit_decision_round("paths")
    assert (result.status, result.error_code) == ("in_progress", None)
    assert result.data["resolved"] == [{"written": "沈昊", "as": "联系人：沈昊"}]
    assert last_result(engine.observe(context(engine, "paths-seen")))["details"]["resolved"] == result.data["resolved"]


@pytest.mark.parametrize("case", ["地点", "联系人", "活动", "内容", "持有物", "工作", "邮件"])
def test_each_prefix_and_the_mail_stamp_are_added_or_stripped(engine_factory, case: str) -> None:
    """按动词与方式增补或去掉类别前缀（地点、联系人、活动、内容、持有物、工作），邮件去掉到达时刻：宿主受理、环境解析到同一
    对象，归一记一条 {written, as}；带着错的前缀（「内容：」写成「游戏：」）也按去掉前缀后的名称唯一解析。"""
    engine = _morning(engine_factory)
    ctx, observed, host = _open(engine, f"prefix-{case}")
    if case == "地点":
        row = _binding(observed, "location")
        label, verb, way = row["object"], "travel", row["actions"][0]["label"]
    elif case == "联系人":
        label, verb, way = "联系人：陈母", "interact", "通话"
    elif case == "活动":
        label, verb, way = _catalog(observed, "lifestyle", True)["object"], "interact", "进行"
    elif case == "内容":
        row = _binding(observed, "content")
        label, verb, way = row["object"], "interact", row["actions"][0]["label"]
    elif case == "持有物":
        label, verb, way = _binding(observed, "holding")["object"], "consume", "食用"
    elif case == "工作":
        label, verb, way = _binding(observed, "work_task")["object"], "interact", "工作"
    else:
        label, verb, way = _MAIL_LABEL, "interact", "阅读"
    written = "邮件：Work planning" if case == "邮件" else label.split("：", 1)[1]
    written_forms = [written, f"游戏：{written}"] if case == "内容" else [written]
    for form in written_forms:
        arguments = _start([form], [verb], [way])
        assert _check(host, "start_activity", arguments) == "", form
        assert [row["object"] for row in _selection(engine, ctx, arguments)] == [label]
        outcome = resolve_start(name_book(observed), StartActivityRequest.model_validate(arguments))
        assert outcome.resolved == ({"written": form, "as": label},)


def test_start_suffix_task_prefix_and_recall_names_resolve_for_the_other_tools(engine_factory) -> None:
    """活动名去掉开始时刻「（1月5日 09:30 起）」、全角括号写成半角都唯一对应本轮活动；任务名不写「工作：」、回看名称不写
    前缀都唯一解析；宿主受理，环境按同一解析受理并在结果 details.resolved 记下（控制工具、回看与提交成果同一口径）。"""
    engine = _morning(engine_factory)
    error, started = _decide(engine, "suffix-start", "start_activity", _start(["自己"], ["interact"], ["等待"]))
    assert (error, started.status) == ("", "in_progress")
    full = engine.state.activities[started.activity_id].name
    assert full.endswith("（1月5日 09:30 起）")
    title = full.removesuffix("（1月5日 09:30 起）")
    assert advance_until_ready(engine)
    ctx, observed, host = _open(engine, "suffix-conclude")
    task = next(row["task_name"] for row in observed["task_bindings"])

    def conclusion(written: str) -> dict:
        """@brief 只点名一项活动、不写记忆的收尾参数。"""
        return {"items": [{"activity_name": written, "summary": "等完了。", "memories": [], "views": []}]}

    for written in (title, full.replace("（", "(").replace("）", ")")):
        assert _check(host, "conclude_activity", conclusion(written)) == ""
        request = REQUEST_MODELS["conclude_activity"].model_validate(conclusion(written))
        names, _ = engine._named_activities(engine.state, _pending(engine, ctx, "conclude_activity", request),
                                            [written])
        assert names == [full]
    bare = task.removeprefix("工作：")
    assert _check(host, "recall_task_summary", {"task_name": bare}) == ""
    recall = REQUEST_MODELS["recall_task_summary"].model_validate({"task_name": bare})
    assert engine._task_binding(_pending(engine, ctx, "recall_task_summary", recall), bare)["task_name"] == task
    engine.submit_action(DirectToolCall(ctx, "action-suffix-conclude", "conclude_activity",
                                        REQUEST_MODELS["conclude_activity"].model_validate(conclusion(title))))
    [concluded] = engine.commit_decision_round("suffix-conclude")
    assert (concluded.status, concluded.data["resolved"]) == ("completed", [{"written": title, "as": full}])
    assert concluded.data["task"] == [full]
    recall_error, recalled = _decide(engine, "suffix-recall", "recall_task_summary", {"task_name": bare})
    assert (recall_error, recalled.status) == ("", "completed")
    assert recalled.data["resolved"] == [{"written": bare, "as": task}] and recalled.data["recall"]["kind"] == "task"


def test_home_words_map_to_the_home_location_and_other_spellings_only_get_candidates(engine_factory) -> None:
    """「家」「住处」指本人的住处（home_location_id）：在办公室时写「家」出行，宿主与环境都解析为住处并受理，归一记下；
    「家（张江居所）」这类其余写法不受理，候选里有住处；人在家时写「家」同样解析为住处，环境以没有路线的业务拒绝说明，
    不崩溃。"""
    engine = _morning(engine_factory)
    place(engine, 1, "office")
    ctx, observed, host = _open(engine, "home-words")
    assert observed["home_location_id"] == "home"
    for word in ("家", "住处"):
        arguments = _start([word], ["travel"], ["步行"])
        assert _check(host, "start_activity", arguments) == ""
        assert [row["object_id"] for row in _selection(engine, ctx, arguments)] == ["home"]
    other = _check(host, "start_activity", _start(["家（张江居所）"], ["travel"], ["步行"]))
    assert other.report.error_code == "NAME_NOT_FOUND"
    assert _HOME_LABEL in [candidate.target for candidate in other.report.items[0].candidates]
    engine.submit_action(DirectToolCall(ctx, "action-home-words", "start_activity",
                                        StartActivityRequest.model_validate(_start(["家"], ["travel"], ["步行"]))))
    [going] = engine.commit_decision_round("home-words")
    assert (going.status, going.data["resolved"]) == ("in_progress", [{"written": "家", "as": _HOME_LABEL}])
    engine = _morning(engine_factory)
    error, stay = _decide(engine, "home-at-home", "start_activity", _start(["家"], ["travel"], ["步行"]))
    assert error == "" and (stay.status, stay.error_code) == ("rejected", "FROZEN_ROUTE_NOT_FOUND")
    assert stay.data["reason"] != GENERIC_REASON


def test_verb_and_way_mismatch_is_way_not_offered_with_the_targets_pairs(engine_factory) -> None:
    """每个方式只属于一个动词，写的动词与方式不一致时不替人物改、不由方式反推动词：报 WAY_NOT_OFFERED，候选是该目标列出的
    （动词，方式）写成的可照填请求（至多 3 个）；绕过宿主时环境同样解析不出，以带逐项明细的 ACTION_REQUEST_INVALID 拒绝。"""
    engine = _morning(engine_factory)
    ctx, observed, host = _open(engine, "mismatch")
    call = _check(host, "start_activity", _start(["联系人：陈母"], ["通话"], ["通话"]))
    [item] = call.report.items
    assert (call.report.error_code, item.field, item.written) == ("WAY_NOT_OFFERED", "actions", "通话")
    assert [{key: candidate.payload()[key] for key in _FILL_IN} for candidate in item.candidates] == [
        {"target": "联系人：陈母", "action": "interact", "way": "通话", "from": None, "to": None}]
    shop = next(row for row in observed["interaction_bindings"] if row["object"] == _SHOP_LABEL)
    drive = _check(host, "start_activity", _start(["好德"], ["驾车前往"], ["驾车"]))
    assert drive.report.error_code == "WAY_NOT_OFFERED"
    assert [(candidate.target, candidate.action, candidate.way) for candidate in drive.report.items[0].candidates] == [
        (_SHOP_LABEL, action["verb"], action["label"]) for action in shop["actions"]][:3]
    assert drive.report.reason == f"「{_SHOP_LABEL}」没有列出「驾车前往：驾车」这种做法"
    assert _selection(engine, ctx, _start(["好德"], ["驾车前往"], ["驾车"])) is None
    engine.submit_action(DirectToolCall(ctx, "action-mismatch", "start_activity",
                                        StartActivityRequest.model_validate(_start(["联系人：陈母"], ["通话"], ["通话"]))))
    [refused] = engine.commit_decision_round("mismatch")
    assert (refused.status, refused.error_code) == ("rejected", "ACTION_REQUEST_INVALID")
    assert [row["error_code"] for row in refused.data["items"]] == ["WAY_NOT_OFFERED"]


def test_way_synonym_is_accepted_only_on_a_unique_hit(engine_factory) -> None:
    """方式同义闭集表：「走路」「开车」「打电话」「看」只在目标列出的方式里恰好命中一个时受理，动词仍须相配，受理结果记下
    方式的归一；表外的写法（「溜达过去」）不受理。"""
    engine = _morning(engine_factory)
    ctx, observed, host = _open(engine, "synonym")
    content = _binding(observed, "content")
    cases = [(_SHOP_LABEL, "travel", "走路", "步行"), (_SHOP_LABEL, "travel", "开车", "驾车"),
             ("联系人：陈母", "interact", "打电话", "通话"),
             (content["object"], "interact", "看", content["actions"][0]["label"])]
    for target, verb, written, way in cases:
        arguments = _start([target], [verb], [written])
        assert _check(host, "start_activity", arguments) == "", written
        [row] = _selection(engine, ctx, arguments)
        assert row["selected_action"]["label"] == way
    assert _check(host, "start_activity", _start([_SHOP_LABEL], ["travel"], ["溜达过去"])).report.error_code == (
        "WAY_NOT_OFFERED")
    assert _check(host, "start_activity", _start([_SHOP_LABEL], ["interact"], ["走路"])).report.error_code == (
        "WAY_NOT_OFFERED")
    engine.submit_action(DirectToolCall(ctx, "action-synonym", "start_activity",
                                        StartActivityRequest.model_validate(_start([_SHOP_LABEL], ["travel"],
                                                                                   ["走路"]))))
    [walking] = engine.commit_decision_round("synonym")
    assert (walking.status, walking.data["resolved"]) == ("in_progress", [{"written": "走路", "as": "步行"}])
    assert engine.state.activities[walking.activity_id].travel_mode == "walking"


def test_a_synonym_hitting_two_listed_ways_is_not_accepted() -> None:
    """同义写法在目标列出的方式里命中两个（一个对象同时列出「接着看」与「从头再看」时写「看」）不受理，宿主报
    WAY_NOT_OFFERED、环境解析不出；只命中一个的「继续看」受理；同义表的每个写法只指方式闭集中的方式。"""
    observed = _observations()
    _add_objects(observed[0]["data"], _visible_object("内容：纪录片", "content", "content-doc",
                                                      _offer("interact", "接着看", "watch"),
                                                      _offer("interact", "从头再看", "watch")))
    host = _prepared(observed)
    bindings = observed[0]["data"]["interaction_bindings"]
    both = _check(host, "start_activity", _start(["内容：纪录片"], ["interact"], ["看"]))
    assert both.report.error_code == "WAY_NOT_OFFERED"
    assert resolve_selection(bindings, ["内容：纪录片"], ["interact"], ["看"]) is None
    assert _check(host, "start_activity", _start(["内容：纪录片"], ["interact"], ["继续看"])) == ""
    [row] = resolve_selection(bindings, ["纪录片"], ["interact"], ["继续看"])
    assert row["selected_action"]["label"] == "接着看"
    assert all(options and options <= set(WAYS) for options in WAY_SYNONYMS.values())
    assert way_options("看") == {"观看", "接着看", "从头再看"} and way_options("溜达过去") == frozenset()
    assert named_kind("obtain", "外卖") == "supply" and named_kind("interact", "外卖") is None


def test_candidates_are_feasible_first_and_submittable_verbatim(engine_factory) -> None:
    """找不到的名称只给候选、不替换：跨类别按相似度排名，此刻可行的在前（「天文观测活动」时能做的「天文摄影」排在更像却
    这里做不了的「天文观测」之前），至多 3 个；候选逐字段原样提交，宿主不纠错、环境受理并开始这项活动。"""
    engine = _morning(engine_factory)
    ctx, observed, host = _open(engine, "candidates")
    doable = {row["object"]: all(action["note"] is None for action in row["actions"])
              for row in observed["interaction_bindings"]}
    doable.update({row["object"]: row["reason"] is None for row in observed["catalog_bindings"]})
    for written in ("天文观测活动", "收拾碗筷与厨房"):
        error = _check(host, "start_activity", _start([written], ["interact"], ["进行"]))
        [item] = error.report.items
        flags = [doable[candidate.target] for candidate in item.candidates]
        assert error.report.error_code == "NAME_NOT_FOUND" and 1 <= len(flags) <= 3
        assert flags == sorted(flags, reverse=True) and flags[0], (written, item.candidates)
    first = _check(host, "start_activity", _start(["天文观测活动"], ["interact"], ["进行"])).report.items[0]
    assert first.candidates[0].target == "活动：天文摄影"
    candidate = _check(host, "start_activity", _start(["收拾碗筷与厨房"], ["interact"], ["进行"])).report.items[0]
    chosen = candidate.candidates[0].payload()
    arguments = {"targets": [chosen["target"]], "actions": [chosen["action"]], "ways": [chosen["way"]],
                 "from": [chosen["from"]], "to": [chosen["to"]], "answering": None}
    assert _check(host, "start_activity", arguments) == ""
    engine.submit_action(DirectToolCall(ctx, "action-candidates", "start_activity",
                                        StartActivityRequest.model_validate(arguments)))
    [result] = engine.commit_decision_round("candidates")
    assert (result.status, result.error_code) == ("in_progress", None)
    assert chosen["target"].split("：", 1)[1] in engine.state.activities[result.activity_id].name


def _variants(label: str) -> list[str]:
    """一个名称的写法变体：原样、去掉类别前缀、全角括号写成半角、插入空白、多写「（模拟）」。"""
    forms = [label, label.replace("（", "(").replace("）", ")"), f" {label} ", f"{label}（模拟）"]
    if "：" in label:
        forms.append(label.split("：", 1)[1])
    return forms


def test_host_acceptance_implies_environment_acceptance(engine_factory) -> None:
    """性质测试：同一冻结观察下，本轮列出的对象、目录绑定、冻结地点表与「家」的各种写法（含方式同义），宿主判为可受理的，
    环境受理路径的名称解析也受理且解析到同一对象；宿主以名称类错误纠正的，环境同样解析不出（名称不存在的指名购买除外，
    它们由环境按名称解析商品）；回看与提交成果的任务名同样一致。"""
    engine = _morning(engine_factory)
    ctx, observed, host = _open(engine, "property")
    targets = [(row["object"], action["verb"], action["label"]) for row in observed["interaction_bindings"]
               for action in row["actions"][:1]]
    targets += [(row["object"], "interact", row["way"]) for row in observed["catalog_bindings"][::7]]
    targets += [(label, "travel", "步行") for label in ("家", _HOME_LABEL, "不存在的地点")]
    targets += [("不存在的对象", "interact", "等待"), ("收拾碗筷与厨房", "interact", "收拾")]
    synonyms = {way: [written for written, options in WAY_SYNONYMS.items() if options == {way}] for way in
                {way for _, _, way in targets}}
    checked = 0
    for label, verb, way in targets:
        for written in _variants(label):
            for form in [way, *synonyms.get(way, [])[:1]]:
                arguments = _start([written], [verb], [form])
                verdict = _check(host, "start_activity", arguments)
                rows = _selection(engine, ctx, arguments)
                if verdict == "":
                    host_rows = resolve_start(name_book(observed), StartActivityRequest.model_validate(arguments)).rows
                    assert rows is not None, (written, verb, form)
                    assert [row["object_id"] for row in rows] == [row["object_id"] for row in host_rows]
                elif verdict.report.error_code in HOST_NAME_CODES and "interaction menu" not in verdict:
                    assert rows is None, (written, verb, form, verdict)
                checked += 1
    assert checked > 500
    for task in (row["task_name"] for row in observed["task_bindings"]):
        for written in _variants(task):
            accepted = _check(host, "recall_task_summary", {"task_name": written}) == ""
            request = REQUEST_MODELS["recall_task_summary"].model_validate({"task_name": written})
            if accepted:
                result = engine._recall_task_summary(engine.state, _pending(engine, ctx, "recall_task_summary",
                                                                            request), request)[1]
                assert result.status == "completed" and result.data["recall"]["kind"] == "task"


def test_every_name_failure_of_a_two_item_request_is_reported(engine_factory) -> None:
    """两项都有名称类问题（一项找不到、一项方式不相配）：宿主逐项报全部（items 的 index 0 与 1，码各自写），绕过宿主时环境
    逐项解析后合并成一次拒绝，两项都在 items 里；名称类以外的条件仍只报第一条。"""
    engine = _morning(engine_factory)
    arguments = _start(["不存在的对象", _SHOP_LABEL], ["interact", "travel"], ["等待", "飞行"])
    error, result = _decide(engine, "two-items", "start_activity", arguments)
    assert [(item.index, item.error_code) for item in error.report.items] == [(0, "NAME_NOT_FOUND"),
                                                                             (1, "WAY_NOT_OFFERED")]
    assert error.report.reason.startswith("第 1 项「不存在的对象」不是可以操作的对象；第 2 项")
    assert (result.status, result.error_code) == ("rejected", "ACTION_REQUEST_INVALID")
    assert [(row["index"], row["error_code"]) for row in result.data["items"]] == [(0, "NAME_NOT_FOUND"),
                                                                                  (1, "WAY_NOT_OFFERED")]
    assert result.message == result.data["reason"] == error.report.reason


def test_unlisted_catalog_objects_are_callable_by_name_with_the_usual_checks(engine_factory) -> None:
    """展示不是资格上限：本轮没有列出的生活活动与内容写名称即可调用（宿主放行、环境受理并开始）；地点类不符的生活活动照常
    以 ELIGIBILITY_LOCATION_KIND_MISMATCH 拒绝并说明原因，不崩溃。"""
    engine = _morning(engine_factory)
    observed = engine.observe(context(engine, "catalog-peek"))
    doable = _catalog(observed, "lifestyle", True)
    blocked = next(row for row in observed["catalog_bindings"] if row["kind"] == "lifestyle"
                   and row["reason"] == default_reason("ELIGIBILITY_LOCATION_KIND_MISMATCH"))
    content = next(row for row in observed["catalog_bindings"] if row["kind"] == "content")
    error, started = _decide(engine, "catalog-do", "start_activity", _start([doable["object"]], ["interact"], ["进行"]))
    assert (error, started.status) == ("", "in_progress")
    assert engine.state.activities[started.activity_id].target_id == doable["object_id"]
    engine = _morning(engine_factory)
    error, refused = _decide(engine, "catalog-blocked", "start_activity", _start([blocked["object"]], ["interact"],
                                                                                 ["进行"]))
    assert (error, refused.status, refused.error_code) == ("", "rejected", "ELIGIBILITY_LOCATION_KIND_MISMATCH")
    assert refused.data["reason"] == blocked["reason"] == default_reason("ELIGIBILITY_LOCATION_KIND_MISMATCH")
    engine = _morning(engine_factory)
    error, watched = _decide(engine, "catalog-watch", "start_activity", _start([content["object"]], ["interact"],
                                                                               [content["way"]]))
    assert (error, watched.status) == ("", "in_progress")
    assert engine.state.activities[watched.activity_id].target_id == content["object_id"]


def test_redundant_from_and_to_are_accepted_with_notes(engine_factory) -> None:
    """与当前所在地点一致的 from、与目标一致的 to 是冗余的：照样受理，details.notes 逐句说明可以写 null，名称的归一记在
    details.resolved（公开结果原样投影；在原地开始的活动下一次观察的进度层可见）；写成别的地方仍按组合不合法纠正。"""
    engine = _morning(engine_factory)
    here = _HOME_LABEL.split("：", 1)[1]
    arguments = _start(["好德"], ["travel"], ["步行"], **{"from": [here], "to": [_SHOP_LABEL]})
    error, result = _decide(engine, "redundant", "start_activity", arguments)
    assert (error, result.status) == ("", "in_progress")
    assert result.data["notes"] == [f"from「{here}」就是现在所在的地点，可以写 null",
                                    f"to「{_SHOP_LABEL}」就是目标本身，可以写 null"]
    assert result.data["resolved"] == [{"written": "好德", "as": _SHOP_LABEL}]
    shown = public_result(result.model_dump(mode="json"), engine.scenario)["details"]
    assert (shown["notes"], shown["resolved"]) == (result.data["notes"], result.data["resolved"])
    engine = _morning(engine_factory)
    ctx, observed, host = _open(engine, "redundant-here")
    doable = _catalog(observed, "lifestyle", True)["object"]
    elsewhere = _check(host, "start_activity", _start([doable], ["interact"], ["进行"], **{"from": ["好德"]}))
    assert elsewhere.report.error_code == "ROUTE_FIELDS_INVALID"
    in_place = _start([doable], ["interact"], ["进行"], **{"from": [_HOME_LABEL]})
    assert _check(host, "start_activity", in_place) == ""
    engine.submit_action(DirectToolCall(ctx, "action-redundant-here", "start_activity",
                                        StartActivityRequest.model_validate(in_place)))
    [started] = engine.commit_decision_round("redundant-here")
    assert (started.status, started.data["notes"]) == (
        "in_progress", [f"from「{_HOME_LABEL}」就是现在所在的地点，可以写 null"])
    assert last_result(engine.observe(context(engine, "redundant-seen")))["details"]["notes"] == started.data["notes"]


def _unknown_purchase(index: int) -> dict:
    """远程下单一件不存在的商品（环境按指名购买解析，SUPPLY_NOT_FOUND 是名称类拒绝码）。"""
    return _start([f"不存在的商品{index}号"], ["obtain"], ["远程下单"])


def test_two_name_rejections_in_one_second_are_free_and_the_third_counts(engine_factory) -> None:
    """同一秒两次名称类拒绝不计同刻轮数、不施加 service_refused 情绪脉冲，人物同一秒再决策；第三次照常计轮、照常施加脉冲；
    质量报告 name_rejections 与之同口径（环境三次，其中两次免计）。"""
    engine = _morning(engine_factory)
    now = engine.state.current_time
    mood = engine.state.agents[1].body.mood_valence
    rounds = []
    for index in range(3):
        error, result = _decide(engine, f"budget-{index}", "start_activity", _unknown_purchase(index))
        agent = engine.state.agents[1]
        assert error == "" and (result.status, result.error_code) == ("rejected", "SUPPLY_NOT_FOUND")
        rounds.append((agent.same_moment_rounds, result.data["same_moment_round"], agent.body.mood_valence))
        assert agent.next_decision_at == now and engine.state.current_time == now
    assert rounds[0] == rounds[1] == (0, 0, mood)
    assert rounds[2][:2] == (1, 1) and rounds[2][2] < mood
    assert same_moment.latest_rejection_code(engine.state, 1) == "SUPPLY_NOT_FOUND"
    events = engine.store.read_events(engine.store.last_event_seq)
    report = build_quality_report(events, [], engine.store.manifest)["name_rejections"]
    assert (report["environment_rejections"], report["environment_free_rejections"]) == (3, 2)
    assert report["environment_codes"]["SUPPLY_NOT_FOUND"] == 3


def test_a_name_rejection_beside_other_rejections_follows_the_same_count(engine_factory) -> None:
    """名称类以外的拒绝照常计轮并施加脉冲；免计只看本秒此前的名称类拒绝次数，最近计入的拒绝码不含免计的名称类拒绝。"""
    engine = _morning(engine_factory)
    error, first = _decide(engine, "mixed-0", "start_activity", _start(["联系人：陈母"], ["通话"], ["通话"]))
    assert error.report.error_code == "WAY_NOT_OFFERED" and first.error_code == "ACTION_REQUEST_INVALID"
    assert engine.state.agents[1].same_moment_rounds == 1
    _, second = _decide(engine, "mixed-1", "start_activity", _unknown_purchase(1))
    assert second.error_code == "SUPPLY_NOT_FOUND" and engine.state.agents[1].same_moment_rounds == 1
    assert same_moment.latest_rejection_code(engine.state, 1) == "ACTION_REQUEST_INVALID"
    assert same_moment.free_rejection_flags(["ACTION_REQUEST_INVALID", "SUPPLY_NOT_FOUND", "TASK_NOT_FOUND",
                                             "SOURCE_NOT_FOUND"]) == [False, True, True, False]


def test_host_and_environment_share_one_name_class_closed_set() -> None:
    """名称类拒绝码闭集是规格 3.4 的十一个码：宿主五个都在宿主错误码闭集里，环境六个都有具体的默认原因且不是宿主码；每个码
    在同刻规则里本秒前两次免计；宿主写进证据的名称类纠错串经 feedback_report 判定为同一个码。"""
    assert HOST_NAME_CODES == {"NAME_NOT_FOUND", "NAME_AMBIGUOUS", "WAY_NOT_OFFERED", "SUPPLY_SOURCE_MISMATCH",
                               "SUBJECT_NOT_KNOWN"}
    assert ENVIRONMENT_NAME_CODES == {"SUPPLY_NOT_FOUND", "SUPPLY_NOT_AT_STORE", "SUPPLY_NAME_AMBIGUOUS",
                                      "FULFILLMENT_NOT_SUPPORTED", "SOURCE_NOT_FOUND", "TASK_NOT_FOUND"}
    assert NAME_REJECTION_CODES == HOST_NAME_CODES | ENVIRONMENT_NAME_CODES and len(NAME_REJECTION_CODES) == 11
    assert HOST_NAME_CODES <= set(HOST_ERROR_CODES) and not ENVIRONMENT_NAME_CODES & set(HOST_ERROR_CODES)
    for code in NAME_REJECTION_CODES:
        assert same_moment.free_rejection_flags([code] * 3) == [True, True, False]
        if code in HOST_NAME_CODES:
            assert feedback_report(f"{code}: start_activity target is not visible").error_code == code
    for code in ENVIRONMENT_NAME_CODES - {"SUPPLY_NOT_AT_STORE", "SOURCE_NOT_FOUND"}:
        assert default_reason(code) != GENERIC_REASON


def test_resolve_name_accepts_only_a_unique_lossless_match() -> None:
    """其余名称参数（活动名、任务名、收件人）：精确、归一与去前缀逐级比对，第一级有匹配就停，唯一才受理；两个活动去掉开始
    时刻后同名时有歧义，不猜。"""
    names = ["等待（1月5日 09:30 起）", "等待（1月5日 10:00 起）", "工作：整理纪要", "联系人：沈昊"]
    assert resolve_name("等待（1月5日 09:30 起）", names).name == "等待（1月5日 09:30 起）"
    assert resolve_name("等待(1月5日09:30起)", names).name == "等待（1月5日 09:30 起）"
    ambiguous = resolve_name("等待", names)
    assert ambiguous.name is None and set(ambiguous.matches) == set(names[:2])
    assert resolve_name("整理纪要", names).name == "工作：整理纪要"
    assert resolve_name("沈昊", names).name == "联系人：沈昊"
    assert (resolve_name("沈浩", names).name, resolve_name("沈浩", names).matches) == (None, ())
