"""开始组的两条调用必须整组原子受理或拒绝。"""
import copy
import pytest
from tests.engine_fixture import engine_factory
from tests.life.conftest import do_at, sleep, stage_day_plan, use_app
from tests.life.environment_runtime import act_group

_SUFFIX = " Neither start request was started."


def _ordered(requests, swapped):
    """按给定先后或交换后的先后登记同一组的两条调用。"""
    return tuple(reversed(requests)) if swapped else tuple(requests)


@pytest.mark.parametrize("swapped", [False, True])
def test_location_and_app_interact_pair_starts_both_slots(engine_factory, swapped):
    """在地点做事占槽位 1、用内容 app 占槽位 2：同组的两条 interact 不再冲突，一起开始，登记先后不改变结果。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _, results = act_group(engine, "pair", *_ordered([do_at("家", "测试休息"), use_app("抖音")], swapped))
    assert [result.status for result in results] == ["in_progress", "in_progress"]
    activities = engine.context.state.activities
    assert sorted(next(iter(activities[result.activity_id].resource_claims)) for result in results) == ["first", "second"]
    assert len(activities) == 2 and len({item.started_at for item in activities.values()}) == 1


@pytest.mark.parametrize("swapped", [False, True])
def test_conflicting_sleep_pair_is_rejected_as_a_whole(engine_factory, swapped):
    """睡眠要求槽位 2 空着：与同组的副调用冲突时整组被拒，两条动作的拒绝内容相同，身体只受一次拒绝脉冲，账户不变。"""
    engine = engine_factory()
    stage_day_plan(engine)
    before = copy.deepcopy(engine.context.state.model_dump(mode="json"))
    _, [first, second] = act_group(engine, "conflict", *_ordered([sleep(), use_app("抖音")], swapped))
    assert first.status == "rejected" and first.error_code == "RESOURCE_CONFLICT"
    assert first.message == "Sleep requires the second activity slot to be empty." + _SUFFIX
    assert second == first.model_copy(update={"action_id": second.action_id})
    assert first.data["fix"] == "Submit start_main_activity alone to sleep."
    assert [item["path"][1] for item in first.data["items"]] == [0, 1]
    assert engine.context.state.activities == {}
    expected_body = {**before["agents"]["1"]["body"], "relaxation_need": 0.25}
    assert engine.context.state.agents[1].body.model_dump(mode="json") == expected_body
    assert engine.context.state.model_dump(mode="json")["accounts"] == before["accounts"]


@pytest.mark.parametrize("swapped", [False, True])
def test_unknown_target_cannot_partially_register_valid_companion(engine_factory, swapped):
    """组内一条调用的名称不存在时另一条合法的调用也不开始：两条动作得到同一份名称拒绝，明细只落在出错的那条调用上。"""
    engine = engine_factory()
    stage_day_plan(engine)
    before = copy.deepcopy(engine.context.state.model_dump(mode="json"))
    _, [first, second] = act_group(engine, "unknown", *_ordered([do_at("家", "测试休息"), use_app("不存在")], swapped))
    assert first.status == "rejected" and first.error_code == "ACTION_REQUEST_INVALID"
    assert second == first.model_copy(update={"action_id": second.action_id})
    assert first.message.endswith(_SUFFIX)
    [item] = first.data["items"]
    assert item["path"] == ["tool_calls", 0 if swapped else 1, "function", "arguments", "target", 0]
    assert (item["error_code"], item["written"]) == ("NAME_NOT_FOUND", "不存在")
    assert engine.context.state.activities == {}
    assert engine.context.state.model_dump(mode="json")["accounts"] == before["accounts"]
