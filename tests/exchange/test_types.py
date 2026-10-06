"""@brief 固定分类闭集及标签的直接行为证明。"""

from worldmock.exchange.types import (
    CONTENT_KIND_LABELS, FOOD_CATEGORY_LABELS, FOOD_STORE_KINDS, PLACE_KIND_ENVIRONMENTS,
    START_ACTIVITY_ACTIONS, ContentKind, FoodCategory, LocationKind, SummarySourceKind,
)


def test_fixed_type_sets_and_labels():
    """@brief 地点、食品、内容与来源成员均等于冻结闭集。"""
    assert tuple(LocationKind) == (
        "home", "office", "park", "fitness", "restaurant", "cafe", "convenience_store", "supermarket")
    assert tuple(FoodCategory) == ("snack", "drink", "meal")
    assert tuple(ContentKind) == ("article", "image_text", "short_video", "long_video", "live")
    assert tuple(SummarySourceKind) == ("real", "synthetic")
    assert FOOD_STORE_KINDS == {"restaurant", "cafe", "convenience_store", "supermarket"}
    assert dict(FOOD_CATEGORY_LABELS) == {"snack": "零食", "drink": "饮品", "meal": "正餐"}
    assert dict(CONTENT_KIND_LABELS) == {
        "article": "文章", "image_text": "图文", "short_video": "短视频", "long_video": "长视频", "live": "实况"}
    assert PLACE_KIND_ENVIRONMENTS["park"] == "outdoor"
    assert START_ACTIVITY_ACTIONS == {
        "travel_to", "work", "purchase", "consume", "use_app", "chat", "call", "read", "do", "wait", "sleep"}
