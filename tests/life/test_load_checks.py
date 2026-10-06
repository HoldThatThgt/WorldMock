"""@brief 人工环境上的统一场景准入和保留工作安排加载检查。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tests.data_paths import ENVIRONMENT_ROOT
from worldmock.errors import IntegrityError
from worldmock.life.load_checks import check_visible_labels, check_work_schedules, visible_object_labels
from worldmock.life.world_setup import load_runtime_scenario

_SCENARIO = ENVIRONMENT_ROOT / "scenarios/single-person.json"


def _changed(tmp_path: Path, mutate) -> Path:
    """@brief 隔离真实环境后改写一个场景反例。"""
    root = tmp_path / "environment"
    shutil.copytree(ENVIRONMENT_ROOT, root)
    path = root / "scenarios/single-person.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    mutate(document)
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.mark.parametrize(("change", "code"), [
    (lambda d: d["environment"].update(environment_version="other"), "ENVIRONMENT_PIN_MISMATCH"),
    (lambda d: d["environment"].update(environment_id="other"), "ENVIRONMENT_PIN_MISMATCH"),
    (lambda d: d.update(accounts=[row for row in d["accounts"] if row["role"] != "merchant:cafe"]),
     "MERCHANT_ACCOUNT_MISSING"),
    (lambda d: d["agents"][0].update(work_topic_ids=["unknown"]), "WORK_TOPIC_UNKNOWN"),
    (lambda d: d["agents"][0].update(work_schedule=None), "WORK_SCHEDULE_REQUIRED"),
    (lambda d: d["locations"].append({"location_id": "unknown"}), "ENVIRONMENT_REF_UNKNOWN"),
    (lambda d: d["agents"][0].update(initial_inventory={"home": {"unknown": 1}}), "ENVIRONMENT_REF_UNKNOWN"),
])
def test_real_loader_rejects_registration_counterexamples(tmp_path, change, code):
    """@brief 场景加载走唯一目录准入入口，每个反例返回精确稳定码。"""
    with pytest.raises(IntegrityError, match=code):
        load_runtime_scenario(_changed(tmp_path, change))


def test_existing_map_without_branch_labels_loads_unchanged() -> None:
    """@brief 环境直接读取发布图，不需要旧地图语料。"""
    semantics, _ = load_runtime_scenario(_SCENARIO)
    assert len(semantics.scenario.locations) == 7
    assert semantics.catalog.map_data.seed == 7
    assert all(location.display_name == location.name for location in semantics.scenario.locations)


def test_work_schedule_workplace_must_be_a_selected_location(tmp_path) -> None:
    """@brief 工作安排的地点必须被场景选中。"""
    path = _changed(tmp_path, lambda d: d["agents"][0]["work_schedule"].update(workplace_location_id="unknown"))
    with pytest.raises(IntegrityError, match="work_schedule.workplace_location_id"):
        load_runtime_scenario(path)


@pytest.mark.parametrize(("field", "weekly"), [("makeup_work_dates", None),
    ("spring_festival_dates", {"starts": 540, "ends": 1080, "breaks": []})])
def test_work_schedule_must_agree_with_the_calendar_inside_the_window(tmp_path, field, weekly):
    """@brief 补班或休假日期必须与人物班次一致。"""
    def change(document):
        document["calendar"][field] = ["2026-01-05"]
        document["agents"][0]["work_schedule"]["weekly"]["0"] = weekly

    with pytest.raises(IntegrityError, match="Work schedules are not admissible"):
        load_runtime_scenario(_changed(tmp_path, change))


def test_overtime_dates_and_dates_outside_the_window_are_not_checked(tmp_path):
    """@brief 窗外休假和加班机会不强行改写班次。"""
    def change(document):
        document["calendar"]["spring_festival_dates"] = ["2027-01-05"]
        document["calendar"]["overtime_opportunity_dates"] = ["2026-01-05"]

    semantics, _ = load_runtime_scenario(_changed(tmp_path, change))
    check_work_schedules(semantics.scenario)


def test_a_null_work_schedule_is_not_cross_checked_and_others_still_are(tmp_path):
    """@brief 没有分配主题的人物可没有工作安排。"""
    def change(document):
        document["agents"][0].update(work_topic_ids=[], work_schedule=None)

    semantics, _ = load_runtime_scenario(_changed(tmp_path, change))
    assert semantics.scenario.agents[0].work_schedule is None


def test_visible_labels_include_registered_apps_foods_and_places():
    """@brief 应用、食品持有物和地点都进入加载期名称唯一性检查。"""
    semantics, _ = load_runtime_scenario(_SCENARIO)
    labels = visible_object_labels(semantics.scenario, ENVIRONMENT_ROOT / "corpora")[1]
    assert "应用：美团" in labels
    assert "持有物：薯片" in labels
    assert "地点：三杯咖啡" in labels
    assert all(len(sources) == 1 for sources in labels.values())


def test_duplicate_object_labels_are_rejected_at_load():
    """@brief 同时可见的应用同名会在加载检查处拒绝，不等到模型决策。"""
    semantics, _ = load_runtime_scenario(_SCENARIO)
    first, second, *rest = semantics.scenario.apps
    duplicate = second.model_copy(update={"name": first.name})
    scenario = semantics.scenario.model_copy(update={"apps": (first, duplicate, *rest)})
    with pytest.raises(IntegrityError, match="non-unique object labels"):
        check_visible_labels(scenario, ENVIRONMENT_ROOT / "corpora")


def _changed_locations(tmp_path, mutate):
    """@brief 按真实目录发布入口建立一个地点登记反例。"""
    from worldmock.catalog.identity import refresh_manifest_identity

    path = _changed(tmp_path, lambda document: None)
    directory = path.parent.parent / "catalog"
    target = directory / "locations.json"
    document = json.loads(target.read_text(encoding="utf-8"))
    mutate({row["location_id"]: row for row in document["locations"]})
    target.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    refresh_manifest_identity(directory)
    return path


def test_registered_location_kind_must_belong_to_the_fixed_set(tmp_path):
    """@brief 发布地点的类别越界由真实加载器拒绝。"""
    path = _changed_locations(tmp_path, lambda rows: rows["home"].update(kind="unknown"))
    with pytest.raises(IntegrityError, match="ENVIRONMENT_KIND_INVALID"):
        load_runtime_scenario(path)


def test_registered_duplicate_location_display_names_are_rejected(tmp_path):
    """@brief 所选地点使用同一显示名时无法供人物区分，登记拒绝。"""
    path = _changed_locations(tmp_path, lambda rows: rows["noodle-2"].update(name=rows["noodle-1"]["name"]))
    with pytest.raises(IntegrityError, match="ENVIRONMENT_ID_DUPLICATE"):
        load_runtime_scenario(path)


def test_registered_branch_label_cannot_duplicate_another_full_name(tmp_path):
    """@brief 分店标注与另一个声明的完整名称相同也属于显示名碰撞。"""
    def change(rows):
        rows["noodle-1"].update(name="面馆", branch_label="北店")
        rows["noodle-2"].update(name="面馆（北店）")

    with pytest.raises(IntegrityError, match="ENVIRONMENT_ID_DUPLICATE"):
        load_runtime_scenario(_changed_locations(tmp_path, change))


def test_registered_branch_labels_make_selected_locations_distinguishable(tmp_path):
    """@brief 两家同名分店经真实组装保留不同显示名和加载期可见标签。"""
    def change(rows):
        rows["noodle-1"].update(name="面馆", branch_label="北店")
        rows["noodle-2"].update(name="面馆", branch_label="南店")

    path = _changed_locations(tmp_path, change)
    semantics, _ = load_runtime_scenario(path)
    locations = {row.location_id: row.display_name for row in semantics.scenario.locations}
    assert locations["noodle-1"] == "面馆（北店）"
    assert locations["noodle-2"] == "面馆（南店）"
    labels = visible_object_labels(semantics.scenario, path.parent.parent / "corpora")[1]
    assert "地点：面馆（北店）" in labels and "地点：面馆（南店）" in labels


def test_registered_branch_labels_are_normalized_as_part_of_the_full_name(tmp_path):
    """@brief 分店标注只在空白上不同仍是同一人物可见名。"""
    def change(rows):
        rows["noodle-1"].update(name="面馆", branch_label="北店")
        rows["noodle-2"].update(name="面馆", branch_label=" 北 店 ")

    with pytest.raises(IntegrityError, match="ENVIRONMENT_ID_DUPLICATE"):
        load_runtime_scenario(_changed_locations(tmp_path, change))


@pytest.mark.parametrize("shift", [
    {"starts": 1080, "ends": 540, "breaks": []},
    {"starts": 540, "ends": 1500, "breaks": []},
    {"starts": 540, "ends": 1080, "breaks": [{"starts": 500, "ends": 600}]},
    {"starts": 540, "ends": 1080, "breaks": [{"starts": 600, "ends": 800}, {"starts": 700, "ends": 900}]},
])
def test_real_loader_rejects_malformed_work_shift_and_break(tmp_path, shift):
    """@brief 形状错误经真实场景读入而非只直接构造模型也会拒绝。"""
    def change(document):
        document["agents"][0]["work_schedule"]["weekly"]["0"] = shift

    with pytest.raises(IntegrityError, match="Scenario validation failed"):
        load_runtime_scenario(_changed(tmp_path, change))
