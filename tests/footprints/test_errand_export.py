"""认证导出的质量报告统计个人事务与收尾记忆，并能从同一批事件逐值重算（docs/VERIFICATION.md#errands-workday-度量与下游-质量报告 、6.4）。

经真实请求、引擎、SQLite、共同检查点与认证导出：两人场景的副本把运行延到 1 月 6 日 01:00（跨过本地零点）。陈曦 22:51
收尾时记下三条事务（一条带 22:55 的提醒钟点），远程下单的纯牛奶在途；22:55 提醒带来的那次决策里她带三条收尾记忆结束
等待，随即开始喝手边的纯牛奶；在途订单送达的那次提交办成第一条，过了零点她取消第三条，第二条到运行终点仍未完成。

另有单人场景的三段短生活，报告与指标直接由 SQLite 里已提交的事件算出：改过提醒钟点、第二次提醒之后才行动；提醒之后
开始制作事务要取得的菜；只记下事务、不写记忆的收尾。
"""

from __future__ import annotations

import json

from tests.engine_fixture import (engine_factory)  # noqa: F401
from tests.life.conftest import (activity_name, conclude_request, errand_add, errand_cancel, errand_update, report_request, wake_by_mail)  # noqa: F401
from tests.life.routing_fixtures import (HOME, _keep_busy, act, observe, open_world, place, routed, run_until, stage,
                                         with_colleague)
from tests.life.test_errand_completion import (DRINK, KIT, KIT_NAME, MEAL, MEAL_NAME, MILK, MILK_ID, WAIT, buy,
                                               errand_events, home, note)
from tests.life.test_errand_reminder import TEA, evening, moment, until
from worldmock.evaluation.errand_metrics import conclusion_memories, errand_metrics
from worldmock.footprints.export import export_run
from worldmock.footprints.models import Event
from worldmock.footprints.quality import build_quality_report
from worldmock.footprints.validation import validate_export
from worldmock.life.activity_control import PHASE_ACTIVE, pending_conclusions
from worldmock.life.errand_state import CANCELLED_KIND, COMPLETED_KIND, REGISTERED_KIND

#: 22:55 收尾写下的三条收尾记忆：一条作息（routine），一条含第二条事务的商品名，一条含第三条事务的名称。
_MEMORIES = [
    {"type": "routine", "text": "睡前总要先喝点热的再洗漱。", "importance": 0.3, "keywords": ["睡前"]},
    {"type": "reflection", "text": "冰箱里的素炒什锦食材包已经吃完了。", "importance": 0.5, "keywords": ["冰箱"]},
    {"type": "goal", "text": "明天下班记得买冰红茶。", "importance": 0.4, "keywords": ["下班"]},
]
#: 当日目标报告每个代码摘要下的四个键（个人事务不改变它）。
_DAY_PLAN_KEYS = {"habitual_consistency", "external_constraints", "deliberate_goals", "gates"}


def _until_one_am(document: dict) -> None:
    """两人场景副本：加同事联系人王磊（routing_fixtures 的缺省改写），运行延到 1 月 6 日 01:00 结束。"""
    with_colleague(document)
    document["end_at"] = "2026-01-06T01:00:00+08:00"


def _late_evening(engine_factory):
    """1 月 5 日 22:50：两人都在家、此刻都可以决策，陈曦手边有一份纯牛奶（集市营业到 23:00，还能远程下单）。"""
    engine = open_world(engine_factory, mutate=_until_one_am)
    now = moment(engine, "2026-01-05 22:50")

    def change(state):
        """推到 22:50，两人此刻都可以决策。"""
        state.current_time = now
        state.pending_events = [event for event in state.pending_events if event.due_at > now]
        for agent in state.agents.values():
            agent.body_settled_at = agent.next_decision_at = now
    stage(engine, change)
    place(engine, 1, HOME, {MILK_ID: 1.0})
    return engine


def _cancel_after_midnight(engine) -> None:
    """过了零点，以一封邮件给陈曦一次决策机会，她在点名手头活动的控制请求里取消第三条事务。"""
    run_until(engine, lambda current: current.context.state.current_time > moment(current, "2026-01-06 00:00"))
    wake_by_mail(engine, 1, offset=60)
    run_until(engine, lambda current: 1 in current.scheduling_state().ready_agent_ids)
    tool, request = _keep_busy(engine, 1, "cancel")
    build = {"report_status": report_request, "conclude_activity": conclude_request}[tool]
    names = [item.activity_name for item in request.items]
    cancelled = act(engine, "cancel", build(*names, errands=[errand_cancel("买冰红茶", "家里还有冰红茶")]), tool=tool)
    assert cancelled.status == "completed", (cancelled.error_code, cancelled.message)


def _overnight_export(engine_factory, tmp_path):
    """跑完记下、提醒、收尾记忆、送达、跨零点取消与运行终点，创建共同检查点并导出：返回（导出目录，引擎）。"""
    engine = _late_evening(engine_factory)
    note(engine, "note", [errand_add("买牛奶", MILK, remind_time="22:55"), errand_add("补食材包", KIT_NAME),
                          errand_add("买冰红茶", TEA)])
    [order] = buy(engine, "order", way="远程下单").data["orders"]
    assert order["status"] == "pending"
    waited = act(engine, "wait", routed(WAIT))
    until(engine, moment(engine, "2026-01-05 22:55"))
    concluded = act(engine, "remind", conclude_request(activity_name(engine, waited.activity_id), summary="不等了。",
                                                       memories=[_MEMORIES]), tool="conclude_activity")
    assert concluded.status == "completed", (concluded.error_code, concluded.message)
    assert act(engine, "drink", routed(DRINK)).status == "in_progress"
    run_until(engine, lambda current: current.context.state.deliveries[order["order_id"]].status == "delivered")
    _cancel_after_midnight(engine)
    run_until(engine, lambda current: current.context.state.current_time == current.context.state.end_time)
    checkpoint = engine.checkpoint({f"agent:{agent_id}": engine.context.store.assets.put_json(
        {"agent_id": agent_id}, {"kind": "errand_export_test"}) for agent_id in engine.context.state.agents})
    export_dir = tmp_path / "errand-export"
    assert export_run(engine.context.store, checkpoint.last_event_seq, export_dir)["experiment_completed"] is True
    return export_dir, engine


def _lives(events: list[dict]) -> dict[str, tuple[dict, dict | None]]:
    """导出里每条事务的（记下事件，终态事件或 None），按事务名。"""
    lives = {}
    for event in events:
        kind = event["data"].get("kind") if event["event_type"] == "environment_event" else None
        if kind == REGISTERED_KIND:
            lives[event["data"]["errand"]["name"]] = (event, None)
        elif kind in {COMPLETED_KIND, CANCELLED_KIND}:
            name = event["data"]["errand"]["name"]
            lives[name] = (lives[name][0], event)
    return lives


def test_an_exported_run_reports_errands_and_recertifies_value_for_value(
        engine_factory, tmp_path) -> None:  # noqa: F811
    """导出的 quality.json 结构为 4，errand_metrics 与 conclusion_memories 紧随 day_plan_metrics，计数与事件逐条相符：
    记下 3 条（分母），办成 1、取消 1、截止时未完成 1；跨夜 2（零点后取消的与到终点仍未完成的；22:55 之后、零点之前
    送达办成的那条不算）；未完成期间陈曦的每次观察待办层都列着该事务（同期观察数与可见次数相等）；提醒 1 条，提醒后
    同一本地日、办成之前她开始喝同一商品，算作提醒后行动；每条办成事件都有可复核的证据。收尾记忆 3 条：作息 1 条，含
    未完成事务名称或商品名称的 2 条。当日目标报告的键集不变。22:55 那次提醒的唤醒原因 errand_reminder_due 在共同身体
    结算与 decision_started 两处都计入已知原因（各一次），两个未识别原因的分桶为空。认证导出（validate_export）从同一批
    事件重算出逐字节相同的报告，两个分组再由导出的事件逐值重算也相同。"""
    export_dir, engine = _overnight_export(engine_factory, tmp_path)
    manifest, events, activities = validate_export(export_dir)
    quality = json.loads((export_dir / "quality.json").read_text(encoding="utf-8"))
    timezone = engine.context.scenario.timezone

    lives = _lives(events)
    assert {name: life[1]["data"]["kind"] if life[1] else None for name, life in lives.items()} == {
        "买牛奶": COMPLETED_KIND, "补食材包": None, "买冰红茶": CANCELLED_KIND}
    delivered = lives["买牛奶"][1]["occurred_at"]
    assert moment(engine, "2026-01-05 22:55") < delivered < moment(engine, "2026-01-06 00:00")
    observations = [event["event_seq"] for event in events if event["event_type"] == "observation"
                    and event["agent_id"] == 1]
    during = sum(registered["event_seq"] < sequence < (terminal or {"event_seq": float("inf")})["event_seq"]
                 for registered, terminal in lives.values() for sequence in observations)
    assert during > 0
    assert quality["errand_metrics"] == {
        "registered_count": 3, "completed_count": 1, "cancelled_count": 1, "open_at_cutoff_count": 1,
        "carried_overnight_count": 2, "visible_observation_count": during, "expected_observation_count": during,
        "reminded_errand_count": 1, "acted_after_reminder_count": 1, "completed_without_evidence_count": 0}
    assert quality["conclusion_memories"] == {"total_count": 3, "routine_count": 1, "errand_overlap_count": 2}
    assert quality["schema_version"] == 4
    assert quality["day_plan_metrics"] and all(set(group) == _DAY_PLAN_KEYS
                                               for group in quality["day_plan_metrics"].values())
    assert "errand" not in json.dumps(quality["day_plan_metrics"])
    rhythm = quality["decision_rhythm"]
    assert rhythm["wake_reason_counts"]["errand_reminder_due"] == 1
    assert rhythm["decision_started_wake_reason_counts"]["errand_reminder_due"] == 1
    assert rhythm["unrecognized_wake_reason_counts"] == rhythm["unrecognized_decision_started_wake_reason_counts"] == {}
    rebuilt = build_quality_report([Event.model_validate(row) for row in events], activities,
                                   manifest["frozen_run_manifest"])
    assert rebuilt == quality
    keys = list(rebuilt)
    assert keys[keys.index("day_plan_metrics"):keys.index("day_plan_metrics") + 3] == [
        "day_plan_metrics", "errand_metrics", "conclusion_memories"]
    assert errand_metrics(events, timezone) == quality["errand_metrics"]
    assert conclusion_memories(events) == quality["conclusion_memories"]


def _stored_events(engine) -> list:
    """运行的 SQLite 里已提交的全部事件（按事件序号）。"""
    return engine.context.store.read_events(engine.context.store.last_event_seq)


def _stored_metrics(engine) -> dict:
    """由已提交的全部事件（与导出 events.jsonl 同形的事件字典）直接算出的 errand_metrics。"""
    return errand_metrics([event.model_dump(mode="json") for event in _stored_events(engine)],
                          engine.context.scenario.timezone)


def _end_what_is_running(engine, label: str) -> None:
    """等到人物 1 可以决策且没有待收尾的活动，结束手头仍在进行的活动（决策不耗模拟时间，同一秒随后还能开始别的）。"""
    run_until(engine, lambda current: 1 in current.scheduling_state().ready_agent_ids
              and not pending_conclusions(current.context.state, 1))
    running = [row["name"] for row in observe(engine, f"{label}-peek")["activity_bindings"]
               if row["phase"] == PHASE_ACTIVE]
    if running:
        ended = act(engine, label, conclude_request(*running, summary="先到这儿。"), tool="conclude_activity")
        assert ended.status == "completed", (ended.error_code, ended.message)


def test_acting_after_a_re_armed_reminder_is_counted_from_the_stored_events(engine_factory) -> None:  # noqa: F811
    """更新时写下新钟点的事务再提醒一次，提醒后行动按任何一次提醒计（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-提醒时刻 、6.1）。真实
    请求、引擎与 SQLite：1 月 5 日 23:41 记下「吃掉纯牛奶 1 份」、提醒 23:50；23:50 被提醒的那次播报把钟点改成 00:10
    （次日），当天没有任何指向纯牛奶的请求；00:10 再被提醒，两条提醒事件分在两个本地日，此时由已提交事件算出的是被
    提醒 1 条、提醒后行动 0 条。她随即结束等待、开始喝手边的奶，受理于第二次提醒的本地日：提醒后行动变为 1 条。"""
    engine = evening(engine_factory, "23:40")
    place(engine, 1, HOME, {MILK_ID: 1.0})
    note(engine, "note", [errand_add("睡前喝奶", MILK, outcome="吃掉", remind_time="23:50")])
    waited = act(engine, "wait", routed(WAIT))
    first, second = moment(engine, "2026-01-05 23:50"), moment(engine, "2026-01-06 00:10")
    until(engine, first)
    updated = act(engine, "update", report_request(activity_name(engine, waited.activity_id), summary="过会儿再喝。",
                                                   errands=[errand_update("睡前喝奶", remind_time="00:10")]),
                  tool="report_status")
    assert updated.status == "completed", (updated.error_code, updated.message)
    until(engine, second)
    assert [event.occurred_at for event in errand_events(engine, kinds=("errand_reminded",))] == [first, second]
    before = _stored_metrics(engine)
    assert (before["reminded_errand_count"], before["acted_after_reminder_count"]) == (1, 0)
    _end_what_is_running(engine, "end")
    drinking = act(engine, "drink", routed(DRINK))
    assert drinking.status == "in_progress", (drinking.error_code, drinking.message)
    assert engine.context.state.current_time == second
    after = _stored_metrics(engine)
    assert (after["reminded_errand_count"], after["acted_after_reminder_count"]) == (1, 1)
    assert (after["registered_count"], after["open_at_cutoff_count"]) == (1, 1)


def test_starting_to_make_the_dish_after_its_reminder_is_counted_from_the_stored_events(
        engine_factory) -> None:  # noqa: F811
    """被受理的制作请求指向配方的产出（docs/VERIFICATION.md#errands-workday-度量与下游-质量报告 ；记录引擎设计「质量报告」）。真实请求、引擎与
    SQLite：12:31 记下「取得素炒什锦 1 份」、提醒 12:40，家里只有一包食材包；12:40 被提醒时还没有行动（被提醒 1 条、
    提醒后行动 0 条）。她随即结束等待、开始用食材包做菜：受理事件的活动快照带着食材包到素炒什锦的库存转换配方，由已
    提交事件算出的提醒后行动变为 1 条；菜还没做完，事务仍未完成。"""
    engine = home(engine_factory, {KIT: 1.0})
    note(engine, "note", [errand_add("做一份菜", MEAL_NAME, remind_time="12:40")])
    act(engine, "wait", routed(WAIT))
    until(engine, moment(engine, "2026-01-05 12:40"))
    assert len(errand_events(engine, kinds=("errand_reminded",))) == 1
    before = _stored_metrics(engine)
    assert (before["reminded_errand_count"], before["acted_after_reminder_count"]) == (1, 0)
    _end_what_is_running(engine, "end")
    making = act(engine, "make", routed((f"持有物：{KIT_NAME}", "consume", "制作", None, None)))
    assert making.status == "in_progress", (making.error_code, making.message)
    [accepted] = [event for event in engine.context.store.read_agent_events("action_accepted", 1)
                  if event.action_id == making.action_id]
    recipe = accepted.data["data"]["activity"]["data"]["inventory_recipe"]
    assert (recipe["input_object_id"], recipe["output_object_id"]) == (KIT, MEAL)
    after = _stored_metrics(engine)
    assert (after["reminded_errand_count"], after["acted_after_reminder_count"]) == (1, 1)
    assert (after["completed_count"], after["open_at_cutoff_count"]) == (0, 1)


def test_a_conclusion_that_only_notes_an_errand_is_no_memory_write_point(engine_factory) -> None:  # noqa: F811
    """个人事务变更不是记忆写入点、不产生 memory_updated（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-记下更新取消 、3.4 第 2 项）。真实请求、
    引擎与 SQLite：人物结束一段等待，收尾里只记下一条事务，没有 memories 与 views。已提交的事件里这条收尾请求带着事务
    变更，没有任何 memory_updated；由这些事件生成的质量报告里记忆核对没有问题（不报 conclusion_without_memory_update），
    没有收尾记忆（conclusion_memories 为 null），事务照常计入 errand_metrics。"""
    engine = home(engine_factory, {})
    note(engine, "note", [errand_add("买牛奶", MILK)])
    events = _stored_events(engine)
    [concluded] = [event for event in events if event.event_type == "action_requested"
                   and event.data["tool_name"] == "conclude_activity"]
    request = concluded.data["request"]
    assert [row["name"] for row in request["errands"]] == ["买牛奶"]
    assert [(item["memories"], item["views"]) for item in request["items"]] == [([], [])]
    assert not [event for event in events if event.event_type == "memory_updated"]
    report = build_quality_report(events, [], engine.context.store.manifest)
    memory = report["memory_references"]
    assert (memory["problem_count"], memory["problems"], memory["memory_update_events"]) == (0, [], 0)
    assert report["conclusion_memories"] is None
    assert (report["errand_metrics"]["registered_count"], report["errand_metrics"]["open_at_cutoff_count"]) == (1, 1)
