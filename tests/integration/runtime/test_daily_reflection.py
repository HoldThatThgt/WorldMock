"""每日反思经真实引擎、SQLite、LifeEnv 与直接导入的 LifeAgent：每个本地日只在第一次睡醒时做一次、先反思再做当天
第一次决策，登记排在观察之前；环境对两路记忆登记的核对与记忆层按归属日期的归并。"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-only")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com/beta")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from agentsociety2.agent.base.react import MemoryUpdateEvidence
from agentsociety2.society import DirectToolCall
from tests.integration.reflection_fixtures import (
    ACTION_NOTE, CONSOLIDATED, INSIGHTS, REFLECTION_NOTE, host_run, memory, memory_layer_of, submit_reflection,
    tool_names,
)
from tests.life.conftest import (
    advance_next, conclude_request, context, do_at, sleep, stage_day_plan, tool_of, travel,
)
from tests.life.environment_runtime import act, finish_activity
from worldmock.errors import IntegrityError, ProtocolError
from worldmock.footprints.models import EventDraft
from worldmock.life import memory_evidence
from worldmock.life.decision_start import is_action_request

_ZONE = ZoneInfo("Asia/Shanghai")
_SHA = "a" * 64


@pytest.fixture(autouse=True)
def _fixed_context_window(monkeypatch) -> None:
    """脚本模型使用固定供应商窗口，不请求外部模型元数据。"""
    from worldmock.life import prompt_capacity

    monkeypatch.setattr(prompt_capacity, "model_context_length", lambda model, metadata: metadata.context_window)


def _evening(persona, config):
    """运行从周一 20:00 开始，持续四天：第一觉跨过午夜，在周二凌晨醒来。"""
    config["start_time"] = datetime.fromisoformat("2026-01-05T20:00:00+08:00")
    config["stop_time"] = datetime.fromisoformat("2026-01-09T20:00:00+08:00")


def _four_days(persona, config):
    """运行从周一 08:00 开始，持续四天。"""
    config["stop_time"] = datetime.fromisoformat("2026-01-09T08:00:00+08:00")


def _local(engine) -> datetime:
    """当前模拟时刻的本地时刻。"""
    return datetime.fromtimestamp(engine.context.state.current_time, _ZONE)


async def _started(engine_factory, tmp_path, mutate):
    """建一个真实运行与它的人物；当日计划已登记，零点计划由确定性桩生成。"""
    engine = engine_factory(mutate=mutate)
    stage_day_plan(engine, submitted=True)
    return await host_run(engine, tmp_path / "people" / "agent_0001")


def _kinds(run, since: int) -> list[str]:
    """从第 since 个请求起，脚本模型收到的请求种类。"""
    return [kind for kind, _ in run.model.requests[since:]]


def _notes(layer: dict) -> dict[str, list[str]]:
    """记忆层 earlier_days 各日期下的感想正文。"""
    return {entry["date"]: [note["text"] for note in entry["self_notes"]] for entry in layer["earlier_days"]}


@pytest.mark.asyncio
async def test_first_wake_up_reflects_registers_and_only_then_takes_the_one_observation(engine_factory, tmp_path):
    """夜间睡眠醒来的那一步：先是记忆巩固与就寝反思的模型请求和响应，再是反思登记（写入点恰为 reflection、归属前一天、
    来源观察为空、事实窗口为空、MEMORY.md 身份前后相等），然后才是这次决策唯一的一份观察与行动请求；行动请求的记忆层
    已含刚写的感想。反思阶段的调用用独立的 reflection 序号，决策回合仍从 0001 起；这些请求不是行动请求，不触发
    decision_started，回复正文也不被读成世界请求的自述。"""
    run = await _started(engine_factory, tmp_path, _evening)
    await run.rest_and_note("晚上在家歇了一会儿，心里踏实。")
    slept = await run.sleep(480)
    assert _local(run.engine).date().isoformat() == "2026-01-06"
    before = len(run.model.requests)
    decision = await run.conclude()

    assert _kinds(run, before) == ["consolidation", "reflection", "action"]
    rows = run.events(decision=decision)
    assert [row.event_type for row in rows[:11]] == [
        "model_request", "model_response", "model_request", "model_response", "memory_updated", "observation",
        "decision_started", "model_request", "model_response", "action_requested", "memory_updated"]
    identity = decision.decision_id
    assert [row.data["call_id"] for row in rows if row.event_type == "model_request"] == [
        f"{identity}:reflection:0001:attempt:1", f"{identity}:reflection:0002:attempt:1",
        f"{identity}:model:0001:attempt:1"]
    for row in rows[:4:2]:
        request = row.data["request"]
        assert is_action_request(request) is False and tool_names(request) in ([], ["submit_reflection"])
        assert request["extra_body"] == {"thinking": {"type": "disabled"}} and not row.data.get("purpose")
    assert tool_names(rows[2].data["request"]) == ["submit_reflection"]

    registered, tail = rows[4].data, rows[10].data
    assert registered["trigger"] == ["reflection"] and registered["operation"] == "append_episodes"
    assert registered["reflection"] == {"date": "2026-01-05", "sleep_activity_id": slept}
    assert registered["observation_event_id"] is None and registered["verified_event_ids"] == []
    assert registered["memory_before_sha256"] == registered["memory_sha256"] and registered["consolidated"] is True
    assert registered["decision_id"] == identity and registered["merged_episode_ids"] == []
    assert [(item["source"], item["type"]) for item in registered["episodes"]] == [("reflection", "reflection")]
    assert tail["reflection"] is None and tail["trigger"] == [] and tail["observation_event_id"] == rows[5].event_id
    assert not [item for item in tail["episodes"] if item.get("source") == "reflection"]
    assert [row.event_type for row in rows].count("observation") == 1
    assert rows[9].data["self_note"] == ACTION_NOTE != REFLECTION_NOTE

    insight = registered["episodes"][0]["text"]
    layer = memory_layer_of(run.model.of("action")[-1])
    assert layer == rows[5].data["memory_layer"]
    assert layer["earlier_days"] == [{"date": "2026-01-05", "settled": [],
                                      "self_notes": [{"activity": None, "text": insight}]}]
    consolidation = json.loads(run.model.of("consolidation")[-1]["messages"][1]["content"])
    assert consolidation["reason"] == "daily reflection"
    assert run.agent._memory_runtime.store.read_subjective_summary() == CONSOLIDATED


@pytest.mark.asyncio
async def test_one_reflection_per_local_day_whatever_else_happens_that_day(engine_factory, tmp_path):
    """睡醒那一秒的第二次决策不再反思；同一天午睡醒来、同一天再睡一觉醒来都不反思，也不因睡眠而强制巩固；
    第二天第一次睡醒再做一次，记在前一天名下。整个运行里每个本地日至多一条反思登记，感想在之后几天继续显示，
    每次决策恰有一份观察；睡醒那一步的只读查询与同秒续接照常工作。"""
    run = await _started(engine_factory, tmp_path, _evening)
    await run.rest_and_note("晚上在家歇了一会儿，心里踏实。")
    await run.sleep(480)
    woke = run.engine.context.state.current_time
    run.model.actions.append(("memory_recent", {"limit": 3}))
    first = await run.conclude()
    assert [row.data["call_id"].split(":")[-3] for row in run.events("model_request", first)] == [
        "0001", "0002", "0001", "0002"]
    queried = run.model.of("action")[-1]["messages"][-1]
    assert queried["role"] == "tool" and INSIGHTS[0] in queried["content"]

    before = len(run.model.requests)
    second, _ = await run.start("interact", "家", "测试休息")
    assert second.observed_at.timestamp() == woke and _kinds(run, before) == ["action"]
    assert len(run.model.of("action")[-1]["messages"]) > 2
    before = len(run.model.requests)
    await run.conclude(memory("早上又歇了一会儿。"))
    await run.sleep(60)
    assert _local(run.engine).date().isoformat() == "2026-01-06"
    await run.conclude()
    await run.sleep(600)
    assert _local(run.engine).date().isoformat() == "2026-01-06"
    await run.conclude()
    assert set(_kinds(run, before)) == {"action"}
    assert [event.data["reflection"]["date"] for event in run.reflections()] == ["2026-01-05"]

    await run.sleep(600)
    assert _local(run.engine).date().isoformat() == "2026-01-07"
    before = len(run.model.requests)
    await run.conclude()
    assert _kinds(run, before) == ["consolidation", "reflection", "action"]
    reflections = run.reflections()
    assert [event.data["reflection"]["date"] for event in reflections] == ["2026-01-05", "2026-01-06"]
    days = [datetime.fromtimestamp(event.occurred_at, _ZONE).date() for event in reflections]
    assert len(set(days)) == len(days) == 2
    notes = _notes(memory_layer_of(run.model.of("action")[-1]))
    assert notes == {"2026-01-05": [INSIGHTS[0]], "2026-01-06": [INSIGHTS[1]]}
    observed: dict[str, int] = {}
    for event in run.events("observation"):
        observed[event.decision_id] = observed.get(event.decision_id, 0) + 1
    assert set(observed.values()) == {1} and len(observed) == run.rounds


@pytest.mark.asyncio
async def test_nap_after_an_all_nighter_reflects_and_files_under_the_day_without_sleep(engine_factory, tmp_path):
    """周一从早到晚没睡、通宵到周二上午，在办公室小睡一小时：周一没有任何反思；周二这次小睡是当天第一次睡醒，
    醒来时做反思并记在周一名下，读到的是此前写下的全部记录。"""
    run = await _started(engine_factory, tmp_path, _four_days)
    engine = run.engine
    await run.rest_and_note("周一早上在家歇了一会儿。")
    _, trip = act(engine, "to-office", travel("公司", "驾车"))
    finish_activity(engine, trip, "to-office")
    for index in range(3):
        _, work = act(engine, f"work-{index}", do_at("公司", "季度销售数据整理"))
        assert work.status == "in_progress", work.error_code
        finish_activity(engine, work, f"work-{index}")
        assert run.reflections() == []
    assert _local(engine).date().isoformat() == "2026-01-06" and _local(engine).hour >= 11
    assert not [item for item in engine.context.state.activities.values() if item.kind == "sleep"]
    nap = await run.sleep(60)
    assert _local(engine).date().isoformat() == "2026-01-06"
    before = len(run.model.requests)
    await run.conclude()
    assert _kinds(run, before) == ["consolidation", "reflection", "action"]
    (registered,) = run.reflections()
    assert registered.data["reflection"] == {"date": "2026-01-05", "sleep_activity_id": nap}
    assert engine.context.state.activities[nap].location_id == "office"
    supplied = json.loads(run.model.of("reflection")[-1]["messages"][1]["content"].split(
        "<memory_reflection_input>", 1)[1].split("</memory_reflection_input>", 1)[0])["episodes"]
    assert [item["text"] for item in supplied] == ["周一早上在家歇了一会儿。"]


@pytest.mark.asyncio
async def test_person_without_any_record_never_calls_the_model_and_the_day_is_not_made_up(engine_factory, tmp_path):
    """运行开始不反思；人物的记忆里一条记录都没有时，当天第一次睡醒不调用模型、不登记；当天之后写下了记录再睡醒，
    也不补这一天的反思。"""
    run = await _started(engine_factory, tmp_path, _four_days)
    assert memory_evidence.due_reflection(run.engine.context.state, 1) is None
    nap = await run.sleep(60)
    assert memory_evidence.due_reflection(run.engine.context.state, 1) == {
        "date": "2026-01-04", "sleep_activity_id": nap}
    await run.conclude(memory("睡了一小会儿，精神好些了。"))
    await run.sleep(60)
    await run.conclude()
    assert {kind for kind, _ in run.model.requests} == {"action"}
    assert run.reflections() == [] and run.events("memory_updated") != []
    assert all(event.data["reflection"] is None for event in run.events("memory_updated"))
    assert [item["source"] for item in run.agent._memory_runtime.store.iter_episodes()] == ["activity_conclusion"]


@pytest.mark.asyncio
async def test_reflection_does_not_use_the_decision_turn_budget(engine_factory, tmp_path):
    """反思阶段的两次模型调用不占决策的回合预算：把回合上限设成 1，睡醒那一步照样完成决策。"""
    run = await _started(engine_factory, tmp_path, _evening)
    await run.rest_and_note("晚上在家歇了一会儿，心里踏实。")
    await run.sleep(480)
    run.agent._max_react_turns = 1
    before = len(run.model.requests)
    decision = await run.conclude()
    assert _kinds(run, before) == ["consolidation", "reflection", "action"]
    assert run.agent._model_call_sequence == 1 and len(run.reflections()) == 1
    (action,) = [row for row in run.events("model_request", decision) if is_action_request(row.data["request"])]
    assert action.data["call_id"].endswith(":model:0001:attempt:1")
    assert "memory_recent" not in tool_names(action.data["request"])


@pytest.mark.asyncio
async def test_exhausted_reflection_retries_fault_the_step_before_any_observation(engine_factory, tmp_path):
    """反思输出首次之外三次重试仍不合合同是运行故障：人物这一步抛出，库里只有巩固与四次反思的请求和响应，
    没有这次决策的记忆登记、观察、decision_started 或世界请求，也没有写入任何感想。"""
    run = await _started(engine_factory, tmp_path, _evening)
    await run.rest_and_note("晚上在家歇了一会儿，心里踏实。")
    await run.sleep(480)
    run.model.reflection_replies = [lambda episodes: submit_reflection([])] * 4
    decision = await run.open()
    run.model.actions.append(("conclude_activity", {"items": [], "errands": []}))
    with pytest.raises(ProtocolError, match="memory reflection output remained invalid after 3 retries"):
        await run.agent.step(0, decision.observed_at)
    rows = run.events(decision=decision)
    assert [row.event_type for row in rows] == ["model_request", "model_response"] * 5
    assert [row.data["call_id"].split(":")[-3] for row in rows[::2]] == ["0001", "0002", "0003", "0004", "0005"]
    assert all(":reflection:" in row.data["call_id"] for row in rows)
    assert len(run.model.of("reflection")) == 4 and len(run.model.actions) == 1
    assert run.reflections() == []
    assert [item["source"] for item in run.agent._memory_runtime.store.iter_episodes()] == ["activity_conclusion"]
    await run.environment.abort_decision_round(decision.round_id, "memory reflection output remained invalid")
    assert run.events("observation", decision) == [] and run.events("memory_updated", decision) == []


def _evidence(decision, observation_event_id, source: str | None = "reflection", **changes) -> MemoryUpdateEvidence:
    """一份形状合法的记忆更新证据：新增一条主观条目，来源观察可为空。"""
    snapshot = {"id": "m_00000001_000001", "type": "reflection", "text": "想把日子过得踏实一些。",
                "epistemic_status": "subjective", **({"source": source} if source else {})}
    fields = dict(context=decision, operation="append_episodes", created_episode_ids=(snapshot["id"],),
                  merged_episode_ids=(), episodes=(snapshot,), memory_before_sha256=_SHA, memory_sha256=_SHA,
                  episodes_sha256="b" * 64, state_sha256="c" * 64, consolidated=False,
                  observation_event_id=observation_event_id, verified_event_ids=())
    return MemoryUpdateEvidence(**{**fields, **changes})


def _open(engine, label: str):
    """在当前时刻开一轮决策，返回它的上下文。"""
    decision = context(engine, label)
    engine.begin_decision_round([decision])
    return decision


def _commit(engine, decision, request, tool: str | None = None, observe: bool = True):
    """在已开的这一轮里取观察（还没取过时）、登记一条世界请求并结算，关上这一轮。"""
    if observe:
        engine.observe(decision)
    engine.submit_action(DirectToolCall(decision, f"action-{decision.round_id}", tool or tool_of(request), request))
    (result,) = engine.commit_decision_round(decision.round_id)
    return result


def _sleep_until_awake(engine, decision, minutes: int = 60) -> str:
    """在已开的这一轮里睡一觉并推进到睡醒那一刻，返回睡眠活动的身份。"""
    result = _commit(engine, decision, sleep(minutes))
    assert result.status == "in_progress", result.error_code
    while engine.context.state.activities[result.activity_id].status == "active":
        advance_next(engine)
    return result.activity_id


def _conclude_sleep(engine, decision, observe: bool = True) -> None:
    """在已开的这一轮里给刚结束的睡眠收尾。"""
    name = next(item.name for item in engine.context.state.activities.values()
                if item.kind == "sleep" and item.status == "completed" and item.concluded_at is None)
    result = _commit(engine, decision, conclude_request(name), "conclude_activity", observe)
    assert result.status == "completed", result.error_code


def _refused(engine, evidence, caplog) -> None:
    """环境以运行故障拒绝一份记忆更新：固定的英文原因与英文错误日志，不写事件。"""
    sequence = engine.context.store.last_event_seq
    caplog.clear()
    with caplog.at_level(logging.ERROR), pytest.raises(ProtocolError) as failure:
        engine.record_memory_update(evidence)
    assert str(failure.value) == "Memory update evidence is invalid"
    assert any(record.getMessage().startswith("Memory update evidence rejected: ") for record in caplog.records)
    assert engine.context.store.last_event_seq == sequence


def test_environment_refuses_reflection_registrations_that_are_not_due(engine_factory, caplog):
    """环境对空观察一路的核对：不该反思时（运行开始）、夹带了不是反思的条目、带事实窗口、MEMORY.md 身份前后不等、
    不是新增条目、同一时刻第二次登记、这次决策已经取了观察，都是运行故障；步尾登记里夹带来源为 reflection 的条目
    同样是运行故障。合法的两路登记各写一条事件。"""
    engine = engine_factory(mutate=_four_days)
    opening = _open(engine, "opening")
    _refused(engine, _evidence(opening, None), caplog)
    slept = _sleep_until_awake(engine, opening)

    woke = _open(engine, "woke")
    assert engine.due_reflection(woke) == {"date": "2026-01-04", "sleep_activity_id": slept}
    _refused(engine, _evidence(woke, None, source=None), caplog)
    _refused(engine, _evidence(woke, None, verified_event_ids=("event-1",)), caplog)
    _refused(engine, _evidence(woke, None, memory_sha256="d" * 64), caplog)
    _refused(engine, _evidence(woke, None, operation="refresh_summary", created_episode_ids=(), episodes=(),
                               memory_sha256="d" * 64), caplog)
    engine.record_memory_update(_evidence(woke, None))
    _refused(engine, _evidence(woke, None), caplog)
    observed = engine.observe(woke)
    _refused(engine, _evidence(woke, None), caplog)
    _refused(engine, _evidence(woke, observed["observation_event_id"]), caplog)
    engine.record_memory_update(_evidence(woke, observed["observation_event_id"], operation="refresh_summary",
                                          created_episode_ids=(), episodes=(), memory_sha256="d" * 64))
    _conclude_sleep(engine, woke, observe=False)
    updates = [event.data for event in engine.context.store.read_agent_events("memory_updated", 1)]
    assert [(row["trigger"], row["reflection"], row["observation_event_id"]) for row in updates] == [
        (["reflection"], {"date": "2026-01-04", "sleep_activity_id": slept}, None),
        ([], None, observed["observation_event_id"])]


def test_environment_refuses_a_reflection_after_the_observation_and_on_a_later_wake_up(engine_factory, caplog):
    """这次决策已经取了观察之后再交空观察的登记被拒；这一秒提交过一轮决策之后不再该反思，登记被拒；
    同一天第二次睡醒同样不该反思，登记被拒。只读方法要求决策轮已开启。"""
    engine = engine_factory(mutate=_four_days)
    _sleep_until_awake(engine, _open(engine, "first"))
    late = _open(engine, "late")
    engine.observe(late)
    _refused(engine, _evidence(late, None), caplog)
    _conclude_sleep(engine, late, observe=False)
    again = _open(engine, "again")
    assert engine.due_reflection(again) is None
    _refused(engine, _evidence(again, None), caplog)
    _sleep_until_awake(engine, again)
    nap = _open(engine, "nap")
    assert _local(engine).date().isoformat() == "2026-01-05"
    assert engine.due_reflection(nap) is None
    _refused(engine, _evidence(nap, None), caplog)
    with pytest.raises(ProtocolError, match="Decision round is not open"):
        engine.due_reflection(context(engine, "other-round"))


def _register(engine, label: str, texts: list[str]) -> dict:
    """在睡醒那一刻、取观察之前登记一次就寝反思（引擎直驱），随后取观察并给这段睡眠收尾；返回这次观察。"""
    decision = _open(engine, label)
    identities = tuple(f"m_{label}_{index}" for index in range(len(texts)))
    snapshots = tuple({"id": identity, "type": "reflection", "text": text, "epistemic_status": "subjective",
                       "source": "reflection"} for identity, text in zip(identities, texts, strict=True))
    engine.record_memory_update(_evidence(decision, None, created_episode_ids=identities, episodes=snapshots))
    observed = engine.observe(decision)
    _conclude_sleep(engine, decision, observe=False)
    return observed


def _long(day: str) -> list[str]:
    """某一天的三条很长的感想，合起来约两千九百字符。"""
    return [f"{day}的第 {index} 条感想：" + "想" * 950 for index in range(3)]


def _size(layer: dict) -> int:
    """记忆层紧凑 JSON 的字符数。"""
    return len(json.dumps(layer, ensure_ascii=False, separators=(",", ":")))


def test_memory_layer_files_insights_under_their_date_and_trims_the_oldest_date_first(engine_factory):
    """感想出现在归属日期的条目下并在之后几天继续显示；记忆层超出 8000 字符窗口时先删最旧日期的感想，
    较新日期的照样显示。"""
    engine = engine_factory(mutate=_four_days)
    stage_day_plan(engine, submitted=True)
    _sleep_until_awake(engine, _open(engine, "monday-nap"))
    first = _register(engine, "monday", _long("周日"))
    assert _notes(first["memory_layer"]) == {"2026-01-04": _long("周日")}
    _sleep_until_awake(engine, _open(engine, "monday-day"), 720)
    _conclude_sleep(engine, _open(engine, "monday-evening"))
    _sleep_until_awake(engine, _open(engine, "monday-night"), 600)
    assert _local(engine).date().isoformat() == "2026-01-06"
    second = _register(engine, "tuesday", _long("周一"))
    assert _notes(second["memory_layer"]) == {"2026-01-04": _long("周日"), "2026-01-05": _long("周一")}
    assert _size(second["memory_layer"]) <= 8000
    _sleep_until_awake(engine, _open(engine, "tuesday-day"), 720)
    _conclude_sleep(engine, _open(engine, "tuesday-evening"))
    _sleep_until_awake(engine, _open(engine, "tuesday-night"), 600)
    assert _local(engine).date().isoformat() == "2026-01-07"
    third = _register(engine, "wednesday", _long("周二"))
    assert _notes(third["memory_layer"]) == {"2026-01-05": _long("周一"), "2026-01-06": _long("周二")}
    assert _size(third["memory_layer"]) <= 8000
    assert _size({**third["memory_layer"], "earlier_days": [
        {"date": "2026-01-04", "settled": [], "self_notes": [{"activity": None, "text": text}
                                                             for text in _long("周日")]},
        *third["memory_layer"]["earlier_days"]]}) > 8000


def test_memory_layer_rejects_a_reflection_without_a_filing_date(engine_factory, caplog):
    """写入点含 reflection 而没有归属日期的记忆更新，记忆层以规定文字拒绝并记英文错误日志。"""
    engine = engine_factory()
    state = engine.context.state
    snapshot = {"id": "m_1", "type": "reflection", "text": "想休息。", "epistemic_status": "subjective",
                "source": "reflection"}
    engine.context.store.append_evidence([EventDraft(
        event_type="memory_updated", occurred_at=state.current_time, agent_id=1, decision_id="decision-old",
        visibility="system", source_kind="environment",
        data={"operation": "append_episodes", "episodes": [snapshot], "trigger": ["reflection"], "reflection": None})])
    decision = context(engine, "broken")
    engine.begin_decision_round([decision])
    with caplog.at_level(logging.ERROR), pytest.raises(IntegrityError) as failure:
        engine.observe(decision)
    assert str(failure.value) == "Bedtime reflection has no filing date"
    assert "Bedtime reflection has no filing date" in caplog.text
    assert memory_evidence.MEMORY_WRITE_POINTS == ("activity_conclusion", "reflection")
