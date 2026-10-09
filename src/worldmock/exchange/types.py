"""@brief 固定商品品类的公开中文名称。"""

from types import MappingProxyType
from typing import Literal


#: 实际收货使用四种固定品类。
GoodsCategory = Literal["snack", "meal", "drink", "goods"]


GOODS_CATEGORY_LABELS = MappingProxyType({
    "snack": "零食", "meal": "食品", "drink": "饮料", "goods": "商品",
})
