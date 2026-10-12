"""真实控制请求和后台计划只由当前证据登记事务与经历。"""

import copy
import json
import re
from datetime import timedelta

import pytest

from tests.life.conftest import (
    advance_until_ready, conclude_request, do_at, errand_cancel, errand_update, plan_day_response, settle_day_plans,
    stage_errands, travel, use_app,
)
from tests.life.environment_inputs import copy_environment
from tests.life.environment_runtime import act, events, finish_activity, settle_decision
from tests.life.prerequisite_inputs import add_rows
from tests.unit.life.test_planning_and_errand_boundaries import note
from worldmock.errors import ProtocolError
from worldmock.life.day_plan_inputs import plan_inputs
from worldmock.runtime import settle_pending_day_plans

#: 经历句开头的起止：「HH:MM–HH:MM」，跨日时各带「M月D日 」。
_SPAN = re.compile(r"^(?:\d+月\d+日 )?\d\d:\d\d–(?:\d+月\d+日 )?\d\d:\d\d ")


def _dinner_environment(tmp_path):
    """夹具副本：住处加两集各 10 分钟的系列「做晚饭」（《备菜做饭》《吃晚饭》）。"""
    root = copy_environment(tmp_path / "dinner-environment")
    source = {"kind": "synthetic", "notice": "测试用合成摘要", "url": None}
    add_rows(root, "content-catalog.jsonl", "content_id", *(
        {"content_id": f"c-dinner-{number}", "duration_seconds": 600, "exertion": "light",
         "location_kinds": ["home"], "pushable": False, "relaxation_relief": "none",
         "series": {"episode": number, "title": "做晚饭"}, "social_relief": "none", "summary": f"{title}。",
         "summary_source": source, "title": title} for number, title in enumerate(("备菜做饭", "吃晚饭"), start=1)))
    return root


def _lived_day(engine):
    """真实引擎在家用一次哔哩哔哩、做一次「测试休息」和一次「做晚饭」系列，各自做到头并收尾；返回用 app 看过的标题。"""
    _, watched = act(engine, "watch", use_app("哔哩哔哩"))
    finish_activity(engine, watched, "watch")
    for label, unit in (("rest", "测试休息"), ("dinner", "做晚饭")):
        _, started = act(engine, label, do_at("家", unit))
        assert started.status == "in_progress", started.error_code
        finish_activity(engine, started, label)
    played = engine.context.state.activities[watched.activity_id].data["played"]
    return {engine.context.catalog.contents[row["content_id"]].title for row in played}


def _content_tails(sentences):
    """内容经历句去掉开头的起止之后的写法，按先后；到达句与收货句不在其中。"""
    return [_SPAN.sub("", sentence, count=1) for sentence in sentences if _SPAN.match(sentence)
            and ("使用" in sentence or "在家：" in sentence)]


def test_control_updates_and_cancels_open_errands_with_one_request_cause(engine_factory):
    engine = engine_factory()
    first = note(engine.context.state)
    other = first.model_copy(update={"errand_id": "note:errand:1", "name": "买一本笔记本",
                                     "product_id": "online-notebook"})
    stage_errands(engine, 1, [first, other])
    _, started = act(engine, "browse-before-change", do_at("家", "测试休息"))
    assert advance_until_ready(engine)
    current = engine.context.state.activities[started.activity_id]
    _, changed = act(engine, "change-open-errands", conclude_request(current.name, errands=[
        errand_update(first.name, memo="明天记得购买", remind_time="08:00"),
        errand_cancel(other.name, "暂时不需要笔记本")]), "conclude_activity")
    assert changed.status == "completed"
    remembered = engine.context.state.agents[1].errands[first.errand_id]
    assert remembered.memo == "明天记得购买" and remembered.remind_at > engine.context.state.current_time
    assert remembered.name == first.name and remembered.product_id == first.product_id and remembered.quantity == 2
    assert other.errand_id not in engine.context.state.agents[1].errands
    facts = events(engine)
    request = next(item for item in facts if item.action_id == changed.action_id and item.event_type == "action_requested")
    records = [item for item in facts if item.data.get("kind") in {"errand_updated", "errand_cancelled"}]
    assert [item.data["kind"] for item in records] == ["errand_updated", "errand_cancelled"]
    assert all(item.caused_by == [request.event_id] and item.visibility == "system" for item in records)
    assert records[0].data["errand"]["memo"] == remembered.memo
    assert records[1].data["errand"]["name"] == other.name and records[1].data["reason"] == "暂时不需要笔记本"


def test_plan_inputs_read_verified_travel_without_changing_world_or_store(engine_factory):
    engine = engine_factory()
    _, departure = act(engine, "actual-travel", travel("公司"))
    assert departure.status == "in_progress"
    finish_activity(engine, departure, "arrived-office")
    before = engine.context.state.model_dump(mode="json")
    event_sequence = engine.context.store.last_event_seq
    planned = plan_inputs(engine.context, engine.observations, engine.midnight_rhythm_pending, 1)
    assert any("步行" in sentence and "公司" in sentence for sentence, _ in planned.experiences)
    known = {item.event_id for item in events(engine)}
    assert all(source in known for _, source in planned.experiences)
    assert planned.payload["上班安排"]["place"] == "公司"
    assert engine.context.state.model_dump(mode="json") == before
    assert engine.context.store.last_event_seq == event_sequence


class PlanningTransport:
    def __init__(self, model_name, responses=(), error=None):
        self.model_name, self.responses, self.error = model_name, list(responses), error
        self.requests = []

    async def call(self, request):
        self.requests.append(copy.deepcopy(request))
        if self.error is not None:
            raise self.error
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_plan_transport_failure_records_request_and_failure_before_propagating(engine_factory):
    engine = engine_factory()
    client = PlanningTransport(engine.context.inputs.config.model.requested_model,
                               error=OSError("Model transport unavailable"))
    before = engine.context.state.model_dump(mode="json")
    with pytest.raises(OSError, match="Model transport unavailable"):
        await settle_pending_day_plans(engine, client)
    facts = events(engine)
    requests = [item for item in facts if item.event_type == "model_request"]
    responses = [item for item in facts if item.event_type == "model_response"]
    assert len(client.requests) == len(requests) == len(responses) == 1
    assert responses[0].data["phase"] == "failure" and responses[0].data["purpose"] == "plan_day"
    assert responses[0].caused_by == [requests[0].event_id] and "Model transport unavailable" in responses[0].data["error"]
    assert engine.context.state.model_dump(mode="json") == before


@pytest.mark.asyncio
async def test_invalid_reminder_uses_only_its_validation_budget_and_retains_correction_evidence(engine_factory):
    def mutate(persona, config):
        config["model"]["day_plan_validation_max_retries"] = 1

    engine = engine_factory(mutate=mutate)
    invalid = plan_day_response(goals=[{"intent_kind": "休息", "goal": "浏览一些内容",
                                       "remind_time": "08:00", "around_time": None}])
    client = PlanningTransport(engine.context.inputs.config.model.requested_model, [invalid, invalid])
    with pytest.raises(ProtocolError, match="plan_day output validation retries exhausted"):
        await settle_pending_day_plans(engine, client)
    assert len(client.requests) == 2
    first, corrected = client.requests
    assert corrected["tools"] == first["tools"] and corrected["messages"][:2] == first["messages"]
    assert "later than the generation time 08:00" in corrected["messages"][-1]["content"]
    requests = [item for item in events(engine) if item.event_type == "model_request"]
    responses = [item for item in events(engine) if item.event_type == "model_response"]
    assert len(requests) == len(responses) == 2 and requests[1].caused_by == [responses[0].event_id]
    assert engine.context.state.agents[1].plan_submitted_dates == set()


def test_plan_inputs_word_app_use_and_location_activities_like_the_timeline(engine_factory, tmp_path):
    """用完 app、在地点做完一件事和一个系列之后，零点计划读到的内容经历句是「<起>–<止> 使用哔哩哔哩《……》」「<起>–<止>
    在家：测试休息」「<起>–<止> 在家：做晚饭《<集>》」，系列逐集一句；不再写「进行《……》」。"""
    engine = engine_factory(environment_root=_dinner_environment(tmp_path))
    titles = _lived_day(engine)
    planned = plan_inputs(engine.context, engine.observations, engine.midnight_rhythm_pending, 1)
    tails = _content_tails([sentence for sentence, _ in planned.experiences])
    watched = [tail for tail in tails if tail.startswith("使用")]
    assert watched and {tail.removeprefix("使用哔哩哔哩《").removesuffix("》") for tail in watched} == titles
    located = [tail for tail in tails if tail.startswith("在")]
    # 一条内容被结算边界切成几段仍是几句：相邻的同一句合并后依次是休息、做晚饭的两集。
    assert [tail for index, tail in enumerate(located) if index == 0 or tail != located[index - 1]] == [
        "在家：测试休息", "在家：做晚饭《备菜做饭》", "在家：做晚饭《吃晚饭》"]
    assert not [sentence for sentence, _ in planned.experiences if "进行《" in sentence]
    first = next(sentence for sentence, _ in planned.experiences if "使用哔哩哔哩《" in sentence)
    assert re.fullmatch(r"\d\d:\d\d–\d\d:\d\d 使用哔哩哔哩《[^》]+》", first)


def test_midnight_plan_request_carries_the_same_experience_sentences(engine_factory, tmp_path):
    """推进到零点：落库的零点计划模型请求里，「已核验经历」正是零点那一刻按同一写法得出的经历句。"""
    def to_one_am(persona, config):
        config["stop_time"] = config["start_time"].replace(hour=1) + timedelta(days=1)

    engine = engine_factory(environment_root=_dinner_environment(tmp_path), mutate=to_one_am)
    _lived_day(engine)
    counter = 0
    while not engine.midnight_rhythm_pending:
        counter += 1
        state = engine.scheduling_state()
        if state.ready_agent_ids:
            settle_decision(engine, f"day-{counter}")
        else:
            engine.advance(int(state.next_event_at.timestamp()) - engine.context.state.current_time,
                           state.next_event_at)
    planned = plan_inputs(engine.context, engine.observations, True, 1)
    expected = [sentence for sentence, _ in planned.experiences]
    assert _content_tails(expected)
    settle_day_plans(engine)
    [request] = [row for row in events(engine, "model_request") if row.data.get("purpose") == "plan_day"]
    payload = json.loads(request.data["request"]["messages"][1]["content"])
    assert payload["已核验经历"] == expected
