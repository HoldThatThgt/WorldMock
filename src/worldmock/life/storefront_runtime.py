"""@brief 将门店购买、真实到达和活动环境校验接入生活引擎。"""

from __future__ import annotations

import logging

from worldmock.life.engine_context import EngineContext
from worldmock.errors import BusinessRuleError
from worldmock.life.basket import purchase_order
from worldmock.life.models import ActionResult, ActionStatus

_LOGGER = logging.getLogger(__name__)


class StorefrontRuntime:
    """@brief 门店动作的引擎接线，领域结算继续使用统一整单购买入口。"""

    def __init__(self, context: EngineContext):
        """@brief 绑定唯一世界状态与冻结购买规则。"""
        self.context = context

    def _purchase(self, state, pending, request):
        """@brief 实际目录食品整单一次付款，结果原样记录真实食品、店铺及 app 身份。"""
        agent = state.agents[pending.agent_id]
        outcome = purchase_order(state, self.context.semantics, pending.agent_id, request)
        seller = f"merchant:{request.items[0].store_id}"
        result = ActionResult(action_id=pending.action_id, agent_id=pending.agent_id,
            status=ActionStatus.COMPLETED, occurred_at=state.current_time, message="Purchase accepted",
            data={"orders": [order.model_dump(mode="json") for order in outcome.orders],
                  "total_price_fen": outcome.total_price_fen,
                  "inventory_changes": list(outcome.inventory_changes),
                  "resource_changes": {f"account:{agent.balance_account_id}": -outcome.total_price_fen,
                                       f"account:{seller}": outcome.total_price_fen}})
        return outcome.state, result
