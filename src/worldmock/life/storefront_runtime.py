"""@brief 将门店购买、真实到达和活动环境校验接入生活引擎。"""

from __future__ import annotations

import logging

from worldmock.life.engine_context import EngineContext
from worldmock.errors import BusinessRuleError
from worldmock.life import projection
from worldmock.life.basket import purchase_order
from worldmock.life.models import ActionResult, ActionStatus

_LOGGER = logging.getLogger(__name__)


class StorefrontRuntime:
    """@brief 门店动作的引擎接线，领域结算继续使用统一整单购买入口。"""

    def __init__(self, context: EngineContext):
        """@brief 绑定唯一世界状态与冻结购买规则。"""
        self.context = context

    def _purchase(self, state, pending, request, named: frozenset[str] = frozenset(), channels: dict | None = None):
        """@brief 购买人物实际接触过的供给或指名购买解析出的供给，逐项校验后一次付款。

        :param named: 指名购买按名称解析出的供给身份：指名路径不查可见凭据，浏览与轮转本身不变
            （docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第五章「指名购买」）。
        :param channels: （供给身份，收货人身份）→ from 解析出的渠道 channel_id：结果的每个订单载荷各记一列 channel_id（不是
            经渠道买的为 null），不进世界状态的 DeliveryState（
            docs/dev/SPEC-map-and-storefront.md#dayrun-fidelity-按名调用与指名购买-购买渠道app-名解析为成员门店  解析规则 4）。
        """
        agent = state.agents[pending.agent_id]
        effective = set(projection.visible_supplies(state, agent, self.context.scenario)) | named
        if any(item.supply_id not in effective for item in request.items):
            _LOGGER.error("Purchase references a supply that was not visible to the agent")
            raise BusinessRuleError("SUPPLY_NOT_VISIBLE")
        outcome = purchase_order(state, self.context.semantics, pending.agent_id, request)
        seller = self.context.rules.supplies[request.items[0].supply_id].seller_account_id
        routed = channels or {}
        result = ActionResult(
            action_id=pending.action_id,
            agent_id=pending.agent_id,
            status=ActionStatus.COMPLETED,
            occurred_at=state.current_time,
            message="Purchase accepted",
            data={
                "orders": [
                    {**order.model_dump(mode="json"), "channel_id": routed.get((order.supply_id, order.recipient_id))}
                    for order in outcome.orders
                ],
                "total_price_fen": outcome.total_price_fen,
                "inventory_changes": list(outcome.inventory_changes),
                "category_matches": list(outcome.category_matches),
                "resource_changes": {
                    f"account:{agent.balance_account_id}": -outcome.total_price_fen,
                    f"account:{seller}": outcome.total_price_fen,
                },
            },
        )
        return outcome.state, result
