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

from worldmock.catalog.affordances import AffordanceView
from worldmock.corpora import load_manifest
from worldmock.errors import BusinessRuleError, IntegrityError, ProtocolError
from worldmock.footprints.models import (
    DECISION_OFFERED_FIELD,
    RESOURCE_FLOWS_FIELD,
    AssetRef,
    EventDraft,
    validate_resource_flows,
)
from worldmock.footprints.store import FootprintStore
from worldmock.life import felt, projection, social_actions
from worldmock.life.decision_start import (defer_decision_starts, flush_decision_starts,
                                            maybe_monologue_start, pop_decision_start)
from worldmock.life.activities import (
    ActivityAdvance,
    ActivityOrigin,
    activity_factors,
    advance_activity,
    change_activity,
    continue_activity,
    schedule_feedback,
    start_activity,
    truncate_activity,
)
from worldmock.life.basket import (
    apply_meal_progress,
    change_inventory,
    held_quantity,
    meal_duration,
    meal_food_rate,
    purchase_order,
    validate_meal,
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
)
from worldmock.life.content import (
    LifestyleItem,
    SubtitleCue,
    load_lifestyle_items,
    mail_headers,
    reveal_mail,
    reveal_watch,
)
from worldmock.life.game import (
    GameOutcomeUnknown,
    new_game_session,
    run_game_command,
    validate_game,
    verify_game_progress,
)
from worldmock.life.models import (
    ActionReceipt,
    ActionResult,
    ActionStatus,
    ActivityChange,
    ActivityKind,
    ActivityStatus,
    BrowseActivitiesRequest,
    ChangePlaybackSpeedRequest,
    ChangeActivityRequest,
    ContinueActivityRequest,
    ContentProgress,
    ConsumeRequest,
    CounterfactualIntervention,
    CounterfactualReceipt,
    DecisionRoundState,
    GameActionRequest,
    LIFESTYLE_TYPE_PREFIX,
    MeetingMinutes,
    PendingAction,
    PlanDayRequest,
    PlanSegmentInput,
    PurchaseOrderRequest,
    REQUEST_MODELS,
    ReviseDayPlanRequest,
    RunIdentity,
    SendMessageRequest,
    SetActivityStateRequest,
    StartBasicActivityRequest,
    StartActivityRequest,
    StartSocialActivityRequest,
    StartTargetedActivityRequest,
    StartTravelRequest,
    StoredAction,
    SubmitWorkRequest,
    WorldState,
    WORLD_SCHEMA_VERSION,
)
from worldmock.life.observe import ObservationMixin
from worldmock.life.rhythm import (
    apply_engine_rhythm,
    engine_rhythm_inputs,
    next_decision_candidates,
)
from worldmock.life.rules import LifeRuleEngine
from worldmock.life.scenario import RuntimeScenario
from worldmock.life.schedule import DayPlan, PlanSegment, WakeReason, revise_day_plan
from worldmock.life.semantics import ScenarioSemantics, load_runtime_scenario
from worldmock.life.settlement import inject_initial_funds
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

#: 生活动作工具到操作日志组件的映射，未列出的工具归属 engine。
_OPS_COMPONENTS = {"purchase": "commerce", "start_travel": "travel", "submit_work": "review"}

#: 反馈边界上可直接提交并自动暂停当前活动的切换工具闭集。
_SWITCH_TOOL_NAMES = frozenset({
    "start_basic_activity", "start_targeted_activity", "start_travel"})

#: 观察合同条目的七键闭集（LifeAgent 侧严格校验同一形状）。
AFFORDANCE_CONTRACT_KEYS = frozenset({
    "surface", "target_discovery", "target_field", "energy_projection",
    "feedback_eligible", "continuable", "game_boundary"})

#: 预排事件种类 → 决策节律唤醒旗标（到达类事件在处理 tick 标记对应人物）。
_SCHEDULED_WAKE_FLAGS = {
    "delivery_arrival": "delivery_arrival",
    "delivery_ready": "delivery_arrival",
    "mail_arrival": "message_arrival",
    "work_available": "task_available",
}


def affordance_contract(view: AffordanceView) -> dict[str, dict]:
    """@brief 从可供性派生视图构造观察合同冻结块（设计文档 §三 L4）。

    合同随场景组装一次性生成、运行期不变：每个 kind 携带
    {surface, target_discovery, target_field, energy_projection,
    feedback_eligible, continuable, game_boundary} 七键；target_field 仅在
    catalog_field 直读时为 [观察字段, 身份键]，否则为 None。continuable 采用
    M01 裁决口径（= feedback 闭集；play_game 的继续交互走 game_boundary）。
    """
    feedback = view.feedback_kinds
    continuable = view.continuable_kinds
    game = view.game_boundary_kinds
    contract: dict[str, dict] = {}
    for name, spec in view.kinds.items():
        contract[name] = {
            "surface": spec.surface,
            "target_discovery": spec.target_discovery,
            "target_field": ([spec.target_field, spec.target_id_key]
                             if spec.target_discovery == "catalog_field" else None),
            "energy_projection": spec.energy_projection,
            "feedback_eligible": name in feedback,
            "continuable": name in continuable,
            "game_boundary": name in game,
        }
    return contract


class LifeEngine(ObservationMixin):
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
        self._work_reviewer: WorkReviewer | None = None
        self.ops = OpsLogger(store.run_dir, self.scenario.timezone)
        self._validate_runtime()

    @classmethod
    def create(cls, scenario_path: Path, run_dir: Path, run_identity: RunIdentity) -> "LifeEngine":
        """@brief 从严格场景新建唯一运行，拒绝覆盖已有工件。"""
        semantics, digest = load_runtime_scenario(scenario_path)
        scenario = semantics.scenario
        corpus_root = (Path(scenario_path).resolve().parent / scenario.corpus_root).resolve()
        load_manifest(corpus_root, scenario.corpus_manifest_sha256)
        _validate_run_identity(scenario, run_identity)
        state = _initial_state(semantics, digest, run_identity.run_id)
        manifest = _run_manifest(scenario, digest, run_identity)
        store = FootprintStore.create(Path(run_dir), manifest, state.model_dump(mode="json"))
        changes = {f"account:{key}": value for key, value in state.accounts.items()}
        changes["account:external:scenario"] = -sum(state.accounts.values())
        injection = EventDraft(event_type="environment_event", occurred_at=state.current_time,
                               visibility="evaluation", source_kind="scenario",
                               data={"kind": "initial_funds_injected", "resource_changes": changes})
        store.commit(state.model_dump(mode="json"), [injection])
        return cls(semantics, digest, corpus_root, store)

    @classmethod
    def open(cls, scenario_path: Path, run_dir: Path) -> "LifeEngine":
        """@brief 只打开已有运行并校验场景和内容身份。"""
        semantics, digest = load_runtime_scenario(scenario_path)
        corpus_root = (Path(scenario_path).resolve().parent
                       / semantics.scenario.corpus_root).resolve()
        load_manifest(corpus_root, semantics.scenario.corpus_manifest_sha256)
        return cls(semantics, digest, corpus_root, FootprintStore(Path(run_dir)))

    def observe(self, context: "DecisionContext") -> dict:
        """@brief 返回人物当下可见事实，并以同版本追加观察事件。"""
        self._validate_context(context)
        agent = self.state.agents[context.agent_id]
        data = self._observation_data(agent)
        draft = EventDraft(event_type="observation", occurred_at=self.state.current_time,
                           agent_id=agent.agent_id, decision_id=context.decision_id,
                           observed_at=self.state.current_time, data=data)
        event = self.store.commit(self.state.model_dump(mode="json"), [draft])[0]
        data["observation_event_id"] = event.event_id
        return data

    def begin_decision_round(self, contexts: list["DecisionContext"]) -> None:
        """@brief 持久化共同观察顺序；decision_started 延迟到独白可得时一次写完整（L1562-1573）。"""
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
        if self.state.agents[pending.agent_id].current_activity_id is not None:
            return
        existing = [event for event in self.store.read_agent_events("model_request", pending.agent_id)
                    if event.action_id == pending.action_id and event.data.get("purpose") == "work_review"]
        if existing:
            raise ProtocolError("Pending work review cannot restart after model evidence was recorded")
        request = SubmitWorkRequest.model_validate(pending.request)
        try:
            submission = prepare_work_submission(
                self.state, self.scenario, self.corpus_root, pending.agent_id, request)
        except BusinessRuleError:
            return
        model_name = self.store.manifest["model_identity"]["requested_model"]
        original = prepare_work_review(submission, model_name)
        prepared = original
        previous_event_id = None
        for attempt in range(1, WORK_REVIEW_MAX_MODEL_CALLS + 1):
            call_id = f"{pending.decision_id}:work-review:{pending.action_id}:attempt:{attempt}"
            request_link = (call_id, attempt, previous_event_id)
            request_event = self.store.commit(
                self.state.model_dump(mode="json"),
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
            self.store.commit(self.state.model_dump(mode="json"), [draft])
            raise
        except Exception as error:
            logging.exception("Work review model request failed")
            draft = _work_review_failure_draft(
                self.state.current_time, pending, link, error)
            self.store.commit(self.state.model_dump(mode="json"), [draft])
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
            self.store.commit(self.state.model_dump(mode="json"), [draft])
            raise
        draft = _work_review_response_draft(
            self.state.current_time, pending, link, (raw, decision, error))
        event = self.store.commit(self.state.model_dump(mode="json"), [draft])[0]
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
        event = self.store.commit(self.state.model_dump(mode="json"), [
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
        """@brief 按冻结顺序结算完整决策轮并一次提交所有状态与结果。"""
        current = self._require_round(round_id)
        submitted = {item.agent_id for item in current.pending_actions}
        if submitted != set(current.expected_agent_ids):
            raise ProtocolError("Decision round does not contain every expected agent")
        staged = self.state.model_copy(deep=True)
        drafts: list[EventDraft] = flush_decision_starts(self._pending_decision_starts, self.state.current_time)
        results = []
        for pending in sorted(current.pending_actions, key=lambda item: item.order):
            staged, result, action_drafts = self._resolve(staged, pending)
            self._ops_action(pending, result)
            results.append(result)
            drafts.extend(action_drafts)
            if not pending.reused:
                staged = _remember_action(staged, pending, result)
        staged = self._snapshot_decision_references(staged, list(current.expected_agent_ids))
        staged.version = self.state.version + 1
        events = self.store.commit(staged.model_dump(mode="json"), drafts)
        self.state = staged
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
        component = _OPS_COMPONENTS.get(pending.tool_name, "engine")
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
        self.store.commit(staged.model_dump(mode="json"), drafts)
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
        event = self.store.commit(self.state.model_dump(mode="json"), [draft])[0]
        if exchange.phase == "request":
            self._model_request_events[key] = event.event_id
        else:
            self._model_request_events.pop(key)
        self._ops_exchange(context, exchange)
        start = maybe_monologue_start(self._pending_decision_starts, context.agent_id,
                                      self.state.current_time, exchange)
        if start is not None:
            self.store.commit(self.state.model_dump(mode="json"), [start])

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
        hashes = (evidence.memory_sha256, evidence.episodes_sha256, evidence.state_sha256)
        if (evidence.operation != "append_episodes" or not evidence.episode_ids
                or len(set(evidence.episode_ids)) != len(evidence.episode_ids)
                or any(not _is_sha256(value) for value in hashes)):
            raise ProtocolError("Memory update evidence is invalid")
        data = {"operation": evidence.operation, "episode_ids": list(evidence.episode_ids),
                "memory_sha256": evidence.memory_sha256, "episodes_sha256": evidence.episodes_sha256,
                "state_sha256": evidence.state_sha256, "consolidated": evidence.consolidated}
        draft = EventDraft(event_type="memory_updated", occurred_at=self.state.current_time,
                           agent_id=evidence.context.agent_id,
                           decision_id=evidence.context.decision_id,
                           visibility="system", source_kind="environment", data=data)
        self.store.commit(self.state.model_dump(mode="json"), [draft])
        self.ops.log(self.state.current_time, "memory", "info", "MEMORY_UPDATED",
                     f"Memory update appended {len(evidence.episode_ids)} episodes",
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
        staged = self.state.model_copy(deep=True)
        drafts, results = self._advance_activities(staged, target_epoch)
        staged, background, arrivals = self._process_scheduled(staged, target_epoch)
        drafts.extend(background)
        staged.current_time = target_epoch
        staged = apply_engine_rhythm(self, staged, target_epoch, interval, arrivals, drafts)
        if not drafts:
            drafts.append(EventDraft(event_type="environment_event", occurred_at=target_epoch,
                                     visibility="system", source_kind="environment",
                                     data={"kind": "time_advanced", "interval_start": self.state.current_time,
                                           "interval_end": target_epoch}))
        if target_epoch == staged.end_time:
            drafts.extend(self._truncate(staged, target_epoch))
            drafts.append(EventDraft(event_type="run_ended", occurred_at=target_epoch,
                                     visibility="system", data={"end_time": target_epoch}))
        staged.version = self.state.version + 1
        self.store.commit(staged.model_dump(mode="json"), drafts)
        self.state = staged
        return results

    def fail_run(self, error: str) -> None:
        """@brief 追加唯一 run_failed，使后续故障覆盖先前完成候选。"""
        if not error:
            raise ProtocolError("Run failure error must not be empty")
        events = self.store.read_events(self.store.last_event_seq)
        if any(event.event_type == "run_failed" for event in events):
            return
        self.store.commit(self.state.model_dump(mode="json"),
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
        event = self.store.commit(staged.model_dump(mode="json"), [draft])[0]
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
        committed = self.store.checkpoint(self.state.model_dump(mode="json"), snapshots)
        self.ops.log(self.state.current_time, "checkpoint", "info", "CHECKPOINT_COMMITTED",
                     f"Checkpoint {committed.checkpoint_id} committed",
                     event_seq_last=committed.last_event_seq)
        return committed

    def close(self) -> None:
        """@brief 关闭当前运行的唯一存储连接。"""
        self.store.close()
        self.ops.close()

    def _validate_runtime(self) -> None:
        """@brief 在运行前全量核对场景、内容、路线、游戏和状态身份。"""
        if self.state.scenario_sha256 != self.scenario_sha256:
            raise IntegrityError("Runtime scenario identity mismatch")
        if self.state.content_manifest_sha256 != self.scenario.corpus_manifest_sha256:
            raise IntegrityError("Runtime corpus identity mismatch")
        if self.state.run_id != self.store.manifest["run_id"]:
            raise IntegrityError("Runtime run identity mismatch")
        validate_routes(self.scenario, self.corpus_root)
        for config in self.scenario.games:
            validate_game(config, self.corpus_root)
        for agent in self.state.agents.values():
            for progress in agent.game_sessions.values():
                verify_game_progress(progress, self.rules.game(progress.game_id), self.corpus_root)

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
            changed = self._after_success(changed, pending.agent_id)
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
            activity = changed.activities.get(agent.current_activity_id or "")
            data = {"error_code": str(error), **result.data}
            if activity is not None:
                data["activity"] = self._activity_snapshot(activity)
            rejected = EventDraft(event_type="action_rejected", occurred_at=state.current_time,
                                  agent_id=pending.agent_id, decision_id=pending.decision_id,
                                  action_id=pending.action_id,
                                  activity_id=activity.activity_id if activity else None,
                                  caused_by=_pending_causes(pending), data=data)
            drafts = [rejected, _result_draft(pending, result, "action_result")]
            if result.status == ActionStatus.FAILED:
                drafts.extend(_decision_failure_drafts(state.current_time, pending, result, agent))
            drafts.extend(self._drain_pending_drafts())
            return changed, result, drafts
        except GameOutcomeUnknown as error:
            result = ActionResult(action_id=pending.action_id, agent_id=pending.agent_id,
                                  status=ActionStatus.UNKNOWN, occurred_at=state.current_time,
                                  error_code="GAME_OUTCOME_UNKNOWN", message=str(error))
            return self._after_unknown(state, pending.agent_id), result, [
                _result_draft(pending, result, "action_result")]

    def _after_success(self, state: WorldState, agent_id: int) -> WorldState:
        """@brief 成功选择重置拒绝计数并按节律计算下一次合法决策时刻。"""
        agent = state.agents[agent_id]
        activity = state.activities.get(agent.current_activity_id or "")
        if activity is not None and activity.kind == ActivityKind.PLAY_GAME:
            # 游戏保持逐回合反馈（L1473）：交互边界到期即提供决策。
            next_at = activity.next_interaction_at or activity.planned_end_at
        else:
            candidate = next_decision_candidates(agent, engine_rhythm_inputs(self, state))
            # 修订决策节律：下一决策时刻只由唤醒候选决定（计划结束、体感分级
            # 跨越、排定通知、计划段起点、重估间隔、营业变化）；900 秒反馈
            # 边界只是结算粒度，不再构成决策时刻。
            next_at = candidate if candidate is not None else (
                state.current_time + self.scenario.activity_feedback_seconds)
        changed = agent.model_copy(update={"next_decision_at": next_at,
                                           "consecutive_rejections": 0}, deep=True)
        agents = dict(state.agents)
        agents[agent_id] = changed
        return state.model_copy(update={"agents": agents}, deep=True)

    def _after_rejection(self, state: WorldState, pending: PendingAction,
                         error: BusinessRuleError) -> tuple[WorldState, ActionResult]:
        """@brief 业务拒绝形成一秒反应边界，达到上限时形成致命结果。"""
        agent = state.agents[pending.agent_id]
        count = agent.consecutive_rejections + 1
        failed = count >= self.scenario.max_consecutive_rejections
        pulse_key = ("work_review_returned" if str(error) == "WORK_REQUIRES_REWORK"
                     else "service_refused")
        body = apply_mood_pulse(agent.body, pulse_key)
        changed_agent = agent.model_copy(update={"next_decision_at": state.current_time + 1,
                                                  "consecutive_rejections": count,
                                                  "decision_failed": failed,
                                                  "body": body}, deep=True)
        changed = _put_agent_and_defer_feedback(state, changed_agent)
        status = ActionStatus.FAILED if failed else ActionStatus.REJECTED
        message = "Agent decision failed after consecutive rejections" if failed else "Action rejected"
        data = {"consecutive_rejections": count,
                "maximum_rejections": self.scenario.max_consecutive_rejections}
        if str(error) == "WORK_REQUIRES_REWORK":
            data["work_review"] = self._public_work_review(pending)
            if not failed:
                message = "Work submission requires rework"
        result = ActionResult(action_id=pending.action_id, agent_id=pending.agent_id,
                              status=status, occurred_at=state.current_time,
                              error_code=str(error), message=message,
                              data=data)
        return changed, result

    def _public_work_review(self, pending: PendingAction) -> dict:
        """@brief 从本次系统评审生成可随动作结果公开的人物反馈。"""
        if pending.work_review is None:
            raise ProtocolError("Rejected work submission has no recorded semantic review")
        request = SubmitWorkRequest.model_validate(pending.request)
        task = self.rules.task(request.task_id)
        source = load_work_input(self.corpus_root, task)
        review = WorkReviewDecision.model_validate(pending.work_review)
        return public_work_review(review, source)

    def _after_unknown(self, state: WorldState, agent_id: int) -> WorldState:
        """@brief 未知游戏结果保留拒绝计数并设置下一秒重试边界。"""
        agent = state.agents[agent_id]
        changed = agent.model_copy(update={"next_decision_at": state.current_time + 1}, deep=True)
        return _put_agent_and_defer_feedback(state, changed)

    def _reused_result(self, state: WorldState,
                       pending: PendingAction) -> tuple[WorldState, ActionResult, list[EventDraft]]:
        """@brief 返回持久终态结果并只记录本次重放证据。"""
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
        agent = state.agents[pending.agent_id].model_copy(
            update={"next_decision_at": state.current_time + 1}, deep=True)
        changed = _put_agent_and_defer_feedback(state, agent)
        return changed, result, [first, second]

    def _dispatch(self, state: WorldState, pending: PendingAction) -> tuple[WorldState, ActionResult]:
        """@brief 依据闭集工具名进入对应领域转换。"""
        model = REQUEST_MODELS[pending.tool_name]
        request = model.model_validate(pending.request)
        self._require_boundary_action(state, pending, request)
        handlers = {"start_basic_activity": self._start_basic,
                    "start_targeted_activity": self._start_targeted,
                    "start_travel": self._start_travel,
                    "set_activity_state": self._set_activity_state,
                    "change_playback_speed": self._change_playback_speed,
                    "continue_activity": self._continue,
                    "purchase": self._purchase, "consume": self._consume,
                    "submit_work": self._submit_work, "game_action": self._game_action,
                    "browse_activities": self._browse_activities,
                    "plan_day": self._plan_day, "revise_day_plan": self._revise_day_plan,
                    "send_message": self._send_message,
                    "start_social_activity": self._start_social_activity}
        return handlers[pending.tool_name](state, pending, request)

    def _require_boundary_action(self, state: WorldState, pending: PendingAction, request) -> None:
        """@brief 活动反馈到期时只接受显式边界动作或直接切换新活动。"""
        agent = state.agents[pending.agent_id]
        if agent.current_activity_id is None:
            return
        activity = state.activities[agent.current_activity_id]
        if activity.next_interaction_at != state.current_time:
            raise BusinessRuleError("RESOURCE_CONFLICT")
        if (pending.tool_name in _SWITCH_TOOL_NAMES
                and activity.boundary_type == "feedback"):
            return
        expected_id = getattr(request, "activity_id", None)
        if activity.kind == ActivityKind.PLAY_GAME:
            allowed = {"game_action", "set_activity_state"}
        elif activity.kind == ActivityKind.WATCH:
            allowed = {"continue_activity", "set_activity_state", "change_playback_speed"}
        else:
            allowed = {"continue_activity", "set_activity_state"}
        if pending.tool_name not in allowed:
            raise BusinessRuleError("ACTIVITY_FEEDBACK_ACTION_REQUIRED")
        if pending.tool_name != "game_action" and expected_id != activity.activity_id:
            raise BusinessRuleError("ACTIVITY_NOT_OWNED")

    def _start_basic(self, state: WorldState, pending: PendingAction,
                     request: StartBasicActivityRequest) -> tuple[WorldState, ActionResult]:
        """@brief 将显式基础启动请求转换为统一领域活动。"""
        kind, target_id = self._lifestyle_request(pending.agent_id, request.activity_type)
        activity = StartActivityRequest(activity_type=kind,
                                        planned_duration_seconds=request.planned_duration_seconds,
                                        target_id=target_id)
        return self._start(state, pending, activity)

    def _lifestyle_request(self, agent_id: int, activity_type: str):
        """@brief 解析 lifestyle:<id> 子类型并对照人物启用声明核对。"""
        if not activity_type.startswith(LIFESTYLE_TYPE_PREFIX):
            return ActivityKind(activity_type), None
        activity_id = activity_type[len(LIFESTYLE_TYPE_PREFIX):]
        if f"{LIFESTYLE_TYPE_PREFIX}{activity_id}" not in self.scenario.body.activity_met:
            raise BusinessRuleError("UNKNOWN_LIFESTYLE_ACTIVITY")
        config = next(item for item in self.scenario.agents if item.agent_id == agent_id)
        enabled = config.lifestyle_activities
        if enabled is not None and activity_id not in enabled:
            raise BusinessRuleError("UNKNOWN_LIFESTYLE_ACTIVITY")
        return ActivityKind.LIFESTYLE, activity_id

    def _start_targeted(self, state: WorldState, pending: PendingAction,
                        request: StartTargetedActivityRequest) -> tuple[WorldState, ActionResult]:
        """@brief 将显式目标活动请求转换为统一领域活动。"""
        activity = StartActivityRequest(**request.model_dump(mode="python"))
        return self._start(state, pending, activity)

    def _start_travel(self, state: WorldState, pending: PendingAction,
                      request: StartTravelRequest) -> tuple[WorldState, ActionResult]:
        """@brief 将显式出行请求转换为统一领域活动。"""
        activity = StartActivityRequest(activity_type=ActivityKind.TRAVEL,
                                        planned_duration_seconds=request.planned_duration_seconds,
                                        destination_id=request.destination_id,
                                        travel_mode=request.travel_mode)
        return self._start(state, pending, activity)

    def _set_activity_state(self, state: WorldState, pending: PendingAction,
                            request: SetActivityStateRequest) -> tuple[WorldState, ActionResult]:
        """@brief 将显式活动状态请求转换为统一领域变更。"""
        change = ChangeActivityRequest(activity_id=request.activity_id, operation=request.operation)
        return self._change(state, pending, change)

    def _change_playback_speed(self, state: WorldState, pending: PendingAction,
                               request: ChangePlaybackSpeedRequest) -> tuple[WorldState, ActionResult]:
        """@brief 将显式播放速度请求转换为统一领域变更。"""
        change = ChangeActivityRequest(activity_id=request.activity_id,
                                       operation="change_speed",
                                       playback_speed=request.playback_speed)
        return self._change(state, pending, change)

    def _auto_pause_for_switch(self, state: WorldState,
                               agent) -> tuple[WorldState, Any]:
        """@brief 反馈边界上直接切换时自动暂停当前活动并保留进度。"""
        current = state.activities[agent.current_activity_id]
        change = ChangeActivityRequest(activity_id=current.activity_id,
                                       operation=ActivityChange.PAUSE)
        agent, paused = change_activity(agent, current, change, state.current_time,
                                        self.scenario.activity_feedback_seconds)
        return _put_activity(state, agent, paused), paused

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
        title = self._activity_title(activity)
        if title is not None:
            payload["title"] = title
        return payload

    def _start(self, state: WorldState, pending: PendingAction,
               request: StartActivityRequest) -> tuple[WorldState, ActionResult]:
        """@brief 启动普通活动并初始化内容、路线或游戏身份。"""
        agent = state.agents[pending.agent_id]
        data = self.rules.validate_start(agent, request, state.current_time)
        if request.activity_type == ActivityKind.WORK:
            validate_work_duration(
                state, self.rules.task(request.target_id), pending.agent_id,
                request.planned_duration_seconds)
        paused_activity = None
        if agent.current_activity_id is not None:
            state, paused_activity = self._auto_pause_for_switch(state, agent)
            agent = state.agents[pending.agent_id]
        origin = ActivityOrigin(pending.action_id, pending.decision_id, pending.requested_event_id)
        agent, activity = start_activity(agent, request, state.current_time, origin,
                                         self.semantics.body)
        activity = schedule_feedback(activity, state.current_time, self.scenario.activity_feedback_seconds)
        changed_state = state
        payment = None
        if request.activity_type == ActivityKind.TRAVEL:
            route = route_for(self.scenario, agent.location_id, request)
            expected = self.semantics.travel_planned_seconds(
                route.mode.value, route.duration_seconds, state.current_time)
            if request.planned_duration_seconds != expected:
                raise BusinessRuleError("ROUTE_DURATION_MISMATCH")
            changed_state, payment = pay_travel_fare(state, self.scenario, pending.agent_id, route)
            data.update(travel_payment=payment, resource_changes=payment["resource_changes"])
        if request.activity_type == ActivityKind.PLAY_GAME:
            session_id = f"game-{pending.action_id}"
            progress = new_game_session(session_id, self.rules.game(request.target_id), self.corpus_root)
            sessions = dict(agent.game_sessions)
            sessions[session_id] = progress
            agent = agent.model_copy(update={"game_sessions": sessions}, deep=True)
            data.update(game_session_id=session_id)
            activity.next_interaction_at = state.current_time + 1
            activity.boundary_type = "game_feedback"
        activity.data.update(data)
        if request.activity_type == ActivityKind.WATCH:
            activity.media_position_seconds = data["start_position_seconds"]
        changed = _put_activity(changed_state, agent, activity)
        result_data = {"activity": self._activity_snapshot(activity)}
        if paused_activity is not None:
            result_data["auto_paused_activity"] = self._activity_snapshot(paused_activity)
        if payment is not None:
            result_data.update(travel_payment=payment, resource_changes=payment["resource_changes"])
        result = ActionResult(action_id=pending.action_id, agent_id=agent.agent_id,
                              status=ActionStatus.IN_PROGRESS, occurred_at=state.current_time,
                              activity_id=activity.activity_id, message="Activity started", data=result_data)
        return changed, result

    def _change(self, state: WorldState, pending: PendingAction,
                request: ChangeActivityRequest) -> tuple[WorldState, ActionResult]:
        """@brief 转换人物已有活动并保留已完成进度。"""
        if request.activity_id not in state.activities:
            raise BusinessRuleError("ACTIVITY_NOT_FOUND")
        agent = state.agents[pending.agent_id]
        current = state.activities[request.activity_id]
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
            feasibility = _remaining_activity_energy(current, agent, self.semantics.body)
            if not feasibility["resume_energy_feasible"]:
                raise BusinessRuleError("INSUFFICIENT_ENERGY")
        changed = _put_activity(state, agent, activity)
        result = ActionResult(action_id=pending.action_id, agent_id=agent.agent_id,
                              status=ActionStatus.COMPLETED, occurred_at=state.current_time,
                              activity_id=activity.activity_id, message="Activity changed",
                              data={"activity": self._activity_snapshot(activity)})
        return changed, result

    def _continue(self, state: WorldState, pending: PendingAction,
                  request: ContinueActivityRequest) -> tuple[WorldState, ActionResult]:
        """@brief 在反馈边界确认保持连续活动并安排下一严格未来边界。"""
        if request.activity_id not in state.activities:
            raise BusinessRuleError("ACTIVITY_NOT_FOUND")
        agent = state.agents[pending.agent_id]
        agent, activity = continue_activity(
            agent, state.activities[request.activity_id], request, state.current_time,
            self.scenario.activity_feedback_seconds)
        changed = _put_activity(state, agent, activity)
        result = ActionResult(action_id=pending.action_id, agent_id=agent.agent_id,
                              status=ActionStatus.COMPLETED, occurred_at=state.current_time,
                              activity_id=activity.activity_id, message="Activity continued",
                              data={"activity": self._activity_snapshot(activity)})
        return changed, result

    def _purchase(self, state: WorldState, pending: PendingAction,
                  request: PurchaseOrderRequest) -> tuple[WorldState, ActionResult]:
        """@brief 结算整单购买：一次付款、同一配送、任一件不可购整单拒绝。"""
        agent = state.agents[pending.agent_id]
        effective = set(projection.visible_products(state, agent, self.scenario))
        invisible = [item.product_id for item in request.items
                     if item.product_id not in effective]
        if invisible:
            raise BusinessRuleError("PRODUCT_NOT_VISIBLE")
        outcome = purchase_order(state, self.scenario, pending.agent_id, request)
        account = agent.balance_account_id
        merchant = self.rules.products[request.items[0].product_id].merchant_account_id
        result = ActionResult(action_id=pending.action_id, agent_id=pending.agent_id,
                              status=ActionStatus.COMPLETED, occurred_at=state.current_time,
                              message="Purchase accepted",
                              data={"orders": [order.model_dump(mode="json") for order in outcome.orders],
                                    "total_price_fen": outcome.total_price_fen,
                                    "resource_changes": {
                                        f"account:{account}": -outcome.total_price_fen,
                                        f"account:{merchant}": outcome.total_price_fen}})
        return outcome.state, result

    def _consume(self, state: WorldState, pending: PendingAction,
                 request: ConsumeRequest) -> tuple[WorldState, ActionResult]:
        """@brief 启动一次多件进食或单件使用活动（L1673：时长取最长、效果累加）。"""
        agent = state.agents[pending.agent_id]
        products = self.rules.products
        validate_meal(agent, products, request)
        duration = meal_duration(products, request)
        start = StartActivityRequest(activity_type=ActivityKind.CONSUME,
                                     planned_duration_seconds=duration,
                                     target_id=request.items[0].product_id)
        origin = ActivityOrigin(pending.action_id, pending.decision_id, pending.requested_event_id)
        agent, activity = start_activity(agent, start, state.current_time, origin,
                                         self.semantics.body)
        activity = schedule_feedback(activity, state.current_time, self.scenario.activity_feedback_seconds)
        from worldmock.life import sleep as sleep_module
        efficiency = sleep_module.food_utilization_efficiency(agent.body.sleep_deficit_hours)
        food_rate = meal_food_rate(products, request) * efficiency
        activity.data.update(request=request.model_dump(mode="json"), consumed={},
                             food_energy_kcal_per_hour=food_rate)
        changed = _put_activity(state, agent, activity)
        result = ActionResult(action_id=pending.action_id, agent_id=pending.agent_id,
                              status=ActionStatus.IN_PROGRESS, occurred_at=state.current_time,
                              activity_id=activity.activity_id, message="Product use started",
                              data={"activity": self._activity_snapshot(activity)})
        return changed, result

    def _submit_work(self, state: WorldState, pending: PendingAction,
                     request: SubmitWorkRequest) -> tuple[WorldState, ActionResult]:
        """@brief 独立验收结构化工作并支付唯一报酬。"""
        submission = prepare_work_submission(state, self.scenario, self.corpus_root,
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
                "resource_changes": {
                    f"account:{self.rules.task(request.task_id).employer_account_id}": -outcome.payment_fen,
                    f"account:{state.agents[pending.agent_id].balance_account_id}": outcome.payment_fen}}
        result = ActionResult(action_id=pending.action_id, agent_id=pending.agent_id,
                              status=ActionStatus.COMPLETED, occurred_at=state.current_time,
                              message="Work accepted and paid", data=data)
        return outcome_state, result

    def _game_action(self, state: WorldState, pending: PendingAction,
                     request: GameActionRequest) -> tuple[WorldState, ActionResult]:
        """@brief 在当前主动游戏活动中执行一个真实 TextWorld 命令。"""
        agent = state.agents[pending.agent_id]
        activity = state.activities.get(agent.current_activity_id or "")
        if not activity or activity.kind != ActivityKind.PLAY_GAME:
            raise BusinessRuleError("GAME_ACTIVITY_NOT_ACTIVE")
        if activity.data.get("game_session_id") != request.game_session_id:
            raise BusinessRuleError("GAME_SESSION_NOT_ACTIVE")
        if activity.next_interaction_at is not None and activity.next_interaction_at > state.current_time:
            raise BusinessRuleError("GAME_COMMAND_TOO_EARLY")
        progress = agent.game_sessions[request.game_session_id]
        outcome = run_game_command(progress, request.command, self.rules.game(progress.game_id), self.corpus_root)
        changed, activity = self._store_game_outcome(state, agent, activity, outcome)
        result = ActionResult(action_id=pending.action_id, agent_id=pending.agent_id,
                              status=ActionStatus.COMPLETED, occurred_at=state.current_time,
                              activity_id=activity.activity_id, message="Game command executed",
                              data={"reward": outcome.reward, "done": outcome.done,
                                    "game": outcome.progress.model_dump(mode="json"),
                                    "activity": self._activity_snapshot(activity)})
        return changed, result

    def _store_game_outcome(self, state, agent, activity, outcome):
        """@brief 保存游戏公开状态并设置下一交互或真实终局。"""
        sessions = dict(agent.game_sessions)
        sessions[outcome.progress.session_id] = outcome.progress
        updates: dict[str, Any] = {"next_interaction_at": state.current_time + 60}
        if outcome.done:
            updates.update(status=ActivityStatus.COMPLETED, ended_at=state.current_time,
                           boundary_type="game_terminal", next_interaction_at=None)
        changed_activity = activity.model_copy(update=updates, deep=True)
        current = None if outcome.done else agent.current_activity_id
        changed_agent = agent.model_copy(update={"game_sessions": sessions, "current_activity_id": current}, deep=True)
        return _put_activity(state, changed_agent, changed_activity), changed_activity

    def _plan_day(self, state: WorldState, pending: PendingAction,
                  request: PlanDayRequest) -> tuple[WorldState, ActionResult]:
        """@brief 登记当日日程骨架：人物自我承诺，环境不执行计划步（L1606-1609）。"""
        agent = state.agents[pending.agent_id]
        now = state.current_time
        first_awake = ({"run_start", "wake_from_sleep"} & set(agent.pending_wake_reasons)
                       or agent.last_decision_time is None)
        if not first_awake:
            raise BusinessRuleError("PLAN_DAY_ONLY_AT_DAY_START")
        date = _datetime(now, self.scenario.timezone).date().isoformat()
        if date in agent.plan_submitted_dates:
            raise BusinessRuleError("PLAN_ALREADY_SUBMITTED")
        day_end = _day_end(now, self.scenario.timezone)
        try:
            segments = tuple(PlanSegment(**segment.model_dump()) for segment in request.segments)
            plan = DayPlan(bedtime=segments[-1].end, day_end=day_end, segments=segments)
        except ValueError as error:
            raise BusinessRuleError("PLAN_SEGMENTS_INVALID") from error
        if plan.segments[0].start < now:
            raise BusinessRuleError("PLAN_SEGMENT_ALREADY_STARTED")
        changed_agent = agent.model_copy(
            update={"day_plan": plan, "plan_submitted_dates": set(agent.plan_submitted_dates) | {date}},
            deep=True)
        changed = _put_agent(state, changed_agent)
        result = ActionResult(action_id=pending.action_id, agent_id=pending.agent_id,
                              status=ActionStatus.COMPLETED, occurred_at=now,
                              message="Day plan registered as self-commitment",
                              data={"day_plan": [segment.model_dump(mode="json")
                                                 for segment in plan.segments]})
        return changed, result

    def _revise_day_plan(self, state: WorldState, pending: PendingAction,
                         request: ReviseDayPlanRequest) -> tuple[WorldState, ActionResult]:
        """@brief 改写尚未开始的当日计划段（L1611：已开始段不可改）。"""
        agent = state.agents[pending.agent_id]
        if agent.day_plan is None:
            raise BusinessRuleError("PLAN_NOT_FOUND")
        try:
            segments = tuple(PlanSegment(**segment.model_dump()) for segment in request.segments)
            plan = revise_day_plan(agent.day_plan, state.current_time, segments)
        except ValueError as error:
            raise BusinessRuleError("PLAN_SEGMENT_ALREADY_STARTED" if "already started" in str(error)
                                    else "PLAN_SEGMENTS_INVALID") from error
        changed_agent = agent.model_copy(update={"day_plan": plan}, deep=True)
        changed = _put_agent(state, changed_agent)
        result = ActionResult(action_id=pending.action_id, agent_id=pending.agent_id,
                              status=ActionStatus.COMPLETED, occurred_at=state.current_time,
                              message="Day plan revised",
                              data={"day_plan": [segment.model_dump(mode="json")
                                                 for segment in plan.segments]})
        return changed, result

    def _send_message(self, state: WorldState, pending: PendingAction,
                      request: SendMessageRequest) -> tuple[WorldState, ActionResult]:
        """@brief 瞬时消息：登记收件方 inbox 与 message_arrival 送达（L1624）。"""
        from worldmock.life import social as social_domain

        agent = state.agents[pending.agent_id]
        contacts = self._agent_config(agent.agent_id).contacts
        outgoing = social_domain.OutgoingMessage(
            message_id=f"message-{pending.action_id}", sender_id=f"agent:{agent.agent_id}",
            recipient_id=request.recipient_id, text=request.text,
            created_at=state.current_time)
        world_agent_ids = {f"agent:{agent_id}" for agent_id in self.state.agents}
        sent = social_domain.send_message(outgoing, world_agent_ids, contacts)
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
        result = ActionResult(action_id=pending.action_id, agent_id=pending.agent_id,
                              status=ActionStatus.COMPLETED, occurred_at=state.current_time,
                              message="Message delivered", data={
                                  "message_id": sent.message.message_id,
                                  "recipient_id": request.recipient_id})
        return state, result

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
                               request: StartSocialActivityRequest) -> tuple[WorldState, ActionResult]:
        """@brief 受理交谈（共同在场）或通话（联系人，10-60 分钟）（L1625-1626）。"""
        agent = state.agents[pending.agent_id]
        origin = ActivityOrigin(pending.action_id, pending.decision_id,
                                pending.requested_event_id)
        if request.kind == "chat":
            started = social_actions.start_chat(state, agent, request, origin,
                                                f"invite-{pending.action_id}")
            if started.invite is not None:
                self._deliver_social_message(state, started.invite)
        else:
            contact = self._contact(request.partner_id, agent.agent_id)
            try:
                started = social_actions.start_call(agent, contact, request, origin,
                                                    state.current_time)
            except ValueError as error:
                raise BusinessRuleError("CALL_DURATION_OUT_OF_RANGE") from error
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
        """@brief 把交谈邀请送达对方 inbox 并留 message_arrival 事件草稿。"""
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
        """@brief 结算所有人物的活动区间，并为每段写完整活动状态。"""
        drafts = []
        results = []
        for activity_id in sorted(state.activities):
            activity = state.activities[activity_id]
            if activity.status != ActivityStatus.ACTIVE:
                continue
            interval_start = activity.last_settled_at
            body_boundary = self._body_boundary_at(activity, state.agents[activity.agent_id])
            if activity.kind in {ActivityKind.CHAT, ActivityKind.CALL}:
                advanced = social_actions.advance_social(
                    activity, state.agents[activity.agent_id], target, self.semantics.body,
                    social_actions.chat_accepted(state, activity.agent_id))
            else:
                advanced = advance_activity(activity, state.agents[activity.agent_id], target,
                                            self.semantics.body)
            if body_boundary == target and not advanced.completed:
                activity_update = {"next_interaction_at": target, "boundary_type": "feedback"}
                changed_activity = advanced.activity.model_copy(update=activity_update, deep=True)
                advanced = ActivityAdvance(changed_activity, advanced.agent, advanced.settlement, False)
            if advanced.completed:
                agent = advanced.agent.model_copy(update={"next_decision_at": target}, deep=True)
                advanced = ActivityAdvance(advanced.activity, agent, advanced.settlement, True)
            state.agents[activity.agent_id] = advanced.agent
            state.activities[activity_id] = advanced.activity
            effects = self._apply_activity_effect(state, advanced.activity, advanced.completed)
            if not advanced.completed:
                current = state.agents[activity.agent_id]
                next_at = self._activity_decision_at(state.activities[activity_id], current, target)
                state.agents[activity.agent_id] = current.model_copy(
                    update={"next_decision_at": next_at}, deep=True)
            data = {"interval_start": interval_start, "interval_end": advanced.activity.last_settled_at,
                    "activity": self._activity_snapshot(state.activities[activity_id]),
                    "body_settlement": _body_settlement_data(advanced.settlement), **effects}
            flows = _resource_flows_data(activity.agent_id, target)
            if flows:
                data[RESOURCE_FLOWS_FIELD] = flows
            drafts.append(EventDraft(event_type="activity_progress", occurred_at=target,
                                     agent_id=activity.agent_id,
                                     decision_id=activity.origin_decision_id,
                                     action_id=activity.origin_action_id, activity_id=activity_id,
                                     caused_by=[activity.origin_request_event_id], data=data))
            if advanced.completed:
                final_activity = state.activities[activity_id]
                result = _activity_terminal_result(final_activity, target,
                                                   self._activity_title(final_activity))
                state.processed_actions = _terminal_action_records(state, result)
                results.append(result)
                drafts.append(_activity_terminal_draft(final_activity, target, [],
                                                       self._activity_title(final_activity)))
        return drafts, results

    def _apply_activity_effect(self, state: WorldState, activity, completed: bool) -> dict:
        """@brief 在推进后投影路线、内容和按区间发生的商品效果。"""
        agent = state.agents[activity.agent_id]
        evidence = {}
        if activity.kind == ActivityKind.TRAVEL:
            route = next(item for item in self.scenario.routes if item.route_id == activity.data["route_id"])
            activity, agent = update_travel(activity, route, agent)
            state.activities[activity.activity_id] = activity
            state.agents[agent.agent_id] = agent
        if activity.kind == ActivityKind.WATCH:
            item = self.rules.content(activity.target_id)
            state.agents[agent.agent_id], activity.data["revealed"] = reveal_watch(
                activity, agent, item, self.rules.cues(item.item_id))
            _dedupe_revealed_cues(activity)
        if activity.kind == ActivityKind.WORK:
            material = reveal_work_progress(
                state, self.corpus_root, self.rules.task(activity.target_id),
                activity.agent_id, activity.activity_id)
            evidence["work_material"] = material
            state.agents[agent.agent_id] = _record_material_reveal(
                state.agents[agent.agent_id], activity.target_id, material, state.current_time)
        if activity.kind == ActivityKind.CONSUME:
            state.agents[activity.agent_id] = apply_meal_progress(
                state.agents[activity.agent_id], self.rules.products, activity,
                self.semantics.body)
            evidence["consumption"] = _meal_consumption_data(activity, agent)
            state.activities[activity.activity_id] = activity
        if not completed:
            return evidence
        if activity.kind == ActivityKind.READ_MAIL:
            state.agents[agent.agent_id], activity.data["revealed"] = reveal_mail(
                activity, agent, self.rules.messages)
        elif activity.kind == ActivityKind.BROWSE:
            state.agents[agent.agent_id] = _browse_visibility(
                agent, self.rules.products, state.current_time)
        return evidence

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
            if outcome.semantics is not None:
                self._adopt_semantics(outcome.semantics)
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

    def _adopt_semantics(self, semantics: ScenarioSemantics) -> None:
        """@brief PRICE_CHANGE 到点换持新语义并同步场景与规则的商品索引。"""
        self.semantics = semantics
        self.scenario = semantics.scenario
        self.rules.scenario = semantics.scenario
        self.rules.products = {item.product_id: item for item in semantics.scenario.products}

    def _truncate(self, state: WorldState, now: int) -> list[EventDraft]:
        """@brief 在实验终点标记所有仍活动的会话并保留实际进度。"""
        drafts = []
        for activity_id, activity in state.activities.items():
            if activity.status not in {ActivityStatus.ACTIVE, ActivityStatus.PAUSED}:
                continue
            activity, agent = truncate_activity(activity, state.agents[activity.agent_id], now)
            state.activities[activity_id] = activity
            state.agents[agent.agent_id] = agent
            result = _activity_terminal_result(activity, now, self._activity_title(activity))
            state.processed_actions = _terminal_action_records(state, result)
            drafts.append(_activity_terminal_draft(activity, now, [], self._activity_title(activity)))
        return drafts

    def _ready(self, agent_id: int) -> bool:
        """@brief 无主活动或任一显式交互边界到达的人物可进行决策。"""
        if self.state.current_time >= self.state.end_time:
            return False
        agent = self.state.agents[agent_id]
        if agent.decision_failed or agent.next_decision_at > self.state.current_time:
            return False
        if agent.current_activity_id is None:
            return True
        activity = self.state.activities[agent.current_activity_id]
        return activity.next_interaction_at == self.state.current_time

    def _next_epoch(self) -> int:
        """@brief 选择严格晚于当前时刻的最近活动、后台或节律边界。"""
        candidates = [self.state.end_time]
        candidates.extend(item.due_at for item in self.state.pending_events if item.due_at > self.state.current_time)
        candidates.extend(agent.next_decision_at for agent in self.state.agents.values()
                          if not agent.decision_failed
                          and agent.next_decision_at > self.state.current_time)
        for activity in self.state.activities.values():
            if activity.status != ActivityStatus.ACTIVE:
                continue
            candidates.append(activity.planned_end_at)
            if activity.next_interaction_at and activity.next_interaction_at > self.state.current_time:
                candidates.append(activity.next_interaction_at)
            body_boundary = self._body_boundary_at(activity, self.state.agents[activity.agent_id])
            if body_boundary is not None:
                candidates.append(body_boundary)
        return min(candidates)

    def _activity_decision_at(self, activity, agent, now: int) -> int:
        """@brief 将活动反馈、完成和身体阈值合并为人物持久下一决策时刻。"""
        candidates = [activity.next_interaction_at or activity.planned_end_at]
        body_boundary = self._body_boundary_at(activity, agent, now)
        if body_boundary is not None:
            candidates.append(body_boundary)
        return min(candidates)

    def _body_boundary_at(self, activity, agent, now: int | None = None) -> int | None:
        """@brief 计算需要人物重新观察的首个身体/体感阈值时刻。"""
        if activity.kind in {ActivityKind.SLEEP, ActivityKind.PLAY_GAME,
                             ActivityKind.CHAT, ActivityKind.CALL}:
            return None
        factors = activity_factors(activity, agent, self.semantics.body)
        current = self.state.current_time if now is None else now
        hour = projection.hour_of_day(current, self.scenario.timezone)
        seconds = next_body_boundary_seconds(agent.body, activity.kind,
                                            self.semantics.body, factors, hour)
        if seconds is None:
            return None
        candidate = current + seconds
        return candidate if candidate < activity.planned_end_at else None

    def _life_snapshot(self) -> dict:
        """@brief 生成恢复时必须重放核对的 life 参与者收据。"""
        games = []
        for agent in self.state.agents.values():
            games.extend(progress.model_dump(mode="json") for progress in agent.game_sessions.values())
        return {"schema_version": 1, "run_id": self.state.run_id, "state_version": self.state.version,
                "current_time": self.state.current_time, "scenario_sha256": self.scenario_sha256,
                "corpus_manifest_sha256": self.scenario.corpus_manifest_sha256, "games": games}


def _initial_state(semantics: ScenarioSemantics, scenario_sha256: str, run_id: str) -> WorldState:
    """@brief 从组装语义形成无隐藏动作的完整初始世界。"""
    scenario = semantics.scenario
    start = int(scenario.start_at.timestamp())
    end = int(scenario.end_at.timestamp())
    agents = {}
    accounts = inject_initial_funds(dict(semantics.accounts), start)
    for config in scenario.agents:
        account_id = f"agent:{config.agent_id}"
        agents[config.agent_id] = _initial_agent(config, scenario, account_id, start)
    pending = compile_schedule(scenario, start, end)
    random_state = hashlib.sha256(f"random:{scenario.random_seed}".encode()).hexdigest()
    return WorldState(schema_version=WORLD_SCHEMA_VERSION, version=0, current_time=start,
                      end_time=end, run_id=run_id,
                      timezone=scenario.timezone, scenario_sha256=scenario_sha256,
                      content_manifest_sha256=scenario.corpus_manifest_sha256, agents=agents,
                      accounts=accounts, merchant_stock={item.product_id: item.initial_stock
                                                         for item in scenario.products},
                      pending_events=pending, random_seed=scenario.random_seed, random_state=random_state)


def _initial_agent(config, scenario: RuntimeScenario, account_id: str, start: int):
    """@brief 构造人物身体（两过程睡眠反推初值）、可见日历和空进度。"""
    from worldmock.life.models import AgentState

    visible_tasks = {item.task_id for item in scenario.tasks if int(item.available_at.timestamp()) <= start}
    visible_mail = {item.message_id for item in scenario.messages if int(item.sent_at.timestamp()) <= start
                    and item.recipient_agent_id == config.agent_id}
    start_hour = _datetime(start, scenario.timezone).hour + _datetime(
        start, scenario.timezone).minute / 60
    body = initial_body(scenario.body, start_hour, config.sleep_need_hours,
                        config.chronotype_midsleep)
    return AgentState(agent_id=config.agent_id, name=config.name, location_id=config.home_location_id,
                      balance_account_id=account_id, skill_levels=config.skill_levels,
                      entertainment_preferences=config.entertainment_preferences,
                      body=body, visible_task_ids=visible_tasks,
                      known_task_ids=set(visible_tasks),
                      visible_message_ids=visible_mail, next_decision_at=start,
                      pending_wake_reasons=["run_start"])


def _run_manifest(scenario: RuntimeScenario, scenario_sha256: str, identity: RunIdentity) -> dict:
    """@brief 绑定新运行的场景、内容和参与人物身份。"""
    return {"schema_version": 1, "run_id": identity.run_id, "source_group_id": identity.source_group_id,
            "scenario_id": scenario.scenario_id,
            "scenario_sha256": scenario_sha256, "corpus_manifest_sha256": scenario.corpus_manifest_sha256,
            "agent_ids": list(identity.agent_ids), "code_sha256": identity.code_sha256,
            "model_identity": identity.model_identity.model_dump(mode="json"),
            "window": {"start": int(scenario.start_at.timestamp()), "end": int(scenario.end_at.timestamp()),
                       "timezone": scenario.timezone}}


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


def _put_agent(state: WorldState, agent) -> WorldState:
    """@brief 将人物投影到世界副本。"""
    agents = dict(state.agents)
    agents[agent.agent_id] = agent
    return state.model_copy(update={"agents": agents}, deep=True)


def _day_end(now: int, timezone_name: str) -> int:
    """@brief 当日结束时刻：场景时区次日零点（计划段不得越过，L1607）。"""
    from datetime import timedelta

    moment = _datetime(now, timezone_name)
    return int((moment + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0).timestamp())


def _put_agent_and_defer_feedback(state: WorldState, agent) -> WorldState:
    """@brief 保存人物一秒反应边界，并让到期活动保持连续等待选择。"""
    agents = dict(state.agents)
    agents[agent.agent_id] = agent
    activities = dict(state.activities)
    activity = activities.get(agent.current_activity_id or "")
    if activity is not None and activity.next_interaction_at == state.current_time:
        activities[activity.activity_id] = activity.model_copy(
            update={"next_interaction_at": state.current_time + 1}, deep=True)
    return state.model_copy(update={"agents": agents, "activities": activities}, deep=True)


def _request_sha(tool_name: str, request) -> str:
    """@brief 对工具名和规范化 Pydantic 请求计算稳定身份。"""
    payload = {"tool_name": tool_name, "request": request.model_dump(mode="json")}
    content = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(content).hexdigest()


def _remember_action(state: WorldState, pending: PendingAction, result: ActionResult) -> WorldState:
    """@brief 保存终态结果供同一 action_id 严格幂等复用。"""
    receipt = ActionReceipt(action_id=pending.action_id, round_id=pending.round_id,
                            decision_id=pending.decision_id, request_sha256=pending.request_sha256)
    stored = StoredAction(action_id=pending.action_id, tool_name=pending.tool_name,
                          request_sha256=pending.request_sha256, receipt=receipt.model_dump(mode="json"),
                          result=result.model_dump(mode="json"))
    actions = dict(state.processed_actions)
    actions[pending.action_id] = stored
    return state.model_copy(update={"processed_actions": actions}, deep=True)


def _remember_terminal_activity(state: WorldState, result: ActionResult,
                                title: str | None = None) -> WorldState:
    """@brief 控制动作终结活动时同步更新最初动作的幂等终态。"""
    if result.activity_id is None or result.activity_id not in state.activities:
        return state
    activity = state.activities[result.activity_id]
    terminal = {ActivityStatus.COMPLETED, ActivityStatus.CANCELLED,
                ActivityStatus.FAILED, ActivityStatus.TRUNCATED}
    if activity.status not in terminal:
        return state
    final = _activity_terminal_result(activity, result.occurred_at, title)
    return state.model_copy(update={"processed_actions": _terminal_action_records(state, final)}, deep=True)


def _terminal_action_records(state: WorldState, result: ActionResult) -> dict[str, StoredAction]:
    """@brief 以活动最终结果替换最初动作的持久幂等结果。"""
    if result.action_id not in state.processed_actions:
        raise ProtocolError("Activity origin action is missing from processed actions")
    actions = dict(state.processed_actions)
    stored = actions[result.action_id]
    actions[result.action_id] = stored.model_copy(update={"result": result.model_dump(mode="json")}, deep=True)
    return actions


def _result_draft(pending: PendingAction, result: ActionResult, event_type: str) -> EventDraft:
    """@brief 将动作结果与原请求事件建立直接因果回链。"""
    data = result.model_dump(mode="json")
    if "resource_changes" in result.data:
        data["resource_changes"] = result.data["resource_changes"]
    return EventDraft(event_type=event_type, occurred_at=result.occurred_at, agent_id=result.agent_id,
                      decision_id=pending.decision_id, action_id=pending.action_id,
                      activity_id=result.activity_id, caused_by=_pending_causes(pending),
                      data=data)


def _terminal_after_action(state: WorldState, result: ActionResult,
                           request_event_id: str, title: str | None = None) -> EventDraft | None:
    """@brief 在控制动作终结另一原始活动时补齐原动作最终结果。"""
    if result.activity_id is None or result.activity_id not in state.activities:
        return None
    activity = state.activities[result.activity_id]
    terminal = {ActivityStatus.COMPLETED, ActivityStatus.CANCELLED,
                ActivityStatus.FAILED, ActivityStatus.TRUNCATED}
    if activity.status not in terminal or activity.origin_action_id == result.action_id:
        return None
    return _activity_terminal_draft(activity, result.occurred_at, [request_event_id], title)


def _activity_terminal_result(activity, occurred_at: int,
                              title: str | None = None) -> ActionResult:
    """@brief 将活动生命周期终态编码为最初动作的最终结果。"""
    statuses = {ActivityStatus.COMPLETED: ActionStatus.COMPLETED,
                ActivityStatus.CANCELLED: ActionStatus.CANCELLED,
                ActivityStatus.FAILED: ActionStatus.FAILED,
                ActivityStatus.TRUNCATED: ActionStatus.CANCELLED}
    messages = {ActivityStatus.COMPLETED: "Activity completed",
                ActivityStatus.CANCELLED: "Activity cancelled",
                ActivityStatus.FAILED: "Activity failed",
                ActivityStatus.TRUNCATED: "Activity truncated at experiment end"}
    snapshot = activity.model_dump(mode="json")
    if title is not None:
        snapshot["title"] = title
    return ActionResult(action_id=activity.origin_action_id, agent_id=activity.agent_id,
                        status=statuses[activity.status], occurred_at=occurred_at,
                        activity_id=activity.activity_id, message=messages[activity.status],
                        data={"activity": snapshot})


def _activity_terminal_draft(activity, occurred_at: int,
                             additional_causes: list[str],
                             title: str | None = None) -> EventDraft:
    """@brief 形成回链原请求且携带权威活动快照的生命周期终态。"""
    result = _activity_terminal_result(activity, occurred_at, title)
    data = result.model_dump(mode="json")
    data["activity"] = data["data"]["activity"]
    causes = list(dict.fromkeys([activity.origin_request_event_id, *additional_causes]))
    return EventDraft(event_type="action_result", occurred_at=occurred_at,
                      agent_id=activity.agent_id, decision_id=activity.origin_decision_id,
                      action_id=activity.origin_action_id, activity_id=activity.activity_id,
                      caused_by=causes, data=data)


def _decision_failure_drafts(now: int, pending: PendingAction, result: ActionResult,
                             agent) -> list[EventDraft]:
    """@brief 连续拒绝到限时记录人物失败并原子形成运行失败。"""
    data = {"kind": "agent_decision_failed", "consecutive_rejections": agent.consecutive_rejections,
            "last_error_code": result.error_code, "action_id": pending.action_id}
    failed = EventDraft(event_type="environment_event", occurred_at=now,
                        agent_id=pending.agent_id, decision_id=pending.decision_id,
                        action_id=pending.action_id, caused_by=_pending_causes(pending), data=data)
    run_failed = EventDraft(event_type="run_failed", occurred_at=now, visibility="system",
                            caused_by=[failed.event_id], data={"error_type": "DecisionFailure",
                            "error": result.message, "agent_id": pending.agent_id,
                            "last_error_code": result.error_code})
    return [failed, run_failed]


def _pending_causes(pending: PendingAction) -> list[str]:
    """@brief 将动作请求和适用的独立工作验收作为直接原因。"""
    causes = [pending.requested_event_id]
    if pending.work_review_event_id:
        causes.append(pending.work_review_event_id)
    return causes


def _work_review_request_draft(now: int, pending: PendingAction, link: tuple[str, int, str | None],
                               request: dict) -> EventDraft:
    """@brief 形成系统私有的完整工作验收模型请求证据。"""
    call_id, attempt, previous_event_id = link
    causes = [pending.requested_event_id]
    if previous_event_id:
        causes.append(previous_event_id)
    data = {"phase": "request", "purpose": "work_review", "call_id": call_id,
            "attempt": attempt, "request": request, "response": None, "error": None}
    return EventDraft(event_type="model_request", occurred_at=now, agent_id=pending.agent_id,
                      decision_id=pending.decision_id, action_id=pending.action_id,
                      caused_by=causes, visibility="system",
                      source_kind="model", data=data)


def _work_review_failure_draft(now: int, pending: PendingAction,
                               link: tuple[str, str, int], error: BaseException) -> EventDraft:
    """@brief 形成工作验收配置或传输失败证据。"""
    call_id, request_event_id, attempt = link
    data = {"phase": "failure", "purpose": "work_review", "call_id": call_id,
            "attempt": attempt, "request": None, "response": None, "error": repr(error)}
    return _work_review_terminal_draft(now, pending, request_event_id, data)


def _work_review_response_draft(now: int, pending: PendingAction,
                                link: tuple[str, str, int], outcome: tuple) -> EventDraft:
    """@brief 形成带原始响应、用量和本地验收结论的工作评审证据。"""
    call_id, request_event_id, attempt = link
    raw, decision, error = outcome
    data = {"phase": "response", "purpose": "work_review", "call_id": call_id,
            "attempt": attempt, "request": None, "response": raw,
            "usage": raw.get("usage") if isinstance(raw, dict) else None,
            "review_decision": decision.model_dump(mode="json") if decision else None,
            "error": repr(error) if error else None}
    return _work_review_terminal_draft(now, pending, request_event_id, data)


def _work_review_terminal_draft(now: int, pending: PendingAction,
                                request_event_id: str, data: dict) -> EventDraft:
    """@brief 将工作验收终态直接回链其唯一请求。"""
    return EventDraft(event_type="model_response", occurred_at=now, agent_id=pending.agent_id,
                      decision_id=pending.decision_id, action_id=pending.action_id,
                      caused_by=[request_event_id], visibility="system", source_kind="model", data=data)


def _result_event_data(result: ActionResult, state: WorldState,
                       title: str | None = None) -> dict:
    """@brief 确保活动受理事件携带固定 cutoff 可重建的权威状态。"""
    data = result.model_dump(mode="json")
    if result.activity_id and result.activity_id in state.activities:
        snapshot = state.activities[result.activity_id].model_dump(mode="json")
        if title is not None:
            snapshot["title"] = title
        data["activity"] = snapshot
    return data


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
    """@brief 取走本区间身体结算分录并重盖主体与时间后校验序列化（§9）。

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


def _meal_consumption_data(activity, before_agent) -> dict:
    """@brief 记录一次多件进食的区间使用、地点持有量（热量结算在分录证据中）。"""
    request = ConsumeRequest.model_validate(activity.data["request"])
    consumed = dict(activity.data.get("consumed", {}))
    rows = []
    for item in request.items:
        rows.append({"product_id": item.product_id,
                     "interval_quantity": consumed.get(item.product_id, 0.0),
                     "held_after": held_quantity(before_agent, item.product_id)})
    return {"location_id": before_agent.location_id, "items": rows,
            "use_mode": request.use_mode}


def _dedupe_revealed_cues(activity) -> None:
    """@brief 横跨结算边界的同一条字幕只交付一次（L1547）。

    reveal_watch 的区间过滤会重复交付起止横跨上一边界的 cue；此处按
    "起点落入本区间才算本次新跨过"去重，与 last_revealed_position 对齐。
    """
    revealed = activity.data.get("revealed")
    if not isinstance(revealed, dict):
        return
    start = revealed.get("position_before_seconds")
    cues = revealed.get("cues")
    if not isinstance(start, (int, float)) or not isinstance(cues, list):
        return
    revealed["cues"] = [cue for cue in cues
                        if isinstance(cue, dict) and cue.get("start_seconds", start) >= start]


def _record_material_reveal(agent, task_id, material, occurred_at):
    """@brief 登记工作材料本区间新读区间（观察侧按上次决策过滤并限 2000 字符）。"""
    if not task_id:
        return agent
    entry = {"task_id": task_id, "at": occurred_at,
             "start_char": material.get("start_char", 0),
             "end_char": material.get("end_char", 0),
             "chars": max(0, int(material.get("end_char", 0)) - int(material.get("start_char", 0)))}
    reveals = list(agent.material_reveals)[-63:] + [entry]
    return agent.model_copy(update={"material_reveals": reveals}, deep=True)


def _browse_visibility(agent, products, now):
    """@brief 浏览完成：可见集=目录全部商品（配送浏览同样适用），TTL 1800 秒。

    有效可见集由 projection.visible_products 按店内常驻 ∪ 浏览 TTL 计算；
    此处只登记浏览时刻与原始可见集（L1545）。
    """
    return agent.model_copy(
        update={"visible_product_ids": set(products), "product_browsed_at": now,
                "product_browsed_location": agent.location_id}, deep=True)


def _activity_progress_phrase(activity) -> str:
    """@brief 活动进度短语（不含完整快照，观察合同禁完整活动快照）。"""
    remaining = max(0, activity.planned_duration_seconds - activity.elapsed_seconds)
    return f"还剩约{round(remaining / 60)}分钟"


def _paused_activity_control(activity) -> dict:
    """@brief 暂停活动的控制身份：activity_id 与剩余时长（不含完整快照）。"""
    remaining = max(0, activity.planned_duration_seconds - activity.elapsed_seconds)
    return {"activity_id": activity.activity_id, "kind": activity.kind.value,
            "remaining_seconds": remaining}


def _is_sha256(value: Any) -> bool:
    """@brief 判断字符串是否为规范小写 SHA-256。"""
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


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

