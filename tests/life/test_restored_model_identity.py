"""决策阶段身份的五个入口验收：新建、打开（含工作区加载与恢复）、普通恢复与反事实重放、环境校验。

清单阶段只剩 action 与 work_review，键名为 stage_model_identity；含退役阶段 decision_statement 的清单经同一校验函数
以英文错误拒绝（docs/dev/SPEC-persona-prompt.md 第七章「清单」）。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from custom.envs.life_env import LifeEnv
from tests.life.test_taste_seam import _run_identity
from worldmock.errors import IntegrityError
from worldmock.life.engine import LifeEngine
from worldmock.life.scenario import load_scenario
from worldmock.runtime import _require_runtime_identity, stage_model_identity
from worldmock.validation import validate_stage_model_identity

_SCENARIO = Path("scenarios/white-collar-12-weeks.json")
_LIFECYCLES = ("open_existing", "restore", "from_workspace")
#: 旧版本清单中的决策自述阶段（退役）。
_RETIRED_STAGE = {"thinking": "disabled", "temperature": 1.0, "max_tokens": 8000, "tools": None,
                  "tool_choice": None}
_ROOT = Path(__file__).resolve().parents[2]


def _with_retired_stage() -> dict:
    """当前阶段身份另加退役的 decision_statement 阶段。"""
    identity = copy.deepcopy(stage_model_identity())
    identity["stages"]["decision_statement"] = dict(_RETIRED_STAGE)
    return identity


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
    stages = manifest["stage_model_identity"]["stages"]
    if corruption == "retired_stage":
        stages["decision_statement"] = dict(_RETIRED_STAGE)
    elif corruption == "unknown_stage":
        stages["monologue"] = dict(stages["action"])
    elif corruption == "old_manifest_key":
        manifest["two_stage_model_identity"] = manifest.pop("stage_model_identity")
    elif corruption == "invalid_stage_identity":
        manifest["stage_model_identity"] = []
    elif corruption == "wrong_action_parameters":
        stages["action"]["max_tokens"] = 4000
    path.write_text(json.dumps(manifest))


@pytest.mark.parametrize("lifecycle", _LIFECYCLES)
@pytest.mark.parametrize(("corruption", "message"), (
    ("retired_stage", "stage decision_statement is retired"),
    ("unknown_stage", "stages must be exactly action, work_review"),
    ("old_manifest_key", "decision stage identity is missing"),
    ("invalid_stage_identity", "identity must be an object"),
    ("wrong_action_parameters", "identity is inconsistent"),
))
async def test_environment_lifecycles_reject_invalid_saved_stage_identity(
    saved_environment, lifecycle, corruption, message, caplog,
):
    """打开、恢复和工作区重建均拒绝无效阶段清单（含退役阶段与旧键名），并留下英文错误日志。"""
    _tamper_manifest(saved_environment[0], corruption)

    with pytest.raises(IntegrityError, match=message):
        await _open_environment(lifecycle, saved_environment)

    assert "WorldMock failure:" in caplog.text


@pytest.mark.parametrize("lifecycle", _LIFECYCLES)
async def test_environment_lifecycles_restore_current_stage_identity(saved_environment, lifecycle):
    """合法生产运行经每条恢复路径保留冻结身份与世界时钟；清单只有 action 与 work_review 两个阶段。"""
    environment = await _open_environment(lifecycle, saved_environment)
    try:
        manifest = environment.engine.store.manifest
        assert manifest["stage_model_identity"] == stage_model_identity()
        assert set(manifest["stage_model_identity"]["stages"]) == {"action", "work_review"}
        assert "two_stage_model_identity" not in manifest
        assert environment.engine.state.current_time == int(saved_environment[1].timestamp())
        assert environment.run_dir == saved_environment[0]
    finally:
        await environment.close()


@pytest.mark.parametrize("lifecycle", _LIFECYCLES)
async def test_explicit_direct_engine_identity_remains_openable(engine_factory, lifecycle):
    """明确直驱创建的 null 身份继续可恢复，不生成模型身份。"""
    engine = engine_factory()
    saved = engine.store.run_dir, engine.scenario.start_at
    assert engine.store.manifest["stage_model_identity"] is None
    engine.close()

    environment = await _open_environment(lifecycle, saved)
    try:
        assert environment.engine.store.manifest["stage_model_identity"] is None
        assert environment.engine.store.read_agent_events("decision_started", 1) == []
    finally:
        await environment.close()


def test_direct_engine_open_rejects_retired_stage(engine_factory):
    """公共引擎打开入口也独立拒绝退役阶段，不能只在环境包装层检查。"""
    engine = engine_factory()
    run_dir = engine.store.run_dir
    engine.close()
    path = run_dir / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["stage_model_identity"] = stage_model_identity()
    path.write_text(json.dumps(manifest))
    _tamper_manifest(run_dir, "retired_stage")

    with pytest.raises(IntegrityError, match="Model identity stage decision_statement is retired"):
        LifeEngine.open(_SCENARIO, run_dir)


def test_new_run_rejects_a_retired_stage_identity(tmp_path):
    """新建入口在写清单前拒绝含退役阶段的身份，不留下运行目录内容。"""
    scenario, _ = load_scenario(_SCENARIO)
    identity = _run_identity(scenario, "retired-stage-create")

    with pytest.raises(IntegrityError, match="Model identity stage decision_statement is retired"):
        LifeEngine.create(_SCENARIO, tmp_path / "run", identity, _with_retired_stage())

    assert not (tmp_path / "run" / "manifest.json").exists()


def test_resume_and_replay_identity_rejects_a_retired_stage_before_comparing():
    """普通恢复与反事实重放共用的父清单身份检查先经同一校验函数拒绝退役阶段，再做等值比对。"""
    manifest = {"model_identity": {}, "stage_model_identity": _with_retired_stage(), "code_sha256": "0" * 64}

    with pytest.raises(IntegrityError, match="Model identity stage decision_statement is retired"):
        _require_runtime_identity(manifest, _ROOT)
    with pytest.raises(IntegrityError, match="Checkpoint model identity differs"):
        _require_runtime_identity({**manifest, "stage_model_identity": stage_model_identity()}, _ROOT)


def test_environment_validation_rejects_a_retired_stage(monkeypatch):
    """环境校验入口不带参数调用同一校验函数：当前身份含退役阶段即拒绝，合法身份返回两个阶段。"""
    import worldmock.runtime as runtime

    assert sorted(validate_stage_model_identity()["stages"]) == ["action", "work_review"]
    monkeypatch.setattr(runtime, "stage_model_identity", _with_retired_stage)
    with pytest.raises(IntegrityError, match="Model identity stage decision_statement is retired"):
        validate_stage_model_identity()
