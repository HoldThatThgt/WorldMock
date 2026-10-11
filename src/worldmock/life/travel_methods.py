"""@brief 出行方式表：把引擎自带的步行与数据集定义的行合成唯一的有序表。

工具层、名称解析用的绑定行、受理、行程规划和人物可见文字都只经本模块读取出行方式；
步行的数值只在这里与数据集的行合在一起，其余模块不各自判断「是不是步行」。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from worldmock.catalog.models import Catalog
from worldmock.errors import IntegrityError
from worldmock.exchange.constants import (
    WALKING_METERS_PER_SECOND, WALKING_TRAVEL_METHOD_ID, WALKING_TRAVEL_METHOD_NAME,
)

#: 工具层对体力档位只有两种说法，不出现身体数值。
EASY, HARD = "轻松", "费力"
#: 数据集声明的体力档位在工具层的说法。
_EFFORT = {"idle": EASY, "light": EASY, "moderate": HARD, "heavy": HARD}


@dataclass(frozen=True, slots=True)
class Method:
    """@brief 一种出行方式的全部事实：自带的步行与数据集的行同一个形状。"""

    #: 出行方式身份：活动、行程身份、app 绑定与回调规则都用它。
    travel_method_id: str
    #: 人物看到并写进 detail 的名称，也用于活动名、环境层和到达句。
    name: str
    #: 基础速度，米/秒；整数与小数保持登记时的写法。
    speed_meters_per_second: int | float
    #: 起步价，分。
    start_price_fen: int
    #: 每公里价格，分。
    price_per_km_fen: int
    #: 代谢当量键：自带的步行是 walking，其余是所声明的体力档位。
    exertion: str
    #: 工具层的说法：「轻松」或「费力」。
    effort: str


#: 引擎自带的步行：任何环境包都有，免费，代谢当量键是它自己的身份。
WALKING = Method(WALKING_TRAVEL_METHOD_ID, WALKING_TRAVEL_METHOD_NAME, WALKING_METERS_PER_SECOND, 0, 0,
                 WALKING_TRAVEL_METHOD_ID, HARD)


def travel_methods(catalog: Catalog) -> tuple[Method, ...]:
    """@brief 全部出行方式的有序表：步行在前，数据集定义的方式按身份升序。"""
    rows = sorted(catalog.travel_methods.values(), key=lambda row: row.travel_method_id)
    return (WALKING, *(Method(row.travel_method_id, row.name, row.speed_meters_per_second, row.start_price_fen,
                              row.price_per_km_fen, row.exertion, _EFFORT[row.exertion]) for row in rows))


def travel_method(catalog: Catalog, travel_method_id: str | None) -> Method:
    """@brief 按身份取一种出行方式；既不是步行也没有登记的身份是完整性故障。"""
    for method in travel_methods(catalog):
        if method.travel_method_id == travel_method_id:
            return method
    logging.error("Travel method is not registered: %s", travel_method_id)
    raise IntegrityError("Travel method is not registered")
