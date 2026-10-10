"""@brief 画像覆盖目标直接取唯一环境的地点显示名、食品与内容应用。"""

from tests.data_paths import ENVIRONMENT_ROOT
from tests.profile.model_fixture import metadata
from worldmock.environment import load_environment
from worldmock.profile.coverage_targets import build_coverage_targets
from worldmock.profile.coverage import validate_coverage
from worldmock.profile.models import HabitSignature, Tastes
from tests.profile.conftest import make_card, make_window


def test_coverage_targets_use_registered_location_display_names_food_and_content_apps():
    inputs = load_environment(ENVIRONMENT_ROOT, metadata_loader=metadata)
    targets = build_coverage_targets(inputs)
    assert targets.habit_response_keywords == tuple(sorted(
        {row.display_name for row in inputs.catalog.locations.values()}))
    assert targets.food_kinds == tuple(sorted({row.name for row in inputs.catalog.goods.values()
                                             if row.category in {"meal", "snack", "drink"}}))
    assert targets.app_names == tuple(sorted(row.name for row in inputs.catalog.apps.values() if row.role == "content"))
    assert set(targets.model_dump()) == {"habit_response_keywords", "food_kinds", "app_names"}
    assert "纸巾" not in targets.food_kinds
    assert {"支付宝", "高德地图", "微信"}.isdisjoint(targets.app_names)
    assert {"睡觉", "步行", "驾车", "打车", "乘公交", "交互", "购买"}.isdisjoint(targets.habit_response_keywords)


def test_coverage_targets_do_not_use_old_persona_catalog_subsets():
    inputs = load_environment(ENVIRONMENT_ROOT, metadata_loader=metadata)
    other = inputs.persona.model_copy(update={"name": "另一个人", "home_location": "公司"})
    assert build_coverage_targets(inputs) == build_coverage_targets(inputs.__class__(
        **{**inputs.__dict__, "persona": other}))


def test_registered_names_cover_actual_habits_and_media_without_function_apps_or_actions():
    """@brief 真实目录只覆盖地点习惯与完整内容应用，功能应用、动作与应用片段均缺失。"""
    inputs = load_environment(ENVIRONMENT_ROOT, metadata_loader=metadata)
    card = make_card(
        habits=(HabitSignature(trigger="下班", response="到五里公园散步", time_window=make_window(19.0, 20.0)),
                HabitSignature(trigger="睡前", response="睡觉", time_window=make_window(22.0, 23.0))),
        tastes=Tastes(content_genres=(), food_preferences=(), media=("抖音", "支付宝", "哔哩", "哔哩哔哩视频")))
    report = validate_coverage(card, build_coverage_targets(inputs))
    assert report.total_items == 6 and report.covered_items == 2
    assert [(item.kind, item.value) for item in report.missing] == [
        ("habit_response", "睡觉"), ("media", "支付宝"), ("media", "哔哩"), ("media", "哔哩哔哩视频")]


def test_location_branches_use_full_display_names_in_habit_targets():
    """@brief 分店显示名保持完整，覆盖目标不退回目录中的基础名称。"""
    from dataclasses import replace

    inputs = load_environment(ENVIRONMENT_ROOT, metadata_loader=metadata)
    locations = dict(inputs.catalog.locations)
    locations["park"] = locations["park"].model_copy(update={"branch_label": "东区"})
    targets = build_coverage_targets(replace(inputs, catalog=replace(inputs.catalog, locations=locations)))
    assert "五里公园（东区）" in targets.habit_response_keywords
    assert "五里公园" not in targets.habit_response_keywords
