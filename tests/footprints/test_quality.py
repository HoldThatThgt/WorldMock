import json

import pytest

from worldmock.errors import IntegrityError
from worldmock.footprints.models import (
    DECISION_OFFERED_FIELD,
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


def _memory(text="我以为自己已经吃过一顿饭。"):
    """人物结束活动时写下的一条主观记忆：{type, text, importance, keywords}，不带任何身份。"""
    return {"type": "reflection", "text": text, "importance": 0.2, "keywords": ["吃饭"]}


def _conclusion(*memories):
    """结束一项活动的 conclude_activity 请求；memories 与 task 等长，空组表示这次不写记忆。"""
    return {"task": ["等待（1月5日 00:00 起）"], "summary": ["等完了。"], "memories": [list(memories)]}


def _tool_response(call_id, usage=None):
    """生活决策的模型响应：恰好一个世界请求（没有 finish，记忆随 conclude_activity.memories 提交）。"""
    arguments = _conclusion(_memory())
    return {
        "phase": "response", "call_id": call_id, "attempt": 1, "error": None,
        "response": {
            "model": "deepseek-flash", "usage": usage,
            "choices": [{"message": {"tool_calls": [{
                "function": {"name": "conclude_activity", "arguments": json.dumps(arguments, ensure_ascii=False)},
            }]}}],
        },
    }


def _usage():
    return {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
            "prompt_cache_hit_tokens": 40, "prompt_cache_miss_tokens": 60}


def _memory_update(identity, observation_id, decision_id, trigger=("activity_conclusion",)):
    """引擎写出的 memory_updated.data：写入点 trigger 与所属决策 decision_id 由引擎按登记推导。"""
    return {"operation": "append_episodes", "created_episode_ids": [identity],
            "merged_episode_ids": [], "episodes": [{"id": identity, "type": "goal",
            "text": "想继续生活", "epistemic_status": "subjective"}],
            "memory_before_sha256": "a" * 64, "memory_sha256": "b" * 64,
            "episodes_sha256": "c" * 64, "state_sha256": "d" * 64,
            "consolidated": False, "observation_event_id": observation_id,
            "verified_event_ids": [], "trigger": list(trigger), "decision_id": decision_id}


def _concluded(sequence, decision_id, *memories, action_id=None):
    """一条已登记的 conclude_activity 请求事件。"""
    return _event(sequence, "action_requested", agent_id=1, decision_id=decision_id,
                  action_id=action_id or f"action-{decision_id}",
                  data={"tool_name": "conclude_activity", "request": _conclusion(*memories)})


def _started(reasons=()):
    """decision_started.data 的闭集四个字段（docs/dev/SPEC-persona-prompt.md 第二章「单一行动阶段」）。"""
    return {"round_id": "round", "round_order": 0, "world_state_version": 1, WAKE_REASONS_FIELD: list(reasons)}


def _activity(identity, kind, elapsed, status="completed", **fields):
    return {"activity_id": identity, "agent_id": 1, "activity_type": kind,
            "elapsed_seconds": elapsed, "status": status, **fields}


def test_quality_report_covers_run_behavior_usage_cost_and_speedup():
    """整份质量报告；两次收尾各在同一决策写出收尾记忆，第二次登记后结算被拒，记忆照写，不报问题。"""
    events = [
        _event(1, "observation", agent_id=1, decision_id="decision-1", data={"verified_experiences": []}),
        _event(2, "model_request", agent_id=1, decision_id="decision-1", data={"call_id": "call-1"}),
        _event(3, "model_response", agent_id=1, decision_id="decision-1",
               data=_tool_response("call-1", _usage())),
        _concluded(4, "decision-1", _memory(), action_id="action-1"),
        _event(5, "memory_updated", agent_id=1, decision_id="decision-1",
               data=_memory_update("memory-1", "event-1", "decision-1")),
        _event(6, "action_result", agent_id=1, action_id="action-1", data={"status": "completed"}),
        _event(7, "observation", agent_id=1, decision_id="decision-2", data={"verified_experiences": []}),
        _event(8, "model_request", agent_id=1, decision_id="decision-2", data={"call_id": "call-2"}),
        _event(9, "model_response", agent_id=1, decision_id="decision-2",
               data=_tool_response("call-2", _usage())),
        _concluded(10, "decision-2", _memory("我想早点回家。"), action_id="action-2"),
        _event(11, "memory_updated", agent_id=1, decision_id="decision-2",
               data=_memory_update("memory-2", "event-7", "decision-2")),
        _event(12, "action_rejected", agent_id=1, action_id="action-2",
               data={"error_code": "ACTIVITY_CONCLUSION_REQUIRED"}),
        _event(13, "action_result", agent_id=1, action_id="action-2", data={"status": "rejected"}),
        _event(14, "activity_progress", data={
            "activity": {"kind": "work"},
            "interval_start": 104, "interval_end": 114, "activity_efficiency": 0.8,
        }),
        _event(15, "activity_progress", data={
            "activity": {"kind": "consume"},
            "interval_start": 114, "interval_end": 115,
            "consumption": {"use_mode": "eat", "food_efficiency": 0.9,
                            "items": [{"interval_quantity": 0.5}]},
        }),
        _event(16, "run_ended", occurred_at=1100, wall_ms=100_000),
    ]
    activities = [
        _activity("watch-1", "watch", 600, target_id="show", data={"repeat": False}),
        _activity("watch-2", "watch", 300, "cancelled", target_id="show", interruption_reason="bored",
                  data={"repeat": True}),
        _activity("game-1", "play_game", 200),
        _activity("work-1", "work", 100),
        _activity("meal-1", "consume", 50, "truncated", interruption_reason="cutoff"),
    ]

    report = build_quality_report(events, activities, _manifest())

    assert report["run_outcome"]["status"] == "completed"
    assert report["actions"]["result_status_counts"]["rejected"] == 1
    assert report["actions"]["business_rejection_codes"] == {"ACTIVITY_CONCLUSION_REQUIRED": 1}
    assert report["activities"]["completed_activities"] == 3
    assert report["activities"]["terminal_interruptions"] == 2
    assert report["content_contact"]["contact_seconds"] == 1100
    assert report["content_contact"]["repeat_watch"]["repeated_sessions"] == 1
    assert report["conversion_efficiency"]["work"]["weighted_mean"] == 0.8
    assert report["conversion_efficiency"]["food"]["weighted_mean"] == 0.9
    assert report["memory_references"] == {
        "memory_update_events": 2, "persisted_episode_ids": 2,
        "write_points_checked": 2, "problem_count": 0, "problems": [],
    }
    assert report["model_usage"]["token_usage"]["complete_tokens"] == {
        "input_tokens": 200, "output_tokens": 40, "total_tokens": 240,
    }
    assert report["model_usage"]["estimated_cost"]["complete_estimate_usd"] == 0.00008448
    assert report["acceleration"]["speedup"] == 10.0


def test_repeat_watch_counts_only_sessions_restarted_from_the_beginning():
    """同一内容的两段观看：第二段是接着看（data.repeat=false）时不算重复观看，从头再看（repeat=true）时算一次。

    时间与能量规格第七章「三层结构」：从上次的位置接着放不算新的一次接触，只有第一次和从头重看才增加接触次数。
    """
    events = [_event(1, "run_ended", occurred_at=1100)]
    first = _activity("watch-1", "watch", 600, target_id="sintel", data={"repeat": False})
    resumed = _activity("watch-2", "watch", 300, target_id="sintel", data={"repeat": False})
    restarted = _activity("watch-2", "watch", 300, target_id="sintel", data={"repeat": True})

    resumed_report = build_quality_report(events, [first, resumed], _manifest())["content_contact"]
    restarted_report = build_quality_report(events, [first, restarted], _manifest())["content_contact"]

    assert resumed_report["session_count"] == restarted_report["session_count"] == 2
    assert resumed_report["repeat_watch"]["repeated_sessions"] == 0
    assert resumed_report["repeat_watch"]["groups"] == []
    assert restarted_report["repeat_watch"]["repeated_sessions"] == 1
    assert restarted_report["repeat_watch"]["groups"] == [{
        "agent_id": 1, "target_id": "sintel", "sessions": 2, "repeated_sessions": 1,
        "activity_ids": ["watch-2"]}]


def test_missing_measurements_remain_unknown_instead_of_becoming_zero():
    events = [
        _event(1, "model_request", data={"call_id": "call"}),
        _event(2, "model_response", data=_tool_response("call")),
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


def test_model_usage_counts_plan_day_and_lists_it_by_purpose():
    """model_usage 计入零点计划 plan_day 的请求与响应，并按 purpose 与人物决策、工作验收分列。"""
    plan = {"phase": "response", "purpose": "plan_day", "call_id": "plan", "attempt": 1, "error": None,
            "response": {"model": "deepseek-flash", "usage": _usage(), "choices": []}}
    events = [_event(1, "model_request", data={"call_id": "decision", "phase": "request"}),
              _event(2, "model_response", data=_tool_response("decision", _usage())),
              _event(3, "model_request", data={"call_id": "plan", "phase": "request", "purpose": "plan_day"}),
              _event(4, "model_response", data=plan),
              _event(5, "run_ended", occurred_at=200)]

    usage = build_quality_report(events, [], _manifest(end=200))["model_usage"]

    assert usage["request_events"] == 2 and usage["provider_responses"] == 2
    assert usage["token_usage"]["complete_tokens"]["total_tokens"] == 240
    assert [(row["purpose"], row["request_events"], row["provider_responses"],
             row["token_usage"]["known_tokens"]["total_tokens"]) for row in usage["by_purpose"]] == [
        ("decision", 1, 1, 120), ("plan_day", 1, 1, 120)]


def test_plan_day_response_naming_memories_is_no_memory_problem():
    """零点计划不是记忆写入点：它的输出多带 memories 字段时，本地校验错误不记为记忆协议问题；
    同一句错误出现在人物决策的响应里照旧记入。"""
    from worldmock.life.day_planning import PlanDayOutputError, parse_plan_day

    arguments = json.dumps({"goals": [], "memories": ["明早七点起床"]}, ensure_ascii=False)
    raw = {"model": "deepseek-flash", "choices": [{"message": {"tool_calls": [
        {"function": {"name": "submit_day_plan", "arguments": arguments}}]}}]}
    with pytest.raises(PlanDayOutputError, match="memories") as caught:
        parse_plan_day(raw, 100, "Asia/Shanghai")
    plan = {"phase": "response", "purpose": "plan_day", "call_id": "plan", "attempt": 1,
            "error": str(caught.value), "response": raw}
    decision = {key: value for key, value in plan.items() if key != "purpose"}

    def problems(data):
        event = _event(1, "model_response", agent_id=1, data=data)
        return build_quality_report([event], [], _manifest())["memory_references"]["problems"]

    assert problems(plan) == []
    assert problems(decision) == [{"code": "memory_protocol_error", "event_id": "event-1", "agent_id": 1}]


def test_actual_unpriced_model_keeps_tokens_but_does_not_invent_cost():
    response = _tool_response("call", _usage())
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
               data=_tool_response("call", _usage())),
        _event(3, "run_ended", occurred_at=200),
    ]

    usage = build_quality_report(events, [], _manifest(end=200))["model_usage"]

    assert usage["estimated_cost"]["complete_estimate_usd"] == 0.00004224


def test_memory_quality_reports_conclusion_without_memory_update():
    """带非空 memories 的 conclude_activity 在同一决策没有收尾写入点的 memory_updated 时报
    conclusion_without_memory_update；memories 全是空组的收尾不要求写记忆（时间与能量规格第七章「来源由系统记录」）。"""
    events = [_event(1, "observation", agent_id=1, decision_id="quiet", data={"verified_experiences": []}),
              _concluded(2, "quiet"),
              _event(3, "observation", agent_id=1, decision_id="decision", data={"verified_experiences": []}),
              _event(4, "model_request", agent_id=1, decision_id="decision", data={"call_id": "call"}),
              _event(5, "model_response", agent_id=1, decision_id="decision", data=_tool_response("call", _usage())),
              _concluded(6, "decision", _memory()),
              _event(7, "run_ended", occurred_at=200)]

    memory = build_quality_report(events, [], _manifest(end=200))["memory_references"]

    assert memory == {"memory_update_events": 0, "persisted_episode_ids": 0, "write_points_checked": 0,
                      "problem_count": 1, "problems": [
                          {"code": "conclusion_without_memory_update", "event_id": "event-6", "agent_id": 1}]}


def test_non_memory_tool_json_error_is_counted_without_memory_warning():
    """生活工具参数错误归入模型协议错误，不误报为记忆问题；conclude_activity.memories 的本地校验错误才是记忆协议错误。"""
    response = _tool_response("call", _usage())
    response["error"] = "invalid tool arguments for report_status"
    function = response["response"]["choices"][0]["message"]["tool_calls"][0]["function"]
    function.update(name="report_status", arguments="{invalid json")
    event = _event(1, "model_response", agent_id=1, decision_id="decision", data=response)
    quality = build_quality_report([event], [], _manifest())
    assert quality["model_usage"]["protocol_error_responses"] == 1
    assert quality["memory_references"]["problem_count"] == 0

    oversized = _tool_response("call", _usage())
    oversized["error"] = "invalid conclude_activity arguments: memories exceed eight entries in total"
    event = _event(1, "model_response", agent_id=1, decision_id="decision", data=oversized)
    memory = build_quality_report([event], [], _manifest())["memory_references"]
    assert memory["problems"] == [{"code": "memory_protocol_error", "event_id": "event-1", "agent_id": 1}]


def test_conclusion_and_reflection_in_one_step_are_each_matched_to_their_registration():
    """醒来那一轮的收尾记忆与就寝反思同在一步：trigger 按顺序列两项，逐项对上登记（收尾请求、醒来的观察）。"""
    events = [
        _event(1, "observation", agent_id=1, decision_id="woke",
               data={"verified_experiences": [], "wake_reasons": ["wake_from_sleep", "planned_end"]}),
        _concluded(2, "woke", _memory("睡得不太踏实。")),
        _event(3, "memory_updated", agent_id=1, decision_id="woke",
               data=_memory_update("m1", "event-1", "woke", ("activity_conclusion", "reflection"))),
        _event(4, "observation", agent_id=1, decision_id="rested",
               data={"verified_experiences": [], "wake_reasons": ["wake_from_sleep"]}),
        _event(5, "memory_updated", agent_id=1, decision_id="rested",
               data=_memory_update("m2", "event-4", "rested", ("reflection",))),
    ]
    memory = build_quality_report(events, [], _manifest())["memory_references"]
    assert memory == {"memory_update_events": 2, "persisted_episode_ids": 2, "write_points_checked": 3,
                      "problem_count": 0, "problems": []}


@pytest.mark.parametrize("wake_reasons,registered,trigger", [
    (["planned_end"], False, ("activity_conclusion",)),
    (["planned_end"], True, ("activity_conclusion", "reflection")),
    (["planned_end"], False, ()),
])
def test_memory_update_whose_write_point_has_no_registration_has_no_source(wake_reasons, registered, trigger):
    """写入点找不到对应登记即 memory_update_without_source：收尾写入点没有同一决策的 conclude_activity、反思写入点
    不在醒来那一轮，或写入了条目却没有任何写入点。"""
    events = [_event(1, "observation", agent_id=1, decision_id="decision",
                     data={"verified_experiences": [], "wake_reasons": wake_reasons}),
              *([_concluded(2, "decision", _memory())] if registered else []),
              _event(3, "memory_updated", agent_id=1, decision_id="decision",
                     data=_memory_update("m1", "event-1", "decision", trigger))]
    memory = build_quality_report(events, [], _manifest())["memory_references"]
    assert memory["write_points_checked"] == len(trigger)
    assert memory["problems"] == [{"code": "memory_update_without_source", "event_id": "event-3", "agent_id": 1}]


@pytest.mark.parametrize("change", [
    {"trigger": "activity_conclusion"}, {"trigger": ["finish"]}, {"trigger": ["reflection", "activity_conclusion"]},
    {"trigger": ["activity_conclusion", "activity_conclusion"]}, {"decision_id": "another-decision"},
    {"decision_id": None},
])
def test_malformed_trigger_or_decision_is_an_invalid_memory_update(change):
    """trigger 须是写入点闭集按序、无重复的列表，decision_id 须等于事件所属决策；否则整条记为 invalid_memory_update。"""
    events = [_event(1, "observation", agent_id=1, decision_id="decision", data={"verified_experiences": []}),
              _concluded(2, "decision", _memory()),
              _event(3, "memory_updated", agent_id=1, decision_id="decision",
                     data={**_memory_update("m1", "event-1", "decision"), **change})]
    memory = build_quality_report(events, [], _manifest())["memory_references"]
    assert memory["memory_update_events"] == 0
    assert [item["code"] for item in memory["problems"]] == [
        "invalid_memory_update", "conclusion_without_memory_update"]


def test_memory_update_must_carry_its_observation_fact_window():
    """记忆证据的观察身份与已核验经历窗口须等于同一决策绑定的观察，否则报 invalid_memory_update_source_window。"""
    events = [_event(1, "observation", agent_id=1, decision_id="decision", data={"verified_experiences": []}),
              _concluded(2, "decision", _memory()),
              _event(3, "memory_updated", agent_id=1, decision_id="decision",
                     data=_memory_update("m1", "event-absent", "decision"))]
    memory = build_quality_report(events, [], _manifest())["memory_references"]
    assert [item["code"] for item in memory["problems"]] == ["invalid_memory_update_source_window"]


def test_merged_memory_identity_is_not_reported_as_a_duplicate():
    first = _memory_update('m1', 'event-1', 'first')
    merged = {**_memory_update('m1', 'event-4', 'second'), 'created_episode_ids': [],
              'merged_episode_ids': ['m1']}
    events = [
        _event(1, 'observation', agent_id=1, decision_id='first', data={'verified_experiences': []}),
        _event(2, 'model_response', agent_id=1, decision_id='first', data=_tool_response('first', _usage())),
        _concluded(3, 'first', _memory()),
        _event(4, 'memory_updated', agent_id=1, decision_id='first', data=first),
        _event(5, 'observation', agent_id=1, decision_id='second', data={'verified_experiences': []}),
        _event(6, 'model_response', agent_id=1, decision_id='second', data=_tool_response('second', _usage())),
        _concluded(7, 'second', _memory()),
        _event(8, 'memory_updated', agent_id=1, decision_id='second',
               data={**merged, 'observation_event_id': 'event-5'}),
    ]
    memory = build_quality_report(events, [], _manifest())['memory_references']
    assert memory['persisted_episode_ids'] == 1
    assert memory['problems'] == []


def test_valid_looking_observed_fact_must_equal_its_committed_source():
    source = _event(1, 'activity_progress', agent_id=1, activity_id='wait', data={
        'interval_start': 90, 'interval_end': 101,
        'activity': {'activity_id': 'wait', 'kind': 'wait'}})
    forged = {'kind': 'waited', 'agent_id': 1, 'activity_id': 'wait',
              'source_event_id': 'event-1', 'event_seq': 1, 'occurred_at': 101,
              'interval_start': 91, 'interval_end': 101, 'duration_seconds': 10, 'reason': None}
    observation = _event(2, 'observation', agent_id=1, decision_id='decision',
                         data={'verified_experiences': [forged]})
    quality = build_quality_report([source, observation], [], _manifest())['memory_references']
    assert [item['code'] for item in quality['problems']] == ['invalid_verified_experience_source']


@pytest.mark.parametrize('source,valid', [
    ({'sequence': 1, 'agent_id': 1, 'visibility': 'agent', 'event_type': 'action_result'}, True),
    ({'sequence': 1, 'agent_id': 1, 'visibility': 'system', 'event_type': 'model_response'}, True),
    ({'sequence': 1, 'agent_id': 2, 'visibility': 'agent', 'event_type': 'action_result'}, False),
    ({'sequence': 3, 'agent_id': 1, 'visibility': 'agent', 'event_type': 'action_result'}, False),
    (None, False),
])
def test_progress_sources_must_be_this_persons_prior_events(source, valid):
    """观察的 progress_event_ids 每个都须解析到本人更早提交的事件；否则记 invalid_observed_result_reference。"""
    events = [_event(2, 'observation', agent_id=1, decision_id='current',
                     data={'progress_event_ids': ['event-1' if source is None or source['sequence'] == 1
                                                  else 'event-3'], 'verified_experiences': []})]
    if source is not None:
        sequence = source['sequence']
        fields = {key: value for key, value in source.items() if key not in {'sequence', 'event_type'}}
        events.insert(0 if sequence == 1 else 1, _event(sequence, source['event_type'], **fields))
    events.append(_event(4, 'model_response', agent_id=1, decision_id='current',
                         data=_tool_response('call', _usage())))
    memory = build_quality_report(events, [], _manifest())['memory_references']
    assert [item['code'] for item in memory['problems']] == ([] if valid else ['invalid_observed_result_reference'])


def test_decision_rhythm_counts_silent_boundaries_and_wake_reasons():
    """决策节律只透传边界与唤醒原因计数；自述两项计数已删除（报告版本 2 起），报告版本现为 3（一日运行保真的新节）。"""
    events = [
        _event(1, "environment_event", data={"kind": "body_settlement", DECISION_OFFERED_FIELD: False,
                                             WAKE_REASONS_FIELD: []}),
        _event(2, "environment_event", data={"kind": "body_settlement", DECISION_OFFERED_FIELD: True,
                                             WAKE_REASONS_FIELD: ["planned_end", "person_arrived"]}),
        _event(3, "environment_event", data={"kind": "body_settlement", DECISION_OFFERED_FIELD: True,
                                             WAKE_REASONS_FIELD: ["planned_end", "mystery"]}),
        _event(4, "environment_event", data={"kind": "body_settlement"}),
        _event(5, "decision_started", agent_id=1, decision_id="decision-1", data=_started()),
        _event(6, "decision_started", agent_id=1, decision_id="decision-2", data=_started(["run_start"])),
        _event(7, "environment_event", agent_id=1, data={"kind": "decision_budget_exhausted",
                                                         "error_code": "ACTION_REQUEST_INVALID",
                                                         "next_decision_at": 1000}),
        _event(8, "run_ended", occurred_at=1100),
    ]

    report = build_quality_report(events, [], _manifest())
    rhythm = report["decision_rhythm"]

    assert report["schema_version"] == 3
    assert rhythm["decision_budget_exhausted_events"] == 1
    assert rhythm["body_settlement_events"] == 4
    assert rhythm["boundaries_with_decision_field"] == 3
    assert rhythm["boundaries_missing_decision_field"] == 1
    assert rhythm["silent_boundaries"] == 1
    assert rhythm["offered_boundaries"] == 2
    assert rhythm["wake_reason_events"] == 3
    assert rhythm["wake_reason_counts"] == {"person_arrived": 1, "planned_end": 2}
    assert rhythm["unrecognized_wake_reason_counts"] == {"mystery": 1}
    assert rhythm["decision_started_events"] == 2
    assert not {"decision_started_with_decision_statement", "decision_started_missing_decision_statement"} & set(
        rhythm)


def test_decision_rhythm_counts_same_moment_rounds():
    """时间与能量规格第一章：同刻轮数按（人物, 模拟秒）计 decision_started，轮次原因取 decision_started。"""
    first, later = 200, 300

    def started(sequence, agent_id, occurred_at, reasons):
        return _event(sequence, "decision_started", occurred_at=occurred_at, agent_id=agent_id,
                      decision_id=f"decision-{sequence}", data=_started(reasons))

    events = [
        started(1, 1, first, ["run_start"]),
        started(2, 1, first, ["no_active_activity"]),
        started(3, 1, first, ["rejection_feedback"]),
        started(4, 2, first, ["run_start"]),
        _event(5, "environment_event", occurred_at=later, agent_id=1,
               data={"kind": "body_settlement", DECISION_OFFERED_FIELD: True,
                     WAKE_REASONS_FIELD: ["no_active_activity"]}),
        started(6, 1, later, ["planned_end"]),
        _event(7, "run_ended", occurred_at=1100),
    ]

    rhythm = build_quality_report(events, [], _manifest())["decision_rhythm"]

    assert rhythm["same_moment_round_counts"] == {"1": 2, "3": 1}
    assert rhythm["decision_started_events"] == 5
    assert rhythm["decision_started_wake_reason_counts"] == {
        "no_active_activity": 1, "planned_end": 1, "rejection_feedback": 1, "run_start": 2}
    assert rhythm["wake_reason_counts"] == {"no_active_activity": 1}
    assert rhythm["unrecognized_decision_started_wake_reason_counts"] == {}
    assert rhythm["unrecognized_wake_reason_counts"] == {}

    foreign = events[:-1] + [started(7, 2, later, ["mystery"]), _event(8, "run_ended", occurred_at=1100)]
    rhythm = build_quality_report(foreign, [], _manifest())["decision_rhythm"]
    assert rhythm["same_moment_round_counts"] == {"1": 3, "3": 1}
    assert rhythm["unrecognized_decision_started_wake_reason_counts"] == {"mystery": 1}


def test_decision_rhythm_reports_zero_coverage_for_legacy_event_sets():
    events = [_event(1, "environment_event", data={"kind": "body_settlement"}),
              _event(2, "decision_started", agent_id=1, decision_id="decision-1", data=_started()),
              _event(3, "run_ended", occurred_at=1100)]

    rhythm = build_quality_report(events, [], _manifest())["decision_rhythm"]

    assert rhythm["silent_boundaries"] == 0
    assert rhythm["offered_boundaries"] == 0
    assert rhythm["boundaries_missing_decision_field"] == 1
    assert rhythm["wake_reason_events"] == 0
    assert rhythm["wake_reason_counts"] == {}
    assert rhythm["unrecognized_wake_reason_counts"] == {}
    assert rhythm["decision_started_events"] == 1
    assert rhythm["decision_budget_exhausted_events"] == 0


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


def test_input_warnings_section_counts_the_frozen_manifest_warnings():
    """input_warnings 一节（docs/dev/SPEC-dayrun-fidelity.md 1.3、九）：按警告码闭集与人物统计清单冻结的警告，场景级警告
    另计；清单没有记录（引擎直驱为 null，或旧清单没有这个键）时计数为 null 而不是 0；只报告，不放行。"""
    from worldmock.profile.injection import INPUT_WARNING_CODES, agreement_warnings, game_weight_warnings

    scenario_level = {"code": "LOCATION_NAME_SUSPICIOUS", "agent_id": None, "message": "显示名带坐标",
                      "details": {"location_id": "poi", "name": "全家（31.2, 121.5）", "patterns": ["coordinates"]}}
    warnings = [*game_weight_warnings(1, ("g1",), None), *agreement_warnings(2, ("a", "b")), scenario_level]
    events = [_event(1, "run_ended", occurred_at=200)]

    section = build_quality_report(events, [], {**_manifest(end=200), "input_warnings": warnings})["input_warnings"]

    expected_codes = dict.fromkeys(INPUT_WARNING_CODES, 0)
    expected_codes.update(GAMES_WITHOUT_TASTE_WEIGHTS=1, PERSONA_AGREEMENT_LOW=2, LOCATION_NAME_SUSPICIOUS=1)
    assert section["recorded"] is True and section["warning_count"] == 4
    assert section["code_counts"] == expected_codes and list(section["code_counts"]) == list(INPUT_WARNING_CODES)
    assert section["agent_counts"] == {"1": 1, "2": 2} and section["scenario_warning_count"] == 1
    assert section["unrecognized_code_counts"] == {}
    for manifest in (_manifest(end=200), {**_manifest(end=200), "input_warnings": None}):
        missing = build_quality_report(events, [], manifest)["input_warnings"]
        assert missing["recorded"] is False
        assert missing["warning_count"] is None and missing["code_counts"] is None
    empty = build_quality_report(events, [], {**_manifest(end=200), "input_warnings": []})["input_warnings"]
    assert empty["warning_count"] == 0 and set(empty["code_counts"].values()) == {0}


def test_quality_report_is_deterministic_for_the_same_authenticated_input():
    events = [_event(1, "run_ended", occurred_at=200)]

    first = build_quality_report(events, [], _manifest(end=200))
    second = build_quality_report(events, [], _manifest(end=200))

    assert first == second
    json.dumps(first, ensure_ascii=False, sort_keys=True, allow_nan=False)


@pytest.mark.parametrize("source,codes", [("reflection", []), ("activity_conclusion", ["invalid_memory_update"])])
def test_bedtime_reflection_snapshot_may_carry_its_source(source, codes):
    """醒来那一轮登记的就寝反思快照可另带 source=reflection（宿主标注，记忆层据此找归属日期）；其他来源值仍是无效登记。"""
    update = {**_memory_update("r1", "event-1", "decision", trigger=("reflection",)),
              "episodes": [{"id": "r1", "type": "reflection", "text": "睡前想了想", "epistemic_status": "subjective",
                            "source": source}]}
    events = [_event(1, "observation", agent_id=1, decision_id="decision",
                     data={"verified_experiences": [], "wake_reasons": ["wake_from_sleep"]}),
              _event(2, "memory_updated", agent_id=1, decision_id="decision", data=update)]
    memory = build_quality_report(events, [], _manifest())["memory_references"]
    assert [item["code"] for item in memory["problems"]] == codes
