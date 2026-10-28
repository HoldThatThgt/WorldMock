"""真实引擎的观察分件：observe 返回它构建的观察，存储层读回逐字节相同；过去一天的那段随活动阶段与改名而变。"""

from __future__ import annotations

import json
from datetime import datetime

from tests.life.conftest import advance_next, conclude_request, context, do_at, sleep, stage_day_plan
from tests.life.environment_runtime import act, finish_activity, observe_only
from tests.life.observation_record import assert_observations_round_trip, record_observations
from worldmock.state.planning.schedule import PlanGoal


def _round_trip(engine, recorded) -> None:
    """这次运行的每条观察都与构建时逐字节相同，分段按活动开始的场景本地日期。"""
    started = {key: item.started_at for key, item in engine.context.state.activities.items()}
    assert_observations_round_trip(engine.context.store, recorded, started, engine.context.config.timezone)


def _row(observed: dict, activity_id: str) -> dict:
    """观察里指向这项活动的那一行绑定；观察没换回原表（行是分件引用字符串）时断言失败。"""
    rows = [row for row in observed["activity_bindings"]
            if isinstance(row, dict) and row["activity_id"] == activity_id]
    assert len(rows) == 1, observed["activity_bindings"]
    return rows[0]


def _stored_refs(engine, observed: dict) -> list[str]:
    """这份观察存下的正文里 activity_bindings 的分件引用。"""
    row = engine.context.store.connection.execute("SELECT body FROM events WHERE event_id=?",
                                                  (observed["observation_event_id"],)).fetchone()
    return json.loads(row[0])["data"]["activity_bindings"]


def test_observe_returns_what_it_built_and_the_store_reads_it_back(engine_factory, monkeypatch):
    recorded = record_observations(monkeypatch)
    engine = engine_factory()
    stage_day_plan(engine)
    _, rest = act(engine, "rest", do_at("家", "测试休息"))
    assert rest.status == "in_progress", rest.error_code
    observed = observe_only(engine, "after-rest")
    assert _row(observed, rest.activity_id)["phase"]
    assert len(recorded) == 2 and recorded[0][1]["activity_bindings"] == []
    _round_trip(engine, recorded)


def test_a_past_day_part_follows_the_activity_phase_after_midnight(engine_factory, monkeypatch):
    recorded = record_observations(monkeypatch)

    def evening(persona, config):
        config["start_time"] = datetime.fromisoformat("2026-01-05T20:00:00+08:00")
        config["stop_time"] = datetime.fromisoformat("2026-01-07T20:00:00+08:00")

    engine = engine_factory(mutate=evening)
    stage_day_plan(engine, submitted=True)
    _, night = act(engine, "evening", sleep(300))
    assert night.status == "in_progress", night.error_code
    while engine.context.state.activities[night.activity_id].status == "active":
        advance_next(engine)
    woke = context(engine, "woke")
    first = engine.observe(woke)
    name = _row(first, night.activity_id)["name"]
    act(engine, "conclude-night", conclude_request(name), "conclude_activity")
    _, rest = act(engine, "night-rest", do_at("家", "测试休息"))
    assert rest.status == "in_progress", rest.error_code
    later = context(engine, "later")
    second = engine.observe(later)
    assert _row(first, night.activity_id)["phase"] != _row(second, night.activity_id)["phase"]
    [frozen] = engine.context.store.read_decision_observations(1, later.decision_id,
                                                               engine.context.state.current_time)
    assert _row(frozen.data, night.activity_id) == _row(second, night.activity_id)
    assert _row(frozen.data, rest.activity_id) == _row(second, rest.activity_id)
    [evening_part] = _stored_refs(engine, first)
    assert len(_stored_refs(engine, second)) == 2 and _stored_refs(engine, second)[0] != evening_part
    _round_trip(engine, recorded)


def test_a_new_goal_renames_an_older_activity_in_its_day_part(engine_factory, monkeypatch):
    recorded = record_observations(monkeypatch)
    engine = engine_factory()
    stage_day_plan(engine)
    _, rest = act(engine, "rest", do_at("家", "测试休息"))
    finish_activity(engine, rest, "rest")
    before = context(engine, "before-goal")
    first = engine.observe(before)
    name = _row(first, rest.activity_id)["name"]
    stage_day_plan(engine, goals=(PlanGoal(intent_kind="休息", goal=name,
                                           registered_at=engine.context.state.current_time),))
    after = context(engine, "after-goal")
    second = engine.observe(after)
    [frozen] = engine.context.store.read_decision_observations(1, after.decision_id,
                                                               engine.context.state.current_time)
    assert _row(frozen.data, rest.activity_id)["name"] == name + "（第二次）"
    assert _row(frozen.data, rest.activity_id) == _row(second, rest.activity_id)
    [before_part], [after_part] = _stored_refs(engine, first), _stored_refs(engine, second)
    assert before_part != after_part
    _round_trip(engine, recorded)
