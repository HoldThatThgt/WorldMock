"""@brief 当前合同的真实引擎、SQLite、共同检查点与五工件认证。"""

from datetime import datetime, timedelta
from copy import deepcopy
import hashlib
import json

import pytest

from worldmock.errors import IntegrityError

from tests.life.conftest import advance_next, submit_round
from worldmock.footprints.export import export_run
from worldmock.footprints.validation import validate_export
from worldmock.life.models import ReportStatusRequest, StartActivityRequest


def short_run(document):
    start = datetime.fromisoformat(document["start_at"])
    document["end_at"] = (start + timedelta(hours=1)).isoformat()
    document["messages"] = []
    document["payrolls"] = []
    document["bills"] = []


def request(target, way, app=None, payment=None, to=None):
    return StartActivityRequest(items=[{"target": target, "way": way, "app": app, "pay_app": payment, "to": to}],
                                answering=None)


def finish(engine):
    rounds = 0
    while engine.context.state.current_time < engine.context.state.end_time:
        ready = engine.scheduling_state().ready_agent_ids
        if ready:
            requests = {}
            for identity in ready:
                active = [item for item in engine.context.state.activities.values()
                          if item.agent_id == identity and item.status == "active"]
                requests[identity] = ("report_status", ReportStatusRequest(items=[
                    {"activity_name": item.name, "summary": "继续当前内容。"} for item in active], errands=[]))
            _, results = submit_round(engine, requests, f"finish-{rounds}")
            assert all(result.error_code is None for result in results)
            rounds += 1
            continue
        advance_next(engine)
    snapshots = {f"agent:{identity}": engine.context.store.assets.put_json(agent.model_dump(mode="json"),
                 {"kind": "test_agent_snapshot"}) for identity, agent in engine.context.state.agents.items()}
    return engine.checkpoint(snapshots)


def test_real_engine_current_five_artifacts_and_receipt_owner(engine_factory, tmp_path):
    engine = engine_factory(mutate=short_run)
    _, purchased = submit_round(engine, {1: ("start_activity", request("牛肉面", "购买", "美团", "支付宝"))}, "buy")
    assert purchased[0].error_code is None
    _, started = submit_round(engine, {1: ("start_activity", request("刷推荐", "进行", "抖音"))}, "play")
    assert started[0].error_code is None
    point = finish(engine)
    directory = tmp_path / "current-export"
    manifest = export_run(engine.context.store, point.last_event_seq, directory)
    certified, events, activities = validate_export(directory)
    assert certified == manifest and manifest["schema_version"] == 4
    assert set(manifest["artifacts"]) == {"events", "activities", "verified_experiences", "purchases", "app_usage"}
    assert manifest["checkpoint"]["state"]["schema_version"] == 11
    assert any(event["data"].get("kind") == "goods_received" for event in events)
    assert any(activity["data"].get("app_id") == "douyin" for activity in activities)


@pytest.fixture
def real_export(engine_factory, tmp_path):
    """@brief 当前真实引擎购买与持续使用完成后，只在导出副本里施加反例。"""
    engine = engine_factory(mutate=short_run)
    _, purchased = submit_round(engine, {1: ("start_activity", request("牛肉面", "购买", "美团", "支付宝"))}, "buy")
    assert purchased[0].error_code is None
    _, started = submit_round(engine, {1: ("start_activity", request("刷推荐", "进行", "抖音"))}, "play")
    assert started[0].error_code is None
    point = finish(engine)
    directory = tmp_path / "actual-export"
    export_run(engine.context.store, point.last_event_seq, directory)
    validate_export(directory)
    return directory


def write_rows(directory, manifest, name, rows):
    """@brief 改写被测导出文件并刷新真实字节摘要和行数，不用损坏哈希代替行为认证。"""
    from worldmock.footprints.models import canonical_json
    descriptor = manifest["artifacts"][name]
    raw = b"".join(canonical_json(row) + b"\n" for row in rows)
    (directory / descriptor["path"]).write_bytes(raw)
    descriptor.update(sha256=hashlib.sha256(raw).hexdigest(), rows=len(rows))


def reproject(directory, manifest, events):
    """@brief 五份工件和质量全部按被污染后的事件重算，失败只能来自实际语义约束。"""
    from worldmock.footprints.app_usage import project_app_usage
    from worldmock.footprints.export import _verified_experience_rows, reconstruct_activities
    from worldmock.footprints.models import Event, canonical_json, sha256_json
    from worldmock.footprints.purchases import project_purchase_facts
    from worldmock.footprints.quality import build_quality_report
    records = [Event.model_validate(event) for event in events]
    activities = reconstruct_activities(records)
    write_rows(directory, manifest, "events", events)
    write_rows(directory, manifest, "activities", activities)
    write_rows(directory, manifest, "verified_experiences",
               _verified_experience_rows(manifest["frozen_run_manifest"]["agent_ids"], records))
    write_rows(directory, manifest, "purchases", project_purchase_facts(events))
    write_rows(directory, manifest, "app_usage", project_app_usage(events))
    quality = canonical_json(build_quality_report(records, activities, manifest["frozen_run_manifest"])) + b"\n"
    (directory / "quality.json").write_bytes(quality)
    manifest["quality_report"].update(size_bytes=len(quality), sha256=hashlib.sha256(quality).hexdigest())
    checkpoint = manifest["checkpoint"]
    checkpoint["last_event_seq"] = manifest["cutoff_event_seq"] = len(events)
    checkpoint["record_sha256"] = sha256_json({key: value for key, value in checkpoint.items()
                                               if key != "record_sha256"})
    (directory / "manifest.json").write_bytes(canonical_json(manifest) + b"\n")
    return activities


def read_export(directory):
    """@brief 原始证据是已认证过的当前真实运行导出。"""
    manifest = json.loads((directory / "manifest.json").read_bytes())
    events = [json.loads(line) for line in (directory / "events.jsonl").read_bytes().splitlines()]
    return manifest, events


@pytest.mark.parametrize("version", ["export_three", "world_ten", "run_three"])
def test_current_export_rejects_every_retired_schema_at_the_consumer(real_export, version):
    manifest, _ = read_export(real_export)
    if version == "export_three":
        manifest["schema_version"] = 3
    elif version == "world_ten":
        manifest["checkpoint"]["state"]["schema_version"] = 10
    else:
        manifest["frozen_run_manifest"]["schema_version"] = 3
    (real_export / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(IntegrityError, match="supported version|world schema|frozen run manifest"):
        validate_export(real_export)


@pytest.mark.parametrize("corruption", ["missing_goods", "duplicate_goods", "arrival_money", "receipt_money",
                                        "backwards_position", "other_person_purchase"])
def test_real_export_rejects_corrupted_events_after_all_five_projections_and_hashes_agree(real_export, corruption):
    manifest, events = read_export(real_export)
    receipt = next(event for event in events if event["data"].get("kind") == "goods_received")
    if corruption == "missing_goods":
        receipt["data"]["kind"] = "test_omitted_receipt"
    elif corruption == "duplicate_goods":
        index = events.index(receipt)
        duplicate = deepcopy(receipt)
        duplicate["event_id"] += "-duplicate"
        events.insert(index + 1, duplicate)
        for sequence, event in enumerate(events, 1):
            event["event_seq"] = sequence
    elif corruption in {"arrival_money", "receipt_money"}:
        kind = "delivery_arrival" if corruption == "arrival_money" else "goods_received"
        event = next(event for event in events if event["data"].get("kind") == kind)
        event["data"]["resource_changes"] = {"account:agent:1": -100, "account:merchant:noodle-1": 100}
        accounts = manifest["checkpoint"]["state"]["accounts"]
        accounts["agent:1"] -= 100
        accounts["merchant:noodle-1"] += 100
    elif corruption == "backwards_position":
        progress = next(event for event in events if event["data"].get("content", {}).get(
            "position_before_seconds", 0) > 0)
        progress["data"]["content"]["position_before_seconds"] = 0
    else:
        result = next(event for event in events if event["event_type"] == "action_result"
                      and event["data"].get("data", {}).get("orders"))
        result["agent_id"] = 2
    reproject(real_export, manifest, events)
    with pytest.raises(IntegrityError, match="Receipt and goods received counts|cannot change account balances|"
                       "Content positions|source event is missing or owned by another"):
        validate_export(real_export)


@pytest.mark.parametrize("corruption", ["no_app", "outside_segment"])
def test_real_export_rejects_rehashed_rows_that_invent_app_or_consumption_time(real_export, corruption):
    manifest, _ = read_export(real_export)
    rows = [json.loads(line) for line in (real_export / "app-usage.jsonl").read_bytes().splitlines()]
    if corruption == "no_app":
        rows[0]["app_id"] = "invented-app"
    else:
        content = next(row for row in rows if row["fact_kind"] == "content_consumed")
        content["interval_start"] -= 3600
    write_rows(real_export, manifest, "app_usage", rows)
    (real_export / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(IntegrityError, match="app usage facts disagree"):
        validate_export(real_export)


@pytest.mark.parametrize("collected", [False, True])
def test_real_station_export_keeps_purchase_payer_and_actual_recipient_notifications_and_receipt(
        engine_factory, tmp_path, collected):
    """@brief 真实多人物引擎跑到终点，再认证到站未取与本人真正领取两种终态。"""
    from tests.integration.life.test_acs_amended_runtime import perform, pump
    from tests.integration.life.test_acs_runtime import request as current_request
    from tests.integration.life.test_acs_system_core import system_world
    from tests.life.conftest import conclude_request
    from worldmock.life.activity_control import is_pending_conclusion
    from worldmock.life.models import active_activities
    system_world(tmp_path / "parcel-input", shipping=90001)
    path = tmp_path / "parcel-input/environment/scenarios/multi-person.json"
    engine = engine_factory(path)
    recipient = f"人物：{engine.context.state.agents[2].name}"
    result = perform(engine, "remote-other", current_request("纸巾", "美团", "购买", to=recipient, payment="微信"))
    assert result.error_code is None, result.data
    order_id = result.data["orders"][0]["order_id"]
    pump(engine, lambda: engine.context.state.deliveries[order_id].status == "ready_for_pickup")
    if collected:
        running = active_activities(engine.context.state, 2)
        if running:
            assert perform(engine, "finish-wait", conclude_request(*(item.name for item in running)),
                           "conclude_activity", 2).error_code is None
        station = engine.context.semantics.catalog.locations[engine.context.state.deliveries[order_id].destination_id]
        walked = perform(engine, "walk", current_request(f"地点：{station.display_name}", None, "步行"), identity=2)
        assert walked.error_code is None, walked.data
        pump(engine, lambda: is_pending_conclusion(engine.context.state.activities[walked.activity_id]))
        assert perform(engine, "end-walk", conclude_request(engine.context.state.activities[walked.activity_id].name),
                       "conclude_activity", 2).error_code is None
        taken = perform(engine, "take", current_request(f"地点：{station.display_name}", None, "领取"), identity=2)
        assert taken.error_code is None, taken.data
    pump(engine, lambda: engine.context.state.current_time == engine.context.state.end_time, limit=2000)
    snapshots = {f"agent:{identity}": engine.context.store.assets.put_json(agent.model_dump(mode="json"),
                 {"kind": "test_agent_snapshot"}) for identity, agent in engine.context.state.agents.items()}
    point = engine.checkpoint(snapshots)
    directory = tmp_path / "station-export"
    export_run(engine.context.store, point.last_event_seq, directory)
    validate_export(directory)
    rows = [json.loads(line) for line in (directory / "purchases.jsonl").read_bytes().splitlines()]
    rows = [row for row in rows if row.get("order_id") == order_id]
    assert next(row for row in rows if row["fact_kind"] == "purchase")["agent_id"] == 1
    ready = [row for row in rows if row["fact_kind"] == "pickup_ready"]
    receipts = [row for row in rows if row["fact_kind"] == "receipt"]
    assert len(ready) == 1 and ready[0]["agent_id"] == 2
    assert len(receipts) == int(collected)
    if collected:
        assert receipts[0]["agent_id"] == 2


def test_real_twenty_content_continuations_export_one_use_with_bounded_decisions(engine_factory, tmp_path):
    """@brief 二十个六分钟内容由真实共同时间接续，两小时只有既有播报决策机会。"""
    from tests.integration.life.test_acs_amended_runtime import all_events, perform, pump, checkpoint
    from tests.integration.life.test_acs_runtime import request

    def content_window(document):
        document.update(start_at="2026-01-05T08:00:00+08:00", end_at="2026-01-05T10:00:00+08:00",
                        messages=[], bills=[], payrolls=[], progress_report_interval_seconds=1800)
    engine = engine_factory(mutate=content_window)
    directory = engine.context.semantics.catalog.source_dir
    feed_id = next(action.action_id for action in engine.context.semantics.catalog.actions.values()
                   if action.name == "刷推荐")
    # 使用完整目录副本重建引擎，新增内容先通过真实注册准入。
    engine.close()
    from worldmock.catalog.identity import refresh_manifest_identity
    from worldmock.life.engine import LifeEngine
    from tests.life.scenario_fixtures import run_identity
    contents_path = directory / "contents.json"
    contents = json.loads(contents_path.read_bytes())
    contents["contents"] = [row for row in contents["contents"] if row["action_id"] != feed_id]
    for index in range(30):
        contents["contents"].append({"content_id": f"cost-unit-{index:02d}", "action_id": feed_id,
            "app_ids": ["bilibili", "douyin"], "title": f"测试短视频 {index}", "summary": "连续浏览的合成内容。",
            "summary_source": {"kind": "synthetic", "url": None, "notice": "测试合成素材"},
            "duration_seconds": 360, "series": None})
    contents["contents"].sort(key=lambda row: row["content_id"])
    contents_path.write_text(json.dumps(contents, ensure_ascii=False), encoding="utf-8")
    refresh_manifest_identity(directory)
    path = directory.parent / "scenarios/single-person.json"
    document = json.loads(path.read_bytes())
    engine = LifeEngine.create(path, tmp_path / "cost-run", run_identity(document, "cost-run"))
    try:
        started = perform(engine, "start-feed", request())
        assert started.error_code is None, started.data
        pump(engine, lambda: engine.context.state.current_time == engine.context.state.end_time)
        events = all_events(engine)
        start = next(event for event in events if event.event_type == "action_accepted"
                     and event.activity_id == started.activity_id).occurred_at
        decisions = [event for event in events if event.agent_id == 1 and event.event_type == "decision_started"
                     and start <= event.occurred_at <= engine.context.state.end_time]
        assert len(decisions) <= 6
        point = checkpoint(engine)
        destination = tmp_path / "cost-export"
        export_run(engine.context.store, point.last_event_seq, destination)
        manifest, _, _ = validate_export(destination)
        rows = [json.loads(line) for line in (destination / "app-usage.jsonl").read_text().splitlines()]
        uses = [row for row in rows if row["fact_kind"] == "app_use" and row["activity_id"] == started.activity_id]
        completed = [row for row in rows if row["fact_kind"] == "content_consumed" and row["completed"]]
        assert len(uses) == 1 and uses[0]["segments"] == [{"started_at": start, "ended_at": start + 7200}]
        assert len(completed) == len({row["content_id"] for row in completed}) == 20
        assert len({row["source_event_id"] for row in completed}) == 20
        assert manifest["schema_version"] == 4
    finally:
        engine.close()


def test_real_manners_pauses_navigation_and_no_app_export_pass_all_invariants(engine_factory, tmp_path):
    """@brief 同一次实际运行导出三种使用方式、暂停、重复路线、推送与终点截断。"""
    from tests.life.conftest import conclude_request, pause_request, report_request
    from tests.integration.life.test_acs_amended_runtime import all_events, perform, pump, checkpoint
    from tests.integration.life.test_acs_runtime import request as current_request
    from tests.life.work_runtime import trip_to
    from worldmock.life.models import SetAlarmRequest
    from worldmock.life.activity_control import is_pending_conclusion

    def window(document):
        document.update(start_at="2026-01-05T08:00:00+08:00", end_at="2026-01-05T17:00:00+08:00",
                        bills=[], payrolls=[])
        for message in document["messages"]:
            message["sent_at"] = "2026-01-05T16:59:00+08:00"
    engine = engine_factory("multi-person.json", mutate=window)
    identities = []
    def by(seconds):
        from tests.life.conftest import wake_by_mail
        at = wake_by_mail(engine, offset=seconds)
        pump(engine, lambda: engine.context.state.current_time >= at)
    def start(target, app=None, way="进行", to=None):
        pending = [item for item in engine.context.state.activities.values() if item.agent_id == 1
                   and item.kind == "wait" and (item.status == "active" or is_pending_conclusion(item))]
        for item in pending:
            end(item.activity_id)
        result = perform(engine, f"start-{len(identities)}", current_request(target, app, way, to))
        assert result.error_code is None, result.data
        identities.append(result.activity_id)
        return result.activity_id
    def end(identity):
        item = engine.context.state.activities[identity]
        result = perform(engine, f"end-{identity}", conclude_request(item.name), "conclude_activity")
        assert result.error_code is None, result.data
    def navigated(label):
        name = next(place.display_name for place in engine.context.scenario.locations if place.location_id == "office")
        identity = start(f"地点：{name}", "高德地图", "步行")
        pump(engine, lambda: is_pending_conclusion(engine.context.state.activities[identity]))
        end(identity)
        return identity
    by(3600)
    feed = start("刷推荐", "抖音")
    by(60)
    paused_at = engine.context.state.current_time
    assert perform(engine, "pause-feed", pause_request(engine.context.state.activities[feed].name),
                   "pause_activity").error_code is None
    by(30)
    resumed_at = engine.context.state.current_time
    assert perform(engine, "resume-feed", report_request(engine.context.state.activities[feed].name),
                   "report_status").error_code is None
    selected = engine.context.state.activities[feed]
    remaining = engine.context.semantics.catalog.contents[selected.data["content_id"]].duration_seconds - selected.media_position_seconds
    by(int(remaining) + 30)
    end(feed)
    course = start("学课程", "哔哩哔哩")
    by(60)
    end(course)
    run = start("跑步", "Keep")
    pump(engine, lambda: is_pending_conclusion(engine.context.state.activities[run]))
    end(run)
    quiet = start("安静休息")
    by(60)
    end(quiet)
    other = engine.context.state.agents[2].name
    chat = start("聊天", to=f"人物：{other}")
    by(60)
    end(chat)
    first_route = navigated("first")
    work = start("办公")
    by(60)
    end(work)
    trip_to(engine, "home", "return-home")
    second_route = navigated("second")
    trip_to(engine, "home", "return-again")
    due = engine.context.state.current_time + 60
    value = datetime.fromtimestamp(due, engine.context.scenario.start_at.tzinfo).isoformat()
    assert perform(engine, "sleep-alarm", SetAlarmRequest(at=value), "set_alarm").error_code is None
    sleep = start("自己", way="睡觉")
    pump(engine, lambda: is_pending_conclusion(engine.context.state.activities[sleep]))
    end(sleep)
    at = engine.context.state.end_time - 60
    by(at - engine.context.state.current_time)
    truncated = start("跑步", "Keep")
    pump(engine, lambda: engine.context.state.current_time == engine.context.state.end_time)
    point = checkpoint(engine)
    directory = tmp_path / "manners-export"
    export_run(engine.context.store, point.last_event_seq, directory)
    manifest, _, _ = validate_export(directory)
    rows = [json.loads(line) for line in (directory / "app-usage.jsonl").read_text().splitlines()]
    def facts(identity):
        return [row for row in rows if row["activity_id"] == identity and not row["fact_kind"].startswith("push_")]
    [use] = [row for row in facts(feed) if row["fact_kind"] == "app_use"]
    assert use["device"] == "phone" and len(use["segments"]) == 2
    assert use["segments"][0]["ended_at"] == paused_at and use["segments"][1]["started_at"] == resumed_at
    assert {row["fact_kind"] for row in facts(feed)} == {"app_use", "content_consumed"}
    assert {row["fact_kind"] for row in facts(course)} == {"app_use", "content_consumed"}
    assert {row["device"] for row in facts(course)} == {"other"}
    for identity in (run, first_route, second_route):
        assert [row["edge"] for row in facts(identity)] == ["start", "end"]
        assert {row["fact_kind"] for row in facts(identity)} == {"app_touch"}
    assert first_route != second_route
    assert all(row["action_id"] is None for identity in (first_route, second_route) for row in facts(identity))
    assert not any(facts(identity) for identity in (quiet, work, chat, sleep))
    assert [row["edge"] for row in facts(truncated)] == ["start"]
    assert all({"fact_kind", "agent_id", "app_id", "device", "action_id", "manner", "activity_id", "source_event_id"}
               <= row.keys() for row in rows)
    pushes = [row for row in rows if row["fact_kind"].startswith("push_")]
    assert {row["fact_kind"] for row in pushes} >= {"push_delivered", "push_opened", "push_released"}
    assert all(row["action_id"] == "browse-feed" for row in pushes)
    assert manifest["schema_version"] == 4
