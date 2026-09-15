"""Life 领域测试共享的真实运行工厂。"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")

from agentsociety2.society import DecisionContext, DirectToolCall  # noqa: E402

from worldmock.life.engine import LifeEngine  # noqa: E402
from worldmock.life.models import ModelIdentity, RunIdentity  # noqa: E402
from worldmock.life.scenario import load_scenario  # noqa: E402


@pytest.fixture
def engine_factory(tmp_path: Path):
    """建立绑定正式内容但隔离存储的 LifeEngine。"""
    created = []

    def create(scenario_name: str = "white-collar-12-weeks.json") -> LifeEngine:
        scenario_path = Path("scenarios") / scenario_name
        scenario, _ = load_scenario(scenario_path)
        run_id = f"test-{len(created)}-{scenario.scenario_id}"
        identity = RunIdentity(
            run_id=run_id,
            source_group_id=f"group-{run_id}",
            agent_ids=tuple(item.agent_id for item in scenario.agents),
            start_at=scenario.start_at.isoformat(),
            end_at=scenario.end_at.isoformat(),
            timezone=scenario.timezone,
            code_sha256="2" * 64,
            model_identity=ModelIdentity(
                provider="deepseek",
                base_url="https://api.deepseek.com/beta",
                requested_model="deepseek-flash",
                thinking="disabled",
                tool_choice="required",
                max_retries=0,
                prompt_sha256="0" * 64,
                tool_schema_sha256="1" * 64,
            ),
        )
        engine = LifeEngine.create(scenario_path, tmp_path / f"run-{len(created)}", identity)
        created.append(engine)
        return engine

    yield create
    for engine in created:
        try:
            engine.close()
        except Exception:
            pass


def context(engine: LifeEngine, round_id: str, agent_id: int = 1) -> DecisionContext:
    """形成指向当前已提交世界的宿主上下文。"""
    observed = datetime.fromtimestamp(engine.state.current_time, ZoneInfo(engine.scenario.timezone))
    return DecisionContext(engine.state.run_id, round_id, f"decision-{round_id}-{agent_id}",
                           agent_id, observed, engine.state.version)


def submit_round(engine: LifeEngine, requests: dict[int, tuple[str, object]], round_id: str):
    """按全部就绪人物创建并提交一个真实决策轮。"""
    ready = engine.scheduling_state().ready_agent_ids
    contexts = [context(engine, round_id, agent_id) for agent_id in ready]
    engine.begin_decision_round(contexts)
    receipts = []
    for item in reversed(contexts):
        tool_name, request = requests[item.agent_id]
        action_id = f"action-{round_id}-{item.agent_id}"
        receipts.append(engine.submit_action(DirectToolCall(item, action_id, tool_name, request)))
    return receipts, engine.commit_decision_round(round_id)


def advance_next(engine: LifeEngine) -> None:
    """推进到环境报告的下一个严格未来边界。"""
    state = engine.scheduling_state()
    assert state.next_event_at is not None
    engine.advance(int(state.next_event_at.timestamp()) - engine.state.current_time, state.next_event_at)
