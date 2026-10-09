"""运行检查工具直接读取真实当前活动足迹，不依赖已删除的目录动作类别。"""

import argparse

import pytest

from scripts import inspect_run
from tests.life.conftest import advance_next, start_activity
from tests.life.environment_runtime import act, events


@pytest.mark.parametrize("action,target,kind,has_content", [
    pytest.param("interact", "哔哩哔哩", "interact", True, id="current-content"),
    pytest.param("interact", "美团", "interact", False, id="plain-browse"),
    pytest.param("sleep", "家", "sleep", False, id="non-content"),
])
def test_inspector_activity_summary_uses_actual_current_content_title(
    engine_factory, capsys, action, target, kind, has_content,
):
    """真实引擎结算后，摘要显示当前内容标题；纯浏览与睡眠不会添加内容标题。"""
    engine = engine_factory()
    _, result = act(engine, "inspect-activity", start_activity(action, target))
    assert result.status == "in_progress", result.error_code
    advance_next(engine)
    progress = [row for row in events(engine, "activity_progress")
                if row.data["activity"]["activity_id"] == result.activity_id]
    assert progress
    snapshot = progress[-1].data["activity"]
    assert snapshot["kind"] == kind
    assert "title" not in snapshot
    title = snapshot.get("content_title")
    assert bool(title) is has_content
    expected = f"{snapshot['name']}{f'《{title}》' if title else ''} → {snapshot['status']}"
    assert inspect_run._brief(progress[-1].model_dump(mode="json")) == expected
    store = engine.context.store
    point = store.last_event_seq
    with inspect_run._connect(store.run_dir) as conn:
        inspect_run.cmd_events(conn, argparse.Namespace(type="activity_progress", agent=1, tail=1, full=False))
    output = capsys.readouterr().out
    assert expected in output
    assert ("《" in output) is has_content
    assert store.last_event_seq == point
