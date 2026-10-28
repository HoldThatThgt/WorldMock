"""@brief 地点内容做完时结算一次的完成效果：按档位补能与一次缓解两项需要，纯函数，不读世界，不在缓冲里留下分录。

@note 数值只读两张既有的表：补能读商品类别的收货热量 `exchange/constants.py::GOODS_ENERGY_KCAL`，缓解读内容按小时
      缓解的档位 `life/interact.py::RELIEF_PER_HOUR`，由调用方 `InteractivityRuntime.settle_completion` 原样传入
      （interact 要调用本模块，本模块不能反过来引入它）；本模块不复制任何数值。补能经与食品收货共用的 `body.food_intake` 结算，
      它自己取走这一笔的分录（docs/design/04-simulation-engine.md「出行、内容与查询」）。
"""

from __future__ import annotations

from collections.abc import Mapping

from worldmock.resource_engine.catalog.models import CompletionEffects
from worldmock.exchange_engine.definitions.body_constants import RESOURCE_METAS
from worldmock.exchange_engine.definitions.constants import GOODS_ENERGY_KCAL
from worldmock.simulation_engine.body.body import (
    BODY_FLOW_OCCURRED_AT,
    BODY_FLOW_OWNER,
    RELAXATION_RESOURCE,
    SOCIAL_RESOURCE,
    food_intake,
)
from worldmock.simulation_engine.body.body_config import BodyConfig
from worldmock.state.world.agent import BodyState
from worldmock.exchange_engine.ledger.ledger_models import FlowEntry
from worldmock.exchange_engine.ledger.ledger import ResourceLedger
from worldmock.exchange_engine.ledger.ledger_models import DRAIN_DIRECTION

#: 两项缓解减少需要的分录原因。
COMPLETION_CAUSE = "content:completion"
#: 两项缓解成员与它们减少的身体资源，次序即结算次序（排在补能之后）。
RELIEF_MEMBERS = (("relaxation_relief", RELAXATION_RESOURCE), ("social_relief", SOCIAL_RESOURCE))


def settle_completion_effects(body: BodyState, effects: CompletionEffects, config: BodyConfig,
                              relief_per_hour: Mapping[str, float]) -> tuple[BodyState, dict, tuple[FlowEntry, ...]]:
    """@brief 按 food、relaxation_relief、social_relief 的次序结算声明的成员，互不影响。

    :param body: 结算前的身体。
    :param effects: 刚做完那条内容声明的完成效果。
    :param config: 身体配置，补能的上限与利用率由食品进账函数按它结算。
    :param relief_per_hour: 内容按小时缓解的档位表 `life/interact.py::RELIEF_PER_HOUR`，两项缓解一次减去其中这一档的量。
    :return: 结算后的身体；恰含三个成员键的证据（没有声明的成员为 None）；实得不为 0 的成员各一条分录，次序同上。
    """
    evidence: dict = {"food": None, "relaxation_relief": None, "social_relief": None}
    flows: list[FlowEntry] = []
    if effects.food is not None:
        body, evidence["food"], entries = _settle_food(body, effects.food, config)
        flows.extend(entries)
    for member, resource in RELIEF_MEMBERS:
        tier = getattr(effects, member)
        if tier is not None:
            body, evidence[member], entries = _settle_relief(body, relief_per_hour[tier], tier, resource)
            flows.extend(entries)
    return body, evidence, tuple(flows)


def _settle_food(body: BodyState, tier: str, config: BodyConfig) -> tuple[BodyState, dict, tuple[FlowEntry, ...]]:
    """@brief 补能的数额是同名商品类别的收货热量，经食品进账函数按此刻的利用率与能量上限结算；实得取登记的数额。"""
    provided = GOODS_ENERGY_KCAL[tier]
    changed, intake, entries = food_intake(body, provided, config)
    return changed, {"tier": tier, "provided": provided, "applied": intake["applied"],
                     "efficiency": intake["efficiency"]}, tuple(entries)


def _settle_relief(body: BodyState, provided: float, tier: str,
                   resource: str) -> tuple[BodyState, dict, tuple[FlowEntry, ...]]:
    """@brief 需要一次减去这一档缓解一小时的量 provided，不低于 0；实得为登记的数额，为 0 时没有分录。"""
    need = getattr(body, resource)
    applied = min(provided, need)
    if not applied:
        return body, {"tier": tier, "provided": provided, "applied": applied}, ()
    entry = FlowEntry(resource, BODY_FLOW_OWNER, DRAIN_DIRECTION, applied, COMPLETION_CAUSE, BODY_FLOW_OCCURRED_AT)
    final = ResourceLedger((RESOURCE_METAS[resource],)).apply([entry], {(resource, BODY_FLOW_OWNER): need})
    changed = body.model_copy(update={resource: final[(resource, BODY_FLOW_OWNER)]}, deep=True)
    return changed, {"tier": tier, "provided": provided, "applied": entry.amount}, (entry,)
