"""控制工具活动名在环境一侧的名称类拒绝（docs/dev/SPEC-dayrun-fidelity.md 3.1 第 5 条、第四章「错误合同」，验收台账
「名称解析」「错误合同」）。

真实 LifeEngine 与 SQLite 上，播报、暂停与结束的请求原样登记给环境（宿主本来会在本轮纠正这些名称，环境这里只防绕过宿主的
请求）：环境逐项按统一名称解析（worldmock.life.name_resolution.resolve_name，与宿主本地校验同一函数）解析活动名，解析不出的
各项合并成一次拒绝，完整参数路径、原值、码、合法值与候选和宿主对同一请求的纠错相同，双方解释准确表达同一名称事实；
不在第一个解析不出的名称处停下；顶层码与 start_activity 的环境拒绝一样是
ACTION_REQUEST_INVALID，不在名称类拒绝码闭集中，照常计入同刻轮数；同一请求里能解析的项不被执行（整组原子）；受限轮复核
同样逐项报；名称都解析出来而对应的不是本人的活动时不是名称类失败，只报这一条。
"""

from __future__ import annotations

import pytest

from tests.life.conftest import advance_until_ready, context, last_result, wake_by_mail
from tests.life.test_name_resolution import _decide, _morning, _pending, _start
from worldmock.errors import BusinessRuleError
from worldmock.life.guidance import refusal_for_code, rejection_guidance
from worldmock.life.models import REQUEST_MODELS, SameMomentStage

#: 三个控制工具（播报、暂停、结束），都经同一个活动名解析。
_CONTROL_TOOLS = ("report_status", "pause_activity", "conclude_activity")
#: 找不到的活动名的准确修正说明。
_NOT_FOUND_FIX = "Use an exact known activity name."


def _control(tool: str, *names: str) -> dict:
    """控制工具的参数：一项一个对象，结束另带空的 memories 与 views；请求级 errands 为空列表。"""
    extra = {"memories": [], "views": []} if tool == "conclude_activity" else {}
    return {"items": [{"activity_name": name, "summary": "先这样。", **extra} for name in names], "errands": []}


def _assert_shared_items(error, records: list[dict]) -> None:
    """宿主和环境实质判断一致，各自英文解释准确表达同一事实。"""
    host = [item.payload() for item in error.report.items]
    fields = {"path", "written", "error_code", "allowed_values", "candidates"}
    assert [{key: row[key] for key in fields} for row in host] == [
        {key: row[key] for key in fields} for row in records]
    for local, remote in zip(host, records, strict=True):
        assert set(local) == set(remote) == fields | {"expected", "why"}
        ambiguous = local["error_code"] == "NAME_AMBIGUOUS"
        assert local["expected"] == "One exact known name is required."
        assert local["why"] == (f"The name {local['written']} is ambiguous." if ambiguous
                                else f"The name {local['written']} is not known.")
        assert remote["expected"] == ("An unambiguous known activity name is required." if ambiguous
                                       else "A known activity name is required.")
        assert remote["why"] == ("The activity name matches several activities." if ambiguous
                                  else "The activity name is not known.")


def _waiting(engine_factory):
    """首日 09:30 开始一段等待，以一封邮件把下一次决策机会钉在一分钟后（等待仍在进行）：（引擎，活动身份，活动名）。"""
    engine = _morning(engine_factory)
    error, started = _decide(engine, "wait-start", "start_activity", _start(["自己"], ["interact"], ["等待"]))
    assert (error, started.status) == ("", "in_progress")
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine)
    return engine, started.activity_id, engine.state.activities[started.activity_id].name


@pytest.mark.parametrize("tool", _CONTROL_TOOLS)
def test_two_unknown_activity_names_are_both_reported_in_one_rejection(engine_factory, tool: str) -> None:
    """两项都不是本轮活动的名称：一次拒绝，两项完整路径、原文、NAME_NOT_FOUND 和候选与宿主逐项相同；英文解释各自准确。
    顶层码 ACTION_REQUEST_INVALID
    照常计入同刻轮数、照常施加情绪脉冲；action_rejected 与下一次观察的公开结果带同样的原因、改法与 items，下一步取该码的
    默认做法。"""
    engine = _morning(engine_factory)
    mood = engine.state.agents[1].body.mood_valence
    error, result = _decide(engine, f"two-{tool}", tool, _control(tool, "散步", "跑步"))
    assert (result.status, result.error_code) == ("rejected", "ACTION_REQUEST_INVALID")
    assert [(row["path"][5], row["path"][-1], row["written"], row["error_code"], row["candidates"])
            for row in result.data["items"]] == [(0, "activity_name", "散步", "NAME_NOT_FOUND", []),
                                                 (1, "activity_name", "跑步", "NAME_NOT_FOUND", [])]
    _assert_shared_items(error, result.data["items"])
    assert result.data["reason"] == "The activity name is not known.; The activity name is not known."
    assert result.data["fix"] == _NOT_FOUND_FIX
    assert result.message == result.data["reason"]
    assert (engine.state.agents[1].same_moment_rounds, result.data["same_moment_round"]) == (1, 1)
    assert engine.state.agents[1].body.mood_valence < mood
    [rejected] = [event for event in engine.store.read_agent_events("action_rejected", 1)
                  if event.action_id == f"action-two-{tool}"]
    assert (rejected.data["error_code"], rejected.data["fix"], rejected.data["items"]) == (
        "ACTION_REQUEST_INVALID", result.data["fix"], result.data["items"])
    details = last_result(engine.observe(context(engine, f"two-{tool}-seen")))["details"]
    default = refusal_for_code("ACTION_REQUEST_INVALID")
    assert details == {"reason": result.data["reason"], "next_step": default.next_step, "fix": result.data["fix"],
                       "items": result.data["items"]}


@pytest.mark.parametrize("tool", _CONTROL_TOOLS)
def test_a_known_and_an_unknown_name_report_only_the_unknown_second_item(engine_factory, tool: str) -> None:
    """正在进行的等待和未知名称同批提交时，只有未知名称写进 items，路径保留原下标并列出最接近的活动名。
    与宿主的实质判断相同；整组原子，等待没有被播报、暂停或结束；同一秒只写等待再提交就受理。"""
    engine, activity_id, name = _waiting(engine_factory)
    before = engine.state.activities[activity_id]
    error, result = _decide(engine, f"mixed-{tool}", tool, _control(tool, name, "等待一会儿"))
    assert (result.status, result.error_code) == ("rejected", "ACTION_REQUEST_INVALID")
    assert [(row["path"][5], row["path"][-1], row["written"], row["error_code"]) for row in result.data["items"]] == [
        (1, "activity_name", "等待一会儿", "NAME_NOT_FOUND")]
    _assert_shared_items(error, result.data["items"])
    assert result.data["reason"] == f"The activity name is not known. Nearby known names: 「{name}」."
    assert result.data["fix"] == _NOT_FOUND_FIX
    assert engine.state.activities[activity_id] == before
    error, accepted = _decide(engine, f"known-{tool}", tool, _control(tool, name))
    assert (error, accepted.status) == ("", "completed")
    assert engine.state.activities[activity_id].progress_note == "先这样。"


def test_an_ambiguous_and_an_unknown_name_are_reported_with_their_own_codes(engine_factory) -> None:
    """9:30 那段等待已收尾、9:31 又开始一段等待时只写「等待」：去掉开始时刻后对应两项活动，明细码 NAME_AMBIGUOUS、原因列出
    两个完整活动名；同一请求里另一项找不到的名称写 NAME_NOT_FOUND；合并成一次拒绝，码各自写、改法每码一句。
    宿主与环境的实质判断相同，各自解释准确。"""
    engine, _, first = _waiting(engine_factory)
    assert _decide(engine, "wait-end", "conclude_activity",
                   _control("conclude_activity", first))[1].status == "completed"
    error, again = _decide(engine, "wait-again", "start_activity", _start(["自己"], ["interact"], ["等待"]))
    assert (error, again.status) == ("", "in_progress")
    second = engine.state.activities[again.activity_id].name
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine)
    error, result = _decide(engine, "ambiguous", "report_status", _control("report_status", "等待", "散步"))
    assert result.error_code == "ACTION_REQUEST_INVALID"
    assert [(row["path"][5], row["written"], row["error_code"]) for row in result.data["items"]] == [
        (0, "等待", "NAME_AMBIGUOUS"), (1, "散步", "NAME_NOT_FOUND")]
    _assert_shared_items(error, result.data["items"])
    assert result.data["reason"] == (f"The name identifies several activities: 「{first}」、「{second}」.; "
                                     "The activity name is not known.")
    assert result.data["fix"] == f"Use an unambiguous complete activity name.; {_NOT_FOUND_FIX}"


def test_the_restricted_round_recheck_reports_every_unknown_name_too(engine_factory) -> None:
    """同刻上限内单项的未知活动名各计一轮，随后进入受限轮；受限轮
    复核按点名的活动判定请求类时同样逐项报全部名称类失败。"""
    engine = _morning(engine_factory)
    for index in range(engine.scenario.max_same_moment_decisions):
        _, single = _decide(engine, f"single-{index}", "pause_activity", _control("pause_activity", "散步"))
        assert (single.error_code, single.data["same_moment_round"]) == ("ACTION_REQUEST_INVALID", index + 1)
    assert (single.data["reason"], single.data["fix"]) == ("The activity name is not known.", _NOT_FOUND_FIX)
    assert engine.state.agents[1].same_moment_stage == SameMomentStage.START_OR_RESUME
    _, result = _decide(engine, "restricted", "conclude_activity", _control("conclude_activity", "散步", "跑步"))
    assert result.error_code == "ACTION_REQUEST_INVALID"
    assert [(row["path"][5], row["error_code"]) for row in result.data["items"]] == [(0, "NAME_NOT_FOUND"),
                                                                                  (1, "NAME_NOT_FOUND")]


def test_a_resolved_name_that_is_not_the_persons_activity_is_refused_without_items(engine_factory) -> None:
    """名称唯一解析、对应的活动却不是本人的（观察与世界对不上）不是名称类失败：以不带明细的 ACTION_REQUEST_INVALID 拒绝；
    同一请求里另有解析不出的名称时名称类失败在前，逐项写进 items（其余条件报第一条）；世界与观察一致时照常解析。"""
    engine, activity_id, name = _waiting(engine_factory)
    ctx = context(engine, "foreign")
    engine.begin_decision_round([ctx])
    engine.observe(ctx)
    request = REQUEST_MODELS["report_status"].model_validate(_control("report_status", name))
    pending = _pending(engine, ctx, "report_status", request)
    foreign = engine.state.model_copy(deep=True)
    foreign.activities[activity_id] = foreign.activities[activity_id].model_copy(update={"agent_id": 2})
    with pytest.raises(BusinessRuleError, match="ACTION_REQUEST_INVALID") as bare:
        engine._named_activities(foreign, pending, [name])
    assert "items" not in rejection_guidance(bare.value)
    with pytest.raises(BusinessRuleError, match="ACTION_REQUEST_INVALID") as named:
        engine._named_activities(foreign, pending, [name, "散步"])
    assert [(row["path"][5], row["written"]) for row in rejection_guidance(named.value)["items"]] == [(1, "散步")]
    assert engine._named_activities(engine.state, pending, [name]) == ([name], [engine.state.activities[activity_id]])
