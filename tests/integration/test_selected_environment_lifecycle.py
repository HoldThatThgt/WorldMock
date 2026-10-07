"""@brief 显式选定环境的真实装配、跨目录恢复和认证导出，不调用模型。"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from tests.data_paths import ENVIRONMENT_ROOT
from tests.integration.test_external_environment_lifecycle import _complete_run, _identity, _snapshot_current
from tests.life.conftest import advance_next, start_activity, submit_round
from worldmock.footprints.export import export_run
from worldmock.footprints.validation import validate_export
from worldmock.life.engine import LifeEngine


def _selected_hour(root: Path) -> tuple[Path, dict]:
    """@brief 保留所选人物与目录原声明，只在隔离副本筛选一小时窗口的原生定时输入。"""
    shutil.copytree(ENVIRONMENT_ROOT, root)
    source = ENVIRONMENT_ROOT / "scenarios/single-person.json"
    original = source.read_bytes()
    document = json.loads(original)
    original_window = {name: document[name] for name in ("start_at", "end_at", "timezone")}
    start = datetime.fromisoformat(document["start_at"])
    end = min(datetime.fromisoformat(document["end_at"]), start + timedelta(hours=1))
    document["end_at"] = end.isoformat()
    document["messages"] = [row for row in document["messages"] if datetime.fromisoformat(row["sent_at"]) < end]
    for name in ("bills", "payrolls"):
        document[name] = [row for row in document[name] if start < datetime.fromisoformat(row["due_at"]) < end]
    path = root / "scenarios/single-person.json"
    path.write_text(json.dumps(document, ensure_ascii=False) + "\n", encoding="utf-8")
    provenance = {
        "source": "scenarios/single-person.json", "source_sha256": hashlib.sha256(original).hexdigest(),
        "original_window": original_window,
        "derived_window": {name: document[name] for name in ("start_at", "end_at", "timezone")},
        "retained_timed_inputs": {name: document[name] for name in ("messages", "bills", "payrolls")},
    }
    (root / "test-window-provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    assert source.read_bytes() == original
    return path, provenance


def test_selected_environment_runs_relocates_and_exports_its_actual_input(tmp_path: Path):
    """@brief 正式参数保持正式来源；人物原值、目录身份、状态与事件穿过真实恢复及认证导出。"""
    bundle = tmp_path / "bundle"
    path, provenance = _selected_hour(bundle / "environment")
    catalog = json.loads((ENVIRONMENT_ROOT / "catalog/manifest.json").read_bytes())
    original = json.loads((ENVIRONMENT_ROOT / "scenarios/single-person.json").read_bytes())
    engine = LifeEngine.create(path, bundle / "run", _identity(path))
    try:
        assert engine.context.semantics.catalog.manifest.model_dump(mode="json") == catalog
        assert json.loads(path.read_bytes())["agents"] == original["agents"]
        assert engine.context.state.end_time - engine.context.state.current_time == 3600
        requests = {agent: ("start_activity", start_activity("等待", "自己"))
                    for agent in engine.scheduling_state().ready_agent_ids}
        _, results = submit_round(engine, requests, "selected-start")
        assert results and all(result.status == "in_progress" for result in results)
        advance_next(engine)
        partial = bundle / "partial"
        partial.mkdir()
        _snapshot_current(engine, partial)
        saved = engine.context.state.model_dump(mode="json")
        engine.close()
        moved = tmp_path / "moved-bundle"
        shutil.move(bundle, moved)
        path = moved / "environment/scenarios/single-person.json"
        engine = LifeEngine.open(path, moved / "run")
        assert engine.context.state.model_dump(mode="json") == saved
        assert engine.context.semantics.catalog.manifest.model_dump(mode="json") == catalog
        completed = moved / "completed"
        completed.mkdir()
        checkpoint = _complete_run(engine, completed)
        manifest = export_run(engine.context.store, checkpoint.last_event_seq, moved / "export")
        checked, events, activities = validate_export(moved / "export")
        assert checked["run_id"] == manifest["run_id"] and checked["experiment_completed"] is True
        assert activities and any(event["event_type"] == "action_result" for event in events)
        source = ENVIRONMENT_ROOT / provenance["source"]
        assert hashlib.sha256(source.read_bytes()).hexdigest() == provenance["source_sha256"]
    finally:
        engine.close()
