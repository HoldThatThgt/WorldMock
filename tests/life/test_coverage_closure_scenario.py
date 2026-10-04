"""覆盖闭合：场景文档与运行视图的手机模拟声明配对、场景文件符号链接、持久活动目标与内容查找的失败关闭分支。"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from pydantic import ValidationError

from worldmock.errors import BusinessRuleError, IntegrityError
from worldmock.life.activities import ActivityOrigin, start_activity
from worldmock.life.activity_validation import validate_activity_targets
from worldmock.life.models import ActivityKind, ActivityStartParams
from worldmock.life.scenario import RuntimeScenario, parse_scenario_document
from worldmock.life.world_setup import load_scenario

_FORMAL = Path("scenarios/white-collar-12-weeks.json")
_PHONE_PAIR = "phone simulation config path and hash must be declared together"
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
    """真实加载器读到只声明一半手机模拟配置的场景文档时以校验失败拒绝，原因指明两项必须同时声明。"""
    payload = json.loads(_FORMAL.read_text(encoding="utf-8"))
    assert "phone_simulation_config" not in payload
    payload.update(half)
    document = tmp_path / "half-phone.json"
    document.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(IntegrityError, match="^Scenario validation failed$") as rejected:
        parse_scenario_document(document)
    assert isinstance(rejected.value.__cause__, ValidationError)
    assert _PHONE_PAIR in str(rejected.value.__cause__)


@pytest.mark.parametrize("half", _HALF_PHONE_DECLARATIONS, ids=_HALF_IDS)
def test_runtime_view_rejects_a_half_declared_phone_simulation_config(half: dict) -> None:
    """运行视图独立复核同一配对：由正式场景导出的运行视图只补一半声明也不能重建。"""
    scenario, _ = load_scenario(_FORMAL)
    runtime = scenario.model_dump(mode="json")
    runtime.update(half)
    with pytest.raises(ValidationError, match=_PHONE_PAIR):
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


def test_watch_admission_rejects_content_outside_the_frozen_scenario(engine_factory) -> None:
    """观看受理只认冻结场景内容：未知内容以 CONTENT_NOT_FOUND 业务拒绝，场景内内容照常解析。"""
    engine = engine_factory()
    person = engine.context.state.agents[1]
    request = ActivityStartParams(
        activity_type=ActivityKind.WATCH, planned_duration_seconds=600, target_id="content-outside-scenario")
    with pytest.raises(BusinessRuleError, match="^CONTENT_NOT_FOUND$"):
        engine.context.rules.validate_start(person, request, engine.context.state.current_time, person.location_id)
    known = engine.context.scenario.contents[0]
    assert engine.context.rules.content(known.item_id) == known
