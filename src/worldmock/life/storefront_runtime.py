"""@brief 将门店购买、真实到达和活动环境校验接入生活引擎。"""

from __future__ import annotations

import logging

from worldmock.errors import BusinessRuleError
from worldmock.life import projection
from worldmock.life.basket import purchase_order
from worldmock.life.models import ActionResult, ActionStatus, ActivityStatus

_LOGGER = logging.getLogger(__name__)


class StorefrontRuntimeMixin:
    """@brief 门店动作的引擎接线，领域结算继续使用统一整单购买入口。"""

    def _purchase(self, state, pending, request, named: frozenset[str] = frozenset(),
                  channels: dict | None = None):
        """@brief 购买人物实际接触过的供给或指名购买解析出的供给，逐项校验后一次付款。

        :param named: 指名购买按名称解析出的供给身份：指名路径不查可见凭据，浏览与轮转本身不变
            （docs/dev/SPEC-persona-prompt.md 第五章「指名购买」）。
        :param channels: （供给身份，收货人身份）→ from 解析出的渠道 channel_id：结果的每个订单载荷各记一列 channel_id（不是
            经渠道买的为 null），不进世界状态的 DeliveryState（docs/dev/SPEC-dayrun-fidelity.md 3.3 解析规则 4）。
        """
        agent = state.agents[pending.agent_id]
        effective = set(projection.visible_supplies(state, agent, self.scenario)) | named
        if any(item.supply_id not in effective for item in request.items):
            _LOGGER.error("Purchase references a supply that was not visible to the agent")
            raise BusinessRuleError("SUPPLY_NOT_VISIBLE")
        outcome = purchase_order(state, self.semantics, pending.agent_id, request)
        seller = self.rules.supplies[request.items[0].supply_id].seller_account_id
        routed = channels or {}
        result = ActionResult(
            action_id=pending.action_id, agent_id=pending.agent_id, status=ActionStatus.COMPLETED,
            occurred_at=state.current_time, message="Purchase accepted",
            data={"orders": [{**order.model_dump(mode="json"),
                              "channel_id": routed.get((order.supply_id, order.recipient_id))}
                             for order in outcome.orders],
                  "total_price_fen": outcome.total_price_fen,
                  "inventory_changes": list(outcome.inventory_changes),
                  "category_matches": list(outcome.category_matches),
                  "resource_changes": {f"account:{agent.balance_account_id}": -outcome.total_price_fen,
                                       f"account:{seller}": outcome.total_price_fen}})
        return outcome.state, result


def record_arrival_access(activity, agent, scenario) -> None:
    """@brief 到达保留位置事实并重新检查进入资格，失败不撤销已完成的旅行。"""
    if activity.status != ActivityStatus.COMPLETED:
        return
    location = next(item for item in scenario.locations if item.location_id == agent.location_id)
    missing = sorted(set(location.required_qualifications) - set(agent.qualifications))
    activity.data["arrival"] = {"location_id": agent.location_id,
                                "access_status": "denied" if missing else "allowed",
                                "missing_qualifications": missing,
                                "source": location.source.model_dump(mode="json")}
    if missing:
        _LOGGER.error("Arrival access denied for location %s", location.location_id)
        activity.status = ActivityStatus.FAILED
        activity.interruption_reason = "LOCATION_ACCESS_DENIED"
