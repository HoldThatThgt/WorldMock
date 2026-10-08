"""@brief 固定分类闭集、标签和店铺供给表的直接行为证明。"""

import pytest

from worldmock.exchange import types
from worldmock.exchange.types import (
    ACTION_CATEGORY_LABELS, APP_USE_MANNER_LABELS, ActionCategory, AppPurpose, AppUseManner,
    CommunicationMode, GOODS_CATEGORY_LABELS,
    GOODS_CATEGORY_STORE_KINDS, GoodsCategory, LocationKind, MANNER_DEVICE, PLACE_KIND_ENVIRONMENTS,
    START_ACTIVITY_ACTIONS, STORE_KINDS, SummarySourceKind,
)


def test_fixed_type_sets_and_labels():
    """@brief 每个闭集成员、次序与标签均为规格冻结的值。"""
    assert tuple(LocationKind) == (
        "home", "office", "park", "fitness", "restaurant", "cafe", "convenience_store", "supermarket", "gas_station", "parcel_station")
    assert tuple(GoodsCategory) == ("meal", "snack", "drink", "leisure", "daily", "fuel")
    assert tuple(ActionCategory) == ("entertainment", "work_study", "social", "exercise", "daily_care")
    assert tuple(AppUseManner) == ("phone_continuous", "other_device", "offline_assisted")
    assert tuple(CommunicationMode) == ("text", "voice")
    assert tuple(AppPurpose) == ("navigation", "shopping", "payment")
    assert tuple(SummarySourceKind) == ("real", "synthetic")
    assert dict(GOODS_CATEGORY_LABELS) == {
        "meal": "正餐", "snack": "零食", "drink": "饮品", "leisure": "休闲用品", "daily": "日常用品", "fuel": "燃油"}
    assert dict(ACTION_CATEGORY_LABELS) == {
        "entertainment": "娱乐", "work_study": "工作与学习", "social": "社交", "exercise": "运动",
        "daily_care": "日常照护"}
    assert dict(APP_USE_MANNER_LABELS) == {
        "phone_continuous": "持续使用手机 app", "other_device": "通过其他设备使用 app",
        "offline_assisted": "借助 app 安排或记录线下行为"}
    assert not hasattr(types, "ContentKind")
    assert not hasattr(types, "CONTENT_KIND_LABELS")
    assert START_ACTIVITY_ACTIONS == {"travel_to", "sleep", "wait", "purchase", "do", "collect_parcel"}


def test_device_and_place_mappings_are_fixed():
    """@brief 使用方式仅映射两种设备，地点环境映射保持现有十项。"""
    assert dict(MANNER_DEVICE) == {
        "phone_continuous": "phone", "other_device": "other", "offline_assisted": "phone"}
    assert dict(PLACE_KIND_ENVIRONMENTS) == {
        "home": "home", "office": "office", "park": "outdoor", "fitness": "fitness",
        "restaurant": "venue", "cafe": "venue", "convenience_store": "venue", "supermarket": "venue", "gas_station": "venue", "parcel_station": "venue"}


def test_goods_store_supply_is_fixed_for_every_category():
    """@brief 每类商品只由固定店铺类型供给，咖啡馆不能供给正餐或非食品。"""
    assert STORE_KINDS == {"restaurant", "cafe", "convenience_store", "supermarket", "gas_station"}
    assert dict(GOODS_CATEGORY_STORE_KINDS) == {
        "meal": {"restaurant", "convenience_store", "supermarket"},
        "snack": STORE_KINDS - {"gas_station"}, "drink": STORE_KINDS - {"gas_station"},
        "leisure": {"convenience_store", "supermarket"}, "daily": {"convenience_store", "supermarket"}, "fuel": {"gas_station"},
    }
    with pytest.raises(TypeError):
        GOODS_CATEGORY_STORE_KINDS[GoodsCategory.MEAL] = STORE_KINDS
    with pytest.raises(AttributeError):
        GOODS_CATEGORY_STORE_KINDS[GoodsCategory.MEAL].add(LocationKind.CAFE)
    with pytest.raises(TypeError):
        MANNER_DEVICE[AppUseManner.OTHER_DEVICE] = "phone"
    with pytest.raises(TypeError):
        ACTION_CATEGORY_LABELS[ActionCategory.SOCIAL] = "交谈"
    with pytest.raises(TypeError):
        APP_USE_MANNER_LABELS[AppUseManner.PHONE_CONTINUOUS] = "手机"


@pytest.mark.parametrize("retired", ["FoodCategory", "FOOD_STORE_KINDS", "FOOD_CATEGORY_LABELS"])
def test_retired_food_names_have_no_compatibility_export(retired):
    """@brief 旧食品闭集名称直接删除，不保留兼容导出。"""
    assert not hasattr(types, retired)
