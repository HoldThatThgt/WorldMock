"""工作正文揭示、独立语义验收和付款测试。"""

from __future__ import annotations

import json
import copy
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

import agentsociety2.config as agentsociety_config
from agentsociety2.society import DirectToolCall
from worldmock.catalog.affordances import default_affordances

from tests.life.conftest import (activity_name, advance_next, advance_until_ready, conclude_pending, context,
                                 progress_calls, stage_day_plan, start_activity, submit_round, target_actions,
                                 tool_targets, wake_by_mail, start_request, pause_request, report_request)
from tests.life.test_work_contracts import (
    AcceptingReviewer,
    _completed_work_state,
    _shared_round,
    _stage_shared_work,
    _submission,
)
from worldmock.errors import ProtocolError
from worldmock.footprints.models import EventDraft
from worldmock.life.models import (active_activities, ActivityState, MeetingMinutes, SubmitWorkActionRequest,
                                   SubmitWorkRequest)
from worldmock.life.scenario import load_scenario
from worldmock.life.result_projection import public_result
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
        return {"id": "review-response", "model": "deepseek-flash", "usage": {
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
        update={"location_id": task.required_location_id, "visible_task_ids": visible,
                "body_settled_at": now}, deep=True)
    staged.version += 1
    event = EventDraft(event_type="environment_event", occurred_at=now, visibility="system",
                       source_kind="scenario", data={"kind": "test_task_start"})
    engine.store.commit(staged.model_dump(mode="json"), [event])
    engine.state = staged


def _complete_required_work(engine) -> None:
    """经实际活动入口完成首个任务所需有效工作时间；工作按计划结束即成待收尾，当秒收尾（规格第七章「活动的生命周期」）。"""
    task = engine.scenario.tasks[0]
    observed = engine.observe(context(engine, "work-time-preview"))
    choice = next((row, action) for row in observed["interaction_bindings"]
                  for action in row["actions"]
                  if row["object_id"] == task.task_id and action["canonical_action"] == "work")
    request = start_request(targets=[choice[0]["object"]], actions=[choice[1]["verb"]],
                                   ways=[choice[1]["label"]])
    _, results = submit_round(engine, {1: ("start_activity", request)}, "work-time")
    activity_id = results[0].activity_id
    feedback_index = 0
    while engine.state.activities[activity_id].status == "active":
        if 1 not in engine.scheduling_state().ready_agent_ids:
            advance_next(engine)
            continue
        feedback_index += 1
        feedback = report_request(activity_name(engine, activity_id), summary="继续完成工作。")
        submit_round(engine, {1: ("report_status", feedback)}, f"work-feedback-{feedback_index}")
    assert conclude_pending(engine, "work-time-end").status == "completed"
    assert engine.state.activities[activity_id].concluded_at == engine.state.current_time


def _public_submission(engine, request: SubmitWorkRequest) -> SubmitWorkActionRequest:
    """把领域内部合同身份转换为人物可见任务名。"""
    return SubmitWorkActionRequest(task_name=f"工作：{engine.rules.task(request.task_id).title}",
                                   deliverable=request.deliverable)


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
    engine.observe(ctx)
    work = request or _paraphrased_submission()
    call = DirectToolCall(ctx, f"action-{round_id}", "submit_work", _public_submission(engine, work))
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
    call = DirectToolCall(ctx, "action-delayed-review", "submit_work",
                          _public_submission(engine, _paraphrased_submission()))
    engine.submit_action(call)
    await engine.prepare_pending_work_review(call.action_id)
    assert engine._work_reviewer.calls == 1
    assert engine.state.model_dump(mode="json") == snapshot
    assert all("work_review" not in row["result"] for row in progress_calls(initial))
    assert not any(event.action_id == call.action_id
                   for event in engine.store.read_agent_events("action_result", 1))
    result = engine.commit_decision_round(ctx.round_id)[0]
    observed = engine.observe(context(engine, "review-visible"))
    public = next(row["result"] for row in progress_calls(observed, "submit_work") if "work_review" in row["result"])
    assert public["work_review"] == json.loads(json.dumps(result.data["work_review"]))
    assert public["work_review"]["accepted"] is accepted
    assert "Supported by the transcript." in json.dumps(observed["progress_layer"], ensure_ascii=False)
    repeated = engine.observe(context(engine, "review-still-pending"))
    assert progress_calls(repeated) == progress_calls(observed)


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
    observed = engine.observe(context(engine, "private-review-visible"))
    public = next(row["result"] for row in progress_calls(observed, "submit_work") if "work_review" in row["result"])
    assert set(public) == {"occurred_at", "status", "error_code", "message", "details", "work_review"}
    assert public["work_review"] == json.loads(json.dumps(expected))
    assert "PRIVATE_SENTINEL" in json.dumps([event.data for event in engine.store.read_agent_events("action_result", 1)])
    rendered = json.dumps(observed["progress_layer"], ensure_ascii=False)
    assert "PRIVATE_SENTINEL" not in rendered
    assert "private-account" not in rendered
    assert "resource_changes" not in rendered


def test_paused_work_keeps_progress_and_has_an_independent_todo(engine_factory) -> None:
    """已经开始但仍欠工时的工作暂停后，暂停的活动在待办层（进度不写「还差」），任务目标的 contract 写达标情况。"""
    engine = engine_factory()
    _at_task_start(engine)
    task = engine.scenario.tasks[0]
    wake_by_mail(engine, offset=900)
    observed = engine.observe(context(engine, "pause-work-preview"))
    choice = next((row, action) for row in observed["interaction_bindings"]
                  for action in row["actions"]
                  if row["object_id"] == task.task_id and action["canonical_action"] == "work")
    request = start_request(targets=[choice[0]["object"]], actions=[choice[1]["verb"]],
                                   ways=[choice[1]["label"]])
    _, results = submit_round(engine, {1: ("start_activity", request)}, "pause-work-start")
    activity_id = results[0].activity_id
    assert advance_until_ready(engine)
    pause = pause_request(activity_name(engine, activity_id), summary="工作进行到一半，先暂停。")
    _, paused = submit_round(engine, {1: ("pause_activity", pause)}, "pause-work")
    assert paused[0].status == "completed"
    assert advance_until_ready(engine)
    observed = engine.observe(context(engine, "paused-work-observation"))
    paused_item = observed["todo_layer"]["paused_activities"][0]
    assert paused_item["name"] == activity_name(engine, activity_id)
    assert paused_item["progress"].startswith("已做") and "还差" not in paused_item["progress"]
    contract = tool_targets(observed)[f"工作：{task.title}"]["contract"]
    assert contract["remaining"].startswith("有效工时还差")
    remaining = observed["tasks"][0]["remaining_contract_seconds"]
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
    assert advance_until_ready(engine)
    replay_context = context(engine, "accepted-work-replay")
    engine.begin_decision_round([replay_context])
    engine.observe(replay_context)
    engine.submit_action(DirectToolCall(
        replay_context, "action-accepted-work", "submit_work",
        _public_submission(engine, _paraphrased_submission())))
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
            conflict_context, "action-accepted-work", "submit_work", _public_submission(engine, changed)))
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

    ctx = context(engine, "rework-feedback")
    observation = engine.observe(ctx)
    visible = next(event.data["data"]["work_review"] for event in engine.store.read_agent_events("action_result", 1)
                   if event.action_id == "action-rework")
    messages = json.dumps(observation["progress_layer"], ensure_ascii=False)
    # 完整证据与进度层共同保留工作反馈；进度层不包含内部参考身份与哈希。
    assert visible == json.loads(json.dumps(feedback))
    summaries = [row["result"] for row in progress_calls(observation)]
    public = next(row for row in summaries if row.get("error_code") == "WORK_REQUIRES_REWORK")
    assert set(public) == {"occurred_at", "status", "error_code", "message", "work_review", "details"}
    assert public["work_review"] == visible
    assert public["error_code"] == "WORK_REQUIRES_REWORK"
    # 返工拒绝同样带一句原因与下一步（docs/dev/SPEC-time-and-energy.md 第五章「动作照常列出，做错了再说明」）。
    assert public["details"] == {"reason": "提交的成果还需要返工", "next_step": "按评审意见修改后再提交"}
    assert (result.data["reason"], result.data["next_step"]) == ("提交的成果还需要返工", "按评审意见修改后再提交")
    assert summaries[-1]["message"] in messages
    assert "Supported by the transcript." in messages
    assert "source_excerpt" in messages and "Hi , I'm David" in messages
    assert "reference_id" not in messages and engine.scenario.tasks[0].oracle_path not in messages

    replay = context(engine, "rework-replay")
    engine.begin_decision_round([replay])
    engine.observe(replay)
    engine.submit_action(DirectToolCall(
        replay, "action-rework", "submit_work", _public_submission(engine, _paraphrased_submission())))
    repeated = engine.commit_decision_round("rework-replay")[0]
    assert repeated == result and reviewer.calls == 1
    assert engine.state.accounts[engine.state.agents[1].balance_account_id] == balance

    # 工作结束的这一秒已用满同刻轮数（收尾、提交、重放）：受限轮里先开始等待，时钟推进后在下一次机会重交（第一章规则 3）。
    _, waited = submit_round(engine, {1: _WAIT}, "rework-wait")
    assert waited[0].status == "in_progress"
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine)
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


def _work_moment(engine, now: int, worked: int = 0, effective: float = 0.0):
    """人物在办公室、首个任务可见的指定时刻；worked 大于 0 时另有一段已收尾的该任务工作（按活动字段表达）。"""
    task = engine.scenario.tasks[0]
    staged = engine.state.model_copy(deep=True)
    staged.current_time = now
    staged.pending_events = [item for item in staged.pending_events if item.due_at > now]
    agent = staged.agents[1]
    staged.agents[1] = agent.model_copy(update={
        "location_id": task.required_location_id, "visible_task_ids": {*agent.visible_task_ids, task.task_id},
        "next_decision_at": now, "body_settled_at": now}, deep=True)
    if worked:
        done = ActivityState(
            activity_id="activity-done-work", agent_id=1, location_id=task.required_location_id, kind="work",
            status="completed", started_at=now - worked, ended_at=now, planned_end_at=now, last_settled_at=now,
            elapsed_seconds=worked, planned_duration_seconds=worked, effective_work_seconds=effective,
            target_id=task.task_id, concluded_at=now,
            resource_claims=set(default_affordances().resource_claims("work")),
            origin_action_id="done-work", origin_decision_id="done-work-decision",
            origin_request_event_id="done-work-request")
        staged.activities[done.activity_id] = done
    return staged


@pytest.mark.parametrize(("limit", "reason"), [
    pytest.param("deadline", "离截止不到一分钟，来不及开始", id="deadline"),
    pytest.param("contract", "这项工作的合同工时只剩不到一分钟，来不及开始", id="contract"),
])
def test_work_start_under_one_minute_stays_listed_and_is_rejected_with_reason_and_next_step(
    engine_factory, limit: str, reason: str,
) -> None:
    """开工条件是距截止与剩余合同都不少于 60 秒：恰好 60 秒时开工动作不带说明；不足 60 秒时照常列出并带说明，选了以
    业务拒绝 ACTIVITY_DURATION_INVALID 返回同一原因与下一步「提交已完成的部分」，不开始活动，也不产生 run_failed
    （docs/dev/SPEC-time-and-energy.md 第八章「工作」）。"""
    engine = engine_factory()
    task = engine.scenario.tasks[0]
    due, start = int(task.due_at.timestamp()), int(task.available_at.timestamp())

    def moment(left: int):
        """距截止（或剩余合同）还剩 left 秒的办公室时刻；合同一支已做的工作尚未达到要求的有效秒数。"""
        if limit == "deadline":
            return _work_moment(engine, due - left)
        worked = task.maximum_duration_seconds - left
        return _work_moment(engine, start + worked, worked, task.required_effective_seconds - 400)

    def work_row(observed: dict) -> dict:
        """本轮工具层中这项工作的「工作」动作。"""
        return target_actions(observed, f"工作：{task.title}")["工作"]

    engine.state = moment(60)
    assert (work_row(engine._observation_data(engine.state.agents[1]))["available"],
            work_row(engine._observation_data(engine.state.agents[1]))["reason"]) == (True, None)
    staged = moment(59)
    staged.version += 1
    engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time, visibility="system",
        source_kind="scenario", data={"kind": "test_late_work_start"})])
    engine.state = staged
    ctx = context(engine, f"late-work-{limit}")
    engine.begin_decision_round([ctx])
    row = work_row(engine.observe(ctx))
    assert (row["available"], row["reason"]) == (False, reason)
    engine.submit_action(DirectToolCall(ctx, f"action-late-work-{limit}", "start_activity",
                                        start_activity("工作", f"工作：{task.title}")))
    result = engine.commit_decision_round(ctx.round_id)[0]
    assert result.status == "rejected" and result.error_code == "ACTIVITY_DURATION_INVALID"
    assert not active_activities(engine.state, 1)
    public = progress_calls(engine.observe(context(engine, f"late-work-{limit}-feedback")))[-1]["result"]
    assert public["error_code"] == "ACTIVITY_DURATION_INVALID"
    assert public["details"] == {"reason": reason, "next_step": "提交已完成的部分"}
    assert not any(event.event_type == "run_failed"
                   for event in engine.store.read_events(engine.store.last_event_seq))


def test_completion_outlook_appears_only_when_the_time_upper_bound_is_short(engine_factory) -> None:
    """剩余要求的有效秒数大于 min(距截止秒数, 剩余合同秒数) 时任务目标 contract.outlook 才写「到截止前做不完」，
    相等或更宽裕时为 null（docs/dev/SPEC-persona-prompt.md「工具层」）。"""
    engine = engine_factory()
    task = engine.scenario.tasks[0]
    due, start = int(task.due_at.timestamp()), int(task.available_at.timestamp())
    required = task.required_effective_seconds
    worked = task.maximum_duration_seconds - 3600
    assert due - (start + worked) > 3600  # 下面两例由剩余合同 3600 秒约束，截止更宽裕
    cases = [
        (start, 0, 0.0, False),  # 刚开放：剩余合同与截止都宽裕
        (due - int(required), 0, 0.0, False),  # 距截止恰好等于剩余要求
        (due - int(required) + 1, 0, 0.0, True),  # 距截止少一秒
        (start + worked, worked, required - 3600, False),  # 剩余合同恰好等于剩余要求
        (start + worked, worked, required - 3601, True),  # 剩余要求多一秒
    ]
    for now, done, effective, short in cases:
        engine.state = _work_moment(engine, now, done, effective)
        observed = engine._observation_data(engine.state.agents[1])
        contract = tool_targets(observed)[f"工作：{task.title}"]["contract"]
        assert contract["outlook"] == ("到截止前做不完" if short else None), (now, done, effective)


_WAIT =("start_activity", start_activity("等待", "自己"))


def _stage_second_task_ready(engine) -> None:
    """多人夹具开放后两人在办公室；人物 1 已有达到合同有效工时的计时任务乙。"""
    _stage_shared_work(engine)
    hourly = engine.rules.task("hourly-2026-01-05")
    staged = _completed_work_state(engine, hourly, 3600, hourly.required_effective_seconds)
    done = next(item for item in staged.activities.values() if item.target_id == hourly.task_id)
    staged.activities[done.activity_id] = done.model_copy(
        update={"resource_claims": set(default_affordances().resource_claims("work"))})
    now = staged.current_time
    staged.pending_events = [event for event in staged.pending_events if event.due_at > now]
    staged.agents[2] = staged.agents[2].model_copy(
        update={"next_decision_at": now, "body_settled_at": now}, deep=True)
    staged.version += 1
    engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=now, visibility="system",
        source_kind="scenario", data={"kind": "test_second_task_ready"})])
    engine.state = staged


def _working_on_first_task(engine) -> str:
    """人物 1 经真实 start_activity 做任务甲、人物 2 等待，推进到人物 1 做甲期间的决策机会。"""
    first = engine.scenario.tasks[0]

    def start(observed):
        binding = next(row for row in observed["interaction_bindings"]
                       if row["kind"] == "work_task" and row["object_id"] == first.task_id)
        return start_activity("工作", binding["object"])

    _shared_round(engine, "first-task-start", {1: ("start_activity", start), 2: _WAIT})
    started = {result.agent_id: result for result in engine.commit_decision_round("first-task-start")}
    assert started[1].status == "in_progress"
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine)
    assert 1 in engine.scheduling_state().ready_agent_ids
    assert engine.state.activities[started[1].activity_id].status == "active"
    return started[1].activity_id


def _submit_action(task) -> tuple[str, SubmitWorkActionRequest]:
    """用本轮可见任务名与公开交付 Schema 构造提交动作。"""
    return "submit_work", SubmitWorkActionRequest(task_name=f"工作：{task.title}",
                                                  deliverable=_submission(task).deliverable)


@pytest.mark.asyncio
async def test_submitting_ready_task_while_working_on_another_reviews_and_pays(engine_factory) -> None:
    """做甲任务时提交已达标的乙任务：评审照常、结算付款、甲继续（时间与能量规格「仍须修复」第 4 条）。"""
    engine = engine_factory("multi-person.json")
    _stage_second_task_ready(engine)
    first, hourly = engine.scenario.tasks[0], engine.rules.task("hourly-2026-01-05")
    work_id = _working_on_first_task(engine)
    reviewer = AcceptingReviewer()
    engine._work_reviewer = reviewer
    _shared_round(engine, "second-task-submit", {1: _submit_action(hourly), 2: _WAIT})
    await engine.prepare_pending_work_review("second-task-submit-1")
    assert reviewer.calls == 1
    before = dict(engine.state.accounts)
    balance = engine.state.agents[1].balance_account_id
    results = {result.agent_id: result for result in engine.commit_decision_round("second-task-submit")}
    assert results[1].status == "completed" and results[1].data["payment_fen"] == 10_000
    assert engine.state.accounts[balance] == before[balance] + 10_000
    assert engine.state.accounts[hourly.employer_account_id] == before[hourly.employer_account_id] - 10_000
    assert hourly.task_id in engine.state.paid_task_ids
    working = active_activities(engine.state, 1)
    assert [item.activity_id for item in working] == [work_id]
    assert working[0].kind == "work" and working[0].target_id == first.task_id


@pytest.mark.asyncio
async def test_submitting_task_in_progress_is_rejected_with_work_task_in_progress(engine_factory) -> None:
    """提交本人正在做的任务是业务拒绝 WORK_TASK_IN_PROGRESS，不调用评审模型，工作继续。"""
    engine = engine_factory("multi-person.json")
    _stage_shared_work(engine)
    first = engine.scenario.tasks[0]
    work_id = _working_on_first_task(engine)
    reviewer = AcceptingReviewer()
    engine._work_reviewer = reviewer
    _shared_round(engine, "busy-task-submit", {1: _submit_action(first), 2: _WAIT})
    await engine.prepare_pending_work_review("busy-task-submit-1")
    before = dict(engine.state.accounts)
    results = {result.agent_id: result for result in engine.commit_decision_round("busy-task-submit")}
    assert reviewer.calls == 0
    assert not [event for event in engine.store.read_agent_events("model_request", 1)
                if event.data.get("purpose") == "work_review"]
    assert results[1].status == "rejected" and results[1].error_code == "WORK_TASK_IN_PROGRESS"
    assert engine.state.accounts == before and first.task_id not in engine.state.paid_task_ids
    assert [item.activity_id for item in active_activities(engine.state, 1)] == [work_id]


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
    engine.observe(ctx)
    call = DirectToolCall(ctx, "bad-schema", "submit_work",
                          _public_submission(engine, _paraphrased_submission()))
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
    fixture = json.loads(Path("tests/life/fixtures/work_review_deepseek_responses.json").read_text(
        encoding="utf-8"))
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
    fixture = json.loads(Path("tests/life/fixtures/work_review_deepseek_responses.json").read_text(
        encoding="utf-8"))
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
    """从真实公开评审函数经公开结果投影（进度层 tool_call.result）写成紧凑 JSON。"""
    result = {"action_id": "reviewed-work", "occurred_at": 1767542400, "status": "completed",
              "error_code": None, "message": "Work completed.",
              "data": {"work_review": public_work_review(decision, source)}}
    scenario = SimpleNamespace(products=(), inventory_items=(), locations=(), timezone="Asia/Shanghai")
    return json.dumps(public_result(result, scenario), ensure_ascii=False)


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
    review = json.loads(rendered)["work_review"]
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
    observed = engine.observe(context(engine, "corrected-review-visible"))
    rendered = json.dumps(observed["progress_layer"], ensure_ascii=False)
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
    fixture = json.loads(Path("tests/life/fixtures/work_review_deepseek_responses.json").read_text(
        encoding="utf-8"))
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

    prepared = prepare_work_review(submission, "deepseek-flash")
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
        "requested_model": "deepseek-flash",
        "base_url": "https://api.deepseek.com",
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
        ("deepseek-chat", "https://api.deepseek.com"),
        ("deepseek-flash", "https://api.deepseek.com/beta"),
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
        "https://api.deepseek.com",
    )

    reviewer = WorkReviewer(_review_model_identity())

    assert reviewer.client is client


def test_work_reviewer_accepts_person_channel_thinking_divergence(monkeypatch) -> None:
    """运行身份记录人物通道思考参数，验收通道自身请求保持 disabled+required。"""
    _bind_review_client(monkeypatch, "deepseek-flash", "https://api.deepseek.com")

    reviewer = WorkReviewer(_review_model_identity())

    assert reviewer.client is not None


def test_work_reviewer_rejects_request_parameter_drift(monkeypatch) -> None:
    """重试参数偏离冻结合同时拒绝。"""
    _bind_review_client(monkeypatch, "deepseek-flash", "https://api.deepseek.com")

    with pytest.raises(ProtocolError, match="parameters disagree"):
        WorkReviewer(_review_model_identity(max_retries=1))
