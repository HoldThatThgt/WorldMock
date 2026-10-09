"""使用真实环境与完整日结算生成可重复的活动、履约和回调基线。"""

from datetime import datetime, timedelta
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import tomlkit

from tests.data_paths import ENVIRONMENT_ROOT
from tests.dayrun_baseline import day_baseline_signature, deterministic_day_runtime
from tests.frozen_inputs import fixture_metadata
from tests.integration.runtime.current_contract_fixtures import compiled_persona
from tests.integration.runtime_fixtures import current_run_identity, write_agent_workspace
from tests.life.conftest import stage_day_plan, start_activity, start_arguments
from tests.life.environment_runtime import act, finish_activity, run_to_end
from worldmock.catalog.identity import refresh_manifest_identity
from worldmock.environment import environment_identity, load_environment
from worldmock.footprints.checkpoint import snapshot_directory
from worldmock.footprints.export import export_run
from worldmock.footprints.validation import validate_export
from worldmock.life.engine import LifeEngine
from worldmock.life.models import StartActivityRequest
from worldmock.run_setup import stage_model_identity


def _callback_rows():
    return [{"state": "driving_days", "initial": 2, "cap": 3, "reset_on_goods": ["tissue"]},
        {"rule_id": "driving", "trigger": {"class": "action", "key": "drive"},
         "effect": {"type": "state_delta", "state": "driving_days", "delta": -1, "once_per": "local_day"}},
        {"rule_id": "hourly-income", "trigger": {"class": "time", "key": "hourly"},
         "effect": {"type": "transfer", "direction": "income", "amount_fen": 100}},
        {"rule_id": "run-start", "trigger": {"class": "time", "key": "run_start"},
         "effect": {"type": "transfer", "direction": "income", "amount_fen": 200}},
        {"rule_id": "run-stop", "trigger": {"class": "time", "key": "run_stop"},
         "effect": {"type": "transfer", "direction": "expense", "amount_fen": 50}},
        {"rule_id": "threshold", "trigger": {"class": "state", "key": "driving_days", "below": 2},
         "effect": {"type": "transfer", "direction": "expense", "amount_fen": 20}}]


def _environment(case: Path, kind: str):
    destination = case / "environment"
    shutil.copytree(ENVIRONMENT_ROOT, destination)
    config_path = destination / "config.toml"
    config = tomlkit.parse(config_path.read_text(encoding="utf-8"))
    config["start_time"] = datetime.fromisoformat("2026-01-05T00:00:00+08:00")
    config["stop_time"] = config["start_time"] + timedelta(days=1)
    config["seed"] = 20260105 if kind == "activity" else 20260106
    config_path.write_text(tomlkit.dumps(config), encoding="utf-8", newline="\n")
    if kind == "callback":
        rows = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in _callback_rows())
        (destination / "catalog/callbacks.jsonl").write_text(rows, encoding="utf-8", newline="\n")
        refresh_manifest_identity(destination / "catalog")
    return load_environment(destination, metadata_loader=fixture_metadata)


def _activity_day(engine):
    _, basket = act(engine, "basket", start_activity("purchase", "牛肉面", "薯片"))
    assert basket.status == "in_progress"
    finish_activity(engine, basket, "basket")
    request = StartActivityRequest.model_validate(start_arguments(["公司", "哔哩哔哩"], ["walk", "interact"]))
    _, paired = act(engine, "travel-media", request)
    assert paired.status == "in_progress"
    active = [activity for activity in engine.context.state.activities.values() if activity.status == "active"]
    for activity in sorted(active, key=lambda item: item.kind == "interact"):
        finish_activity(engine, SimpleNamespace(activity_id=activity.activity_id), activity.kind.value)


def _callback_day(engine):
    _, drive = act(engine, "drive", start_activity("drive", "四季便利"))
    assert drive.status == "in_progress"
    finish_activity(engine, drive, "drive")
    _, purchase = act(engine, "reset-purchase", start_activity("purchase", "纸巾", "饭团"))
    assert purchase.status == "in_progress"
    finish_activity(engine, purchase, "reset-purchase")
    assert engine.context.state.agents[1].callback_states["driving_days"] == 3


def record_complete_day(monkeypatch, case: Path, project: Path, kind: str) -> dict:
    deterministic_day_runtime(monkeypatch)
    case.mkdir(parents=True)
    inputs = _environment(case, kind)
    identity = current_run_identity(inputs, f"baseline-{kind}")
    compiled = compiled_persona(inputs)
    engine = LifeEngine.create(inputs, case / "run", identity,
        stage_model_identity(inputs.config.model, inputs.model_metadata),
        {"world_dynamics": compiled.world_dynamics.model_dump(mode="json")})
    try:
        stage_day_plan(engine)
        (_activity_day if kind == "activity" else _callback_day)(engine)
        recorded = run_to_end(engine)
        assert any(row.event_type == "run_ended" for row in recorded)
        assert not any(row.event_type == "run_failed" for row in recorded)
        workspace = write_agent_workspace(case / "agent")
        engine.checkpoint({"agent:1": snapshot_directory(workspace, engine.context.store.assets)})
        export_run(engine.context.store, engine.context.store.last_event_seq, case / "export")
        exported, _, _ = validate_export(case / "export")
        assert exported["experiment_completed"]
        return {"persona_sha256": inputs.persona_sha256, "config_sha256": inputs.config_sha256,
                "environment_identity": environment_identity(inputs), "seed": inputs.config.seed,
                "summary": day_baseline_signature(case, project)}
    finally:
        engine.close()
