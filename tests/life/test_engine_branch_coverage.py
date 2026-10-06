"""life 引擎簇分支覆盖补测：结算边界、状态校验、恢复路径与校验器拒绝分支。

本文件只新增用例（不改动既有断言），按 C4b 覆盖缺口清单逐分支写正/反例：
- body.py：体感数值/分级/观察投影、娱乐账上限、结算与时长校验负例；
- models.py：请求模型校验器各拒绝形态与 DeepSeek strict Schema 合同；
- scenario.py：场景文档各校验拒绝分支（非法声明各形态）；
- rhythm.py：共同用餐、任务到期脉冲与未知地点的节律边界；
- projection.py/observe.py/decision_start.py：投影与两阶段决策边界；
- engine.py：协议故障、幂等重放、边界动作守卫、活动变更与恢复路径。
"""

from __future__ import annotations

from tests.data_paths import ENVIRONMENT_ROOT

import asyncio
import json
import math
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agentsociety2.agent.base import MemoryUpdateEvidence, ModelCallEvidence
from agentsociety2.society import DecisionContext, DirectToolCall

from tests.life.conftest import (StubPlanDayClient, activity_name, advance_next, advance_until_ready, conclude_request,
                                 context, drive, settle_day_plans, stage_day_plan, submit_round, wake_by_mail,
                                 start_request, pause_request, report_request)
from tests.life.test_scenario import _assert_invalid, _payload, _replace
from tests.life.test_work_review import _at_task_start, _complete_required_work, _paraphrased_submission
from worldmock.errors import BusinessRuleError, IntegrityError, ProtocolError
from worldmock.life import felt, projection, rhythm
from worldmock.life.body import (
    ActivityFactors,
    BoundaryClock,
    apply_food,
    interpolate,
    maximum_energy_duration_seconds,
    next_body_boundary_seconds,
    settle_body,
)
from worldmock.life.decision_start import is_action_request, maybe_action_request_start, response_self_note
from worldmock.life.engine import LifeEngine
from worldmock.life.activity_evidence import dedupe_revealed_cues, record_material_reveal
from worldmock.life.action_evidence import _terminal_action_records
from worldmock.life.world_ops import _activity_progress_phrase, _paused_activity_control
from worldmock.life.models import (ActionStatus, ActivityKind, ActivityStartParams, ActivityState, ActivityStatus,
                                   ActionResult, ChangeActivityRequest, ConsumeRequest, CounterfactualIntervention,
                                   CurvePoint, deepseek_strict_schema, MeetingMinutes, ModelIdentity, PendingAction,
                                   PlanDayRequest, PlanGoalInput, PurchaseItem, PurchaseOrderRequest,
                                   ReviseDayPlanRequest, RunIdentity, SameMomentStage, SendMessageActionRequest,
                                   SubmitWorkActionRequest, WorldState, active_activities)
from worldmock.life.observe import _recent_meal_counts
from worldmock.life.scenario import BodyConfig, LifeScenario, MotivationProfile, parse_scenario_document
from worldmock.life.world_setup import load_scenario
from worldmock.footprints.models import EventDraft
from worldmock.runtime import settle_pending_day_plans


# ---------------------------------------------------------------------------
# 共享夹具与助手
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def body_config() -> BodyConfig:
    """读取正式冻结身体参数（65kg/1600/2400/800/400）。"""
    return load_scenario(ENVIRONMENT_ROOT / "scenarios/single-person.json")[0].body


@pytest.fixture(scope="module")
def formal() -> LifeScenario:
    """读取正式场景文档作为负例模板。"""
    return load_scenario(ENVIRONMENT_ROOT / "scenarios/single-person.json")[0]


def _act(engine, round_id: str, tool_name: str, request, agent_id: int = 1):
    """单人物真实轮：观察、登记并结算。"""
    ctx = context(engine, round_id, agent_id)
    engine.begin_decision_round([ctx])
    engine.observe(ctx)
    engine.submit_action(DirectToolCall(ctx, f"action-{round_id}", tool_name, request))
    return engine.commit_decision_round(round_id)[0]


def _advance_until_agent_ready(engine, agent_id: int = 1, limit: int = 96) -> None:
    """推进穿过静默与他人决策边界，直到目标人物获得决策机会。"""
    for index in range(limit):
        ready = list(engine.scheduling_state().ready_agent_ids)
        if agent_id in ready or not ready:
            if agent_id in ready:
                return
            state = engine.scheduling_state()
            if state.next_event_at is None:
                return
            advance_next(engine)
            continue
        submit_round(engine, {rid: ("start_activity",
                                    start_request(targets=["自己"], actions=["interact"], ways=["等待"]))
                              for rid in ready}, f"filler-{index}")


def _act_ready(engine, round_id: str, tool_name: str, request, agent_id: int = 1):
    """向当前就绪的每个人物提交一轮（其他人物等待 60 秒），返回主角结果。"""
    ready = list(engine.scheduling_state().ready_agent_ids)
    requests = {}
    for rid in ready:
        requests[rid] = (tool_name, request) if rid == agent_id else (
            "start_activity",
            start_request(targets=["自己"], actions=["interact"], ways=["等待"]))
    _, results = submit_round(engine, requests, round_id)
    return next(item for item in results if item.agent_id == agent_id)


def _commit_staged(engine, staged) -> None:
    """把测试改造过的状态经存储落盘（保持版本连续），再换入引擎。"""
    staged.version += 1
    engine.context.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time,
        visibility="system", data={"kind": "test_state_staged"})])
    engine.context.state = staged


def _stage_agent(engine, **updates) -> None:
    """改造主角状态并落盘（保留其余世界事实）。"""
    staged = engine.context.state.model_copy(deep=True)
    staged.agents[1] = staged.agents[1].model_copy(update=updates, deep=True)
    _commit_staged(engine, staged)


def _run_identity(scn, **updates) -> RunIdentity:
    """构造与场景一致（可覆盖关键字段）的运行身份。"""
    fields = dict(
        run_id="coverage-run",
        source_group_id="group-coverage-run",
        agent_ids=tuple(item.agent_id for item in scn.agents),
        start_at=scn.start_at.isoformat(),
        end_at=scn.end_at.isoformat(),
        timezone=scn.timezone,
        code_sha256="2" * 64,
        model_identity=ModelIdentity(
            provider="openai_compatible",
            base_url="https://api.deepseek.com/beta",
            requested_model="deepseek-flash",
            thinking="enabled",
            tool_choice="auto",
            max_retries=0,
            prompt_sha256="0" * 64,
            tool_schema_sha256="1" * 64,
        ),
    )
    fields.update(updates)
    return RunIdentity(**fields)


class _AcceptingReviewer:
    """按声明返回接受/语义拒绝的最小评审客户端桩。"""

    def __init__(self, rejected: bool = False):
        self.rejected = rejected
        self.calls = 0

    async def call(self, prepared):
        self.calls += 1
        references = [{
            "reference_id": identity,
            "required_fact_covered": not self.rejected,
            "source_supported": not self.rejected,
            "contradiction": self.rejected,
            "source_excerpt": "Hi , I'm David",
            "rationale": "Supported by the transcript.",
        } for identity in prepared.expected_reference_ids]
        deliveries = [{
            "delivery_path": path,
            "source_supported": not self.rejected,
            "contradiction": self.rejected,
            "source_excerpt": "Hi , I'm David",
            "rationale": "Supported by the transcript.",
        } for path in prepared.expected_delivery_paths]
        arguments = {"reference_assessments": references,
                     "delivery_assessments": deliveries, "accepted": not self.rejected}
        return {"choices": [{"message": {"tool_calls": [{
            "id": f"review-{self.calls}", "type": "function",
            "function": {"name": "submit_work_review",
                         "arguments": json.dumps(arguments)}}]}}]}


def _stage_submission(engine, rejected: bool = False):
    """完成公开工作并登记尚未结算的提交（返回动作标识）。"""
    _at_task_start(engine)
    _complete_required_work(engine)
    engine.reviews.reviewer = _AcceptingReviewer(rejected)
    round_id = "cover-submit"
    ctx = context(engine, round_id)
    engine.begin_decision_round([ctx])
    engine.observe(ctx)
    call = DirectToolCall(ctx, f"action-{round_id}", "submit_work", _submission_action(engine))
    engine.submit_action(call)
    return call


def _submission_action(engine) -> SubmitWorkActionRequest:
    """@brief 用人物可读任务名称提交测试会议纪要。"""
    original = _paraphrased_submission()
    observed = engine.context.store.read_agent_events("observation", 1)[-1].data
    task = next(row for row in observed["task_bindings"] if row["task_id"] == original.task_id)
    return SubmitWorkActionRequest(
        task_name=task["task_name"], deliverable=original.deliverable)


# ---------------------------------------------------------------------------
# body.py：体感投影、账本上限与结算校验负例
# ---------------------------------------------------------------------------


def test_interpolate_fails_closed_on_non_finite_input() -> None:
    """非有限输入不落入任何区间，插值失败关闭（有限值场景见交付报告的不可达说明）。"""
    from worldmock.life import sleep
    curve = tuple(CurvePoint(input_value=point.input_hours, output_value=point.output_factor)
                  for point in sleep.WORK_EFFICIENCY_CURVE)
    with pytest.raises(ValueError, match="interval is missing"):
        interpolate(curve, float("nan"))


def test_settle_body_rejects_negative_duration(body_config) -> None:
    """负时长结算被拒绝。"""
    body = felt_body(1600)
    with pytest.raises(ValueError, match="cannot be negative"):
        settle_body(body, ActivityKind.WAIT, -1, body_config)


def test_apply_food_rejects_negative_calories(body_config) -> None:
    """负热量进食被拒绝。"""
    with pytest.raises(ValueError, match="cannot be negative"):
        apply_food(felt_body(1600), -5, body_config)


@pytest.mark.parametrize("kwargs, message", [
    ({"energy_kcal": -1}, "available energy cannot be negative"),
    ({"limit_seconds": -1}, "duration limit cannot be negative"),
])
def test_maximum_energy_duration_rejects_negative_inputs(body_config, kwargs, message) -> None:
    """负能量或负上限秒数均拒绝。"""
    arguments = dict(kind=ActivityKind.WORK, energy_kcal=100, config=body_config)
    arguments.update(kwargs)
    with pytest.raises(ValueError, match=message):
        maximum_energy_duration_seconds(**arguments)


def felt_body(energy_kcal: float = 1600, **updates):
    """构造带缺省的身体状态。"""
    fields = dict(energy_kcal=energy_kcal, sleep_pressure=0.2,
                  sleep_deficit_hours=0, entertainment_need=0)
    fields.update(updates)
    from worldmock.life.models import BodyState
    return BodyState(**fields)


def felt_agent(body):
    """把身体状态装进最小人物状态，供观察投影的体感函数使用。"""
    from worldmock.life.models import AgentState
    return AgentState(agent_id=1, name="体感", location_id="home", balance_account_id="agent:1",
                      body=body, body_settled_at=0, next_decision_at=0)


def test_felt_values_expose_five_numeric_signals(body_config) -> None:
    """体感数值组含五项信号，餐时窗口只放大食欲（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「其他体感分级」）。"""
    agent = felt_agent(felt_body(1600, social_need=0.4, mood_valence=0.2))
    plain = projection.felt_values(agent, 22.0)
    in_window = projection.felt_values(agent, 12.5, felt.MealWindows(lunch=(12.0, 13.5)))
    assert set(plain) == {"appetite", "sleepiness", "entertainment_need",
                          "social_need", "mood_valence"}
    assert plain["social_need"] == 0.4 and plain["mood_valence"] == 0.2
    # 餐时项在窗口内加 0.15（experiment_assumption，docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「其他体感分级」）
    assert in_window["appetite"] == pytest.approx(plain["appetite"] + 0.15)
    assert plain["sleepiness"] > 0


def test_body_projection_renders_five_band_texts_and_numeric_values(body_config) -> None:
    """观察投影分级/数值两组：分级五项进提示，数值只进事件；低能量按紧急阈值与 100 kcal 两档显示饥饿
    （docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「数值与分级的分离」，docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动「饥饿在低能量时不失灵」）。"""
    body = felt_body(1600, social_need=0.7, mood_valence=0.5, entertainment_need=0.7)
    levels = projection.body_projection(felt_agent(body), 20.0, body_config)["levels"]
    assert set(levels) == {"appetite", "sleepiness", "entertainment", "social", "mood"}
    assert levels["social"] == "有点孤单"
    assert levels["mood"] == "很开心"
    assert levels["entertainment"] == "很想放松"
    emergency = projection.body_projection(felt_agent(felt_body(300)), 8.0, body_config)
    starving = projection.body_projection(felt_agent(felt_body(60)), 8.0, body_config)
    calm = projection.body_projection(felt_agent(felt_body(1600)), 8.0, body_config)
    assert emergency["levels"]["appetite"] == "饿得难受"
    assert starving["levels"]["appetite"] == "饿得发慌"
    assert calm["levels"]["appetite"] == "不饿"
    for observation in (emergency, starving, calm):
        assert set(observation["values"]) == {"appetite", "sleep_pressure", "sleepiness",
                                              "energy_kcal", "sleep_deficit_hours", "entertainment_need",
                                              "social_need", "mood_valence"}
    assert emergency["values"]["energy_kcal"] == 300


def test_entertainment_need_capped_at_one_when_already_full(body_config) -> None:
    """娱乐需要已到 1 时清醒累积不再入账，期末钳制为 1。"""
    result = settle_body(felt_body(1600, entertainment_need=1.0),
                         ActivityKind.WORK, 3600, body_config)
    assert result.after.entertainment_need == 1.0


def test_wait_does_not_relieve_full_entertainment_need(body_config) -> None:
    """等待不含隐式内容接触，已满的娱乐需求保持上限。"""
    result = settle_body(felt_body(1600, entertainment_need=1.0),
                         ActivityKind.WAIT, 3600, body_config)
    assert result.after.entertainment_need == 1.0


def test_zero_second_interval_settles_no_entertainment_entries(body_config) -> None:
    """零时长结算不产生任何娱乐分录（累积为零的边界）。"""
    before = felt_body(1600, entertainment_need=0.5, sleep_pressure=0.3)
    result = settle_body(before, ActivityKind.WAIT, 0, body_config)
    assert result.after.entertainment_need == 0.5
    assert result.after.sleep_pressure == pytest.approx(0.3)


def test_negative_work_factors_are_rejected(body_config) -> None:
    """负技能/工具因子拒绝结算。"""
    with pytest.raises(ValueError, match="cannot be negative"):
        settle_body(felt_body(1600), ActivityKind.WORK, 3600, body_config,
                    ActivityFactors(1, 0, work_skill_factor=-0.5))


def test_next_body_boundary_skips_zero_rates_when_nothing_due(body_config) -> None:
    """300 kcal 时仍给出跌破 100 kcal 的候选；能量已低于全部三个阈值且无昼夜输入时才没有身体边界。"""
    quiet = body_config.model_copy(update={"entertainment_need_per_awake_hour": 0})
    burn = quiet.basal_kcal_per_hour + (quiet.activity_met["wait"] - 1) * quiet.mass_kg
    body = felt_body(300, social_need=1.0)
    assert next_body_boundary_seconds(body, ActivityKind.WAIT, quiet, None, None) == (
        math.floor((300 - felt.APPETITE_STARVING_KCAL) * 3600 / burn) + 1)
    starving = felt_body(60, social_need=1.0)
    assert next_body_boundary_seconds(starving, ActivityKind.WAIT, quiet, None, None) is None
    # 娱乐速率 0 的资源在昼夜输入存在时也不产生候选（跳过continue 分支）。
    with_hour = next_body_boundary_seconds(felt_body(1600, social_need=1.0),
                                           ActivityKind.WAIT, quiet, None, BoundaryClock(12.0))
    assert with_hour is not None and with_hour > 0


def test_next_body_boundary_covers_entertainment_band_edges(body_config) -> None:
    """正常清醒等待给出首个娱乐分级边界（正例）。"""
    seconds = next_body_boundary_seconds(felt_body(1600, entertainment_need=0.0),
                                         ActivityKind.WAIT, body_config, None, BoundaryClock(12.0))
    assert seconds is not None and seconds > 0


# ---------------------------------------------------------------------------
# models.py：请求模型校验器拒绝分支与 DeepSeek strict 合同
# ---------------------------------------------------------------------------


def test_counterfactual_sources_must_be_unique_and_hypothesis_nonblank() -> None:
    """干预证据身份唯一、假设文本非空白。"""
    base = dict(kind="set_balance_fen", agent_id=1, balance_fen=1,
                hypothesis="w", source_event_ids=("a" * 64,))
    with pytest.raises(ValidationError, match="unique"):
        CounterfactualIntervention(**base | {"source_event_ids": ("a" * 64, "a" * 64)})
    with pytest.raises(ValidationError, match="blank"):
        CounterfactualIntervention(**base | {"hypothesis": "   "})


def _activity_state(**updates) -> ActivityState:
    """构造合法活动状态并可覆盖字段。"""
    fields = dict(activity_id="activity-x", agent_id=1, location_id="home", kind=ActivityKind.WAIT,
                  status=ActivityStatus.ACTIVE, started_at=0, planned_end_at=100,
                  last_settled_at=0, elapsed_seconds=0, planned_duration_seconds=100,
                  origin_action_id="action-x", origin_decision_id="decision-x",
                  origin_request_event_id="event-x")
    fields.update(updates)
    return ActivityState(**fields)


def test_activity_lifecycle_validators_reject_disagreements() -> None:
    """终态必须带结束时刻；进度区间不得早于开始或超计划。"""
    with pytest.raises(ValidationError, match="lifecycle status"):
        ActivityState.model_validate(_activity_state(
            status=ActivityStatus.COMPLETED, ended_at=None).model_dump())
    with pytest.raises(ValidationError, match="progress interval"):
        ActivityState.model_validate(_activity_state(
            status=ActivityStatus.COMPLETED, ended_at=100,
            elapsed_seconds=200).model_dump())


def test_world_state_rejects_activity_with_unknown_owner(engine_factory) -> None:
    """活动集合中人物归属未知时世界状态拒绝恢复。"""
    engine = engine_factory()
    started = _act(engine, "owner-wait", "start_activity",
                   start_request(targets=["自己"], actions=["interact"], ways=["等待"]))
    staged = engine.context.state.model_copy(deep=True)
    staged.activities[started.activity_id] = staged.activities[started.activity_id].model_copy(
        update={"agent_id": 999}, deep=True)
    with pytest.raises(ValidationError, match="identity or owner"):
        WorldState.model_validate(staged.model_dump())


def test_travel_target_requires_destination_and_mode() -> None:
    """出行启动必须携带目的地与方式（领域内统一请求模型）。"""
    with pytest.raises(ValidationError, match="required for travel"):
        ActivityStartParams(activity_type="travel", planned_duration_seconds=600)


def test_playback_speed_only_for_change_speed_operation() -> None:
    """非变速操作携带速度被拒绝。"""
    with pytest.raises(ValidationError, match="only for change_speed"):
        ChangeActivityRequest(activity_id="a", operation="pause", playback_speed=2)


def test_plan_goal_rejects_blank_text_and_old_time_fields() -> None:
    """目标必须有文字，旧绝对时段请求严格拒绝。"""
    with pytest.raises(ValidationError, match="pattern"):
        PlanGoalInput(intent_kind="工作", goal=" ", remind_time=None, around_time=None)
    with pytest.raises(ValidationError, match="extra_forbidden"):
        PlanGoalInput.model_validate({"intent_kind": "工作", "goal": "推进资料整理", "remind_time": None,
                                      "around_time": None, "start": 100})


def test_meeting_minutes_fields_reject_whitespace_only_text() -> None:
    """会议纪要字段模式先行拒绝纯空白文本（校验器空白分支由下方直调用例覆盖）。"""
    with pytest.raises(ValidationError, match="pattern"):
        MeetingMinutes(summary=" ", decisions=("d",), action_items=("a",))


def test_meeting_minutes_validate_text_direct_call_blank_and_non_blank() -> None:
    """直调校验器取空白与非空白两态。

    直调原因：字段层 pattern=r"\\S" 先行拒绝一切纯空白文本（上一用例），
    经正常模型构造永远到不了校验器的空白分支；编排者裁决 pattern 守卫与
    校验器双防线为合理分层，故以 model_construct 绕过字段校验直调校验器，
    覆盖 validate_text 的 raise 分支与通过分支。
    """
    blank = MeetingMinutes.model_construct(summary="ok", decisions=(" ",), action_items=())
    with pytest.raises(ValueError, match="meeting minutes text must not be blank"):
        blank.validate_text()
    healthy = MeetingMinutes(summary="ok", decisions=("d",), action_items=("a",))
    assert healthy.validate_text() is healthy


def test_deepseek_strict_schema_rejects_optional_fields() -> None:
    """DeepSeek strict 对象要求全部属性显式必填。"""

    class PartiallyRequired(BaseModel):
        required_field: int
        optional_field: str = "default"

    with pytest.raises(ValueError, match="not all required"):
        deepseek_strict_schema(PartiallyRequired)


def test_deepseek_strict_schema_rejects_open_objects() -> None:
    """DeepSeek strict 对象必须封闭。"""

    class Open(BaseModel):
        field: int

    with pytest.raises(ValueError, match="not closed"):
        deepseek_strict_schema(Open)


def test_deepseek_strict_schema_rejects_unsupported_keys() -> None:
    """Schema 内出现服务端不支持的键（如 minLength）即拒绝。"""

    class ClosedWithLength(BaseModel):
        model_config = ConfigDict(extra="forbid")
        name: str = Field(min_length=1)

    with pytest.raises(ValueError, match="unsupported keys"):
        deepseek_strict_schema(ClosedWithLength)


# ---------------------------------------------------------------------------
# scenario.py：校验器拒绝分支（非法声明各形态）
# ---------------------------------------------------------------------------


def _invalid_body(body_config, message, **updates) -> None:
    """重校验改造后的身体参数并断言拒绝原因。"""
    payload = body_config.model_copy(update=updates).model_dump()
    with pytest.raises(ValidationError, match=message):
        BodyConfig.model_validate(payload)


def test_body_config_rejects_energy_and_threshold_disagreements(body_config) -> None:
    """初始能量超上限、阈值失序与无穷系数均拒绝。"""
    _invalid_body(body_config, "exceeds maximum energy", initial_energy_kcal=9999)
    _invalid_body(body_config, "ordered within maximum energy", hunger_threshold_kcal=300)
    _invalid_body(body_config, "must be finite", work_notes_multiplier=float("inf"))


def test_body_config_rejects_bad_curves(body_config) -> None:
    """效率曲线输入必须严格递增、输出保持在零到一。"""
    reversed_curve = (CurvePoint(input_value=8, output_value=1),
                      CurvePoint(input_value=0, output_value=0))
    _invalid_body(body_config, "strictly increasing",
                  food_efficiency_curve=reversed_curve)
    too_big = (CurvePoint(input_value=0, output_value=1.5),
               CurvePoint(input_value=8, output_value=1))
    _invalid_body(body_config, "between zero and one",
                  food_efficiency_curve=too_big)


def _invalid_profile(formal, message, **updates) -> None:
    """重校验改造后的动机档案并断言拒绝原因。"""
    profile = formal.agents[0].motivation
    assert profile is not None
    payload = profile.model_copy(update=updates).model_dump()
    with pytest.raises(ValidationError, match=message):
        MotivationProfile.model_validate(payload)


def test_motivation_profile_validators_reject_each_shape(formal) -> None:
    """大五取值区间、权重非负、舒适非负与志向键闭集。"""
    _invalid_profile(formal, "between 1 and 10", big_five={"openness": 11, "conscientiousness": 5,
                                                           "extraversion": 5, "agreeableness": 5,
                                                           "neuroticism": 5})
    weights = dict(formal.agents[0].motivation.weights)
    _invalid_profile(formal, "non-negative finite", weights=weights | {"goal_task": -0.1})
    comfort = dict(formal.agents[0].motivation.comfort)
    _invalid_profile(formal, "non-negative finite", comfort=comfort | {"energy_kcal": -1})
    _invalid_profile(formal, "exactly daily_effective_work_seconds",
                     aspiration={"wrong_key": 1})


def test_agent_config_rejects_bad_windows_and_identities() -> None:
    """餐窗时序/重叠、空白生活活动、重复联系人身份拒绝。"""
    _assert_invalid(_replace(("agents", 0, "lunch_window"), [13, 13]),
                    "must satisfy 0 <= start < end <= 24")
    _assert_invalid(_replace(("agents", 0, "lunch_window"), [12, 19]),
                    "must not overlap lunch window")
    _assert_invalid(_replace(("agents", 0, "lifestyle_activities"), [" "]),
                    "must not be blank")
    contacts = _payload()["agents"][0]["contacts"]
    _assert_invalid(_replace(("agents", 0, "contacts"), contacts + [dict(contacts[0])]),
                    "contact ids must be unique")


def test_agent_with_single_meal_window_is_valid() -> None:
    """只声明午餐窗（晚餐缺省）的档案合法。"""
    payload = _replace(("agents", 0, "dinner_window"), None)
    document = LifeScenario.model_validate(payload)
    agent = document.agents[0]
    assert agent.lunch_window is not None and agent.dinner_window is None


def test_product_config_rejects_use_mode_and_serving_disagreements() -> None:
    """商品用餐方式唯一合法、食品份量字段一致且非空白。"""
    runtime = load_scenario(ENVIRONMENT_ROOT / "scenarios/single-person.json")[0]
    template = next(item for item in runtime.products if "eat" in item.use_modes)
    payload = template.model_dump()

    def invalid(message, **updates):
        with pytest.raises(ValidationError, match=message):
            from worldmock.life.scenario import ProductConfig
            ProductConfig.model_validate(payload | updates)

    invalid("unique supported values", use_modes=["eat", "eat"])
    invalid("serving fields are inconsistent", use_modes=["eat"],
            serving_quantity=None, serving_unit=None)
    invalid("serving unit must not be blank",
            use_modes=["eat"], serving_quantity=100, serving_unit=" ")
    invalid("non-food product cannot provide food energy",
            use_modes=["use"], serving_quantity=None, serving_unit=None,
            energy_kcal_per_unit=50)
    invalid("food product must be consumable",
            use_modes=["eat"], consumable=False)


def test_work_task_due_must_follow_availability(formal) -> None:
    """任务截止不得早于开放。"""
    task = formal.tasks[0].model_dump()
    task.pop("available_at")
    task["appointment"] = {"starts_at": task["due_at"], "reason": "Scheduled review"}
    with pytest.raises(ValidationError, match="follow appointment"):
        from worldmock.life.scenario import WorkTaskConfig
        WorkTaskConfig.model_validate(task)


def test_calendar_rejects_duplicate_dates_and_noncanonical_iso() -> None:
    """日历列表重复与不规范 ISO 日期拒绝。"""
    _assert_invalid(_replace(("calendar", "makeup_work_dates"), ["2026-02-28", "2026-02-28"]),
                    "must not contain duplicates")
    _assert_invalid(_replace(("calendar", "sick_leave_date"), "20260312"),
                    "canonical ISO format")


def test_scenario_interval_and_timezone_must_be_consistent() -> None:
    """结束早于开始、时区声明与时刻偏移不一致均拒绝。"""
    _assert_invalid(_replace(("end_at",), "2026-01-05T00:00:00+08:00"),
                    "time interval or timezone is invalid")
    _assert_invalid(_replace(("start_at",), "2026-01-05T00:00:00+09:00"),
                    "do not match the declared timezone")
    _assert_invalid(_replace(("start_at",), "2026-01-05T00:00:00"),
                    "must be timezone-aware")


def test_scenario_rejects_unknown_references() -> None:
    """住处/路线/受理人未知引用拒绝。"""
    _assert_invalid(_replace(("agents", 0, "home_location_id"), "nowhere"),
                    "agent home is not a declared location")
    _assert_invalid(_replace(("routes",), [{"origin_id": "nowhere"}]),
                    "Extra inputs are not permitted")
    _assert_invalid(_replace(("tasks", 0, "assignee_agent_id"), "agent:99"),
                    "unknown assignee agent")
    _assert_invalid(_replace(("tasks", 0, "due_at"), "2026-01-05T08:00:00+08:00"),
                    "work task is outside the scenario interval")


def test_schedule_sections_reject_each_invalid_shape(engine_factory) -> None:
    """预排段时区、窗口、账户引用与对象引用的拒绝形态。"""
    _assert_invalid(_replace(("bills", 0, "due_at"), "2026-01-05T09:00:00+09:00"),
                    "do not match the declared timezone")
    _assert_invalid(_replace(("bills", 0, "due_at"), "2026-12-31T09:00:00+08:00"),
                    "outside the scenario interval")
    _assert_invalid(_replace(("bills", 0, "payee_account_id"), "agent:1"),
                    "payer and payee accounts must differ")
    _assert_invalid(_replace(("payrolls", 0, "employer_account_id"), "employer:ghost"),
                    "undeclared employer account")
    _assert_invalid(_replace(("payrolls", 0, "agent_id"), 99),
                    "unknown agent")
    source = engine_factory().context.scenario.supplies[0].source.model_dump(mode="json")
    payload = _payload()
    payload["price_changes"] = [{"change_id": "change-x", "supply_id": "supply-ghost",
                                 "price_version": "new-version", "source": source, "amount_fen": 100,
                                 "due_at": "2026-01-06T09:00:00+08:00"}]
    _assert_invalid(payload, "price change references an unknown supply")
    payload = _payload()
    payload["product_releases"] = [{"release_id": "release-x", "supply_id": "supply-ghost",
                                    "source": source, "stock": 5,
                                    "due_at": "2026-01-06T09:00:00+08:00"}]
    _assert_invalid(payload, "product release references an unknown supply")
    payload = _payload()
    payload["product_releases"] = [{"release_id": "release-x",
                                    "supply_id": payload["supplies"][0]["supply_id"], "source": source,
                                    "merchant": "merchant:wrong", "stock": 5,
                                    "due_at": "2026-01-06T09:00:00+08:00"}]
    _assert_invalid(payload, "Extra inputs are not permitted")
    payload = _payload()
    payload["accounts"].append({"account_id": "agent:1", "money_fen": 1, "role": "personal:dup"})
    _assert_invalid(payload, "must not reuse agent account ids")


def test_runtime_scenario_rejects_interval_route_and_account_shapes(engine_factory) -> None:
    """运行时场景：窗口失序、路线键重复与账户角色覆盖拒绝。"""
    scenario = load_scenario(ENVIRONMENT_ROOT / "scenarios/single-person.json")[0]

    def revalidate(message, **updates):
        staged = scenario.model_copy(update=updates)
        with pytest.raises(ValidationError, match=message):
            type(scenario).model_validate(staged.model_dump())

    revalidate("time interval or timezone is invalid", end_at=scenario.start_at)
    twin = scenario.routes[0].model_copy(update={"route_id": "route-twin"})
    revalidate("route lookup keys must be unique",
               routes=(scenario.routes[0], twin))
    revalidate("account ids must not be blank", transport_account_id=" ")
    revalidate("account roles must use distinct account ids",
               transport_account_id=scenario.employer_account_id)


def test_parse_scenario_document_rejects_missing_invalid_and_mismatched(tmp_path) -> None:
    """缺失文件、非法 JSON 与语料清单失配均失败关闭。"""
    formal_path = ENVIRONMENT_ROOT / "scenarios/single-person.json"
    with pytest.raises(IntegrityError, match="missing or is a symbolic link"):
        parse_scenario_document(tmp_path / "absent.json")
    garbage = tmp_path / "garbage.json"
    garbage.write_bytes(b"not-json")
    with pytest.raises(IntegrityError, match="validation failed"):
        parse_scenario_document(garbage)
    payload = json.loads(formal_path.read_text(encoding="utf-8"))
    payload["corpus_manifest_sha256"] = "0" * 64
    mismatched = tmp_path / "mismatched.json"
    mismatched.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(IntegrityError, match="corpus manifest identity is invalid"):
        parse_scenario_document(mismatched)


# ---------------------------------------------------------------------------
# rhythm.py：共同用餐、任务到期脉冲与未知地点
# ---------------------------------------------------------------------------


def _rhythm_inputs(engine, staged, interval: int = 3600):
    """以引擎事实装配节律输入。"""
    return rhythm.engine_rhythm_inputs(engine.context, staged, interval)


def _consume_activity(engine, use_mode: str, activity_id: str = "activity-meal"):
    """构造进行中的进食活动（请求形态与引擎落库一致）。"""
    from worldmock.life.models import ConsumeItem
    request = ConsumeRequest(items=(ConsumeItem(product_id="food-6923644266066",
                                               quantity=1),),
                             use_mode=use_mode, duration_seconds=600)
    return ActivityState.model_validate(_activity_state(
        activity_id=activity_id, kind=ActivityKind.CONSUME,
        status=ActivityStatus.ACTIVE, started_at=engine.context.state.current_time,
        planned_end_at=engine.context.state.current_time + 600,
        last_settled_at=engine.context.state.current_time,
        planned_duration_seconds=600,
        data={"request": request.model_dump(mode="json")}).model_dump())


def test_shared_meal_covers_social_accrual_only_for_eat_mode(engine_factory) -> None:
    """共同在场堂食停止社交需要累积；外带取餐不覆盖（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「其他体感分级」）。"""
    engine = engine_factory("multi-person.json")
    now = engine.context.state.current_time
    staged = engine.context.state.model_copy(deep=True)
    staged.activities["activity-meal"] = _consume_activity(engine, "eat")
    staged.agents[1] = staged.agents[1].model_copy(
        update={"current_activity_id": "activity-meal",
                "location_id": staged.agents[2].location_id,
                "body": staged.agents[1].body.model_copy(
                    update={"social_need": 0.2, "mood_updated_at": now}, deep=True)},
        deep=True)
    settled = rhythm.apply_decision_rhythm(_rhythm_inputs(engine, staged))[0]
    # 堂食覆盖：0.2 + 0.04 清醒增长 − 0.3/h 共同用餐 → 钳到 0
    assert settled.agents[1].body.social_need == 0.0

    takeaway = staged.model_copy(deep=True)
    takeaway.activities["activity-meal"] = _consume_activity(engine, "use")
    settled = rhythm.apply_decision_rhythm(_rhythm_inputs(engine, takeaway))[0]
    assert settled.agents[1].body.social_need == pytest.approx(0.2 + 0.04)


def test_task_deadline_missed_pulses_negative_mood(engine_factory) -> None:
    """任务到期未交叠加 −0.4 脉冲（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「其他体感分级」）。"""
    engine = engine_factory()
    task = engine.context.scenario.tasks[0]
    due = int(task.due_at.timestamp())
    staged = engine.context.state.model_copy(deep=True)
    staged.current_time = due
    staged.pending_events = [item for item in staged.pending_events if item.due_at > due]
    staged.agents[1] = staged.agents[1].model_copy(
        update={"visible_task_ids": {task.task_id}, "next_decision_at": due,
                "last_decision_time": due - 5400}, deep=True)
    staged.agents[1].body = staged.agents[1].body.model_copy(
        update={"mood_valence": 0.0, "mood_updated_at": due}, deep=True)
    settled = rhythm.apply_decision_rhythm(_rhythm_inputs(engine, staged, interval=900))[0]
    assert settled.agents[1].body.mood_valence == pytest.approx(-0.4)


def test_unknown_location_yields_no_opening_or_closing_candidates(engine_factory) -> None:
    """未知地点不产生营业变化唤醒候选。"""
    engine = engine_factory()
    staged = engine.context.state.model_copy(deep=True)
    staged.agents[1] = staged.agents[1].model_copy(update={"location_id": "nowhere"}, deep=True)
    inputs = _rhythm_inputs(engine, staged)
    assert rhythm._current_location(staged.agents[1], inputs) is None
    assert rhythm._closing_at(staged.agents[1], inputs) is None
    assert rhythm._just_opened(staged.agents[1], inputs) is False


# ---------------------------------------------------------------------------
# projection.py 与 observe.py：投影边界
# ---------------------------------------------------------------------------


def test_inbox_rows_skip_visible_ids_without_scenario_config(engine_factory) -> None:
    """收件箱头跳过无场景邮件配置的可见身份。"""
    engine = engine_factory()
    agent = engine.context.state.agents[1].model_copy(
        update={"visible_message_ids": {"ghost-mail"}}, deep=True)
    inputs = projection.ProjectionInputs(state=engine.context.state, scenario=engine.context.scenario,
                                         agent=agent, task_rows=())
    assert projection._inbox_rows(inputs) == []


def test_visible_task_rows_keep_overdue_tasks_until_the_end_of_their_day(engine_factory) -> None:
    """未付款的逾期任务保留在可见任务行与工作池中，直到截止当日的本地日结束；次日零点起不再列出
    （docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第六章「逾期工作从观察和工具中消失」）。"""
    engine = engine_factory()
    task = engine.context.scenario.tasks[0]
    due = int(task.due_at.timestamp())
    next_day = int((task.due_at.replace(hour=0, minute=0, second=0) + timedelta(days=1)).timestamp())
    _stage_agent(engine, visible_task_ids={task.task_id})
    rows = engine.observations._visible_task_rows(engine.context.state.agents[1])
    assert [row["task_id"] for row in rows] == [task.task_id]
    for moment, listed in ((due + 60, [task.task_id]), (next_day - 1, [task.task_id]), (next_day, [])):
        staged = engine.context.state.model_copy(deep=True)
        staged.current_time = moment
        staged.pending_events = [item for item in staged.pending_events if item.due_at > moment]
        _commit_staged(engine, staged)
        agent = engine.context.state.agents[1]
        assert [row["task_id"] for row in engine.observations._visible_task_rows(agent)] == listed
        assert [entry.object_id for entry in engine.observations._task_pool(agent)] == listed


def test_motivation_internal_returns_none_when_no_candidates(engine_factory, monkeypatch) -> None:
    """动机枚举为空时观察不携带内部动机仪表。"""
    engine = engine_factory()
    monkeypatch.setattr("worldmock.life.observe.evaluate_motivation", lambda *args: [])
    assert engine.observations._motivation_internal(engine.context.state.agents[1]) is None


def test_activity_summary_foods_row_requires_products(engine_factory) -> None:
    """活动品类计数只在场景声明商品时含 foods 行。"""
    engine = engine_factory()
    summary = engine.observations._activity_summary(engine.context.state.agents[1])
    assert "foods" in summary
    scenario = engine.context.scenario.model_copy(update={"products": (), "supplies": ()})
    engine.context.semantics = replace(engine.context.semantics, scenario=scenario)
    engine.context.rules.scenario = scenario
    engine.context.rules.supplies = {}
    assert "foods" not in engine.observations._activity_summary(engine.context.state.agents[1])


def test_recent_meal_counts_use_actual_settled_consumption() -> None:
    """近期进食只统计本人物过去一天的已结算食用量。"""
    now = 10_000
    activities = [
        SimpleNamespace(agent_id=1, kind=ActivityKind.CONSUME, started_at=now - 60,
                        data={"request": {"use_mode": "eat"}, "consumed": {"food-x": 1.5}}),
        SimpleNamespace(agent_id=1, kind=ActivityKind.CONSUME, started_at=now - 120,
                        data={"request": {"use_mode": "use"}, "consumed": {"food-x": 3}}),
        SimpleNamespace(agent_id=2, kind=ActivityKind.CONSUME, started_at=now - 120,
                        data={"request": {"use_mode": "eat"}, "consumed": {"food-x": 4}}),
    ]
    assert _recent_meal_counts(activities, 1, now) == {"food-x": 2}


# ---------------------------------------------------------------------------
# decision_start.py：行动请求判定、首个行动请求取出草稿与回复正文
# ---------------------------------------------------------------------------

#: 一个行动请求：没有 purpose、tools 含 start_activity、thinking 开启。
_ACTION_REQUEST = {"tools": [{"type": "function", "function": {"name": "memory_recent"}},
                             {"type": "function", "function": {"name": "start_activity"}}],
                   "extra_body": {"thinking": {"type": "enabled"}}}


def _exchange(request: dict, phase: str = "request") -> SimpleNamespace:
    """构造模型交换证据形态。"""
    return SimpleNamespace(request=request, phase=phase, response=None, error=None)


@pytest.mark.parametrize("request_shape", [
    {**_ACTION_REQUEST, "purpose": "plan_day"},
    {**_ACTION_REQUEST, "purpose": "work_review"},
    {**_ACTION_REQUEST, "extra_body": {"thinking": {"type": "disabled"}}},
    {**_ACTION_REQUEST, "extra_body": None},
    {key: value for key, value in _ACTION_REQUEST.items() if key != "tools"},
    {**_ACTION_REQUEST, "tools": [{"type": "function", "function": {"name": "finish"}}, "broken"]},
    {"tools": [{"type": "function", "function": {"name": "submit_reflection"}}], "tool_choice": "required",
     "extra_body": {"thinking": {"type": "disabled"}}},
    {"tools": [{"type": "function", "function": {"name": "submit_reflection"}}], "tool_choice": "required",
     "extra_body": {"thinking": {"type": "enabled"}}},
    None,
])
def test_only_action_requests_satisfy_the_predicate(request_shape) -> None:
    """带 purpose、思考关闭、没有 start_activity 的请求（零点计划、工作验收、只读问答、记忆巩固、就寝反思）都不是行动请求。

    就寝反思的请求不带 purpose、带工具，但工具唯一为 submit_reflection（docs/dev/SPEC-as2-integration.md#errands-workday-运行修复入库-就寝反思经严格工具提交 ）：无论思考
    开关怎样，都按工具中没有 start_activity 判为不是行动请求，不产生 decision_started。"""
    assert is_action_request(_ACTION_REQUEST) is True
    assert is_action_request({**_ACTION_REQUEST, "purpose": None}) is True
    assert is_action_request(request_shape) is False


def test_first_action_request_pops_the_pending_start_once() -> None:
    """只有 phase=request 的行动请求取出草稿；响应与非行动请求不消费，第二个行动请求不再产出。"""
    data = {"round_id": "r", "round_order": 0, "world_state_version": 3, "wake_reasons": []}
    pending = {1: {"decision_id": "d", "data": data}}
    assert maybe_action_request_start(pending, 1, 42, _exchange(_ACTION_REQUEST, "response")) is None
    assert maybe_action_request_start(pending, 1, 42, _exchange({**_ACTION_REQUEST, "purpose": "x"})) is None
    assert 1 in pending

    draft = maybe_action_request_start(pending, 1, 42, _exchange(_ACTION_REQUEST))

    assert pending == {}
    assert draft is not None and draft.event_type == "decision_started"
    assert (draft.decision_id, draft.occurred_at, draft.observed_at, draft.data) == ("d", 42, 42, data)
    assert maybe_action_request_start(pending, 1, 43, _exchange(_ACTION_REQUEST)) is None


@pytest.mark.parametrize(("response", "note"), [
    ({"choices": [{"message": {"content": "  先歇一会儿。\n"}}]}, "先歇一会儿。"),
    ({"choices": [{"message": {"content": "   "}}]}, None),
    ({"choices": [{"message": {"content": None, "tool_calls": []}}]}, None),
    ({"choices": [{"message": {}}]}, None),
    ({"choices": []}, None),
    ({"choices": ["broken"]}, None),
    (None, None),
])
def test_response_self_note_is_the_stripped_reply_body_or_none(response, note) -> None:
    """self_note 取回复正文去首尾空白；空、缺失或形状不符时为 None，不代写。"""
    assert response_self_note(response) == note


# ---------------------------------------------------------------------------
# engine.py：协议故障、状态校验与恢复路径
# ---------------------------------------------------------------------------


def test_begin_decision_round_rejects_double_open_and_bad_contexts(engine_factory) -> None:
    """重复开轮、轮次不一致与重复人物均协议拒绝。"""
    engine = engine_factory()
    ctx = context(engine, "one")
    engine.begin_decision_round([ctx])
    engine.observe(ctx)
    engine.submit_action(DirectToolCall(
        ctx, "action-one", "start_activity",
        start_request(targets=["自己"], actions=["interact"], ways=["等待"])))
    with pytest.raises(ProtocolError, match="cannot be started"):
        engine.begin_decision_round([ctx])
    with pytest.raises(ProtocolError, match="cannot be started"):
        engine.begin_decision_round([])
    engine.commit_decision_round("one")
    advance_until_ready(engine)
    first = context(engine, "two-a")
    second = DecisionContext(engine.context.state.run_id, "two-b", "decision-two-b-1", 1,
                             datetime.fromtimestamp(engine.context.state.current_time,
                                                    ZoneInfo(engine.context.scenario.timezone)),
                             engine.context.state.version)
    with pytest.raises(ProtocolError, match="disagree or repeat an agent"):
        engine.begin_decision_round([first, second])


def test_same_round_resubmit_is_idempotent_and_parameter_conflict_fails(engine_factory) -> None:
    """同轮重放同参动作返回原回执；参数冲突协议拒绝。"""
    engine = engine_factory()
    ctx = context(engine, "replay-round")
    engine.begin_decision_round([ctx])
    request = start_request(targets=["自己"], actions=["interact"], ways=["等待"])
    call = DirectToolCall(ctx, "same-action", "start_activity", request)
    first = engine.submit_action(call)
    second = engine.submit_action(call)
    assert second == first
    assert len(engine.decisions.round.pending_actions) == 1
    changed = start_request(targets=["自己"], actions=["sleep"], ways=["睡觉"])
    with pytest.raises(ProtocolError, match="different parameters"):
        engine.submit_action(DirectToolCall(ctx, "same-action", "start_activity", changed))


async def test_prepare_pending_work_review_rejects_missing_action(engine_factory) -> None:
    """未知动作标识的评审准备协议拒绝。"""
    engine = engine_factory()
    ctx = context(engine, "review-missing")
    engine.begin_decision_round([ctx])
    engine.submit_action(DirectToolCall(
        ctx, "action-review-missing", "start_activity",
        start_request(targets=["自己"], actions=["interact"], ways=["等待"])))
    with pytest.raises(ProtocolError, match="does not exist"):
        await engine.prepare_pending_work_review("ghost-action")
    # 非工作提交的挂起动作不进入评审（静默返回，不记录评审）。
    await engine.prepare_pending_work_review("action-review-missing")
    assert all(item.work_review is None for item in engine.decisions.round.pending_actions)


@pytest.mark.parametrize("kind", ["wait", "work"])
async def test_prepare_pending_work_review_during_independent_wait(engine_factory, kind) -> None:
    """等待占用位置时仍可执行瞬时工作提交，不打断已有活动。"""
    engine = engine_factory()
    if kind == "wait":
        _stage_submission(engine)
        engine.abort_decision_round("cover-submit", "busy-agent teardown")
        advance_until_ready(engine)
    else:
        from tests.life.conftest import stage_day_plan
        _at_task_start(engine)
        stage_day_plan(engine)
    if kind == "wait":
        request = start_request(targets=["自己"], actions=["interact"], ways=["等待"])
        tool = "start_activity"
    else:
        task_id = engine.context.scenario.tasks[0].task_id
        row = next(item for item in engine.observations._visible_task_rows(engine.context.state.agents[1])
                   if item["task_id"] == task_id)
        request = start_request(
            targets=[f"工作：{row['title']}"], actions=["interact"], ways=["工作"])
        tool = "start_activity"
    started = _act(engine, "busy-work-or-wait", tool, request)
    assert started.status == "in_progress"
    # 在等待（或工作）仍进行时给一次决策机会：播报只针对进行中活动的锚点，等待结束那一秒它已是待收尾。
    wake_by_mail(engine, offset=60)
    advance_until_ready(engine)
    assert engine.context.state.activities[started.activity_id].status == ActivityStatus.ACTIVE
    round_id = "busy-submit"
    ctx = context(engine, round_id)
    engine.begin_decision_round([ctx])
    engine.observe(ctx)
    call = DirectToolCall(ctx, f"action-{round_id}", "submit_work", _submission_action(engine))
    engine.submit_action(call)
    await engine.prepare_pending_work_review(call.action_id)
    pending = next(item for item in engine.decisions.round.pending_actions
                   if item.action_id == call.action_id)
    assert (pending.work_review is not None) == (kind == "wait")
    if kind == "work":
        assert not [event for event in engine.context.store.read_agent_events("model_request", 1)
                    if event.action_id == call.action_id]
    assert active_activities(engine.context.state, 1)
    engine.abort_decision_round(round_id, "test teardown")


async def test_prepare_pending_work_review_while_the_wait_awaits_conclusion(engine_factory) -> None:
    """同一场景晚到 900 秒：等待在决策机会前已结束、待收尾，这时提交工作不调用验收模型（与 _dispatch 同一判定），
    结算以 ACTIVITY_CONCLUSION_REQUIRED 拒绝（第七章「活动一结束就要结束它」）。"""
    engine = engine_factory()
    _stage_submission(engine)
    engine.abort_decision_round("cover-submit", "busy-agent teardown")
    advance_until_ready(engine)
    started = _act(engine, "wait-before-submit", "start_activity",
                   start_request(targets=["自己"], actions=["interact"], ways=["等待"]))
    assert started.status == "in_progress"
    wake_by_mail(engine, offset=900)
    for step in range(16):
        if engine.context.state.activities[started.activity_id].status != ActivityStatus.ACTIVE:
            break
        if engine.scheduling_state().ready_agent_ids:
            _act(engine, f"keep-waiting-{step}", "report_status", report_request(
                activity_name(engine, started.activity_id), summary="继续等。"))
        else:
            advance_next(engine)
    waited = engine.context.state.activities[started.activity_id]
    assert waited.status == ActivityStatus.COMPLETED and waited.concluded_at is None
    assert engine.scheduling_state().ready_agent_ids == (1,)
    round_id = "submit-while-pending"
    ctx = context(engine, round_id)
    engine.begin_decision_round([ctx])
    engine.observe(ctx)
    call = DirectToolCall(ctx, f"action-{round_id}", "submit_work", _submission_action(engine))
    engine.submit_action(call)
    await engine.prepare_pending_work_review(call.action_id)
    pending = next(item for item in engine.decisions.round.pending_actions if item.action_id == call.action_id)
    assert pending.work_review is None and engine.reviews.reviewer.calls == 0
    result = engine.commit_decision_round(round_id)[0]
    assert (result.status, result.error_code) == ("rejected", "ACTIVITY_CONCLUSION_REQUIRED")
    assert not [event for event in engine.context.store.read_agent_events("model_request", 1)
                if event.data.get("purpose") == "work_review"]


def test_commit_decision_round_requires_every_expected_agent(engine_factory) -> None:
    """缺少人物提交的决策轮提交被拒绝。"""
    engine = engine_factory("multi-person.json")
    contexts = [context(engine, "partial", agent_id)
                for agent_id in engine.scheduling_state().ready_agent_ids]
    engine.begin_decision_round(contexts)
    engine.submit_action(DirectToolCall(
        contexts[0], "action-partial", "start_activity",
        start_request(targets=["自己"], actions=["interact"], ways=["等待"])))
    with pytest.raises(ProtocolError, match="every expected agent"):
        engine.commit_decision_round("partial")


def test_abort_decision_round_skips_memory_for_reused_action(engine_factory) -> None:
    """中止轮对重放动作不重复写入幂等记忆。"""
    engine = engine_factory()
    request = start_request(targets=["自己"], actions=["interact"], ways=["等待"])
    _act(engine, "abort-first", "start_activity", request)
    advance_until_ready(engine)
    ctx = context(engine, "abort-replay")
    engine.begin_decision_round([ctx])
    engine.submit_action(DirectToolCall(ctx, "action-abort-first",
                                        "start_activity", request))
    engine.abort_decision_round("abort-replay", BusinessRuleError("test abort"))
    stored = engine.context.state.processed_actions["action-abort-first"]
    assert "result" not in stored.model_dump() or stored.result is None or \
        stored.result.get("error_code") != "DECISION_ROUND_ABORTED"


def test_model_exchange_phase_and_duplicate_request_rejected(engine_factory) -> None:
    """非法阶段/尝试号与重复请求登记均协议拒绝。"""
    engine = engine_factory()
    ctx = context(engine, "exchange-guard")
    engine.begin_decision_round([ctx])
    request = {"model": "deepseek-flash", "messages": []}
    with pytest.raises(ProtocolError, match="phase or attempt is invalid"):
        engine.record_model_exchange(ModelCallEvidence(ctx, "call-bad", "request", 0, request))
    call_id = f"{ctx.decision_id}:model:0001:attempt:1"
    engine.record_model_exchange(ModelCallEvidence(ctx, call_id, "request", 1, request))
    with pytest.raises(ProtocolError, match="recorded twice"):
        engine.record_model_exchange(ModelCallEvidence(ctx, call_id, "request", 1, request))


def test_record_memory_update_rejects_invalid_evidence(engine_factory) -> None:
    """重复片段身份的记忆证据协议拒绝。"""
    engine = engine_factory()
    ctx = context(engine, "memory-guard")
    engine.begin_decision_round([ctx])
    digest = "a" * 64
    observation = engine.observe(ctx)
    evidence = MemoryUpdateEvidence(
        context=ctx, operation="append_episodes", created_episode_ids=("ep-1", "ep-1"),
        merged_episode_ids=(), episodes=({"id": "ep-1", "type": "reflection",
                                          "text": "主观回忆", "epistemic_status": "subjective"},),
        memory_before_sha256=digest, memory_sha256=digest,
        episodes_sha256=digest, state_sha256=digest, consolidated=False,
        observation_event_id=observation["observation_event_id"], verified_event_ids=())
    with pytest.raises(ProtocolError, match="evidence is invalid"):
        engine.record_memory_update(evidence)


def test_advance_guards_open_round_due_decision_and_boundary(engine_factory) -> None:
    """开轮中、决策到期与边界不符的推进均协议拒绝。"""
    engine = engine_factory()
    zone = ZoneInfo(engine.context.scenario.timezone)
    target = datetime.fromtimestamp(engine.context.state.current_time + 900, zone)
    with pytest.raises(ProtocolError, match="while an agent decision is due"):
        engine.advance(900, target)
    ctx = context(engine, "advance-guard")
    engine.begin_decision_round([ctx])
    with pytest.raises(ProtocolError, match="during an open decision round"):
        engine.advance(900, target)
    engine.observe(ctx)
    engine.submit_action(DirectToolCall(
        ctx, "action-advance-guard", "start_activity",
        start_request(targets=["自己"], actions=["interact"], ways=["等待"])))
    engine.commit_decision_round("advance-guard")
    with pytest.raises(ProtocolError, match="next scheduling boundary"):
        engine.advance(601, datetime.fromtimestamp(engine.context.state.current_time + 600, zone))


def test_advance_rejects_tick_consistent_jump_past_next_scheduling_boundary(engine_factory) -> None:
    """时钟边界跳跃守卫（docs/dev/SPEC-as2-integration.md#spec-as2-integration-特性规格agentsociety-2-集成与改动「决策调度改动」、docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「共享时钟」；Uncle Bob 条款 docs/VERIFICATION.md#verification-worldmock-验收标准「源码突变审查」）。

    推进必须同时满足三条：tick 严格为正、t = 上次确认时刻 + tick、且命中
    下一调度边界；不得跨过新开始活动更早的结束边界。本用例瞄准
    `target_epoch != self._next_epoch()` 子句：tick 算术正确（t = now+tick）
    但目标距离是真实边界的两倍——只有该子句会拒绝（601/600 的 tick 失配
    用例测不到它）。正例：精确推进到边界被接受。
    """
    engine = engine_factory()
    zone = ZoneInfo(engine.context.scenario.timezone)
    ctx = context(engine, "boundary-jump")
    engine.begin_decision_round([ctx])
    engine.observe(ctx)
    engine.submit_action(DirectToolCall(
        ctx, "action-boundary-jump", "start_activity",
        start_request(targets=["自己"], actions=["interact"], ways=["等待"])))
    engine.commit_decision_round("boundary-jump")
    now = engine.context.state.current_time
    next_epoch = engine.clock._next_epoch()
    assert next_epoch > now
    with pytest.raises(ProtocolError, match="next scheduling boundary"):
        engine.advance(2 * (next_epoch - now),
                       datetime.fromtimestamp(now + 2 * (next_epoch - now), zone))
    engine.advance(next_epoch - now, datetime.fromtimestamp(next_epoch, zone))
    assert engine.context.state.current_time == next_epoch


def test_fail_run_rejects_empty_error(engine_factory) -> None:
    """空错误串的失败通知协议拒绝。"""
    engine = engine_factory()
    with pytest.raises(ProtocolError, match="must not be empty"):
        engine.fail_run("")


def test_apply_counterfactual_requires_strict_model_and_valid_branch(engine_factory, tmp_path) -> None:
    """非严格模型、分支证据无效与目标人物未知均协议拒绝。"""
    engine = engine_factory()
    with pytest.raises(ProtocolError, match="strict life model"):
        engine.apply_counterfactual({"kind": "set_balance_fen"})

    from tests.life.test_counterfactual import _digest, _intervention, _plain_branch, _source_event
    source = _source_event(engine, 1)
    intervention = _intervention(source)
    _plain_branch(engine, tmp_path / "plain-branch")
    branch_dir = tmp_path / "plain-branch"
    manifest = json.loads((branch_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest.update({"branch_kind": "counterfactual",
                     "intervention_sha256": _digest(intervention),
                     "parent_checkpoint_id": "checkpoint-forged"})
    (branch_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    forged = LifeEngine.open(ENVIRONMENT_ROOT / "scenarios/single-person.json", branch_dir)
    try:
        with pytest.raises(ProtocolError, match="creation evidence is invalid"):
            forged.apply_counterfactual(intervention)
    finally:
        forged.close()

    from tests.life.test_counterfactual import _branch
    ghost = _intervention(source, agent_id=99)
    child = _branch(engine, tmp_path / "ghost-agent", ghost)
    try:
        with pytest.raises(ProtocolError, match="target agent is invalid"):
            child.apply_counterfactual(ghost)
    finally:
        child.close()


def test_checkpoint_requires_complete_agent_snapshot_set(engine_factory) -> None:
    """快照集合不完整的检查点提交被拒绝。"""
    engine = engine_factory()
    with pytest.raises(IntegrityError, match="incomplete"):
        engine.checkpoint({})


def test_runtime_identity_validation_rejects_each_mismatch(engine_factory) -> None:
    """运行时身份三查（场景/语料/运行号）各自拒绝。"""
    engine = engine_factory()
    pristine = engine.context.state
    engine.context.state = pristine.model_copy(update={"scenario_sha256": "0" * 64})
    with pytest.raises(IntegrityError, match="scenario identity mismatch"):
        engine.context.validate()
    engine.context.state = pristine.model_copy(update={"content_manifest_sha256": "0" * 64})
    with pytest.raises(IntegrityError, match="corpus identity mismatch"):
        engine.context.validate()
    engine.context.state = pristine.model_copy(update={"run_id": "other-run"})
    with pytest.raises(IntegrityError, match="run identity mismatch"):
        engine.context.validate()


def test_run_identity_disagreements_rejected_at_create(tmp_path) -> None:
    """运行身份与冻结场景人物/窗口/时区不一致在创建即拒绝。"""
    scenario_path = ENVIRONMENT_ROOT / "scenarios/single-person.json"
    scenario, _ = load_scenario(scenario_path)
    with pytest.raises(IntegrityError, match="agent ids disagree"):
        LifeEngine.create(scenario_path, tmp_path / "run-agents",
                          _run_identity(scenario, agent_ids=(1, 2)))
    with pytest.raises(IntegrityError, match="window disagrees"):
        LifeEngine.create(scenario_path, tmp_path / "run-window",
                          _run_identity(scenario, end_at="2027-01-01T00:00:00+08:00"))
    with pytest.raises(IntegrityError, match="timezone disagrees"):
        LifeEngine.create(scenario_path, tmp_path / "run-zone",
                          _run_identity(scenario, timezone="Asia/Tokyo"))


def test_context_and_call_guards_reject_each_invalid_shape(engine_factory) -> None:
    """宿主上下文/工具调用各拒绝形态。"""
    engine = engine_factory()
    with pytest.raises(ProtocolError, match="AgentSociety2 DecisionContext"):
        engine.begin_decision_round([SimpleNamespace()])
    wrong_run = DecisionContext("other-run", "r", "d-1", 1,
                                datetime.fromtimestamp(engine.context.state.current_time,
                                                       ZoneInfo(engine.context.scenario.timezone)),
                                engine.context.state.version)
    with pytest.raises(ProtocolError, match="run or agent is invalid"):
        engine.begin_decision_round([wrong_run])
    stale = DecisionContext(engine.context.state.run_id, "r2", "d-2", 1,
                            datetime.fromtimestamp(engine.context.state.current_time + 1,
                                                   ZoneInfo(engine.context.scenario.timezone)),
                            engine.context.state.version)
    with pytest.raises(ProtocolError, match="observation time is stale"):
        engine.begin_decision_round([stale])
    versioned = DecisionContext(engine.context.state.run_id, "r3", "d-3", 1,
                                datetime.fromtimestamp(engine.context.state.current_time,
                                                       ZoneInfo(engine.context.scenario.timezone)),
                                engine.context.state.version + 5)
    with pytest.raises(ProtocolError, match="world version is stale"):
        engine.begin_decision_round([versioned])

    ctx = context(engine, "call-guards")
    request = start_request(targets=["自己"], actions=["interact"], ways=["等待"])
    with pytest.raises(ProtocolError, match="requires an open decision round"):
        engine.submit_action(DirectToolCall(ctx, "a-1", "start_activity", request))
    engine.begin_decision_round([ctx])
    engine.observe(ctx)
    with pytest.raises(ProtocolError, match="another decision round"):
        engine.submit_action(DirectToolCall(
            DecisionContext(engine.context.state.run_id, "other-round", "d-4", 1,
                            datetime.fromtimestamp(engine.context.state.current_time,
                                                   ZoneInfo(engine.context.scenario.timezone)),
                            engine.context.state.version),
            "a-2", "start_activity", request))
    with pytest.raises(ProtocolError, match="does not match its tool"):
        engine.submit_action(DirectToolCall(ctx, "a-3", "start_activity",
                                            PurchaseOrderRequest(
                                                items=(PurchaseItem(supply_id="food-6923644266066",
                                                                   quantity=1, price_version="v1",
                                                                   recipient_id="agent:1"),),
                                                fulfillment="delivery", destination_id="home")))
    engine.submit_action(DirectToolCall(ctx, "a-4", "start_activity", request))
    with pytest.raises(ProtocolError, match="already submitted"):
        engine.submit_action(DirectToolCall(ctx, "a-5", "start_activity", request))
    with pytest.raises(ProtocolError, match="not open"):
        engine.commit_decision_round("wrong-round-id")
    engine.commit_decision_round("call-guards")


def test_submit_work_guards_review_presence_and_identity(engine_factory) -> None:
    """未留证与身份不符的提交分别协议拒绝。"""
    engine = engine_factory()
    call = _stage_submission(engine)
    pending = next(item for item in engine.decisions.round.pending_actions
                   if item.action_id == call.action_id)
    with pytest.raises(ProtocolError, match="no recorded semantic review"):
        engine.actions.public_work_review(pending)
    with pytest.raises(ProtocolError, match="no recorded semantic review"):
        engine.commit_decision_round("cover-submit")
    engine.abort_decision_round("cover-submit", "teardown")

    engine = engine_factory()
    call = _stage_submission(engine)
    asyncio.get_event_loop_policy()
    asyncio.run(engine.prepare_pending_work_review(call.action_id))
    swapped = _submission_action(engine).model_copy(
        update={"deliverable": MeetingMinutes(
            summary="A different summary entirely.",
            decisions=("Different decision.",),
            action_items=("Different action.",))})
    engine.decisions.round.pending_actions[0] = engine.decisions.round.pending_actions[0].model_copy(
        update={"request": swapped.model_dump(mode="json")})
    with pytest.raises(ProtocolError, match="identity disagrees"):
        engine.commit_decision_round("cover-submit")


def test_rework_rejection_at_limit_keeps_review_feedback(engine_factory) -> None:
    """达到同刻上限的返工仍是业务结果并公开评审反馈；没有进行中活动时同一秒再决策并进入受限轮。"""
    engine = engine_factory()
    _at_task_start(engine)
    _complete_required_work(engine)
    limit = engine.context.scenario.max_same_moment_decisions
    assert not active_activities(engine.context.state, 1)
    _stage_agent(engine, same_moment_rounds=limit - 1)
    engine.reviews.reviewer = _AcceptingReviewer(rejected=True)
    round_id = "rework-limit"
    ctx = context(engine, round_id)
    engine.begin_decision_round([ctx])
    engine.observe(ctx)
    engine.submit_action(DirectToolCall(ctx, f"action-{round_id}", "submit_work",
                                        _submission_action(engine)))
    asyncio.run(engine.prepare_pending_work_review(f"action-{round_id}"))
    result = engine.commit_decision_round(round_id)[0]
    assert result.status.value == "rejected"
    assert result.error_code == "WORK_REQUIRES_REWORK"
    assert "work_review" in result.data
    assert result.message == result.data["reason"] == "The submitted work needs revision before acceptance."
    assert result.data["same_moment_round"] == limit
    assert result.data["max_same_moment_decisions"] == limit
    assert result.data["next_decision_at"] == engine.context.state.current_time
    agent = engine.context.state.agents[1]
    assert agent.next_decision_at == engine.context.state.current_time
    assert agent.same_moment_stage == SameMomentStage.START_OR_RESUME
    assert agent.pending_wake_reasons == ["rejection_feedback", "no_active_activity"]


# ---------------------------------------------------------------------------
# engine.py：活动边界守卫与业务拒绝分支
# ---------------------------------------------------------------------------


def test_boundary_action_requires_owned_current_activity(engine_factory) -> None:
    """控制工具只能点名本轮可见的本人活动：未知名称与他人的活动名都以 ACTION_REQUEST_INVALID 业务拒绝，
    已有活动照常进行（时间与能量规格第七章「播报与控制都作用于活动」）。"""
    from tests.life.conftest import stage_day_plan

    engine = engine_factory("multi-person.json")
    for agent_id in engine.context.state.agents:
        stage_day_plan(engine, agent_id)
    _, results = submit_round(engine, {
        1: ("start_activity", start_request(targets=["自己"], actions=["interact"], ways=["等待"])),
        2: ("start_activity", start_request(targets=["自己"], actions=["sleep"], ways=["睡觉"]))}, "own-start")
    assert {item.agent_id: item.status.value for item in results} == {1: "in_progress", 2: "in_progress"}
    other = next(item.activity_id for item in results if item.agent_id == 2)
    wake_by_mail(engine, agent_id=1, offset=60)
    _advance_until_agent_ready(engine, 1)
    assert engine.scheduling_state().ready_agent_ids == (1,)
    for index, name in enumerate(("不存在的活动", activity_name(engine, other))):
        wrong_owner = _act(engine, f"own-wrong-{index}", "pause_activity",
                           pause_request(name, summary="先暂停"))
        assert wrong_owner.status.value == "rejected"
        assert wrong_owner.error_code == "ACTION_REQUEST_INVALID"
    assert {item.status for item in engine.context.state.activities.values()} == {ActivityStatus.ACTIVE}


def test_unknown_activity_controls_reject_when_idle(engine_factory) -> None:
    """空闲人物播报、暂停或结束未知活动名都以 ACTION_REQUEST_INVALID 业务拒绝，并在同一秒再决策（第一章规则 2）。"""
    engine = engine_factory()
    start = engine.context.state.current_time
    requests = [("report_status", report_request("不存在的活动", summary="还在进行")),
                ("pause_activity", pause_request("不存在的活动", summary="先暂停")),
                ("conclude_activity", conclude_request("不存在的活动", summary="已结束"))]
    for index, (tool, request) in enumerate(requests):
        ghost = _act(engine, f"own-missing-{index}", tool, request)
        assert ghost.status.value == "rejected" and ghost.error_code == "ACTION_REQUEST_INVALID"
        assert engine.context.state.current_time == start and engine.scheduling_state().ready_agent_ids == (1,)


@pytest.mark.parametrize("existing", [False, True])
def test_purchase_by_unlisted_name_resolves_only_existing_supplies(engine_factory, existing) -> None:
    """指名购买（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第五章「指名购买」，裁决 #19）：不存在的名称以 SUPPLY_NOT_FOUND 拒绝，
    世界不变；真实存在但未呈现、没有可见凭据的供给按标签到店购买，按当前报价结算，库存与持有各变一份。"""
    from tests.life.conftest import stage_day_plan
    from tests.life.test_commerce import _stage_open
    from worldmock.life.discovery import supply_row

    engine = engine_factory()
    if existing:
        _stage_open(engine)
        staged = engine.context.state.model_copy(deep=True)
        for agent in staged.agents.values():
            agent.body_settled_at = staged.current_time
        staged.agents[1].location_id = "market"
        _commit_staged(engine, staged)
        stage_day_plan(engine)
    ctx = context(engine, "buy-invisible")
    engine.begin_decision_round([ctx])
    engine.observe(ctx)
    object_name, way = "不存在的供给", "远程下单"
    if existing:
        shown = set(engine.observations.pending_visible_supplies[1])
        discovery = engine.observations._discovery_context(engine.context.state.agents[1])
        hidden = [row for supply in engine.context.scenario.supplies if supply.location_id == "market"
                  for row in [supply_row(discovery, supply)]
                  if row["supply_id"] not in shown and row["purchase_allowed"] and row["pickup_allowed"]
                  and row["purchase_price_fen"] <= engine.context.state.accounts["agent:1"]]
        assert shown and hidden
        supply = next(item for item in engine.context.scenario.supplies
                      if item.supply_id == hidden[0]["supply_id"])
        product = next(item for item in engine.context.scenario.products
                       if item.product_id == supply.product_id)
        place = next(item for item in engine.context.scenario.locations
                     if item.location_id == supply.location_id)
        object_name, way = f"{place.name}出售的{product.name}", "到店"
    before = engine.context.state.model_copy(deep=True)
    request = start_request(targets=[object_name], actions=["obtain"], ways=[way])
    engine.submit_action(DirectToolCall(ctx, "action-buy-invisible", "start_activity", request))
    result = engine.commit_decision_round("buy-invisible")[0]
    if existing:
        assert (result.status.value, result.error_code) == ("completed", None)
        [order] = result.data["orders"]
        assert (order["supply_id"], order["total_price_fen"]) == (supply.supply_id, hidden[0]["purchase_price_fen"])
        assert engine.context.state.supply_stock[supply.supply_id] == before.supply_stock[supply.supply_id] - 1
        assert engine.context.state.agents[1].inventory["market"][supply.product_id] == (
            before.agents[1].inventory.get("market", {}).get(supply.product_id, 0) + 1)
        assert engine.context.state.accounts["agent:1"] == before.accounts["agent:1"] - hidden[0]["purchase_price_fen"]
        return
    assert (result.status.value, result.error_code) == ("rejected", "SUPPLY_NOT_FOUND")
    assert engine.context.state.accounts == before.accounts
    assert engine.context.state.supply_stock == before.supply_stock
    assert engine.context.state.agents[1].inventory == before.agents[1].inventory
    assert engine.context.state.deliveries == before.deliveries
    assert engine.context.state.pending_events == before.pending_events


def _goals_with_wake_events(engine, offsets: tuple[int, ...]) -> PlanDayRequest:
    """空目标不制造时钟，活动分支所需的精确机会由真实邮件事件提供。"""
    for offset in offsets:
        wake_by_mail(engine, offset=offset)
    return PlanDayRequest(goals=[])


def test_plan_day_rejects_duplicate_and_invalid_goals(engine_factory) -> None:
    """零点计划的空清单也只登记一次：同日醒来后不再生成，系统入口的重复登记是协议错误；绕过构造器的非法目标在
    环境严格边界拒绝：人物修订为 ACTION_REQUEST_INVALID，系统入口为协议错误且不落盘（第四章）。"""
    engine = engine_factory()
    assert settle_day_plans(engine)[0]["goals"] == 0
    staged = engine.context.state.model_copy(deep=True)
    staged.agents[1] = staged.agents[1].model_copy(
        update={"pending_wake_reasons": ["wake_from_sleep"],
                "next_decision_at": staged.current_time}, deep=True)
    _commit_staged(engine, staged)
    again = StubPlanDayClient()
    assert drive(settle_pending_day_plans(engine, client=again)) == [] and again.calls == []
    with pytest.raises(ProtocolError, match="already generated"):
        engine.register_day_plan(1, PlanDayRequest(goals=[]), [], [])
    invalid_goal = PlanGoalInput.model_construct(intent_kind="unknown", goal="推进目标", remind_time=None, around_time=None)
    forged = ReviseDayPlanRequest.model_construct(goals=(invalid_goal,))
    invalid = _act(engine, "revise-invalid", "revise_day_plan", forged)
    assert invalid.status.value == "rejected"
    assert invalid.error_code == "ACTION_REQUEST_INVALID"
    assert engine.context.state.agents[1].day_plan.goals == ()

    fresh = engine_factory()
    committed = fresh.context.store.last_event_seq
    with pytest.raises(ProtocolError, match="Generated day plan is invalid"):
        fresh.register_day_plan(1, PlanDayRequest.model_construct(goals=(invalid_goal,)), [], [])
    assert fresh.context.state.agents[1].day_plan is None and not fresh.context.state.agents[1].plan_submitted_dates
    assert fresh.context.store.last_event_seq == committed


def test_revise_day_plan_requires_existing_plan(engine_factory) -> None:
    """无当日目标时修订被拒绝。"""
    engine = engine_factory()
    result = _act(engine, "revise-none", "revise_day_plan", ReviseDayPlanRequest(
        goals=[PlanGoalInput(intent_kind="休息", goal="歇一会儿", remind_time=None, around_time=None),]))
    assert result.status.value == "rejected"
    assert result.error_code == "PLAN_NOT_FOUND"


def test_send_message_rejects_unknown_recipient_and_delivers_to_none_policy_contact(engine_factory) -> None:
    """未知收件人业务拒绝（引擎边界转换，非裸 ValueError 杀 run）；给 none 策略联系人的消息照常送达、不回复：
    不登记模型回复请求，也不预排回复（第五章「动作照常列出，做错了再说明」，不再以 CONTACT_REPLY_NOT_MODEL 拒绝）。"""
    engine = engine_factory()
    rejected = _act(engine, "say-ghost", "send_message",
                    SendMessageActionRequest(recipient_name="不存在的联系人", text="hi"))
    assert rejected.status.value == "rejected"
    assert rejected.error_code == "RECIPIENT_NOT_FOUND"

    multi = engine_factory("multi-person.json")
    contact = next(item for item in multi.context.scenario.agents[0].contacts if item.contact_id == "contact-chenxi-colleague")
    assert contact.reply_policy == "none"
    observed = multi.observe(context(multi, "colleague-preview"))
    colleague = next(row for row in observed["interaction_bindings"]
                     if row["object_id"] == "contact-chenxi-colleague")
    delivered = _act_ready(multi, "say-colleague", "send_message",
                           SendMessageActionRequest(recipient_name=colleague["object"], text="hi"))
    assert delivered.status.value == "completed" and delivered.error_code is None
    assert delivered.data["recipient_id"] == "contact-chenxi-colleague"
    events = multi.context.store.read_events(multi.context.store.last_event_seq)
    assert [event.data["recipient_id"] for event in events if event.data.get("kind") == "message_arrival"
            and event.data.get("message_id") == delivered.data["message_id"]] == ["contact-chenxi-colleague"]
    assert not [event for event in events if event.data.get("kind") == "model_npc_reply_pending"]
    assert not [item for item in multi.context.state.pending_events if item.kind == "message_arrival"]
    assert not multi.context.state.agents[1].inbox


def test_call_rejects_unknown_contact(engine_factory) -> None:
    """未声明联系人的通话被拒绝。"""
    engine = engine_factory()
    result = _act(engine, "call-ghost", "start_activity",
                  start_request(targets=["联系人：不存在的人"], actions=["interact"], ways=["通话"]))
    assert result.status.value == "rejected"
    assert result.error_code == "ACTION_REQUEST_INVALID"


def test_ready_is_false_after_run_end_and_terminal_action_missing(engine_factory) -> None:
    """运行结束后不再提供决策；缺失原始动作的终态登记失败关闭。"""
    engine = engine_factory()
    staged = engine.context.state.model_copy(update={"current_time": engine.context.state.end_time})
    engine.context.state = staged
    assert engine.clock._ready(1) is False
    result = ActionResult(action_id="ghost-action", agent_id=1,
                          status=ActionStatus.COMPLETED, occurred_at=0,
                          message="test")
    with pytest.raises(ProtocolError, match="origin action is missing"):
        _terminal_action_records(engine.context.state, result)


def test_dedupe_and_material_and_visibility_helpers_guard_shapes(engine_factory) -> None:
    """字幕去重/材料登记/浏览可见对缺失与畸形数据失败关闭。"""
    engine = engine_factory()
    activity = _activity_state(kind=ActivityKind.WATCH)
    dedupe_revealed_cues(activity)  # 无 revealed 数据：直接返回
    activity.data["revealed"] = {"position_before_seconds": "not-a-number", "cues": []}
    dedupe_revealed_cues(activity)  # 形畸形：直接返回
    agent = engine.context.state.agents[1]
    unchanged = record_material_reveal(agent, "", {"start_char": 0, "end_char": 10}, 0)
    assert unchanged is agent
    phrase = _activity_progress_phrase(_activity_state(elapsed_seconds=60,
                                                       planned_duration_seconds=120))
    assert phrase == "还剩约1分钟"
    control = _paused_activity_control(_activity_state(elapsed_seconds=60,
                                                       planned_duration_seconds=120))
    assert control["remaining_seconds"] == 60
    assert set(control) == {"activity_id", "kind", "remaining_seconds"}


def test_change_on_social_activity_uses_social_domain(engine_factory) -> None:
    """交谈会话在结束边界进入社交域取消并保留已结算进度。"""
    from tests.life.conftest import stage_day_plan

    engine = engine_factory("multi-person.json")
    stage_day_plan(engine, 1)
    stage_day_plan(engine, 2)
    requests = {1: ("start_activity", start_request(targets=["人物：李凡"], actions=["interact"],
                                                           ways=["交谈"])),
        2: ("start_activity", start_request(targets=["自己"], actions=["interact"], ways=["等待"]))}
    _, results = submit_round(engine, requests, "chat-start")
    chat = next(item for item in results if item.agent_id == 1)
    assert chat.status.value == "in_progress"
    activity_id = chat.activity_id
    wake_by_mail(engine, offset=60)
    _advance_until_agent_ready(engine, 1)
    hung_up = _act_ready(engine, "chat-cancel", "conclude_activity",
                         conclude_request(activity_name(engine, activity_id), summary="聊完了"))
    assert hung_up.status.value == "completed"
    activity = engine.context.state.activities[activity_id]
    assert activity.status == ActivityStatus.CANCELLED
    assert activity.boundary_type == "cancelled" and activity.interruption_reason == "agent_cancelled"
    assert activity.concluded_at == engine.context.state.current_time
    assert not active_activities(engine.context.state, 1)


# ---------------------------------------------------------------------------
# engine._presented_messages：已读 = 本决策观察中全文已呈现的消息
# ---------------------------------------------------------------------------


def _observed_with_presented(engine, monkeypatch, round_id: str, presented: list[str]):
    """开启单人决策轮并观察；观察数据只带给定的 presented_message_ids（其余观察键与本判定无关）。"""
    stage_day_plan(engine)
    monkeypatch.setattr(engine.observations, '_observation_data', lambda agent: {'agent_id': agent.agent_id, 'presented_message_ids': list(presented)})
    decision = context(engine, round_id)
    engine.begin_decision_round([decision])
    engine.observe(decision)
    return decision


def _pending_for(decision, observed_at: int) -> SimpleNamespace:
    """与观察配对所需的待结算动作字段。"""
    return SimpleNamespace(agent_id=decision.agent_id, decision_id=decision.decision_id, observed_at=observed_at)


def test_presented_messages_come_from_the_decision_observation(engine_factory, monkeypatch) -> None:
    """已读只取本决策、本秒观察的 presented_message_ids；没有观察（引擎直驱）时什么也没呈现。"""
    engine = engine_factory()
    decision = _observed_with_presented(engine, monkeypatch, "presented", ["message-1", "message-2"])
    now = engine.context.state.current_time
    assert engine.decisions._presented_messages(_pending_for(decision, now)) == frozenset({"message-1", "message-2"})
    other = SimpleNamespace(agent_id=decision.agent_id, decision_id="decision-without-observation", observed_at=now)
    assert engine.decisions._presented_messages(other) == frozenset()
    assert engine.decisions._presented_messages(_pending_for(decision, now + 1)) == frozenset()


def test_ambiguous_decision_observation_is_a_protocol_error(engine_factory, monkeypatch) -> None:
    """同一决策同一秒有两条观察时无法判定呈现了什么，按协议错误拒绝。"""
    engine = engine_factory()
    decision = _observed_with_presented(engine, monkeypatch, "ambiguous", ["message-1"])
    engine.observe(decision)
    with pytest.raises(ProtocolError, match="Decision observation is ambiguous"):
        engine.decisions._presented_messages(_pending_for(decision, engine.context.state.current_time))
