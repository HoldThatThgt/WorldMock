"""工作合同类型、工时上限、资金和多人任务竞争测试。"""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from pathlib import Path

import pytest

from agentsociety2.society import DirectToolCall

from tests.life.conftest import advance_next, context
from worldmock.errors import BusinessRuleError, IntegrityError
from worldmock.footprints.models import EventDraft
from worldmock.life.models import (
    ActivityState,
    MeetingMinutes,
    ReportStatusRequest,
    StartActivityRequest,
    SubmitWorkActionRequest,
    SubmitWorkRequest,
)
from worldmock.life.scenario import load_scenario
from worldmock.life.work import (
    load_work_input,
    load_work_oracle,
    prepare_work_submission,
    settle_work,
    validate_work_duration,
    validate_work_start,
)


class AcceptingReviewer:
    """按实际评审请求返回完整且来源支持的严格工具响应。"""

    def __init__(self) -> None:
        self.calls = 0

    async def call(self, prepared):
        """对请求中的每个隐藏事实和实际交付路径逐项给出支持证据。"""
        self.calls += 1
        references = [{"reference_id": identity, "required_fact_covered": True,
                       "source_supported": True, "contradiction": False,
                       "source_excerpt": "Hi , I'm David", "rationale": "Supported."}
                      for identity in prepared.expected_reference_ids]
        deliveries = [{"delivery_path": path, "source_supported": True,
                       "contradiction": False, "source_excerpt": "Hi , I'm David",
                       "rationale": "Supported."} for path in prepared.expected_delivery_paths]
        arguments = {"reference_assessments": references,
                     "delivery_assessments": deliveries, "accepted": True}
        return {"id": f"review-{self.calls}", "model": "deepseek-v4.1-flash",
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
                "choices": [{"message": {"tool_calls": [{
                    "id": f"review-tool-{self.calls}", "type": "function", "function": {
                        "name": "submit_work_review", "arguments": json.dumps(arguments)}}]}}]}


def _submission(task) -> SubmitWorkRequest:
    """只用公开任务身份和公开交付 Schema 构造有类型纪要。"""
    minutes = MeetingMinutes(summary="Supported summary.", decisions=("Supported decision.",),
                             action_items=("Supported action.",))
    return SubmitWorkRequest(task_id=task.task_id, deliverable=minutes)


def _completed_work_state(engine, task, worked: int, effective: float, agent_id: int = 1):
    """形成已通过真实活动字段表达的完成工作状态供合同纯函数验证。"""
    state = engine.state.model_copy(deep=True)
    now = int(task.available_at.timestamp()) + worked
    state.current_time = now
    agent = state.agents[agent_id]
    visible = set(agent.visible_task_ids)
    visible.add(task.task_id)
    state.agents[agent_id] = agent.model_copy(
        update={"location_id": task.required_location_id, "visible_task_ids": visible,
                "next_decision_at": now, "body_settled_at": now}, deep=True)
    activity = ActivityState(
        activity_id=f"completed-{task.task_id}-{agent_id}", agent_id=agent_id, kind="work",
        status="completed", started_at=now - worked, ended_at=now, planned_end_at=now,
        last_settled_at=now, elapsed_seconds=worked, planned_duration_seconds=worked,
        effective_work_seconds=effective, target_id=task.task_id,
        origin_action_id=f"work-{agent_id}", origin_decision_id=f"work-decision-{agent_id}",
        origin_request_event_id=f"work-request-{agent_id}")
    state.activities[activity.activity_id] = activity
    return state


@pytest.mark.parametrize(
    ("task_id", "worked", "expected_payment"),
    (("hourly-2026-01-05", 3600, 10_000), ("piece-2026-01-05", 3600, 15_000)),
)
def test_hourly_and_piece_contracts_use_actual_time_and_frozen_piece_count(
    engine_factory, task_id, worked, expected_payment,
) -> None:
    """计时合同按实际秒数，计件合同按冻结件数，技能产出不重复降低工资。"""
    engine = engine_factory("multi-person.json")
    task = next(item for item in engine.scenario.tasks if item.task_id == task_id)
    state = _completed_work_state(engine, task, worked, task.required_effective_seconds)
    submission = prepare_work_submission(
        state, engine.semantics, engine.corpus_root, 1, _submission(task))
    outcome = settle_work(state, submission, True)
    assert submission.metrics.worked_seconds == worked
    assert outcome.payment_fen == expected_payment
    assert outcome.state.accounts["agent:1"] == state.accounts["agent:1"] + expected_payment
    assert outcome.state.accounts[task.employer_account_id] == (
        state.accounts[task.employer_account_id] - expected_payment)


def test_work_start_rejects_skill_location_and_deadline_boundaries(engine_factory) -> None:
    """技能、位置和有效期分别形成稳定业务拒绝。"""
    engine = engine_factory("multi-person.json")
    task = engine.scenario.tasks[0]
    now = int(task.available_at.timestamp())
    agent = engine.state.agents[1].model_copy(update={"visible_task_ids": {task.task_id}}, deep=True)
    with pytest.raises(BusinessRuleError, match="ACTIVITY_PRECONDITION_UNMET"):
        validate_work_start(agent, task, now)
    at_office = agent.model_copy(update={"location_id": task.required_location_id}, deep=True)
    unskilled = at_office.model_copy(update={"skill_levels": {task.required_skill: 0.1}}, deep=True)
    with pytest.raises(BusinessRuleError, match="ACTIVITY_PRECONDITION_UNMET"):
        validate_work_start(unskilled, task, now)
    with pytest.raises(BusinessRuleError, match="WORK_TASK_NOT_AVAILABLE"):
        validate_work_start(at_office, task, int((task.due_at + timedelta(seconds=1)).timestamp()))


def test_scenario_material_oracles_load_explicit_json_references() -> None:
    """scenario 材料走显式 JSON 参考装载：摘要句注入 expected，输入/参考分离。"""
    scenario, _ = load_scenario(Path("scenarios/white-collar-12-weeks.json"))
    task = next(item for item in scenario.tasks
                if item.oracle_path == "scenario-work/references/work-0021.oracle.json")
    source = load_work_input(Path("corpora"), task)
    oracle = load_work_oracle(Path("corpora"), task)
    reference = json.loads(Path("corpora/scenario-work/references/work-0021.reference.json").read_text(
        encoding="utf-8"))
    assert oracle["type"] == "scenario"
    assert oracle["expected"]["summary"] == reference["summary_sentences"]
    assert oracle["expected"]["decisions"] and oracle["expected"]["action_items"]
    for sentence in reference["summary_sentences"]:
        assert sentence not in source  # 参考对评估端可见，不进人物输入


def test_scenario_material_reference_missing_sentences_fail_closed(tmp_path) -> None:
    """参考 JSON 缺摘要句、损坏 JSON 与悬空 source_reference 都 fail-closed。"""
    root = tmp_path / "corpora"
    references = root / "scenario-work/references"
    references.mkdir(parents=True)
    oracle_path = references / "broken.oracle.json"

    def shell(payload: dict):
        """写入 oracle 并构造身份匹配的极简任务壳。"""
        oracle_path.write_text(json.dumps(payload))
        return type("Shell", (), {
            "oracle_path": "scenario-work/references/broken.oracle.json",
            "oracle_sha256": hashlib.sha256(oracle_path.read_bytes()).hexdigest(),
        })()

    (references / "broken.reference.json").write_text(
        json.dumps({"type": "scenario", "summary_sentences": []}))
    with pytest.raises(IntegrityError, match="Work reference summary sentences are missing"):
        load_work_oracle(root, shell(
            {"source_reference": "references/broken.reference.json", "expected": {}}))
    (references / "broken.reference.json").write_text("{not json")
    with pytest.raises(IntegrityError, match="Work reference is invalid JSON"):
        load_work_oracle(root, shell(
            {"source_reference": "references/broken.reference.json", "expected": {}}))
    with pytest.raises(IntegrityError, match="Work reference path is invalid"):
        load_work_oracle(root, shell(
            {"source_reference": "references/absent.reference.json", "expected": {}}))


@pytest.mark.parametrize(
    ("task_id", "worked", "expected_payment"),
    (("backend-hourly-2026-01-05", 3600, 10_000), ("backend-piece-2026-01-05", 3600, 15_000)),
)
def test_li_fan_backend_contracts_settle_with_scenario_materials(
    engine_factory, task_id, worked, expected_payment,
) -> None:
    """李凡的后端材料合同按计时实际秒数与冻结件数结算。"""
    engine = engine_factory("multi-person.json")
    task = next(item for item in engine.scenario.tasks if item.task_id == task_id)
    assert task.assignee_agent_id == "agent:2"
    assert task.required_skill == "backend_engineering"
    assert task.input_path.startswith("scenario-work/inputs/")
    state = _completed_work_state(engine, task, worked, task.required_effective_seconds, agent_id=2)
    submission = prepare_work_submission(
        state, engine.semantics, engine.corpus_root, 2, _submission(task))
    outcome = settle_work(state, submission, True)
    assert outcome.payment_fen == expected_payment
    assert outcome.state.accounts["agent:2"] == state.accounts["agent:2"] + expected_payment
    assert outcome.state.accounts[task.employer_account_id] == (
        state.accounts[task.employer_account_id] - expected_payment)


def test_cumulative_work_limit_counts_cancelled_progress_across_restarts(engine_factory) -> None:
    """已取消活动的实际工作秒数仍占合同总上限，不能靠重复启动绕过。"""
    engine = engine_factory("multi-person.json")
    task = next(item for item in engine.scenario.tasks if item.task_id == "hourly-2026-01-05")
    state = _completed_work_state(engine, task, 3600, 2160)
    activity_id = next(iter(state.activities))
    state.activities[activity_id] = state.activities[activity_id].model_copy(
        update={"status": "cancelled", "boundary_type": "cancelled"}, deep=True)
    validate_work_duration(state, task, 1, 3600)
    with pytest.raises(BusinessRuleError, match="WORK_DURATION_EXCEEDS_CONTRACT"):
        validate_work_duration(state, task, 1, 3601)


def test_late_duplicate_and_unfunded_work_stop_before_semantic_payment(engine_factory) -> None:
    """迟交、重复身份和雇主无资金都在付款前拒绝且不改变输入状态。"""
    engine = engine_factory("multi-person.json")
    task = engine.scenario.tasks[0]
    request = _submission(task)
    state = _completed_work_state(engine, task, task.maximum_duration_seconds,
                                  task.required_effective_seconds)
    late = state.model_copy(update={"current_time": int(task.due_at.timestamp()) + 1}, deep=True)
    with pytest.raises(BusinessRuleError, match="WORK_SUBMISSION_LATE"):
        prepare_work_submission(late, engine.semantics, engine.corpus_root, 1, request)
    duplicate = state.model_copy(update={"paid_task_ids": {task.task_id}}, deep=True)
    with pytest.raises(BusinessRuleError, match="WORK_TASK_ALREADY_PAID"):
        prepare_work_submission(duplicate, engine.semantics, engine.corpus_root, 1, request)
    accounts = dict(state.accounts)
    accounts[task.employer_account_id] = 0
    unfunded = state.model_copy(update={"accounts": accounts}, deep=True)
    frozen = unfunded.model_dump(mode="json")
    with pytest.raises(BusinessRuleError, match="EMPLOYER_FUNDS_INSUFFICIENT"):
        prepare_work_submission(unfunded, engine.semantics, engine.corpus_root, 1, request)
    assert unfunded.model_dump(mode="json") == frozen


def _stage_shared_work(engine) -> None:
    """把多人夹具移到首个工作机会并让两人位于办公室。"""
    task = engine.scenario.tasks[0]
    now = int(task.available_at.timestamp())
    staged = engine.state.model_copy(deep=True)
    staged.current_time = now
    staged.pending_events = [event for event in staged.pending_events if event.due_at > now]
    for agent_id, agent in staged.agents.items():
        visible = set(agent.visible_task_ids)
        visible.add(task.task_id)
        staged.agents[agent_id] = agent.model_copy(
            update={"location_id": task.required_location_id, "visible_task_ids": visible,
                    "next_decision_at": now, "body_settled_at": now}, deep=True)
    staged.version += 1
    event = EventDraft(event_type="environment_event", occurred_at=now, visibility="system",
                       source_kind="scenario", data={"kind": "test_shared_work_start"})
    engine.store.commit(staged.model_dump(mode="json"), [event])
    engine.state = staged


def _shared_round(engine, round_id: str, requests: dict[int, tuple[str, object]]) -> list:
    """按公布结算顺序观察，并按相反到达顺序登记多人动作。"""
    ready = engine.scheduling_state().ready_agent_ids
    contexts = {agent_id: context(engine, round_id, agent_id) for agent_id in ready}
    engine.begin_decision_round([contexts[agent_id] for agent_id in ready])
    observations = {agent_id: engine.observe(ctx) for agent_id, ctx in contexts.items()}
    for agent_id in reversed(ready):
        tool, request = requests[agent_id]
        if callable(request):
            request = request(observations[agent_id])
        engine.submit_action(DirectToolCall(contexts[agent_id], f"{round_id}-{agent_id}", tool, request))
    return ready


def _complete_shared_work(engine, task) -> None:
    """让两个人物通过全部实际反馈边界完成同一任务的最大合同工时。"""
    def start(observed):
        binding = next(row for row in observed["interaction_bindings"]
                       if row["kind"] == "work_task" and row["object_id"] == task.task_id)
        return StartActivityRequest(targets=[binding["object"]], actions=["工作"])

    ready = _shared_round(engine, "shared-work", {1: ("start_activity", start),
                                                   2: ("start_activity", start)})
    engine.commit_decision_round("shared-work")
    feedback = 0
    while any(engine.state.activities[f"activity-shared-work-{agent_id}"].status == "active"
              for agent_id in ready):
        if not engine.scheduling_state().ready_agent_ids:
            advance_next(engine)
            continue
        feedback += 1
        requests = {agent_id: ("report_status", ReportStatusRequest(
            task=[next(item.session_name for item in engine.state.sessions.values()
                       if item.agent_id == agent_id
                       and f"activity-shared-work-{agent_id}" in item.activity_ids)],
            summary=["继续完成工作。"])) for agent_id in ready}
        _shared_round(engine, f"shared-feedback-{feedback}", requests)
        engine.commit_decision_round(f"shared-feedback-{feedback}")


@pytest.mark.asyncio
async def test_same_task_competition_pays_only_seeded_first_settlement(engine_factory) -> None:
    """两人完成并同轮提交同一任务时，只按预定顺序支付一次，调用到达不决定胜者。"""
    engine = engine_factory("multi-person.json")
    _stage_shared_work(engine)
    task = engine.scenario.tasks[0]
    _complete_shared_work(engine, task)
    reviewer = AcceptingReviewer()
    engine._work_reviewer = reviewer
    requests = {agent_id: ("submit_work", SubmitWorkActionRequest(
        task_name=task.title, deliverable=_submission(task).deliverable))
                for agent_id in engine.state.agents}
    ready = _shared_round(engine, "shared-submit", requests)
    for agent_id in ready:
        await engine.prepare_pending_work_review(f"shared-submit-{agent_id}")
    before = dict(engine.state.accounts)
    results = engine.commit_decision_round("shared-submit")
    by_agent = {result.agent_id: result for result in results}
    winner, loser = ready
    assert by_agent[winner].status == "completed"
    assert by_agent[loser].status == "rejected"
    assert by_agent[loser].error_code == "WORK_TASK_ALREADY_PAID"
    assert engine.state.accounts[f"agent:{winner}"] == before[f"agent:{winner}"] + task.pay_fen
    assert engine.state.accounts[f"agent:{loser}"] == before[f"agent:{loser}"]
    assert engine.state.accounts[task.employer_account_id] == before[task.employer_account_id] - task.pay_fen
    money = [event for event in engine.store.read_events(engine.store.last_event_seq)
             if event.event_type == "action_result" and event.data.get("resource_changes")]
    assert len(money) == 1 and money[0].agent_id == winner
    assert reviewer.calls == 2
