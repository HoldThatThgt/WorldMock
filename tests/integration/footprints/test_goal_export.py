"""当日目标只保存人物计划，不由内容关键词制造完成事实。"""

import json

from tests.footprints.recording import recorded_engine
from worldmock.footprints.export import export_run
from worldmock.footprints.validation import validate_export
from worldmock.cli import main


def test_actual_run_never_exports_automatic_goal_completion(engine_factory, tmp_path):
    engine, point = recorded_engine(engine_factory)
    destination = tmp_path / "export"
    export_run(engine.context.store, point.last_event_seq, destination)
    _, events, _ = validate_export(destination)
    assert not any(row["data"].get("kind") == "goal_progressed" for row in events)
    quality = json.loads((destination / "quality.json").read_bytes())
    for group in quality["day_plan_metrics"].values():
        assert set(group) == {"external_constraints", "gates"}
    assert b"progressed_at" not in (destination / "events.jsonl").read_bytes()


def test_export_command_preserves_actual_cutoff_and_frozen_identity(engine_factory, tmp_path):
    engine, point = recorded_engine(engine_factory)
    destination = tmp_path / "cli-export"
    assert main(["export", "--run-dir", str(engine.context.store.run_dir), "--cutoff", str(point.last_event_seq),
                 "--output", str(destination)]) == 0
    manifest, _, _ = validate_export(destination)
    assert manifest["cutoff_event_seq"] == point.last_event_seq
    assert manifest["frozen_run_manifest"] == engine.context.store.manifest
