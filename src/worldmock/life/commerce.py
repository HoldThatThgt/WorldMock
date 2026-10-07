"""@brief 配送到期委托唯一收货结算，不维护持有或库存。"""

from __future__ import annotations

from worldmock.life.models import WorldState
from worldmock.life.receipt import ReceiptOutcome, settle_receipt
from worldmock.life.semantics import ScenarioSemantics


def deliver_order(state: WorldState, semantics: ScenarioSemantics, order_id: str) -> ReceiptOutcome:
    """@brief 到期配送与到店购买使用同一幂等收货入口。"""
    return settle_receipt(state, semantics, order_id)
