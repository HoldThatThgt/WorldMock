"""工作正文揭示、独立语义验收和付款测试。"""

from __future__ import annotations

import json
import copy
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

import agentsociety2.config as agentsociety_config
from agentsociety2.agent.base import ReactLoopRequest
from agentsociety2.society import DirectToolCall
from custom.agents.life_agent import LifeAgent

from tests.life.conftest import advance_until_ready, advance_next, context, submit_round, stage_day_plan, wake_by_mail
from worldmock.errors import ProtocolError
from worldmock.footprints.models import EventDraft
from worldmock.life.models import (
    ContinueActivityRequest,
    MeetingMinutes,
    SetActivityStateRequest,
    StartTargetedActivityRequest,
    SubmitWorkRequest,
)
from worldmock.life.scenario import load_scenario
from worldmock.life.narration import render_facts, render_scene
from worldmock.life.result_projection import public_action_result
from worldmock.life.work import WorkMetrics, WorkSubmission, load_work_input, load_work_oracle
from worldmock.life.work_review import (
    WorkReviewer,
    WorkReviewDecision,
    WorkReviewOutputError,
    WorkReviewResponse,
    parse_work_review,
    prepare_work_review,
    public_work_review,
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
        return {"id": "review-response", "model": "deepseek-v4.1-flash", "usage": {
                    "prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
                "choices": [{"message": {"tool_calls": [{
                    "id": f"review-tool-{self.calls}", "type": "function", "function": {
                        "name": "submit_work_review", "arguments": json.dumps(arguments)}}]}}]}


class PrivateRationaleReviewer(StaticReviewer):
    """先返回含私有值的合法结构，后续响应可在现有协议重试中纠正。"""

    def __init__(self, collection: str, private_kind: str, invalid_attempts: int = 1):
        """保留真实请求响应，验证失败留证与纠错反馈。"""
        super().__init__()
        self.collection = collection
        self.private_kind = private_kind
        self.invalid_attempts = invalid_attempts
        self.requests = []
        self.responses = []
        self.private_value = ""

    async def call(self, prepared):
        """只污染公开理由字段，其余真实评审约束保持有效。"""
        raw = await super().call(prepared)
        self.requests.append(copy.deepcopy(prepared.request))
        if self.calls <= self.invalid_attempts:
            self.private_value = _private_rationale_value(prepared, self.private_kind)
            _replace_rationale(raw, self.collection, f"The source supports internal evidence [{self.private_value}].")
        self.responses.append(copy.deepcopy(raw))
        return raw


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


def _paraphrased_submission(task_id: str = "task-2026-01-05") -> SubmitWorkRequest:
    """形成不逐字复制官方参考的语义正确纪要。"""
    minutes = MeetingMinutes(
        summary="The team launched a remote-control project and discussed price, cost, and possible features.",
        decisions=("The retail price is 25 euros.", "The product is intended for international sale.",
                   "Manufacturing must stay at or below 12.50 euros."),
        action_items=("The industrial designer owns the working design.",
                      "The interface designer owns technical functions.",
                      "Marketing will define the product requirements."),
    )
    return SubmitWorkRequest(task_id=task_id, deliverable=minutes)


def _fixture_minutes(case: dict) -> MeetingMinutes:
    """从历史原始收据仅投影当前公开纪要字段。"""
    historical = case["deliverable"]
    return MeetingMinutes(
        summary=historical["summary"],
        decisions=tuple(historical["decisions"]),
        action_items=tuple(historical["action_items"]),
    )


async def _submit_with_review(
    engine,
    reviewer: StaticReviewer,
    round_id: str,
    request: SubmitWorkRequest | None = None,
):
    """走完整提交、模型验收准备和原子结算路径。"""
    engine._work_reviewer = reviewer
    ctx = context(engine, round_id)
    engine.begin_decision_round([ctx])
    work = request or _paraphrased_submission()
    call = DirectToolCall(ctx, f"action-{round_id}", "submit_work", work)
    engine.submit_action(call)
    await engine.prepare_pending_work_review(call.action_id)
    return engine.commit_decision_round(round_id)[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("accepted", [True, False])
async def test_prepared_work_review_is_private_until_round_settlement(engine_factory, accepted) -> None:
    """独立验收准备不改世界与观察，原子结算后下一次观察才出现公开反馈。"""
    engine = engine_factory()
    _at_task_start(engine)
    _complete_required_work(engine)
    engine._work_reviewer = StaticReviewer(accepted=accepted)
    ctx = context(engine, "delayed-review")
    engine.begin_decision_round([ctx])
    initial = engine.observe(ctx)
    snapshot = engine.state.model_dump(mode="json")
    call = DirectToolCall(ctx, "action-delayed-review", "submit_work", _paraphrased_submission())
    engine.submit_action(call)
    await engine.prepare_pending_work_review(call.action_id)
    assert engine._work_reviewer.calls == 1
    assert engine.state.model_dump(mode="json") == snapshot
    assert engine._recent_action_results(1) == []
    assert all(row["action_id"] != call.action_id for row in initial["facts"]["recent_action_results"])
    assert not any(event.action_id == call.action_id
                   for event in engine.store.read_agent_events("action_result", 1))
    result = engine.commit_decision_round(ctx.round_id)[0]
    advance_next(engine)
    observed = engine.observe(context(engine, "review-visible"))
    public = next(row for row in observed["facts"]["recent_action_results"] if row["action_id"] == call.action_id)
    assert public["work_review"] == json.loads(json.dumps(result.data["work_review"]))
    assert public["work_review"]["accepted"] is accepted
    assert "Supported by the transcript." in render_facts(observed["facts"])
    assert engine.observe(context(engine, "review-consumed"))["facts"]["recent_action_results"] == []


@pytest.mark.asyncio
async def test_nested_private_fields_in_real_settlement_do_not_enter_public_results(
    engine_factory, monkeypatch,
) -> None:
    """结算证据保留内部字段，事实清单只按公开评审的逐层白名单取值。"""
    engine = engine_factory()
    _at_task_start(engine)
    _complete_required_work(engine)
    original_dispatch = engine._dispatch
    expected = {}
    private = {"oracle_sha256": "PRIVATE_SENTINEL", "accounts": {"private-account": 123},
               "activity": {"status": "active"}, "energy_kcal": 2000,
               "reference_id": "summary:private", "oracle": {"expected": "PRIVATE_SENTINEL"}}

    def inject_private_fields(state, pending):
        """在真实领域结果中注入未来可能新增的内部证据字段。"""
        changed, result = original_dispatch(state, pending)
        data = copy.deepcopy(result.data)
        expected.update(copy.deepcopy(data["work_review"]))
        data.update(copy.deepcopy(private))
        data["work_review"].update(copy.deepcopy(private))
        for collection in ("required_fact_assessments", "delivery_assessments"):
            for assessment in data["work_review"][collection]:
                assessment.update(copy.deepcopy(private))
        return changed, result.model_copy(update={"data": data})

    monkeypatch.setattr(engine, "_dispatch", inject_private_fields)
    await _submit_with_review(engine, StaticReviewer(), "private-review")
    advance_next(engine)
    observed = engine.observe(context(engine, "private-review-visible"))
    public = next(row for row in observed["facts"]["recent_action_results"]
                  if row["action_id"] == "action-private-review")
    assert set(public) == {"action_id", "occurred_at", "status", "error_code", "message", "work_review"}
    assert public["work_review"] == json.loads(json.dumps(expected))
    assert "PRIVATE_SENTINEL" in json.dumps(observed["recent_action_results"])
    rendered = render_facts(observed["facts"])
    assert "PRIVATE_SENTINEL" not in rendered
    assert "private-account" not in rendered
    assert "resource_changes" not in rendered


def test_paused_work_keeps_progress_and_has_an_independent_todo(engine_factory) -> None:
    """已经开始但仍欠工时的工作暂停后，同时保留活动进度与独立待办提醒。"""
    engine = engine_factory()
    _at_task_start(engine)
    task = engine.scenario.tasks[0]
    wake_by_mail(engine, offset=900)
    request = StartTargetedActivityRequest(activity_type="work", target_id=task.task_id,
                                            planned_duration_seconds=task.maximum_duration_seconds,
                                            playback_speed=1)
    _, results = submit_round(engine, {1: ("start_targeted_activity", request)}, "pause-work-start")
    activity_id = results[0].activity_id
    assert advance_until_ready(engine)
    pause = SetActivityStateRequest(activity_id=activity_id, operation="pause")
    _, paused = submit_round(engine, {1: ("set_activity_state", pause)}, "pause-work")
    assert paused[0].status == "completed"
    assert advance_until_ready(engine)
    observed = engine.observe(context(engine, "paused-work-observation"))
    scene = render_scene(observed["scene"])
    assert "之前做到一半的" in scene and "还没收尾。" in scene
    assert observed["scene"]["paused_activities"][0]["progress"] in scene
    assert "有待办任务" in scene
    remaining = observed["facts"]["tasks"][0]["remaining_contract_seconds"]
    assert 0 < remaining < task.maximum_duration_seconds


@pytest.mark.asyncio
async def test_work_material_requires_time_and_paraphrase_can_be_paid(engine_factory) -> None:
    """观察不免费泄漏正文，实际工作揭示全文，独立正例才支付。"""
    engine = engine_factory()
    _at_task_start(engine)
    before = engine.observe(context(engine, "before-work"))["tasks"][0]
    assert before["material"]["text"] == ""
    assert set(before["deliverable_schema"]["properties"]) == {
        "summary", "decisions", "action_items"}
    _complete_required_work(engine)
    after = engine.observe(context(engine, "after-work"))["tasks"][0]
    assert after["material"]["end_char"] == after["material"]["total_chars"]
    request = _paraphrased_submission(after["task_id"])
    assert set(request.model_dump(mode="json")) == {"task_id", "deliverable"}
    balance = engine.state.accounts[engine.state.agents[1].balance_account_id]
    reviewer = StaticReviewer()
    result = await _submit_with_review(engine, reviewer, "accepted-work", request)
    assert result.status == "completed" and result.data["payment_fen"] == 60_000
    assert result.data["work_review"]["accepted"] is True
    assert "oracle_sha256" not in result.data
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

    advance_until_ready(engine)
    conflict_context = context(engine, "accepted-work-conflict")
    engine.begin_decision_round([conflict_context])
    engine.observe(conflict_context)
    request = _paraphrased_submission()
    changed_minutes = request.deliverable.model_copy(update={"summary": "Different summary."})
    changed = request.model_copy(update={"deliverable": changed_minutes})
    with pytest.raises(ProtocolError, match="reused with different parameters"):
        engine.submit_action(DirectToolCall(
            conflict_context, "action-accepted-work", "submit_work", changed))
    assert engine.state.accounts == paid_accounts and reviewer.calls == 1


@pytest.mark.asyncio
async def test_negated_or_unsupported_minutes_require_rework_without_payment(engine_factory) -> None:
    """即使模型自报不接受，代码也依据逐项矛盾拒绝并保持余额。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _at_task_start(engine)
    _complete_required_work(engine)
    balance = engine.state.accounts[engine.state.agents[1].balance_account_id]
    reviewer = StaticReviewer(accepted=False, reported_accepted=True)
    result = await _submit_with_review(engine, reviewer, "rework")
    assert result.status == "rejected" and result.error_code == "WORK_REQUIRES_REWORK"
    assert engine.state.accounts[engine.state.agents[1].balance_account_id] == balance
    assert "task-2026-01-05" not in engine.state.paid_task_ids
    feedback = result.data["work_review"]
    assert feedback["accepted"] is False and feedback["required_fact_missing"] > 0
    assert feedback["missing_content_categories"] == ["action_items", "decisions", "summary"]
    assert feedback["required_fact_assessments"][0]["source_excerpt"] == "Hi , I'm David"
    assert "reference_id" not in json.dumps(feedback)
    assert "oracle_sha256" not in json.dumps(feedback)

    advance_next(engine)
    ctx = context(engine, "rework-feedback")
    observation = engine.observe(ctx)
    visible_result = next(
        item for item in observation["recent_action_results"]
        if item["action_id"] == "action-rework"
    )
    visible = visible_result["result"]["data"]["work_review"]
    agent = LifeAgent()
    wrapped = [{"turn": 0, "action": "observe", "ok": True, "data": observation}]
    request = ReactLoopRequest(0, ctx.observed_at, wrapped)
    agent.prepare_react_loop(request)
    messages = json.dumps(agent.build_react_messages(request), ensure_ascii=False)
    # 完整证据与公开提示共同保留工作反馈；提示不包含内部参考身份与哈希。
    assert visible == json.loads(json.dumps(feedback))
    summaries = observation["facts"]["recent_action_results"]
    assert [row["action_id"] for row in summaries] == [
        row["action_id"] for row in observation["recent_action_results"]]
    public = next(row for row in summaries if row["action_id"] == "action-rework")
    assert set(public) == {"action_id", "occurred_at", "status", "error_code", "message", "work_review"}
    assert public["work_review"] == visible
    assert public["error_code"] == "WORK_REQUIRES_REWORK"
    assert summaries[-1]["message"] in messages
    assert "Supported by the transcript." in messages
    assert "source_excerpt" in messages and "Hi , I'm David" in messages
    assert "reference_id" not in messages and engine.scenario.tasks[0].oracle_path not in messages

    replay = context(engine, "rework-replay")
    engine.begin_decision_round([replay])
    engine.observe(replay)
    engine.submit_action(DirectToolCall(
        replay, "action-rework", "submit_work", _paraphrased_submission()))
    repeated = engine.commit_decision_round("rework-replay")[0]
    assert repeated == result and reviewer.calls == 1
    assert engine.state.accounts[engine.state.agents[1].balance_account_id] == balance

    advance_next(engine)
    revised_minutes = _paraphrased_submission().deliverable.model_copy(
        update={"summary": "The source supports the remote-control project, price, cost, and ownership."})
    revised = _paraphrased_submission().model_copy(update={"deliverable": revised_minutes})
    accepting = StaticReviewer()
    accepted = await _submit_with_review(engine, accepting, "rework-accepted", revised)
    assert accepted.status == "completed" and accepting.calls == 1
    assert engine.state.accounts[engine.state.agents[1].balance_account_id] == balance + 60_000


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
async def test_review_identity_failure_exhausts_three_calls_and_preserves_responses(engine_factory) -> None:
    """身份遗漏连续三次时留存全部响应并终止，不执行工作结算。"""
    engine = engine_factory()
    _at_task_start(engine)
    _complete_required_work(engine)
    reviewer = StaticReviewer(omit_reference=True)
    engine._work_reviewer = reviewer
    ctx = context(engine, "bad-schema")
    engine.begin_decision_round([ctx])
    call = DirectToolCall(ctx, "bad-schema", "submit_work", _paraphrased_submission())
    engine.submit_action(call)
    balance = dict(engine.state.accounts)
    with pytest.raises(ProtocolError, match="remained invalid after three"):
        await engine.prepare_pending_work_review(call.action_id)
    responses = [event for event in engine.store.read_events(engine.store.last_event_seq)
                 if event.event_type == "model_response" and event.data.get("purpose") == "work_review"]
    assert len(responses) == 3 and reviewer.calls == 3
    assert all(event.data["response"]["id"] == "review-response" for event in responses)
    assert all("reference_assessments.reference_id" in event.data["error"] for event in responses)
    assert engine.state.accounts == balance and not engine.state.paid_task_ids
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
        request = SubmitWorkRequest(task_id=task.task_id, deliverable=_fixture_minutes(case))
        submission = WorkSubmission(task, None, request, WorkMetrics(60_000, 14_400, 14_400),
                                    oracle, source)
        prepared = prepare_work_review(submission, fixture["model"])
        decision = parse_work_review(case["raw_response"], prepared)
        observed.append(decision.accepted)
        assert decision.accepted is case["expected_accepted"]
        assert case["raw_response"]["usage"]["total_tokens"] > 0
    assert observed == [True, False]


def _prepared_review_and_raw_response():
    """从真实响应夹具和真实工作输入生成可走完整公开链的评审。"""
    fixture = json.loads(Path("tests/life/fixtures/work_review_deepseek_responses.json").read_text())
    scenario, _ = load_scenario(Path("scenarios/white-collar-12-weeks.json"))
    task = scenario.tasks[0]
    case = fixture["cases"][0]
    request = SubmitWorkRequest(task_id=task.task_id, deliverable=_fixture_minutes(case))
    submission = WorkSubmission(
        task, None, request, WorkMetrics(60_000, 14_400, 14_400),
        load_work_oracle(Path("corpora"), task), load_work_input(Path("corpora"), task))
    return prepare_work_review(submission, fixture["model"]), copy.deepcopy(case["raw_response"])


def _replace_rationale(raw: dict, collection: str, text: str) -> None:
    """只更改合法工具响应中的指定理由，保持全部身份与其他判断有效。"""
    function = raw["choices"][0]["message"]["tool_calls"][0]["function"]
    arguments = json.loads(function["arguments"])
    arguments[collection][0]["rationale"] = text
    function["arguments"] = json.dumps(arguments)


def _render_public_review_decision(decision, source: str) -> str:
    """从真实公开评审函数经结果投影进入提示渲染。"""
    row = {"action_id": "reviewed-work", "occurred_at": 1767542400, "status": "completed",
           "error_code": None, "message": "Work completed.",
           "result": {"data": {"work_review": public_work_review(decision, source)}}}
    return render_facts({"recent_action_results": [public_action_result(row)]})


def _private_rationale_value(prepared, kind: str) -> str:
    """选择交叉引用身份、已知摘要或任意大小写摘要作为隐私反例。"""
    return {"reference": prepared.expected_reference_ids[-1],
            "known_digest": prepared.oracle_sha256,
            "unknown_digest": "0123456789abcdef" * 4,
            "uppercase_digest": "0123456789ABCDEF" * 4}[kind]


@pytest.mark.parametrize("collection", ["reference_assessments", "delivery_assessments"])
@pytest.mark.parametrize("private_kind", ["reference", "known_digest", "unknown_digest", "uppercase_digest"])
def test_private_rationale_is_rejected_in_parse_to_prompt_chain(collection, private_kind, caplog) -> None:
    """合法 Schema 中的私有理由必须在解析阶段阻止，不能经公开投影进入提示。"""
    prepared, raw = _prepared_review_and_raw_response()
    private = _private_rationale_value(prepared, private_kind)
    _replace_rationale(raw, collection, f"The summary is supported; internal evidence is [{private}].")
    with caplog.at_level(logging.ERROR), pytest.raises(WorkReviewOutputError, match="rationale") as caught:
        decision = parse_work_review(raw, prepared)
        _render_public_review_decision(decision, prepared.source)
    assert private not in str(caught.value)
    errors = [record.message for record in caplog.records if record.levelno == logging.ERROR]
    assert errors and all(private not in message for message in errors)
    assert all(not any("\u4e00" <= char <= "\u9fff" for char in message) for message in errors)


@pytest.mark.parametrize("collection", ["reference_assessments", "delivery_assessments"])
@pytest.mark.parametrize("private_kind", ["reference", "known_digest", "uppercase_digest"])
@pytest.mark.parametrize("restored_from_json", [False, True])
def test_reloaded_decision_cannot_bypass_private_rationale_validation(
    collection, private_kind, restored_from_json, caplog,
) -> None:
    """已经落盘的评审对象即使绕开响应解析，公开入口也不能泄漏私有理由。"""
    prepared, raw = _prepared_review_and_raw_response()
    decision = parse_work_review(raw, prepared)
    private = _private_rationale_value(prepared, private_kind)
    saved = decision.model_dump(mode="json")
    saved[collection][0]["rationale"] = f"This result depends on internal evidence [{private}]."
    restored = (WorkReviewDecision.model_validate_json(json.dumps(saved)) if restored_from_json
                else WorkReviewDecision(**saved))
    with caplog.at_level(logging.ERROR), pytest.raises(WorkReviewOutputError, match="rationale") as caught:
        _render_public_review_decision(restored, prepared.source)
    assert private not in str(caught.value)
    assert any(record.levelno == logging.ERROR for record in caplog.records)
    assert private not in caplog.text


@pytest.mark.parametrize("collection,public_collection", [
    ("reference_assessments", "required_fact_assessments"),
    ("delivery_assessments", "delivery_assessments"),
])
def test_full_legal_rationale_survives_parse_projection_and_render(collection, public_collection) -> None:
    """使用公开内容品类的完整长理由原样公开，不截断也不生成替代理由。"""
    prepared, raw = _prepared_review_and_raw_response()
    rationale = ("The summary, decisions and action_items agree with the source. " * 10
                 + "The complete rationale ends here.")
    _replace_rationale(raw, collection, rationale)
    decision = parse_work_review(raw, prepared)
    rendered = _render_public_review_decision(decision, prepared.source)
    review = json.loads(rendered)["recent_action_results"][0]["work_review"]
    assert review[public_collection][0]["rationale"] == rationale


@pytest.mark.asyncio
@pytest.mark.parametrize("collection", ["reference_assessments", "delivery_assessments"])
@pytest.mark.parametrize("private_kind", ["reference", "known_digest"])
async def test_private_rationale_retries_preserve_evidence_and_publish_only_corrected_feedback(
    engine_factory, collection, private_kind, caplog,
) -> None:
    """私有理由触发现有纠错预算，原始响应留存，结算和下一观察只发布合法反馈。"""
    engine = engine_factory()
    _at_task_start(engine)
    _complete_required_work(engine)
    reviewer = PrivateRationaleReviewer(collection, private_kind)
    with caplog.at_level(logging.ERROR):
        result = await _submit_with_review(engine, reviewer, "private-rationale-corrected")
    assert reviewer.calls == 2 and result.status == "completed"
    feedback = json.loads(reviewer.requests[1]["messages"][-1]["content"])
    assert feedback["PREVIOUS_INVALID_RESPONSE"] == reviewer.responses[0]
    assert "rationale" in feedback["VALIDATION_ERROR"]
    assert reviewer.private_value not in feedback["VALIDATION_ERROR"]
    responses = [event for event in engine.store.read_agent_events("model_response", 1)
                 if event.data.get("purpose") == "work_review"]
    assert len(responses) == 2
    assert responses[0].data["response"] == reviewer.responses[0]
    assert feedback["VALIDATION_ERROR"] in responses[0].data["error"]
    assert responses[0].visibility == "system"
    assert reviewer.private_value in json.dumps(responses[0].data["response"])
    assert any(record.levelno == logging.ERROR for record in caplog.records)
    assert reviewer.private_value not in caplog.text
    advance_next(engine)
    observed = engine.observe(context(engine, "corrected-review-visible"))
    rendered = render_facts(observed["facts"])
    assert "Supported by the transcript." in rendered
    assert reviewer.private_value not in rendered


@pytest.mark.asyncio
async def test_private_rationale_exhaustion_never_settles_work_or_leaks_in_error(engine_factory, caplog) -> None:
    """三次私有理由均留存原始证据并终止，错误与日志不包含私有值。"""
    engine = engine_factory()
    _at_task_start(engine)
    _complete_required_work(engine)
    reviewer = PrivateRationaleReviewer("delivery_assessments", "known_digest", invalid_attempts=3)
    balances = dict(engine.state.accounts)
    with caplog.at_level(logging.ERROR), pytest.raises(ProtocolError, match="remained invalid after three"):
        await _submit_with_review(engine, reviewer, "private-rationale-exhausted")
    responses = [event for event in engine.store.read_agent_events("model_response", 1)
                 if event.data.get("purpose") == "work_review"]
    assert reviewer.calls == len(responses) == 3
    assert [event.data["response"] for event in responses] == reviewer.responses
    assert all("rationale" in event.data["error"] for event in responses)
    assert all(reviewer.private_value not in event.data["error"] for event in responses)
    assert reviewer.private_value not in caplog.text
    assert engine.state.accounts == balances and not engine.state.paid_task_ids
    assert not any(event.action_id == "action-private-rationale-exhausted"
                   for event in engine.store.read_agent_events("action_result", 1))
    engine.abort_decision_round("private-rationale-exhausted", "expected private rationale failure")


def test_work_review_request_uses_required_strict_contract() -> None:
    """独立工作验收使用同一 beta strict 参数且本地保留数组非空硬约束。"""
    fixture = json.loads(Path("tests/life/fixtures/work_review_deepseek_responses.json").read_text())
    scenario, _ = load_scenario(Path("scenarios/white-collar-12-weeks.json"))
    task = scenario.tasks[0]
    case = fixture["cases"][0]
    request = SubmitWorkRequest(
        task_id=task.task_id,
        deliverable=_fixture_minutes(case),
    )
    submission = WorkSubmission(
        task,
        None,
        request,
        WorkMetrics(60_000, 14_400, 14_400),
        load_work_oracle(Path("corpora"), task),
        load_work_input(Path("corpora"), task),
    )

    prepared = prepare_work_review(submission, "deepseek-v4.1-flash")
    tool = prepared.request["tools"][0]["function"]

    assert prepared.request["tool_choice"] == "required"
    assert prepared.request["max_retries"] == 0
    assert prepared.request["extra_body"] == {"thinking": {"type": "disabled"}}
    instructions = " ".join(prepared.request["messages"][0]["content"].split())
    assert "Write each rationale using public content categories" in instructions
    assert "Never include internal reference identities or SHA-256 digests in a rationale." in instructions
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
        "requested_model": "deepseek-v4.1-flash",
        "base_url": "https://openrouter.ai/api/v1",
        "thinking": "enabled",
        "tool_choice": "auto",
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
        ("deepseek-chat", "https://openrouter.ai/api/v1"),
        ("deepseek-v4.1-flash", "https://api.deepseek.com"),
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
        "deepseek-v4.1-flash",
        "https://openrouter.ai/api/v1",
    )

    reviewer = WorkReviewer(_review_model_identity())

    assert reviewer.client is client


def test_work_reviewer_accepts_person_channel_thinking_divergence(monkeypatch) -> None:
    """运行身份记录人物通道思考参数，验收通道自身请求保持 disabled+required。"""
    _bind_review_client(monkeypatch, "deepseek-v4.1-flash", "https://openrouter.ai/api/v1")

    reviewer = WorkReviewer(_review_model_identity())

    assert reviewer.client is not None


def test_work_reviewer_rejects_request_parameter_drift(monkeypatch) -> None:
    """重试参数偏离冻结合同时拒绝。"""
    _bind_review_client(monkeypatch, "deepseek-v4.1-flash", "https://openrouter.ai/api/v1")

    with pytest.raises(ProtocolError, match="parameters disagree"):
        WorkReviewer(_review_model_identity(max_retries=1))
