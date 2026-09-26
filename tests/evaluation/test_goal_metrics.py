"""三层报告的事件回放、独立证据、隔离和缺样本验收。"""

from copy import deepcopy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from worldmock.evaluation.behavior_metrics import behavior_metrics_report
from worldmock.evaluation.goal_metrics import EXTERNAL_CONSTRAINT_ERROR_CODES, day_plan_metrics
from worldmock.footprints.models import canonical_json

CODE = "a" * 64
ZONE = ZoneInfo("Asia/Shanghai")
#: 外部约束闭集只保留营业、到达准入、路线与工作可用性（docs/dev/SPEC-time-and-energy.md 第五章「动作照常列出，
#: 做错了再说明」：删去 PICKUP_LOCATION_REQUIRED 与工作地点、技能不符的 ACTIVITY_PRECONDITION_UNMET）。
FROZEN_EXTERNAL_CODES = {
    "MERCHANT_CLOSED", "MERCHANT_OPENING_UNKNOWN", "LOCATION_ACCESS_DENIED",
    "WORK_TASK_NOT_AVAILABLE", "WORK_SUBMISSION_LATE",
    "FROZEN_ROUTE_NOT_FOUND", "ROUTE_UNREACHABLE", "ROUTE_INFORMATION_UNKNOWN",
    "DELIVERY_ROUTE_NOT_FOUND", "DELIVERY_ROUTE_UNREACHABLE", "DELIVERY_ROUTE_INFORMATION_UNKNOWN",
}
#: 人物选了带说明的动作而被拒绝：三个引导性拒绝码、占用冲突与已移出闭集的到店地点码，都计入 other_rejections。
GUIDED_REJECTION_CODES = (
    "ACTIVITY_PRECONDITION_UNMET", "ACTIVITY_CONCLUSION_REQUIRED", "SESSION_LIMIT_EXCEEDED",
    "RESOURCE_CONFLICT", "PICKUP_LOCATION_REQUIRED",
)


def clock(day=0, hour=8, minute=0):
    return int((datetime(2026, 9, 21, hour, minute, tzinfo=ZONE) + timedelta(days=day)).timestamp())


def event(sequence, kind, at, data=None, **fields):
    return {"event_id": f"e{sequence}", "event_seq": sequence, "event_type": kind, "occurred_at": at,
            "run_id": "run", "agent_id": 1, "code_sha256": CODE, "data": data or {}, **fields}


def goal(kind="工作", text="推进报告", registered_at=None, **fields):
    return {"intent_kind": kind, "goal": text, "registered_at": registered_at or clock(),
            "progressed_at": None, "progress_activity_id": None, **fields}


def plan_result(sequence, goals, at=None, day=0, **fields):
    return event(sequence, "action_result", at or clock(day),
                 {"status": "completed", "data": {"day_plan": {
                     "day_end": clock(day + 1, 0), "goals": goals}}}, action_id=f"plan-{sequence}", **fields)


def progress(sequence, start, end, kind="work", goals=None, day=0, activity_id=None, **fields):
    activity_id = activity_id or f"activity-{sequence}"
    data = {"interval_start": start, "interval_end": end, "activity": {
        "activity_id": activity_id, "agent_id": fields.get("agent_id", 1), "kind": kind,
        "status": "active", "location_id": "office"}}
    if goals is not None:
        data["day_plan"] = {"day_end": clock(day + 1, 0), "goals": goals}
    return event(sequence, "activity_progress", end, data, activity_id=activity_id, **fields)


def report(events):
    return day_plan_metrics(events, ZONE.key)[CODE]


def progressed_goal(at, activity_id, **fields):
    return goal(progressed_at=at, progress_activity_id=activity_id, **fields)


def test_revision_history_preserves_denominator_and_initial_priority():
    initial = [goal(), goal("运动", "慢跑")]
    unchanged = [initial[1], initial[0]]
    edited = [goal("工作", "提交报告", clock(0, 9)), initial[1]]
    events = [plan_result(1, initial), plan_result(2, unchanged, clock(0, 8, 30)),
              plan_result(3, edited, clock(0, 9)), plan_result(4, [initial[1]], clock(0, 10)),
              plan_result(5, [initial[1], goal(registered_at=clock(0, 11))], clock(0, 11))]
    final = [initial[1], progressed_goal(clock(0, 12), "work", registered_at=clock(0, 11))]
    events.append(progress(6, clock(0, 11), clock(0, 12), goals=final, activity_id="work"))
    result = report(events)["deliberate_goals"]
    day = result["per_agent_day"]["run:1:2026-09-21"]
    assert [item["status"] for item in day["goals"]] == ["discarded", "pending", "discarded", "progressed"]
    assert [item["initial_priority"] for item in day["goals"]] == [1, 2, 1, 2]
    assert day["goal_count"] == 4 and day["discarded_count"] == 2 and day["family_progress_rate"] == 0.25
    assert day["by_priority"]["1"]["family_progress_rate"] == 0
    assert day["by_priority"]["2"]["family_progress_rate"] == 0.5
    assert day["goals"][-1]["registration_event_id"] == "e5"
    assert day["goals"][-1]["progress_event_id"] == "e6"
    assert result["reference"] is None


def test_progressed_goal_survives_revisions_and_idempotent_replay():
    settled = progressed_goal(clock(0, 9), "work")
    events = [plan_result(1, [goal()]), progress(2, clock(), clock(0, 9), goals=[settled], activity_id="work"),
              plan_result(3, [settled, goal("运动", "跑步", clock(0, 10))], clock(0, 10))]
    repeated = deepcopy(events[0])
    repeated.update(event_id="replayed", event_seq=4, occurred_at=clock(0, 11))
    repeated["data"]["reused"] = True
    events.append(repeated)
    day = report(events)["deliberate_goals"]["per_agent_day"]["run:1:2026-09-21"]
    assert day["goal_count"] == 2 and day["progressed_count"] == 1
    assert day["goals"][0]["progress_event_id"] == "e2"


@pytest.mark.parametrize("change", [
    "zero_duration", "negative_duration", "future_interval", "failed", "cancelled", "wrong_snapshot_agent",
    "wrong_event_agent", "wrong_snapshot_activity", "wrong_activity", "before_registration", "wrong_family",
    "missing_category", "next_day", "forged_progress_time", "other_run", "no_registration",
])
def test_goal_progress_requires_matching_actual_positive_settlement(change):
    settled = progressed_goal(clock(0, 9), "work")
    registration = plan_result(1, [goal()])
    actual = progress(2, clock(), clock(0, 9), goals=[settled], activity_id="work")
    data, snapshot = actual["data"], actual["data"]["activity"]
    if change == "zero_duration":
        data["interval_start"] = data["interval_end"]
    elif change == "negative_duration":
        data["interval_start"] = data["interval_end"] + 1
    elif change == "future_interval":
        actual["occurred_at"] -= 1
    elif change in {"failed", "cancelled"}:
        snapshot["status"] = change
    elif change == "wrong_snapshot_agent":
        snapshot["agent_id"] = 2
    elif change == "wrong_event_agent":
        actual["agent_id"] = snapshot["agent_id"] = 2
    elif change == "wrong_snapshot_activity":
        snapshot["activity_id"] = "another"
    elif change == "wrong_activity":
        settled["progress_activity_id"] = "another"
    elif change == "before_registration":
        data.update(interval_start=clock(0, 6), interval_end=clock(0, 7))
        settled["progressed_at"] = actual["occurred_at"] = clock(0, 7)
    elif change == "wrong_family":
        snapshot["kind"] = "watch"
    elif change == "missing_category":
        snapshot["kind"] = "lifestyle"
    elif change == "next_day":
        data.update(interval_start=clock(1, 8), interval_end=clock(1, 9))
        settled["progressed_at"] = actual["occurred_at"] = clock(1, 9)
    elif change == "forged_progress_time":
        settled["progressed_at"] = clock(0, 10)
    elif change == "other_run":
        actual["run_id"] = "other"
    events = [actual] if change == "no_registration" else [registration, actual]
    assert report(events)["deliberate_goals"]["summary"]["progressed_count"] == 0


def test_isolated_progress_fields_in_action_results_cannot_prove_progress():
    events = [plan_result(1, [goal()]), plan_result(2, [progressed_goal(clock(0, 9), "ghost")], clock(0, 9))]
    result = report(events)["deliberate_goals"]
    assert result["summary"]["goal_count"] == 1 and result["summary"]["progressed_count"] == 0


def test_shared_family_uses_one_activity_once_and_first_matching_goal():
    goals = [goal("通勤", "到公司"), goal("外出", "散步")]
    both = [dict(item, progressed_at=clock(0, 9), progress_activity_id="travel") for item in goals]
    events = [plan_result(1, goals), progress(2, clock(), clock(0, 9), "travel", both, activity_id="travel"),
              progress(3, clock(0, 9), clock(0, 10), "travel", both, activity_id="travel")]
    day = report(events)["deliberate_goals"]["per_agent_day"]["run:1:2026-09-21"]
    assert [item["status"] for item in day["goals"]] == ["progressed", "pending"]
    assert day["progressed_count"] == 1


def test_later_valid_interval_cannot_reuse_activity_for_another_goal():
    goals = [goal("通勤", "到公司"), goal("外出", "散步")]
    first = dict(goals[0], progressed_at=clock(0, 9), progress_activity_id="travel")
    second = dict(goals[1], progressed_at=clock(0, 10), progress_activity_id="travel")
    events = [plan_result(1, goals),
              progress(2, clock(), clock(0, 9), "travel", [first, goals[1]], activity_id="travel"),
              progress(3, clock(0, 9), clock(0, 10), "travel", [first, second], activity_id="travel")]
    day = report(events)["deliberate_goals"]["per_agent_day"]["run:1:2026-09-21"]
    assert [item["status"] for item in day["goals"]] == ["progressed", "pending"]
    assert day["progressed_count"] == 1
    assert day["goals"][0]["progress_event_id"] == "e2"
    assert day["goals"][1]["progress_event_id"] is None


def test_lifestyle_uses_actual_category_and_cross_midnight_interval_stays_in_old_day():
    original = goal("运动", "散步运动", clock(0, 23))
    settled = dict(original, progressed_at=clock(1, 0), progress_activity_id="exercise")
    actual = progress(2, clock(0, 23), clock(1, 0), "lifestyle", [settled], activity_id="exercise")
    actual["data"]["activity"]["category"] = "sport"
    events = [plan_result(1, [original], clock(0, 23)), actual, plan_result(3, [], clock(1, 8), day=1)]
    days = report(events)["deliberate_goals"]["per_agent_day"]
    assert days["run:1:2026-09-21"]["progressed_count"] == 1
    assert days["run:1:2026-09-22"]["goal_count"] == 0
    assert days["run:1:2026-09-22"]["family_progress_rate"] is None


def test_report_isolates_runs_agents_days_and_code_groups():
    events = [plan_result(1, [goal()]), plan_result(2, [goal()], agent_id=2),
              plan_result(1, [goal()], run_id="other"),
              plan_result(3, [goal(registered_at=clock(1))], day=1),
              plan_result(1, [goal()], code_sha256="b" * 64)]
    groups = day_plan_metrics(events, ZONE.key)
    assert len(groups) == 2
    assert groups[CODE]["deliberate_goals"]["summary"]["agent_day_count"] == 4
    assert groups[CODE]["deliberate_goals"]["summary"]["goal_count"] == 4
    assert groups["b" * 64]["deliberate_goals"]["summary"]["goal_count"] == 1


def test_habit_uses_first_settlement_context_two_days_and_weighted_pairs():
    events = [progress(1, clock(), clock(0, 8, 10), activity_id="first"),
              progress(2, clock(0, 9), clock(0, 9, 10), "watch", activity_id="first"),
              progress(3, clock(1), clock(1, 8, 10)),
              progress(4, clock(1, 8, 20), clock(1, 8, 30), "watch"),
              progress(5, clock(0, 9), clock(0, 9, 10), "wait"),
              progress(6, clock(1, 9), clock(1, 9, 10), "wait"),
              progress(7, clock(0, 10), clock(0, 10, 10), "wait")]
    habit = report(events)["habitual_consistency"]
    assert habit["comparable_pair_count"] == 8
    assert habit["consistency_rate"] == 0.5
    assert habit["groups"]["run:1:office:8"]["sample_count"] == 3
    assert habit["groups"]["run:1:office:8"]["consistency_rate"] == 1 / 3
    assert habit["groups"]["run:1:office:10"]["consistency_rate"] is None
    assert habit["missing_samples"]["contexts_without_two_days"] == 1


def test_habit_does_not_merge_runs_people_locations_or_hours():
    events = [progress(1, clock(), clock(0, 8, 10)),
              progress(2, clock(1), clock(1, 8, 10), run_id="other"),
              progress(3, clock(1), clock(1, 8, 10), agent_id=2),
              progress(4, clock(1, 9), clock(1, 9, 10)),
              progress(5, clock(1), clock(1, 8, 10))]
    events[-1]["data"]["activity"]["location_id"] = "home"
    habit = report(events)["habitual_consistency"]
    assert len(habit["groups"]) == 5 and habit["comparable_pair_count"] == 0
    assert habit["consistency_rate"] is None


def test_habit_zero_duration_cannot_create_or_claim_first_activity_sample():
    empty = progress(1, clock(), clock(), activity_id="same")
    empty_habit = report([empty])["habitual_consistency"]
    assert empty_habit["groups"] == {}
    assert empty_habit["missing_samples"]["no_positive_activity_settlement"] == 1
    events = [empty, progress(2, clock(0, 9), clock(0, 9, 10), "watch", activity_id="same"),
              progress(3, clock(1, 9), clock(1, 9, 10), "watch", activity_id="next-day")]
    habit = report(events)["habitual_consistency"]
    assert set(habit["groups"]) == {"run:1:office:9"}
    group = habit["groups"]["run:1:office:9"]
    assert group["sample_count"] == 2 and group["day_count"] == 2
    assert [sample["event_id"] for sample in group["samples"]] == ["e2", "e3"]
    assert habit["consistency_rate"] == 1


def test_same_day_repetition_is_not_cross_day_habit_evidence():
    events = [progress(1, clock(), clock(0, 8, 10), activity_id="first"),
              progress(2, clock(0, 8, 20), clock(0, 8, 30), activity_id="second")]
    habit = report(events)["habitual_consistency"]
    group = habit["groups"]["run:1:office:8"]
    assert group["sample_count"] == 2 and group["day_count"] == 1
    assert group["comparable_pair_count"] == 0 and group["consistency_rate"] is None
    assert habit["comparable_pair_count"] == 0 and habit["consistency_rate"] is None
    assert habit["missing_samples"]["contexts_without_two_days"] == 1


def test_missing_lifestyle_category_or_location_does_not_guess_habits():
    events = [progress(1, clock(), clock(0, 8, 10), "lifestyle"),
              progress(2, clock(1), clock(1, 8, 10))]
    events[0]["data"]["activity"]["family"] = "work"
    events[1]["data"]["activity"].pop("location_id")
    habit = report(events)["habitual_consistency"]
    assert habit["groups"] == {} and habit["consistency_rate"] is None
    assert habit["missing_samples"]["missing_activity_family_or_location"] == 2


@pytest.mark.parametrize("code", sorted(FROZEN_EXTERNAL_CODES))
def test_all_frozen_external_constraint_codes_block_gate_once(code):
    rejected = event(1, "action_rejected", clock(), {"error_code": code}, action_id="action")
    result = event(2, "action_result", clock(), {"status": "rejected", "error_code": code}, action_id="action")
    replay = event(3, "action_rejected", clock(), {"error_code": code, "reused": True}, action_id="action")
    metrics = report([rejected, result, replay])
    external = metrics["external_constraints"]
    assert external["block_count"] == 1 and external["by_error_code"] == {code: 1}
    assert external["source_event_ids"] == ["e1"] and external["evidence_complete"] is True
    assert external["observed_action_acceptance_rate"] == 0
    assert metrics["gates"]["external_constraint_block_count"]["value"] == 1
    assert metrics["gates"]["external_constraint_block_count"]["pass"] is False


def test_external_constraint_actions_are_run_scoped_and_protocol_rejections_are_separate():
    events = [event(1, "action_result", clock(), {"status": "completed"}, action_id="same"),
              event(2, "action_result", clock(), {"status": "cancelled"}, action_id="cancelled"),
              event(3, "action_result", clock(), {"status": "failed", "error_code": "INTERNAL"}, action_id="failed"),
              event(4, "action_rejected", clock(), {"error_code": "BAD_REQUEST"}, action_id="bad"),
              event(5, "action_result", clock(), {"status": "in_progress"}, action_id="pending"),
              event(1, "action_rejected", clock(), {"error_code": "MERCHANT_CLOSED"},
                    action_id="same", run_id="other")]
    external = report(events)["external_constraints"]
    assert external["terminal_action_count"] == 5
    assert external["observed_action_acceptance_rate"] == 0.6
    assert external["other_rejections"] == {
        "count": 1, "by_error_code": {"BAD_REQUEST": 1}, "source_event_ids": ["e4"]}
    assert external["block_count"] == 1


@pytest.mark.parametrize("fields,data", [
    ({}, {"error_code": "MERCHANT_CLOSED"}), ({"action_id": "missing"}, {}),
    ({"action_id": "missing"}, {"error_code": ""}),
])
def test_missing_rejection_evidence_fails_closed(fields, data):
    metrics = report([event(1, "action_rejected", clock(), data, **fields)])
    assert metrics["external_constraints"]["evidence_complete"] is False
    assert metrics["gates"]["external_constraint_block_count"]["value"] is None
    assert metrics["gates"]["external_constraint_block_count"]["pass"] is False


def test_empty_samples_are_explicit_and_shared_behavior_entrypoint_matches():
    events = [event(1, "run_ended", clock())]
    metrics = report(events)
    assert metrics["habitual_consistency"]["consistency_rate"] is None
    assert metrics["habitual_consistency"]["missing_samples"]
    assert metrics["deliberate_goals"]["summary"]["family_progress_rate"] is None
    assert metrics["deliberate_goals"]["notes"]
    assert metrics["external_constraints"]["observed_action_acceptance_rate"] is None
    assert metrics["gates"]["external_constraint_block_count"]["pass"] is True
    behavior = behavior_metrics_report(events)[CODE]
    for name in ("habitual_consistency", "external_constraints", "deliberate_goals"):
        assert behavior["reports"][name] == metrics[name]
    assert behavior["gates"]["external_constraint_block_count"] == metrics["gates"]["external_constraint_block_count"]
    assert "plan_actual_deviation" not in behavior["reports"]
    assert canonical_json(day_plan_metrics(events, ZONE.key)) == canonical_json(day_plan_metrics(events, ZONE.key))
    assert day_plan_metrics([], ZONE.key) == {}


def test_registration_cannot_claim_a_different_local_day_and_order_is_deterministic():
    invalid = plan_result(1, [goal()], clock(1), day=0)
    events = [invalid, plan_result(2, [], clock(1), day=1)]
    metrics = report(events)
    assert list(metrics["deliberate_goals"]["per_agent_day"]) == ["run:1:2026-09-22"]
    assert canonical_json(report(events)) == canonical_json(report(list(reversed(events))))


def test_habit_context_hour_uses_first_interval_start_when_settlement_crosses_hour():
    events = [progress(1, clock(0, 8, 59), clock(0, 9, 1)),
              progress(2, clock(1, 8, 50), clock(1, 9, 10))]
    habit = report(events)["habitual_consistency"]
    assert list(habit["groups"]) == ["run:1:office:8"]
    assert habit["comparable_pair_count"] == 2 and habit["consistency_rate"] == 1


def test_terminal_action_replay_does_not_turn_cancelled_activity_into_rejection():
    terminal = event(1, "action_result", clock(), {"status": "cancelled"}, action_id="cancelled")
    replay = event(2, "action_rejected", clock(), {"status": "cancelled", "reused": True}, action_id="cancelled")
    metrics = report([terminal, replay])
    assert metrics["external_constraints"]["terminal_action_count"] == 1
    assert metrics["external_constraints"]["observed_action_acceptance_rate"] == 1
    assert metrics["external_constraints"]["evidence_complete"] is True
    assert metrics["external_constraints"]["other_rejections"]["count"] == 0


def test_external_constraint_error_vocabulary_is_the_frozen_spec_set():
    assert EXTERNAL_CONSTRAINT_ERROR_CODES == FROZEN_EXTERNAL_CODES
    assert not EXTERNAL_CONSTRAINT_ERROR_CODES & set(GUIDED_REJECTION_CODES)


def test_guided_rejections_count_as_other_rejections_not_external_constraints():
    """引导性拒绝（需要在某地或先结束手上的事、先收尾、名额已满）与占用冲突是人物选了带说明的动作：
    逐动作计入 other_rejections，不计为外部约束，外部约束门禁仍通过（第五章「动作照常列出，做错了再说明」）。"""
    events = []
    for index, code in enumerate(GUIDED_REJECTION_CODES):
        action = f"guided-{index}"
        events += [
            event(3 * index + 1, "action_rejected", clock(), {"error_code": code}, action_id=action),
            event(3 * index + 2, "action_result", clock(), {"status": "rejected", "error_code": code},
                  action_id=action),
            event(3 * index + 3, "action_rejected", clock(), {"error_code": code, "reused": True}, action_id=action),
        ]
    metrics = report(events)
    external = metrics["external_constraints"]
    assert external["block_count"] == 0 and external["by_error_code"] == {}
    assert external["other_rejections"] == {
        "count": len(GUIDED_REJECTION_CODES),
        "by_error_code": {code: 1 for code in sorted(GUIDED_REJECTION_CODES)},
        "source_event_ids": sorted(f"e{3 * index + 1}" for index in range(len(GUIDED_REJECTION_CODES)))}
    assert external["evidence_complete"] is True and external["observed_action_acceptance_rate"] == 0
    assert metrics["gates"]["external_constraint_block_count"]["value"] == 0
    assert metrics["gates"]["external_constraint_block_count"]["pass"] is True


@pytest.mark.parametrize("status", ["rejected", "failed"])
def test_external_constraint_failure_from_action_terminal_without_rejection_event(status):
    metrics = report([event(1, "action_result", clock(),
                            {"status": status, "error_code": "WORK_SUBMISSION_LATE"}, action_id="late")])
    assert metrics["external_constraints"]["block_count"] == 1
    assert metrics["external_constraints"]["observed_action_acceptance_rate"] == 0
    assert metrics["gates"]["external_constraint_block_count"]["pass"] is False


def test_habit_does_not_replace_missing_first_settlement_with_later_known_context():
    first = progress(1, clock(), clock(0, 8, 10), "lifestyle", activity_id="same")
    later = progress(2, clock(1), clock(1, 8, 10), "lifestyle", activity_id="same")
    later["data"]["activity"]["category"] = "sport"
    habit = report([first, later])["habitual_consistency"]
    assert habit["groups"] == {}
    assert habit["missing_samples"]["missing_activity_family_or_location"] == 1


def test_habit_outdoor_and_travel_share_the_established_commute_family():
    travel = progress(1, clock(), clock(0, 8, 10), "travel")
    outdoor = progress(2, clock(1), clock(1, 8, 10), "lifestyle")
    outdoor["data"]["activity"]["category"] = "outdoor"
    habit = report([travel, outdoor])["habitual_consistency"]
    assert habit["consistency_rate"] == 1 and habit["comparable_pair_count"] == 2
    assert [sample["family"] for sample in habit["groups"]["run:1:office:8"]["samples"]] == ["commute", "commute"]
