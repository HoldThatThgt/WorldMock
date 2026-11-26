"""日会话连续上下文、continue_activity、lookup_targets、阈值 Compaction、跨日重置与冲突回滚验收测试。"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from tests.engine_fixture import engine_factory  # noqa: F401
from tests.integration.runtime.current_contract_fixtures import ScriptedModel, decide, resolved_binding  # noqa: F401,E402
from tests.integration.runtime.test_life_agent_protocol import _ModelResponse, _MultipleModelResponse  # noqa: E402
from tests.life.conftest import (  # noqa: E402
    MAIN_TOOL,
    SIDE_TOOL,
    activity_name,
    advance_next,
    advance_next_async,
    advance_until_ready,
    conclude_request,
    continue_request,
    do_at,
    main_arguments,
    side_arguments,
    stage_day_plan,
    stub_day_session,
)
from tests.life.environment_runtime import act, observe_only, until_ended  # noqa: E402
from worldmock.agents.life_agent import LifeAgent, person_inputs  # noqa: E402
from worldmock.agents.prompt import prompt_capacity  # noqa: E402
from worldmock.agents.prompt.life_prompt import user_layer  # noqa: E402
from worldmock.agents.protocol import agent_loop as agent_loop_mod  # noqa: E402
from worldmock.foundation.errors import PreconditionError  # noqa: E402
from worldmock.simulation_engine.decisions import merger as merger_mod  # noqa: E402
from worldmock.simulation_engine.observation.memory.days import published_today  # noqa: E402
from worldmock.state.interaction.requests import LookupTargetsRequest  # noqa: E402

_MULTI_AGENT_ENV = Path(__file__).resolve().parents[2] / "fixtures" / "multi-agent-environment"


@pytest.fixture(autouse=True)
def _fixed_context_window(monkeypatch) -> None:
    """固定供应商上下文窗口以专注验证日会话契约。"""
    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model, metadata: metadata.context_window)


def _assert_single_user_message(request: dict) -> None:
    """校验每次模型请求仅在 messages[1] 有一条 role='user' 消息。"""
    roles = [msg["role"] for msg in request["messages"]]
    assert roles[:2] == ["system", "user"]
    assert "user" not in roles[2:]


@pytest.mark.asyncio
async def test_multi_wakeup_day_session_grows_prefix_byte_identically_with_pair_deltas(engine_factory) -> None:
    """跨唤醒日会话逐字节沿用历史前缀并以 role='tool' 追加环境差量（含成组双边界回填与 prompt_cache_key）。"""
    engine = engine_factory(mutate=lambda _p, c: (
        c["models"]["reasoner"].__setitem__("prompt_cache_key", True),
        c.__setitem__("reconsideration_interval_seconds", 60),
    ))
    stage_day_plan(engine)
    run_id = engine.context.state.run_id

    # 唤醒 1：日初建立 [system, user]，单条 start_main_activity 退出
    _, [req1], _, [res1] = await decide(engine, "w1", [
        _ModelResponse("c-w1", MAIN_TOOL, json.dumps(main_arguments("interact", "家", "测试休息"), ensure_ascii=False)),
    ])
    _assert_single_user_message(req1)
    assert req1["extra_body"]["prompt_cache_key"] == f"{run_id}:a1:2026-01-05"
    ds1 = engine.context.state.agents[1].day_session
    assert ds1 is not None and ds1.local_date == "2026-01-05"
    assert ds1.pending_boundary_calls == ("c-w1",)
    assert len(ds1.messages) == 3 and ds1.messages[-1]["role"] == "assistant"

    # 唤醒 2：活动结束后收尾，再以 start_main_activity + start_side_activity 成组退出
    assert advance_until_ready(engine)
    rest_name = activity_name(engine, res1.activity_id)
    _, reqs2, _, _ = await decide(engine, "w2", [
        _ModelResponse("c-conclude", "conclude_activity", json.dumps(conclude_request(rest_name).model_dump(mode="json"),
                                                                   ensure_ascii=False)),
        _MultipleModelResponse([
            ("c-w2-main", MAIN_TOOL, json.dumps(main_arguments("interact", "家", "测试闲聊"), ensure_ascii=False)),
            ("c-w2-side", SIDE_TOOL, json.dumps(side_arguments("interact", "哔哩哔哩"), ensure_ascii=False)),
        ]),
    ])
    req2_a, req2_b = reqs2
    _assert_single_user_message(req2_a)
    _assert_single_user_message(req2_b)
    assert req2_a["messages"][:2] == req1["messages"]
    assert req2_a["messages"][2]["role"] == "assistant"
    assert req2_a["messages"][3]["role"] == "tool" and req2_a["messages"][3]["tool_call_id"] == "c-w1"
    delta1 = json.loads(req2_a["messages"][3]["content"])
    assert delta1["ok"] is True and "activity_id" not in json.dumps(delta1, ensure_ascii=False)
    assert delta1["result"]["status"] == "settled" and "boundary" in delta1 and "changes" in delta1
    assert "记忆层" in delta1["changes"] and "今天_append" in delta1["changes"]["记忆层"]
    assert req2_b["messages"][:len(req2_a["messages"])] == req2_a["messages"]
    ds2 = engine.context.state.agents[1].day_session
    assert ds2 is not None and ds2.pending_boundary_calls == ("c-w2-main", "c-w2-side")

    # 唤醒 3：成组退出后回填两条配对 role='tool'，前条仅回执、末条带完整 boundary 与 changes
    assert advance_until_ready(engine)
    _, [req3], _, _ = await decide(engine, "w3", [
        _ModelResponse("c-w3", "continue_activity", json.dumps({"reason": None})),
    ])
    _assert_single_user_message(req3)
    assert req3["messages"][:len(req2_b["messages"])] == req2_b["messages"]
    pair_tools = req3["messages"][-2:]
    assert [m["role"] for m in pair_tools] == ["tool", "tool"]
    assert [m["tool_call_id"] for m in pair_tools] == ["c-w2-main", "c-w2-side"]
    first_payload, second_payload = json.loads(pair_tools[0]["content"]), json.loads(pair_tools[1]["content"])
    assert first_payload["ok"] is True and "boundary" not in first_payload and "changes" not in first_payload
    assert second_payload["ok"] is True and "boundary" in second_payload and "changes" in second_payload

    # 对照默认 prompt_cache_key=False 时不注入 extra_body.prompt_cache_key
    default_engine = engine_factory()
    stage_day_plan(default_engine)
    _, [req_default], _, _ = await decide(default_engine, "no-key", [
        _ModelResponse("c-def", MAIN_TOOL, json.dumps(main_arguments("interact", "家", "测试休息"), ensure_ascii=False)),
    ])
    assert "prompt_cache_key" not in req_default["extra_body"]


@pytest.mark.asyncio
async def test_continue_activity_preconditions_silent_omission_and_reasoned_entry(engine_factory) -> None:
    """continue_activity 拒绝无进行中/待收尾状态；无 reason 静默跳过 TodayEntry，带 reason 写入「续行：...」。"""
    engine = engine_factory(mutate=lambda _p, c: c.__setitem__("reconsideration_interval_seconds", 300))
    stage_day_plan(engine)

    # 无进行中活动时拒绝
    _, no_active = act(engine, "cont-idle", continue_request())
    assert no_active.status == "rejected" and no_active.error_code == "ACTIVITY_PRECONDITION_UNMET"

    # 开始并推进至待收尾时拒绝
    _, started = act(engine, "start-rest", do_at("家", "测试休息"))
    assert started.status == "in_progress"
    until_ended(engine, started.activity_id)
    assert engine.scheduling_state().ready_agent_ids == (1,)
    _, pending_refused = act(engine, "cont-pending", continue_request())
    assert pending_refused.status == "rejected" and pending_refused.error_code == "ACTIVITY_CONCLUSION_REQUIRED"

    # 收尾并重新开始测试休息，在重估时刻分别执行静默续行与带原因续行
    rest_name = activity_name(engine, started.activity_id)
    act(engine, "conclude", conclude_request(rest_name), "conclude_activity")
    _, active_rest = act(engine, "rest-long", do_at("家", "测试休息"))
    assert active_rest.status == "in_progress"
    assert advance_until_ready(engine)

    [w_silent] = engine.prepare_barrier((1,))
    sb_silent = engine.open_sandbox(w_silent)
    binding_silent = resolved_binding(ScriptedModel([
        _ModelResponse("c-silent", "continue_activity", json.dumps({"reason": None})),
    ]), frozen=engine.context.inputs)
    out_silent = await LifeAgent(person_inputs(engine.context.inputs.persona_by_id[1])).decide_wakeup(
        w_silent, sb_silent, binding_silent,
    )
    assert out_silent.status == "completed"
    engine.commit_barrier([out_silent])
    today_after_silent = published_today(
        engine.context.inputs, engine.context.store, 1,
        engine.context.state.current_time, engine.context.store.last_event_seq,
    )
    assert w_silent.wakeup_id not in {entry.wakeup_id for entry in today_after_silent.entries}

    assert advance_until_ready(engine)
    [w_reason] = engine.prepare_barrier((1,))
    sb_reason = engine.open_sandbox(w_reason)
    binding_reason = resolved_binding(ScriptedModel([
        _ModelResponse("c-reason", "continue_activity", json.dumps({"reason": "继续休息一会儿。"}, ensure_ascii=False)),
    ]), frozen=engine.context.inputs)
    out_reason = await LifeAgent(person_inputs(engine.context.inputs.persona_by_id[1])).decide_wakeup(
        w_reason, sb_reason, binding_reason,
    )
    engine.commit_barrier([out_reason])
    today_after_reason = published_today(
        engine.context.inputs, engine.context.store, 1,
        engine.context.state.current_time, engine.context.store.last_event_seq,
    )
    reasoned = [entry for entry in today_after_reason.entries if entry.wakeup_id == w_reason.wakeup_id]
    assert len(reasoned) == 1
    assert reasoned[0].shown["自述"] == ["续行：继续休息一会儿。"]
    assert reasoned[0].shown["类别"] == "生活"


@pytest.mark.asyncio
async def test_lookup_targets_is_zero_time_zero_footprint_and_persists_loaded_keys(engine_factory) -> None:
    """lookup_targets 零耗时且零 app_use/action 足迹，将命中目标键写入 loaded_target_keys 并持续出现在工具层。"""
    engine = engine_factory()
    stage_day_plan(engine)
    initial_obs = observe_only(engine, "init")
    initial_names = {row["name"] for row in initial_obs["tool_layer"]["targets"]}
    assert "柠檬茶" not in initial_names and "三杯咖啡" not in initial_names
    assert {"家", "公司", "快递驿站"} <= initial_names

    t_before = engine.context.state.current_time
    _, reqs, answer, _ = await decide(engine, "lookup-flow", [
        _MultipleModelResponse([
            ("q-prod", "lookup_targets", json.dumps({"kind": "product", "keyword": "柠檬茶", "limit": 10, "offset": 0},
                                                    ensure_ascii=False)),
            ("q-loc", "lookup_targets", json.dumps({"kind": "location", "keyword": "咖啡", "limit": 10, "offset": 0},
                                                   ensure_ascii=False)),
        ]),
        _ModelResponse("c-rest", MAIN_TOOL, json.dumps(main_arguments("interact", "家", "测试休息"), ensure_ascii=False)),
    ])
    assert engine.context.state.current_time == t_before
    assert answer == [MAIN_TOOL]
    events = list(engine.context.store.iter_events(engine.context.store.last_event_seq))
    assert not any(ev.event_type == "action_requested" and ev.data.get("tool_name") == "lookup_targets"
                   for ev in events)
    assert not any(ev.data.get("kind") == "app_foreground" for ev in events)

    prod_msg = json.loads(reqs[1]["messages"][-2]["content"])
    loc_msg = json.loads(reqs[1]["messages"][-1]["content"])
    assert prod_msg["ok"] is True and loc_msg["ok"] is True
    assert set(prod_msg["result"]) == {"total", "has_more", "next_offset", "targets", "loaded_count"}
    assert prod_msg["result"]["targets"][0]["name"] == "柠檬茶"
    assert loc_msg["result"]["targets"][0]["name"] == "三杯咖啡"
    for forbidden in ("goods_id", "location_id", "app_id"):
        assert forbidden not in reqs[1]["messages"][-2]["content"]
        assert forbidden not in reqs[1]["messages"][-1]["content"]

    ds = engine.context.state.agents[1].day_session
    assert ds is not None
    assert ("product", "lemon-tea") in ds.loaded_target_keys
    assert ("location", "cafe") in ds.loaded_target_keys

    # 下一次观察继续保留已加载目标
    next_obs = observe_only(engine, "after-lookup")
    next_names = {row["name"] for row in next_obs["tool_layer"]["targets"]}
    assert {"柠檬茶", "三杯咖啡"} <= next_names

    # 直接沙盒调用查询也同样合法
    assert advance_until_ready(engine)
    [w_direct] = engine.prepare_barrier((1,))
    sb_direct = engine.open_sandbox(w_direct)
    direct = sb_direct.lookup_targets(LookupTargetsRequest(kind=None, keyword="柠檬茶", limit=5, offset=0))
    assert direct["ok"] is True and direct["result"]["loaded_count"] >= 1
    sb_direct.finish_wakeup(None, stub_day_session(w_direct))


@pytest.mark.asyncio
async def test_threshold_compaction_folds_history_clears_loaded_keys_and_emits_evidence(
    engine_factory, monkeypatch,
) -> None:
    """达到 compact_trigger_tokens 触发日内 Compaction：保留 system、折叠摘要、<=6 组尾部、清空加载目标并写证据。"""
    def _low_trigger(_p, cfg) -> None:
        cfg["models"]["reasoner"]["compact_trigger_tokens"] = 16384
        cfg["reconsideration_interval_seconds"] = 300

    engine = engine_factory(mutate=_low_trigger)
    stage_day_plan(engine)

    # 先完成一段休息并载入目标，构造多组 (assistant, tool) 交互历史（> 6 组）
    await decide(engine, "w-start", [
        _ModelResponse("c-l", "lookup_targets", json.dumps({"kind": "product", "keyword": "柠檬茶",
                                                             "limit": 5, "offset": 0}, ensure_ascii=False)),
        _ModelResponse("c-s", MAIN_TOOL, json.dumps(main_arguments("interact", "家", "测试休息"), ensure_ascii=False)),
    ])
    assert advance_until_ready(engine)
    rest_name = activity_name(engine, next(iter(engine.context.state.activities)))
    await decide(engine, "w-mid", [
        _ModelResponse("g1", "revise_day_plan", '{"goals":[]}'),
        _ModelResponse("g2", "search_memory", '{"category":"全部","time":"今天","keyword":null}'),
        _ModelResponse("g3", "search_memory", '{"category":"全部","time":"今天","keyword":null}'),
        _ModelResponse("g4", "report_status", json.dumps({"items": [{"activity_name": rest_name, "summary": "继续。"}],
                                                          "errands": []}, ensure_ascii=False)),
        _ModelResponse("g5", "revise_day_plan", '{"goals":[]}'),
        _ModelResponse("g6", "continue_activity", '{"reason":null}'),
    ])
    assert advance_until_ready(engine)
    ds_before = engine.context.state.agents[1].day_session
    assert ds_before is not None and ("product", "lemon-tea") in ds_before.loaded_target_keys

    # 在消息尚未含「折叠摘要」且历史 > 2 时模拟越过 compact_trigger_tokens 水位
    real_counter = prompt_capacity.prompt_token_count

    def _high_until_compacted(request: dict) -> int:
        if len(request["messages"]) > 2 and "折叠摘要" not in request["messages"][1]["content"]:
            return 20000
        return real_counter(request)

    monkeypatch.setattr(prompt_capacity, "prompt_token_count", _high_until_compacted)
    monkeypatch.setattr(agent_loop_mod, "prompt_token_count", _high_until_compacted)
    _, reqs, _, _ = await decide(engine, "w-compact", [
        _ModelResponse("g7", "revise_day_plan", '{"goals":[]}'),
        _ModelResponse("g8", "continue_activity", '{"reason":null}'),
    ])

    compacted_req = reqs[0]
    _assert_single_user_message(compacted_req)
    assert compacted_req["messages"][0]["content"] == ds_before.system_prompt
    rebuilt_layers = json.loads(compacted_req["messages"][1]["content"])
    memory_layer = user_layer(rebuilt_layers, "记忆层")
    assert "折叠摘要" in memory_layer
    summary = memory_layer["折叠摘要"]
    assert set(summary) == {
        "folded_through_seq", "folded_until_clock", "completed_activities", "places_visited",
        "purchases", "body_and_balance_notes", "open_commitments",
    }
    tail_assistants = [m for m in compacted_req["messages"][2:] if m["role"] == "assistant"]
    assert 1 <= len(tail_assistants) <= 6

    ds_after = engine.context.state.agents[1].day_session
    assert ds_after is not None and ds_after.compaction_count == 1
    assert ds_after.compacted_through_seq == summary["folded_through_seq"] > 0
    assert ds_after.loaded_target_keys == ()

    compact_events = [ev for ev in engine.context.store.iter_events(engine.context.store.last_event_seq)
                      if ev.event_type == "environment_event" and ev.data.get("kind") == "day_session_compacted"]
    assert len(compact_events) == 1
    ev_data = compact_events[0].data
    assert ev_data["compaction_count"] == 1 and ev_data["compacted_through_seq"] == ds_after.compacted_through_seq
    ops_entries = [json.loads(line) for path in sorted((engine.context.store.run_dir / "logs").glob("ops-*.jsonl"))
                   for line in path.read_text(encoding="utf-8").splitlines()]
    assert any(entry.get("code") == "DAY_SESSION_COMPACTED" for entry in ops_entries)


@pytest.mark.asyncio
async def test_midnight_reset_and_conflict_rollback_day_session_invariants(engine_factory, monkeypatch) -> None:
    """跨午夜结算后清空 day_session；多人物冲突回滚保留本轮唤醒前的 day_session 并丢弃沙盒变更。"""
    def _near_midnight(_p, cfg) -> None:
        cfg["start_time"] = datetime.fromisoformat("2026-01-05T23:00:00+08:00")
        cfg["stop_time"] = datetime.fromisoformat("2026-01-06T08:00:00+08:00")

    engine = engine_factory(mutate=_near_midnight)
    stage_day_plan(engine, submitted=True)
    await decide(engine, "night-sleep", [
        _ModelResponse("c-night", MAIN_TOOL, json.dumps(main_arguments("sleep", "60"), ensure_ascii=False)),
    ])
    ds_day1 = engine.context.state.agents[1].day_session
    assert ds_day1 is not None and ds_day1.local_date == "2026-01-05"

    # 推进跨过本地零点，settle_midnight_tasks 完成后 day_session 置为 None
    while engine.context.state.current_time < int(datetime.fromisoformat("2026-01-06T00:00:00+08:00").timestamp()):
        await advance_next_async(engine)
    assert engine.context.state.agents[1].day_session is None

    # 次日首次唤醒重新装配 [system, init_user]
    while not engine.scheduling_state().ready_agent_ids:
        advance_next(engine)
    _, [req_day2, _], _, _ = await decide(engine, "day2-wake", [
        _ModelResponse("c-day2", "conclude_activity", json.dumps(
            conclude_request(activity_name(engine, next(iter(engine.context.state.activities)))).model_dump(mode="json"),
            ensure_ascii=False)),
        _ModelResponse("c-day2-rest", MAIN_TOOL, json.dumps(
            main_arguments("interact", "家", "测试休息"), ensure_ascii=False)),
    ])
    _assert_single_user_message(req_day2)
    assert [m["role"] for m in req_day2["messages"][:2]] == ["system", "user"]
    assert engine.context.state.agents[1].day_session.local_date == "2026-01-06"

    # 多人物并发冲突回滚：保留唤醒前 day_session，丢弃沙盒中的目标加载与局部会话修改
    multi = engine_factory(environment_root=_MULTI_AGENT_ENV)
    stage_day_plan(multi, agent_id=1)
    stage_day_plan(multi, agent_id=2)
    w1_a, w2_a = multi.prepare_barrier((1, 2))
    s1_a, s2_a = multi.open_sandbox(w1_a), multi.open_sandbox(w2_a)
    s1_a.dispatch_world_tool(MAIN_TOOL, do_at("家", "测试休息"), f"{w1_a.wakeup_id}:action:0001")
    s2_a.dispatch_world_tool(MAIN_TOOL, do_at("家", "测试休息"), f"{w2_a.wakeup_id}:action:0001")
    multi.commit_barrier([
        s1_a.finish_wakeup(None, stub_day_session(w1_a)),
        s2_a.finish_wakeup(None, stub_day_session(w2_a)),
    ])
    assert advance_until_ready(multi)
    pre_wakeup_ds2 = multi.context.state.agents[2].day_session
    assert pre_wakeup_ds2 is not None

    w1_b, w2_b = multi.prepare_barrier((1, 2))
    s1_b, s2_b = multi.open_sandbox(w1_b), multi.open_sandbox(w2_b)
    for aid, sb, wk in ((1, s1_b, w1_b), (2, s2_b, w2_b)):
        done_names = [item.name for item in multi.context.state.activities.values() if item.agent_id == aid]
        sb.dispatch_world_tool("conclude_activity", conclude_request(*done_names), f"{wk.wakeup_id}:action:0001")
    s2_b.lookup_targets(LookupTargetsRequest(kind="product", keyword="柠檬茶", limit=5, offset=0))
    s1_b.dispatch_world_tool(MAIN_TOOL, do_at("家", "测试闲聊"), f"{w1_b.wakeup_id}:action:0002")
    s2_b.dispatch_world_tool(MAIN_TOOL, do_at("家", "测试休息"), f"{w2_b.wakeup_id}:action:0002")
    mutated_ds2 = pre_wakeup_ds2.model_copy(update={"compaction_count": 99})
    o1_b = s1_b.finish_wakeup(None, stub_day_session(w1_b))
    o2_b = s2_b.finish_wakeup(None, mutated_ds2)

    orig_exec = merger_mod.execute_invocation

    def _conflict_on_agent2(context, actions, state, inv, event_ids):
        if inv.agent_id == 2 and inv.tool_name == MAIN_TOOL:
            raise PreconditionError("RESOURCE_CONFLICT", ["resource"])
        return orig_exec(context, actions, state, inv, event_ids)

    monkeypatch.setattr(merger_mod, "execute_invocation", _conflict_on_agent2)
    receipt = multi.commit_barrier([o2_b, o1_b])
    monkeypatch.undo()
    assert receipt.committed_agent_ids == (1,) and receipt.conflict_requeued_agent_ids == (2,)
    agent2 = multi.context.state.agents[2]
    assert agent2.day_session == pre_wakeup_ds2
    assert agent2.next_decision_at == multi.context.state.current_time
    assert "rejection_feedback" in agent2.pending_wake_reasons
