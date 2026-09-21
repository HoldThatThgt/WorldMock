"""引擎观察投影与 LifeAgent 控制合同的真实形状接缝测试。

背景（runs/deepseek-day-openrouter 一日模拟死因）：观察投影层构造
active_activity 控制行时丢失 status 键，LifeAgent 校验 status == "active"
缺键即抛 RuntimeError。迁移前两侧测试各自绿、接缝零覆盖是根因温床——
本文件用真实引擎跑出含非空 active_activity 的观察投影，直接喂给
LifeAgent.prepare_react_loop（_visible_activity_controls 调用链），
钉住 docs/CONTRACTS.md「观察投影字段」的控制行字段闭集。
"""

from __future__ import annotations

import copy
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://openrouter.ai/api/v1")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.agent.base import ReactLoopRequest  # noqa: E402
from agentsociety2.society import DirectToolCall  # noqa: E402

from custom.agents.life_agent import LifeAgent  # noqa: E402
from tests.life.conftest import (  # noqa: E402
    advance_next,
    context,
    engine_factory as _engine_factory,  # noqa: F401
)
from worldmock.life.models import (  # noqa: E402
    PlanDayRequest,
    PlanSegmentInput,
    StartBasicActivityRequest,
)

#: CONTRACTS.md「观察投影字段」钉住的 active_activity 控制行字段闭集（7 键）。
_ACTIVE_ACTIVITY_CONTROL_KEYS = {
    "activity_id", "kind", "status", "title", "progress",
    "elapsed_seconds", "planned_duration_seconds",
}


def _plan(engine, offsets: tuple[int, ...], span: int = 100) -> PlanDayRequest:
    """以受控段起点构造当日计划：段起点即 plan_block_boundary 唤醒时刻。"""
    base = engine.state.current_time
    local = datetime.fromtimestamp(base, ZoneInfo(engine.scenario.timezone))
    anchor = int((local + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0).timestamp())
    segments = [PlanSegmentInput(start=base + offset, end=base + offset + span,
                                 intent_kind="休息", note="")
                for offset in offsets[:-1]]
    segments.append(PlanSegmentInput(start=base + offsets[-1], end=anchor,
                                     intent_kind="休息", note=""))
    return PlanDayRequest(segments=tuple(segments))


def _submit(engine, round_id: str, tool_name: str, request) -> None:
    """按真实顺序观察、登记并提交单人物动作后结算本轮。"""
    ctx = context(engine, round_id)
    engine.begin_decision_round([ctx])
    engine.observe(ctx)
    engine.submit_action(DirectToolCall(
        ctx, f"action-{round_id}", tool_name, request))
    engine.commit_decision_round(round_id)


def _observe_active_wait(engine) -> tuple[dict, object]:
    """跑真实引擎到段起点唤醒，让 wait 活动横跨观察点仍留在槽位。

    wait 计划 2700 秒而下一唤醒在 +1800 段起点（elapsed 900 < 2700），
    故该观察是 wait 启动后的第一个决策机会：active_activity 非空——
    与一日模拟 08:00 轮崩溃场景同构。
    """
    _submit(engine, "seam-plan", "plan_day",
            _plan(engine, (900, 1800, 2700, 3600, 4500)))
    advance_next(engine)
    _submit(engine, "seam-wait", "start_basic_activity",
            StartBasicActivityRequest(
                activity_type="wait", planned_duration_seconds=2700))
    advance_next(engine)
    ctx = context(engine, "seam-observe")
    engine.begin_decision_round([ctx])
    return engine.observe(ctx), ctx


def _observation_records(observation: dict) -> list[dict]:
    """构造宿主观察记录的真实承载形状（turn/action/ok/data 四键）。"""
    return [{"turn": 0, "action": "observe", "ok": True, "data": observation}]


def test_engine_active_activity_control_feeds_life_agent(_engine_factory) -> None:
    """真实投影的非空 active_activity 控制行被 LifeAgent 控制合同直接接受。"""
    engine = _engine_factory()
    observation, ctx = _observe_active_wait(engine)

    control = observation["active_activity"]
    assert control is not None
    assert control["kind"] == "wait"
    assert control["status"] == "active"
    assert set(control) == _ACTIVE_ACTIVITY_CONTROL_KEYS

    agent = LifeAgent()
    agent.prepare_react_loop(
        ReactLoopRequest(0, ctx.observed_at, _observation_records(observation)))
    schemas = {item["function"]["name"]: item["function"]
               for item in agent.build_react_tool_schemas(readonly=False)}
    assert {"continue_activity", "set_activity_state"} <= set(schemas)
    state = schemas["set_activity_state"]["parameters"]["properties"]
    assert state["activity_id"]["enum"] == [control["activity_id"]]
    assert state["operation"]["enum"] == ["pause", "cancel"]
    assert schemas["continue_activity"]["parameters"]["properties"][
        "activity_id"]["enum"] == [control["activity_id"]]


def test_life_agent_rejects_active_activity_without_active_status(
    _engine_factory,
) -> None:
    """status 键缺失（历史缺陷）或非 active 值时控制合同构建必须失败。"""
    engine = _engine_factory()
    observation, ctx = _observe_active_wait(engine)
    assert observation["active_activity"]["status"] == "active"

    def sabotage_drop_status(row: dict) -> None:
        row.pop("status")

    def sabotage_pause_status(row: dict) -> None:
        row["status"] = "paused"

    for sabotage in (sabotage_drop_status, sabotage_pause_status):
        damaged = copy.deepcopy(observation)
        sabotage(damaged["active_activity"])
        agent = LifeAgent()
        with pytest.raises(RuntimeError, match="active activity state is invalid"):
            agent.prepare_react_loop(ReactLoopRequest(
                0, ctx.observed_at, _observation_records(damaged)))
