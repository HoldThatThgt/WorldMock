"""工作、娱乐、身体边界与实验截断的专项行为测试。"""

from __future__ import annotations

from tests.data_paths import ENVIRONMENT_ROOT

import pytest

from worldmock.life.activities import (
    ActivityOrigin,
    advance_activity,
    start_activity,
    truncate_activity,
)
from worldmock.life import felt, projection
from worldmock.life.body import (
    ActivityFactors,
    BoundaryClock,
    _FeltProjection,
    _felt_band_boundaries,
    _meal_window_boundaries,
    next_body_boundary_seconds,
    next_shared_body_boundary_seconds,
    settle_shared_body,
)
from worldmock.life.models import (
    ActivityKind,
    ActivityStatus,
    AgentState,
    BodyState,
    ActivityStartParams,
)
from worldmock.life.world_setup import load_scenario



@pytest.fixture(scope="module")
def body_config():
    """读取正式场景中已经冻结的身体与工作工具参数。"""
    return load_scenario(ENVIRONMENT_ROOT / "scenarios/single-person.json")[0].body.model_copy(
        update={"mass_kg": 65})


def _agent(*, need: float = 0.5) -> AgentState:
    """@brief 构造具有明确身体初值的人物，不引入已删除的技能或口味字段。"""
    return AgentState(agent_id=1, body_settled_at=0, name='worker', location_id='office',
                      balance_account_id='agent:1', next_decision_at=0,
                      body=BodyState(energy_kcal=1600, sleep_pressure=0.0,
                                     sleep_deficit_hours=0, entertainment_need=need))


def _start(kind: ActivityKind, seconds: int, agent: AgentState, config, target: str):
    """用完整来源身份启动一个专项测试活动。"""
    request = ActivityStartParams(
        activity_type=kind,
        planned_duration_seconds=seconds,
        target_id=target,
    )
    origin = ActivityOrigin("action", "decision", "request-event")
    return start_activity(agent, request, 0, origin, config)


def test_work_progress_counts_actual_elapsed_seconds(body_config):
    """@brief 工作只按实际投入秒数累计，结算没有技能、笔记或有效工时折算。"""
    person, activity = _start(ActivityKind.WORK, 3600, _agent(), body_config, 'topic')
    result = advance_activity(activity, person, 3600, body_config)
    assert result.activity.elapsed_seconds == 3600
    assert 'effective_work_seconds' not in result.activity.model_dump()
    assert result.settlement is not None
    assert not hasattr(result.settlement, 'work_efficiency')


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
    """@brief 进食净增能量时，食欲降档是最早候选，整秒等于饥饿跨档扫描给出的时刻。"""
    near_emergency = BodyState(energy_kcal=401, sleep_pressure=0.0, sleep_deficit_hours=0, entertainment_need=0)
    eating = ActivityFactors(met=1.5, food_energy_kcal_per_hour=500)

    consume_seconds = next_body_boundary_seconds(
        near_emergency, ActivityKind.CONSUME, body_config, eating, BoundaryClock(12.0))


    # 401 kcal、12:00：食欲约 0.91（饿得难受）；进食每小时净增约 402.5 kcal，约 500 秒后降到「很饿」。
    net_gain = 500 - (body_config.basal_kcal_per_hour + 0.5 * body_config.mass_kg)

    def level(seconds: int) -> str:
        energy = 401 + net_gain * seconds / 3600
        appetite = felt.compute_appetite(energy, 12.0 + seconds / 3600)
        return felt.appetite_level(appetite, energy, body_config.emergency_threshold_kcal)

    assert felt.compute_appetite(401, 12.0) == pytest.approx(0.914, abs=1e-3)
    assert level(0) == "饿得难受"
    crossing = next(seconds for seconds in range(1, 3600) if level(seconds) != "饿得难受")
    assert level(crossing) == "很饿" and 450 <= crossing <= 550
    assert consume_seconds == crossing
    # 娱乐需要 0→0.3 档按清醒 0.08/h 增长的候选（13500 秒）仍由体感候选给出，只是晚于食欲降档。
    scan = _FeltProjection(near_emergency, -net_gain, BoundaryClock(12.0), body_config)
    assert round(0.3 / 0.08 * 3600) in _felt_band_boundaries(scan)


def _quiet(body_config):
    """娱乐需要不增长的身体配置：体感候选只剩困意、饥饿与餐时窗口。"""
    return body_config.model_copy(update={"entertainment_need_per_awake_hour": 0})


def _levels_after(body: BodyState, activities: tuple, config, clock: BoundaryClock, seconds: int) -> dict:
    """真实共同结算 seconds 秒后的体感分级（与观察、唤醒判定同一投影）。"""
    after = settle_shared_body(body, activities, seconds, config).after
    person = _agent().model_copy(update={"body": after}, deep=True)
    return projection.body_projection(person, (clock.hour_of_day + seconds / 3600) % 24,
                                      config, clock.meal_windows)["levels"]


def _crossing(config, body: BodyState, activities: tuple, clock: BoundaryClock) -> tuple[int, dict, dict]:
    """预测下一身体边界，并给出现在与边界时刻的真实分级；断言边界前一秒分级仍与现在相同。"""
    predicted = next_shared_body_boundary_seconds(body, activities, config, clock)
    assert predicted is not None and predicted > 0
    before = _levels_after(body, activities, config, clock, 0)
    assert _levels_after(body, activities, config, clock, predicted - 1) == before
    return predicted, before, _levels_after(body, activities, config, clock, predicted)


@pytest.mark.parametrize(("energy", "hour", "kind", "met", "expected"), [
    (760, 14.0, ActivityKind.WORK, 1.5, ("饿", "很饿")),
    (405, 8.0, ActivityKind.WORK, 1.5, ("很饿", "饿得难受")),
    (104, 8.0, ActivityKind.WAIT, 1.0, ("饿得难受", "饿得发慌")),
])
def test_hunger_upward_crossing_is_not_later_than_actual(body_config, energy, hour, kind, met, expected) -> None:
    """饥饿升档（公式档、400 与 100 千卡保底档）的候选恰为真实结算首次变档的整秒。"""
    config = _quiet(body_config)
    body = BodyState(energy_kcal=energy, sleep_pressure=0.2, sleep_deficit_hours=0, entertainment_need=0,
                     social_need=1.0)
    _, before, at = _crossing(config, body, ((kind, ActivityFactors(met)),), BoundaryClock(hour))
    assert (before["appetite"], at["appetite"]) == expected


def test_hunger_downward_crossing_at_meal_window_end(body_config) -> None:
    """午餐窗口结束时餐时项跳变，饥饿降档；窗口终点既是扫描结果也是餐时候选。"""
    config = _quiet(body_config)
    body = BodyState(energy_kcal=790, sleep_pressure=0.2, sleep_deficit_hours=0, entertainment_need=0,
                     social_need=1.0)
    clock = BoundaryClock(13 + 20 / 60, felt.MealWindows(lunch=(11.5, 13.5)))
    predicted, before, at = _crossing(config, body, ((ActivityKind.WAIT, ActivityFactors(1.0)),), clock)
    assert predicted == 600
    assert (before["appetite"], at["appetite"]) == ("很饿", "饿")


def test_hunger_crossing_reverted_by_meal_window_end_is_not_skipped(body_config) -> None:
    """午餐窗口终点前 231 秒升档、终点处餐时项跳变又降回：预测恰为升档的整秒，不推迟到窗口终点。

    升档与降回都落在同一个 900 秒步内，只在步点比较分级时看不到这一段；窗口终点的前一秒与那一秒作步点后，
    扫描在升档所在的步内二分到整秒（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第三章「让人物趁早吃」、第八章「节律」）。
    """
    config = _quiet(body_config)
    body = BodyState(energy_kcal=661.448507, sleep_pressure=0.2296, chronotype_midsleep=3.0,
                     sleep_deficit_hours=0, entertainment_need=0, social_need=1.0)
    clock = BoundaryClock(13 + 42 / 3600, felt.MealWindows(lunch=(11.5, 13.5), dinner=(18.0, 20.0)))
    activities = ((ActivityKind.WAIT, ActivityFactors(1.3)),)
    predicted, before, at = _crossing(config, body, activities, clock)
    assert predicted == 1527
    assert (before["appetite"], at["appetite"]) == ("很饿", "饿得难受")
    lunch_end = min(_meal_window_boundaries(clock))
    assert lunch_end == 1758
    assert _levels_after(body, activities, config, clock, lunch_end - 1)["appetite"] == "饿得难受"
    assert _levels_after(body, activities, config, clock, lunch_end)["appetite"] == "很饿"


def test_chat_and_call_are_predicted_but_sleep_and_game_are_not(body_config) -> None:
    """交谈、通话期间同样预测饥饿跨档（按社交 MET）；睡眠、游戏不另做预测。"""
    config = _quiet(body_config)
    body = BodyState(energy_kcal=405, sleep_pressure=0.2, sleep_deficit_hours=0, entertainment_need=0,
                     social_need=1.0)
    clock = BoundaryClock(8.0)
    burn = config.basal_kcal_per_hour + 0.5 * config.mass_kg
    chat = next_shared_body_boundary_seconds(body, ((ActivityKind.CHAT, ActivityFactors(1.5)),), config, clock)
    assert chat == int(5 * 3600 / burn) + 1
    call = next_shared_body_boundary_seconds(body, ((ActivityKind.CALL, ActivityFactors(1.3)),), config, clock)
    assert call is not None and call > chat
    assert next_shared_body_boundary_seconds(body, ((ActivityKind.SLEEP, ActivityFactors(1.0)),), config, clock) is None
    app = next_shared_body_boundary_seconds(body, ((ActivityKind.USE_APP, ActivityFactors(1.0)),), config, clock)
    assert app is not None and app > 0


def test_meal_window_edges_are_boundary_candidates(body_config) -> None:
    """午餐、晚餐窗口的起止时刻都是候选：取严格晚于现在的下一次到来。"""
    windows = felt.MealWindows(lunch=(11.5, 13.5), dinner=(18.0, 20.0))
    assert sorted(_meal_window_boundaries(BoundaryClock(21.0, windows))) == [52200, 59400, 75600, 82800]
    assert sorted(_meal_window_boundaries(BoundaryClock(11.5, windows))) == [7200, 23400, 30600, 86400]
    assert _meal_window_boundaries(BoundaryClock(11.0)) == []
    config = _quiet(body_config)
    body = BodyState(energy_kcal=2400, sleep_pressure=0.05, sleep_deficit_hours=0, entertainment_need=0,
                     social_need=1.0)
    for hour, seconds in ((11.0, 1800), (12.0, 5400), (17.0, 3600), (19.0, 3600)):
        clock = BoundaryClock(hour, windows)
        assert next_body_boundary_seconds(body, ActivityKind.WAIT, config, None, clock) == seconds
