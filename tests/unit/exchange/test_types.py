"""固定商品品类公开标签与退役字段的验证。"""

import pytest
from typing import get_args
from worldmock.exchange_engine.definitions import types
from worldmock.exchange_engine.definitions.types import GOODS_CATEGORY_LABELS, GoodsCategory


def test_exact_four_categories_and_public_labels():
    assert get_args(GoodsCategory) == ("snack", "meal", "drink", "goods")
    assert dict(GOODS_CATEGORY_LABELS) == {
        "snack": "零食", "meal": "食品", "drink": "饮料", "goods": "商品"}
    with pytest.raises(TypeError):
        GOODS_CATEGORY_LABELS["goods"] = "daily"


@pytest.mark.parametrize("retired", ["ActionCategory", "AppUseManner", "AppPurpose", "CommunicationMode",
                                     "MANNER_DEVICE", "START_ACTIVITY_ACTIONS", "GOODS_CATEGORY_STORE_KINDS"])
def test_retired_inference_tables_have_no_compatibility_export(retired):
    assert not hasattr(types, retired)
