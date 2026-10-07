"""@brief 内容没有分类，统一执行结果只投影真实登记标题与摘要。"""

from tests.life.action_worlds import action_world, advance, begin, pin
from worldmock.life.action_run import action_content_result
from worldmock.life.content import load_content_titles


def test_registered_titles_are_complete_and_do_not_define_content_kinds():
    semantics, _ = action_world()
    items = tuple(semantics.catalog.contents.values())
    assert load_content_titles(items) == {item.content_id: item.title for item in items}
    assert all("kind" not in type(item).model_fields for item in items)


def test_content_snapshot_and_result_carry_real_title_summary_and_action_identity():
    semantics, state = action_world()
    pin(state)
    state, activity = begin(semantics, state)
    item = semantics.catalog.contents["c-short"]
    result = action_content_result(activity, semantics.catalog)
    assert result == {"action": "刷推荐", "app": "抖音", "title": item.title,
                      "summary": item.summary, "duration": "约 2 分钟", "pushed": True}
    assert "repeat" not in activity.data and "kind" not in activity.data and "summary" not in activity.data


def test_completed_content_evidence_and_played_rows_keep_positions_without_classification():
    semantics, state = action_world()
    pin(state)
    state, activity = begin(semantics, state)
    changed, evidence = advance(semantics, state, activity, 120)
    assert evidence["completed"] is True and evidence["completed_rounds_after"] == 1
    assert "kind" not in evidence
    assert changed.activities[activity.activity_id].data["played"] == [
        {"content_id": "c-short", "from_position": 0, "to_position": 120}]
