"""整份世界深复制的结构共享：活动与已处理动作换新容器、共享条目，其余字段完整深复制。

条目是不可变值（docs/design/09-io-formats.md「世界、人物与活动」）；条目对象的同一性就是这项省时的约定，
所以本文件按对象身份断言。
"""

import copy

from tests.life.conftest import travel
from tests.life.environment_runtime import act
from worldmock.foundation.serialization import canonical_json


def _walking(engine_factory):
    """走向公司之后的真实世界：至少有一项活动、一条已处理动作、一段会话和一个预排事件。"""
    engine = engine_factory()
    act(engine, "walk", travel("公司"))
    state = engine.context.state
    assert state.activities and state.processed_actions and state.sessions and state.pending_events
    return state


def _bytes(state) -> bytes:
    """世界的规范 JSON 字节。"""
    return canonical_json(state.model_dump(mode="json"))


def test_deep_copy_gives_fresh_entry_containers_that_share_the_entries(engine_factory):
    """深复制换新的活动与已处理动作字典，字典里是同一批条目对象、次序不变，两份世界的规范字节相同。"""
    state = _walking(engine_factory)
    copied = state.model_copy(deep=True)
    assert copied.activities is not state.activities
    assert copied.processed_actions is not state.processed_actions
    assert all(copied.activities[key] is entry for key, entry in state.activities.items())
    assert all(copied.processed_actions[key] is entry for key, entry in state.processed_actions.items())
    assert list(copied.activities) == list(state.activities)
    assert list(copied.processed_actions) == list(state.processed_actions)
    assert _bytes(copied) == _bytes(state)


def test_every_other_field_is_a_full_deep_copy(engine_factory):
    """人物、身体、会话与预排事件都是新对象；改副本的人物地点与账户不动原世界的任何一个字节。"""
    state = _walking(engine_factory)
    before = _bytes(state)
    copied = state.model_copy(deep=True)
    assert copied.agents[1] is not state.agents[1]
    assert copied.agents[1].body is not state.agents[1].body
    assert all(copied.sessions[key] is not entry for key, entry in state.sessions.items())
    assert all(item is not original for item, original in zip(copied.pending_events, state.pending_events,
                                                              strict=True))
    copied.agents[1].location_id = "x"
    copied.accounts["agent:1"] = 0
    assert _bytes(state) == before


def test_update_copy_and_copy_module_share_entries_and_leave_the_original_unchanged(engine_factory):
    """带更新的深复制与 copy.deepcopy 同样共享条目；原世界不变，更新只落在副本上。"""
    state = _walking(engine_factory)
    before = _bytes(state)
    updated = state.model_copy(update={"current_time": state.current_time + 1}, deep=True)
    for copied in (updated, copy.deepcopy(state)):
        assert all(copied.activities[key] is entry for key, entry in state.activities.items())
        assert all(copied.processed_actions[key] is entry for key, entry in state.processed_actions.items())
    assert updated.current_time == state.current_time + 1
    assert _bytes(state) == before


def test_copying_one_entry_still_copies_its_nested_data(engine_factory):
    """复制单个共享条目仍是完整深复制：带更新复制出新对象和新的 data，已提交世界里的那个条目不变。"""
    state = _walking(engine_factory)
    copied = state.model_copy(deep=True)
    key = next(iter(copied.activities))
    entry, name = copied.activities[key], state.activities[key].name
    changed = entry.model_copy(update={"name": entry.name + "x"}, deep=True)
    assert changed is not entry
    assert changed.data is not entry.data
    assert state.activities[key].name == name
