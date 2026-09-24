"""travel-time 分时系数：查表、结算、报价、快照一致性与存在性准入。"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from worldmock.catalog import load_catalog
from worldmock.life.map_data import load_map_dataset
from worldmock.life.models import (
    ActivityKind,
    ActivityStatus,
    StartActivityRequest,
    TravelMode,
)
from worldmock.life.body import minimum_energy_cost
from worldmock.footprints.models import EventDraft
from worldmock.life.scenario import parse_scenario_document
from worldmock.life.semantics import assemble_semantics, load_runtime_scenario
from tests.life.conftest import advance_next, context
from agentsociety2.society import DirectToolCall

ROOT = Path(__file__).resolve().parents[2]
SCENARIO = ROOT / "scenarios/white-collar-12-weeks.json"
CATALOG = ROOT / "catalog"
FACTORS = ROOT / "catalog/snapshots/traffic/weekday-hour-factors.json"
CALIBRATE = ROOT / "scripts/calibrate_traffic.py"


def _epoch(hour: int, minute: int = 0) -> int:
    """2026-01-05（周一，场景首日）场景时区的出发时刻。"""
    return int(datetime(2026, 1, 5, hour, minute,
                        tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())


@pytest.fixture(scope="module")
def table() -> dict:
    return json.loads(FACTORS.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def semantics():
    document, _ = parse_scenario_document(SCENARIO)
    return assemble_semantics(document, load_catalog(CATALOG), load_map_dataset(ROOT / "corpora", document.map_dataset))


def test_factor_lookup_matches_the_snapshot_table_for_every_hour(semantics, table) -> None:
    """driving 全 24 小时与快照系数表逐点一致；walking 恒为 1.0。"""
    factors = semantics.travel_time_profile.factors
    for hour in range(24):
        expected = float(table["hours"][f"{hour:02d}"])
        assert semantics.travel_time_factor("driving", _epoch(hour)) == expected
        assert factors[f"driving_{hour:02d}"] == expected
        assert semantics.travel_time_factor("walking", _epoch(hour)) == 1.0


def test_factor_ordering_peaks_offpeak_and_night(semantics, table) -> None:
    """正例：早高峰 > 午间平峰 > 夜间自由流 = 1.0；全局峰顶在 18 时。"""
    night = semantics.travel_time_factor("driving", _epoch(3))
    offpeak = semantics.travel_time_factor("driving", _epoch(14))
    morning = semantics.travel_time_factor("driving", _epoch(8))
    evening = semantics.travel_time_factor("driving", _epoch(18))
    assert night == 1.0 and float(table["hours"]["03"]) == 1.0
    assert morning > offpeak > night
    assert evening >= morning
    peak_hour = max(table["hours"], key=lambda key: float(table["hours"][key]))
    assert peak_hour == "18"


def test_same_route_duration_differs_between_0730_and_1400(semantics) -> None:
    """同一冻结路线：7:30 出发与 14:00 出发的受理时长不同且都 ≥ 自由流。"""
    route = next(item for item in semantics.scenario.routes
                 if item.origin_id == "home" and item.destination_id == "office"
                 and item.mode == TravelMode.DRIVING)
    morning = semantics.travel_planned_seconds("driving", route.duration_seconds, _epoch(7, 30))
    afternoon = semantics.travel_planned_seconds("driving", route.duration_seconds, _epoch(14))
    assert morning == math.ceil(route.duration_seconds * 1.45) == 314
    assert afternoon == math.ceil(route.duration_seconds * 1.18) == 255
    assert morning > afternoon > route.duration_seconds


def test_walking_duration_is_immune_to_the_factor(semantics) -> None:
    """步行不受系数影响：任意时段的计划时长等于冻结自由流时长。"""
    for hour in (0, 3, 8, 12, 18, 23):
        assert semantics.travel_planned_seconds("walking", 900, _epoch(hour)) == 900


def _stage_peak(engine) -> None:
    """把隔离测试世界移到早高峰并保持身体结算时钟一致。"""
    staged = engine.state.model_copy(deep=True)
    staged.current_time = _epoch(8)
    staged.pending_events = [event for event in staged.pending_events if event.due_at > staged.current_time]
    for agent_id, agent in staged.agents.items():
        staged.agents[agent_id] = agent.model_copy(
            update={"next_decision_at": staged.current_time, "body_settled_at": staged.current_time}, deep=True)
    staged.version += 1
    event = EventDraft(event_type="environment_event", occurred_at=staged.current_time,
                       visibility="system", source_kind="scenario", data={"kind": "test_peak_time"})
    engine.store.commit(staged.model_dump(mode="json"), [event])
    engine.state = staged


def _start_driving(engine, name: str):
    """只从本轮冻结观察选择办公室和驾车动作。"""
    ctx = context(engine, name)
    engine.begin_decision_round([ctx])
    observed = engine.observe(ctx)
    binding = next(row for row in observed["interaction_bindings"]
                   if row["kind"] == "location" and row["object_id"] == "office")
    assert any(action["label"] == "驾车前往" for action in binding["actions"])
    request = StartActivityRequest(targets=[binding["object"]], actions=["驾车前往"])
    engine.submit_action(DirectToolCall(ctx, f"action-{name}", "start_activity", request))
    result = engine.commit_decision_round(name)[0]
    return observed, result


def test_engine_start_travel_uses_frozen_factored_duration(engine_factory) -> None:
    """引擎采用本轮冻结路线时长，人物不填写自由流或报价数值。"""
    engine = engine_factory()
    _stage_peak(engine)
    route = next(item for item in engine.scenario.routes
                 if item.origin_id == "home" and item.destination_id == "office"
                 and item.mode == TravelMode.DRIVING)
    quote = engine.semantics.travel_planned_seconds(
        "driving", route.duration_seconds, engine.state.current_time)
    assert quote > route.duration_seconds
    observed, accepted = _start_driving(engine, "quoted-accepted")
    observed_quote = next(row for row in observed["routes"]
                          if row["destination_id"] == "office" and row["mode"] == "driving")
    assert observed_quote["duration_seconds"] == quote
    assert accepted.status.value == "in_progress"
    activity = engine.state.activities[accepted.activity_id]
    assert activity.planned_duration_seconds == quote
    assert activity.location_id == "home"
    assert activity.destination_id == "office"


def test_engine_travel_settles_energy_for_the_factored_duration(engine_factory) -> None:
    """路途能量按折算后时长结算（MET 路径），到达后地点更新。"""
    engine = engine_factory()
    _stage_peak(engine)
    route = next(item for item in engine.scenario.routes
                 if item.origin_id == "home" and item.destination_id == "office"
                 and item.mode == TravelMode.DRIVING)
    quote = engine.semantics.travel_planned_seconds(
        "driving", route.duration_seconds, engine.state.current_time)
    before = engine.state.agents[1].body.energy_kcal
    _, result = _start_driving(engine, "travel-energy")
    assert result.status.value == "in_progress"
    activity_id = result.activity_id
    while engine.state.activities[activity_id].status == ActivityStatus.ACTIVE:
        advance_next(engine)
    met = engine.semantics.body.activity_met["travel:driving"]
    expected = minimum_energy_cost(ActivityKind.TRAVEL, quote, engine.semantics.body, met)
    assert engine.state.agents[1].body.energy_kcal == pytest.approx(before - expected)
    assert engine.state.agents[1].location_id == "office"


def test_observation_quotes_the_factored_duration(engine_factory) -> None:
    """观察层路线报价：duration_seconds 为出发时刻折算值并附自由流时长与系数。"""
    engine = engine_factory()
    observed = engine.observe(context(engine, "quote-night"))
    hour = datetime.fromtimestamp(engine.state.current_time,
                                  ZoneInfo(engine.scenario.timezone)).hour
    driving = next(item for item in observed["routes"]
                   if item["destination_id"] == "office" and item["mode"] == "driving")
    walking = next(item for item in observed["routes"]
                   if item["destination_id"] == "office" and item["mode"] == "walking")
    factor = engine.semantics.travel_time_factor("driving", engine.state.current_time)
    assert driving["travel_time_factor"] == factor
    assert driving["duration_seconds"] == math.ceil(driving["free_flow_duration_seconds"] * factor)
    assert walking["duration_seconds"] == walking["free_flow_duration_seconds"]
    assert hour == 0  # 首轮在场景起点（夜间小时）折算生效


def test_calibrate_rerun_is_byte_identical() -> None:
    """标定脚本可重跑：锚点快照 → 系数表字节一致（--check 门禁）。"""
    completed = subprocess.run([sys.executable, str(CALIBRATE), "--check"],
                               capture_output=True, text=True, cwd=ROOT)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "byte-identical" in completed.stdout


def test_rule_branches_agree_with_the_snapshot_table(table) -> None:
    """规则文件 24+1 分支与系数表 JSON 数值一致（规则与快照不脱钩）。"""
    document, _ = parse_scenario_document(SCENARIO)
    factors = assemble_semantics(
        document, load_catalog(CATALOG), load_map_dataset(ROOT / "corpora", document.map_dataset)
    ).travel_time_profile.factors
    assert set(factors) == {"walking"} | {f"driving_{hour:02d}" for hour in range(24)}
    assert factors["walking"] == 1.0
    for hour in range(24):
        assert factors[f"driving_{hour:02d}"] == float(table["hours"][f"{hour:02d}"])


def test_admission_rejects_a_catalog_without_the_travel_time_profile(tmp_path) -> None:
    """存在性准入：目录缺少 travel-time 数值表时组装直接拒绝。"""
    from worldmock.errors import IntegrityError

    copied = tmp_path / "catalog"
    shutil.copytree(CATALOG, copied)
    (copied / "rules/travel-time.toml").unlink()
    with pytest.raises(IntegrityError, match="Catalog load failed"):
        load_runtime_scenario(SCENARIO, copied)
