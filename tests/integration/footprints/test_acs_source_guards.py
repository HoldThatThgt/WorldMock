"""@brief 从真实登记动作推进取得足迹，验证内容来源和人物归属不可伪造。"""

from copy import deepcopy

import pytest

from tests.life.action_worlds import action_world, advance, begin
from worldmock.errors import IntegrityError
from worldmock.footprints.app_usage import project_app_usage, validate_app_event_source
from worldmock.footprints.models import Event


def _actual_progress():
    """@brief 通过真实生产加载、动作初始化与正秒结算构造当前合同的持久事件。"""
    semantics, state = action_world()
    state, activity = begin(semantics, state)
    changed, content = advance(semantics, state, activity, 60)
    after = changed.activities[activity.activity_id]
    event = Event(event_type="activity_progress", run_id=state.run_id, event_seq=1,
        agent_id=activity.agent_id, activity_id=activity.activity_id, occurred_at=changed.current_time,
        recorded_at="2026-01-05T00:01:00+00:00", wall_elapsed_ms=1,
        state_version_before=state.version, state_version_after=state.version + 1,
        data={"activity": after.model_dump(mode="json"), "content": content,
              "interval_start": activity.last_settled_at, "interval_end": after.last_settled_at})
    return semantics, changed, event.model_dump(mode="json")


@pytest.mark.parametrize("fault,reason", [
    ("retired_kind", "Activity snapshot uses a retired activity kind"),
    ("different_owner", "Activity snapshot owner disagrees with its source event"),
    ("different_app", "Content consumption app disagrees with its actual action"),
])
def test_actual_content_evidence_rejects_retired_kind_wrong_owner_and_wrong_app(fault, reason):
    """@brief 原快照可导出真实消费；改变种类、人物或应用即拒绝，且不修改输入足迹或世界。"""
    semantics, state, event = _actual_progress()
    assert validate_app_event_source(event) is None
    baseline = project_app_usage([event])
    consumed = next(row for row in baseline if row["fact_kind"] == "content_consumed")
    assert consumed["interval_end"] - consumed["interval_start"] == 60
    assert consumed["app_id"] == event["data"]["activity"]["data"]["app_id"]
    assert consumed["source_event_id"] == event["event_id"]
    corrupted = deepcopy(event)
    if fault == "retired_kind":
        corrupted["data"]["activity"]["kind"] = "use_app"
    elif fault == "different_owner":
        corrupted["data"]["activity"]["agent_id"] = 2
    else:
        different = next(app for app in semantics.catalog.apps if app != consumed["app_id"])
        corrupted["data"]["content"]["app_id"] = different
    before, original = state.model_dump_json(), deepcopy(event)
    corrupted_before = deepcopy(corrupted)
    with pytest.raises(IntegrityError, match=reason):
        validate_app_event_source(corrupted)
    with pytest.raises(IntegrityError, match=reason):
        project_app_usage([corrupted])
    assert event == original and corrupted == corrupted_before
    assert state.model_dump_json() == before and project_app_usage([event]) == baseline
