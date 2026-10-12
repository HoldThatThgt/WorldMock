"""@brief 商品品类的完整计数与例名，以及可以写进 prefer 的下单 app 全名；不展示功能 app。"""

from collections import defaultdict
from worldmock.exchange.types import GOODS_CATEGORY_LABELS
from worldmock.life.discovery import product_bindings
from worldmock.life.order_apps import order_app_ids

MAX_EXAMPLES = 3


def callable_index(context) -> dict:
    """@brief 全量可查询商品不受窗口裁剪；prefer_apps 是全部下单 app 的完整名称，按 app_id 升序。

    prefer_apps 只取决于冻结目录，整次运行每一次观察都相同：不按此刻营业与否筛选，不轮转，不截断；
    没有下单 app 的环境里是空数组。
    """
    groups = defaultdict(list)
    for row in product_bindings(context):
        category = GOODS_CATEGORY_LABELS[context.catalog.goods[row["goods_id"]].category]
        groups[category].append(row["object"])
    rows = [{"category": category, "count": f"{len(names)} 个", "examples": sorted(names)[:MAX_EXAMPLES]}
            for category, names in sorted(groups.items())]
    return {"goods": rows, "prefer_apps": [context.catalog.apps[key].name for key in order_app_ids(context.catalog)]}
