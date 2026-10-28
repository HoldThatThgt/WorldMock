"""快做完的活动只能结束不能暂停：经真实请求、引擎、存储与宿主，人物看到的说明与提交后的结果一致。"""

from __future__ import annotations

import json

import pytest

from tests.integration.runtime.current_contract_fixtures import ToolResponse, decide
from tests.life.conftest import (
    advance_until_ready,
    conclude_request,
    context,
    do_at,
    last_result,
    pause_request,
    purchase,
    sleep,
    stage_day_plan,
    target_actions,
    target_tools,
    travel,
    use_app,
)
from tests.life.environment_inputs import catalog_rows, copy_environment, write_catalog_rows
from tests.life.environment_runtime import act, events, finish_activity
from worldmock.simulation_engine.activities.lifecycle import activity_control

#: 夹具里绑定到家的「测试休息」时长 900 秒。
_REST_SECONDS = 900
#: 夹具里 Keep 唯一的那条内容「测试慢跑」时长 1200 秒。
_RUN_SECONDS = 1200
#: 睡眠约束的拒绝原因：槽位 2 被占时去睡觉。
_SLEEP_REASON = "Sleep requires the second activity slot to be empty."


def _decides_after(seconds: int):
    """让人物在开始一件事之后恰好这么多秒得到一次决策机会（重估间隔）。"""
    def mutate(persona, config):
        """只改重估间隔。"""
        config["reconsideration_interval_seconds"] = seconds
    return mutate


def _reason(name: str) -> str:
    """不足三分钟时暂停的拒绝原因，也是工具层 pause_activity 一行的说明。"""
    return f"The activity {name} ends in under three minutes and cannot be paused."


def _resting(engine_factory, left: int):
    """在家开始「测试休息」并推进到它离计划结束恰好还剩 left 秒的那次决策机会；返回引擎、活动身份与活动名。"""
    engine = engine_factory(mutate=_decides_after(_REST_SECONDS - left))
    stage_day_plan(engine, submitted=True)
    start = engine.context.state.current_time
    _, rest = act(engine, "rest", do_at("家", "测试休息"))
    assert rest.status == "in_progress", rest.error_code
    assert advance_until_ready(engine)
    activity = engine.context.state.activities[rest.activity_id]
    assert engine.context.state.current_time == start + _REST_SECONDS - left
    assert activity.status == "active" and activity.planned_end_at - engine.context.state.current_time == left
    return engine, rest.activity_id, activity.name


def _paused_events(engine) -> list:
    """已提交的暂停事件。"""
    return [row for row in events(engine, "environment_event") if row.data.get("kind") == "activity_paused"]


def test_pause_with_179_seconds_left_is_refused_changes_nothing_and_conclude_succeeds(engine_factory):
    """离计划结束还剩 179 秒：工具层 pause_activity 一行不可用并带原因，提交后以同一句原因拒绝，世界与事件都不变；
    人物同一刻再决策，用 conclude_activity 结束成立。"""
    engine, activity_id, name = _resting(engine_factory, 179)
    before = engine.context.state.activities[activity_id].model_dump(mode="json")
    version = engine.context.state.version
    observed, refused = act(engine, "pause", pause_request(name), "pause_activity")
    tools = target_tools(observed, name)
    assert tools["pause_activity"] == {"tool": "pause_activity", "available": False, "reason": _reason(name)}
    assert tools["conclude_activity"] == {"tool": "conclude_activity", "available": True, "reason": None}
    assert tools["report_status"] == {"tool": "report_status", "available": True, "reason": None}
    assert refused.status == "rejected" and refused.error_code == "ACTIVITY_PAUSE_UNAVAILABLE"
    assert refused.message == refused.data["reason"] == _reason(name)
    assert refused.data["next_step"] == f"Conclude {name} or let it finish."
    assert refused.data["fix"] == "Conclude this activity or let it finish instead."
    after = engine.context.state
    assert after.activities[activity_id].model_dump(mode="json") == before and after.version == version + 1
    assert not _paused_events(engine) and engine.scheduling_state().ready_agent_ids == (1,)

    seen, concluded = act(engine, "conclude", conclude_request(name), "conclude_activity")
    assert seen["todo_layer"]["paused_activities"] == []
    assert target_tools(seen, name)["pause_activity"]["reason"] == _reason(name)
    shown = last_result(seen)
    assert shown["status"] == "rejected" and shown["error_code"] == "ACTIVITY_PAUSE_UNAVAILABLE"
    assert shown["details"]["reason"] == _reason(name)
    assert shown["details"]["next_step"] == f"Conclude {name} or let it finish."
    assert concluded.status == "completed", concluded.error_code
    ended = engine.context.state.activities[activity_id]
    assert ended.status == "cancelled" and ended.concluded_at == ended.ended_at == engine.context.state.current_time
    assert ended.elapsed_seconds == _REST_SECONDS - 179


def test_pause_with_exactly_180_seconds_left_succeeds(engine_factory):
    """恰好还剩 180 秒：工具层 pause_activity 一行可用，暂停成立，活动进入待办层，槽位 1 让出。"""
    engine, activity_id, name = _resting(engine_factory, 180)
    observed, paused = act(engine, "pause", pause_request(name), "pause_activity")
    assert target_tools(observed, name)["pause_activity"] == {"tool": "pause_activity", "available": True,
                                                              "reason": None}
    assert paused.status == "completed", paused.error_code
    activity = engine.context.state.activities[activity_id]
    assert activity.status == "paused" and activity.planned_duration_seconds - activity.elapsed_seconds == 180
    assert [row.activity_id for row in _paused_events(engine)] == [activity_id]
    after, other = act(engine, "other", do_at("家", "测试闲聊"))
    assert [row["name"] for row in after["todo_layer"]["paused_activities"]] == [name]
    assert other.status == "in_progress", other.error_code


@pytest.mark.parametrize("left,step", [(179, "Conclude"), (180, "Conclude or pause")])
def test_slot_conflict_offers_pausing_only_while_the_running_activity_can_be_paused(engine_factory, left, step):
    """正在做的事离结束不足三分钟时，点名另一件事、出发或睡觉的同槽位占用拒绝只写「先结束」；恰好三分钟时仍写
    「先结束或暂停」。工具层各行的说明是同一句原因。"""
    engine, activity_id, name = _resting(engine_factory, left)
    reason = f"The first activity slot is occupied by {name}."
    observed, other = act(engine, "other", do_at("家", "测试闲聊"))
    assert other.status == "rejected" and other.error_code == "RESOURCE_CONFLICT" and other.message == reason
    assert other.data["next_step"] == f"{step} {name} first."
    assert target_actions(observed, "家")["interact"]["reason"] == reason
    assert target_tools(observed, name)["pause_activity"]["available"] is (left >= 180)
    for label, request in (("leave", travel("公司", "步行")), ("nap", sleep(30))):
        _, blocked = act(engine, label, request)
        assert blocked.status == "rejected" and blocked.message == reason
        assert blocked.data["next_step"] == f"{step} {name} first."
    assert engine.context.state.activities[activity_id].status == "active"


@pytest.mark.parametrize("left", [179, 180])
def test_sleep_refusal_offers_pausing_the_app_only_while_it_can_be_paused(engine_factory, left):
    """槽位 2 的 app 使用还在放时去睡觉：app 使用随时可以暂停，离这一条结束不足三分钟和恰好三分钟一样，睡眠约束的
    下一步写「先结束或暂停」，工具层 pause_activity 一行可用，照着暂停之后能睡。"""
    engine = engine_factory(mutate=_decides_after(_RUN_SECONDS - left))
    stage_day_plan(engine, submitted=True)
    _, used = act(engine, "keep", use_app("Keep"))
    assert used.status == "in_progress", used.error_code
    assert advance_until_ready(engine)
    activity = engine.context.state.activities[used.activity_id]
    assert activity.status == "active" and activity.resource_claims == {"second"}
    assert activity.planned_end_at - engine.context.state.current_time == left
    before = activity.model_dump(mode="json")
    observed, refused = act(engine, "sleepy", sleep(30))
    assert refused.status == "rejected" and refused.error_code == "RESOURCE_CONFLICT"
    assert refused.message == refused.data["reason"] == _SLEEP_REASON
    assert refused.data["next_step"] == "Conclude or pause the second activity first."
    assert refused.data["fix"] is None and refused.data["unmet"] == []
    assert engine.context.state.activities[used.activity_id].model_dump(mode="json") == before
    pausing = target_tools(observed, activity.name)["pause_activity"]
    assert pausing == {"tool": "pause_activity", "available": True, "reason": None}
    _, released = act(engine, "release", pause_request(activity.name), "pause_activity")
    assert released.status == "completed", released.error_code
    assert engine.context.state.activities[used.activity_id].status == "paused"
    _, slept = act(engine, "sleep", sleep(30))
    assert slept.status == "in_progress", slept.error_code
    assert engine.context.state.activities[slept.activity_id].kind == "sleep"


def test_sleep_refusal_never_offers_pausing_a_running_purchase(engine_factory):
    """槽位 2 是进行中的购买时去睡觉：购买不能暂停，睡眠约束的下一步只写「先结束」；照着暂停会被拒绝，结束之后能睡。"""
    engine = engine_factory(mutate=_decides_after(20))
    stage_day_plan(engine, submitted=True)
    _, bought = act(engine, "noodles", purchase("牛肉面"))
    assert bought.status == "in_progress", bought.error_code
    assert advance_until_ready(engine)
    order = engine.context.state.activities[bought.activity_id]
    assert order.status == "active" and order.kind == "purchase" and order.resource_claims == {"second"}
    assert activity_control.pausable(engine.context.state, order) is False
    observed, refused = act(engine, "sleepy", sleep(30))
    assert refused.status == "rejected" and refused.error_code == "RESOURCE_CONFLICT"
    assert refused.message == _SLEEP_REASON
    assert refused.data["next_step"] == "Conclude the second activity first."
    assert target_tools(observed, order.name)["pause_activity"]["available"] is False
    _, paused = act(engine, "pause", pause_request(order.name), "pause_activity")
    assert paused.status == "rejected" and paused.error_code == "ACTIVITY_PAUSE_UNAVAILABLE"
    assert engine.context.state.activities[bought.activity_id].status == "active"
    _, concluded = act(engine, "conclude", conclude_request(order.name), "conclude_activity")
    assert concluded.status == "completed", concluded.error_code
    _, slept = act(engine, "sleep", sleep(30))
    assert slept.status == "in_progress", slept.error_code


def _one_minute_rest(tmp_path):
    """夹具副本：家里的「测试休息」改成 60 秒。"""
    root = copy_environment(tmp_path / "one-minute")
    rows = catalog_rows(root, "content-catalog.jsonl")
    next(row for row in rows if row["content_id"] == "c-rest")["duration_seconds"] = 60
    write_catalog_rows(root, "content-catalog.jsonl", rows)
    return root


def test_a_sixty_second_content_is_never_pausable(engine_factory, tmp_path):
    """整段只有 60 秒的在地点做的事从开始就不能暂停：刚开始时判定就是不能，做到一半时工具层照样标不可用、暂停以
    不足三分钟的三句被拒；这时点名「测试闲聊」的同槽位占用拒绝只写「先结束」。等它做完后照常收尾。"""
    engine = engine_factory(_one_minute_rest(tmp_path), mutate=_decides_after(20))
    stage_day_plan(engine, submitted=True)
    start = engine.context.state.current_time
    _, used = act(engine, "rest", do_at("家", "测试休息"))
    assert used.status == "in_progress", used.error_code
    activity = engine.context.state.activities[used.activity_id]
    assert activity.planned_duration_seconds == 60 and activity.data["content_id"] == "c-rest"
    assert activity_control.pausable(engine.context.state, activity) is False
    assert advance_until_ready(engine) and engine.context.state.current_time == start + 20
    observed, refused = act(engine, "pause", pause_request(activity.name), "pause_activity")
    assert target_tools(observed, activity.name)["pause_activity"] == {
        "tool": "pause_activity", "available": False, "reason": _reason(activity.name)}
    assert refused.status == "rejected" and refused.message == _reason(activity.name)
    assert refused.data["next_step"] == f"Conclude {activity.name} or let it finish."
    assert refused.data["fix"] == "Conclude this activity or let it finish instead."
    _, second = act(engine, "chat", do_at("家", "测试闲聊"))
    assert second.status == "rejected" and second.error_code == "RESOURCE_CONFLICT"
    assert second.data["next_step"] == f"Conclude {activity.name} first."
    assert not _paused_events(engine)
    concluded = finish_activity(engine, used, "rest")
    assert concluded.status == "completed", concluded.error_code
    done = engine.context.state.activities[used.activity_id]
    assert done.status == "completed" and done.ended_at == start + 60 and done.concluded_at == start + 60


@pytest.mark.asyncio
async def test_host_submits_the_pause_and_gets_the_reason_the_tool_layer_already_showed(engine_factory):
    """经真实宿主：模型在提示里看到 pause_activity 不可用的那句原因；它照样提交时宿主不拦，环境以同一句原因拒绝，
    世界不变；同一刻下一次决策的提示带着这次拒绝，模型改用 conclude_activity 结束成立。"""
    engine, activity_id, name = _resting(engine_factory, 179)
    pause = {"items": [{"activity_name": name, "summary": "先停一下。"}], "errands": []}
    observed, requests, _, [refused] = await decide(engine, "pause", [ToolResponse("pause_activity", pause)])
    assert target_tools(observed, name)["pause_activity"]["reason"] == _reason(name)
    prompt = json.dumps(requests[0]["messages"], ensure_ascii=False)
    assert len(requests) == 1 and _reason(name) in prompt
    assert refused.status == "rejected" and refused.error_code == "ACTIVITY_PAUSE_UNAVAILABLE"
    assert refused.message == _reason(name)
    registered = [row.data["tool_name"] for row in events(engine, "action_requested")]
    assert registered == ["start_main_activity", "pause_activity"]
    assert engine.context.state.activities[activity_id].status == "active" and not _paused_events(engine)
    conclude = {"items": [{"activity_name": name, "summary": "就到这里。", "memories": [], "views": []}],
                "errands": []}
    _, again, _, [concluded] = await decide(engine, "conclude", [ToolResponse("conclude_activity", conclude)])
    assert f"Conclude {name} or let it finish." in json.dumps(again[0]["messages"], ensure_ascii=False)
    assert concluded.status == "completed", concluded.error_code
    assert engine.context.state.activities[activity_id].status == "cancelled"
    assert engine.observe(context(engine, "after"))["todo_layer"]["paused_activities"] == []
