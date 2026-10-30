"""search_memory 在真实引擎上的取数（docs/design/06-agent.md「检索记忆」）：今天条目取已发布的全文、同秒链里看得到上一次决策、
回顾事件按截止序号换成检索的回顾期、「未整理」那天退回原始条目；只读冻结观察所在序号及以前，不写事件、不改世界，结果不含
内部身份，重新打开运行后逐字节相同。

夹具环境 2026-01-05（周一）08:00 在家开始；跨零点的用例从 23:00 开始。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from types import MappingProxyType

from tests.life.conftest import (
    StubMemoryDigestClient,
    activity_name,
    advance_until_ready,
    conclude_request,
    do_at,
    memory_digest_response,
    query,
    settle_midnight_tasks,
    sleep,
    stage_day_plan,
    submit_round,
    use_app,
)
from tests.life.environment_runtime import act, observe_only, until_ended
from worldmock.simulation_engine.engine import LifeEngine
from worldmock.simulation_engine.evidence import memory_digest
from worldmock.simulation_engine.observation.memory.days import past_day_entries, published_today
from worldmock.simulation_engine.observation.memory.entries import entry_full
from worldmock.simulation_engine.observation.memory.search import memory_sources
from worldmock.state.interaction.requests import ReviseDayPlanRequest, SearchMemoryRequest

#: 八位以上的十六进制串（内部身份的样子）。
_HEX = re.compile(r"[0-9a-f]{8,}")


def _search(category: str, time: str, keyword: str | None = None) -> SearchMemoryRequest:
    """一次检索请求。"""
    return SearchMemoryRequest(category=category, time=time, keyword=keyword)


def _frozen(engine, observed: dict):
    """一次观察对应的已提交观察事件（冻结观察）。"""
    return engine.context.store.read_event(observed["observation_event_id"])


def _observation(engine, label: str):
    """此刻的一次只读观察，返回冻结观察事件。"""
    return _frozen(engine, observe_only(engine, label))


def _decide(engine, label: str, request, tool: str | None = None) -> None:
    """经 submit_round 做一次决策。"""
    from tests.life.conftest import tool_of

    submit_round(engine, {1: (tool or tool_of(request), request)}, label)


def _late(persona, config) -> None:
    """运行配置：2026-01-05 23:00 开始，2026-01-08 08:00 截止。"""
    config["start_time"] = datetime.fromisoformat("2026-01-05T23:00:00+08:00")
    config["stop_time"] = datetime.fromisoformat("2026-01-08T08:00:00+08:00")


def _until_midnight(engine) -> None:
    """逐边界推进到下一个零点（零点节律待判），零点任务一个都不跑。"""
    while not engine.midnight_rhythm_pending:
        state = engine.scheduling_state()
        engine.advance(int(state.next_event_at.timestamp()) - engine.context.state.current_time, state.next_event_at)


def _unsorted_life_day(engine_factory):
    """01-05 23:00 做测试休息并收尾，再睡 480 分钟跨过零点；零点的日回顾里生活类四次都不合格，记为「未整理」；
    推进到人物在 01-06 睡醒、能再决策。"""
    engine = engine_factory(mutate=_late)
    stage_day_plan(engine, submitted=True)
    _, rest = act(engine, "rest", do_at("家", "测试休息"))
    assert advance_until_ready(engine)
    act(engine, "end", conclude_request(activity_name(engine, rest.activity_id)), "conclude_activity")
    act(engine, "night", sleep(480))
    _until_midnight(engine)
    [inputs] = memory_digest.pending_inputs(engine.context, engine.observations, 1, "day")
    arguments = StubMemoryDigestClient().answer(inputs.payload)
    arguments["life"] = {**arguments["life"], "text": "睡了9觉。"}
    client = StubMemoryDigestClient(responses=[memory_digest_response(arguments)] * 4)
    settle_midnight_tasks(engine, digest_client=client)
    assert advance_until_ready(engine)
    return engine


def test_today_search_returns_the_published_full_entries(engine_factory):
    """经 submit_round 依次开始测试休息、收尾时写一条 routine 记忆、查柠檬茶并在它做完后看哔哩哔哩，第五次决策里检索
    「今天」：结果逐字节等于已发布条目的全文、按发布次序倒排，含「记住：」的 routine 记忆与「当时查到」，不含本次决策
    自己的条目。"""
    engine = engine_factory()
    _decide(engine, "rest", do_at("家", "测试休息"))
    assert advance_until_ready(engine)
    name = next(item.name for item in engine.context.state.activities.values())
    routine = {"type": "routine", "text": "午后歇一刻钟。", "importance": 0.5, "keywords": []}
    _decide(engine, "end", conclude_request(name, memories=[[routine]]), "conclude_activity")
    _decide(engine, "ask", query("柠檬茶"))
    asked = next(item for item in engine.context.state.activities.values() if item.kind == "query")
    until_ended(engine, asked.activity_id)
    _decide(engine, "watch", use_app("哔哩哔哩"))
    frozen = _observation(engine, "search")
    answer = engine.search_memory(1, _search("全部", "今天"), frozen)
    published = published_today(engine.context.inputs, engine.context.store, 1, frozen.occurred_at, frozen.event_seq)
    assert len(published.entries) == 4
    assert answer["结果"] == [entry_full(entry) for entry in reversed(published.entries)]
    texts = json.dumps(answer, ensure_ascii=False)
    assert "记住：午后歇一刻钟。" in texts and any("当时查到" in item for item in answer["结果"])
    assert answer["未列出"] == [] and list(answer) == ["类别", "时间", "关键词", "结果", "未列出"]


def test_today_search_in_a_same_second_chain_sees_the_previous_decision(engine_factory):
    """同一秒先修订今日计划、再检索「今天」：检索看到「改了今日计划」那一条。"""
    engine = engine_factory()
    stage_day_plan(engine)
    act(engine, "revise", ReviseDayPlanRequest(goals=[]), "revise_day_plan")
    moment = engine.context.state.current_time
    frozen = _observation(engine, "chain")
    assert frozen.occurred_at == moment
    answer = engine.search_memory(1, _search("全部", "今天"), frozen)
    assert [item["核验"] for item in answer["结果"]] == [["改了今日计划"]]


def _register(engine, tier: str, period: str, digests: dict, fallback: tuple = ()) -> None:
    """直接登记一期回顾（证据是一条请求证据）。"""
    filled = {key: None if text is None else {"text": text, "source_event_ids": [f"source-{key}"],
                                               "cited_event_ids": [f"source-{key}"]}
              for key, text in digests.items()}
    outcome = memory_digest.DigestOutcome(memory_digest.DigestPeriod(tier, period), MappingProxyType(filled),
                                          tuple(fallback))
    evidence = [memory_digest.request_draft(engine.context.state.current_time, 1, (f"{tier}-{period}", 1, None), {})]
    engine.register_memory_digest(1, outcome, evidence)


def test_digest_events_become_search_digests_up_to_the_cutoff(engine_factory):
    """登记日、月、年三期回顾（日回顾的生活类在 fallback）：memory_sources 按事件序号把它们换成检索的回顾期，类别换成
    中文，「未整理」的类别照 fallback；截止序号之前登记的才出现。"""
    engine = engine_factory()
    before = _observation(engine, "before")
    _register(engine, "day", "2026-01-04", {"food": "吃了面。", "work": None, "life": None}, ("life",))
    _register(engine, "month", "2025-12", {"food": None, "work": "月底加了班。", "life": None})
    _register(engine, "year", "2025", {"food": None, "work": None, "life": "这一年搬了家。"})
    after = _observation(engine, "after")
    assert memory_sources(engine.context, 1, before).digests == ()
    digests = memory_sources(engine.context, 1, after).digests
    assert [(item.tier, item.period, item.texts, item.unsorted) for item in digests] == [
        ("day", "2026-01-04", {"饮食": "吃了面。"}, frozenset({"生活"})),
        ("month", "2025-12", {"工作": "月底加了班。"}, frozenset()),
        ("year", "2025", {"生活": "这一年搬了家。"}, frozenset())]


def test_unsorted_day_falls_back_to_that_days_raw_entries(engine_factory):
    """第一天两次决策加一段跨零点的睡眠，零点时生活类重试用尽：第二天检索第一天生活类得到「未整理」与那天这一类的原始
    条目，零点收尾条目（24:00）在最前，其余按时刻从晚到早，同一时刻的按条目次序倒排。"""
    engine = _unsorted_life_day(engine_factory)
    frozen = _observation(engine, "morning")
    answer = engine.search_memory(1, _search("生活", "2026-01-05"), frozen)
    raw = past_day_entries(engine.context.inputs, engine.context.store, 1, "2026-01-05", frozen.occurred_at)
    sliced = [part for entry in raw if (part := entry_full(entry, "生活")) is not None]
    assert raw[-1].clock == "24:00" and len(sliced) == len(raw) == 4
    assert [item["时刻"] for item in sliced] == ["23:00", "23:15", "23:15", "24:00"]
    assert answer["结果"] == [{"日期": "2026-01-05", "类别": "生活", "未整理": True, "条目": sliced[::-1]}]
    assert answer["结果"][0]["条目"][0]["时刻"] == "24:00" and answer["未列出"] == []


def test_search_reads_nothing_after_the_frozen_observation(engine_factory):
    """以较早的观察调用检索：之后提交的条目与之后登记的回顾都不出现，回答与那时逐字节相同。"""
    engine = engine_factory()
    observed, rest = act(engine, "rest", do_at("家", "测试休息"))
    frozen = _frozen(engine, observed)
    requests = [_search("全部", "今天"), _search("全部", "全部", "测试"), _search("生活", "2026-01-04~2026-01-05")]
    first = [json.dumps(engine.search_memory(1, request, frozen), ensure_ascii=False) for request in requests]
    assert advance_until_ready(engine)
    act(engine, "end", conclude_request(activity_name(engine, rest.activity_id)), "conclude_activity")
    _register(engine, "day", "2026-01-04", {"food": None, "work": None, "life": "测试了一整天。"})
    later = _observation(engine, "later")
    assert engine.search_memory(1, _search("全部", "全部", "测试"), later)["结果"] != {}
    assert [json.dumps(engine.search_memory(1, request, frozen), ensure_ascii=False) for request in requests] == first


def test_search_does_not_write_or_change_the_world(engine_factory):
    """几种检索前后，事件序号与世界状态的 JSON 都不变。"""
    engine = _unsorted_life_day(engine_factory)
    frozen = _observation(engine, "morning")
    sequence, state = engine.context.store.last_event_seq, engine.context.state.model_dump_json()
    for request in (_search("全部", "今天"), _search("生活", "2026-01-05"), _search("全部", "全部", "测试"),
                    _search("全部", "2026-01"), _search("饮食", "2026-01-01~2026-01-05")):
        engine.search_memory(1, request, frozen)
    assert engine.context.store.last_event_seq == sequence
    assert engine.context.state.model_dump_json() == state


def test_search_results_carry_no_internal_identity(engine_factory):
    """检索结果里没有事件身份、决策身份、回顾的来源身份，也没有八位以上的十六进制串。"""
    engine = _unsorted_life_day(engine_factory)
    act(engine, "woke", conclude_request(next(item.name for item in engine.context.state.activities.values()
                                              if item.kind == "sleep")), "conclude_activity")
    frozen = _observation(engine, "morning")
    store = engine.context.store
    events = list(store.iter_events(store.last_event_seq))
    private = {event.event_id for event in events} | {event.decision_id for event in events if event.decision_id}
    private |= {source for event in events if event.data.get("kind") == "memory_digest_generated"
                for item in event.data["digests"].values() if item for source in item["source_event_ids"]}
    for request in (_search("全部", "今天"), _search("全部", "2026-01-05"), _search("全部", "全部", "测试"),
                    _search("全部", "2026-01")):
        text = json.dumps(engine.search_memory(1, request, frozen), ensure_ascii=False)
        assert not [identity for identity in private if identity in text]
        assert _HEX.search(text) is None


def test_search_is_byte_identical_after_reopening_the_run(engine_factory):
    """关闭并重新打开运行（新的存储对象，缓存为空）之后，同一冻结观察上的检索逐字节相同。"""
    engine = _unsorted_life_day(engine_factory)
    frozen = _observation(engine, "morning")
    requests = (_search("全部", "今天"), _search("生活", "2026-01-05"), _search("全部", "全部", "测试"))
    first = [json.dumps(engine.search_memory(1, request, frozen), ensure_ascii=False) for request in requests]
    inputs, run_dir = engine.context.inputs, engine.context.store.run_dir
    engine.close()
    reopened = LifeEngine.open(inputs, run_dir)
    try:
        again = reopened.context.store.read_event(frozen.event_id)
        assert [json.dumps(reopened.search_memory(1, request, again), ensure_ascii=False)
                for request in requests] == first
    finally:
        reopened.close()
