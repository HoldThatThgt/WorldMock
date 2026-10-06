"""@brief WorkReviewCoordinator 的独立生活运行职责。"""

from __future__ import annotations

import asyncio
import logging
from worldmock.errors import BusinessRuleError, ProtocolError
from worldmock.life import activity_control, admission
from worldmock.life.action_evidence import (
    _work_review_failure_draft,
    _work_review_request_draft,
    _work_review_response_draft,
)
from worldmock.life.menu_execution import require_interaction_menu
from worldmock.life.models import PendingAction, SubmitWorkActionRequest, SubmitWorkRequest
from worldmock.life.session_actions import ActionHandlers
from worldmock.life.models import validated_payload
from worldmock.life.work import prepare_work_submission
from worldmock.life.work_review import (
    WORK_REVIEW_MAX_MODEL_CALLS,
    WorkReviewOutputError,
    WorkReviewer,
    parse_work_review,
    prepare_work_review,
    prepare_work_review_correction,
)

from worldmock.life.engine_context import EngineContext


class WorkReviewCoordinator:
    """@brief 显式依赖上下文的WorkReviewCoordinator。"""

    def __init__(self, context: EngineContext, actions: ActionHandlers):
        """@brief 绑定工作准入与状态，验收客户端只归此对象。"""
        self.context, self.actions = context, actions
        self.reviewer: WorkReviewer | None = None

    async def prepare(self, pending: PendingAction) -> None:
        """@brief 为通过前置条件的工作提交执行有限次独立语义验收。
        有待收尾活动时结算必以 ACTIVITY_CONCLUSION_REQUIRED 拒绝（与 _dispatch 同一判定），不调用验收模型。
        """
        if pending.tool_name != "submit_work" or pending.reused or pending.work_review is not None:
            return
        if activity_control.pending_conclusions(self.context.state, pending.agent_id):
            return
        start = SubmitWorkActionRequest.model_validate(pending.request)
        existing = [
            event
            for event in self.context.store.read_agent_events("model_request", pending.agent_id)
            if event.action_id == pending.action_id and event.data.get("purpose") == "work_review"
        ]
        if existing:
            raise ProtocolError("Pending work review cannot restart after model evidence was recorded")
        try:
            task_id = self.actions._task_id_for_name(pending, start.task_name)
            request = SubmitWorkRequest(task_id=task_id, deliverable=start.deliverable)
            require_interaction_menu(self.context.scenario, self.context.state, pending, [])
            admission.require_hands_free(
                self.actions._admission_context(self.context.state, pending.agent_id), "submit work"
            )
            submission = prepare_work_submission(
                self.context.state, self.context.semantics, self.context.corpus_root, pending.agent_id, request
            )
        except BusinessRuleError:
            return
        model_name = self.context.store.manifest["model_identity"]["requested_model"]
        original = prepare_work_review(submission, model_name)
        prepared = original
        previous_event_id = None
        for attempt in range(1, WORK_REVIEW_MAX_MODEL_CALLS + 1):
            call_id = f"{pending.decision_id}:work-review:{pending.action_id}:attempt:{attempt}"
            request_link = (call_id, attempt, previous_event_id)
            request_event = self.context.store.commit(
                validated_payload(self.context.state), [_work_review_request_draft(
                    self.context.state.current_time, pending, request_link, prepared.request)])[0]
            link = (call_id, request_event.event_id, attempt)
            raw = await self._call_work_reviewer(prepared, pending, link)
            (decision, error, response_event_id) = self._parse_work_reviewer(raw, original, pending, link)
            if decision is not None:
                pending.work_review = decision.model_dump(mode="json")
                pending.work_review_event_id = response_event_id
                return
            if attempt == WORK_REVIEW_MAX_MODEL_CALLS:
                raise ProtocolError("Work review response remained invalid after three protocol attempts") from error
            previous_event_id = response_event_id
            prepared = prepare_work_review_correction(original, raw, error)

    async def _call_work_reviewer(self, prepared, pending, link: tuple[str, str, int]) -> dict:
        """@brief 调用固定客户端；任意配置或传输失败均留证并终止。"""
        try:
            reviewer = self.reviewer or WorkReviewer(self.context.store.manifest["model_identity"])
            self.reviewer = reviewer
            return await reviewer.call(prepared)
        except asyncio.CancelledError as error:
            logging.error("Work review model request was cancelled")
            draft = _work_review_failure_draft(self.context.state.current_time, pending, link, error)
            self.context.store.commit(validated_payload(self.context.state), [draft])
            raise
        except Exception as error:
            logging.exception("Work review model request failed")
            draft = _work_review_failure_draft(self.context.state.current_time, pending, link, error)
            self.context.store.commit(validated_payload(self.context.state), [draft])
            raise

    def _parse_work_reviewer(self, raw, prepared, pending, link: tuple[str, str, int]):
        """@brief 保存原始响应，并区分可纠正输出与内部解析故障。"""
        error = None
        try:
            decision = parse_work_review(raw, prepared)
        except WorkReviewOutputError as caught:
            logging.warning("Work review protocol output is invalid: %s", caught)
            (decision, error) = (None, caught)
        except Exception as caught:
            logging.exception("Work review response parsing failed")
            draft = _work_review_response_draft(self.context.state.current_time, pending, link, (raw, None, caught))
            self.context.store.commit(validated_payload(self.context.state), [draft])
            raise
        draft = _work_review_response_draft(self.context.state.current_time, pending, link, (raw, decision, error))
        event = self.context.store.commit(validated_payload(self.context.state), [draft])[0]
        return (decision, error, event.event_id)
