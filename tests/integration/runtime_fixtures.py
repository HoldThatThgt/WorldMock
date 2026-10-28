"""@brief 严格恢复测试共用的当前人物工作区，字段直接来自真实宿主合同。"""

from __future__ import annotations

import json
from pathlib import Path

#: 重放分支里人物的余额改成这个数；它不改变检查点之前已经冻结的任何抽取。
BRANCH_BALANCE = 123456


def write_agent_workspace(workspace: Path, *, agent_document=None, config_document=None,
                          missing=(), initialized: bool = False) -> Path:
    """@brief 写入当前人物身份与宿主配置，显式损坏参数只用于真实恢复拒绝测试。"""
    workspace.mkdir(parents=True)
    agent = agent_document if agent_document is not None else {
        "schema_version": 1, "agent_class": "LifeAgent", "agent_id": 1,
        "initialized_at": "2026-01-01T00:00:00+00:00" if initialized else None,
        "profile": _created_persona_profile()}
    config = config_document if config_document is not None else {"enable_memory": True, "max_react_turns": 16}
    for name, document in (("AGENT.json", agent), ("config.json", config)):
        if name not in missing:
            (workspace / name).write_text(document if isinstance(document, str) else json.dumps(document))
    return workspace


def _created_persona_profile() -> dict:
    """@brief 恢复夹具冻结与真实新建入口同形的有效卡片，卡片由夹具人设表构造。"""
    from tests.data_paths import ENVIRONMENT_ROOT
    from tests.frozen_inputs import fixture_metadata
    from worldmock.configuration.inputs.environment import load_environment
    from worldmock.runtime.framework.setup import creation_inputs

    inputs = load_environment(ENVIRONMENT_ROOT, metadata_loader=fixture_metadata)
    return creation_inputs(inputs)[0][0]["profile"]


def current_run_identity(inputs, run_id: str):
    """@brief 使用当前运行代码、模型合同和真实场景建立可恢复身份。"""
    from worldmock.state.identity import RunIdentity
    from worldmock.runtime.framework.setup import _model_identity, runtime_code_sha256

    return RunIdentity(run_id=run_id, source_group_id=f"group-{run_id}",
                       agent_ids=(inputs.persona.agent_id,),
                       start_at=inputs.config.start_time.isoformat(), end_at=inputs.config.stop_time.isoformat(),
                       timezone=inputs.config.timezone, model_identity=_model_identity(inputs.config.model),
                       code_sha256=runtime_code_sha256(Path(__file__).resolve().parents[2]))


def branch_of(parent, tmp_path: Path, kind: str, source: str):
    """@brief 在父运行此刻写共同检查点，从它开出恢复分支或带一项余额干预的重放分支，返回打开的分支引擎。

    :param kind: resume 是普通恢复；replay 是反事实重放，分支打开后把人物余额改成 BRANCH_BALANCE。
    :param source: 重放干预引用的来源事件身份。
    """
    from worldmock.recording_engine.storage.checkpoint import restore_branch, snapshot_directory
    from worldmock.simulation_engine.engine import LifeEngine
    from worldmock.state.decisions.results import CounterfactualIntervention
    from worldmock.simulation_engine.world.world_updates import _request_sha

    workspace = write_agent_workspace(tmp_path / "agent")
    checkpoint = parent.checkpoint({"agent:1": snapshot_directory(workspace, parent.context.store.assets)})
    intervention = CounterfactualIntervention(kind="set_balance_fen", agent_id=1, balance_fen=BRANCH_BALANCE,
                                              hypothesis="余额改变不影响已经冻结的抽取。", source_event_ids=(source,))
    metadata = None if kind == "resume" else {
        "branch_kind": "counterfactual",
        "intervention_sha256": _request_sha("counterfactual_intervention", intervention)}
    restore_branch(parent.context.store, checkpoint.checkpoint_id, tmp_path / "branch",
                   *(() if metadata is None else (metadata,))).close()
    child = LifeEngine.open(parent.context.inputs, tmp_path / "branch")
    if kind == "replay":
        child.apply_counterfactual(intervention)
    return child
