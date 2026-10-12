"""@brief 地点内容做完时结算一次的完成效果：按档位补能与一次缓解两项需要，纯函数，不读世界、不写缓冲。

@note 数值只读两张既有的表：补能读商品类别的收货热量 `GOODS_ENERGY_KCAL`，缓解读内容按小时缓解的档位
      `interact.RELIEF_PER_HOUR`；本模块不复制任何数值。
"""

from __future__ import annotations

from collections.abc import Callable

from worldmock.catalog.models import CompletionEffects
from worldmock.exchange.body import RESOURCE_METAS
from worldmock.exchange.constants import GOODS_ENERGY_KCAL
from worldmock.life.body import BODY_FLOW_OCCURRED_AT, BODY_FLOW_OWNER, RELAXATION_RESOURCE, SOCIAL_RESOURCE
from worldmock.life.body_config import BodyConfig
from worldmock.life.models import BodyState
from worldmock.resources import FlowEntry, ResourceLedger
from worldmock.resources.models import DRAIN_DIRECTION

#: 两项缓解减少需要的分录原因。
COMPLETION_CAUSE = "content:completion"
#: 两项缓解成员与它们减少的身体资源，次序即结算次序（排在补能之后）。
RELIEF_MEMBERS = (("relaxation_relief", RELAXATION_RESOURCE), ("social_relief", SOCIAL_RESOURCE))
#: 食品进账函数：输入身体、热量与身体配置，返回进账后的身体、{provided, applied, efficiency} 与这一笔自己的分录。
FoodIntake = Callable[[BodyState, float, BodyConfig], tuple[BodyState, dict, tuple[FlowEntry, ...]]]


def settle_completion_effects(body: BodyState, effects: CompletionEffects, config: BodyConfig,
                              food: FoodIntake) -> tuple[BodyState, dict, tuple[FlowEntry, ...]]:
    """@brief 按 food、relaxation_relief、social_relief 的次序结算声明的成员，互不影响。

    :param body: 结算前的身体。
    :param effects: 刚做完那条内容声明的完成效果。
    :param config: 身体配置，补能的上限与利用率由食品进账函数按它结算。
    :param food: 食品进账函数，与食品收货共用的那一个；它自己取走这一笔的分录。
    :return: 结算后的身体；恰含三个成员键的证据（没有声明的成员为 None）；实得不为 0 的成员各一条分录，次序同上。
    """
    evidence: dict = {"food": None, "relaxation_relief": None, "social_relief": None}
    flows: list[FlowEntry] = []
    if effects.food is not None:
        body, evidence["food"], entries = _settle_food(body, effects.food, config, food)
        flows.extend(entries)
    for member, resource in RELIEF_MEMBERS:
        tier = getattr(effects, member)
        if tier is not None:
            body, evidence[member], entries = _settle_relief(body, tier, resource)
            flows.extend(entries)
    return body, evidence, tuple(flows)


def _settle_food(body: BodyState, tier: str, config: BodyConfig,
                 food: FoodIntake) -> tuple[BodyState, dict, tuple[FlowEntry, ...]]:
    """@brief 补能的数额是同名商品类别的收货热量，经食品进账函数按此刻的利用率与能量上限结算；实得取登记的数额。"""
    provided = GOODS_ENERGY_KCAL[tier]
    changed, intake, entries = food(body, provided, config)
    return changed, {"tier": tier, "provided": provided, "applied": intake["applied"],
                     "efficiency": intake["efficiency"]}, tuple(entries)


def _settle_relief(body: BodyState, tier: str, resource: str) -> tuple[BodyState, dict, tuple[FlowEntry, ...]]:
    """@brief 需要一次减去这一档缓解一小时的量，不低于 0；实得为登记的数额，为 0 时没有分录。"""
    # 档位表的唯一出处在 interact，而 interact 结算完成效果时导入本模块，所以在这里延迟导入。
    from worldmock.life.interact import RELIEF_PER_HOUR

    provided = RELIEF_PER_HOUR[tier]
    need = getattr(body, resource)
    applied = min(provided, need)
    if not applied:
        return body, {"tier": tier, "provided": provided, "applied": applied}, ()
    entry = FlowEntry(resource, BODY_FLOW_OWNER, DRAIN_DIRECTION, applied, COMPLETION_CAUSE, BODY_FLOW_OCCURRED_AT)
    final = ResourceLedger((RESOURCE_METAS[resource],)).apply([entry], {(resource, BODY_FLOW_OWNER): need})
    changed = body.model_copy(update={resource: final[(resource, BODY_FLOW_OWNER)]}, deep=True)
    return changed, {"tier": tier, "provided": provided, "applied": entry.amount}, (entry,)
