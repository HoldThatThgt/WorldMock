"""买菜做饭：凭状态的地点内容只在本人这个回调状态不小于 1 时列出和开始，开始的那次提交里把状态减 1 并写一条
consume_on_start，起因是这次开始调用的受理结果；做完时照完成效果结算。

都经真实请求、引擎与 SQLite：样例环境副本 grocery_environment 登记状态 groceries，家多一件凭它、做完供一餐的「做饭」
（docs/design/04-simulation-engine.md「出行、内容与查询」「回调事务」，05-recording-engine.md「事件与来源」）。
副本里「测试休息」改成三小时，等外卖送到时人物就在做它。
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from tests.life.conftest import (
    action_identity, advance_next, advance_until_ready, conclude_request, context, do_at, pause_request, purchase,
    report_request, travel,
)
from tests.life.environment_inputs import (
    DINNER_EPISODES, GROCERIES, catalog_rows, grocery_environment, set_content, write_catalog_rows,
)
from tests.life.environment_runtime import (
    act, act_group, events, every_five_minutes, finish_activity, one_hour, reach, settle_decision, until_ended,
)
from tests.integration.runtime_fixtures import write_agent_workspace
from worldmock.footprints.checkpoint import restore_branch, snapshot_directory
from worldmock.life.engine import LifeEngine

#: 「测试休息」在副本里的时长：足够等到外卖送到。
LONG_REST = 3 * 3600
#: 人物读到的五个提示层。
PROMPT_LAYERS = ("tool_layer", "todo_layer", "environment_layer", "memory_layer", "progress_layer")


def body(energy=None, *more):
    """运行配置：给定初始能量，再叠加别的配置。"""
    def mutate(persona, config):
        if energy is not None:
            persona["body"]["initial_energy_kcal"] = energy
        for item in more:
            item(persona, config)
    return mutate


def grocery_engine(engine_factory, tmp_path, mutate=None, change=None, **declared):
    """grocery_environment 副本上的真实引擎；declared 是状态的 initial、cap 与 goods，change(root) 在副本上再改几行，
    mutate 改人设与运行配置。"""
    root = grocery_environment(tmp_path / "groceries", **declared)
    set_content(root, "c-rest", duration_seconds=LONG_REST)
    if change is not None:
        change(root)
    return engine_factory(root, mutate=mutate)


def groceries(engine):
    """人物此刻 groceries 的值。"""
    return engine.context.state.agents[1].callback_states[GROCERIES]


def effects(engine, effect_type):
    """已提交的某一种回调效果事件。"""
    return [row for row in events(engine, "environment_event")
            if row.data.get("kind") == "callback_effect" and row.data["type"] == effect_type]


def accepted(engine, action_id):
    """一条动作受理时写下的 action_result 事件。"""
    return next(row for row in events(engine, "action_result") if row.action_id == action_id)


def home_units(engine, label):
    """人物此刻的一次观察里，家在工具层列出的单元名。"""
    observed = engine.observe(context(engine, f"{label}-{engine.context.state.current_time}"))
    home = next(row for row in observed["tool_layer"]["targets"] if row["name"] == "家")
    return {row["detail"]: row for row in home["actions"] if row["action"] == "interact"}


def finished(engine, activity_id):
    """这项活动里内容做到末端的那几条 activity_progress。"""
    return [row for row in events(engine, "activity_progress")
            if row.activity_id == activity_id and row.data.get("content", {}).get("completed")]


def fed(engine):
    """结算了完成效果、其中声明了 food 的 activity_progress。"""
    return [row for row in events(engine, "activity_progress")
            if ((row.data.get("content") or {}).get("completion_effects") or {}).get("food") is not None]


def settled_at(engine, moment):
    """那一秒人物 1 的共同身体结算记录。"""
    [row] = [item for item in events(engine, "environment_event")
             if item.data.get("kind") == "body_settlement" and item.occurred_at == moment and item.agent_id == 1]
    return row


def energy_now(engine):
    """人物此刻的能量。"""
    return engine.context.state.agents[1].body.energy_kcal


def pending_names(engine):
    """做完待收尾的活动名。"""
    return [item.name for item in engine.context.state.activities.values()
            if item.status not in {"active", "paused"} and item.concluded_at is None]


def receive(engine, *goods):
    """在家点名「测试休息」并同组下单 goods，推进到最后一件送到的那一秒，再结束「测试休息」与待收尾的购买。"""
    label = f"buy-{engine.context.state.current_time}"
    _, (rest, bought) = act_group(engine, label, do_at("家", "测试休息"), purchase(*goods))
    assert (rest.status, bought.status) == ("in_progress", "in_progress"), (rest.error_code, bought.error_code)
    orders = engine.context.state.activities[bought.activity_id].data["order_ids"]
    reach(engine, max(engine.context.state.deliveries[key].expected_at for key in orders))
    assert {engine.context.state.deliveries[key].status for key in orders} == {"delivered"}
    assert advance_until_ready(engine)
    names = [engine.context.state.activities[rest.activity_id].name, *pending_names(engine)]
    _, ended = act(engine, f"{label}-end", conclude_request(*names), "conclude_activity")
    assert ended.status == "completed", ended.error_code


def cook(engine, label="cook"):
    """在家点名「做饭」，返回开始结果。"""
    _, started = act(engine, label, do_at("家", "做饭"))
    assert started.status == "in_progress", started.error_code
    return started


def conclude(engine, activity_id, label):
    """按本人活动名结束一项活动。"""
    name = engine.context.state.activities[activity_id].name
    _, concluded = act(engine, label, conclude_request(name), "conclude_activity")
    assert concluded.status == "completed", concluded.error_code


def test_received_groceries_open_cooking_and_starting_it_uses_them(engine_factory, tmp_path):
    """外卖送来扑克牌：收货把 groceries 置为 1，家里列出「做饭」；开始的那次提交里状态从 1 到 0，恰有一条
    consume_on_start，起因是这次开始调用的受理结果，开始结果不提状态；开始后它不再列出；做完那一秒的完成效果与能量
    变化一致，resource_flows 最前是一条能量 source 分录。"""
    engine = grocery_engine(engine_factory, tmp_path, body(1000.0))
    assert "做饭" not in home_units(engine, "before")
    receive(engine, "扑克牌")
    [reset] = effects(engine, "reset_to_cap")
    assert (reset.data["goods_id"], reset.data["before"], reset.data["after"], groceries(engine)) == (
        "playing-cards", 0, 1, 1)
    assert "做饭" in home_units(engine, "opened")
    started = cook(engine)
    result = accepted(engine, action_identity("cook"))
    [consumed] = effects(engine, "consume_on_start")
    assert consumed.data == {"kind": "callback_effect", "type": "consume_on_start", "rule_id": None,
                             "state": GROCERIES, "content_id": "cook", "before": 1, "after": 0}
    assert consumed.caused_by == [result.event_id] and consumed.occurred_at == result.occurred_at
    assert (consumed.source_kind, consumed.visibility, consumed.resource_changes) == ("environment", "system", {})
    assert groceries(engine) == 0 and GROCERIES not in json.dumps(result.data, ensure_ascii=False)
    assert advance_until_ready(engine) and "做饭" not in home_units(engine, "cooking")
    until_ended(engine, started.activity_id)
    [done] = finished(engine, started.activity_id)
    food = done.data["content"]["completion_effects"]["food"]
    after = settled_at(engine, done.occurred_at).data["body_settlement"]["after"]["energy_kcal"]
    assert food["tier"] == "meal" and food["applied"] > 0
    assert energy_now(engine) == pytest.approx(after + food["applied"])
    assert done.data["resource_flows"][0] == {"resource": "energy_kcal", "owner": "agent:1", "direction": "source",
                                              "amount": food["applied"], "cause": "settle:shared:energy_kcal"}
    assert len(effects(engine, "consume_on_start")) == 1 and groceries(engine) == 0


def test_two_receipts_still_open_cooking_once(engine_factory, tmp_path):
    """一次买两件食材、收货两次：两条置为上限的效果，状态仍是上限 1；做一次之后是 0，不再列出。"""
    engine = grocery_engine(engine_factory, tmp_path)
    receive(engine, "扑克牌", "扑克牌")
    assert [(row.data["before"], row.data["after"]) for row in effects(engine, "reset_to_cap")] == [(0, 1), (1, 1)]
    assert groceries(engine) == 1
    cook(engine)
    assert groceries(engine) == 0 and len(effects(engine, "consume_on_start")) == 1
    assert advance_until_ready(engine) and "做饭" not in home_units(engine, "cooking")


def test_a_refused_group_leaves_the_groceries_untouched(engine_factory, tmp_path):
    """同组的购买余额不足：整组被拒，「做饭」没有开始，状态仍是 1，没有 consume_on_start；之后单独点名照常开始。"""
    poor = lambda persona, config: persona.update(initial_balance_fen=100)
    engine = grocery_engine(engine_factory, tmp_path, poor, initial=1)
    _, results = act_group(engine, "group", do_at("家", "做饭"), purchase("扑克牌"))
    assert [(row.status, row.error_code) for row in results] == [("rejected", "INSUFFICIENT_FUNDS")] * 2
    assert groceries(engine) == 1 and effects(engine, "consume_on_start") == []
    assert not engine.context.state.activities
    cook(engine, "alone")
    assert groceries(engine) == 0 and len(effects(engine, "consume_on_start")) == 1


def paused_cook(engine):
    """初始有食材时开始「做饭」、做一会儿再暂停；此时状态是 0，「做饭」不列出。返回开始结果与本人活动名。"""
    started = cook(engine)
    assert advance_until_ready(engine)
    name = engine.context.state.activities[started.activity_id].name
    _, paused = act(engine, "pause", pause_request(name), "pause_activity")
    assert paused.status == "completed", paused.error_code
    assert groceries(engine) == 0 and "做饭" not in home_units(engine, "paused")
    return started, name


def test_resuming_a_paused_cook_uses_no_more_groceries(engine_factory, tmp_path):
    """开始后暂停：状态 0 时「做饭」不列出；暂停期间又收到食材，它照旧列出但被暂停的活动占着；恢复不减、不被拒绝，
    做完照常结算一次完成效果，状态仍是 1。"""
    engine = grocery_engine(engine_factory, tmp_path, every_five_minutes, initial=1)
    started, name = paused_cook(engine)
    receive(engine, "扑克牌")
    held = home_units(engine, "reopened")["做饭"]
    assert groceries(engine) == 1 and (held["available"], held["reason"]) == (
        False, f"做饭 is held by the paused activity {name}.")
    _, resumed = act(engine, "resume", report_request(name), "report_status")
    assert resumed.status == "completed", resumed.error_code
    assert engine.context.state.activities[started.activity_id].status == "active"
    assert groceries(engine) == 1 and len(effects(engine, "consume_on_start")) == 1
    until_ended(engine, started.activity_id)
    assert len(fed(engine)) == 1 and groceries(engine) == 1 and len(effects(engine, "consume_on_start")) == 1


def test_resuming_a_paused_cook_is_not_refused_while_the_groceries_are_used(engine_factory, tmp_path):
    """开始后暂停、之后没再收到食材：状态是 0、「做饭」未开放，恢复照样受理而不以未开放拒绝，也不减；活动回到进行中，
    做完照常结算一次完成效果，状态仍是 0。"""
    engine = grocery_engine(engine_factory, tmp_path, every_five_minutes, initial=1)
    started, name = paused_cook(engine)
    _, resumed = act(engine, "resume", report_request(name), "report_status")
    assert resumed.status == "completed", resumed.error_code
    assert engine.context.state.activities[started.activity_id].status == "active"
    assert groceries(engine) == 0 and len(effects(engine, "consume_on_start")) == 1
    until_ended(engine, started.activity_id)
    [meal] = fed(engine)
    assert meal.activity_id == started.activity_id and groceries(engine) == 0


def test_ending_early_keeps_the_groceries_used_and_the_next_cook_starts_over(engine_factory, tmp_path):
    """做 5 分钟就结束：已减的不退、没有完成效果，「做饭」不再列出；再收到食材后它又列出，时长是全长、没有「接着做」，
    点名从头做起、再减 1，做完结算完成效果。"""
    engine = grocery_engine(engine_factory, tmp_path, body(1000.0, every_five_minutes), initial=1)
    first = cook(engine, "first")
    assert advance_until_ready(engine)
    conclude(engine, first.activity_id, "end")
    assert engine.context.state.activities[first.activity_id].status == "cancelled"
    assert groceries(engine) == 0 and fed(engine) == [] and "做饭" not in home_units(engine, "ended")
    receive(engine, "扑克牌")
    row = home_units(engine, "again")["做饭"]
    assert (row["available"], row["duration"], row["note"]) == (True, "约 1 小时", "供一餐")
    second = cook(engine, "second")
    activity = engine.context.state.activities[second.activity_id]
    assert activity.planned_duration_seconds == 3600 and activity.data["start_position_seconds"] == 0.0
    assert groceries(engine) == 0
    assert [row.data["before"] for row in effects(engine, "consume_on_start")] == [1, 1]
    until_ended(engine, second.activity_id)
    [meal] = fed(engine)
    assert meal.activity_id == second.activity_id


def _consumed_dinner(root):
    """副本里家多一个凭 groceries 的三集系列「做晚饭」，只有《吃晚饭》供一餐。"""
    rows = catalog_rows(root, "content-catalog.jsonl")
    for content_id, episode, title, seconds, exertion in DINNER_EPISODES:
        row = {"consumes_state": GROCERIES, "content_id": content_id, "duration_seconds": seconds,
               "exertion": exertion, "location_kinds": ["home"], "pushable": False, "relaxation_relief": "none",
               "series": {"episode": episode, "title": "做晚饭"}, "social_relief": "none",
               "summary": f"{title}的测试用合成摘要。",
               "summary_source": {"kind": "synthetic", "notice": "测试用合成摘要", "url": None}, "title": title}
        if content_id == "dinner-2-eat":
            row["completion_effects"] = {"food": "meal"}
        rows.append(row)
    write_catalog_rows(root, "content-catalog.jsonl", sorted(rows, key=lambda row: row["content_id"]))


def test_a_series_uses_the_groceries_once_and_feeds_only_its_meal_episode(engine_factory, tmp_path):
    """点名凭状态的系列「做晚饭」：开始时减一次（内容是第一集），自动接下一集不再减；三集都做完，只有《吃晚饭》
    做完的那一秒结算完成效果。"""
    engine = grocery_engine(engine_factory, tmp_path, change=_consumed_dinner, initial=1)
    _, dinner = act(engine, "dinner", do_at("家", "做晚饭"))
    assert dinner.status == "in_progress", dinner.error_code
    [consumed] = effects(engine, "consume_on_start")
    assert (consumed.data["content_id"], groceries(engine)) == ("dinner-1-cook", 0)
    until_ended(engine, dinner.activity_id)
    rows = finished(engine, dinner.activity_id)
    assert [row.data["content"]["content_id"] for row in rows] == ["dinner-1-cook", "dinner-2-eat", "dinner-3-wash"]
    assert [row.data["content"]["completion_effects"] is not None for row in rows] == [False, True, False]
    assert effects(engine, "consume_on_start") == [consumed] and groceries(engine) == 0


def _threshold_rules(offset):
    """副本里登记 groceries 低于 1 时扣 100 分的阈值规则；offset 时另登记开始 interact 就把 groceries 加 1 的动作规则。"""
    def change(root):
        state = {"state": GROCERIES, "initial": 1, "cap": 1, "reset_on_goods": ["playing-cards"]}
        rules = [{"rule_id": "groceries-out", "trigger": {"class": "state", "key": GROCERIES, "below": 1},
                  "effect": {"type": "transfer", "direction": "expense", "amount_fen": 100}}]
        if offset:
            rules.insert(0, {"rule_id": "cook-refill", "trigger": {"class": "action", "key": "interact"},
                             "effect": {"type": "state_delta", "state": GROCERIES, "delta": 1}})
        write_catalog_rows(root, "callbacks.jsonl", [state, *rules])
    return change


def _rule_effects(engine, rule_id):
    """某条规则的回调效果事件。"""
    return [row for row in events(engine, "environment_event")
            if row.data.get("kind") == "callback_effect" and row.data.get("rule_id") == rule_id]


@pytest.mark.parametrize("offset", [False, True], ids=["used-up", "offset-in-the-same-commit"])
def test_using_up_the_groceries_fires_the_threshold_rule_once(engine_factory, tmp_path, offset):
    """below 为 1 的状态规则在开始做饭的那次提交里触发一次，起因指向这次开始调用的受理结果，之后不再触发；同一次提交
    里动作规则把状态加回来时，提交前后的值相同，不触发。"""
    engine = grocery_engine(engine_factory, tmp_path, change=_threshold_rules(offset))
    balance = engine.context.state.accounts["agent:1"]
    started = cook(engine)
    result = accepted(engine, action_identity("cook"))
    [consumed] = effects(engine, "consume_on_start")
    assert consumed.caused_by == [result.event_id]
    fired = _rule_effects(engine, "groceries-out")
    if offset:
        [refill] = _rule_effects(engine, "cook-refill")
        assert (refill.data["before"], refill.data["after"], groceries(engine)) == (0, 1, 1) and fired == []
        assert engine.context.state.accounts["agent:1"] == balance
    else:
        [fee] = fired
        assert fee.caused_by == [result.event_id] and fee.occurred_at == result.occurred_at
        assert groceries(engine) == 0 and engine.context.state.accounts["agent:1"] == balance - 100
    until_ended(engine, started.activity_id)
    assert _rule_effects(engine, "groceries-out") == fired


def _station_meal(root):
    """副本里快递驿站多一件凭 groceries 的「驿站加餐」，五分钟。"""
    rows = catalog_rows(root, "content-catalog.jsonl")
    service = next(row for row in rows if row["content_id"] == "parcel-service")
    rows.append({**service, "consumes_state": GROCERIES, "content_id": "station-meal", "duration_seconds": 300,
                 "title": "驿站加餐"})
    write_catalog_rows(root, "content-catalog.jsonl", sorted(rows, key=lambda row: row["content_id"]))


def _six_days(persona, config):
    """运行六个本地日，足够快递到站。"""
    config["stop_time"] = config["start_time"] + timedelta(days=6)


def _until_free(engine):
    """推进并处理决策机会，直到人物有决策机会而手上没有进行中或待收尾的活动。"""
    for counter in range(500):
        if not engine.scheduling_state().ready_agent_ids:
            advance_next(engine)
            continue
        occupied = [item for item in engine.context.state.activities.values()
                    if item.status == "active" or item.status not in {"paused", "truncated"}
                    and item.concluded_at is None]
        if not occupied:
            return
        settle_decision(engine, f"free-{counter}-{engine.context.state.current_time}")
    raise AssertionError("the person never became free")


def test_at_the_parcel_station_collecting_comes_before_using_the_groceries(engine_factory, tmp_path):
    """状态 1、上限 2，平台笔记本到站：在驿站点名凭状态的「驿站加餐」，同一次受理先取件（收货把状态置为上限 2），
    再减 1 到 1；三条事件按这个次序，都回链这条受理结果。"""
    engine = grocery_engine(engine_factory, tmp_path, _six_days, _station_meal, initial=1, cap=2,
                            goods=("online-notebook",))
    _, bought = act(engine, "notebook", purchase("平台笔记本"))
    assert bought.status == "in_progress", bought.error_code
    finish_activity(engine, bought, "notebook")
    [order] = engine.context.state.deliveries.values()
    reach(engine, order.expected_at)
    _until_free(engine)
    _, trip = act(engine, "to-station", travel("快递驿站", "驾车"))
    finish_activity(engine, trip, "at-station")
    assert engine.context.state.deliveries[order.order_id].status == "ready_for_pickup"
    _, meal = act(engine, "meal", do_at("快递驿站", "驿站加餐"))
    assert meal.status == "in_progress", meal.error_code
    result = accepted(engine, action_identity("meal"))
    later = [row for row in events(engine, "environment_event") if row.event_seq > result.event_seq
             and row.data.get("kind") in {"goods_received", "callback_effect"}]
    assert [row.data.get("type", row.data["kind"]) for row in later] == [
        "goods_received", "reset_to_cap", "consume_on_start"]
    received, reset, consumed = later
    assert received.caused_by == consumed.caused_by == [result.event_id] and reset.caused_by == [received.event_id]
    assert [(row.data["before"], row.data["after"]) for row in (reset, consumed)] == [(1, 2), (2, 1)]
    assert groceries(engine) == 1


@pytest.mark.parametrize("seconds,settles", [(3600, True), (3601, False)], ids=["finished-at-the-cut-off", "cut-short"])
def test_the_run_cut_off_neither_refunds_nor_feeds_an_unfinished_cook(engine_factory, tmp_path, seconds, settles):
    """一小时的运行里点名时长 seconds 的「做饭」：开始时减 1，截止不退；恰在截止那一秒做完照常结算完成效果，截止截断
    没做完的没有。"""
    change = lambda root: set_content(root, "cook", duration_seconds=seconds)
    engine = grocery_engine(engine_factory, tmp_path, one_hour, change, initial=1)
    started = cook(engine)
    reach(engine, engine.context.state.end_time)
    activity = engine.context.state.activities[started.activity_id]
    assert activity.status == ("completed" if settles else "truncated")
    assert groceries(engine) == 0 and len(effects(engine, "consume_on_start")) == 1
    assert len(fed(engine)) == int(settles)


def test_a_branch_restored_mid_cook_uses_the_groceries_once_and_feeds_the_same(engine_factory, tmp_path):
    """做饭做到一半写检查点并恢复成分支：分支里状态已是 0、仍只有原来那一条 consume_on_start；分支与原运行做完时的
    完成效果证据、能量和状态逐值相同，都不再减。"""
    engine = grocery_engine(engine_factory, tmp_path, body(1000.0, every_five_minutes), initial=1)
    started = cook(engine)
    assert advance_until_ready(engine)
    workspace = write_agent_workspace(tmp_path / "agent")
    point = engine.checkpoint({"agent:1": snapshot_directory(workspace, engine.context.store.assets)})
    restore_branch(engine.context.store, point.checkpoint_id, tmp_path / "branch").close()
    child = LifeEngine.open(engine.context.inputs, tmp_path / "branch")
    try:
        [original] = effects(engine, "consume_on_start")
        assert groceries(child) == 0 and [row.event_id for row in effects(child, "consume_on_start")] == [
            original.event_id]
        for run in (engine, child):
            until_ended(run, started.activity_id)
        [first], [branched] = fed(engine), fed(child)
        assert branched.occurred_at == first.occurred_at and branched.data["content"] == first.data["content"]
        assert energy_now(child) == energy_now(engine)
        assert groceries(child) == groceries(engine) == 0
        assert len(effects(child, "consume_on_start")) == len(effects(engine, "consume_on_start")) == 1
    finally:
        child.close()


def test_reopening_after_a_checkpoint_does_not_use_the_groceries_again(engine_factory, tmp_path):
    """开始做饭后写检查点、重新打开存储：状态逐值相同，consume_on_start 仍是一条；继续做完也不再减。"""
    engine = grocery_engine(engine_factory, tmp_path, initial=1)
    started = cook(engine)
    workspace = write_agent_workspace(tmp_path / "agent")
    engine.checkpoint({"agent:1": snapshot_directory(workspace, engine.context.store.assets)})
    inputs, run_dir = engine.context.inputs, engine.context.store.run_dir
    engine.close()
    reopened = LifeEngine.open(inputs, run_dir)
    try:
        assert groceries(reopened) == 0 and len(effects(reopened, "consume_on_start")) == 1
        until_ended(reopened, started.activity_id)
        assert groceries(reopened) == 0 and len(effects(reopened, "consume_on_start")) == 1
        assert len(fed(reopened)) == 1
    finally:
        reopened.close()


def _plain_cook(root):
    """「做饭」不凭状态，完成效果只有 food。"""
    rows = catalog_rows(root, "content-catalog.jsonl")
    row = next(item for item in rows if item["content_id"] == "cook")
    del row["consumes_state"]
    row["completion_effects"] = {"food": "meal"}
    write_catalog_rows(root, "content-catalog.jsonl", rows)


def test_an_open_consumed_state_and_relief_members_change_no_prompt_layer(engine_factory, tmp_path):
    """同一环境的两份副本只差「做饭」的 consumes_state（状态 2、上限 2，开始之后仍开放）与完成效果的两项缓解成员：
    开始时与做到一半时，人物读到的各层逐字节相同。"""
    relieved = lambda root: set_content(root, "cook", completion_effects={
        "food": "meal", "relaxation_relief": "high", "social_relief": "low"})
    consuming = grocery_engine(engine_factory, tmp_path / "consuming", change=relieved, initial=2, cap=2)
    plain = grocery_engine(engine_factory, tmp_path / "plain", change=_plain_cook, initial=2, cap=2)
    seen = []
    for engine in (consuming, plain):
        observed, _ = act(engine, "cook", do_at("家", "做饭"))
        assert advance_until_ready(engine)
        midway = engine.observe(context(engine, "midway"))
        seen.append([{key: layer[key] for key in PROMPT_LAYERS} for layer in (observed, midway)])
    assert (groceries(consuming), groceries(plain)) == (1, 2)
    assert seen[0] == seen[1]
