"""查询经真实宿主：写出歧义名称时当场纠错、给出可以照抄的查询候选、不登记世界请求；做完之后下一次决策的模型请求里
读得到答案（docs/design/04-simulation-engine.md「出行、内容与查询」、docs/design/06-agent.md「进度、待办与记忆」）。"""

from __future__ import annotations

import json

import pytest

from tests.integration.runtime.current_contract_fixtures import ToolResponse, decide
from tests.life.conftest import MAIN_TOOL, SIDE_TOOL, advance_next, main_arguments, side_arguments, stage_day_plan
from tests.life.environment_inputs import catalog_rows, copy_environment, write_catalog_rows
from worldmock.life.models import StartSideActivityRequest


def _requested(engine) -> list:
    """已登记的世界请求（action_requested 事件），按落库先后。"""
    return [event for event in engine.context.store.read_events(engine.context.store.last_event_seq)
            if event.event_type == "action_requested"]


@pytest.mark.asyncio
async def test_ambiguous_query_name_is_corrected_on_the_host_with_query_candidates(engine_factory, tmp_path):
    """副本里有「柠檬茶（大）」与「柠檬茶(大)」两件展示的商品，人物写「柠檬茶[大]」：宿主当场纠错，明细码 NAME_AMBIGUOUS，
    候选是两条只替换出错一项、prefer 照写的查询调用；这一回合不登记世界请求，下一回合照抄候选就被受理。"""
    root = copy_environment(tmp_path / "large-tea")
    rows = catalog_rows(root, "merchant-catalog.jsonl")
    rows.extend({"category": "drink", "goods_id": goods_id, "name": name, "price_fen": 1500,
                 "store_kinds": ["restaurant"]} for goods_id, name in (("tea-large-a", "柠檬茶（大）"),
                                                                       ("tea-large-b", "柠檬茶(大)")))
    write_catalog_rows(root, "merchant-catalog.jsonl", sorted(rows, key=lambda row: row["goods_id"]))
    engine = engine_factory(root)
    stage_day_plan(engine, submitted=True)
    written = side_arguments("query", "零食", "柠檬茶[大]", prefer="美团")
    chosen = side_arguments("query", "零食", "柠檬茶（大）", prefer="美团")
    _, requests, _, results = await decide(engine, "ambiguous", [
        ToolResponse(SIDE_TOOL, written), ToolResponse(SIDE_TOOL, chosen, "call-fixed")])
    feedback = json.loads(requests[1]["messages"][-1]["content"])
    [item] = feedback["items"]
    assert (item["error_code"], item["written"]) == ("NAME_AMBIGUOUS", "柠檬茶[大]")
    assert item["path"] == ["tool_calls", 0, "function", "arguments", "target", 1]
    assert item["candidates"] == [
        {"tool": SIDE_TOOL, "arguments": side_arguments("query", "零食", name, prefer="美团"), "note": None}
        for name in ("柠檬茶（大）", "柠檬茶(大)")]
    for candidate in item["candidates"]:
        StartSideActivityRequest.model_validate(candidate["arguments"])
    assert [event.data["request"] for event in _requested(engine)] == [chosen]
    assert results[0].status == "in_progress"


@pytest.mark.asyncio
async def test_person_reads_the_answer_in_the_next_decision(engine_factory):
    """真实宿主提交一次查询；做完之后的下一次决策，落库的模型请求的用户消息里有答案里的店名与「可以下单」。"""
    engine = engine_factory()
    stage_day_plan(engine, submitted=True)
    _, _, _, results = await decide(engine, "ask", [ToolResponse(SIDE_TOOL, side_arguments("query", "柠檬茶"))])
    assert results[0].status == "in_progress"
    activity_id = results[0].activity_id
    while engine.context.state.activities[activity_id].status == "active":
        advance_next(engine)
    assert engine.scheduling_state().ready_agent_ids == (1,)
    before = engine.context.store.last_event_seq
    _, requests, _, _ = await decide(engine, "read", [
        ToolResponse(MAIN_TOOL, main_arguments("interact", "家", "测试休息"))])
    stored = [event.data["request"] for event in engine.context.store.read_events(engine.context.store.last_event_seq)
              if event.event_type == "model_request" and event.event_seq > before]
    assert stored == requests
    [user] = [message["content"] for message in stored[0]["messages"] if message["role"] == "user"]
    [progress] = [layer for layer in json.loads(user) if layer["layer"] == "进度层"]
    answered = [row["result"]["details"]["query"] for row in progress["events"] if row.get("tool") == SIDE_TOOL]
    assert ("一心面馆", "可以下单") in {(row["store"], row["now"]) for row in answered[0]["rows"]}
