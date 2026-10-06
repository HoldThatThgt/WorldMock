"""LifeEngine 反事实分支干预边界测试。"""

from __future__ import annotations

from tests.data_paths import ENVIRONMENT_ROOT
from worldmock.footprints.checkpoint import restore_branch

import hashlib
import json
from pathlib import Path

import pytest

from tests.life.conftest import context
from worldmock.errors import ProtocolError
from worldmock.footprints.assets import atomic_write
from worldmock.footprints.models import canonical_json
from worldmock.life.engine import LifeEngine
from worldmock.life.models import CounterfactualIntervention


def _digest(intervention: CounterfactualIntervention) -> str:
    """复现公开动作身份编码。"""
    payload = {"tool_name": "counterfactual_intervention",
               "request": intervention.model_dump(mode="json")}
    content = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode()
    return hashlib.sha256(content).hexdigest()


def _source_event(engine, agent_id: int) -> str:
    """写入并返回目标人物实际可见的观察事件。"""
    engine.observe(context(engine, f"source-{agent_id}", agent_id))
    return engine.context.store.read_events(engine.context.store.last_event_seq)[-1].event_id


def _branch(engine, destination: Path, intervention: CounterfactualIntervention,
            manifest_updates: dict | None = None) -> LifeEngine:
    """从共同检查点通过生产接口恢复反事实分支。"""
    snapshots = {}
    for agent_id in engine.context.store.manifest["agent_ids"]:
        snapshots[f"agent:{agent_id}"] = engine.context.store.assets.put_json(
            {"agent_id": agent_id}, {"kind": "counterfactual_test"})
    checkpoint = engine.checkpoint(snapshots)
    metadata = {"branch_kind": "counterfactual",
                "intervention_sha256": _digest(intervention)}
    store = restore_branch(engine.context.store, checkpoint.checkpoint_id, destination, metadata)
    store.close()
    if manifest_updates:
        path = destination / "manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest.update(manifest_updates)
        atomic_write(path, canonical_json(manifest))
    scenario = (ENVIRONMENT_ROOT / "scenarios") / ("multi-person.json" if len(engine.context.state.agents) > 1
                                    else "single-person.json")
    return LifeEngine.open(scenario, destination)


def _plain_branch(engine, destination: Path) -> LifeEngine:
    """建立一次普通恢复分支以验证嵌套来源截止。"""
    snapshots = {f"agent:{agent_id}": engine.context.store.assets.put_json(
        {"agent_id": agent_id}, {"kind": "nested_counterfactual_test"})
        for agent_id in engine.context.store.manifest["agent_ids"]}
    checkpoint = engine.checkpoint(snapshots)
    store = restore_branch(engine.context.store, checkpoint.checkpoint_id, destination)
    store.close()
    scenario = ENVIRONMENT_ROOT / "scenarios/single-person.json"
    return LifeEngine.open(scenario, destination)


def _intervention(source_event_id: str, agent_id: int = 1) -> CounterfactualIntervention:
    """形成一个由人物可见证据支持的余额假设。"""
    return CounterfactualIntervention(kind="set_balance_fen", agent_id=agent_id,
                                      balance_fen=123_456,
                                      hypothesis="Higher liquidity changes purchase choices.",
                                      source_event_ids=(source_event_id,))


def test_counterfactual_balance_is_double_entry_versioned_and_one_shot(engine_factory, tmp_path) -> None:
    """合法干预只改变目标余额一次，并留下完整双边事实。"""
    parent = engine_factory()
    source = _source_event(parent, 1)
    intervention = _intervention(source)
    child = _branch(parent, tmp_path / "counterfactual-success", intervention)
    try:
        before_version = child.context.state.version
        before_balance = child.context.state.accounts[child.context.state.agents[1].balance_account_id]
        receipt = child.apply_counterfactual(intervention)
        event = child.context.store.read_events(child.context.store.last_event_seq)[-1]
        assert receipt.before_balance_fen == before_balance
        assert receipt.after_balance_fen == 123_456
        assert child.context.state.version == before_version + 1 == receipt.world_state_version
        assert event.data["kind"] == "counterfactual_intervention"
        assert event.data["source_event_ids"] == [source]
        assert sum(event.data["resource_changes"].values()) == 0
        state = child.context.state.model_dump(mode="json")
        cutoff = child.context.store.last_event_seq
        with pytest.raises(ProtocolError, match="precede the first decision and occur once"):
            child.apply_counterfactual(intervention)
        assert child.context.state.model_dump(mode="json") == state
        assert child.context.store.last_event_seq == cutoff
    finally:
        child.close()


def test_counterfactual_rejects_manifest_hash_mismatch_without_mutation(engine_factory, tmp_path) -> None:
    """manifest 没有绑定相同干预身份时拒绝且不提交事件。"""
    parent = engine_factory()
    intervention = _intervention(_source_event(parent, 1))
    child = _branch(parent, tmp_path / "counterfactual-hash", intervention,
                    {"intervention_sha256": "0" * 64})
    try:
        state = child.context.state.model_dump(mode="json")
        cutoff = child.context.store.last_event_seq
        with pytest.raises(ProtocolError, match="manifest is invalid"):
            child.apply_counterfactual(intervention)
        assert child.context.state.model_dump(mode="json") == state
        assert child.context.store.last_event_seq == cutoff
    finally:
        child.close()


def test_counterfactual_rejects_evidence_created_after_parent_checkpoint(engine_factory, tmp_path) -> None:
    """子分支新观察不能伪装为父检查点已经存在的来源。"""
    parent = engine_factory()
    original = _intervention(_source_event(parent, 1))
    child = _branch(parent, tmp_path / "counterfactual-late", original)
    try:
        late = _source_event(child, 1)
        changed = _intervention(late)
        child.context.store.manifest["intervention_sha256"] = _digest(changed)
        path = child.context.store.run_dir / "manifest.json"
        atomic_write(path, canonical_json(child.context.store.manifest))
        with pytest.raises(ProtocolError, match="not visible to the target agent"):
            child.apply_counterfactual(changed)
    finally:
        child.close()


def test_counterfactual_rejects_another_agents_private_evidence(engine_factory, tmp_path) -> None:
    """人物一不能把人物二的私有观察作为自身假设来源。"""
    parent = engine_factory("multi-person.json")
    intervention = _intervention(_source_event(parent, 2), agent_id=1)
    child = _branch(parent, tmp_path / "counterfactual-other", intervention)
    try:
        with pytest.raises(ProtocolError, match="not visible to the target agent"):
            child.apply_counterfactual(intervention)
    finally:
        child.close()


def test_counterfactual_rejects_after_first_decision_without_mutation(engine_factory, tmp_path) -> None:
    """子分支一旦开始首次共同决策便永久关闭干预入口。"""
    parent = engine_factory()
    intervention = _intervention(_source_event(parent, 1))
    child = _branch(parent, tmp_path / "counterfactual-late-decision", intervention)
    try:
        ctx = context(child, "already-started")
        child.begin_decision_round([ctx])
        state = child.context.state.model_dump(mode="json")
        cutoff = child.context.store.last_event_seq
        with pytest.raises(ProtocolError, match="precede the first decision"):
            child.apply_counterfactual(intervention)
        assert child.context.state.model_dump(mode="json") == state
        assert child.context.store.last_event_seq == cutoff
        child.abort_decision_round("already-started", "test cleanup")
    finally:
        child.close()


def test_nested_counterfactual_accepts_source_after_ancestor_branch(engine_factory, tmp_path) -> None:
    """当前父检查点前、祖先分支后的新观察仍是合法反事实来源。"""
    root = engine_factory()
    middle = _plain_branch(root, tmp_path / "middle-branch")
    try:
        source = _source_event(middle, 1)
        intervention = _intervention(source)
        child = _branch(middle, tmp_path / "nested-counterfactual", intervention)
        try:
            receipt = child.apply_counterfactual(intervention)
            assert receipt.after_balance_fen == intervention.balance_fen
        finally:
            child.close()
    finally:
        middle.close()


def test_counterfactual_rejects_wrong_model_before_open_round(engine_factory):
    """真实公开入口保留严格模型先于分支与轮次约束的错误顺序。"""
    from tests.life.conftest import context
    from worldmock.errors import ProtocolError

    engine = engine_factory()
    engine.begin_decision_round([context(engine, "strict-counterfactual")])
    with pytest.raises(ProtocolError, match="Counterfactual intervention must use the strict life model"):
        engine.apply_counterfactual({})
    engine.abort_decision_round("strict-counterfactual", "Expected invalid model")
