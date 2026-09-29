from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from worldmock.evaluation.behavior_metrics import (
    TIME_USE_REFERENCE_MINUTES,
    UNREPORTED_CODE_KEY,
    _js_distance,
    behavior_metrics_report,
)
from worldmock.footprints.models import canonical_json
from worldmock.life.interaction_menu import WAYS

ZONE = ZoneInfo("Asia/Shanghai")
CODE_A = "a" * 64
CODE_B = "b" * 64


def _clock(day: int, hour: int, minute: int = 0) -> int:
    """@brief 以 2026-01-05 为第 0 天生成 Asia/Shanghai 本地时刻的 epoch 秒。"""
    return int(datetime(2026, 1, 5 + day, hour, minute, tzinfo=ZONE).timestamp())


def _event(
    event_type: str,
    occurred_at: int,
    *,
    agent_id: int = 1,
    data: dict[str, Any] | None = None,
    run_id: str = "run-1",
    decision_id: str | None = None,
    activity_id: str | None = None,
    code_sha256: str = CODE_A,
    event_seq: int = 0,
) -> dict[str, Any]:
    """@brief 构造最小足迹事件 dict 夹具。"""
    return {"event_id": f"e{event_seq}-{event_type}-{agent_id}", "event_type": event_type,
            "occurred_at": occurred_at, "agent_id": agent_id, "decision_id": decision_id,
            "activity_id": activity_id, "data": data or {}, "run_id": run_id,
            "event_seq": event_seq, "code_sha256": code_sha256}


def _progress(
    event_seq: int,
    start: int,
    end: int,
    kind: str,
    *,
    agent_id: int = 1,
    run_id: str = "run-1",
    activity_id: str | None = None,
    snapshot_extra: dict[str, Any] | None = None,
    data_extra: dict[str, Any] | None = None,
    code_sha256: str = CODE_A,
) -> dict[str, Any]:
    """@brief 构造带活动快照与结算区间的 activity_progress 事件。"""
    activity_id = activity_id or f"act-{kind}-{agent_id}-{event_seq}"
    snapshot = {"activity_id": activity_id, "agent_id": agent_id, "kind": kind,
                "status": "active", "started_at": start}
    snapshot.update(snapshot_extra or {})
    data = {"interval_start": start, "interval_end": end, "activity": snapshot}
    data.update(data_extra or {})
    return _event("activity_progress", end, agent_id=agent_id, data=data, run_id=run_id,
                  activity_id=activity_id, code_sha256=code_sha256, event_seq=event_seq)


def _body(seq: int, start: int, end: int, activity_ids: list[str], **fields: Any) -> dict[str, Any]:
    """@brief 构造每人每区间唯一的共同身体结算。"""
    data = {"kind": "body_settlement", "interval_start": start, "interval_end": end,
            "activity_ids": activity_ids, **fields}
    return _event("environment_event", end, data=data, event_seq=seq)


def _model_request(
    event_seq: int,
    call_id: str,
    user_texts: list[str],
    *,
    agent_id: int = 1,
    purpose: str | None = None,
    usage: dict[str, int] | None = None,
    code_sha256: str = CODE_A,
    decision_id: str | None = None,
) -> dict[str, Any]:
    """@brief 构造决策 model_request 事件（messages 只含 user 消息；决策身份缺省取 call_id）。"""
    data: dict[str, Any] = {
        "phase": "request", "call_id": call_id, "attempt": 1,
        "request": {"model": "test-model",
                    "messages": [{"role": "user", "content": text} for text in user_texts]},
        "response": None, "error": None,
    }
    if purpose:
        data["purpose"] = purpose
    if usage:
        data["usage"] = usage
    return _event("model_request", _clock(0, 8) + event_seq, agent_id=agent_id, data=data,
                  decision_id=decision_id or call_id, event_seq=event_seq, code_sha256=code_sha256)


def _model_response(event_seq: int, call_id: str, prompt_tokens: int) -> dict[str, Any]:
    """@brief 构造与请求配对的 model_response 事件（usage 在 response 内）。"""
    return _event("model_response", _clock(0, 8, event_seq),
                  data={"phase": "response", "call_id": call_id, "attempt": 1, "request": None,
                        "response": {"usage": {"prompt_tokens": prompt_tokens}}, "error": None},
                  decision_id=call_id, event_seq=event_seq)


def _action(event_seq: int, decision_id: str, tool_name: str,
            request: dict[str, Any] | None = None, action_id: str | None = None) -> dict[str, Any]:
    """@brief 构造 action_requested 事件（动作身份缺省为 None，受理事件按身份回链）。"""
    event = _event("action_requested", _clock(0, 8, event_seq),
                   data={"tool_name": tool_name, "request": request or {}, "request_sha256": "0" * 64,
                         "reused": False},
                   decision_id=decision_id, event_seq=event_seq)
    event["action_id"] = action_id
    return event


def _start(event_seq: int, decision_id: str, action_id: str, way: str, target: str) -> dict[str, Any]:
    """@brief 构造一项 start_activity 请求：对象、动词与方式三数组（动词由方式唯一确定）。"""
    request = {"targets": [target], "actions": [WAYS[way][0]], "ways": [way]}
    return _action(event_seq, decision_id, "start_activity", request, action_id)


def _accepted(event_seq: int, decision_id: str, action_id: str, *kinds: str) -> dict[str, Any]:
    """@brief 构造 action_accepted 事件：一项时带 activity 快照，两项时带 data.activities（与引擎受理事件同形）。"""
    snapshots = [{"activity_id": f"{action_id}-{index}", "kind": kind} for index, kind in enumerate(kinds)]
    data: dict[str, Any] = {"status": "in_progress", "data": {}}
    if len(snapshots) == 1:
        data["activity"] = snapshots[0]
    else:
        data["data"] = {"activities": snapshots}
    event = _event("action_accepted", _clock(0, 8, event_seq), data=data, decision_id=decision_id,
                   event_seq=event_seq)
    event["action_id"] = action_id
    return event


def _memory(episodes: list[dict[str, str]], *, event_seq: int = 90) -> dict[str, Any]:
    """@brief 构造带规范主观条目快照的 memory_updated 事件。"""
    snapshots = [{"id": f"episode-{index}", "epistemic_status": "subjective", **episode}
                 for index, episode in enumerate(episodes)]
    return _event("memory_updated", _clock(0, 20) + event_seq,
                  data={"operation": "append_episodes", "episodes": snapshots}, event_seq=event_seq)


def _report(events: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    """@brief 取单代码组的报告（默认全部事件同 code）。"""
    return behavior_metrics_report(events, **kwargs)[CODE_A]


def test_observation_repetition_gate_pass_and_fail() -> None:
    """@brief 门禁按（人物，模拟秒）计：同一 user 文本出现在两个不同秒的决策中必须失败，文本各不相同时通过。"""
    distinct = [
        _model_request(1, "d1", ["观察 A：天气晴朗"]),
        _model_request(2, "d2", ["观察 B：收到邮件"]),
    ]
    gate = _report(distinct)["gates"]["observation_repetition"]
    assert gate["value"] == 1 and gate["pass"] is True
    assert gate["duplicated_observation_count"] == 0
    assert gate["user_message_count"] == 2

    duplicated = [
        _model_request(1, "call-1", ["观察 A：天气晴朗"], decision_id="d1"),
        _model_request(2, "call-2", ["观察 A：天气晴朗"], decision_id="d2"),
    ]
    assert duplicated[0]["occurred_at"] != duplicated[1]["occurred_at"]
    gate = _report(duplicated)["gates"]["observation_repetition"]
    assert gate["value"] == 2 and gate["pass"] is False
    assert gate["duplicated_observation_count"] == 1


def _same_second(events: list[dict[str, Any]], second: int) -> list[dict[str, Any]]:
    """@brief 把请求夹具放到同一模拟秒（同一决策或同秒续接链内的请求共享世界时刻）。"""
    for event in events:
        event["occurred_at"] = second
    return events


def test_observation_gate_counts_action_loop_resend_once() -> None:
    """@brief 同一决策的行动循环内重发同一观察只计一次（E7-1、VF2）。"""
    events = _same_second([_model_request(seq, f"call-{seq}", ["观察 A：天气晴朗"], decision_id="d1")
                           for seq in (1, 2, 3)], _clock(0, 8))
    gate = _report(events)["gates"]["observation_repetition"]
    assert gate["value"] == 1 and gate["pass"] is True
    assert gate["duplicated_observation_count"] == 0
    assert gate["user_message_count"] == 3


def test_observation_gate_allows_same_second_continuation_chain_reuse() -> None:
    """@brief 同一人物同一秒的相邻决策续接复用链首 user：多个 decision_id 共享一个链键，门禁通过。"""
    head = '[{"layer":"工具层"},{"layer":"进度层","events":[]}]'
    chain = _same_second([
        _model_request(1, "call-1", [head], decision_id="round-1"),
        _model_request(2, "call-2", [head], decision_id="round-2"),
        _model_request(3, "call-3", [head], decision_id="round-3"),
    ], _clock(0, 8))
    gate = _report(chain)["gates"]["observation_repetition"]
    assert gate["value"] == 1 and gate["pass"] is True
    assert gate["duplicated_observation_count"] == 0
    assert gate["user_message_count"] == 3


def test_observation_gate_fails_on_cross_second_or_cross_agent_duplicate() -> None:
    """@brief 同一 user 文本跨秒（时间推进后）或跨人物重复出现仍失败。"""
    text = '[{"layer":"环境层"}]'
    cross_second = _same_second([_model_request(1, "call-1", [text], decision_id="d1")], _clock(0, 8))
    cross_second += _same_second([_model_request(2, "call-2", [text], decision_id="d2")], _clock(0, 8) + 1)
    gate = _report(cross_second)["gates"]["observation_repetition"]
    assert gate["value"] == 2 and gate["pass"] is False
    assert gate["duplicated_observation_count"] == 1

    cross_agent = _same_second([
        _model_request(1, "call-1", [text], decision_id="d1", agent_id=1),
        _model_request(2, "call-2", [text], decision_id="d2", agent_id=2),
    ], _clock(0, 8))
    gate = _report(cross_agent)["gates"]["observation_repetition"]
    assert gate["value"] == 2 and gate["pass"] is False


def test_observation_gate_same_moment_rounds_are_separate_decisions() -> None:
    """@brief 同一秒的两轮决策各有 decision_id，观察文本不同时门禁通过。"""
    rounds = _same_second([
        _model_request(1, "call-1", ["观察：刚醒，还没有进行中的活动"], decision_id="round-1"),
        _model_request(2, "call-2", ["观察：上一轮开始被拒，仍没有进行中的活动"], decision_id="round-2"),
    ], _clock(0, 8))
    gate = _report(rounds)["gates"]["observation_repetition"]
    assert gate["value"] == 1 and gate["pass"] is True
    assert gate["duplicated_observation_count"] == 0


def test_observation_gate_fails_closed_without_user_messages() -> None:
    """@brief 无任何决策 user 消息时门禁按不通过处理并注明未提供。"""
    events = [_event("decision_started", _clock(0, 8), decision_id="d0")]
    report = _report(events)
    gate = report["gates"]["observation_repetition"]
    assert gate["value"] is None and gate["pass"] is False
    assert any("user 消息" in note for note in report["notes"])


def test_silent_boundary_ratio_exact_values() -> None:
    """@brief 静默占比 0.6 通过、0.4 失败；sleep 边界不计入清醒分母。"""
    def waking(seq: int, offered: bool) -> dict[str, Any]:
        return _body(seq, _clock(0, seq, 0), _clock(0, seq, 10), [f"watch-{seq}"],
                     decision_offered=offered)

    mostly_silent = [waking(1, False), waking(2, False), waking(3, False),
                     waking(4, True), waking(5, True)]
    gate = _report(mostly_silent)["gates"]["silent_boundary_ratio"]
    assert gate["value"] == 0.6 and gate["pass"] is True
    assert gate["silent_boundaries"] == 3 and gate["known_boundaries"] == 5

    mostly_offered = [waking(1, False), waking(2, False), waking(3, True),
                      waking(4, True), waking(5, True)]
    gate = _report(mostly_offered)["gates"]["silent_boundary_ratio"]
    assert gate["value"] == 0.4 and gate["pass"] is False

    sleep_boundary = _body(9, _clock(0, 1), _clock(0, 2), ["sleep-9"], decision_offered=True)
    sleep_progress = _progress(10, _clock(0, 1), _clock(0, 2), "sleep", activity_id="sleep-9")
    gate = _report(mostly_silent + [sleep_boundary, sleep_progress])["gates"]["silent_boundary_ratio"]
    assert gate["known_boundaries"] == 5


def test_decision_token_median_from_paired_response() -> None:
    """@brief 中位数取自配对 model_response 的 prompt token；工作验收请求排除。"""
    events = [
        _model_request(1, "d1", ["观察 1"]),
        _model_response(2, "d1", 100),
        _model_request(3, "d2", ["观察 2"]),
        _model_response(4, "d2", 300),
        _model_request(5, "d3", ["观察 3"], usage={"prompt_tokens": 200}),
        _model_request(6, "d4", ["验收"], purpose="work_review"),
        _model_response(7, "d4", 99999),
    ]
    report = _report(events)["reports"]["decision_prompt_token_median"]
    assert report["value"] == 200 and report["sample_size"] == 3
    assert report["target_reference"] == 15000


def test_plan_day_requests_are_not_decision_requests() -> None:
    """@brief 零点计划 plan_day 的请求不是人物决策：不进决策 token 中位数，也不进观察重复门禁（即便文本相同）。"""
    events = [
        _model_request(1, "d1", ["观察 1"]),
        _model_response(2, "d1", 100),
        _model_request(3, "plan-day:1:2026-01-05:attempt:1", ["观察 1"], purpose="plan_day"),
        _model_response(4, "plan-day:1:2026-01-05:attempt:1", 99999),
        _model_request(5, "plan-day:1:2026-01-06:attempt:1", ["观察 1"], purpose="plan_day"),
    ]

    report = _report(events)

    assert report["reports"]["decision_prompt_token_median"]["sample_size"] == 1
    assert report["reports"]["decision_prompt_token_median"]["value"] == 100
    gate = report["gates"]["observation_repetition"]
    assert gate["value"] == 1 and gate["user_message_count"] == 1


def test_autonomous_decision_rate_counts_continue_and_wait_as_passive() -> None:
    """@brief 最终动作为进度播报或等待的决策不计入自主决策；等待按受理时实际开始的活动 kind=wait 判定
    （docs/design/07-evaluation.md「2.2 报告项」），不按请求里的动词或方式。"""
    events = [
        _event("decision_started", _clock(0, 8, 1), decision_id="d1"),
        _action(2, "d1", "report_status", {"items": [{"activity_name": "当前会话", "summary": "继续。"}]}),
        _event("decision_started", _clock(0, 8, 3), decision_id="d2"),
        _start(4, "d2", "a2", "等待", "自己"),
        _accepted(5, "d2", "a2", "wait"),
        _event("decision_started", _clock(0, 8, 6), decision_id="d3"),
        _start(7, "d3", "a3", "接着看", "内容：节目"),
        _accepted(8, "d3", "a3", "watch"),
        _event("decision_started", _clock(0, 8, 9), decision_id="d4"),
    ]
    report = _report(events)["reports"]["effective_autonomous_decision_rate"]
    assert report["value"] == 1 / 3
    assert report["decision_count"] == 4 and report["decisions_with_action"] == 3
    assert report["autonomous_decisions"] == 1


def test_wait_is_judged_by_the_started_activity_kind_not_the_request() -> None:
    """@brief 选了等待但被拒绝（没有开始等待）不算被动；等待与出行一起开始也不是只在等待，二者都计自主。"""
    events = [
        _event("decision_started", _clock(0, 8, 1), decision_id="d1"),
        _start(2, "d1", "a1", "等待", "自己"),
        _event("action_rejected", _clock(0, 8, 3), decision_id="d1", event_seq=3,
               data={"error_code": "ACTIVITY_CONCLUSION_REQUIRED"}) | {"action_id": "a1"},
        _event("decision_started", _clock(0, 8, 4), decision_id="d2"),
        _action(5, "d2", "start_activity", {"targets": ["城西办公室", "自己"], "actions": ["travel", "interact"],
                                            "ways": ["步行", "等待"]}, "a2"),
        _accepted(6, "d2", "a2", "travel", "wait"),
        _event("decision_started", _clock(0, 8, 7), decision_id="d3"),
        _start(8, "d3", "a3", "等待", "自己"),
        _accepted(9, "d3", "a3", "wait"),
    ]
    report = _report(events)["reports"]["effective_autonomous_decision_rate"]
    assert report == {"value": 2 / 3, "decision_count": 3, "decisions_with_action": 3,
                      "autonomous_decisions": 2, "control_decisions": 0}


def test_autonomous_decision_rate_lists_control_rounds_separately() -> None:
    """@brief 只做结束、暂停或修订的控制轮单列计数，既不算自主也不算被动（docs/design/07-evaluation.md「2.2 报告项」）。

    待收尾活动当秒 conclude_activity 的收尾轮（时间与能量规格第七章）因此不稀释自主决策占比。
    """
    control = [
        _event("decision_started", _clock(0, 8, 1), decision_id="d1"),
        _action(2, "d1", "conclude_activity", {"items": [
            {"activity_name": "等待（1月5日 08:00 起）", "summary": "等完了。", "memories": [], "views": []}]}),
        _event("decision_started", _clock(0, 8, 3), decision_id="d2"),
        _action(4, "d2", "pause_activity", {"items": [{"activity_name": "观看节目（1月5日 08:00 起）", "summary": "先停一下。"}]}),
        _event("decision_started", _clock(0, 8, 5), decision_id="d3"),
        _action(6, "d3", "revise_day_plan", {"goals": []}),
    ]
    events = control + [
        _event("decision_started", _clock(0, 8, 7), decision_id="d4"),
        _start(8, "d4", "a4", "接着看", "内容：节目"),
        _accepted(9, "d4", "a4", "watch"),
        _event("decision_started", _clock(0, 8, 10), decision_id="d5"),
        _start(11, "d5", "a5", "等待", "自己"),
        _accepted(12, "d5", "a5", "wait"),
    ]
    report = _report(events)["reports"]["effective_autonomous_decision_rate"]
    assert report == {"value": 0.5, "decision_count": 5, "decisions_with_action": 5,
                      "autonomous_decisions": 1, "control_decisions": 3}
    only_control = _report(control)["reports"]["effective_autonomous_decision_rate"]
    assert only_control == {"value": None, "decision_count": 3, "decisions_without_action": 0,
                            "control_decisions": 3}


def test_wake_reason_distribution_counts() -> None:
    """@brief 唤醒原因按出现次数累计；sleep 的事件也参与统计。"""
    events = [
        _body(1, _clock(0, 9), _clock(0, 10), ["w1"], wake_reasons=["commitment_threatened"]),
        _body(2, _clock(0, 10), _clock(0, 11), ["s1"],
              wake_reasons=["commitment_threatened", "person_arrived"]),
        _body(3, _clock(0, 11), _clock(0, 12), ["wk1"]),
    ]
    report = _report(events)["reports"]["wake_reason_distribution"]
    assert report["counts"] == {"person_arrived": 1, "commitment_threatened": 2}
    assert report["events_with_wake_reasons"] == 2
    assert report["body_settlement_events"] == 3


def test_wake_reason_distribution_counts_same_moment_round_reasons() -> None:
    """@brief 同刻轮次不写共同身体结算，其原因只从 decision_started 计数；边界计数不受影响。"""
    second = _clock(0, 8)
    boundaries = [
        _body(1, _clock(0, 7), second, ["w1"], wake_reasons=["no_active_activity"]),
        _body(2, _clock(0, 7), second, ["w2"], wake_reasons=[]),
    ]
    rounds = [
        _event("decision_started", second, decision_id=f"d{seq}", event_seq=seq,
               data={"wake_reasons": reasons})
        for seq, reasons in ((3, ["run_start"]), (4, ["no_active_activity"]), (5, ["rejection_feedback"]))
    ]
    report = _report(boundaries + rounds)["reports"]["wake_reason_distribution"]
    assert report["counts"] == {"no_active_activity": 1}
    assert report["events_with_wake_reasons"] == 2
    assert report["body_settlement_events"] == 2
    assert report["decision_started_counts"] == {
        "no_active_activity": 1, "rejection_feedback": 1, "run_start": 1}
    assert report["decision_started_events_with_wake_reasons"] == 3


def test_wait_minutes_and_early_arrival() -> None:
    """@brief wait 活动按区间累计分钟；到达早于开门按整点差累计。"""
    events = [
        _progress(1, _clock(0, 9), _clock(0, 9, 30), "wait", activity_id="wait-1"),
        _progress(2, _clock(0, 12), _clock(0, 12, 15), "wait", activity_id="wait-2"),
        _progress(3, _clock(0, 7), _clock(0, 7, 30), "travel", activity_id="go-1",
                  snapshot_extra={"destination_id": "office", "ended_at": _clock(0, 7, 30),
                                  "data": {"arrival": {"location_id": "office"}}}),
        _progress(4, _clock(0, 7, 30), _clock(0, 8), "travel", activity_id="go-2",
                  snapshot_extra={"destination_id": "office", "ended_at": _clock(0, 8),
                                  "data": {"arrival": {"location_id": "office"}}}),
        _progress(5, _clock(0, 9), _clock(0, 9, 20), "travel", activity_id="go-3",
                  snapshot_extra={"destination_id": "home", "ended_at": _clock(0, 9, 20),
                                  "data": {"arrival": {"location_id": "home"}}}),
    ]
    report = _report(events, location_open_hours={"office": 8})
    wait = report["reports"]["wait_minutes"]
    assert wait["total_minutes"] == 45.0
    assert wait["per_agent_minutes"] == {"1": 45.0}
    assert report["reports"]["unknown_wait_minutes"]["total_minutes"] == 45.0
    early = report["reports"]["early_arrival_minutes"]
    assert early["value"] == 30.0
    assert early["per_location_minutes"] == {"office": 30.0}

    missing = _report(events)["reports"]["early_arrival_minutes"]
    assert missing["value"] is None


def test_parallel_activity_time_does_not_double_person_elapsed_or_fake_content() -> None:
    """@brief 共同时间取并集，等待自身不产生媒体接触。"""
    start = _clock(0, 8)
    events = [
        _body(1, start, start + 1800, ["walk", "watch"]),
        _progress(2, start, start + 1800, "travel", activity_id="walk"),
        _progress(3, start, start + 1800, "watch", activity_id="watch",
                  snapshot_extra={"data": {"revealed": {
                      "position_before_seconds": 0, "position_after_seconds": 900}}}),
        _body(4, start + 1800, start + 3600, ["wait"]),
        _progress(5, start + 1800, start + 3600, "wait", activity_id="wait"),
    ]
    reports = _report(events)["reports"]
    assert reports["person_elapsed_minutes"]["per_agent_minutes"] == {"1": 60.0}
    assert reports["content_contact_minutes"]["total_minutes"] == 30.0
    assert reports["wait_minutes"]["total_minutes"] == 30.0
    assert reports["unknown_wait_minutes"]["total_minutes"] == 30.0


def test_sleep_schedule_from_sleep_intervals() -> None:
    """@brief 就寝 23:00、起床 07:00、时长 8 小时由 sleep 区间直接得到。"""
    events = [_progress(1, _clock(0, 23), _clock(1, 7), "sleep", activity_id="sleep-1",
                        snapshot_extra={"started_at": _clock(0, 23), "ended_at": _clock(1, 7)})]
    schedule = _report(events)["reports"]["sleep_schedule"]
    assert schedule["sessions"] == [{
        "agent_id": "1", "bed_time": "23:00", "rise_time": "07:00", "duration_hours": 8.0,
        "started_at": _clock(0, 23), "ended_at": _clock(1, 7),
    }]
    assert schedule["summary"]["median_bed_minutes_of_day"] == 23 * 60
    assert schedule["summary"]["median_rise_minutes_of_day"] == 7 * 60
    assert schedule["summary"]["mean_duration_hours"] == 8.0


def test_meal_diversity_simpson_and_repetition_anchors() -> None:
    """@brief Simpson 浓度 {a:2,b:1,c:1}=0.375；只算实际 eat 正增量。"""
    def eaten(seq: int, product: str, use_mode: str = "eat") -> dict[str, Any]:
        return _progress(seq, _clock(0, 8, seq), _clock(0, 8, seq + 1), "consume",
                         activity_id=f"meal-{seq}", data_extra={"consumption": {
                             "use_mode": use_mode,
                             "items": [{"product_id": product, "interval_quantity": 0.5}]}})
    events = [_action(10, "d1", "consume", {"product_id": "phantom", "use_mode": "eat"}),
              eaten(1, "a"), eaten(2, "a"), eaten(3, "b"), eaten(4, "c"), eaten(5, "d", "use")]
    diversity = _report(events)["reports"]["meal_diversity"]
    assert diversity["simpson_concentration"] == 0.375
    assert diversity["consume_event_count"] == 4
    assert diversity["distinct_products"] == 3
    assert diversity["product_counts"] == {"a": 2, "b": 1, "c": 1}

    hours = 3600
    window_events = [eaten(11, "a"), eaten(12, "a"), eaten(13, "b"), eaten(14, "a")]
    window_events[1]["occurred_at"] = _clock(0, 0) + 10 * hours
    window_events[2]["occurred_at"] = _clock(0, 0) + 20 * hours
    window_events[3]["occurred_at"] = _clock(0, 0) + 25 * hours
    rate = _report(window_events)["reports"]["meal_diversity"]["repetition_24h_rate"]
    assert rate == 0.5


def test_js_distance_hand_anchor() -> None:
    """@brief 手算锚点：P=(.5,.5,0)、Q=(.5,.25,.25) 的 JS 散度与距离。

    手算过程（log2 口径）：M=(.5,.375,.125)；KL(P||M)=.5*log2(4/3)=0.2075187496394219；
    KL(Q||M)=.25*log2(2/3)+.25*1=-0.1462406251802890+0.25=0.1037593748197110；
    JSD=(KL(P||M)+KL(Q||M))/2=0.1556390622295664；距离=sqrt(JSD)。
    """
    divergence, distance = _js_distance([1.0, 1.0, 0.0], [2.0, 1.0, 1.0])
    assert abs(divergence - 0.1556390622295664) < 1e-12
    assert abs(distance - 0.3945111687006674) < 1e-12
    assert _js_distance([1.0, 0.0], [1.0, 0.0]) == (0.0, 0.0)


def test_reference_minutes_match_spec_constants() -> None:
    """@brief 参照常量与 docs/VERIFICATION.md「真实性评估与结论边界」逐项一致（分钟换算）。"""
    assert TIME_USE_REFERENCE_MINUTES == {
        "sleep": 553, "hygiene": 50, "meals": 104, "work": 460, "transport": 44,
        "socializing": 21, "exercise": 41, "screen_entertainment": 165, "no_reference": 0,
    }


def test_activity_distribution_matches_reference_exactly() -> None:
    """@brief 家族分钟与参照同比例时 JS 距离为 0；外出并入通勤、缺字段记未归类。"""
    events = [
        _progress(1, _clock(0, 0), _clock(0, 9, 13), "sleep", activity_id="sleep-1"),
        _progress(2, _clock(0, 10), _clock(0, 10, 50), "lifestyle", activity_id="wash-1",
                  snapshot_extra={"family": "morning_routine"}),
        _progress(3, _clock(0, 12), _clock(0, 13, 44), "consume", activity_id="meal-1",
                  data_extra={"consumption": {"use_mode": "eat", "items": [
                      {"product_id": "meal", "interval_quantity": 1.0}]}}),
        _progress(4, _clock(0, 14), _clock(0, 21, 40), "work", activity_id="work-1"),
        _progress(5, _clock(0, 22), _clock(0, 22, 34), "travel", activity_id="go-1"),
        _progress(6, _clock(0, 23), _clock(0, 23, 21), "lifestyle", activity_id="chat-1",
                  snapshot_extra={"family": "social"}),
        _progress(7, _clock(1, 0), _clock(1, 0, 41), "lifestyle", activity_id="run-1",
                  snapshot_extra={"category": "sport"}),
        _progress(8, _clock(1, 2), _clock(1, 4, 45), "watch", activity_id="watch-1"),
        _progress(9, _clock(1, 5), _clock(1, 5, 10), "lifestyle", activity_id="trip-1",
                  snapshot_extra={"family": "outing"}),
        _progress(10, _clock(1, 6), _clock(1, 6, 30), "lifestyle", activity_id="misc-1"),
    ]
    distribution = _report(events)["reports"]["activity_distribution"]
    entry = distribution["per_agent"]["run-1:1"]
    assert entry["observed_days"] == 2
    assert entry["family_minutes"]["commute"] == 34.0
    assert entry["outing_minutes_merged_into_commute"] == 10.0
    assert entry["unclassified_minutes"] == 30.0
    commute_bucket = entry["family_minutes"]["commute"] + entry["outing_minutes_merged_into_commute"]
    assert commute_bucket == 44.0
    assert entry["js_distance"] == 0.0
    assert distribution["js_distance_mean"] == 0.0
    assert any("外出" in item for item in distribution["conventions"])


def test_persona_distinguishability_leave_one_out() -> None:
    """@brief 三次运行、两个可分人物时留一最近邻准确率为 1；运行不足时不计算。"""
    events: list[dict[str, Any]] = []
    seq = 0
    for run_id in ("r1", "r2", "r3"):
        seq += 1
        events.append(_progress(seq, _clock(0, 0), _clock(0, 10), "sleep", agent_id=1,
                                run_id=run_id, activity_id=f"{run_id}-s1"))
        seq += 1
        events.append(_progress(seq, _clock(0, 11), _clock(0, 13), "work", agent_id=1,
                                run_id=run_id, activity_id=f"{run_id}-w1"))
        seq += 1
        events.append(_progress(seq, _clock(0, 0), _clock(0, 10), "watch", agent_id=2,
                                run_id=run_id, activity_id=f"{run_id}-v1"))
        seq += 1
        events.append(_progress(seq, _clock(0, 11), _clock(0, 12), "consume", agent_id=2,
                                run_id=run_id, activity_id=f"{run_id}-m1"))
    report = _report(events)["reports"]["persona_distinguishability"]
    assert report["accuracy"] == 1.0 and report["run_count"] == 3

    short = [event for event in events if event["run_id"] != "r3"]
    report = _report(short)["reports"]["persona_distinguishability"]
    assert report["accuracy"] is None and report["run_count"] == 2


def test_social_minutes_and_message_counts() -> None:
    """@brief 交谈/通话按真实结算区间累计；消息按独立工具与到达事件计数。"""
    events = [
        _start(1, "d1", "a1", "交谈", "人物：同事"),
        _start(2, "d2", "a2", "通话", "联系人：朋友"),
        _progress(3, _clock(0, 9), _clock(0, 9, 30), "chat"),
        _progress(4, _clock(0, 10), _clock(0, 10, 15), "call"),
        _action(5, "d3", "send_message", {"recipient_name": "同事", "text": "在忙吗"}),
        _action(6, "d4", "send_message", {"recipient_name": "朋友", "text": "周末聚？"}),
        _event("environment_event", _clock(0, 15),
               data={"kind": "message_arrival", "sender_id": 2, "text": "在"}, event_seq=7),
    ]
    report = _report(events)["reports"]["social"]
    assert report["chat_call_minutes"] == {"call": 15.0, "chat": 30.0}
    assert report["sent_message_count"] == 2 and report["received_message_count"] == 1
    assert not any("planned_duration_seconds" in note for note in _report(events)["notes"])


def test_memory_repetition_gate_values() -> None:
    """@brief 3/10=0.3 失败、2/10=0.2 边界通过、全唯一为 0；类型内才比较。"""
    texts = ["早上去了公园散步很高兴", "晚上在家读了一本书", "中午吃了一碗面条"]
    fail_events = [_memory([{"text": texts[0], "type": "goal"},
                            {"text": texts[0], "type": "goal"},
                            {"text": texts[0], "type": "goal"},
                            {"text": texts[1], "type": "goal"},
                            {"text": texts[2], "type": "goal"}])]
    gate = _report(fail_events)["gates"]["memory_repetition_ratio"]
    assert gate["value"] == 0.3 and gate["pass"] is False

    boundary_events = [_memory([{"text": texts[0], "type": "goal"},
                                {"text": texts[0], "type": "goal"},
                                {"text": texts[1], "type": "goal"},
                                {"text": texts[1], "type": "goal"},
                                {"text": "下午开了一个长会", "type": "goal"}])]
    gate = _report(boundary_events)["gates"]["memory_repetition_ratio"]
    assert gate["value"] == 0.2 and gate["pass"] is True

    distinct_types = [_memory([{"text": texts[0], "type": "routine"},
                              {"text": texts[0], "type": "goal"}])]
    gate = _report(distinct_types)["gates"]["memory_repetition_ratio"]
    assert gate["value"] == 0.0 and gate["pass"] is True

    malformed = [_memory([{"text": texts[0], "type": "goal"}])]
    malformed[0]["data"]["episodes"].append({"text": texts[0], "kind": "event"})
    gate = _report(malformed)["gates"]["memory_repetition_ratio"]
    assert gate["value"] is None and gate["pass"] is False


def test_memory_md_identity_residue_gate() -> None:
    """@brief 含示例身份或示例 AOI 编号即失败；干净文本通过；未提供按不通过处理。"""
    dirty = {1: "李凡是后端工程师。\n- Marketing analyst at AOI 500038964"}
    gate = behavior_metrics_report([_event("run_ended", _clock(0, 23))],
                                   memory_md_texts=dirty)[CODE_A]["gates"]["memory_md_identity_residue"]
    assert gate["value"] == 1 and gate["pass"] is False
    assert gate["matched_agents"] == {"1": ["aoi 500038964", "marketing analyst"]}

    clean = {1: "李凡是后端工程师，住在张江。", 2: "王茜是产品经理。"}
    gate = behavior_metrics_report([_event("run_ended", _clock(0, 23))],
                                   memory_md_texts=clean)[CODE_A]["gates"]["memory_md_identity_residue"]
    assert gate["value"] == 0 and gate["pass"] is True

    absent = _report([_event("run_ended", _clock(0, 23))])["gates"]["memory_md_identity_residue"]
    assert absent["value"] is None and absent["pass"] is False


def test_missing_metric_evidence_is_reported_unknown_and_gates_fail_closed() -> None:
    """@brief 不完整的合成指标输入保持未知，不补造静默、记忆或身份测量。"""
    events = [
        _event("decision_started", _clock(0, 8), decision_id="d1"),
        _model_request(2, "d1", ["当前观察：在家，精神不错"]),
        _progress(3, _clock(0, 9), _clock(0, 10), "watch", activity_id="w1"),
        _event("memory_updated", _clock(0, 20),
               data={"operation": "append_episodes", "episode_ids": ["f" * 64]}),
        _event("run_ended", _clock(0, 23), data={"reason": "window_end"}),
    ]
    report = _report(events)
    assert report["schema_version"] == "behavior-metrics-1"
    assert report["gates"]["observation_repetition"]["pass"] is True
    for key in ("silent_boundary_ratio", "memory_repetition_ratio", "memory_md_identity_residue"):
        assert report["gates"][key]["value"] is None
        assert report["gates"][key]["pass"] is False
    assert report["reports"]["decision_prompt_token_median"]["value"] is None
    assert report["reports"]["wake_reason_distribution"]["counts"] == {}
    notes = "".join(report["notes"])
    for keyword in ("decision_offered", "wake_reasons", "主观记忆快照", "MEMORY.md", "prompt token"):
        assert keyword in notes
    assert report["reports"]["early_arrival_minutes"]["value"] is None


def test_report_groups_by_code_sha256() -> None:
    """@brief 输出按事件携带的 code_sha256 分组；缺失归入未上报组并注明。"""
    events = [
        _model_request(1, "d1", ["观察 A"]),
        _model_request(2, "d2", ["观察 B"], code_sha256=CODE_B),
        _model_request(3, "d3", ["观察 C"], code_sha256=""),
    ]
    report = behavior_metrics_report(events)
    assert set(report) == {CODE_A, CODE_B, UNREPORTED_CODE_KEY}
    assert "未携带 code_sha256" in "".join(report[UNREPORTED_CODE_KEY]["notes"])


def test_same_input_produces_byte_identical_report() -> None:
    """@brief 确定性：同输入两次计算的规范 JSON 逐字节相等。"""
    events = [
        _model_request(1, "d1", ["观察 A"]),
        _model_response(2, "d1", 1234),
        _progress(3, _clock(0, 9), _clock(0, 10), "watch", activity_id="w1",
                  data_extra={"decision_offered": False, "wake_reasons": ["commitment_threatened"]}),
        _progress(4, _clock(0, 23), _clock(1, 7), "sleep", activity_id="s1",
                  snapshot_extra={"started_at": _clock(0, 23), "ended_at": _clock(1, 7)}),
        _action(5, "d2", "consume", {"product_id": "noodle", "use_mode": "eat"}),
        _memory([{"text": "今天上班写了很多代码", "kind": "episode"}]),
    ]
    first = behavior_metrics_report(events, location_open_hours={"office": 8},
                                    memory_md_texts={1: "李凡是后端工程师"})
    second = behavior_metrics_report(events, location_open_hours={"office": 8},
                                     memory_md_texts={1: "李凡是后端工程师"})
    assert canonical_json(first) == canonical_json(second)


def test_person_elapsed_merges_overlapping_nested_and_disjoint_intervals() -> None:
    """报告对相交、包含和分离的区间取并集，并分别核算不同人物。"""
    start = _clock(0, 8)
    intervals = [(0, 1800), (900, 2700), (1200, 1500), (3600, 4200)]
    events = [_body(index, start + begin, start + end, [])
              for index, (begin, end) in enumerate(intervals, start=1)]
    other = _body(5, start, start + 600, [])
    other["agent_id"] = 2
    events.append(other)
    report = _report(events)["reports"]["person_elapsed_minutes"]
    assert report["per_agent_minutes"] == {"1": 55.0, "2": 10.0}
    assert sum(report["per_agent_minutes"].values()) == 65.0
