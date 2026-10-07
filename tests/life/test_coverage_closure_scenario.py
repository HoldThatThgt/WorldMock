"""覆盖闭合：场景文档与运行视图的手机模拟声明配对、场景文件符号链接、持久活动目标与内容查找的失败关闭分支。"""

from __future__ import annotations

from tests.data_paths import ARTIFICIAL_ENVIRONMENT_ROOT, ENVIRONMENT_ROOT

import json
import logging
from pathlib import Path

import pytest
from pydantic import ValidationError

from worldmock.errors import BusinessRuleError, IntegrityError
from worldmock.life.activities import ActivityOrigin, start_activity
from worldmock.life.activity_validation import validate_activity_targets
from worldmock.life.engine import LifeEngine
from worldmock.life.models import ActivityKind, ActivityStartParams
from worldmock.life.scenario import RuntimeScenario, parse_scenario_document
from worldmock.life.world_setup import load_scenario
from tests.life.conftest import stage_day_plan, start_activity as named_start, submit_round
from tests.life.test_location_sessions import _stage

_FORMAL = ENVIRONMENT_ROOT / "scenarios/single-person.json"
_REMOVED_FIELDS = "Extra inputs are not permitted"
#: 只声明手机模拟配置的一半：有路径无哈希、有哈希无路径。
_HALF_PHONE_DECLARATIONS = (
    {"phone_simulation_config": "phone-simulation.json"},
    {"phone_simulation_config_sha256": "a" * 64},
)
_HALF_IDS = ["path-only", "hash-only"]


def _origin(name: str) -> ActivityOrigin:
    """形成可核对的持久活动来源身份。"""
    return ActivityOrigin(name, f"decision-{name}", f"request-{name}")


@pytest.mark.parametrize("half", _HALF_PHONE_DECLARATIONS, ids=_HALF_IDS)
def test_scenario_loader_rejects_a_half_declared_phone_simulation_config(half: dict, tmp_path: Path) -> None:
    """真实加载器读到只声明一半手机模拟配置的场景文档时以校验失败拒绝，原因指明两项不再受理退役字段。"""
    payload = json.loads(_FORMAL.read_text(encoding="utf-8"))
    assert "phone_simulation_config" not in payload
    payload.update(half)
    document = tmp_path / "half-phone.json"
    document.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(IntegrityError, match="^Scenario validation failed$") as rejected:
        parse_scenario_document(document)
    assert isinstance(rejected.value.__cause__, ValidationError)
    assert _REMOVED_FIELDS in str(rejected.value.__cause__)


@pytest.mark.parametrize("half", _HALF_PHONE_DECLARATIONS, ids=_HALF_IDS)
def test_runtime_view_rejects_a_half_declared_phone_simulation_config(half: dict) -> None:
    """运行视图同样拒绝退役字段：由正式场景导出的运行视图只补一半声明也不能重建。"""
    scenario, _ = load_scenario(_FORMAL)
    runtime = scenario.model_dump(mode="json")
    runtime.update(half)
    with pytest.raises(ValidationError, match=_REMOVED_FIELDS):
        RuntimeScenario.model_validate(runtime)


def test_scenario_loader_refuses_a_symbolic_link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """场景文件被标记为符号链接即拒绝，即使链接内容是完整有效的正式场景。"""
    link = tmp_path / "linked-scenario.json"
    link.write_bytes(_FORMAL.read_bytes())
    original_is_symlink = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == link or original_is_symlink(path))
    with pytest.raises(IntegrityError, match="^Scenario file is missing or is a symbolic link$"):
        parse_scenario_document(link)


def test_restore_check_rejects_a_persisted_activity_outside_the_frozen_scenario(
    engine_factory, caplog: pytest.LogCaptureFixture,
) -> None:
    """恢复复核：目标合法的持久活动通过；指向冻结场景外工作任务的持久活动以完整性错误拒绝并记下活动身份。"""
    engine = engine_factory()
    person, now, body = engine.context.state.agents[1], engine.context.state.current_time, engine.context.scenario.body
    _, waiting = start_activity(person, ActivityStartParams(
        activity_type=ActivityKind.WAIT, planned_duration_seconds=600), now, _origin("wait"), body)
    engine.context.state.activities[waiting.activity_id] = waiting
    assert validate_activity_targets(engine.context.state, engine.context.rules, engine.context.scenario) is None
    _, stray = start_activity(person, ActivityStartParams(
        activity_type=ActivityKind.WORK, planned_duration_seconds=600, target_id="task-outside-scenario"),
        now, _origin("work"), body)
    engine.context.state.activities[stray.activity_id] = stray
    with caplog.at_level(logging.ERROR), pytest.raises(IntegrityError, match="^Persisted activity target is invalid$"):
        validate_activity_targets(engine.context.state, engine.context.rules, engine.context.scenario)
    assert f"Persisted activity target is invalid: {stray.activity_id}" in caplog.messages


def test_app_admission_rejects_target_outside_the_frozen_catalog(engine_factory) -> None:
    """应用受理只认冻结目录：未知应用拒绝，目录内有真实内容的应用正常受理。"""
    engine = engine_factory()
    person = engine.context.state.agents[1]
    request = ActivityStartParams(
        activity_type=ActivityKind.USE_APP, planned_duration_seconds=600, target_id="app-outside-catalog")
    with pytest.raises(BusinessRuleError, match="^TARGET_NOT_FOUND$"):
        engine.context.rules.validate_start(person, request, engine.context.state.current_time, person.location_id)
    known = engine.context.semantics.catalog.apps["douyin"]
    assert engine.context.rules.app(known.app_id) == known
    request = request.model_copy(update={"target_id": known.app_id})
    assert engine.context.rules.validate_start(person, request, engine.context.state.current_time,
                                               person.location_id) == {"app_id": known.app_id}


def test_actual_food_activity_reopens_with_current_catalog_and_rejects_unknown_food(engine_factory) -> None:
    """真实食用活动经冻结目录恢复；篡改为目录外食品的持久请求在开放引擎时拒绝。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _, rows = submit_round(engine, {1: ("start_activity", named_start("食用", "持有物：薯片"))}, "food-restore")
    assert rows[0].status == "in_progress", rows[0].error_code
    identity = rows[0].activity_id
    activity = engine.context.state.activities[identity]
    assert activity.kind == ActivityKind.CONSUME
    assert activity.data["request"]["items"][0]["product_id"] in engine.context.rules.foods
    run_dir = engine.context.store.run_dir
    reopened = LifeEngine.open(ARTIFICIAL_ENVIRONMENT_ROOT / "scenarios/single-person.json", run_dir)
    try:
        assert reopened.context.state.activities[identity] == activity
    finally:
        reopened.close()

    def forge_food(state):
        payload = state.activities[identity].model_dump(mode="json")
        payload["target_id"] = "food-outside-catalog"
        payload["data"]["request"]["items"][0]["product_id"] = "food-outside-catalog"
        state.activities[identity] = type(activity).model_validate(payload)

    _stage(engine, forge_food)
    with pytest.raises(IntegrityError, match="^Persisted activity target is invalid$"):
        LifeEngine.open(ARTIFICIAL_ENVIRONMENT_ROOT / "scenarios/single-person.json", run_dir)
