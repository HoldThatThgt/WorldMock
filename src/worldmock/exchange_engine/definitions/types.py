"""@brief 商品的固定品类、公开名称与收货品质。"""

from types import MappingProxyType
from typing import Literal


#: 实际收货使用四种固定品类。
GoodsCategory = Literal["snack", "meal", "drink", "goods"]

#: 到店或外卖的每份商品在收货时确定一档品质。
GoodsQuality = Literal["好", "中", "坏"]


GOODS_CATEGORY_LABELS = MappingProxyType({
    "snack": "零食", "meal": "食品", "drink": "饮料", "goods": "商品",
})
