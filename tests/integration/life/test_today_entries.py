"""今天条目在真实引擎上的行为：每次决策一条、从下一次决策起可见；窗口、各行的写法、全文、零点收尾条目、只追加与回看。

夹具环境 2026-01-05（周一）08:00 在家开始；期望的条目照夹具运行的实际时刻、名称与金额写成字面值。
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from agentsociety2.agent.base import ModelCallEvidence
from agentsociety2.society import DirectToolCall

from tests.life.conftest import (
    activity_name,
    advance_until_ready,
    conclude_request,
    context,
    do_at,
    errand_add,
    errand_cancel,
    errand_update,
    pause_request,
    purchase,
    query,
    report_request,
    sleep,
    stage_day_plan,
    tool_of,
    travel,
    use_app,
)
from tests.life.day_baseline_runtime import create_complete_day_engine, play_complete_day
from tests.life.environment_inputs import five_episode_environment, meal_environment, catalog_rows, write_catalog_rows
from tests.life.environment_runtime import act, act_group, observe_only, reach, until_ended
from tests.life.observation_record import record_observations
from tests.dayrun_baseline import deterministic_day_runtime
from worldmock.agents.prompt.narration import render_layer_json
from worldmock.foundation.errors import BusinessRuleError
from worldmock.recording_engine.storage.store import FootprintStore
from worldmock.simulation_engine.observation.layers.memory_layer import memory_view
from worldmock.simulation_engine.observation.memory.days import past_day_entries, published_today
from worldmock.simulation_engine.observation.memory.entries import entry_full
from worldmock.state.interaction.requests import RecallTaskSummaryRequest, ReviseDayPlanRequest

ZONE = ZoneInfo("Asia/Shanghai")


def _today(engine):
    """此刻已发布的今天（条目与已发布界）。"""
    return published_today(engine.context.inputs, engine.context.store, 1, engine.context.state.current_time,
                           engine.context.store.last_event_seq)


def _last(engine) -> dict:
    """下一次观察里「今天」的末项。"""
    return observe_only(engine, "next")["memory_layer"]["今天"][-1]


def _lines(engine) -> list[str]:
    """下一次观察里「今天」末项的核验。"""
    return _last(engine).get("核验", [])


def _noted(engine, label, request, note, tool=None):
    """带正文的一次决策：登记请求之前记一对模型请求与响应，响应的正文就是 self_note。"""
    decision = context(engine, label)
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    call = ModelCallEvidence(decision, f"{label}-call", "request", 1, {"messages": []})
    engine.record_model_exchange(call)
    engine.record_model_exchange(replace(call, phase="response",
                                         response={"choices": [{"message": {"role": "assistant", "content": note}}]}))
    engine.submit_action(DirectToolCall(decision, f"{decision.decision_id}:action:0001", tool or tool_of(request),
                                        request))
    return observed, engine.commit_decision_round(label)[0]


def _at(clock: str, day: str = "2026-01-05") -> int:
    """上海时区的本地时刻。"""
    return int(datetime.fromisoformat(f"{day}T{clock}:00+08:00").timestamp())


def _rested(engine):
    """在家做「测试休息」直到它在 08:15 按计划结束，返回活动名。"""
    _, rest = act(engine, "rest", do_at("家", "测试休息"))
    assert rest.status == "in_progress", rest.error_code
    assert advance_until_ready(engine)
    return activity_name(engine, rest.activity_id)


def test_each_decision_publishes_one_entry_from_the_next_decision(engine_factory):
    """三次决策（开始测试休息、收尾、开始哔哩哔哩）：第 k 次决策的观察里「今天」恰有 k−1 项，就是此前各次决策的条目。"""
    engine = engine_factory()
    first, rest = act(engine, "rest", do_at("家", "测试休息"))
    assert advance_until_ready(engine)
    second, _ = act(engine, "end", conclude_request(activity_name(engine, rest.activity_id)), "conclude_activity")
    third, _ = act(engine, "watch", use_app("哔哩哔哩"))
    entries = _today(engine).entries
    assert len(entries) == 3
    for count, observed in enumerate((first, second, third)):
        assert observed["memory_layer"]["今天"] == [entry.shown for entry in entries[:count]]


def test_a_same_second_chain_decision_sees_the_previous_entry(engine_factory):
    """同一秒先收尾、再开始：第二次决策的观察「今天」末项是收尾条目。"""
    engine = engine_factory()
    name = _rested(engine)
    act(engine, "end", conclude_request(name), "conclude_activity")
    observed, _ = act(engine, "watch", use_app("哔哩哔哩"))
    assert observed["memory_layer"]["今天"][-1] == {"时刻": "08:15", "类别": "生活", "核验": ["测试休息 08:00–08:15"],
                                                   "自述": ["测试休息：收尾。"]}


def _queried(engine):
    """开始查询柠檬茶并推进到它做完（查询自己写下 concluded_at，不挡后面的开始）。"""
    _, asked = act(engine, "ask", query("柠檬茶"))
    assert asked.status == "in_progress", asked.error_code
    until_ended(engine, asked.activity_id)
    return asked


def test_window_holds_clock_settled_facts_and_the_own_commit(engine_factory):
    """查询在时钟推进里做完，做完那一刻开始哔哩哔哩：这次决策的条目先写查完行，再写自己的开始行。"""
    engine = engine_factory()
    _queried(engine)
    act(engine, "watch", use_app("哔哩哔哩"))
    assert _lines(engine) == ["查完饿了么·柠檬茶：1项可买，12–12元", "开始哔哩哔哩"]


def _late(persona, config):
    """运行配置：2026-01-05 23:00 开始，次日 03:00 结束。"""
    config["start_time"] = datetime.fromisoformat("2026-01-05T23:00:00+08:00")
    config["stop_time"] = datetime.fromisoformat("2026-01-06T03:00:00+08:00")


def test_published_bound_is_the_previous_commit_or_the_midnight_bound(engine_factory):
    """第二次决策观察时已发布界等于第一次决策的提交末序号；跨过零点后的第一次观察等于次日零点之前最后一个事件的序号；
    取今天条目的函数与记忆层门面给出同一个界。"""
    engine = engine_factory(mutate=_late)
    stage_day_plan(engine, submitted=True)
    act(engine, "rest", do_at("家", "测试休息"))
    first = engine.context.store.commit_last_seq(max(event.event_seq for event in engine.context.store.iter_events(
        engine.context.store.last_event_seq) if event.event_type == "action_result"))
    assert advance_until_ready(engine)
    assert _today(engine).published == first
    act(engine, "end", conclude_request(activity_name(engine, next(iter(engine.context.state.activities)))),
        "conclude_activity")
    _, nap = act(engine, "nap", sleep(60))
    assert nap.status == "in_progress", nap.error_code
    until_ended(engine, nap.activity_id)
    assert engine.context.state.current_time > _at("00:00", "2026-01-06")
    bound = engine.context.store.last_event_seq_before(_at("00:00", "2026-01-06"))
    layer = engine.observations._layer_context(engine.context.state.agents[1], [])
    assert _today(engine).published == bound == memory_view(layer, engine.context.store).published


def test_a_world_request_registered_after_the_cutoff_is_not_yet_a_decision(engine_factory):
    """收尾一次之后再登记一条收尾而不结算：以这次观察的时刻与事件序号取今天，不抛错，条目与已发布界与登记之前相同；
    结算以后下一次观察的「今天」末项是这次收尾的条目。"""
    engine = engine_factory()
    _, started = act_group(engine, "pair", do_at("家", "测试休息"), use_app("哔哩哔哩"))
    assert advance_until_ready(engine)
    act(engine, "end", conclude_request(activity_name(engine, started[0].activity_id)), "conclude_activity")
    assert advance_until_ready(engine)
    before = _today(engine)
    decision = context(engine, "late")
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    frozen = engine.context.store.read_event(observed["observation_event_id"])
    watching = activity_name(engine, started[1].activity_id)
    engine.submit_action(DirectToolCall(decision, f"{decision.decision_id}:action:0001", "conclude_activity",
                                        conclude_request(watching)))
    during = published_today(engine.context.inputs, engine.context.store, 1, frozen.occurred_at, frozen.event_seq)
    assert [entry.shown for entry in during.entries] == [entry.shown for entry in before.entries]
    assert during.published == before.published
    engine.commit_decision_round("late")
    assert _last(engine)["自述"] == ["哔哩哔哩：收尾。"]


def _conclude(engine):
    """收尾：测试休息按计划结束后收尾它。"""
    act(engine, "end", conclude_request(_rested(engine)), "conclude_activity")


def _report(engine):
    """只交 report_status：哔哩哔哩用到下一次决策机会时播报。"""
    _, watching = act(engine, "watch", use_app("哔哩哔哩"))
    assert advance_until_ready(engine)
    act(engine, "report", report_request(activity_name(engine, watching.activity_id)), "report_status")


def _report_settled(engine):
    """report_status，窗口里另有结算：测试闲聊与查询柠檬茶同组开始，查询做完时播报测试闲聊。"""
    _, started = act_group(engine, "pair", do_at("家", "测试闲聊"), query("柠檬茶"))
    assert advance_until_ready(engine)
    act(engine, "report", report_request(activity_name(engine, started[0].activity_id)), "report_status")


def _start(engine):
    """只开始一项。"""
    act(engine, "rest", do_at("家", "测试休息"))


def _group(engine):
    """开始组。"""
    act_group(engine, "pair", do_at("家", "测试休息"), use_app("哔哩哔哩"))


def _start_noted(engine):
    """开始并带正文。"""
    _noted(engine, "rest", do_at("家", "测试休息"), "先歇一会儿。")


def _start_settled(engine):
    """开始时窗口里有结算：查询做完那一刻开始测试休息并带正文。"""
    _queried(engine)
    _noted(engine, "rest", do_at("家", "测试休息"), "查完了，先歇一会儿。")


def _purchase(engine):
    """开始购买并带正文。"""
    _noted(engine, "buy", purchase("柠檬茶"), "买杯柠檬茶。")


def _query(engine):
    """开始查询并带正文。"""
    _noted(engine, "ask", query("柠檬茶"), "看看柠檬茶多少钱。")


def _rejected(engine):
    """只有被拒请求：购买目录里没有的盖浇饭并带正文。"""
    _noted(engine, "buy", purchase("盖浇饭"), "想吃盖浇饭。")


def _pause(engine):
    """暂停：哔哩哔哩用到下一次决策机会时暂停。"""
    _, watching = act(engine, "watch", use_app("哔哩哔哩"))
    assert advance_until_ready(engine)
    act(engine, "pause", pause_request(activity_name(engine, watching.activity_id)), "pause_activity")


def _revise(engine):
    """改计划：预置当日计划后交一份新清单。"""
    stage_day_plan(engine)
    act(engine, "revise", ReviseDayPlanRequest(goals=[]), "revise_day_plan")


@pytest.mark.parametrize("run,entry", [
    (_conclude, {"时刻": "08:15", "类别": "生活", "核验": ["测试休息 08:00–08:15"], "自述": ["测试休息：收尾。"]}),
    (_report, {"时刻": "08:30", "类别": "生活", "自述": ["进展·哔哩哔哩：继续。"]}),
    (_report_settled, {"时刻": "08:07", "类别": "生活、饮食", "核验": ["查完饿了么·柠檬茶：1项可买，12–12元"],
                       "自述": ["进展·测试闲聊：继续。"]}),
    (_start, {"时刻": "08:00", "类别": "生活", "核验": ["开始测试休息"]}),
    (_group, {"时刻": "08:00", "类别": "生活", "核验": ["开始测试休息、哔哩哔哩"]}),
    (_start_noted, {"时刻": "08:00", "类别": "生活", "核验": ["开始测试休息"], "自述": ["正文：先歇一会儿。"]}),
    (_start_settled, {"时刻": "08:05", "类别": "生活、饮食", "核验": ["查完饿了么·柠檬茶：1项可买，12–12元", "开始测试休息"],
                      "自述": ["正文：查完了，先歇一会儿。"]}),
    (_purchase, {"时刻": "08:00", "类别": "饮食", "核验": ["开始购买柠檬茶"], "自述": ["正文：买杯柠檬茶。"]}),
    (_query, {"时刻": "08:00", "类别": "饮食", "核验": ["开始查询柠檬茶"], "自述": ["正文：看看柠檬茶多少钱。"]}),
    (_rejected, {"时刻": "08:00", "类别": "饮食", "核验": ["被拒：购买盖浇饭"], "自述": ["正文：想吃盖浇饭。"]}),
    (_pause, {"时刻": "08:30", "类别": "生活", "核验": ["暂停哔哩哔哩"], "自述": ["哔哩哔哩：先停一下。"]}),
    (_revise, {"时刻": "08:00", "类别": "生活", "核验": ["改了今日计划"]}),
], ids=["conclude", "report", "report-with-settlement", "start", "start-group", "start-with-body",
        "start-after-settlement", "start-purchase", "start-query", "rejected", "pause", "revise"])
def test_twelve_decision_kinds_have_pinned_entries(engine_factory, run, entry):
    """各类决策的下一次观察「今天」末项逐字节等于写死的条目。"""
    engine = engine_factory()
    run(engine)
    assert json.dumps(_last(engine), ensure_ascii=False) == json.dumps(entry, ensure_ascii=False)


def _every_five(persona, config):
    """运行配置：每五分钟一次重估，做到一半的事也有决策机会。"""
    config["reconsideration_interval_seconds"] = 300


def _ending_lines(engine) -> list[str]:
    """今天各条目里的结束行短句。"""
    return [line.text for entry in _today(engine).entries for line in entry.lines if line.kind == "ending"]


def _planned(engine):
    """按计划结束：测试休息做满 15 分钟后收尾。"""
    _conclude(engine)


def _cut_short(engine):
    """被收尾提前结束：测试休息做到第一次重估就收尾。"""
    _, rest = act(engine, "rest", do_at("家", "测试休息"))
    assert advance_until_ready(engine)
    act(engine, "end", conclude_request(activity_name(engine, rest.activity_id)), "conclude_activity")


def _app_concluded(engine):
    """app 被收尾：Keep 用到第一次重估就收尾。"""
    _, run = act(engine, "run", use_app("Keep"))
    assert advance_until_ready(engine)
    act(engine, "end", conclude_request(activity_name(engine, run.activity_id)), "conclude_activity")


def _arrived(engine):
    """出行到达：步行去公司，到达后收尾。"""
    _, trip = act(engine, "walk", travel("公司"))
    until_ended(engine, trip.activity_id)
    act(engine, "end", conclude_request(activity_name(engine, trip.activity_id)), "conclude_activity")


def _overnight(engine):
    """隔夜睡眠：23:00 睡两个小时，醒来后收尾。"""
    stage_day_plan(engine, submitted=True)
    _, nap = act(engine, "night", sleep(120))
    until_ended(engine, nap.activity_id)
    act(engine, "end", conclude_request(activity_name(engine, nap.activity_id)), "conclude_activity")


def _resumed(engine):
    """暂停后恢复：哔哩哔哩 08:05 暂停并同秒开始测试闲聊，08:10 播报恢复，08:15 测试闲聊结束时两项一起收尾。"""
    _, watching = act(engine, "watch", use_app("哔哩哔哩"))
    name = activity_name(engine, watching.activity_id)
    assert advance_until_ready(engine)
    act(engine, "pause", pause_request(name), "pause_activity")
    _, chat = act(engine, "chat", do_at("家", "测试闲聊"))
    assert advance_until_ready(engine)
    act(engine, "resume", report_request(name), "report_status")
    until_ended(engine, chat.activity_id)
    act(engine, "end", conclude_request(name, activity_name(engine, chat.activity_id)), "conclude_activity")


@pytest.mark.parametrize("run,mutate,line", [
    (_planned, None, "测试休息 08:00–08:15"),
    (_cut_short, _every_five, "测试休息 08:00–08:05，提前结束"),
    (_app_concluded, _every_five, "Keep 08:00–08:05"),
    (_arrived, None, "步行到公司 08:00–08:31"),
    (_overnight, _late, "睡觉 昨23:00–01:00"),
    (_resumed, _every_five, "哔哩哔哩 08:00–08:15（暂停 08:05–08:10）"),
], ids=["planned", "concluded-early", "app-concluded", "travel-arrival", "overnight-sleep", "paused-and-resumed"])
def test_ending_lines_of_the_fixture_activities(engine_factory, run, mutate, line):
    """各种终局的结束行逐字节等于期望：按计划结束与到达不写终局，app 被收尾不写「结束使用」，跨日写「昨」，暂停写区间。"""
    engine = engine_factory(mutate=mutate)
    run(engine)
    assert [text for text in _ending_lines(engine) if text.startswith(line.split(" ")[0] + " ")] == [line]


def test_title_lists_in_short_and_full_lines(engine_factory, tmp_path):
    """五集副本：08:30 收尾的短句只写第一集再接「等4条」，完整行列四集再接「（《测试剧集第五集》未完）」；放完的短句「…等5条」、
    完整行五集没有「未完」。四集「做晚饭」的短句列前三集再接「等4条」，完整行四集全列。单集与短名同名的测试休息不写标题。"""
    from tests.integration.life.test_autoplay_display import _end, _watch

    titles = [f"《测试剧集第{number}集》" for number in "一二三四五"]
    for how, short, full in (("concluded", "哔哩哔哩 08:00–08:30，《测试剧集第一集》等4条",
                              f"哔哩哔哩 08:00–08:30，{''.join(titles[:4])}（《测试剧集第五集》未完）"),
                             ("finished", "哔哩哔哩 08:00–08:33，《测试剧集第一集》等5条",
                              f"哔哩哔哩 08:00–08:33，{''.join(titles)}")):
        engine, _, activity_id = _watch(engine_factory, tmp_path / how)
        _end(engine, activity_id, how)
        (line,) = [line for entry in _today(engine).entries for line in entry.lines if line.kind == "ending"]
        assert (line.text, line.full) == (short, full)
    root = meal_environment(tmp_path / "meal")
    rows = catalog_rows(root, "content-catalog.jsonl")
    rows.append({"content_id": "dinner-4-trash", "duration_seconds": 600, "exertion": "light",
                 "location_kinds": ["home"], "pushable": False, "relaxation_relief": "none",
                 "series": {"episode": 4, "title": "做晚饭"}, "social_relief": "none", "summary": "倒垃圾的测试用合成摘要。",
                 "summary_source": {"kind": "synthetic", "notice": "测试用合成摘要", "url": None}, "title": "倒垃圾"})
    write_catalog_rows(root, "content-catalog.jsonl", sorted(rows, key=lambda row: row["content_id"]))
    engine = engine_factory(root)
    _, dinner = act(engine, "dinner", do_at("家", "做晚饭"))
    until_ended(engine, dinner.activity_id)
    act(engine, "end", conclude_request(activity_name(engine, dinner.activity_id)), "conclude_activity")
    (line,) = [line for line in _today(engine).entries[-1].lines if line.kind == "ending"]
    assert line.text.endswith("，《备菜做饭》《吃晚饭》《洗碗收拾厨房》等4条")
    assert line.full.endswith("，《备菜做饭》《吃晚饭》《洗碗收拾厨房》《倒垃圾》") and line.bare == line.text.split("，")[0]
    engine = engine_factory()
    _conclude(engine)
    assert _ending_lines(engine) == ["测试休息 08:00–08:15"]


def _bought(engine, label, *goods, prefer=None):
    """开始购买并推进到它做完、收尾它；返回收尾那次决策的条目的核验。"""
    _, bought = act(engine, label, purchase(*goods, prefer=prefer))
    assert bought.status == "in_progress", bought.error_code
    until_ended(engine, bought.activity_id)
    act(engine, f"{label}-end", conclude_request(activity_name(engine, bought.activity_id)), "conclude_activity")
    return _lines(engine)


def _in_store(engine):
    """到店购买：步行去四季便利，在店里买纸巾。"""
    _, trip = act(engine, "walk", travel("四季便利"))
    until_ended(engine, trip.activity_id)
    act(engine, "arrive", conclude_request(activity_name(engine, trip.activity_id)), "conclude_activity")
    return _bought(engine, "tissue", "纸巾")


@pytest.mark.parametrize("run,expected", [
    (lambda engine: _bought(engine, "tea", "柠檬茶", prefer="饿了么"), "买柠檬茶1份，17元，预计08:25送到家"),
    (lambda engine: _bought(engine, "pair", "柠檬茶", "薯片", prefer="饿了么"), "买薯片、柠檬茶各1份，共25元，预计08:25送到家"),
    (lambda engine: _bought(engine, "mixed", "薯片", "薯片", "柠檬茶", prefer="饿了么"),
     "买薯片、薯片、柠檬茶各1份，共33元，预计08:25送到家"),
    (_in_store, "买纸巾1份，6元"),
], ids=["one-delivered-home", "two-each-one", "two-portions-of-one-goods", "in-store"])
def test_purchase_lines(engine_factory, run, expected):
    """购买行逐字节等于期望：订单按结果里的次序，份数都是 1 时写「各1份」（同一商品写两次就是两张一份的订单），到店购买
    没有送达段；不带下单 app，购买活动没有结束行。付款在购买做完那一秒，做完即唤醒人物，所以条目时刻与付款同一分钟。"""
    engine = engine_factory()
    lines = run(engine)
    assert [line for line in lines if "买" in line and not line.startswith("开始")] == [expected]
    assert not [line for line in _ending_lines(engine) if line.startswith("购买")]
    assert "饿了么" not in render_layer_json(observe_only(engine, "check")["memory_layer"])


def _until_delivered(engine, label: str) -> None:
    """推进并照实处理决策机会，直到本人有一次送达事件；送达那一刻的决策不在这里做。"""
    count = 0
    while not [event for event in engine.context.store.read_agent_events("environment_event", 1)
               if event.data.get("kind") == "delivery_arrival"]:
        count += 1
        assert count < 200
        arrived = [event for event in engine.context.store.read_agent_events("environment_event", 1)
                   if event.data.get("kind") == "delivery_arrival"]
        if advance_until_ready(engine, limit=1) and not arrived:
            _settle(engine, f"{label}-{count}")


def _settle(engine, label: str) -> None:
    """一次照实的决策：有待收尾的活动就收尾，否则播报进行中的活动，都没有时在家做一次测试闲聊。"""
    activities = engine.context.state.activities.values()
    pending = [item.name for item in activities
               if item.status not in {"active", "paused"} and item.concluded_at is None]
    active = [item.name for item in activities if item.status == "active"]
    if pending:
        act(engine, label, conclude_request(*pending), "conclude_activity")
    elif active:
        act(engine, label, report_request(*active), "report_status")
    else:
        act(engine, label, do_at("家", "测试闲聊"))


def _six_days(persona, config):
    """运行配置：六天的运行，等得到驿站的快递。"""
    config["stop_time"] = config["start_time"] + timedelta(days=6)


def test_delivery_and_pickup_lines(engine_factory):
    """醒着时外卖送达的那次决策写「送达柠檬茶」（与条目同一分钟，不带时刻前缀）；平台笔记本到驿站写「平台笔记本已到取货点待取」。"""
    engine = engine_factory()
    _bought(engine, "tea", "柠檬茶", prefer="饿了么")
    _until_delivered(engine, "wait")
    _settle(engine, "delivered")
    assert "送达柠檬茶" in _lines(engine)
    engine = engine_factory(mutate=_six_days)
    _bought(engine, "notebook", "平台笔记本")
    (order,) = engine.context.state.deliveries.values()
    reach(engine, order.expected_at)
    assert engine.context.state.deliveries[order.order_id].status == "ready_for_pickup"
    assert advance_until_ready(engine)
    _settle(engine, "arrived")
    (line,) = [line for line in _lines(engine) if "平台笔记本" in line]
    assert line.endswith("平台笔记本已到取货点待取")


def test_query_line_and_its_answers(engine_factory):
    """查柠檬茶做完：下一次决策的条目有查完行，渲染形状里没有店名与答案行；全文的「当时查到」先写查询时刻与 app，再逐行写
    商品与价钱；查询提前结束时写成结束行。"""
    engine = engine_factory()
    asked = _queried(engine)
    act(engine, "watch", use_app("哔哩哔哩"))
    shown = observe_only(engine, "next")["memory_layer"]
    assert "查完饿了么·柠檬茶：1项可买，12–12元" in shown["今天"][-1]["核验"]
    assert "三杯咖啡" not in render_layer_json(shown) and "柠檬茶 12元" not in render_layer_json(shown)
    ended = engine.context.state.activities[asked.activity_id].ended_at
    full = entry_full(_today(engine).entries[-1])
    assert full["当时查到"][0] == f"{datetime.fromtimestamp(ended, ZONE):%H:%M} 在饿了么查柠檬茶"
    assert set(full["当时查到"][1:]) <= {"柠檬茶 12元", "柠檬茶 12元，当时不可买"} and len(full["当时查到"]) > 1
    engine = engine_factory(mutate=_every_five)
    _, asked = act(engine, "ask", query("柠檬茶"))
    assert advance_until_ready(engine)
    act(engine, "stop", conclude_request(activity_name(engine, asked.activity_id)), "conclude_activity")
    assert [line for line in _ending_lines(engine) if line.startswith("查询柠檬茶 ")] == [
        "查询柠檬茶 08:00–08:05，提前结束"]


def test_errand_lines(engine_factory):
    """播报时新增两条个人事务、再更新与取消其中一条，各写一行；买到所需商品后写「办成「…」」。"""
    engine = engine_factory(mutate=_every_five)
    _, watching = act(engine, "watch", use_app("哔哩哔哩"))
    name = activity_name(engine, watching.activity_id)
    steps = [((errand_add("备好饮料", "柠檬茶"), errand_add("买纸巾", "纸巾")),
              ["新增个人事务「备好饮料」", "新增个人事务「买纸巾」"]),
             ((errand_update("买纸巾", memo="要软的"),), ["更新个人事务「买纸巾」"]),
             ((errand_cancel("买纸巾", "家里还有"),), ["取消个人事务「买纸巾」"])]
    for index, (changes, lines) in enumerate(steps):
        assert advance_until_ready(engine)
        act(engine, f"report-{index}", report_request(name, errands=changes), "report_status")
        assert _lines(engine) == lines
    assert advance_until_ready(engine)
    act(engine, "stop", conclude_request(name), "conclude_activity")
    _bought(engine, "tea", "柠檬茶", prefer="饿了么")
    _until_delivered(engine, "wait")
    _settle(engine, "delivered")
    assert "办成「备好饮料」" in _lines(engine)


@pytest.mark.parametrize("requests,line", [
    ((query("盖浇饭"),), "被拒：查询盖浇饭"),
    ((do_at("家", "测试休息"), purchase("盖浇饭")), "被拒：开始测试休息、购买盖浇饭"),
    ((ReviseDayPlanRequest(goals=[]),), "被拒：改今日计划"),
    ((conclude_request("不存在的活动"),), "被拒：收尾不存在的活动"),
], ids=["query-unknown-goods", "start-group", "revise-without-plan", "conclude-unknown-activity"])
def test_rejection_lines(engine_factory, requests, line):
    """被拒的请求写一行「被拒：〈动作对象〉」，不带原因。"""
    engine = engine_factory()
    if len(requests) == 2:
        _, results = act_group(engine, "refused", *requests)
    else:
        _, result = act(engine, "refused", requests[0])
        results = [result]
    assert {result.status for result in results} == {"rejected"}
    assert _lines(engine) == [line]


def test_line_order_puts_settled_facts_first(engine_factory):
    """收尾购买并新增事务、同秒睡下，外卖与办成在睡着时写下；醒来收尾睡眠并再新增一条事务：这次收尾的核验依次是送达、
    办成（按来源事件序号）、睡眠的结束行（结束事件更晚），最后是本次决策自己的事务变更行。"""
    engine = engine_factory()
    stage_day_plan(engine, submitted=True)
    _, bought = act(engine, "buy", purchase("柠檬茶", prefer="饿了么"))
    until_ended(engine, bought.activity_id)
    act(engine, "end", conclude_request(activity_name(engine, bought.activity_id),
                                        errands=(errand_add("备好饮料", "柠檬茶"),)), "conclude_activity")
    (order,) = engine.context.state.deliveries.values()
    assert engine.context.state.current_time < order.expected_at
    _, nap = act(engine, "nap", sleep(60))
    assert nap.status == "in_progress", nap.error_code
    until_ended(engine, nap.activity_id)
    act(engine, "wake", conclude_request(activity_name(engine, nap.activity_id),
                                         errands=(errand_add("买纸巾", "纸巾"),)), "conclude_activity")
    assert _lines(engine) == ["08:25 送达柠檬茶", "办成「备好饮料」", "睡觉 08:02–09:02", "新增个人事务「买纸巾」"]


def test_self_items_follow_prefix_order_and_routine_stays_in_the_full_text(engine_factory):
    """收尾一项带两条看法、一条 commitment、一条 routine 与正文：渲染的自述依次是总结、两条看法、记住、正文，没有 routine；
    全文在「记住：commitment」之后是「记住：routine」；与总结逐字相同的记忆两处都没有。"""
    engine = engine_factory()
    name = _rested(engine)
    memory = lambda kind, text: {"type": kind, "text": text, "importance": 0.5, "keywords": []}
    views = [[{"type": "preference", "about": "纸巾", "text": "喜欢软的。", "importance": 0.5, "keywords": []},
              {"type": "relationship", "about": "老周", "text": "该约他吃饭。", "importance": 0.5, "keywords": []}]]
    memories = [[memory("commitment", "明天早点起。"), memory("routine", "午后歇一刻钟。"), memory("commitment", "歇好了。")]]
    _noted(engine, "end", conclude_request(name, summary="歇好了。", memories=memories, views=views), "下午接着忙。",
           "conclude_activity")
    assert _last(engine)["自述"] == ["测试休息：歇好了。", "看法·纸巾：喜欢软的。", "看法·老周：该约他吃饭。", "记住：明天早点起。",
                                    "正文：下午接着忙。"]
    assert entry_full(_today(engine).entries[-1])["自述"] == [
        "测试休息：歇好了。", "看法·纸巾：喜欢软的。", "看法·老周：该约他吃饭。", "记住：明天早点起。", "记住：午后歇一刻钟。",
        "正文：下午接着忙。"]


def test_category_string_lists_the_main_category_first(engine_factory):
    """查询做完那一刻在家开始测试休息并购买牛肉面：核验先写饮食的查完行，「类别」仍以主请求测试休息的类别打头，后接饮食。"""
    engine = engine_factory()
    _queried(engine)
    _, started = act_group(engine, "pair", do_at("家", "测试休息"), purchase("牛肉面", prefer="饿了么"))
    assert [result.status for result in started] == ["in_progress", "in_progress"]
    entry = _last(engine)
    assert entry["核验"][0].startswith("查完") and entry["类别"] == "生活、饮食"


def _night(persona, config):
    """运行配置：2026-01-05 23:00 开始，次日 23:00 结束。"""
    config["start_time"] = datetime.fromisoformat("2026-01-05T23:00:00+08:00")
    config["stop_time"] = datetime.fromisoformat("2026-01-06T23:00:00+08:00")


def _overnight_order(engine_factory, monkeypatch=None):
    """23:00 下单一份外卖，做完时收尾它、同秒睡下；外卖在睡着时、零点之前送达，睡眠跨过零点。返回引擎、收尾之后
    零点以后的各次观察（monkeypatch 给出时由 record_observations 记下）与睡眠的活动身份。"""
    recorded = record_observations(monkeypatch) if monkeypatch is not None else None
    engine = engine_factory(mutate=_night)
    stage_day_plan(engine, submitted=True)
    _, bought = act(engine, "buy", purchase("柠檬茶", prefer="饿了么"))
    until_ended(engine, bought.activity_id)
    act(engine, "end", conclude_request(activity_name(engine, bought.activity_id)), "conclude_activity")
    _, nap = act(engine, "night", sleep())
    assert nap.status == "in_progress", nap.error_code
    until_ended(engine, nap.activity_id)
    act(engine, "wake", conclude_request(activity_name(engine, nap.activity_id)), "conclude_activity")
    return engine, recorded, nap.activity_id


def test_closing_entry_collects_facts_after_the_last_decision_and_crossing_lines(engine_factory):
    """23:00 下单、睡下之后零点之前送达的外卖与跨过零点的睡眠写进 1 月 5 日的零点收尾条目；1 月 6 日醒来收尾睡眠的那次
    决策写「睡觉 昨…」。"""
    engine, _, nap_id = _overnight_order(engine_factory)
    entries = past_day_entries(engine.context.inputs, engine.context.store, 1, "2026-01-05",
                               engine.context.state.current_time)
    (arrival,) = [event for event in engine.context.store.read_agent_events("environment_event", 1)
                  if event.data.get("kind") == "delivery_arrival"]
    started = engine.context.state.activities[nap_id].started_at
    clock = lambda epoch: f"{datetime.fromtimestamp(epoch, ZONE):%H:%M}"
    assert entry_full(entries[-1]) == {"时刻": "24:00", "类别": "饮食、生活", "核验": [
        f"{clock(arrival.occurred_at)} 送达柠檬茶", f"{clock(started)} 开始睡觉，跨过零点"]}
    assert _lines(engine)[0].startswith(f"睡觉 昨{clock(started)}–")


def test_closing_entry_never_enters_the_prompt(engine_factory, monkeypatch):
    """同上运行：零点后的每次观察都不含「24:00」与「跨过零点」，零点后的第一次观察「今天」为空。"""
    engine, recorded, _ = _overnight_order(engine_factory, monkeypatch)
    midnight = _at("00:00", "2026-01-06")
    after = [built["memory_layer"] for _, built in recorded if built["current_time"] >= midnight]
    assert after and after[0]["今天"] == []
    assert all("24:00" not in render_layer_json(layer) and "跨过零点" not in render_layer_json(layer)
               for layer in after)


def test_entries_are_append_only_within_a_day(tmp_path, monkeypatch):
    """记下活动日的全部观察：同一天里前一次观察的「今天」总是后一次的前缀。"""
    deterministic_day_runtime(monkeypatch)
    recorded = record_observations(monkeypatch)
    engine = create_complete_day_engine(tmp_path / "case", "activity")
    try:
        play_complete_day(engine, "activity")
    finally:
        engine.close()
    days = [(datetime.fromtimestamp(built["current_time"], ZONE).date(), built["memory_layer"]["今天"])
            for _, built in recorded]
    assert len(days) > 10
    for (day, earlier), (later_day, later) in zip(days, days[1:]):
        if day == later_day:
            assert later[:len(earlier)] == earlier


@pytest.mark.parametrize("kind", ["activity", "callback"])
def test_a_recorded_day_has_one_entry_per_decision_within_120_characters(tmp_path, monkeypatch, kind):
    """跑完一份完整日：那天的决策条目数等于那天的决策数，每条渲染形状的核验与自述合计至多 120 字，且都有核验或自述。"""
    deterministic_day_runtime(monkeypatch)
    engine = create_complete_day_engine(tmp_path / "case", kind)
    try:
        play_complete_day(engine, kind)
        entries = past_day_entries(engine.context.inputs, engine.context.store, 1, "2026-01-05",
                                   engine.context.state.current_time)
        requests = engine.context.store.read_agent_events("action_requested", 1)
    finally:
        engine.close()
    decisions = {event.decision_id for event in requests
                 if datetime.fromtimestamp(event.occurred_at, ZONE).date().isoformat() == "2026-01-05"}
    shown = [entry.shown for entry in entries if entry.decision_id is not None]
    assert len(shown) == len(decisions) > 3
    for item in shown:
        assert "核验" in item or "自述" in item
        assert sum(len(text) for key in ("核验", "自述") for text in item.get(key, ())) <= 120


def test_a_fresh_store_rebuilds_byte_identical_entries(engine_factory):
    """同一 footprints.sqlite 新开一个存储对象（缓存为空）：今天的条目逐项渲染形状与全文相同，已发布界相同。"""
    engine = engine_factory()
    _queried(engine)
    _, started = act_group(engine, "pair", do_at("家", "测试休息"), purchase("牛肉面", prefer="饿了么"))
    until_ended(engine, started[1].activity_id)
    act(engine, "end", conclude_request(activity_name(engine, started[1].activity_id)), "conclude_activity")
    original = _today(engine)
    fresh = FootprintStore(engine.context.store.run_dir)
    try:
        rebuilt = published_today(engine.context.inputs, fresh, 1, engine.context.state.current_time,
                                  fresh.last_event_seq)
    finally:
        fresh.close()
    assert [entry.shown for entry in rebuilt.entries] == [entry.shown for entry in original.entries]
    assert [entry_full(entry) for entry in rebuilt.entries] == [entry_full(entry) for entry in original.entries]
    assert rebuilt.published == original.published and len(original.entries) == 3


def _recall(engine, name):
    """以此刻的观察回看这项活动。"""
    observed = engine.observe(context(engine, f"recall-{engine.context.state.current_time}"))
    return engine.read_task_summary(1, RecallTaskSummaryRequest(task_name=name), observed)["recall"]


def test_recall_dedup_only_counts_untruncated_text(engine_factory):
    """播报一句 30 字的进展后回看：结果只写「哔哩哔哩」与「已在记忆层」；播报一句 200 字的进展（渲染被截断）后回看得到
    完整的自述。"""
    engine = engine_factory(mutate=_every_five)
    _, watching = act(engine, "watch", use_app("哔哩哔哩"))
    name = activity_name(engine, watching.activity_id)
    short, long = "看" * 30, "看" * 200
    assert advance_until_ready(engine)
    act(engine, "short", report_request(name, summary=short), "report_status")
    recalled = _recall(engine, name)
    assert (recalled["activity"], recalled["progress_note"]) == ("哔哩哔哩", "已在记忆层")
    assert advance_until_ready(engine)
    act(engine, "long", report_request(name, summary=long), "report_status")
    assert _last(engine)["自述"][0].endswith("…")
    assert _recall(engine, name)["progress_note"] == long


def test_a_short_label_maps_to_every_same_named_activity_of_the_day(engine_factory):
    """一天里两次收尾不同的测试休息：两项活动绑定的 note_labels 都含「测试休息」，按「测试休息」回看是歧义，TASK_NOT_FOUND。"""
    engine = engine_factory()
    first = _rested(engine)
    act(engine, "end-1", conclude_request(first), "conclude_activity")
    _, second = act(engine, "rest-2", do_at("家", "测试休息"))
    assert advance_until_ready(engine)
    act(engine, "end-2", conclude_request(activity_name(engine, second.activity_id)), "conclude_activity")
    observed = observe_only(engine, "labels")
    rests = [row for row in observed["activity_bindings"] if "测试休息（" in row["name"]]
    assert len(rests) == 2 and all("测试休息" in row["note_labels"] for row in rests)
    with pytest.raises(BusinessRuleError, match="TASK_NOT_FOUND"):
        engine.read_task_summary(1, RecallTaskSummaryRequest(task_name="测试休息"), observed)
