"""所选完整环境跨目录恢复后仍使用原文字节与实际地图。"""

import shutil
from datetime import timedelta

from tests.frozen_inputs import fixture_metadata
from tests.life.conftest import advance_next, stage_day_plan, use_app
from tests.life.environment_runtime import act
from tests.system.lifecycle.test_external_environment_lifecycle import _complete_run, _snapshot_current
from worldmock.environment import load_environment
from worldmock.footprints.export import export_run
from worldmock.footprints.validation import validate_export
from worldmock.life.engine import LifeEngine


def test_selected_environment_runs_relocates_and_exports_its_actual_input(engine_factory, tmp_path):
    """显式选择的完整输入和真实活动在移动目录后通过原始摘要与导出认证。"""
    def one_hour(persona, config):
        config["stop_time"] = config["start_time"] + timedelta(hours=1)

    engine = engine_factory(mutate=one_hour)
    stage_day_plan(engine)
    original_inputs = engine.context.inputs
    original_identity = engine.context.store.manifest["environment_identity"]
    _, started = act(engine, "selected-start", use_app("美团"))
    assert started.status == "in_progress"
    advance_next(engine)
    _snapshot_current(engine, tmp_path / "partial")
    saved = engine.context.state.model_dump(mode="json")
    source_run = engine.context.store.run_dir
    engine.close()
    moved = tmp_path / "moved"
    moved.mkdir()
    shutil.copytree(original_inputs.root, moved / "environment")
    shutil.move(source_run, moved / "run")
    inputs = load_environment(moved / "environment", metadata_loader=fixture_metadata)
    reopened = LifeEngine.open(inputs, moved / "run")
    try:
        assert reopened.context.state.model_dump(mode="json") == saved
        assert reopened.context.store.manifest["environment_identity"] == original_identity
        assert inputs.persona_sha256 == original_inputs.persona_sha256
        assert inputs.config_sha256 == original_inputs.config_sha256
        checkpoint = _complete_run(reopened, moved / "completed")
        export_run(reopened.context.store, checkpoint.last_event_seq, moved / "export")
        manifest, _, activities = validate_export(moved / "export")
        assert manifest["experiment_completed"] is True
        assert any(row["activity_id"] == started.activity_id for row in activities)
    finally:
        reopened.close()
