"""流式导出：分页读一遍截止前缀写出的每个字节，与按整段完整事件、照以前的做法算出的参照逐字节相同。"""

import hashlib
import os
from datetime import date

import pytest

from tests.footprints.recording import errand_engine, recorded_engine
from tests.footprints.statement_log import StatementLog
from tests.life.conftest import action_identity
from worldmock.errors import IntegrityError
from worldmock.footprints import timeline_labels
from worldmock.footprints.app_usage import project_app_usage
from worldmock.footprints.export import _verified_experience_rows, export_run, reconstruct_activities
from worldmock.footprints.frozen_environment import frozen_display_names
from worldmock.footprints.location_stays import project_location_stays
from worldmock.footprints.models import canonical_json
from worldmock.footprints.purchases import project_purchase_facts
from worldmock.footprints.quality import build_quality_report
from worldmock.footprints.timeline_labels import timeline_sources

#: 两个夹具运行：购买加内容使用，以及个人事务由真实收货完成。
RUNS = {"recorded": recorded_engine, "errand": errand_engine}


@pytest.fixture(params=["recorded", "errand"])
def fixture_run(request, engine_factory):
    """运行到截止并写了最终检查点的夹具运行（引擎，最终检查点）。"""
    return RUNS[request.param](engine_factory)


def _jsonl(rows):
    """规范 JSONL 字节与 write_jsonl 给出的工件身份。"""
    raw = b"".join(canonical_json(row) + b"\n" for row in rows)
    return raw, {"rows": len(rows), "sha256": hashlib.sha256(raw).hexdigest()}


def _files(directory):
    """导出目录里每个文件的相对路径与字节。"""
    return {path.relative_to(directory).as_posix(): path.read_bytes()
            for path in sorted(directory.rglob("*")) if path.is_file()}


def test_streamed_export_equals_the_full_event_reference(fixture_run, tmp_path):
    engine, point = fixture_run
    store, cutoff = engine.context.store, point.last_event_seq
    manifest = export_run(store, cutoff, tmp_path / "export")
    full = list(store.iter_events(cutoff))
    rows = [event.model_dump() for event in full]
    activities = reconstruct_activities(full)
    names = frozen_display_names(store.run_dir, store.manifest)
    expected = {
        "events": ("events.jsonl", rows),
        "activities": ("activities.jsonl", activities),
        "verified_experiences": ("verified-experiences.jsonl",
                                 _verified_experience_rows(store.manifest["agent_ids"], full)),
        "purchases": ("purchases.jsonl", project_purchase_facts(rows)),
        "app_usage": ("app-usage.jsonl", project_app_usage(rows)),
        "location_stays": ("location-stays.jsonl", project_location_stays(rows)),
    }
    for name, (path, records) in expected.items():
        raw, identity = _jsonl(records)
        assert (tmp_path / "export" / path).read_bytes() == raw, name
        assert manifest["artifacts"][name] == {"path": path, **identity}, name
    quality = canonical_json(build_quality_report(full, activities, store.manifest, names))
    assert (tmp_path / "export" / "quality.json").read_bytes() == quality
    renderer = timeline_labels._load_render_day()
    assert manifest["timeline"]
    for record in manifest["timeline"]:
        payload = renderer.render_day_markdown(store.connection, record["agent_id"], date.fromisoformat(record["date"]),
                                               store.manifest["window"]["timezone"], names).encode("utf-8")
        assert (tmp_path / "export" / record["path"]).read_bytes() == payload
        assert record["sha256"] == hashlib.sha256(payload).hexdigest()


def test_mid_run_cutoff_timeline_stops_at_the_cutoff(engine_factory, tmp_path):
    engine, _ = recorded_engine(engine_factory)
    store = engine.context.store
    content = action_identity("recorded-content")
    cutoff = next(event.event_seq for event in store.iter_events(store.last_event_seq)
                  if event.event_type == "action_requested" and event.action_id == content) - 1
    manifest = export_run(store, cutoff, tmp_path / "export")
    prefix = list(store.iter_events(cutoff))
    names = frozen_display_names(store.run_dir, store.manifest)
    zone = store.manifest["window"]["timezone"]
    renderer = timeline_labels._load_render_day()
    (record,) = manifest["timeline"]
    day = date.fromisoformat(record["date"])
    progress, orders = timeline_sources(prefix)
    agent_progress = [body for body in progress if body.get("agent_id") == 1]
    rows = renderer.day_rows(agent_progress, orders, names, day, zone)
    written = (tmp_path / "export" / record["path"]).read_text(encoding="utf-8")
    assert written == renderer.day_markdown(1, day, rows)
    assert rows and not [row for row in rows if "哔哩哔哩" in row["label"]]
    whole = renderer.render_day_markdown(store.connection, 1, day, zone, names)
    assert "哔哩哔哩" in whole and whole != written
    quality = canonical_json(build_quality_report(prefix, reconstruct_activities(prefix), store.manifest, names))
    assert (tmp_path / "export" / "quality.json").read_bytes() == quality


def test_export_rejects_a_cutoff_outside_the_sequence_before_creating_anything(engine_factory, tmp_path):
    engine, _ = recorded_engine(engine_factory)
    store = engine.context.store
    cases = [(-1, "^Event cutoff is outside the committed sequence$"),
             (store.last_event_seq + 1, "^Event cutoff is outside the committed sequence$"),
             (0, "^Cannot export an empty event range$")]
    for cutoff, message in cases:
        with pytest.raises(IntegrityError, match=message):
            export_run(store, cutoff, tmp_path / "export")
        assert not (tmp_path / "export").exists()
        assert not [item for item in os.listdir(tmp_path) if item.startswith(".export-")]


def test_export_reads_the_store_one_page_at_a_time(engine_factory, tmp_path, monkeypatch):
    engine, point = recorded_engine(engine_factory)
    store, cutoff = engine.context.store, point.last_event_seq
    export_run(store, cutoff, tmp_path / "default")
    monkeypatch.setattr("worldmock.footprints.store.EVENT_PAGE_SIZE", 3)
    log = StatementLog(store.connection)
    monkeypatch.setattr(store, "connection", log)
    export_run(store, cutoff, tmp_path / "paged")
    page = "SELECT event_seq,event_id,body FROM events WHERE event_seq>?"
    windows = [parameters for sql, parameters in log.statements if sql.startswith(page)]
    assert windows and all(0 < stop - after <= 3 for after, stop in windows)
    assert [after for after, _ in windows] == [0, *(stop for _, stop in windows[:-1])]
    assert windows[-1][1] == cutoff
    for sql, _ in log.statements:
        compact = " ".join(sql.split())
        if "body" in compact and "FROM events" in compact:
            assert any(bound in compact for bound in ("event_seq>?", "event_seq=?", "event_id=?")), compact
    assert _files(tmp_path / "paged") == _files(tmp_path / "default")
