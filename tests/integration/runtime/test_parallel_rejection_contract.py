"""当前槽位的并行动作必须整组原子受理或拒绝。"""
import copy
import pytest
from tests.engine_fixture import engine_factory
from tests.life.conftest import stage_day_plan, start_request
from tests.life.environment_runtime import act


@pytest.mark.parametrize("targets", [["家", "抖音"], ["抖音", "家"]])
def test_conflicting_interact_pair_is_rejected_as_a_whole(engine_factory, targets):
    engine = engine_factory()
    stage_day_plan(engine)
    before = copy.deepcopy(engine.context.state.model_dump(mode="json"))
    _, result = act(engine, "conflict", start_request(targets=targets, actions=["interact", "interact"]))
    assert result.status == "rejected" and result.error_code == "RESOURCE_CONFLICT"
    assert engine.context.state.activities == {}
    expected_body = {**before["agents"]["1"]["body"], "relaxation_need": 0.25}
    assert engine.context.state.agents[1].body.model_dump(mode="json") == expected_body
    assert engine.context.state.model_dump(mode="json")["accounts"] == before["accounts"]


def test_unknown_target_cannot_partially_register_valid_companion(engine_factory):
    engine = engine_factory()
    stage_day_plan(engine)
    _, result = act(engine, "unknown", start_request(targets=["家", "不存在"], actions=["interact", "interact"]))
    assert result.status == "rejected"
    assert engine.context.state.activities == {}
