"""三层报告的事件回放、独立证据、隔离和缺样本验收。"""

from copy import deepcopy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from worldmock.evaluation.behavior_metrics import behavior_metrics_report
from worldmock.evaluation.goal_metrics import UNREPORTED_CODE_KEY, day_plan_metrics
from worldmock.footprints.models import canonical_json

CODE = "a" * 64
ZONE = ZoneInfo("Asia/Shanghai")
#: 人物选了带说明的动作而被拒绝：三个引导性拒绝码、占用冲突与已移出闭集的到店地点码，都计入 other_rejections。
GUIDED_REJECTION_CODES = (
    "ACTIVITY_PRECONDITION_UNMET", "ACTIVITY_CONCLUSION_REQUIRED", "SESSION_LIMIT_EXCEEDED",
    "RESOURCE_CONFLICT", "SUPPLY_SOURCE_MISMATCH",
)


def clock(day=0, hour=8, minute=0):
    return int((datetime(2026, 9, 21, hour, minute, tzinfo=ZONE) + timedelta(days=day)).timestamp())


def event(sequence, kind, at, data=None, **fields):
    return {"event_id": f"e{sequence}", "event_seq": sequence, "event_type": kind, "occurred_at": at,
            "run_id": "run", "agent_id": 1, "code_sha256": CODE, "action_id": None, "data": data or {}, **fields}


def goal(kind="工作", text="推进报告", registered_at=None, **fields):
    return {"intent_kind": kind, "goal": text, "registered_at": registered_at or clock(),
            "progressed_at": None, "progress_activity_id": None, **fields}


def plan_result(sequence, goals, at=None, day=0, generated=False, **fields):
    """当日目标登记：generated 为零点计划的 day_plan_generated 环境事件，否则为修订的已完成动作结果。"""
    plan = {"day_end": clock(day + 1, 0), "goals": goals}
    if generated:
        return event(sequence, "environment_event", at or clock(day),
                     {"kind": "day_plan_generated", "day_plan": plan, "source_event_ids": []},
                     visibility="system", **fields)
    return event(sequence, "action_result", at or clock(day), {"status": "completed", "data": {"day_plan": plan}},
                 action_id=f"plan-{sequence}", **fields)


def progress(sequence, start, end, kind="work_study", goals=None, day=0, activity_id=None, **fields):
    activity_id = activity_id or f"activity-{sequence}"
    data = {"interval_start": start, "interval_end": end, "activity": {
        "activity_id": activity_id, "agent_id": fields.get("agent_id", 1), "kind": kind,
        "status": "active", "location_id": "office",
        "data": {"category": kind}}}
    if kind in {"work_study", "entertainment", "exercise", "social", "daily_care"}:
        data["activity"]["kind"] = "action"
    if goals is not None:
        data["day_plan"] = {"day_end": clock(day + 1, 0), "goals": goals}
    return event(sequence, "activity_progress", end, data, activity_id=activity_id, **fields)


def report(events):
    return day_plan_metrics(events, ZONE.key)[CODE]


def progressed_goal(at, activity_id, **fields):
    return goal(progressed_at=at, progress_activity_id=activity_id, **fields)
















def _started(event: dict, started: int | None) -> dict:
    """给推进事件的活动快照写上实际开始时刻（None 表示快照没有这个键）。"""
    if started is not None:
        event["data"]["activity"]["started_at"] = started
    return event






















def test_external_constraint_actions_are_run_scoped_and_protocol_rejections_are_separate():
    events = [event(1, "action_result", clock(), {"status": "completed"}, action_id="same"),
              event(2, "action_result", clock(), {"status": "cancelled"}, action_id="cancelled"),
              event(3, "action_result", clock(), {"status": "failed", "error_code": "INTERNAL"}, action_id="failed"),
              event(4, "action_rejected", clock(), {"error_code": "BAD_REQUEST"}, action_id="bad"),
              event(5, "action_result", clock(), {"status": "in_progress"}, action_id="pending"),
              event(1, "action_rejected", clock(), {"error_code": "ACTION_REQUEST_INVALID"},
                    action_id="same", run_id="other")]
    external = report(events)["external_constraints"]
    assert external["terminal_action_count"] == 5
    assert external["observed_action_acceptance_rate"] == 0.6
    assert external["other_rejections"] == {
        "count": 2, "by_error_code": {"ACTION_REQUEST_INVALID":1, "BAD_REQUEST":1},
        "source_event_ids": ["e1", "e4"]}
    assert external["block_count"] == 0


@pytest.mark.parametrize("fields,data", [
    ({}, {"error_code": "ACTION_REQUEST_INVALID"}), ({"action_id": "missing"}, {}),
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
    assert set(metrics) == {"external_constraints", "gates"}
    assert metrics["external_constraints"]["observed_action_acceptance_rate"] is None
    assert metrics["gates"]["external_constraint_block_count"]["pass"] is True
    behavior = behavior_metrics_report(events)[CODE]
    for name in ("external_constraints",):
        assert behavior["reports"][name] == metrics[name]
    assert behavior["gates"]["external_constraint_block_count"] == metrics["gates"]["external_constraint_block_count"]
    assert "plan_actual_deviation" not in behavior["reports"]
    assert canonical_json(day_plan_metrics(events, ZONE.key)) == canonical_json(day_plan_metrics(events, ZONE.key))
    assert day_plan_metrics([], ZONE.key) == {}






def test_terminal_action_replay_does_not_turn_cancelled_activity_into_rejection():
    terminal = event(1, "action_result", clock(), {"status": "cancelled"}, action_id="cancelled")
    replay = event(2, "action_rejected", clock(), {"status": "cancelled", "reused": True}, action_id="cancelled")
    metrics = report([terminal, replay])
    assert metrics["external_constraints"]["terminal_action_count"] == 1
    assert metrics["external_constraints"]["observed_action_acceptance_rate"] == 1
    assert metrics["external_constraints"]["evidence_complete"] is True
    assert metrics["external_constraints"]["other_rejections"]["count"] == 0


@pytest.mark.parametrize("code", [
    "LOCATION_CLOSED", "INSUFFICIENT_FUNDS", "INSUFFICIENT_TRAVEL_FUNDS",
    "DELIVERY_NOT_DUE", "PARCEL_NOT_READY", "CONTENT_EXHAUSTED",
])
def test_actual_world_constraint_is_counted_once_and_fails_the_block_gate(code):
    events = [event(1, "action_rejected", clock(), {"error_code": code}, action_id="blocked"),
              event(2, "action_result", clock(), {"status": "rejected", "error_code": code},
                    action_id="blocked"),
              event(3, "action_rejected", clock(), {"error_code": code, "reused": True}, action_id="blocked")]
    metrics = report(events)
    external = metrics["external_constraints"]
    assert external["block_count"] == 1 and external["by_error_code"] == {code: 1}
    assert external["source_event_ids"] == ["e1"]
    assert external["other_rejections"]["count"] == 0
    assert external["observed_action_acceptance_rate"] == 0
    assert metrics["gates"]["external_constraint_block_count"]["value"] == 1
    assert metrics["gates"]["external_constraint_block_count"]["pass"] is False


def test_real_unaffordable_purchase_is_an_external_block(engine_factory):
    from tests.life.conftest import purchase
    from tests.life.environment_runtime import act

    def empty_balance(persona, config):
        persona["initial_balance_fen"] = 0

    engine = engine_factory(mutate=empty_balance)
    _, result = act(engine, "unaffordable-purchase", purchase("柠檬茶"))
    assert result.status == "rejected" and result.error_code == "INSUFFICIENT_FUNDS"
    events = [row.model_dump(mode="json") for row in engine.context.store.read_events(
        engine.context.store.last_event_seq)]
    metrics = day_plan_metrics(events, engine.context.config.timezone)[UNREPORTED_CODE_KEY]
    assert metrics["external_constraints"]["block_count"] == 1
    assert metrics["external_constraints"]["by_error_code"] == {"INSUFFICIENT_FUNDS": 1}
    assert metrics["external_constraints"]["other_rejections"]["count"] == 0
    assert metrics["gates"]["external_constraint_block_count"]["pass"] is False


def test_real_rejected_start_group_counts_two_rejected_actions(engine_factory):
    """一个被拒的开始组是两条被拒动作：步行（不花钱）与买不起的柠檬茶同组，整组不开始，两条动作各有同一个拒绝码；
    按动作计数的外部约束不合并，记两次受阻、两条终态动作，证据是两条动作各自的拒绝事件。"""
    from tests.life.conftest import purchase, travel
    from tests.life.environment_runtime import act_group

    def empty_balance(persona, config):
        persona["initial_balance_fen"] = 0

    engine = engine_factory(mutate=empty_balance)
    _, results = act_group(engine, "unaffordable-group", travel("公司"), purchase("柠檬茶"))
    assert [(row.status, row.error_code) for row in results] == [("rejected", "INSUFFICIENT_FUNDS")] * 2
    assert engine.context.state.activities == {}
    events = [row.model_dump(mode="json") for row in engine.context.store.read_events(
        engine.context.store.last_event_seq)]
    rejected = [row for row in events if row["event_type"] == "action_rejected"]
    assert [row["action_id"] for row in rejected] == ["action-unaffordable-group-1", "action-unaffordable-group-2"]
    metrics = day_plan_metrics(events, engine.context.config.timezone)[UNREPORTED_CODE_KEY]
    external = metrics["external_constraints"]
    assert external["block_count"] == 2 and external["by_error_code"] == {"INSUFFICIENT_FUNDS": 2}
    assert external["source_event_ids"] == sorted(row["event_id"] for row in rejected)
    assert external["terminal_action_count"] == 2 and external["observed_action_acceptance_rate"] == 0
    assert external["other_rejections"]["count"] == 0 and external["evidence_complete"] is True
    assert metrics["gates"]["external_constraint_block_count"]["value"] == 2
    assert metrics["gates"]["external_constraint_block_count"]["pass"] is False


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
def test_terminal_rejections_and_activity_failures_have_distinct_public_counts(status):
    """@brief 请求被拒与已受理活动失败分开计数；二者都不再制造已删除的外部约束。"""
    rejected = status == "rejected"
    metrics = report([event(1, "action_result", clock(),
                            {"status": status, "error_code": "ACTION_REQUEST_INVALID" if rejected else None},
                            action_id="terminal")])
    assert metrics["external_constraints"]["block_count"] == 0
    assert metrics["external_constraints"]["other_rejections"] == {
        "count":int(rejected), "by_error_code":{"ACTION_REQUEST_INVALID":1} if rejected else {},
        "source_event_ids":["e1"] if rejected else []}
    assert metrics["external_constraints"]["observed_action_acceptance_rate"] == int(not rejected)
    assert metrics["gates"]["external_constraint_block_count"]["pass"] is True
