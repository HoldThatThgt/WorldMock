"""search_world 启动、耗时随机流、作用域与 app_use 归因、>100 尾召回、Fact Guard 纠错与退役项缺席测试。"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import shutil
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from tests.data_paths import ENVIRONMENT_ROOT  # noqa: E402
from tests.engine_fixture import engine_factory  # noqa: F401
from tests.integration.runtime.current_contract_fixtures import ScriptedModel, decide, inputs, resolved_binding  # noqa: E402
from tests.integration.runtime.test_life_agent_protocol import _ModelResponse  # noqa: E402
from tests.life.conftest import SIDE_TOOL, advance_next, side_arguments, stage_day_plan  # noqa: E402
from worldmock.agents.life_agent import LifeAgent, person_inputs  # noqa: E402
from worldmock.agents.prompt import prompt_capacity  # noqa: E402
from worldmock.agents.search.search_session import SearchSessionRunner  # noqa: E402
from worldmock.configuration.inputs.models import PersonaConfig, RunConfig  # noqa: E402
from worldmock.foundation.errors import ProtocolError  # noqa: E402
from worldmock.foundation.randomness import random_stream  # noqa: E402
from worldmock.resource_engine.catalog.identity import refresh_manifest_identity  # noqa: E402
from worldmock.simulation_engine.activities.search.search_activity import (  # noqa: E402
    SEARCH_SECONDS_MAX,
    SEARCH_SECONDS_MIN,
    attribute_search_app,
    search_seconds,
)
from worldmock.simulation_engine.observation.layers.search_projection import search_details  # noqa: E402
from worldmock.state.decisions.barrier import SearchSessionOutcome  # noqa: E402
from worldmock.state.interaction.actions import SEARCH_TOOL  # noqa: E402
from worldmock.state.world.kinds import ActivityKind  # noqa: E402


@pytest.fixture(autouse=True)
def _fixed_context_window(monkeypatch) -> None:
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model, metadata: metadata.context_window)


@pytest.mark.asyncio
async def test_search_duration_uses_seeded_stream_and_start_fills_app_id_only_when_prefer_matches(engine_factory):
    """search_world 活动时长按 random_stream(random_state, f'search-duration:{action_id}').randint(180,420)；
    start 仅当 prefer 精确匹配时才写 data.app_id，为空或不匹配时置为 None。"""
    for prefer, expected_app, label in (("饿了么", "eleme", "matched"), ("未知外卖", None, "unmatched"),
                                        (None, None, "none")):
        engine = engine_factory()
        stage_day_plan(engine)
        seed_before = engine.context.state.random_state
        _, _, _, [res] = await decide(engine, f"search-{label}", [
            _ModelResponse("c-sw", SEARCH_TOOL, json.dumps({"query": "柠檬茶", "prefer": prefer}, ensure_ascii=False)),
        ])
        assert res.status == "in_progress"
        act = engine.context.state.activities[res.activity_id]
        assert act.kind == ActivityKind.SEARCH
        expected_dur = random_stream(seed_before, f"search-duration:{act.origin_action_id}").randint(
            SEARCH_SECONDS_MIN, SEARCH_SECONDS_MAX,
        )
        assert act.planned_duration_seconds == expected_dur
        assert SEARCH_SECONDS_MIN <= expected_dur <= SEARCH_SECONDS_MAX
        assert act.data["app_id"] == expected_app and act.data["scoped"] is (expected_app is not None)


@pytest.mark.asyncio
async def test_complete_searches_attribution_majority_tie_break_and_seeded_fallback(engine_factory):
    """unscoped 搜索完结归因：prefer 未命中时按命中商品 selling_app_id 多数票归因（同票字典序小者）；
    空提交按 search-app:{action_id} 确定性抽样；Fact Guard 严格压制 summary 中的虚假报价文本。"""
    engine = engine_factory()
    stage_day_plan(engine)
    await decide(engine, "unscoped", [
        _ModelResponse("c-sw", SEARCH_TOOL, json.dumps({"query": "吃点什么", "prefer": "不存在的应用"}, ensure_ascii=False)),
    ])
    act = next(iter(engine.context.state.activities.values()))
    assert act.data["app_id"] is None
    while not engine.due_searches():
        advance_next(engine)
    [(task, catalog)] = engine.due_searches()
    # lemon-tea 同时在 eleme、meituan 可买 -> 并列 1 票，字典序 "eleme" < "meituan"；
    # 若再加只属于 meituan 的 beef-noodles -> meituan 2 票 > eleme 1 票。
    fake_price_summary = "牛肉面现在只卖1元且免运费！"
    model = ScriptedModel([
        _ModelResponse("s-1", "submit_search_results", json.dumps({
            "goods_ids": ["lemon-tea", "beef-noodles"],
            "place_ids": ["noodle-1"],
            "summary": fake_price_summary,
        }, ensure_ascii=False)),
    ])
    out = await SearchSessionRunner().run(task, catalog, resolved_binding(model, frozen=engine.context.inputs, stage="search"))
    engine.complete_searches([out])
    done = engine.context.state.activities[act.activity_id]
    assert done.data["app_id"] == "meituan" and done.data["scoped"] is False
    projected = search_details(done.model_dump(mode="json"), engine.context.inputs)
    prices = {row["product"]: row["price"] for row in projected["goods_rows"]}
    assert prices == {"柠檬茶": "12 元", "牛肉面": "28 元"}

    # 并列平票时字典序取最小 ("eleme" < "meituan")，空商品列表走 search-app:{action_id} 流
    tie_engine = engine_factory()
    stage_day_plan(tie_engine)
    await decide(tie_engine, "tie-sw", [
        _ModelResponse("c-sw", SEARCH_TOOL, json.dumps({"query": "柠檬茶", "prefer": None}, ensure_ascii=False)),
    ])
    tie_act = next(iter(tie_engine.context.state.activities.values()))
    while not tie_engine.due_searches():
        advance_next(tie_engine)
    [(t_tie, c_tie)] = tie_engine.due_searches()
    m_tie = ScriptedModel([_ModelResponse("s-1", "submit_search_results", json.dumps({
        "goods_ids": ["lemon-tea"], "place_ids": [], "summary": "柠檬茶",
    }, ensure_ascii=False))])
    out_tie = await SearchSessionRunner().run(t_tie, c_tie, resolved_binding(m_tie, frozen=tie_engine.context.inputs, stage="search"))
    tie_engine.complete_searches([out_tie])
    assert tie_engine.context.state.activities[tie_act.activity_id].data["app_id"] == "eleme"

    empty_engine = engine_factory()
    stage_day_plan(empty_engine)
    await decide(empty_engine, "empty-sw", [
        _ModelResponse("c-sw", SEARCH_TOOL, json.dumps({"query": "空白", "prefer": None}, ensure_ascii=False)),
    ])
    empty_act = next(iter(empty_engine.context.state.activities.values()))
    while not empty_engine.due_searches():
        advance_next(empty_engine)
    [(t_empty, c_empty)] = empty_engine.due_searches()
    m_empty = ScriptedModel([_ModelResponse("s-1", "submit_search_results", json.dumps({
        "goods_ids": [], "place_ids": [], "summary": "无命中",
    }, ensure_ascii=False))])
    out_empty = await SearchSessionRunner().run(t_empty, c_empty, resolved_binding(m_empty, frozen=empty_engine.context.inputs, stage="search"))
    empty_engine.complete_searches([out_empty])
    expected_draw = random_stream(empty_engine.context.state.random_state, f"search-app:{empty_act.origin_action_id}").choice(
        ["eleme", "meituan"],
    )
    assert empty_engine.context.state.activities[empty_act.activity_id].data["app_id"] == expected_draw
    assert attribute_search_app(empty_engine.context.inputs, empty_engine.context.state.random_state, empty_act, ()) == expected_draw


@pytest.mark.asyncio
async def test_tail_recall_over_large_catalog_past_rank_20(engine_factory, tmp_path):
    """超过 100 件商品的冻结目录中，目标商品排在 100 名之后（远超 20 条上限），子会话分页检索召回并通过 Fact Guard。"""
    large_root = tmp_path / "large-catalog-env"
    shutil.copytree(ENVIRONMENT_ROOT, large_root)
    merchant_path = large_root / "catalog" / "merchant-catalog.jsonl"
    existing = [json.loads(line) for line in merchant_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    extra = [
        {"category": "drink", "goods_id": f"filler-drink-{idx:03d}", "name": f"普通咖啡{idx:03d}",
         "price_fen": 100 + idx, "store_kinds": ["cafe"]}
        for idx in range(110)
    ]
    extra.append({
        "category": "drink", "goods_id": "tail-special-brew", "name": "特调海盐冰萃咖啡",
        "price_fen": 9800, "store_kinds": ["cafe"],
    })
    rows = sorted([*existing, *extra], key=lambda item: item["goods_id"])
    merchant_path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in rows) + "\n",
                             encoding="utf-8", newline="\n")
    refresh_manifest_identity(large_root / "catalog")

    engine = engine_factory(environment_root=large_root)
    stage_day_plan(engine)
    await decide(engine, "tail-call", [
        _ModelResponse("c-sw", SEARCH_TOOL, json.dumps({"query": "特调海盐冰萃咖啡", "prefer": None}, ensure_ascii=False)),
    ])
    while not engine.due_searches():
        advance_next(engine)
    [(task, catalog)] = engine.due_searches()
    page1 = catalog.search_goods({
        "keywords": ["咖啡"], "category": None, "app": None, "store": None,
        "max_price_yuan": None, "open_only": False, "limit": 20, "offset": 0,
    })
    assert page1["total"] > 100 and all(item["goods_id"] != "tail-special-brew" for item in page1["items"])

    model = ScriptedModel([
        _ModelResponse("s-p1", "search_goods", json.dumps({
            "keywords": ["咖啡"], "category": None, "app": None, "store": None,
            "max_price_yuan": None, "open_only": False, "limit": 50, "offset": 0,
        }, ensure_ascii=False)),
        _ModelResponse("s-p3", "search_goods", json.dumps({
            "keywords": ["咖啡"], "category": None, "app": None, "store": None,
            "max_price_yuan": None, "open_only": False, "limit": 50, "offset": 100,
        }, ensure_ascii=False)),
        _ModelResponse("s-sub", "submit_search_results", json.dumps({
            "goods_ids": ["tail-special-brew"], "place_ids": ["cafe"], "summary": "第 3 页召回目标特调咖啡。",
        }, ensure_ascii=False)),
    ])
    out = await SearchSessionRunner().run(task, catalog, resolved_binding(model, frozen=engine.context.inputs, stage="search"))
    assert out.goods_ids == ("tail-special-brew",)
    assert len(json.loads(model.requests[2]["messages"][-1]["content"])["items"]) >= 10
    receipt = engine.complete_searches([out])
    assert receipt.completed_count == 1


@pytest.mark.asyncio
async def test_fact_guard_rejects_fabricated_or_out_of_scope_ids_in_loop_and_on_commit(engine_factory):
    """编造不存在或越界的 goods_id/place_id 时：子会话内 Fact Guard 拒交并返回 SEARCH_FACT_INVALID 促其改交；
    引擎 complete_searches 入口直接复核同样拒绝伪造结果。"""
    engine = engine_factory()
    stage_day_plan(engine)
    await decide(engine, "scoped-search", [
        _ModelResponse("c-sw", SEARCH_TOOL, json.dumps({"query": "面食或饮品", "prefer": "饿了么"}, ensure_ascii=False)),
    ])
    while not engine.due_searches():
        advance_next(engine)
    [(task, catalog)] = engine.due_searches()
    model = ScriptedModel([
        _ModelResponse("s-bad", "submit_search_results", json.dumps({
            "goods_ids": ["invented-good", "beef-noodles"], "place_ids": ["fake-place"],
            "summary": "包含伪造及非饿了么商品。",
        }, ensure_ascii=False)),
        _ModelResponse("s-ok", "submit_search_results", json.dumps({
            "goods_ids": ["lemon-tea"], "place_ids": ["cafe"], "summary": "改为饿了么在售柠檬茶。",
        }, ensure_ascii=False)),
    ])
    out = await SearchSessionRunner().run(task, catalog, resolved_binding(model, frozen=engine.context.inputs, stage="search"))
    assert out.goods_ids == ("lemon-tea",) and out.place_ids == ("cafe",)
    verdict = json.loads(model.requests[1]["messages"][-1]["content"])
    assert verdict["error_code"] == "SEARCH_FACT_INVALID"
    assert set(verdict["invalid_goods_ids"]) == {"invented-good", "beef-noodles"}
    assert verdict["invalid_place_ids"] == ["fake-place"]

    forged = SearchSessionOutcome(
        action_id=task.action_id, agent_id=task.agent_id, activity_id=task.activity_id,
        goods_ids=("beef-noodles",), place_ids=(), summary="绕过子会话强交。",
    )
    with pytest.raises(ProtocolError, match="^Fact guard rejected search outcome ids$"):
        engine.complete_searches([forged])


@pytest.mark.asyncio
async def test_search_session_max_turns_exhaustion_raises_protocol_error_without_fallback(engine_factory):
    """SearchSessionRunner 回合预算耗尽仍未合法交卷时直接抛出 ProtocolError，无静态兜底并带回未提交模型草稿。"""
    engine = engine_factory()
    stage_day_plan(engine)
    await decide(engine, "exhaust", [_ModelResponse("c-sw", SEARCH_TOOL, '{"query":"饮品","prefer":null}')])
    while not engine.due_searches():
        advance_next(engine)
    [(task, catalog)] = engine.due_searches()
    limited = task.model_copy(update={"turn_budget": 2})
    search_call = json.dumps({
        "keywords": ["茶"], "category": None, "app": None, "store": None,
        "max_price_yuan": None, "open_only": False, "limit": 10, "offset": 0,
    }, ensure_ascii=False)
    model = ScriptedModel([
        _ModelResponse("s-1", "search_goods", search_call),
        _ModelResponse("s-2", "submit_search_results", json.dumps({
            "goods_ids": ["invented-only"], "place_ids": [], "summary": "仍非法。",
        }, ensure_ascii=False)),
    ])
    with pytest.raises(ProtocolError, match="^Search session turn budget exhausted$") as caught:
        await SearchSessionRunner().run(limited, catalog,
                                        resolved_binding(model, frozen=engine.context.inputs, stage="search"))
    assert len(getattr(caught.value, "evidence_drafts", ())) == 4


@pytest.mark.asyncio
async def test_last_turn_search_world_suspension_gets_extra_resume_turn(engine_factory):
    """在 max_react_turns 最后一回合之前刚好挂起 search_world，恢复后 effective_turn_limit 至少放行 1 个非挂起收尾回合。"""
    engine = engine_factory(mutate=lambda _p, cfg: cfg["models"]["reasoner"].__setitem__("max_react_turns", 2))
    stage_day_plan(engine)
    [w1] = engine.prepare_barrier((1,))
    sb1 = engine.open_sandbox(w1)
    # 回合 1：查一次记忆；回合 2（刚好耗尽 max_react_turns=2）：调 search_world 挂起！
    # 注意 remaining=1 时 _life_turn_tools 会缩减到 6 核心工具；因此要测试当 turn_used == max_react_turns (比如 max_turns=2 时 turn=1 调用 search_world 或人工模拟 turn_used == max_react_turns 恢复)!
    m1 = ScriptedModel([
        _ModelResponse("c-sw", SEARCH_TOOL, json.dumps({"query": "柠檬茶", "prefer": "饿了么"}, ensure_ascii=False)),
    ])
    out1 = await LifeAgent(person_inputs(engine.context.inputs.persona_by_id[1])).decide_wakeup(
        w1, sb1, resolved_binding(m1, frozen=engine.context.inputs, max_turns=2),
    )
    assert out1.status == "suspended"
    # 把 suspended_loop.turn_used 设为 2（等于 max_react_turns=2）模拟临界耗尽前挂起后恢复
    sup = out1.state.agents[1].suspended_loop
    edge_loop = sup.model_copy(update={"turn_used": 2}, deep=True)
    out_edge = out1.model_copy(update={"state": out1.state.model_copy(
        update={"agents": {**out1.state.agents, 1: out1.state.agents[1].model_copy(
            update={"suspended_loop": edge_loop}, deep=True)}}, deep=True)})
    engine.commit_barrier([out_edge])

    while not engine.due_searches():
        advance_next(engine)
    [(task, catalog)] = engine.due_searches()
    s_model = ScriptedModel([_ModelResponse("s-1", "submit_search_results", json.dumps({
        "goods_ids": ["lemon-tea"], "place_ids": ["cafe"], "summary": "可买。",
    }, ensure_ascii=False))])
    engine.complete_searches([
        await SearchSessionRunner().run(task, catalog,
                                        resolved_binding(s_model, frozen=engine.context.inputs, stage="search")),
    ])
    [w2] = engine.prepare_barrier((1,))
    assert w2.mode == "resume_search" and w2.suspended_loop.turn_used == 2
    sb2 = engine.open_sandbox(w2)
    m2 = ScriptedModel([_ModelResponse("c-buy", SIDE_TOOL, json.dumps(
        side_arguments("purchase", "柠檬茶", prefer="饿了么"), ensure_ascii=False))])
    # effective limit: start=3 > base=2 -> 给满 1 个不带 search_world 的恢复回合！
    out2 = await LifeAgent(person_inputs(engine.context.inputs.persona_by_id[1])).decide_wakeup(
        w2, sb2, resolved_binding(m2, frozen=engine.context.inputs, max_turns=2),
    )
    assert out2.status == "completed"
    engine.commit_barrier([out2])
    assert len(m2.requests) == 1 and len(m2.requests[0]["tools"]) == 6
    assert SEARCH_TOOL not in {t["function"]["name"] for t in m2.requests[0]["tools"]}


def test_retired_modules_classes_and_config_fields_are_completely_absent() -> None:
    """所有已退役模块、协调器、旧配置字段与旧人设单文件均彻底不存在。"""
    src_root = Path(__file__).resolve().parents[3] / "src" / "worldmock"
    for module_path in (
        "worldmock.runtime.lifecycle.continuation",
        "worldmock.simulation_engine.decisions.same_moment",
        "worldmock.simulation_engine.actions.query_activity",
        "worldmock.agents.answer.query_answer",
        "worldmock.agents.protocol.react_loop",
        "worldmock.agents.protocol.life_continuation",
    ):
        try:
            spec = importlib.util.find_spec(module_path)
        except ModuleNotFoundError:
            spec = None
        assert spec is None
    for file_rel in (
        "runtime/lifecycle/continuation.py",
        "simulation_engine/decisions/same_moment.py",
        "simulation_engine/actions/query_activity.py",
        "agents/answer/query_answer.py",
        "agents/protocol/react_loop.py",
        "agents/protocol/life_continuation.py",
    ):
        assert not (src_root / file_rel).exists()
    import worldmock.runtime.lifecycle.runner as runner_mod
    assert not hasattr(runner_mod, "DecisionCoordinator")
    assert "same_second_continuation" not in RunConfig.model_fields
    assert "model" not in RunConfig.model_fields
    assert not (ENVIRONMENT_ROOT / "persona.toml").exists()
    assert isinstance(inputs().persona_by_id[1], PersonaConfig)
