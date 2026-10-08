"""LifeEngine 到完整终态与认证导出的真实链路测试。"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from agentsociety2.society import DirectToolCall
from agentsociety2.agent.base import MemoryUpdateEvidence, ModelCallEvidence

from worldmock.agents.life_prompt import life_user_layers, system_message, user_message
from tests.life.conftest import (advance_next, conclude_request, context, pending_names, stage_day_plan,
                                 start_activity, submit_round, wake_by_mail, start_request)
from worldmock.footprints.export import export_run, write_jsonl
from worldmock.footprints.models import Event, EventDraft
from worldmock.footprints.validation import validate_export
from worldmock.errors import IntegrityError
from worldmock.life.models import (PauseActivityRequest, ReportStatusRequest, SendMessageActionRequest,
                                   StartActivityRequest, active_activities)
from worldmock.life.activity_control import pending_conclusions
from worldmock.life.social import CONTACT_REPLY_DELAY_SECONDS
from worldmock.runtime import settle_pending_model_replies


class _StubReplyClient:
    """联系人回复模型客户端桩：记录请求并返回脚本化响应。"""

    model_name = "deepseek-flash"

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


def _model_contact(document):
    """@brief 本测试明确声明模型回复联系人，人工环境不默认包含任意通讯录。"""
    document["agents"][0]["contacts"] = [{"contact_id":"contact-chenxi-oldfriend", "salutation":"老友",
        "relation":"朋友", "closeness":16, "channels":["phone"], "reply_policy":"model"}]


def _send_message_to_model_contact(engine, round_id: str) -> None:
    """真实决策轮：人物 1 向 model 策略老友联系人发送一条消息。"""
    ctx = context(engine, round_id)
    engine.begin_decision_round([ctx])
    observed = engine.observe(ctx)
    recipient = next(row["object"] for row in observed["interaction_bindings"]
                     if row["object_id"] == "contact-chenxi-oldfriend")
    engine.submit_action(DirectToolCall(
        ctx, f"action-{round_id}", "send_message",
        SendMessageActionRequest(recipient_name=recipient, text="周五晚上一起吃饭？")))
    engine.commit_decision_round(round_id)


def _wait_until_reply_due(engine, due: int) -> None:
    """人物在发消息的同一秒开始等待（瞬时动作后同秒再决策），时钟随后推进到回复送达时刻。"""
    submit_round(engine, {1: ("start_activity", start_activity("等待", "自己"))}, f"reply-wait-{due}")
    while engine.context.state.current_time < due:
        advance_next(engine)


def _stage_final_interval(engine, seconds: int) -> None:
    """把隔离运行置于终点前的明确区间，并移除越过时钟的后台事件。"""
    staged = engine.context.state.model_copy(deep=True)
    staged.current_time = staged.end_time - seconds
    staged.pending_events = []
    staged.agents[1] = staged.agents[1].model_copy(
        update={"next_decision_at": staged.current_time,
                "body_settled_at": staged.current_time}, deep=True)
    staged.version += 1
    draft = EventDraft(event_type="environment_event", occurred_at=staged.current_time,
                       visibility="system", source_kind="scenario",
                       data={"kind": "test_final_interval", "seconds": seconds})
    engine.context.store.commit(staged.model_dump(mode="json"), [draft])
    engine.context.state = staged


def _wait_request(observation: dict) -> StartActivityRequest:
    """@brief 从冻结观察选择人物实际可见的等待动作：「自己」对象的动词 interact 与方式「等待」。"""
    own = next(row for row in observation["interaction_bindings"] if row["kind"] == "self")
    action = next(row for row in own["actions"] if row["canonical_action"] == "wait")
    assert (action["verb"], action["label"]) == ("interact", "等待")
    return start_request(targets=[own["object"]], actions=[action["verb"]], ways=[action["label"]])


_MEMORY = {"type": "routine", "text": "等了一会儿，还是先去忙别的。", "importance": 0.4, "keywords": ["等待"]}
#: 每轮模型响应的回复正文；引擎把它登记为 action_requested.data.self_note。
_SELF_NOTE = "先歇一会儿，再看下一步。"
_OBSERVATION_LAYERS = {"tool_layer", "todo_layer", "environment_layer", "memory_layer"}
_LIFECYCLE_KINDS = ("activity_concluded", "activity_paused", "activity_resumed")


def _recorded_round(engine, round_id: str, tool_name: str, build) -> tuple:
    """@brief 真实决策轮：冻结观察，记录一次实际发给模型的行动请求（窗口的决策提示来源），再登记请求。

    :returns: (上下文, 本轮观察, 记录的模型请求)；由调用方在写入记忆证据后提交本轮。
    """
    ctx = context(engine, round_id)
    engine.begin_decision_round([ctx])
    observation = engine.observe(ctx)
    layers = life_user_layers(observation, {"layer": "人设层", "name": engine.context.state.agents[1].name})
    prompt = {"model": "fixture", "messages": [system_message(False), user_message(layers)],
              "tools": [{"type": "function", "function": {"name": tool_name, "parameters": {}}}]}
    call_id = f"{round_id}-model-call"
    engine.record_model_exchange(ModelCallEvidence(ctx, call_id, "request", 1, prompt))
    engine.record_model_exchange(ModelCallEvidence(ctx, call_id, "response", 1, prompt, response={
        "choices": [{"message": {"content": f"  {_SELF_NOTE}\n",
                                 "tool_calls": [{"function": {"name": tool_name, "arguments": "{}"}}]}}]}))
    engine.submit_action(DirectToolCall(ctx, f"action-{round_id}", tool_name, build(observation)))
    return ctx, observation, prompt


def _conclusion_memory_evidence(ctx, observation: dict) -> MemoryUpdateEvidence:
    """@brief 宿主写入收尾记忆后的落盘证据：一条主观条目，来源窗口取本轮观察的全部已核验经历。"""
    episode = {"id": "episode-conclusion", "type": _MEMORY["type"], "text": _MEMORY["text"],
               "epistemic_status": "subjective"}
    return MemoryUpdateEvidence(
        context=ctx, operation="append_episodes", created_episode_ids=(episode["id"],), merged_episode_ids=(),
        episodes=(episode,), memory_before_sha256="0" * 64, memory_sha256="a" * 64, episodes_sha256="b" * 64,
        state_sha256="c" * 64, consolidated=False, observation_event_id=observation["observation_event_id"],
        verified_event_ids=tuple(dict.fromkeys(row["source_event_id"] for row in observation["verified_experiences"])))


def test_completed_activity_exports_terminal_outcome_and_recorded_prompt(engine_factory, tmp_path: Path) -> None:
    """真实活动终态回链原动作，并通过 export、认证和三类训练窗口消费；链上有一次带记忆的收尾：动作窗口目标保留
    完整请求，意图与 dreaming 窗口中的动作请求投影不含记忆正文（规格第七章「finish 并入 conclude_activity」、第九章交付链）；
    行动请求按真实七层记录，dreaming 读其中的层 JSON。"""
    engine = engine_factory()
    duration = engine.context.scenario.activity_feedback_seconds
    _stage_final_interval(engine, 2 * duration)
    _recorded_round(engine, "first-wait", "start_activity", _wait_request)
    assert engine.commit_decision_round("first-wait")[0].status == "in_progress"
    advance_next(engine)
    conclusion, seen, _ = _recorded_round(engine, "wait-end", "conclude_activity", lambda observed: conclude_request(
        *pending_names(observed), memories=[[_MEMORY]]))
    engine.record_memory_update(_conclusion_memory_evidence(conclusion, seen))
    assert engine.commit_decision_round("wait-end")[0].status == "completed"
    ctx, observation, recorded_prompt = _recorded_round(engine, "final-wait", "start_activity", _wait_request)
    action_id = "action-final-wait"
    started = engine.commit_decision_round("final-wait")[0]
    assert started.status == "in_progress"
    activity_id = started.activity_id

    advance_next(engine)
    assert engine.context.state.current_time == engine.context.state.end_time
    terminal = [event for event in engine.context.store.read_agent_events("action_result", 1)
                if event.action_id == action_id][-1]
    requested = [event for event in engine.context.store.read_agent_events("action_requested", 1)
                 if event.action_id == action_id][0]
    assert terminal.data["status"] == "completed"
    assert terminal.activity_id == activity_id
    assert terminal.decision_id == ctx.decision_id
    assert requested.event_id in terminal.caused_by
    assert terminal.data["activity"]["ended_at"] == engine.context.state.end_time

    agent_ref = engine.context.store.assets.put_json({"agent_id": 1}, {"kind": "export_chain_test"})
    checkpoint = engine.checkpoint({"agent:1": agent_ref})
    export_dir = tmp_path / "life-export"
    manifest = export_run(engine.context.store, checkpoint.last_event_seq, export_dir)
    validated, events, activities = validate_export(export_dir)

    assert manifest["experiment_completed"] is True
    assert validated["checkpoint"]["checkpoint_id"] == checkpoint.checkpoint_id
    activity = next(item for item in activities if item["activity_id"] == activity_id)
    assert activity["status"] == "completed"
    assert activity["location_id"] == observation["location_id"]
    assert activity["segments"] == [{"started_at": engine.context.state.end_time - duration,
                                     "ended_at": engine.context.state.end_time}]
    [prompt] = [event for event in events if event["event_type"] == "model_request"
                and event["decision_id"] == ctx.decision_id and event["data"]["phase"] == "request"]
    assert prompt["data"]["request"] == recorded_prompt
    [exported_request] = [event for event in events if event["event_type"] == "action_requested"
                         and event["action_id"] == action_id]
    assert exported_request["data"]["self_note"] == _SELF_NOTE
    [concluded_request] = [event for event in events if event["event_type"] == "action_requested"
                           and event["action_id"] == "action-wait-end"]
    assert concluded_request["data"]["request"]["items"][0]["memories"] == [_MEMORY]
    updated = [event for event in events if event["event_type"] == "memory_updated"]
    assert [(event["decision_id"], event["data"]["trigger"]) for event in updated] == [
        (conclusion.decision_id, ["activity_conclusion"])]
    assert _MEMORY["text"] in recorded_prompt["messages"][1]["content"]
    assert events[-1]["event_type"] == "checkpoint_created"



def test_export_contains_hash_bound_timeline_projections(engine_factory, tmp_path: Path) -> None:
    """导出为每个覆盖日生成 timeline/<agent>-<date>.md 投影并哈希绑定清单。"""
    import importlib.util

    engine = engine_factory()
    _stage_final_interval(engine, engine.context.scenario.activity_feedback_seconds)
    ctx = context(engine, "timeline-wait")
    engine.begin_decision_round([ctx])
    observation = engine.observe(ctx)
    request = _wait_request(observation)
    engine.submit_action(DirectToolCall(ctx, "action-timeline-wait", "start_activity", request))
    engine.commit_decision_round("timeline-wait")
    advance_next(engine)

    agent_ref = engine.context.store.assets.put_json({"agent_id": 1}, {"kind": "timeline_export_test"})
    checkpoint = engine.checkpoint({"agent:1": agent_ref})
    export_dir = tmp_path / "timeline-export"
    manifest = export_run(engine.context.store, checkpoint.last_event_seq, export_dir)

    zone = ZoneInfo(engine.context.scenario.timezone)
    # 覆盖窗口为 [start, end)：终点整点当日不在清单，取最后一个被覆盖时刻。
    day = datetime.fromtimestamp(engine.context.state.current_time - 1, zone).date()
    relative = f"timeline/1-{day.isoformat()}.md"
    record = next(item for item in manifest["timeline"] if item["path"] == relative)
    payload = (export_dir / relative).read_bytes()
    assert record["size_bytes"] == len(payload)
    assert record["sha256"] == hashlib.sha256(payload).hexdigest()
    # 与 scripts/render_day.py 的投影函数逐字节一致（单一实现）。
    spec = importlib.util.spec_from_file_location(
        "render_day_check", Path(__file__).resolve().parents[3] / "scripts" / "render_day.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert payload.decode("utf-8") == module.render_day_markdown(
        engine.context.store.connection, 1, day, engine.context.scenario.timezone)
    # 时间线只写中文种类名（docs/design/10-runtime.md ）：等待是「空档」，不写英文的 wait。
    assert "空档" in payload.decode("utf-8") and "wait" not in payload.decode("utf-8")


def test_truncated_activity_exports_experiment_end_reason(engine_factory, tmp_path: Path) -> None:
    """实验终点截断原因进入权威状态、终态事件和认证导出质量。"""
    engine = engine_factory()
    _stage_final_interval(engine, 60)
    ctx = context(engine, "truncated-wait")
    engine.begin_decision_round([ctx])
    observation = engine.observe(ctx)
    action_id = "action-truncated-wait"
    request = _wait_request(observation)
    engine.submit_action(DirectToolCall(ctx, action_id, "start_activity", request))
    started = engine.commit_decision_round("truncated-wait")[0]
    activity_id = started.activity_id

    advance_next(engine)
    activity = engine.context.state.activities[activity_id]
    assert activity.status == "truncated"
    assert activity.interruption_reason == "experiment_end"
    terminal = [event for event in engine.context.store.read_agent_events("action_result", 1)
                if event.action_id == action_id][-1]
    assert terminal.data["activity"]["interruption_reason"] == "experiment_end"

    agent_ref = engine.context.store.assets.put_json({"agent_id": 1}, {"kind": "truncated_export_test"})
    checkpoint = engine.checkpoint({"agent:1": agent_ref})
    export_dir = tmp_path / "truncated-export"
    export_run(engine.context.store, checkpoint.last_event_seq, export_dir)
    _, _, activities = validate_export(export_dir)
    quality = json.loads((export_dir / "quality.json").read_text())

    exported = next(item for item in activities if item["activity_id"] == activity_id)
    assert exported["interruption_reason"] == "experiment_end"
    assert quality["activities"]["interruptions_missing_reason"] == 0
    assert quality["activities"]["interruption_reason_counts"] == {"experiment_end": 1}


def _round(engine, name: str, tool_name: str, build) -> tuple[dict, object]:
    """@brief 单人真实决策轮：冻结观察后按观察构造请求（活动名取自本轮私有绑定），登记并结算。"""
    decision = context(engine, name)
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    request = build(observed) if callable(build) else build
    engine.submit_action(DirectToolCall(decision, f"action-{name}", tool_name, request))
    result = engine.commit_decision_round(name)[0]
    assert result.status in {"completed", "in_progress"}, result.error_code
    return observed, result


def _named(observed: dict, activity_id: str) -> str:
    """@brief 本轮观察私有绑定里该活动的可读名称（做什么＋对象＋开始时刻）。"""
    return next(row["name"] for row in observed["activity_bindings"] if row["activity_id"] == activity_id)


def _offered(observed: dict, action: str, mode: str | None = None) -> StartActivityRequest:
    """@brief 从冻结观察选一个提供该动作的对象，照填其动词与方式（出行按方式筛选）。"""
    return next(start_request(targets=[row["object"]], actions=[offered["verb"]], ways=[offered["label"]])
                for row in observed["interaction_bindings"] for offered in row["actions"]
                if offered["canonical_action"] == action and (mode is None or offered.get("travel_mode") == mode))


def _next_opportunity(engine, offset: int = 60) -> None:
    """@brief 以受控邮件到达把下一次决策机会钉在 offset 秒后并推进到那里。"""
    due = wake_by_mail(engine, offset=offset)
    while engine.context.state.current_time < due:
        advance_next(engine)
    assert engine.scheduling_state().ready_agent_ids == (1,)


def _control(activity_id: str, model, summary: str):
    """@brief 按本轮可见活动名点名一项活动的播报或暂停请求。"""
    return lambda observed: model(items=[{"activity_name": _named(observed, activity_id), "summary": summary}],
                                  errands=[])


def _lifecycle_run(engine) -> tuple[dict[str, str], dict[str, dict]]:
    """@brief 真实控制链：刷推荐→暂停→等待→恢复→带记忆结束等待→播报→再暂停→步行出发→到达当秒收尾。

    :returns: (活动身份, 各轮冻结观察)；动作在导出时仍暂停在家，等待由人物结束，出行到达后收尾。
    """
    stage_day_plan(engine)
    observed = {}
    _, watch = _round(engine, "watch", "start_activity", start_activity("进行", "刷推荐", app=["抖音"]))
    ids = {"watch": watch.activity_id}
    _next_opportunity(engine)
    _round(engine, "pause", "pause_activity", _control(ids["watch"], PauseActivityRequest, "先停一下。"))
    _, waited = _round(engine, "wait", "start_activity", _wait_request)
    ids["wait"] = waited.activity_id
    _next_opportunity(engine)
    _round(engine, "resume", "report_status", _control(ids["watch"], ReportStatusRequest, "接着看。"))
    _next_opportunity(engine)
    observed["conclude"], _ = _round(engine, "conclude", "conclude_activity", lambda seen: conclude_request(
        _named(seen, ids["wait"]), summary="不等了。", memories=[[_MEMORY]]))
    _next_opportunity(engine)
    _round(engine, "report", "report_status", _control(ids["watch"], ReportStatusRequest, "还在看。"))
    _next_opportunity(engine)
    _round(engine, "pause-again", "pause_activity", _control(ids["watch"], PauseActivityRequest, "出门前停下。"))
    _, travel = _round(engine, "depart", "start_activity", lambda seen: _offered(seen, "travel_to", "walking"))
    ids["travel"] = travel.activity_id
    for step in range(64):
        if engine.context.state.activities[ids["travel"]].status != "active":
            break
        if engine.scheduling_state().ready_agent_ids:
            _round(engine, f"road-{step}", "report_status", _control(ids["travel"], ReportStatusRequest, "在路上。"))
        else:
            advance_next(engine)
    observed["arrive"], _ = _round(engine, "arrive", "conclude_activity",
                                   lambda seen: conclude_request(*pending_names(seen), summary="到了。"))
    return ids, observed


def _events(engine, event_type: str, action_id: str) -> list:
    """@brief 本人某动作的同类已提交事件。"""
    return [event for event in engine.context.store.read_agent_events(event_type, 1) if event.action_id == action_id]


def test_person_conclusion_keeps_memories_in_request_and_registers_private_sources(engine_factory) -> None:
    """人物带记忆结束进行中的等待：请求事件保留完整 memories，结果登记私有 memory_sources，活动记为 cancelled
    并写一次原始动作终态（规格第七章「finish 并入 conclude_activity」、第八章「活动」）。"""
    engine = engine_factory()
    ids, observed = _lifecycle_run(engine)
    waited = engine.context.state.activities[ids["wait"]]
    name = _named(observed["conclude"], ids["wait"])
    requested = _events(engine, "action_requested", "action-conclude")
    assert len(requested) == 1
    assert requested[0].data["request"] == conclude_request(
        name, summary="不等了。", memories=[[_MEMORY]]).model_dump(mode="json")
    assert requested[0].data["request"]["items"][0]["memories"] == [_MEMORY]
    result = next(event for event in _events(engine, "action_result", "action-conclude")
                  if event.data["status"] == "completed")
    verified = list(dict.fromkeys(row["source_event_id"] for row in observed["conclude"]["verified_experiences"]
                                  if row["activity_id"] == ids["wait"]))
    assert verified
    assert result.data["data"]["memory_sources"] == [{
        "task": name, "activity_id": ids["wait"], "session_id": waited.session_ids[-1],
        "verified_event_ids": verified}]
    assert result.data["data"]["activity_ids"] == [ids["wait"]] and "task" not in result.data["data"]
    assert (waited.status, waited.interruption_reason) == ("cancelled", "agent_cancelled")
    assert waited.concluded_at == result.occurred_at
    terminal = [event for event in _events(engine, "action_result", "action-wait")
                if event.data["status"] != "in_progress"]
    assert [event.data["status"] for event in terminal] == ["cancelled"]
    assert requested[0].event_id in terminal[0].caused_by
    assert engine.context.state.processed_actions["action-wait"].result["status"] == "cancelled"
    layers = json.dumps([observed["arrive"][key] for key in (
        "tool_layer", "todo_layer", "environment_layer", "memory_layer", "progress_layer")], ensure_ascii=False)
    assert "memory_sources" not in layers and ids["wait"] not in layers


def test_lifecycle_records_and_export_match_the_final_checkpoint(engine_factory, tmp_path: Path) -> None:
    """结束、暂停与恢复逐活动各写一条带快照的 environment_event；会话切换不写事件；在最后一次检查点导出的
    activities.jsonl 逐项等于该最终检查点：人物结束的为 cancelled，暂停的为 paused（规格第七章、第八章「活动」）。"""
    engine = engine_factory()
    ids, _ = _lifecycle_run(engine)
    events = engine.context.store.read_events(engine.context.store.last_event_seq)
    lifecycle = [event for event in events if event.event_type == "environment_event"
                 and event.data.get("kind") in _LIFECYCLE_KINDS]
    expected = [("activity_paused", "watch", "action-pause", "paused"),
                ("activity_resumed", "watch", "action-resume", "active"),
                ("activity_concluded", "wait", "action-conclude", "cancelled"),
                ("activity_paused", "watch", "action-pause-again", "paused"),
                ("activity_concluded", "travel", "action-arrive", "completed")]
    assert [(event.data["kind"], event.activity_id, event.action_id, event.data["activity"]["status"])
            for event in lifecycle] == [(kind, ids[key], action, status) for kind, key, action, status in expected]
    for event in lifecycle:
        request = _events(engine, "action_requested", event.action_id)[0]
        assert request.event_id in event.caused_by
        assert event.data["activity_id"] == event.data["activity"]["activity_id"] == event.activity_id
        concluded = event.data["activity"]["concluded_at"]
        assert concluded == (event.occurred_at if event.data["kind"] == "activity_concluded" else None)
    assert not [event for event in events if "session" in str(event.data.get("kind", ""))]
    sessions = sorted(engine.context.state.sessions.values(), key=lambda item: item.started_at)
    travel = engine.context.state.activities[ids["travel"]]
    assert [(item.kind, item.status) for item in sessions] == [("stay", "ended"), ("transit", "ended"),
                                                              ("stay", "active")]
    assert sessions[0].ended_at == sessions[1].started_at == travel.started_at
    assert sessions[1].ended_at == sessions[2].started_at == travel.ended_at
    assert sessions[2].location_id == travel.destination_id and travel.session_ids == [sessions[1].session_id]
    reference = engine.context.store.assets.put_json({"agent_id": 1}, {"kind": "lifecycle_export_test"})
    checkpoint = engine.checkpoint({"agent:1": reference})
    export_dir = tmp_path / "lifecycle-export"
    manifest = export_run(engine.context.store, checkpoint.last_event_seq, export_dir)
    assert manifest["experiment_completed"] is False
    exported = {row["activity_id"]: row for row in (
        json.loads(line) for line in (export_dir / "activities.jsonl").read_text(encoding="utf-8").splitlines())}
    final = checkpoint.state["activities"]
    assert set(exported) == set(final) == set(ids.values())
    for activity_id, snapshot in final.items():
        row = exported[activity_id]
        assert row["activity_type"] == snapshot["kind"]
        assert {key: row[key] for key in snapshot if key != "kind"} == {
            key: value for key, value in snapshot.items() if key != "kind"}
    assert {key: exported[ids[key]]["status"] for key in ids} == {
        "watch": "paused", "wait": "cancelled", "travel": "completed"}
    assert exported[ids["watch"]]["activity_type"] == "action"
    assert {key: exported[ids["watch"]]["data"][key] for key in ("action_id", "category", "app_id", "manner")} == {
        "action_id": "browse-feed", "category": "entertainment", "app_id": "douyin",
        "manner": "phone_continuous"}


@pytest.mark.asyncio
async def test_model_npc_reply_loop_delivers_to_inbox_with_evidence(engine_factory) -> None:
    """成功回复：独立调用参数正确、证据在生成时写入；回复预排在 CONTACT_REPLY_DELAY_SECONDS 后送达，到期才进收件箱，
    留 message_arrival 送达记录并以 message_arrival 唤醒清醒的收件人；不改资源（裁决 #21）。"""
    engine = engine_factory(mutate=_model_contact)
    _send_message_to_model_contact(engine, "npc-reply")
    sent_at = engine.context.state.current_time
    due = sent_at + CONTACT_REPLY_DELAY_SECONDS

    client = _StubReplyClient(texts=["好啊，老地方见。"])
    settled = await settle_pending_model_replies(engine, client=client)

    assert len(settled) == 1 and settled[0]["source_kind"] == "model_npc"
    assert len(client.calls) == 1
    request_payload = client.calls[0]
    assert request_payload["extra_body"] == {"thinking": {"type": "disabled"}}
    assert request_payload["max_retries"] == 0
    assert "tools" not in request_payload and "tool_choice" not in request_payload
    assert "周五晚上一起吃饭" in request_payload["messages"][1]["content"]

    reply_id = settled[0]["reply_message_id"]
    assert reply_id not in engine.context.state.agents[1].inbox
    scheduled = [item for item in engine.context.state.pending_events if item.kind == "message_arrival"]
    assert [(item.agent_id, item.due_at) for item in scheduled] == [(1, due)]

    events = engine.context.store.read_events(engine.context.store.last_event_seq)
    evidence = [item for item in events if item.data.get("kind") == "model_npc_reply"]
    assert len(evidence) == 1 and evidence[0].occurred_at == sent_at
    assert evidence[0].data["source_kind"] == "model_npc"
    assert evidence[0].data["request"] == request_payload
    assert evidence[0].data["usage"]["prompt_tokens"] == 21
    assert evidence[0].data["response"]["model"] == "deepseek-flash"
    # 后验守卫没有命中：只有一次尝试，不用兜底句（docs/design/10-runtime.md ）。
    assert [(item["request"], item["text"], item["matched"]) for item in evidence[0].data["attempts"]] == [
        (request_payload, "好啊，老地方见。", [])]
    assert evidence[0].data["guard_applied"] is False
    pending = [item for item in events if item.data.get("kind") == "model_npc_reply_pending"]
    assert len(pending) == 1 and evidence[0].caused_by == [pending[0].event_id]
    assert not [item for item in events if item.data.get("kind") == "message_arrival"
                and item.data.get("message_id") == reply_id]

    _wait_until_reply_due(engine, due)
    assert engine.context.state.current_time == due
    inbox = engine.context.state.agents[1].inbox
    assert inbox[reply_id].text == "好啊，老地方见。"
    assert inbox[reply_id].sender_id == "contact-chenxi-oldfriend"
    assert len(inbox[reply_id].text) <= 200
    events = engine.context.store.read_events(engine.context.store.last_event_seq)
    arrivals = [item for item in events if item.data.get("kind") == "message_arrival"
                and item.data.get("message_id") == reply_id]
    assert len(arrivals) == 1 and arrivals[0].agent_id == 1 and arrivals[0].occurred_at == due
    assert engine.scheduling_state().ready_agent_ids == (1,)
    assert "message_arrival" in engine.context.state.agents[1].pending_wake_reasons
    # 只进收件箱：回复证据与送达事件不携带任何资源分录。
    assert not [item for item in [*evidence, *arrivals] if item.data.get("resource_changes")]


@pytest.mark.asyncio
async def test_model_npc_reply_transport_failure_propagates(engine_factory) -> None:
    """传输异常按运行故障传播：不吞异常、不留半写状态。"""
    engine = engine_factory(mutate=_model_contact)
    _send_message_to_model_contact(engine, "npc-reply-fail")

    client = _StubReplyClient(error=OSError("connection reset by peer"))
    with pytest.raises(OSError, match="connection reset"):
        await settle_pending_model_replies(engine, client=client)

    assert engine.context.state.agents[1].inbox == {}
    events = engine.context.store.read_events(engine.context.store.last_event_seq)
    assert not [item for item in events if item.data.get("kind") == "model_npc_reply"]


@pytest.mark.asyncio
async def test_model_npc_reply_at_most_once_per_message(engine_factory) -> None:
    """同一消息最多一次回复：二次结算零调用、零重复收件。"""
    engine = engine_factory(mutate=_model_contact)
    _send_message_to_model_contact(engine, "npc-reply-once")

    client = _StubReplyClient()
    first = await settle_pending_model_replies(engine, client=client)
    second = await settle_pending_model_replies(engine, client=client)

    assert len(first) == 1 and second == []
    assert len(client.calls) == 1
    assert len([item for item in engine.context.state.pending_events if item.kind == "message_arrival"]) == 1
    _wait_until_reply_due(engine, engine.context.state.current_time + CONTACT_REPLY_DELAY_SECONDS)
    inbox_ids = list(engine.context.state.agents[1].inbox)
    assert len(inbox_ids) == len(set(inbox_ids)) == 1


def _app_export_window(document):
    """@brief 显式声明短运行窗口，经真实时钟覆盖整点推送及最长内容的实际完成。"""
    document["start_at"] = "2026-01-05T07:59:00+08:00"
    document["end_at"] = "2026-01-05T08:55:00+08:00"
    document["messages"][0]["sent_at"] = "2026-01-05T08:54:00+08:00"
    document["payrolls"][0]["due_at"] = "2026-01-05T08:45:00+08:00"


def _pushed_content_completed(engine, push: dict) -> bool:
    """@brief 只从真实人物内容进度判断本次推送内容是否已经完成。"""
    progress = engine.context.state.agents[1].content_progress.get(push["content_id"])
    return progress is not None and progress.completed_rounds >= 1


def _matching_app_activity(engine, push: dict):
    """@brief 返回本次推送所属动作与应用的进行中活动。"""
    return next((item for item in active_activities(engine.context.state, 1)
                 if item.kind == "action" and item.data.get("action_id") == push["action_id"]
                 and item.data.get("app_id") == push["app_id"]), None)


def _app_wake_request(engine, push: dict) -> tuple[str, object]:
    """@brief 在真实唤醒点开始推送动作，内容完成后结束一次，随后只等待。"""
    pending = pending_conclusions(engine.context.state, 1)
    active = active_activities(engine.context.state, 1)
    if pending:
        return "conclude_activity", conclude_request(*(item.name for item in pending))
    running = _matching_app_activity(engine, push)
    if running is not None:
        if _pushed_content_completed(engine, push):
            return "conclude_activity", conclude_request(running.name)
        return "report_status", ReportStatusRequest(
            items=[{"activity_name": running.name, "summary": "继续看当前内容。"}], errands=[])
    seen = any(item.kind == "action" and item.data.get("action_id") == push["action_id"]
               and item.data.get("app_id") == push["app_id"] for item in engine.context.state.activities.values())
    if active:
        if not seen:
            return "conclude_activity", conclude_request(*(item.name for item in active))
        return "report_status", ReportStatusRequest(
            items=[{"activity_name": item.name, "summary": "继续等待。"} for item in active], errands=[])
    if seen:
        return "start_activity", start_activity("等待", "自己")
    return "start_activity", start_activity("进行", push["action"], app=[push["app"]])


def _idle_wake_request(engine) -> tuple[str, object]:
    """@brief 在真实唤醒点收尾已结束活动、播报进行中活动，否则开始等待。"""
    pending = pending_conclusions(engine.context.state, 1)
    if pending:
        return "conclude_activity", conclude_request(*(item.name for item in pending))
    active = active_activities(engine.context.state, 1)
    if active:
        return "report_status", ReportStatusRequest(
            items=[{"activity_name": item.name, "summary": "继续等待。"} for item in active], errands=[])
    return "start_activity", start_activity("等待", "自己")


def _drive_app_export(engine, target: int, push: dict | None = None) -> None:
    """@brief 只在真实决策机会提交当前请求并推进排程，不改写时钟、内容或推送状态。"""
    for step in range(300):
        if engine.context.state.current_time >= target:
            return
        if not engine.scheduling_state().ready_agent_ids:
            advance_next(engine)
            continue
        request = _app_wake_request(engine, push) if push is not None else _idle_wake_request(engine)
        _, results = submit_round(engine, {1:request}, f"app-export-{target}-{step}")
        assert results[0].status in {"completed", "in_progress"}
    raise AssertionError("Action export did not reach its actual boundary")


@pytest.fixture
def completed_app_export(engine_factory, tmp_path):
    """@brief 真实引擎发出推送、人物打开并消费后，以完整共同检查点认证导出。"""
    engine = engine_factory(mutate=_app_export_window)
    stage_day_plan(engine)
    target = min(event.due_at for event in engine.context.state.pending_events if event.kind == "app_push")
    _drive_app_export(engine, target)
    push = next(event for event in engine.context.store.read_events(engine.context.store.last_event_seq)
                if event.event_type == "environment_event" and event.data.get("kind") == "app_push"
                and "push_id" in event.data)
    _drive_app_export(engine, engine.context.state.end_time, push.data)
    reference = engine.context.store.assets.put_json({"agent_id":1}, {"kind":"app_export_test"})
    checkpoint = engine.checkpoint({"agent:1":reference})
    directory = tmp_path / "app-export"
    manifest = export_run(engine.context.store, checkpoint.last_event_seq, directory)
    return directory, engine, manifest, push


def test_real_app_export_has_five_source_bound_fact_types_and_exact_quality_sections(completed_app_export):
    """@brief 五类 app 事实与实际活动、内容、推送逐项相符；同一前缀能认证重算。"""
    directory, engine, manifest, push = completed_app_export
    validated, events, activities = validate_export(directory)
    assert validated == manifest and manifest["schema_version"] == 4
    assert set(manifest["artifacts"]) == {"events", "activities", "verified_experiences", "purchases", "app_usage"}
    facts = [json.loads(line) for line in (directory / "app-usage.jsonl").read_text().splitlines()]
    assert {row["fact_kind"] for row in facts} == {
        "app_use", "content_consumed", "push_delivered", "push_opened", "push_released"}
    sources = {event["event_id"]:event for event in events}
    assert all(row["source_event_id"] in sources for row in facts)
    for row in (row for row in facts if row["fact_kind"] == "content_consumed"):
        source = sources[row["source_event_id"]]
        evidence = source["data"]["content"]
        action = source["data"]["activity"]["data"]
        assert (row["content_id"], row["app_id"], row["action_id"], row["manner"], row["completed"]) == (
            evidence["content_id"], evidence["app_id"], action["action_id"], action["manner"],
            evidence["completed"])
        assert (row["position_before"], row["position_after"]) == (
            evidence["position_before_seconds"], evidence["position_after_seconds"])
        assert "kind" not in row and "kind" not in evidence
    consumed_pin = [row for row in facts if row["fact_kind"] == "content_consumed" and row["push_id"]]
    assert consumed_pin and {row["push_id"] for row in consumed_pin} == {push.data["push_id"]}
    assert any(row["completed"] and row["content_id"] == push.data["content_id"] for row in consumed_pin)
    app_uses = [row for row in facts if row["fact_kind"] == "app_use"]
    assert len(app_uses) == len({row["activity_id"] for row in app_uses})
    assert {row["activity_id"] for row in app_uses} == {
        activity["activity_id"] for activity in activities if activity["activity_type"] == "action"
        and activity["data"].get("app_id") is not None}
    assert {(row["action_id"], row["manner"], row["device"]) for row in app_uses} == {
        ("browse-feed", "phone_continuous", "phone")}
    assert all(row["status"] == "cancelled" and row["segments"][-1]["ended_at"] < engine.context.state.end_time
               for row in app_uses)
    quality = json.loads((directory / "quality.json").read_text())
    assert quality["schema_version"] == 6
    assert set(quality["content_contact"]) == {"by_app", "by_action", "repeat_watch", "repeat_seconds_share"}
    assert quality["content_contact"]["by_app"] == {push.data["app_id"]:sum(
        row["fact_kind"] == "content_consumed" for row in facts)}
    progress_events = [event for event in events if event["event_type"] == "activity_progress"
                       and "content" in event["data"]]
    repeated = [event for event in progress_events if event["data"]["content"]["completed_rounds_after"]
                - int(event["data"]["content"]["completed"]) >= 1]
    total_seconds = sum(event["data"]["interval_end"] - event["data"]["interval_start"]
                        for event in progress_events)
    repeated_seconds = sum(event["data"]["interval_end"] - event["data"]["interval_start"]
                           for event in repeated)
    assert quality["content_contact"]["by_action"] == {push.data["action_id"]: len(progress_events)}
    assert quality["content_contact"]["repeat_watch"] == len(repeated)
    assert quality["content_contact"]["repeat_seconds_share"] == pytest.approx(repeated_seconds / total_seconds)
    assert quality["app_pushes"] == {"pushed":1, "opened_in_session":1, "consumed_pinned":1,
        "released_by_completion":1, "expired_with_session":0, "outside_window_violations":0}
    assert set(quality["work_topics"]) == {"topics", "completed", "worked_seconds_by_topic", "reports"}
    assert "work" not in quality["conversion_efficiency"]
    assert "work_review" not in {row["purpose"] for row in quality["model_usage"]["by_purpose"]}
    assert manifest["frozen_run_manifest"]["environment_identity"] == engine.context.store.manifest[
        "environment_identity"]


@pytest.mark.parametrize("rehash", [False, True])
def test_real_app_export_rejects_tampered_or_rehashed_content_facts(completed_app_export, rehash):
    """@brief 修改内容事实即使重写文件摘要和行数，仍因真实事件重算不一致而拒绝。"""
    directory, _, manifest, _ = completed_app_export
    path = directory / "app-usage.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    next(row for row in rows if row["fact_kind"] == "content_consumed")["content_id"] = "invented-content"
    raw = b"".join(json.dumps(row, ensure_ascii=False, sort_keys=True).encode() + b"\n" for row in rows)
    path.write_bytes(raw)
    if rehash:
        manifest["artifacts"]["app_usage"].update(rows=len(rows), sha256=hashlib.sha256(raw).hexdigest())
        (directory / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(IntegrityError, match="artifact hash|app usage facts disagree"):
        validate_export(directory)


@pytest.mark.parametrize("kind", ["push", "progress"])
@pytest.mark.parametrize("field,value", [("source_kind", "model"), ("visibility", "system"),
                                        ("visibility", "evaluation")])
def test_real_app_export_rejects_rehashed_events_with_forged_fact_provenance(completed_app_export, kind, field, value):
    """@brief 真正运行事件改成模型或私有来源，即使事件与经历文件都重算摘要，也不能再认证。"""
    directory, _, manifest, _ = completed_app_export
    rows = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
    if kind == "push":
        forged = next(row for row in rows if row["event_type"] == "environment_event"
                      and row["data"].get("kind") == "app_push" and "push_id" in row["data"])
    else:
        forged = next(row for row in rows if row["event_type"] == "activity_progress" and "content" in row["data"])
    forged[field] = value
    for row in rows:
        Event.model_validate(row)
    manifest["artifacts"]["events"] = write_jsonl(directory / "events.jsonl", rows)
    # 模拟连同经历派生文件一起重签的篡改，拒绝必须落在真实来源合同而非遗漏的派生摘要。
    experiences = [json.loads(line) for line in (directory / "verified-experiences.jsonl").read_text().splitlines()]
    experiences = [row for row in experiences if row["source_event_id"] != forged["event_id"]]
    manifest["artifacts"]["verified_experiences"] = write_jsonl(
        directory / "verified-experiences.jsonl", experiences)
    (directory / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(IntegrityError, match="agent-visible environment event|invalid provenance or visibility"):
        validate_export(directory)


@pytest.fixture
def completed_travel_export(engine_factory, tmp_path):
    """@brief 真正步行到公司并推进完整窗口，认证实际出行、推送和会话到达。"""
    engine = engine_factory(mutate=_app_export_window)
    stage_day_plan(engine)
    _, results = submit_round(engine, {1:("start_activity", start_activity("步行", "地点：公司"))},
                              "travel-export-start")
    assert results[0].status == "in_progress"
    _drive_app_export(engine, engine.context.state.end_time)
    reference = engine.context.store.assets.put_json({"agent_id":1}, {"kind":"travel_export_test"})
    checkpoint = engine.checkpoint({"agent:1":reference})
    directory = tmp_path / "travel-export"
    manifest = export_run(engine.context.store, checkpoint.last_event_seq, directory)
    return directory, engine, manifest


@pytest.mark.parametrize("fixture", ["completed_app_export", "completed_travel_export"])
@pytest.mark.parametrize("field,value", [("source_kind", "model"), ("visibility", "system"),
                                        ("event_type", "model_response")])
def test_real_export_rejects_rehashed_app_or_travel_snapshot_provenance(request, fixture, field, value):
    """@brief 真实活动受理快照改成私有或模型事件，即使重签事件文件，也不能投影 app 或会话事实。"""
    directory, _, manifest, *_ = request.getfixturevalue(fixture)
    rows = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
    forged = next(row for row in rows if row["event_type"] == "action_accepted"
                  and row["data"].get("activity", {}).get("kind") in {"action", "travel"})
    forged[field] = value
    Event.model_validate(forged)
    manifest["artifacts"]["events"] = write_jsonl(directory / "events.jsonl", rows)
    (directory / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(IntegrityError, match="agent-visible environment events"):
        validate_export(directory)


def test_real_export_rejects_rehashed_content_injected_into_a_wait_activity(completed_app_export):
    """@brief 实际等待事件夹带完成轮次也不能制造 repeat_watch 或手机消费，来源真实不等于正文真实。"""
    directory, _, manifest, _ = completed_app_export
    rows = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
    content = next(row["data"]["content"] for row in rows if row["event_type"] == "activity_progress"
                   and "content" in row["data"])
    waiting = next(row for row in rows if row["event_type"] == "activity_progress"
                   and row["data"]["activity"]["kind"] == "wait")
    waiting["data"]["content"] = dict(content, completed_rounds_after=7)
    Event.model_validate(waiting)
    manifest["artifacts"]["events"] = write_jsonl(directory / "events.jsonl", rows)
    (directory / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(IntegrityError, match="Content consumption must use the current action content contract"):
        validate_export(directory)


@pytest.mark.parametrize("field,value", [("source_kind", "model"), ("visibility", "agent"),
                                        ("visibility", "evaluation")])
def test_real_export_rejects_rehashed_run_end_provenance(completed_app_export, field, value):
    """@brief 真实运行结束事件重签成模型或人物正文，也不能宣称会话结束并制造过期事实。"""
    directory, _, manifest, _ = completed_app_export
    rows = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
    ending = next(row for row in rows if row["event_type"] == "run_ended")
    assert ending["source_kind"] == "environment" and ending["visibility"] == "system"
    ending[field] = value
    Event.model_validate(ending)
    manifest["artifacts"]["events"] = write_jsonl(directory / "events.jsonl", rows)
    (directory / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(IntegrityError, match="Run end must be a system-visible environment event"):
        validate_export(directory)
