"""引擎写入路径域不变量复校（P0-2）与 taste_weights 运行时接线（docs/dev/SPEC-life-realism.md「动机层」「世界素材品类要求」）。

先红后绿：修复前 LifeEngine 的 16 处 store.commit 与 create 初始落盘不重跑
WorldState.validate_world，五条不变量可被落盘（C6 探针实测 create 5/5、
commit 4/5，负账户已被存储账本校验顺带拦截）；修复后全部在写入前转为
IntegrityError。taste_weights 接线：_motivation_context 此前硬编码空映射，
即使人物持久化口味权重（AgentState.taste_weights，模型字段）也被丢弃。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_state_serialization import _world_state  # noqa: E402

from tests.life.conftest import context  # noqa: E402
from worldmock.errors import IntegrityError  # noqa: E402
from worldmock.footprints.models import EventDraft  # noqa: E402
from worldmock.footprints.store import FootprintStore  # noqa: E402
from worldmock.life import state_commit  # noqa: E402
from worldmock.life.models import WorldState  # noqa: E402


def _violations() -> dict[str, dict]:
    """五条域不变量各自的最小违规载荷（与 models.validate_world 一一对应）。"""
    base = _world_state().model_dump(mode="json")
    agent = base["agents"]["1"]
    return {
        "time_exceeds_end": {"current_time": 11},
        "negative_account": {"accounts": {"agent:1": -1}},
        "negative_supply_stock": {"supply_stock": {"m1": -1}},
        "agent_identity_mismatch": {"agents": {1: {**agent, "agent_id": 2}}},
        "negative_inventory": {"agents": {1: {**agent, "inventory": {"fridge": {"milk": -1}}}}},
    }


def _drifted(name: str) -> WorldState:
    """构造带单条不变量违规的漂移状态（版本前移模拟结算路径改写）。

    以 model_construct 绕过构造期校验，复现 model_copy+属性赋值路径
    （该路径不重跑 pydantic 校验，正是 P0-2 的写入缺口机制）。
    """
    base = _world_state().model_dump(mode="json")
    return WorldState.model_construct(
        **{**base, **_violations()[name], "version": base["version"] + 1})


def _draft(state: WorldState) -> list[EventDraft]:
    """与状态同批提交的最小环境事件草稿。"""
    return [EventDraft(event_type="environment_event", occurred_at=state.current_time,
                       visibility="system", data={"kind": "probe"})]


@pytest.mark.parametrize("name", sorted(_violations()))
def test_commit_choke_point_rejects_every_domain_invariant_before_persisting(name, tmp_path):
    """五条不变量 × commit_state：写入前全部 IntegrityError，足迹库零落盘。"""
    store = FootprintStore.create(
        tmp_path / "run", {"run_id": "r1", "agent_ids": [1], "code": "f"},
        _world_state().model_dump(mode="json"))
    drifted = _drifted(name)
    with pytest.raises(IntegrityError):
        state_commit.commit_state(store, drifted, _draft(drifted))
    assert store.state["version"] == _world_state().model_dump(mode="json")["version"]
    store.close()


@pytest.mark.parametrize("name", sorted(_violations()))
def test_create_initial_persist_rejects_every_domain_invariant(name):
    """五条不变量 × validated_payload：create 初始落盘路径同样复校。"""
    bad = _violations()[name]
    base = _world_state().model_dump(mode="json")
    with pytest.raises(IntegrityError):
        state_commit.validated_payload(WorldState.model_construct(**{**base, **bad}))


def test_engine_write_path_rejects_drifted_state(engine_factory):
    """引擎 observe 写入路径：漂移状态在提交前被拒（修复前此处静默落盘）。"""
    engine = engine_factory()
    staged = engine.state.model_copy(deep=True)
    staged.supply_stock[next(iter(staged.supply_stock), "m1")] = -1
    staged.version = engine.state.version + 1
    engine.state = staged
    with pytest.raises(IntegrityError):
        engine.observe(context(engine, "write-recheck"))
    assert engine.store.state["version"] == staged.version - 1


def test_motivation_context_uses_persisted_taste_weights(engine_factory):
    """正例：人物持久化 taste_weights 进入 MotivationContext（接线后不再丢弃）。"""
    engine = engine_factory()
    agent = engine.state.agents[1]
    weights = {"movie-0001": 0.75}
    staged = engine.state.model_copy(deep=True)
    staged.agents[1] = agent.model_copy(update={"taste_weights": weights}, deep=True)
    staged.version = engine.state.version + 1
    engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time,
        visibility="system", data={"kind": "test_seed_taste"})])
    engine.state = staged

    profile = next(item.motivation for item in engine.scenario.agents
                   if item.agent_id == 1 and item.motivation is not None)
    wired = engine._motivation_context(engine.state.agents[1], profile)

    assert dict(wired.taste_weights) == weights


def test_motivation_context_without_persisted_weights_stays_empty(engine_factory):
    """反例：无持久化权重时保持空映射（空=不抬升，不做缺省抬升）。"""
    engine = engine_factory()
    profile = next(item.motivation for item in engine.scenario.agents
                   if item.agent_id == 1 and item.motivation is not None)
    wired = engine._motivation_context(engine.state.agents[1], profile)

    assert dict(wired.taste_weights) == {}
