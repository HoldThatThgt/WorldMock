"""@brief 按职责保存现有定义与纯计算。"""


from __future__ import annotations
import logging
from collections.abc import Mapping
from typing import Any, NamedTuple
from worldmock.foundation.errors import IntegrityError

_LOGGER = logging.getLogger(__name__)


class Evidence(NamedTuple):
    """@brief 本人的收货证据：事务所有人、商品身份与本次取得的份数。"""

    owner_id: int
    product_id: str
    quantity: float



def evidence_rows(event_type: str, agent_id: int | None, data: Mapping[str, Any]) -> list[Evidence]:
    """@brief 完成依据只读本人 goods_received，订单、自述与旧进食证据不推进事务。"""
    if event_type != "environment_event" or data.get("kind") != "goods_received":
        return []
    if type(agent_id) is not int or agent_id <= 0:
        _LOGGER.error("Goods receipt evidence has no simulated recipient")
        raise IntegrityError("Goods receipt evidence has no simulated recipient")
    return [Evidence(agent_id, data["goods_id"], float(data["quantity"]))]
