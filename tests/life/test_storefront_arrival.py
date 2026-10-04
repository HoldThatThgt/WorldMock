"""通过真实生活引擎验证到达后的门店条件、旅行事实和暂停活动的地点重验。"""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from tests.life.conftest import (activity_name, advance_next, advance_until_ready, conclude_request, context,
                                 pause_request, report_request, stage_day_plan, start_activity,
                                 wake_by_mail)
from tests.life.test_commerce import _observed_action
from tests.life.test_scheduling import _custom_engine, _document, _identity
from worldmock.footprints.models import EventDraft
from worldmock.life.engine import LifeEngine
from worldmock.life.models import active_activities
from worldmock.life.object_labels import supply_label
from worldmock.life.opening_hours import opening_status
from worldmock.life.result_projection import public_result

PRODUCT = "food-6923644266066"
STORE = "poi-3538682402"
SUPPLY = f"supply-{STORE}-{PRODUCT}"
HOME_ACTIVITY = "wellness-morning-wash"
ROOT = Path(__file__).resolve().parents[2]


def _source():
    """@brief 受控世界变化有自身的场景假设来源。"""
    return {"kind": "scenario", "url": "scenario://storefront-arrival/condition-change",
            "retrieved_at": "2026-09-20T00:00:00+08:00", "license": "WorldMock-project",
            "notice": "Controlled arrival test assumption", "fact_cutoff": "2026-09-20",
            "applicability": "Explicit simulated condition change, not an observed retail fact"}


def _write_json(path, value):
    """@brief 改写前解除隔离副本的硬链接，禁止修改正式语料字节。"""
    if path.exists():
        path.unlink()
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _restricted_engine(tmp_path):
    """@brief 复制正式冻结输入并声明进入资格，所有哈希按真实准入重新钉扎。"""
    corpus = tmp_path / "corpora"
    shutil.copytree(ROOT / "corpora", corpus)
    places_path = corpus / "zhangjiang-map/locations.json"
    places = json.loads(places_path.read_text(encoding="utf-8"))
    restricted = next(item for item in places["locations"] if item["location_id"] == STORE)
    restricted.update(required_qualifications=["arrival-member"], source=_source())
    places_sha = _write_json(places_path, places)
    dataset_path = corpus / "zhangjiang-map/20260105/dataset.json"
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    dataset["locations_file"]["sha256"] = places_sha
    dataset_sha = _write_json(dataset_path, dataset)
    manifest_path = corpus / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest["entries"]:
        if entry["path"] in {"zhangjiang-map/locations.json", "zhangjiang-map/20260105/dataset.json"}:
            path = corpus / entry["path"]
            entry.update(sha256=hashlib.sha256(path.read_bytes()).hexdigest(), size_bytes=path.stat().st_size)
    manifest_sha = _write_json(manifest_path, manifest)
    document = _document("white-collar-12-weeks.json")
    document.update(start_at="2026-01-05T08:00:00+08:00", corpus_root="../corpora",
                    corpus_manifest_sha256=manifest_sha)
    document["map_dataset"]["sha256"] = dataset_sha
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    shutil.copytree(ROOT / "catalog", tmp_path / "catalog")
    path = scenarios / "restricted.json"
    _write_json(path, document)
    return LifeEngine.create(path, tmp_path / "run-arrival", _identity(document, "restricted-arrival"))


@pytest.fixture
def arrival_engine(tmp_path, request):
    """@brief 条件在八点仍成立，真实事件或分钟营业边界在旅途中使其变化。"""
    case = request.param

    def configure(document):
        """@brief 仅声明本用例实际发生的调价或售罄事件。"""
        document["start_at"] = "2026-01-05T08:00:00+08:00"
        if case == "price":
            document["price_changes"] = [{"change_id": "arrival-price", "supply_id": SUPPLY,
                "price_version": "arrival-price-v2", "amount_fen": 700,
                "due_at": "2026-01-05T08:01:00+08:00", "source": _source()}]
        if case == "stock":
            document["product_releases"] = [{"release_id": "arrival-sold-out", "supply_id": SUPPLY,
                "stock": 0, "due_at": "2026-01-05T08:01:00+08:00", "source": _source()}]

    engine = (_restricted_engine(tmp_path) if case == "access" else
              _custom_engine("white-collar-12-weeks.json", configure, tmp_path, run_id="arrival"))
    stage_day_plan(engine)
    if case == "closed":
        staged = engine.context.state.model_copy(deep=True)
        staged.opening_exceptions[STORE]["2026-01-05"] = [(0, 481)]
        staged.version += 1
        engine.context.store.commit(staged.model_dump(mode="json"), [EventDraft(
            event_type="environment_event", occurred_at=staged.current_time, visibility="system",
            source_kind="scenario", data={"kind": "test_minute_closing_declared"})])
        engine.context.state = staged
    yield engine
    engine.close()


def _finish_activity(engine, identity, phase):
    """@brief 仅经真实时间边界与按活动名称播报推进活动到完成或失败。"""
    for step in range(64):
        if engine.context.state.activities[identity].status != "active":
            return engine.context.state.activities[identity]
        if engine.scheduling_state().ready_agent_ids:
            _, result = _observed_action(engine, f"{phase}-report-{step}", "report_status",
                                        report_request(activity_name(engine, identity), summary="继续出行。"))
            assert result.status == "completed", result.error_code
        else:
            advance_next(engine)
    pytest.fail("Activity did not reach its terminal state")


def _travel(engine, destination, name):
    """@brief 按当前冻结地点名称驾车，实际支付交通费并在到达当秒收尾。"""
    origin = engine.context.state.agents[1].location_id
    route = next(item for item in engine.context.scenario.routes
                 if item.origin_id == origin and item.destination_id == destination and item.mode == "driving")
    before = dict(engine.context.state.accounts)
    _, started = _observed_action(engine, name, "start_activity", lambda observed: start_activity(
        "驾车", next(row["object"] for row in observed["interaction_bindings"]
                   if row["kind"] == "location" and row["object_id"] == destination)))
    assert started.status == "in_progress", started.error_code
    activity = _finish_activity(engine, started.activity_id, name)
    assert engine.context.state.agents[1].location_id == destination
    assert activity.route_progress_meters == pytest.approx(route.distance_meters)
    assert activity.elapsed_seconds == activity.planned_duration_seconds
    assert engine.context.state.accounts["agent:1"] == before["agent:1"] - route.fare_fen
    assert engine.context.state.accounts[engine.context.scenario.transport_account_id] == (
        before[engine.context.scenario.transport_account_id] + route.fare_fen)
    assert route.fare_fen > 0
    _, ended = _observed_action(engine, f"{name}-conclude", "conclude_activity",
                                conclude_request(activity_name(engine, activity.activity_id)))
    assert ended.status == "completed", ended.error_code
    return engine.context.state.activities[activity.activity_id]


def _purchase_facts(engine):
    """@brief 比较全部商务副作用，排除合法拒绝的节律字段。"""
    payload = engine.context.state.model_dump(mode="json")
    return {**{name: payload[name] for name in ("accounts", "supply_stock", "supply_quotes", "deliveries",
                                              "pending_events", "activities")},
            "inventory": payload["agents"]["1"]["inventory"],
            "location_id": payload["agents"]["1"]["location_id"]}


def _supply_name(engine):
    """@brief 由正式门店和商品名称构造唯一可读供给名称，不向人物请求写内部身份。"""
    store = next(item for item in engine.context.scenario.locations if item.location_id == STORE)
    return supply_label(store.name, engine.context.rules.products[PRODUCT].name)


@pytest.mark.parametrize("arrival_engine,reason", [
    ("closed", "MERCHANT_CLOSED"), ("stock", "OUT_OF_STOCK"), ("price", None),
], indirect=["arrival_engine"])
def test_purchase_rechecks_changed_conditions_after_real_travel_without_reversing_it(arrival_engine, reason):
    """@brief 出发时可买，到达后闭店和售罄原子拒绝；调价使用当前冻结报价，已支付旅行始终保留。"""
    engine = arrival_engine
    location = next(item for item in engine.context.scenario.locations if item.location_id == STORE)
    assert opening_status(location, engine.context.state.current_time, engine.context.scenario.timezone,
                          engine.context.state.opening_exceptions[STORE]) == "open"
    old_quote = engine.context.state.supply_quotes[SUPPLY].model_copy(deep=True)
    assert engine.context.state.supply_stock[SUPPLY] > 0
    travel = _travel(engine, STORE, "drive-to-store")
    assert travel.status == "completed" and travel.data["arrival"]["access_status"] == "allowed"
    before = _purchase_facts(engine)
    _, result = _observed_action(engine, "buy-after-arrival", "start_activity",
                                 start_activity("到店", _supply_name(engine)))
    if reason is not None:
        assert result.status == "rejected" and result.error_code == reason
        assert _purchase_facts(engine) == before
        assert not result.data.get("resource_changes") and not result.data.get("inventory_changes")
    else:
        assert result.status == "completed", result.error_code
        actual = engine.context.state.supply_quotes[SUPPLY]
        assert actual.price_version != old_quote.price_version and actual.price_fen == 700
        assert result.data["total_price_fen"] == 700
        assert before["accounts"]["agent:1"] - engine.context.state.accounts["agent:1"] == 700
        assert engine.context.state.supply_stock[SUPPLY] == before["supply_stock"][SUPPLY] - 1
        assert engine.context.state.agents[1].inventory[STORE][PRODUCT] == 1
    terminal = [event for event in engine.context.store.read_agent_events("action_result", 1)
                if event.action_id == "action-drive-to-store" and event.data["status"] == "completed"]
    assert len(terminal) == 1
    assert terminal[0].data["data"]["activity"]["route_progress_meters"] == travel.route_progress_meters


@pytest.mark.parametrize("arrival_engine", ["access"], indirect=True)
def test_arrival_rechecks_access_and_records_denial_without_undoing_real_travel(arrival_engine):
    """@brief 冻结道路允许启程，实际到达拒绝进入资格并保留位置、里程、交通费和完整失败证据。"""
    engine = arrival_engine
    travel = _travel(engine, STORE, "drive-to-restricted-place")
    assert travel.status == "failed" and travel.interruption_reason == "LOCATION_ACCESS_DENIED"
    assert travel.data["arrival"]["missing_qualifications"] == ["arrival-member"]
    assert travel.data["arrival"]["access_status"] == "denied"
    assert not active_activities(engine.context.state, 1)
    terminal = [event for event in engine.context.store.read_agent_events("action_result", 1)
                if event.action_id == "action-drive-to-restricted-place" and event.data["status"] == "failed"]
    assert len(terminal) == 1
    recorded = terminal[0].data["data"]["activity"]
    assert recorded["data"]["arrival"] == travel.data["arrival"]
    assert recorded["interruption_reason"] == "LOCATION_ACCESS_DENIED"
    assert terminal[0].data["error_code"] == "LOCATION_ACCESS_DENIED"
    assert 1 in engine.scheduling_state().ready_agent_ids
    assert engine.context.state.agents[1].next_decision_at == engine.context.state.current_time
    observed = engine.observe(context(engine, "arrival-denial-feedback"))
    public = public_result(terminal[0].data, engine.context.scenario)
    place = next(item.name for item in engine.context.scenario.locations if item.location_id == STORE)
    assert public["details"]["arrival"] == {"place": place, "access": "没能进去"}
    settled = [line for entry in observed["memory_layer"]["earlier_today"] for line in entry["settled"]]
    assert any(place in line and "失败" in line for line in settled)


@pytest.mark.parametrize("arrival_engine", ["activity"], indirect=True)
def test_paused_lifestyle_cannot_resume_after_real_travel_to_incompatible_environment(arrival_engine):
    """@brief 居所活动实际启动并暂停，驾车到办公室后用 report_status 恢复时重验开始地点。"""
    engine = arrival_engine
    item = next(item for item in engine.context.lifestyle_items if item.activity_id == HOME_ACTIVITY)
    _, started = _observed_action(engine, "start-home-activity", "start_activity", start_activity("进行", item.name))
    assert started.status == "in_progress", started.error_code
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine)
    name = activity_name(engine, started.activity_id)
    _, paused = _observed_action(engine, "pause-home-activity", "pause_activity", pause_request(name, summary="先暂停。"))
    assert paused.status == "completed", paused.error_code
    original = copy.deepcopy(engine.context.state.activities[started.activity_id])
    assert original.status == "paused" and original.elapsed_seconds > 0
    assert _travel(engine, "office", "drive-to-office").status == "completed"
    before = _purchase_facts(engine)
    _, rejected = _observed_action(engine, "resume-at-office", "report_status", report_request(name, summary="继续。"))
    assert rejected.status == "rejected" and rejected.error_code == "ACTIVITY_PRECONDITION_UNMET"
    home = next(item.name for item in engine.context.scenario.locations if item.location_id == "home")
    assert rejected.data["reason"] == f"Resume {name} at its start location {home}; it can be concluded elsewhere."
    assert rejected.data["next_step"] == f"Travel to {home}." and rejected.data["unmet"] == ["activity_location"]
    assert engine.context.state.activities[started.activity_id] == original
    assert _purchase_facts(engine) == before
    assert not active_activities(engine.context.state, 1) and engine.context.state.agents[1].location_id == "office"
