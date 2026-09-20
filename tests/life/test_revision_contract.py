"""《人物真实性修订合同（2026-09-18）》引擎迁移验收：决策节律、观察投影与
社交/日程/多件结算的正反例。基线工厂与 wake_by_mail 见 conftest。"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agentsociety2.society import DirectToolCall

from tests.life.conftest import (
    advance_next,
    advance_until_ready,
    browse_activities,
    context,
    wake_by_mail,
)
from worldmock.life import projection
from worldmock.life.models import (
    ActivityKind,
    ContinueActivityRequest,
    PlanDayRequest,
    PlanSegmentInput,
    ReviseDayPlanRequest,
    SendMessageRequest,
    StartBasicActivityRequest,
    StartSocialActivityRequest,
    StartTargetedActivityRequest,
    WorldState,
)
from worldmock.life.narration import FORBIDDEN_OBSERVATION_KEYS
from worldmock.life.scenario import load_scenario


def _act(engine, round_id: str, tool_name: str, request, agent_id: int = 1):
    """单人物真实轮：观察、登记并结算。"""
    ctx = context(engine, round_id, agent_id)
    engine.begin_decision_round([ctx])
    engine.submit_action(DirectToolCall(ctx, f"action-{round_id}", tool_name, request))
    return engine.commit_decision_round(round_id)[0]


def _commit_staged(engine, staged) -> None:
    """把测试改造过的状态经存储落盘（保持版本连续），再换入引擎。"""
    from worldmock.footprints.models import EventDraft

    staged.version += 1
    engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time,
        visibility="system", data={"kind": "test_state_staged"})])
    engine.state = staged


def _round_ready(engine, round_id, tool, request, agent_id=1):
    """向当前就绪的每个人物提交一轮（其他人物等待 60 秒），返回主角结果。"""
    from tests.life.conftest import submit_round

    ready = list(engine.scheduling_state().ready_agent_ids)
    requests = {}
    for rid in ready:
        requests[rid] = (tool, request) if rid == agent_id else (
            "start_basic_activity",
            StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=60))
    _, results = submit_round(engine, requests, round_id)
    return next(item for item in results if item.agent_id == agent_id)


def _events(engine, event_type: str, agent_id: int = 1):
    return [event for event in engine.store.read_events(engine.store.last_event_seq)
            if event.event_type == event_type and event.agent_id == agent_id]


def test_silent_boundary_settles_without_decision_events(engine_factory) -> None:
    """静默边界照常结算（activity_progress + decision_offered=false）且不产生决策事件。"""
    engine = engine_factory()
    _act(engine, "wait-long", "start_basic_activity",
         StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=7200))
    boundary = engine.state.current_time + 900
    advance_next(engine)  # 首个 900 边界：无唤醒条件即静默
    assert not engine.scheduling_state().ready_agent_ids
    progress = [event for event in _events(engine, "activity_progress") if event.agent_id == 1]
    assert progress, "settlement boundary must leave activity_progress evidence"
    assert progress[-1].data["decision_offered"] is False
    assert progress[-1].data["wake_reasons"] == []
    for event_type in ("decision_started", "observation", "model_request", "memory_updated"):
        at_boundary = [event for event in _events(engine, event_type)
                       if event.occurred_at >= boundary]
        assert at_boundary == []


def test_offered_boundary_records_actual_wake_reasons(engine_factory) -> None:
    """唤醒命中的边界记录 decision_offered=true 与实际唤醒原因。"""
    engine = engine_factory()
    _act(engine, "wait-short", "start_basic_activity",
         StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=900))
    advance_next(engine)  # 计划结束唤醒
    assert engine.scheduling_state().ready_agent_ids == (1,)
    progress = [event for event in _events(engine, "activity_progress")][-1]
    assert progress.data["decision_offered"] is True
    assert "planned_end" in progress.data["wake_reasons"]


def test_engine_wake_reasons_cover_each_trigger_class(engine_factory) -> None:
    """每类唤醒条件经引擎真实触发一次（run_start/段界/消息/配送/重估/拒绝）。"""
    engine = engine_factory()
    _act(engine, "plan", "plan_day",
         _anchored_plan(engine, (900, 1800, 2700, 3600, 4500), span=60))
    first = _events(engine, "decision_started")
    assert first and first[-1].data["wake_reasons"] == ["run_start"]
    advance_next(engine)
    assert engine.state.agents[1].pending_wake_reasons == ["plan_block_boundary"]
    _act(engine, "hold-1", "start_basic_activity",
         StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=3600))
    # message_arrival：邮件到达唤醒
    wake_by_mail(engine)
    advance_next(engine)
    assert "message_arrival" in engine.state.agents[1].pending_wake_reasons
    _act(engine, "hold-2", "start_basic_activity",
         StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=3600))
    # delivery_arrival：配送到达唤醒
    staged = engine.state.model_copy(deep=True)
    staged.agents[1] = staged.agents[1].model_copy(
        update={"pending_wake_reasons": []}, deep=True)
    from worldmock.life.models import DeliveryState, FulfillmentKind
    from worldmock.scheduling.events import ScheduledEvent, ScheduledKind
    order = DeliveryState(order_id="order-wake", agent_id=1, product_id="food-x",
                          quantity=1, destination_id="home",
                          fulfillment=FulfillmentKind.DELIVERY,
                          created_at=staged.current_time,
                          expected_at=staged.current_time + 60, status="pending",
                          total_price_fen=100)
    staged.deliveries[order.order_id] = order
    staged.pending_events.append(ScheduledEvent(
        scheduled_id="delivery-wake", kind=ScheduledKind.DELIVERY_ARRIVAL,
        due_at=order.expected_at, sequence=900, agent_id=1,
        data={"order_id": order.order_id}))
    _commit_staged(engine, staged)
    advance_next(engine)
    assert "delivery_arrival" in engine.state.agents[1].pending_wake_reasons
    _act(engine, "hold-3", "start_basic_activity",
         StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=3600))
    # reconsideration_interval：距上次决策满 5400 秒
    staged = engine.state.model_copy(deep=True)
    staged.agents[1] = staged.agents[1].model_copy(
        update={"pending_wake_reasons": [], "last_decision_time":
                staged.current_time - 5400}, deep=True)
    _commit_staged(engine, staged)
    advance_next(engine)
    assert "reconsideration_interval" in engine.state.agents[1].pending_wake_reasons
    # rejection_feedback：业务拒绝后一秒边界
    _act(engine, "reject", "start_targeted_activity",
         StartTargetedActivityRequest(activity_type="watch", target_id="missing",
                                      planned_duration_seconds=60, playback_speed=1))
    advance_next(engine)
    assert "rejection_feedback" in engine.state.agents[1].pending_wake_reasons


def test_sleep_is_not_interrupted_by_reconsideration(engine_factory) -> None:
    """睡眠不受重估间隔影响：只在计划结束或唤醒事件提供决策。"""
    engine = engine_factory()
    start = engine.state.current_time
    _act(engine, "sleep-9k", "start_basic_activity",
         StartBasicActivityRequest(activity_type="sleep", planned_duration_seconds=9000))
    while engine.state.current_time < start + 9000:  # 跨过 5400 秒重估点
        advance_next(engine)
        if engine.state.current_time < start + 9000:
            assert not engine.scheduling_state().ready_agent_ids, (
                "sleep must not be interrupted by the reconsideration interval")
    advance_until_ready(engine)
    assert engine.state.agents[1].current_activity_id is None
    assert engine.state.activities["activity-action-sleep-9k"].status.value == "completed"
    assert "planned_end" in engine.state.agents[1].pending_wake_reasons


def test_felt_band_hysteresis_uses_last_decision_reference(engine_factory) -> None:
    """体感分级滞回：结算重算产生的中间分级与决策参照一致时不构成唤醒。"""
    engine = engine_factory()
    staged = engine.state.model_copy(deep=True)
    from worldmock.life import felt as felt_module
    from worldmock.life.projection import body_projection
    hour = projection.hour_of_day(staged.current_time, engine.scenario.timezone)
    levels = body_projection(staged.agents[1], hour)["levels"]
    staged.agents[1] = staged.agents[1].model_copy(
        update={"felt_bands_at_last_decision": levels}, deep=True)
    staged.version += 1
    engine.state = staged
    _act(engine, "steady-wait", "start_basic_activity",
         StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=3600))
    before = engine.state.agents[1].body.entertainment_need
    advance_next(engine)
    settled = engine.state.agents[1].body.entertainment_need
    assert settled > before, "settlement must keep evolving the numeric value"
    assert engine.scheduling_state().ready_agent_ids == (), (
        "band unchanged versus the decision reference must stay silent")
    assert engine.state.agents[1].pending_wake_reasons == []


def test_observation_projection_blacklist_and_product_ttl(engine_factory) -> None:
    """观察事件不产生黑名单字段；商品浏览 1800 秒后清空。"""
    engine = engine_factory()
    ctx = context(engine, "observe-clean")
    engine.begin_decision_round([ctx])
    data = engine.observe(ctx)
    engine.abort_decision_round("observe-clean", "blacklist probe")
    import json as json_module

    prompt_payload = json_module.dumps(data["scene"], default=str) + json_module.dumps(
        data["facts"], default=str)
    for key in FORBIDDEN_OBSERVATION_KEYS:
        assert key not in data["scene"] and key not in data["facts"], (
            f"blacklisted field {key} leaked into prompt inputs")
        assert f'"{key}"' not in prompt_payload
    assert "end_time" not in data and "world_state_version" not in data
    # facts 路线只保留五键白名单
    for route in data["facts"]["routes"]:
        assert set(route) <= {"destination", "mode", "minutes", "fare", "feasible"}
    # 商品 TTL：浏览后 1800 秒内可见，超时清空
    _act(engine, "browse-ttl", "start_targeted_activity",
         StartTargetedActivityRequest(activity_type="browse", target_id="product_catalog",
                                      planned_duration_seconds=60, playback_speed=1))
    advance_until_ready(engine)  # 浏览完成边界登记可见集与浏览时刻
    staged = engine.state.model_copy(deep=True)
    staged.current_time = staged.agents[1].product_browsed_at + 1801
    staged.version += 1
    engine.state = staged
    visible = projection.visible_products(engine.state, engine.state.agents[1],
                                          engine.scenario)
    assert visible == [], "products must clear 1800s after browse"


def test_work_material_reveal_capped_at_2000_chars_per_decision(engine_factory) -> None:
    """工作材料只揭示自上次决策以来新读区间，单次累计 ≤2000 字符。"""
    engine = engine_factory()
    agent = engine.state.agents[1].model_copy(
        update={"material_reveals": [
            {"task_id": "task-a", "at": 200, "start_char": 0, "end_char": 1500, "chars": 1500},
            {"task_id": "task-a", "at": 300, "start_char": 1500, "end_char": 3400, "chars": 1900},
            {"task_id": "task-b", "at": 100, "start_char": 0, "end_char": 900, "chars": 900},
        ], "last_decision_time": 150}, deep=True)
    staged = engine.state.model_copy(deep=True)
    staged.agents[1] = agent
    staged.version += 1
    engine.state = staged
    ranges = projection.new_material_ranges(engine.state.agents[1])
    assert sum(row["chars"] for row in ranges["task-a"]) == 2000  # 截断到上限
    assert not ranges.get("task-b")  # 上次决策前的不重复揭示


def test_subtitle_does_not_repeat_across_boundary(engine_factory) -> None:
    """横跨结算边界的同一条字幕只交付一次。"""
    engine = engine_factory()
    from worldmock.life.models import StartTargetedActivityRequest
    _act(engine, "watch-dedupe", "start_targeted_activity",
         StartTargetedActivityRequest(
             activity_type="watch", target_id="sintel-en-subtitles",
             planned_duration_seconds=7200, playback_speed=1))
    wake_by_mail(engine)
    advance_next(engine)
    _act(engine, "keep-watching", "continue_activity",
         ContinueActivityRequest(activity_id="activity-action-watch-dedupe"))
    advance_next(engine)
    progresses = [event for event in _events(engine, "activity_progress")]
    delivered: dict[int, set[int]] = {}
    for event in progresses:
        cues = event.data["activity"]["data"].get("revealed", {}).get("cues", [])
        for cue in cues:
            delivered.setdefault(cue["cue_id"], set()).add(event.data["interval_end"])
    repeated = {cue_id: stamps for cue_id, stamps in delivered.items() if len(stamps) > 1}
    assert repeated == {}, f"cues delivered on multiple boundaries: {sorted(repeated)}"


def test_plan_day_registers_self_commitment_and_rejects_midday(engine_factory) -> None:
    """plan_day 正例登记为自我承诺；非首醒决策与重复提交被拒绝。"""
    engine = engine_factory()
    plan = _anchored_plan(engine, (0, 1200, 2400, 3600, 4800))
    result = _act(engine, "plan-ok", "plan_day", plan)
    assert result.status.value == "completed"
    assert engine.state.agents[1].day_plan is not None
    assert len(engine.state.agents[1].day_plan.segments) == 5
    # 反例：同日重复提交
    advance_until_ready(engine)
    again = _act(engine, "plan-dup", "plan_day", plan)
    assert again.status.value == "rejected"
    assert again.error_code in {"PLAN_ALREADY_SUBMITTED", "PLAN_DAY_ONLY_AT_DAY_START"}


def test_plan_day_reopens_on_day_two_after_rollover(engine_factory) -> None:
    """跨日边界把 day_plan 归零：第二天首次醒来 plan_day 工具重新开放且可再提交。

    docs/dev/SPEC-life-realism.md「日程骨架」"每天第一次醒来的决策必须先提交 plan_day"要求计划按日归零：
    day1 提交后跨日睡眠，day2 醒来时观察的 day_plan 必须为空（只反映当日
    计划），人物侧 plan_day 契约重新开放，引擎侧接受新一天的首个提交。
    """
    from datetime import datetime as dt
    from zoneinfo import ZoneInfo

    from custom.agents.life_agent import (
        _available_action_models,
        _visible_action_contracts,
        _visible_plan_day_contract,
        _visible_revise_plan_contract,
    )

    engine = engine_factory()
    day1 = _anchored_plan(engine, (0, 1200, 2400, 3600, 4800))
    first = _act(engine, "plan-day1", "plan_day", day1)
    assert first.status.value == "completed"
    assert engine.state.agents[1].day_plan is not None
    advance_until_ready(engine)
    _act(engine, "sleep-cross", "start_basic_activity",
         StartBasicActivityRequest(activity_type="sleep", planned_duration_seconds=86_400))
    for _ in range(400):  # 穿过整夜睡眠的静默边界直到跨日醒来
        if engine.scheduling_state().ready_agent_ids:
            break
        advance_next(engine)
    local = dt.fromtimestamp(engine.state.current_time, ZoneInfo(engine.scenario.timezone))
    assert local.date().isoformat() == "2026-01-06"
    assert "wake_from_sleep" in engine.state.agents[1].pending_wake_reasons
    assert engine.state.agents[1].day_plan is None, "跨日边界必须把前一天的 day_plan 归零"
    ctx = context(engine, "observe-day2")
    engine.begin_decision_round([ctx])
    data = engine.observe(ctx)
    engine.abort_decision_round("observe-day2", "probe")
    assert data["facts"]["day_plan"] is None, "观察的 day_plan 只反映当日计划"
    assert _visible_plan_day_contract(data) != {}
    assert _visible_revise_plan_contract(data) == {}
    contracts = _visible_action_contracts(data)
    available = _available_action_models({}, {"active": None, "paused_ids": []}, contracts)
    assert "plan_day" in available
    day2 = _anchored_plan(engine, (0, 1200, 2400, 3600, 4800))
    second = _act(engine, "plan-day2", "plan_day", day2)
    assert second.status.value == "completed"
    assert sorted(engine.state.agents[1].plan_submitted_dates) == ["2026-01-05", "2026-01-06"]


def test_plan_day_retry_allowed_after_engine_rejection(engine_factory) -> None:
    """引擎拒绝（段重叠，本地校验不覆盖）后的一秒重试边界上 plan_day 仍可提交。

    重试边界的唤醒原因是 rejection_feedback；plan_day 的可见性与引擎判据
    都必须把它当作当日首次计划决策的延续，否则一次骨架拒绝=当天永久锁死。
    """
    from custom.agents.life_agent import _visible_plan_day_contract

    engine = engine_factory()
    base = engine.state.current_time
    overlapping = PlanDayRequest(segments=tuple(
        PlanSegmentInput(start=base + off, end=base + off + 600,
                         intent_kind="工作", note="") for off in (0, 1200, 2400, 2700, 4800)))
    rejected = _act(engine, "plan-overlap", "plan_day", overlapping)
    assert rejected.status.value == "rejected"
    assert rejected.error_code == "PLAN_SEGMENTS_INVALID"
    advance_next(engine)  # 拒绝后的一秒重试边界
    assert "rejection_feedback" in engine.state.agents[1].pending_wake_reasons
    ctx = context(engine, "observe-retry")
    engine.begin_decision_round([ctx])
    data = engine.observe(ctx)
    engine.abort_decision_round("observe-retry", "probe")
    assert _visible_plan_day_contract(data) != {}, "重试边界上 plan_day 工具必须对人物可见"
    valid = _anchored_plan(engine, (0, 1200, 2400, 3600, 4800))
    retry = _act(engine, "plan-retry", "plan_day", valid)
    assert retry.status.value == "completed", (
        f"拒绝后的当日重试被拒：{retry.error_code}")
    assert engine.state.agents[1].day_plan is not None


def test_revise_day_plan_only_rewrites_unstarted_segments(engine_factory) -> None:
    """revise_day_plan 正例改写未开始段；已开始段被拒绝。"""
    engine = engine_factory()
    base = engine.state.current_time
    _act(engine, "plan-base", "plan_day", _anchored_plan(engine, (0, 1200, 2400, 3600, 4800)))
    advance_until_ready(engine)  # 第一段已开始
    tail = ReviseDayPlanRequest(segments=(
        PlanSegmentInput(start=base + 600, end=base + 700, intent_kind="娱乐", note="已开始"),
        PlanSegmentInput(start=base + 3000, end=base + 5400, intent_kind="休息", note=""),
    ))
    rejected = _act(engine, "revise-started", "revise_day_plan", tail)
    assert rejected.status.value == "rejected"
    assert rejected.error_code == "PLAN_SEGMENT_ALREADY_STARTED"
    advance_next(engine)  # 拒绝后的一秒反应边界
    anchor = _next_local_midnight(engine, base)
    future = ReviseDayPlanRequest(segments=(
        PlanSegmentInput(start=base + 3600, end=base + 4200, intent_kind="家务", note=""),
        PlanSegmentInput(start=base + 4200, end=anchor, intent_kind="睡眠", note=""),))
    ok = _act(engine, "revise-future", "revise_day_plan", future)
    assert ok.status.value == "completed"
    assert any(segment.intent_kind == "家务"
               for segment in engine.state.agents[1].day_plan.segments)


def test_send_message_delivers_inbox_and_arrival_event(engine_factory) -> None:
    """send_message 瞬时登记：收件方下次观察 inbox 可见并留 message_arrival。"""
    from tests.life.conftest import submit_round
    from worldmock.life.models import StartBasicActivityRequest as Basic

    engine = engine_factory("multi-person.json")
    _, results = submit_round(engine, {
        1: ("send_message", SendMessageRequest(recipient_id="agent:2", text="中午一起吃饭？")),
        2: ("start_basic_activity", Basic(activity_type="wait", planned_duration_seconds=60)),
    }, "say-hi")
    result = next(item for item in results if item.agent_id == 1)
    assert result.status.value == "completed"
    arrivals = [event for event in engine.store.read_events(engine.store.last_event_seq)
                if event.data.get("kind") == "message_arrival"]
    assert arrivals and arrivals[-1].data["recipient_id"] == "agent:2"
    assert any(message.kind == "direct" and message.sender_id == "agent:1"
               for message in engine.state.agents[2].inbox.values())
    from tests.life.conftest import advance_until_ready

    while tuple(engine.scheduling_state().ready_agent_ids) != (2,):
        advance_until_ready(engine)
    ctx = context(engine, "see-inbox", 2)
    engine.begin_decision_round([ctx])
    data = engine.observe(ctx)
    engine.abort_decision_round("see-inbox", "probe")
    assert data["facts"]["inbox"], "recipient must see the message in the next observation"


def test_chat_requires_copresence_and_settles_relief(engine_factory) -> None:
    """交谈正例：共同在场且对方可交谈（对方收到 chat_invite）；不在场被拒绝。"""
    from tests.life.conftest import submit_round
    from worldmock.life.models import StartBasicActivityRequest as Basic

    engine = engine_factory("multi-person.json")
    _, results = submit_round(engine, {
        1: ("start_social_activity", StartSocialActivityRequest(
            kind="chat", partner_id="agent:2", duration_minutes=15)),
        2: ("start_basic_activity", Basic(activity_type="wait", planned_duration_seconds=60)),
    }, "chat-ok-round")
    ok = next(item for item in results if item.agent_id == 1)
    assert ok.status.value == "in_progress"
    activity = engine.state.activities[ok.activity_id]
    assert activity.kind == ActivityKind.CHAT
    assert activity.data["partner_id"] == "agent:2"
    assert any(message.kind == "chat_invite" for message in engine.state.agents[2].inbox.values())
    # 反例：对方不在场（去了办公室）即拒绝
    staged = engine.state.model_copy(deep=True)
    staged.agents[2] = staged.agents[2].model_copy(update={"location_id": "office"}, deep=True)
    _commit_staged(engine, staged)
    while 1 not in engine.scheduling_state().ready_agent_ids:
        ready_now = list(engine.scheduling_state().ready_agent_ids)
        if ready_now:
            _round_ready(engine, f"hold-{engine.state.current_time}",
                         "start_basic_activity",
                         StartBasicActivityRequest(activity_type="wait",
                                                   planned_duration_seconds=3600))
        assert advance_until_ready(engine), "agent 1 never regained a decision"
    staged = engine.state.model_copy(deep=True)
    staged.agents[2] = staged.agents[2].model_copy(update={"location_id": "office"}, deep=True)
    _commit_staged(engine, staged)
    rejected = _round_ready(engine, "chat-remote", "start_social_activity",
                            StartSocialActivityRequest(kind="chat", partner_id="agent:2",
                                                       duration_minutes=15))
    assert rejected.status.value == "rejected"


def test_call_facing_contact_validates_duration(engine_factory) -> None:
    """通话面向联系人：10-60 分钟合同由 social 域校验（越界拒绝）。"""
    engine = engine_factory("multi-person.json")
    staged = engine.state.model_copy(deep=True)
    from worldmock.life.scenario import AgentConfig
    from worldmock.life.social import Contact
    config = staged.scenario if False else None  # placeholder to satisfy lint
    from worldmock.life import social as social_domain
    contacts = (Contact(contact_id="contact-mother", salutation="妈妈", relation="家人",
                        closeness=25, channels=("phone",), reply_policy="none"),)
    document = engine.semantics.document.model_copy(update={
        "agents": tuple(item.model_copy(update={"contacts": contacts})
                        if item.agent_id == 1 else item
                        for item in engine.semantics.document.agents)})
    runtime = engine.scenario.model_copy(update={
        "agents": tuple(item.model_copy(update={"contacts": contacts})
                        if item.agent_id == 1 else item
                        for item in engine.scenario.agents)})
    engine.semantics = engine.semantics.__class__(
        document=document, catalog=engine.semantics.catalog, scenario=runtime,
        body=engine.semantics.body, refs=engine.semantics.refs,
        accounts=engine.semantics.accounts)
    engine.scenario = runtime
    too_short = _round_ready(engine, "call-short", "start_social_activity",
                             StartSocialActivityRequest(kind="call", partner_id="contact-mother",
                                                        duration_minutes=5))
    assert too_short.status.value == "rejected"
    advance_next(engine)  # 拒绝后的一秒反应边界
    ok = _round_ready(engine, "call-ok", "start_social_activity",
                      StartSocialActivityRequest(kind="call", partner_id="contact-mother",
                                                 duration_minutes=20))
    assert ok.status.value == "in_progress"
    assert engine.state.activities[ok.activity_id].kind == ActivityKind.CALL


def _stage_open_hours(engine, hour: int = 12) -> None:
    """把世界移到营业时段（商家 7-23 营业，正午全开）。"""
    from datetime import datetime as dt
    from zoneinfo import ZoneInfo

    staged = engine.state.model_copy(deep=True)
    local = dt.fromtimestamp(staged.current_time, ZoneInfo(engine.scenario.timezone))
    noon = int(local.replace(hour=hour, minute=0, second=0, microsecond=0).timestamp())
    staged.current_time = noon
    staged.pending_events = [item for item in staged.pending_events if item.due_at > noon]
    for agent_id, agent in staged.agents.items():
        staged.agents[agent_id] = agent.model_copy(update={"next_decision_at": noon}, deep=True)
    _commit_staged(engine, staged)


def test_multi_item_purchase_rejects_whole_order(engine_factory) -> None:
    """多件购买：同一商家一次付款；任一件不可购整单拒绝且不自动删件。"""
    engine = engine_factory()
    from worldmock.life.models import FulfillmentKind, PurchaseItem, PurchaseOrderRequest
    _stage_open_hours(engine)
    _act(engine, "browse-order", "start_targeted_activity",
         StartTargetedActivityRequest(activity_type="browse", target_id="product_catalog",
                                      planned_duration_seconds=60, playback_speed=1))
    advance_until_ready(engine)
    by_merchant: dict[str, list] = {}
    for item in sorted(engine.scenario.products, key=lambda value: value.product_id):
        by_merchant.setdefault(item.merchant_account_id, []).append(item)
    products = max(by_merchant.values(), key=len)[:2]  # 同一商家的前两件
    ok_request = PurchaseOrderRequest(
        items=tuple(PurchaseItem(product_id=item.product_id, quantity=1,
                                 price_version=item.price_version)
                    for item in products),
        fulfillment=FulfillmentKind.DELIVERY, destination_id="home")
    result = _act(engine, "order-ok", "purchase", ok_request)
    assert result.status.value == "completed"
    assert len(result.data["orders"]) == 2
    assert len({order["expected_at"] for order in result.data["orders"]}) == 1  # 一次配送
    assert sum(entry[1] for entry in result.data["resource_changes"].items()
               if entry[0].endswith(products[0].merchant_account_id)) == result.data["total_price_fen"]
    # 反例：第二件报价过期 → 整单拒绝
    engine2 = engine_factory()
    _stage_open_hours(engine2)
    _act(engine2, "browse-stale", "start_targeted_activity",
         StartTargetedActivityRequest(activity_type="browse", target_id="product_catalog",
                                      planned_duration_seconds=60, playback_speed=1))
    advance_until_ready(engine2)
    by_merchant = {}
    for item in sorted(engine2.scenario.products, key=lambda value: value.product_id):
        by_merchant.setdefault(item.merchant_account_id, []).append(item)
    two = max(by_merchant.values(), key=len)[:2]  # 同一商家的前两件
    stale = PurchaseItem(product_id=two[1].product_id, quantity=1, price_version="stale")
    fresh = PurchaseItem(product_id=two[0].product_id, quantity=1,
                         price_version=two[0].price_version)
    rejected = _act(engine2, "order-stale", "purchase", PurchaseOrderRequest(
        items=(fresh, stale), fulfillment=FulfillmentKind.DELIVERY, destination_id="home"))
    assert rejected.status.value == "rejected"
    assert rejected.error_code == "PRICE_VERSION_MISMATCH"
    assert engine2.state.merchant_stock[two[0].product_id] == two[0].initial_stock, (
        "rejected order must not decrement any item")


def test_multi_item_meal_accumulates_energy(engine_factory) -> None:
    """多件进食：时长取最长、热量与效果累加。"""
    engine = engine_factory()
    foods = [item for item in sorted(engine.scenario.products,
                                     key=lambda item: item.product_id)
             if "eat" in item.use_modes and item.energy_kcal_per_unit > 0][:2]
    staged = engine.state.model_copy(deep=True)
    inventory = {"home": {item.product_id: 1.0 for item in foods}}
    staged.agents[1] = staged.agents[1].model_copy(
        update={"inventory": inventory, "location_id": "home"}, deep=True)
    staged.version += 1
    engine.state = staged
    from worldmock.life.models import ConsumeItem, ConsumeRequest
    request = ConsumeRequest(
        items=tuple(ConsumeItem(product_id=item.product_id, quantity=1) for item in foods),
        use_mode="eat", duration_seconds=max(item.use_duration_seconds for item in foods))
    result = _act(engine, "meal-multi", "consume", request)
    assert result.status.value == "in_progress"
    duration = engine.state.activities[result.activity_id].planned_duration_seconds
    assert duration == max(item.use_duration_seconds for item in foods)
    while engine.state.activities[result.activity_id].status.value == "active":
        advance_next(engine)
    agent = engine.state.agents[1]
    energy_gain = agent.body.energy_kcal - engine.semantics.body.initial_energy_kcal
    raw = sum(item.energy_kcal_per_unit for item in foods)
    from worldmock.life import sleep as sleep_module
    expected = raw * sleep_module.food_utilization_efficiency(0)
    assert 0 < energy_gain <= expected  # 热量按最长时长进度累加（效果累加）
    for item in foods:
        assert agent.inventory["home"][item.product_id] == 0


def test_world_state_schema_version_three_rejects_old_checkpoints(engine_factory) -> None:
    """旧检查点（无 schema_version 或版本 2）加载即拒绝；版本 3 是唯一合法结构版本。

    版本 3 引入 `presentation_state`（轮转所需的「上次呈现时刻」）；不保留兼容读取
    路径，旧检查点一律拒载（docs/CONTRACTS.md「派生量字段」）。
    """
    engine = engine_factory()
    legacy = engine.state.model_dump(mode="json")
    legacy.pop("schema_version")
    with pytest.raises(Exception, match="schema_version"):
        WorldState.model_validate(legacy)
    previous = engine.state.model_dump(mode="json")
    previous["schema_version"] = 2
    with pytest.raises(Exception, match="schema_version"):
        WorldState.model_validate(previous)
    fresh = engine.state.model_dump(mode="json")
    assert fresh["schema_version"] == 3
    assert fresh["presentation_state"] == {}


def _next_local_midnight(engine, now: int) -> int:
    """计算场景时区的次日零点（缺省就寝锚点=persona-mapping 默认窗口末端 24:00）。"""
    from datetime import timedelta

    from worldmock.life.engine import _datetime

    local = _datetime(now, engine.scenario.timezone)
    return int((local + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0).timestamp())


def _anchored_plan(engine, offsets: tuple[int, ...], span: int = 600,
                   intent: str = "工作") -> PlanDayRequest:
    """构造末段恰好覆盖到缺省就寝锚点（次日零点）的合法计划（D6 缺口 3 夹具）。"""
    now = engine.state.current_time
    anchor = _next_local_midnight(engine, now)
    segments = [PlanSegmentInput(start=now + offset, end=now + offset + span,
                                 intent_kind=intent, note="")
                for offset in offsets[:-1]]
    segments.append(PlanSegmentInput(start=now + offsets[-1], end=anchor,
                                     intent_kind="休息", note=""))
    return PlanDayRequest(segments=tuple(segments))


def _stage_bedtime_window(engine, window: tuple[float, float]) -> None:
    """给人物 1 声明就寝窗口（B4 人物卡片声明面），并落盘换入引擎。"""
    runtime = engine.scenario.model_copy(update={
        "agents": tuple(item.model_copy(update={"bedtime_window": window})
                        if item.agent_id == 1 else item
                        for item in engine.scenario.agents)})
    engine.scenario = runtime
    staged = engine.state.model_copy(deep=True)
    _commit_staged(engine, staged)


def _observe_current(engine, round_id: str, agent_id: int = 1) -> dict:
    """在当前状态做一次只读观察并返回 data（不结算动作）。"""
    ctx = context(engine, round_id, agent_id)
    engine.begin_decision_round([ctx])
    data = engine.observe(ctx)
    engine.abort_decision_round(round_id, "probe")
    return data


def test_plan_day_rejects_plan_below_bedtime_anchor(engine_factory) -> None:
    """缺口 3：末段早于就寝锚点拒绝；恰好到达通过；缺省锚点留痕在 day_plan.bedtime。"""
    engine = engine_factory()
    _stage_bedtime_window(engine, None)  # B4 落地后场景已声明；本测试钉住缺省取值链
    now = engine.state.current_time
    anchor = _next_local_midnight(engine, now)
    short = PlanDayRequest(segments=tuple(
        PlanSegmentInput(start=now + off, end=now + off + 600,
                         intent_kind="工作", note="") for off in (0, 1200, 2400, 3600, 4800)))
    rejected = _act(engine, "plan-short", "plan_day", short)
    assert rejected.status.value == "rejected", (
        "末段未覆盖到就寝锚点的计划必须被拒绝（空约束修复）")
    assert rejected.error_code == "PLAN_SEGMENTS_INVALID"
    assert "cover until bedtime" in rejected.message

    advance_next(engine)  # 拒绝后的一秒重试边界
    reaching = _anchored_plan(engine, (0, 1200, 2400, 3600, 4800))
    ok = _act(engine, "plan-reach", "plan_day", reaching)
    assert ok.status.value == "completed", f"恰好覆盖到锚点必须通过：{ok.error_code}"
    stored = engine.state.agents[1].day_plan
    assert stored is not None and stored.bedtime == anchor, (
        "缺省取值链留痕：声明缺失时锚点=persona-mapping 默认窗口末端（次日零点）")


def test_plan_day_uses_declared_bedtime_window_end(engine_factory) -> None:
    """缺口 3：人物声明的就寝窗口末端覆盖缺省锚点（22-23 点声明→锚点=23:00）。"""
    engine = engine_factory()
    _stage_bedtime_window(engine, (22.0, 23.0))
    now = engine.state.current_time
    from datetime import datetime as dt
    from zoneinfo import ZoneInfo

    local = dt.fromtimestamp(now, ZoneInfo(engine.scenario.timezone))
    declared_anchor = int(local.replace(hour=23, minute=0, second=0,
                                        microsecond=0).timestamp())
    early = PlanDayRequest(segments=tuple(
        PlanSegmentInput(start=now + off, end=now + off + 600,
                         intent_kind="工作", note="") for off in (0, 1200, 2400, 3600, 4800)))
    rejected = _act(engine, "plan-declared-short", "plan_day", early)
    assert rejected.status.value == "rejected", "声明锚点前结束必须被拒绝"
    advance_next(engine)  # 拒绝后的一秒重试边界
    retry_now = engine.state.current_time
    reaching = PlanDayRequest(segments=tuple(
        [PlanSegmentInput(start=retry_now + off, end=retry_now + off + 600,
                          intent_kind="工作", note="")
         for off in (0, 12000, 24000, 36000)]
        + [PlanSegmentInput(start=retry_now + 48000, end=declared_anchor,
                            intent_kind="休息", note="")]))
    ok = _act(engine, "plan-declared-reach", "plan_day", reaching)
    assert ok.status.value == "completed", f"覆盖到声明锚点必须通过：{ok.error_code}"
    assert engine.state.agents[1].day_plan.bedtime == declared_anchor


def test_plan_day_anchor_follows_real_scenario_declaration(engine_factory) -> None:
    """B4：场景声明的就寝窗口末端成为真实锚点（陈曦 22:30-23:30→锚点=23:30）。"""
    engine = engine_factory()  # white-collar：陈曦声明 bedtime_window (22.5, 23.5)
    assert engine.scenario.agents[0].bedtime_window == (22.5, 23.5), (
        "场景文档的就寝窗口声明必须随组装进入运行时场景")
    now = engine.state.current_time
    from datetime import datetime as dt
    from zoneinfo import ZoneInfo

    local = dt.fromtimestamp(now, ZoneInfo(engine.scenario.timezone))
    declared_anchor = int(local.replace(hour=23, minute=30, second=0,
                                        microsecond=0).timestamp())
    early = PlanDayRequest(segments=tuple(
        PlanSegmentInput(start=now + off, end=now + off + 600,
                         intent_kind="工作", note="") for off in (0, 1200, 2400, 3600, 4800)))
    rejected = _act(engine, "plan-scenario-short", "plan_day", early)
    assert rejected.status.value == "rejected", "末段早于场景声明锚点必须被拒绝"
    assert rejected.error_code == "PLAN_SEGMENTS_INVALID"
    assert "cover until bedtime" in rejected.message
    advance_next(engine)  # 拒绝后的一秒重试边界
    retry_now = engine.state.current_time
    reaching = PlanDayRequest(segments=tuple(
        [PlanSegmentInput(start=retry_now + off, end=retry_now + off + 600,
                          intent_kind="工作", note="")
         for off in (0, 12000, 24000, 36000)]
        + [PlanSegmentInput(start=retry_now + 48000, end=declared_anchor,
                            intent_kind="休息", note="")]))
    ok = _act(engine, "plan-scenario-reach", "plan_day", reaching)
    assert ok.status.value == "completed", f"覆盖到场景声明锚点必须通过：{ok.error_code}"
    assert engine.state.agents[1].day_plan.bedtime == declared_anchor, (
        "day_plan.bedtime 留痕必须等于场景声明的窗口末端（23:30）")


def test_facts_day_plan_hidden_after_bedtime_matches_scene(engine_factory) -> None:
    """缺口 5：bedtime→midnight 窗口 scene 与 facts 的 day_plan 同步隐藏。"""
    from datetime import datetime as dt
    from zoneinfo import ZoneInfo

    engine = engine_factory()
    _stage_bedtime_window(engine, (22.0, 23.0))
    now = engine.state.current_time
    local = dt.fromtimestamp(now, ZoneInfo(engine.scenario.timezone))
    bedtime = int(local.replace(hour=23, minute=0, second=0, microsecond=0).timestamp())
    reaching = PlanDayRequest(segments=tuple(
        PlanSegmentInput(start=now + off, end=now + off + 600,
                         intent_kind="工作", note="")
        for off in (0, 12000, 24000, 36000, 48000))[:4]
        + (PlanSegmentInput(start=now + 48000, end=bedtime,
                            intent_kind="休息", note=""),))
    assert _act(engine, "plan-align", "plan_day", reaching).status.value == "completed"

    staged = engine.state.model_copy(deep=True)
    staged.current_time = bedtime - 3600  # 就寝前：两层都携带当日计划
    staged.version += 1
    engine.state = staged
    before = _observe_current(engine, "observe-before-bedtime")
    assert before["scene"]["day_plan"] is not None
    assert before["facts"]["day_plan"] is not None

    staged = engine.state.model_copy(deep=True)
    staged.current_time = bedtime + 60  # 就寝后、跨日零点前：两层一致隐藏
    staged.version += 1
    engine.state = staged
    after = _observe_current(engine, "observe-after-bedtime")
    assert after["scene"]["day_plan"] is None
    assert after["facts"]["day_plan"] is None, (
        "bedtime→midnight 窗口 facts 必须与 scene 同口径隐藏当日已过期计划")


def test_rejection_message_carries_error_code_and_reason(engine_factory) -> None:
    """缺口 7：拒绝消息携带业务错误码与可读原因，经 recent_action_results 进 facts。"""
    engine = engine_factory()
    now = engine.state.current_time
    overlapping = PlanDayRequest(segments=tuple(
        PlanSegmentInput(start=now + off, end=now + off + 600,
                         intent_kind="工作", note="") for off in (0, 1200, 2400, 2700, 4800)))
    rejected = _act(engine, "plan-overlap-reason", "plan_day", overlapping)
    assert rejected.status.value == "rejected"
    assert "PLAN_SEGMENTS_INVALID" in rejected.message, (
        f"模型必须看到业务错误码，实际 message={rejected.message!r}")
    assert "overlap" in rejected.message
    advance_next(engine)  # 一秒重试边界：观察携带拒绝摘要
    data = _observe_current(engine, "observe-rejection")
    rows = data["facts"]["recent_action_results"]
    assert rows and rows[-1]["status"] == "rejected"
    assert set(rows[-1]) == {"action_id", "occurred_at", "status", "error_code", "message"}
    assert rows[-1]["error_code"] == "PLAN_SEGMENTS_INVALID"
    assert rows[-1]["occurred_at"] == rejected.occurred_at
    assert "PLAN_SEGMENTS_INVALID" in rows[-1]["message"], (
        "错误码经摘要消息进入 facts，模型可据此修正重试")
    # 修正后可重试：骨架合法的计划在同一日成功提交
    valid = _anchored_plan(engine, (0, 1200, 2400, 3600, 4800))
    retried = _act(engine, "plan-corrected", "plan_day", valid)
    assert retried.status.value == "completed", (
        f"看到原因后修正重试必须成功：{retried.error_code}")


def test_plan_segment_pulse_requires_matching_activity(engine_factory) -> None:
    """缺口 8：段结束情绪脉冲只在时间加权主导活动家族与段意图匹配时发放。"""
    from worldmock.life import felt as felt_module
    from worldmock.life.models import ActivityState, ActivityStatus
    from worldmock.life.rhythm import _plan_and_deadline_pulses, engine_rhythm_inputs
    from worldmock.life.schedule import DayPlan, PlanSegment

    def pulse_delta(kind: ActivityKind, target_id, intent: str) -> float:
        engine = engine_factory()
        now = engine.state.current_time
        end = now + 3000
        segments = (
            PlanSegment(start=now, end=now + 600, intent_kind="休息"),
            PlanSegment(start=now + 600, end=now + 1200, intent_kind="休息"),
            PlanSegment(start=now + 1200, end=now + 1800, intent_kind="休息"),
            PlanSegment(start=now + 1800, end=now + 2400, intent_kind="休息"),
            PlanSegment(start=now + 2400, end=end, intent_kind=intent),
        )
        activity = ActivityState(
            activity_id="activity-pulse-probe", agent_id=1, kind=kind,
            status=ActivityStatus.COMPLETED, started_at=now + 2400, ended_at=end,
            planned_end_at=end, last_settled_at=end, elapsed_seconds=600,
            planned_duration_seconds=600, origin_action_id="probe-action",
            origin_decision_id="probe-decision", origin_request_event_id="probe-event",
            target_id=target_id)
        staged = engine.state.model_copy(deep=True)
        staged.current_time = end
        staged.activities[activity.activity_id] = activity
        staged.agents[1] = staged.agents[1].model_copy(
            update={"day_plan": DayPlan(bedtime=end, day_end=end + 3600,
                                        segments=segments)}, deep=True)
        inputs = engine_rhythm_inputs(engine, staged, interval=900)
        settled = _plan_and_deadline_pulses(staged.agents[1], inputs)
        return settled.body.mood_valence - staged.agents[1].body.mood_valence

    body_norm = engine_factory().state.agents[1].body.neuroticism_norm
    expected = felt_module.apply_mood_pulse(0.0, 0.05, body_norm)
    followed = pulse_delta(ActivityKind.WORK, None, "工作")
    assert followed == pytest.approx(expected), (
        f"照做（工作段内主导工作）应发放 +0.05 脉冲，实际 Δ={followed}")
    mismatched = pulse_delta(ActivityKind.WAIT, None, "工作")
    assert mismatched == 0.0, f"未照做（等待主导）不应发放脉冲，实际 Δ={mismatched}"


def test_sleep_debt_hours_has_no_residual_references() -> None:
    """sleep_debt_hours 删除后：四个迁移文件无状态字段残留引用（grep 断言）。

    motivation.py（冻结、断链待其所有者修复）、semantics/scenario 的目录资源
    标识与场景初值字段不在本断言范围（目录数据身份，非状态字段）。
    """
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        ["grep", "-rn", "-e", ".sleep_debt_hours", "-e", "sleep_debt_hours=",
         "-e", "sleep_debt_hours:", "-e", '\"sleep_debt_hours\"',
         str(root / "src/worldmock/life/models.py"),
         str(root / "src/worldmock/life/body.py"),
         str(root / "src/worldmock/life/engine.py")],
        capture_output=True, text=True, check=False)
    code_lines = [line for line in result.stdout.splitlines()
                  if line and not line.split(":", 2)[-1].lstrip().startswith("#")
                  and "CATALOG_SLEEP_RESOURCE" not in line
                  and "initial_sleep_debt_hours" not in line]
    assert code_lines == [], f"residual sleep_debt_hours references: {code_lines}"


def _browsed_state(engine, location_id: str):
    """把人物放到一个非商家地点并在该处完成一次配送浏览（浏览集为整份目录）。"""
    product_ids = {item.product_id for item in engine.scenario.products}
    staged = engine.state.model_copy(deep=True)
    now = staged.current_time
    staged.agents[1] = staged.agents[1].model_copy(update={
        "location_id": location_id, "visible_product_ids": set(product_ids),
        "product_browsed_at": now, "product_browsed_location": location_id,
        "inventory": {}}, deep=True)
    engine.state = staged
    return product_ids


def test_browsed_products_expire_on_leaving_merchant_location(engine_factory) -> None:
    """浏览可见期是两支并列：离开商家地点即清空，与 1800 秒 TTL 无关。"""
    engine = engine_factory()
    browsed_at = "office"  # 办公室不是任何商品商家，只有浏览支能把目录带进可见集
    product_ids = _browsed_state(engine, browsed_at)
    agent = engine.state.agents[1]

    # 在浏览地点且未超 TTL：浏览支可见（店内常驻支为空，可见集完全来自浏览）。
    assert set(projection.visible_products(engine.state, agent, engine.scenario)) == product_ids

    # 未超 TTL 但已离开浏览地点：浏览支清空，可见集回到空。
    moved = engine.state.model_copy(deep=True)
    moved.agents[1] = moved.agents[1].model_copy(update={"location_id": "home"}, deep=True)
    engine.state = moved
    agent = engine.state.agents[1]
    assert agent.product_browsed_at is not None
    assert engine.state.current_time - agent.product_browsed_at <= 1800
    assert projection.visible_products(engine.state, agent, engine.scenario) == [], (
        "leaving the browsed location must clear browsed products before the TTL elapses")

    # 回到浏览地点且未超 TTL：浏览支恢复可见（可见期由地点与时刻两支共同判定）。
    back = engine.state.model_copy(deep=True)
    back.agents[1] = back.agents[1].model_copy(update={"location_id": browsed_at}, deep=True)
    engine.state = back
    assert set(projection.visible_products(
        engine.state, engine.state.agents[1], engine.scenario)) == product_ids
