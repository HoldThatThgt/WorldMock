"""双对象逐项动作在正式场景中的组合准入和共同结算验收。"""

from __future__ import annotations

import pytest

from tests.life.conftest import stage_day_plan
from tests.life.test_parallel_activities import _advance_with_decisions, _start_pair, _start_pair_with_choices
from worldmock.catalog.affordances import engine_affordances

#: 所选动作的内部标准动作 → 注册表活动类型（占用按类型声明，每种动作占自己的名字，docs/dev/SPEC-parallel-activities.md#spec-parallel-activities-占用声明）。
_KINDS = {"wait": "wait", "do": "lifestyle", "travel_to": "travel", "sleep": "sleep", "use_app": "use_app"}


def _registry_claims(engine, choice: tuple[dict, dict]) -> frozenset[str]:
    """@brief 所选动作按注册表重列占用：使用 app 只有 app 占用，出行按出行方式。"""
    row, action = choice
    view = engine_affordances()
    return view.resource_claims(_KINDS[action["canonical_action"]], travel_mode=action["travel_mode"])


@pytest.mark.parametrize("partner", ["wait", "do", "travel_to"])
def test_app_can_share_time_with_compatible_activity(engine_factory, partner: str) -> None:
    """使用 app（use_app）与等待（wait）、生活活动（lifestyle）、步行（travel）不是同一种动作，
    占用不相交，可同轮启动并保留独立身份。"""
    engine = engine_factory()
    stage_day_plan(engine)
    result, choices = _start_pair_with_choices(engine, "use_app", partner, f"app-{partner}")
    assert result.status == "in_progress", result.error_code
    activities = [item for item in engine.context.state.activities.values()
                  if item.origin_action_id == result.action_id]
    assert len(activities) == 2
    assert len({item.activity_id for item in activities}) == 2
    assert all(item.status == "active" for item in activities)
    assert len(result.data["activities"]) == 2
    app, other = (_registry_claims(engine, choice) for choice in choices)
    assert app == {"use_app"} and not app & other
    assert {frozenset(item.resource_claims) for item in activities} == {app, other}


@pytest.mark.parametrize(("partner", "overlap"), [pytest.param("sleep", {"use_app"}, id="sleep"),
                                                  pytest.param("use_app", {"use_app"}, id="use_app")])
def test_incompatible_pair_is_rejected_without_partial_state(engine_factory, partner: str, overlap: set[str]) -> None:
    """使用 app 与睡眠（全部占用）或另一段使用 app（同一种）的注册表占用相交：整组以 RESOURCE_CONFLICT 拒绝并写明原因与下一步，
    不留下首项活动；会话只剩运行开始的停留会话且未改动（第五章「并行准入」、第七章）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    before = engine.context.state.model_dump(mode="json")
    result, choices = _start_pair_with_choices(engine, "use_app", partner, f"app-{partner}-conflict")
    assert result.status == "rejected"
    assert result.error_code == "RESOURCE_CONFLICT"
    app, other = (_registry_claims(engine, choice) for choice in choices)
    assert app & other == overlap
    names = [f"{action['label']}{row['object'].partition('：')[2]}" for row, action in choices]
    assert result.data["reason"] == f"{names[0]} cannot run together with {names[1]}."
    assert result.data["next_step"] == "Choose only one of the conflicting activities."
    assert result.data["unmet"] == ["resource_claims"]
    assert engine.context.state.activities == {}
    sessions = list(engine.context.state.sessions.values())
    assert [(item.kind, item.status, item.activity_ids) for item in sessions] == [("stay", "active", [])]
    assert engine.context.state.model_dump(mode="json")["sessions"] == before["sessions"]
    assert engine.context.state.accounts == before["accounts"]


def test_parallel_progress_uses_one_shared_body_interval(engine_factory) -> None:
    """两个活动在同一时间段各写进度，身体只结算一笔。"""
    engine = engine_factory()
    stage_day_plan(engine)
    started = _start_pair(engine, "use_app", "wait", "shared-body")
    assert started.status == "in_progress", started.error_code
    before = engine.context.store.last_event_seq
    for index in range(40):
        _advance_with_decisions(engine, index)
        events = [item for item in engine.context.store.read_events(engine.context.store.last_event_seq)
                  if item.event_seq > before]
        progress = [item for item in events if item.event_type == "activity_progress"
                    and item.action_id == started.action_id]
        if len(progress) >= 2:
            break
    assert len(progress) >= 2
    body_ids = {item.data["body_settlement_event_id"] for item in progress[:2]}
    assert len(body_ids) == 1
