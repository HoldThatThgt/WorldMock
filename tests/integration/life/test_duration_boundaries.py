"""@brief 内容、睡眠需要和反馈间隔在呈现与真实受理中使用同一派生时长。"""

import json
import shutil

import pytest

from tests.data_paths import ARTIFICIAL_ENVIRONMENT_ROOT
from tests.life.conftest import stage_day_plan
from tests.integration.life.test_acs_amended_runtime import perform
from tests.integration.life.test_acs_runtime import request
from worldmock.catalog.identity import refresh_manifest_identity
from worldmock.life.models import ActivityKind
from worldmock.life.readable import duration_phrase
from worldmock.life.tool_layer import _facts_seconds, _content_facts


@pytest.mark.parametrize("feedback, seconds", [(60, 120), (86400, 2400)])
def test_content_duration_is_derived_in_listing_and_real_admission(engine_factory, tmp_path, feedback, seconds):
    """@brief 同一动作的实际内容决定计划，不由应用常数或反馈间隔决定。"""
    directory = tmp_path / "duration-environment"
    shutil.copytree(ARTIFICIAL_ENVIRONMENT_ROOT, directory)
    path = directory / "catalog/contents.json"
    contents = json.loads(path.read_text())
    next(row for row in contents["contents"] if row["content_id"] == "c-course")["duration_seconds"] = seconds
    path.write_text(json.dumps(contents, ensure_ascii=False), encoding="utf-8")
    refresh_manifest_identity(directory / "catalog")
    scenario = directory / "scenarios/single-person.json"
    document = json.loads(scenario.read_text())
    document["activity_feedback_seconds"] = feedback
    scenario.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    engine = engine_factory(scenario)
    stage_day_plan(engine)
    layer = engine.observations._layer_context(engine.context.state.agents[1], ())
    action = engine.context.semantics.catalog.actions["study-course"]
    facts = _content_facts(layer, {"object_id": action.action_id},
                           {"app_id": "bilibili", "category": action.category, "manner": action.manner})
    assert facts["duration"] == duration_phrase(seconds)
    result = perform(engine, "course-start", request("学课程", "哔哩哔哩"))
    assert result.status == "in_progress", result.data
    activity = engine.context.state.activities[result.activity_id]
    assert activity.kind == ActivityKind.ACTION
    assert activity.data["content_id"] == "c-course"
    assert activity.planned_duration_seconds == seconds


@pytest.mark.parametrize("hours, expected", [(7.0, 25_200), (7.123456, 25_644), (9.0, 32_400)])
def test_sleep_duration_uses_personal_need_in_listing_and_real_admission(engine_factory, hours, expected):
    """@brief 人物睡眠需要在呈现和真实受理中得到同一整秒计划。"""
    engine = engine_factory(mutate=lambda document: document["agents"][0].update(sleep_need_hours=hours))
    stage_day_plan(engine)
    agent = engine.context.state.agents[1]
    layer = engine.observations._layer_context(agent, ())
    assert _facts_seconds(layer, {"object_id": "agent:1"}, {"canonical_action": "sleep"}) == expected
    result = perform(engine, "sleep-start", request("自己", None, "睡觉"))
    assert result.status == "in_progress", result.data
    activity = engine.context.state.activities[result.activity_id]
    assert activity.kind == ActivityKind.SLEEP
    assert activity.planned_duration_seconds == expected


@pytest.mark.parametrize("seconds", [60, 86_400])
def test_feedback_duration_matches_listing_and_real_admission(engine_factory, seconds):
    """@brief 等待的实际计划与呈现同时使用场景反馈间隔的合法上下界。"""
    engine = engine_factory(mutate=lambda document: document.update(activity_feedback_seconds=seconds))
    stage_day_plan(engine)
    agent = engine.context.state.agents[1]
    layer = engine.observations._layer_context(agent, ())
    assert _facts_seconds(layer, {"object_id": "agent:1"}, {"canonical_action": "wait"}) == seconds
    result = perform(engine, "wait-start", request("自己", None, "等待"))
    assert result.status == "in_progress", result.data
    activity = engine.context.state.activities[result.activity_id]
    assert activity.kind == ActivityKind.WAIT
    assert activity.planned_duration_seconds == seconds
