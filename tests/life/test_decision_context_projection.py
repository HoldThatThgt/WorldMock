"""决策上下文的完整结果、场景选择、预算及递归公开边界。"""

from __future__ import annotations

import logging

import pytest

from tests.life.conftest import advance_next, context, submit_round
from worldmock.errors import BusinessRuleError
from worldmock.footprints.models import EventDraft
from worldmock.life.models import StartActivityRequest
from worldmock.life.narration import (
    ForbiddenObservationFieldError,
    PromptBudgetExceededError,
    RenderBudgetExceededError,
    compose_user_message,
    enforce_budgets,
    render_facts,
    render_scene,
)
from worldmock.life.social import Message


def _reject_unobserved_rounds(engine, count: int) -> list:
    """通过真实登记和结算积累多条结果，期间人物不读取新观察。"""
    results = []
    for index in range(count):
        request = StartActivityRequest(
            activity_type="watch", target_id=f"unavailable-{index}",
            planned_duration_seconds=60, playback_speed=1)
        _, batch = submit_round(engine, {1: ("start_activity", request)}, f"unobserved-{index}")
        results.extend(batch)
        advance_next(engine)
    return results


def test_all_new_settlement_results_survive_and_are_consumed_once(engine_factory) -> None:
    """超过五条的真实拒绝结果按事件顺序完整出现，下一次无新结果时全部为空。"""
    engine = engine_factory()
    engine.observe(context(engine, "initial-observation"))
    results = _reject_unobserved_rounds(engine, 7)
    observed = engine.observe(context(engine, "all-results"))
    public = observed["facts"]["recent_action_results"]
    assert len(public) == 7
    assert len({row["action_id"] for row in public}) == 7
    assert public == [{key: result.model_dump(mode="json")[key] for key in (
        "action_id", "occurred_at", "status", "error_code", "message")} for result in results]
    assert observed["scene"]["recent_action_results"] == [results[-1].message]
    repeated = engine.observe(context(engine, "no-new-results"))
    assert repeated["facts"]["recent_action_results"] == []
    assert repeated["scene"]["recent_action_results"] == []
    assert repeated["recent_action_results"] == []


def test_long_rejection_cause_remains_complete_through_settlement(engine_factory, monkeypatch) -> None:
    """完整底层拒绝原因经过真实结算和提示渲染后保留尾部。"""
    engine = engine_factory()
    cause = "The requested duration violates the current contract. " * 10 + "FINAL_CAUSE_MUST_REMAIN"

    def reject(_state, _pending):
        """在领域执行边界产生带长原因的业务拒绝。"""
        raise BusinessRuleError("CONTRACT_VIOLATION") from ValueError(cause)

    monkeypatch.setattr(engine, "_dispatch", reject)
    result = _reject_unobserved_rounds(engine, 1)[0]
    observed = engine.observe(context(engine, "long-cause"))
    message = observed["facts"]["recent_action_results"][0]["message"]
    assert message == result.message == f"Action rejected: CONTRACT_VIOLATION: {cause}"
    assert "FINAL_CAUSE_MUST_REMAIN" in render_facts(observed["facts"])


def test_full_results_fail_user_budget_instead_of_dropping_history(engine_factory, monkeypatch, caplog) -> None:
    """完整结果超过整体预算即失败，所有结果及每条错误尾部都保留。"""
    engine = engine_factory()
    detail = "CONSTRAINT_" + "z" * 6100 + "_END_OF_REASON"

    def reject(_state, _pending):
        """通过真实业务拒绝产生超过单次提示预算的完整结果。"""
        raise BusinessRuleError(detail)

    monkeypatch.setattr(engine, "_dispatch", reject)
    _reject_unobserved_rounds(engine, 7)
    observed = engine.observe(context(engine, "oversized-results"))
    results = observed["facts"]["recent_action_results"]
    assert len(results) == 7 and all(row["message"] == f"Action rejected: {detail}" for row in results)
    with caplog.at_level(logging.ERROR), pytest.raises(PromptBudgetExceededError, match="user message exceeds"):
        compose_user_message("人物卡", render_scene(observed["scene"]), render_facts(observed["facts"]))
    assert any(record.levelno == logging.ERROR and "User message exceeds" in record.message
               for record in caplog.records)


@pytest.mark.parametrize("older_unread", [True, False])
def test_latest_inbox_heads_use_time_and_identity_and_only_older_unread_is_flagged(
    engine_factory, older_unread: bool,
) -> None:
    """收件箱输入乱序且时间相同时，最新三条稳定有序，未读提示只看未展示集合。"""
    engine = engine_factory()
    staged = engine.state.model_copy(deep=True)
    agent = staged.agents[1]
    entries = [("old-z", -30), ("new-e", -1), ("old-b", -10), ("new-d", -1), ("new-c", -1)]
    agent.inbox = {
        key: Message(message_id=key, kind="direct", sender_id="contact:test", recipient_id="agent:1",
                     text=f"标题-{key}", created_at=staged.current_time + offset)
        for key, offset in entries}
    agent.visible_message_ids = set()
    agent.read_inbox_ids = {"old-z"} if older_unread else {"old-z", "old-b"}
    staged.version += 1
    engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=staged.current_time, visibility="system",
        data={"kind": "test_inbox"})])
    engine.state = staged
    observation = engine.observe(context(engine, "inbox-order"))
    assert [row["id"] for row in observation["facts"]["inbox"]] == [
        "old-z", "old-b", "new-c", "new-d", "new-e"]
    assert observation["scene"]["inbox_head"] == ["标题-new-c", "标题-new-d", "标题-new-e"]
    assert observation["scene"]["inbox_has_older_unread"] is older_unread
    assert ("更早还有未读消息" in render_scene(observation["scene"])) is older_unread
    assert [row["read"] for row in observation["facts"]["inbox"]] == [
        True, not older_unread, False, False, False]


@pytest.mark.parametrize("field,value", [
    ("oracle_sha256", "private-hash"), ("request_sha256", "private-request"),
    ("reference_id", "summary:private"), ("oracle", {"answer": "private"}),
    ("accounts", {"agent:1": 100}), ("balance_fen", 100), ("energy_kcal", 1800),
    ("sleepiness", 0.4), ("activity_snapshot", {"status": "active"}),
    ("activity", {"status": "active"}),
])
def test_nested_private_result_fields_fail_before_render_and_log(field, value, caplog) -> None:
    """公开字段中的再次嵌套不能绕过递归黑名单，每次违规留下英文错误日志。"""
    payload = {"recent_action_results": [{"work_review": {"required_fact_assessments": [
        {"rationale": {field: value}}]}}]}
    with caplog.at_level(logging.ERROR), pytest.raises(ForbiddenObservationFieldError):
        render_facts(payload)
    assert any(record.levelno == logging.ERROR and "rejected" in record.message for record in caplog.records)


@pytest.mark.parametrize("kind", ["user", "system", "scene", "persona"])
def test_all_render_budget_failures_emit_english_error_logs(kind, caplog) -> None:
    """四种硬预算入口均明确失败并记录英文错误。"""
    operations = {
        "user": lambda: enforce_budgets(40001, 0),
        "system": lambda: enforce_budgets(0, 3001),
        "scene": lambda: render_scene({"recent_action_results": ["事" * 801]}),
        "persona": lambda: compose_user_message("我" * 601, "", ""),
    }
    with caplog.at_level(logging.ERROR), pytest.raises((PromptBudgetExceededError, RenderBudgetExceededError)):
        operations[kind]()
    errors = [record.message for record in caplog.records if record.levelno == logging.ERROR]
    assert errors and all(not any("\u4e00" <= char <= "\u9fff" for char in message) for message in errors)
