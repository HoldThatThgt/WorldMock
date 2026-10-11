"""两种运行输入警告由真实目录和人设表算出，冻结进运行清单、写进运维日志并进入导出的质量报告。"""
import json

import pytest

from tests.frozen_inputs import fixture_metadata
from tests.integration.runtime.current_contract_fixtures import ROOT, inputs, metadata_service
from tests.life.conftest import advance_next, do_at, stage_day_plan
from tests.life.environment_inputs import catalog_rows, copy_environment, write_catalog_rows
from tests.life.environment_runtime import act
from worldmock.environment import load_environment
from worldmock.footprints.export import export_run
from worldmock.run_setup import creation_inputs, input_warnings

#: 带坐标的地点显示名。
SUSPICIOUS_NAME = "五里公园（31.2, 121.5）"


def _warned_environment(tmp_path):
    """夹具环境的副本：去掉全部 app 内容（只有内容的 app 一并去掉，还卖商品的哔哩哔哩改成纯目录 app），
    再给公园起一个带坐标的显示名，按生产入口刷新清单。"""
    root = copy_environment(tmp_path / "warned-environment")
    contents = [row for row in catalog_rows(root, "content-catalog.jsonl") if not row.get("app_ids")]
    write_catalog_rows(root, "content-catalog.jsonl", contents)
    apps = [row for row in catalog_rows(root, "app-catalog.jsonl") if row["app_id"] not in {"douyin", "keep"}]
    next(row for row in apps if row["app_id"] == "bilibili")["browse_duration_seconds"] = 600
    write_catalog_rows(root, "app-catalog.jsonl", apps)
    locations = catalog_rows(root, "location.jsonl")
    next(row for row in locations if row["location_id"] == "park")["name"] = SUSPICIOUS_NAME
    write_catalog_rows(root, "location.jsonl", locations)
    return root


def test_fixture_environment_has_no_input_warnings():
    """夹具环境有 app 内容、地点名都是自然名称：新建运行的输入没有警告，工作区输入照样带着由人设表构造的卡片。"""
    frozen = inputs()
    specs, warnings = creation_inputs(frozen)
    assert warnings == [] and input_warnings(frozen) == []
    assert specs[0]["profile"]["persona_card_struct"]["tastes"]["media"] == ["长视频", "短视频"]


def test_actual_input_warnings_are_frozen_and_exported(engine_factory, tmp_path):
    """没有 app 内容而人设表写了媒介、地点显示名带坐标：两条警告按真实目录算出，冻结进清单并计入导出的质量报告。"""
    root = _warned_environment(tmp_path)
    loaded = load_environment(root, metadata_loader=fixture_metadata)
    specs, warnings = creation_inputs(loaded)
    assert specs[0]["profile"]["persona_card_struct"]
    assert [(row["code"], row["agent_id"]) for row in warnings] == [
        ("CONTENT_EMPTY_WITH_MEDIA_TASTES", 1), ("LOCATION_NAME_SUSPICIOUS", None)]
    assert warnings[0]["details"] == {"media": ["长视频", "短视频"], "habits": []}
    assert warnings[1]["details"] == {"location_id": "park", "name": SUSPICIOUS_NAME, "patterns": ["coordinates"]}
    engine = engine_factory(environment_root=root, create_options={"input_warnings": warnings})
    assert engine.context.store.manifest["input_warnings"] == warnings
    stage_day_plan(engine)
    act(engine, "content", do_at("家", "测试休息"))
    advance_next(engine)
    export_run(engine.context.store, engine.context.store.last_event_seq, tmp_path / "export")
    quality = json.loads((tmp_path / "export/quality.json").read_text(encoding="utf-8"))
    assert quality["input_warnings"]["recorded"] is True and quality["input_warnings"]["warning_count"] == 2
    assert quality["input_warnings"]["code_counts"] == {
        "CONTENT_EMPTY_WITH_MEDIA_TASTES": 1, "LOCATION_NAME_SUSPICIOUS": 1}
    assert quality["input_warnings"]["agent_counts"] == {"1": 1}
    assert quality["input_warnings"]["scenario_warning_count"] == 1


def test_new_run_writes_each_input_warning_to_the_manifest_options_and_the_operations_log(tmp_path, monkeypatch):
    """真实新建入口：警告随人物工作区输入一起算出，放进引擎的新建选项，并各写一条运维日志；运行不因警告失败。"""
    from worldmock.runtime import RunRequest, _prepare_new_run, _society_specs

    root = _warned_environment(tmp_path)
    metadata_service(monkeypatch, load_environment(root, metadata_loader=fixture_metadata))
    prepared = _prepare_new_run(RunRequest(ROOT, root, tmp_path / "run", "warned", "warned"))
    specs = _society_specs(prepared)
    options = prepared.env_kwargs["create_options"]
    assert [row["code"] for row in options["input_warnings"]] == [
        "CONTENT_EMPTY_WITH_MEDIA_TASTES", "LOCATION_NAME_SUSPICIOUS"]
    assert options["persona_habit_responses"] == {1: ["公园慢跑半小时", "读几页小说"]}
    assert "world_dynamics" not in options and specs[0]["id"] == 1
    logged = [json.loads(line) for path in sorted((tmp_path / "run/logs").glob("*"))
              for line in path.read_text(encoding="utf-8").splitlines()]
    frozen = [row for row in logged if row["detail"] == "Input warning frozen into the run manifest"]
    assert [(row["code"], row["level"], row["component"]) for row in frozen] == [
        ("CONTENT_EMPTY_WITH_MEDIA_TASTES", "warn", "runtime"), ("LOCATION_NAME_SUSPICIOUS", "warn", "runtime")]
    assert frozen[0]["agent_id"] == 1 and "agent_id" not in frozen[1]


@pytest.mark.parametrize("code", ["CARD_FIELD_OVERRIDDEN", "CARD_FIELD_CONFLICT", "PERSONA_AGREEMENT_LOW",
                                  "COVERAGE_BELOW_THRESHOLD"])
def test_engine_refuses_to_freeze_a_deleted_warning_code(engine_factory, code):
    """引擎新建运行时拒绝闭集之外的警告码：随人物卡片编译删除的四个码不能再进运行清单。"""
    from worldmock.errors import IntegrityError

    warning = {"code": code, "agent_id": 1, "message": "retired", "details": {}}
    with pytest.raises(IntegrityError, match="closed warning shape"):
        engine_factory(create_options={"input_warnings": [warning]})
