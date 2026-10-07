"""@brief 补订行为在真实请求、共同时间、SQLite 和重开边界上的验收。"""

import json
from datetime import datetime

import pytest

from tests.life.conftest import advance_next, context, submit_round, report_request, conclude_request, pause_request
from tests.life.scenario_fixtures import run_identity
from tests.life.test_acs_runtime import request
from tests.life.test_acs_system_core import system_world
from worldmock.life.models import SetAlarmRequest, active_activities
from worldmock.life.activity_control import is_pending_conclusion


def all_events(engine):
    """@brief 读取本次运行实际已提交的全部事件。"""
    return engine.context.store.read_events(engine.context.store.last_event_seq)


def checkpoint(engine):
    """@brief 全部人物资产与真实 life 快照经共同检查点持久化。"""
    snapshots = {f"agent:{identity}": engine.context.store.assets.put_json(agent.model_dump(mode="json"),
                 {"kind": "test_agent_snapshot"}) for identity, agent in engine.context.state.agents.items()}
    return engine.checkpoint(snapshots)


def busy(engine, identity):
    """@brief 测试驱动只对其他人物作明确等待、播报或收尾。"""
    activities = [item for item in engine.context.state.activities.values() if item.agent_id == identity]
    pending = [item for item in activities if is_pending_conclusion(item)]
    if pending:
        return "conclude_activity", conclude_request(*(item.name for item in pending))
    running = active_activities(engine.context.state, identity)
    if running:
        return "report_status", report_request(*(item.name for item in running))
    return "start_activity", request("自己", None, "等待")


def perform(engine, label, value, tool="start_activity", identity=1):
    """@brief 按全部真实就绪人物原子提交决策并选出本人回执。"""
    pump(engine, lambda: identity in engine.scheduling_state().ready_agent_ids)
    requests = {person: busy(engine, person) for person in engine.scheduling_state().ready_agent_ids}
    requests[identity] = (tool, value)
    _, results = submit_round(engine, requests, label)
    return next(item for item in results if item.agent_id == identity)


def pump(engine, done, limit=600):
    """@brief 经真实提交和未来边界推进，不直接改人物状态。"""
    for step in range(limit):
        if done():
            return
        ready = engine.scheduling_state().ready_agent_ids
        if ready:
            submit_round(engine, {person: busy(engine, person) for person in ready}, f"pump-{step}-{engine.context.state.version}")
        else:
            advance_next(engine)
    raise AssertionError("Runtime condition was not reached")


@pytest.fixture
def amended_engine(tmp_path):
    """@brief 加油与驿站是原人工目录的明确副本，原七点图字节不变。"""
    from worldmock.life.engine import LifeEngine
    system_world(tmp_path, shipping=90001)
    path = tmp_path / "environment/scenarios/multi-person.json"
    document = json.loads(path.read_bytes())
    engine = LifeEngine.create(path, tmp_path / "run", run_identity(document, "amended-runtime"))
    yield engine
    engine.close()


def reopen(engine):
    """@brief 最新已提交快照重开，不重新抽取和重复结算。"""
    from worldmock.life.engine import LifeEngine
    checkpoint(engine)
    path = engine.context.semantics.catalog.source_dir.parent / "scenarios/multi-person.json"
    return LifeEngine.open(path, engine.context.store.run_dir)


def test_long_delivery_arrival_collection_and_reopen_have_no_inventory(amended_engine):
    engine = amended_engine
    result = perform(engine, "long-buy", request("纸巾", "美团", "购买", payment="微信"))
    assert result.error_code is None, result.data
    order = result.data["orders"][0]
    identity = order["order_id"]
    assert order["fulfillment"] == "station_pickup"
    assert order["expected_at"] - order["created_at"] == 90001
    assert order["delivery_address_id"] == "home"
    pump(engine, lambda: engine.context.state.deliveries[identity].status == "ready_for_pickup")
    events = all_events(engine)
    assert not [event for event in events if event.data.get("kind") == "goods_received" and event.data["order_id"] == identity]
    arrival = next(event for event in events if event.data.get("kind") == "delivery_arrival"
                   and any(row["order_id"] == identity for row in event.data["orders"]))
    assert arrival.agent_id == 1 and arrival.visibility == "agent"
    order = engine.context.state.deliveries[identity]
    station = engine.context.semantics.catalog.locations[order.destination_id].display_name
    running = active_activities(engine.context.state, 1)
    if running:
        ended = perform(engine, "end-wait", conclude_request(*(item.name for item in running)), "conclude_activity")
        assert ended.error_code is None, ended.data
    result = perform(engine, "walk-station", request(f"地点：{station}", None, "步行"))
    assert result.error_code is None, result.data
    pump(engine, lambda: is_pending_conclusion(engine.context.state.activities[result.activity_id]))
    perform(engine, "finish-walk", conclude_request(engine.context.state.activities[result.activity_id].name),
            "conclude_activity")
    before = engine.context.state.current_time
    collected = perform(engine, "collect", request(f"地点：{station}", None, "领取"))
    assert collected.error_code is None, collected.data
    assert collected.activity_id is None and engine.context.state.current_time == before
    assert engine.context.state.deliveries[identity].received_at == before
    receipts = [event for event in all_events(engine) if event.data.get("kind") == "goods_received"
                and event.data["order_id"] == identity]
    assert len(receipts) == 1 and receipts[0].agent_id == 1
    actual = next(event for event in all_events(engine) if event.event_type == "action_result"
                  and event.action_id == collected.action_id)
    assert receipts[0].caused_by == [actual.event_id]
    snapshot = engine.context.state.model_dump(mode="json")
    assert "inventory" not in snapshot["agents"]["1"] and "contact_holdings" not in snapshot
    opened = reopen(engine)
    try:
        assert opened.context.state.model_dump(mode="json") == snapshot
    finally:
        opened.close()
    rejected = perform(engine, "collect-again", request(f"地点：{station}", None, "领取"))
    assert rejected.error_code == "PARCEL_NOT_READY"
    assert len([event for event in all_events(engine) if event.data.get("kind") == "goods_received"
                and event.data["order_id"] == identity]) == 1


def test_actual_driving_day_fuel_receipt_and_same_day_trip(amended_engine):
    engine = amended_engine
    started = perform(engine, "drive-gas", request("地点：测试加油站", None, "驾车"))
    assert started.error_code is None, started.data
    assert engine.context.state.agents[1].driving_days == 2
    pump(engine, lambda: is_pending_conclusion(engine.context.state.activities[started.activity_id]))
    assert engine.context.state.agents[1].driving_days == 1
    perform(engine, "finish-drive", conclude_request(engine.context.state.activities[started.activity_id].name),
            "conclude_activity")
    before = engine.context.state.agents[1].body.model_dump()
    result = perform(engine, "fuel", request("测试燃油", None, "购买", payment="加油支付"))
    assert result.error_code is None, result.data
    assert engine.context.state.agents[1].driving_days == 8
    assert engine.context.state.agents[1].body.model_dump() == before
    event = next(event for event in all_events(engine) if event.data.get("kind") == "goods_received"
                 and event.data["goods_id"] == "fuel-pack")
    assert event.data["provided"] == event.data["applied"] == 7
    trip = perform(engine, "drive-home", request("地点：家", None, "驾车"))
    assert trip.error_code is None, trip.data
    pump(engine, lambda: is_pending_conclusion(engine.context.state.activities[trip.activity_id]))
    assert engine.context.state.agents[1].driving_days == 8


def test_alarm_ends_real_sleep_after_elapsed_seconds_and_reopen(engine_factory):
    engine = engine_factory()
    now = engine.context.state.current_time
    at = datetime.fromtimestamp(now + 300, engine.context.scenario.start_at.tzinfo).isoformat()
    alarm = perform(engine, "alarm", SetAlarmRequest(at=at), "set_alarm")
    assert alarm.error_code is None, alarm.data
    sleep = perform(engine, "sleep", request("自己", None, "睡觉"))
    assert sleep.error_code is None, sleep.data
    pump(engine, lambda: is_pending_conclusion(engine.context.state.activities[sleep.activity_id]))
    activity = engine.context.state.activities[sleep.activity_id]
    assert activity.elapsed_seconds == 300 and activity.ended_at == now + 300
    assert activity.boundary_type == "alarm_ring"
    rings = [event for event in all_events(engine) if event.data.get("kind") == "alarm_ring"]
    assert len(rings) == 1 and rings[0].agent_id == 1
    result = next(event for event in all_events(engine) if event.event_type == "action_result"
                  and event.action_id == alarm.action_id)
    assert rings[0].caused_by == [result.event_id]
    from worldmock.life.engine import LifeEngine
    from tests.data_paths import ARTIFICIAL_ENVIRONMENT_ROOT
    checkpoint(engine)
    opened = LifeEngine.open(ARTIFICIAL_ENVIRONMENT_ROOT / "scenarios/single-person.json", engine.context.store.run_dir)
    try:
        assert opened.context.state.model_dump(mode="json") == engine.context.state.model_dump(mode="json")
    finally:
        opened.close()


@pytest.fixture
def work_engine(tmp_path):
    """@brief 完整人工环境副本缩短排班，工作时长与通信方式仍经真实登记。"""
    import shutil
    from tests.data_paths import ARTIFICIAL_ENVIRONMENT_ROOT
    from tests.catalog.conftest import read_payload, write_payload, edit_row
    from worldmock.life.engine import LifeEngine
    root = tmp_path / "work-environment"
    shutil.copytree(ARTIFICIAL_ENVIRONMENT_ROOT, root)
    directory = root / "catalog"
    edit_row(directory, "contents.json", "topic-sales", {"duration_seconds": 3600})
    edit_row(directory, "contents.json", "c-call", {"duration_seconds": 1800})
    actions = read_payload(directory, "actions.json")
    actions["actions"].append({"action_id": "text-chat", "name": "文字聊天", "description": "围绕真实主题写聊天注记。",
        "category": "social", "app_ids": ["wechat"], "manner": "phone_continuous", "communication_mode": "text"})
    actions["actions"].sort(key=lambda item: item["action_id"])
    write_payload(directory, "actions.json", actions)
    contents = read_payload(directory, "contents.json")
    contents["contents"].append({"content_id": "text-topic", "action_id": "text-chat", "app_ids": ["wechat"],
        "title": "午间项目讨论", "summary": "讨论今天项目的安排。",
        "summary_source": {"kind": "synthetic", "url": None, "notice": "测试用合成摘要"},
        "duration_seconds": 1800, "series": None})
    contents["contents"].sort(key=lambda item: item["content_id"])
    write_payload(directory, "contents.json", contents)
    path = root / "scenarios/single-person.json"
    document = json.loads(path.read_bytes())
    for shift in document["agents"][0]["work_schedule"]["weekly"].values():
        if shift is not None:
            shift.update(starts=540, ends=541)
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    engine = LifeEngine.create(path, tmp_path / "work-run", run_identity(document, "work-current"))
    yield engine
    engine.close()


def pulse(engine, seconds, label):
    """@brief 使用已授权一次性闹钟形成实际未来决策，不直接修改时间。"""
    pump(engine, lambda: 1 in engine.scheduling_state().ready_agent_ids)
    now = engine.context.state.current_time
    at = datetime.fromtimestamp(now + seconds, engine.context.scenario.start_at.tzinfo).isoformat()
    result = perform(engine, label, SetAlarmRequest(at=at), "set_alarm")
    assert result.error_code is None, result.data
    pump(engine, lambda: engine.context.state.current_time >= now + seconds)


def test_real_work_pause_report_restore_completion_and_exhaustion(work_engine):
    engine = work_engine
    trip = perform(engine, "go-office", request("地点：公司", None, "步行"))
    assert trip.error_code is None, trip.data
    pump(engine, lambda: is_pending_conclusion(engine.context.state.activities[trip.activity_id]))
    perform(engine, "end-office-trip", conclude_request(engine.context.state.activities[trip.activity_id].name),
            "conclude_activity")
    work = perform(engine, "work", request("办公", None))
    assert work.error_code is None, work.data
    pulse(engine, 20, "work-pulse")
    activity = engine.context.state.activities[work.activity_id]
    elapsed = activity.elapsed_seconds
    assert 0 < elapsed < 3600
    reported = perform(engine, "work-self-report", report_request(activity.name, summary="我声称已经完成全部工作。"),
                       "report_status")
    assert reported.error_code is None, reported.data
    assert engine.context.state.agents[1].content_progress["topic-sales"].position_seconds == elapsed
    assert engine.context.state.activities[work.activity_id].status == "active"
    paused = perform(engine, "pause-work", pause_request(activity.name), "pause_activity")
    assert paused.error_code is None, paused.data
    elapsed = engine.context.state.activities[work.activity_id].elapsed_seconds
    point = checkpoint(engine)
    from worldmock.life.engine import LifeEngine
    path = engine.context.semantics.catalog.source_dir.parent / "scenarios/single-person.json"
    opened = LifeEngine.open(path, engine.context.store.run_dir)
    try:
        assert opened.context.state.model_dump(mode="json") == point.state
        assert opened.context.state.activities[work.activity_id].status == "paused"
    finally:
        opened.close()
    alarm_at = datetime.fromtimestamp(engine.context.state.current_time + 1800,
        engine.context.scenario.start_at.tzinfo).isoformat()
    perform(engine, "paused-alarm", SetAlarmRequest(at=alarm_at), "set_alarm")
    waiting = perform(engine, "paused-wait", request("自己", None, "等待"))
    assert waiting.error_code is None, waiting.data
    pump(engine, lambda: engine.context.state.current_time >= int(datetime.fromisoformat(alarm_at).timestamp()))
    assert engine.context.state.current_time == int(datetime.fromisoformat(alarm_at).timestamp())
    assert engine.context.state.agents[1].content_progress["topic-sales"].position_seconds == elapsed
    assert engine.context.state.activities[work.activity_id].elapsed_seconds == elapsed
    current = active_activities(engine.context.state, 1)
    pending = [item for item in engine.context.state.activities.values()
               if item.agent_id == 1 and is_pending_conclusion(item)]
    if current or pending:
        perform(engine, "finish-paused-wait", conclude_request(*(item.name for item in (*current, *pending))),
                "conclude_activity")
    resumed = perform(engine, "resume-work", report_request(activity.name, summary="继续做剩余部分。"), "report_status")
    assert resumed.error_code is None, resumed.data
    restored = engine.context.state.activities[work.activity_id]
    assert restored.data["content_id"] == activity.data["content_id"]
    assert restored.data["draw_index"] == activity.data["draw_index"]
    assert restored.media_position_seconds == elapsed
    pump(engine, lambda: is_pending_conclusion(engine.context.state.activities[work.activity_id]))
    final = engine.context.state.agents[1].content_progress["topic-sales"]
    assert final.completed_rounds == 1 and final.position_seconds == 0
    completed = [event for event in all_events(engine) if event.data.get("kind") == "work_content_completed"]
    assert len(completed) == 1 and completed[0].data["content_id"] == "topic-sales"
    perform(engine, "conclude-work", conclude_request(activity.name), "conclude_activity")
    rejected = perform(engine, "work-again", request("办公", None))
    assert rejected.error_code == "CONTENT_EXHAUSTED"


@pytest.mark.parametrize("name,mode", [("文字聊天", "text"), ("语音通话", "voice")])
def test_real_text_chat_notes_and_voice_dispatch_use_declared_mode(work_engine, monkeypatch, name, mode):
    from worldmock.life import social
    engine = work_engine
    calls = []
    original = social.start_call
    def tracked(*arguments):
        calls.append(arguments)
        return original(*arguments)
    monkeypatch.setattr(social, "start_call", tracked)
    started = perform(engine, f"chat-{mode}", request(name, "微信", to="联系人：王磊"))
    assert started.error_code is None, started.data
    activity = engine.context.state.activities[started.activity_id]
    assert activity.data["category"] == "social"
    assert len(calls) == (1 if mode == "voice" else 0)
    pulse(engine, 60, f"chat-pulse-{mode}")
    progress = engine.context.state.agents[1].content_progress[activity.data["content_id"]].model_copy(deep=True)
    text = "我向王磊讨论了项目安排，现在准备整理结论。"
    result = perform(engine, f"chat-report-{mode}", report_request(activity.name, summary=text), "report_status")
    assert result.error_code is None, result.data
    assert engine.context.state.activities[started.activity_id].progress_note == text
    assert engine.context.state.agents[1].content_progress[activity.data["content_id"]] == progress
    assert not engine.context.state.agents[1].outbox
    assert not [event for event in all_events(engine) if event.data.get("kind") == "message_arrival"]
    assert len(calls) == (1 if mode == "voice" else 0)
