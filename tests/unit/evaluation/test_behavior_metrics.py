from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from worldmock.evaluation.behavior_metrics import (
    BEDTIME_DAY_START_MINUTES,
    BEDTIME_MIN_SECONDS,
    UNREPORTED_CODE_KEY,
    behavior_metrics_report,
)
from worldmock.footprints.models import canonical_json
from worldmock.evaluation.errors import EvaluationError

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
            "event_seq": event_seq, "code_sha256": code_sha256, "action_id": None}


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
                "status": "active", "started_at": start, "data": {}}
    if kind == "interact":
        snapshot["data"] = {"action": "interact", "content_id": "test-topic"}
    if kind == "travel":
        snapshot["data"] = {"action": "travel"}
    snapshot.update(snapshot_extra or {})
    data = {"interval_start": start, "interval_end": end, "activity": snapshot}
    data.update(data_extra or {})
    return _event("activity_progress", end, agent_id=agent_id, data=data, run_id=run_id,
                  activity_id=activity_id, code_sha256=code_sha256, event_seq=event_seq)


def _receipt_events(seq: int, product: str, at: int, category: str = "meal") -> list[dict]:
    """@brief 当前完整订单与本人实际收货是纯度量用例的独立输入事实。"""
    order = {"order_id": f"order-{seq}", "agent_id": 1, "goods_id": product,
        "category": category, "store_id": "store", "app_id": None, "payment_app_id": "alipay",
        "unit_price_fen": 1000, "delivery_fee_fen": 0, "quantity": 1, "total_price_fen": 1000,
        "fulfillment": "pickup", "created_at": at, "received_at": at,
        "status": "delivered"}
    result = _event("action_result", at, event_seq=seq * 2,
                    data={"status": "completed", "data": {"orders": [order]}})
    result["action_id"] = f"purchase-{seq}"
    receipt = _event("environment_event", at, event_seq=seq * 2 + 1,
        data={"kind": "goods_received", "order_id": order["order_id"], "provided": {}, "applied": {}})
    receipt["caused_by"] = [result["event_id"]]
    return [result, receipt]


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


#: 行动请求开放的世界请求工具（两个开始工具与一个控制工具，只填函数名）。
DECISION_TOOLS = ("start_main_activity", "start_side_activity", "conclude_activity")


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
        _model_request(6, "d4", ["验收"], purpose="plan_day"),
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


def _with_tools(event: dict[str, Any], *names: str) -> dict[str, Any]:
    """@brief 给请求夹具加上实际开放的原生工具（只填函数名）。"""
    event["data"]["request"]["tools"] = [{"type": "function", "function": {"name": name}} for name in names]
    return event


def test_reflection_requests_and_their_retries_are_not_decision_requests() -> None:
    """@brief 就寝反思的请求只带 submit_reflection、不带 purpose（docs/design/10-runtime.md ）：两个人物各有
    一次校验重试，附回消息逐字相同、分处两条（人物，秒）续接链，也不进观察重复门禁与决策 token 中位数；工具中有
    start_main_activity 的行动请求与不带工具的记忆巩固照计。"""
    correction = ('{"INSTRUCTION":"Return a complete new submit_reflection tool call using the unchanged schema.",'
                  '"PREVIOUS_INVALID_RESPONSE":[],'
                  '"VALIDATION_ERROR":"response must contain exactly one submit_reflection tool call"}')
    events = [
        _with_tools(_model_request(1, "act-1", ["观察 1"]), *DECISION_TOOLS),
        _model_response(2, "act-1", 100),
        _model_request(3, "consolidate-1", ["记忆归纳 1"]),
        _model_response(4, "consolidate-1", 300),
    ]
    for seq, agent_id in ((5, 1), (9, 2)):
        for offset, texts in ((0, [f"反思输入 {agent_id}"]), (2, [f"反思输入 {agent_id}", correction])):
            call = f"reflect-{agent_id}-{offset}"
            events += [_with_tools(_model_request(seq + offset, call, texts, agent_id=agent_id), "submit_reflection"),
                       _model_response(seq + offset + 1, call, 20000)]
    retries = [event for event in events if event["event_type"] == "model_request"
               and correction in [message["content"] for message in event["data"]["request"]["messages"]]]
    assert len({(event["agent_id"], event["occurred_at"]) for event in retries}) == 2

    report = _report(events)

    gate = report["gates"]["observation_repetition"]
    assert (gate["value"], gate["pass"], gate["duplicated_observation_count"]) == (1, True, 0)
    assert gate["user_message_count"] == 2
    median = report["reports"]["decision_prompt_token_median"]
    assert (median["value"], median["sample_size"]) == (200, 2)


def test_tool_requests_without_start_main_activity_are_not_decision_requests() -> None:
    """@brief 带工具的请求只在工具中有 start_main_activity 时才是人物决策请求（docs/design/10-runtime.md 「身份与
    判定」、docs/design/07-evaluation.md「2.1 协议门禁」「2.2 报告项」），不是「只要不是就寝反思就算」：只读问答的请求不带
    purpose，工具是三个记忆查询与 finish（末回合只剩 finish）。它们的 user 文本即便与另一秒的行动请求逐字相同、prompt
    token 很大，也不进观察重复门禁与决策 token 中位数。只开放已删除的 start_activity 的请求同样不算。"""
    events = [
        _with_tools(_model_request(1, "act-1", ["观察 1"]), *DECISION_TOOLS),
        _model_response(2, "act-1", 100),
        _with_tools(_model_request(3, "ask-1", ["观察 1"]),
                    "memory_recent", "memory_search", "memory_time_range", "finish"),
        _model_response(4, "ask-1", 20000),
        _with_tools(_model_request(5, "ask-2", ["观察 1"]), "finish"),
        _model_response(6, "ask-2", 30000),
        _with_tools(_model_request(7, "retired-1", ["观察 1"]), "start_activity", "conclude_activity"),
        _model_response(8, "retired-1", 40000),
    ]
    requests = [event for event in events if event["event_type"] == "model_request"]
    assert len({event["occurred_at"] for event in requests}) == 4
    assert not any(event["data"].get("purpose") for event in requests)

    report = _report(events)

    gate = report["gates"]["observation_repetition"]
    assert (gate["value"], gate["pass"], gate["duplicated_observation_count"]) == (1, True, 0)
    assert gate["user_message_count"] == 1
    median = report["reports"]["decision_prompt_token_median"]
    assert (median["value"], median["sample_size"]) == (100, 1)








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


def test_early_arrival_uses_actual_daily_opening() -> None:
    """@brief wait 活动按区间累计分钟；到达早于开门按整点差累计。"""
    events = [
        _progress(1, _clock(0, 9), _clock(0, 9, 30), "interact", activity_id="wait-1"),
        _progress(2, _clock(0, 12), _clock(0, 12, 15), "interact", activity_id="wait-2"),
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
    from worldmock.catalog.models import Location, OpeningHours
    office = Location(location_id="office", name="Office", kind="office", app_ids=(),
        opens=OpeningHours(weekly={"mon": (("08:00", "18:00"),)}))
    report = _report(events, locations={"office": office})
    early = report["reports"]["early_arrival_minutes"]
    assert early["value"] == 30.0
    assert early["per_location_minutes"] == {"office": 30.0}

    missing = _report(events)["reports"]["early_arrival_minutes"]
    assert missing["value"] is None


def test_parallel_activity_time_does_not_double_person_elapsed_or_fake_content() -> None:
    """@brief 共同时间取并集，等待自身不产生媒体接触。"""
    start = _clock(0, 8)
    events = [
        _body(1, start, start + 1800, ["walk", "use_app"]),
        _progress(2, start, start + 1800, "travel", activity_id="walk"),
        _progress(3, start, start + 1800, 'interact', activity_id="use_app",
                  snapshot_extra={"target_id":"bilibili"}, data_extra={"content": {
                      "content_id":"long-video", "app_id":"bilibili", "kind":"long_video",
                      "position_before_seconds":0, "position_after_seconds":900,
                      "completed":False, "completed_rounds_after":0}}),
        _body(4, start + 1800, start + 3600, ["interact"]),
        _progress(5, start + 1800, start + 3600, "interact", activity_id="interact"),
    ]
    reports = _report(events)["reports"]
    assert reports["person_elapsed_minutes"]["per_agent_minutes"] == {"1": 60.0}
    assert reports["content_contact_minutes"]["total_minutes"] == 30.0
    assert not {"wait_minutes", "unknown_wait_minutes"} & reports.keys()


def test_sleep_schedule_from_sleep_intervals() -> None:
    """@brief 就寝 23:00、起床 07:00、时长 8 小时由 sleep 区间直接得到，这一段记为就寝；没有小睡。"""
    events = [_progress(1, _clock(0, 23), _clock(1, 7), "sleep", activity_id="sleep-1",
                        snapshot_extra={"started_at": _clock(0, 23), "ended_at": _clock(1, 7),
                                        "status": "completed"})]
    schedule = _report(events)["reports"]["sleep_schedule"]
    assert schedule["sessions"] == [{
        "agent_id": "1", "bed_time": "23:00", "rise_time": "07:00", "duration_hours": 8.0,
        "started_at": _clock(0, 23), "ended_at": _clock(1, 7), "category": "bedtime",
    }]
    assert schedule["summary"] == {"median_bed_minutes_of_day": 23 * 60, "median_rise_minutes_of_day": 7 * 60,
                                   "mean_duration_hours": 8.0, "session_count": 1}
    assert schedule["naps"] == {"count": 0, "mean_duration_hours": None}


def _slept(seq: int, start: int, seconds: int, *, status: str = "completed", agent_id: int = 1) -> dict[str, Any]:
    """@brief 一段从 start 起实际睡了 seconds 秒的睡眠的终局进度事件；status 不是 completed 时是还没睡醒的那一段。"""
    extra = {"started_at": start, "status": status}
    if status == "completed":
        extra["ended_at"] = start + seconds
    return _progress(seq, start, start + seconds, "sleep", activity_id=f"sleep-{agent_id}-{seq}", agent_id=agent_id,
                     snapshot_extra=extra)


def _nights(clocks: list[tuple[int, int, int]], minutes: int = 470) -> dict[str, Any]:
    """@brief 各晚（第几天, 时, 分）入睡、各睡 minutes 分钟的睡眠报告。"""
    events = [_slept(index + 1, _clock(day, hour, minute), minutes * 60)
              for index, (day, hour, minute) in enumerate(clocks)]
    return _report(events)["reports"]["sleep_schedule"]


def test_bedtime_needs_one_hundred_eighty_minutes_and_shorter_sleep_is_a_nap() -> None:
    """@brief 179 分 59 秒是小睡，180 分钟整是就寝；写 120 分钟又多睡 60 分钟（实际 180 分钟）是就寝；
    小睡单列次数与平均时长，不进就寝的统计。"""
    assert BEDTIME_MIN_SECONDS == 10800
    events = [_slept(1, _clock(0, 13), 10799), _slept(2, _clock(1, 1), 10800),
              _slept(3, _clock(2, 14), 120 * 60 + 60 * 60), _slept(4, _clock(3, 13), 1800)]
    schedule = _report(events)["reports"]["sleep_schedule"]
    assert [row["category"] for row in schedule["sessions"]] == ["nap", "bedtime", "bedtime", "nap"]
    assert schedule["summary"]["session_count"] == 2 and schedule["summary"]["mean_duration_hours"] == 3.0
    assert schedule["summary"]["median_rise_minutes_of_day"] == (4 * 60 + 17 * 60) / 2
    assert schedule["naps"] == {"count": 2, "mean_duration_hours": round((10799 + 1800) / 2 / 3600, 4)}


def test_unfinished_sleep_counts_on_neither_side() -> None:
    """@brief 23:00 入睡、运行在 00:00 结束时还没睡醒的那一段记为 unfinished：照样列在 sessions 里，就寝与小睡都不计。"""
    events = [_slept(1, _clock(0, 0, 10), 470 * 60), _slept(2, _clock(0, 23), 3600, status="truncated"),
              _slept(3, _clock(1, 2), 9 * 3600, status="active", agent_id=2)]
    report = _report(events)
    schedule = report["reports"]["sleep_schedule"]
    assert [row["category"] for row in schedule["sessions"]] == ["bedtime", "unfinished", "unfinished"]
    assert schedule["summary"]["session_count"] == 1 and schedule["naps"]["count"] == 0
    assert schedule["summary"]["mean_duration_hours"] == round(470 / 60, 4)
    assert not [note for note in report["notes"] if "180 分钟" in note]


def test_sleep_without_any_bedtime_has_no_summary_and_says_so() -> None:
    """@brief 有睡眠但没有睡满 180 分钟的：summary 为空并写规定说明；一段有效睡眠都没有时沿用原有说明，小睡次数为 0
    时平均时长为空。"""
    naps = _report([_slept(1, _clock(0, 13), 3600), _slept(2, _clock(1, 13), 1800)])
    schedule = naps["reports"]["sleep_schedule"]
    assert schedule["summary"] is None and schedule["naps"] == {"count": 2, "mean_duration_hours": 0.75}
    assert "没有睡满 180 分钟的睡眠，就寝与起床时刻无法计算" in naps["notes"]
    only_unfinished = _report([_slept(1, _clock(0, 23), 3600, status="truncated")])
    assert only_unfinished["reports"]["sleep_schedule"]["summary"] is None
    assert "没有睡满 180 分钟的睡眠，就寝与起床时刻无法计算" in only_unfinished["notes"]
    empty = _report([_event("run_ended", _clock(0, 23))])
    assert empty["reports"]["sleep_schedule"] == {"sessions": [], "summary": None,
                                                  "naps": {"count": 0, "mean_duration_hours": None}}
    assert "无有效 sleep 活动区间，就寝与起床时刻无法计算" in empty["notes"]
    assert "没有睡满 180 分钟的睡眠，就寝与起床时刻无法计算" not in empty["notes"]


@pytest.mark.parametrize("clocks,median", [
    ([(0, 23, 50), (2, 0, 10), (2, 23, 40), (4, 0, 20)], 0),
    ([(0, 23, 0), (2, 3, 0)], 60),
    ([(0, 5, 50), (1, 6, 10)], 1080),
    ([(0, 23, 50), (2, 0, 10), (3, 0, 30)], 10),
    ([(0, 0, 10), (1, 0, 10), (2, 0, 10)], 10),
    ([(0, 23, 0), (1, 23, 0)], 23 * 60),
    ([(0, 9, 0), (1, 9, 0)], 9 * 60),
], ids=["around_midnight", "eleven_and_three", "either_side_of_six", "three_nights", "ten_past_midnight",
        "eleven_every_night", "night_shift"])
def test_bedtime_median_starts_the_day_at_six_in_the_morning(clocks, median) -> None:
    """@brief 就寝时刻的中位数以 06:00 为一天的起点：零点两侧的四晚算出 00:00 而不是 12:00；
    就寝落在 06:00 两侧的两晚算出 18:00（已知后果）。"""
    assert BEDTIME_DAY_START_MINUTES == 360
    assert _nights(clocks)["summary"]["median_bed_minutes_of_day"] == median


def test_rise_median_is_not_shifted() -> None:
    """@brief 起床时刻的中位数直接取自本地零点起的分钟：23:50 与 00:10 起床的两晚算出 12:00，不随就寝的平移改变。"""
    schedule = _nights([(0, 15, 50), (1, 16, 10)], minutes=480)
    assert schedule["summary"]["median_rise_minutes_of_day"] == (23 * 60 + 50 + 10) / 2
    assert schedule["summary"]["median_bed_minutes_of_day"] == 16 * 60


def test_twelve_weeks_of_nights_and_workday_naps_report_bedtimes_and_naps_apart() -> None:
    """@brief 林悦 12 周：84 夜 00:10 到 08:00，60 个工作日各午睡 13:00 到 14:00。就寝 00:10、起床 08:00、
    平均 7.8333 小时、84 段；小睡 60 次、平均 1 小时。只看周一到周五每天午睡的一周同样如此。"""
    events, seq = [], 0
    for day in range(84):
        seq += 1
        events.append(_slept(seq, _clock(0, 0, 10) + day * 86400, 470 * 60))
        if day % 7 < 5:
            seq += 1
            events.append(_slept(seq, _clock(0, 13) + day * 86400, 3600))
    schedule = _report(events)["reports"]["sleep_schedule"]
    assert schedule["summary"] == {"median_bed_minutes_of_day": 10, "median_rise_minutes_of_day": 480,
                                   "mean_duration_hours": 7.8333, "session_count": 84}
    assert schedule["naps"] == {"count": 60, "mean_duration_hours": 1.0}
    week = _report(events[:10])["reports"]["sleep_schedule"]
    assert week["summary"] == {"median_bed_minutes_of_day": 10, "median_rise_minutes_of_day": 480,
                               "mean_duration_hours": 7.8333, "session_count": 5}
    assert week["naps"] == {"count": 5, "mean_duration_hours": 1.0}


@pytest.mark.parametrize("end", [None, "1767618000", True])
def test_sleep_report_rejects_missing_or_invalid_progress_end(end, caplog):
    """@brief 缺少真实结算终点时拒绝，不能把记录时刻或零当作睡眠终点。"""
    progress = _progress(1, _clock(0, 23), _clock(1, 7), "sleep", activity_id="sleep-1")
    if end is None:
        del progress["data"]["interval_end"]
    else:
        progress["data"]["interval_end"] = end
    with pytest.raises(EvaluationError, match="integer interval_end") as caught:
        _report([progress])
    assert caught.value.code == "invalid_input"
    assert "Activity progress requires an integer interval_end" in caplog.text


def test_meal_diversity_simpson_and_repetition_anchors() -> None:
    """@brief Simpson {a:2,c:1}=5/9；饮料、购买请求和非食品均不产生进食样本。"""
    events = [_action(50, "d1", "start_side_activity", {"action": "purchase", "target": ["幻影商品"]})]
    for seq, product, category in ((1, "a", "meal"), (2, "a", "meal"), (3, "b", "drink"),
                                   (4, "c", "snack"), (5, "d", "goods")):
        events.extend(_receipt_events(seq, product, _clock(0, 8, seq), category))
    diversity = _report(events)["reports"]["meal_diversity"]
    assert diversity["simpson_concentration"] == 0.555556
    assert diversity["receipt_count"] == 3 and diversity["distinct_products"] == 2
    assert diversity["product_counts"] == {"a": 2, "c": 1}
    window_events = []
    for seq, product, at in ((11, "a", _clock(0, 8)), (12, "a", _clock(0, 10)),
                              (13, "b", _clock(0, 20)), (14, "a", _clock(1, 1))):
        window_events.extend(_receipt_events(seq, product, at))
    assert _report(window_events)["reports"]["meal_diversity"]["repetition_24h_rate"] == 0.5












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
    """@brief 不完整的合成指标输入保持未知，不补造静默、记忆或身份测量；不带工作安排评估输入时工作负荷与作息偏离为 null
    并写说明。"""
    events = [
        _event("decision_started", _clock(0, 8), decision_id="d1"),
        _model_request(2, "d1", ["当前观察：在家，精神不错"]),
        _progress(3, _clock(0, 9), _clock(0, 10), 'interact', activity_id="w1"),
        _event("memory_updated", _clock(0, 20),
               data={"operation": "append_episodes", "episode_ids": ["f" * 64]}),
        _event("run_ended", _clock(0, 23), data={"reason": "window_end"}),
    ]
    report = _report(events)
    assert report["schema_version"] == "behavior-metrics-4"
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
    assert (report["reports"]["workload"], report["reports"]["routine_deviation"]) == (None, None)
    assert "未提供工作安排评估输入，workload 与 routine_deviation 为 null" in report["notes"]


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
        _progress(3, _clock(0, 9), _clock(0, 10), 'interact', activity_id="w1",
                  data_extra={"decision_offered": False, "wake_reasons": ["commitment_threatened"]}),
        _progress(4, _clock(0, 23), _clock(1, 7), "sleep", activity_id="s1",
                  snapshot_extra={"started_at": _clock(0, 23), "ended_at": _clock(1, 7)}),
        *_receipt_events(5, "noodle", _clock(0, 9)),
        _memory([{"text": "今天上班写了很多代码", "kind": "episode"}]),
    ]
    first = behavior_metrics_report(events, locations={},
                                    memory_md_texts={1: "李凡是后端工程师"})
    second = behavior_metrics_report(events, locations={},
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

def test_reports_have_no_retired_classification_or_passive_metrics():
    reports = _report([_event("run_ended", _clock(0, 23))])["reports"]
    retired = {"wait_minutes", "unknown_wait_minutes", "social", "effective_autonomous_decision_rate",
               "activity_distribution", "persona_distinguishability", "habitual_consistency", "deliberate_goals"}
    assert not retired & reports.keys()
    assert "external_constraints" in reports


def test_all_true_content_advances_count_without_a_content_category():
    start = _clock(0, 8)
    content = {"content_id": "actual-topic", "app_id": None,
               "position_before_seconds": 0, "position_after_seconds": 300}
    played = _progress(1, start, start + 300, "interact", data_extra={"content": content})
    browser = _progress(2, start + 300, start + 600, "interact",
                        snapshot_extra={"data": {"app_id": "store", "content_id": None}})
    stationary = _progress(3, start + 600, start + 900, "interact",
                           data_extra={"content": {**content, "position_after_seconds": 0}})
    assert _report([played, browser, stationary])["reports"]["content_contact_minutes"]["total_minutes"] == 5


def test_drinks_only_produce_no_food_samples():
    events = _receipt_events(1, "coffee", _clock(0, 8), "drink")
    sample = _report(events)["reports"]["meal_diversity"]
    assert sample["receipt_count"] == 0
    assert sample["simpson_concentration"] is None


def test_behavior_metrics_reject_retired_activity_before_reporting():
    import pytest
    from worldmock.evaluation.errors import EvaluationError

    event = _event("activity_progress", _clock(0, 8), data={"activity": {"kind": "work"}})
    with pytest.raises(EvaluationError, match="retired activity"):
        behavior_metrics_report([event])
