"""在地点吃的一餐：写了 completion_effects 的地点内容做到末端的那一秒，在同一秒共同身体结算之后结算一次完成效果。

都经真实请求、引擎与 SQLite：样例环境副本 meal_environment 里「测试休息」供一餐，家的三集系列「做晚饭」只有
《吃晚饭》供一餐（docs/design/04-simulation-engine.md「出行、内容与查询」，05-recording-engine.md「事件与来源」）。
期望值用生产函数算，不写死。
"""

from __future__ import annotations

import pytest

from tests.integration.runtime_fixtures import write_agent_workspace
from tests.life.conftest import (
    advance_next, advance_until_ready, conclude_request, context, do_at, pause_request, purchase, report_request,
    stage_errands,
)
from tests.life.current_world import stage_moment
from tests.life.environment_inputs import catalog_rows, meal_environment, set_content, write_catalog_rows
from tests.life.environment_runtime import (
    act, act_group, events, every_five_minutes, one_hour, reach, until_ended,
)
from worldmock.exchange.constants import GOODS_ENERGY_KCAL
from worldmock.footprints.checkpoint import restore_branch, snapshot_directory
from worldmock.life.body import food_intake
from worldmock.life.engine import LifeEngine
from worldmock.life.errand_state import ErrandState
from worldmock.life.models import BodyState
from worldmock.life.purchase_offers import quote_goods
from worldmock.life.sleep import food_utilization_efficiency

#: 能量上限（千卡）。
CAP = 2400


def meal_engine(engine_factory, tmp_path, mutate=None, change=None):
    """meal_environment 副本上的真实引擎；change(root) 在副本上再改几行，mutate 改人设与运行配置。"""
    root = meal_environment(tmp_path / "meal")
    if change is not None:
        change(root)
    return engine_factory(root, mutate=mutate)


def body(energy=None, deficit=None, *more):
    """运行配置：给定初始能量与欠觉小时数，再叠加别的配置。"""
    def mutate(persona, config):
        if energy is not None:
            persona["body"]["initial_energy_kcal"] = energy
        if deficit is not None:
            persona["body"]["initial_sleep_debt_hours"] = deficit
        for item in more:
            item(persona, config)
    return mutate


def progress_rows(engine, activity_id=None):
    """已提交的 activity_progress 事件，可按活动身份筛。"""
    return [row for row in events(engine, "activity_progress") if activity_id in (None, row.activity_id)]


def finished(engine, activity_id=None):
    """内容做到末端的那几条 activity_progress。"""
    return [row for row in progress_rows(engine, activity_id) if row.data.get("content", {}).get("completed")]


def fed(engine):
    """结算了完成效果、其中声明了 food 的 activity_progress。"""
    return [row for row in progress_rows(engine)
            if ((row.data.get("content") or {}).get("completion_effects") or {}).get("food") is not None]


def settled_at(engine, moment):
    """那一秒人物 1 的共同身体结算记录。"""
    [row] = [item for item in events(engine, "environment_event")
             if item.data.get("kind") == "body_settlement" and item.occurred_at == moment and item.agent_id == 1]
    return row


def energy_now(engine):
    """人物此刻的能量。"""
    return engine.context.state.agents[1].body.energy_kcal


def expected_food(engine, moment, tier="meal"):
    """生产函数对那一秒共同身体结算之后的身体结算一份 tier 的补能：返回（结算后的能量，{provided, applied, efficiency}）。"""
    after = BodyState.model_validate(settled_at(engine, moment).data["body_settlement"]["after"])
    _, intake, _ = food_intake(after, GOODS_ENERGY_KCAL[tier], engine.context.body)
    return after.energy_kcal, intake


def food_evidence(row):
    """一条 activity_progress 上 food 成员的证据。"""
    return row.data["content"]["completion_effects"]["food"]


def energy_sources(row):
    """一条事件上能量进账的分录。"""
    return [flow for flow in row.data.get("resource_flows", [])
            if (flow["resource"], flow["direction"]) == ("energy_kcal", "source")]


def rest_until_five_minutes(engine):
    """点名「测试休息」，推进到开始后五分钟的那次决策机会；返回开始结果。"""
    _, rest = act(engine, "rest", do_at("家", "测试休息"))
    assert rest.status == "in_progress", rest.error_code
    assert advance_until_ready(engine)
    assert engine.context.state.activities[rest.activity_id].elapsed_seconds == 300
    return rest


def test_finishing_a_meal_unit_adds_one_food_in_that_second(engine_factory, tmp_path):
    """「测试休息」做到一半的结算边界上能量只减不增、没有完成效果；做到末端的那一秒，能量等于共同身体结算之后的能量加上
    生产函数算出的一件食品的进账；随后收尾，能量不再因此增加，全程恰一条带 food 的事件。"""
    engine = meal_engine(engine_factory, tmp_path, body(1000.0, None, every_five_minutes))
    rest = rest_until_five_minutes(engine)
    now = engine.context.state.current_time
    [midway] = progress_rows(engine, rest.activity_id)
    assert midway.data["content"]["completed"] is False and midway.data["content"]["completion_effects"] is None
    assert energy_now(engine) == settled_at(engine, now).data["body_settlement"]["after"]["energy_kcal"] < 1000.0
    until_ended(engine, rest.activity_id)
    [done] = finished(engine, rest.activity_id)
    before, intake = expected_food(engine, done.occurred_at)
    assert done.occurred_at == now + 600 and intake["applied"] > 0
    assert done.data["content"]["completion_effects"] == {
        "food": {"tier": "meal", **intake}, "relaxation_relief": None, "social_relief": None}
    assert energy_now(engine) == pytest.approx(before + intake["applied"])
    name = engine.context.state.activities[rest.activity_id].name
    eaten = energy_now(engine)
    _, concluded = act(engine, "conclude", conclude_request(name), "conclude_activity")
    assert concluded.status == "completed", concluded.error_code
    assert energy_now(engine) == eaten and fed(engine) == [done]


@pytest.mark.parametrize("wash_fed", [False, True], ids=["only-eating", "eating-and-washing"])
def test_series_settles_only_the_episodes_that_declare_food(engine_factory, tmp_path, wash_fed):
    """点名「做晚饭」：只有写了 food 的那一集做完那一秒进账，活动仍在进行并已是下一集；把《洗碗收拾厨房》也写成供一餐
    的变体有两条带 food 的事件，各在自己那一集做完的那一秒。"""
    change = (lambda root: set_content(root, "dinner-3-wash", completion_effects={"food": "meal"})) if wash_fed \
        else None
    engine = meal_engine(engine_factory, tmp_path, change=change)
    _, dinner = act(engine, "dinner", do_at("家", "做晚饭"))
    start = engine.context.state.current_time
    until_ended(engine, dinner.activity_id)
    rows = finished(engine, dinner.activity_id)
    assert [(row.data["content"]["content_id"], row.occurred_at) for row in rows] == [
        ("dinner-1-cook", start + 1800), ("dinner-2-eat", start + 3300), ("dinner-3-wash", start + 4200)]
    served = [row.data["content"]["completion_effects"] is not None for row in rows]
    assert served == [False, True, wash_fed]
    assert fed(engine) == [row for row in rows if row.data["content"]["completion_effects"] is not None]
    for row in rows:
        assert bool(energy_sources(row)) == (row.data["content"]["completion_effects"] is not None)
    eating = rows[1]
    assert eating.data["activity"]["status"] == "active"
    assert eating.data["activity"]["data"]["content_id"] == "dinner-3-wash"
    assert food_evidence(eating) == {"tier": "meal", **expected_food(engine, eating.occurred_at)[1]}


def test_a_delivery_in_the_same_second_is_received_after_the_meal(engine_factory, tmp_path):
    """外卖恰在「测试休息」做完那一秒送到、能量接近上限：先结算在地点吃的一餐，进到上限为止；同一秒的收货实得 0.0、
    没有分录。"""
    root = meal_environment(tmp_path / "meal")
    probe = engine_factory(root, mutate=body(2300.0))
    quote = quote_goods(probe.context.inputs, probe.context.state, 1, "beef-noodles", None)
    set_content(root, "c-rest", duration_seconds=quote.expected_at - probe.context.state.current_time)
    engine = engine_factory(root, mutate=body(2300.0))
    _, (rest, ordered) = act_group(engine, "dinner", do_at("家", "测试休息"), purchase("牛肉面"))
    assert (rest.status, ordered.status) == ("in_progress", "in_progress"), (rest.error_code, ordered.error_code)
    [order] = engine.context.state.deliveries.values()
    assert order.expected_at == engine.context.state.activities[rest.activity_id].planned_end_at
    reach(engine, order.expected_at)
    [done] = finished(engine, rest.activity_id)
    [received] = [row for row in events(engine, "environment_event") if row.data.get("kind") == "goods_received"]
    before, _ = expected_food(engine, order.expected_at)
    assert done.occurred_at == received.occurred_at == order.expected_at and done.event_seq < received.event_seq
    assert food_evidence(done)["applied"] == pytest.approx(CAP - before) and food_evidence(done)["applied"] < 540
    assert (received.data["applied"], received.data["resource_flows"]) == (0.0, [])
    assert energy_now(engine) == CAP


def test_a_location_content_without_food_settles_nothing(engine_factory, tmp_path):
    """「测试闲聊」没有声明完成效果：做完时内容证据的 completion_effects 为 null，能量只有消耗。"""
    engine = meal_engine(engine_factory, tmp_path)
    _, chat = act(engine, "chat", do_at("家", "测试闲聊"))
    until_ended(engine, chat.activity_id)
    [done] = finished(engine, chat.activity_id)
    assert done.data["content"]["completion_effects"] is None and not energy_sources(done)
    after = settled_at(engine, done.occurred_at).data["body_settlement"]["after"]["energy_kcal"]
    assert energy_now(engine) == after < 1800.0 and fed(engine) == []


def test_food_uses_the_utilization_of_the_moment_and_stops_at_the_cap(engine_factory, tmp_path):
    """欠觉 16 小时开始：efficiency 是生产函数在那一秒的值；能量 2,300 开始：只进到 2,400，applied 是实际进的那部分，
    等于分录的数额。"""
    sleepy = meal_engine(engine_factory, tmp_path / "sleepy", body(1000.0, 16.0))
    _, rest = act(sleepy, "rest", do_at("家", "测试休息"))
    until_ended(sleepy, rest.activity_id)
    [done] = finished(sleepy, rest.activity_id)
    deficit = settled_at(sleepy, done.occurred_at).data["body_settlement"]["after"]["sleep_deficit_hours"]
    assert food_evidence(done)["efficiency"] == food_utilization_efficiency(deficit) < 0.9
    full = meal_engine(engine_factory, tmp_path / "full", body(2300.0))
    _, rest = act(full, "rest", do_at("家", "测试休息"))
    until_ended(full, rest.activity_id)
    [done] = finished(full, rest.activity_id)
    before, _ = expected_food(full, done.occurred_at)
    applied = food_evidence(done)["applied"]
    assert applied == pytest.approx(CAP - before) and applied == sum(flow["amount"] for flow in energy_sources(done))
    assert energy_now(full) == CAP


def _store_bite(root, seconds):
    """副本里给便利店加一件供一餐的地点内容「店里小坐」，时长 seconds 秒。"""
    rows = catalog_rows(root, "content-catalog.jsonl")
    rows = [row for row in rows if row["content_id"] != "store-bite"]
    rows.append({"completion_effects": {"food": "meal"}, "content_id": "store-bite", "duration_seconds": seconds,
                 "exertion": "idle", "location_kinds": ["convenience_store"], "pushable": False,
                 "relaxation_relief": "none", "social_relief": "none", "summary": "在店里坐着吃点东西。",
                 "summary_source": {"kind": "synthetic", "notice": "测试用合成摘要", "url": None}, "title": "店里小坐"})
    write_catalog_rows(root, "content-catalog.jsonl", sorted(rows, key=lambda row: row["content_id"]))


def test_a_meal_at_the_energy_cap_applies_zero_and_registers_no_energy_flow(engine_factory, tmp_path):
    """在四季便利同一组里先买饭团、再点名「店里小坐」，两者在同一秒结束：先结束的购买收货把能量补到上限，随后做完的
    这一餐 applied 是 0.0，证据照写，这条事件没有能量分录。"""
    def at_store(persona, config):
        persona["initial_location"] = "四季便利"
        persona["body"]["initial_energy_kcal"] = 2400.0

    root = meal_environment(tmp_path / "meal")
    _store_bite(root, 60)
    probe = engine_factory(root, mutate=at_store)
    _, (bought, _) = act_group(probe, "cap", purchase("饭团"), do_at("四季便利", "店里小坐"))
    seconds = probe.context.state.activities[bought.activity_id].planned_duration_seconds
    _store_bite(root, seconds)
    engine = engine_factory(root, mutate=at_store)
    _, (bought, bite) = act_group(engine, "cap", purchase("饭团"), do_at("四季便利", "店里小坐"))
    activities = engine.context.state.activities
    assert activities[bought.activity_id].planned_end_at == activities[bite.activity_id].planned_end_at
    until_ended(engine, bite.activity_id)
    [done] = finished(engine, bite.activity_id)
    assert food_evidence(done)["applied"] == 0.0 and type(food_evidence(done)["applied"]) is float
    assert food_evidence(done)["provided"] == 600 and not energy_sources(done)
    [received] = [row for row in events(engine, "environment_event") if row.data.get("kind") == "goods_received"]
    assert received.occurred_at == done.occurred_at and received.event_seq < done.event_seq
    assert energy_now(engine) == CAP


def test_a_location_meal_costs_nothing_and_is_not_a_receipt(engine_factory, tmp_path):
    """做完供一餐的事：账户余额不变，没有订单，也没有收货、付款和通知事件。"""
    engine = meal_engine(engine_factory, tmp_path, body(1000.0))
    accounts = dict(engine.context.state.accounts)
    _, rest = act(engine, "rest", do_at("家", "测试休息"))
    until_ended(engine, rest.activity_id)
    assert len(fed(engine)) == 1
    assert engine.context.state.accounts == accounts and engine.context.state.deliveries == {}
    kinds = {row.data.get("kind") for row in events(engine, "environment_event")}
    assert not kinds & {"goods_received", "purchase_payment", "payment_notification", "order_notification"}


def _end_rest_at_once(engine):
    """点名「测试休息」，在开始的同一秒就结束：开始之后把下一次决策机会放回这一秒，时刻不动。"""
    _, rest = act(engine, "rest", do_at("家", "测试休息"))
    assert rest.status == "in_progress", rest.error_code
    started = engine.context.state.current_time
    stage_moment(engine)
    assert engine.context.state.current_time == started
    return rest, engine.context.state.activities[rest.activity_id].name


def _end_rest_midway(engine):
    """「测试休息」做 5 分钟后用 conclude_activity 结束。"""
    rest = rest_until_five_minutes(engine)
    return rest, engine.context.state.activities[rest.activity_id].name


def chat_while_paused(engine, rest):
    """「测试休息」做 5 分钟后暂停，暂停期间做完「测试闲聊」并收尾，让时间在暂停中过去；返回暂停那一刻的能量。"""
    name = engine.context.state.activities[rest.activity_id].name
    _, paused = act(engine, "pause", pause_request(name), "pause_activity")
    assert paused.status == "completed", paused.error_code
    paused_energy = energy_now(engine)
    _, chat = act(engine, "chat", do_at("家", "测试闲聊"))
    assert chat.status == "in_progress", chat.error_code
    until_ended(engine, chat.activity_id)
    chatted = engine.context.state.activities[chat.activity_id].name
    _, concluded = act(engine, "chat-conclude", conclude_request(chatted), "conclude_activity")
    assert concluded.status == "completed", concluded.error_code
    assert engine.context.state.activities[rest.activity_id].status == "paused"
    return paused_energy


def _end_rest_after_pausing(engine):
    """「测试休息」做 5 分钟后暂停，过一段时间再结束。"""
    rest = rest_until_five_minutes(engine)
    chat_while_paused(engine, rest)
    return rest, engine.context.state.activities[rest.activity_id].name


def _end_dinner_at(seconds):
    """点名「做晚饭」，做到开始后 seconds 秒的那次决策机会时结束。"""
    def end(engine):
        _, dinner = act(engine, "dinner", do_at("家", "做晚饭"))
        reach(engine, engine.context.state.current_time + seconds)
        return dinner, engine.context.state.activities[dinner.activity_id].name
    return end


@pytest.mark.parametrize("end", [_end_rest_midway, _end_rest_at_once, _end_rest_after_pausing, _end_dinner_at(600),
                                 _end_dinner_at(2400)],
                         ids=["rest-midway", "rest-ended-in-its-first-second", "rest-paused-then-ended",
                              "dinner-while-cooking", "dinner-while-eating"])
def test_ending_early_settles_no_completion_effects(engine_factory, tmp_path, end):
    """做到一半结束、开始的同一秒就结束、暂停后再结束、系列在《备菜做饭》里结束、《吃晚饭》做到一半结束（被结束的一集
    不供餐）：都没有完成效果，能量从不进账；被结束的这件事仍按「结束就算做完」记进度，整个单元的轮数是 1。"""
    engine = meal_engine(engine_factory, tmp_path, body(1000.0, None, every_five_minutes))
    activity, name = end(engine)
    _, concluded = act(engine, "end", conclude_request(name), "conclude_activity")
    assert concluded.status == "completed", concluded.error_code
    assert engine.context.state.activities[activity.activity_id].status == "cancelled"
    assert fed(engine) == [] and not any(energy_sources(row) for row in progress_rows(engine))
    assert all(row.data["content"]["completion_effects"] is None for row in progress_rows(engine, activity.activity_id)
               if "content" in row.data)
    assert energy_now(engine) <= 1000.0
    progress = engine.context.state.agents[1].content_progress
    unit = ("c-rest",) if name.startswith("进行测试休息") else ("dinner-1-cook", "dinner-2-eat", "dinner-3-wash")
    assert {key: (progress[key].position_seconds, progress[key].completed_rounds) for key in unit} == {
        key: (0.0, 1) for key in unit}


def test_ending_after_the_meal_episode_keeps_that_meal_once(engine_factory, tmp_path):
    """《吃晚饭》做完之后、在《洗碗收拾厨房》里结束：那一餐在它做完的那一秒已经进账，结束不追回也不再进一次。"""
    engine = meal_engine(engine_factory, tmp_path, every_five_minutes)
    _, dinner = act(engine, "dinner", do_at("家", "做晚饭"))
    reach(engine, engine.context.state.current_time + 3600)
    activity = engine.context.state.activities[dinner.activity_id]
    assert activity.data["content_id"] == "dinner-3-wash"
    [meal] = fed(engine)
    eaten = energy_now(engine)
    _, concluded = act(engine, "end", conclude_request(activity.name), "conclude_activity")
    assert concluded.status == "completed", concluded.error_code
    assert fed(engine) == [meal] and energy_now(engine) == eaten
    assert meal.data["content"]["content_id"] == "dinner-2-eat" and food_evidence(meal)["applied"] > 0


def test_resuming_a_paused_meal_settles_it_once_at_the_finish(engine_factory, tmp_path):
    """「测试休息」暂停、恢复、做完：暂停期间能量不增，做完那一秒进账一次。"""
    engine = meal_engine(engine_factory, tmp_path, body(1000.0, None, every_five_minutes))
    rest = rest_until_five_minutes(engine)
    name = engine.context.state.activities[rest.activity_id].name
    paused_energy = chat_while_paused(engine, rest)
    assert energy_now(engine) < paused_energy and fed(engine) == []
    _, resumed = act(engine, "resume", report_request(name), "report_status")
    assert resumed.status == "completed", resumed.error_code
    until_ended(engine, rest.activity_id)
    [done] = finished(engine, rest.activity_id)
    assert fed(engine) == [done]
    before, intake = expected_food(engine, done.occurred_at)
    assert food_evidence(done) == {"tier": "meal", **intake}
    assert energy_now(engine) == pytest.approx(before + intake["applied"])


def test_doing_it_again_settles_again_within_the_cap(engine_factory, tmp_path):
    """同一件事连做两遍：两条带 food 的事件，第二次受能量上限约束。"""
    engine = meal_engine(engine_factory, tmp_path)
    for label in ("first", "second"):
        _, rest = act(engine, label, do_at("家", "测试休息"))
        assert rest.status == "in_progress", rest.error_code
        until_ended(engine, rest.activity_id)
        name = engine.context.state.activities[rest.activity_id].name
        act(engine, f"{label}-conclude", conclude_request(name), "conclude_activity")
    first, second = fed(engine)
    assert food_evidence(first)["applied"] == pytest.approx(540.0)
    before, intake = expected_food(engine, second.occurred_at)
    assert food_evidence(second) == {"tier": "meal", **intake}
    assert food_evidence(second)["applied"] == pytest.approx(CAP - before) and food_evidence(second)["applied"] < 540
    assert energy_now(engine) == CAP


@pytest.mark.parametrize("seconds,settles", [(3600, True), (3601, False)], ids=["finished-at-the-cut-off", "cut-short"])
def test_run_end_settles_only_a_meal_finished_by_the_cut_off(engine_factory, tmp_path, seconds, settles):
    """一小时的运行里点名时长 seconds 的「测试休息」：恰在截止那一秒做完照常结算，活动在截断之前先结算；截止截断没做完的
    没有完成效果。"""
    change = lambda root: set_content(root, "c-rest", duration_seconds=seconds)
    engine = meal_engine(engine_factory, tmp_path, one_hour, change)
    _, rest = act(engine, "rest", do_at("家", "测试休息"))
    reach(engine, engine.context.state.end_time)
    activity = engine.context.state.activities[rest.activity_id]
    assert activity.status == ("completed" if settles else "truncated")
    assert len(fed(engine)) == int(settles)
    if settles:
        assert fed(engine)[0].occurred_at == engine.context.state.end_time


def test_reopening_after_a_checkpoint_does_not_settle_the_meal_again(engine_factory, tmp_path):
    """做完后写检查点、重新打开存储：人物能量逐值相同，带 food 的事件仍是一条，继续推进也不再多一条。"""
    engine = meal_engine(engine_factory, tmp_path, body(1000.0))
    _, rest = act(engine, "rest", do_at("家", "测试休息"))
    until_ended(engine, rest.activity_id)
    name = engine.context.state.activities[rest.activity_id].name
    act(engine, "conclude", conclude_request(name), "conclude_activity")
    _, chat = act(engine, "chat", do_at("家", "测试闲聊"))
    assert chat.status == "in_progress", chat.error_code
    eaten = energy_now(engine)
    workspace = write_agent_workspace(tmp_path / "agent")
    engine.checkpoint({"agent:1": snapshot_directory(workspace, engine.context.store.assets)})
    inputs, run_dir = engine.context.inputs, engine.context.store.run_dir
    engine.close()
    reopened = LifeEngine.open(inputs, run_dir)
    try:
        assert energy_now(reopened) == eaten and len(fed(reopened)) == 1
        advance_next(reopened)
        assert len(fed(reopened)) == 1 and energy_now(reopened) < eaten
    finally:
        reopened.close()


def test_a_branch_restored_mid_meal_settles_the_same_completion(engine_factory, tmp_path):
    """做到一半写检查点并恢复成分支：分支做完时的完成效果证据与原运行逐值相同。"""
    engine = meal_engine(engine_factory, tmp_path, body(1000.0, None, every_five_minutes))
    rest = rest_until_five_minutes(engine)
    workspace = write_agent_workspace(tmp_path / "agent")
    point = engine.checkpoint({"agent:1": snapshot_directory(workspace, engine.context.store.assets)})
    restore_branch(engine.context.store, point.checkpoint_id, tmp_path / "branch").close()
    child = LifeEngine.open(engine.context.inputs, tmp_path / "branch")
    try:
        for run in (engine, child):
            until_ended(run, rest.activity_id)
        [original], [branched] = fed(engine), fed(child)
        assert branched.occurred_at == original.occurred_at
        assert branched.data["content"] == original.data["content"]
        assert energy_now(child) == energy_now(engine)
    finally:
        child.close()


def test_the_evidence_sits_on_the_finishing_progress_event(engine_factory, tmp_path):
    """证据在做完那条 activity_progress 上：completion_effects 恰三个成员键，food 恰四个键；resource_flows 第一条是能量
    进账、数额等于 applied；同一秒的共同身体结算不含这一餐，世界里的能量是它的结算后加上 applied；活动数据里没有与
    完成效果有关的键。"""
    engine = meal_engine(engine_factory, tmp_path, body(1000.0))
    _, rest = act(engine, "rest", do_at("家", "测试休息"))
    until_ended(engine, rest.activity_id)
    [done] = finished(engine, rest.activity_id)
    effects = done.data["content"]["completion_effects"]
    assert set(effects) == {"food", "relaxation_relief", "social_relief"}
    assert set(effects["food"]) == {"tier", "provided", "applied", "efficiency"}
    assert done.data["resource_flows"][0] == {"resource": "energy_kcal", "owner": "agent:1", "direction": "source",
                                              "amount": effects["food"]["applied"],
                                              "cause": "settle:shared:energy_kcal"}
    settled = settled_at(engine, done.occurred_at)
    record = settled.data["body_settlement"]
    assert not [flow for flow in settled.data["resource_flows"] if flow["direction"] == "source"
                and flow["resource"] == "energy_kcal"]
    burned = sum(flow["amount"] for flow in settled.data["resource_flows"] if flow["resource"] == "energy_kcal")
    assert record["after"]["energy_kcal"] == pytest.approx(record["before"]["energy_kcal"] - burned)
    assert energy_now(engine) == pytest.approx(record["after"]["energy_kcal"] + effects["food"]["applied"])
    for row in progress_rows(engine, rest.activity_id):
        assert not {key for key in row.data["activity"]["data"] if "completion" in key or "meal" in key}
    assert not {key for key in engine.context.state.activities[rest.activity_id].data if "completion" in key}


@pytest.mark.parametrize("start_hour,appetite_before,appetite_after", [(8, "有点想吃", "不饿"), (12, "饿", "有点想吃")],
                         ids=["outside-meal-windows", "in-the-lunch-window"])
def test_appetite_after_the_meal_reads_the_new_energy(engine_factory, tmp_path, start_hour, appetite_before,
                                                      appetite_after):
    """能量 1,000 开始做「测试休息」：08:00 做完后饥饿分级从「有点想吃」降到「不饿」，那一秒的唤醒原因含
    felt_band_change；12:00（午饭时段之内）做完后是「有点想吃」，不会到「不饿」。"""
    def at_hour(persona, config):
        config["start_time"] = config["start_time"].replace(hour=start_hour)
        config["stop_time"] = config["stop_time"].replace(hour=start_hour)

    engine = meal_engine(engine_factory, tmp_path, body(1000.0, None, at_hour))
    observed, rest = act(engine, "rest", do_at("家", "测试休息"))
    assert observed["environment_layer"]["body"]["appetite"] == appetite_before
    until_ended(engine, rest.activity_id)
    [done] = fed(engine)
    after = engine.observe(context(engine, "after"))["environment_layer"]["body"]["appetite"]
    assert after == appetite_after
    if appetite_after == "不饿":
        assert "felt_band_change" in settled_at(engine, done.occurred_at).data["wake_reasons"]


def test_a_meal_alone_does_not_offer_a_decision(engine_factory, tmp_path):
    """《吃晚饭》做完而饥饿分级没有跨档、也没有别的原因时，那一秒的共同身体结算 decision_offered 为假。"""
    engine = meal_engine(engine_factory, tmp_path)
    _, dinner = act(engine, "dinner", do_at("家", "做晚饭"))
    until_ended(engine, dinner.activity_id)
    [meal] = fed(engine)
    record = settled_at(engine, meal.occurred_at)
    assert record.data["decision_offered"] is False and record.data["wake_reasons"] == []


def test_the_meal_resets_no_callback_state_and_credits_no_errand(engine_factory, tmp_path):
    """副本里登记收到牛肉面时重置的状态，并预置一项「买牛肉面」的未完成事务：做完供一餐的事之后状态值不变、没有重置
    事件，事务累计量不变。"""
    def callbacks(root):
        write_catalog_rows(root, "callbacks.jsonl", [
            {"state": "noodles", "initial": 0, "cap": 1, "reset_on_goods": ["beef-noodles"]}])

    engine = meal_engine(engine_factory, tmp_path, body(1000.0), callbacks)
    now = engine.context.state.current_time
    errand = ErrandState(errand_id="noodles", name="买牛肉面", product_id="beef-noodles", quantity=1, progress=0.0,
                         memo=None, registered_at=now, remind_at=None, reminded_at=None,
                         origin_action_id="action-noodles", origin_event_id="request-noodles")
    stage_errands(engine, 1, (errand,))
    _, rest = act(engine, "rest", do_at("家", "测试休息"))
    until_ended(engine, rest.activity_id)
    assert len(fed(engine)) == 1
    person = engine.context.state.agents[1]
    assert person.callback_states == {"noodles": 0} and person.errands == {"noodles": errand}
    kinds = {row.data.get("kind") for row in events(engine, "environment_event")}
    assert not kinds & {"callback_effect", "errand_progressed", "errand_completed"}


def test_a_relief_only_completion_lowers_the_needs_by_its_tiers(engine_factory, tmp_path):
    """「测试闲聊」只写 completion_effects 的两项缓解、不凭状态：随时列出；做完时放松与社交需要各减去一小时的档位量（不低于
    0），分录是两条 drain、原因 content:completion，排在这条事件 resource_flows 的最前；没有 food。"""
    change = lambda root: set_content(root, "c-chat", completion_effects={"relaxation_relief": "medium",
                                                                         "social_relief": "low"})
    engine = meal_engine(engine_factory, tmp_path, change=change)
    observed, chat = act(engine, "chat", do_at("家", "测试闲聊"))
    home = next(row for row in observed["tool_layer"]["targets"] if row["name"] == "家")
    row = next(item for item in home["actions"] if item["detail"] == "测试闲聊")
    assert (row["available"], row["note"]) == (True, None)
    until_ended(engine, chat.activity_id)
    [done] = finished(engine, chat.activity_id)
    after = settled_at(engine, done.occurred_at).data["body_settlement"]["after"]
    relaxation, social = min(0.3, after["relaxation_need"]), min(0.1, after["social_need"])
    assert done.data["content"]["completion_effects"] == {
        "food": None, "relaxation_relief": {"tier": "medium", "provided": 0.3, "applied": relaxation},
        "social_relief": {"tier": "low", "provided": 0.1, "applied": social}}
    assert done.data["resource_flows"][:2] == [
        {"resource": "relaxation_need", "owner": "agent:1", "direction": "drain", "amount": relaxation,
         "cause": "content:completion"},
        {"resource": "social_need", "owner": "agent:1", "direction": "drain", "amount": social,
         "cause": "content:completion"}]
    person = engine.context.state.agents[1].body
    assert person.relaxation_need == pytest.approx(after["relaxation_need"] - relaxation, abs=1e-12)
    assert person.social_need == pytest.approx(after["social_need"] - social, abs=1e-12)
    assert energy_now(engine) == after["energy_kcal"] and fed(engine) == []


#: 人物读到的五个提示层。
PROMPT_LAYERS = ("tool_layer", "todo_layer", "environment_layer", "memory_layer", "progress_layer")


def test_relief_members_change_no_prompt_layer(engine_factory, tmp_path):
    """同一环境的两份副本只差两项缓解成员（「测试闲聊」写了 completion_effects 的缓解档位）：开始时与做到一半时，人物读到的
    各层逐字节相同；完成效果只在做完时体现在体感上，不写进任何提示层。"""
    relieved = meal_engine(engine_factory, tmp_path / "relieved", every_five_minutes, lambda root: set_content(
        root, "c-chat", completion_effects={"relaxation_relief": "high", "social_relief": "low"}))
    plain = meal_engine(engine_factory, tmp_path / "plain", every_five_minutes)
    seen = []
    for engine in (relieved, plain):
        observed, _ = act(engine, "chat", do_at("家", "测试闲聊"))
        assert advance_until_ready(engine)
        midway = engine.observe(context(engine, "midway"))
        seen.append([{key: layer[key] for key in PROMPT_LAYERS} for layer in (observed, midway)])
    assert seen[0] == seen[1]
