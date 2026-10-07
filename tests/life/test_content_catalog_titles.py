"""@brief 已登记内容的标题直接投影；场景旧结构仍由严格模型拒绝。"""

import pytest
from pydantic import ValidationError

from tests.life.purchase_worlds import registered_world
from worldmock.life.content import _content_snapshot, app_content_result, load_content_titles, settle_app_usage
from worldmock.life.models import ActivityState
from worldmock.life.scenario import LifeScenario, RuntimeScenario


def test_content_titles_project_actual_registered_environment():
    """@brief 生产标题投影保留真实登记条目的身份和标题，不再读取字幕或旧内容池。"""
    semantics, _ = registered_world()
    assert load_content_titles(semantics.scenario.contents) == {
        identity: item.title for identity, item in semantics.catalog.contents.items()}
    assert load_content_titles(semantics.scenario.contents)["c-long"] == "测试长视频"


@pytest.mark.parametrize("model", [LifeScenario, RuntimeScenario])
def test_old_scenario_schema_is_rejected(model):
    """@brief 已退役场景结构在严格模型边界明确拒绝。"""
    with pytest.raises(ValidationError) as error:
        model.model_validate({"schema_version": 5})
    assert any(item["loc"] == ("schema_version",) and item["type"] == "literal_error"
               for item in error.value.errors())


def _app_activity(state, duration):
    """@brief 直接内容投影使用当前仍存续的 app 活动形状，不创建引擎。"""
    return ActivityState(
        activity_id="content-projection", agent_id=1, location_id="home", kind="use_app", status="active",
        started_at=state.current_time, planned_end_at=state.current_time + duration,
        last_settled_at=state.current_time, elapsed_seconds=0, planned_duration_seconds=duration,
        target_id="douyin", origin_action_id="open-content", origin_decision_id="decision",
        origin_request_event_id="request", data={"draw_index": 0, "played": []})


def test_content_snapshot_and_result_keep_content_facts_without_classification():
    """@brief 内容快照与公开结果保留身份、摘要、进度及推送状态，不增加分类字段。"""
    semantics, state = registered_world()
    item = semantics.catalog.contents["c-short"]
    snapshot = _content_snapshot(state.agents[1], _app_activity(state, item.duration_seconds), item, None)
    assert snapshot.data == {
        "draw_index": 0, "played": [], "app_id": "douyin", "content_id": "c-short", "summary": item.summary,
        "start_position_seconds": 0, "pinned_by_push_id": None, "repeat": False,
    }
    assert app_content_result(snapshot, semantics.catalog) == {
        "app": "抖音", "title": item.title, "summary": item.summary, "pushed": False,
    }
    assert snapshot.next_interaction_at == state.current_time + item.duration_seconds


def test_completed_content_evidence_and_played_rows_keep_positions_without_classification():
    """@brief 实际完成的内容保留消费区间、轮数与位置事实，消费证据和已播列表不再带分类。"""
    semantics, state = registered_world()
    item = semantics.catalog.contents["c-short"]
    activity = _content_snapshot(state.agents[1], _app_activity(state, item.duration_seconds), item, None)
    activity = activity.model_copy(update={"last_settled_at": activity.planned_end_at,
                                           "media_position_seconds": item.duration_seconds,
                                           "elapsed_seconds": item.duration_seconds})
    state.current_time = activity.planned_end_at
    state.agents[1].body_settled_at = state.current_time
    changed, evidence = settle_app_usage(state, activity, semantics.catalog, 0)
    assert evidence == {
        "content_id": "c-short", "app_id": "douyin", "position_before_seconds": 0,
        "position_after_seconds": item.duration_seconds, "completed": True, "completed_rounds_after": 1,
    }
    assert changed.activities[activity.activity_id].data["played"] == [{
        "content_id": "c-short", "from_position": 0, "to_position": item.duration_seconds,
    }]
    assert changed.agents[1].content_progress["c-short"].completed_rounds == 1
