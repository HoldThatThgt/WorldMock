import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from worldmock.errors import IntegrityError
from worldmock.footprints.models import (
    DECISION_OFFERED_FIELD,
    WAKE_REASONS_FIELD,
    Event,
)
from tests.footprints.display_fixtures import sample_catalog_rows, sample_display_names
from worldmock.footprints.display_wording import display_names
from worldmock.footprints.quality import build_quality_report
from worldmock.life.error_contract import ErrorItem, ErrorReport


def _event(sequence, event_type, *, occurred_at=None, run_id="run", wall_ms=None, **fields):
    data = fields.pop("data", {})
    if event_type == "run_ended":
        fields.setdefault("visibility", "system")
    return Event(
        event_id=f"event-{sequence}", event_type=event_type,
        occurred_at=100 + sequence if occurred_at is None else occurred_at,
        run_id=run_id, event_seq=sequence,
        recorded_at=fields.pop("recorded_at", "2026-09-15T07:00:00+00:00"),
        wall_elapsed_ms=sequence * 100 if wall_ms is None else wall_ms,
        state_version_before=0, state_version_after=0, data=data, **fields,
    )


def _manifest(start=100, end=1100):
    return {"run_id": "run", "source_group_id": "group", "work_content_ids": {"1": []},
            "window": {"start": start, "end": end, "timezone": "Asia/Shanghai"}, "lineage": []}


def _memory(text="我以为自己已经吃过一顿饭。"):
    """人物结束活动时写下的一条主观记忆：{type, text, importance, keywords}，不带任何身份。"""
    return {"type": "reflection", "text": text, "importance": 0.2, "keywords": ["吃饭"]}


def _conclusion(*memories):
    """结束一项活动的 conclude_activity 请求（一项一个对象）；没有记忆与看法表示这次不写记忆。"""
    return {"items": [
        {"activity_name": "等待（1月5日 00:00 起）", "summary": "等完了。", "memories": list(memories), "views": []}],
        "errands": []}


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


def _feedback(code="ARGUMENTS_INVALID", path=("items", 0, "memories")):
    """与生产协议一致的结构化反馈；错误位置不依赖日志文本。"""
    return ErrorReport(code, "The parameter is invalid.", "Correct the reported parameter.",
                       (ErrorItem(path, None, code),)).payload()


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
    """decision_started.data 的闭集四个字段（docs/design/06-agent.md 第二章「单一行动阶段」）。"""
    return {"round_id": "round", "round_order": 0, "world_state_version": 1, WAKE_REASONS_FIELD: list(reasons)}


def _activity(identity, kind, elapsed, status="completed", **fields):
    return {"activity_id": identity, "agent_id": 1, "activity_type": kind,
            "elapsed_seconds": elapsed, "status": status, **fields}


def _content_event(sequence, *, before=0, after=300, completed=False, rounds=0, app_id="bilibili",
                   content="c-long", agent_id=1, category="entertainment", action_id="recommend",
                   location_id="home"):
    """@brief 内容接触来自当前 ACTION 和完整真实内容结算区间；内容与 app 取样例环境里登记的身份。"""
    start = 100 + (rounds - int(completed)) * 600 + before
    end = start + after - before
    activity = {"activity_id": f"phone-{sequence}", "agent_id": agent_id, "kind": "interact",
                "location_id": location_id, "started_at": start, "ended_at": None, "status": "active", "session_ids": [],
                "data": {"action": "interact", "app_id": app_id,
                         "content_id": content, "pinned_by_push_id": None}}
    return _event(sequence, "activity_progress", occurred_at=end, agent_id=agent_id,
                  activity_id=activity["activity_id"], data={"activity": activity,
                        "interval_start": start, "interval_end": end,
                        "content": {"content_id": content, "app_id": app_id,
                                    "position_before_seconds": before, "position_after_seconds": after,
                                    "completed": completed, "completed_rounds_after": rounds,
                                    "completion_effects": None}})


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
        _event(15, "environment_event", agent_id=1, data={"kind": "goods_received", "category": "meal",
                                                        "provided": 400, "applied": 360}),
        _content_event(16, after=600, completed=True, rounds=1),
        _content_event(17, after=300, rounds=1),
        _event(18, "run_ended", occurred_at=1100, wall_ms=100_000),
    ]
    activities = [
        _activity("app-1", "use_app", 600, target_id="bilibili"),
        _activity("app-2", "use_app", 300, "cancelled", target_id="bilibili", interruption_reason="bored"),
        _activity("rest-1", "lifestyle", 200),
        _activity("work-1", "work", 100),
        _activity("meal-1", "consume", 50, "truncated", interruption_reason="cutoff"),
    ]

    report = build_quality_report(events, activities, _manifest(), sample_display_names())

    assert report["run_outcome"]["status"] == "completed"
    assert report["actions"]["result_status_counts"]["rejected"] == 1
    assert report["actions"]["business_rejection_codes"] == {"ACTIVITY_CONCLUSION_REQUIRED": 1}
    assert report["activities"]["completed_activities"] == 3
    assert report["activities"]["terminal_interruptions"] == 2
    assert report["content_contact"] == {"by_app": {"bilibili": 2},
                                         "repeat_watch": 1, "repeat_seconds_share": pytest.approx(1 / 3)}
    assert "work" not in report["conversion_efficiency"]
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


def test_repeat_watch_counts_completed_rounds():
    """@brief 首轮未完接着看不计重复；完成过内容后，本轮的每条真实消费事实均计重复。"""
    first = _content_event(1, after=600)
    resumed = _content_event(2, before=600, after=900)
    restarted = _content_event(2, before=0, after=300, rounds=1)
    finished = _content_event(1, after=600, completed=True, rounds=1)
    activities = [_activity("app-1", "use_app", 600, target_id="bilibili"),
                  _activity("app-2", "use_app", 300, target_id="bilibili")]
    resumed_report = build_quality_report([first, resumed], activities, _manifest(), sample_display_names())["content_contact"]
    restarted_report = build_quality_report([finished, restarted], activities, _manifest(), sample_display_names())["content_contact"]
    assert resumed_report["by_app"] == restarted_report["by_app"] == {"bilibili": 2}
    assert resumed_report["repeat_watch"] == 0
    assert restarted_report["repeat_watch"] == 1


def test_missing_measurements_remain_unknown_instead_of_becoming_zero():
    events = [
        _event(1, "model_request", data={"call_id": "call"}),
        _event(2, "model_response", data=_tool_response("call")),
        _event(3, "run_ended", occurred_at=200),
    ]
    activities = [_activity("app-known", "use_app", 20, target_id="bilibili"),
                  _activity("app-missing", "use_app", None, target_id="bilibili")]

    report = build_quality_report(events, activities, _manifest(end=200), sample_display_names())

    contact = report["content_contact"]
    assert contact == {"by_app": {}, "repeat_watch": 0, "repeat_seconds_share": None}
    assert report["activities"]["known_elapsed_seconds"] == 20
    assert report["activities"]["elapsed_seconds_complete"] is False
    assert report["model_usage"]["token_usage"]["known_tokens"] is None
    assert report["model_usage"]["token_usage"]["complete_tokens"] is None
    assert report["model_usage"]["token_usage"]["provider_responses_missing_usage"] == 1
    assert report["model_usage"]["estimated_cost"]["known_estimate_usd"] is None
    assert "work" not in report["conversion_efficiency"]
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

    usage = build_quality_report(events, [], _manifest(end=200), sample_display_names())["model_usage"]

    assert usage["request_events"] == 2 and usage["provider_responses"] == 2
    assert usage["token_usage"]["complete_tokens"]["total_tokens"] == 240
    assert [(row["purpose"], row["request_events"], row["provider_responses"],
             row["token_usage"]["known_tokens"]["total_tokens"]) for row in usage["by_purpose"]] == [
        ("decision", 1, 1, 120), ("plan_day", 1, 1, 120)]


def test_plan_day_response_naming_memories_is_no_memory_problem():
    """零点计划不是记忆写入点：它的输出多带 memories 字段时，本地校验错误不记为记忆协议问题；
    人物决策带实际记忆字段的结构化错误时记入。"""
    from worldmock.life.day_planning import PlanDayOutputError, parse_plan_day

    arguments = json.dumps({"goals": [], "memories": ["明早七点起床"]}, ensure_ascii=False)
    raw = {"model": "deepseek-flash", "choices": [{"message": {"tool_calls": [
        {"function": {"name": "submit_day_plan", "arguments": arguments}}]}}]}
    with pytest.raises(PlanDayOutputError, match="memories") as caught:
        parse_plan_day(raw, 100, "Asia/Shanghai")
    plan = {"phase": "response", "purpose": "plan_day", "call_id": "plan", "attempt": 1,
            "error": str(caught.value), "response": raw}
    decision = {key: value for key, value in plan.items() if key != "purpose"}
    decision["protocol_feedback"] = _feedback(path=("memories",))

    def problems(data):
        event = _event(1, "model_response", agent_id=1, data=data)
        return build_quality_report([event], [], _manifest(), sample_display_names())["memory_references"]["problems"]

    assert problems(plan) == []
    assert problems(decision) == [{"code": "memory_protocol_error", "event_id": "event-1", "agent_id": 1}]


def test_actual_unpriced_model_keeps_tokens_but_does_not_invent_cost():
    response = _tool_response("call", _usage())
    response["response"]["model"] = "another-model"
    events = [_event(1, "model_request", data={"call_id": "call"}),
              _event(2, "model_response", data=response),
              _event(3, "run_ended", occurred_at=200)]

    usage = build_quality_report(events, [], _manifest(end=200), sample_display_names())["model_usage"]

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

    usage = build_quality_report(events, [], _manifest(end=200), sample_display_names())["model_usage"]

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

    usage = build_quality_report(events, [], _manifest(end=200), sample_display_names())["model_usage"]

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

    memory = build_quality_report(events, [], _manifest(end=200), sample_display_names())["memory_references"]

    assert memory == {"memory_update_events": 0, "persisted_episode_ids": 0, "write_points_checked": 0,
                      "problem_count": 1, "problems": [
                          {"code": "conclusion_without_memory_update", "event_id": "event-6", "agent_id": 1}]}


def test_memory_quality_reads_current_control_items_and_structured_errors():
    """只写看法也要求写记忆；协议问题按结构化路径识别，旧请求字段被直接拒绝。"""
    view = {"type": "relationship", "about": "王磊", "text": "王磊人不错。", "importance": 0.5, "keywords": ["同事"]}
    name = "等待（1月5日 00:00 起）"
    requests = {
        "views_only": {"items": [{"activity_name": name, "summary": "等完了。", "memories": [], "views": [view]}],
                       "errands": []},
        "nothing": {"items": [{"activity_name": name, "summary": "等完了。", "memories": [], "views": []}],
                    "errands": []},
        "with_memory": _conclusion(_memory()),
    }
    events = [_event(index + 1, "action_requested", agent_id=1, decision_id=label, action_id=f"action-{label}",
                     data={"tool_name": "conclude_activity", "request": request})
              for index, (label, request) in enumerate(requests.items())]
    views_error = _tool_response("call", _usage())
    views_error["error"] = "invalid conclude_activity arguments: items.0.views.0.about Field required"
    views_error["protocol_feedback"] = _feedback(path=("items", 0, "views", 0, "about"))
    events += [_event(len(events) + 1, "model_response", agent_id=1, decision_id="protocol", data=views_error),
               _event(len(events) + 2, "run_ended", occurred_at=200)]

    memory = build_quality_report(events, [], _manifest(end=200), sample_display_names())["memory_references"]

    assert memory["problems"] == [
        *({"code": "memory_protocol_error", "event_id": f"event-{len(requests) + 1}", "agent_id": 1},),
        *({"code": "conclusion_without_memory_update", "event_id": f"event-{index + 1}", "agent_id": 1}
          for index, label in enumerate(requests) if label in {"views_only", "with_memory"}),
    ]


def test_a_conclusion_that_only_changes_errands_is_no_memory_write_point():
    """个人事务变更不是记忆写入点（docs/design/04-simulation-engine.md、3.4 第 2 项）：只记下一条事务、没有 memories 与
    views 的收尾不要求同一决策写出收尾记忆，不报 conclusion_without_memory_update；同样带着这条事务变更、另写了一条记忆的
    收尾照常要求，只有它被报出。"""
    added = {"change": "新增", "name": "买牛奶", "outcome": "取得", "item": "纯牛奶", "quantity": 1, "memo": None,
             "remind_time": None, "reason": None}
    requests = {"errands_only": {**_conclusion(), "errands": [added]},
                "errands_and_memory": {**_conclusion(_memory()), "errands": [added]}}
    events = [_event(index + 1, "action_requested", agent_id=1, decision_id=label, action_id=f"action-{label}",
                     data={"tool_name": "conclude_activity", "request": request})
              for index, (label, request) in enumerate(requests.items())]
    events.append(_event(len(events) + 1, "run_ended", occurred_at=200))

    memory = build_quality_report(events, [], _manifest(end=200), sample_display_names())["memory_references"]

    assert memory["problems"] == [{"code": "conclusion_without_memory_update", "event_id": "event-2", "agent_id": 1}]


def test_a_conclusion_recorded_without_errands_is_an_integrity_fault():
    """质量报告经 control_items 读每一条 conclude_activity 请求（docs/design/04-simulation-engine.md、2.3）：带 items 而缺
    请求级 errands 的旧形状记录是完整性故障。同一条请求带着 errands（空列表）时报告照常生成、没有记忆问题。"""
    def report(request: dict) -> dict:
        """只有这一条收尾请求与运行终点的事件集的质量报告。"""
        events = [_event(1, "action_requested", agent_id=1, decision_id="decision", action_id="action",
                         data={"tool_name": "conclude_activity", "request": request}),
                  _event(2, "run_ended", occurred_at=200)]
        return build_quality_report(events, [], _manifest(end=200), sample_display_names())

    current = _conclusion()
    assert report(current)["memory_references"]["problem_count"] == 0
    with pytest.raises(IntegrityError, match="conclude_activity request lacks errands"):
        report({"items": current["items"]})


def test_non_memory_tool_json_error_is_counted_without_memory_warning():
    """生活工具参数错误归入模型协议错误，不误报为记忆问题；conclude_activity.memories 的本地校验错误才是记忆协议错误。"""
    response = _tool_response("call", _usage())
    response["error"] = "invalid tool arguments for report_status"
    function = response["response"]["choices"][0]["message"]["tool_calls"][0]["function"]
    function.update(name="report_status", arguments="{invalid json")
    event = _event(1, "model_response", agent_id=1, decision_id="decision", data=response)
    quality = build_quality_report([event], [], _manifest(), sample_display_names())
    assert quality["model_usage"]["protocol_error_responses"] == 1
    assert quality["memory_references"]["problem_count"] == 0

    oversized = _tool_response("call", _usage())
    oversized["error"] = "invalid conclude_activity arguments: memories exceed eight entries in total"
    oversized["protocol_feedback"] = _feedback()
    event = _event(1, "model_response", agent_id=1, decision_id="decision", data=oversized)
    memory = build_quality_report([event], [], _manifest(), sample_display_names())["memory_references"]
    assert memory["problems"] == [{"code": "memory_protocol_error", "event_id": "event-1", "agent_id": 1}]


#: 周二 2026-01-06 08:00（上海）：一夜睡醒的那一刻。
_WOKE = int(datetime(2026, 1, 6, 8, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())


def _wake(sequence, at=_WOKE, activity_id="night", status="completed"):
    """环境写下的睡眠终局进度：快照是在 at 结束的已完成睡眠（status 不是 completed 时没有结束时刻）。"""
    snapshot = {"kind": "sleep", "status": status, "agent_id": 1, "ended_at": at if status == "completed" else None}
    return _event(sequence, "activity_progress", occurred_at=at, agent_id=1, activity_id=activity_id,
                  data={"activity": snapshot})


def _reflected(sequence, identity="r1", *, at=_WOKE, date="2026-01-05", decision_id="woke", **changes):
    """取观察之前的就寝反思登记：来源观察为空，写入点恰为 reflection，带归属日期与所属睡眠。"""
    data = {**_memory_update(identity, None, decision_id, ("reflection",)), "memory_sha256": "a" * 64,
            "episodes": [{"id": identity, "type": "reflection", "text": "想早点睡。",
                          "epistemic_status": "subjective", "source": "reflection"}],
            "reflection": {"date": date, "sleep_activity_id": "night"}, **changes}
    return _event(sequence, "memory_updated", occurred_at=at, agent_id=1, decision_id=decision_id, data=data)


def _observed(sequence, at=_WOKE, decision_id="woke"):
    """一次决策的冻结观察，事实窗口为空。"""
    return _event(sequence, "observation", occurred_at=at, agent_id=1, decision_id=decision_id,
                  data={"verified_experiences": []})


def _memory_problems(events):
    """质量报告的记忆来源问题（码与事件）。"""
    memory = build_quality_report(events, [], _manifest(), sample_display_names())["memory_references"]
    return [(item["code"], item["event_id"]) for item in memory["problems"]]


def test_reflection_before_the_observation_and_conclusion_after_it_are_each_matched():
    """@brief 睡醒那一步的两条登记各有来源：反思登记排在观察之前、对应当天第一次睡醒并记在前一天名下，
    不核对事实窗口；步尾登记对应同一决策的 conclude_activity 与观察。"""
    events = [_wake(1), _reflected(2), _observed(3), _concluded(4, "woke", _memory("睡得不太踏实。")),
              _event(5, "memory_updated", occurred_at=_WOKE, agent_id=1, decision_id="woke",
                     data={**_memory_update("m1", "event-3", "woke"), "reflection": None})]
    events[3] = events[3].model_copy(update={"occurred_at": _WOKE})
    memory = build_quality_report(events, [], _manifest(), sample_display_names())["memory_references"]
    assert memory == {"memory_update_events": 2, "persisted_episode_ids": 2, "write_points_checked": 2,
                      "problem_count": 0, "problems": []}


@pytest.mark.parametrize("case", ["no_wake", "wake_at_another_time", "sleep_not_completed", "later_wake_of_the_day",
                                  "filed_under_today", "filed_two_days_back", "after_the_observation"])
def test_reflection_registration_without_its_first_wake_up_of_the_day_has_no_source(case):
    """就寝反思登记只凭事件独立重算：找不到对应的睡醒、不是当天第一次睡醒、日期不是前一天、排在本决策的观察之后，
    各记一条 memory_update_without_source。"""
    events = {
        "no_wake": [_reflected(1)],
        "wake_at_another_time": [_wake(1, _WOKE - 60), _reflected(2)],
        "sleep_not_completed": [_wake(1, status="active"), _reflected(2)],
        "later_wake_of_the_day": [_wake(1, _WOKE - 5 * 3600, "first"), _wake(2), _reflected(3)],
        "filed_under_today": [_wake(1), _reflected(2, date="2026-01-06")],
        "filed_two_days_back": [_wake(1), _reflected(2, date="2026-01-04")],
        "after_the_observation": [_wake(1), _observed(2), _reflected(3)],
    }[case]
    assert _memory_problems(events) == [("memory_update_without_source", events[-1].event_id)]


def test_second_reflection_registration_at_the_same_moment_has_no_source():
    """同一人物同一时刻只能有一条反思登记：第二条记一条 memory_update_without_source，第一条不报。"""
    events = [_wake(1), _reflected(2, "r1"), _reflected(3, "r2"), _observed(4)]
    assert _memory_problems(events) == [("memory_update_without_source", "event-3")]


def test_a_wake_up_just_before_local_midnight_does_not_take_the_next_day_s_reflection():
    """本地日按运行清单的窗口时区分：前一天 23:59 的睡醒与当天 08:00 的睡醒不在同一天，后者仍是当天第一次。"""
    events = [_wake(1, _WOKE - 8 * 3600 - 60, "evening"), _wake(2), _reflected(3), _observed(4)]
    assert _memory_problems(events) == []
    same_day = [_wake(1, _WOKE - 8 * 3600, "midnight"), _wake(2), _reflected(3), _observed(4)]
    assert _memory_problems(same_day) == [("memory_update_without_source", "event-3")]


@pytest.mark.parametrize("change", [
    {"observation_event_id": "event-9"}, {"reflection": {"date": "2026-01-05"}},
    {"reflection": {"date": "", "sleep_activity_id": "night"}}, {"reflection": "2026-01-05"},
    {"reflection": {"date": "2026-01-05", "sleep_activity_id": "night", "extra": 1}},
])
def test_reflection_registration_with_a_malformed_binding_is_an_invalid_memory_update(change):
    """反思登记的来源观察必须为空，归属恰含日期与所属睡眠两个非空字符串；否则整条记为 invalid_memory_update。"""
    events = [_wake(1), _reflected(2, **change), _observed(3)]
    assert _memory_problems(events) == [("invalid_memory_update", "event-2")]


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
    memory = build_quality_report(events, [], _manifest(), sample_display_names())["memory_references"]
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
    memory = build_quality_report(events, [], _manifest(), sample_display_names())["memory_references"]
    assert memory["memory_update_events"] == 0
    assert [item["code"] for item in memory["problems"]] == [
        "invalid_memory_update", "conclusion_without_memory_update"]


def test_memory_update_must_carry_its_observation_fact_window():
    """记忆证据的观察身份与已核验经历窗口须等于同一决策绑定的观察，否则报 invalid_memory_update_source_window。"""
    events = [_event(1, "observation", agent_id=1, decision_id="decision", data={"verified_experiences": []}),
              _concluded(2, "decision", _memory()),
              _event(3, "memory_updated", agent_id=1, decision_id="decision",
                     data=_memory_update("m1", "event-absent", "decision"))]
    memory = build_quality_report(events, [], _manifest(), sample_display_names())["memory_references"]
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
    memory = build_quality_report(events, [], _manifest(), sample_display_names())['memory_references']
    assert memory['persisted_episode_ids'] == 1
    assert memory['problems'] == []


def test_valid_looking_observed_fact_must_equal_its_committed_source():
    source = _event(1, "environment_event", occurred_at=101, agent_id=1, data={
        "kind": "goods_received", "order_id": "order", "goods_id": "goods", "category": "meal", "quantity": 1})
    forged = {"kind": "received", "agent_id": 1, "activity_id": None, "source_event_id": "event-1",
              "event_seq": 1, "occurred_at": 101, "order_id": "order", "goods_id": "goods", "category": "meal",
              "quantity": 2}
    observation = _event(2, "observation", agent_id=1, decision_id="decision",
                         data={"verified_experiences": [forged]})
    quality = build_quality_report([source, observation], [], _manifest(), sample_display_names())["memory_references"]
    assert [item["code"] for item in quality["problems"]] == ["invalid_verified_experience_source"]


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
    memory = build_quality_report(events, [], _manifest(), sample_display_names())['memory_references']
    assert [item['code'] for item in memory['problems']] == ([] if valid else ['invalid_observed_result_reference'])


def test_decision_rhythm_counts_silent_boundaries_and_wake_reasons():
    """决策节律只透传边界与唤醒原因计数；自述两项计数已删除（报告版本 2 起），报告版本现为 4（版本 3 是一日运行保真的
    新节，版本 4 是个人事务与工作安排规格的两个新节）。"""
    events = [
        _event(1, "environment_event", agent_id=1, data={"kind": "body_settlement", DECISION_OFFERED_FIELD: False,
                                             WAKE_REASONS_FIELD: []}),
        _event(2, "environment_event", agent_id=1, data={"kind": "body_settlement", DECISION_OFFERED_FIELD: True,
                                             WAKE_REASONS_FIELD: ["planned_end", "delivery_arrival"]}),
        _event(3, "environment_event", agent_id=1, data={"kind": "body_settlement", DECISION_OFFERED_FIELD: True,
                                             WAKE_REASONS_FIELD: ["planned_end", "mystery"]}),
        _event(4, "environment_event", agent_id=1, data={"kind": "body_settlement"}),
        _event(5, "decision_started", agent_id=1, decision_id="decision-1", data=_started()),
        _event(6, "decision_started", agent_id=1, decision_id="decision-2", data=_started(["session_open"])),
        _event(7, "environment_event", agent_id=1, data={"kind": "decision_budget_exhausted",
                                                         "error_code": "ACTION_REQUEST_INVALID",
                                                         "next_decision_at": 1000}),
        _event(8, "run_ended", occurred_at=1100),
    ]

    report = build_quality_report(events, [], _manifest(), sample_display_names())
    rhythm = report["decision_rhythm"]

    assert report["schema_version"] == 9
    assert "decision_budget_exhausted_events" not in rhythm
    assert rhythm["body_settlement_events"] == 4
    assert rhythm["boundaries_with_decision_field"] == 3
    assert "boundaries_missing_decision_field" not in rhythm
    assert rhythm["silent_boundaries"] == 1
    assert rhythm["offered_boundaries"] == 2
    assert rhythm["wake_reason_events"] == 3
    assert rhythm["wake_reason_counts"] == {"delivery_arrival": 1, "planned_end": 2}
    assert rhythm["unrecognized_wake_reason_counts"] == {"mystery": 1}
    assert rhythm["decision_started_events"] == 2
    assert not {"decision_started_with_decision_statement", "decision_started_missing_decision_statement"} & set(
        rhythm)


def test_report_sections_are_the_documented_grouping_without_a_language_count():
    """报告的分组恰是记录引擎设计「质量报告」登记的十九组（docs/design/05-recording-engine.md），顺序相同，另有版本与
    运行身份；报告结构为 9：父运行各段的 lineage 紧随 acceleration；个人事务与工作安排规格的两个新组
    errand_metrics、conclusion_memories
    在报告与登记句里都紧随 day_plan_metrics（docs/design/01-architecture.md），没有任何事务事件与收尾记忆时两组
    都为 null。其中没有语言计数：一日运行保真裁决 D18 不作语言要求，人物写的正文、总结与记忆用中文还是英文不在报告里
    计数（docs/design/06-agent.md），一日运行保真新增的五节是输入警告、时间线标签、联系人回复守卫、名称类
    拒绝与截断。"""
    report = build_quality_report([_event(1, "run_ended", occurred_at=200)], [], _manifest(end=200), sample_display_names())
    documented = ['event_range', 'run_outcome', 'actions', 'activities', 'content_contact', 'app_pushes', 'conversion_efficiency', 'decision_rhythm', 'memory_references', 'model_usage', 'acceleration', 'lineage', 'day_plan_metrics', 'errand_metrics', 'conclusion_memories', 'input_warnings', 'timeline_labels', 'name_rejections', 'truncations']
    assert len(documented) == len(set(documented)) == 19
    assert documented[-4:] == ["input_warnings", "timeline_labels", "name_rejections", "truncations"]
    plan = documented.index("day_plan_metrics")
    assert documented[plan:plan + 3] == ["day_plan_metrics", "errand_metrics", "conclusion_memories"]
    assert list(report)[3:] == documented
    assert set(report) == {"schema_version", "run_id", "source_group_id", *documented}
    assert report["schema_version"] == 9
    assert (report["errand_metrics"], report["conclusion_memories"]) == (None, None)
    assert [key for key in report if any(word in key for word in ("language", "english", "chinese"))] == []


def test_decision_rhythm_counts_same_moment_rounds():
    """时间与能量规格第一章：同刻轮数按（人物, 模拟秒）计 decision_started，轮次原因取 decision_started。"""
    first, later = 200, 300

    def started(sequence, agent_id, occurred_at, reasons):
        return _event(sequence, "decision_started", occurred_at=occurred_at, agent_id=agent_id,
                      decision_id=f"decision-{sequence}", data=_started(reasons))

    events = [
        started(1, 1, first, ["session_open"]),
        started(2, 1, first, ["no_active_activity"]),
        started(3, 1, first, ["rejection_feedback"]),
        started(4, 2, first, ["session_open"]),
        _event(5, "environment_event", occurred_at=later, agent_id=1,
               data={"kind": "body_settlement", DECISION_OFFERED_FIELD: True,
                     WAKE_REASONS_FIELD: ["no_active_activity"]}),
        started(6, 1, later, ["planned_end"]),
        _event(7, "run_ended", occurred_at=1100),
    ]

    rhythm = build_quality_report(events, [], _manifest(), sample_display_names())["decision_rhythm"]

    assert rhythm["same_moment_round_counts"] == {"1": 2, "3": 1}
    assert rhythm["decision_started_events"] == 5
    assert rhythm["decision_started_wake_reason_counts"] == {
        "no_active_activity": 1, "planned_end": 1, "rejection_feedback": 1, "session_open": 2}
    assert rhythm["wake_reason_counts"] == {"no_active_activity": 1}
    assert rhythm["unrecognized_decision_started_wake_reason_counts"] == {}
    assert rhythm["unrecognized_wake_reason_counts"] == {}

    foreign = events[:-1] + [started(7, 2, later, ["mystery"]), _event(8, "run_ended", occurred_at=1100)]
    rhythm = build_quality_report(foreign, [], _manifest(), sample_display_names())["decision_rhythm"]
    assert rhythm["same_moment_round_counts"] == {"1": 3, "3": 1}
    assert rhythm["unrecognized_decision_started_wake_reason_counts"] == {"mystery": 1}


def test_errand_reminder_due_is_a_known_wake_reason():
    """个人事务提醒的唤醒原因 errand_reminder_due（docs/design/04-simulation-engine.md#simulation-engine-唤醒-日计划与个人事务）由质量报告按同一份足迹词表
    分桶：共同身体结算与 decision_started 里的它都计入已知原因，与同刻到期的当日目标提醒各计一次，未识别的原因为空。"""
    reasons = ["reminder_due"]
    events = [
        _event(1, "environment_event", agent_id=1, data={"kind": "body_settlement", DECISION_OFFERED_FIELD: True,
                                                         WAKE_REASONS_FIELD: reasons}),
        _event(2, "decision_started", agent_id=1, decision_id="decision-1", data=_started(reasons)),
        _event(3, "environment_event", agent_id=1, data={"kind": "body_settlement", DECISION_OFFERED_FIELD: True,
                                                         WAKE_REASONS_FIELD: ["reminder_due"]}),
        _event(4, "decision_started", agent_id=1, decision_id="decision-2", data=_started(["reminder_due"])),
        _event(5, "run_ended", occurred_at=1100),
    ]

    rhythm = build_quality_report(events, [], _manifest(), sample_display_names())["decision_rhythm"]

    assert rhythm["wake_reason_counts"] == {"reminder_due": 2}
    assert rhythm["decision_started_wake_reason_counts"] == {"reminder_due": 2}
    assert rhythm["unrecognized_wake_reason_counts"] == {}
    assert rhythm["unrecognized_decision_started_wake_reason_counts"] == {}
    assert rhythm["offered_boundaries"] == 2


def test_decision_rhythm_reports_zero_coverage_for_legacy_event_sets():
    events = [_event(1, "environment_event", agent_id=1, data={"kind": "body_settlement"}),
              _event(2, "decision_started", agent_id=1, decision_id="decision-1", data=_started()),
              _event(3, "run_ended", occurred_at=1100)]

    rhythm = build_quality_report(events, [], _manifest(), sample_display_names())["decision_rhythm"]

    assert rhythm["silent_boundaries"] == 0
    assert rhythm["offered_boundaries"] == 0
    assert "boundaries_missing_decision_field" not in rhythm
    assert rhythm["wake_reason_events"] == 0
    assert rhythm["wake_reason_counts"] == {}
    assert rhythm["unrecognized_wake_reason_counts"] == {}
    assert rhythm["decision_started_events"] == 1
    assert "decision_budget_exhausted_events" not in rhythm


def test_acceleration_sums_observed_wall_time_across_restore_branches():
    events = [_event(1, "observation", occurred_at=100, run_id="parent", wall_ms=2_000),
              _event(2, "run_ended", occurred_at=200, run_id="run", wall_ms=3_000)]

    acceleration = build_quality_report(events, [], _manifest(end=200), sample_display_names())["acceleration"]

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
        build_quality_report(events, [], _manifest(), sample_display_names())


def test_input_warnings_section_counts_the_frozen_manifest_warnings():
    """input_warnings 一节（docs/design/08-configuration.md「校验与输入冻结」）：按警告码闭集与人物统计清单冻结的警告，
    场景级警告另计；闭集之外的码（包括随人物卡片编译删除的那些）单列；清单没有记录（引擎直驱为 null，或旧清单没有这个键）
    时计数为 null 而不是 0；只报告，不放行。"""
    from worldmock.input_warnings import INPUT_WARNING_CODES

    def personal(agent_id):
        return {"code": "CONTENT_EMPTY_WITH_MEDIA_TASTES", "agent_id": agent_id, "message": "没有绑定内容",
                "details": {"media": ["短视频"], "habits": []}}

    scenario_level = {"code": "LOCATION_NAME_SUSPICIOUS", "agent_id": None, "message": "显示名带坐标",
                      "details": {"location_id": "poi", "name": "全家（31.2, 121.5）", "patterns": ["coordinates"]}}
    deleted = {"code": "CARD_FIELD_CONFLICT", "agent_id": 2, "message": "旧清单里的警告", "details": {}}
    warnings = [personal(1), personal(2), deleted, scenario_level]
    events = [_event(1, "run_ended", occurred_at=200)]

    section = build_quality_report(events, [], {**_manifest(end=200), "input_warnings": warnings}, sample_display_names())["input_warnings"]

    assert INPUT_WARNING_CODES == ("CONTENT_EMPTY_WITH_MEDIA_TASTES", "LOCATION_NAME_SUSPICIOUS")
    assert section["recorded"] is True and section["warning_count"] == 4
    assert section["code_counts"] == {"CONTENT_EMPTY_WITH_MEDIA_TASTES": 2, "LOCATION_NAME_SUSPICIOUS": 1}
    assert list(section["code_counts"]) == list(INPUT_WARNING_CODES)
    assert section["agent_counts"] == {"1": 1, "2": 2} and section["scenario_warning_count"] == 1
    assert section["unrecognized_code_counts"] == {"CARD_FIELD_CONFLICT": 1}
    for manifest in (_manifest(end=200), {**_manifest(end=200), "input_warnings": None}):
        missing = build_quality_report(events, [], manifest, sample_display_names())["input_warnings"]
        assert missing["recorded"] is False
        assert missing["warning_count"] is None and missing["code_counts"] is None
    empty = build_quality_report(events, [], {**_manifest(end=200), "input_warnings": []}, sample_display_names())["input_warnings"]
    assert empty["warning_count"] == 0 and set(empty["code_counts"].values()) == {0}


#: 截断回合写进模型响应证据的协议错误串（src/worldmock/agents/life_truncation.py）。
_TRUNCATION_ERROR = ("OUTPUT_TRUNCATED: model output reached max_tokens while reasoning, before any tool call "
                     "(finish_reason=length)")
#: 4a5b0e6 两次全天运行的真实截断记录（只省略请求与思考正文）。
_TRUNCATION_RUNS = json.loads((Path(__file__).resolve().parents[3] / 'tests' / "fixtures" / "dayrun_truncations.json")
                              .read_text(encoding="utf-8"))


def _truncated_response(call_id, usage, purpose=None):
    """思考用尽输出上限的模型响应：finish_reason=length，正文为空，没有工具调用，协议错误为 OUTPUT_TRUNCATED。"""
    data = {"phase": "response", "call_id": call_id, "attempt": 1, "error": _TRUNCATION_ERROR,
            "response": {"model": "deepseek-flash", "usage": usage, "choices": [
                {"finish_reason": "length", "index": 0, "message": {"content": "", "tool_calls": None}}]}}
    return {**data, "purpose": purpose} if purpose else data


def _truncation_usage(output, reasoning):
    """截断回复的 usage：输出 token 与其中的思考 token。"""
    return {"prompt_tokens": 100, "completion_tokens": output, "total_tokens": 100 + output,
            "completion_tokens_details": {"reasoning_tokens": reasoning}}


def test_truncations_count_length_responses_with_their_output_and_reasoning_tokens():
    """truncations 一节（docs/design/10-runtime.md、九）：只计 finish_reason=length 的提供商回复，每个调用身份
    一条（重复终态不重复计），按用途分列（人物决策记为 decision）；浪费的输出 token 与思考 token 取提供商实报的
    completion_tokens 与 completion_tokens_details.reasoning_tokens；正常回复与没有回复的传输失败不计入。"""
    events = [
        _event(1, "model_request", agent_id=1, decision_id="d1", data={"call_id": "call-1"}),
        _event(2, "model_response", agent_id=1, decision_id="d1",
               data=_truncated_response("call-1", _truncation_usage(8000, 8000))),
        _event(3, "model_request", agent_id=1, decision_id="d1", data={"call_id": "call-2"}),
        _event(4, "model_response", agent_id=1, decision_id="d1", data=_tool_response("call-2", _usage())),
        _event(5, "model_request", agent_id=1, decision_id="d2", data={"call_id": "call-3"}),
        _event(6, "model_response", agent_id=1, decision_id="d2",
               data=_truncated_response("call-3", _truncation_usage(8001, 7990))),
        _event(7, "model_response", agent_id=1, decision_id="d2",
               data=_truncated_response("call-3", _truncation_usage(8001, 7990))),
        _event(8, "model_request", agent_id=1, data={"call_id": "plan-1", "purpose": "plan_day"}),
        _event(9, "model_response", agent_id=1,
               data=_truncated_response("plan-1", _truncation_usage(500, 0), "plan_day")),
        _event(10, "model_request", agent_id=1, decision_id="d3", data={"call_id": "call-4"}),
        _event(11, "model_response", agent_id=1, decision_id="d3",
               data={"phase": "failure", "call_id": "call-4", "attempt": 1, "error": "TimeoutError()"}),
    ]

    report = build_quality_report(events, [], _manifest(end=200), sample_display_names())

    section = report["truncations"]
    assert section == {"truncated_responses": 3, "purpose_counts": {"decision": 2, "plan_day": 1},
                       "wasted_output_tokens": 16501, "reasoning_tokens": 15990,
                       "responses_missing_output_tokens": 0, "responses_missing_reasoning_tokens": 0,
                       "method": section["method"]}
    assert "finish_reason=length" in section["method"] and report["memory_references"]["problem_count"] == 0


def test_truncation_tokens_missing_from_a_response_stay_unknown_instead_of_zero():
    """某次截断的回复没有实报 token（没有 usage，或没有思考 token 明细）时对应合计为 null，缺失次数单列，不记为零；
    没有截断的运行次数与两项合计都是 0。"""
    no_details = {"prompt_tokens": 10, "completion_tokens": 8000, "total_tokens": 8010}
    events = [
        _event(1, "model_request", agent_id=1, decision_id="d1", data={"call_id": "call-1"}),
        _event(2, "model_response", agent_id=1, decision_id="d1", data=_truncated_response("call-1", None)),
        _event(3, "model_request", agent_id=1, decision_id="d1", data={"call_id": "call-2"}),
        _event(4, "model_response", agent_id=1, decision_id="d1", data=_truncated_response("call-2", no_details)),
    ]

    section = build_quality_report(events, [], _manifest(end=200), sample_display_names())["truncations"]

    assert section["truncated_responses"] == 2
    assert (section["wasted_output_tokens"], section["reasoning_tokens"]) == (None, None)
    assert (section["responses_missing_output_tokens"], section["responses_missing_reasoning_tokens"]) == (1, 2)
    none = build_quality_report([_event(1, "run_ended", occurred_at=200)], [], _manifest(end=200), sample_display_names())["truncations"]
    assert (none["truncated_responses"], none["purpose_counts"], none["wasted_output_tokens"],
            none["reasoning_tokens"]) == (0, {}, 0, 0)


@pytest.mark.parametrize(("label", "count", "tokens"), [pytest.param("white_collar", 3, 24001, id="white_collar"),
                                                        pytest.param("business", 26, 208000, id="business")])
def test_truncations_match_the_recorded_day_runs(label, count, tokens):
    """4a5b0e6 两次全天运行的真实模型响应记录（tests/fixtures/dayrun_truncations.json，只省略请求与思考正文）：
    truncations 与运行记录一致，WC 3 次、BIZ 26 次截断（规格 7.2「现状」3/177、26/599 的分子），全部是行动决策；截断回复
    的输出全是思考（浪费的输出 token 等于思考 token，WC 其中一次为 8001）；同一决策中截断之后的非截断回复不计入。"""
    run = _TRUNCATION_RUNS[label]
    events = [Event.model_validate(row) for row in run["events"]]

    section = build_quality_report(events, [], {"run_id": run["run_id"], "source_group_id": run["run_id"], "work_content_ids": {}, "window": run["window"],
                                            "lineage": []}, sample_display_names())["truncations"]

    assert section["truncated_responses"] == run["summary"]["truncated_responses"] == count < len(events)
    assert section["purpose_counts"] == {"decision": count}
    assert section["wasted_output_tokens"] == section["reasoning_tokens"] == tokens
    assert tokens == run["summary"]["truncated_output_tokens"] == run["summary"]["truncated_reasoning_tokens"]
    assert run["summary"]["finish_reasons"]["length"] == count


def test_quality_report_is_deterministic_for_the_same_authenticated_input():
    events = [_event(1, "run_ended", occurred_at=200)]

    first = build_quality_report(events, [], _manifest(end=200), sample_display_names())
    second = build_quality_report(events, [], _manifest(end=200), sample_display_names())

    assert first == second
    json.dumps(first, ensure_ascii=False, sort_keys=True, allow_nan=False)


@pytest.mark.parametrize("source,codes", [("reflection", []), ("activity_conclusion", ["invalid_memory_update"])])
def test_bedtime_reflection_snapshot_may_carry_its_source(source, codes):
    """@brief 反思登记的快照带来源 reflection；别的来源写法不是合法的主观快照。"""
    snapshot = {"id": "r1", "type": "reflection", "text": "睡前有点困", "epistemic_status": "subjective",
                "source": source}
    events = [_wake(1), _reflected(2, episodes=[snapshot]), _observed(3)]
    memory = build_quality_report(events, [], _manifest(), sample_display_names())["memory_references"]
    assert [item["code"] for item in memory["problems"]] == codes


def _progress(sequence, snapshot, start, end):
    """人物 1 一段活动进度：结算区间 [start, end) 与活动快照。"""
    return _event(sequence, "activity_progress", occurred_at=end, agent_id=1, activity_id=snapshot["activity_id"],
                  data={"activity": {"agent_id": 1, "location_id": "home", "data": {}, **snapshot},
                        "interval_start": start, "interval_end": end})


def _timeline_names(place, lifestyle, subject, title):
    """目录里的名称由入参给：一个地点、一个 app 和其中的一条内容，另有一个阅读 app（内容标题在书名号里，不检查）。
    名称表照目录行经 display_names 建出：在样例环境的行上照抄一行、换掉身份与名称。"""
    apps, contents, locations, goods, methods = sample_catalog_rows()
    app, location = apps[0], locations[0]
    content = next(row for row in contents if row.series is None)
    return display_names(
        [*apps, app.model_copy(update={"app_id": "platform", "name": lifestyle}),
         app.model_copy(update={"app_id": "reader", "name": "阅读"})],
        [*contents, content.model_copy(update={"content_id": subject, "title": title})],
        [*locations, location.model_copy(update={"location_id": "loc-x", "name": place, "branch_label": None})],
        goods, methods)


def _timeline_events(subject):
    """四段真实活动区间：步行去那个地点、在那个 app 里看那条内容、在阅读 app 里看同一条内容、睡觉。"""
    return [
        _progress(2, {"activity_id": "a-go", "kind": "travel", "status": "completed", "travel_method_id": "walking",
                      "destination_id": "loc-x"}, 100, 200),
        _progress(3, {"activity_id": "a-life", "kind": "interact",
                      "data": {"action": "interact", "app_id": "platform", "content_id": subject}}, 200, 300),
        _progress(4, {"activity_id": "a-content", "kind": "interact",
                      "data": {"action": "interact", "app_id": "reader", "content_id": subject}}, 300, 400),
        _progress(5, {"activity_id": "a-wait", "kind": "sleep"}, 400, 500),
    ]


def _labels_report(place, lifestyle, title):
    """同批事件经唯一的渲染器投影后的时间线标签检查；名称由入参给。"""
    names = _timeline_names(place, lifestyle, "topic", title)
    return build_quality_report(_timeline_events("topic"), [], _manifest(), names)["timeline_labels"]


def test_timeline_labels_count_english_kind_names_and_internal_ids_in_the_rendered_timeline():
    """时间线标签检查（docs/design/10-runtime.md）：用唯一的时间线渲染器投影同批事件，标签里恰为英文种类名的
    片段与内部标识形态的片段各计一项；书名号与引号里的作品标题、邮件主题不查；名称都是中文时两项为零。"""
    crafted = _labels_report("poi-node-11991340957", "interact", "work")
    assert (crafted["rows_checked"], crafted["english_kind_names"], crafted["internal_ids"]) == (4, 1, 1)
    assert [(item["agent_id"], item["date"], item["interval"], item["label"], item["item"], item["text"])
            for item in crafted["findings"]] == [
        (1, "1970-01-01", "08:01–08:03", "步行去poi-node-11991340957", "internal_id", "poi-node-11991340957"),
        (1, "1970-01-01", "08:03–08:05", "使用interact《work》", "english_kind_name", "interact")]
    clean = _labels_report("中山公园", "瑜伽", "周报")
    assert clean == {"rows_checked": 4, "english_kind_names": 0, "internal_ids": 0, "findings": []}


def test_timeline_labels_count_hexadecimal_and_hash_identifiers_as_internal_ids():
    """内部标识形态还包括十二位以上的十六进制串与 # 加六位十六进制（docs/design/10-runtime.md）：地名与 app 名里
    漏出的这两种形态经唯一的渲染器投影后各计一项内部标识；十一位的十六进制串与 # 加五位不是标识形态，不计。"""
    crafted = _labels_report("便利店#a1b2c3", "3e71a031bb3d", "周报")
    assert (crafted["english_kind_names"], crafted["internal_ids"]) == (0, 2)
    assert [(item["label"], item["item"], item["text"]) for item in crafted["findings"]] == [
        ("步行去便利店#a1b2c3", "internal_id", "#a1b2c3"),
        ("使用3e71a031bb3d《周报》", "internal_id", "3e71a031bb3d")]
    near = _labels_report("便利店#a1b2c", "3e71a031bb3", "周报")
    assert (near["english_kind_names"], near["internal_ids"], near["findings"]) == (0, 0, [])


def test_timeline_label_that_leaks_the_english_query_kind_is_counted():
    """查询是第五种活动：时间线标签里漏出的英文 query 与其余四种的英文种类名一样计一项。"""
    report = _labels_report("中山公园", "query", "周报")
    assert (report["english_kind_names"], report["internal_ids"]) == (1, 0)
    assert [(item["label"], item["item"], item["text"]) for item in report["findings"]] == [
        ("使用query《周报》", "english_kind_name", "query")]


def _protocol_error(sequence, call_id, code, purpose=None):
    """一次带结构化协议错误证据的模型终态响应。"""
    response = _tool_response(call_id, _usage())
    response["error"] = "Text is not used to classify protocol errors."
    response["protocol_feedback"] = _feedback(code, ("tool_calls", 0))
    if purpose is not None:
        response["purpose"] = purpose
    return _event(sequence, "model_response", agent_id=1, decision_id=f"decision-{call_id}", data=response)


def _rejected(sequence, code, occurred_at):
    """一条业务拒绝事件（action_rejected）。"""
    data = {"error_code": code}
    return _event(sequence, "action_rejected", occurred_at=occurred_at, agent_id=1, action_id=f"action-{sequence}",
                  data=data)


def test_name_rejections_are_counted_apart_from_other_protocol_errors():
    """名称类拒绝一节（docs/design/03-resource-engine.md、第九章）：宿主的名称类纠错按 protocol_feedback 的码逐码计数，
    其余协议错误（非法 JSON、没有工具调用、参数形状）另计，工作验收等有 purpose 的调用不算；环境的名称类拒绝按码计数，
    同一人物同一秒的前两次记为免计；名称类以外的拒绝码不进这一节。"""
    events = [
        _protocol_error(1, "c1", "NAME_NOT_FOUND"),
        _protocol_error(2, "c2", "ACTION_NOT_OFFERED"),
        _protocol_error(3, "c3", "SUBJECT_NOT_KNOWN"),
        _protocol_error(4, "c4", "ARGUMENTS_NOT_JSON"),
        _protocol_error(5, "c5", "TOOL_CALL_REQUIRED"),
        _protocol_error(6, "c6", "ARGUMENTS_INVALID"),
        _protocol_error(7, "c7", "NAME_NOT_FOUND", purpose="plan_day"),
        _rejected(8, "SUPPLY_NOT_FOUND", 500), _rejected(9, "SUPPLY_NOT_FOUND", 500),
        _rejected(10, "ACTION_REQUEST_INVALID", 500), _rejected(11, "TASK_NOT_FOUND", 500),
        _rejected(12, "SUPPLY_NOT_FOUND", 600),
    ]
    report = build_quality_report(events, [], _manifest(end=1100), sample_display_names())["name_rejections"]
    assert (report["host_protocol_errors"], report["other_protocol_errors"]) == (3, 3)
    assert report["host_codes"] == {"NAME_AMBIGUOUS": 0, "NAME_NOT_FOUND": 1, "SUBJECT_NOT_KNOWN": 1,
                                     "ACTION_NOT_OFFERED": 1}
    assert report["other_protocol_codes"] == {"ARGUMENTS_INVALID": 1, "ARGUMENTS_NOT_JSON": 1, "TOOL_CALL_REQUIRED": 1}
    assert report["environment_rejections"] == 4
    assert report["environment_codes"] == {"SUPPLY_NOT_FOUND": 3, "TASK_NOT_FOUND": 1}
    assert report["environment_free_rejections"] == 3


def test_protocol_quality_uses_structured_evidence_without_log_inference():
    """误导性错误文本不改变真实分类；没有结构化协议错误的服务日志不冒充名称拒绝。"""
    response = _tool_response("structured", _usage())
    response["error"] = "NAME_NOT_FOUND: memories text is misleading"
    response["protocol_feedback"] = _feedback("ARGUMENTS_NOT_JSON", ("tool_calls", 0, "function", "arguments"))
    raw = _tool_response("unstructured", _usage())
    raw["error"] = "NAME_NOT_FOUND: memories is only a log string"
    events = [_event(1, "model_response", agent_id=1, data=response),
              _event(2, "model_response", agent_id=1, data=raw)]
    report = build_quality_report(events, [], _manifest(), sample_display_names())
    assert report["name_rejections"]["host_protocol_errors"] == 0
    assert report["name_rejections"]["other_protocol_codes"] == {"ARGUMENTS_NOT_JSON": 1}
    assert report["memory_references"]["problem_count"] == 0


def test_work_progress_does_not_create_conversion_efficiency():
    """@brief 工作进度只留下真实秒数，质量报告转换效率只记录食物热量利用。"""
    events = [_content_event(1, after=10, app_id=None, content="topic-sales", category="work_study",
                             action_id="work", location_id="office")]
    activity = _activity("phone-1", "action", 10, "active", data={"category": "work_study"})
    report = build_quality_report(events, [activity], {**_manifest(), "work_content_ids": {"1": ["topic-sales"]}},
                                  sample_display_names())
    assert set(report["conversion_efficiency"]) == {"food", "method"}
    assert report["conversion_efficiency"]["food"]["weighted_mean"] is None



def test_drink_receipt_does_not_count_as_calorie_conversion():
    events = [_event(1, "environment_event", agent_id=1,
                     data={"kind": "goods_received", "category": "drink", "provided": 400, "applied": 300})]
    report = build_quality_report(events, [], _manifest(), sample_display_names())
    assert report["conversion_efficiency"]["food"]["sample_count"] == 0
    assert report["conversion_efficiency"]["food"]["unavailable_or_incomplete_reason"] == "no_food_receipt"
    assert not {"work_topics", "contact_reply_guard"} & report.keys()
    assert "by_action" not in report["content_contact"]


def test_old_wake_name_does_not_prove_reflection_without_sleep_completion():
    events = [_event(1, "observation", agent_id=1, decision_id="decision",
                     data={"verified_experiences": [], "wake_reasons": ["session_open"]}),
              _event(2, "memory_updated", agent_id=1, decision_id="decision",
                     data=_memory_update("r1", "event-1", "decision", trigger=("reflection",)))]
    problems = build_quality_report(events, [], _manifest(), sample_display_names())["memory_references"]["problems"]
    assert [item["code"] for item in problems] == ["memory_update_without_source"]
