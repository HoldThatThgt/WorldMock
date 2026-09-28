"""修订合同引擎侧口味接缝：profile 权重 -> AgentState -> MotivationContext。

C8 交付了计算侧（runtime._agent_specs 写 profile["taste_weights"]）；本文件
钉住引擎侧最后一段：注入值入 AgentState.taste_weights、恢复走检查点携带值
（足迹库 WorldState，不重算）、无口味映射为空、observe 读侧拿到非空映射。
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")

from worldmock.life.engine import LifeEngine  # noqa: E402
from worldmock.life.models import ModelIdentity, RunIdentity  # noqa: E402
from worldmock.life.scenario import load_scenario  # noqa: E402

_SCENARIO = Path("scenarios/white-collar-12-weeks.json")


def _run_identity(scenario, run_id: str) -> RunIdentity:
    """形成与 tests/life/conftest 工厂同口径的运行身份。"""
    return RunIdentity(
        run_id=run_id,
        source_group_id=f"group-{run_id}",
        agent_ids=tuple(item.agent_id for item in scenario.agents),
        start_at=scenario.start_at.isoformat(),
        end_at=scenario.end_at.isoformat(),
        timezone=scenario.timezone,
        code_sha256="9" * 64,
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


def _weights(scenario) -> dict[str, float]:
    """取场景前两个内容条目的确定性注入权重（不依赖语料 id 硬编码）。"""
    return {scenario.contents[0].item_id: 1.0, scenario.contents[1].item_id: 0.5}


async def test_life_env_init_injects_taste_weights_into_agent_state(tmp_path) -> None:
    """生产接线：LifeEnv 把 runtime 注入的人物口味权重写入 AgentState。"""
    from custom.envs.life_env import LifeEnv

    scenario, _ = load_scenario(_SCENARIO)
    weights = _weights(scenario)
    env = LifeEnv(scenario_path=str(_SCENARIO), run_dir=str(tmp_path / "run"),
                  run_identity=_run_identity(scenario, "taste-seam-new").model_dump(mode="json"),
                  create_options={"agent_taste_weights": {1: weights}})
    await env.init(datetime.fromtimestamp(
        scenario.start_at.timestamp(), ZoneInfo(scenario.timezone)))
    try:
        assert env.engine.state.agents[1].taste_weights == weights
    finally:
        await env.close()


async def test_life_env_init_without_taste_weights_leaves_empty_mapping(tmp_path) -> None:
    """无注入（如无 background_story 人物）时引擎侧保持空映射=不抬升。"""
    from custom.envs.life_env import LifeEnv

    scenario, _ = load_scenario(_SCENARIO)
    env = LifeEnv(scenario_path=str(_SCENARIO), run_dir=str(tmp_path / "run"),
                  run_identity=_run_identity(scenario, "taste-seam-empty").model_dump(mode="json"))
    await env.init(datetime.fromtimestamp(
        scenario.start_at.timestamp(), ZoneInfo(scenario.timezone)))
    try:
        assert env.engine.state.agents[1].taste_weights == {}
    finally:
        await env.close()


async def test_engine_open_restores_checkpoint_weights_without_recompute(tmp_path) -> None:
    """恢复不重算：足迹库携带的检查点值优先（open 无权重入参可传）。"""
    scenario, _ = load_scenario(_SCENARIO)
    sentinel = {scenario.contents[0].item_id: 0.123}  # 非可推导值：重算必失真
    engine = LifeEngine.create(_SCENARIO, tmp_path / "run",
                               _run_identity(scenario, "taste-seam-restore"), None,
                               {1: sentinel})
    engine.close()
    reopened = LifeEngine.open(_SCENARIO, tmp_path / "run")
    try:
        assert reopened.state.agents[1].taste_weights == sentinel
    finally:
        reopened.close()


async def test_life_env_open_existing_uses_checkpoint_weights(tmp_path) -> None:
    """open_existing 环境恢复分支同样走检查点携带值，不重算。"""
    from custom.envs.life_env import LifeEnv

    scenario, _ = load_scenario(_SCENARIO)
    sentinel = {scenario.contents[0].item_id: 0.123}
    seed = LifeEngine.create(_SCENARIO, tmp_path / "run",
                             _run_identity(scenario, "taste-seam-open"), None, {1: sentinel})
    seed.close()
    env = LifeEnv(scenario_path=str(_SCENARIO), run_dir=str(tmp_path / "run"),
                  open_existing=True)
    await env.init(datetime.fromtimestamp(
        scenario.start_at.timestamp(), ZoneInfo(scenario.timezone)))
    try:
        assert env.engine.state.agents[1].taste_weights == sentinel
    finally:
        await env.close()


async def test_card_without_tastes_carries_all_zero_weights(tmp_path) -> None:
    """无口味卡片的全 0 权重按注入值原样入态（与空映射区分）。"""
    scenario, _ = load_scenario(_SCENARIO)
    zeroed = {item.item_id: 0.0 for item in scenario.contents}
    engine = LifeEngine.create(_SCENARIO, tmp_path / "run",
                               _run_identity(scenario, "taste-seam-zero"), None, {1: zeroed})
    try:
        assert engine.state.agents[1].taste_weights == zeroed
        assert set(engine.state.agents[1].taste_weights) == {
            item.item_id for item in scenario.contents}
    finally:
        engine.close()


async def test_motivation_context_reads_non_empty_taste_weights(tmp_path) -> None:
    """observe 读侧正例：MotivationContext 拿到 AgentState 的非空口味映射。"""
    scenario, _ = load_scenario(_SCENARIO)
    weights = _weights(scenario)
    engine = LifeEngine.create(_SCENARIO, tmp_path / "run",
                               _run_identity(scenario, "taste-seam-observe"), None, {1: weights})
    try:
        profile = next(item.motivation for item in engine.scenario.agents
                       if item.agent_id == 1 and item.motivation is not None)
        context = engine._motivation_context(engine.state.agents[1], profile)
        assert context.taste_weights == weights
    finally:
        engine.close()
