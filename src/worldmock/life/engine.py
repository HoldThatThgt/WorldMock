"""@brief 统一执行生活决策轮、时间推进、事件和共同检查点。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from worldmock.categories import Action
from worldmock.catalog.affordances import AffordanceView
from worldmock.corpora import load_manifest
from worldmock.errors import BusinessRuleError, IntegrityError, PreconditionError, ProtocolError
from worldmock.footprints.models import (DECISION_OFFERED_FIELD, RESOURCE_FLOWS_FIELD,
                                        AssetRef, EventDraft, validate_resource_flows)
from worldmock.footprints.store import FootprintStore
from worldmock.life import felt, projection, same_moment, social_actions
from worldmock.life.action_evidence import (
    _activity_terminal_draft, _activity_terminal_result, _origin_terminal_draft,
    _origin_terminal_result,
    _pending_causes, _remember_action, _remember_terminal_activity, _result_draft,
    _result_event_data, _terminal_action_records, _terminal_after_action,
    _work_review_failure_draft, _work_review_request_draft,
    _work_review_response_draft,
)
from worldmock.life.memory_evidence import _memory_evidence_data
from worldmock.life.menu_execution import require_interaction_menu
from worldmock.life.interaction_menu import canonical_action
from worldmock.life.activity_evidence import (dedupe_revealed_cues as _dedupe_revealed_cues,
                                              meal_consumption_data as _meal_consumption_data,
                                              record_material_reveal as _record_material_reveal)
from worldmock.life.activity_validation import validate_activity_energy, validate_activity_targets
from worldmock.life.decision_start import (defer_decision_starts, flush_decision_starts,
                                            maybe_decision_statement_start, pop_decision_start)
from worldmock.life.activities import (
    ActivityAdvance,
    ActivityOrigin,
    activity_factors,
    advance_activity_shared,
    change_activity,
    resource_claims,
    schedule_feedback,
    start_activity,
    truncate_activity,
)
from worldmock.life.basket import (
    apply_meal_progress,
    change_inventory,
    meal_duration,
    meal_food_rate,
    validate_meal_inventory, validate_meal_use,
)
from worldmock.life.body import (
    ENERGY_RECOVERY_ACTIVITY_KINDS,
    ActivityFactors,
    decay_mood,
    apply_discomfort_pulse,
    apply_mood_pulse,
    apply_social_ledger,
    initial_body,
    interpolate,
    last_flows,
    maximum_energy_duration_seconds,
    minimum_energy_cost,
    next_body_boundary_seconds,
    next_shared_body_boundary_seconds,
    settle_shared_body,
    shared_energy_cost,
)
from worldmock.life.content import (
    LifestyleItem,
    SubtitleCue,
    load_lifestyle_items,
    mail_headers,
    reveal_mail,
    reveal_watch,
)
from worldmock.life.category_contract import match_rows, require_item_rules
from worldmock.life.game import validate_game
from worldmock.life.models import (
    ActionReceipt,
    ActionResult,
    ActionStatus,
    ActivityChange,
    ActivityKind,
    ActivityStartParams,
    ActivityStatus,
    ChangeActivityRequest,
    ContentProgress,
    ConsumeRequest,
    CounterfactualIntervention,
    CounterfactualReceipt,
    DecisionRoundState,
    MeetingMinutes,
    PendingAction,
    PlanDayRequest,
    REQUEST_MODELS,
    rejection_reason_text,
    ReviseDayPlanRequest,
    RunIdentity,
    SameMomentPresentation,
    SameMomentStage,
    SendMessageRequest,
    SessionStatus,
    SubmitWorkActionRequest,
    StartSocialActivityRequest,
    StoredAction,
    SubmitWorkRequest,
    WorldState,
    WORLD_SCHEMA_VERSION,
    active_activities,
)
from worldmock.life.inventory_recipe import (RecipeContext, apply_inventory_recipe,
                                             prepare_inventory_recipe)
from worldmock.life.observe import ObservationMixin
from worldmock.life.rhythm import (
    RhythmBoundary,
    apply_engine_rhythm,
    engine_rhythm_inputs,
    next_decision_candidates,
)
from worldmock.life.run_identity import category_identity, open_category_validated_store, require_category_identity
from worldmock.life.rules import LifeRuleEngine
from worldmock.life.scenario import RuntimeScenario
from worldmock.life.schedule import (PlanGoal, WakeReason,
                                     day_end_seconds, day_plan_expired, expired_plan_agent_ids,
                                     initial_day_plan, revise_day_plan)
from worldmock.life.semantics import ScenarioSemantics, load_runtime_scenario
from worldmock.life.session_actions import SessionActionMixin
from worldmock.life.settlement import inject_initial_funds
from worldmock.life.state_commit import (commit_state, flush_presentation, flush_supply_visibility, stage_presentation,
                                         validated_payload)
from worldmock.life.storefront_runtime import StorefrontRuntimeMixin, record_arrival_access
from worldmock.life.storefront_state import initial_supply_state, map_provenance, validate_storefront_state
from worldmock.life.travel import pay_travel_fare, route_for, update_travel, validate_routes
from worldmock.life.work import (
    load_work_input,
    prepare_work_submission,
    reveal_work_progress,
    settle_work,
    validate_work_duration,
    visible_work_material,
    work_totals,
)
from worldmock.life.work_review import (
    WORK_REVIEW_MAX_MODEL_CALLS,
    WorkReviewDecision,
    WorkReviewOutputError,
    WorkReviewer,
    parse_work_review,
    prepare_work_review,
    prepare_work_review_correction,
    public_work_review,
    review_matches,
)
from worldmock.opslog import OpsLogger
from worldmock.scheduling.compile import compile_schedule
from worldmock.scheduling.effects import apply_scheduled_effect

if TYPE_CHECKING:
    from agentsociety2.society import DecisionContext, DirectToolCall, SchedulingState
    from worldmock.footprints.models import Checkpoint

#: 会话动作的 action 取值到操作日志组件的映射，未列出的动作归属 engine。
_OPS_COMPONENTS = {"purchase": "commerce", "travel_to": "travel", "submit_work": "review"}

#: 观察合同条目的六键闭集（LifeAgent 侧严格校验同一形状）。
AFFORDANCE_CONTRACT_KEYS = frozenset({
    "action", "target_discovery", "target_field", "energy_projection",
    "feedback_eligible", "game_boundary"})

#: 预排事件种类 → 决策节律唤醒旗标（到达类事件在处理 tick 标记对应人物）。
_SCHEDULED_WAKE_FLAGS = {
    "delivery_arrival": "delivery_arrival",
    "delivery_ready": "delivery_arrival",
    "mail_arrival": "message_arrival",
    "message_arrival": "message_arrival",
    "work_available": "task_available",
}


def affordance_contract(view: AffordanceView) -> dict[str, dict]:
    """@brief 从可供性派生视图构造观察合同冻结块（docs/design/02-exchange-engine.md「可供性与活动声明」）。

    合同随场景组装后不变，键集见 AFFORDANCE_CONTRACT_KEYS；游戏继续交互走 game_boundary。
    """
    feedback = view.feedback_kinds
    game = view.game_boundary_kinds
    contract: dict[str, dict] = {}
    for name, spec in view.kinds.items():
        contract[name] = {
            "action": spec.action,
            "target_discovery": spec.target_discovery,
            "target_field": ([spec.target_field, spec.target_id_key]
                             if spec.target_discovery == "catalog_field" else None),
            "energy_projection": spec.energy_projection,
            "feedback_eligible": name in feedback,
            "game_boundary": name in game,
        }
    return contract


class LifeEngine(ObservationMixin, StorefrontRuntimeMixin, SessionActionMixin):
    """@brief LifeEnv 的单写入领域服务和状态权威（观察投影见 life.observe Mixin）。"""

    def __init__(self, semantics: ScenarioSemantics, scenario_sha256: str, corpus_root: Path,
                 store: FootprintStore):
        """@brief 绑定经组装准入的场景语义、内容与持久化运行。"""
        self.semantics = semantics
        self.scenario = semantics.scenario
        self.scenario_sha256 = scenario_sha256
        self.corpus_root = Path(corpus_root).resolve()
        self.store = store
        self.rules = LifeRuleEngine(self.scenario, self.corpus_root)
        self._affordance_contract = affordance_contract(semantics.affordances)
        self.lifestyle_items = load_lifestyle_items(
            self.corpus_root, self.scenario.body.activity_met)
        self._lifestyle_names = {item.activity_id: item.name for item in self.lifestyle_items}
        self.state = WorldState.model_validate(store.state)
        self._round: DecisionRoundState | None = None
        self._pending_decision_starts: dict[int, dict] = {}
        self._model_request_events: dict[tuple[str, str], str] = {}
        self._pending_event_drafts: list[EventDraft] = []
        self._pending_presentation: dict[int, SameMomentPresentation] = {}
        self._pending_visible_supplies: dict[int, set[str]] = {}
        self._work_reviewer: WorkReviewer | None = None
        self.ops = OpsLogger(store.run_dir, self.scenario.timezone)
        self._validate_runtime()

    @classmethod
    def create(cls, scenario_path: Path, run_dir: Path, run_identity: RunIdentity,
               two_stage_identity: dict | None = None, agent_taste_weights: dict | None = None) -> "LifeEngine":
        """@brief 从严格场景新建唯一运行，拒绝覆盖已有工件；口味权重按注入值入态。"""
        semantics, digest = load_runtime_scenario(scenario_path)
        scenario = semantics.scenario
        corpus_root = (Path(scenario_path).resolve().parent / scenario.corpus_root).resolve()
        load_manifest(corpus_root, scenario.corpus_manifest_sha256)
        _validate_run_identity(scenario, run_identity)
        state = _initial_state(semantics, digest, run_identity.run_id, agent_taste_weights or {})
        manifest = _run_manifest(semantics, digest, run_identity, two_stage_identity)
        store = FootprintStore.create(Path(run_dir), manifest, validated_payload(state))
        changes = {f"account:{key}": value for key, value in state.accounts.items()}
        changes["account:external:scenario"] = -sum(state.accounts.values())
        injection = EventDraft(event_type="environment_event", occurred_at=state.current_time,
                               visibility="evaluation", source_kind="scenario",
                               data={"kind": "initial_funds_injected", "resource_changes": changes})
        commit_state(store, state, [injection])
        return cls(semantics, digest, corpus_root, store)

    @classmethod
    def open(cls, scenario_path: Path, run_dir: Path) -> "LifeEngine":
        """@brief 只打开已有运行并校验场景和内容身份。"""
        semantics, digest = load_runtime_scenario(scenario_path)
        corpus_root = (Path(scenario_path).resolve().parent
                       / semantics.scenario.corpus_root).resolve()
        load_manifest(corpus_root, semantics.scenario.corpus_manifest_sha256)
        return cls(semantics, digest, corpus_root,
                   open_category_validated_store(Path(run_dir), semantics))

    def observe(self, context: "DecisionContext") -> dict:
        """@brief 返回人物当下可见事实，并以同版本追加观察事件。"""
        self._validate_context(context)
        data = self._observation_data(self.state.agents[context.agent_id])
        draft = EventDraft(event_type="observation", occurred_at=self.state.current_time,
                           agent_id=context.agent_id, decision_id=context.decision_id,
                           observed_at=self.state.current_time, data=data)
        data["observation_event_id"] = commit_state(self.store, self.state, [draft])[0].event_id
        return data

    def begin_decision_round(self, contexts: list["DecisionContext"]) -> None:
        """@brief 持久化观察顺序；decision_started 延迟到决策自述可得时写（docs/dev/SPEC-life-realism.md「两阶段决策」）。"""
        if self._round is not None or not contexts:
            raise ProtocolError("Decision round cannot be started")
        for context in contexts:
            self._validate_context(context)
        round_ids = {context.round_id for context in contexts}
        agent_ids = [context.agent_id for context in contexts]
        if len(round_ids) != 1 or len(agent_ids) != len(set(agent_ids)):
            raise ProtocolError("Decision round contexts disagree or repeat an agent")
        ready = list(self._ready_agent_ids())
        if agent_ids != ready:
            raise ProtocolError("Decision round contexts must match the scheduled agent order")
        round_id = contexts[0].round_id
        self._round = DecisionRoundState(round_id=round_id, expected_agent_ids=agent_ids,
                                         observed_version=self.state.version)
        self._pending_decision_starts = defer_decision_starts(contexts, round_id, self.state)
        self.ops.log(self.state.current_time, "engine", "info", "DECISION_ROUND_BEGAN",
                     f"Decision round opened for {len(agent_ids)} agents", round_id=round_id)

    def submit_action(self, call: "DirectToolCall") -> ActionReceipt:
        """@brief 严格登记宿主绑定的有类型工具调用，尚不改变生活状态。"""
        self._validate_call(call)
        request_sha = _request_sha(call.tool_name, call.request)
        existing = self._existing_action(call.action_id, call.tool_name, request_sha)
        if existing is not None:
            receipt = ActionReceipt.model_validate(existing.receipt)
            if existing.result is None:
                return receipt
            self._queue_action(call, request_sha, reused=True)
            return receipt
        return self._queue_action(call, request_sha, reused=False)

    async def prepare_pending_work_review(self, action_id: str) -> None:
        """@brief 为通过前置条件的工作提交执行有限次独立语义验收。"""
        pending = next((item for item in self._round.pending_actions if item.action_id == action_id), None)
        if pending is None:
            raise ProtocolError("Pending work review action does not exist")
        if pending.tool_name != "submit_work" or pending.reused or pending.work_review is not None:
            return
        start = SubmitWorkActionRequest.model_validate(pending.request)
        existing = [event for event in self.store.read_agent_events("model_request", pending.agent_id)
                    if event.action_id == pending.action_id and event.data.get("purpose") == "work_review"]
        if existing:
            raise ProtocolError("Pending work review cannot restart after model evidence was recorded")
        try:
            task_id = self._task_id_for_name(pending, start.task_name)
            request = SubmitWorkRequest(task_id=task_id, deliverable=start.deliverable)
            require_interaction_menu(self.scenario, self.state, pending, start)
            submission = prepare_work_submission(
                self.state, self.semantics, self.corpus_root, pending.agent_id, request)
        except BusinessRuleError:
            return
        model_name = self.store.manifest["model_identity"]["requested_model"]
        original = prepare_work_review(submission, model_name)
        prepared = original
        previous_event_id = None
        for attempt in range(1, WORK_REVIEW_MAX_MODEL_CALLS + 1):
            call_id = f"{pending.decision_id}:work-review:{pending.action_id}:attempt:{attempt}"
            request_link = (call_id, attempt, previous_event_id)
            request_event = commit_state(
                self.store, self.state,
                [_work_review_request_draft(self.state.current_time, pending, request_link, prepared.request)],
            )[0]
            link = (call_id, request_event.event_id, attempt)
            raw = await self._call_work_reviewer(prepared, pending, link)
            decision, error, response_event_id = self._parse_work_reviewer(raw, original, pending, link)
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
            reviewer = self._work_reviewer or WorkReviewer(self.store.manifest["model_identity"])
            self._work_reviewer = reviewer
            return await reviewer.call(prepared)
        except asyncio.CancelledError as error:
            logging.error("Work review model request was cancelled")
            draft = _work_review_failure_draft(
                self.state.current_time, pending, link, error)
            commit_state(self.store, self.state, [draft])
            raise
        except Exception as error:
            logging.exception("Work review model request failed")
            draft = _work_review_failure_draft(
                self.state.current_time, pending, link, error)
            commit_state(self.store, self.state, [draft])
            raise

    def _parse_work_reviewer(self, raw, prepared, pending, link: tuple[str, str, int]):
        """@brief 保存原始响应，并区分可纠正输出与内部解析故障。"""
        error = None
        try:
            decision = parse_work_review(raw, prepared)
        except WorkReviewOutputError as caught:
            logging.warning("Work review protocol output is invalid: %s", caught)
            decision, error = None, caught
        except Exception as caught:
            logging.exception("Work review response parsing failed")
            draft = _work_review_response_draft(
                self.state.current_time, pending, link, (raw, None, caught))
            commit_state(self.store, self.state, [draft])
            raise
        draft = _work_review_response_draft(
            self.state.current_time, pending, link, (raw, decision, error))
        event = commit_state(self.store, self.state, [draft])[0]
        return decision, error, event.event_id

    def _queue_action(self, call: "DirectToolCall", request_sha: str, reused: bool) -> ActionReceipt:
        """@brief 追加请求证据并加入当前轮，重放动作仍不再次执行。"""
        receipt = ActionReceipt(action_id=call.action_id, round_id=call.context.round_id,
                                decision_id=call.context.decision_id, request_sha256=request_sha)
        start = pop_decision_start(self._pending_decision_starts, call.context.agent_id, self.state.current_time)
        draft = EventDraft(event_type="action_requested", occurred_at=self.state.current_time,
                           agent_id=call.context.agent_id, decision_id=call.context.decision_id,
                           action_id=call.action_id, source_kind="model",
                           data={"tool_name": call.tool_name, "request": call.request.model_dump(mode="json"),
                                 "request_sha256": request_sha, "reused": reused})
        event = commit_state(self.store, self.state, [
            item for item in (start, draft) if item is not None])[-1]
        pending = PendingAction(run_id=call.context.run_id, round_id=call.context.round_id,
                                decision_id=call.context.decision_id, action_id=call.action_id,
                                agent_id=call.context.agent_id, observed_at=self.state.current_time,
                                world_state_version=self.state.version,
                                order=self._round.expected_agent_ids.index(call.context.agent_id),
                                tool_name=call.tool_name, request=call.request.model_dump(mode="json"),
                                request_sha256=request_sha, requested_event_id=event.event_id, reused=reused)
        self._round.pending_actions.append(pending)
        return receipt

    def commit_decision_round(self, round_id: str) -> list[ActionResult]:
        """@brief 按冻结顺序结算完整决策轮并一次提交所有状态与结果。

        人物消息与交谈邀请的收件人在本轮全部动作结算之后合并同秒机会（docs/dev/SPEC-time-and-energy.md
        第一章「人物之间的消息」），不被收件人本轮自己的受理排期覆盖。
        """
        current = self._require_round(round_id)
        submitted = {item.agent_id for item in current.pending_actions}
        if submitted != set(current.expected_agent_ids):
            raise ProtocolError("Decision round does not contain every expected agent")
        staged = stage_presentation(self.state, self._pending_presentation).model_copy(deep=True)
        staged = flush_supply_visibility(staged, self._pending_visible_supplies)
        drafts: list[EventDraft] = flush_decision_starts(self._pending_decision_starts, self.state.current_time)
        results = []
        for pending in sorted(current.pending_actions, key=lambda item: item.order):
            staged, result, action_drafts = self._resolve(staged, pending)
            self._ops_action(pending, result)
            results.append(result)
            drafts.extend(action_drafts)
            if not pending.reused:
                staged = _remember_action(staged, pending, result)
        recipients = {draft.agent_id for draft in drafts if draft.event_type == "environment_event"
                      and draft.data.get("kind") == "message_arrival" and draft.agent_id is not None}
        staged = same_moment.merge_message_readiness(staged, recipients, self.scenario.max_same_moment_decisions)
        staged = self._snapshot_decision_references(staged, list(current.expected_agent_ids))
        staged.version = self.state.version + 1
        events = commit_state(self.store, staged, drafts)
        self.state = staged
        self._pending_presentation = {}
        self._pending_visible_supplies = {}
        self._round = None
        self._ops_committed(round_id, results, events)
        return results

    def _ops_committed(self, round_id: str, results: list[ActionResult], events: list) -> None:
        """@brief 写决策轮结算操作日志，事件区间用于与足迹库互相跳转。"""
        self.ops.log(self.state.current_time, "engine", "info", "DECISION_ROUND_COMMITTED",
                     f"Decision round committed {len(results)} actions", round_id=round_id,
                     event_seq_first=events[0].event_seq if events else None,
                     event_seq_last=events[-1].event_seq if events else None)

    def _ops_action(self, pending: PendingAction, result: ActionResult) -> None:
        """@brief 写动作受理或拒绝与资金变动操作日志；重放动作不重复记资金。"""
        actions = pending.request.get("actions", []) if pending.tool_name == "start_activity" else []
        components = {_OPS_COMPONENTS.get(canonical_action(action), "engine")
                      for action in actions if isinstance(action, str)}
        component = next(iter(components)) if len(components) == 1 else _OPS_COMPONENTS.get(
            pending.tool_name, "engine")
        accepted = result.status in {ActionStatus.ACCEPTED, ActionStatus.IN_PROGRESS,
                                     ActionStatus.COMPLETED}
        keys = {"agent_id": pending.agent_id, "decision_id": pending.decision_id,
                "round_id": pending.round_id, "action_id": pending.action_id,
                "tool_name": pending.tool_name, "error_code": result.error_code,
                "reused": pending.reused}
        self.ops.log(result.occurred_at, component, "info" if accepted else "warn",
                     "ACTION_ACCEPTED" if accepted else "ACTION_REJECTED",
                     f"Action {pending.tool_name} resulted in {result.status.value}", **keys)
        changes = result.data.get("resource_changes")
        if changes and not pending.reused:
            self.ops.log(result.occurred_at, component, "info", "FUNDS_CHANGED",
                         f"Action {pending.tool_name} moved account funds",
                         account_changes=changes, **keys)

    def abort_decision_round(self, round_id: str, error: Exception | str) -> None:
        """@brief 保存失败结果和幂等身份，不执行本轮生活动作。"""
        current = self._require_round(round_id)
        staged = self.state.model_copy(deep=True)
        drafts = flush_decision_starts(self._pending_decision_starts, self.state.current_time)
        for pending in current.pending_actions:
            result = ActionResult(action_id=pending.action_id, agent_id=pending.agent_id,
                                  status=ActionStatus.FAILED, occurred_at=self.state.current_time,
                                  error_code="DECISION_ROUND_ABORTED", message=str(error))
            if not pending.reused:
                staged = _remember_action(staged, pending, result)
            drafts.append(_result_draft(pending, result, "action_result"))
        staged.version = self.state.version + 1
        drafts.append(EventDraft(event_type="environment_event", occurred_at=self.state.current_time,
                                 visibility="system", data={"kind": "decision_round_aborted",
                                                             "round_id": round_id, "error": str(error)}))
        drafts.append(_failure_draft(self.state.current_time, error, round_id))
        commit_state(self.store, staged, drafts)
        self.state = staged
        self._round = None
        self.ops.log(self.state.current_time, "engine", "error", "DECISION_ROUND_ABORTED",
                     f"Decision round aborted after {len(current.pending_actions)} actions: {error}",
                     round_id=round_id)

    def scheduling_state(self) -> "SchedulingState":
        """@brief 返回 AS2 使用的带时区当前时刻、就绪人物和下一边界。"""
        from agentsociety2.society import SchedulingState

        current = _datetime(self.state.current_time, self.scenario.timezone)
        if self.state.current_time >= self.state.end_time:
            return SchedulingState(current, (), None, self.state.version)
        ready = self._ready_agent_ids()
        next_at = _datetime(self._next_epoch(), self.scenario.timezone)
        return SchedulingState(current, ready, next_at, self.state.version)

    def _ready_agent_ids(self) -> tuple[int, ...]:
        """@brief 以冻结随机状态为同秒竞争人物生成可恢复顺序。"""
        ready = sorted(agent.agent_id for agent in self.state.agents.values() if self._ready(agent.agent_id))
        material = f"{self.state.random_state}:{self.state.current_time}:{self.state.version}".encode()
        seed = int.from_bytes(hashlib.sha256(material).digest()[:8], "big")
        random.Random(seed).shuffle(ready)
        return tuple(ready)

    def record_model_exchange(self, exchange: Any) -> None:
        """@brief 保存实际模型请求、响应或失败，并保持生活状态不变。"""
        context = exchange.context
        self._validate_context(context)
        if (exchange.phase not in {"request", "response", "failure"} or exchange.attempt < 1
                or not isinstance(exchange.call_id, str) or not exchange.call_id):
            raise ProtocolError("Model exchange phase or attempt is invalid")
        key = (context.decision_id, exchange.call_id)
        cause = []
        if exchange.phase == "request" and key in self._model_request_events:
            raise ProtocolError("Model request call was recorded twice")
        if exchange.phase != "request":
            if key not in self._model_request_events:
                raise ProtocolError("Model response has no recorded request")
            cause = [self._model_request_events[key]]
        data = {"phase": exchange.phase, "call_id": exchange.call_id, "attempt": exchange.attempt,
                "request": exchange.request,
                "response": exchange.response, "error": exchange.error}
        draft = EventDraft(event_type="model_request" if exchange.phase == "request" else "model_response",
                           occurred_at=self.state.current_time, agent_id=context.agent_id,
                           decision_id=context.decision_id, caused_by=cause, visibility="system",
                           source_kind="model", data=data)
        event = commit_state(self.store, self.state, [draft])[0]
        if exchange.phase == "request":
            self._model_request_events[key] = event.event_id
        else:
            self._model_request_events.pop(key)
        self._ops_exchange(context, exchange)
        start = maybe_decision_statement_start(self._pending_decision_starts, context.agent_id,
                                      self.state.current_time, exchange)
        if start is not None:
            commit_state(self.store, self.state, [start])

    def _ops_exchange(self, context: "DecisionContext", exchange: Any) -> None:
        """@brief 写模型交换操作日志，带响应中可用的模型名与 token 用量。"""
        response = exchange.response if isinstance(exchange.response, dict) else {}
        usage = response.get("usage") or {}
        self.ops.log(self.state.current_time, "agent_runtime",
                     "error" if exchange.phase == "failure" else "info", "MODEL_EXCHANGE",
                     f"Model {exchange.phase} recorded for call {exchange.call_id}",
                     agent_id=context.agent_id, decision_id=context.decision_id,
                     model=response.get("model"), tokens_in=usage.get("prompt_tokens"),
                     tokens_out=usage.get("completion_tokens"))

    def record_memory_update(self, evidence: Any) -> None:
        """@brief 保存人物已落盘记忆的枚举操作和内容身份。"""
        self._validate_context(evidence.context)
        try:
            data = _memory_evidence_data(evidence)
            observations = self.store.read_agent_events("observation", evidence.context.agent_id)
            observation = next((event for event in reversed(observations)
                                if event.event_id == evidence.observation_event_id
                                and event.decision_id == evidence.context.decision_id), None)
            if observation is None:
                raise ProtocolError("Memory update observation is invalid")
            visible_ids = {row["source_event_id"]
                           for row in observation.data.get("verified_experiences", [])}
            if set(evidence.verified_event_ids) != visible_ids:
                raise ProtocolError("Memory update source window is invalid")
        except (ProtocolError, KeyError, AttributeError, TypeError) as exc:
            logging.error("Memory update evidence rejected: %s", exc)
            raise ProtocolError("Memory update evidence is invalid") from exc
        draft = EventDraft(event_type="memory_updated", occurred_at=self.state.current_time,
                           agent_id=evidence.context.agent_id,
                           decision_id=evidence.context.decision_id,
                           visibility="system", source_kind="environment", data=data)
        commit_state(self.store, self.state, [draft])
        self.ops.log(self.state.current_time, "memory", "info", "MEMORY_UPDATED",
                     f"Memory update recorded {len(data['episodes'])} episodes",
                     agent_id=evidence.context.agent_id,
                     decision_id=evidence.context.decision_id)

    def advance(self, tick: int, target: datetime) -> list[ActionResult]:
        """@brief 精确推进到下一事件边界并结算连续活动、后台事件与决策节律。"""
        if self._round is not None:
            raise ProtocolError("World time cannot advance during an open decision round")
        if any(self._ready(agent_id) for agent_id in self.state.agents):
            raise ProtocolError("World time cannot advance while an agent decision is due")
        target_epoch = int(target.timestamp())
        if tick <= 0 or target_epoch - self.state.current_time != tick or target_epoch != self._next_epoch():
            raise ProtocolError("World advance must match the next scheduling boundary")
        interval = target_epoch - self.state.current_time
        staged = flush_presentation(stage_presentation(self.state, self._pending_presentation))
        staged = flush_supply_visibility(staged, self._pending_visible_supplies)
        staged = staged.model_copy(update={"current_time": target_epoch}, deep=True)
        staged = same_moment.reset_same_moment(staged)
        drafts, results = self._advance_activities(staged, target_epoch)
        staged, background, arrivals = self._process_scheduled(staged, target_epoch)
        drafts.extend(background)
        staged = apply_engine_rhythm(self, staged,
                                     RhythmBoundary(target_epoch, interval, arrivals, drafts))
        for agent_id in expired_plan_agent_ids(staged):
            staged = _put_agent(staged, staged.agents[agent_id].model_copy(update={"day_plan": None}, deep=True))
        if target_epoch == staged.end_time:
            drafts.extend(self._truncate(staged, target_epoch))
            drafts.append(EventDraft(event_type="run_ended", occurred_at=target_epoch,
                                     visibility="system", data={"end_time": target_epoch}))
        staged.version = self.state.version + 1
        commit_state(self.store, staged, drafts)
        self.state = staged
        self._pending_presentation = {}
        self._pending_visible_supplies = {}
        return results

    def fail_run(self, error: str) -> None:
        """@brief 追加唯一 run_failed，使后续故障覆盖先前完成候选。"""
        if not error:
            raise ProtocolError("Run failure error must not be empty")
        events = self.store.read_events(self.store.last_event_seq)
        if any(event.event_type == "run_failed" for event in events):
            return
        commit_state(self.store, self.state,
                     [_failure_draft(self.state.current_time, error, None)])
        self.ops.log(self.state.current_time, "engine", "error", "RUN_FAILED",
                     f"Run failed: {error}")

    def apply_counterfactual(self, intervention: CounterfactualIntervention) -> CounterfactualReceipt:
        """@brief 在新反事实分支首次决策前提交一次对等余额干预。"""
        if type(intervention) is not CounterfactualIntervention:
            raise ProtocolError("Counterfactual intervention must use the strict life model")
        digest = _request_sha("counterfactual_intervention", intervention)
        self._validate_counterfactual(intervention, digest)
        agent = self.state.agents[intervention.agent_id]
        before = self.state.accounts[agent.balance_account_id]
        staged = self.state.model_copy(deep=True)
        staged.accounts[agent.balance_account_id] = intervention.balance_fen
        staged.version = self.state.version + 1
        changes = {f"account:{agent.balance_account_id}": intervention.balance_fen - before,
                   "account:external:scenario": before - intervention.balance_fen}
        data = {"kind": "counterfactual_intervention", "intervention_kind": intervention.kind,
                "hypothesis": intervention.hypothesis,
                "source_event_ids": list(intervention.source_event_ids),
                "intervention_sha256": digest, "before_balance_fen": before,
                "after_balance_fen": intervention.balance_fen, "resource_changes": changes}
        draft = EventDraft(event_type="environment_event", occurred_at=staged.current_time,
                           agent_id=intervention.agent_id, caused_by=list(intervention.source_event_ids),
                           visibility="evaluation", source_kind="scenario", data=data)
        event = commit_state(self.store, staged, [draft])[0]
        self.state = staged
        return CounterfactualReceipt(intervention_sha256=digest, event_id=event.event_id,
                                     agent_id=intervention.agent_id, before_balance_fen=before,
                                     after_balance_fen=intervention.balance_fen,
                                     world_state_version=staged.version)

    def _validate_counterfactual(self, intervention: CounterfactualIntervention, digest: str) -> None:
        """@brief 核对分支身份、唯一时序及父检查点以前的人物可见证据。"""
        manifest = self.store.manifest
        if (manifest.get("branch_kind") != "counterfactual"
                or manifest.get("intervention_sha256") != digest
                or not manifest.get("parent_checkpoint_id")):
            raise ProtocolError("Counterfactual branch manifest is invalid")
        events = self.store.read_events(self.store.last_event_seq)
        branch = _current_branch_event(events, manifest)
        if branch is None:
            raise ProtocolError("Counterfactual branch creation evidence is invalid")
        child_events = [event for event in events if event.event_seq > branch.event_seq]
        # 轮已开启（decision_started 延迟未写）同样构成首次决策已开始。
        if (self._round is not None or any(event.event_type == "decision_started" or
               (event.event_type == "environment_event"
                and event.data.get("kind") == "counterfactual_intervention") for event in child_events)):
            raise ProtocolError("Counterfactual intervention must precede the first decision and occur once")
        self._validate_counterfactual_sources(intervention, events)

    def _validate_counterfactual_sources(self, intervention, events) -> None:
        """@brief 要求全部来源来自父检查点截止内且对目标人物可见。"""
        branch = _current_branch_event(events, self.store.manifest)
        if branch is None or intervention.agent_id not in self.state.agents:
            raise ProtocolError("Counterfactual branch or target agent is invalid")
        sources = {event.event_id: event for event in events if event.event_seq < branch.event_seq}
        for event_id in intervention.source_event_ids:
            event = sources.get(event_id)
            if (event is None or event.visibility != "agent"
                    or event.agent_id not in {None, intervention.agent_id}):
                raise ProtocolError("Counterfactual source is not visible to the target agent")

    def checkpoint(self, agent_snapshots: dict[str, AssetRef]) -> "Checkpoint":
        """@brief 合并全部人物和 life 快照后提交一个共同检查点。"""
        expected = {f"agent:{agent_id}" for agent_id in self.store.manifest["agent_ids"]}
        if set(agent_snapshots) != expected:
            raise IntegrityError("Checkpoint agent snapshot set is incomplete")
        receipt = self._life_snapshot()
        reference = self.store.assets.put_json(receipt, {"kind": "life_checkpoint"})
        snapshots = dict(agent_snapshots)
        snapshots["life"] = reference
        committed = self.store.checkpoint(validated_payload(self.state), snapshots)
        self.ops.log(self.state.current_time, "checkpoint", "info", "CHECKPOINT_COMMITTED",
                     f"Checkpoint {committed.checkpoint_id} committed",
                     event_seq_last=committed.last_event_seq)
        return committed

    def close(self) -> None:
        """@brief 关闭当前运行的唯一存储连接。"""
        self.store.close()
        self.ops.close()

    def _validate_runtime(self) -> None:
        """@brief 在新建和恢复时核对场景、内容、决策阶段、路线、游戏和状态身份。"""
        if self.state.scenario_sha256 != self.scenario_sha256:
            raise IntegrityError("Runtime scenario identity mismatch")
        if self.state.content_manifest_sha256 != self.scenario.corpus_manifest_sha256:
            raise IntegrityError("Runtime corpus identity mismatch")
        if self.state.run_id != self.store.manifest["run_id"]:
            raise IntegrityError("Runtime run identity mismatch")
        if "two_stage_model_identity" not in self.store.manifest:
            raise IntegrityError("Runtime decision stage identity is missing")
        identity = self.store.manifest["two_stage_model_identity"]
        if identity is not None:
            from worldmock.validation import validate_two_stage_model_identity
            validate_two_stage_model_identity(identity)
        require_category_identity(self.store.manifest, self.semantics)
        validate_activity_targets(self.state, self.rules, self.scenario)
        validate_activity_energy(self.state, self.semantics.body)
        validate_storefront_state(self.state, self.scenario, self.store.manifest)
        validate_routes(self.scenario, self.corpus_root)
        for config in self.scenario.games:
            validate_game(config, self.corpus_root)

    def _validate_context(self, context: "DecisionContext") -> None:
        """@brief 拒绝运行、时刻、人物或世界版本过期的宿主上下文。"""
        from agentsociety2.society import DecisionContext

        if not isinstance(context, DecisionContext):
            raise ProtocolError("Context must use the AgentSociety2 DecisionContext type")
        if context.run_id != self.state.run_id or context.agent_id not in self.state.agents:
            raise ProtocolError("Decision context run or agent is invalid")
        if int(context.observed_at.timestamp()) != self.state.current_time:
            raise ProtocolError("Decision context observation time is stale")
        if context.world_state_version != self.state.version:
            raise ProtocolError("Decision context world version is stale")

    def _validate_call(self, call: "DirectToolCall") -> None:
        """@brief 拒绝任意字典请求、错误工具类型和非当前决策轮。"""
        from agentsociety2.society import DirectToolCall

        if not isinstance(call, DirectToolCall) or self._round is None:
            raise ProtocolError("Direct tool call requires an open decision round")
        self._validate_context(call.context)
        if call.context.round_id != self._round.round_id:
            raise ProtocolError("Direct tool call belongs to another decision round")
        owned = [item for item in self._round.pending_actions if item.agent_id == call.context.agent_id]
        if owned and all(item.action_id != call.action_id for item in owned):
            raise ProtocolError("Agent already submitted its decision round action")
        expected = REQUEST_MODELS.get(call.tool_name)
        if expected is None or type(call.request) is not expected:
            raise ProtocolError("Direct tool call request type does not match its tool")

    def _existing_action(self, action_id: str, tool_name: str, request_sha: str) -> StoredAction | None:
        """@brief 对持久和本轮动作执行严格参数一致的幂等判断。"""
        stored = self.state.processed_actions.get(action_id)
        pending = next((item for item in self._round.pending_actions if item.action_id == action_id), None)
        if stored and (stored.tool_name != tool_name or stored.request_sha256 != request_sha):
            raise ProtocolError("Action identity was reused with different parameters")
        if pending and (pending.tool_name != tool_name or pending.request_sha256 != request_sha):
            raise ProtocolError("Action identity was reused with different parameters")
        if pending:
            receipt = ActionReceipt(action_id=action_id, round_id=pending.round_id,
                                    decision_id=pending.decision_id, request_sha256=request_sha)
            return StoredAction(action_id=action_id, tool_name=tool_name, request_sha256=request_sha,
                                receipt=receipt.model_dump(mode="json"))
        return stored

    def _require_round(self, round_id: str) -> DecisionRoundState:
        """@brief 返回指定开放轮，否则作为协议故障终止。"""
        if self._round is None or self._round.round_id != round_id:
            raise ProtocolError("Decision round is not open")
        return self._round

    @property
    def recovery_kinds(self) -> frozenset:
        """@brief 能量可穿透下界的恢复活动闭集（注册表派生）。"""
        return ENERGY_RECOVERY_ACTIVITY_KINDS

    def meal_windows_by_agent(self) -> dict[int, "felt.MealWindows"]:
        """@brief 人物档案餐时窗口索引（观察与滞回判定同源）。"""
        return {config.agent_id: felt.MealWindows(lunch=config.lunch_window,
                                                  dinner=config.dinner_window)
                for config in self.scenario.agents}

    def _drain_pending_drafts(self) -> list[EventDraft]:
        """@brief 取走动作结算中暂存的送达/模型回复待办事件草稿。"""
        drained = list(self._pending_event_drafts)
        self._pending_event_drafts.clear()
        return drained

    def _resolve(self, state: WorldState, pending: PendingAction) -> tuple[WorldState, ActionResult, list[EventDraft]]:
        """@brief 将一个有类型请求结算为业务成功或稳定拒绝。"""
        if pending.reused:
            return self._reused_result(state, pending)
        try:
            changed, result = self._dispatch(state, pending)
            changed = self._after_success(changed, pending.agent_id, pending.tool_name)
            resolved = changed.activities.get(result.activity_id) if result.activity_id else None
            resolved_title = self._activity_title(resolved) if resolved is not None else None
            changed = _remember_terminal_activity(changed, result, resolved_title)
            accepted = EventDraft(event_type="action_accepted", occurred_at=state.current_time,
                                  agent_id=pending.agent_id, decision_id=pending.decision_id,
                                  action_id=pending.action_id, activity_id=result.activity_id,
                                  caused_by=_pending_causes(pending),
                                  data=_result_event_data(result, changed, resolved_title))
            drafts = [accepted, _result_draft(pending, result, "action_result")]
            terminal = _terminal_after_action(changed, result, pending.requested_event_id,
                                              resolved_title)
            if terminal is not None:
                drafts.append(terminal)
            drafts.extend(self._drain_pending_drafts())
            return changed, result, drafts
        except BusinessRuleError as error:
            changed, result = self._after_rejection(state, pending, error)
            agent = changed.agents[pending.agent_id]
            activity = next(iter(active_activities(changed, agent.agent_id)), None)
            data = {"error_code": str(error), **result.data}
            if activity is not None:
                data["activity"] = self._activity_snapshot(activity)
            rejected = EventDraft(event_type="action_rejected", occurred_at=state.current_time,
                                  agent_id=pending.agent_id, decision_id=pending.decision_id,
                                  action_id=pending.action_id,
                                  activity_id=activity.activity_id if activity else None,
                                  caused_by=_pending_causes(pending), data=data)
            drafts = [rejected, _result_draft(pending, result, "action_result")]
            drafts.extend(self._drain_pending_drafts())
            return changed, result, drafts

    def _after_success(self, state: WorldState, agent_id: int, tool_name: str) -> WorldState:
        """@brief 受理计入同刻轮数并清零连续拒绝，按同刻规则或决策节律安排下一次决策（第一章规则 2、4、7）。"""
        agent = same_moment.count_round(state.agents[agent_id])
        game = next((item for item in active_activities(state, agent_id)
                     if item.kind == ActivityKind.PLAY_GAME), None)
        # 游戏保持逐回合反馈（docs/dev/SPEC-life-runtime.md「决策节律：结算边界与唤醒条件分离」）：交互边界到期即提供决策。
        game_turn = (lambda: game.next_interaction_at or game.planned_end_at) if game is not None else None
        next_at, reasons, stage = self._round_schedule(state, agent, False, game_turn)
        changed = agent.model_copy(update={"next_decision_at": next_at,
                                           "consecutive_rejections": 0,
                                           "pending_wake_reasons": reasons,
                                           "same_moment_stage": stage}, deep=True)
        return _put_agent(state, changed)

    def _round_schedule(self, state: WorldState, agent, rejected: bool,
                        rhythm_next=None) -> tuple[int, list[str], SameMomentStage]:
        """@brief 按同刻阶梯安排本轮之后的下一次决策（docs/dev/SPEC-time-and-energy.md 第一章规则 2–4、7）。"""
        now = state.current_time

        def by_rhythm() -> int:
            """@brief 节律候选以本次决策时刻为上次决策时刻（规则 7）；无候选取结算间隔。"""
            candidate = next_decision_candidates(agent.model_copy(update={"last_decision_time": now}),
                                                 engine_rhythm_inputs(self, state))
            return candidate if candidate is not None else now + self.scenario.activity_feedback_seconds

        outcome = same_moment.RoundOutcome(now=now, agent=agent,
                                           active=bool(active_activities(state, agent.agent_id)),
                                           rejected=rejected, limit=self.scenario.max_same_moment_decisions)
        return same_moment.schedule_after_round(outcome, rhythm_next or by_rhythm)

    def _after_rejection(self, state: WorldState, pending: PendingAction,
                         error: BusinessRuleError) -> tuple[WorldState, ActionResult]:
        """@brief 业务拒绝计入同刻轮数与连续拒绝；未达上限同一秒再决策，服务拒绝脉冲每人每秒至多一次。"""
        agent = same_moment.count_round(state.agents[pending.agent_id])
        next_decision_at, reasons, stage = self._round_schedule(state, agent, True)
        pulse_key = same_moment.refusal_pulse_key(str(error))
        if pulse_key == same_moment.SERVICE_REFUSED and same_moment.refusal_pulse_applied(state, agent.agent_id):
            pulse_key = None
        body = agent.body if pulse_key is None else apply_mood_pulse(agent.body, pulse_key)
        changed_agent = agent.model_copy(update={"next_decision_at": next_decision_at,
                                                  "consecutive_rejections": agent.consecutive_rejections + 1,
                                                  "pending_wake_reasons": reasons,
                                                  "same_moment_stage": stage,
                                                  "body": body}, deep=True)
        changed = _put_agent_and_defer_game_turn(state, changed_agent)
        message = "Action rejected: " + rejection_reason_text(error)
        data = {"same_moment_round": changed_agent.same_moment_rounds,
                "max_same_moment_decisions": self.scenario.max_same_moment_decisions,
                "next_decision_at": next_decision_at}
        if str(error) == "WORK_REQUIRES_REWORK":
            data["work_review"] = self._public_work_review(pending)
            message = f"Work submission requires rework: {rejection_reason_text(error)}"
        if isinstance(error, PreconditionError):
            data["unmet"] = error.unmet
            if error.suggested is not None:
                data["suggested"] = error.suggested
        result = ActionResult(action_id=pending.action_id, agent_id=pending.agent_id,
                              status=ActionStatus.REJECTED, occurred_at=state.current_time,
                              error_code=str(error), message=message,
                              data=data)
        return changed, result

    def _public_work_review(self, pending: PendingAction) -> dict:
        """@brief 从本次系统评审生成可随动作结果公开的人物反馈。"""
        if pending.work_review is None:
            raise ProtocolError("Rejected work submission has no recorded semantic review")
        request = SubmitWorkActionRequest.model_validate(pending.request)
        task = self.rules.task(self._task_id_for_name(pending, request.task_name))
        source = load_work_input(self.corpus_root, task)
        review = WorkReviewDecision.model_validate(pending.work_review)
        return public_work_review(review, source)

    def _reused_result(self, state: WorldState,
                       pending: PendingAction) -> tuple[WorldState, ActionResult, list[EventDraft]]:
        """@brief 返回持久终态结果并只记录本次重放证据；重放计入同刻轮数，按存档结果排期，不重做领域动作与脉冲（规则 9）。"""
        stored = state.processed_actions[pending.action_id]
        result = ActionResult.model_validate(stored.result)
        accepted_statuses = {ActionStatus.ACCEPTED, ActionStatus.IN_PROGRESS, ActionStatus.COMPLETED}
        event_type = "action_accepted" if result.status in accepted_statuses else "action_rejected"
        data = result.model_dump(mode="json")
        data["reused"] = True
        first = EventDraft(event_type=event_type, occurred_at=state.current_time,
                           agent_id=pending.agent_id, decision_id=pending.decision_id,
                           action_id=pending.action_id, activity_id=result.activity_id,
                           caused_by=[pending.requested_event_id], data=data)
        second = EventDraft(event_type="action_result", occurred_at=state.current_time,
                            agent_id=pending.agent_id, decision_id=pending.decision_id,
                            action_id=pending.action_id, activity_id=result.activity_id,
                            caused_by=[pending.requested_event_id], data=data)
        agent = same_moment.count_round(state.agents[pending.agent_id])
        next_at, reasons, stage = self._round_schedule(state, agent, result.status == ActionStatus.REJECTED)
        agent = agent.model_copy(update={"next_decision_at": next_at, "pending_wake_reasons": reasons,
                                         "same_moment_stage": stage}, deep=True)
        changed = _put_agent_and_defer_game_turn(state, agent)
        return changed, result, [first, second]

    def _dispatch(self, state: WorldState, pending: PendingAction) -> tuple[WorldState, ActionResult]:
        """@brief 依据闭集工具名进入对应领域转换。"""
        model = REQUEST_MODELS[pending.tool_name]
        try:
            request = model.model_validate(pending.request)
        except ValueError as error:
            # 请求再验证失败（绕过构造期校验的载荷，如超长 goal）转业务拒绝：裸 ValidationError 穿透 _resolve 杀 run（unit-16 E2 深层防御）。
            raise BusinessRuleError("ACTION_REQUEST_INVALID") from error
        require_interaction_menu(self.scenario, state, pending, request)
        self._require_boundary_action(state, pending, request)
        handlers = {
            "start_activity": self._start_activity,
            "submit_work": self._submit_work_action,
            "send_message": self._send_message_action,
            "recall_task_summary": self._recall_task_summary,
            "report_status": self._report_status,
            "pause_activity": self._pause_activity,
            "conclude_activity": self._conclude_activity,
            "plan_day": self._plan_day,
            "revise_day_plan": self._revise_day_plan,
        }
        return handlers[pending.tool_name](state, pending, request)

    def _require_boundary_action(self, state: WorldState, pending: PendingAction, request) -> None:
        """@brief 决策须已到期；达到同刻上限后的受限轮只接受本轮开放的动作类（第一章「实现约定」）。"""
        agent = state.agents[pending.agent_id]
        if agent.next_decision_at > state.current_time:
            raise BusinessRuleError("ACTIVITY_FEEDBACK_NOT_DUE")
        if agent.same_moment_stage != SameMomentStage.OPEN:
            same_moment.require_stage_action(agent.same_moment_stage,
                                             self._same_moment_request_kind(state, pending, request))

    def _same_moment_request_kind(self, state: WorldState, pending: PendingAction, request) -> str:
        """@brief 受限轮复核的请求类：只等待、开始持续活动（不含到店购买与远程下单）、恢复暂停活动或其他。"""
        if pending.tool_name == "start_activity":
            actions = [row["selected_action"]["canonical_action"]
                       for row in self._resolve_interaction_bindings(pending, request)]
            if actions == ["wait"]:
                return "wait"
            return "other" if "purchase" in actions else "start"
        if pending.tool_name == "report_status":
            sessions = self._sessions_for_names(state, pending, request.task)
            return "resume" if all(item.status == SessionStatus.PAUSED for item in sessions) else "other"
        return "other"

    def _activity_title(self, activity) -> str | None:
        """@brief 解析有内容目标活动的展示标题，无标题活动返回 None。"""
        if activity.target_id is None:
            return None
        if activity.kind == ActivityKind.LIFESTYLE:
            return self._lifestyle_names.get(activity.target_id)
        if activity.kind in (ActivityKind.WATCH, ActivityKind.PLAY_GAME):
            return self.rules.content_titles.get(activity.target_id)
        return None

    def _activity_snapshot(self, activity) -> dict:
        """@brief 导出活动权威快照并为内容型活动附查看器直显标题。"""
        payload = activity.model_dump(mode="json")
        if activity.kind == ActivityKind.LIFESTYLE:
            item_id = (activity.target_id or "").removeprefix("lifestyle:")
            item = next((item for item in self.lifestyle_items if item.activity_id == item_id), None)
            if item is not None:
                payload["category"] = item.category
        title = self._activity_title(activity)
        if title is not None:
            payload["title"] = title
        return payload

    def _travel_start_context(self, state: WorldState, agent,
                              request: ActivityStartParams):
        """@brief 在扣款前完成路线与品类规则复查。"""
        if request.activity_type != ActivityKind.TRAVEL:
            return None
        route = route_for(self.scenario, agent.location_id, request)
        match = self.semantics.travel_match(route.mode.value)
        expected = self.semantics.travel_planned_seconds(
            route.mode.value, route.duration_seconds, state.current_time)
        if request.planned_duration_seconds != expected:
            raise BusinessRuleError("ROUTE_DURATION_MISMATCH")
        return route, match

    def _start(self, state: WorldState, pending: PendingAction,
               request: ActivityStartParams, energy_check: bool = True) -> tuple[WorldState, ActionResult]:
        """@brief 启动普通活动并初始化内容、路线或游戏身份。"""
        agent = state.agents[pending.agent_id]
        data = self.rules.validate_start(agent, request, state.current_time)
        if request.activity_type == ActivityKind.WORK:
            validate_work_duration(
                state, self.rules.task(request.target_id), pending.agent_id,
                request.planned_duration_seconds)
        travel_context = self._travel_start_context(state, agent, request)
        self._require_available_resources(state, pending.agent_id, resource_claims(request))
        origin = _activity_origin(state, pending)
        agent, activity = start_activity(agent, request, state.current_time, origin,
                                         self.semantics.body)
        activity = schedule_feedback(activity, state.current_time, self.scenario.activity_feedback_seconds)
        changed_state = state
        payment = None
        if travel_context is not None:
            route, _ = travel_context
            changed_state, payment = pay_travel_fare(state, self.scenario, pending.agent_id, route)
            data.update(travel_payment=payment, resource_changes=payment["resource_changes"])
        activity.data.update(data)
        if request.activity_type == ActivityKind.WATCH:
            activity.media_position_seconds = data["start_position_seconds"]
        if energy_check:
            self._require_energy_plan(changed_state, agent, activity)
        changed = _put_activity(changed_state, agent, activity)
        result_data = {"activity": self._activity_snapshot(activity)}
        if payment is not None:
            result_data.update(travel_payment=payment, resource_changes=payment["resource_changes"])
        if travel_context is not None:
            result_data["category_matches"] = match_rows((travel_context[1],))
        result = ActionResult(action_id=pending.action_id, agent_id=agent.agent_id,
                              status=ActionStatus.IN_PROGRESS, occurred_at=state.current_time,
                              activity_id=activity.activity_id, message="Activity started", data=result_data)
        return changed, result

    def _require_available_resources(self, state: WorldState, agent_id: int,
                                     claims: set[str], exclude_id: str | None = None) -> None:
        """@brief 以全部 ACTIVE 活动显式声明校验同人资源交集。"""
        occupied = set().union(*(item.resource_claims for item in active_activities(state, agent_id)
                                 if item.activity_id != exclude_id))
        if occupied & claims:
            raise BusinessRuleError("RESOURCE_CONFLICT")

    def _require_energy_plan(self, state: WorldState, agent, candidate=None) -> None:
        """@brief 按各结束点最大 MET 校验活动集合的共同剩余能量。"""
        activities = (*active_activities(state, agent.agent_id), *((candidate,) if candidate else ()))
        durations = tuple((item.planned_duration_seconds - item.elapsed_seconds,
                           self._activity_met(item)) for item in activities)
        projected = shared_energy_cost(durations, self.semantics.body)
        if (projected > agent.body.energy_kcal + 1e-9
                and not all(item.kind in ENERGY_RECOVERY_ACTIVITY_KINDS for item in activities)):
            raise BusinessRuleError("INSUFFICIENT_ENERGY")

    def _activity_met(self, activity) -> float:
        """@brief 使用冻结声明与社交常量读取单项 MET。"""
        if activity.kind == ActivityKind.CHAT:
            return social_actions.CHAT_MET
        if activity.kind == ActivityKind.CALL:
            return social_actions.CALL_MET
        key = self.semantics.affordances.met_lookup_key(
            activity.kind.value, travel_mode=activity.travel_mode, target_id=activity.target_id)
        return self.semantics.body.activity_met[key]

    def _change(self, state: WorldState, pending: PendingAction,
                request: ChangeActivityRequest) -> tuple[WorldState, ActionResult]:
        """@brief 转换人物已有活动并保留已完成进度。"""
        if request.activity_id not in state.activities:
            raise BusinessRuleError("ACTIVITY_NOT_FOUND")
        agent = state.agents[pending.agent_id]
        current = state.activities[request.activity_id]
        if request.operation == ActivityChange.RESUME:
            self.rules.validate_activity_location(agent, current.kind, current.target_id)
            self._require_available_resources(state, pending.agent_id, current.resource_claims,
                                              current.activity_id)
        if current.kind in {ActivityKind.CHAT, ActivityKind.CALL}:
            agent, activity = social_actions.social_activity_change(
                agent, current, request.operation.value, state.current_time)
            changed = _put_activity(state, agent, activity)
            result = ActionResult(action_id=pending.action_id, agent_id=agent.agent_id,
                                  status=ActionStatus.COMPLETED, occurred_at=state.current_time,
                                  activity_id=activity.activity_id, message="Activity changed",
                                  data={"activity": self._activity_snapshot(activity)})
            return changed, result
        if request.operation.value == "change_speed" and current.kind == ActivityKind.WATCH:
            remaining = current.planned_duration_seconds - current.elapsed_seconds
            content = self.rules.content(current.target_id)
            if current.media_position_seconds + remaining * request.playback_speed > content.duration_seconds:
                raise BusinessRuleError("CONTENT_END_EXCEEDED")
        agent, activity = change_activity(agent, current, request, state.current_time,
                                          self.scenario.activity_feedback_seconds)
        if request.operation == ActivityChange.RESUME:
            self._require_energy_plan(state, agent, activity)
        changed = _put_activity(state, agent, activity)
        result = ActionResult(action_id=pending.action_id, agent_id=agent.agent_id,
                              status=ActionStatus.COMPLETED, occurred_at=state.current_time,
                              activity_id=activity.activity_id, message="Activity changed",
                              data={"activity": self._activity_snapshot(activity)})
        return changed, result

    def _consume(self, state: WorldState, pending: PendingAction,
                 request: ConsumeRequest, energy_check: bool = True) -> tuple[WorldState, ActionResult]:
        """@brief 启动一次多件进食或单件使用活动（docs/dev/SPEC-life-realism.md「商务与进食」：时长取最长、效果累加）。"""
        agent = state.agents[pending.agent_id]
        products = self.rules.items
        action = Action(f"consume:{request.use_mode}")
        matches = require_item_rules(
            self.semantics.catalog, products,
            (item.product_id for item in request.items), action)
        validate_meal_use(agent, products, request)
        recipe = prepare_inventory_recipe(
            agent, RecipeContext(self.semantics.catalog, products, self.scenario.recipe_output_bindings),
            request, matches[0])
        if recipe is None:
            validate_meal_inventory(agent, request)
        duration = meal_duration(products, request)
        start = ActivityStartParams(activity_type=ActivityKind.CONSUME,
                                    planned_duration_seconds=duration,
                                    target_id=request.items[0].product_id)
        origin = _activity_origin(state, pending)
        self._require_available_resources(state, pending.agent_id, resource_claims(start))
        agent, activity = start_activity(agent, start, state.current_time, origin,
                                         self.semantics.body)
        activity = schedule_feedback(activity, state.current_time, self.scenario.activity_feedback_seconds)
        from worldmock.life import sleep as sleep_module
        efficiency = sleep_module.food_utilization_efficiency(agent.body.sleep_deficit_hours)
        food_rate = meal_food_rate(products, request) * efficiency
        activity.data.update(request=request.model_dump(mode="json"), consumed={},
                             food_energy_kcal_per_hour=food_rate)
        if recipe is not None:
            activity.data["inventory_recipe"] = recipe.payload()
        if energy_check:
            self._require_energy_plan(state, agent, activity)
        changed = _put_activity(state, agent, activity)
        result = ActionResult(action_id=pending.action_id, agent_id=pending.agent_id,
                              status=ActionStatus.IN_PROGRESS, occurred_at=state.current_time,
                              activity_id=activity.activity_id, message="Product use started",
                              data={"activity": self._activity_snapshot(activity),
                                    "category_matches": match_rows(matches)})
        return changed, result

    def _submit_work(self, state: WorldState, pending: PendingAction,
                     request: SubmitWorkRequest) -> tuple[WorldState, ActionResult]:
        """@brief 独立验收结构化工作并支付唯一报酬。"""
        submission = prepare_work_submission(state, self.semantics, self.corpus_root,
                                             pending.agent_id, request)
        if pending.work_review is None:
            raise ProtocolError("Eligible work submission has no recorded semantic review")
        review = WorkReviewDecision.model_validate(pending.work_review)
        prepared = prepare_work_review(
            submission, self.store.manifest["model_identity"]["requested_model"])
        if not review_matches(review, prepared):
            raise ProtocolError("Work review identity disagrees with the current submission")
        outcome = settle_work(state, submission, review.accepted)
        feedback = public_work_review(review, submission.source)
        agent_after = outcome.state.agents[pending.agent_id].model_copy(
            update={"body": apply_mood_pulse(outcome.state.agents[pending.agent_id].body,
                                             "work_review_passed")}, deep=True)
        agents = dict(outcome.state.agents)
        agents[pending.agent_id] = agent_after
        outcome_state = outcome.state.model_copy(update={"agents": agents}, deep=True)
        data = {"task_id": request.task_id, "payment_fen": outcome.payment_fen,
                "worked_seconds": outcome.worked_seconds,
                "effective_seconds": outcome.effective_seconds, "work_review": feedback,
                "category_matches": match_rows((self.semantics.contract_match(submission.task),)),
                "resource_changes": {
                    f"account:{self.rules.task(request.task_id).employer_account_id}": -outcome.payment_fen,
                    f"account:{state.agents[pending.agent_id].balance_account_id}": outcome.payment_fen}}
        result = ActionResult(action_id=pending.action_id, agent_id=pending.agent_id,
                              status=ActionStatus.COMPLETED, occurred_at=state.current_time,
                              message="Work accepted and paid", data=data)
        return outcome_state, result

    def _plan_day(self, state: WorldState, pending: PendingAction,
                  request: PlanDayRequest) -> tuple[WorldState, ActionResult]:
        """@brief 登记当日有序目标；成功登记每天至多一次，迟交与拒绝重试均允许。"""
        agent = state.agents[pending.agent_id]
        now = state.current_time
        date = _datetime(now, self.scenario.timezone).date().isoformat()
        planned_today = agent.day_plan is not None and not day_plan_expired(agent.day_plan, now)
        if date in agent.plan_submitted_dates or planned_today:
            raise BusinessRuleError("PLAN_ALREADY_SUBMITTED")
        day_end = day_end_seconds(now, self.scenario.timezone)
        try:
            goals = tuple(PlanGoal(**goal.model_dump(), registered_at=now) for goal in request.goals)
            plan = initial_day_plan(goals, day_end)
        except ValueError as error:
            raise BusinessRuleError("PLAN_GOALS_INVALID") from error
        changed_agent = agent.model_copy(
            update={"day_plan": plan, "plan_submitted_dates": set(agent.plan_submitted_dates) | {date}},
            deep=True)
        changed = _put_agent(state, changed_agent)
        result = ActionResult(action_id=pending.action_id, agent_id=pending.agent_id,
                              status=ActionStatus.COMPLETED, occurred_at=now,
                              message="Day plan registered as self-commitment",
                              data={"day_plan": plan.model_dump(mode="json")})
        return changed, result

    def _revise_day_plan(self, state: WorldState, pending: PendingAction,
                         request: ReviseDayPlanRequest) -> tuple[WorldState, ActionResult]:
        """@brief 替换未推进的当日目标，已推进目标保持不变。"""
        agent = state.agents[pending.agent_id]
        if agent.day_plan is None or day_plan_expired(agent.day_plan, state.current_time):
            raise BusinessRuleError("PLAN_NOT_FOUND")
        try:
            goals = tuple(PlanGoal(**goal.model_dump(), registered_at=state.current_time) for goal in request.goals)
            plan = revise_day_plan(agent.day_plan, state.current_time, goals)
        except ValueError as error:
            raise BusinessRuleError("PLAN_GOAL_ALREADY_PROGRESSED" if "already progressed" in str(error)
                                    else "PLAN_GOALS_INVALID") from error
        changed_agent = agent.model_copy(update={"day_plan": plan}, deep=True)
        changed = _put_agent(state, changed_agent)
        result = ActionResult(action_id=pending.action_id, agent_id=pending.agent_id,
                              status=ActionStatus.COMPLETED, occurred_at=state.current_time,
                              message="Day plan revised",
                              data={"day_plan": plan.model_dump(mode="json")})
        return changed, result

    def _send_message(self, state: WorldState, pending: PendingAction,
                      request: SendMessageRequest) -> tuple[WorldState, ActionResult]:
        """@brief 瞬时消息：登记收件方 inbox 与 message_arrival 送达（docs/dev/SPEC-life-runtime.md「社交最小可行版」）。"""
        from worldmock.life import social as social_domain

        agent = state.agents[pending.agent_id]
        contacts = self._agent_config(agent.agent_id).contacts
        outgoing = social_domain.OutgoingMessage(
            message_id=f"message-{pending.action_id}", sender_id=f"agent:{agent.agent_id}",
            recipient_id=request.recipient_id, text=request.text,
            created_at=state.current_time)
        world_agent_ids = {f"agent:{agent_id}" for agent_id in self.state.agents}
        # 域校验（收件人存在 + 文本 300 上限）ValueError 转业务拒绝：裸穿杀 run；RECIPIENT_NOT_FOUND 由此复活（收件登记的 else 分支不可达）。
        try:
            sent = social_domain.send_message(outgoing, world_agent_ids, contacts)
        except ValueError as error:
            if "recipient not found" in str(error):
                raise BusinessRuleError("RECIPIENT_NOT_FOUND") from error
            raise BusinessRuleError("MESSAGE_TEXT_TOO_LONG") from error
        state = self._register_delivered_message(state, agent, contacts, request, sent)
        result = ActionResult(action_id=pending.action_id, agent_id=pending.agent_id,
                              status=ActionStatus.COMPLETED, occurred_at=state.current_time,
                              message="Message delivered", data={
                                  "message_id": sent.message.message_id,
                                  "recipient_id": request.recipient_id})
        return state, result

    def _register_delivered_message(self, state: WorldState, agent, contacts,
                                    request: SendMessageRequest, sent) -> WorldState:
        """@brief 送达登记：收件方 inbox、model 联系人回复待办与 message_arrival 事件。

        同世界收件人的 message_arrival 草稿登记其同秒就绪，由 commit_decision_round 在本轮全部动作结算后合并
        （docs/dev/SPEC-time-and-energy.md 第一章「人物之间的消息」）。
        """
        drafts: list[EventDraft] = []
        recipient_agent = next((item for item in state.agents.values()
                                if f"agent:{item.agent_id}" == request.recipient_id), None)
        if recipient_agent is not None:
            inbox = dict(state.agents[recipient_agent.agent_id].inbox)
            inbox[sent.message.message_id] = sent.message
            state = _put_agent(state, state.agents[recipient_agent.agent_id].model_copy(
                update={"inbox": inbox}, deep=True))
        elif any(contact.contact_id == request.recipient_id for contact in contacts):
            reply_request = self._contact_reply_request(sent.message, contacts)
            drafts.append(EventDraft(
                event_type="environment_event", occurred_at=state.current_time,
                agent_id=agent.agent_id, source_kind="model", visibility="system",
                data={"kind": "model_npc_reply_pending",
                      "request": reply_request.model_dump(mode="json")}))
        else:
            raise BusinessRuleError("RECIPIENT_NOT_FOUND")
        arrival = EventDraft(event_type="environment_event", occurred_at=state.current_time,
                             agent_id=recipient_agent.agent_id if recipient_agent else None,
                             source_kind="environment",
                             data={"kind": "message_arrival",
                                   "message_id": sent.message.message_id,
                                   "recipient_id": request.recipient_id,
                                   "occurred_at": state.current_time})
        drafts.append(arrival)
        self._pending_event_drafts.extend(drafts)
        return state

    def _contact_reply_request(self, message, contacts):
        """@brief model 策略联系人的回复请求（待宿主执行的外部调用登记）。"""
        from worldmock.life import social as social_domain

        contact = next(item for item in contacts if item.contact_id == message.recipient_id)
        if contact.reply_policy != "model":
            raise BusinessRuleError("CONTACT_REPLY_NOT_MODEL")
        return social_domain.model_reply_request(social_domain.IncomingMessage(
            message_id=message.message_id, sender_id=message.sender_id,
            contact_id=contact.contact_id, text=message.text,
            received_at=message.created_at))

    def _start_social_activity(self, state: WorldState, pending: PendingAction,
                               request: StartSocialActivityRequest,
                               energy_check: bool = True) -> tuple[WorldState, ActionResult]:
        """@brief 受理交谈（共同在场）或通话（联系人，10-60 分钟）（docs/dev/SPEC-life-runtime.md「社交最小可行版」）。"""
        agent = state.agents[pending.agent_id]
        origin = _activity_origin(state, pending)
        if request.kind == "chat":
            # 共同在场/可交谈 ValueError 转业务拒绝：裸异常穿透 _resolve 杀 run（观察层把睡着/游戏中的人也列入 partner_id 枚举）。
            try:
                started = social_actions.start_chat(state, agent, request, origin,
                                                    f"invite-{pending.action_id}")
            except ValueError as error:
                raise BusinessRuleError("CHAT_PARTNER_NOT_AVAILABLE") from error
        else:
            contact = self._contact(request.partner_id, agent.agent_id)
            try:
                started = social_actions.start_call(agent, contact, request, origin,
                                                    state.current_time)
            except ValueError as error:
                raise BusinessRuleError("CALL_DURATION_OUT_OF_RANGE") from error
        self._require_available_resources(state, pending.agent_id, started.activity.resource_claims)
        if energy_check:
            self._require_energy_plan(state, started.agent, started.activity)
        if started.invite is not None:
            self._deliver_social_message(state, started.invite)
        changed = _put_activity(state, started.agent, started.activity)
        result = ActionResult(action_id=pending.action_id, agent_id=pending.agent_id,
                              status=ActionStatus.IN_PROGRESS, occurred_at=state.current_time,
                              activity_id=started.activity.activity_id,
                              message="Social activity started",
                              data={"activity": self._activity_snapshot(started.activity)})
        return changed, result

    def _contact(self, contact_id: str, agent_id: int):
        """@brief 查找人物声明的联系人（找不到即业务拒绝）。"""
        contacts = self._agent_config(agent_id).contacts
        matches = [item for item in contacts if item.contact_id == contact_id]
        if len(matches) != 1:
            raise BusinessRuleError("CONTACT_NOT_FOUND")
        return matches[0]

    def _deliver_social_message(self, state: WorldState, sent) -> None:
        """@brief 把交谈邀请送达对方 inbox 并留 message_arrival 事件草稿。

        该草稿登记清醒收件人的同秒就绪，由 commit_decision_round 在本轮全部动作结算后合并
        （docs/dev/SPEC-time-and-energy.md 第一章「人物之间的消息」）。
        """
        recipient = next((item for item in state.agents.values()
                          if f"agent:{item.agent_id}" == sent.message.recipient_id), None)
        if recipient is not None:
            inbox = dict(state.agents[recipient.agent_id].inbox)
            inbox[sent.message.message_id] = sent.message
            state.agents[recipient.agent_id] = state.agents[recipient.agent_id].model_copy(
                update={"inbox": inbox}, deep=True)
        self._pending_event_drafts.append(EventDraft(
            event_type="environment_event", occurred_at=state.current_time,
            agent_id=recipient.agent_id if recipient else None, source_kind="environment",
            data={"kind": "message_arrival", "message_id": sent.message.message_id,
                  "recipient_id": sent.message.recipient_id,
                  "occurred_at": sent.arrival.occurred_at}))

    def _advance_activities(self, state: WorldState, target: int) -> tuple[list[EventDraft], list[ActionResult]]:
        """@brief 每个人先结算共同身体，再独立推进全部正在执行的活动。"""
        drafts: list[EventDraft] = []
        results: list[ActionResult] = []
        for agent_id in sorted(state.agents):
            agent = state.agents[agent_id]
            interval_start = agent.body_settled_at
            if interval_start == target:
                continue
            activities = active_activities(state, agent_id)
            factors = tuple((item.kind, self._shared_factors(item, agent)) for item in activities)
            settlement = settle_shared_body(agent.body, factors, target - interval_start,
                                            self.semantics.body)
            body_event = EventDraft(event_type="environment_event", occurred_at=target,
                                    agent_id=agent_id, source_kind="environment",
                                    caused_by=list(dict.fromkeys(item.origin_request_event_id
                                                                 for item in activities)),
                                    data={"kind": "body_settlement", "interval_start": interval_start,
                                          "interval_end": target,
                                          "activity_ids": [item.activity_id for item in activities],
                                          "body_settlement": _body_settlement_data(settlement),
                                          RESOURCE_FLOWS_FIELD: _resource_flows_data(agent_id, target)})
            drafts.append(body_event)
            state.agents[agent_id] = agent.model_copy(
                update={"body": settlement.after, "body_settled_at": target}, deep=True)
            for activity in activities:
                progress, terminal = self._advance_one_activity(state, activity, target,
                                                                 settlement, body_event.event_id)
                drafts.append(progress)
                if terminal is not None:
                    result, event = terminal
                    results.append(result)
                    drafts.append(event)
        return drafts, results

    def _shared_factors(self, activity, agent) -> ActivityFactors:
        """@brief 社交活动用冻结常量，其余活动复用自身声明系数。"""
        if activity.kind in {ActivityKind.CHAT, ActivityKind.CALL}:
            return ActivityFactors(self._activity_met(activity))
        return activity_factors(activity, agent, self.semantics.body)

    def _advance_one_activity(self, state: WorldState, activity, target: int,
                              settlement, body_event_id: str):
        """@brief 推进单项进度并引用已排在前面的唯一共同身体事件。"""
        agent = state.agents[activity.agent_id]
        before_consumed = dict(activity.data.get("consumed", {}))
        if activity.kind in {ActivityKind.CHAT, ActivityKind.CALL}:
            advanced = social_actions.advance_social_shared(
                activity, agent, target, settlement,
                social_actions.chat_accepted(state, activity.agent_id))
        else:
            advanced = advance_activity_shared(activity, agent, target, settlement)
        if advanced.completed:
            advanced = ActivityAdvance(advanced.activity, advanced.agent.model_copy(
                update={"next_decision_at": target}, deep=True), settlement, True)
        state.agents[activity.agent_id] = advanced.agent
        state.activities[activity.activity_id] = advanced.activity
        effects = self._apply_activity_effect(state, advanced.activity, advanced.completed,
                                              before_consumed)
        data = {"interval_start": activity.last_settled_at, "interval_end": target,
                "activity": self._activity_snapshot(state.activities[activity.activity_id]),
                "body_settlement_event_id": body_event_id, **effects}
        if activity.kind == ActivityKind.WORK:
            data["activity_efficiency"] = settlement.work_efficiency
        flows = _resource_flows_data(activity.agent_id, target)
        if flows:
            data[RESOURCE_FLOWS_FIELD] = flows
        draft = EventDraft(event_type="activity_progress", occurred_at=target,
                           agent_id=activity.agent_id, decision_id=activity.origin_decision_id,
                           action_id=activity.origin_action_id, activity_id=activity.activity_id,
                           caused_by=[body_event_id, activity.origin_request_event_id], data=data)
        return draft, self._terminal_for_progress(state, state.activities[activity.activity_id], target)

    def _terminal_for_progress(self, state: WorldState, activity, target: int):
        """@brief 原动作的所有活动均终结后写一次聚合结算。"""
        if activity.status not in {ActivityStatus.COMPLETED, ActivityStatus.CANCELLED,
                                   ActivityStatus.FAILED, ActivityStatus.TRUNCATED}:
            return None
        if activity.origin_action_id not in state.processed_actions:
            raise ProtocolError("Activity origin action is missing from processed actions")
        result = _origin_terminal_result(state, activity, target, self._activity_title(activity))
        if result is None:
            return None
        state.processed_actions = _terminal_action_records(state, result)
        return result, _origin_terminal_draft(state, activity, target, [], self._activity_title(activity))

    def _apply_activity_effect(self, state: WorldState, activity, completed: bool,
                               before_consumed: dict[str, float]) -> dict:
        """@brief 在推进后投影路线、内容和按区间发生的商品效果。"""
        agent = state.agents[activity.agent_id]
        evidence = {}
        if activity.kind == ActivityKind.TRAVEL:
            route = next(item for item in self.scenario.routes if item.route_id == activity.data["route_id"])
            activity, agent = update_travel(activity, route, agent)
            record_arrival_access(activity, agent, self.scenario)
            state.activities[activity.activity_id] = activity
            state.agents[agent.agent_id] = agent
        if activity.kind == ActivityKind.WATCH:
            item = self.rules.content(activity.target_id)
            state.agents[agent.agent_id], activity.data["revealed"] = reveal_watch(
                activity, agent, item, self.rules.cues(item.item_id))
            if item.kind != "podcast_episode":
                _dedupe_revealed_cues(activity)
        if activity.kind == ActivityKind.WORK:
            material = reveal_work_progress(
                state, self.corpus_root, self.rules.task(activity.target_id),
                activity.agent_id, activity.activity_id)
            evidence["work_material"] = material
            state.agents[agent.agent_id] = _record_material_reveal(
                state.agents[agent.agent_id], activity.target_id, material, state.current_time)
        if activity.kind == ActivityKind.CONSUME:
            evidence["consumption"] = self._apply_consume_effect(
                state, activity, completed, before_consumed)
        if not completed:
            return evidence
        if activity.kind == ActivityKind.READ_MAIL:
            state.agents[agent.agent_id], activity.data["revealed"] = reveal_mail(
                activity, agent, self.rules.messages)
        elif activity.kind == ActivityKind.BROWSE:
            staged, revealed = self._complete_product_browse(state, agent)
            state.agents, state.presentation_state = staged.agents, staged.presentation_state
            activity.data["revealed"] = revealed
            evidence["browse_result"] = revealed
        return evidence

    def _apply_consume_effect(self, state: WorldState, activity, completed: bool,
                              before_consumed: dict[str, float]) -> dict:
        """@brief 记录本区间实际食用增量和当时的营养利用效率。"""
        from worldmock.life import sleep as sleep_module

        agent = state.agents[activity.agent_id]
        food_efficiency = (sleep_module.food_utilization_efficiency(agent.body.sleep_deficit_hours)
                           if activity.data["request"]["use_mode"] == "eat" else None)
        if completed and "inventory_recipe" in activity.data:
            state.agents[activity.agent_id] = apply_inventory_recipe(
                agent, self.rules.items, self.semantics.catalog, activity)
        elif "inventory_recipe" not in activity.data:
            state.agents[activity.agent_id] = apply_meal_progress(
                agent, self.rules.items, activity, self.semantics.body)
        state.activities[activity.activity_id] = activity
        data = _meal_consumption_data(activity, state.agents[activity.agent_id], before_consumed)
        data["food_efficiency"] = food_efficiency
        return data

    def _process_scheduled(
            self, state: WorldState, target: int
    ) -> tuple[WorldState, list[EventDraft], dict[int, set[str]]]:
        """@brief 处理同秒稳定排序的邮件、工作机会、配送与预排段事件并标唤醒旗标。"""
        due = [item for item in state.pending_events if item.due_at == target]
        state.pending_events = [item for item in state.pending_events if item.due_at != target]
        drafts = []
        arrivals: dict[int, set[str]] = {}
        for item in sorted(due, key=lambda value: (value.sequence, value.scheduled_id)):
            outcome = apply_scheduled_effect(state, item, self.semantics)
            state = outcome.state
            if item.agent_id is not None:
                arrivals.setdefault(item.agent_id, set()).add(
                    _SCHEDULED_WAKE_FLAGS.get(item.kind.value, "message_arrival"))
            self.ops.log(target, "engine", "info", "SCHEDULED_EVENT_DISPATCHED",
                         f"Scheduled event {item.kind.value} dispatched", agent_id=item.agent_id,
                         event_kind=item.kind.value, scheduled_id=item.scheduled_id)
            drafts.append(EventDraft(event_type="environment_event", occurred_at=target,
                                     agent_id=item.agent_id, source_kind="scenario",
                                     data={"kind": item.kind.value, "scheduled_id": item.scheduled_id,
                                           **outcome.data}))
        return state, drafts, arrivals

    def _truncate(self, state: WorldState, now: int) -> list[EventDraft]:
        """@brief 在实验终点标记所有仍活动的会话并保留实际进度。"""
        drafts = []
        for activity_id, activity in state.activities.items():
            if activity.status not in {ActivityStatus.ACTIVE, ActivityStatus.PAUSED}:
                continue
            activity, agent = truncate_activity(activity, state.agents[activity.agent_id], now)
            state.activities[activity_id] = activity
            state.agents[agent.agent_id] = agent
            result = _origin_terminal_result(state, activity, now, self._activity_title(activity))
            if result is not None:
                state.processed_actions = _terminal_action_records(state, result)
                drafts.append(_origin_terminal_draft(state, activity, now, [], self._activity_title(activity)))
        return drafts

    def _ready(self, agent_id: int) -> bool:
        """@brief next_decision_at 已到的人物就绪（同刻机会只由其等于当前时刻表达，第一章规则 8）。"""
        if self.state.current_time >= self.state.end_time:
            return False
        agent = self.state.agents[agent_id]
        if agent.next_decision_at > self.state.current_time:
            return False
        return True

    def _next_epoch(self) -> int:
        """@brief 选择严格晚于当前时刻的最近活动、后台或节律边界。"""
        candidates = [self.state.end_time]
        candidates.extend(item.due_at for item in self.state.pending_events if item.due_at > self.state.current_time)
        candidates.extend(agent.next_decision_at for agent in self.state.agents.values()
                          if agent.next_decision_at > self.state.current_time)
        for activity in self.state.activities.values():
            if activity.status != ActivityStatus.ACTIVE:
                continue
            candidates.append(activity.planned_end_at)
            if activity.next_interaction_at and activity.next_interaction_at > self.state.current_time:
                candidates.append(activity.next_interaction_at)
        for agent_id in self.state.agents:
            body_boundary = self._body_boundary_at(agent_id)
            if body_boundary is not None:
                candidates.append(body_boundary)
        return min(candidates)

    def _body_boundary_at(self, agent_id: int) -> int | None:
        """@brief 按同人活动集合预测首个共同身体阈值，空闲仍使用静息。"""
        agent = self.state.agents[agent_id]
        activities = active_activities(self.state, agent_id)
        current = self.state.current_time
        hour = projection.hour_of_day(current, self.scenario.timezone)
        factors = tuple((item.kind, self._shared_factors(item, agent)) for item in activities)
        seconds = next_shared_body_boundary_seconds(agent.body, factors, self.semantics.body, hour)
        if seconds is None:
            return None
        candidate = current + seconds
        end = min((item.planned_end_at for item in activities), default=self.state.end_time)
        return candidate if candidate < end else None

    def _life_snapshot(self) -> dict:
        """@brief 生成恢复时必须重放核对的 life 参与者收据。"""
        return {"schema_version": 1, "run_id": self.state.run_id, "state_version": self.state.version,
                "current_time": self.state.current_time, "scenario_sha256": self.scenario_sha256,
                "corpus_manifest_sha256": self.scenario.corpus_manifest_sha256}


def _initial_state(semantics: ScenarioSemantics, scenario_sha256: str, run_id: str, taste_weights: dict) -> WorldState:
    """@brief 从组装语义形成无隐藏动作的完整初始世界；口味权重按人物注入。"""
    scenario = semantics.scenario
    start = int(scenario.start_at.timestamp())
    end = int(scenario.end_at.timestamp())
    agents = {}
    accounts = inject_initial_funds(dict(semantics.accounts), start)
    for config in scenario.agents:
        account_id = f"agent:{config.agent_id}"
        agents[config.agent_id] = _initial_agent(config, scenario, account_id, start, taste_weights)
    pending = compile_schedule(scenario, start, end)
    random_state = hashlib.sha256(f"random:{scenario.random_seed}".encode()).hexdigest()
    return WorldState(schema_version=WORLD_SCHEMA_VERSION, version=0, current_time=start,
                      end_time=end, run_id=run_id,
                      timezone=scenario.timezone, scenario_sha256=scenario_sha256,
                      content_manifest_sha256=scenario.corpus_manifest_sha256, agents=agents,
                      accounts=accounts, **initial_supply_state(scenario, start),
                      pending_events=pending, random_seed=scenario.random_seed, random_state=random_state)


def _initial_agent(config, scenario: RuntimeScenario, account_id: str, start: int, taste_weights: dict):
    """@brief 构造人物身体（两过程睡眠反推初值）、可见日历、空进度和注入口味。"""
    from worldmock.life.models import AgentState

    assignee = f"agent:{config.agent_id}"
    visible_tasks = {item.task_id for item in scenario.tasks
                     if item.assignee_agent_id == assignee and int(item.available_at.timestamp()) <= start}
    visible_mail = {item.message_id for item in scenario.messages if int(item.sent_at.timestamp()) <= start
                    and item.recipient_agent_id == config.agent_id}
    start_hour = _datetime(start, scenario.timezone).hour + _datetime(start, scenario.timezone).minute / 60
    body = initial_body(scenario.body, start_hour, config.sleep_need_hours,
                        config.chronotype_midsleep)
    return AgentState(agent_id=config.agent_id, name=config.name, location_id=config.home_location_id,
                      balance_account_id=account_id, skill_levels=config.skill_levels,
                      qualifications=config.qualifications,
                      body=body, body_settled_at=start,
                      visible_task_ids=visible_tasks, known_task_ids=set(visible_tasks),
                      visible_message_ids=visible_mail, next_decision_at=start,
                      pending_wake_reasons=["run_start"], taste_weights=taste_weights.get(config.agent_id) or {},
                      inventory=config.initial_inventory)


def _run_manifest(semantics: ScenarioSemantics, scenario_sha256: str, identity: RunIdentity,
                  two_stage_identity: dict | None = None) -> dict:
    """@brief 绑定新运行的场景、内容、人物身份与两阶段请求形状。"""
    if two_stage_identity is not None:
        from worldmock.validation import validate_two_stage_model_identity
        validate_two_stage_model_identity(two_stage_identity)
    scenario = semantics.scenario
    category_fields = category_identity(semantics)
    return {"schema_version": 1, "run_id": identity.run_id, "source_group_id": identity.source_group_id,
            "scenario_id": scenario.scenario_id, "map_dataset": map_provenance(scenario),
            "scenario_sha256": scenario_sha256, "corpus_manifest_sha256": scenario.corpus_manifest_sha256,
            "agent_ids": list(identity.agent_ids), "code_sha256": identity.code_sha256,
            "model_identity": identity.model_identity.model_dump(mode="json"),
            "two_stage_model_identity": two_stage_identity,
            "window": {"start": int(scenario.start_at.timestamp()), "end": int(scenario.end_at.timestamp()),
                       "timezone": scenario.timezone}, **category_fields}


def _validate_run_identity(scenario: RuntimeScenario, identity: RunIdentity) -> None:
    """@brief 拒绝 runtime 身份与冻结场景窗口或人物不一致。"""
    expected_agents = tuple(sorted(item.agent_id for item in scenario.agents))
    if tuple(sorted(identity.agent_ids)) != expected_agents:
        raise IntegrityError("Run identity agent ids disagree with scenario")
    if identity.start_at != scenario.start_at.isoformat() or identity.end_at != scenario.end_at.isoformat():
        raise IntegrityError("Run identity window disagrees with scenario")
    if identity.timezone != scenario.timezone:
        raise IntegrityError("Run identity timezone disagrees with scenario")


def _put_activity(state: WorldState, agent, activity) -> WorldState:
    """@brief 将人物和活动一起投影到世界副本。"""
    agents = dict(state.agents)
    agents[agent.agent_id] = agent
    activities = dict(state.activities)
    activities[activity.activity_id] = activity
    return state.model_copy(update={"agents": agents, "activities": activities}, deep=True)


def _activity_origin(state: WorldState, pending: PendingAction) -> ActivityOrigin:
    """@brief 同一模型动作内给每条活动分配独立身份，保留共同的原动作归因。"""
    ordinal = 1 + sum(item.origin_action_id == pending.action_id for item in state.activities.values())
    return ActivityOrigin(pending.action_id, pending.decision_id, pending.requested_event_id, ordinal)


def _put_agent(state: WorldState, agent) -> WorldState:
    """@brief 将人物投影到世界副本。"""
    agents = dict(state.agents)
    agents[agent.agent_id] = agent
    return state.model_copy(update={"agents": agents}, deep=True)


def _put_agent_and_defer_game_turn(state: WorldState, agent) -> WorldState:
    """@brief 保存人物，并把本秒到期的游戏回合顺延一秒：防止达到同刻上限后游戏回合让人物在同一秒再就绪。"""
    agents = dict(state.agents)
    agents[agent.agent_id] = agent
    activities = dict(state.activities)
    for activity in active_activities(state, agent.agent_id):
        if activity.kind == ActivityKind.PLAY_GAME and activity.next_interaction_at == state.current_time:
            activities[activity.activity_id] = activity.model_copy(
                update={"next_interaction_at": state.current_time + 1}, deep=True)
    return state.model_copy(update={"agents": agents, "activities": activities}, deep=True)


def _request_sha(tool_name: str, request) -> str:
    """@brief 对工具名和规范化 Pydantic 请求计算稳定身份。"""
    payload = {"tool_name": tool_name, "request": request.model_dump(mode="json")}
    content = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(content).hexdigest()


def _datetime(epoch: int, timezone_name: str) -> datetime:
    """@brief 将权威 UTC 秒转为场景时区的带时区日期时间。"""
    return datetime.fromtimestamp(epoch, ZoneInfo(timezone_name))


def _body_settlement_data(settlement) -> dict:
    """@brief 将有类型身体结算转换为可持久 JSON 证据。"""
    return {"before": settlement.before.model_dump(mode="json"),
            "after": settlement.after.model_dump(mode="json"),
            "duration_seconds": settlement.duration_seconds,
            "basal_energy_kcal": settlement.basal_energy_kcal,
            "extra_energy_kcal": settlement.extra_energy_kcal,
            "food_energy_kcal": settlement.food_energy_kcal,
            "food_efficiency": settlement.food_efficiency,
            "work_efficiency": settlement.work_efficiency}


def _resource_flows_data(agent_id: int, occurred_at: int) -> list[dict[str, Any]]:
    """@brief 取走本区间身体结算分录并重盖主体与时间后校验序列化（docs/design/05-recording-engine.md「事件正文与资源证据」）。

    body 结算函数以占位 owner 与零时间戳缓冲分录；引擎按事件归属重盖为
    agent:{id} 与事件模拟时间，序列化为 {resource, owner, direction, amount,
    cause}[] 经 validate_resource_flows fail-closed 校验。无分录的结算点
    返回空列表（事件不携带该字段）。
    """
    payload = [{"resource": stamped.resource, "owner": stamped.owner,
                "direction": stamped.direction, "amount": stamped.amount,
                "cause": stamped.cause}
               for stamped in (replace(entry, owner=f"agent:{agent_id}", occurred_at=occurred_at)
                               for entry in last_flows())]
    return validate_resource_flows(payload) if payload else []


def _activity_progress_phrase(activity) -> str:
    """@brief 活动进度短语（不含完整快照，观察合同禁完整活动快照）。"""
    remaining = max(0, activity.planned_duration_seconds - activity.elapsed_seconds)
    return f"还剩约{round(remaining / 60)}分钟"


def _paused_activity_control(activity) -> dict:
    """@brief 暂停活动的控制身份：activity_id 与剩余时长（不含完整快照）。"""
    remaining = max(0, activity.planned_duration_seconds - activity.elapsed_seconds)
    return {"activity_id": activity.activity_id, "kind": activity.kind.value,
            "remaining_seconds": remaining}


def _current_branch_event(events: list, manifest: dict):
    """@brief 定位属于当前运行且绑定当前父检查点的唯一分支创建事件。"""
    matches = [event for event in events if event.run_id == manifest.get("run_id")
               and event.event_type == "environment_event"
               and event.data.get("kind") == "branch_created"
               and event.data.get("parent_checkpoint_id") == manifest.get("parent_checkpoint_id")]
    return matches[0] if len(matches) == 1 else None


def _failure_draft(occurred_at: int, error: Exception | str, round_id: str | None) -> EventDraft:
    """@brief 形成可覆盖先前 run_ended 的致命运行失败证据。"""
    error_type = type(error).__name__ if isinstance(error, Exception) else "RuntimeError"
    return EventDraft(event_type="run_failed", occurred_at=occurred_at, visibility="system",
                      data={"error_type": error_type, "error": str(error), "round_id": round_id})


def _remaining_activity_energy(activity, agent, body) -> dict:
    """@brief 以暂停活动剩余墙钟时长计算当前恢复能量合同。"""
    remaining = max(0, activity.planned_duration_seconds - activity.elapsed_seconds)
    factors = activity_factors(activity, agent, body)
    cost = minimum_energy_cost(activity.kind, remaining, body, factors.met)
    feasible = activity.kind in ENERGY_RECOVERY_ACTIVITY_KINDS or cost <= agent.body.energy_kcal
    return {"remaining_duration_seconds": remaining, "minimum_energy_cost_kcal": cost,
            "resume_energy_feasible": feasible}
