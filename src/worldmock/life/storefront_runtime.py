"""@brief 把整单购买与收货人证据接入同一次生活动作提交。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from worldmock.footprints.models import EventDraft
from worldmock.life.basket import purchase_order
from worldmock.life.models import ActionResult, ActionStatus
from worldmock.life.parcel import collect_parcels

if TYPE_CHECKING:
    from worldmock.life.engine_context import EngineContext


class StorefrontRuntime:
    """@brief 购买接线保存当前请求的收货草稿，提交前由真实结果事件建立因果。"""

    def __init__(self, context: EngineContext):
        """@brief 绑定唯一世界状态与冻结购买规则。"""
        self.context = context
        self.pending_receipts: list[EventDraft] = []

    def _purchase(self, state, pending, request):
        """@brief 一次付款并记录真实订单，收货草稿归收货人而非付款人。"""
        agent = state.agents[pending.agent_id]
        outcome = purchase_order(state, self.context.semantics, pending.agent_id, request)
        self.pending_receipts.extend(outcome.receipts)
        seller = f"merchant:{request.items[0].store_id}"
        result = ActionResult(action_id=pending.action_id, agent_id=pending.agent_id,
            status=ActionStatus.COMPLETED, occurred_at=state.current_time, message="Purchase accepted",
            data={"orders": [order.model_dump(mode="json") for order in outcome.orders],
                  "total_price_fen": outcome.total_price_fen,
                  "resource_changes": {f"account:{agent.balance_account_id}": -outcome.total_price_fen,
                                       f"account:{seller}": outcome.total_price_fen}})
        return outcome.state, result

    def drain_receipts(self) -> list[EventDraft]:
        """@brief 取走当前请求的收货草稿；成功或拒绝后均须调用以免串单。"""
        receipts = self.pending_receipts
        self.pending_receipts = []
        return receipts

    def _collect_parcels(self, state, pending, station_id):
        """@brief 零秒领取不付款、不建活动，真实收货草稿同次提交绑定本人结果。"""
        outcome = collect_parcels(state, self.context.semantics, pending.agent_id, station_id)
        self.pending_receipts.extend(outcome.receipts)
        result = ActionResult(action_id=pending.action_id, agent_id=pending.agent_id,
            status=ActionStatus.COMPLETED, occurred_at=state.current_time, message="Parcels collected",
            data={"orders": [order.model_dump(mode="json") for order in outcome.orders]})
        return outcome.state, result
