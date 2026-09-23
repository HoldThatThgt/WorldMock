"""真实场景观察的人物可见信息与私有动作绑定合同。"""

from __future__ import annotations

import json

import pytest

from worldmock.life.narration import render_facts


@pytest.mark.parametrize("scenario_name", [
    "white-collar-12-weeks.json", "multi-person.json", "multi-person-half-day.json",
])
def test_real_observation_uses_readable_objects_and_private_bindings(engine_factory, scenario_name: str) -> None:
    """@brief 人物只见语义名称；引擎在同一观察里保留独立的身份绑定。"""
    engine = engine_factory(scenario_name)
    agent_id = engine.scenario.agents[0].agent_id
    observed = engine._observation_data(engine.state.agents[agent_id])
    facts = observed["facts"]
    public = facts["interactive_objects"]
    bindings = observed["interaction_bindings"]

    assert public
    assert len(public) == len(bindings)
    assert [row["object"] for row in public] == [row["object"] for row in bindings]
    assert len({row["object"] for row in public}) == len(public)
    assert all(set(row) == {"object", "actions", "preconditions_met"} for row in public)
    assert all("object_id" in row for row in bindings)
    assert set(facts["progress"]) == {"sessions", "results", "alarms"}

    text = render_facts(facts)
    encoded = json.loads(text)
    assert encoded == facts
    for hidden in ("object_id", "session_id", "task_id", "product_id", "supply_id",
                   "price_version", "recent_action_results"):
        assert f'"{hidden}"' not in text
    for task in engine.scenario.tasks:
        assert task.task_id not in text
