"""@brief 画像覆盖目标直接取登记的动作、地点、商品与应用。"""

from tests.data_paths import ARTIFICIAL_ENVIRONMENT_ROOT
from worldmock.life.world_setup import load_runtime_scenario
from worldmock.profile.coverage_targets import build_coverage_targets


def test_coverage_targets_use_registered_actions_places_food_goods_and_apps():
    """@brief 固定人工环境的期望逐名列出，非食品商品不混入食品偏好。"""
    semantics, _ = load_runtime_scenario(ARTIFICIAL_ENVIRONMENT_ROOT / "scenarios/single-person.json")
    targets = build_coverage_targets(semantics.scenario, semantics.catalog)
    assert targets.habit_response_keywords == tuple(sorted({
        "三杯咖啡", "一心面馆", "二味小炒", "公司", "五里公园", "四季便利", "家",
        "刷推荐", "办公", "安静休息", "跑步", "聊天", "学课程", "语音通话",
    }))
    assert targets.food_kinds == tuple(sorted({"牛肉面", "饭团", "柠檬茶", "薯片"}))
    assert targets.action_names == tuple(sorted({"刷推荐", "办公", "安静休息", "跑步", "聊天", "学课程", "语音通话"}))
    assert "media_kinds" not in targets.model_dump()
    assert targets.app_names == tuple(sorted({"哔哩哔哩", "微信", "抖音", "支付宝", "美团", "饿了么", "高德地图", "Keep"}))
    assert "扑克牌" not in targets.food_kinds
    assert "纸巾" not in targets.food_kinds


def test_coverage_targets_follow_the_catalog_instead_of_a_scenario_subset():
    """@brief 同目录下的人物与所选地点变化不会收窄画像可覆盖的登记名称。"""
    semantics, _ = load_runtime_scenario(ARTIFICIAL_ENVIRONMENT_ROOT / "scenarios/single-person.json")
    scenario = semantics.scenario.model_copy(update={"locations": (), "apps": (), "goods": (), "actions": ()})
    assert build_coverage_targets(scenario, semantics.catalog) == build_coverage_targets(
        semantics.scenario, semantics.catalog)
