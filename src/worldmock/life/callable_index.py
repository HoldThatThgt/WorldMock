"""@brief 商品品类的完整计数与例名，不展示功能 app。"""

from collections import defaultdict
from worldmock.exchange.types import GOODS_CATEGORY_LABELS
from worldmock.life.discovery import product_bindings

MAX_EXAMPLES = 3


def callable_index(context) -> dict:
    """@brief 全量可查询商品不受窗口裁剪。"""
    groups = defaultdict(list)
    for row in product_bindings(context):
        category = GOODS_CATEGORY_LABELS[context.catalog.goods[row["goods_id"]].category]
        groups[category].append(row["object"])
    rows = [{"category": category, "count": f"{len(names)} 个", "examples": sorted(names)[:MAX_EXAMPLES]}
            for category, names in sorted(groups.items())]
    return {"goods": rows}
