"""运行 manifest 两阶段 model_identity 冻结与防篡改测试（C1 接缝三收口）。"""

from __future__ import annotations

import copy
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com/beta")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from worldmock.errors import IntegrityError
from worldmock.life.engine import LifeEngine
from worldmock.life.models import ModelIdentity, RunIdentity
from worldmock.life.scenario import load_scenario
from worldmock.runtime import two_stage_model_identity
from worldmock.validation import validate_two_stage_model_identity

_SCENARIO = Path("scenarios/white-collar-12-weeks.json")


def _run_identity(scenario) -> RunIdentity:
    """形成与 conftest 工厂同口径的运行身份（隔离运行目录）。"""
    run_id = f"manifest-test-{scenario.scenario_id}"
    return RunIdentity(
        run_id=run_id,
        source_group_id=f"group-{run_id}",
        agent_ids=tuple(item.agent_id for item in scenario.agents),
        start_at=scenario.start_at.isoformat(),
        end_at=scenario.end_at.isoformat(),
        timezone=scenario.timezone,
        code_sha256="3" * 64,
        model_identity=ModelIdentity(
            provider="deepseek",
            base_url="https://api.deepseek.com/beta",
            requested_model="deepseek-flash",
            thinking="enabled",
            tool_choice="auto",
            max_retries=0,
            prompt_sha256="0" * 64,
            tool_schema_sha256="1" * 64,
        ),
    )


async def test_life_env_init_freezes_two_stage_identity_into_manifest(tmp_path) -> None:
    """生产接线：LifeEnv 新建运行把两阶段身份冻结进运行 manifest。"""
    from custom.envs.life_env import LifeEnv

    scenario, _ = load_scenario(_SCENARIO)
    env = LifeEnv(scenario_path=str(_SCENARIO), run_dir=str(tmp_path / "run"),
                  run_identity=_run_identity(scenario).model_dump(mode="json"))
    await env.init(datetime.fromtimestamp(
        scenario.start_at.timestamp(), ZoneInfo(scenario.timezone)))
    try:
        manifest = env.engine.store.manifest
    finally:
        await env.close()

    identity = manifest["two_stage_model_identity"]
    assert set(identity["stages"]) == {"monologue", "action", "work_review"}
    # 与 runtime 单一事实源逐字节一致（B5 设计决定 1 的对齐目标）。
    assert identity == two_stage_model_identity()
    validate_two_stage_model_identity(identity)
    # 单阶段 model_identity 块保持恢复门可比形状（runtime._require_runtime_identity）。
    assert set(manifest["model_identity"]) == {
        "provider", "base_url", "requested_model", "thinking", "tool_choice",
        "max_retries", "prompt_sha256", "tool_schema_sha256"}


def test_manifest_two_stage_identity_tampering_is_rejected(tmp_path) -> None:
    """篡改拒绝：manifest 冻结值的任何阶段篡改被一致性校验拒绝。"""
    scenario, _ = load_scenario(_SCENARIO)
    engine = LifeEngine.create(_SCENARIO, tmp_path / "run", _run_identity(scenario),
                               two_stage_model_identity())
    try:
        frozen = copy.deepcopy(engine.store.manifest["two_stage_model_identity"])
    finally:
        engine.close()

    validate_two_stage_model_identity(frozen)
    tampered = copy.deepcopy(frozen)
    tampered["stages"]["monologue"]["temperature"] = 0.7
    with pytest.raises(IntegrityError, match="monologue"):
        validate_two_stage_model_identity(tampered)
    no_beta = copy.deepcopy(frozen)
    no_beta["base_url"] = "https://api.deepseek.com"
    with pytest.raises(IntegrityError, match="beta"):
        validate_two_stage_model_identity(no_beta)


def test_engine_created_without_two_stage_identity_keeps_null_slot(tmp_path) -> None:
    """反例：未传两阶段身份时 manifest 槽位为 null（既有测试工厂口径不变）。"""
    scenario, _ = load_scenario(_SCENARIO)
    engine = LifeEngine.create(_SCENARIO, tmp_path / "run", _run_identity(scenario))
    try:
        assert engine.store.manifest["two_stage_model_identity"] is None
    finally:
        engine.close()
