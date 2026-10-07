"""当前规则、场景、拒绝反馈与真实状态写入的公共合同边界。"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import json
import logging

import pytest
from pydantic import ValidationError

from tests import data_paths
from tests.life.conftest import start_activity
from tests.life.routing_fixtures import act
from tests.life.test_motivation import CONTRACT_PROFILE
from worldmock.errors import BusinessRuleError, IntegrityError, PreconditionError
from worldmock.life.durations import capped_duration_seconds
from worldmock.life.error_contract import Candidate, ErrorItem, ErrorReport, host_defaults
from worldmock.life.load_checks import check_location_kinds, check_visible_labels
from worldmock.life.models import ActivityKind, ActivityStartParams, validated_payload
from worldmock.life.scenario import BodyConfig, LifeScenario, MotivationProfile


@pytest.fixture
def scenario_document():
    """读取共享人工环境的真实结构七文档，每个用例独立改写。"""
    return json.loads((data_paths.SCENARIOS_ROOT / "single-person.json").read_text())


def _mail(document):
    """声明在场景窗口内发给已有主体的普通邮件。"""
    sent_at = datetime.fromisoformat(document["start_at"]) + timedelta(hours=1)
    return {"message_id": "guard-mail", "thread_id": "guard-thread", "recipient_agent_id": 1,
            "sent_at": sent_at.isoformat(), "subject": "普通邮件", "body": "今天记得联系。"}


@pytest.mark.parametrize("duration, expected", [(60, 60), (86401, 86400)])
def test_duration_accepts_minimum_and_caps_at_one_day(duration, expected):
    """一分钟与一天上限使用同一正常活动时长合同。"""
    assert capped_duration_seconds(duration) == expected


def test_duration_refuses_less_than_one_minute():
    """不足一分钟应被业务拒绝，不能制造零长度活动。"""
    with pytest.raises(BusinessRuleError, match="^ACTIVITY_DURATION_INVALID$"):
        capped_duration_seconds(59)


@pytest.mark.parametrize("change", ["path", "expected", "why", "candidates"])
def test_error_item_refuses_missing_explanation_or_too_many_candidates(change, caplog):
    """无定位、无解释或超过三条建议不能作为可执行反馈。"""
    arguments = {"path": ("items", 0), "written": None, "error_code": "ARGUMENTS_INVALID"}
    arguments[change] = tuple(Candidate("start_activity", {}) for _ in range(4)) if change == "candidates" else (
        () if change == "path" else "")
    with caplog.at_level(logging.ERROR), pytest.raises(ValueError, match="require a path and explanations"):
        ErrorItem(**arguments)
    assert "Error item has invalid path, explanation or candidate count" in caplog.text


def test_valid_error_item_and_report_preserve_nulls_and_complete_feedback():
    """正常五字段反馈保留原值、完整位置与三条可用建议。"""
    candidates = tuple(Candidate("start_activity", {"items": []}, "Select a visible app.") for _ in range(3))
    item = ErrorItem(("items", 0, "target"), None, "NAME_NOT_FOUND", candidates=candidates)
    report = ErrorReport("NAME_NOT_FOUND", "An exact known name is required.", items=(item,))
    payload = report.payload()
    assert set(payload) == {"error_code", "reason", "fix", "items", "unmet"}
    assert payload["fix"] is None and payload["unmet"] == []
    assert payload["items"][0]["written"] is None
    assert payload["items"][0]["path"] == ["tool_calls", 0, "function", "arguments", "items", 0, "target"]
    assert payload["items"][0]["candidates"] == [candidate.payload() for candidate in candidates]


@pytest.mark.parametrize("code, reason", [("", "A readable reason."), ("ARGUMENTS_INVALID", "")])
def test_error_report_refuses_missing_code_or_reason(code, reason, caplog):
    """原子拒绝必须携带业务码与可读原因。"""
    with caplog.at_level(logging.ERROR), pytest.raises(ValueError, match="requires a code and reason"):
        ErrorReport(code, reason)
    assert "Error report has no code or reason" in caplog.text


def test_host_error_defaults_are_closed_and_work_topic_guidance_is_exact(caplog):
    """已分配主题错误使用冻结说明，未知码明确失败。"""
    assert host_defaults("WORK_TOPIC_NOT_FOUND") == (
        "The named work topic is not assigned to you.", "Use an exact known work topic name.")
    with caplog.at_level(logging.ERROR), pytest.raises(ValueError, match="unknown host error code: INVENTED"):
        host_defaults("INVENTED")
    assert "Unknown host error code: INVENTED" in caplog.text


def test_runtime_location_guard_refuses_corrupted_kind_before_observation(engine_factory, caplog):
    """已加载目录仍在运行校验边界拒绝损坏的地点类别。"""
    engine = engine_factory()
    scenario = engine.context.scenario
    check_location_kinds(scenario)
    original = scenario.locations[0]
    corrupted = original.model_copy(update={"kind": "invented-place"})
    changed = scenario.model_copy(update={"locations": (corrupted, *scenario.locations[1:])})
    with caplog.at_level(logging.ERROR), pytest.raises(IntegrityError, match="not place types") as caught:
        check_location_kinds(changed)
    assert original.location_id in str(caught.value) and "invented-place" in str(caught.value)
    assert "allowed kinds:" in str(caught.value) and "not place types" in caplog.text


def test_runtime_place_names_require_actual_branch_disambiguation(engine_factory, caplog):
    """两个地点显示名重合必须在呈现之前拒绝，并保留分店标签建议。"""
    engine = engine_factory()
    scenario = engine.context.scenario
    check_visible_labels(scenario, engine.context.corpus_root)
    first, second, *rest = scenario.locations
    duplicate = second.model_copy(update={"name": first.name, "branch_label": first.branch_label})
    changed = scenario.model_copy(update={"locations": (first, duplicate, *rest)})
    with caplog.at_level(logging.ERROR), pytest.raises(IntegrityError, match="share display names") as caught:
        check_visible_labels(changed, engine.context.corpus_root)
    error = str(caught.value)
    assert first.display_name in error and first.location_id in error and second.location_id in error
    assert "branch_label" in error and "share display names" in caplog.text


@pytest.mark.parametrize("drift, reason", [
    ("settlement", "world activity settlement time is invalid"),
    ("remaining", "world activity remaining duration is invalid"),
    ("claims", "world activity resource claims disagree with the registry"),
    ("overlap", "world active activities claim the same resource"),
])
def test_actual_activity_state_is_rechecked_before_persisting(engine_factory, drift, reason, caplog):
    """真实打开app形成的活动不能以损坏时序、剩余时间或资源占用重新持久化。"""
    engine = engine_factory()
    rules = engine.context.rules
    app = next(item for item in rules.apps.values() if rules.contents_by_app[item.app_id])
    started = act(engine, "guard-open", start_activity("打开", app.name))
    assert started.status == "in_progress"
    state = engine.context.state.model_copy(deep=True)
    activity = state.activities[started.activity_id]
    assert activity.resource_claims and activity.elapsed_seconds == 0
    assert validated_payload(state)["activities"][started.activity_id]["target_id"] == app.app_id
    committed = deepcopy(engine.context.store.state)
    if drift == "settlement":
        activity.elapsed_seconds = 1
    elif drift == "remaining":
        activity.planned_end_at += 1
    elif drift == "claims":
        activity.resource_claims = set()
    else:
        duplicate = activity.model_copy(update={"activity_id": "guard-copy"}, deep=True)
        state.activities[duplicate.activity_id] = duplicate
    with caplog.at_level(logging.ERROR), pytest.raises(IntegrityError, match=reason):
        validated_payload(state)
    assert engine.context.store.state == committed
    assert reason.lower() in caplog.text.lower()


def test_rule_admission_accepts_content_app_and_refuses_registered_empty_app(engine_factory, caplog):
    """app已登记仍须有可用内容，正常受理只返回真实app身份。"""
    engine = engine_factory()
    rules, state = engine.context.rules, engine.context.state
    agent = state.agents[1]
    content_app = next(item for item in rules.apps.values() if rules.contents_by_app[item.app_id])
    empty_app = next(item for item in rules.apps.values() if not rules.contents_by_app[item.app_id])
    request = ActivityStartParams(activity_type=ActivityKind.USE_APP,
                                  planned_duration_seconds=2400, target_id=content_app.app_id)
    assert rules.validate_start(agent, request, state.current_time, agent.location_id) == {
        "app_id": content_app.app_id}
    request = request.model_copy(update={"target_id": empty_app.app_id})
    with caplog.at_level(logging.ERROR), pytest.raises(BusinessRuleError, match="^APP_CONTENT_EMPTY$"):
        rules.validate_start(agent, request, state.current_time, agent.location_id)
    assert "Selected app has no content" in caplog.text


def test_work_rule_refusal_suggests_the_real_workplace_name(engine_factory):
    """在家拒绝工作应建议真实工作地点，在现场则返回已分配主题。"""
    engine = engine_factory()
    rules, state = engine.context.rules, engine.context.state
    config = rules.scenario.agents[0]
    topic_id = config.work_topic_ids[0]
    agent = state.agents[config.agent_id]
    workplace = config.work_schedule.workplace_location_id
    request = ActivityStartParams(activity_type=ActivityKind.WORK,
                                  planned_duration_seconds=3600, target_id=topic_id)
    with pytest.raises(PreconditionError, match="ACTIVITY_PRECONDITION_UNMET") as caught:
        rules.validate_start(agent, request, state.current_time, agent.location_id)
    assert caught.value.unmet == ["required_location_id"]
    assert caught.value.suggested == {"travel_to": rules.locations[workplace].name}
    present = agent.model_copy(update={"location_id": workplace})
    assert rules.validate_start(present, request, state.current_time, workplace) == {"topic_id": topic_id}


def test_registered_topic_is_private_to_its_assigned_person(engine_factory, caplog):
    """同一世界中的另一人物不能受理未分配给自己的已登记主题。"""
    engine = engine_factory("multi-person.json")
    rules = engine.context.rules
    assert rules.work_topic("topic-sales", 1).topic_id == "topic-sales"
    with caplog.at_level(logging.ERROR), pytest.raises(BusinessRuleError, match="^WORK_TOPIC_NOT_FOUND$"):
        rules.work_topic("topic-sales", 2)
    assert "Work topic is not assigned to this agent" in caplog.text


def test_resolved_body_view_refuses_hunger_threshold_above_energy_capacity(engine_factory, caplog):
    """正常固定规则身体视图可用，但体感阈值不能越过能量容量。"""
    engine = engine_factory()
    body = engine.context.scenario.body.model_dump(mode="json")
    assert BodyConfig.model_validate(body).hunger_threshold_kcal == body["hunger_threshold_kcal"]
    body.update(initial_energy_kcal=0, maximum_energy_kcal=body["hunger_threshold_kcal"] - 1)
    with caplog.at_level(logging.ERROR), pytest.raises(ValidationError, match="hunger threshold exceeds maximum"):
        BodyConfig.model_validate(body)
    assert "body hunger threshold exceeds maximum energy" in caplog.text


def test_motivation_trait_keys_are_closed_and_preserve_actual_work_aspiration(caplog):
    """完整动机档案使用五个特质与实际工时，不能用任意同数量的键替换。"""
    document = deepcopy(CONTRACT_PROFILE)
    assert MotivationProfile.model_validate(document).aspiration == {"daily_work_seconds": 14400}
    document["big_five"]["invented"] = document["big_five"].pop("openness")
    with caplog.at_level(logging.ERROR), pytest.raises(ValidationError, match="exactly the five trait keys"):
        MotivationProfile.model_validate(document)
    assert "motivation big_five must use exactly the five trait keys" in caplog.text


@pytest.mark.parametrize("topics", [("topic-sales", "topic-sales"), (" ",)])
def test_scenario_refuses_duplicate_or_blank_assigned_topic_names(scenario_document, topics, caplog):
    """工作分配使用唯一非空主题身份。"""
    assert LifeScenario.model_validate(scenario_document).agents[0].work_topic_ids == ("topic-sales",)
    scenario_document["agents"][0]["work_topic_ids"] = topics
    with caplog.at_level(logging.ERROR), pytest.raises(ValidationError, match="unique non-empty names"):
        LifeScenario.model_validate(scenario_document)
    assert "work topic ids must be unique non-empty names" in caplog.text


@pytest.mark.parametrize("inventory, reason", [
    ({" ": {"chips": 1}}, "initial inventory locations and holdings must not be empty"),
    ({"home": {}}, "initial inventory locations and holdings must not be empty"),
    ({"home": {" ": 1}}, "initial inventory object ids must not be empty"),
])
def test_scenario_refuses_empty_initial_holding_identity(scenario_document, inventory, reason, caplog):
    """预置食品必须有真实地点、非空持有集合与对象身份。"""
    assert LifeScenario.model_validate(scenario_document).agents[0].home_location_id == "home"
    scenario_document["agents"][0]["initial_inventory"] = inventory
    with caplog.at_level(logging.ERROR), pytest.raises(ValidationError, match=reason):
        LifeScenario.model_validate(scenario_document)
    assert reason in caplog.text


@pytest.mark.parametrize("section, reason", [
    ("contact", "contact receive location is not a declared location"),
    ("message", "message references an unknown agent"),
])
def test_scenario_rejects_unknown_contact_delivery_place_or_mail_recipient(scenario_document, section, reason):
    """社交收货地点与邮件收件主体必须在当前世界中存在。"""
    if section == "contact":
        contact = {"contact_id": "friend", "salutation": "朋友", "relation": "friend", "closeness": 10,
                   "channels": ["message"], "reply_policy": "none", "receive_location_id": "home"}
        scenario_document["agents"][0]["contacts"] = [contact]
        assert LifeScenario.model_validate(scenario_document).agents[0].contacts[0].receive_location_id == "home"
        contact["receive_location_id"] = "unknown-place"
    else:
        mail = _mail(scenario_document)
        scenario_document["messages"] = [mail]
        assert LifeScenario.model_validate(scenario_document).messages[0].recipient_agent_id == 1
        mail["recipient_agent_id"] = 999
    with pytest.raises(ValidationError, match=reason):
        LifeScenario.model_validate(scenario_document)


def test_scenario_requires_menus_for_every_fixed_location_kind(scenario_document):
    """八类地点菜单是场景完整合同，即使该类没有选中地点也不能缺失。"""
    assert len(LifeScenario.model_validate(scenario_document).interaction_menus_by_location_kind) == 8
    scenario_document["interaction_menus_by_location_kind"].pop("cafe")
    with pytest.raises(ValidationError, match="all fixed location kinds"):
        LifeScenario.model_validate(scenario_document)


@pytest.mark.parametrize("role, reason", [
    ("duplicate", "account roles must be unique"),
    ("unknown", "account role is outside the fixed account domain"),
])
def test_scenario_account_roles_are_unique_and_closed(scenario_document, role, reason):
    """不同账户身份不能复用角色，角色也不能越出已声明账户域。"""
    assert LifeScenario.model_validate(scenario_document).accounts
    if role == "duplicate":
        added = deepcopy(scenario_document["accounts"][0])
        added["account_id"] = "guard:account"
        scenario_document["accounts"].append(added)
    else:
        scenario_document["accounts"][0]["role"] = "invented:account"
    with pytest.raises(ValidationError, match=reason):
        LifeScenario.model_validate(scenario_document)


@pytest.mark.parametrize("clock", ["end", "wrong_offset"])
def test_scenario_mail_clock_matches_declared_zone_and_precedes_end(scenario_document, clock):
    """普通邮件正常进入窗口，但结束点及错误区域偏移不能被受理。"""
    mail = _mail(scenario_document)
    scenario_document["messages"] = [mail]
    assert LifeScenario.model_validate(scenario_document).messages[0].message_id == "guard-mail"
    mail["sent_at"] = scenario_document["end_at"] if clock == "end" else "2026-01-05T01:00:00+00:00"
    with pytest.raises(ValidationError, match="message timestamp is invalid"):
        LifeScenario.model_validate(scenario_document)


@pytest.mark.parametrize("field, reason, logged", [
    ("calendar", "calendar date must use ISO format", "Calendar date must use ISO format"),
    ("timezone", "scenario timezone is unknown", "Scenario timezone is unknown"),
    ("messages", "message_id values must be unique", "message_id values must be unique"),
])
def test_scenario_calendar_zone_and_message_identity_are_authentic(scenario_document, field, reason, logged, caplog):
    """日期须可解析、区域须真实存在、消息身份须唯一，拒绝时保留英文日志。"""
    assert LifeScenario.model_validate(scenario_document).scenario_id
    if field == "calendar":
        scenario_document["calendar"]["social_dinner_date"] = "not-a-date"
    elif field == "timezone":
        scenario_document["timezone"] = "Mars/Olympus"
    else:
        mail = _mail(scenario_document)
        scenario_document["messages"] = [mail, deepcopy(mail)]
    with caplog.at_level(logging.ERROR), pytest.raises(ValidationError, match=reason):
        LifeScenario.model_validate(scenario_document)
    assert logged in caplog.text
