"""个人事务只由本人实际收到的商品完成，并可从导出证据重算。"""

import json

from tests.footprints.recording import short_window
from tests.life.conftest import advance_next, conclude_request, errand_add, start_activity
from tests.life.environment_runtime import act, finish_activity, run_to_end
from worldmock.footprints.export import export_run
from worldmock.footprints.models import Event
from worldmock.footprints.quality import build_quality_report
from worldmock.footprints.validation import validate_export


def test_errand_completion_recertifies_actual_received_goods(engine_factory, tmp_path):
    def setup(persona, config):
        short_window(persona, config)
        config["progress_report_interval_seconds"] = 60
    engine = engine_factory(mutate=setup)
    _, browse = act(engine, "note-browse", start_activity("interact", "美团"))
    while not engine.scheduling_state().ready_agent_ids:
        advance_next(engine)
    activity = engine.context.state.activities[browse.activity_id]
    _, noted = act(engine, "note", conclude_request(activity.name, errands=[errand_add("买纸巾", "纸巾")]),
                   "conclude_activity")
    assert noted.status == "completed"
    _, bought = act(engine, "buy", start_activity("purchase", "纸巾"))
    assert engine.context.state.agents[1].errands
    finish_activity(engine, bought, "buy")
    assert engine.context.state.agents[1].errands == {}
    run_to_end(engine)
    store = engine.context.store
    point = engine.checkpoint({"agent:1": store.assets.put_json({"saved": True}, {"kind": "snapshot"})})
    destination = tmp_path / "export"
    export_run(store, point.last_event_seq, destination)
    manifest, rows, activities = validate_export(destination)
    quality = json.loads((destination / "quality.json").read_bytes())
    assert quality == build_quality_report([Event.model_validate(row) for row in rows], activities,
                                          manifest["frozen_run_manifest"])
    assert quality["errand_metrics"]["registered_count"] == quality["errand_metrics"]["completed_count"] == 1
    assert quality["errand_metrics"]["completed_without_evidence_count"] == 0
    assert not any(row["event_type"] == "memory_updated" for row in rows)
    completed = next(row for row in rows if row["data"].get("kind") == "errand_completed")
    source = next(row for row in rows if row["event_id"] == completed["caused_by"][0])
    assert source["data"]["kind"] == "goods_received" and source["data"]["goods_id"] == "tissue"
