"""使用真实环境与完整日结算生成可重复的活动、履约和回调基线。"""

from collections.abc import Callable
from datetime import datetime, timedelta
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import tomlkit

from tests.data_paths import ENVIRONMENT_ROOT
from tests.dayrun_baseline import day_baseline_signature, deterministic_day_runtime
from tests.frozen_inputs import fixture_metadata
from tests.integration.runtime_fixtures import current_run_identity, write_agent_workspace
from tests.life.conftest import do_at, purchase, query, sleep, stage_day_plan, travel, use_app
from tests.life.environment_runtime import act, act_group, conclude_app_use, finish_activity, run_to_end
from worldmock.catalog.identity import refresh_manifest_identity
from worldmock.environment import environment_identity, load_environment
from worldmock.footprints.checkpoint import snapshot_directory
from worldmock.footprints.export import export_run
from worldmock.footprints.validation import validate_export
from worldmock.life.engine import LifeEngine
from worldmock.run_setup import stage_model_identity


def _callback_rows():
    return [{"state": "driving_days", "initial": 2, "cap": 3, "reset_on_goods": ["tissue"]},
        {"rule_id": "driving", "trigger": {"class": "action", "key": "travel", "travel_method_id": "driving"},
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
    """同一轮在家点名休息并买一份牛肉面、两份薯片（prefer 写「饿了么」：薯片经饿了么从三杯咖啡来，牛肉面另选到
    一心面馆的美团）；查询「柠檬茶」（prefer 为 null），推进到做完，不为它收尾；同一轮驾车去公司并打开哔哩哔哩，
    哔哩哔哩放完第一条之后的第一次决策就收尾这次使用（连同那时待收尾的出行），出行还在路上时再推进到到达并收尾；
    在公司点名「季度销售数据整理」并做完；在公司睡 60 分钟。"""
    _, home = act_group(engine, "home-basket", do_at("家", "测试休息"),
                        purchase("牛肉面", "薯片", "薯片", prefer="饿了么"))
    assert [item.status for item in home] == ["in_progress", "in_progress"]
    _finish_active(engine, "home")
    _, asked = act(engine, "home-query", query("柠檬茶"))
    assert asked.status == "in_progress"
    finish_activity(engine, asked, "home-query")
    # 查询自己结束：做完即写下 concluded_at，不待收尾，下一组开始不被挡住。
    _, paired = act_group(engine, "travel-media", travel("公司", "驾车"), use_app("哔哩哔哩"))
    assert [item.status for item in paired] == ["in_progress", "in_progress"]
    conclude_app_use(engine, paired[1].activity_id, "trip-media")
    _finish_active(engine, "trip")
    _, work = act(engine, "office-work", do_at("公司", "季度销售数据整理"))
    assert work.status == "in_progress"
    finish_activity(engine, work, "office-work")
    _, nap = act(engine, "office-nap", sleep(60))
    assert nap.status == "in_progress"
    finish_activity(engine, nap, "office-nap")


def _callback_day(engine):
    """驾车去四季便利；在店里购买纸巾和饭团（prefer 为 null，人在卖这两件商品的地点，到店买）；driving_days 回到 3。"""
    _, drive = act(engine, "drive", travel("四季便利", "驾车"))
    assert drive.status == "in_progress"
    finish_activity(engine, drive, "drive")
    _, bought = act(engine, "reset-purchase", purchase("纸巾", "饭团"))
    assert bought.status == "in_progress"
    finish_activity(engine, bought, "reset-purchase")
    assert engine.context.state.agents[1].callback_states["driving_days"] == 3


def create_complete_day_engine(case: Path, kind: str) -> LifeEngine:
    """@brief 在 case 下建好这种完整日（activity 或 callback）的环境副本并创建引擎；固定记录身份与计时
    （deterministic_day_runtime）由调用方负责。"""
    case.mkdir(parents=True)
    inputs = _environment(case, kind)
    identity = current_run_identity(inputs, f"baseline-{kind}")
    return LifeEngine.create(inputs, case / "run", identity,
        stage_model_identity(inputs.config.model, inputs.model_metadata), {})


def play_complete_day(engine: LifeEngine, kind: str) -> list:
    """@brief 预置当日计划、走完这种完整日的脚本并推进到运行截止，返回全部已提交事件。"""
    stage_day_plan(engine)
    (_activity_day if kind == "activity" else _callback_day)(engine)
    return run_to_end(engine)


def record_complete_day(monkeypatch, case: Path, project: Path, kind: str,
                        signature: Callable[[Path, Path], dict] = day_baseline_signature) -> dict:
    """@brief 录一份脚本化完整日并返回其签名；signature 缺省为绑定代码身份的签名，行为比较传行为签名。"""
    deterministic_day_runtime(monkeypatch)
    engine = create_complete_day_engine(case, kind)
    inputs = engine.context.inputs
    try:
        recorded = play_complete_day(engine, kind)
        assert any(row.event_type == "run_ended" for row in recorded)
        assert not any(row.event_type == "run_failed" for row in recorded)
        workspace = write_agent_workspace(case / "agent")
        engine.checkpoint({"agent:1": snapshot_directory(workspace, engine.context.store.assets)})
        export_run(engine.context.store, engine.context.store.last_event_seq, case / "export")
        exported, _ = validate_export(case / "export")
        assert exported["experiment_completed"]
        return {"persona_sha256": inputs.persona_sha256, "config_sha256": inputs.config_sha256,
                "environment_identity": environment_identity(inputs), "seed": inputs.config.seed,
                "summary": signature(case, project)}
    finally:
        engine.close()
