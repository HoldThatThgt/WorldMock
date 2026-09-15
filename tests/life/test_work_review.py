"""工作正文揭示、独立语义验收和付款测试。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import agentsociety2.config as agentsociety_config
from agentsociety2.society import DirectToolCall

from tests.life.conftest import advance_next, context, submit_round
from worldmock.errors import ProtocolError
from worldmock.footprints.models import EventDraft
from worldmock.life.models import (
    ContinueActivityRequest,
    MeetingMinutes,
    StartTargetedActivityRequest,
    SubmitWorkRequest,
)
from worldmock.life.scenario import load_scenario
from worldmock.life.work import WorkMetrics, WorkSubmission, load_work_input, load_work_oracle
from worldmock.life.work_review import (
    WorkReviewer,
    WorkReviewResponse,
    parse_work_review,
    prepare_work_review,
)


class StaticReviewer:
    """返回由测试控制的真实 LiteLLM 响应边界形状。"""

    def __init__(self, accepted: bool = True, omit_reference: bool = False,
                 reported_accepted: bool | None = None):
        self.accepted = accepted
        self.omit_reference = omit_reference
        self.reported_accepted = accepted if reported_accepted is None else reported_accepted
        self.calls = 0

    async def call(self, prepared):
        """使用实际请求的预期身份形成唯一工具调用。"""
        self.calls += 1
        references = list(prepared.expected_reference_ids)
        if self.omit_reference:
            references.pop()
        reference_rows = [{"reference_id": identity,
                           "required_fact_covered": self.accepted,
                           "source_supported": self.accepted,
                           "contradiction": not self.accepted,
                           "source_excerpt": "Hi , I'm David",
                           "rationale": "Supported by the transcript."}
                          for identity in references]
        delivery_rows = [{"delivery_path": path, "source_supported": self.accepted,
                          "contradiction": not self.accepted,
                          "source_excerpt": "Hi , I'm David",
                          "rationale": "Supported by the transcript."}
                         for path in prepared.expected_delivery_paths]
        arguments = {"reference_assessments": reference_rows,
                     "delivery_assessments": delivery_rows, "accepted": self.reported_accepted}
        return {"id": "review-response", "model": "deepseek-flash", "usage": {
                    "prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
                "choices": [{"message": {"tool_calls": [{"function": {
                    "name": "submit_work_review", "arguments": json.dumps(arguments)}}]}}]}


def _at_task_start(engine) -> None:
    """把测试夹具推进到任务开放时刻和办公室。"""
    task = engine.scenario.tasks[0]
    now = int(task.available_at.timestamp())
    staged = engine.state.model_copy(deep=True)
    staged.current_time = now
    staged.pending_events = [item for item in staged.pending_events if item.due_at > now]
    agent = staged.agents[1]
    visible = set(agent.visible_task_ids)
    visible.add(task.task_id)
    staged.agents[1] = agent.model_copy(
        update={"location_id": task.required_location_id, "visible_task_ids": visible}, deep=True)
    staged.version += 1
    event = EventDraft(event_type="environment_event", occurred_at=now, visibility="system",
                       source_kind="scenario", data={"kind": "test_task_start"})
    engine.store.commit(staged.model_dump(mode="json"), [event])
    engine.state = staged


def _complete_required_work(engine) -> None:
    """经实际活动入口完成首个任务所需有效工作时间。"""
    task = engine.scenario.tasks[0]
    duration = task.maximum_duration_seconds
    request = StartTargetedActivityRequest(
        activity_type="work", target_id=task.task_id,
        planned_duration_seconds=duration, playback_speed=1)
    _, results = submit_round(engine, {1: ("start_targeted_activity", request)}, "work-time")
    activity_id = results[0].activity_id
    feedback_index = 0
    while engine.state.activities[activity_id].status == "active":
        if 1 not in engine.scheduling_state().ready_agent_ids:
            advance_next(engine)
            continue
        feedback_index += 1
        request = ContinueActivityRequest(activity_id=activity_id)
        submit_round(engine, {1: ("continue_activity", request)}, f"work-feedback-{feedback_index}")


def _paraphrased_submission() -> SubmitWorkRequest:
    """形成不逐字复制官方参考的语义正确纪要。"""
    minutes = MeetingMinutes(
        meeting_id="ES2002a",
        summary="The team launched a remote-control project and discussed price, cost, and possible features.",
        decisions=("The retail price is 25 euros.", "The product is intended for international sale.",
                   "Manufacturing must stay at or below 12.50 euros."),
        action_items=("The industrial designer owns the working design.",
                      "The interface designer owns technical functions.",
                      "Marketing will define the product requirements."),
    )
    return SubmitWorkRequest(task_id="task-2026-01-05", submission_id="submission-one",
                             deliverable=minutes)


async def _submit_with_review(engine, reviewer: StaticReviewer, round_id: str):
    """走完整提交、模型验收准备和原子结算路径。"""
    engine._work_reviewer = reviewer
    ctx = context(engine, round_id)
    engine.begin_decision_round([ctx])
    call = DirectToolCall(ctx, f"action-{round_id}", "submit_work", _paraphrased_submission())
    engine.submit_action(call)
    await engine.prepare_pending_work_review(call.action_id)
    return engine.commit_decision_round(round_id)[0]


@pytest.mark.asyncio
async def test_work_material_requires_time_and_paraphrase_can_be_paid(engine_factory) -> None:
    """观察不免费泄漏正文，实际工作揭示全文，独立正例才支付。"""
    engine = engine_factory()
    _at_task_start(engine)
    before = engine.observe(context(engine, "before-work"))["tasks"][0]
    assert before["material"]["text"] == ""
    assert set(before["deliverable_schema"]["properties"]) == {
        "meeting_id", "summary", "decisions", "action_items"}
    _complete_required_work(engine)
    after = engine.observe(context(engine, "after-work"))["tasks"][0]
    assert after["material"]["end_char"] == after["material"]["total_chars"]
    balance = engine.state.accounts[engine.state.agents[1].balance_account_id]
    reviewer = StaticReviewer()
    result = await _submit_with_review(engine, reviewer, "accepted-work")
    assert result.status == "completed" and result.data["payment_fen"] == 60_000
    assert engine.state.accounts[engine.state.agents[1].balance_account_id] == balance + 60_000
    reviews = [event for event in engine.store.read_events(engine.store.last_event_seq)
               if event.data.get("purpose") == "work_review"]
    assert [event.visibility for event in reviews] == ["system", "system"]
    assert reviews[1].event_id in next(event for event in engine.store.read_events(
        engine.store.last_event_seq) if event.action_id == "action-accepted-work"
        and event.event_type == "action_accepted").caused_by
    paid_accounts = dict(engine.state.accounts)
    advance_next(engine)
    replay_context = context(engine, "accepted-work-replay")
    engine.begin_decision_round([replay_context])
    engine.observe(replay_context)
    engine.submit_action(DirectToolCall(
        replay_context, "action-accepted-work", "submit_work", _paraphrased_submission()))
    replay = engine.commit_decision_round("accepted-work-replay")[0]
    assert replay == result and engine.state.accounts == paid_accounts
    canonical = [event for event in engine.store.read_agent_events("action_result", 1)
                 if event.action_id == "action-accepted-work" and event.data.get("resource_changes")]
    assert len(canonical) == 1 and sum(canonical[0].data["resource_changes"].values()) == 0
    assert reviewer.calls == 1

    advance_next(engine)
    conflict_context = context(engine, "accepted-work-conflict")
    engine.begin_decision_round([conflict_context])
    engine.observe(conflict_context)
    changed = _paraphrased_submission().model_copy(update={"submission_id": "different-submission"})
    with pytest.raises(ProtocolError, match="reused with different parameters"):
        engine.submit_action(DirectToolCall(
            conflict_context, "action-accepted-work", "submit_work", changed))
    assert engine.state.accounts == paid_accounts and reviewer.calls == 1


@pytest.mark.asyncio
async def test_negated_or_unsupported_minutes_require_rework_without_payment(engine_factory) -> None:
    """即使模型自报不接受，代码也依据逐项矛盾拒绝并保持余额。"""
    engine = engine_factory()
    _at_task_start(engine)
    _complete_required_work(engine)
    balance = engine.state.accounts[engine.state.agents[1].balance_account_id]
    reviewer = StaticReviewer(accepted=False, reported_accepted=True)
    result = await _submit_with_review(engine, reviewer, "rework")
    assert result.status == "rejected" and result.error_code == "WORK_REQUIRES_REWORK"
    assert engine.state.accounts[engine.state.agents[1].balance_account_id] == balance
    assert "task-2026-01-05" not in engine.state.paid_task_ids


@pytest.mark.asyncio
async def test_incomplete_work_skips_model_review_and_is_business_rejection(engine_factory) -> None:
    """确定性进度不足不浪费外部调用，仍由结算写业务拒绝。"""
    engine = engine_factory()
    _at_task_start(engine)
    reviewer = StaticReviewer()
    result = await _submit_with_review(engine, reviewer, "incomplete")
    assert reviewer.calls == 0
    assert result.status == "rejected" and result.error_code == "WORK_INCOMPLETE"


@pytest.mark.asyncio
async def test_review_schema_failure_is_fatal_and_preserves_raw_response(engine_factory) -> None:
    """模型漏掉必需事实属于致命 Schema 故障，原始响应仍留为系统证据。"""
    engine = engine_factory()
    _at_task_start(engine)
    _complete_required_work(engine)
    reviewer = StaticReviewer(omit_reference=True)
    engine._work_reviewer = reviewer
    ctx = context(engine, "bad-schema")
    engine.begin_decision_round([ctx])
    call = DirectToolCall(ctx, "bad-schema", "submit_work", _paraphrased_submission())
    engine.submit_action(call)
    with pytest.raises(ProtocolError, match="incomplete or duplicated"):
        await engine.prepare_pending_work_review(call.action_id)
    responses = [event for event in engine.store.read_events(engine.store.last_event_seq)
                 if event.event_type == "model_response" and event.data.get("purpose") == "work_review"]
    assert responses[-1].data["response"]["id"] == "review-response"
    assert "incomplete or duplicated" in responses[-1].data["error"]
    engine.abort_decision_round("bad-schema", "expected schema failure")


def test_frozen_real_deepseek_responses_accept_paraphrase_and_reject_hallucination() -> None:
    """真实 DeepSeek 工具响应经过当前严格解析器保持正例通过、反例拒绝。"""
    fixture = json.loads(Path("tests/life/fixtures/work_review_deepseek_responses.json").read_text())
    scenario, _ = load_scenario(Path("scenarios/white-collar-12-weeks.json"))
    task = scenario.tasks[0]
    source = load_work_input(Path("corpora"), task)
    oracle = load_work_oracle(Path("corpora"), task)
    observed = []
    for case in fixture["cases"]:
        minutes = MeetingMinutes.model_validate(case["deliverable"])
        request = SubmitWorkRequest(task_id=task.task_id, submission_id=case["label"],
                                    deliverable=minutes)
        submission = WorkSubmission(task, None, request, WorkMetrics(60_000, 14_400, 14_400),
                                    oracle, source)
        prepared = prepare_work_review(submission, fixture["model"])
        decision = parse_work_review(case["raw_response"], prepared)
        observed.append(decision.accepted)
        assert decision.accepted is case["expected_accepted"]
        assert case["raw_response"]["usage"]["total_tokens"] > 0
    assert observed == [True, False]


def test_work_review_request_uses_required_strict_contract() -> None:
    """独立工作验收使用同一 beta strict 参数且本地保留数组非空硬约束。"""
    fixture = json.loads(Path("tests/life/fixtures/work_review_deepseek_responses.json").read_text())
    scenario, _ = load_scenario(Path("scenarios/white-collar-12-weeks.json"))
    task = scenario.tasks[0]
    case = fixture["cases"][0]
    request = SubmitWorkRequest(
        task_id=task.task_id,
        submission_id="strict-contract",
        deliverable=MeetingMinutes.model_validate(case["deliverable"]),
    )
    submission = WorkSubmission(
        task,
        None,
        request,
        WorkMetrics(60_000, 14_400, 14_400),
        load_work_oracle(Path("corpora"), task),
        load_work_input(Path("corpora"), task),
    )

    prepared = prepare_work_review(submission, "deepseek-flash")
    tool = prepared.request["tools"][0]["function"]

    assert prepared.request["tool_choice"] == "required"
    assert prepared.request["max_retries"] == 0
    assert prepared.request["extra_body"] == {"thinking": {"type": "disabled"}}
    assert tool["name"] == "submit_work_review" and tool["strict"] is True
    assert tool["parameters"] == WorkReviewResponse.model_json_schema(mode="validation")
    with pytest.raises(ValueError, match="requires reference and delivery"):
        WorkReviewResponse(
            reference_assessments=(),
            delivery_assessments=(),
            accepted=False,
        )


def _review_model_identity(**updates) -> dict:
    """形成 WorkReviewer 客户端身份测试使用的冻结参数。"""
    identity = {
        "requested_model": "deepseek-flash",
        "base_url": "https://api.deepseek.com/beta",
        "thinking": "disabled",
        "tool_choice": "required",
        "max_retries": 0,
    }
    identity.update(updates)
    return identity


def _bind_review_client(monkeypatch, model_name: str, base_url: str):
    """把实际客户端构造入口绑定到指定模型与完整 URL。"""
    client = SimpleNamespace(model_name=model_name, base_url=base_url)

    def build_client(role: str):
        assert role == "default"
        return client

    monkeypatch.setattr(agentsociety_config, "build_client_for_role", build_client)
    return client


@pytest.mark.parametrize(
    ("model_name", "base_url"),
    [
        ("deepseek-chat", "https://api.deepseek.com/beta"),
        ("deepseek-flash", "https://api.deepseek.com"),
    ],
)
def test_work_reviewer_rejects_model_or_full_base_url_mismatch(
    monkeypatch,
    model_name: str,
    base_url: str,
) -> None:
    """实际客户端的模型或完整 beta URL 不符时分别拒绝。"""
    _bind_review_client(monkeypatch, model_name, base_url)

    with pytest.raises(ProtocolError, match="identity disagrees"):
        WorkReviewer(_review_model_identity())


def test_work_reviewer_accepts_exact_client_and_request_identity(monkeypatch) -> None:
    """模型、完整 URL 和既有请求参数全部匹配时接受客户端。"""
    client = _bind_review_client(
        monkeypatch,
        "deepseek-flash",
        "https://api.deepseek.com/beta",
    )

    reviewer = WorkReviewer(_review_model_identity())

    assert reviewer.client is client


@pytest.mark.parametrize(
    "updates",
    [
        {"thinking": "enabled"},
        {"tool_choice": "auto"},
        {"max_retries": 1},
    ],
)
def test_work_reviewer_rejects_request_parameter_drift(monkeypatch, updates: dict) -> None:
    """思考、工具选择或重试参数偏离冻结合同时拒绝。"""
    _bind_review_client(monkeypatch, "deepseek-flash", "https://api.deepseek.com/beta")

    with pytest.raises(ProtocolError, match="parameters disagree"):
        WorkReviewer(_review_model_identity(**updates))
