"""SuspendedLoopState 检查点往返恢复、提前取消与反事实清空、TodayEntry 同日合并/跨午夜分切与因果链测试。"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import pytest

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from tests.engine_fixture import engine_factory  # noqa: F401
from tests.integration.runtime.current_contract_fixtures import (  # noqa: E402
    ScriptedModel,
    decide,
    resolved_binding,
)
from tests.integration.runtime.test_life_agent_protocol import _ModelResponse  # noqa: E402
from tests.life.conftest import SIDE_TOOL, activity_name, advance_next, advance_next_async, conclude_request, side_arguments, stage_day_plan  # noqa: E402
from tests.life.current_world import stage_moment  # noqa: E402
from worldmock.agents.life_agent import LifeAgent, person_inputs  # noqa: E402
from worldmock.agents.memory.workspace import create_workspaces, restore_workspaces, snapshot_workspaces  # noqa: E402
from worldmock.agents.prompt import prompt_capacity  # noqa: E402
from worldmock.agents.search.search_session import SearchSessionRunner  # noqa: E402
from worldmock.foundation.serialization import sha256_json  # noqa: E402
from worldmock.foundation.time.readable import local_date  # noqa: E402
from worldmock.recording_engine.storage.checkpoint import restore_branch  # noqa: E402
from worldmock.runtime.lifecycle.runner import _intervention_sha256  # noqa: E402
from worldmock.simulation_engine.actions.start_group import ToolInvocation  # noqa: E402
from worldmock.simulation_engine.engine import LifeEngine  # noqa: E402
from worldmock.state.decisions.results import CounterfactualIntervention  # noqa: E402
from worldmock.state.interaction.actions import SEARCH_TOOL  # noqa: E402
from worldmock.state.world.kinds import ActivityStatus  # noqa: E402


@pytest.fixture(autouse=True)
def _fixed_context_window(monkeypatch) -> None:
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model, metadata: metadata.context_window)


@pytest.mark.asyncio
async def test_suspended_loop_checkpoints_resumes_and_completes_causal_chain(engine_factory, tmp_path) -> None:
    """search_world 挂起携带 pending_tool_call_id 落盘检查点，重开后恢复原 wakeup_id 并接续 purchase，完整保持因果链。"""
    engine = engine_factory()
    stage_day_plan(engine)
    create_workspaces(engine.context.store.run_dir, (1,))
    await decide(engine, "suspend", [
        _ModelResponse("call-sw-suspend", SEARCH_TOOL, json.dumps({"query": "柠檬茶", "prefer": "美团"},
                                                                  ensure_ascii=False), content="先搜一下。"),
    ])
    sup = engine.context.state.agents[1].suspended_loop
    assert sup is not None and sup.pending_tool_call_id == "call-sw-suspend"
    assert sup.turn_used == 1 and sup.activity_id and sup.wakeup_id
    point = engine.checkpoint(snapshot_workspaces(engine.context.store.run_dir, (1,), engine.context.store.assets))

    resumed_dir = tmp_path / "resumed-search"
    child = restore_branch(engine.context.store, point.checkpoint_id, resumed_dir)
    try:
        restore_workspaces(point.snapshots, child.assets, resumed_dir, (1,))
    finally:
        child.close()

    reopened = LifeEngine.open(engine.context.inputs, resumed_dir)
    try:
        reopened_sup = reopened.context.state.agents[1].suspended_loop
        assert reopened_sup is not None and reopened_sup == sup
        while not reopened.due_searches():
            advance_next(reopened)
        [(task, catalog)] = reopened.due_searches()
        search_model = ScriptedModel([
            _ModelResponse("s-1", "submit_search_results", json.dumps({
                "goods_ids": ["lemon-tea"], "place_ids": ["cafe"], "summary": "美团可购柠檬茶。",
            }, ensure_ascii=False)),
        ])
        search_out = await SearchSessionRunner().run(
            task, catalog, resolved_binding(search_model, frozen=reopened.context.inputs, stage="search"),
        )
        reopened.complete_searches([search_out])
        [w_resume] = reopened.prepare_barrier((1,))
        assert w_resume.mode == "resume_search" and w_resume.wakeup_id == sup.wakeup_id
        assert w_resume.suspended_loop.pending_tool_call_id == "call-sw-suspend"

        sb_resume = reopened.open_sandbox(w_resume)
        m_buy = ScriptedModel([
            _ModelResponse("call-buy", SIDE_TOOL, json.dumps(side_arguments("purchase", "柠檬茶", prefer="美团"),
                                                              ensure_ascii=False), content="就买这个。"),
        ])
        out_buy = await LifeAgent(person_inputs(reopened.context.inputs.persona_by_id[1])).decide_wakeup(
            w_resume, sb_resume, resolved_binding(m_buy, frozen=reopened.context.inputs, stage="action"),
        )
        assert out_buy.status == "completed"
        reopened.commit_barrier([out_buy])
        assert reopened.context.state.agents[1].suspended_loop is None

        tool_msg = m_buy.requests[0]["messages"][-1]
        assert tool_msg["role"] == "tool" and tool_msg["tool_call_id"] == "call-sw-suspend"
        payload = json.loads(tool_msg["content"])
        assert payload["activity_id"] == sup.activity_id and payload["search"]["goods_rows"][0]["product"] == "柠檬茶"

        all_events = [e for e in reopened.context.store.iter_events(reopened.context.store.last_event_seq)]
        req_tools = [e.data["tool_name"] for e in all_events if e.event_type == "action_requested"]
        assert req_tools == [SEARCH_TOOL, SIDE_TOOL]
        purposes = [e.data.get("purpose") for e in all_events if e.event_type == "model_request"]
        assert "search_session" in purposes and None in purposes
        # 同日同一 wakeup_id 在 memory_layer["今天"] 中归并成唯一条目
        obs_after, _, _ = reopened.observations.build_wakeup_observation(reopened.context.state.agents[1])
        today_list = obs_after["memory_layer"]["今天"]
        assert len(today_list) == 1 and "柠檬茶" in json.dumps(today_list, ensure_ascii=False)
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_early_conclude_and_counterfactual_clear_suspended_loop(engine_factory, tmp_path) -> None:
    """conclude_activity 提前取消进行中搜索及反事实分支干预均彻底清空 suspended_loop。"""
    e_cancel = engine_factory()
    stage_day_plan(e_cancel)
    obs_before, _, _, [res] = await decide(e_cancel, "s-cancel", [
        _ModelResponse("c-sw", SEARCH_TOOL, json.dumps({"query": "茶", "prefer": None}, ensure_ascii=False)),
    ])
    assert e_cancel.context.state.agents[1].suspended_loop is not None
    search_name = activity_name(e_cancel, res.activity_id)
    conclude_req = conclude_request(search_name, summary="不搜了。").model_dump(mode="json")
    obs_running, _, _ = e_cancel.observations.build_wakeup_observation(e_cancel.context.state.agents[1])
    inv = ToolInvocation(
        1, "wakeup-cancel", "wakeup-cancel:action:0001", "conclude_activity", conclude_req,
        sha256_json({"tool_name": "conclude_activity", "request": conclude_req}),
        "evt-req-cancel", obs_running,
    )
    cancelled_state, _ = e_cancel.actions.dispatch(e_cancel.context.state, [inv])
    assert cancelled_state.agents[1].suspended_loop is None
    assert cancelled_state.activities[res.activity_id].status == ActivityStatus.CANCELLED

    e_cf = engine_factory()
    stage_day_plan(e_cf)
    create_workspaces(e_cf.context.store.run_dir, (1,))
    obs, _, _, _ = await decide(e_cf, "cf-suspend", [
        _ModelResponse("c-sw", SEARCH_TOOL, json.dumps({"query": "茶", "prefer": None}, ensure_ascii=False)),
    ])
    assert e_cf.context.state.agents[1].suspended_loop is not None
    pt = e_cf.checkpoint(snapshot_workspaces(e_cf.context.store.run_dir, (1,), e_cf.context.store.assets))
    intervention = CounterfactualIntervention(
        kind="set_balance_fen", agent_id=1, balance_fen=100_000,
        hypothesis="Counterfactual cancels suspended search.", source_event_ids=(obs["observation_event_id"],),
    )
    digest = _intervention_sha256(intervention)
    cf_dir = tmp_path / "cf-branch"
    child = restore_branch(e_cf.context.store, pt.checkpoint_id, cf_dir,
                           {"branch_kind": "counterfactual", "intervention_sha256": digest})
    try:
        restore_workspaces(pt.snapshots, child.assets, cf_dir, (1,))
    finally:
        child.close()
    reopened_cf = LifeEngine.open(e_cf.context.inputs, cf_dir)
    try:
        reopened_cf.apply_counterfactual(intervention)
        assert reopened_cf.context.state.agents[1].suspended_loop is None
    finally:
        reopened_cf.close()


@pytest.mark.asyncio
async def test_cross_midnight_search_resume_splits_today_entries_by_local_date(engine_factory) -> None:
    """23:57 发起 search_world 挂起并于次日 00:02 恢复提交购买：按 (wakeup_id, local_date) 跨日分切而不混入同条。"""
    def _near_midnight(_persona, config) -> None:
        config["start_time"] = datetime.fromisoformat("2026-01-05T23:00:00+08:00")
        config["stop_time"] = datetime.fromisoformat("2026-01-06T08:00:00+08:00")

    engine = engine_factory(mutate=_near_midnight)
    stage_moment(engine, int(datetime.fromisoformat("2026-01-05T23:57:00+08:00").timestamp()))
    stage_day_plan(engine)
    await decide(engine, "late-suspend", [
        _ModelResponse("c-late", SEARCH_TOOL, json.dumps({"query": "柠檬茶", "prefer": "美团"}, ensure_ascii=False)),
    ])
    sup = engine.context.state.agents[1].suspended_loop
    assert sup is not None
    while not engine.due_searches():
        await advance_next_async(engine)
    stage_day_plan(engine)
    [(task, catalog)] = engine.due_searches()
    s_model = ScriptedModel([
        _ModelResponse("s-1", "submit_search_results", json.dumps({
            "goods_ids": ["lemon-tea"], "place_ids": ["cafe"], "summary": "夜间可购。",
        }, ensure_ascii=False)),
    ])
    engine.complete_searches([
        await SearchSessionRunner().run(task, catalog,
                                        resolved_binding(s_model, frozen=engine.context.inputs, stage="search")),
    ])
    [w_next_day] = engine.prepare_barrier((1,))
    assert w_next_day.wakeup_id == sup.wakeup_id and w_next_day.mode == "resume_search"
    sb = engine.open_sandbox(w_next_day)
    out = await LifeAgent(person_inputs(engine.context.inputs.persona_by_id[1])).decide_wakeup(
        w_next_day, sb, resolved_binding(ScriptedModel([
            _ModelResponse("c-buy", SIDE_TOOL, json.dumps(side_arguments("purchase", "柠檬茶", prefer="美团"),
                                                          ensure_ascii=False)),
        ]), frozen=engine.context.inputs, stage="action"),
    )
    engine.commit_barrier([out])
    reqs = [e for e in engine.context.store.read_agent_events("action_requested", 1) if e.wakeup_id == sup.wakeup_id]
    tz = engine.context.inputs.config.timezone
    assert [(e.data["tool_name"], local_date(e.occurred_at, tz)) for e in reqs] == [
        (SEARCH_TOOL, "2026-01-05"), (SIDE_TOOL, "2026-01-06"),
    ]
    obs_day2, _, _ = engine.observations.build_wakeup_observation(engine.context.state.agents[1])
    today_list = obs_day2["memory_layer"]["今天"]
    assert len(today_list) == 1
    serialized = json.dumps(today_list, ensure_ascii=False)
    assert "搜了一下" not in serialized and "购买" in serialized


@pytest.mark.asyncio
async def test_conclude_with_memories_before_search_suspend_persists_memory_update(
    engine_factory, host_factory,
) -> None:
    """同一唤醒先以带记忆的 conclude_activity 收尾、再发起 search_world 挂起，恢复完成时仍识别写入点并登记 memory_updated。"""
    from tests.life.conftest import MAIN_TOOL, advance_until_ready, main_arguments

    engine = engine_factory()
    stage_day_plan(engine, submitted=True)
    conclude_mem = conclude_request("TODO", memories=[[{
        "type": "reflection", "text": "休息片刻后精神恢复不少。", "keywords": ["休息"], "importance": 0.5,
    }]])
    model = ScriptedModel([_ModelResponse("c-rest", MAIN_TOOL, json.dumps(main_arguments("sleep", "60"),
                                                                           ensure_ascii=False))])
    host = host_factory(engine, model)
    await host.step()
    assert advance_until_ready(engine)
    act = next(iter(engine.context.state.activities.values()))
    conclude_payload = conclude_mem.model_copy(
        update={"items": [conclude_mem.items[0].model_copy(update={"activity_name": act.name})]},
    ).model_dump(mode="json")
    model.responses.extend([
        _ModelResponse("c-conclude", "conclude_activity", json.dumps(conclude_payload, ensure_ascii=False)),
        _ModelResponse("c-sw", SEARCH_TOOL, json.dumps({"query": "柠檬茶", "prefer": "美团"}, ensure_ascii=False)),
    ])
    await host.step()
    suspended = engine.context.state.agents[1].suspended_loop
    assert suspended is not None
    while not engine.due_searches():
        await advance_next_async(engine)
    model.responses.extend([
        _ModelResponse("s-submit", "submit_search_results", json.dumps({
            "goods_ids": ["lemon-tea"], "place_ids": ["cafe"], "summary": "美团可购柠檬茶。",
        }, ensure_ascii=False)),
        _ModelResponse("c-buy", SIDE_TOOL, json.dumps(side_arguments("purchase", "柠檬茶", prefer="美团"),
                                                      ensure_ascii=False)),
    ])
    await host.step()
    updates = [
        e for e in engine.context.store.iter_events(engine.context.store.last_event_seq)
        if e.event_type == "memory_updated" and e.wakeup_id == suspended.wakeup_id
    ]
    assert len(updates) == 1
    assert updates[0].data["trigger"] == ["activity_conclusion"] and updates[0].data["created_episode_ids"]


@pytest.mark.asyncio
async def test_progress_report_wakeup_finishes_cleanly_on_stop_reply_after_report_status(
    engine_factory, host_factory,
) -> None:
    """长活动进行中途因 progress_report_due 唤醒：调用 report_status 后以无工具调用回复直接结束唤醒并推进时钟。"""
    from types import SimpleNamespace
    from tests.life.conftest import MAIN_TOOL, main_arguments, report_request

    engine = engine_factory(mutate=lambda _p, c: c.__setitem__("progress_report_interval_seconds", 60))
    stage_day_plan(engine, submitted=True)
    target_title = next(
        row.title for row in engine.context.inputs.catalog.contents.values()
        if "home" in row.location_kinds and row.duration_seconds >= 300 and row.consumes_state is None
    )
    model = ScriptedModel([_ModelResponse("c-start", MAIN_TOOL,
                                          json.dumps(main_arguments("interact", "家", target_title),
                                                     ensure_ascii=False))])
    host = host_factory(engine, model)
    await host.step()
    act = next(iter(engine.context.state.activities.values()))
    now = engine.context.state.current_time
    assert "progress_report_due" in engine.context.state.agents[1].pending_wake_reasons
    stop_msg = SimpleNamespace(tool_calls=[], content="正在专注做完手头的事。", reasoning_content="")
    model.responses.extend([
        _ModelResponse("c-report", "report_status", json.dumps(
            report_request(act.name, summary="已做一会，继续推进。").model_dump(mode="json"), ensure_ascii=False)),
        SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=stop_msg)]),
    ])
    await host.step()
    assert engine.context.state.current_time > now
    assert any(e.event_type == "action_requested" and e.data.get("tool_name") == "report_status"
               for e in engine.context.store.iter_events(engine.context.store.last_event_seq))
