"""实际世界在初始写入和后续提交前复校时钟、账户、人物身份及驾车日余额。"""

from __future__ import annotations

import pytest

from tests.life.conftest import context
from tests.life.purchase_worlds import registered_world
from worldmock.errors import IntegrityError
from worldmock.footprints.models import EventDraft
from worldmock.footprints.store import FootprintStore
from worldmock.life.models import WorldState, validated_payload

NAMES = ("time_exceeds_end", "negative_account", "body_ahead_of_world", "agent_identity_mismatch",
         "negative_driving_days")


def _world_state() -> WorldState:
    """从真实登记环境形成完整、已校验的初始纯世界。"""
    return registered_world()[1]


def _drifted(name: str) -> WorldState:
    """明确利用属性改写绕过构造校验，复现运行中的漂移状态。"""
    state = _world_state().model_copy(deep=True)
    state.version += 1
    if name == "time_exceeds_end":
        state.current_time = state.end_time + 1
    elif name == "negative_account":
        state.accounts["agent:1"] = -1
    elif name == "body_ahead_of_world":
        state.agents[1].body_settled_at = state.current_time + 1
    elif name == "agent_identity_mismatch":
        state.agents[1].agent_id = 2
    elif name == "negative_driving_days":
        state.agents[1].driving_days = -1
    else:
        raise ValueError("Unknown invariant probe")
    return state


def _draft(state: WorldState) -> list[EventDraft]:
    """与被复校世界同批提交的环境事件。"""
    return [EventDraft(event_type="environment_event", occurred_at=state.current_time,
                       visibility="system", data={"kind": "probe"})]


@pytest.mark.parametrize("name", NAMES)
def test_commit_choke_point_rejects_every_domain_invariant_before_persisting(name, tmp_path):
    """调用生产提交路径同一 validated_payload，坏状态不能进入足迹库。"""
    initial = _world_state().model_dump(mode="json")
    store = FootprintStore.create(tmp_path / "run", {"run_id": "r1", "agent_ids": [1]}, initial)
    try:
        drifted = _drifted(name)
        with pytest.raises(IntegrityError):
            store.commit(validated_payload(drifted), _draft(drifted))
        assert store.state == initial
        assert store.last_event_seq == 0
    finally:
        store.close()


@pytest.mark.parametrize("name", NAMES)
def test_create_initial_persist_rejects_every_domain_invariant(name):
    """初始发布使用同一复校口径。"""
    with pytest.raises(IntegrityError):
        validated_payload(_drifted(name))


def test_engine_write_path_rejects_drifted_state(engine_factory):
    """真实 observe 提交路径拒绝负的驾车日余额，已提交的世界版本不前移。"""
    engine = engine_factory()
    staged = engine.context.state.model_copy(deep=True)
    staged.agents[1].driving_days = -1
    staged.version += 1
    engine.context.state = staged
    with pytest.raises(IntegrityError):
        engine.observe(context(engine, "write-recheck"))
    assert engine.context.store.state["version"] == staged.version - 1
