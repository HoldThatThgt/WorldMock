"""运行 manifest 两阶段 model_identity 冻结与防篡改测试（C1 接缝三收口）。"""

from __future__ import annotations

import copy
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from worldmock.errors import IntegrityError
from worldmock.life.engine import LifeEngine
from worldmock.life.models import ModelIdentity, RunIdentity
from worldmock.life.scenario import load_scenario
from worldmock.runtime import _model_identity, _require_runtime_identity, runtime_code_sha256, two_stage_model_identity
from worldmock.validation import validate_two_stage_model_identity

_SCENARIO = Path("scenarios/white-collar-12-weeks.json")


@pytest.mark.parametrize("field,expected", [("transport_max_retries", 3), ("max_react_turns", 16)])
@pytest.mark.parametrize("change", ["missing", "incremented", "float", "string"])
def test_run_budgets_are_frozen_and_missing_or_changed_values_rejected(field, expected, change) -> None:
    """真实运行身份冻结纠错预算，旧身份缺字段或篡改时拒绝，不生成缺省值。"""
    identity = two_stage_model_identity()
    assert identity[field] == expected
    assert validate_two_stage_model_identity(identity) == identity
    if change == "missing":
        del identity[field]
    else:
        identity[field] = {"incremented": expected + 1, "float": float(expected), "string": str(expected)}[change]
    with pytest.raises(IntegrityError, match=field):
        validate_two_stage_model_identity(identity)


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
            provider="openai_compatible",
            base_url="https://api.deepseek.com",
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
    assert set(identity["stages"]) == {"decision_statement", "action", "work_review"}
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
    tampered["stages"]["decision_statement"]["temperature"] = 0.7
    with pytest.raises(IntegrityError, match="decision_statement"):
        validate_two_stage_model_identity(tampered)
    wrong_endpoint = copy.deepcopy(frozen)
    wrong_endpoint["base_url"] = "https://elsewhere.example/v1"
    with pytest.raises(IntegrityError, match="base_url"):
        validate_two_stage_model_identity(wrong_endpoint)


@pytest.mark.parametrize("keep_current", [False, True])
def test_retired_stage_is_rejected_even_beside_the_current_stage(keep_current, caplog) -> None:
    """阶段名精确闭集；旧名称替换或混入当前清单均拒绝。"""
    identity = two_stage_model_identity()
    identity["stages"]["monologue"] = copy.deepcopy(identity["stages"]["decision_statement"])
    if not keep_current:
        del identity["stages"]["decision_statement"]

    with pytest.raises(IntegrityError, match="stages must be exactly decision_statement"):
        validate_two_stage_model_identity(identity)

    assert "Model identity stages must be exactly" in caplog.text


def test_engine_create_rejects_retired_stage_before_writing_run_artifacts(tmp_path) -> None:
    """真实新建入口拒绝旧阶段；独立校验通过不能替代持久化前校验。"""
    scenario, _ = load_scenario(_SCENARIO)
    identity = two_stage_model_identity()
    identity["stages"]["monologue"] = identity["stages"].pop("decision_statement")
    run_dir = tmp_path / "old-stage"

    with pytest.raises(IntegrityError, match="stages must be exactly decision_statement"):
        LifeEngine.create(_SCENARIO, run_dir, _run_identity(scenario), identity)

    assert not (run_dir / "manifest.json").exists()
    assert not (run_dir / "footprints.sqlite").exists()


def test_runtime_restore_rejects_retired_stage_even_when_code_identity_matches() -> None:
    """恢复入口独立验证阶段身份，不能只依赖源码哈希间接拒绝旧清单。"""
    root = Path(__file__).resolve().parents[2]
    stages = two_stage_model_identity()
    stages["stages"]["monologue"] = stages["stages"].pop("decision_statement")
    manifest = {"model_identity": _model_identity().model_dump(mode="json"),
                "two_stage_model_identity": stages, "code_sha256": runtime_code_sha256(root)}

    with pytest.raises(IntegrityError, match="decision stage identity differs"):
        _require_runtime_identity(manifest, root)


def test_engine_created_without_two_stage_identity_keeps_null_slot(tmp_path) -> None:
    """反例：未传两阶段身份时 manifest 槽位为 null（既有测试工厂口径不变）。"""
    scenario, _ = load_scenario(_SCENARIO)
    engine = LifeEngine.create(_SCENARIO, tmp_path / "run", _run_identity(scenario))
    try:
        assert engine.store.manifest["two_stage_model_identity"] is None
    finally:
        engine.close()
