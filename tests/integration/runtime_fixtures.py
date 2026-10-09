"""@brief 严格恢复测试共用的当前人物工作区，字段直接来自真实宿主合同。"""

from __future__ import annotations

import json
from pathlib import Path


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
    """@brief 恢复夹具冻结与真实新建入口同形的有效卡片，抽取层使用明确测试卡片。"""
    from tests.data_paths import ENVIRONMENT_ROOT
    from tests.profile.conftest import make_card
    from tests.frozen_inputs import fixture_metadata
    from worldmock.environment import load_environment
    from worldmock.run_setup import creation_inputs

    inputs = load_environment(ENVIRONMENT_ROOT, metadata_loader=fixture_metadata)
    return creation_inputs(inputs, {inputs.persona.agent_id: make_card()})[0][0]["profile"]


def current_run_identity(inputs, run_id: str):
    """@brief 使用当前运行代码、模型合同和真实场景建立可恢复身份。"""
    from worldmock.life.models import RunIdentity
    from worldmock.run_setup import _model_identity, runtime_code_sha256

    return RunIdentity(run_id=run_id, source_group_id=f"group-{run_id}",
                       agent_ids=(inputs.persona.agent_id,),
                       start_at=inputs.config.start_time.isoformat(), end_at=inputs.config.stop_time.isoformat(),
                       timezone=inputs.config.timezone, model_identity=_model_identity(inputs.config.model),
                       code_sha256=runtime_code_sha256(Path(__file__).resolve().parents[2]))
