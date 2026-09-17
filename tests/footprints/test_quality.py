import json

import pytest

from worldmock.errors import IntegrityError
from worldmock.footprints.models import (
    DECISION_OFFERED_FIELD,
    INNER_MONOLOGUE_FIELD,
    WAKE_REASONS_FIELD,
    Event,
)
from worldmock.footprints.quality import build_quality_report


def _event(sequence, event_type, *, occurred_at=None, run_id="run", wall_ms=None, **fields):
    data = fields.pop("data", {})
    return Event(
        event_id=f"event-{sequence}", event_type=event_type,
        occurred_at=100 + sequence if occurred_at is None else occurred_at,
        run_id=run_id, event_seq=sequence,
        recorded_at=fields.pop("recorded_at", "2026-09-15T07:00:00+00:00"),
        wall_elapsed_ms=sequence * 100 if wall_ms is None else wall_ms,
        state_version_before=0, state_version_after=0, data=data, **fields,
    )


def _manifest(start=100, end=1100):
    return {"run_id": "run", "source_group_id": "group",
            "window": {"start": start, "end": end, "timezone": "Asia/Shanghai"}}


def _tool_response(call_id, references, usage=None):
    memory = {"text": "summary", "type": "observation", "importance": 0.5}
    if references is not None:
        memory["refs"] = references
    arguments = {"memories": [memory]}
    return {
        "phase": "response", "call_id": call_id, "attempt": 1, "error": None,
        "response": {
            "model": "deepseek-flash", "usage": usage,
            "choices": [{"message": {"tool_calls": [{
                "function": {"name": "finish", "arguments": json.dumps(arguments)},
            }]}}],
        },
    }


def _usage():
    return {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
            "prompt_cache_hit_tokens": 40, "prompt_cache_miss_tokens": 60}


def _activity(identity, kind, elapsed, status="completed", **fields):
    return {"activity_id": identity, "agent_id": 1, "activity_type": kind,
            "elapsed_seconds": elapsed, "status": status, **fields}


def test_quality_report_covers_run_behavior_usage_cost_and_speedup():
    events = [
        _event(1, "model_request", agent_id=1, decision_id="decision-1", data={"call_id": "call-1"}),
        _event(2, "model_response", agent_id=1, decision_id="decision-1",
               data=_tool_response("call-1", None, _usage())),
        _event(3, "memory_updated", agent_id=1, decision_id="decision-1",
               data={"episode_ids": ["memory-1"]}),
        _event(4, "model_request", agent_id=1, decision_id="decision-2", data={"call_id": "call-2"}),
        _event(5, "model_response", agent_id=1, decision_id="decision-2",
               data=_tool_response("call-2", ["memory-1"], _usage())),
        _event(6, "memory_updated", agent_id=1, decision_id="decision-2",
               data={"episode_ids": ["memory-2"]}),
        _event(7, "action_requested", agent_id=1, action_id="action-1"),
        _event(8, "action_result", agent_id=1, action_id="action-1", data={"status": "completed"}),
        _event(9, "action_requested", agent_id=1, action_id="action-2"),
        _event(10, "action_rejected", agent_id=1, action_id="action-2",
               data={"error_code": "RESOURCE_BUSY"}),
        _event(11, "action_result", agent_id=1, action_id="action-2", data={"status": "rejected"}),
        _event(12, "activity_progress", data={
            "activity": {"kind": "work"},
            "body_settlement": {"duration_seconds": 100, "work_efficiency": 0.8},
        }),
        _event(13, "activity_progress", data={
            "activity": {"kind": "consume"},
            "consumption": {"food_settlement": {"duration_seconds": 50, "food_efficiency": 0.9}},
        }),
        _event(14, "run_ended", occurred_at=1100, wall_ms=100_000),
    ]
    activities = [
        _activity("watch-1", "watch", 600, target_id="show"),
        _activity("watch-2", "watch", 300, "cancelled", target_id="show", interruption_reason="bored"),
        _activity("game-1", "play_game", 200),
        _activity("work-1", "work", 100),
        _activity("meal-1", "consume", 50, "truncated", interruption_reason="cutoff"),
    ]

    report = build_quality_report(events, activities, _manifest())

    assert report["run_outcome"]["status"] == "completed"
    assert report["actions"]["result_status_counts"]["rejected"] == 1
    assert report["actions"]["business_rejection_codes"] == {"RESOURCE_BUSY": 1}
    assert report["activities"]["completed_activities"] == 3
    assert report["activities"]["terminal_interruptions"] == 2
    assert report["content_contact"]["contact_seconds"] == 1100
    assert report["content_contact"]["repeat_watch"]["repeated_sessions"] == 1
    assert report["conversion_efficiency"]["work"]["weighted_mean"] == 0.8
    assert report["conversion_efficiency"]["food"]["weighted_mean"] == 0.9
    assert report["memory_references"] == {
        "memory_update_events": 2, "persisted_episode_ids": 2,
        "references_checked": 1, "problem_count": 0, "problems": [],
    }
    assert report["model_usage"]["token_usage"]["complete_tokens"] == {
        "input_tokens": 200, "output_tokens": 40, "total_tokens": 240,
    }
    assert report["model_usage"]["estimated_cost"]["complete_estimate_usd"] == 0.00008448
    assert report["acceleration"]["speedup"] == 10.0


def test_missing_measurements_remain_unknown_instead_of_becoming_zero():
    events = [
        _event(1, "model_request", data={"call_id": "call"}),
        _event(2, "model_response", data=_tool_response("call", [], None)),
        _event(3, "run_ended", occurred_at=200),
    ]
    activities = [_activity("watch-known", "watch", 20, target_id="show"),
                  _activity("watch-missing", "watch", None, target_id="show")]

    report = build_quality_report(events, activities, _manifest(end=200))

    contact = report["content_contact"]
    assert contact["contact_seconds"] is None
    assert contact["known_contact_seconds"] == 20
    assert contact["sessions_missing_duration"] == 1
    assert report["activities"]["known_elapsed_seconds"] == 20
    assert report["activities"]["elapsed_seconds_complete"] is False
    assert report["model_usage"]["token_usage"]["known_tokens"] is None
    assert report["model_usage"]["token_usage"]["complete_tokens"] is None
    assert report["model_usage"]["token_usage"]["provider_responses_missing_usage"] == 1
    assert report["model_usage"]["estimated_cost"]["known_estimate_usd"] is None
    assert report["conversion_efficiency"]["work"]["weighted_mean"] is None
    assert report["conversion_efficiency"]["food"]["weighted_mean"] is None


def test_actual_unpriced_model_keeps_tokens_but_does_not_invent_cost():
    response = _tool_response("call", [], _usage())
    response["response"]["model"] = "another-model"
    events = [_event(1, "model_request", data={"call_id": "call"}),
              _event(2, "model_response", data=response),
              _event(3, "run_ended", occurred_at=200)]

    usage = build_quality_report(events, [], _manifest(end=200))["model_usage"]

    assert usage["token_usage"]["known_tokens"]["total_tokens"] == 120
    assert usage["estimated_cost"]["complete_estimate_usd"] is None
    assert usage["estimated_cost"]["unavailable_reasons"] == {
        "actual_model_has_no_frozen_price": 1,
    }


def test_transport_failure_marks_tokens_and_cost_unknown():
    events = [
        _event(1, "model_request", data={"call_id": "call"}),
        _event(2, "model_response", data={
            "phase": "failure", "call_id": "call", "response": None, "error": "timeout",
        }),
        _event(3, "run_failed", occurred_at=200, data={"error": "timeout"}),
    ]

    usage = build_quality_report(events, [], _manifest(end=200))["model_usage"]

    assert usage["transport_or_terminal_failures"] == 1
    assert usage["token_usage"]["calls_without_provider_response"] == 1
    assert usage["token_usage"]["complete_tokens"] is None
    assert usage["estimated_cost"]["known_estimate_usd"] is None
    assert usage["estimated_cost"]["unavailable_reasons"] == {
        "terminal_without_provider_response_cost_unknown": 1,
    }


def test_cost_band_uses_request_time_when_response_crosses_boundary():
    request_time = "2026-09-15T03:59:59+00:00"
    response_time = "2026-09-15T04:00:01+00:00"
    events = [
        _event(1, "model_request", recorded_at=request_time, data={"call_id": "call"}),
        _event(2, "model_response", recorded_at=response_time,
               data=_tool_response("call", [], _usage())),
        _event(3, "run_ended", occurred_at=200),
    ]

    usage = build_quality_report(events, [], _manifest(end=200))["model_usage"]

    assert usage["estimated_cost"]["complete_estimate_usd"] == 0.00004224


def test_memory_quality_reports_unknown_reference_and_missing_update():
    events = [_event(1, "model_request", agent_id=1, decision_id="decision", data={"call_id": "call"}),
              _event(2, "model_response", agent_id=1, decision_id="decision",
                     data=_tool_response("call", ["absent"], _usage())),
              _event(3, "run_ended", occurred_at=200)]

    memory = build_quality_report(events, [], _manifest(end=200))["memory_references"]

    assert memory["references_checked"] == 1
    assert [problem["code"] for problem in memory["problems"]] == [
        "unknown_memory_reference", "finish_without_memory_update",
    ]
    assert memory["problems"][0]["reference_ids"] == ["absent"]


def test_finish_memory_can_reference_its_observation_and_visible_prior_result():
    events = [_event(1, 'action_result', agent_id=1, action_id='prior', data={'status': 'completed'}),
              _event(2, 'observation', agent_id=1, decision_id='current',
                     data={'recent_action_results': [{'event_id': 'event-1'}]}),
              _event(3, 'model_request', agent_id=1, decision_id='current', data={'call_id': 'call'}),
              _event(4, 'model_response', agent_id=1, decision_id='current',
                     data=_tool_response('call', ['event-1', 'event-2'], _usage())),
              _event(5, 'memory_updated', agent_id=1, decision_id='current', data={'episode_ids': ['m1']})]
    memory = build_quality_report(events, [], _manifest())['memory_references']
    assert memory['references_checked'] == 2
    assert memory['problems'] == []


@pytest.mark.parametrize('source_agent,visibility', [(2, 'agent'), (1, 'system')])
def test_finish_rejects_environment_refs_unavailable_to_this_person(source_agent, visibility):
    events = [_event(1, 'action_result', agent_id=source_agent, visibility=visibility),
              _event(2, 'observation', agent_id=1, decision_id='current',
                     data={'recent_action_results': [{'event_id': 'event-1'}]}),
              _event(3, 'model_response', agent_id=1, decision_id='current',
                     data=_tool_response('call', ['event-1'], _usage()))]
    memory = build_quality_report(events, [], _manifest())['memory_references']
    assert {item['code'] for item in memory['problems']} == {
        'invalid_observed_result_reference', 'unknown_memory_reference', 'finish_without_memory_update'}


def test_environment_event_cannot_be_used_as_a_memory_read_episode():
    response = _tool_response('call', [], _usage())
    function = response['response']['choices'][0]['message']['tool_calls'][0]['function']
    function.update(name='memory_read', arguments=json.dumps({'ids': ['event-1']}))
    events = [_event(1, 'observation', agent_id=1, decision_id='current'),
              _event(2, 'model_response', agent_id=1, decision_id='current', data=response)]
    memory = build_quality_report(events, [], _manifest())['memory_references']
    assert [item['code'] for item in memory['problems']] == ['unknown_memory_reference']


def test_decision_rhythm_counts_silent_boundaries_wake_reasons_and_monologues():
    events = [
        _event(1, "activity_progress", data={DECISION_OFFERED_FIELD: False,
                                             WAKE_REASONS_FIELD: []}),
        _event(2, "activity_progress", data={DECISION_OFFERED_FIELD: True,
                                             WAKE_REASONS_FIELD: ["planned_end", "person_arrived"]}),
        _event(3, "activity_progress", data={DECISION_OFFERED_FIELD: True,
                                             WAKE_REASONS_FIELD: ["planned_end", "mystery"]}),
        _event(4, "activity_progress", data={"activity": {"kind": "work"}}),
        _event(5, "decision_started", agent_id=1, decision_id="decision-1",
               data={INNER_MONOLOGUE_FIELD: "今天想早点到办公室。"}),
        _event(6, "decision_started", agent_id=1, decision_id="decision-2",
               data={"phase": "monologue"}),
        _event(7, "run_ended", occurred_at=1100),
    ]

    rhythm = build_quality_report(events, [], _manifest())["decision_rhythm"]

    assert rhythm["activity_progress_events"] == 4
    assert rhythm["boundaries_with_decision_field"] == 3
    assert rhythm["boundaries_missing_decision_field"] == 1
    assert rhythm["silent_boundaries"] == 1
    assert rhythm["offered_boundaries"] == 2
    assert rhythm["wake_reason_events"] == 3
    assert rhythm["wake_reason_counts"] == {"person_arrived": 1, "planned_end": 2}
    assert rhythm["unrecognized_wake_reason_counts"] == {"mystery": 1}
    assert rhythm["decision_started_events"] == 2
    assert rhythm["decision_started_with_monologue"] == 1
    assert rhythm["decision_started_missing_monologue"] == 1


def test_decision_rhythm_reports_zero_coverage_for_legacy_event_sets():
    events = [_event(1, "activity_progress", data={"activity": {"kind": "work"}}),
              _event(2, "decision_started", agent_id=1, decision_id="decision-1", data={}),
              _event(3, "run_ended", occurred_at=1100)]

    rhythm = build_quality_report(events, [], _manifest())["decision_rhythm"]

    assert rhythm["silent_boundaries"] == 0
    assert rhythm["offered_boundaries"] == 0
    assert rhythm["boundaries_missing_decision_field"] == 1
    assert rhythm["wake_reason_events"] == 0
    assert rhythm["wake_reason_counts"] == {}
    assert rhythm["unrecognized_wake_reason_counts"] == {}
    assert rhythm["decision_started_events"] == 1
    assert rhythm["decision_started_with_monologue"] == 0
    assert rhythm["decision_started_missing_monologue"] == 1


def test_acceleration_sums_observed_wall_time_across_restore_branches():
    events = [_event(1, "observation", occurred_at=100, run_id="parent", wall_ms=2_000),
              _event(2, "run_ended", occurred_at=200, run_id="run", wall_ms=3_000)]

    acceleration = build_quality_report(events, [], _manifest(end=200))["acceleration"]

    assert acceleration == {
        "simulated_seconds": 100, "observed_wall_seconds": 5.0, "speedup": 20.0,
        "available": True, "unavailable_reason": None,
        "wall_seconds_by_run": {"parent": 2.0, "run": 3.0},
        "method": "Sum of maximum persisted wall_elapsed_ms for each represented run branch",
    }


@pytest.mark.parametrize("events", [
    [],
    [_event(2, "observation"), _event(1, "run_ended", occurred_at=200)],
    [_event(1, "observation", occurred_at=200), _event(2, "run_ended", occurred_at=199)],
])
def test_invalid_quality_inputs_are_rejected(events):
    with pytest.raises(IntegrityError):
        build_quality_report(events, [], _manifest())


def test_quality_report_is_deterministic_for_the_same_authenticated_input():
    events = [_event(1, "run_ended", occurred_at=200)]

    first = build_quality_report(events, [], _manifest(end=200))
    second = build_quality_report(events, [], _manifest(end=200))

    assert first == second
    json.dumps(first, ensure_ascii=False, sort_keys=True, allow_nan=False)
