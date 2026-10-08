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
from tests.integration.life.test_acs_amended_runtime import perform
from tests.integration.life.test_acs_runtime import request
from worldmock.footprints.models import EventDraft

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


def test_restore_check_rejects_a_persisted_action_outside_the_frozen_catalog(engine_factory, caplog):
    """@brief 真实动作快照通过，未知动作身份失败并写实际活动身份到错误日志。"""
    engine = engine_factory()
    started = perform(engine, "action", request())
    assert started.error_code is None
    assert validate_activity_targets(engine.context.state, engine.context.rules, engine.context.scenario) is None
    actual = engine.context.state.activities[started.activity_id]
    stray = actual.model_copy(update={"data": {**actual.data, "action_id": "outside-action"}}, deep=True)
    changed = engine.context.state.model_copy(deep=True)
    changed.activities[actual.activity_id] = stray
    with caplog.at_level(logging.ERROR), pytest.raises(IntegrityError, match="^Persisted activity target is invalid$"):
        validate_activity_targets(changed, engine.context.rules, engine.context.scenario)
    assert f"Persisted activity target is invalid: {stray.activity_id}" in caplog.messages


@pytest.mark.parametrize("target,app,payment,code", [
    ("刷推荐", "不存在应用", None, "APP_NOT_OFFERED"),
    ("牛肉面", "美团", "不存在付款应用", "PAYMENT_APP_NOT_BOUND"),
])
def test_app_admission_rejects_target_outside_the_frozen_catalog(engine_factory, target, app, payment, code):
    """@brief 明确选择未知应用拒绝；登记应用不从其他动作借内容。"""
    engine = engine_factory()
    with pytest.raises(BusinessRuleError, match="^TARGET_NOT_FOUND$"):
        engine.context.rules.app("app-outside-catalog")
    known = engine.context.semantics.catalog.apps["douyin"]
    assert engine.context.rules.app(known.app_id) == known
    accounts = dict(engine.context.state.accounts)
    result = perform(engine, "unknown-app", request(target, app, "购买" if payment else "进行", payment=payment))
    assert result.error_code == code
    assert result.data["items"][0]["allowed_values"]
    assert result.data["items"][0]["candidates"]
    assert engine.context.state.activities == {} and engine.context.state.agents[1].content_progress == {}
    assert engine.context.state.accounts == accounts and engine.context.state.deliveries == {}


@pytest.mark.parametrize("field,value", [("action_id", "outside-action"), ("content_id", "outside-content"),
    ("app_id", "outside-app"), ("app_id", "wechat")])
def test_actual_action_reopens_and_frozen_identity_corruption_is_rejected(engine_factory, field, value):
    """@brief 真正运行快照可重开；损坏动作、内容或应用绑定均被同一冻结目录拒绝。"""
    engine = engine_factory()
    started = perform(engine, "action-restore", request())
    assert started.error_code is None
    actual = engine.context.state.activities[started.activity_id]
    assert actual.kind == ActivityKind.ACTION and len(actual.data) == 11
    directory = engine.context.semantics.catalog.source_dir
    path = directory.parent / "scenarios/single-person.json"
    run_dir = engine.context.store.run_dir
    reopened = LifeEngine.open(path, run_dir)
    try:
        assert reopened.context.state.activities[actual.activity_id] == actual
    finally:
        reopened.close()
    changed = engine.context.state.model_copy(deep=True)
    changed.activities[actual.activity_id] = actual.model_copy(update={"data": {**actual.data, field: value}}, deep=True)
    changed.version += 1
    engine.context.store.commit(changed.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=changed.current_time, visibility="system",
        data={"kind": "test_frozen_action_corrupted"})])
    with pytest.raises(IntegrityError, match="^Persisted activity target is invalid$"):
        LifeEngine.open(path, run_dir)
