"""下限缓解与累积口径（docs/dev/SPEC-dayrun-fidelity.md 1.2，裁决 D3、S1）。

偏好只调幅度、不决定有无：实际缓解用有效偏好 f + (1 − f)·w（f 取场景 body 的下限偏好，缺省 0.5），
全部排序仍用原始口味权重；有缓解的活动进行时不再累积娱乐需要，共同区间与单活动两条结算路径净效果相同，
引擎的边界预测也按同一口径（观看时没有娱乐升档边界）；游戏与内容同一口味算法，没有题材声明的游戏权重为零、按下限缓解。
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from agentsociety2.society import DirectToolCall

from tests.life.conftest import advance_next, bring_to_window, context, stage_day_plan, start_request, wake_by_mail
from tests.profile.conftest import make_card
from worldmock.footprints.models import EventDraft
from worldmock.life import felt
from worldmock.life.activities import (
    ActivityOrigin,
    activity_factors,
    advance_activity,
    lifestyle_relief_rates,
    relief_preference,
    shared_activity_factors,
    start_activity,
)
from worldmock.life.body import last_flows, settle_shared_body
from worldmock.life.candidates import CandidateEntry, CandidateInputs, LocationView, recall
from worldmock.life.models import ActivityKind, ActivityStartParams, AgentState, BodyState
from worldmock.life.scenario import load_scenario
from worldmock.profile.models import Tastes
from worldmock.runtime import _agent_specs, _content_genre_catalog

_FORMAL = Path("scenarios/white-collar-12-weeks.json")
#: 规格 1.2 的数值核对口径：内容缓解速率 0.30/小时（正式场景的网文条目），清醒累积 0.08/小时（目录），f = 0.5。
_NOVEL = "novel-0002"
_NOVEL_RATE = 0.3


@pytest.fixture(scope="module")
def body_config():
    """正式场景组装后的身体参数：场景没有声明下限偏好，取缺省 0.5。"""
    return load_scenario(_FORMAL)[0].body


@pytest.fixture(autouse=True)
def _clean_flow_buffer():
    """按测试隔离全局身体分录缓冲。"""
    last_flows()
    yield
    last_flows()


def _agent(need: float, weights: dict[str, float]) -> AgentState:
    """带给定娱乐需要与口味权重的人物。"""
    return AgentState(agent_id=1, name="Lin", location_id="home", balance_account_id="agent:1",
                      body_settled_at=0, next_decision_at=0, taste_weights=weights,
                      body=BodyState(energy_kcal=1600, sleep_pressure=0.0, sleep_deficit_hours=0,
                                     entertainment_need=need))


def _running(agent: AgentState, kind: ActivityKind, target: str | None, config, data: dict | None = None):
    """经领域入口开始一段三小时的活动，并写入受理时由规则冻结的缓解证据。"""
    request = ActivityStartParams(activity_type=kind, planned_duration_seconds=10_800, target_id=target)
    person, activity = start_activity(agent, request, 0, ActivityOrigin("a", "d", "e"), config)
    activity.data.update(data or {})
    return person, activity


def _watch(agent: AgentState, config, rate: float = _NOVEL_RATE, exposures: int = 1):
    """观看一条内容（条目费率与接触次数）。"""
    return _running(agent, ActivityKind.WATCH, _NOVEL, config,
                    {"entertainment_relief_per_hour": rate, "exposure_count": exposures})


# ---------------------------------------------------------------------------
# 有效偏好：下限、单调、w = 1 不变
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("floor", [0.01, 0.3, 0.5, 0.7, 0.99])
def test_relief_preference_has_floor_at_zero_identity_at_one_and_is_strictly_monotone(floor: float) -> None:
    """f + (1 − f)·w：w = 0 取 f，w = 1 取 1，对 w 严格单调；低于 f 的权重不被拉成并列（不是 max(w, f)）。"""
    weights = [step / 20 for step in range(21)]
    values = [relief_preference(weight, floor) for weight in weights]
    assert values[0] == pytest.approx(floor)
    assert values[-1] == pytest.approx(1.0)
    assert all(low < high for low, high in zip(values, values[1:]))
    below = [relief_preference(weight, floor) for weight in weights if weight < floor]
    assert len(set(below)) == len(below)


def test_disliked_content_is_relieved_at_the_floor_without_awake_accrual(body_config) -> None:
    """口味权重为 0 的内容按 f 缓解：速率 = 费率 × f ÷ 接触次数；共同结算一小时只降不升。"""
    assert body_config.entertainment_relief_preference_floor == 0.5
    agent, activity = _watch(_agent(0.8, {}), body_config)
    factors = shared_activity_factors(activity, agent, body_config)
    assert factors.entertainment_relief_per_hour == pytest.approx(_NOVEL_RATE * 0.5)
    settled = settle_shared_body(agent.body, ((activity.kind, factors),), 3600, body_config)
    assert settled.after.entertainment_need == pytest.approx(0.8 - _NOVEL_RATE * 0.5)
    _, repeated = _watch(_agent(0.8, {}), body_config, exposures=3)
    assert activity_factors(repeated, agent, body_config).entertainment_relief_per_hour == pytest.approx(
        _NOVEL_RATE * 0.5 / 3)


@pytest.mark.parametrize("kind", [ActivityKind.WATCH, ActivityKind.PLAY_GAME])
def test_actual_relief_rises_strictly_with_taste_weight(body_config, kind: ActivityKind) -> None:
    """引擎实际缓解随口味权重严格单调：w = 0 为下限 f × 基础速率，w = 1 为基础速率本身。"""
    base = _NOVEL_RATE if kind == ActivityKind.WATCH else body_config.entertainment_relief_per_hour["play_game"]
    target = _NOVEL if kind == ActivityKind.WATCH else "textworld-1201"
    data = {"entertainment_relief_per_hour": _NOVEL_RATE, "exposure_count": 1} if kind == ActivityKind.WATCH else {}
    reliefs = []
    for weight in [step / 10 for step in range(11)]:
        agent, activity = _running(_agent(0.9, {target: weight}), kind, target, body_config, data)
        reliefs.append(activity_factors(activity, agent, body_config).entertainment_relief_per_hour)
    assert reliefs[0] == pytest.approx(base * 0.5)
    assert reliefs[-1] == pytest.approx(base)
    assert all(low < high for low, high in zip(reliefs, reliefs[1:]))


# ---------------------------------------------------------------------------
# 游戏：与内容同一口味算法，没有题材声明的走下限（裁决 S1）
# ---------------------------------------------------------------------------

def _compiled(genres: tuple[str, ...]):
    """确定性画像编译桩：只给定卡片口味题材，不触发真实模型调用。"""
    card = make_card(tastes=Tastes(content_genres=genres, food_preferences=(), media=()))
    return lambda agent: card


def test_runtime_computes_game_weights_with_the_content_algorithm() -> None:
    """创建运行的人物档案为每个游戏按 game_id 计算口味权重：精确交集 ÷ 条目题材数，没有题材声明的为 0。"""
    scenario, _ = load_scenario(_FORMAL)
    games = [item.game_id for item in scenario.games]
    genres = {games[0]: ("解谜", "冒险"), games[1]: ("解谜", "动作")}
    specs = _agent_specs(scenario, persona_compiler=_compiled(("解谜", "冒险")), content_genres=genres)
    weights = specs[0]["profile"]["taste_weights"]
    assert set(games) <= set(weights)
    assert weights[games[0]] == 1.0 and weights[games[1]] == 0.5
    assert all(weights[game] == 0.0 for game in games[2:])
    assert set(weights) == {item.item_id for item in scenario.contents} | set(games)


def test_games_without_genre_declarations_fall_to_the_floor(body_config) -> None:
    """正式场景的 TextWorld 游戏在题材表里没有声明：权重为 0，实际缓解 = f × 目录游戏费率，仍大于 0。"""
    scenario, _ = load_scenario(_FORMAL)
    genres = _content_genre_catalog(_FORMAL, scenario)
    specs = _agent_specs(scenario, persona_compiler=_compiled(("解谜",)), content_genres=genres)
    weights = specs[0]["profile"]["taste_weights"]
    game = scenario.games[0].game_id
    assert game not in genres and weights[game] == 0.0
    assert all(weights[item.game_id] == 0.0 for item in scenario.games)
    agent, activity = _running(_agent(0.9, weights), ActivityKind.PLAY_GAME, game, body_config)
    relief = activity_factors(activity, agent, body_config).entertainment_relief_per_hour
    assert relief == pytest.approx(0.5 * body_config.entertainment_relief_per_hour["play_game"]) and relief > 0


# ---------------------------------------------------------------------------
# 排序仍用原始权重：零权重照旧排在最后
# ---------------------------------------------------------------------------

def test_zero_weight_sorts_last_although_it_is_relieved_at_the_floor(body_config) -> None:
    """排序键取原始口味权重（定点整数），零权重排在全部非零之后；它的实际缓解却按下限 f 大于 0。"""
    weights = {"novel-a": 0.0, "novel-b": 0.2, "novel-c": 0.05}
    entries = tuple(CandidateEntry(object_id=item, kind="content", bucket="webnovel_excerpt",
                                   minimum_seconds=600, maximum_seconds=3600) for item in sorted(weights))
    page = recall(CandidateInputs(
        location=LocationView(location_id="home", location_kinds=frozenset({"home"}), opening_status="open",
                              minimum_seconds=60, maximum_seconds=14_400),
        locations={}, sources={"browsed": entries}, local_hour=21.0, current_time=0,
        window_end_time=86_400, taste_weights=weights, attention_priors={"webnovel_excerpt": 0.5}))
    assert [(row.object_id, row.taste_weight) for row in page.retained] == [
        ("novel-b", 2000), ("novel-c", 500), ("novel-a", 0)]
    reliefs = {}
    for item, weight in weights.items():
        agent, activity = _watch(_agent(0.9, {_NOVEL: weight}), body_config)
        reliefs[item] = activity_factors(activity, agent, body_config).entertainment_relief_per_hour
    assert reliefs["novel-a"] == pytest.approx(_NOVEL_RATE * 0.5)
    assert 0 < reliefs["novel-a"] < reliefs["novel-c"] < reliefs["novel-b"]


# ---------------------------------------------------------------------------
# 累积口径：两条结算路径对同一活动给出相同净效果
# ---------------------------------------------------------------------------

_PATH_CASES = {
    "watch-disliked": (ActivityKind.WATCH, _NOVEL, 0.0, 1),
    "watch-partly-liked-repeat": (ActivityKind.WATCH, _NOVEL, 0.6, 3),
    "watch-liked": (ActivityKind.WATCH, _NOVEL, 1.0, 1),
    "game-no-genre": (ActivityKind.PLAY_GAME, "textworld-1201", 0.0, None),
    "lifestyle": (ActivityKind.LIFESTYLE, "jogging", 0.0, None),
    "wait": (ActivityKind.WAIT, None, 0.0, None),
}


@pytest.mark.parametrize("case", sorted(_PATH_CASES))
@pytest.mark.parametrize("need", [0.0, 0.03, 0.5, 0.97, 1.0])
@pytest.mark.parametrize("seconds", [60, 3600, 10_800])
def test_single_and_shared_paths_settle_the_same_net_effect(body_config, case: str, need: float,
                                                            seconds: int) -> None:
    """单活动路径（advance_activity → settle_body）与共同区间路径（settle_shared_body）净效果相同：
    有缓解时只降不升（触底为 0），没有缓解时按清醒累积（触顶为 1）。"""
    kind, target, weight, exposures = _PATH_CASES[case]
    data = ({"entertainment_relief_per_hour": _NOVEL_RATE, "exposure_count": exposures}
            if exposures is not None else None)
    agent, activity = _running(_agent(need, {target: weight} if target else {}), kind, target, body_config, data)
    single = advance_activity(activity, agent, seconds, body_config).agent.body.entertainment_need
    factors = shared_activity_factors(activity, agent, body_config)
    shared = settle_shared_body(agent.body, ((kind, factors),), seconds, body_config).after.entertainment_need
    assert single == pytest.approx(shared, abs=1e-12)
    relief = factors.entertainment_relief_per_hour
    hours = seconds / 3600
    expected = (max(0.0, need - relief * hours) if relief > 0
                else min(1.0, need + body_config.entertainment_need_per_awake_hour * hours))
    assert shared == pytest.approx(expected, abs=1e-12)
    if kind == ActivityKind.LIFESTYLE:
        assert relief == pytest.approx(lifestyle_relief_rates("jogging")[0]) and relief > 0


def test_parallel_relieving_activities_add_up_in_the_shared_settlement(body_config) -> None:
    """共同身体结算先汇总各活动的缓解再限幅（同一身体通道先汇总，docs/dev/SPEC-dayrun-behavior.md「共同时间与身体结算」）：
    观看不喜欢的网文（按下限 f 缓解）与慢跑（生活活动 effects 的缓解）同一区间进行时，一小时按两者缓解之和下降，不取
    其中较大的一项，也不叠加清醒累积。"""
    agent = _agent(0.9, {})
    _, watching = _watch(agent, body_config)
    _, jogging = _running(agent, ActivityKind.LIFESTYLE, "jogging", body_config)
    parts = tuple((item.kind, shared_activity_factors(item, agent, body_config)) for item in (watching, jogging))
    reliefs = [factors.entertainment_relief_per_hour for _, factors in parts]
    assert reliefs == [pytest.approx(_NOVEL_RATE * 0.5), pytest.approx(lifestyle_relief_rates("jogging")[0])]
    settled = settle_shared_body(agent.body, parts, 3600, body_config)
    assert settled.after.entertainment_need == pytest.approx(0.9 - sum(reliefs)) and 0.9 - sum(reliefs) > 0
    flows = [entry for entry in last_flows() if entry.resource == "entertainment_need"]
    assert [(entry.direction, entry.amount) for entry in flows] == [("drain", pytest.approx(sum(reliefs)))]


# ---------------------------------------------------------------------------
# 数值核对：经引擎的共同身体结算，从 1.0 降到「有点想放松」
# ---------------------------------------------------------------------------

def _stage_agent(engine, need: float, weights: dict[str, float]) -> None:
    """把人物 1 的娱乐需要与口味权重落盘为真实状态（版本 +1 并留痕）。"""
    staged = engine.state.model_copy(deep=True)
    agent = staged.agents[1]
    staged.agents[1] = agent.model_copy(update={
        "taste_weights": weights, "body": agent.body.model_copy(update={"entertainment_need": need})}, deep=True)
    staged.version += 1
    engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time,
        visibility="system", data={"kind": "test_relief_floor_staged"})])
    engine.state = staged


def _start_watching(engine, item_id: str) -> str:
    """真实决策轮：按本轮列出的方式开始观看一条内容，返回受理的活动标识（先让它成为内容分区最久未呈现的对象，
    换新名额把它放进本轮窗口）。"""
    bring_to_window(engine, (item_id,))
    round_id = f"watch-{item_id}"
    decision = context(engine, round_id)
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    binding = next(row for row in observed["interaction_bindings"]
                   if row["kind"] == "content" and row["object_id"] == item_id)
    (action,) = binding["actions"]
    request = start_request(targets=[binding["object"]], actions=[action["verb"]], ways=[action["label"]])
    engine.submit_action(DirectToolCall(decision, f"action-{round_id}", "start_activity", request))
    result = engine.commit_decision_round(round_id)[0]
    assert result.status == "in_progress", result.error_code
    return result.activity_id


def _need_after(engine, hours: float) -> tuple[float, dict]:
    """经引擎的共同身体结算（LifeEngine._advance_activities）把不间断的观看结算 hours 小时。

    区间内活动集合不变时结算对时长线性，与引擎在中途边界分段结算的结果相同；返回娱乐需要与 body_settlement 正文。
    """
    target = engine.state.current_time + round(hours * 3600)
    staged = engine.state.model_copy(update={"current_time": target}, deep=True)
    drafts, _ = engine._advance_activities(staged, target)
    event = next(draft for draft in drafts if draft.data.get("kind") == "body_settlement" and draft.agent_id == 1)
    return staged.agents[1].body.entertainment_need, event.data


@pytest.mark.parametrize("weight", [0.0, 1.0], ids=["disliked", "liked"])
def test_committed_body_settlement_relieves_without_accrual(engine_factory, weight: float) -> None:
    """真实推进一段边界：提交的 body_settlement 只含娱乐缓解的 drain 分录，需要按 费率 × 有效偏好 下降，
    不喜欢的内容按下限 f 缓解，不再叠加清醒累积。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _stage_agent(engine, 1.0, {_NOVEL: weight})
    activity_id = _start_watching(engine, _NOVEL)
    started = engine.state.current_time
    wake_by_mail(engine, offset=600)
    advance_next(engine)
    elapsed = engine.state.current_time - started
    assert 0 < elapsed <= 600
    settlement = [event for event in engine.store.read_agent_events("environment_event", 1)
                  if event.data.get("kind") == "body_settlement"][-1]
    assert activity_id in settlement.data["activity_ids"]
    rate = _NOVEL_RATE * (0.5 + 0.5 * weight)
    need = engine.state.agents[1].body.entertainment_need
    assert need == pytest.approx(1.0 - rate * elapsed / 3600)
    assert settlement.data["body_settlement"]["after"]["entertainment_need"] == pytest.approx(need)
    flows = [row for row in settlement.data["resource_flows"] if row["resource"] == "entertainment_need"]
    assert [row["direction"] for row in flows] == ["drain"]
    assert flows[0]["amount"] == pytest.approx(rate * elapsed / 3600)


@pytest.mark.parametrize(("weight", "still", "crossed"), [(0.0, 2.6, 2.7), (1.0, 1.3, 1.4)],
                         ids=["disliked-about-2.7h", "liked-about-1.3h"])
def test_watching_from_full_need_reaches_the_lower_band_in_the_specified_time(
        engine_factory, weight: float, still: float, crossed: float) -> None:
    """规格 1.2 的数值：缓解速率 0.30/小时、清醒累积 0.08/小时、f = 0.5，从 1.0 观看到「有点想放松」
    不喜欢的内容约 2.7 小时、喜欢的约 1.3 小时（有缓解时不再累积）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _stage_agent(engine, 1.0, {_NOVEL: weight})
    activity_id = _start_watching(engine, _NOVEL)
    activity = engine.state.activities[activity_id]
    assert activity.data["entertainment_relief_per_hour"] == _NOVEL_RATE and activity.data["exposure_count"] == 1
    assert engine.semantics.body.entertainment_need_per_awake_hour == 0.08
    rate = _NOVEL_RATE * relief_preference(weight, engine.semantics.body.entertainment_relief_preference_floor)
    before, _ = _need_after(engine, still)
    after, settlement = _need_after(engine, crossed)
    assert felt.entertainment_need_level(before) == "很想放松"
    assert felt.entertainment_need_level(after) == "有点想放松"
    assert activity_id in settlement["activity_ids"]
    assert settlement["body_settlement"]["after"]["entertainment_need"] == pytest.approx(after)
    assert after == pytest.approx(1.0 - rate * crossed)
    assert (1.0 - 0.6) / rate == pytest.approx(8 / 3 if weight == 0.0 else 4 / 3)


# ---------------------------------------------------------------------------
# 边界预测与结算同一口径：有缓解的活动进行时没有娱乐升档边界
# ---------------------------------------------------------------------------

#: 距「很想放松」下界 0.6 还差 120.5 秒清醒累积（目录 0.08/小时）的娱乐需要：清醒时第 121 秒升档，是最早的身体边界
#: （跨档落在两个整秒之间，前后一秒的分级不受浮点舍入影响）。
_NEAR_BAND = 0.6 - 0.08 * 120.5 / 3600


def _start_waiting(engine) -> str:
    """真实决策轮：开始一段等待（清醒、没有缓解的活动），返回受理的活动标识。"""
    decision = context(engine, "wait")
    engine.begin_decision_round([decision])
    engine.observe(decision)
    request = start_request(targets=["自己"], actions=["interact"], ways=["等待"])
    engine.submit_action(DirectToolCall(decision, "action-wait", "start_activity", request))
    result = engine.commit_decision_round("wait")[0]
    assert result.status == "in_progress", result.error_code
    return result.activity_id


def _next_boundary(engine) -> int:
    """引擎报告的下一个严格未来边界（与 AS2 调度同一来源：活动、提醒、预排事件、下一决策时刻与身体边界）。"""
    return int(engine.scheduling_state().next_event_at.timestamp())


def test_plain_awake_time_still_predicts_the_upward_entertainment_crossing(engine_factory) -> None:
    """没有缓解的清醒时间（等待）照常预测升档：引擎的下一边界恰是娱乐需要按清醒 0.08/小时升入「很想放松」的整秒
    （前一秒仍是「有点想放松」），推进到该边界时身体已升档，并以体感跨档给出决策机会。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _stage_agent(engine, _NEAR_BAND, {})
    activity_id = _start_waiting(engine)
    start = engine.state.current_time
    rate = engine.semantics.body.entertainment_need_per_awake_hour
    elapsed = _next_boundary(engine) - start
    assert elapsed == 121 < engine.state.activities[activity_id].planned_end_at - start
    assert felt.entertainment_need_level(_NEAR_BAND + rate * (elapsed - 1) / 3600) == "有点想放松"
    assert felt.entertainment_need_level(_NEAR_BAND + rate * elapsed / 3600) == "很想放松"

    advance_next(engine)

    agent = engine.state.agents[1]
    assert engine.state.current_time == start + elapsed
    assert agent.body.entertainment_need == pytest.approx(_NEAR_BAND + rate * elapsed / 3600)
    assert felt.entertainment_need_level(agent.body.entertainment_need) == "很想放松"
    assert "felt_band_change" in agent.pending_wake_reasons


def test_a_watch_session_creates_no_upward_entertainment_band_boundary(engine_factory) -> None:
    """规格 1.2 统一口径「有缓解的活动进行时不再累积娱乐需要」也管边界预测：同样的娱乐需要下改为观看，引擎不再在
    按清醒速率外推的升档时刻（121 秒后）设边界；推进到实际的下一边界时需要只按缓解下降，仍是「有点想放松」，
    本区间的娱乐分录只有缓解的 drain。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _stage_agent(engine, _NEAR_BAND, {})
    activity_id = _start_watching(engine, _NOVEL)
    start = engine.state.current_time
    rate = engine.semantics.body.entertainment_need_per_awake_hour
    awake_crossing = math.ceil((0.6 - _NEAR_BAND) / rate * 3600)
    assert awake_crossing == 121

    boundary = _next_boundary(engine)
    advance_next(engine)

    assert boundary > start + awake_crossing
    need = engine.state.agents[1].body.entertainment_need
    relief = _NOVEL_RATE * relief_preference(0.0, engine.semantics.body.entertainment_relief_preference_floor)
    assert need == pytest.approx(_NEAR_BAND - relief * (boundary - start) / 3600)
    assert felt.entertainment_need_level(need) == "有点想放松"
    settlement = [event for event in engine.store.read_agent_events("environment_event", 1)
                  if event.data.get("kind") == "body_settlement"][-1]
    assert settlement.data["interval_end"] == boundary and activity_id in settlement.data["activity_ids"]
    flows = [row for row in settlement.data["resource_flows"] if row["resource"] == "entertainment_need"]
    assert [row["direction"] for row in flows] == ["drain"]
