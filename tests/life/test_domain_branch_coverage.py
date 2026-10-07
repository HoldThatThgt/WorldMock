"""当前食品、社交、身体与动机领域的实际行为分支。"""
from __future__ import annotations

import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from tests.data_paths import ENVIRONMENT_ROOT
from tests.life.purchase_worlds import registered_world
from tests.life.test_travel_graph import travel_case
from worldmock.catalog.affordances import engine_affordances
from worldmock.catalog.models import Content
from worldmock.errors import BusinessRuleError, IntegrityError
from worldmock.exchange.constants import FOOD_CONSTANTS
from worldmock.life import felt, social
from worldmock.life.activities import ActivityOrigin, activity_factors, advance_activity, change_activity, truncate_activity
from worldmock.life.basket import apply_meal_progress, change_inventory, meal_duration, meal_food_rate, validate_meal_use
from worldmock.life.commerce import deliver_order
from worldmock.life.content import load_lifestyle_items, mail_headers, reveal_mail
from worldmock.life.models import (ActivityChange, ActivityKind, ActivityState, ActivityStatus, AgentState, BodyState,
    ChangeActivityRequest, ConsumeItem, ConsumeRequest, StartSocialActivityRequest, TravelMode, WorldState)
from worldmock.life.motivation import (MotivationContext, _continue_candidate, _sleep_candidate, _sleep_deficit_delta,
    _softmax, _travel_candidate, load_motivation_transforms)
from worldmock.life.scenario import LifeScenario, MessageConfig
from worldmock.life.semantics import assemble_semantics
from worldmock.life.social import SOCIAL_RESOURCE_CLAIMS
from worldmock.life.social_actions import (advance_social, end_partner_chats, partner_presence, shared_chat_seconds,
    social_activity_change, start_call, start_chat)
from worldmock.life.travel import plan_trip, update_travel
from worldmock.life.work import validate_work_start
from worldmock.life.world_setup import load_runtime_scenario
from worldmock.model_feedback import tool_arguments

TIMEZONE = "Asia/Shanghai"
CURRENT = 1_700_000_000


@lru_cache(maxsize=1)
def _registered():
    """纯领域检查只从真实登记文件读取不可变环境语义。"""
    return registered_world()


def _body():
    """采用真实加载的系统身体常数。"""
    return _registered()[0].body


def _agent(**overrides) -> AgentState:
    """从完整人物模型复制待验证的领域事实。"""
    agent = _registered()[1].agents[1].model_copy(deep=True)
    defaults = {"body_settled_at": 0, "next_decision_at": 0,
                "body": agent.body.model_copy(update={"energy_kcal": 1600, "sleep_deficit_hours": 4,
                                                     "entertainment_need": 0.5})}
    return AgentState.model_validate(agent.model_copy(update={**defaults, **overrides}).model_dump())


def _world(agent, *, activities=None, current_time=CURRENT) -> WorldState:
    """保留登记世界全部结构；纯函数反事实只替换人物、活动和观察时刻。"""
    base = _registered()[1].model_copy(deep=True)
    return base.model_copy(update={"agents": {agent.agent_id: agent}, "activities": activities or {},
                                   "current_time": current_time})


def _activity(**overrides) -> ActivityState:
    """活动纯函数夹具；非法证据分支明确绕过模型构造校验。"""
    kind = overrides.get("kind", ActivityKind.WAIT)
    claims = (set(SOCIAL_RESOURCE_CLAIMS) if kind in {ActivityKind.CHAT, ActivityKind.CALL}
              else set(engine_affordances().resource_claims(kind.value,
                  travel_mode=overrides.get("travel_mode"))))
    payload = dict(activity_id="a1", agent_id=1, kind=kind, status=ActivityStatus.ACTIVE, started_at=0,
        planned_end_at=600, last_settled_at=0, elapsed_seconds=0, planned_duration_seconds=600,
        origin_action_id="act", origin_decision_id="dec", origin_request_event_id="req", location_id="home",
        resource_claims=claims, data={})
    return ActivityState.model_construct(**{**payload, **overrides})


def _origin() -> ActivityOrigin:
    return ActivityOrigin("act", "dec", "req")


def _consume(product_id="beef-noodles", quantity=1, use_mode="eat", duration=1200) -> ConsumeRequest:
    return ConsumeRequest(items=(ConsumeItem(product_id=product_id, quantity=quantity),),
                          use_mode=use_mode, duration_seconds=duration)


def _context(**overrides) -> MotivationContext:
    """动机候选用同一真实目录、人物菜单及固定身体规则。"""
    semantics, state = _registered()
    config = semantics.scenario.agents[0]
    defaults = dict(agent=_agent(), balance_fen=20_000, body=_body(), home_location_id="home",
        catalog=semantics.catalog, location_ids=frozenset(item.location_id for item in semantics.scenario.locations),
        menu=semantics.scenario.interaction_menus_by_location_kind["home"], lifestyle_items=(),
        activity_location_kinds={}, work_topics=tuple(semantics.catalog.work_topics.values()), topic_totals={},
        workplace_location_id=config.work_schedule.workplace_location_id, active_activities=(), habit_events=(),
        consecutive_rejections=0, max_same_moment_decisions=3, total_work_seconds=0,
        current_time=CURRENT, end_time=CURRENT + 86400, timezone=TIMEZONE)
    return MotivationContext(**{**defaults, **overrides})


def test_commerce_deliver_order_rejects_unknown_order() -> None:
    """到期配送对未知订单按业务拒绝。"""
    with pytest.raises(BusinessRuleError, match="ORDER_NOT_FOUND"):
        deliver_order(_world(_agent()), "order-missing")


def test_basket_change_inventory_rejects_over_consume() -> None:
    """负值变动超过持有量即 PRODUCT_NOT_HELD（其他地点库存不救场）。"""
    agent = _agent(location_id="home", inventory={"home": {"beef-noodles": 1.0},
                                                 "noodle-1": {"beef-noodles": 5.0}})
    with pytest.raises(BusinessRuleError, match="PRODUCT_NOT_HELD"):
        change_inventory(agent, "beef-noodles", -2.0)


def test_basket_validate_meal_rejects_unknown_product() -> None:
    """进食校验对目录外商品拒绝。"""
    with pytest.raises(BusinessRuleError, match="PRODUCT_NOT_FOUND"):
        validate_meal_use(_agent(), {}, _consume())


def _presence(agent_id="agent:2", location="home", awake=True) -> social.AgentPresence:
    return social.AgentPresence(agent_id=agent_id, name="B", location=location,
                                activity_family="idle", awake=awake)


def test_social_chat_requires_positive_duration_and_partner_recipient() -> None:
    """交谈域校验：时长必须为正且邀请收件人必须是对方本人。"""
    invite = social.OutgoingMessage(message_id="m1", sender_id="agent:1",
                                    recipient_id="agent:2", text="聊聊？", created_at=1)
    with pytest.raises(ValueError, match="duration must be positive"):
        social.start_chat("home", _presence(), 0, invite)
    wrong = invite.model_copy(update={"recipient_id": "agent:9"})
    with pytest.raises(ValueError, match="must be the partner"):
        social.start_chat("home", _presence(), 10, wrong)


def _social_state(agent: AgentState, activities=None) -> WorldState:
    return _world(agent, activities=activities if activities is not None else {})


def test_social_actions_partner_presence_rejects_unknown_partner() -> None:
    """引擎侧解析对方在场：目录外人物即业务拒绝。"""
    with pytest.raises(BusinessRuleError, match="SOCIAL_PARTNER_NOT_FOUND"):
        partner_presence(_social_state(_agent()), "agent:9")


def test_social_actions_start_chat_and_call_conflict_with_occupied_resources(engine_factory) -> None:
    """交谈、通话同占 talk：正在进行的通话与新的交谈、通话按 talk 的交集逐项拒绝；正在看的视频占 watch、screen，
    与交谈、通话不是同一种，照常受理（docs/dev/SPEC-parallel-activities.md#spec-parallel-activities-占用声明）。"""
    busy = _agent()
    talking = _activity(activity_id="talking", kind=ActivityKind.CALL, resource_claims={"talk"})
    watch = _activity(kind=ActivityKind.USE_APP, resource_claims={"screen"})
    state = _social_state(busy, activities={talking.activity_id: talking})
    watching = _social_state(busy, activities={watch.activity_id: watch})
    state.agents[2] = _agent(agent_id=2, location_id="home")
    request = StartSocialActivityRequest(kind="chat", partner_id="agent:2",
                                         duration_minutes=10)
    chat = start_chat(state, busy, request, _origin(), "m1")
    contact = social.Contact(contact_id="c1", salutation="妈", relation="母亲",
                             closeness=20, channels=("phone",), reply_policy="none")
    call = start_call(busy, contact, request, _origin(), CURRENT)
    engine = engine_factory()
    for started in (chat, call):
        assert started.activity.resource_claims == SOCIAL_RESOURCE_CLAIMS == {"talk"}
        with pytest.raises(BusinessRuleError, match="RESOURCE_CONFLICT"):
            engine.actions._require_available_resources(state, busy.agent_id, started.activity.resource_claims)
        engine.actions._require_available_resources(watching, busy.agent_id, started.activity.resource_claims)


def _chat(activity_id: str, agent_id: int, partner: int, **overrides) -> ActivityState:
    """以另一人物为伙伴的进行中交谈（默认 0 秒开始、600 秒计划结束、尚未结算）。"""
    return _activity(activity_id=activity_id, agent_id=agent_id, kind=ActivityKind.CHAT,
                     data={"partner_id": f"agent:{partner}"}, **overrides)


def test_social_actions_choosing_chat_with_the_inviter_accepts() -> None:
    """对方正以本人为伙伴交谈时，对其选择交谈即接受：不再发邀请，计划结束对齐发起方；同一对人物再次发起以
    CHAT_ALREADY_ACTIVE 拒绝（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第五章「交谈」）。"""
    invitation = _chat("a2", 2, 1, started_at=CURRENT - 60, last_settled_at=CURRENT, planned_end_at=CURRENT + 540)
    state = _social_state(_agent(), activities={"a2": invitation})
    state.agents[2] = _agent(agent_id=2, name="B")
    request = StartSocialActivityRequest(kind="chat", partner_id="agent:2", duration_minutes=10)
    accepted = start_chat(state, state.agents[1], request, _origin(), "m1")
    assert accepted.invite is None
    assert accepted.activity.planned_end_at == invitation.planned_end_at
    assert accepted.activity.data == {"partner_id": "agent:2", "accepted": True}
    assert accepted.accepted.activity_id == "a2" and accepted.accepted.data["accepted"] is True
    state.activities[accepted.activity.activity_id] = accepted.activity
    with pytest.raises(BusinessRuleError, match="CHAT_ALREADY_ACTIVE"):
        start_chat(state, state.agents[1], request, _origin(), "m2")
    # 接受以共同在场为前提：准入已先按目标在场拒绝，领域层遇到不在一处的接受是程序错误。
    apart = _social_state(_agent(), activities={"a2": invitation})
    apart.agents[2] = _agent(agent_id=2, name="B", location_id="office")
    with pytest.raises(ValueError, match="co-presence"):
        start_chat(apart, apart.agents[1], request, _origin(), "m3")
    # 发起方的交谈只剩不足一分钟：对齐后不足最短时长，以带原因与下一步的 ACTIVITY_TOO_SHORT 拒绝。
    late = _social_state(_agent(), activities={"a2": invitation.model_copy(update={"planned_end_at": CURRENT + 59})})
    late.agents[2] = _agent(agent_id=2, name="B")
    with pytest.raises(BusinessRuleError, match="ACTIVITY_TOO_SHORT") as refused:
        start_chat(late, late.agents[1], request, _origin(), "m4")
    assert (refused.value.public_reason, refused.value.suggested) == (
        "The other person's conversation has less than one minute remaining.", {"next_step": "Wait for the current conversation to end, then start a new one."})


def test_social_actions_shared_chat_seconds_counts_the_reciprocal_overlap() -> None:
    """接受带来的缓解按区间内双方交谈同时进行的秒数：对方以本人为伙伴的交谈在区间起点进行中才算，止于双方较早的
    结束；以推进前的状态判定，两人谁先推进结果都一样（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第五章「交谈」）。"""
    mine, partner = _chat("a1", 1, 2), _chat("a2", 2, 1)

    def shared(other: ActivityState | None, target: int = 300) -> int:
        activities = {"a1": mine} if other is None else {"a1": mine, "a2": other}
        return shared_chat_seconds(_social_state(_agent(), activities=activities), mine, target)

    both = _social_state(_agent(), activities={"a1": mine, "a2": partner})
    assert shared_chat_seconds(both, mine, 300) == shared_chat_seconds(both, partner, 300) == 300
    assert shared(partner, 900) == 600  # 本人的计划结束封顶
    assert shared(partner.model_copy(update={"planned_end_at": 200})) == 200
    # 对方在本次推进中先结算完成的，按其结束时刻计。
    assert shared(partner.model_copy(update={"status": ActivityStatus.COMPLETED, "ended_at": 250})) == 250
    assert shared(partner.model_copy(update={"status": ActivityStatus.COMPLETED, "ended_at": 0})) == 0
    assert shared(partner.model_copy(update={"started_at": 100})) == 0  # 区间起点时还没有接受
    assert shared(_chat("a2", 2, 3)) == 0  # 对方在和别人交谈
    assert shared(None) == 0
    call = _activity(kind=ActivityKind.CALL, data={"contact_id": "c1"})
    assert shared_chat_seconds(both, call, 300) == 0


def test_social_actions_ending_a_chat_ends_the_partner_chat_the_same_second() -> None:
    """一方的交谈结束时，对方以其为伙伴的进行中交谈同秒结束：completed，boundary_type 与 interruption_reason
    都记 partner_ended（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第五章「交谈」）；没有这样的对方交谈时世界不变。"""
    mine, partner = _chat("a1", 1, 2), _chat("a2", 2, 1)
    ended = mine.model_copy(update={"status": ActivityStatus.CANCELLED, "ended_at": 300})
    before = _world(_agent(), activities={"a1": mine, "a2": partner}, current_time=300)
    changed, partners = end_partner_chats(before, before.model_copy(update={"activities": {"a1": ended,
                                                                                          "a2": partner}}))
    closed = changed.activities["a2"]
    assert partners == [2] and changed.activities["a1"] == ended
    assert (closed.status, closed.ended_at, closed.boundary_type, closed.interruption_reason) == (
        ActivityStatus.COMPLETED, 300, "partner_ended", "partner_ended")
    alone = _world(_agent(), activities={"a1": mine}, current_time=300)
    after = alone.model_copy(update={"activities": {"a1": ended}})
    assert end_partner_chats(alone, after) == (after, [])
    # 没有人物伙伴的交谈结束时不牵连任何人。
    stray = _activity(activity_id="a3", kind=ActivityKind.CHAT, data={})
    lone = _world(_agent(), activities={"a3": stray, "a2": partner}, current_time=300)
    finished = lone.model_copy(update={"activities": {
        "a3": stray.model_copy(update={"status": ActivityStatus.CANCELLED, "ended_at": 300}), "a2": partner}})
    assert end_partner_chats(lone, finished) == (finished, [])


def test_social_actions_advance_rejects_backwards_settlement_time() -> None:
    """社交活动结算时间倒流是程序错误，不是业务拒绝。"""
    backwards = _activity(kind=ActivityKind.CHAT, last_settled_at=900)
    with pytest.raises(ValueError, match="moved backwards"):
        advance_social(backwards, _agent(), 600, _body(), shared_seconds=0)


def test_social_actions_advance_skips_non_active_activity() -> None:
    """暂停的交谈推进为无结算空转，保留既有进度。"""
    paused = _activity(kind=ActivityKind.CHAT, status=ActivityStatus.PAUSED)
    result = advance_social(paused, _agent(), 600, _body(), shared_seconds=0)
    assert result.settlement is None and result.completed is False
    assert result.activity.status == ActivityStatus.PAUSED


def test_social_actions_advance_completes_chat_without_agent_activity_slot() -> None:
    """到计划终点完成交谈；终态归活动，人物不保存旧主活动槽。"""
    finishing = _activity(kind=ActivityKind.CHAT)
    result = advance_social(finishing, _agent(), 600, _body(), shared_seconds=0)
    assert result.completed is True
    assert result.activity.status == ActivityStatus.COMPLETED
    assert result.activity.boundary_type == "planned_end"
    assert result.agent.agent_id == 1 and result.agent.body_settled_at == 600


@pytest.mark.parametrize(("kind", "shared_seconds"), ((ActivityKind.CALL, 0), (ActivityKind.CHAT, 3600),
                                                      (ActivityKind.CHAT, 1800), (ActivityKind.CHAT, 0)))
def test_social_actions_advance_applies_felt_ledger_by_shared_seconds(kind, shared_seconds) -> None:
    """通话按通话账本；交谈中双方同时进行的秒数按交谈账本缓解，其余秒数按发起方 0.2/小时
    （docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第五章「交谈」）。"""
    start_need = 0.8
    agent = _agent(body=_agent().body.model_copy(update={"social_need": start_need}))
    activity = _activity(kind=kind, planned_end_at=3600, planned_duration_seconds=3600,
                         data={"partner_id": "agent:2"})
    result = advance_social(activity, agent, 3600, _body(), shared_seconds=shared_seconds)
    hours, shared = 1.0, shared_seconds / 3600
    if kind == ActivityKind.CALL:
        expected = felt.apply_social_need_ledger(start_need, felt.SocialExposure(awake_hours=hours, call_hours=hours))
    else:
        exposure = felt.SocialExposure(awake_hours=hours, conversation_hours=shared)
        expected = (felt.apply_social_need_ledger(start_need, exposure)
                    - social.CHAT_RELIEF_DECLINED_PER_HOUR * (hours - shared))
    assert result.agent.body.social_need == pytest.approx(expected)


def test_social_activity_change_cancel_pause_and_unknown_operations() -> None:
    """社交活动转换闭集：取消任意时刻、不可暂停、未知操作拒绝、恢复无操作。"""
    active = _activity(kind=ActivityKind.CALL)
    agent, cancelled = social_activity_change(_agent(), active,
                                              "cancel", 100)
    assert cancelled.status == ActivityStatus.CANCELLED
    assert cancelled.boundary_type == "cancelled"
    assert agent.agent_id == 1
    terminal = cancelled
    with pytest.raises(BusinessRuleError, match="ACTIVITY_ALREADY_TERMINAL"):
        social_activity_change(agent, terminal, "cancel", 100)
    with pytest.raises(BusinessRuleError, match="SOCIAL_ACTIVITY_CANNOT_PAUSE"):
        social_activity_change(_agent(), active, "pause", 100)
    with pytest.raises(BusinessRuleError, match="SOCIAL_ACTIVITY_OPERATION_UNKNOWN"):
        social_activity_change(_agent(), active, "rewind", 100)
    same_agent, same = social_activity_change(_agent(), active,
                                              "resume", 100)
    assert same.activity_id == "a1" and same_agent.agent_id == 1


def _feedback_activity(**overrides) -> ActivityState:
    payload = dict(next_interaction_at=1000, boundary_type="feedback")
    payload.update(overrides)
    return _activity(**payload)


def test_change_activity_rejects_foreign_activity() -> None:
    """活动属于调用者：他人活动拒绝（ACTIVITY_NOT_OWNED）。"""
    foreign = _activity(agent_id=2)
    request = ChangeActivityRequest(activity_id="a1", operation=ActivityChange.PAUSE)
    with pytest.raises(BusinessRuleError, match="ACTIVITY_NOT_OWNED"):
        change_activity(_agent(), foreign, request, 1000, 300)


def test_advance_lifestyle_without_social_or_health_effects_skips_relief() -> None:
    """无社交/健康效应的生活活动只走常规结算，不落额外缓解。"""
    body = _body().model_copy(update={
        "activity_met": {**_body().activity_met, "lifestyle:test-walk": 1.5}})
    activity = _activity(kind=ActivityKind.LIFESTYLE, target_id="test-walk",
                         data={"effects": {"entertainment": 0.2, "social": 0.0, "health": 0.0}})
    restored = ActivityState.model_validate_json(activity.model_dump_json())
    result = advance_activity(restored, _agent(), 300, body)
    assert result.completed is False
    assert result.agent.body.social_need == _agent().body.social_need
    assert result.agent.body.entertainment_need == pytest.approx(0.5 - 0.1 / 12)


def test_change_activity_resume_conflicts_with_occupied_resources(engine_factory) -> None:
    """恢复前引擎校验同人的活动占用交集（两段观看都占 watch、screen），冲突时暂停进度不变。"""
    paused = _activity(status=ActivityStatus.PAUSED, resource_claims={"screen"})
    active = _activity(activity_id="occupied", resource_claims={"screen"})
    state = _world(_agent(), activities={"a1": paused, "occupied": active})
    engine = engine_factory()
    with pytest.raises(BusinessRuleError, match="RESOURCE_CONFLICT"):
        engine.actions._require_available_resources(state, 1, paused.resource_claims, paused.activity_id)
    assert state.activities["a1"].status == ActivityStatus.PAUSED
    assert state.activities["a1"].elapsed_seconds == 0


def test_change_activity_cancel_rejects_terminal_activity() -> None:
    """已终结活动不可再退出（ACTIVITY_ALREADY_TERMINAL）。"""
    done = _activity(status=ActivityStatus.COMPLETED, ended_at=600)
    request = ChangeActivityRequest(activity_id="a1", operation=ActivityChange.CANCEL)
    with pytest.raises(BusinessRuleError, match="ACTIVITY_ALREADY_TERMINAL"):
        change_activity(_agent(), done, request, 1000, 300)


def test_advance_activity_skips_non_active_and_rejects_backwards_clock() -> None:
    """非活动态推进为空转；结算时刻倒流是程序错误。"""
    paused = _activity(status=ActivityStatus.PAUSED)
    result = advance_activity(paused, _agent(), 600, _body())
    assert result.completed is False and result.settlement is None
    backwards = _activity(last_settled_at=900)
    with pytest.raises(ValueError, match="moved backwards"):
        advance_activity(backwards, _agent(), 600, _body())


def test_truncate_activity_keeps_terminal_activity_untouched() -> None:
    """实验终点截断只作用于进行中/暂停活动，终态保持原样。"""
    done = _activity(status=ActivityStatus.COMPLETED, ended_at=600)
    changed, agent = truncate_activity(done, _agent(), 900)
    assert changed is done and agent == _agent()


def test_motivation_transform_scales_cover_all_resources() -> None:
    """归一尺度闭集覆盖五类资源键。"""
    transforms = load_motivation_transforms(ENVIRONMENT_ROOT / "catalog" / "rules")
    assert transforms is not None
    scales = {resource: transforms.scale_of(resource)
              for resource in ("money_fen", "energy_kcal", "sleep_debt_hours",
                               "entertainment_need", "goal_task")}
    assert all(value > 0 for value in scales.values())
    with pytest.raises(KeyError):
        transforms.scale_of("unknown_resource")


@pytest.mark.parametrize(
    ("content", "match"),
    (
        ("not a toml [", "malformed"),
        ("[prospect]\nloss_exponent = 0.88\n", "is missing"),
        ("[prospect]\nloss_exponent = 0.88\n[homeostatic]\nloss_exponent = 0.5\n",
         "declared twice"),
    ),
)
def test_load_motivation_transforms_fails_closed_on_bad_rules(tmp_path, content, match) -> None:
    """动机规则在场即声明意图：坏 TOML/缺分组/重复键 fail-closed。"""
    (tmp_path / "motivation.toml").write_text(content, encoding="utf-8")
    with pytest.raises(IntegrityError, match=match):
        load_motivation_transforms(tmp_path)


def test_load_motivation_transforms_missing_rules_are_rejected(tmp_path) -> None:
    """规则必须完整发布，缺失不能静默关闭动机提示。"""
    with pytest.raises(IntegrityError, match="snapshot is missing"):
        load_motivation_transforms(tmp_path)


def test_softmax_empty_value_list_returns_empty() -> None:
    """空值列表的 softmax 仍为空（无候选时的稳定约定）。"""
    assert _softmax([], 1.0) == []


def test_sleep_candidate_skips_when_session_falls_below_minimum() -> None:
    """小睡候选时长被钳到不足一分钟时不出候选。"""
    context = _context(agent=_agent(body=BodyState(energy_kcal=1600, sleep_pressure=0.0,
                                                   sleep_deficit_hours=0.0,
                                                   entertainment_need=0.5)))
    params = {"fallback_seconds": 30, "min_session_seconds": 0, "cap_seconds": 4 * 3600}
    assert _sleep_candidate(context, params) is None


def test_continue_candidate_covers_travel_lifestyle_and_consume_flows() -> None:
    """继续当前活动按活动类型取 MET 与资源流。"""
    context = _context(agent=_agent())
    travel = _activity(kind=ActivityKind.TRAVEL, travel_mode=TravelMode.DRIVING,
                       planned_end_at=3600, planned_duration_seconds=3600)
    assert _continue_candidate(context, travel) is not None
    lifestyle_body = _body().model_copy(update={
        "activity_met": {**_body().activity_met, "lifestyle:jog": 2.0}})
    lifestyle = _activity(kind=ActivityKind.LIFESTYLE, target_id="jog",
                          data={"effects": {"entertainment": 0.2, "social": 0.0, "health": 0.0}},
                          planned_end_at=3600, planned_duration_seconds=3600)
    assert _continue_candidate(_context(body=lifestyle_body), lifestyle) is not None
    consume = _activity(kind=ActivityKind.CONSUME,
                        planned_end_at=3600, planned_duration_seconds=3600,
                        data={"food_energy_kcal_per_hour": 1600})
    candidate = _continue_candidate(context, consume)
    assert candidate is not None and candidate.energy_delta > -candidate.energy_delta


def test_continue_candidate_skips_when_remaining_or_energy_infeasible() -> None:
    """剩余不足一分钟或能量不可完成时不出继续候选。"""
    almost_done = _activity(elapsed_seconds=570)
    assert _continue_candidate(_context(), almost_done) is None
    long_work = _activity(kind=ActivityKind.WORK, planned_end_at=7200,
                          planned_duration_seconds=7200)
    exhausted = _agent(body=BodyState(energy_kcal=10, sleep_pressure=0.0,
                                      sleep_deficit_hours=4.0, entertainment_need=0.5))
    assert _continue_candidate(_context(agent=exhausted), long_work) is None


def test_continue_candidate_skips_social_boundary_kinds_without_met_lookup() -> None:
    """交谈/通话不出继续候选：引擎仅接受取消，且其 kind 不在注册表 MET 表内。

    run11 观察降级复现面：chat 以 ACTIVE 跨决策边界后，继续候选按 kind 查
    activity_met 抛 KeyError('chat')，使 motivation_internal 整段从观察消失。
    """
    chat = _activity(kind=ActivityKind.CHAT, planned_end_at=900,
                     planned_duration_seconds=900)
    call = _activity(kind=ActivityKind.CALL, planned_end_at=900,
                     planned_duration_seconds=900)
    assert _continue_candidate(_context(), chat) is None
    assert _continue_candidate(_context(), call) is None


def test_sleep_deficit_delta_recovers_only_for_sleep() -> None:
    """睡眠按 1h/h 清账，其余活动按清醒标度累积。"""
    context = _context(agent=_agent())
    sleeping = _sleep_deficit_delta(context, ActivityKind.SLEEP, 1.0)
    awake = _sleep_deficit_delta(context, ActivityKind.WORK, 1.0)
    assert sleeping < 0 < awake


def _write(root: Path, relative: str, data: bytes) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_load_lifestyle_items_rejects_broken_catalogs(tmp_path) -> None:
    """生活目录闭集校验：缺 items/坏行/重复 id/身份不匹配逐项 fail-closed。"""
    good_row = {"activity_id": "walk", "name_zh": "散步", "category": "wellness",
                "typical_duration_minutes": 30, "description_zh": "走走",
                "met_value": 2.0, "effects": {"entertainment": 0.2, "social": 0.0, "health": 0.0}}
    _write(tmp_path, "lifestyle/items.json", json.dumps({"items": [good_row]}).encode())
    items = load_lifestyle_items(tmp_path, {"lifestyle:walk": 2.0})
    assert [item.activity_id for item in items] == ["walk"]

    def broken(payload) -> None:
        _write(tmp_path, "lifestyle/items.json", payload)
        with pytest.raises(IntegrityError):
            load_lifestyle_items(tmp_path, {"lifestyle:walk": 2.0})

    broken(json.dumps({"nope": 1}).encode())  # items 缺席
    broken(json.dumps({"items": [1]}).encode())  # 行不是对象
    broken(json.dumps({"items": [{"activity_id": "walk"}]}).encode())  # 行字段缺失
    broken(json.dumps({"items": [good_row, dict(good_row)]}).encode())  # 重复 id
    _write(tmp_path, "lifestyle/items.json", json.dumps({"items": [good_row]}).encode())
    with pytest.raises(IntegrityError, match="do not match"):
        load_lifestyle_items(tmp_path, {"lifestyle:walk": 2.0, "lifestyle:other": 1.0})


def test_reveal_mail_requires_visible_message() -> None:
    """邮件正文只对已到达且读完的活动揭示：不可见即拒绝。"""
    agent = _agent(visible_message_ids=set())
    activity = _activity(kind=ActivityKind.READ_MAIL, target_id="m1")
    with pytest.raises(BusinessRuleError, match="MESSAGE_NOT_VISIBLE"):
        reveal_mail(activity, agent, {})


def test_mail_headers_show_body_only_for_read_messages() -> None:
    """收件箱头：已读展示正文，未读只有标题。"""
    zone = ZoneInfo(TIMEZONE)
    messages = {
        "m1": MessageConfig.model_validate({
            "message_id": "m1", "thread_id": "t1", "recipient_agent_id": 1,
            "sent_at": datetime(2023, 11, 1, tzinfo=zone), "subject": "s1", "body": "b1"}),
        "m2": MessageConfig.model_validate({
            "message_id": "m2", "thread_id": "t2", "recipient_agent_id": 1,
            "sent_at": datetime(2023, 11, 2, tzinfo=zone), "subject": "s2", "body": "b2"}),
    }
    agent = _agent(visible_message_ids={"m1", "m2"}, read_message_ids={"m1"})
    headers = mail_headers(agent, messages)
    assert [record["message_id"] for record in headers] == ["m1", "m2"]
    assert headers[0]["read"] is True and headers[0]["body"] == "b1"
    assert headers[1]["read"] is False and "body" not in headers[1]


def test_update_travel_moves_location_only_on_arrival() -> None:
    """按完成比例推进距离，到达前不改变人物地点。"""
    _, _, state, trip = travel_case()
    midway = state.activities["travel-1"].model_copy(update={"elapsed_seconds": 100})
    changed_activity, same_agent = update_travel(midway, trip, state.agents[1])
    assert changed_activity.route_progress_meters == pytest.approx(trip.distance_meters * 100 / trip.duration_seconds)
    assert same_agent.location_id == "home"
    done = midway.model_copy(update={"elapsed_seconds": trip.duration_seconds})
    _, arrived = update_travel(done, trip, state.agents[1])
    assert arrived.location_id == "office"


def test_load_runtime_scenario_wraps_catalog_load_failures(tmp_path) -> None:
    """完整加载把目录装载错误包装为 IntegrityError（fail-closed）。"""
    with pytest.raises(IntegrityError, match="ENVIRONMENT_IDENTITY_MISMATCH"):
        load_runtime_scenario(ENVIRONMENT_ROOT / "scenarios/single-person.json",
                              catalog_dir=tmp_path / "missing-catalog")



def test_basket_meal_duration_enforces_longest_and_catalog_contract():
    """同餐固定类别时长取最长，未知食品和虚构时长均拒绝。"""
    foods = _registered()[0].catalog.foods
    meal = ConsumeRequest(items=(ConsumeItem(product_id="beef-noodles", quantity=1),
                                 ConsumeItem(product_id="chips", quantity=1)), use_mode="eat", duration_seconds=1200)
    assert meal_duration(foods, meal) == 1200
    with pytest.raises(BusinessRuleError, match="PRODUCT_NOT_FOUND"):
        meal_duration(foods, _consume("missing"))
    with pytest.raises(BusinessRuleError, match="MEAL_DURATION_MUST_BE_LONGEST"):
        meal_duration(foods, _consume(duration=600))


@pytest.mark.parametrize("count", [0, 4])
def test_basket_validate_meal_item_count_and_non_food_single(count):
    """当前食品同餐仍限定一至三项，旧非食品使用方式退出。"""
    request = ConsumeRequest.model_construct(items=tuple(ConsumeItem(product_id="chips", quantity=1)
                                           for _ in range(count)), use_mode="eat", duration_seconds=900)
    with pytest.raises(BusinessRuleError, match="MEAL_ITEM_COUNT_OUT_OF_RANGE"):
        validate_meal_use(_agent(), _registered()[0].catalog.foods, request)
    wrong = request.model_copy(update={"items": (ConsumeItem(product_id="chips", quantity=1),), "use_mode": "use"})
    with pytest.raises(BusinessRuleError, match="PRODUCT_USE_MODE_NOT_ALLOWED"):
        validate_meal_use(_agent(), _registered()[0].catalog.foods, wrong)


def test_basket_apply_meal_progress_is_incremental_and_does_not_double_count():
    """实际已开封食品只按进度差结算，重复结算不会重复获得热量。"""
    foods = _registered()[0].catalog.foods
    request = _consume()
    activity = _activity(kind=ActivityKind.CONSUME, planned_duration_seconds=1200, elapsed_seconds=600,
        data={"request": request.model_dump(mode="json"), "opened": {"beef-noodles": 1}})
    agent = _agent(body=_agent().body.model_copy(update={"energy_kcal": 500}))
    first = apply_meal_progress(agent, foods, activity, _body())
    second = apply_meal_progress(first, foods, activity, _body())
    assert activity.data["consumed"] == {"beef-noodles": 0.5}
    assert first.body.energy_kcal > agent.body.energy_kcal
    assert second == first


def test_basket_food_rate_uses_actual_category_energy_and_serving():
    """摄入速率仅由实际食品类别、份数和本餐时长形成。"""
    foods = _registered()[0].catalog.foods
    fixed = FOOD_CONSTANTS[foods["beef-noodles"].category]
    assert meal_food_rate(foods, _consume()) == fixed.energy_kcal * 3600 / 1200


def test_activity_factors_rejects_incomplete_frozen_evidence():
    """消费活动缺少有效摄入证据时结算明确失败。"""
    with pytest.raises(ValueError, match="food energy rate is invalid"):
        activity_factors(_activity(kind=ActivityKind.CONSUME, data={"food_energy_kcal_per_hour": -5}),
                         _agent(), _body())


def test_validate_work_start_en_route_is_not_at_the_workplace():
    """真实指派主题依照人物固定工作地点，不将途中视为在场。"""
    semantics = _registered()[0]
    config = semantics.scenario.agents[0]
    topic = semantics.catalog.work_topics[config.work_topic_ids[0]]
    with pytest.raises(BusinessRuleError, match="ACTIVITY_PRECONDITION_UNMET"):
        validate_work_start(config, topic, None)
    validate_work_start(config, topic, config.work_schedule.workplace_location_id)


@pytest.mark.parametrize("calls,match", [(None, "exactly one"), ([], "exactly one"),
    ([{"function": {"name": "other", "arguments": "{}"}}], "only tool call"),
    ([{"function": {"name": "report_status", "arguments": []}}], "JSON string")])
def test_tool_arguments_rejects_malformed_responses(calls, match):
    """当前原生工具参数提取拒绝错误数量、名称和非文本参数。"""
    with pytest.raises(ValueError, match=match):
        tool_arguments(calls, "report_status", ValueError)


def test_rules_lookup_helpers_reject_unknown_ids(engine_factory):
    """真实主题与应用查找只有登记对象可受理。"""
    rules = engine_factory().context.rules
    topic = next(iter(rules.work_topics.values()))
    assert rules.work_topic(topic.topic_id, 1) == topic
    app = next(iter(rules.apps.values()))
    assert rules.app(app.app_id) == app
    with pytest.raises(BusinessRuleError, match="WORK_TOPIC_NOT_FOUND"):
        rules.work_topic("missing-topic", 1)
    with pytest.raises(BusinessRuleError, match="TARGET_NOT_FOUND"):
        rules.app("missing-app")


def test_travel_candidate_rejects_short_or_energy_infeasible_legs():
    """出行候选依照真实图，不把原地视作出行，能量不足的路程不作动机建议。"""
    assert _travel_candidate(_context(), "work", "home") is None
    exhausted = _context(agent=_agent(body=_agent().body.model_copy(update={"energy_kcal": 1})))
    assert _travel_candidate(exhausted, "work", "office") is None
    assert _travel_candidate(_context(), "work", "office") is not None


def test_content_configuration_rejects_blank_or_missing_titles():
    """实际登记内容始终提供可读标题，缺失和空白标题均拒绝。"""
    row = next(iter(_registered()[0].catalog.contents.values())).model_dump(mode="json")
    assert Content.model_validate(row).title == row["title"]
    for title in ("", "   "):
        with pytest.raises(ValidationError):
            Content.model_validate({**row, "title": title})
    del row["title"]
    with pytest.raises(ValidationError):
        Content.model_validate(row)


def test_assembly_with_agentless_document_fails_closed():
    """当前场景必须声明真实人物，空人物文档在加载前即拒绝。"""
    row = _registered()[0].document.model_dump(mode="json")
    with pytest.raises(ValidationError):
        LifeScenario.model_validate({**row, "agents": []})


def test_travel_fare_rejects_an_unknown_mode():
    """出行只使用固定步行与驾车方式，不接受目录之外方式。"""
    catalog, _, _, _ = travel_case()
    with pytest.raises(ValueError, match="outside the fixed set"):
        plan_trip(catalog.graph, "home", "office", "biking")


def test_assembly_requires_unique_role_account_binding():
    """真实场景的工作主题需要显式唯一雇主账户，不能生成未声明账户。"""
    sem = _registered()[0]
    employers = [row for row in sem.document.accounts if row.role.startswith("employer:")]
    assert len(employers) == 1
    document = sem.document.model_copy(update={"accounts": tuple(row for row in sem.document.accounts
                                                              if row not in employers)})
    corpus_root = (ENVIRONMENT_ROOT / "scenarios" / document.corpus_root).resolve()
    with pytest.raises(IntegrityError, match="ENVIRONMENT_REF_UNKNOWN"):
        assemble_semantics(document, sem.catalog, corpus_root)
