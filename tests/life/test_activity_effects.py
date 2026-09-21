"""工作、娱乐、身体边界与实验截断的专项行为测试。"""

from __future__ import annotations

import pytest

from worldmock.life.activities import (
    ActivityOrigin,
    advance_activity,
    start_activity,
    truncate_activity,
)
from worldmock.life.body import ActivityFactors, next_body_boundary_seconds
from worldmock.life.models import (
    ActivityKind,
    ActivityStatus,
    AgentState,
    BodyState,
    StartBasicActivityRequest,
    StartActivityRequest,
    StartTargetedActivityRequest,
    StartTravelRequest,
    TravelMode,
)
from worldmock.life.scenario import load_scenario

from tests.life.conftest import advance_next, submit_round, travel_quote


@pytest.fixture(scope="module")
def body_config():
    """读取正式场景中已经冻结的身体与工作工具参数。"""
    return load_scenario("scenarios/white-collar-12-weeks.json")[0].body


def _agent(skill: float = 0.8, *, notes: bool = False, need: float = 0.5) -> AgentState:
    """构造具有明确工作技能、工具效果和娱乐需要的人物。"""
    effects = {"work_notes"} if notes else set()
    return AgentState(
        agent_id=1,
        body_settled_at=0,
        name="worker",
        location_id="office",
        balance_account_id="agent:1",
        skill_levels={"meeting_analysis": skill},
        taste_weights={"content": 0.7},
        active_effects=effects,
        next_decision_at=0,
        body=BodyState(energy_kcal=1600, sleep_pressure=0.0, sleep_deficit_hours=0, entertainment_need=need),
    )


def _start(kind: ActivityKind, seconds: int, agent: AgentState, config, target: str):
    """用完整来源身份启动一个专项测试活动。"""
    request = StartActivityRequest(
        activity_type=kind,
        planned_duration_seconds=seconds,
        target_id=target,
    )
    origin = ActivityOrigin("action", "decision", "request-event")
    return start_activity(agent, request, 0, origin, config)


@pytest.mark.parametrize(
    ("skill", "notes", "expected"),
    [(0.6, False, 2160), (0.8, False, 2880), (0.8, True, 3168)],
)
def test_work_progress_uses_skill_and_actual_notes_effect(
    body_config,
    skill: float,
    notes: bool,
    expected: float,
) -> None:
    """同一小时工作按实际技能和已生效工作笔记产生不同有效秒数。"""
    person, activity = _start(ActivityKind.WORK, 3600, _agent(skill, notes=notes), body_config, "task")
    activity.data.update(
        required_skill="meeting_analysis",
        skill_level=skill,
        work_notes_multiplier=body_config.work_notes_multiplier,
    )

    result = advance_activity(activity, person, 3600, body_config)

    assert result.activity.effective_work_seconds == pytest.approx(expected)
    assert result.settlement is not None
    assert result.settlement.work_efficiency == pytest.approx(expected / 3600)


def test_repeat_watch_uses_content_rate_and_exposure_count(body_config) -> None:
    """内容自身效果参与结算，第二次接触按次数降低相同时长的缓解。"""
    person, first = _start(ActivityKind.WATCH, 60, _agent(need=0.8), body_config, "content")
    first.data.update(entertainment_relief_per_hour=0.5, exposure_count=1)
    first_result = advance_activity(first, person, 60, body_config)
    repeated_person, second = _start(
        ActivityKind.WATCH,
        60,
        _agent(need=0.8),
        body_config,
        "content",
    )
    second.data.update(entertainment_relief_per_hour=0.5, exposure_count=2)

    second_result = advance_activity(second, repeated_person, 60, body_config)

    assert first_result.agent.body.entertainment_need == pytest.approx(0.8 - 0.35 / 60)
    assert second_result.agent.body.entertainment_need == pytest.approx(0.8 - 0.175 / 60)


def test_paused_activity_is_truncated_at_experiment_end(body_config) -> None:
    """暂停活动在实验终点保留进度并成为明确截断终态。"""
    person, activity = _start(ActivityKind.WAIT, 600, _agent(), body_config, "wait")
    person = person.model_copy(update={"current_activity_id": None}, deep=True)
    activity = activity.model_copy(
        update={"status": ActivityStatus.PAUSED, "boundary_type": "paused"},
        deep=True,
    )

    activity, person = truncate_activity(activity, person, 300)

    assert activity.status == ActivityStatus.TRUNCATED
    assert activity.ended_at == 300
    assert activity.elapsed_seconds == 0
    assert activity.boundary_type == "experiment_end"
    assert activity.interruption_reason == "experiment_end"
    assert person.current_activity_id is None


def test_body_boundary_predicts_first_energy_threshold_second(body_config) -> None:
    """能量从401下降时在首个严格低于400的整秒产生边界。"""
    body = BodyState(energy_kcal=401, sleep_pressure=0.0, sleep_deficit_hours=0, entertainment_need=0)

    seconds = next_body_boundary_seconds(
        body,
        ActivityKind.WAIT,
        body_config,
        ActivityFactors(met=1.3),
    )

    assert seconds == 43


def test_body_boundary_uses_curve_node_and_food_net_energy(body_config) -> None:
    """工作效率节点精确分段，进食净增能量不会伪造紧急下降边界。"""
    near_curve = BodyState(energy_kcal=1600, sleep_pressure=0.0, sleep_deficit_hours=7.9, entertainment_need=0)
    near_emergency = BodyState(energy_kcal=401, sleep_pressure=0.0, sleep_deficit_hours=0, entertainment_need=0)

    curve_seconds = next_body_boundary_seconds(
        near_curve,
        ActivityKind.WORK,
        body_config,
        ActivityFactors(met=1.5),
    )
    consume_seconds = next_body_boundary_seconds(
        near_emergency,
        ActivityKind.CONSUME,
        body_config,
        ActivityFactors(met=1.5, food_energy_kcal_per_hour=500),
        hour_of_day=12.0,
    )

    # 修订合同：不足账累积率 = need/(24-need)（7.5h 需要量即 7.5/16.5/h），
    # 体感分级跨越候选（娱乐 0→0.3 档按 0.08/h）参与下一边界预测。
    deficit_rate = 7.5 / 16.5
    assert curve_seconds == round((8 - 7.9) / deficit_rate * 3600)
    entertainment_crossing = round(0.3 / 0.08 * 3600)
    assert consume_seconds == entertainment_crossing


def test_engine_watch_uses_frozen_content_rate_and_preference(engine_factory) -> None:
    """真实 LifeEngine 结算路径使用内容效果、口味权重和实际接触时长。"""
    engine = engine_factory()
    # wait 前导 900s：边等边刷后娱乐需要 ≈0.0075（0.08−0.05 每小时净累积
    # ×0.25h），高于 60s 观看的未钳制缓解量 0.3×0.7×(60/3600)=0.0035，
    # 保证断言观测的是内容费率×偏好的未触底 drain 而非钳到 0 的退化值。
    wait = StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=900)
    submit_round(engine, {1: ("start_basic_activity", wait)}, "watch-need")
    advance_next(engine)
    agent = engine.state.agents[1]
    before = agent.body.entertainment_need
    item = engine.rules.content("sintel-en-subtitles")
    # docs/dev/SPEC-life-realism.md「画像编译」：结算偏好源=taste_weights（卡片口味 x genre 确定性权重）；
    # 引擎路径在 PersonaCard 入库前为空映射，经 staged 提交注入权重验证结算链路。
    from worldmock.footprints.models import EventDraft

    staged = engine.state.model_copy(deep=True)
    staged.agents[1] = staged.agents[1].model_copy(
        update={"taste_weights": {item.item_id: 0.7}}, deep=True)
    staged.version += 1
    engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time,
        visibility="system", data={"kind": "test_seed_watch_taste"})])
    engine.state = staged
    preference = engine.state.agents[1].taste_weights[item.item_id]
    request = StartTargetedActivityRequest(
        activity_type="watch",
        planned_duration_seconds=60,
        target_id=item.item_id,
        playback_speed=1,
    )

    _, results = submit_round(engine, {1: ("start_targeted_activity", request)}, "watch-real")
    advance_next(engine)

    activity = engine.state.activities[results[0].activity_id]
    expected = before + (engine.semantics.body.entertainment_need_per_awake_hour
                         - item.entertainment_relief_per_hour * preference) / 60
    assert activity.data["entertainment_relief_per_hour"] == item.entertainment_relief_per_hour
    assert activity.data["exposure_count"] == 1
    assert engine.state.agents[1].body.entertainment_need == pytest.approx(expected)


def test_engine_work_progress_uses_frozen_skill_without_unused_tool(engine_factory) -> None:
    """真实 LifeEngine 工作推进使用人物技能，未生效工具不乘场景倍率。"""
    engine = engine_factory()
    sleep = StartBasicActivityRequest(activity_type="sleep", planned_duration_seconds=32_400)
    _, sleep_results = submit_round(engine, {1: ("start_basic_activity", sleep)}, "sleep-to-work")
    while engine.state.activities[sleep_results[0].activity_id].status == ActivityStatus.ACTIVE:
        advance_next(engine)
    travel = StartTravelRequest(
        planned_duration_seconds=travel_quote(engine, "driving", 216),
        destination_id="office",
        travel_mode=TravelMode.DRIVING,
    )
    _, travel_results = submit_round(engine, {1: ("start_travel", travel)}, "travel-to-work")
    while engine.state.activities[travel_results[0].activity_id].status == ActivityStatus.ACTIVE:
        advance_next(engine)
    work = StartTargetedActivityRequest(
        activity_type="work",
        planned_duration_seconds=3600,
        target_id="task-2026-01-05",
        playback_speed=1,
    )
    _, work_results = submit_round(engine, {1: ("start_targeted_activity", work)}, "work-real")

    advance_next(engine)

    activity = engine.state.activities[work_results[0].activity_id]
    assert activity.elapsed_seconds == engine.scenario.activity_feedback_seconds
    assert activity.data["skill_level"] == 0.8
    assert activity.data["work_notes_multiplier"] == 1.1
    assert activity.effective_work_seconds == pytest.approx(activity.elapsed_seconds * 0.8)
