"""外部完整环境的内容隔离、关闭恢复与认证导出。"""

import json
import shutil

import pytest

from tests.data_paths import ARTIFICIAL_ENVIRONMENT_ROOT
from tests.integration.runtime_fixtures import write_agent_workspace
from tests.life.conftest import advance_next, stage_day_plan, start_activity
from tests.life.environment_runtime import act, finish_activity, run_to_end
from worldmock.catalog.identity import refresh_manifest_identity
from worldmock.footprints.checkpoint import snapshot_directory
from worldmock.footprints.export import export_run
from worldmock.footprints.validation import validate_export
from worldmock.life.engine import LifeEngine


def _snapshot_current(engine, root):
    """以真实人物工作区保存共同检查点。"""
    workspace = write_agent_workspace(root / "agent")
    return engine.checkpoint({"agent:1": snapshot_directory(workspace, engine.context.store.assets)})


def _complete_run(engine, root):
    """真实结算到声明截止，然后保存共同检查点。"""
    run_to_end(engine)
    return _snapshot_current(engine, root)


def _content_environment(root, title, duration):
    """只修改外部目录中的内容正文与绑定，重新计算真实目录身份。"""
    shutil.copytree(ARTIFICIAL_ENVIRONMENT_ROOT, root)
    path = root / "catalog/content-catalog.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        if row["content_id"] == "c-article":
            row.update(title=title, summary=f"{title}的测试用合成摘要。", duration_seconds=duration)
        elif "bilibili" in row.get("app_ids", []):
            row["app_ids"] = ["douyin"]
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                    encoding="utf-8", newline="\n")
    refresh_manifest_identity(root / "catalog")
    return root


@pytest.mark.parametrize("title,duration", [("短暂休息", 600), ("长时间休息", 1200)])
def test_external_content_survives_real_start_reopen_and_authenticated_export(
        engine_factory, tmp_path, title, duration):
    """环境内容原值穿过实际消费、关闭恢复、完整运行和认证导出。"""
    root = _content_environment(tmp_path / "environment", title, duration)
    engine = engine_factory(root)
    stage_day_plan(engine)
    _, started = act(engine, "content-start", start_activity("interact", "哔哩哔哩"))
    assert started.status == "in_progress"
    activity = engine.context.state.activities[started.activity_id]
    assert activity.kind == "interact" and activity.data["content_id"] == "c-article"
    assert activity.planned_duration_seconds == duration
    advance_next(engine)
    saved = engine.context.state.model_dump(mode="json")
    run = engine.context.store.run_dir
    inputs = engine.context.inputs
    engine.close()
    reopened = LifeEngine.open(inputs, run)
    try:
        assert reopened.context.state.model_dump(mode="json") == saved
        assert reopened.context.catalog.contents["c-article"].title == title
        finish_activity(reopened, started, "content")
        assert reopened.context.state.agents[1].content_progress["c-article"].completed_rounds == 1
        checkpoint = _complete_run(reopened, tmp_path / "completed")
        export_run(reopened.context.store, checkpoint.last_event_seq, tmp_path / "export")
        manifest, events, activities = validate_export(tmp_path / "export")
        assert manifest["experiment_completed"] is True
        exported = next(row for row in activities if row["activity_id"] == started.activity_id)
        assert exported["status"] == "completed" and exported["data"]["content_id"] == "c-article"
        assert any(row["event_type"] == "activity_progress" and row["activity_id"] == started.activity_id
                   and row["data"]["content"]["completed"] for row in events)
    finally:
        reopened.close()


def test_external_environments_keep_content_and_persona_independent(engine_factory, tmp_path):
    """同时加载两个完整环境，不共享目录内容或人物可变状态。"""
    first = engine_factory(_content_environment(tmp_path / "first", "短暂休息", 600))
    second = engine_factory(_content_environment(tmp_path / "second", "长时间休息", 1200))
    assert first.context.catalog.contents["c-article"].title == "短暂休息"
    assert second.context.catalog.contents["c-article"].title == "长时间休息"
    first.context.state.agents[1].body.relaxation_need = 0.9
    assert second.context.state.agents[1].body.relaxation_need == 0.2
    assert first.context.store.manifest["environment_identity"] != second.context.store.manifest["environment_identity"]
