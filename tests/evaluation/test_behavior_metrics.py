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


def _model_request(
    event_seq: int,
    call_id: str,
    user_texts: list[str],
    *,
    agent_id: int = 1,
    purpose: str | None = None,
    usage: dict[str, int] | None = None,
    code_sha256: str = CODE_A,
) -> dict[str, Any]:
    """@brief 构造决策 model_request 事件（messages 只含 user 消息）。"""
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
                  decision_id=call_id, event_seq=event_seq, code_sha256=code_sha256)


def _model_response(event_seq: int, call_id: str, prompt_tokens: int) -> dict[str, Any]:
    """@brief 构造与请求配对的 model_response 事件（usage 在 response 内）。"""
    return _event("model_response", _clock(0, 8, event_seq),
                  data={"phase": "response", "call_id": call_id, "attempt": 1, "request": None,
                        "response": {"usage": {"prompt_tokens": prompt_tokens}}, "error": None},
                  decision_id=call_id, event_seq=event_seq)


def _action(event_seq: int, decision_id: str, tool_name: str,
            request: dict[str, Any] | None = None) -> dict[str, Any]:
    """@brief 构造 action_requested 事件。"""
    return _event("action_requested", _clock(0, 8, event_seq),
                  data={"tool_name": tool_name, "request": request or {}, "request_sha256": "0" * 64,
                        "reused": False},
                  decision_id=decision_id, event_seq=event_seq)


def _memory(episodes: list[dict[str, str]], *, event_seq: int = 90) -> dict[str, Any]:
    """@brief 构造带 episodes 文本的 memory_updated 事件。"""
    return _event("memory_updated", _clock(0, 20) + event_seq,
                  data={"operation": "append_episodes", "episodes": episodes}, event_seq=event_seq)


def _report(events: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    """@brief 取单代码组的报告（默认全部事件同 code）。"""
    return behavior_metrics_report(events, **kwargs)[CODE_A]


def test_observation_repetition_gate_pass_and_fail() -> None:
    """@brief 同一 user 观察文本出现两次时门禁必须失败，全唯一时通过。"""
    distinct = [
        _model_request(1, "d1", ["观察 A：天气晴朗"]),
        _model_request(2, "d2", ["观察 B：收到邮件"]),
    ]
    gate = _report(distinct)["gates"]["observation_repetition"]
    assert gate["value"] == 1 and gate["pass"] is True
    assert gate["duplicated_observation_count"] == 0

    duplicated = [
        _model_request(1, "d1", ["观察 A：天气晴朗"]),
        _model_request(2, "d2", ["观察 A：天气晴朗"]),
    ]
    gate = _report(duplicated)["gates"]["observation_repetition"]
    assert gate["value"] == 2 and gate["pass"] is False
    assert gate["duplicated_observation_count"] == 1


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
        return _progress(seq, _clock(0, seq, 0), _clock(0, seq, 10), "watch",
                         activity_id=f"watch-{seq}",
                         data_extra={"decision_offered": offered})

    mostly_silent = [waking(1, False), waking(2, False), waking(3, False),
                     waking(4, True), waking(5, True)]
    gate = _report(mostly_silent)["gates"]["silent_boundary_ratio"]
    assert gate["value"] == 0.6 and gate["pass"] is True
    assert gate["silent_boundaries"] == 3 and gate["known_boundaries"] == 5

    mostly_offered = [waking(1, False), waking(2, False), waking(3, True),
                      waking(4, True), waking(5, True)]
    gate = _report(mostly_offered)["gates"]["silent_boundary_ratio"]
    assert gate["value"] == 0.4 and gate["pass"] is False

    sleep_boundary = _progress(9, _clock(0, 1), _clock(0, 2), "sleep")
    gate = _report(mostly_silent + [sleep_boundary])["gates"]["silent_boundary_ratio"]
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


def test_autonomous_decision_rate_counts_continue_and_wait_as_passive() -> None:
    """@brief 最终动作为 continue 或 wait 的决策不计入自主决策。"""
    events = [
        _event("decision_started", _clock(0, 8, 1), decision_id="d1"),
        _action(2, "d1", "continue_activity"),
        _event("decision_started", _clock(0, 8, 3), decision_id="d2"),
        _action(4, "d2", "start_basic_activity", {"activity_type": "wait", "duration_seconds": 600}),
        _event("decision_started", _clock(0, 8, 5), decision_id="d3"),
        _action(6, "d3", "start_targeted_activity", {"activity_type": "watch", "target_id": "x"}),
        _event("decision_started", _clock(0, 8, 7), decision_id="d4"),
    ]
    report = _report(events)["reports"]["effective_autonomous_decision_rate"]
    assert report["value"] == 1 / 3
    assert report["decision_count"] == 4 and report["decisions_with_action"] == 3
    assert report["autonomous_decisions"] == 1


def test_wake_reason_distribution_counts() -> None:
    """@brief 唤醒原因按出现次数累计；sleep 的事件也参与统计。"""
    events = [
        _progress(1, _clock(0, 9), _clock(0, 10), "watch", activity_id="w1",
                  data_extra={"wake_reasons": ["plan_block_boundary"]}),
        _progress(2, _clock(0, 10), _clock(0, 11), "sleep", activity_id="s1",
                  data_extra={"wake_reasons": ["plan_block_boundary", "people_change"]}),
        _progress(3, _clock(0, 11), _clock(0, 12), "work", activity_id="wk1"),
    ]
    report = _report(events)["reports"]["wake_reason_distribution"]
    assert report["counts"] == {"people_change": 1, "plan_block_boundary": 2}
    assert report["events_with_wake_reasons"] == 2
    assert report["activity_progress_events"] == 3


def test_wait_minutes_and_early_arrival() -> None:
    """@brief wait 活动按区间累计分钟；到达早于开门按整点差累计。"""
    events = [
        _progress(1, _clock(0, 9), _clock(0, 9, 30), "wait", activity_id="wait-1"),
        _progress(2, _clock(0, 12), _clock(0, 12, 15), "wait", activity_id="wait-2"),
        _progress(3, _clock(0, 7), _clock(0, 7, 30), "travel", activity_id="go-1",
                  snapshot_extra={"destination_id": "office", "ended_at": _clock(0, 7, 30)}),
        _progress(4, _clock(0, 7, 30), _clock(0, 8), "travel", activity_id="go-2",
                  snapshot_extra={"destination_id": "office", "ended_at": _clock(0, 8)}),
        _progress(5, _clock(0, 9), _clock(0, 9, 20), "travel", activity_id="go-3",
                  snapshot_extra={"destination_id": "home", "ended_at": _clock(0, 9, 20)}),
    ]
    report = _report(events, location_open_hours={"office": 8})
    wait = report["reports"]["wait_minutes"]
    assert wait["total_minutes"] == 45.0
    assert wait["per_agent_minutes"] == {"1": 45.0}
    early = report["reports"]["early_arrival_minutes"]
    assert early["value"] == 30.0
    assert early["per_location_minutes"] == {"office": 30.0}

    missing = _report(events)["reports"]["early_arrival_minutes"]
    assert missing["value"] is None


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
    """@brief Simpson 浓度 {a:2,b:1,c:1}=0.375；24h 重复率按前进窗口逐事件判定。"""
    events = [
        _action(1, "d1", "consume", {"product_id": "a", "quantity": 1, "use_mode": "eat",
                                     "duration_seconds": 1200}),
        _action(2, "d2", "consume", {"product_id": "a", "quantity": 1, "use_mode": "eat",
                                     "duration_seconds": 1200}),
        _action(3, "d3", "consume", {"product_id": "b", "quantity": 1, "use_mode": "eat",
                                     "duration_seconds": 1200}),
        _action(4, "d4", "consume", {"product_id": "c", "quantity": 1, "use_mode": "eat",
                                     "duration_seconds": 1200}),
        _action(5, "d5", "consume", {"product_id": "d", "quantity": 1, "use_mode": "use",
                                     "duration_seconds": 1200}),
    ]
    diversity = _report(events)["reports"]["meal_diversity"]
    assert diversity["simpson_concentration"] == 0.375
    assert diversity["consume_event_count"] == 4
    assert diversity["distinct_products"] == 3
    assert diversity["product_counts"] == {"a": 2, "b": 1, "c": 1}

    hours = 3600
    window_events = [
        _action(1, "d1", "consume", {"product_id": "a", "use_mode": "eat"}),
        _action(2, "d2", "consume", {"product_id": "a", "use_mode": "eat"}),
        _action(3, "d3", "consume", {"product_id": "b", "use_mode": "eat"}),
        _action(4, "d4", "consume", {"product_id": "a", "use_mode": "eat"}),
    ]
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
    """@brief 参照常量与规格 L1704-1706 逐项一致（分钟换算）。"""
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
        _progress(3, _clock(0, 12), _clock(0, 13, 44), "consume", activity_id="meal-1"),
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
    """@brief 交谈/通话按请求 duration_minutes 累计；消息按动作与到达事件计数。"""
    events = [
        _action(1, "d1", "start_social_activity", {"kind": "chat", "partner_id": 2,
                                                   "duration_minutes": 30}),
        _action(2, "d2", "start_social_activity", {"kind": "call", "partner_id": "c1",
                                                   "duration_minutes": 15}),
        _action(3, "d3", "send_message", {"recipient_id": 2, "text": "在忙吗"}),
        _action(4, "d4", "send_message", {"recipient_id": "c1", "text": "周末聚？"}),
        _action(5, "d5", "start_social_activity", {"kind": "chat"}),
        _event("environment_event", _clock(0, 15),
               data={"kind": "message_arrival", "sender_id": 2, "text": "在"}, event_seq=6),
    ]
    report = _report(events)["reports"]["social"]
    assert report["chat_call_minutes"] == {"call": 15.0, "chat": 30.0}
    assert report["sent_message_count"] == 2 and report["received_message_count"] == 1
    assert any("start_social_activity" in note for note in _report(events)["notes"])


def test_memory_repetition_gate_values() -> None:
    """@brief 3/10=0.3 失败、2/10=0.2 边界通过、全唯一为 0；类型内才比较。"""
    texts = ["早上去了公园散步很高兴", "晚上在家读了一本书", "中午吃了一碗面条"]
    fail_events = [_memory([{"text": texts[0], "kind": "episode"},
                            {"text": texts[0], "kind": "episode"},
                            {"text": texts[0], "kind": "episode"},
                            {"text": texts[1], "kind": "episode"},
                            {"text": texts[2], "kind": "episode"}])]
    gate = _report(fail_events)["gates"]["memory_repetition_ratio"]
    assert gate["value"] == 0.3 and gate["pass"] is False

    boundary_events = [_memory([{"text": texts[0], "kind": "episode"},
                                {"text": texts[0], "kind": "episode"},
                                {"text": texts[1], "kind": "episode"},
                                {"text": texts[1], "kind": "episode"},
                                {"text": "下午开了一个长会", "kind": "episode"}])]
    gate = _report(boundary_events)["gates"]["memory_repetition_ratio"]
    assert gate["value"] == 0.2 and gate["pass"] is True

    distinct_types = [_memory([{"text": texts[0], "kind": "habit"},
                              {"text": texts[0], "kind": "event"}])]
    gate = _report(distinct_types)["gates"]["memory_repetition_ratio"]
    assert gate["value"] == 0.0 and gate["pass"] is True


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


def test_old_style_events_do_not_crash_and_gates_fail_closed() -> None:
    """@brief 无未来字段（decision_offered/wake_reasons/episodes）的旧式事件不崩溃。"""
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
    for keyword in ("decision_offered", "wake_reasons", "episodes", "MEMORY.md", "prompt token"):
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
                  data_extra={"decision_offered": False, "wake_reasons": ["plan_block_boundary"]}),
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


def _plan_result(event_seq: int, occurred_at: int, segments: list[dict[str, Any]],
                 *, agent_id: int = 1) -> dict[str, Any]:
    """@brief 构造携带 day_plan 段列表的完成 action_result 事件。"""
    return _event("action_result", occurred_at, agent_id=agent_id,
                  data={"action_id": f"a{event_seq}", "agent_id": agent_id, "status": "completed",
                        "occurred_at": occurred_at, "message": "Day plan registered",
                        "data": {"day_plan": segments}}, event_seq=event_seq)


def test_plan_actual_deviation_report_hand_anchor() -> None:
    """@brief 手算锚点：按日对比计划段与实际活动家族分钟（修订覆盖初版、跨日切分）。

    手算（2026-01-05，人物 1）：初版计划 工作 09:00-12:00(180m)+娱乐 12:00-13:00(60m)；
    11:00 修订为 工作 09:00-12:00(180m)+娱乐 13:00-14:30(90m)——生效口径取修订。
    实际：work 09:00-11:00(120m)、watch 11:00-12:00(娱乐 60m)、无家族 lifestyle
    13:00-13:30(unclassified 30m)、sleep 23:00→次日 07:00（当日切分 60m）。
    偏离矩阵：work 120-180=-60；entertainment 60-90=-30；sleep 60-0=+60；
    unclassified 30-0=+30；绝对偏离合计 180m；计划/实际总量均 270m。
    """
    segments_initial = [
        {"start": _clock(0, 9), "end": _clock(0, 12), "intent_kind": "工作", "note": ""},
        {"start": _clock(0, 12), "end": _clock(0, 13), "intent_kind": "娱乐", "note": ""},
    ]
    segments_revised = [
        {"start": _clock(0, 9), "end": _clock(0, 12), "intent_kind": "工作", "note": ""},
        {"start": _clock(0, 13), "end": _clock(0, 14, 30), "intent_kind": "娱乐", "note": ""},
        {"start": _clock(0, 15), "intent_kind": "家务", "note": "缺 end 的畸形段记 0 分钟"},
    ]
    rejected = _event("action_result", _clock(0, 8), agent_id=1,
                      data={"action_id": "a0", "agent_id": 1, "status": "rejected",
                            "occurred_at": _clock(0, 8), "message": "Action rejected",
                            "data": {"consecutive_rejections": 1}}, event_seq=0)
    events = [
        rejected,
        _plan_result(1, _clock(0, 8), segments_initial),
        _plan_result(2, _clock(0, 11), segments_revised),
        _progress(3, _clock(0, 9), _clock(0, 11), "work", activity_id="w1"),
        _progress(4, _clock(0, 11), _clock(0, 12), "watch", activity_id="v1"),
        _progress(5, _clock(0, 13), _clock(0, 13, 30), "lifestyle", activity_id="misc-1"),
        _progress(6, _clock(0, 14), _clock(0, 13), "wait", activity_id="bad-interval"),
        _progress(7, _clock(0, 23), _clock(1, 7), "sleep", activity_id="s1"),
    ]
    report = _report(events)["reports"]["plan_actual_deviation"]
    entry = report["per_agent_day"]["1:2026-01-05"]
    assert entry["plan_segments"] == [
        {"intent_kind": "工作", "family": "work", "planned_minutes": 180.0,
         "actual_same_family_minutes": 120.0},
        {"intent_kind": "娱乐", "family": "entertainment", "planned_minutes": 90.0,
         "actual_same_family_minutes": 60.0},
        {"intent_kind": "家务", "family": "chore", "planned_minutes": 0.0,
         "actual_same_family_minutes": 0.0},
    ]
    assert entry["family_deviation"] == {
        "chore": {"planned_minutes": 0.0, "actual_minutes": 0.0, "deviation_minutes": 0.0},
        "entertainment": {"planned_minutes": 90.0, "actual_minutes": 60.0,
                          "deviation_minutes": -30.0},
        "sleep": {"planned_minutes": 0.0, "actual_minutes": 60.0, "deviation_minutes": 60.0},
        "unclassified": {"planned_minutes": 0.0, "actual_minutes": 30.0,
                         "deviation_minutes": 30.0},
        "work": {"planned_minutes": 180.0, "actual_minutes": 120.0, "deviation_minutes": -60.0},
    }
    assert entry["planned_total_minutes"] == 270.0
    assert entry["actual_total_minutes"] == 270.0
    assert entry["absolute_deviation_minutes"] == 180.0
    assert report["summary"] == {"agent_day_count": 1, "planned_total_minutes": 270.0,
                                 "actual_total_minutes": 270.0,
                                 "absolute_deviation_minutes": 180.0,
                                 "mean_absolute_deviation_minutes": 180.0}
    assert "1:2026-01-06" not in report["per_agent_day"], "无计划的本地日不产生偏离行"
    assert report["conventions"], "偏离报告必须携带口径说明"
    again = _report(events)["reports"]["plan_actual_deviation"]
    assert canonical_json(again) == canonical_json(report)


def test_plan_actual_deviation_fails_closed_without_plan_events() -> None:
    """@brief 无任何完成的 plan_day 登记时偏离报告 fail-closed 并注明。"""
    report = _report([_progress(1, _clock(0, 9), _clock(0, 10), "work", activity_id="w1")])
    deviation = report["reports"]["plan_actual_deviation"]
    assert deviation["per_agent_day"] == {} and deviation["summary"] is None
    assert any("plan_day" in note for note in report["notes"])
