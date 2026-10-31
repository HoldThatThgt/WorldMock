"""日、月、年回顾经真实引擎与 SQLite 生成（docs/design/06-agent.md「日、月、年回顾」）：零点任务经生产入口
runtime.settle_pending_memory_digests 执行，客户端是确定性桩 StubMemoryDigestClient 或换掉 Router 的真实 MemoryDigestClient。

除非用例另说，零点推进之后只由用例自己调用回顾任务：_live 逐边界推进时不跑任何零点任务，_finish_midnight 只跑零点计划
与节律。
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path

import pytest
from litellm.exceptions import APIConnectionError

import worldmock.runtime.lifecycle.runner as runtime
from tests.integration.life.test_opslog import _records
from tests.integration.runtime_fixtures import write_agent_workspace
from tests.life.conftest import (
    StubMemoryDigestClient,
    StubPlanDayClient,
    activity_name,
    advance_until_ready,
    conclude_request,
    do_at,
    drive,
    memory_digest_response,
    purchase,
    query,
    report_request,
    settle_midnight_tasks,
    sleep,
    stage_day_plan,
    travel,
)
from tests.integration.life.test_grocery_cooking import cook, grocery_engine, receive
from tests.life.environment_inputs import catalog_rows, write_catalog_rows
from tests.life.environment_runtime import act, act_group, events, settle_decision, until_ended
from tests.unit.as2.test_llm_dispatcher_transport import _client, _error
from worldmock.configuration.persona.persona_card import persona_card
from worldmock.configuration.persona.persona_text import render_card
from worldmock.configuration.persona.work_schedule import shift_on
from worldmock.foundation.errors import IntegrityError, ProtocolError
from worldmock.foundation.time.readable import local_date
from worldmock.recording_engine.events.models import EventDraft
from worldmock.recording_engine.storage.checkpoint import restore_branch, snapshot_directory
from worldmock.runtime.lifecycle.runner import MemoryDigestClient, settle_pending_day_plans, settle_pending_memory_digests
from worldmock.simulation_engine.engine import LifeEngine
from worldmock.simulation_engine.evidence import memory_digest
from worldmock.simulation_engine.observation.memory import days
from worldmock.simulation_engine.observation.memory.categories import memory_book
from worldmock.simulation_engine.observation.memory.digests import registered_digests
from worldmock.simulation_engine.observation.memory.entries import short_name
from worldmock.simulation_engine.world.world_updates import _request_sha
from worldmock.state.decisions.results import CounterfactualIntervention
from worldmock.state.world.kinds import ActivityKind

ZONE = "Asia/Shanghai"


def _at(moment: str) -> int:
    """上海时区的本地时刻（ISO 写法，不带时区）。"""
    return int(datetime.fromisoformat(f"{moment}+08:00").timestamp())


def _stop(moment: str):
    """运行配置：从样例的 2026-01-05（星期一）08:00 开始，到 moment 截止。"""
    def mutate(persona, config):
        config["stop_time"] = datetime.fromisoformat(f"{moment}+08:00")
    return mutate


def _late(persona, config):
    """运行配置：2026-01-05 23:00 开始，2026-01-08 08:00 截止。"""
    config["start_time"] = datetime.fromisoformat("2026-01-05T23:00:00+08:00")
    config["stop_time"] = datetime.fromisoformat("2026-01-08T08:00:00+08:00")


def _live(engine, until: int | None = None, decide=settle_decision) -> None:
    """逐边界推进并处理决策机会，零点任务一个都不跑：停在下一个零点（零点节律待判）、until 或运行终点。"""
    counter = 0
    while not engine.midnight_rhythm_pending and (until is None or engine.context.state.current_time < until):
        state = engine.scheduling_state()
        if state.ready_agent_ids:
            counter += 1
            decide(engine, f"live-{counter}-{engine.context.state.current_time}")
        elif state.next_event_at is None:
            return
        else:
            engine.advance(int(state.next_event_at.timestamp()) - engine.context.state.current_time,
                           state.next_event_at)


def _finish_midnight(engine) -> None:
    """零点只跑零点计划与节律，不跑回顾。"""
    drive(settle_pending_day_plans(engine, StubPlanDayClient({"requested_model": "test-model"})))
    engine.decide_midnight_rhythm()


def _digests(engine, client=None) -> list[dict]:
    """执行回顾任务，缺省用确定性桩。"""
    return drive(settle_pending_memory_digests(engine, client or StubMemoryDigestClient()))


def _digest_events(engine) -> list:
    """已提交的回顾登记事件。"""
    return [row for row in events(engine, "environment_event") if row.data.get("kind") == "memory_digest_generated"]


def _digest_rows(engine, phase: str) -> list:
    """回顾的模型证据（purpose 为 memory_digest），按 phase 取请求或响应。"""
    kind = "model_request" if phase == "request" else "model_response"
    return [row for row in events(engine, kind) if row.data.get("purpose") == "memory_digest"]


def _rich_day(engine) -> None:
    """2026-01-05 的一天：测试闲聊与查询柠檬茶同组开始、查询做完时播报、收尾后买柠檬茶，之后照常睡觉，最后一觉跨过零点；
    停在 01-06 零点。"""
    stage_day_plan(engine, submitted=True)
    _, started = act_group(engine, "pair", do_at("家", "测试闲聊"), query("柠檬茶"))
    assert advance_until_ready(engine)
    act(engine, "report", report_request(activity_name(engine, started[0].activity_id)), "report_status")
    until_ended(engine, started[0].activity_id)
    act(engine, "end", conclude_request(activity_name(engine, started[0].activity_id)), "conclude_activity")
    _, tea = act(engine, "buy", purchase("柠檬茶"))
    assert tea.status == "in_progress", tea.error_code
    _live(engine)


def _night(engine) -> None:
    """2026-01-05 23:00 开始睡 480 分钟，停在 01-06 零点：那天只有生活类的两条条目。"""
    stage_day_plan(engine, submitted=True)
    _, night = act(engine, "night", sleep(480))
    assert night.status == "in_progress", night.error_code
    _live(engine)


class _Built:
    """记下运行时构造过的回顾客户端；构造出的就是确定性桩。"""

    def __init__(self, monkeypatch):
        self.clients: list[StubMemoryDigestClient] = []
        built = self.clients

        class Recorded(StubMemoryDigestClient):
            def __init__(self, model_identity):
                super().__init__(model_identity)
                built.append(self)

        monkeypatch.setattr(runtime, "MemoryDigestClient", Recorded)


def _counted_entries(monkeypatch, empty: bool = False) -> list[str]:
    """把 days.past_day_entries 换成先记下日期、再转调原函数的包装（empty 时那天当作没有条目），返回记下的日期。"""
    original, calls = days.past_day_entries, []

    def counted(inputs, store, agent_id, day, now):
        calls.append(day)
        return () if empty else original(inputs, store, agent_id, day, now)

    monkeypatch.setattr(days, "past_day_entries", counted)
    return calls


def test_day_digest_payload_numbers_category_parts_of_full_entries(engine_factory):
    """日回顾的用户消息逐字节等于写死的输入：先饮食、后工作、再生活连续编号；混合条目在两类各出一行，只含各自的行和项；
    做完的查询带「当时查到」；跨过零点的收尾条目「时刻」为 24:00，排在类内最后；空类为 []；人物卡片与工作区的同一文字。"""
    engine = engine_factory(mutate=_stop("2026-01-07T08:00:00"))
    _rich_day(engine)
    client = StubMemoryDigestClient()
    assert _digests(engine, client) == [{"agent_id": 1, "tier": "day", "period": "2026-01-05", "fallback": []}]
    [request] = client.calls
    expected = {
        "人物卡片": render_card(persona_card(engine.context.persona)),
        "饮食": [
            {"编号": "n1", "时刻": "08:07", "核验": ["查完饿了么·柠檬茶：1项可买，12–12元"],
             "当时查到": ["08:07 在饿了么查柠檬茶", "柠檬茶 12元", "柠檬茶 12元", "柠檬茶 12元，当时不可买",
                          "柠檬茶 12元，当时不可买"]},
            {"编号": "n2", "时刻": "08:10", "核验": ["开始购买柠檬茶"]},
            {"编号": "n3", "时刻": "08:12", "核验": ["买柠檬茶1份，17元，预计08:28送到家"], "自述": ["购买柠檬茶：收尾。"]},
            {"编号": "n4", "时刻": "15:42", "核验": ["08:28 送达柠檬茶"]}],
        "工作": [],
        "生活": [
            {"编号": "n5", "时刻": "08:00", "核验": ["开始测试闲聊、查询柠檬茶"]},
            {"编号": "n6", "时刻": "08:07", "自述": ["进展·测试闲聊：继续。"]},
            {"编号": "n7", "时刻": "08:10", "核验": ["测试闲聊 08:00–08:10"], "自述": ["测试闲聊：收尾。"]},
            {"编号": "n8", "时刻": "08:12", "核验": ["开始睡觉"]},
            {"编号": "n9", "时刻": "15:42", "核验": ["睡觉 08:12–15:42"], "自述": ["睡觉：收尾。"]},
            {"编号": "n10", "时刻": "15:42", "核验": ["开始睡觉"]},
            {"编号": "n11", "时刻": "23:12", "核验": ["睡觉 15:42–23:12"], "自述": ["睡觉：收尾。"]},
            {"编号": "n12", "时刻": "23:12", "核验": ["开始睡觉"]},
            {"编号": "n13", "时刻": "24:00", "核验": ["23:12 开始睡觉，跨过零点"]}]}
    assert request["messages"][1]["content"] == json.dumps(expected, ensure_ascii=False, separators=(",", ":"))


def test_day_without_entries_calls_no_model_and_is_remembered(engine_factory, monkeypatch):
    """那天没有条目：不构造客户端、不调用模型、不写事件；同一库对象上再调用时不再读那天的条目。

    真实运行里 F 之后的每一天都有条目（就绪的人物总要决策，跨夜的活动有一句跨过零点），所以这里让 days.past_day_entries
    把那天当作没有条目。
    """
    engine = engine_factory(mutate=_late)
    _night(engine)
    built, calls = _Built(monkeypatch), _counted_entries(monkeypatch, empty=True)
    before = engine.context.store.last_event_seq
    assert drive(settle_pending_memory_digests(engine)) == []
    assert built.clients == [] and calls and engine.context.store.last_event_seq == before
    calls.clear()
    assert drive(settle_pending_memory_digests(engine)) == []
    assert calls == [] and built.clients == []


def test_category_without_entries_is_null_and_not_fallback(engine_factory):
    """只有饮食和生活条目的日子：工作为 null 且不进 fallback。"""
    engine = engine_factory(mutate=_stop("2026-01-07T08:00:00"))
    _rich_day(engine)
    _digests(engine)
    [event] = _digest_events(engine)
    assert event.data["digests"]["work"] is None and event.data["fallback"] == []
    assert event.data["digests"]["food"]["text"] == "收到和用到了柠檬茶。"
    assert event.data["digests"]["life"]["text"] == "这一天这一类的事照常。"


def _without_work(persona, config):
    """人物没有工作安排（目录也没有「工作」覆盖），从 2026-01-05 23:00 开始。"""
    _late(persona, config)
    del persona["work_schedule"]


def test_disabled_work_category_is_always_null(engine_factory):
    """工作类不启用：用户消息的「工作」为 []，事件的 work 为 null 且不进 fallback；模型给工作写一段时这次尝试报错。"""
    engine = engine_factory(mutate=_without_work)
    assert memory_book(engine.context.store, engine.context.inputs).categories == ("饮食", "生活")
    _night(engine)
    [inputs] = memory_digest.pending_inputs(engine.context, engine.observations, 1, "day")
    assert inputs.payload["工作"] == [] and inputs.rows["work"] == ()
    life = [row.number for row in inputs.rows["life"]]
    wrong = memory_digest_response({"food": None, "work": {"text": "上班。", "refs": life},
                                    "life": {"text": "睡了一觉。", "refs": life}})
    _digests(engine, StubMemoryDigestClient(responses=[wrong]))
    first, second = _digest_rows(engine, "response")
    assert first.data["error"] == "digests.work must be null: the input has no work records"
    assert second.data["error"] is None
    [event] = _digest_events(engine)
    assert event.data["digests"]["work"] is None and "work" not in event.data["fallback"]
    assert event.data["digests"]["life"]["text"] == "睡了一觉。"


def test_registration_commits_evidence_and_event_in_one_version(engine_factory, monkeypatch):
    """一次登记：证据与登记事件同一次提交、同一前后版本、序号连续；世界只有版本加一；信封、data 键序与起因。"""
    engine = engine_factory(mutate=_late)
    _night(engine)
    committed, original = [], engine.context.commit_step

    def spy(staged, drafts):
        committed.append(list(drafts))
        return original(staged, drafts)

    monkeypatch.setattr(engine.context, "commit_step", spy)
    world = engine.context.state.model_dump(mode="json")
    version, first = world.pop("version"), engine.context.store.last_event_seq
    _digests(engine)
    after = engine.context.state.model_dump(mode="json")
    assert after.pop("version") == version + 1 and after == world
    [drafts] = committed
    assert [draft.event_type for draft in drafts] == ["model_request", "model_response", "environment_event"]
    assert list(drafts[-1].data) == ["kind", "tier", "period", "digests", "fallback"]
    assert list(drafts[-1].data["digests"]) == ["food", "work", "life"]
    assert list(drafts[-1].data["digests"]["life"]) == ["text", "source_event_ids", "cited_event_ids"]
    rows = list(engine.context.store.iter_events(engine.context.store.last_event_seq, after=first))
    assert [row.event_seq for row in rows] == list(range(first + 1, first + 4))
    assert {(row.state_version_before, row.state_version_after) for row in rows} == {(version, version + 1)}
    request, response, event = rows
    assert (event.event_type, event.agent_id, event.occurred_at) == ("environment_event", 1, _at("2026-01-06T00:00:00"))
    assert (event.source_kind, event.visibility, event.resource_changes) == ("model", "system", {})
    assert (event.decision_id, event.action_id, event.activity_id) == (None, None, None)
    assert event.caused_by == [response.event_id] and response.caused_by == [request.event_id]
    assert event.data["kind"] == "memory_digest_generated" and event.data["period"] == "2026-01-05"


def _registered(engine) -> tuple:
    """登记过 2026-01-05 日回顾之后：（那一期的结果，一份新的请求证据）。"""
    _night(engine)
    [inputs] = memory_digest.pending_inputs(engine.context, engine.observations, 1, "day")
    _digests(engine)
    accepted = {"life": memory_digest.DigestParagraph(text="睡了一觉。", refs=[inputs.rows["life"][0].number])}
    evidence = [memory_digest.request_draft(engine.context.state.current_time, 1, ("again", 1, None), {})]
    return memory_digest.digest_outcome(inputs, accepted), evidence


def test_registering_the_same_period_twice_is_a_protocol_error(engine_factory, caplog):
    """再登记同一期是协议错误：先写带调用前缀的错误日志，库里没有新事件；证据为空同样是协议错误。"""
    engine = engine_factory(mutate=_late)
    outcome, evidence = _registered(engine)
    before = engine.context.store.last_event_seq
    with caplog.at_level(logging.ERROR), pytest.raises(
            ProtocolError, match="^Memory digest was already generated for this period$"):
        engine.register_memory_digest(1, outcome, evidence)
    assert "Memory digest was already generated for memory-digest:1:day:2026-01-05" in caplog.messages
    with pytest.raises(ProtocolError, match="^Memory digest registration requires model evidence$"):
        engine.register_memory_digest(1, outcome, [])
    assert "Memory digest registration requires model evidence" in caplog.messages
    assert engine.context.store.last_event_seq == before


def test_registry_rejects_a_period_registered_twice(engine_factory):
    """手工追加第二条同期的回顾事件之后，唯一读取入口报完整性错误。"""
    engine = engine_factory(mutate=_late)
    _registered(engine)
    [event] = _digest_events(engine)
    staged = engine.context.state.model_copy(deep=True)
    staged.version += 1
    engine.context.commit_step(staged, [EventDraft(
        event_type="environment_event", occurred_at=event.occurred_at, agent_id=1, caused_by=list(event.caused_by),
        visibility="system", source_kind="model", data=dict(event.data))])
    with pytest.raises(IntegrityError, match="^Memory digest period was registered twice$"):
        registered_digests(engine.context.store, 1)


#: 超过日回顾字数上限（250）的饮食段落（260 字，连标点）。
_LONG_FOOD = "收到和用到了柠檬茶。" + "我" * 250


def _bad_food(engine) -> dict:
    """饮食类正文超过日回顾字数上限、其余类合格的一次回答。"""
    [inputs] = memory_digest.pending_inputs(engine.context, engine.observations, 1, "day")
    arguments = StubMemoryDigestClient().answer(inputs.payload)
    arguments["food"] = {**arguments["food"], "text": _LONG_FOOD}
    return memory_digest_response(arguments)


def test_validation_exhaustion_records_fallback_and_the_run_continues(engine_factory, caplog):
    """四次都给不合格的饮食：四次请求、四次响应证据，最后一次带错误；饮食进 fallback，其余类照常登记，任务不抛出。"""
    engine = engine_factory(mutate=_stop("2026-01-07T08:00:00"))
    _rich_day(engine)
    bad = _bad_food(engine)
    with caplog.at_level(logging.ERROR):
        assert _digests(engine, StubMemoryDigestClient(responses=[bad] * 4)) == [
            {"agent_id": 1, "tier": "day", "period": "2026-01-05", "fallback": ["food"]}]
    assert [row.data["call_id"] for row in _digest_rows(engine, "request")] == [
        f"memory-digest:1:day:2026-01-05:attempt:{number}" for number in (1, 2, 3, 4)]
    responses = _digest_rows(engine, "response")
    assert len(responses) == 4 and all(row.data["error"] for row in responses)
    assert responses[-1].data["error"] == (
        "digests.food.text has 260 characters counting punctuation; the day digest limit is 250; "
        "shorten it by at least 10")
    assert responses[0].data["error"] == responses[-1].data["error"]
    [event] = _digest_events(engine)
    assert event.data["fallback"] == ["food"] and event.data["digests"]["food"] is None
    assert event.data["digests"]["life"] is not None
    assert ("Memory digest validation retries exhausted for memory-digest:1:day:2026-01-05; "
            "fallback categories: food") in caplog.messages
    assert "Memory digest call memory-digest:1:day:2026-01-05:attempt:4 failed validation" in caplog.messages
    engine.decide_midnight_rhythm()
    assert engine.scheduling_state().ready_agent_ids or engine.scheduling_state().next_event_at is not None


def _retries(count: int):
    """回顾校验重试上限为 count 的运行配置（其余同 _stop 到 01-07 08:00）。"""
    def mutate(persona, config):
        _stop("2026-01-07T08:00:00")(persona, config)
        config["model"]["reflection_validation_max_retries"] = count
    return mutate


@pytest.mark.parametrize("count,attempts", [(0, 1), (3, 4)], ids=["0", "3"])
def test_retry_budget_is_reflection_validation_max_retries(engine_factory, count, attempts):
    """尝试次数是 reflection_validation_max_retries 加一：上限 0 时一次就降级，3 时四次。"""
    engine = engine_factory(mutate=_retries(count))
    _rich_day(engine)
    bad = _bad_food(engine)
    assert _digests(engine, StubMemoryDigestClient(responses=[bad] * 5))[0]["fallback"] == ["food"]
    assert len(_digest_rows(engine, "request")) == len(_digest_rows(engine, "response")) == attempts


def test_transport_failure_commits_evidence_before_raising(engine_factory, caplog):
    """传输失败：请求与失败证据已提交，没有登记事件，异常原样抛出。"""
    engine = engine_factory(mutate=_late)
    _night(engine)
    failure = ConnectionError("network is down")
    with caplog.at_level(logging.ERROR), pytest.raises(ConnectionError) as raised:
        _digests(engine, StubMemoryDigestClient(raises=failure))
    assert raised.value is failure
    assert [row.data["phase"] for row in _digest_rows(engine, "request")] == ["request"]
    [stored] = _digest_rows(engine, "response")
    assert (stored.data["phase"], stored.data["error"]) == ("failure", repr(failure))
    assert _digest_events(engine) == []
    assert "Memory digest model request failed: ConnectionError" in caplog.messages


def _year_end(persona, config):
    """运行配置：2026-12-31（星期四）20:00 开始，2027-01-01 08:00 截止。"""
    config["start_time"] = datetime.fromisoformat("2026-12-31T20:00:00+08:00")
    config["stop_time"] = datetime.fromisoformat("2027-01-01T08:00:00+08:00")


def _month_end(persona, config):
    """运行配置：2026-10-31（星期六）20:00 开始，2026-11-01 08:00 截止。"""
    config["start_time"] = datetime.fromisoformat("2026-10-31T20:00:00+08:00")
    config["stop_time"] = datetime.fromisoformat("2026-11-01T08:00:00+08:00")


def _resting(engine, label) -> None:
    """当天 22:00 之前在家没事做时做一次「测试休息」，其余照 settle_decision（22:00 起在家睡觉过夜）。"""
    state = engine.context.state
    busy = [item for item in state.activities.values()
            if item.status in {"active", "paused"} or item.concluded_at is None]
    bedtime = _at(f"{local_date(state.current_time, ZONE)}T22:00:00")
    if not busy and state.current_time < bedtime:
        _, rest = act(engine, label, do_at("家", "测试休息"))
        assert rest.status == "in_progress", rest.error_code
    else:
        settle_decision(engine, label)


def _evening(engine, until: int | None = None) -> None:
    """20:00 起在家：测试闲聊与买柠檬茶同组开始，之后 22:00 之前没事做就做「测试休息」，22:00 起睡觉跨过零点；停在 until
    或下一个零点。"""
    stage_day_plan(engine, submitted=True)
    _, started = act_group(engine, "pair", do_at("家", "测试闲聊"), purchase("柠檬茶"))
    assert [item.status for item in started] == ["in_progress", "in_progress"]
    _live(engine, until, decide=_resting)


def _rests(engine) -> int:
    """完成或收尾了的「测试休息」项数。"""
    return sum(short_name(item.name) == "测试休息" and item.status in {"completed", "cancelled"}
               for item in engine.context.state.activities.values())


def _digest_seq(engine, period: str) -> int:
    """这一期回顾登记事件的序号。"""
    [row] = [row for row in _digest_events(engine) if row.data["period"] == period]
    return row.event_seq


def _plan_seq(engine, day: str) -> int:
    """这一天零点登记的零点计划事件的序号。"""
    [row] = [row for row in events(engine, "environment_event") if row.data.get("kind") == "day_plan_generated"
             and row.occurred_at == _at(f"{day}T00:00:00")]
    return row.event_seq


def _periods(engine) -> list[tuple[str, str]]:
    """已登记的回顾期次，按事件序号。"""
    return [(row.data["tier"], row.data["period"]) for row in _digest_events(engine)]


def test_month_digest_reads_day_digests_and_count_lines(engine_factory):
    """10-31 20:00 → 11-01 08:00：11-01 零点先登记 10-31 的日回顾（生活类四次都超过字数上限、记为未整理），再登记 2026-10 的月回顾；
    月回顾的用户消息是 d 编号的日回顾行（饮食一行回顾、生活一行「未整理」）与按次数统计的行，没有人物卡片与月份；事件序号上
    日回顾 < 月回顾 < 11-01 的零点计划。"""
    engine = engine_factory(mutate=_month_end)
    _evening(engine)
    [day] = memory_digest.pending_inputs(engine.context, engine.observations, 1, "day")
    arguments = StubMemoryDigestClient().answer(day.payload)
    arguments["life"] = {**arguments["life"], "text": "这一天这一类的事照常。" + "我" * 250}
    client = StubMemoryDigestClient(responses=[memory_digest_response(arguments)] * 4)
    settle_midnight_tasks(engine, digest_client=client)
    assert _periods(engine) == [("day", "2026-10-31"), ("month", "2026-10")]
    assert _digest_events(engine)[0].data["fallback"] == ["life"]
    assert [json.loads(call["messages"][1]["content"]).get("人物卡片") is None for call in client.calls] == [
        False] * 4 + [True]
    expected = {
        "饮食": {"日回顾": [{"编号": "d1", "日期": "2026-10-31", "回顾": "收到和用到了柠檬茶。"}], "次数统计": ["柠檬茶 1 次"]},
        "工作": {"日回顾": [], "次数统计": []},
        "生活": {"日回顾": [{"编号": "d2", "日期": "2026-10-31", "未整理": True}],
                 "次数统计": [f"测试休息 {_rests(engine)} 次", "测试闲聊 1 次"]}}
    assert _rests(engine) > 1
    assert client.calls[-1]["messages"][0]["content"] == memory_digest.MONTH_DIGEST_PROMPT
    assert client.calls[-1]["messages"][1]["content"] == json.dumps(expected, ensure_ascii=False, separators=(",", ":"))
    month = _digest_events(engine)[1]
    assert month.data["digests"]["life"]["text"] == "这段时间的回顾已经整理。"
    assert month.data["digests"]["life"]["source_event_ids"] == [_digest_events(engine)[0].event_id]
    assert _digest_seq(engine, "2026-10-31") < _digest_seq(engine, "2026-10") < _plan_seq(engine, "2026-11-01")


def test_year_digest_reads_month_digests_and_year_counts(engine_factory):
    """12-31 20:00 → 2027-01-01 08:00：01-01 零点依次登记 12-31 日回顾、2026-12 月回顾、2026 年回顾，序号递增，年回顾在
    2027-01-01 的零点计划之前；年回顾的用户消息是 m 编号的月回顾行与全年的次数统计，没有人物卡片与年份。"""
    engine = engine_factory(mutate=_year_end)
    _evening(engine)
    client = StubMemoryDigestClient()
    settle_midnight_tasks(engine, digest_client=client)
    assert _periods(engine) == [("day", "2026-12-31"), ("month", "2026-12"), ("year", "2026")]
    assert [call["messages"][0]["content"] for call in client.calls] == [
        memory_digest.DAY_DIGEST_PROMPT, memory_digest.MONTH_DIGEST_PROMPT, memory_digest.YEAR_DIGEST_PROMPT]
    layer = engine.observations._layer_context(engine.context.state.agents[1], [])
    counts = days.count_lines(layer, "2026-01-01", "2026-12-31")
    assert counts["生活"] == (f"测试休息 {_rests(engine)} 次", "测试闲聊 1 次") and counts["饮食"] == ("柠檬茶 1 次",)
    expected = {
        "饮食": {"月回顾": [{"编号": "m1", "月份": "2026-12", "回顾": "这段时间的回顾已经整理。"}], "次数统计": ["柠檬茶 1 次"]},
        "工作": {"月回顾": [], "次数统计": []},
        "生活": {"月回顾": [{"编号": "m2", "月份": "2026-12", "回顾": "这段时间的回顾已经整理。"}],
                 "次数统计": list(counts["生活"])}}
    assert client.calls[-1]["messages"][1]["content"] == json.dumps(expected, ensure_ascii=False, separators=(",", ":"))
    year = _digest_events(engine)[2]
    assert year.data["digests"]["food"]["source_event_ids"] == [_digest_events(engine)[1].event_id]
    assert (_digest_seq(engine, "2026-12-31") < _digest_seq(engine, "2026-12") < _digest_seq(engine, "2026")
            < _plan_seq(engine, "2027-01-01"))


def _counterfactual(engine, checkpoint, tmp_path: Path, name: str) -> LifeEngine:
    """从检查点开出的反事实分支：分支元数据带干预摘要，打开后先提交一项余额干预。"""
    intervention = CounterfactualIntervention(
        kind="set_balance_fen", agent_id=1, balance_fen=150000, hypothesis="余额改变不影响已经登记的回顾。",
        source_event_ids=(events(engine, "action_result")[-1].event_id,))
    metadata = {"branch_kind": "counterfactual",
                "intervention_sha256": _request_sha("counterfactual_intervention", intervention)}
    restore_branch(engine.context.store, checkpoint.checkpoint_id, tmp_path / name, metadata).close()
    child = LifeEngine.open(engine.context.inputs, tmp_path / name)
    child.apply_counterfactual(intervention)
    return child


def test_month_and_year_digests_are_not_regenerated_on_resume_or_branch(engine_factory, tmp_path):
    """过了 2027-01-01 零点取检查点 A：从 A 普通恢复、从 A 开反事实分支，回顾任务都什么也不生成，各自库里三期各恰一条；
    从 12-31 22:00 之后、零点之前的检查点开分支跑过零点，分支里 12-31 日回顾、2026-12 月回顾、2026 年回顾各生成一次。"""
    engine = engine_factory(mutate=_year_end)
    _evening(engine, until=_at("2026-12-31T22:00:00"))
    before = _checkpoint(engine, tmp_path, "before-midnight")
    _live(engine, decide=_resting)
    settle_midnight_tasks(engine)
    every = [("day", "2026-12-31"), ("month", "2026-12"), ("year", "2026")]
    assert _periods(engine) == every
    point = _checkpoint(engine, tmp_path, "a")
    for child in (_branch(engine, point, tmp_path, "resume"), _counterfactual(engine, point, tmp_path, "replay")):
        try:
            assert _digests(child) == [] and _periods(child) == every
        finally:
            child.close()
    child = _branch(engine, before, tmp_path, "before")
    try:
        assert _periods(child) == []
        _live(child, decide=_resting)
        settle_midnight_tasks(child)
        assert _periods(child) == every
    finally:
        child.close()


def _working(engine, label) -> None:
    """跑到运行终点时在公司做「季度销售数据整理」，其余照 settle_decision。"""
    state = engine.context.state
    busy = [item for item in state.activities.values()
            if item.status in {"active", "paused"} or item.concluded_at is None]
    if not busy and state.agents[1].location_id == "office":
        _, work = act(engine, label, do_at("公司", "季度销售数据整理"))
        assert work.status == "in_progress", work.error_code
    else:
        settle_decision(engine, label)


def _finish(engine, activity_id: str, label: str) -> None:
    """推进到这项活动终结（其间的决策照 until_ended），再按它的冻结名称收尾。"""
    until_ended(engine, activity_id)
    name = engine.context.state.activities[activity_id].name
    _, concluded = act(engine, label, conclude_request(name), "conclude_activity")
    assert concluded.status == "completed", concluded.error_code


def _purchase_expense(root: Path) -> None:
    """每次购买动作受理时另扣 10 元（回调规则）：受理时钱够、扣过之后付款时不够的那一次购买失败。"""
    rows = catalog_rows(root, "callbacks.jsonl")
    rows.append({"rule_id": "purchase-expense", "trigger": {"class": "action", "key": "purchase"},
                 "effect": {"type": "transfer", "direction": "expense", "amount_fen": 1000}})
    write_catalog_rows(root, "callbacks.jsonl", rows)


def test_count_lines_count_food_receipts_cooked_kits_and_ended_activities(engine_factory, tmp_path):
    """同一天里两次收到柠檬茶、收到食材包扑克牌并用它做饭、三次人物收尾的「测试休息」、买到的平台笔记本、做完的「测试闲聊」、
    付款时钱不够而失败的第二次购买平台笔记本、两项同短名且人物收尾的「季度销售数据整理」与运行截止截断的第三项：推进到运行
    终点后直接统计，饮料按收货计 2 次，食材包只按做饭计 1 次（它的收货与购买不计），失败与截断的活动不计，同短名的合并计入；
    次数相同按首次计入的事件先后。"""
    def mutate(persona, config):
        _stop("2026-01-05T18:00:00")(persona, config)
        persona["initial_balance_fen"] = 12000

    engine = grocery_engine(engine_factory, tmp_path, mutate=mutate, change=_purchase_expense, initial=0)
    stage_day_plan(engine, submitted=True)
    for goods in ("柠檬茶", "柠檬茶", "扑克牌"):
        receive(engine, goods)
    _finish(engine, cook(engine).activity_id, "cooked")
    _finish(engine, act(engine, "notebook", purchase("平台笔记本"))[1].activity_id, "bought")
    _finish(engine, act(engine, "chat", do_at("家", "测试闲聊"))[1].activity_id, "chatted")
    _, broke = act(engine, "broke", purchase("平台笔记本"))
    _finish(engine, broke.activity_id, "broke-end")
    assert engine.context.state.activities[broke.activity_id].status == "failed"
    _finish(engine, act(engine, "trip", travel("公司"))[1].activity_id, "arrived")
    for label in ("first", "second"):
        _, work = act(engine, label, do_at("公司", "季度销售数据整理"))
        assert advance_until_ready(engine)
        act(engine, f"{label}-end", conclude_request(engine.context.state.activities[work.activity_id].name),
            "conclude_activity")
    _live(engine, decide=_working)
    state = engine.context.state
    assert state.current_time == state.end_time
    assert [item.status for item in state.activities.values() if short_name(item.name) == "季度销售数据整理"] == [
        "cancelled", "cancelled", "truncated"]
    counts = days.count_lines(engine.observations._layer_context(state.agents[1], []), "2026-01-05", "2026-01-05")
    assert counts == {"饮食": ("柠檬茶 2 次", "扑克牌 1 次"),
                      "工作": ("季度销售数据整理 2 次", "步行前往公司 1 次"),
                      "生活": ("测试休息 3 次", "购买平台笔记本 1 次", "测试闲聊 1 次")}
    assert days.count_lines(engine.observations._layer_context(state.agents[1], []), "2026-01-06", "2026-01-31") == {
        "饮食": (), "工作": (), "生活": ()}

@pytest.mark.parametrize("stop", ["2026-01-06T00:00:00", "2026-01-06T08:00:00"], ids=["midnight", "daytime"])
def test_run_end_generates_no_digest(engine_factory, monkeypatch, stop):
    """运行到终点时回顾任务在构造客户端之前返回：终点恰在零点时那个零点不生成，终点在白天时最后一天和缺的前一天都不生成。"""
    def mutate(persona, config):
        _late(persona, config)
        config["stop_time"] = datetime.fromisoformat(f"{stop}+08:00")

    engine = engine_factory(mutate=mutate)
    _night(engine)
    if engine.midnight_rhythm_pending:
        _finish_midnight(engine)
        _live(engine)
    assert engine.context.state.current_time == engine.context.state.end_time
    built = _Built(monkeypatch)
    assert drive(settle_pending_memory_digests(engine)) == []
    assert built.clients == [] and _digest_events(engine) == []


def _rest_days(*rest):
    """运行配置：2026-01-05（星期一）08:00 到 01-11 08:00，rest 里的日期改为休息日。"""
    def mutate(persona, config):
        _stop("2026-01-11T08:00:00")(persona, config)
        for day in rest:
            persona["work_schedule"].setdefault("exceptions", {})[day] = []
    return mutate


def _commuting(engine, label) -> None:
    """当班日在家没事做时去公司一次（住处与班次地点之间的出行归工作），其余照 settle_decision。"""
    state = engine.context.state
    today = local_date(state.current_time, ZONE)
    schedule = engine.context.persona.work_schedule
    busy = [item for item in state.activities.values()
            if item.status in {"active", "paused"} or item.concluded_at is None]
    went = any(item.kind == ActivityKind.TRAVEL and local_date(item.started_at, ZONE) == today
               for item in state.activities.values())
    at_home = state.agents[1].location_id == engine.context.inputs.home_location_id
    if not busy and not went and at_home and shift_on(schedule, date.fromisoformat(today)) is not None:
        act(engine, label, travel("公司"))
    else:
        settle_decision(engine, label)


@pytest.mark.parametrize("rest,expected", [
    ((), ("2026-01-07", "2026-01-08", "2026-01-09")),
    (("2026-01-07", "2026-01-08", "2026-01-09"),
     ("2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09")),
], ids=["recent-work-days", "work-before-rest-days"])
def test_resume_fills_only_missing_recent_days(engine_factory, rest, expected):
    """五天里每天的零点只跑零点计划与节律，第六天零点再跑回顾：日历上的 T−1…T−3 与最近三个工作类有条目的日子都生成，
    更早的日子不生成，按日期先后；T−1…T−3 是休息日时，更早的两个通勤日也生成。"""
    engine = engine_factory(mutate=_rest_days(*rest))
    stage_day_plan(engine, submitted=True)
    while True:
        _live(engine, decide=_commuting)
        if local_date(engine.context.state.current_time, ZONE) == "2026-01-10":
            break
        _finish_midnight(engine)
    assert [item["period"] for item in _digests(engine)] == list(expected)
    assert [row.data["period"] for row in _digest_events(engine)] == list(expected)


def _checkpoint(engine, tmp_path: Path, name: str):
    """引擎此刻的共同检查点（人物工作区是一份当前合同的工作区）。"""
    workspace = write_agent_workspace(tmp_path / f"agent-{name}")
    return engine.checkpoint({"agent:1": snapshot_directory(workspace, engine.context.store.assets)})


async def test_checkpoint_after_registration_builds_no_entries(engine_factory, monkeypatch, tmp_path):
    """登记之后连续两次 LifeEnv.checkpoint：不再读任何往日的条目，除两次检查点自己以外不写事件。"""
    from agentsociety2.society import SocietyCheckpointRequest
    from custom.envs.life_env import LifeEnv

    engine = engine_factory(mutate=_late)
    _night(engine)
    settle_midnight_tasks(engine)
    assert len(_digest_events(engine)) == 1
    calls, built = _counted_entries(monkeypatch), _Built(monkeypatch)
    environment = LifeEnv()
    environment.engine = engine
    workspace = write_agent_workspace(tmp_path / "agent")
    moment = datetime.fromtimestamp(engine.context.state.current_time).astimezone()
    request = SocietyCheckpointRequest(run_id=engine.context.state.run_id, round_id="checkpoint",
                                       current_time=moment, agent_workspaces=((1, str(workspace)),))
    before = engine.context.store.last_event_seq
    await environment.checkpoint(request)
    await environment.checkpoint(request)
    added = engine.context.store.iter_events(engine.context.store.last_event_seq, after=before)
    assert [row.event_type for row in added] == ["checkpoint_created", "checkpoint_created"]
    assert calls == [] and built.clients == []


def _branch(engine, checkpoint, tmp_path: Path, name: str) -> LifeEngine:
    """从检查点开出的普通恢复分支。"""
    restore_branch(engine.context.store, checkpoint.checkpoint_id, tmp_path / name).close()
    return LifeEngine.open(engine.context.inputs, tmp_path / name)


def test_branch_inherits_digests_and_regenerates_after_its_checkpoint(engine_factory, tmp_path):
    """父运行过了零点后取检查点 A，再过一个零点：分支继承 A 之前的回顾不重复，A 之后的那个零点在分支里生成一次。"""
    engine = engine_factory(mutate=_late)
    _night(engine)
    settle_midnight_tasks(engine)
    point = _checkpoint(engine, tmp_path, "a")
    _live(engine)
    settle_midnight_tasks(engine)
    assert [row.data["period"] for row in _digest_events(engine)] == ["2026-01-05", "2026-01-06"]
    child = _branch(engine, point, tmp_path, "branch")
    try:
        assert [row.data["period"] for row in _digest_events(child)] == ["2026-01-05"]
        assert _digests(child) == []
        _live(child)
        settle_midnight_tasks(child)
        assert [row.data["period"] for row in _digest_events(child)] == ["2026-01-05", "2026-01-06"]
    finally:
        child.close()


def test_midnight_crash_resume_generates_once(engine_factory, tmp_path):
    """零点回顾传输失败使运行故障；从零点之前的检查点开分支跑过零点：分支库里这一期恰有一条回顾事件。"""
    engine = engine_factory(mutate=_late)
    stage_day_plan(engine, submitted=True)
    act(engine, "night", sleep(480))
    point = _checkpoint(engine, tmp_path, "before-midnight")
    _live(engine)
    with pytest.raises(ConnectionError):
        _digests(engine, StubMemoryDigestClient(raises=ConnectionError("down")))
    engine.fail_run("ConnectionError('down')")
    assert _digest_events(engine) == []
    child = _branch(engine, point, tmp_path, "branch")
    try:
        assert _digest_rows(child, "request") == []
        _live(child)
        settle_midnight_tasks(child)
        assert [row.data["period"] for row in _digest_events(child)] == ["2026-01-05"]
        [first] = _digest_rows(child, "request")
        assert first.data["call_id"] == _digest_rows(engine, "request")[0].data["call_id"]
    finally:
        child.close()


def _digest_caller(monkeypatch, identity: dict, script: list) -> MemoryDigestClient:
    """按冻结身份建立真实 MemoryDigestClient；它取得的是只替换 Router 的真实 LLMClient，按脚本失败或作答。"""
    import agentsociety2.config

    inner, _ = _client(monkeypatch, script, clock=[0.0])
    inner.model_name, inner.base_url = identity["requested_model"], identity["base_url"]
    monkeypatch.setattr(agentsociety2.config, "build_client_for_role", lambda role: inner)
    return MemoryDigestClient(identity)


async def test_digest_ops_records_and_transport_waits(engine_factory, monkeypatch):
    """经真实 MemoryDigestClient：每次回顾调用一条 MEMORY_DIGEST_CALL（文字、级别、耗时），有失败发送的那次另有一条
    级别 warn、带人物的传输等待记录；零点计划的记录码不出现。"""
    engine = engine_factory(mutate=_stop("2026-01-07T08:00:00"))
    _rich_day(engine)
    [inputs] = memory_digest.pending_inputs(engine.context, engine.observations, 1, "day")
    good = memory_digest_response(StubMemoryDigestClient().answer(inputs.payload))
    identity = engine.context.store.manifest["stage_model_identity"]
    caller = _digest_caller(monkeypatch, identity, [_error(APIConnectionError), _bad_food(engine), good])
    assert [item["fallback"] for item in await settle_pending_memory_digests(engine, caller)] == [[]]
    records = _records(engine)
    calls = [row for row in records if row["code"] == "MEMORY_DIGEST_CALL"]
    assert [(row["call_id"], row["outcome"], row["level"]) for row in calls] == [
        (f"memory-digest:1:day:2026-01-05:attempt:{number}", "response", "info") for number in (1, 2)]
    assert [row["detail"] for row in calls] == [
        f"Memory digest call memory-digest:1:day:2026-01-05:attempt:{number} ended with response" for number in (1, 2)]
    assert all(row["agent_id"] == 1 and isinstance(row["call_ms"], float) and row["call_ms"] >= 0 for row in calls)
    [wait] = [row for row in records if row["code"] == "MODEL_TRANSPORT_WAIT"]
    assert (wait["level"], wait["agent_id"], wait["call_id"]) == ("warn", 1, "memory-digest:1:day:2026-01-05:attempt:1")
    assert not [row for row in records if row["code"] == "PLAN_DAY_CALL"]


def test_digest_does_not_change_the_world(engine_factory):
    """登记前后的世界 JSON 除版本外逐字节相同：回顾是整理出来的回忆，不改任何状态。"""
    engine = engine_factory(mutate=_stop("2026-01-07T08:00:00"))
    _rich_day(engine)
    before = engine.context.state.model_dump(mode="json")
    _digests(engine)
    after = engine.context.state.model_dump(mode="json")
    assert after.pop("version") == before.pop("version") + 1
    assert json.dumps(after, ensure_ascii=False, sort_keys=True) == json.dumps(before, ensure_ascii=False, sort_keys=True)
