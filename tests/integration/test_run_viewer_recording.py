"""@brief 用真实宿主流程录制查看器请求，隔离于纯资源与 Node 门禁。"""

from pathlib import Path
import json

import pytest

from worldmock.life import prompt_capacity

from tests.engine_fixture import engine_factory as _engine_factory  # noqa: F401
from tests.integration.test_same_second_continuation import _environment, _reply, _single
from tests.life.conftest import advance_until_ready, errand_add, stage_day_plan, start_arguments


def _viewer_scenario(document):
    """@brief 从当前人工场景声明固定日内窗口，邮件仍由真实排程到达。"""
    document["start_at"] = "2026-01-05T09:54:00+08:00"
    document["messages"][0]["sent_at"] = "2026-01-05T09:58:00+08:00"
    document["messages"].append({**document["messages"][0], "message_id": "viewer-pause-mail",
                                 "thread_id": "viewer-pause-thread", "sent_at": "2026-01-05T10:04:00+08:00",
                                 "subject": "稍后处理个人事务"})


async def _viewer_exchange(environment, round_id, tool, arguments, note=""):
    """@brief 只替换远程模型响应，真实人物请求、环境结算与事件全部由宿主产生。"""
    return await _single(environment, round_id, [_reply(
        f"call-{round_id}", tool, arguments, "照当前处境处理手上的事。", note)])


async def _finish_viewer_activity(environment, activity_id):
    """@brief 每个真实唤醒后播报再继续，直到活动实际完成。"""
    engine = environment.engine
    for index in range(16):
        assert advance_until_ready(engine)
        activity = engine.context.state.activities[activity_id]
        if activity.status.value == "completed":
            return activity
        assert activity.status.value == "active"
        await _viewer_exchange(environment, f"continue-{activity_id}-{index}", "report_status", {
            "items": [{"activity_name": activity.name, "summary": "继续做完。"}], "errands": []})
    raise AssertionError("Viewer activity did not complete")


async def _recorded_viewer_requests(factory):
    """@brief 完成事务、动作、闹钟与暂停后录制真实链首及同秒续接请求。"""
    engine = factory(mutate=_viewer_scenario)
    stage_day_plan(engine, submitted=True)
    environment = _environment(engine)
    await _viewer_exchange(environment, "initial-wait", "start_activity", start_arguments(
        ["自己"], ["interact"], ["等待"]))
    assert advance_until_ready(engine)
    initial = next(iter(engine.context.state.activities.values()))
    errands = [errand_add("买柠檬茶", "柠檬茶", remind_time="10:00"),
               errand_add("买牛肉面", "牛肉面", remind_time="18:30")]
    await _viewer_exchange(environment, "note", "conclude_activity", {
        "items": [{"activity_name": initial.name, "summary": "先吃点零食。", "memories": [], "views": []}],
        "errands": errands}, "先把事务记下来。")
    await _viewer_exchange(environment, "alarm", "set_alarm", {"at": "2026-01-05T10:00:00+08:00"})
    await _viewer_exchange(environment, "open", "start_activity", start_arguments(
        ["刷推荐"], ["interact"], ["进行"], app=["哔哩哔哩"]))
    assert advance_until_ready(engine)
    app = next(item for item in engine.context.state.activities.values() if item.kind.value == "action")
    await _viewer_exchange(environment, "pause", "pause_activity", {
        "items": [{"activity_name": app.name, "summary": "先歇一会儿。"}], "errands": []})
    await _viewer_exchange(environment, "wait", "start_activity", start_arguments(["自己"], ["interact"], ["等待"]))
    waiting = next(item for item in engine.context.state.activities.values()
                   if item.kind.value == "wait" and item.status.value == "active")
    waiting = await _finish_viewer_activity(environment, waiting.activity_id)
    _, first, _ = await _viewer_exchange(environment, "action", "conclude_activity", {
        "items": [{"activity_name": waiting.name, "summary": "歇好了。", "memories": [], "views": []}],
        "errands": []}, "歇好了，记得买柠檬茶。")
    _, second, _ = await _viewer_exchange(environment, "continuation", "revise_day_plan", {
        "goals": [{"intent_kind": "休息", "goal": "休息之后买柠檬茶", "remind_time": None,
                   "around_time": None}]})
    assert not engine.context.store.read_agent_events("action_rejected", 1)
    return {"action": first[0], "continuation": second[0]}, engine


@pytest.mark.asyncio
async def test_viewer_fixture_is_recorded_from_the_current_host(_engine_factory, monkeypatch):
    """@brief 夹具的宿主合同由真实动作、闹钟与同秒续接路径共同验证。"""
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model: 1_048_576)
    requests, engine = await _recorded_viewer_requests(_engine_factory)
    fixture = Path(__file__).parents[1] / "fixtures" / "viewer-context.json"
    recorded = json.loads(fixture.read_text(encoding="utf-8"))
    assert recorded["action"]["messages"][0] == requests["action"]["messages"][0]
    assert recorded["action"]["tools"] == requests["action"]["tools"]
    assert recorded["continuation"]["messages"][:2] == recorded["action"]["messages"]
    assert requests["continuation"]["messages"][:2] == requests["action"]["messages"]
    events = engine.context.store.read_events(engine.context.store.last_event_seq)
    assert any(event.data.get("kind") == "app_push" and event.visibility == "agent" for event in events)
    assert any(event.data.get("kind") == "alarm_ring" and event.visibility == "agent" for event in events)
