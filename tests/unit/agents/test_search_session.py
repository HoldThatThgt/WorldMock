"""@brief 异步搜索子会话运行器纯单元测试：隔离消息、禁用思考、Fact Guard 纠偏、预算耗尽与事件归属。"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from worldmock.agents.search.search_session import SearchSessionRunner
from worldmock.configuration.inputs.models import ModelMetadata, ModelProfileConfig
from worldmock.configuration.model.model_registry import ResolvedModelBinding
from worldmock.foundation.errors import ProtocolError
from worldmock.state.decisions.barrier import SearchTaskSpec


def _task(turn_budget: int = 4) -> SearchTaskSpec:
    """@brief 构造纯内存 SearchTaskSpec。"""
    return SearchTaskSpec(
        action_id="run:wake:100:v1:a1:action:0001",
        agent_id=1,
        activity_id="act-search-42",
        origin_wakeup_id="run:wake:100:v1:a1",
        query="想买一杯少糖燕麦拿铁",
        prefer="美团外卖",
        scoped_app_id="app-meituan",
        current_location_id="loc-home-1",
        location_name="自己家",
        opened_at_clock="10:10",
        local_date="2026-01-05",
        weather_label="晴",
        open_apps=("美团外卖",),
        category_overview={"饮食": 10},
        turn_budget=turn_budget,
    )


def _call(cid: str, name: str, args: dict[str, Any]) -> SimpleNamespace:
    """@brief 构造工具调用替身。"""
    return SimpleNamespace(id=cid, type="function",
                           function=SimpleNamespace(name=name, arguments=json.dumps(args, ensure_ascii=False)))


def _reply(calls: list[SimpleNamespace]) -> SimpleNamespace:
    """@brief 构造模型响应替身。"""
    return SimpleNamespace(
        model="deepseek-chat",
        choices=[SimpleNamespace(finish_reason="tool_calls",
                                 message=SimpleNamespace(role="assistant", content=None,
                                                         tool_calls=calls, reasoning_content=None))],
        usage={"prompt_tokens": 80, "completion_tokens": 16},
    )


class _ScriptedSearchClient:
    """@brief 记录请求并按脚本弹出回复的模型替身。"""

    def __init__(self, script: list[Any]) -> None:
        self._script = list(script)
        self.requests: list[dict[str, Any]] = []

    async def call(self, request: dict[str, Any]) -> Any:
        self.requests.append(request)
        return self._script.pop(0)


class _FakeCatalog:
    """@brief 假 SearchCatalogHandle：记录查询并执行可控的 Fact Guard 核验结果。"""

    def __init__(self, verdicts: list[dict[str, Any]], *, fail_on: str | None = None) -> None:
        self._verdicts = list(verdicts)
        self._fail_on = fail_on
        self.calls: list[tuple[str, Any]] = []

    def search_goods(self, request) -> dict[str, Any]:
        if self._fail_on == "search_goods":
            raise RuntimeError("catalog offline")
        self.calls.append(("search_goods", request))
        return {"total": 1, "items": [{"goods_id": "g-latte", "name": "燕麦拿铁", "category": "饮食",
                                       "unit_hint": "杯", "app_ids": ["app-meituan"]}],
                "has_more": False, "next_offset": 1}

    def inspect_goods_offers(self, request) -> dict[str, Any]:
        self.calls.append(("inspect_goods_offers", request))
        return {"offers": [{"goods_id": "g-latte", "orderable": True, "open_now": True}]}

    def search_places(self, request) -> dict[str, Any]:
        self.calls.append(("search_places", request))
        return {"total": 1, "items": [{"location_id": "loc-cafe-1", "name": "转角咖啡", "kind": "cafe",
                                       "open_now": True, "opening_hours": ["08:00-21:00"],
                                       "rating": 4.8, "travel_options": [], "has_content": False,
                                       "content_summary": None}], "has_more": False, "next_offset": 1}

    def validate_submission(self, request) -> dict[str, Any]:
        self.calls.append(("validate_submission", request))
        return self._verdicts.pop(0)


def _binding(client: Any) -> ResolvedModelBinding:
    """@brief 构造开启思考开关的模型档，验证子会话依然强制关闭思考。"""
    profile = ModelProfileConfig(
        provider="openai_compatible", base_url="https://api.example.invalid/v1",
        requested_model="deepseek-chat", api_key_env="WORLDMOCK_API_KEY",
        metadata_url="https://api.example.invalid/v1/models", context_window=262144,
        thinking=True, max_output_tokens=4096, max_react_turns=8,
    )
    meta = ModelMetadata(
        requested_model="deepseek-chat", metadata_url="https://api.example.invalid/v1/models",
        context_window=262144, max_output_tokens=4096,
    )
    return ResolvedModelBinding("search-prof", profile, meta, client, 4096)


@pytest.mark.asyncio
async def test_search_session_completes_with_fact_guard_feedback_and_evidence_drafts():
    """@brief 完整验证子会话禁用思考、工具顺序、幻觉编号退回纠偏、最终交卷及 EventDraft 归属字段。"""
    runner = SearchSessionRunner()
    catalog = _FakeCatalog([
        {"valid": False, "invalid_goods_ids": ["hallucinated-id"], "reason": "Unknown goods_id"},
        {"valid": True},
    ])
    client = _ScriptedSearchClient([
        _reply([
            _call("c1", "search_goods", {
                "keywords": ["燕麦拿铁"], "category": None, "app": "app-meituan", "store": None,
                "max_price_yuan": None, "open_only": True, "limit": 10, "offset": 0}),
            _call("c2", "search_places", {
                "keywords": ["咖啡"], "kind": None, "open_only": True, "limit": 10, "offset": 0}),
        ]),
        _reply([_call("c3", "inspect_goods_offers", {"goods_ids": ["g-latte"]})]),
        _reply([_call("c4", "submit_search_results", {
            "goods_ids": ["hallucinated-id"], "place_ids": [], "summary": "推荐这款。"})]),
        _reply([_call("c5", "submit_search_results", {
            "goods_ids": ["g-latte"], "place_ids": ["loc-cafe-1"],
            "summary": "推荐转角咖啡的燕麦拿铁，当前可下单或前往。"})]),
    ])
    outcome = await runner.run(_task(turn_budget=4), catalog, _binding(client))
    assert outcome.goods_ids == ("g-latte",) and outcome.place_ids == ("loc-cafe-1",)
    assert outcome.action_id == "run:wake:100:v1:a1:action:0001" and outcome.activity_id == "act-search-42"
    assert all(req["extra_body"] == {"thinking": {"type": "disabled"}} for req in client.requests)
    assert len(client.requests[3]["tools"]) == 1
    assert client.requests[3]["tools"][0]["function"]["name"] == "submit_search_results"
    assert len(outcome.evidence_drafts) == 8
    reqs = [d for d in outcome.evidence_drafts if d.event_type == "model_request"]
    resps = [d for d in outcome.evidence_drafts if d.event_type == "model_response"]
    assert all(d.activity_id == "act-search-42" and d.wakeup_id == "run:wake:100:v1:a1" for d in outcome.evidence_drafts)
    assert [d.data["call_id"] for d in reqs] == [
        f"run:wake:100:v1:a1:action:0001:search:{turn:04d}:attempt:1" for turn in range(1, 5)]
    assert all(d.data["purpose"] == "search_session" for d in outcome.evidence_drafts)
    assert [r.caused_by[0] for r in resps] == [q.event_id for q in reqs]
    assert all(r.data["request"] is None for r in resps)


@pytest.mark.asyncio
async def test_search_session_enforces_batch_rules_and_raises_on_budget_exhaustion(caplog):
    """@brief 混合读写与交卷被拒，预算耗尽前未通过 submit_search_results 则记日志并抛 ProtocolError。"""
    runner = SearchSessionRunner()
    catalog = _FakeCatalog([{"valid": False, "error_code": "INVALID_IDS"}])
    client = _ScriptedSearchClient([
        _reply([
            _call("m1", "search_goods", {
                "keywords": ["咖啡"], "category": None, "app": None, "store": None,
                "max_price_yuan": None, "open_only": False, "limit": 5, "offset": 0}),
            _call("m2", "submit_search_results", {"goods_ids": [], "place_ids": [], "summary": "混批无效"}),
        ]),
        _reply([_call("m3", "submit_search_results", {"goods_ids": [], "place_ids": [], "summary": "无命中"})]),
    ])
    with pytest.raises(ProtocolError, match="^Search session turn budget exhausted$") as caught:
        await runner.run(_task(turn_budget=2), catalog, _binding(client))
    assert "Search session turn budget exhausted" in caplog.text
    assert len(caught.value.evidence_drafts) == 4


@pytest.mark.asyncio
async def test_search_session_wraps_catalog_errors_and_accepts_zero_hit_submission():
    """@brief 零命中合规交卷直接通过；目录调用异常包装为 TOOL_SERVICE_FAILED。"""
    runner = SearchSessionRunner()
    ok_catalog = _FakeCatalog([{"valid": True}])
    zero_client = _ScriptedSearchClient([
        _reply([_call("z1", "submit_search_results", {
            "goods_ids": [], "place_ids": [], "summary": "当前时段暂无符合条件的可买商品或营业地点。"})]),
    ])
    zero_out = await runner.run(_task(turn_budget=2), ok_catalog, _binding(zero_client))
    assert zero_out.goods_ids == () and zero_out.place_ids == ()

    bad_catalog = _FakeCatalog([], fail_on="search_goods")
    bad_client = _ScriptedSearchClient([
        _reply([_call("e1", "search_goods", {
            "keywords": ["水"], "category": None, "app": None, "store": None,
            "max_price_yuan": None, "open_only": False, "limit": 5, "offset": 0})]),
    ])
    with pytest.raises(RuntimeError, match="TOOL_SERVICE_FAILED"):
        await runner.run(_task(turn_budget=3), bad_catalog, _binding(bad_client))
