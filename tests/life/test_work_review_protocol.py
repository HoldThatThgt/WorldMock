"""工作评审有限协议纠正、持久化顺序和故障边界测试。"""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path

import pytest
from agentsociety2.society import DirectToolCall

import worldmock.life.engine as engine_module
from tests.life.conftest import advance_next, context
from tests.life.test_export_integration import _stage_final_interval
from tests.life.test_work_review import (
    _at_task_start,
    _complete_required_work,
    _paraphrased_submission,
)
from worldmock.errors import ProtocolError
from worldmock.footprints.export import export_run
from worldmock.footprints.store import FootprintStore
from worldmock.footprints.validation import validate_export
from worldmock.life.models import StartBasicActivityRequest


class SequenceReviewer:
    """按测试声明返回响应或抛出基础设施异常。"""

    def __init__(self, *outcomes: object):
        self.outcomes = list(outcomes)
        self.calls = 0
        self.requests: list[dict] = []
        self.responses: list[dict] = []
        self.run_dir: Path | None = None
        self.persisted_before_calls: list[list[str]] = []

    async def call(self, prepared):
        """记录实际请求并返回当前序列项。"""
        if self.run_dir is not None:
            self.persisted_before_calls.append(_persisted_review_phases(self.run_dir))
        self.requests.append(copy.deepcopy(prepared.request))
        outcome = self.outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, BaseException):
            raise outcome
        raw = _review_response(prepared, self.calls, outcome == "semantic_rejection")
        _damage_response(raw, str(outcome))
        self.responses.append(copy.deepcopy(raw))
        return raw


def _review_response(prepared, call_number: int, rejected: bool = False) -> dict:
    """形成包含所有真实预期身份的完整工具响应。"""
    references = [_reference_row(identity, rejected) for identity in prepared.expected_reference_ids]
    deliveries = [_delivery_row(path, rejected) for path in prepared.expected_delivery_paths]
    arguments = {
        "reference_assessments": references,
        "delivery_assessments": deliveries,
        "accepted": not rejected,
    }
    tool_call = {
        "id": f"review-tool-{call_number}",
        "type": "function",
        "function": {"name": "submit_work_review", "arguments": json.dumps(arguments)},
    }
    return {
        "id": f"review-response-{call_number}",
        "model": "deepseek-flash",
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
        "choices": [{"message": {"tool_calls": [tool_call]}}],
    }


def _reference_row(identity: str, rejected: bool) -> dict:
    """形成一个结构完整的参考事实结论。"""
    return {
        "reference_id": identity,
        "required_fact_covered": not rejected,
        "source_supported": not rejected,
        "contradiction": rejected,
        "source_excerpt": "Hi , I'm David",
        "rationale": "Supported by the transcript.",
    }


def _delivery_row(path: str, rejected: bool) -> dict:
    """形成一个结构完整的交付字段结论。"""
    return {
        "delivery_path": path,
        "source_supported": not rejected,
        "contradiction": rejected,
        "source_excerpt": "Hi , I'm David",
        "rationale": "Supported by the transcript.",
    }


def _damage_response(raw: dict, outcome: str) -> None:
    """只对声明的协议错误修改当前响应。"""
    call = raw["choices"][0]["message"]["tool_calls"][0]
    if outcome == "missing_contradiction":
        arguments = json.loads(call["function"]["arguments"])
        arguments["reference_assessments"][2].pop("contradiction")
        call["function"]["arguments"] = json.dumps(arguments)
    elif outcome == "invalid_json":
        call["function"]["arguments"] = "{"
    elif outcome == "missing_identity":
        arguments = json.loads(call["function"]["arguments"])
        arguments["reference_assessments"].pop()
        call["function"]["arguments"] = json.dumps(arguments)
    elif outcome == "missing_tool_call":
        raw["choices"][0]["message"]["tool_calls"] = []
    elif outcome == "invalid_tool_envelope":
        call["id"] = ""


def _stage_submission(engine, reviewer: SequenceReviewer, round_id: str):
    """完成公开工作并登记尚未结算的提交。"""
    _at_task_start(engine)
    _complete_required_work(engine)
    engine._work_reviewer = reviewer
    reviewer.run_dir = engine.store.run_dir
    ctx = context(engine, round_id)
    engine.begin_decision_round([ctx])
    call = DirectToolCall(ctx, f"action-{round_id}", "submit_work", _paraphrased_submission())
    engine.submit_action(call)
    return call


def _persisted_reviews(engine) -> list:
    """通过第二个 SQLite 连接读取已经提交的工作评审证据。"""
    store = FootprintStore(engine.store.run_dir)
    try:
        return [event for event in store.read_events(store.last_event_seq)
                if event.data.get("purpose") == "work_review"]
    finally:
        store.close()


def _persisted_review_phases(run_dir: Path) -> list[str]:
    """在模型调用边界通过独立连接读取已提交阶段。"""
    store = FootprintStore(run_dir)
    try:
        return [event.data["phase"] for event in store.read_events(store.last_event_seq)
                if event.data.get("purpose") == "work_review"]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_missing_contradiction_is_corrected_after_durable_feedback(engine_factory) -> None:
    """真实漏字段先留存，再由同材料、Schema 和提示的第二次请求纠正。"""
    engine = engine_factory()
    reviewer = SequenceReviewer("missing_contradiction", "accepted")
    call = _stage_submission(engine, reviewer, "missing-field")
    before = dict(engine.state.accounts)

    await engine.prepare_pending_work_review(call.action_id)
    result = engine.commit_decision_round("missing-field")[0]
    reviews = _persisted_reviews(engine)

    assert result.status == "completed" and reviewer.calls == 2
    assert [event.event_type for event in reviews] == [
        "model_request", "model_response", "model_request", "model_response"]
    assert [event.data["attempt"] for event in reviews] == [1, 1, 2, 2]
    assert reviews[1].event_id in reviews[2].caused_by
    assert reviewer.persisted_before_calls == [["request"], ["request", "response", "request"]]
    _assert_correction_request(reviews, reviewer)
    accepted = next(event for event in engine.store.read_agent_events("action_accepted", 1)
                    if event.action_id == call.action_id)
    assert reviews[-1].event_id in accepted.caused_by
    agent_account = engine.state.agents[1].balance_account_id
    assert engine.state.accounts[agent_account] == before[agent_account] + 60_000


def _assert_correction_request(reviews: list, reviewer: SequenceReviewer) -> None:
    """核对纠正请求安全包含完整前次响应和具体字段反馈。"""
    initial, correction = reviewer.requests
    assert correction["messages"][:2] == initial["messages"]
    assert {key: value for key, value in correction.items() if key != "messages"} == {
        key: value for key, value in initial.items() if key != "messages"}
    assert [item["role"] for item in correction["messages"]] == ["system", "user", "user"]
    feedback = json.loads(correction["messages"][-1]["content"])
    assert feedback["PREVIOUS_INVALID_RESPONSE"] == reviews[1].data["response"]
    assert "reference_assessments.2.contradiction" in feedback["VALIDATION_ERROR"]
    assert correction["max_retries"] == 0


@pytest.mark.parametrize(
    "first_error",
    ["missing_tool_call", "invalid_tool_envelope", "invalid_json", "missing_identity"],
)
@pytest.mark.asyncio
async def test_recognized_output_errors_are_corrected_once(engine_factory, first_error: str) -> None:
    """工具 envelope、JSON 和评审身份错误都只触发一次模型输出纠正。"""
    engine = engine_factory()
    reviewer = SequenceReviewer(first_error, "accepted")
    call = _stage_submission(engine, reviewer, f"correct-{first_error}")

    await engine.prepare_pending_work_review(call.action_id)
    result = engine.commit_decision_round(f"correct-{first_error}")[0]

    assert result.status == "completed" and reviewer.calls == 2
    assert len(_persisted_reviews(engine)) == 4


@pytest.mark.asyncio
async def test_valid_semantic_rejection_returns_after_one_call(engine_factory) -> None:
    """结构有效的语义拒绝不通过重复评审争取接受。"""
    engine = engine_factory()
    reviewer = SequenceReviewer("semantic_rejection", "accepted")
    call = _stage_submission(engine, reviewer, "semantic-rejection")
    before = dict(engine.state.accounts)

    await engine.prepare_pending_work_review(call.action_id)
    result = engine.commit_decision_round("semantic-rejection")[0]

    assert result.status == "rejected" and result.error_code == "WORK_REQUIRES_REWORK"
    assert reviewer.calls == 1 and len(_persisted_reviews(engine)) == 2
    assert engine.state.accounts == before and not engine.state.paid_task_ids


@pytest.mark.asyncio
async def test_three_invalid_outputs_exhaust_without_life_settlement(engine_factory) -> None:
    """三次结构错误全部留存后终止，余额、任务和生活状态不结算。"""
    engine = engine_factory()
    reviewer = SequenceReviewer(*(["missing_contradiction"] * 6))
    call = _stage_submission(engine, reviewer, "exhausted")
    before = engine.state.model_dump(mode="json")

    with pytest.raises(ProtocolError, match="remained invalid after three"):
        await engine.prepare_pending_work_review(call.action_id)

    reviews = _persisted_reviews(engine)
    assert reviewer.calls == 3 and [event.data["attempt"] for event in reviews] == [1, 1, 2, 2, 3, 3]
    assert len({event.data["call_id"] for event in reviews}) == 3
    assert all(reviews[index].event_id in reviews[index + 1].caused_by for index in (1, 3))
    assert engine.state.model_dump(mode="json") == before
    with pytest.raises(ProtocolError, match="cannot restart"):
        await engine.prepare_pending_work_review(call.action_id)
    assert reviewer.calls == 3


@pytest.mark.parametrize("error_type", [TimeoutError, OSError, RuntimeError, asyncio.CancelledError])
@pytest.mark.asyncio
async def test_infrastructure_errors_are_fatal_without_correction(engine_factory, error_type) -> None:
    """传输、超时、取消和客户端内部故障都只记录一次请求终态。"""
    engine = engine_factory()
    reviewer = SequenceReviewer(error_type("infrastructure failed"), "accepted")
    call = _stage_submission(engine, reviewer, f"fatal-{error_type.__name__}")
    before = engine.state.model_dump(mode="json")

    with pytest.raises(error_type, match="infrastructure failed"):
        await engine.prepare_pending_work_review(call.action_id)

    reviews = _persisted_reviews(engine)
    assert reviewer.calls == 1 and [event.data["phase"] for event in reviews] == ["request", "failure"]
    assert reviews[1].caused_by == [reviews[0].event_id]
    assert engine.state.model_dump(mode="json") == before


@pytest.mark.asyncio
async def test_internal_parser_error_persists_response_without_correction(engine_factory, monkeypatch) -> None:
    """内部解析代码异常保留原始响应并立即传播。"""
    engine = engine_factory()
    reviewer = SequenceReviewer("accepted", "accepted")
    call = _stage_submission(engine, reviewer, "parser-failure")

    def fail_parse(raw, prepared):
        raise RuntimeError("parser implementation failed")

    monkeypatch.setattr(engine_module, "parse_work_review", fail_parse)
    with pytest.raises(RuntimeError, match="parser implementation failed"):
        await engine.prepare_pending_work_review(call.action_id)

    reviews = _persisted_reviews(engine)
    assert reviewer.calls == 1 and [event.data["phase"] for event in reviews] == ["request", "response"]
    assert reviews[1].data["response"] == reviewer.responses[0]
    assert "parser implementation failed" in reviews[1].data["error"]


@pytest.mark.asyncio
async def test_response_persistence_failure_stops_before_correction(engine_factory, monkeypatch) -> None:
    """原始错误响应写盘失败时不发送第二次模型请求。"""
    engine = engine_factory()
    reviewer = SequenceReviewer("missing_contradiction", "accepted")
    call = _stage_submission(engine, reviewer, "persistence-failure")
    original_commit = engine.store.commit

    def fail_response(state, drafts):
        if drafts and drafts[0].event_type == "model_response":
            raise OSError("response persistence failed")
        return original_commit(state, drafts)

    monkeypatch.setattr(engine.store, "commit", fail_response)
    with pytest.raises(OSError, match="response persistence failed"):
        await engine.prepare_pending_work_review(call.action_id)

    assert reviewer.calls == 1
    assert [event.data["phase"] for event in _persisted_reviews(engine)] == ["request"]


def _finish_and_export(engine, destination: Path) -> tuple[dict, list[dict], dict]:
    """将当前真实引擎推进至终点并返回认证导出及质量报告。"""
    _stage_final_interval(engine, 60)
    ctx = context(engine, "review-export-end")
    engine.begin_decision_round([ctx])
    engine.observe(ctx)
    request = StartBasicActivityRequest(activity_type="wait", planned_duration_seconds=60)
    engine.submit_action(DirectToolCall(ctx, "review-export-wait", "start_basic_activity", request))
    engine.commit_decision_round("review-export-end")
    advance_next(engine)
    reference = engine.store.assets.put_json({"agent_id": 1}, {"kind": "review_protocol_export"})
    checkpoint = engine.checkpoint({"agent:1": reference})
    export_run(engine.store, checkpoint.last_event_seq, destination)
    manifest, events, _ = validate_export(destination)
    quality = json.loads((destination / "quality.json").read_text())
    return manifest, events, quality


@pytest.mark.asyncio
async def test_corrected_review_is_accepted_by_complete_export_quality(engine_factory, tmp_path: Path) -> None:
    """早期协议错误是可审计响应，不会把最终完整运行误判为失败。"""
    engine = engine_factory()
    reviewer = SequenceReviewer("missing_contradiction", "accepted")
    call = _stage_submission(engine, reviewer, "exported-correction")
    await engine.prepare_pending_work_review(call.action_id)
    result = engine.commit_decision_round("exported-correction")[0]

    manifest, events, quality = _finish_and_export(engine, tmp_path / "review-export")
    usage = quality["model_usage"]

    assert result.status == "completed" and manifest["experiment_completed"] is True
    assert not any(event["event_type"] == "run_failed" for event in events)
    assert usage["request_events"] == usage["unique_request_call_ids"] == 2
    assert usage["terminal_call_ids"] == usage["provider_responses"] == 2
    assert usage["protocol_error_responses"] == 1
    assert usage["pending_call_ids"] == usage["orphan_terminal_call_ids"] == []
    assert usage["duplicate_terminal_call_ids"] == []
    assert usage["token_usage"]["complete_tokens"] == {
        "input_tokens": 20, "output_tokens": 20, "total_tokens": 40}
