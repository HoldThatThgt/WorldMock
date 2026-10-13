"""真实宿主把只用于下单和查询的 app 写进 target 的调用当场纠错：不登记世界请求、时钟不动、人物在同一次决策里重写
（docs/design/04-simulation-engine.md「动作、名称与并行」）。

真实引擎、LifeEnv 与 LifeAgent，只替换模型传输；夹具环境 2026 年 1 月 5 日 08:00 人在家。
"""

from __future__ import annotations

import json

import pytest

from tests.integration.life.test_app_opening import OFFERED_FIX, order_app_item, order_app_why
from tests.integration.runtime.current_contract_fixtures import ToolResponse, decide
from tests.life.conftest import SIDE_TOOL, context, side_arguments, stage_day_plan
from worldmock.life.error_contract import ERROR_KEYS
from worldmock.life.prompt_schemas import validate_layer

OPEN_MEITUAN = side_arguments("interact", "美团")
OPEN_KEEP = side_arguments("interact", "Keep")


async def _open_keep_after(engine_factory, *replies: dict):
    """一次决策里先依次回复给定的副工具参数，最后回复打开 Keep；返回引擎、决策前的时刻、发给模型的请求与结果。"""
    engine = engine_factory()
    stage_day_plan(engine)
    before = engine.context.state.current_time
    responses = [ToolResponse(SIDE_TOOL, arguments, f"call-{index}")
                 for index, arguments in enumerate([*replies, OPEN_KEEP])]
    _, requests, _, [result] = await decide(engine, "open", responses)
    assert result.status == "in_progress", result.error_code
    return engine, before, requests


def _feedback(engine) -> list[dict]:
    """这次决策落库的模型响应里的 protocol_feedback，按响应先后。"""
    return [row.data["protocol_feedback"] for row in engine.context.store.read_agent_events("model_response", 1)
            if "protocol_feedback" in row.data]


@pytest.mark.asyncio
async def test_host_corrects_opening_an_order_only_app_without_registering_a_request(engine_factory):
    """先回复打开美团、再回复打开 Keep：先那条响应的 protocol_feedback 顶层码是 ACTION_NOT_OFFERED，reason 等于 why，
    明细与环境复核逐字相同；模型读到同样五个键；整次决策只登记 Keep 那一条请求，没有 action_rejected；同刻决策
    轮数与只回复打开 Keep 时相同，世界时刻没有动。"""
    engine, before, requests = await _open_keep_after(engine_factory, OPEN_MEITUAN)
    expected = {"error_code": "ACTION_NOT_OFFERED", "reason": order_app_why("美团"), "fix": OFFERED_FIX,
                "items": [order_app_item("美团", "interact")], "unmet": []}
    assert _feedback(engine) == [expected]
    assert len(requests) == 2
    read = json.loads(requests[1]["messages"][-1]["content"])
    assert {key: read[key] for key in ERROR_KEYS} == expected
    [requested] = engine.context.store.read_agent_events("action_requested", 1)
    assert (requested.data["tool_name"], requested.data["request"]) == (SIDE_TOOL, OPEN_KEEP)
    assert engine.context.store.read_agent_events("action_rejected", 1) == []
    plain, _, _ = await _open_keep_after(engine_factory)
    assert engine.context.state.agents[1].same_moment_rounds == plain.context.state.agents[1].same_moment_rounds
    assert engine.context.state.current_time == before


@pytest.mark.asyncio
async def test_rejected_today_keeps_the_actionable_half(engine_factory):
    """同样两次回复之后再观察：进度层备忘有一行，written 是 interact，why 是那一句的前 60 个字符（可照做的半句完整
    留下）；整层通过层校验。"""
    engine, _, _ = await _open_keep_after(engine_factory, OPEN_MEITUAN)
    observed = engine.observe(context(engine, "after"))
    rows = observed["progress_layer"]["rejected_today"]
    assert [(row["written"], row["why"]) for row in rows] == [
        ("interact", "Write 美团 in prefer of purchase or query, not in target: this")]
    validate_layer(observed["progress_layer"])


@pytest.mark.asyncio
async def test_shape_errors_come_before_the_order_app_refusal(engine_factory):
    """打开美团而 prefer 写了美团：请求形状先于名称，纠错是 ARGUMENTS_INVALID，明细只有 prefer 那一条，没有名称明细。"""
    engine, _, _ = await _open_keep_after(engine_factory, {**OPEN_MEITUAN, "prefer": "美团"})
    [feedback] = _feedback(engine)
    assert feedback["error_code"] == "ARGUMENTS_INVALID"
    assert [(item["path"][-1], item["why"]) for item in feedback["items"]] == [
        ("prefer", "prefer must be null when action is interact")]
