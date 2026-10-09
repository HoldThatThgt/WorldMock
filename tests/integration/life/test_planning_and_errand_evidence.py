"""真实控制请求和后台计划只由当前证据登记事务与经历。"""

import copy

import pytest

from tests.life.conftest import (
    advance_until_ready, conclude_request, errand_cancel, errand_update, plan_day_response, stage_errands, start_activity,
)
from tests.life.environment_runtime import act, events, finish_activity
from tests.unit.life.test_planning_and_errand_boundaries import note
from worldmock.errors import ProtocolError
from worldmock.life.day_plan_inputs import plan_inputs
from worldmock.runtime import settle_pending_day_plans


def test_control_updates_and_cancels_open_errands_with_one_request_cause(engine_factory):
    engine = engine_factory()
    first = note(engine.context.state)
    other = first.model_copy(update={"errand_id": "note:errand:1", "name": "买一本笔记本",
                                     "product_id": "online-notebook"})
    stage_errands(engine, 1, [first, other])
    _, started = act(engine, "browse-before-change", start_activity("interact", "家"))
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
    _, travel = act(engine, "actual-travel", start_activity("walk", "公司"))
    assert travel.status == "in_progress"
    finish_activity(engine, travel, "arrived-office")
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
