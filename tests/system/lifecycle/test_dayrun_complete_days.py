"""冻结环境跨完整自然日推进，所有实际决策与计划正常结算。"""
from datetime import timedelta
import pytest
from tests.life.conftest import stage_day_plan
from tests.life.environment_runtime import run_to_end
from worldmock.footprints.export import export_run
from worldmock.footprints.validation import validate_export
from tests.integration.runtime_fixtures import write_agent_workspace
from worldmock.footprints.checkpoint import snapshot_directory

@pytest.mark.parametrize("days", [1, 2])
def test_complete_days_reach_declared_stop_without_failure(engine_factory, tmp_path, days):
    def extend(persona, config):
        config["stop_time"] = config["start_time"] + timedelta(days=days)
    engine = engine_factory(mutate=extend)
    stage_day_plan(engine)
    recorded = run_to_end(engine)
    assert engine.context.state.current_time == engine.context.state.end_time
    assert any(row.event_type == "run_ended" for row in recorded)
    assert not any(row.event_type == "run_failed" for row in recorded)
    workspace = write_agent_workspace(tmp_path / "agent")
    engine.checkpoint({"agent:1": snapshot_directory(workspace, engine.context.store.assets)})
    export_run(engine.context.store, engine.context.store.last_event_seq, tmp_path / "export")
    manifest, exported_events, activities = validate_export(tmp_path / "export")
    assert manifest["experiment_completed"] is True and exported_events and activities
