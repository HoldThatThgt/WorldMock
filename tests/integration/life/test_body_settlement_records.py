"""身体结算记录的「结算后」只反映这一笔自己的增减：同一秒的收货不进这条记录，不适脉冲对着记账之后的身体算并落在
自己那条记录上，业务拒绝的放松脉冲记在这次拒绝自己的结果里（docs/design/05-recording-engine.md「事件与来源」）。"""

import json

import pytest

from tests.life.conftest import advance_next, do_at, purchase, travel
from tests.life.environment_runtime import act, act_group, events, finish_activity, reach
from worldmock.life.body import last_flows

#: 身体结算记录自己记账的四项资源。
RESOURCES = ("energy_kcal", "sleep_deficit_hours", "relaxation_need", "social_need")
PULSE = "relaxation:event"


def records(engine, kind):
    """已提交的某一类环境事件。"""
    return [row for row in events(engine, "environment_event") if row.data.get("kind") == kind]


def balanced(record):
    """四项资源都满足「结算前」加上本记录的 source 分录、减去 drain 分录等于「结算后」。"""
    settlement, flows = record.data["body_settlement"], record.data["resource_flows"]
    for name in RESOURCES:
        delta = sum(flow["amount"] if flow["direction"] == "source" else -flow["amount"]
                    for flow in flows if flow["resource"] == name)
        assert settlement["after"][name] == pytest.approx(settlement["before"][name] + delta, abs=1e-9), name


def causes(record):
    """一条记录上全部分录的原因。"""
    return [flow["cause"] for flow in record.data["resource_flows"]]


def settlement_and_receipt_at(engine, moment):
    """同一秒的那条身体结算记录与那条收货记录。"""
    [settled] = [row for row in records(engine, "body_settlement") if row.occurred_at == moment]
    [received] = [row for row in records(engine, "goods_received") if row.occurred_at == moment]
    return settled, received


def assert_receipt_kept_out(engine, settled, received):
    """收货不进身体结算记录；世界里的能量等于「结算后」加收货实得。"""
    balanced(settled)
    assert [(flow["resource"], flow["direction"], flow["amount"]) for flow in received.data["resource_flows"]] == [
        ("energy_kcal", "source", 540.0)]
    after = settled.data["body_settlement"]["after"]["energy_kcal"]
    assert engine.context.state.agents[1].body.energy_kcal == pytest.approx(after + received.data["applied"])


def test_delivery_received_in_the_settled_second_stays_out_of_the_body_settlement(engine_factory):
    engine = engine_factory()
    _, ordered = act(engine, "noodles", purchase("牛肉面"))
    assert ordered.status == "in_progress", ordered.error_code
    finish_activity(engine, ordered, "noodles")
    [order] = engine.context.state.deliveries.values()
    reach(engine, order.expected_at)
    settled, received = settlement_and_receipt_at(engine, order.expected_at)
    assert_receipt_kept_out(engine, settled, received)


def test_store_purchase_completed_in_the_settled_second_stays_out_of_the_body_settlement(engine_factory):
    def at_store(persona, config):
        persona["initial_location"] = "四季便利"
        persona["body"]["initial_energy_kcal"] = 1000.0

    engine = engine_factory(mutate=at_store)
    _, bought = act(engine, "rice-ball", purchase("饭团"))
    assert bought.status == "in_progress", bought.error_code
    finish_activity(engine, bought, "rice-ball")
    [received] = records(engine, "goods_received")
    settled, received = settlement_and_receipt_at(engine, received.occurred_at)
    assert_receipt_kept_out(engine, settled, received)


def hungry_at_work(relaxation):
    """人物在公司、能量见底（清醒且饥饿即不适），初始放松需要给定。"""
    def mutate(persona, config):
        persona["initial_location"] = "公司"
        persona["initial_relaxation_need"] = relaxation
        persona["body"]["initial_energy_kcal"] = 0.0
    return mutate


def test_discomfort_pulse_is_recorded_on_its_own_body_settlement(engine_factory):
    engine = engine_factory(mutate=hungry_at_work(0.0))
    started = engine.context.state.current_time
    _, result = act(engine, "hungry-work", do_at("公司", "季度销售数据整理"))
    assert result.status == "in_progress", result.error_code
    reach(engine, started + 3 * 3600)
    assert not records(engine, "goods_received")
    last = records(engine, "body_settlement")[-1]
    assert last.occurred_at == engine.context.state.current_time and PULSE in causes(last)
    balanced(last)
    after = last.data["body_settlement"]["after"]["relaxation_need"]
    assert engine.context.state.agents[1].body.relaxation_need == pytest.approx(after, abs=1e-12)


def test_discomfort_pulse_is_billed_against_the_settled_body_not_the_received_drink(engine_factory):
    engine = engine_factory(mutate=hungry_at_work(0.98))
    started = engine.context.state.current_time
    _, result = act(engine, "hungry-work", do_at("公司", "季度销售数据整理"))
    assert result.status == "in_progress", result.error_code
    reach(engine, started + 2 * 3600 + 1800)
    _, ordered = act(engine, "tea", purchase("柠檬茶"))
    assert ordered.status == "in_progress", ordered.error_code
    [order] = engine.context.state.deliveries.values()
    reach(engine, order.expected_at)
    settled, received = settlement_and_receipt_at(engine, order.expected_at)
    balanced(settled)
    flows = settled.data["resource_flows"]
    shared = sum(flow["amount"] if flow["direction"] == "source" else -flow["amount"]
                 for flow in flows if flow["resource"] == "relaxation_need" and flow["cause"] != PULSE)
    pulse = sum(flow["amount"] for flow in flows if flow["cause"] == PULSE)
    settled_before_pulse = settled.data["body_settlement"]["before"]["relaxation_need"] + shared
    assert pulse == pytest.approx(1.0 - settled_before_pulse, abs=1e-12)
    assert (PULSE in causes(settled)) is (pulse > 0)
    assert settled.data["body_settlement"]["after"]["relaxation_need"] == pytest.approx(1.0)
    relieved = settled_before_pulse - received.data["applied"]
    assert received.data["applied"] == 0.1
    assert engine.context.state.agents[1].body.relaxation_need == pytest.approx(relieved + pulse, abs=1e-12)


def rejection_events(engine, action_id):
    """一条动作写下的拒绝事件与结果事件。"""
    [rejected] = [row for row in events(engine, "action_rejected") if row.action_id == action_id]
    [result] = [row for row in events(engine, "action_result") if row.action_id == action_id]
    return rejected, result


def test_rejection_pulse_flows_are_written_on_the_first_action_result_and_not_carried_forward(engine_factory):
    engine = engine_factory()
    _, refused = act(engine, "home-again", travel("家"))
    assert refused.status == "rejected", refused.error_code
    assert last_flows() == ()
    rejected, result = rejection_events(engine, "action-home-again")
    assert result.data["resource_flows"] == [{"resource": "relaxation_need", "owner": "agent:1",
                                              "direction": "source", "amount": pytest.approx(0.05),
                                              "cause": PULSE}]
    assert "resource_flows" not in rejected.data
    observed, started = act(engine, "rest", do_at("家", "测试休息"))
    assert started.status == "in_progress", started.error_code
    assert "resource_flows" not in json.dumps(observed, ensure_ascii=False)
    advance_next(engine)
    following = records(engine, "body_settlement")[-1]
    assert following.occurred_at == engine.context.state.current_time
    assert PULSE not in causes(following)
    balanced(following)


def test_a_rejected_pair_of_start_requests_writes_the_pulse_once(engine_factory):
    engine = engine_factory()
    _, results = act_group(engine, "pair", travel("家"), purchase("柠檬茶"))
    assert [item.status for item in results] == ["rejected", "rejected"]
    first_rejected, first_result = rejection_events(engine, "action-pair-1")
    second_rejected, second_result = rejection_events(engine, "action-pair-2")
    assert first_rejected.data == second_rejected.data and "resource_flows" not in first_rejected.data
    assert causes(first_result) == [PULSE] and "resource_flows" not in second_result.data
    assert last_flows() == ()


def test_a_rejection_without_a_pulse_writes_no_flows(engine_factory):
    engine = engine_factory()
    act(engine, "first", travel("家"))
    before = engine.context.state.agents[1].body.relaxation_need
    _, results = act_group(engine, "second", travel("家"), purchase("柠檬茶"))
    assert [item.status for item in results] == ["rejected", "rejected"]
    for action_id in ("action-second-1", "action-second-2"):
        assert "resource_flows" not in rejection_events(engine, action_id)[1].data
    assert engine.context.state.agents[1].body.relaxation_need == before


def test_replaying_a_rejected_request_applies_no_pulse_and_writes_no_flows(engine_factory):
    engine = engine_factory()
    act(engine, "home-again", travel("家"))
    seen = engine.context.store.last_event_seq
    before = engine.context.state.agents[1].body.relaxation_need
    _, replayed = act(engine, "replay", travel("家"), action_id="action-home-again")
    assert replayed.status == "rejected"
    written = [row for row in events(engine) if row.event_seq > seen]
    assert any(row.data.get("reused") for row in written)
    assert not [row for row in written if "resource_flows" in row.data]
    assert engine.context.state.agents[1].body.relaxation_need == before
    assert last_flows() == ()
