"""真实计划请求容量、提醒登记和个人事务变更的边界。"""
from worldmock.recording_engine.projections.commerce.errand_evidence import evidence_rows as _errands_evidence_rows

from datetime import datetime
from math import ceil
from zoneinfo import ZoneInfo

import pytest

from tests.integration.runtime.current_contract_fixtures import inputs
from tests.life.conftest import errand_add, errand_update, plan_day_response
from worldmock.foundation.errors import BusinessRuleError, IntegrityError, ProtocolError
from worldmock.recording_engine.events.models import EventDraft
from worldmock.configuration.inputs.models import ModelMetadata
from worldmock.simulation_engine.scheduling.plans import errands
from worldmock.simulation_engine.scheduling.plans.day_planning import (
    PLAN_DAY_PROMPT,
    PlanDayOutputError,
    agents_missing_plan,
    fit_plan_day,
    parse_plan_day,
    prepare_plan_day,
    registration,
)
from worldmock.state.planning.errands import ErrandState
from worldmock.state.interaction.requests import PlanDayRequest
from worldmock.agents.prompt.prompt_capacity import request_token_count


def note(state, identity="note:errand:0", product="tissue"):
    return ErrandState(errand_id=identity, name="补充纸巾", product_id=product, quantity=2, progress=0.0,
        memo="下班后购买", registered_at=state.current_time, remind_at=None, reminded_at=None,
        origin_action_id="note", origin_event_id="noted-event")


def plan_request(clock):
    return PlanDayRequest(goals=[{"intent_kind": "休息", "goal": "浏览一些内容",
                                 "remind_time": clock, "around_time": None}])


def test_plan_capacity_removes_oldest_real_experience_and_preserves_other_input():
    frozen = inputs()
    payload = {"人物": {"名字": frozen.persona.name}, "个人事务": [{"名称": "补纸巾"}]}
    sentences = ["这是已经核验的旧经历。" * 1800, "最后一次已经核验的经历。"]
    short = prepare_plan_day({**payload, "已核验经历": sentences[1:]}, frozen.config.model.requested_model)
    full = prepare_plan_day({**payload, "已核验经历": sentences}, frozen.config.model.requested_model)
    short["max_tokens"] = full["max_tokens"] = 1000
    threshold = (request_token_count(short) + request_token_count(full)) / 2
    metadata = ModelMetadata(requested_model=frozen.config.model.requested_model,
        metadata_url=frozen.config.model.metadata_url, context_window=ceil(threshold / 0.85), max_output_tokens=1000)
    request, retained = fit_plan_day(payload, sentences, frozen.config.model.requested_model, metadata)
    import json
    sent = json.loads(request["messages"][1]["content"])
    assert retained == 1 and sent == {**payload, "已核验经历": sentences[1:]}
    assert sentences[0].startswith("这是已经核验的旧经历") and request["max_tokens"] == 1000


def test_plan_day_keeps_its_own_eight_thousand_output_budget():
    """零点日计划用自己的 8000 输出上限，思考关闭；不随决策的 16000 改变。"""
    frozen = inputs()
    request = prepare_plan_day({"已核验经历": []}, frozen.config.model.requested_model)
    assert request["max_tokens"] == 8000
    assert request["extra_body"] == {"thinking": {"type": "disabled"}}


def test_plan_day_template_takes_the_day_kind_from_the_arrangement():
    """零点计划模板写明上不上班只看上班安排的 day、不看星期几：这一整句是模板的最后一行，「工作安排是固定世界事实」
    只出现一次；零点计划请求的系统消息就是这份模板。"""
    sentence = ("上班安排的 day 是今天上不上班的唯一依据，不看星期几：休息日没有通勤、打卡和上班，即使是星期一到星期五；"
                "工作日照 hours 与 place 上班，即使是周末。工作安排是固定世界事实，今日目标不能改变班次；"
                "不要安排在班次中离开工作地点，已声明休息时段除外。")
    assert sentence in PLAN_DAY_PROMPT
    assert PLAN_DAY_PROMPT.split("\n")[-1] == sentence
    assert PLAN_DAY_PROMPT.count("工作安排是固定世界事实") == 1
    assert prepare_plan_day({}, "test-model")["messages"][0]["content"] == PLAN_DAY_PROMPT


def test_plan_capacity_cannot_trim_fixed_input_when_no_experiences_remain():
    frozen = inputs()
    metadata = ModelMetadata(requested_model=frozen.config.model.requested_model,
        metadata_url=frozen.config.model.metadata_url, context_window=500, max_output_tokens=100)
    with pytest.raises(RuntimeError, match="plan_day request capacity exceeded"):
        fit_plan_day({"人物": {"背景": "必须保留的背景。" * 1000}}, [], metadata.requested_model, metadata)


@pytest.mark.parametrize("clock", ["07:59", "08:00", "00:00"])
def test_plan_reminder_must_be_later_than_generation_and_before_local_midnight(engine_factory, clock):
    engine = engine_factory()
    request = plan_request(clock)
    raw = plan_day_response(goals=[goal.model_dump(mode="json") for goal in request.goals])
    with pytest.raises(PlanDayOutputError, match="later than the generation time 08:00"):
        parse_plan_day(raw, engine.context.state.current_time, engine.context.state.timezone)
    before = engine.context.state.model_dump(mode="json")
    with pytest.raises(ProtocolError, match="Generated day plan is invalid"):
        registration(engine.context.state, 1, request, ((), ()), engine.context.state.timezone)
    assert engine.context.state.model_dump(mode="json") == before


def test_duplicate_local_day_registration_preserves_the_first_plan(engine_factory):
    engine = engine_factory()
    state, plan, _ = registration(engine.context.state, 1, plan_request("09:00"), ((), ()),
                                  engine.context.state.timezone)
    with pytest.raises(ProtocolError, match="already generated for this local day"):
        registration(state, 1, plan_request("10:00"), ((), ()), state.timezone)
    assert state.agents[1].day_plan == plan


def test_run_end_has_no_missing_plan_and_duplicate_intents_are_schema_errors(engine_factory):
    engine = engine_factory()
    stopped = engine.context.state.model_copy(update={"current_time": engine.context.state.end_time})
    assert agents_missing_plan(stopped, stopped.timezone) == []
    goal = {"intent_kind": "休息", "goal": "浏览一些内容", "remind_time": None, "around_time": None}
    with pytest.raises(PlanDayOutputError, match="intent_kind"):
        parse_plan_day(plan_day_response(goals=[goal, goal]), engine.context.state.current_time, stopped.timezone)


def test_future_errand_clock_stays_in_the_current_local_date():
    now = int(datetime.fromisoformat("2026-01-05T08:00:00+08:00").timestamp())
    future = errands.remind_epoch("09:00", now, "Asia/Shanghai")
    assert datetime.fromtimestamp(future, ZoneInfo("Asia/Shanghai")).isoformat() == "2026-01-05T09:00:00+08:00"


@pytest.mark.parametrize("moment,clock,expected", [
    ("2026-01-05T08:00:00+08:00", "08:00", "2026-01-06T08:00:00+08:00"),
    ("2026-03-07T12:00:00-05:00", "11:00", "2026-03-08T11:00:00-04:00"),
])
def test_equal_or_past_errand_clock_advances_one_local_date_across_daylight_saving(moment, clock, expected):
    zone = "Asia/Shanghai" if moment.endswith("+08:00") else "America/New_York"
    now = int(datetime.fromisoformat(moment).timestamp())
    reminded = errands.remind_epoch(clock, now, zone)
    assert datetime.fromtimestamp(reminded, ZoneInfo(zone)).isoformat() == expected


def test_errand_application_refuses_unresolved_names_without_partial_addition(engine_factory):
    engine = engine_factory()
    agent = engine.context.state.agents[1]
    observed = errands.bindings(agent, engine.context.catalog.goods)
    changes = [errand_add("补充纸巾", "纸巾"), errand_update("不存在的事务")]
    issues = errands.change_issues(changes, observed)
    refusal = errands.change_refusal(issues)
    before = agent.model_dump(mode="json")
    origin = errands.ErrandOrigin("bad-change", "request", engine.context.state.current_time, "Asia/Shanghai")
    with pytest.raises(BusinessRuleError, match="ACTION_REQUEST_INVALID") as failure:
        errands.apply_changes(agent, changes, observed, origin)
    assert failure.value.public_reason == refusal.reason
    assert "exact name" in refusal.fix.lower() and agent.model_dump(mode="json") == before


def test_stale_observed_errand_cannot_change_the_current_person_state(engine_factory):
    engine = engine_factory()
    agent = engine.context.state.agents[1].model_copy(deep=True)
    remembered = note(engine.context.state)
    observed_agent = agent.model_copy(update={"errands": {remembered.errand_id: remembered}})
    observed = errands.bindings(observed_agent, engine.context.catalog.goods)
    origin = errands.ErrandOrigin("stale-change", "request", engine.context.state.current_time, "Asia/Shanghai")
    with pytest.raises(BusinessRuleError, match="ACTION_REQUEST_INVALID"):
        errands.apply_changes(agent, [errand_update(remembered.name)], observed, origin)
    assert agent.errands == {}


@pytest.mark.parametrize("recipient", [None, True, 0, -1])
def test_goods_receipt_requires_an_actual_simulated_recipient(recipient):
    with pytest.raises(IntegrityError, match="no simulated recipient"):
        _errands_evidence_rows("environment_event", recipient,
                              {"kind": "goods_received", "goods_id": "tissue", "quantity": 1})


def test_errand_only_accepts_receipts_after_its_registration_in_the_same_commit(engine_factory):
    engine = engine_factory()
    state = engine.context.state.model_copy(deep=True)
    remembered = note(state)
    state.agents[1].errands = {remembered.errand_id: remembered}
    receipt = EventDraft(event_type="environment_event", occurred_at=state.current_time, agent_id=1,
        visibility="agent", data={"kind": "goods_received", "goods_id": "tissue", "quantity": 1})
    registration_event = EventDraft(event_type="environment_event", occurred_at=state.current_time, agent_id=1,
        visibility="system", data={"kind": "errand_registered", "errand": remembered.model_dump(mode="json")})
    unchanged, ignored = errands.settle(state, [receipt, registration_event])
    assert unchanged is state and ignored == []
    changed, accepted = errands.settle(state, [registration_event, receipt])
    assert changed.agents[1].errands[remembered.errand_id].progress == 1
    assert len(accepted) == 1 and accepted[0].caused_by == [receipt.event_id]
