"""真实事件截止、终点和共同检查点决定导出是否完成。"""

import hashlib
import json

import pytest

from tests.footprints.recording import recorded_engine
from worldmock.errors import IntegrityError
from worldmock.footprints.export import export_run
from worldmock.footprints.models import EventDraft
from worldmock.footprints.validation import validate_export


def test_final_checkpoint_completes_only_the_full_committed_cutoff(engine_factory, tmp_path):
    engine, point = recorded_engine(engine_factory)
    store = engine.context.store
    partial = export_run(store, point.last_event_seq - 1, tmp_path / "partial")
    complete = export_run(store, point.last_event_seq, tmp_path / "complete")
    assert partial["experiment_completed"] is False and complete["experiment_completed"] is True
    with pytest.raises(IntegrityError, match="completed experiment"):
        validate_export(tmp_path / "partial")
    assert validate_export(tmp_path / "complete")[0] == complete
    store.commit(store.state, [EventDraft(event_type="observation", occurred_at=store.state["current_time"])])
    late = export_run(store, store.last_event_seq, tmp_path / "late")
    assert late["experiment_completed"] is False


def test_corrupt_source_asset_never_publishes_export(engine_factory, tmp_path):
    engine, point = recorded_engine(engine_factory)
    store = engine.context.store
    ref = store.manifest["environment_assets"]["persona.toml"]
    (store.assets.root / ref["sha256"]).write_bytes(b"broken")
    output = tmp_path / "export"
    with pytest.raises(IntegrityError):
        export_run(store, point.last_event_seq, output)
    assert not output.exists()


def test_timeline_and_quality_are_hash_bound_projections_of_actual_run(engine_factory, tmp_path):
    engine, point = recorded_engine(engine_factory)
    directory = tmp_path / "export"
    manifest = export_run(engine.context.store, point.last_event_seq, directory)
    quality = json.loads((directory / "quality.json").read_bytes())
    assert quality["activities"]["person_elapsed_seconds_by_agent"]["1"] == 3600
    assert manifest["quality_report"]["sha256"] == hashlib.sha256((directory / "quality.json").read_bytes()).hexdigest()
    for descriptor in manifest["timeline"]:
        content = (directory / descriptor["path"]).read_bytes()
        assert len(content) == descriptor["size_bytes"] and hashlib.sha256(content).hexdigest() == descriptor["sha256"]
        assert any(entry.title in content.decode("utf-8") for entry in engine.context.catalog.contents.values())
    verified = [json.loads(line) for line in (directory / "verified-experiences.jsonl").read_bytes().splitlines()]
    assert {row["kind"] for row in verified} >= {"received", "played"}
    assert all(row["kind"] != "waited" for row in verified)
