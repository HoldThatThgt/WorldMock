"""@brief 真实食品给予只移动持有，不增加身体能量；失败保持原子。"""

import pytest
from worldmock.errors import BusinessRuleError
from worldmock.life.giving import give
from worldmock.life.models import WorldState
from tests.life.purchase_worlds import ENVIRONMENT, registered_world


def giving_world(other_place="home"):
    semantics, state = registered_world(scenario_path=ENVIRONMENT / "scenarios/multi-person.json")
    other = next(identity for identity in state.agents if identity != 1)
    agents = {**state.agents, 1: state.agents[1].model_copy(update={"inventory": {"home": {"chips": 1.5}}}),
              other: state.agents[other].model_copy(update={"location_id": other_place, "inventory": {}})}
    return semantics, WorldState.model_validate(state.model_copy(update={"agents": agents}).model_dump()), other


def test_giving_moves_only_actual_food_portion_and_does_not_feed_anyone():
    semantics, state, other = giving_world()
    energies = {identity: agent.body.energy_kcal for identity, agent in state.agents.items()}
    changed, row = give(state, semantics.scenario, 1, {"object_id": "chips", "recipient_id": f"agent:{other}"})
    assert row == {"food_id": "chips", "quantity": 1.0, "recipient_id": f"agent:{other}", "location_id": "home"}
    assert changed.agents[1].inventory["home"]["chips"] == 0.5
    assert changed.agents[other].inventory["home"]["chips"] == 1
    assert {identity: agent.body.energy_kcal for identity, agent in changed.agents.items()} == energies
    assert not changed.activities and state.agents[1].inventory["home"]["chips"] == 1.5


def test_giving_to_absent_recipient_is_atomic_without_retired_unmet():
    semantics, state, other = giving_world("park")
    before = state.model_dump_json()
    with pytest.raises(BusinessRuleError, match="ACTIVITY_PRECONDITION_UNMET") as failure:
        give(state, semantics.scenario, 1, {"object_id": "chips", "recipient_id": f"agent:{other}"})
    assert failure.value.unmet == []
    assert state.model_dump_json() == before
