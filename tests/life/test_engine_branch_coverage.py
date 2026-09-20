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

import asyncio
import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agentsociety2.agent.base import MemoryUpdateEvidence, ModelCallEvidence
from agentsociety2.society import DecisionContext, DirectToolCall

from tests.life.conftest import advance_next, advance_until_ready, context, submit_round, wake_by_mail
from tests.life.test_scenario import _assert_invalid, _payload, _replace
from tests.life.test_work_review import _at_task_start, _complete_required_work, _paraphrased_submission
from worldmock.errors import BusinessRuleError, IntegrityError, ProtocolError
from worldmock.life import felt, projection, rhythm
from worldmock.life.body import (
    ActivityFactors,
    apply_food,
    felt_levels,
    felt_values,
    interpolate,
    maximum_energy_duration_seconds,
    next_body_boundary_seconds,
    settle_body,
)
from worldmock.life.body import body_observation
from worldmock.life.decision_start import maybe_monologue_start
from worldmock.life.engine import (
    LifeEngine,
    _activity_progress_phrase,
    _dedupe_revealed_cues,
    _paused_activity_control,
    _record_material_reveal,
    _terminal_action_records,
)
from worldmock.life.models import (
    ActionStatus,
    ActivityKind,
    ActivityState,
    ActivityStatus,
    ActionResult,
    ChangeActivityRequest,
    ChangePlaybackSpeedRequest,
    ConsumeRequest,
    ContinueActivityRequest,
    CounterfactualIntervention,
    CurvePoint,
    deepseek_strict_schema,
    GameActionRequest,
    MeetingMinutes,
    ModelIdentity,
    PendingAction,
    PlanDayRequest,
    PlanSegmentInput,
    PurchaseItem,
    PurchaseOrderRequest,
    ReviseDayPlanRequest,
    RunIdentity,
    SendMessageRequest,
    SetActivityStateRequest,
    StartBasicActivityRequest,
    StartSocialActivityRequest,
    StartTargetedActivityRequest,
    SubmitWorkRequest,
    WorldState,
)
from worldmock.life.observe import _recent_meal_counts
from worldmock.life.scenario import (
    BodyConfig,
    LifeScenario,
    MotivationProfile,
    parse_scenario_document,
    load_scenario,
)
from worldmock.footprints.models import EventDraft, INNER_MONOLOGUE_FIELD


# ---------------------------------------------------------------------------
# 共享夹具与助手
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def body_config() -> BodyConfig:
    """读取正式冻结身体参数（65kg/1600/2400/800/400）。"""
    return load_scenario("scenarios/white-collar-12-weeks.json")[0].body


@pytest.fixture(scope="module")
def formal() -> LifeScenario:
    """读取正式场景文档作为负例模板。"""
    return load_scenario("scenarios/white-collar-12-weeks.json")[0]


def _act(engine, round_id: str, tool_name: str, request, agent_id: int = 1):
    """单人物真实轮：观察、登记并结算。"""
    ctx = context(engine, round_id, agent_id)
    engine.begin_decision_round([ctx])
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
        submit_round(engine, {rid: ("start_basic_activity",
                                    StartBasicActivityRequest(activity_type="wait",
                                                              planned_duration_seconds=60))
                              for rid in ready}, f"filler-{index}")


def _act_ready(engine, round_id: str, tool_name: str, request, agent_id: int = 1):
    """向当前就绪的每个人物提交一轮（其他人物等待 60 秒），返回主角结果。"""
    ready = list(engine.scheduling_state().ready_agent_ids)
    requests = {}
    for rid in ready:
        requests[rid] = (tool_name, request) if rid == agent_id else (
            "start_basic_activity",
            StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=60))
    _, results = submit_round(engine, requests, round_id)
    return next(item for item in results if item.agent_id == agent_id)


def _commit_staged(engine, staged) -> None:
    """把测试改造过的状态经存储落盘（保持版本连续），再换入引擎。"""
    staged.version += 1
    engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time,
        visibility="system", data={"kind": "test_state_staged"})])
    engine.state = staged


def _stage_agent(engine, **updates) -> None:
    """改造主角状态并落盘（保留其余世界事实）。"""
    staged = engine.state.model_copy(deep=True)
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
            provider="deepseek",
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
    engine._work_reviewer = _AcceptingReviewer(rejected)
    round_id = "cover-submit"
    ctx = context(engine, round_id)
    engine.begin_decision_round([ctx])
    call = DirectToolCall(ctx, f"action-{round_id}", "submit_work", _paraphrased_submission())
    engine.submit_action(call)
    return call


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


def test_felt_values_expose_five_numeric_signals(body_config) -> None:
    """体感数值组含五项信号，餐时窗口只放大食欲（docs/dev/SPEC-life-runtime.md「其他体感分级」）。"""
    body = felt_body(1600, social_need=0.4, mood_valence=0.2)
    plain = felt_values(body, 22.0)
    in_window = felt_values(body, 12.5, felt.MealWindows(lunch=(12.0, 13.5)))
    assert set(plain) == {"appetite", "sleepiness", "entertainment_need",
                          "social_need", "mood_valence"}
    assert plain["social_need"] == 0.4 and plain["mood_valence"] == 0.2
    # 餐时项在窗口内加 0.15（experiment_assumption，docs/dev/SPEC-life-runtime.md「其他体感分级」）
    assert in_window["appetite"] == pytest.approx(plain["appetite"] + 0.15)
    assert plain["sleepiness"] > 0


def test_felt_levels_render_five_band_texts(body_config) -> None:
    """体感分级组输出五项中文分级文本（docs/dev/SPEC-life-runtime.md「其他体感分级」）。"""
    body = felt_body(1600, social_need=0.7, mood_valence=0.5, entertainment_need=0.7)
    levels = felt_levels(body, 20.0)
    assert set(levels) == {"appetite", "sleepiness", "entertainment", "social", "mood"}
    assert levels["social"] == "有点孤单"
    assert levels["mood"] == "很开心"
    assert levels["entertainment"] == "很想放松"


def test_body_observation_alert_ladder_and_two_group_projection(body_config) -> None:
    """观察投影分级/数值两组；能量告警按 400/800 阈值分档（docs/dev/SPEC-life-runtime.md「其他体感分级」「数值与分级的分离」）。"""
    emergency = body_observation(felt_body(300), body_config, 12.0)
    hungry = body_observation(felt_body(600), body_config, 12.0)
    calm = body_observation(felt_body(1600), body_config, 12.0)
    assert emergency["alert"] == "emergency_energy"
    assert hungry["alert"] == "hungry"
    assert calm["alert"] is None
    for observation in (emergency, hungry, calm):
        assert set(observation["levels"]) == {"appetite", "sleepiness",
                                              "entertainment", "social", "mood"}
        assert set(observation["values"]) == {"energy_kcal", "sleep_pressure", "sleepiness",
                                              "sleep_deficit_hours", "entertainment_need",
                                              "social_need", "mood_valence"}
    assert emergency["values"]["energy_kcal"] == 300


def test_entertainment_need_capped_at_one_when_already_full(body_config) -> None:
    """娱乐需要已到 1 时清醒累积不再入账，期末钳制为 1。"""
    result = settle_body(felt_body(1600, entertainment_need=1.0),
                         ActivityKind.WORK, 3600, body_config)
    assert result.after.entertainment_need == 1.0


def test_wait_keeps_slow_net_accrual_from_full_need(body_config) -> None:
    """wait=边等边刷：需要已满时被动消费按费率小幅回降（净语义保持）。"""
    result = settle_body(felt_body(1600, entertainment_need=1.0),
                         ActivityKind.WAIT, 3600, body_config)
    assert result.after.entertainment_need == pytest.approx(1.0 - 0.05)


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
    """无曲线活动+娱乐速率 0+能量已低于全部阈值+无昼夜输入→无身体边界。"""
    quiet = body_config.model_copy(update={"entertainment_need_per_awake_hour": 0})
    body = felt_body(300, social_need=1.0)
    assert next_body_boundary_seconds(body, ActivityKind.WAIT, quiet, None, None) is None
    # 娱乐速率 0 的资源在昼夜输入存在时也不产生候选（跳过continue 分支）。
    with_hour = next_body_boundary_seconds(felt_body(1600, social_need=1.0),
                                           ActivityKind.WAIT, quiet, None, 12.0)
    assert with_hour is not None and with_hour > 0


def test_next_body_boundary_covers_entertainment_band_edges(body_config) -> None:
    """正常清醒等待给出首个娱乐分级边界（正例）。"""
    seconds = next_body_boundary_seconds(felt_body(1600, entertainment_need=0.0),
                                         ActivityKind.WAIT, body_config, None, 12.0)
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
    fields = dict(activity_id="activity-x", agent_id=1, kind=ActivityKind.WAIT,
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


def test_world_state_rejects_dangling_current_activity(engine_factory) -> None:
    """人物当前活动引用不存在时世界状态拒绝校验。"""
    engine = engine_factory()
    staged = engine.state.model_copy(deep=True)
    staged.agents[1] = staged.agents[1].model_copy(update={"current_activity_id": "ghost"})
    with pytest.raises(ValidationError, match="current activity does not exist"):
        WorldState.model_validate(staged.model_dump())


def test_travel_target_requires_destination_and_mode() -> None:
    """出行启动必须携带目的地与方式（领域内统一请求模型）。"""
    from worldmock.life.models import StartActivityRequest
    with pytest.raises(ValidationError, match="required for travel"):
        StartActivityRequest(activity_type="travel", planned_duration_seconds=600)


def test_playback_speed_only_for_change_speed_operation() -> None:
    """非变速操作携带速度被拒绝。"""
    with pytest.raises(ValidationError, match="only for change_speed"):
        ChangeActivityRequest(activity_id="a", operation="pause", playback_speed=2)


def test_plan_segment_span_must_be_positive() -> None:
    """计划段跨度必须为正。"""
    with pytest.raises(ValidationError, match="earlier than end"):
        PlanSegmentInput(start=100, end=100, intent_kind="工作", note="")


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


def test_game_command_length_is_capped_by_request_model() -> None:
    """游戏命令超过 200 字符被请求模型拒绝。"""
    with pytest.raises(ValidationError, match="exceeds 200 characters"):
        GameActionRequest(game_session_id="game-1", command="look " * 60)


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
    runtime = load_scenario("scenarios/white-collar-12-weeks.json")[0]
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
    task["due_at"] = task["available_at"]
    with pytest.raises(ValidationError, match="follow availability"):
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
    _assert_invalid(_replace(("routes", 0, "origin_id"), "nowhere"),
                    "route references an unknown location")
    _assert_invalid(_replace(("tasks", 0, "assignee_agent_id"), "agent:99"),
                    "unknown assignee agent")
    _assert_invalid(_replace(("tasks", 0, "due_at"), "2026-01-05T09:00:00+08:00"),
                    "work due time must follow availability")


def test_schedule_sections_reject_each_invalid_shape() -> None:
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
    payload = _payload()
    payload["price_changes"] = [{"change_id": "change-x", "object_id": "food-ghost",
                                 "object_version": 1, "amount_fen": 100,
                                 "due_at": "2026-01-06T09:00:00+08:00"}]
    _assert_invalid(payload, "price change references an unknown product")
    payload = _payload()
    payload["product_releases"] = [{"release_id": "release-x", "object_id": "food-ghost",
                                    "merchant": "merchant:grocery", "stock": 5,
                                    "due_at": "2026-01-06T09:00:00+08:00"}]
    _assert_invalid(payload, "product release references an unknown product")
    payload = _payload()
    payload["product_releases"] = [{"release_id": "release-x",
                                    "object_id": payload["products"][0]["product_id"],
                                    "merchant": "merchant:wrong", "stock": 5,
                                    "due_at": "2026-01-06T09:00:00+08:00"}]
    _assert_invalid(payload, "merchant disagrees with the product binding")
    payload = _payload()
    payload["accounts"].append({"account_id": "agent:1", "money_fen": 1, "role": "personal:dup"})
    _assert_invalid(payload, "must not reuse agent account ids")


def test_runtime_scenario_rejects_interval_route_and_account_shapes(engine_factory) -> None:
    """运行时场景：窗口失序、路线键重复与账户角色覆盖拒绝。"""
    scenario = load_scenario("scenarios/white-collar-12-weeks.json")[0]

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
    """符号链接、缺失文件、非法 JSON 与语料清单失配均失败关闭。"""
    formal_path = Path("scenarios/white-collar-12-weeks.json")
    link = tmp_path / "scenario-link.json"
    link.symlink_to(formal_path.resolve())
    with pytest.raises(IntegrityError, match="symbolic link"):
        parse_scenario_document(link)
    with pytest.raises(IntegrityError, match="missing or is a symbolic link"):
        parse_scenario_document(tmp_path / "absent.json")
    garbage = tmp_path / "garbage.json"
    garbage.write_bytes(b"not-json")
    with pytest.raises(IntegrityError, match="validation failed"):
        parse_scenario_document(garbage)
    payload = json.loads(formal_path.read_text())
    payload["corpus_manifest_sha256"] = "0" * 64
    mismatched = tmp_path / "mismatched.json"
    mismatched.write_text(json.dumps(payload))
    with pytest.raises(IntegrityError, match="corpus manifest identity is invalid"):
        parse_scenario_document(mismatched)


# ---------------------------------------------------------------------------
# rhythm.py：共同用餐、任务到期脉冲与未知地点
# ---------------------------------------------------------------------------


def _rhythm_inputs(engine, staged, interval: int = 3600):
    """以引擎事实装配节律输入。"""
    return rhythm.engine_rhythm_inputs(engine, staged, interval)


def _consume_activity(engine, use_mode: str, activity_id: str = "activity-meal"):
    """构造进行中的进食活动（请求形态与引擎落库一致）。"""
    from worldmock.life.models import ConsumeItem
    request = ConsumeRequest(items=(ConsumeItem(product_id="food-6923644266066",
                                               quantity=1),),
                             use_mode=use_mode, duration_seconds=600)
    return ActivityState.model_validate(_activity_state(
        activity_id=activity_id, kind=ActivityKind.CONSUME,
        status=ActivityStatus.ACTIVE, started_at=engine.state.current_time,
        planned_end_at=engine.state.current_time + 600,
        last_settled_at=engine.state.current_time,
        planned_duration_seconds=600,
        data={"request": request.model_dump(mode="json")}).model_dump())


def test_shared_meal_covers_social_accrual_only_for_eat_mode(engine_factory) -> None:
    """共同在场堂食停止社交需要累积；外带取餐不覆盖（docs/dev/SPEC-life-runtime.md「其他体感分级」）。"""
    engine = engine_factory("multi-person.json")
    now = engine.state.current_time
    staged = engine.state.model_copy(deep=True)
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
    """任务到期未交叠加 −0.4 脉冲（docs/dev/SPEC-life-runtime.md「其他体感分级」）。"""
    engine = engine_factory()
    task = engine.scenario.tasks[0]
    due = int(task.due_at.timestamp())
    staged = engine.state.model_copy(deep=True)
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
    staged = engine.state.model_copy(deep=True)
    staged.agents[1] = staged.agents[1].model_copy(update={"location_id": "nowhere"}, deep=True)
    inputs = _rhythm_inputs(engine, staged)
    assert rhythm._current_location(staged.agents[1], inputs) is None
    assert rhythm._closing_at(staged.agents[1], inputs) is None
    assert rhythm._just_opened(staged.agents[1], inputs) is False


# ---------------------------------------------------------------------------
# projection.py 与 observe.py：投影边界
# ---------------------------------------------------------------------------


def test_task_due_soon_ignores_missing_due_and_hits_window() -> None:
    """待办提示跳过无截止行，命中 2 小时窗口返回真。"""
    now = 10_000
    assert projection._task_due_soon(({"due_at": None},), now) is False
    assert projection._task_due_soon(({"due_at": None}, {"due_at": now + 3600}), now) is True


def test_inbox_rows_skip_visible_ids_without_scenario_config(engine_factory) -> None:
    """收件箱头跳过无场景邮件配置的可见身份。"""
    engine = engine_factory()
    agent = engine.state.agents[1].model_copy(
        update={"visible_message_ids": {"ghost-mail"}}, deep=True)
    inputs = projection.ProjectionInputs(state=engine.state, scenario=engine.scenario,
                                         agent=agent, recent_results=(), task_rows=())
    assert projection._inbox_rows(inputs) == []


def test_visible_task_rows_hide_overdue_tasks(engine_factory) -> None:
    """过期任务不再进入可见任务行。"""
    engine = engine_factory()
    task = engine.scenario.tasks[0]
    due = int(task.due_at.timestamp())
    _stage_agent(engine, visible_task_ids={task.task_id})
    rows = engine._visible_task_rows(engine.state.agents[1])
    assert [row["task_id"] for row in rows] == [task.task_id]
    staged = engine.state.model_copy(deep=True)
    staged.current_time = due + 60
    staged.pending_events = [item for item in staged.pending_events if item.due_at > due + 60]
    _commit_staged(engine, staged)
    assert engine._visible_task_rows(engine.state.agents[1]) == []


def test_motivation_internal_returns_none_when_no_candidates(engine_factory, monkeypatch) -> None:
    """动机枚举为空时观察不携带内部动机仪表。"""
    engine = engine_factory()
    monkeypatch.setattr("worldmock.life.observe.evaluate_motivation", lambda *args: [])
    assert engine._motivation_internal(engine.state.agents[1]) is None


def test_activity_summary_foods_row_requires_products(engine_factory) -> None:
    """活动品类计数只在场景声明商品时含 foods 行。"""
    engine = engine_factory()
    summary = engine._activity_summary(engine.state.agents[1])
    assert "foods" in summary
    engine.scenario = engine.scenario.model_copy(update={"products": ()})
    assert "foods" not in engine._activity_summary(engine.state.agents[1])


def test_recent_meal_counts_skip_malformed_items() -> None:
    """近期进食计数跳过非列表 items 与非法条目，只累计合法商品。"""
    now = 10_000
    events = [
        SimpleNamespace(data={"tool_name": "consume", "request": {
            "use_mode": "eat", "items": "not-a-list"}}, occurred_at=now - 60),
        SimpleNamespace(data={"tool_name": "consume", "request": {
            "use_mode": "eat", "items": [{"product_id": 123},
                                         {"product_id": "food-x", "quantity": 2}]}},
            occurred_at=now - 120),
    ]
    assert _recent_meal_counts(events, now) == {"food-x": 2}


# ---------------------------------------------------------------------------
# decision_start.py：独白响应判定的拒绝形态
# ---------------------------------------------------------------------------


def _exchange(request: dict, content, error=None) -> SimpleNamespace:
    """构造模型交换证据形态。"""
    response = None if content is None else {"choices": [{"message": {"content": content}}]}
    return SimpleNamespace(request=request, response=response, error=error)


def test_maybe_monologue_start_rejects_non_monologue_sampling() -> None:
    """非独白采样参数（温度/长度不符）不消费延迟草稿。"""
    pending = {1: {"decision_id": "d", "data": {"round_id": "r"}}}
    exchange = _exchange({"temperature": 0.7, "max_tokens": 400}, "我今天很饿。")
    assert maybe_monologue_start(pending, 1, 0, exchange) is None
    assert 1 in pending


def test_maybe_monologue_start_swallows_invalid_monologue_text() -> None:
    """空文本/超长独白按协议错误返回 None，不抛错不消费草稿。"""
    pending = {1: {"decision_id": "d", "data": {"round_id": "r"}}}
    request = {"temperature": 1.3, "max_tokens": 400}
    assert maybe_monologue_start(pending, 1, 0, _exchange(request, "")) is None
    assert maybe_monologue_start(pending, 1, 0, _exchange(request, "饿" * 201)) is None
    assert 1 in pending


def test_maybe_monologue_start_accepts_valid_text_and_pops_pending() -> None:
    """有效独白取出草稿并携带 inner_monologue 字段。"""
    pending = {1: {"decision_id": "d", "data": {"round_id": "r"}}}
    draft = maybe_monologue_start(pending, 1, 42,
                                  _exchange({"temperature": 1.3, "max_tokens": 400},
                                            "我现在很饿，想去找点吃的。"))
    assert pending == {}
    assert draft is not None and draft.event_type == "decision_started"
    assert draft.data[INNER_MONOLOGUE_FIELD] == "我现在很饿，想去找点吃的。"


# ---------------------------------------------------------------------------
# engine.py：协议故障、状态校验与恢复路径
# ---------------------------------------------------------------------------


def test_begin_decision_round_rejects_double_open_and_bad_contexts(engine_factory) -> None:
    """重复开轮、轮次不一致与重复人物均协议拒绝。"""
    engine = engine_factory()
    ctx = context(engine, "one")
    engine.begin_decision_round([ctx])
    engine.submit_action(DirectToolCall(
        ctx, "action-one", "start_basic_activity",
        StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=60)))
    with pytest.raises(ProtocolError, match="cannot be started"):
        engine.begin_decision_round([ctx])
    with pytest.raises(ProtocolError, match="cannot be started"):
        engine.begin_decision_round([])
    engine.commit_decision_round("one")
    advance_until_ready(engine)
    first = context(engine, "two-a")
    second = DecisionContext(engine.state.run_id, "two-b", "decision-two-b-1", 1,
                             datetime.fromtimestamp(engine.state.current_time,
                                                    ZoneInfo(engine.scenario.timezone)),
                             engine.state.version)
    with pytest.raises(ProtocolError, match="disagree or repeat an agent"):
        engine.begin_decision_round([first, second])


def test_same_round_resubmit_is_idempotent_and_parameter_conflict_fails(engine_factory) -> None:
    """同轮重放同参动作返回原回执；参数冲突协议拒绝。"""
    engine = engine_factory()
    ctx = context(engine, "replay-round")
    engine.begin_decision_round([ctx])
    request = StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=60)
    call = DirectToolCall(ctx, "same-action", "start_basic_activity", request)
    first = engine.submit_action(call)
    second = engine.submit_action(call)
    assert second == first
    assert len(engine._round.pending_actions) == 1
    changed = StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=120)
    with pytest.raises(ProtocolError, match="different parameters"):
        engine.submit_action(DirectToolCall(ctx, "same-action", "start_basic_activity", changed))


async def test_prepare_pending_work_review_rejects_missing_action(engine_factory) -> None:
    """未知动作标识的评审准备协议拒绝。"""
    engine = engine_factory()
    ctx = context(engine, "review-missing")
    engine.begin_decision_round([ctx])
    engine.submit_action(DirectToolCall(
        ctx, "action-review-missing", "start_basic_activity",
        StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=60)))
    with pytest.raises(ProtocolError, match="does not exist"):
        await engine.prepare_pending_work_review("ghost-action")
    # 非工作提交的挂起动作不进入评审（静默返回，不记录评审）。
    await engine.prepare_pending_work_review("action-review-missing")
    assert all(item.work_review is None for item in engine._round.pending_actions)


async def test_prepare_pending_work_review_skips_busy_agent(engine_factory) -> None:
    """人物有进行中活动时评审准备直接跳过（不记录评审）。"""
    engine = engine_factory()
    _stage_submission(engine)
    # 中止未准备评审的提交轮，再让人物进入等待活动后重新提交工作。
    engine.abort_decision_round("cover-submit", "busy-agent teardown")
    advance_until_ready(engine)
    wait = StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=3600)
    _act(engine, "busy-wait", "start_basic_activity", wait)
    wake_by_mail(engine)
    advance_until_ready(engine)
    round_id = "busy-submit"
    ctx = context(engine, round_id)
    engine.begin_decision_round([ctx])
    call = DirectToolCall(ctx, f"action-{round_id}", "submit_work", _paraphrased_submission())
    engine.submit_action(call)
    await engine.prepare_pending_work_review(call.action_id)
    pending = next(item for item in engine._round.pending_actions
                   if item.action_id == call.action_id)
    assert pending.work_review is None
    engine.abort_decision_round(round_id, "test teardown")


def test_commit_decision_round_requires_every_expected_agent(engine_factory) -> None:
    """缺少人物提交的决策轮提交被拒绝。"""
    engine = engine_factory("multi-person.json")
    contexts = [context(engine, "partial", agent_id)
                for agent_id in engine.scheduling_state().ready_agent_ids]
    engine.begin_decision_round(contexts)
    engine.submit_action(DirectToolCall(
        contexts[0], "action-partial", "start_basic_activity",
        StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=60)))
    with pytest.raises(ProtocolError, match="every expected agent"):
        engine.commit_decision_round("partial")


def test_abort_decision_round_skips_memory_for_reused_action(engine_factory) -> None:
    """中止轮对重放动作不重复写入幂等记忆。"""
    engine = engine_factory()
    request = StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=60)
    _act(engine, "abort-first", "start_basic_activity", request)
    advance_until_ready(engine)
    ctx = context(engine, "abort-replay")
    engine.begin_decision_round([ctx])
    engine.submit_action(DirectToolCall(ctx, "action-abort-first",
                                        "start_basic_activity", request))
    engine.abort_decision_round("abort-replay", BusinessRuleError("test abort"))
    stored = engine.state.processed_actions["action-abort-first"]
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
    evidence = MemoryUpdateEvidence(ctx, "append_episodes", ["ep-1", "ep-1"],
                                    digest, digest, digest, False)
    with pytest.raises(ProtocolError, match="evidence is invalid"):
        engine.record_memory_update(evidence)


def test_advance_guards_open_round_due_decision_and_boundary(engine_factory) -> None:
    """开轮中、决策到期与边界不符的推进均协议拒绝。"""
    engine = engine_factory()
    zone = ZoneInfo(engine.scenario.timezone)
    target = datetime.fromtimestamp(engine.state.current_time + 900, zone)
    with pytest.raises(ProtocolError, match="while an agent decision is due"):
        engine.advance(900, target)
    ctx = context(engine, "advance-guard")
    engine.begin_decision_round([ctx])
    with pytest.raises(ProtocolError, match="during an open decision round"):
        engine.advance(900, target)
    engine.submit_action(DirectToolCall(
        ctx, "action-advance-guard", "start_basic_activity",
        StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=3600)))
    engine.commit_decision_round("advance-guard")
    with pytest.raises(ProtocolError, match="next scheduling boundary"):
        engine.advance(601, datetime.fromtimestamp(engine.state.current_time + 600, zone))


def test_advance_rejects_tick_consistent_jump_past_next_scheduling_boundary(engine_factory) -> None:
    """时钟边界跳跃守卫（docs/dev/SPEC-as2-integration.md「决策调度改动」、docs/dev/SPEC-life-runtime.md「共享时钟」；Uncle Bob 条款 docs/VERIFICATION.md「源码突变审查」）。

    推进必须同时满足三条：tick 严格为正、t = 上次确认时刻 + tick、且命中
    下一调度边界；不得跨过新开始活动更早的结束边界。本用例瞄准
    `target_epoch != self._next_epoch()` 子句：tick 算术正确（t = now+tick）
    但目标距离是真实边界的两倍——只有该子句会拒绝（601/600 的 tick 失配
    用例测不到它）。正例：精确推进到边界被接受。
    """
    engine = engine_factory()
    zone = ZoneInfo(engine.scenario.timezone)
    ctx = context(engine, "boundary-jump")
    engine.begin_decision_round([ctx])
    engine.submit_action(DirectToolCall(
        ctx, "action-boundary-jump", "start_basic_activity",
        StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=3600)))
    engine.commit_decision_round("boundary-jump")
    now = engine.state.current_time
    next_epoch = engine._next_epoch()
    assert next_epoch > now
    with pytest.raises(ProtocolError, match="next scheduling boundary"):
        engine.advance(2 * (next_epoch - now),
                       datetime.fromtimestamp(now + 2 * (next_epoch - now), zone))
    engine.advance(next_epoch - now, datetime.fromtimestamp(next_epoch, zone))
    assert engine.state.current_time == next_epoch


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
    manifest = json.loads((branch_dir / "manifest.json").read_text())
    manifest.update({"branch_kind": "counterfactual",
                     "intervention_sha256": _digest(intervention),
                     "parent_checkpoint_id": "checkpoint-forged"})
    (branch_dir / "manifest.json").write_text(json.dumps(manifest))
    forged = LifeEngine.open(Path("scenarios/white-collar-12-weeks.json"), branch_dir)
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
    pristine = engine.state
    engine.state = pristine.model_copy(update={"scenario_sha256": "0" * 64})
    with pytest.raises(IntegrityError, match="scenario identity mismatch"):
        engine._validate_runtime()
    engine.state = pristine.model_copy(update={"content_manifest_sha256": "0" * 64})
    with pytest.raises(IntegrityError, match="corpus identity mismatch"):
        engine._validate_runtime()
    engine.state = pristine.model_copy(update={"run_id": "other-run"})
    with pytest.raises(IntegrityError, match="run identity mismatch"):
        engine._validate_runtime()


def test_run_identity_disagreements_rejected_at_create(tmp_path) -> None:
    """运行身份与冻结场景人物/窗口/时区不一致在创建即拒绝。"""
    scenario_path = Path("scenarios/white-collar-12-weeks.json")
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
                                datetime.fromtimestamp(engine.state.current_time,
                                                       ZoneInfo(engine.scenario.timezone)),
                                engine.state.version)
    with pytest.raises(ProtocolError, match="run or agent is invalid"):
        engine.begin_decision_round([wrong_run])
    stale = DecisionContext(engine.state.run_id, "r2", "d-2", 1,
                            datetime.fromtimestamp(engine.state.current_time + 1,
                                                   ZoneInfo(engine.scenario.timezone)),
                            engine.state.version)
    with pytest.raises(ProtocolError, match="observation time is stale"):
        engine.begin_decision_round([stale])
    versioned = DecisionContext(engine.state.run_id, "r3", "d-3", 1,
                                datetime.fromtimestamp(engine.state.current_time,
                                                       ZoneInfo(engine.scenario.timezone)),
                                engine.state.version + 5)
    with pytest.raises(ProtocolError, match="world version is stale"):
        engine.begin_decision_round([versioned])

    ctx = context(engine, "call-guards")
    request = StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=60)
    with pytest.raises(ProtocolError, match="requires an open decision round"):
        engine.submit_action(DirectToolCall(ctx, "a-1", "start_basic_activity", request))
    engine.begin_decision_round([ctx])
    with pytest.raises(ProtocolError, match="another decision round"):
        engine.submit_action(DirectToolCall(
            DecisionContext(engine.state.run_id, "other-round", "d-4", 1,
                            datetime.fromtimestamp(engine.state.current_time,
                                                   ZoneInfo(engine.scenario.timezone)),
                            engine.state.version),
            "a-2", "start_basic_activity", request))
    with pytest.raises(ProtocolError, match="does not match its tool"):
        engine.submit_action(DirectToolCall(ctx, "a-3", "start_basic_activity",
                                            PurchaseOrderRequest(
                                                items=(PurchaseItem(product_id="food-6923644266066",
                                                                   quantity=1, price_version="v1"),),
                                                fulfillment="delivery", destination_id="home")))
    engine.submit_action(DirectToolCall(ctx, "a-4", "start_basic_activity", request))
    with pytest.raises(ProtocolError, match="already submitted"):
        engine.submit_action(DirectToolCall(ctx, "a-5", "start_basic_activity", request))
    with pytest.raises(ProtocolError, match="not open"):
        engine.commit_decision_round("wrong-round-id")
    engine.commit_decision_round("call-guards")


def test_submit_work_guards_review_presence_and_identity(engine_factory) -> None:
    """未留证与身份不符的提交分别协议拒绝。"""
    engine = engine_factory()
    call = _stage_submission(engine)
    pending = next(item for item in engine._round.pending_actions
                   if item.action_id == call.action_id)
    with pytest.raises(ProtocolError, match="no recorded semantic review"):
        engine._public_work_review(pending)
    with pytest.raises(ProtocolError, match="no recorded semantic review"):
        engine.commit_decision_round("cover-submit")
    engine.abort_decision_round("cover-submit", "teardown")

    engine = engine_factory()
    call = _stage_submission(engine)
    asyncio.get_event_loop_policy()
    asyncio.run(engine.prepare_pending_work_review(call.action_id))
    swapped = _paraphrased_submission().model_copy(
        update={"deliverable": MeetingMinutes(
            summary="A different summary entirely.",
            decisions=("Different decision.",),
            action_items=("Different action.",))})
    engine._round.pending_actions[0] = engine._round.pending_actions[0].model_copy(
        update={"request": swapped.model_dump(mode="json")})
    with pytest.raises(ProtocolError, match="identity disagrees"):
        engine.commit_decision_round("cover-submit")


def test_rework_rejection_at_limit_fails_decision_with_review_feedback(engine_factory) -> None:
    """达到连续拒绝上限的返工退回成为失败决策，仍公开评审反馈。"""
    engine = engine_factory()
    _at_task_start(engine)
    _complete_required_work(engine)
    limit = engine.scenario.max_consecutive_rejections
    _stage_agent(engine, consecutive_rejections=limit - 1)
    engine._work_reviewer = _AcceptingReviewer(rejected=True)
    round_id = "rework-limit"
    ctx = context(engine, round_id)
    engine.begin_decision_round([ctx])
    engine.submit_action(DirectToolCall(ctx, f"action-{round_id}", "submit_work",
                                        _paraphrased_submission()))
    asyncio.run(engine.prepare_pending_work_review(f"action-{round_id}"))
    result = engine.commit_decision_round(round_id)[0]
    assert result.status.value == "failed"
    assert result.error_code == "WORK_REQUIRES_REWORK"
    assert "work_review" in result.data
    assert result.message == ("Agent decision failed after consecutive "
                             "rejections: WORK_REQUIRES_REWORK")
    assert engine.state.agents[1].decision_failed is True


# ---------------------------------------------------------------------------
# engine.py：活动边界守卫与业务拒绝分支
# ---------------------------------------------------------------------------


def test_boundary_action_requires_owned_current_activity(engine_factory) -> None:
    """反馈边界上非当前活动身份的控制被拒绝。"""
    engine = engine_factory()
    request = StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=3600)
    _act(engine, "own-wait", "start_basic_activity", request)
    wake_by_mail(engine)
    advance_next(engine)
    wrong_owner = _act(engine, "own-wrong", "set_activity_state",
                       SetActivityStateRequest(activity_id="activity-ghost", operation="pause"))
    assert wrong_owner.status.value == "rejected"
    assert wrong_owner.error_code == "ACTIVITY_NOT_OWNED"


def test_unknown_activity_change_and_continue_reject_when_idle(engine_factory) -> None:
    """空闲人物对未知活动的变更与继续请求按未找到拒绝。"""
    engine = engine_factory()
    ghost_change = _act(engine, "own-missing", "set_activity_state",
                        SetActivityStateRequest(activity_id="activity-ghost", operation="pause"))
    assert ghost_change.error_code == "ACTIVITY_NOT_FOUND"
    advance_until_ready(engine)  # 拒绝后的一秒反应边界
    continue_ghost = _act(engine, "own-continue", "continue_activity",
                          ContinueActivityRequest(activity_id="activity-ghost"))
    assert continue_ghost.error_code == "ACTIVITY_NOT_FOUND"


def test_purchase_rejects_invisible_product(engine_factory) -> None:
    """不可见商品的整单购买被拒绝。"""
    engine = engine_factory()
    result = _act(engine, "buy-ghost", "purchase", PurchaseOrderRequest(
        items=(PurchaseItem(product_id="food-ghost", quantity=1, price_version="v1"),),
        fulfillment="delivery", destination_id="home"))
    assert result.status.value == "rejected"
    assert result.error_code == "PRODUCT_NOT_VISIBLE"


def test_change_playback_speed_bounds_by_remaining_media(engine_factory) -> None:
    """调速不越过剩余媒体时长：超速拒绝、可容纳调速生效。"""
    engine = engine_factory()
    _act(engine, "speed-plan", "plan_day", _plan_request(engine, (900, 1800, 2700, 3600, 4500)))
    advance_next(engine)
    started = _act(engine, "speed-watch", "start_targeted_activity",
                   StartTargetedActivityRequest(
                       activity_type="watch", target_id="sintel-en-subtitles",
                       planned_duration_seconds=1260, playback_speed=0.5))
    activity_id = started.activity_id
    advance_next(engine)
    exceeded = _act(engine, "speed-too-fast", "change_playback_speed",
                    ChangePlaybackSpeedRequest(activity_id=activity_id, playback_speed=2))
    assert exceeded.status.value == "rejected"
    assert exceeded.error_code == "CONTENT_END_EXCEEDED"
    advance_until_ready(engine)
    legal = _act(engine, "speed-ok", "change_playback_speed",
                 ChangePlaybackSpeedRequest(activity_id=activity_id, playback_speed=0.5))
    assert legal.status.value == "completed"
    assert engine.state.activities[activity_id].playback_speed == 0.5


def _plan_request(engine, offsets: tuple[int, ...], span: int = 600) -> PlanDayRequest:
    """构造覆盖若干偏移时刻的当日计划（末段延伸到缺省就寝锚点，docs/dev/SPEC-life-realism.md「日程骨架」）。"""
    from zoneinfo import ZoneInfo

    base = engine.state.current_time
    local = datetime.fromtimestamp(base, ZoneInfo(engine.scenario.timezone))
    anchor = int((local + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0).timestamp())
    segments = [PlanSegmentInput(start=base + offset, end=base + offset + span,
                                 intent_kind="休息", note="")
                for offset in offsets[:-1]]
    segments.append(PlanSegmentInput(start=base + offsets[-1], end=anchor,
                                     intent_kind="休息", note=""))
    return PlanDayRequest(segments=tuple(segments))


def test_game_action_guards_activity_session_and_timing(engine_factory) -> None:
    """无游戏活动、会话不符与未到交互时刻的游戏命令逐项拒绝。"""
    engine = engine_factory()
    no_game = _act(engine, "game-none", "game_action",
                   GameActionRequest(game_session_id="ghost", command="look"))
    assert no_game.status.value == "rejected"
    assert no_game.error_code == "GAME_ACTIVITY_NOT_ACTIVE"
    advance_until_ready(engine)  # 拒绝后的一秒反应边界

    _act(engine, "game-plan", "plan_day", _plan_request(engine, (30, 1800, 3600, 5400, 7200)))
    advance_next(engine)
    started = _act(engine, "game-start", "start_targeted_activity",
                   StartTargetedActivityRequest(
                       activity_type="play_game", target_id="textworld-1201",
                       planned_duration_seconds=1800, playback_speed=1))
    activity_id = started.activity_id
    session_id = engine.state.activities[activity_id].data["game_session_id"]
    wake_by_mail(engine, offset=1800)
    advance_next(engine)
    advance_until_ready(engine)
    wrong_session = _act(engine, "game-wrong-session", "game_action",
                         GameActionRequest(game_session_id="ghost-session", command="look"))
    assert wrong_session.error_code == "GAME_SESSION_NOT_ACTIVE"


def test_store_game_outcome_terminal_closes_activity(engine_factory) -> None:
    """游戏终局把活动置为完成并清空下一交互时刻。"""
    engine = engine_factory()
    _act(engine, "terminal-plan", "plan_day", _plan_request(engine, (900, 1800, 2700, 3600, 4500)))
    advance_next(engine)
    started = _act(engine, "terminal-game", "start_targeted_activity",
                   StartTargetedActivityRequest(
                       activity_type="play_game", target_id="textworld-1201",
                       planned_duration_seconds=1800, playback_speed=1))
    activity = engine.state.activities[started.activity_id]
    agent = engine.state.agents[1]
    session = agent.game_sessions[activity.data["game_session_id"]]
    outcome = SimpleNamespace(done=True, progress=session)
    changed, final_activity = engine._store_game_outcome(
        engine.state, agent, activity, outcome)
    assert final_activity.status == ActivityStatus.COMPLETED
    assert final_activity.boundary_type == "game_terminal"
    assert final_activity.next_interaction_at is None
    assert changed.agents[1].current_activity_id is None


def test_plan_day_rejects_duplicate_invalid_and_started_segments(engine_factory) -> None:
    """同日重复、骨架不合法与已开始段的计划提交逐项拒绝。"""
    engine = engine_factory()
    plan = _plan_request(engine, (0, 1200, 2400, 3600, 4800))
    ok = _act(engine, "plan-ok", "plan_day", plan)
    assert ok.status.value == "completed"
    staged = engine.state.model_copy(deep=True)
    staged.agents[1] = staged.agents[1].model_copy(
        update={"pending_wake_reasons": ["wake_from_sleep"],
                "next_decision_at": staged.current_time}, deep=True)
    _commit_staged(engine, staged)
    duplicate = _act(engine, "plan-dup", "plan_day", plan)
    assert duplicate.error_code == "PLAN_ALREADY_SUBMITTED"

    fresh = engine_factory()
    short_tail = _plan_request(fresh, (0, 1200, 2400, 3600))
    invalid = _act(fresh, "plan-invalid", "plan_day", short_tail)
    assert invalid.error_code == "PLAN_SEGMENTS_INVALID"
    advance_until_ready(fresh)  # 拒绝后的一秒反应边界

    virgin = engine_factory()
    past = PlanDayRequest(segments=tuple(
        PlanSegmentInput(start=virgin.state.current_time + offset,
                         end=virgin.state.current_time + offset + 3000,
                         intent_kind="休息", note="")
        for offset in (-60, 3500, 7000, 10500, 14000)))
    started = _act(virgin, "plan-started", "plan_day", past)
    assert started.error_code == "PLAN_SEGMENT_ALREADY_STARTED"


def test_revise_day_plan_requires_existing_plan(engine_factory) -> None:
    """无当日计划时修订被拒绝。"""
    engine = engine_factory()
    result = _act(engine, "revise-none", "revise_day_plan", ReviseDayPlanRequest(
        segments=(PlanSegmentInput(start=engine.state.current_time + 600,
                                   end=engine.state.current_time + 1200,
                                   intent_kind="休息", note=""),)))
    assert result.status.value == "rejected"
    assert result.error_code == "PLAN_NOT_FOUND"


def test_send_message_rejects_unknown_recipient_and_non_model_contact(engine_factory) -> None:
    """未知收件人拒绝；非模型回复策略联系人拒绝登记回复请求。"""
    engine = engine_factory()
    with pytest.raises(ValueError, match="recipient not found"):
        _act(engine, "say-ghost", "send_message",
             SendMessageRequest(recipient_id="agent:9", text="hi"))

    multi = engine_factory("multi-person.json")
    refused = _act_ready(multi, "say-colleague", "send_message",
                         SendMessageRequest(recipient_id="contact-chenxi-colleague", text="hi"))
    assert refused.error_code == "CONTACT_REPLY_NOT_MODEL"


def test_call_rejects_unknown_contact(engine_factory) -> None:
    """未声明联系人的通话被拒绝。"""
    engine = engine_factory()
    result = _act(engine, "call-ghost", "start_social_activity",
                  StartSocialActivityRequest(kind="call", partner_id="contact-ghost",
                                             duration_minutes=10))
    assert result.status.value == "rejected"
    assert result.error_code == "CONTACT_NOT_FOUND"


def test_ready_is_false_after_run_end_and_terminal_action_missing(engine_factory) -> None:
    """运行结束后不再提供决策；缺失原始动作的终态登记失败关闭。"""
    engine = engine_factory()
    staged = engine.state.model_copy(update={"current_time": engine.state.end_time})
    engine.state = staged
    assert engine._ready(1) is False
    result = ActionResult(action_id="ghost-action", agent_id=1,
                          status=ActionStatus.COMPLETED, occurred_at=0,
                          message="test")
    with pytest.raises(ProtocolError, match="origin action is missing"):
        _terminal_action_records(engine.state, result)


def test_dedupe_and_material_and_visibility_helpers_guard_shapes(engine_factory) -> None:
    """字幕去重/材料登记/浏览可见对缺失与畸形数据失败关闭。"""
    engine = engine_factory()
    activity = _activity_state(kind=ActivityKind.WATCH)
    _dedupe_revealed_cues(activity)  # 无 revealed 数据：直接返回
    activity.data["revealed"] = {"position_before_seconds": "not-a-number", "cues": []}
    _dedupe_revealed_cues(activity)  # 形畸形：直接返回
    agent = engine.state.agents[1]
    unchanged = _record_material_reveal(agent, "", {"start_char": 0, "end_char": 10}, 0)
    assert unchanged is agent
    phrase = _activity_progress_phrase(_activity_state(elapsed_seconds=60,
                                                       planned_duration_seconds=120))
    assert phrase == "还剩约1分钟"
    control = _paused_activity_control(_activity_state(elapsed_seconds=60,
                                                       planned_duration_seconds=120))
    assert control["remaining_seconds"] == 60
    assert set(control) == {"activity_id", "kind", "remaining_seconds"}


def test_change_on_social_activity_uses_social_domain(engine_factory) -> None:
    """交谈活动的边界控制走社交域转换：挂断取消并保留已结算进度。"""
    engine = engine_factory("multi-person.json")
    requests = {1: ("start_social_activity", StartSocialActivityRequest(
        kind="chat", partner_id="agent:2", duration_minutes=30)),
        2: ("start_basic_activity", StartBasicActivityRequest(
            activity_type="wait", planned_duration_seconds=60))}
    _, results = submit_round(engine, requests, "chat-start")
    chat = next(item for item in results if item.agent_id == 1)
    assert chat.status.value == "in_progress"
    activity_id = chat.activity_id
    wake_by_mail(engine)
    _advance_until_agent_ready(engine, 1)
    hung_up = _act_ready(engine, "chat-cancel", "set_activity_state",
                         SetActivityStateRequest(activity_id=activity_id, operation="cancel"))
    assert hung_up.status.value == "completed"
    activity = engine.state.activities[activity_id]
    assert activity.status == ActivityStatus.CANCELLED
    assert activity.boundary_type == "cancelled"
    assert engine.state.agents[1].current_activity_id is None
