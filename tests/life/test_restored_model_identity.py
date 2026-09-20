"""统一打开入口对决策阶段身份的真实环境生命周期验收。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from custom.envs.life_env import LifeEnv
from tests.life.test_taste_seam import _run_identity
from worldmock.errors import IntegrityError
from worldmock.life.engine import LifeEngine
from worldmock.life.scenario import load_scenario
from worldmock.runtime import two_stage_model_identity

_SCENARIO = Path("scenarios/white-collar-12-weeks.json")
_LIFECYCLES = ("open_existing", "restore", "from_workspace")


@pytest.fixture
async def saved_environment(tmp_path):
    """经正式 LifeEnv 新建并关闭运行，恢复测试只改明确目标字段。"""
    scenario, _ = load_scenario(_SCENARIO)
    run_dir = tmp_path / "run"
    environment = LifeEnv(
        scenario_path=str(_SCENARIO), run_dir=str(run_dir),
        run_identity=_run_identity(scenario, "restored-stage-identity").model_dump(mode="json"),
    )
    await environment.init(scenario.start_at)
    await environment.to_workspace(run_dir)
    await environment.close()
    return run_dir, scenario.start_at


async def _open_environment(lifecycle, saved):
    """调用公开生命周期入口，不直接调用内部身份校验函数。"""
    run_dir, start_at = saved
    if lifecycle == "from_workspace":
        return await LifeEnv.from_workspace(run_dir, scenario_path=str(_SCENARIO))
    environment = LifeEnv(scenario_path=str(_SCENARIO), run_dir=str(run_dir),
                          open_existing=lifecycle == "open_existing")
    if lifecycle == "open_existing":
        await environment.init(start_at)
    else:
        assert await environment.restore(run_dir) is True
    return environment


def _tamper_manifest(run_dir, corruption):
    """破坏持久清单中的单一身份合同，保留场景、语料和世界状态。"""
    path = run_dir / "manifest.json"
    manifest = json.loads(path.read_text())
    stages = manifest["two_stage_model_identity"]["stages"]
    if corruption == "retired_stage":
        stages["monologue"] = stages.pop("decision_statement")
    elif corruption == "additional_retired_stage":
        stages["monologue"] = dict(stages["decision_statement"])
    elif corruption == "missing_stage_identity":
        del manifest["two_stage_model_identity"]
    elif corruption == "invalid_stage_identity":
        manifest["two_stage_model_identity"] = []
    elif corruption == "wrong_statement_parameters":
        stages["decision_statement"]["temperature"] = 0.7
    path.write_text(json.dumps(manifest))


@pytest.mark.parametrize("lifecycle", _LIFECYCLES)
@pytest.mark.parametrize("corruption", (
    "retired_stage", "additional_retired_stage", "missing_stage_identity",
    "invalid_stage_identity", "wrong_statement_parameters",
))
async def test_environment_lifecycles_reject_invalid_saved_stage_identity(
    saved_environment, lifecycle, corruption, caplog,
):
    """打开、恢复和工作区重建均拒绝无效阶段清单，并留下英文错误日志。"""
    _tamper_manifest(saved_environment[0], corruption)

    with pytest.raises(IntegrityError, match="identity|stages"):
        await _open_environment(lifecycle, saved_environment)

    assert "WorldMock failure:" in caplog.text


@pytest.mark.parametrize("lifecycle", _LIFECYCLES)
async def test_environment_lifecycles_restore_current_stage_identity(saved_environment, lifecycle):
    """合法生产运行经每条恢复路径保留冻结身份与世界时钟。"""
    environment = await _open_environment(lifecycle, saved_environment)
    try:
        assert environment.engine.store.manifest["two_stage_model_identity"] == two_stage_model_identity()
        assert environment.engine.state.current_time == int(saved_environment[1].timestamp())
        assert environment.run_dir == saved_environment[0]
    finally:
        await environment.close()


@pytest.mark.parametrize("lifecycle", _LIFECYCLES)
async def test_explicit_direct_engine_identity_remains_openable(engine_factory, lifecycle):
    """明确直驱创建的 null 身份继续可恢复，不生成模型身份或决策自述。"""
    engine = engine_factory()
    saved = engine.store.run_dir, engine.scenario.start_at
    assert engine.store.manifest["two_stage_model_identity"] is None
    engine.close()

    environment = await _open_environment(lifecycle, saved)
    try:
        assert environment.engine.store.manifest["two_stage_model_identity"] is None
        assert environment.engine.store.read_agent_events("decision_started", 1) == []
    finally:
        await environment.close()


def test_direct_engine_open_rejects_retired_stage(engine_factory):
    """公共引擎打开入口也独立拒绝旧名，不能只在环境包装层检查。"""
    engine = engine_factory()
    run_dir = engine.store.run_dir
    engine.close()
    path = run_dir / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["two_stage_model_identity"] = two_stage_model_identity()
    path.write_text(json.dumps(manifest))
    _tamper_manifest(run_dir, "retired_stage")

    with pytest.raises(IntegrityError, match="stages must be exactly decision_statement"):
        LifeEngine.open(_SCENARIO, run_dir)
