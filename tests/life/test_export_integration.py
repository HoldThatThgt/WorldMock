"""LifeEngine 到认证导出与训练窗口的真实链路测试。"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from agentsociety2.society import DirectToolCall

from tests.life.conftest import advance_next, context
from worldmock.delivery.windows import prepare_windows
from worldmock.footprints.export import export_run
from worldmock.footprints.models import EventDraft
from worldmock.footprints.validation import validate_export
from worldmock.life.models import SendMessageRequest, StartBasicActivityRequest
from worldmock.runtime import settle_pending_model_replies


class _StubReplyClient:
    """联系人回复模型客户端桩：记录请求并返回脚本化响应。"""

    model_name = "deepseek-v4.1-flash"

    def __init__(self, texts=None, error=None):
        self.calls = []
        self.texts = list(texts or [])
        self.error = error

    async def call(self, request):
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        text = self.texts.pop(0) if self.texts else "好啊，老地方见。"
        return {"model": self.model_name,
                "choices": [{"message": {"content": text}}],
                "usage": {"prompt_tokens": 21, "completion_tokens": 7}}


def _send_message_to_model_contact(engine, round_id: str) -> None:
    """真实决策轮：人物 1 向 model 策略老友联系人发送一条消息。"""
    ctx = context(engine, round_id)
    engine.begin_decision_round([ctx])
    engine.submit_action(DirectToolCall(
        ctx, f"action-{round_id}", "send_message",
        SendMessageRequest(recipient_id="contact-chenxi-oldfriend", text="周五晚上一起吃饭？")))
    engine.commit_decision_round(round_id)


def _stage_final_interval(engine, seconds: int) -> None:
    """把隔离运行置于终点前的明确区间，并移除越过时钟的后台事件。"""
    staged = engine.state.model_copy(deep=True)
    staged.current_time = staged.end_time - seconds
    staged.pending_events = []
    staged.agents[1] = staged.agents[1].model_copy(
        update={"next_decision_at": staged.current_time,
                "body_settled_at": staged.current_time}, deep=True)
    staged.version += 1
    draft = EventDraft(event_type="environment_event", occurred_at=staged.current_time,
                       visibility="system", source_kind="scenario",
                       data={"kind": "test_final_interval", "seconds": seconds})
    engine.store.commit(staged.model_dump(mode="json"), [draft])
    engine.state = staged


def test_completed_activity_exports_terminal_outcome_and_training_windows(engine_factory, tmp_path: Path) -> None:
    """真实活动终态回链原动作，并通过 export、认证和三类训练窗口消费。"""
    engine = engine_factory()
    _stage_final_interval(engine, 120)
    ctx = context(engine, "final-wait")
    engine.begin_decision_round([ctx])
    observation = engine.observe(ctx)
    action_id = "action-final-wait"
    request = StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=120)
    engine.submit_action(DirectToolCall(ctx, action_id, "start_basic_activity", request))
    started = engine.commit_decision_round("final-wait")[0]
    assert started.status == "in_progress"
    activity_id = started.activity_id

    advance_next(engine)
    assert engine.state.current_time == engine.state.end_time
    terminal = [event for event in engine.store.read_agent_events("action_result", 1)
                if event.action_id == action_id][-1]
    requested = [event for event in engine.store.read_agent_events("action_requested", 1)
                 if event.action_id == action_id][0]
    assert terminal.data["status"] == "completed"
    assert terminal.activity_id == activity_id
    assert terminal.decision_id == ctx.decision_id
    assert requested.event_id in terminal.caused_by
    assert terminal.data["activity"]["ended_at"] == engine.state.end_time

    agent_ref = engine.store.assets.put_json({"agent_id": 1}, {"kind": "export_chain_test"})
    checkpoint = engine.checkpoint({"agent:1": agent_ref})
    export_dir = tmp_path / "life-export"
    manifest = export_run(engine.store, checkpoint.last_event_seq, export_dir)
    validated, events, activities = validate_export(export_dir)
    windows = prepare_windows(export_dir)

    assert manifest["experiment_completed"] is True
    assert validated["checkpoint"]["checkpoint_id"] == checkpoint.checkpoint_id
    activity = next(item for item in activities if item["activity_id"] == activity_id)
    assert activity["status"] == "completed"
    assert activity["segments"] == [{"started_at": engine.state.end_time - 120,
                                     "ended_at": engine.state.end_time}]
    action = next(item for item in windows["action"]
                  if item["target"]["request"]["action_id"] == action_id)
    outcomes = action["target"]["outcomes"]
    assert any(item["event_id"] == terminal.event_id and item["data"]["status"] == "completed"
               for item in outcomes)
    assert observation["observation_event_id"] in action["provenance"]["source_event_ids"]
    assert windows["intent"] and windows["dreaming"] and events[-1]["event_type"] == "checkpoint_created"


def test_export_contains_hash_bound_timeline_projections(engine_factory, tmp_path: Path) -> None:
    """导出为每个覆盖日生成 timeline/<agent>-<date>.md 投影并哈希绑定清单。"""
    import importlib.util

    engine = engine_factory()
    _stage_final_interval(engine, 120)
    ctx = context(engine, "timeline-wait")
    engine.begin_decision_round([ctx])
    engine.observe(ctx)
    request = StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=120)
    engine.submit_action(DirectToolCall(ctx, "action-timeline-wait", "start_basic_activity", request))
    engine.commit_decision_round("timeline-wait")
    advance_next(engine)

    agent_ref = engine.store.assets.put_json({"agent_id": 1}, {"kind": "timeline_export_test"})
    checkpoint = engine.checkpoint({"agent:1": agent_ref})
    export_dir = tmp_path / "timeline-export"
    manifest = export_run(engine.store, checkpoint.last_event_seq, export_dir)

    zone = ZoneInfo(engine.scenario.timezone)
    # 覆盖窗口为 [start, end)：终点整点当日不在清单，取最后一个被覆盖时刻。
    day = datetime.fromtimestamp(engine.state.current_time - 1, zone).date()
    relative = f"timeline/1-{day.isoformat()}.md"
    record = next(item for item in manifest["timeline"] if item["path"] == relative)
    payload = (export_dir / relative).read_bytes()
    assert record["size_bytes"] == len(payload)
    assert record["sha256"] == hashlib.sha256(payload).hexdigest()
    # 与 scripts/render_day.py 的投影函数逐字节一致（单一实现）。
    spec = importlib.util.spec_from_file_location(
        "render_day_check", Path(__file__).resolve().parents[2] / "scripts" / "render_day.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert payload.decode("utf-8") == module.render_day_markdown(
        engine.store.connection, 1, day)
    assert "wait" in payload.decode("utf-8") or "空档" in payload.decode("utf-8")


def test_truncated_activity_exports_experiment_end_reason(engine_factory, tmp_path: Path) -> None:
    """实验终点截断原因进入权威状态、终态事件和认证导出质量。"""
    engine = engine_factory()
    _stage_final_interval(engine, 60)
    ctx = context(engine, "truncated-wait")
    engine.begin_decision_round([ctx])
    engine.observe(ctx)
    action_id = "action-truncated-wait"
    request = StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=120)
    engine.submit_action(DirectToolCall(ctx, action_id, "start_basic_activity", request))
    started = engine.commit_decision_round("truncated-wait")[0]
    activity_id = started.activity_id

    advance_next(engine)
    activity = engine.state.activities[activity_id]
    assert activity.status == "truncated"
    assert activity.interruption_reason == "experiment_end"
    terminal = [event for event in engine.store.read_agent_events("action_result", 1)
                if event.action_id == action_id][-1]
    assert terminal.data["activity"]["interruption_reason"] == "experiment_end"

    agent_ref = engine.store.assets.put_json({"agent_id": 1}, {"kind": "truncated_export_test"})
    checkpoint = engine.checkpoint({"agent:1": agent_ref})
    export_dir = tmp_path / "truncated-export"
    export_run(engine.store, checkpoint.last_event_seq, export_dir)
    _, _, activities = validate_export(export_dir)
    quality = json.loads((export_dir / "quality.json").read_text())

    exported = next(item for item in activities if item["activity_id"] == activity_id)
    assert exported["interruption_reason"] == "experiment_end"
    assert quality["activities"]["interruptions_missing_reason"] == 0
    assert quality["activities"]["interruption_reason_counts"] == {"experiment_end": 1}


@pytest.mark.asyncio
async def test_model_npc_reply_loop_delivers_to_inbox_with_evidence(engine_factory) -> None:
    """成功回复：独立调用参数正确、证据事件齐全、只进收件箱不改资源。"""
    engine = engine_factory()
    _send_message_to_model_contact(engine, "npc-reply")

    client = _StubReplyClient(texts=["好啊，老地方见。"])
    settled = await settle_pending_model_replies(engine, client=client)

    assert len(settled) == 1 and settled[0]["source_kind"] == "model_npc"
    assert len(client.calls) == 1
    request_payload = client.calls[0]
    assert request_payload["extra_body"] == {"thinking": {"type": "disabled"},
                                             "reasoning": {"max_tokens": 1500}}
    assert request_payload["max_retries"] == 0
    assert "tools" not in request_payload and "tool_choice" not in request_payload
    assert "周五晚上一起吃饭" in request_payload["messages"][1]["content"]

    reply_id = settled[0]["reply_message_id"]
    inbox = engine.state.agents[1].inbox
    assert reply_id in inbox
    assert inbox[reply_id].text == "好啊，老地方见。"
    assert inbox[reply_id].sender_id == "contact-chenxi-oldfriend"
    assert len(inbox[reply_id].text) <= 200

    events = engine.store.read_events(engine.store.last_event_seq)
    evidence = [item for item in events if item.data.get("kind") == "model_npc_reply"]
    assert len(evidence) == 1
    assert evidence[0].data["source_kind"] == "model_npc"
    assert evidence[0].data["request"] == request_payload
    assert evidence[0].data["usage"]["prompt_tokens"] == 21
    assert evidence[0].data["response"]["model"] == "deepseek-v4.1-flash"
    pending = [item for item in events if item.data.get("kind") == "model_npc_reply_pending"]
    assert len(pending) == 1 and evidence[0].caused_by == [pending[0].event_id]
    arrivals = [item for item in events if item.data.get("kind") == "message_arrival"
                and item.data.get("message_id") == reply_id]
    assert len(arrivals) == 1 and arrivals[0].agent_id == 1
    # 只进收件箱：回复证据与送达事件不携带任何资源分录。
    assert not [item for item in [*evidence, *arrivals] if item.data.get("resource_changes")]


@pytest.mark.asyncio
async def test_model_npc_reply_transport_failure_propagates(engine_factory) -> None:
    """传输异常按运行故障传播：不吞异常、不留半写状态。"""
    engine = engine_factory()
    _send_message_to_model_contact(engine, "npc-reply-fail")

    client = _StubReplyClient(error=OSError("connection reset by peer"))
    with pytest.raises(OSError, match="connection reset"):
        await settle_pending_model_replies(engine, client=client)

    assert engine.state.agents[1].inbox == {}
    events = engine.store.read_events(engine.store.last_event_seq)
    assert not [item for item in events if item.data.get("kind") == "model_npc_reply"]


@pytest.mark.asyncio
async def test_model_npc_reply_at_most_once_per_message(engine_factory) -> None:
    """同一消息最多一次回复：二次结算零调用、零重复收件。"""
    engine = engine_factory()
    _send_message_to_model_contact(engine, "npc-reply-once")

    client = _StubReplyClient()
    first = await settle_pending_model_replies(engine, client=client)
    second = await settle_pending_model_replies(engine, client=client)

    assert len(first) == 1 and second == []
    assert len(client.calls) == 1
    inbox_ids = list(engine.state.agents[1].inbox)
    assert len(inbox_ids) == len(set(inbox_ids)) == 1
