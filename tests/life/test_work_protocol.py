"""@brief 工作主题经真实引擎、人物工具和提示闭集产生连续叙述与工时事实。"""

from __future__ import annotations

import json

import pytest
from agentsociety2.agent.base import ReactDecision, ReactLoopRequest
from pydantic import ValidationError

from custom.agents.life_agent import LifeAgent
from worldmock.agents.life_prompt import LIFE_SYSTEM_LAYER
from custom.envs.life_env import LifeEnv, _STABLE_TOOL_SURFACE
from tests.life.conftest import (activity_name, advance_next, advance_until_ready, conclude_request, context, pause_request, report_request,
                                 stage_day_plan, start_activity, submit_round, wake_by_mail)
from worldmock.errors import IntegrityError
from worldmock.footprints.models import EventDraft
from worldmock.life.models import ActivityKind, REQUEST_MODELS
from worldmock.life.prompt_schemas import (PROGRESS_LAYER_SCHEMA, TOOL_LAYER_SCHEMA, validate_layer)
from worldmock.life.work import contract_view, work_totals
from worldmock.run_setup import stage_model_identity, validate_stage_model_identity


def trip_to(engine, destination: str, label: str) -> None:
    """@brief 经真实出行请求、时钟推进和收尾改变停留会话。"""
    place = next(item.name for item in engine.context.scenario.locations if item.location_id == destination)
    _, results = submit_round(engine, {1: ("start_activity", start_activity("步行", f"地点：{place}"))}, label)
    assert results[0].status == "in_progress", (results[0].error_code, results[0].data)
    key = results[0].activity_id
    name = activity_name(engine, key)
    while engine.context.state.activities[key].status == "active":
        if engine.scheduling_state().ready_agent_ids:
            submit_round(engine, {1: ("report_status", report_request(name))}, f"{label}-report-{engine.context.state.version}")
        advance_next(engine)
    submit_round(engine, {1: ("conclude_activity", conclude_request(name))}, f"{label}-conclude")


def wake_for_control(engine, offset: int = 60) -> None:
    """@brief 实际邮件到达提供下一次控制机会，不直接改决策时刻。"""
    wake_by_mail(engine, offset=offset)
    assert advance_until_ready(engine)


def at_workplace(engine) -> None:
    """@brief 人物通过真实出行到自己的工作地点，不复制主题完成状态。"""
    stage_day_plan(engine)
    trip_to(engine, engine.context.scenario.agents[0].work_schedule.workplace_location_id, "arrive-office")


def observed(engine, label: str) -> dict:
    """@brief 真实观察写入事件，直接验证两个受工作影响的提示层。"""
    data = engine.observe(context(engine, label))
    validate_layer(data["tool_layer"])
    validate_layer(data["progress_layer"])
    return data


def test_work_contract_and_reports_use_real_hours_without_submission(engine_factory) -> None:
    """@brief 工作自开始可见，文字完成不改工时，播报回执和历史取实际结算。"""
    engine = engine_factory()
    topic = engine.context.scenario.work_topics[0]
    initial = observed(engine, "work-initial")
    task = next(row for row in initial["tool_layer"]["targets"] if row["kind"] == "task")
    assert task["contract"] == contract_view(topic, 0)
    assert task["tools"] == []
    assert initial["work_topics"] == [{"topic_id": topic.topic_id, "topic": topic.topic,
                                       "target_seconds": topic.target_seconds, "worked_seconds": 0}]
    at_workplace(engine)
    _, results = submit_round(engine, {1: ("start_activity", start_activity("工作", task["name"]))}, "work-start")
    started = results[0]
    assert started.status == "in_progress"
    activity = engine.context.state.activities[started.activity_id]
    assert activity.data == {"topic_id": topic.topic_id, "worked_before_seconds": 0}
    name = activity_name(engine, started.activity_id)
    wake_for_control(engine)
    before = work_totals(engine.context.state, 1, topic.topic_id)
    first_reported_at = engine.context.state.current_time
    _, reports = submit_round(engine, {1: ("report_status", report_request(name, summary="已完成全部工作。"))},
                              "work-report-first")
    assert work_totals(engine.context.state, 1, topic.topic_id) == before
    assert reports[0].data["work"] == [{"name": name, "done": "约 0 小时", "remaining": "约 8 小时"}]
    assert not [event for event in engine.context.store.read_events(engine.context.store.last_event_seq) if event.data.get("kind") == "work_topic_completed"]
    wake_for_control(engine, offset=600)
    _, reports = submit_round(engine, {1: ("report_status", report_request(name, summary="正在核对销售记录。"))},
                              "work-report-second")
    view = observed(engine, "work-reported")
    current = next(row for row in view["tool_layer"]["targets"] if row["name"] == name)
    assert current["last_report"] == "正在核对销售记录。"
    assert current["state"] == "已做约 0.2 小时，还差约 7.8 小时"
    assert reports[0].data["work"] == [{"name": name, "done": "约 0.2 小时", "remaining": "约 7.8 小时"}]
    recall = engine.read_task_summary(1, REQUEST_MODELS["recall_task_summary"](task_name=task["name"]), view)
    assert recall["recall"]["progress_note"] == "正在核对销售记录。"
    assert recall["recall"]["note_history"] == [{"updated_at": first_reported_at, "note": "已完成全部工作。"}]
    assert "deadline" not in json.dumps(view["tool_layer"], ensure_ascii=False)


def test_work_report_survives_real_session_change(engine_factory) -> None:
    """@brief 暂停、离开并返回后，恢复仍读取同一真实工作活动最后播报。"""
    engine = engine_factory()
    at_workplace(engine)
    topic = engine.context.scenario.work_topics[0]
    _, started = submit_round(engine, {1: ("start_activity", start_activity("工作", f"工作：{topic.topic}"))}, "cross-start")
    name = activity_name(engine, started[0].activity_id)
    wake_for_control(engine)
    submit_round(engine, {1: ("report_status", report_request(name, summary="先核对了季度总额。"))}, "cross-report")
    wake_for_control(engine)
    submit_round(engine, {1: ("pause_activity", pause_request(name, summary="先核对了季度总额。"))}, "cross-pause")
    config = engine.context.scenario.agents[0]
    trip_to(engine, config.home_location_id, "cross-go-home")
    trip_to(engine, config.work_schedule.workplace_location_id, "cross-return-office")
    submit_round(engine, {1: ("report_status", report_request(name, summary="先核对了季度总额。"))}, "cross-resume")
    target = next(row for row in observed(engine, "cross-view")["tool_layer"]["targets"] if row["name"] == name)
    assert target["last_report"] == "先核对了季度总额。"
    assert len(engine.context.state.activities[started[0].activity_id].session_ids) == 2


def test_work_tool_and_stage_closed_sets_match_real_host() -> None:
    """@brief 世界工具八个、原生工具十一个，身份只允许行动并明确拒绝评审。"""
    from tests.integration.test_life_agent_protocol import _prepared
    agent = _prepared()
    offered = agent.build_react_tool_schemas(readonly=False)
    assert len(offered) == 11
    assert len(_STABLE_TOOL_SURFACE) == 8
    assert "submit_work" not in REQUEST_MODELS and "submit_work" not in _STABLE_TOOL_SURFACE
    assert "submit_work" not in LIFE_SYSTEM_LAYER["rules"][1]
    identity = stage_model_identity()
    assert set(identity["stages"]) == {"action"}
    identity["stages"]["work_review"] = {"thinking": "disabled", "tool_choice": "required"}
    with pytest.raises(IntegrityError, match="Model identity stage work_review is retired"):
        validate_stage_model_identity(identity)


def test_work_prompt_schema_rejects_old_contract_review_and_completion_shapes() -> None:
    """@brief 旧工作合同、工作评审结果和材料相关邮件不满足当前提示闭集。"""
    from jsonschema import Draft202012Validator
    target = {"name": "工作：季度销售", "kind": "task", "phase": None, "state": None,
              "contract": {"topic": "季度销售", "target": "约 8 小时", "done": "约 0 小时", "remaining": "约 8 小时"},
              "actions": [], "tools": []}
    layer = {"layer": "工具层", "targets": [target], "callable_index": {
        "lifestyle": [], "goods": [], "query_categories": []}}
    validator = Draft202012Validator(TOOL_LAYER_SCHEMA)
    assert not list(validator.iter_errors(layer))
    target["contract"]["deadline"] = "2026-01-05T00:00:00+08:00"
    assert list(validator.iter_errors(layer))
    schema = PROGRESS_LAYER_SCHEMA["properties"]["events"]["items"]
    valid = {"occurred_at": "2026-01-05T00:00:00+08:00", "type": "work_completed", "topic": "季度销售"}
    assert not list(Draft202012Validator(schema).iter_errors(valid))
    assert list(Draft202012Validator(schema).iter_errors({**valid, "worked_seconds": 28800}))
    result = {"occurred_at": valid["occurred_at"], "type": "tool_call", "tool": "report_status", "arguments": {},
              "result": {"occurred_at": valid["occurred_at"], "status": "completed", "error_code": None,
                         "message": "reported", "details": {}, "work_review": {}}}
    assert list(Draft202012Validator(schema).iter_errors(result))


def test_unknown_work_name_has_the_same_host_and_environment_code(engine_factory) -> None:
    """@brief 未分配主题在真实宿主和环境直调均给出唯一工作主题错误，不落入通用名称错误。"""
    from agentsociety2.agent.base import ReactDecision
    from tests.life.conftest import start_arguments
    from tests.life.test_tool_layer import _host
    engine = engine_factory()
    stage_day_plan(engine)
    data = observed(engine, "unknown-work-preview")
    person = _host(engine, context(engine, "unknown-work-host"), data)
    arguments = start_arguments(["工作：不存在的主题"], ["interact"], ["工作"])
    error = person.validate_react_decisions([ReactDecision("", "start_activity", arguments, "")], False)
    assert error.startswith("WORK_TOPIC_NOT_FOUND:")
    _, results = submit_round(engine, {1: ("start_activity", REQUEST_MODELS["start_activity"](**arguments))},
                              "unknown-work-environment")
    assert results[0].error_code == "WORK_TOPIC_NOT_FOUND"
    assert results[0].data["reason"] == "The named work topic is not assigned to you."
    assert results[0].data["next_step"] == "Use an exact known work topic name."
