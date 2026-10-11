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
from tests.life.conftest import do_at, purchase, sleep, stage_day_plan, travel, use_app
from tests.life.environment_runtime import act, act_group, finish_activity, run_to_end
from worldmock.catalog.identity import refresh_manifest_identity
from worldmock.environment import environment_identity, load_environment
from worldmock.footprints.checkpoint import snapshot_directory
from worldmock.footprints.export import export_run
from worldmock.footprints.validation import validate_export
from worldmock.life.engine import LifeEngine
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


def _finish_active(engine, label):
    """把此刻进行中的活动逐项推进到真实终局并收尾：先结束得早的，再结束得晚的。"""
    active = [activity for activity in engine.context.state.activities.values() if activity.status == "active"]
    for activity in sorted(active, key=lambda item: (item.planned_end_at, item.activity_id)):
        finish_activity(engine, SimpleNamespace(activity_id=activity.activity_id), f"{label}-{activity.kind.value}")


def _activity_day(engine):
    """同一轮在家点名休息并买一份牛肉面、两份薯片；同一轮驾车去公司并打开哔哩哔哩；在公司点名「季度销售数据整理」
    并做完；在公司睡 60 分钟。"""
    _, home = act_group(engine, "home-basket", do_at("家", "测试休息"), purchase("牛肉面", "薯片", "薯片"))
    assert [item.status for item in home] == ["in_progress", "in_progress"]
    _finish_active(engine, "home")
    _, paired = act_group(engine, "travel-media", travel("公司", "驾车"), use_app("哔哩哔哩"))
    assert [item.status for item in paired] == ["in_progress", "in_progress"]
    _finish_active(engine, "trip")
    _, work = act(engine, "office-work", do_at("公司", "季度销售数据整理"))
    assert work.status == "in_progress"
    finish_activity(engine, work, "office-work")
    _, nap = act(engine, "office-nap", sleep(60))
    assert nap.status == "in_progress"
    finish_activity(engine, nap, "office-nap")


def _callback_day(engine):
    _, drive = act(engine, "drive", travel("四季便利", "驾车"))
    assert drive.status == "in_progress"
    finish_activity(engine, drive, "drive")
    _, bought = act(engine, "reset-purchase", purchase("纸巾", "饭团"))
    assert bought.status == "in_progress"
    finish_activity(engine, bought, "reset-purchase")
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
