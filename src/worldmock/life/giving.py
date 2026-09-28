"""@brief 给予：把手边的持有物交给人物（docs/dev/SPEC-persona-prompt.md 第五章「来源与去向」）。

给予是瞬时动作，不占名额，任何人都不增加摄入：从本人手边移出每件 min(1, 持有)，记入对方持有（同世界人物入其同一地点的
库存，场景联系人入 contact_holdings）。要求同处：同世界人物与本人在同一地点，联系人则本人位于其收货地点。内部标准动作
仍是 consume（方式「给予」），菜单、可供性目录与品类配对不变。拒绝：手边没有用 PRODUCT_NOT_HELD；不同处用
ACTIVITY_PRECONDITION_UNMET（unmet=recipient_present）；收件人不存在的 RECIPIENT_NOT_FOUND 由 routing 在此之前判定。
"""

from __future__ import annotations

import logging

from worldmock.life.admission import present_location
from worldmock.life.commerce import add_holding
from worldmock.life.guidance import Refusal, refusal_for_code
from worldmock.life.models import WorldState
from worldmock.life.recipients import person_name, recipient_location
from worldmock.life.scenario import RuntimeScenario

#: 一次给予每件至多交出的份数。
GIVE_PORTION_LIMIT = 1.0


def give_quantity(held: float) -> float:
    """@brief 一次给予交出的份数：min(1, 持有)。"""
    return min(GIVE_PORTION_LIMIT, held)


def held_refusal(state: WorldState, agent_id: int, product_id: str) -> Refusal | None:
    """@brief 手边（此刻所在地点）没有这件东西时为 PRODUCT_NOT_HELD，否则为 None。"""
    present = present_location(state, agent_id)
    held = state.agents[agent_id].inventory.get(present, {}).get(product_id, 0) if present is not None else 0
    return refusal_for_code("PRODUCT_NOT_HELD") if held <= 1e-9 else None


def give(state: WorldState, scenario: RuntimeScenario, agent_id: int, binding: dict) -> tuple[WorldState, dict]:
    """@brief 把绑定行所指的手边持有物交给其去向（routing.routed_selection 写上的 recipient_id）。

    :returns: （新世界，给予记录 {"product_id", "quantity", "recipient_id", "location_id"}）。
    :raises PreconditionError: PRODUCT_NOT_HELD 或 ACTIVITY_PRECONDITION_UNMET（不同处）。
    """
    product_id, recipient = binding["object_id"], binding["recipient_id"]
    refusal = held_refusal(state, agent_id, product_id) or _presence_refusal(state, scenario, agent_id, recipient)
    if refusal is not None:
        logging.error("Giving was refused: %s", refusal.code)
        raise refusal.error()
    present = present_location(state, agent_id)
    agent = state.agents[agent_id]
    quantity = give_quantity(agent.inventory[present][product_id])
    inventory = {key: dict(value) for key, value in agent.inventory.items()}
    inventory[present][product_id] = round(inventory[present][product_id] - quantity, 9)
    changed = state.model_copy(update={"agents": {**state.agents, agent_id: agent.model_copy(
        update={"inventory": inventory}, deep=True)}}, deep=True)
    changed = add_holding(changed, recipient, present, (product_id, quantity))
    return changed, {"product_id": product_id, "quantity": quantity, "recipient_id": recipient,
                     "location_id": present}


def _presence_refusal(state: WorldState, scenario: RuntimeScenario, agent_id: int, recipient: str) -> Refusal | None:
    """@brief 要和对方同处：同世界人物在本人此刻所在地点，联系人的收货地点就是本人此刻所在地点。"""
    where = recipient_location(state, scenario, agent_id, recipient)
    if where is not None and where == present_location(state, agent_id):
        return None
    name = person_name(state, scenario, agent_id, recipient)
    places = {item.location_id: item.name for item in scenario.locations}
    step = f"去{places[where]}" if where is not None else f"等和{name}在一起时再给"
    return Refusal("ACTIVITY_PRECONDITION_UNMET", f"要和{name}在同一个地方才能给他", step, ("recipient_present",))
