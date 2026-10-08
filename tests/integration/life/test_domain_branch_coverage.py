"""@brief 当前统一动作、社交、身体、出行与严格目录的领域边界。"""

from functools import lru_cache

import pytest
from pydantic import ValidationError

from tests.data_paths import ENVIRONMENT_ROOT
from tests.life.purchase_worlds import registered_world
from tests.integration.life.test_travel_graph import travel_case
from worldmock.catalog.affordances import engine_affordances
from worldmock.catalog.models import Content
from worldmock.errors import BusinessRuleError, IntegrityError
from worldmock.exchange.types import ActionCategory
from worldmock.life import felt, social
from worldmock.life.activities import activity_factors, advance_activity, change_activity, truncate_activity
from worldmock.life.commerce import deliver_order
from worldmock.life.content import visible_mail
from worldmock.life.models import ActivityChange, ActivityKind, ActivityState, ActivityStatus, AgentState, ChangeActivityRequest
from worldmock.life.scenario import LifeScenario
from worldmock.life.semantics import assemble_semantics
from worldmock.life.social_actions import advance_social, end_partner_chats, partner_presence, shared_chat_seconds, start_chat
from worldmock.life.travel import plan_trip, update_travel
from worldmock.life.work import validate_work_start
from worldmock.life.world_setup import load_runtime_scenario
from worldmock.model_feedback import tool_arguments


@lru_cache(maxsize=1)
def _registered():
    """@brief 纯领域检查只读取真实登记环境。"""
    return registered_world()


def _body():
    """@brief 身体系数来自真实加载的系统常数。"""
    return _registered()[0].body


def _agent(**overrides):
    """@brief 完整严格人物只替换待验证的反事实字段。"""
    agent = _registered()[1].agents[1].model_copy(deep=True)
    values = {"body_settled_at": 0, "next_decision_at": 0,
              "body": agent.body.model_copy(update={"energy_kcal": 1600, "social_need": 0.8})}
    return AgentState.model_validate(agent.model_copy(update={**values, **overrides}).model_dump())


def _world(agent, *, activities=None, current_time=0):
    """@brief 保留完整登记世界，反事实只替换人物与真实活动。"""
    return _registered()[1].model_copy(update={"agents": {agent.agent_id: agent},
        "activities": activities or {}, "current_time": current_time}, deep=True)


def _activity(**overrides):
    """@brief 当前四类活动的纯夹具，非法分支显式绕过构造校验。"""
    kind = overrides.get("kind", ActivityKind.WAIT)
    claims = set(engine_affordances().resource_claims(kind.value)) if kind != ActivityKind.ACTION else {"screen"}
    payload = dict(activity_id="a1", agent_id=1, kind=kind, status=ActivityStatus.ACTIVE, started_at=0,
        planned_end_at=600, last_settled_at=0, elapsed_seconds=0, planned_duration_seconds=600,
        origin_action_id="act", origin_decision_id="dec", origin_request_event_id="req", location_id="home",
        resource_claims=claims, data={})
    return ActivityState.model_construct(**{**payload, **overrides})


def _presence(agent_id="agent:2", location="home", awake=True):
    """@brief 合作方在场只来自明确人物事实。"""
    return social.AgentPresence(agent_id=agent_id, name="B", location=location, activity_family="idle", awake=awake)


def _chat(activity_id, identity, counterpart, **overrides):
    """@brief 当面社交使用统一 ACTION 快照与实际对方身份。"""
    data = {"action_id": "conversation", "category": "social", "manner": None,
        "communication_mode": None, "app_id": None, "content_id": "conversation-topic", "series": None,
        "episode": None, "counterpart": f"agent:{counterpart}", "push_id": None, "consumed_seconds_before": 0}
    return _activity(activity_id=activity_id, agent_id=identity, kind=ActivityKind.ACTION,
                     resource_claims={"talk"}, data=data, **overrides)


def test_social_chat_requires_positive_duration_and_partner_recipient() -> None:
    """交谈域校验：时长必须为正且邀请收件人必须是对方本人。"""
    invite = social.OutgoingMessage(message_id="m1", sender_id="agent:1",
                                    recipient_id="agent:2", text="聊聊？", created_at=1)
    with pytest.raises(ValueError, match="duration must be positive"):
        social.start_chat("home", _presence(), 0, invite)
    wrong = invite.model_copy(update={"recipient_id": "agent:9"})
    with pytest.raises(ValueError, match="must be the partner"):
        social.start_chat("home", _presence(), 10, wrong)


def test_social_actions_partner_presence_rejects_unknown_partner() -> None:
    """引擎侧解析对方在场：目录外人物即业务拒绝。"""
    with pytest.raises(BusinessRuleError, match="SOCIAL_PARTNER_NOT_FOUND"):
        partner_presence(_world(_agent()), "agent:9")


def test_change_activity_rejects_foreign_activity() -> None:
    """活动属于调用者：他人活动拒绝（ACTIVITY_NOT_OWNED）。"""
    foreign = _activity(agent_id=2)
    request = ChangeActivityRequest(activity_id="a1", operation=ActivityChange.PAUSE)
    with pytest.raises(BusinessRuleError, match="ACTIVITY_NOT_OWNED"):
        change_activity(_agent(), foreign, request, 1000, 300)


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


@pytest.mark.parametrize("calls,match", [(None, "exactly one"), ([], "exactly one"),
    ([{"function": {"name": "other", "arguments": "{}"}}], "only tool call"),
    ([{"function": {"name": "report_status", "arguments": []}}], "JSON string")])
def test_tool_arguments_rejects_malformed_responses(calls, match):
    """当前原生工具参数提取拒绝错误数量、名称和非文本参数。"""
    with pytest.raises(ValueError, match=match):
        tool_arguments(calls, "report_status", ValueError)


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
    with pytest.raises(IntegrityError, match="ENVIRONMENT_REF_UNKNOWN"):
        assemble_semantics(document, sem.catalog)


def test_commerce_deliver_order_rejects_unknown_order():
    """@brief 唯一收货入口拒绝未知订单，不产生收货或账户变动。"""
    semantics, state = _registered()
    before = state.model_dump_json()
    with pytest.raises(BusinessRuleError, match="ORDER_NOT_FOUND"):
        deliver_order(state, semantics, "order-missing")
    assert state.model_dump_json() == before


def test_chat_acceptance_keeps_selected_content_and_rejects_absent_partner():
    """@brief 接受当面邀请保持各自已选内容；对方不在场不能假装交谈。"""
    invitation, chosen = _chat("other", 2, 1), _chat("mine", 1, 2)
    state = _world(_agent(), activities={invitation.activity_id: invitation})
    state.agents[2] = _agent(agent_id=2, name="B")
    accepted = start_chat(state, chosen, "invite")
    assert accepted.accepted == invitation and accepted.invite is None
    assert accepted.activity.data["content_id"] == chosen.data["content_id"]
    state.agents[2] = state.agents[2].model_copy(update={"location_id": "office"})
    with pytest.raises(BusinessRuleError, match="CHAT_PARTNER_NOT_AVAILABLE"):
        start_chat(state, chosen, "invite")


def test_shared_chat_seconds_uses_reciprocal_overlap_and_actual_start():
    """@brief 共同在场秒数只取双方实际重叠，未接受或对方在和别人交谈不能计入。"""
    mine, other = _chat("mine", 1, 2), _chat("other", 2, 1)
    state = _world(_agent(), activities={"mine": mine, "other": other})
    assert shared_chat_seconds(state, mine, 300) == shared_chat_seconds(state, other, 300) == 300
    state.activities["other"] = other.model_copy(update={"planned_end_at": 200})
    assert shared_chat_seconds(state, mine, 300) == 200
    state.activities["other"] = other.model_copy(update={"started_at": 100})
    assert shared_chat_seconds(state, mine, 300) == 0
    state.activities["other"] = _chat("other", 2, 3)
    assert shared_chat_seconds(state, mine, 300) == 0
    del state.activities["other"]
    assert shared_chat_seconds(state, mine, 300) == 0


def test_ending_chat_ends_partner_same_second_without_rewinding_content():
    """@brief 一方结束时对方同刻结束，内容游标保留真实已消费位置。"""
    mine, other = _chat("mine", 1, 2), _chat("other", 2, 1, media_position_seconds=120)
    before = _world(_agent(), activities={"mine": mine, "other": other}, current_time=300)
    ended = mine.model_copy(update={"status": ActivityStatus.CANCELLED, "ended_at": 300})
    after = before.model_copy(update={"activities": {"mine": ended, "other": other}}, deep=True)
    changed, partners = end_partner_chats(before, after)
    assert partners == [2]
    assert changed.activities["other"].ended_at == 300
    assert changed.activities["other"].boundary_type == "partner_ended"
    assert changed.activities["other"].media_position_seconds == 120


@pytest.mark.parametrize("app,shared", [("wechat", 0), (None, 3600), (None, 1800), (None, 0)])
def test_social_ledger_uses_actual_remote_or_reciprocal_seconds(app, shared):
    """@brief 远程与共同在场分别按固定速率缓解，不借旧活动分类。"""
    activity = _chat("mine", 1, 2, planned_end_at=3600, planned_duration_seconds=3600)
    activity.data["app_id"] = app
    result = advance_social(activity, _agent(), 3600, _body(), shared)
    exposure = (felt.SocialExposure(awake_hours=1, remote_hours=1) if app else
                felt.SocialExposure(awake_hours=1, conversation_hours=shared / 3600))
    expected = felt.apply_social_need_ledger(0.8, exposure)
    if app is None:
        from worldmock.exchange.constants import CHAT_RELIEF_DECLINED_PER_HOUR
        expected = max(0, expected - CHAT_RELIEF_DECLINED_PER_HOUR * (1 - shared / 3600))
    assert result.agent.body.social_need == pytest.approx(expected)
    assert result.activity.media_position_seconds == 3600


def test_action_category_body_coefficients_require_actual_frozen_category():
    """@brief 动作身体效果只按固定类别与真实 app 有无查表，任意类别不能落效果。"""
    action = _chat("mine", 1, 2)
    assert activity_factors(action, _agent(), _body()).met > 0
    broken = action.model_copy(update={"data": {**action.data, "category": "unknown"}})
    with pytest.raises(ValueError):
        activity_factors(broken, _agent(), _body())


def test_work_start_uses_assigned_content_and_actual_workplace():
    """@brief 环境指派内容只在本人工作地点可受理，途中和家里不能当工作地点。"""
    semantics = _registered()[0]
    config = semantics.scenario.agents[0]
    content = semantics.catalog.contents[config.work_content_ids[0]]
    action = semantics.catalog.actions[content.action_id]
    place = semantics.catalog.locations[config.work_schedule.workplace_location_id]
    with pytest.raises(BusinessRuleError, match="ACTIVITY_PRECONDITION_UNMET"):
        validate_work_start(config, content, None, action.name, place.display_name)
    validate_work_start(config, content, place.location_id, action.name, place.display_name)


def test_arrived_mail_is_visible_with_body_and_no_read_marker():
    """@brief 未到达邮件不可见，到达后全文立即可見，没有旧已读状态。"""
    semantics = _registered()[0]
    messages = {item.message_id: item for item in semantics.scenario.messages}
    assert visible_mail(_agent(visible_message_ids=set()), messages) == []
    agent = _agent(visible_message_ids=set(messages))
    rows = visible_mail(agent, messages)
    assert {row["message_id"] for row in rows} == set(messages)
    assert all(row["body"] == messages[row["message_id"]].body and "read" not in row for row in rows)
    assert "read_message_ids" not in agent.model_dump()


def test_current_app_lookup_requires_registered_identity(engine_factory):
    """@brief 应用查询仅取得现行登记对象，不猜未知应用。"""
    rules = engine_factory().context.rules
    app = next(iter(rules.apps.values()))
    assert rules.app(app.app_id) == app
    with pytest.raises(BusinessRuleError, match="TARGET_NOT_FOUND"):
        rules.app("missing-app")
