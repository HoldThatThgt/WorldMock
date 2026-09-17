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

from worldmock.corpora import load_manifest
from worldmock.errors import BusinessRuleError, IntegrityError, ProtocolError
from worldmock.footprints.models import (
    RESOURCE_FLOWS_FIELD,
    AssetRef,
    EventDraft,
    validate_resource_flows,
)
from worldmock.footprints.store import FootprintStore
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
from worldmock.life.body import (
    ENERGY_RECOVERY_ACTIVITY_KINDS,
    body_observation,
    interpolate,
    last_flows,
    maximum_energy_duration_seconds,
    minimum_energy_cost,
    next_body_boundary_seconds,
)
from worldmock.life.commerce import apply_product_progress, held_quantity, purchase
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
    PurchaseRequest,
    REQUEST_MODELS,
    RunIdentity,
    SetActivityStateRequest,
    StartBasicActivityRequest,
    StartActivityRequest,
    StartTargetedActivityRequest,
    StartTravelRequest,
    StoredAction,
    SubmitWorkRequest,
    WorldState,
)
from worldmock.life.motivation import (
    MotivationContext,
    evaluate_motivation,
)
from worldmock.life.rules import LifeRuleEngine
from worldmock.life.scenario import RuntimeScenario
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


class LifeEngine:
    """@brief LifeEnv 的单写入领域服务和状态权威。"""

    def __init__(self, semantics: ScenarioSemantics, scenario_sha256: str, corpus_root: Path,
                 store: FootprintStore):
        """@brief 绑定经组装准入的场景语义、内容与持久化运行。"""
        self.semantics = semantics
        self.scenario = semantics.scenario
        self.scenario_sha256 = scenario_sha256
        self.corpus_root = Path(corpus_root).resolve()
        self.store = store
        self.rules = LifeRuleEngine(self.scenario, self.corpus_root)
        self.lifestyle_items = load_lifestyle_items(
            self.corpus_root, self.scenario.body.activity_met)
        self.state = WorldState.model_validate(store.state)
        self._round: DecisionRoundState | None = None
        self._model_request_events: dict[tuple[str, str], str] = {}
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
        """@brief 持久化共同观察版本与明确人物结算顺序。"""
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
        drafts = [EventDraft(event_type="decision_started", occurred_at=self.state.current_time,
                             agent_id=item.agent_id, decision_id=item.decision_id,
                             observed_at=self.state.current_time,
                             data={"round_id": round_id, "round_order": order,
                                   "world_state_version": self.state.version})
                  for order, item in enumerate(contexts)]
        self.store.commit(self.state.model_dump(mode="json"), drafts)
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
        draft = EventDraft(event_type="action_requested", occurred_at=self.state.current_time,
                           agent_id=call.context.agent_id, decision_id=call.context.decision_id,
                           action_id=call.action_id, source_kind="model",
                           data={"tool_name": call.tool_name, "request": call.request.model_dump(mode="json"),
                                 "request_sha256": request_sha, "reused": reused})
        event = self.store.commit(self.state.model_dump(mode="json"), [draft])[0]
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
        drafts: list[EventDraft] = []
        results = []
        for pending in sorted(current.pending_actions, key=lambda item: item.order):
            staged, result, action_drafts = self._resolve(staged, pending)
            self._ops_action(pending, result)
            results.append(result)
            drafts.extend(action_drafts)
            if not pending.reused:
                staged = _remember_action(staged, pending, result)
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
        drafts = []
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
        """@brief 精确推进到下一事件边界并结算连续活动和后台事件。"""
        if self._round is not None:
            raise ProtocolError("World time cannot advance during an open decision round")
        if any(self._ready(agent_id) for agent_id in self.state.agents):
            raise ProtocolError("World time cannot advance while an agent decision is due")
        target_epoch = int(target.timestamp())
        if tick <= 0 or target_epoch - self.state.current_time != tick or target_epoch != self._next_epoch():
            raise ProtocolError("World advance must match the next scheduling boundary")
        staged = self.state.model_copy(deep=True)
        drafts, results = self._advance_activities(staged, target_epoch)
        staged, background = self._process_scheduled(staged, target_epoch)
        drafts.extend(background)
        staged.current_time = target_epoch
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
        if any(event.event_type == "decision_started" or
               (event.event_type == "environment_event"
                and event.data.get("kind") == "counterfactual_intervention") for event in child_events):
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

    def _observation_data(self, agent) -> dict:
        """@brief 投影当前人物可知内容，不暴露工作 oracle 或未来消息。"""
        active = self.state.activities.get(agent.current_activity_id) if agent.current_activity_id else None
        paused = [_paused_activity_observation(item, agent, self.semantics.body) for item in sorted(
            self.state.activities.values(), key=lambda value: value.activity_id)
            if item.agent_id == agent.agent_id and item.status == ActivityStatus.PAUSED]
        tasks = [self._visible_task(item, agent.agent_id) for item in sorted(agent.visible_task_ids)
                 if item not in self.state.paid_task_ids
                 and self.state.current_time <= int(self.rules.tasks[item].due_at.timestamp())]
        products = [self._visible_product(item) for item in sorted(agent.visible_product_ids)]
        deliveries = [item.model_dump(mode="json") for item in self.state.deliveries.values()
                      if item.agent_id == agent.agent_id and item.status == "pending"]
        data = {"world_state_version": self.state.version, "current_time": self.state.current_time,
                "end_time": self.state.end_time, "agent_id": agent.agent_id, "location_id": agent.location_id,
                "balance_fen": self.state.accounts[agent.balance_account_id],
                "body": body_observation(agent.body, self.semantics.body),
                "active_activity": active.model_dump(mode="json") if active else None,
                "paused_activities": paused,
                "messages": mail_headers(agent, self.rules.messages), "tasks": tasks, "products": products,
                "calendar_context": _calendar_context(self.scenario, self.state.current_time),
                "locations": _location_catalog(self.scenario),
                "routes": _route_catalog(self.scenario.routes, self.semantics.body,
                                      agent.location_id, agent.body.energy_kcal,
                                      self.state.current_time, self.semantics),
                "energy_feasibility": _activity_energy_feasibility(agent, self.semantics.body),
                "activity_summary": self._activity_summary(agent),
                "content_progress": self._content_progress(agent),
                "game_catalog": _game_catalog(self.scenario),
                "browse_targets": ["product_catalog"],
                "inventory": agent.inventory,
                "available_inventory": agent.inventory.get(agent.location_id, {}),
                "pending_deliveries": deliveries,
                "recent_action_results": self._recent_action_results(agent.agent_id),
                "active_effects": sorted(agent.active_effects),
                "games": {key: value.model_dump(mode="json") for key, value in agent.game_sessions.items()}}
        hints = self._motivation_hints(agent)
        if hints is not None:
            data["motivation_hints"] = hints
        market_summary = self._market_summary()
        if market_summary is not None:
            data["market_summary"] = market_summary
        return data

    def _market_summary(self) -> dict | None:
        """@brief 商店存在性常识：无需浏览即知道的营业状态、在售数与价格区间。

        存在性是常识（楼下有店、大概什么价位），商品明细才由 browse 门控
        （products 键）。无商品目录的场景缺省不写该键。
        """
        products = self.rules.products
        if not products:
            return None
        hour = _datetime(self.state.current_time, self.scenario.timezone).hour
        open_now = any(
            location.open_hour <= hour < location.close_hour
            for location in self.scenario.locations
            if location.location_id in {item.merchant_location_id for item in products.values()})
        prices = [item.price_fen for item in products.values()]
        return {"open": open_now, "product_count": len(products),
                "price_range_fen": [min(prices), max(prices)]}

    def _activity_summary(self, agent) -> dict[str, int]:
        """@brief 一行品类计数：观察只报各活动品类的在场数量，明细由下钻读取。

        全量 content_catalog 逐条展开（192+ 项）被本摘要取代（Stanford 环境
        树/Sims 智能对象广告的分层菜单模式）：键即 browse_activities 的合法
        品类闭集——内容 kind、人物启用的生活活动品类与 foods，值是条目数。
        TextWorld 游戏目录仍由 game_catalog 独立暴露，不并入本摘要。
        """
        summary: dict[str, int] = {}
        for item in self.scenario.contents:
            summary[item.kind] = summary.get(item.kind, 0) + 1
        for entry in self._enabled_lifestyle_items(agent):
            summary[entry.category] = summary.get(entry.category, 0) + 1
        if self.scenario.products:
            summary["foods"] = len(self.scenario.products)
        return dict(sorted(summary.items()))

    def _enabled_lifestyle_items(self, agent) -> tuple[LifestyleItem, ...]:
        """@brief 返回人物启用声明过滤后的生活活动目录条目（None=全局启用）。"""
        config = next(item for item in self.scenario.agents if item.agent_id == agent.agent_id)
        enabled = config.lifestyle_activities
        if enabled is None:
            return self.lifestyle_items
        allowed = set(enabled)
        return tuple(item for item in self.lifestyle_items if item.activity_id in allowed)

    def _browse_activities(self, state: WorldState, pending: PendingAction,
                           request: BrowseActivitiesRequest) -> tuple[WorldState, ActionResult]:
        """@brief 下钻读取一个活动品类目录：纯查询，不启动活动、不消耗内容。

        合法品类从 activity_summary 的键派生；内容品类返回与观看受理一致的
        条目形状（含分段与进度），生活活动品类返回名称/时长/MET/描述，
        foods 返回菜单级商品行（购买合同仍走 product_catalog 的限时 browse）。
        结果只作为动作反馈返回，不写入 content_progress。
        """
        agent = state.agents[pending.agent_id]
        category = request.category
        if category not in self._activity_summary(agent):
            raise BusinessRuleError("BROWSE_CATEGORY_NOT_FOUND")
        content_kinds = {item.kind for item in self.scenario.contents}
        if category in content_kinds:
            items = [row for row in _content_catalog(
                        self.scenario, self.rules.subtitles, self.rules.content_titles,
                        agent.content_progress)
                     if row["kind"] == category]
            note = "watch items accept start_targeted_activity with activity_type watch"
        elif category == "foods":
            items = [{"product_id": product.product_id,
                      "price_fen": product.price_fen,
                      "use_modes": list(product.use_modes),
                      "use_duration_seconds": product.use_duration_seconds,
                      "merchant_location_id": product.merchant_location_id}
                     for product in sorted(self.scenario.products,
                                           key=lambda item: item.product_id)]
            note = "menu-level view only; purchase contracts need the timed product catalog browse"
        else:
            items = [{"activity_id": entry.activity_id,
                      "activity_type": f"{LIFESTYLE_TYPE_PREFIX}{entry.activity_id}",
                      "name": entry.name, "category": entry.category,
                      "typical_duration_minutes": entry.typical_duration_minutes,
                      "description": entry.description, "met_value": entry.met_value}
                     for entry in sorted(self._enabled_lifestyle_items(agent),
                                         key=lambda item: item.activity_id)
                     if entry.category == category]
            note = "lifestyle items accept start_basic_activity with activity_type lifestyle:<activity_id>"
        result = ActionResult(action_id=pending.action_id, agent_id=agent.agent_id,
                              status=ActionStatus.COMPLETED, occurred_at=state.current_time,
                              message="Activity menu browsed",
                              data={"tool": "browse_activities", "category": category,
                                    "count": len(items), "note": note, "items": items})
        return state, result

    def _motivation_hints(self, agent) -> dict | None:
        """@brief 计算动机提示；无档案、无变换参数或求值失败一律不注入。

        提示是建议不是账：本方法只读生活状态，任何求值异常都吞掉并记日志
        （fail-open 于 hints），结算路径不感知其存在（fail-closed 于结算）。
        """
        profile = next((item.motivation for item in self.scenario.agents
                        if item.agent_id == agent.agent_id and item.motivation is not None), None)
        transforms = self.semantics.motivation_transforms
        if profile is None or transforms is None:
            return None
        try:
            context = self._motivation_context(agent, profile)
            scored = evaluate_motivation(context, profile, transforms)
            if not scored:
                return None
            return {"schema_version": 1, "generated_at": self.state.current_time,
                    "candidates": [item.to_hint() for item in scored]}
        except Exception:
            logging.warning("Motivation hints evaluation failed", exc_info=True)
            return None

    def _motivation_context(self, agent, profile) -> MotivationContext:
        """@brief 从当前世界装配求值输入切片（全部只读）。

        contents 不再传全量目录：按品类摘要 + 每品类 top-2 采样（权 =
        attention_prior[kind] × 内容偏好）传入，观察分层化后评分器仍能
        产生高质量推荐而不需要全量列表。
        """
        now = self.state.current_time
        tasks = [self.rules.tasks[item] for item in sorted(agent.visible_task_ids)
                 if item not in self.state.paid_task_ids
                 and now <= int(self.rules.tasks[item].due_at.timestamp())]
        habit = tuple((event.data.get("tool_name"),
                       event.data.get("request") if isinstance(event.data.get("request"), dict) else {})
                      for event in self.store.read_agent_events("action_requested", agent.agent_id))
        effective_total = sum(item.effective_work_seconds for item in self.state.activities.values()
                              if item.agent_id == agent.agent_id and item.kind == ActivityKind.WORK
                              and item.status != ActivityStatus.FAILED)
        return MotivationContext(
            agent=agent,
            balance_fen=self.state.accounts[agent.balance_account_id],
            body=self.semantics.body,
            home_location_id=next(item.home_location_id for item in self.scenario.agents
                                  if item.agent_id == agent.agent_id),
            products=tuple(self.rules.products[item] for item in sorted(agent.visible_product_ids)),
            merchant_stock=self.state.merchant_stock,
            locations={item.location_id: item for item in self.scenario.locations},
            routes=self.scenario.routes,
            tasks=tuple(tasks),
            task_totals={task.task_id: work_totals(self.state, agent.agent_id, task.task_id)
                         for task in tasks},
            contents=_sample_contents(self.scenario.contents, profile, agent),
            content_category_counts={
                kind: sum(1 for item in self.scenario.contents if item.kind == kind)
                for kind in {item.kind for item in self.scenario.contents}},
            content_progress=agent.content_progress,
            active_activity=(self.state.activities.get(agent.current_activity_id)
                             if agent.current_activity_id else None),
            habit_events=habit,
            consecutive_rejections=agent.consecutive_rejections,
            max_consecutive_rejections=self.scenario.max_consecutive_rejections,
            total_effective_seconds=effective_total,
            current_time=now,
            end_time=self.state.end_time,
            timezone=self.scenario.timezone,
            planned_travel_seconds=lambda mode, base: self.semantics.travel_planned_seconds(
                mode, base, now),
            catalog_products=tuple(self.rules.products[item] for item in sorted(self.rules.products)),
            content_segments={item_id: tuple(cue.end_seconds for cue in cues)
                              for item_id, cues in self.rules.subtitles.items()})

    def _recent_action_results(self, agent_id: int) -> list[dict]:
        """@brief 返回该人物上次观察后新产生的动作结算事实。"""
        cutoff = self.store.last_agent_event_seq("observation", agent_id)
        events = self.store.read_agent_events("action_result", agent_id, after=cutoff)
        results = [event for event in events if event.action_id is not None
                   and event.visibility == "agent"]
        return [{"event_id": event.event_id, "action_id": event.action_id,
                 "decision_id": event.decision_id, "occurred_at": event.occurred_at,
                 "status": event.data.get("status"), "error_code": event.data.get("error_code"),
                 "message": event.data.get("message"), "result": event.data}
                for event in results]

    def _visible_task(self, task_id: str, agent_id: int) -> dict:
        """@brief 返回公开合同、交付 Schema 和仅按实际工作揭示的正文。"""
        task = self.rules.tasks[task_id]
        data = task.model_dump(mode="json", exclude={"oracle_path", "oracle_sha256", "input_path"})
        worked, effective = work_totals(self.state, agent_id, task_id)
        data["deliverable_schema"] = MeetingMinutes.model_json_schema()
        data["material"] = visible_work_material(self.state, self.corpus_root, task, agent_id)
        data["worked_seconds"] = worked
        data["effective_seconds"] = effective
        data["remaining_contract_seconds"] = max(0, task.maximum_duration_seconds - worked)
        return data

    def _visible_product(self, product_id: str) -> dict:
        """@brief 返回商品公开合同和权威当前库存，不暴露初始库存。"""
        data = self.rules.products[product_id].model_dump(mode="json", exclude={"initial_stock"})
        data["current_stock"] = self.state.merchant_stock[product_id]
        return data

    def _content_progress(self, agent) -> dict[str, dict]:
        """@brief 返回人物已观看位置、接触次数、展示标题和当前媒体剩余时长。

        标题来自语料 items.json：无展示标题的条目（feed 类、game_session）
        不写 title 键，观察方以条目身份键区分展示方式。
        """
        result = {}
        for item_id, progress in sorted(agent.content_progress.items()):
            item = self.rules.content(item_id)
            data = progress.model_dump(mode="json")
            data["remaining_media_seconds"] = max(0.0, item.duration_seconds - progress.position_seconds)
            title = self.rules.content_titles.get(item_id)
            if title is not None:
                data["title"] = title
            result[item_id] = data
        return result

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

    def _resolve(self, state: WorldState, pending: PendingAction) -> tuple[WorldState, ActionResult, list[EventDraft]]:
        """@brief 将一个有类型请求结算为业务成功或稳定拒绝。"""
        if pending.reused:
            return self._reused_result(state, pending)
        try:
            changed, result = self._dispatch(state, pending)
            changed = self._after_success(changed, pending.agent_id)
            changed = _remember_terminal_activity(changed, result)
            accepted = EventDraft(event_type="action_accepted", occurred_at=state.current_time,
                                  agent_id=pending.agent_id, decision_id=pending.decision_id,
                                  action_id=pending.action_id, activity_id=result.activity_id,
                                  caused_by=_pending_causes(pending),
                                  data=_result_event_data(result, changed))
            drafts = [accepted, _result_draft(pending, result, "action_result")]
            terminal = _terminal_after_action(changed, result, pending.requested_event_id)
            if terminal is not None:
                drafts.append(terminal)
            return changed, result, drafts
        except BusinessRuleError as error:
            changed, result = self._after_rejection(state, pending, error)
            agent = changed.agents[pending.agent_id]
            activity = changed.activities.get(agent.current_activity_id or "")
            data = {"error_code": str(error), **result.data}
            if activity is not None:
                data["activity"] = activity.model_dump(mode="json")
            rejected = EventDraft(event_type="action_rejected", occurred_at=state.current_time,
                                  agent_id=pending.agent_id, decision_id=pending.decision_id,
                                  action_id=pending.action_id,
                                  activity_id=activity.activity_id if activity else None,
                                  caused_by=_pending_causes(pending), data=data)
            drafts = [rejected, _result_draft(pending, result, "action_result")]
            if result.status == ActionStatus.FAILED:
                drafts.extend(_decision_failure_drafts(state.current_time, pending, result, agent))
            return changed, result, drafts
        except GameOutcomeUnknown as error:
            result = ActionResult(action_id=pending.action_id, agent_id=pending.agent_id,
                                  status=ActionStatus.UNKNOWN, occurred_at=state.current_time,
                                  error_code="GAME_OUTCOME_UNKNOWN", message=str(error))
            return self._after_unknown(state, pending.agent_id), result, [
                _result_draft(pending, result, "action_result")]

    def _after_success(self, state: WorldState, agent_id: int) -> WorldState:
        """@brief 成功选择重置拒绝计数并设置下一次合法决策时刻。"""
        agent = state.agents[agent_id]
        activity = state.activities.get(agent.current_activity_id or "")
        if activity is None:
            next_at = state.current_time + 1
        else:
            next_at = self._activity_decision_at(activity, agent, state.current_time)
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
        changed_agent = agent.model_copy(update={"next_decision_at": state.current_time + 1,
                                                  "consecutive_rejections": count,
                                                  "decision_failed": failed}, deep=True)
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
                    "browse_activities": self._browse_activities}
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
        result_data = {"activity": activity.model_dump(mode="json")}
        if paused_activity is not None:
            result_data["auto_paused_activity"] = paused_activity.model_dump(mode="json")
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
                              data={"activity": activity.model_dump(mode="json")})
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
                              data={"activity": activity.model_dump(mode="json")})
        return changed, result

    def _purchase(self, state: WorldState, pending: PendingAction,
                  request: PurchaseRequest) -> tuple[WorldState, ActionResult]:
        """@brief 结算报价、支付、库存和履约计划。"""
        if request.product_id not in state.agents[pending.agent_id].visible_product_ids:
            raise BusinessRuleError("PRODUCT_NOT_VISIBLE")
        outcome = purchase(state, self.scenario, pending.agent_id, request)
        result = ActionResult(action_id=pending.action_id, agent_id=pending.agent_id,
                              status=ActionStatus.COMPLETED, occurred_at=state.current_time,
                              message="Purchase accepted",
                              data={"order": outcome.delivery.model_dump(mode="json"),
                                    "resource_changes": {
                                        f"account:{state.agents[pending.agent_id].balance_account_id}":
                                            -outcome.delivery.total_price_fen,
                                        f"account:{self.rules.product(request.product_id).merchant_account_id}":
                                            outcome.delivery.total_price_fen}})
        return outcome.state, result

    def _consume(self, state: WorldState, pending: PendingAction,
                 request: ConsumeRequest) -> tuple[WorldState, ActionResult]:
        """@brief 启动实际有时长的商品使用活动。"""
        agent = state.agents[pending.agent_id]
        product = self.rules.product(request.product_id)
        if held_quantity(agent, product.product_id) < request.quantity:
            raise BusinessRuleError("PRODUCT_NOT_HELD")
        if request.use_mode not in product.use_modes or request.duration_seconds != product.use_duration_seconds:
            raise BusinessRuleError("PRODUCT_USE_NOT_ALLOWED")
        start = StartActivityRequest(activity_type=ActivityKind.CONSUME,
                                     planned_duration_seconds=request.duration_seconds,
                                     target_id=request.product_id)
        origin = ActivityOrigin(pending.action_id, pending.decision_id, pending.requested_event_id)
        agent, activity = start_activity(agent, start, state.current_time, origin,
                                         self.semantics.body)
        activity = schedule_feedback(activity, state.current_time, self.scenario.activity_feedback_seconds)
        efficiency = interpolate(self.semantics.body.food_efficiency_curve,
                                  agent.body.sleep_debt_hours)
        raw_energy = product.energy_kcal_per_unit * request.quantity
        food_rate = raw_energy * efficiency * 3600 / request.duration_seconds
        activity.data.update(request=request.model_dump(mode="json"), consumed_quantity=0.0,
                             food_energy_kcal_per_hour=food_rate)
        changed = _put_activity(state, agent, activity)
        result = ActionResult(action_id=pending.action_id, agent_id=pending.agent_id,
                              status=ActionStatus.IN_PROGRESS, occurred_at=state.current_time,
                              activity_id=activity.activity_id, message="Product use started",
                              data={"activity": activity.model_dump(mode="json")})
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
        data = {"task_id": request.task_id, "payment_fen": outcome.payment_fen,
                "worked_seconds": outcome.worked_seconds,
                "effective_seconds": outcome.effective_seconds, "work_review": feedback,
                "resource_changes": {
                    f"account:{self.rules.task(request.task_id).employer_account_id}": -outcome.payment_fen,
                    f"account:{state.agents[pending.agent_id].balance_account_id}": outcome.payment_fen}}
        result = ActionResult(action_id=pending.action_id, agent_id=pending.agent_id,
                              status=ActionStatus.COMPLETED, occurred_at=state.current_time,
                              message="Work accepted and paid", data=data)
        return outcome.state, result

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
                                    "activity": activity.model_dump(mode="json")})
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
                    "activity": state.activities[activity_id].model_dump(mode="json"),
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
                result = _activity_terminal_result(state.activities[activity_id], target)
                state.processed_actions = _terminal_action_records(state, result)
                results.append(result)
                drafts.append(_activity_terminal_draft(state.activities[activity_id], target, []))
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
        if activity.kind == ActivityKind.WORK:
            evidence["work_material"] = reveal_work_progress(
                state, self.corpus_root, self.rules.task(activity.target_id),
                activity.agent_id, activity.activity_id)
        if activity.kind == ActivityKind.CONSUME:
            outcome = apply_product_progress(agent, self.rules.product(activity.target_id),
                                             activity, self.semantics.body)
            state.agents[agent.agent_id] = outcome.agent
            activity.data["consumed_quantity"] = outcome.consumed_quantity
            evidence["consumption"] = _consumption_data(activity, agent, outcome)
            state.activities[activity.activity_id] = activity
        if not completed:
            return evidence
        if activity.kind == ActivityKind.READ_MAIL:
            state.agents[agent.agent_id], activity.data["revealed"] = reveal_mail(
                activity, agent, self.rules.messages)
        elif activity.kind == ActivityKind.BROWSE:
            state.agents[agent.agent_id] = agent.model_copy(
                update={"visible_product_ids": set(self.rules.products)}, deep=True)
        return evidence

    def _process_scheduled(self, state: WorldState, target: int) -> tuple[WorldState, list[EventDraft]]:
        """@brief 处理同秒稳定排序的邮件、工作机会、配送与预排段事件。"""
        due = [item for item in state.pending_events if item.due_at == target]
        state.pending_events = [item for item in state.pending_events if item.due_at != target]
        drafts = []
        for item in sorted(due, key=lambda value: (value.sequence, value.scheduled_id)):
            outcome = apply_scheduled_effect(state, item, self.semantics)
            if outcome.semantics is not None:
                self._adopt_semantics(outcome.semantics)
            state = outcome.state
            self.ops.log(target, "engine", "info", "SCHEDULED_EVENT_DISPATCHED",
                         f"Scheduled event {item.kind.value} dispatched", agent_id=item.agent_id,
                         event_kind=item.kind.value, scheduled_id=item.scheduled_id)
            drafts.append(EventDraft(event_type="environment_event", occurred_at=target,
                                     agent_id=item.agent_id, source_kind="scenario",
                                     data={"kind": item.kind.value, "scheduled_id": item.scheduled_id,
                                           **outcome.data}))
        return state, drafts

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
            result = _activity_terminal_result(activity, now)
            state.processed_actions = _terminal_action_records(state, result)
            drafts.append(_activity_terminal_draft(activity, now, []))
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
        """@brief 选择严格晚于当前时刻的最近活动或后台边界。"""
        candidates = [self.state.end_time]
        candidates.extend(item.due_at for item in self.state.pending_events if item.due_at > self.state.current_time)
        candidates.extend(agent.next_decision_at for agent in self.state.agents.values()
                          if not agent.decision_failed and agent.current_activity_id is None
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
        """@brief 计算需要人物重新观察的首个身体阈值时刻。"""
        if activity.kind in {ActivityKind.SLEEP, ActivityKind.PLAY_GAME}:
            return None
        factors = activity_factors(activity, agent, self.semantics.body)
        seconds = next_body_boundary_seconds(agent.body, activity.kind,
                                            self.semantics.body, factors)
        if seconds is None:
            return None
        candidate = (self.state.current_time if now is None else now) + seconds
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
    return WorldState(version=0, current_time=start, end_time=end, run_id=run_id,
                      timezone=scenario.timezone, scenario_sha256=scenario_sha256,
                      content_manifest_sha256=scenario.corpus_manifest_sha256, agents=agents,
                      accounts=accounts, merchant_stock={item.product_id: item.initial_stock
                                                         for item in scenario.products},
                      pending_events=pending, random_seed=scenario.random_seed, random_state=random_state)


def _initial_agent(config, scenario: RuntimeScenario, account_id: str, start: int):
    """@brief 构造人物身体、可见日历和空进度。"""
    from worldmock.life.models import AgentState, BodyState

    visible_tasks = {item.task_id for item in scenario.tasks if int(item.available_at.timestamp()) <= start}
    visible_mail = {item.message_id for item in scenario.messages if int(item.sent_at.timestamp()) <= start
                    and item.recipient_agent_id == config.agent_id}
    body = BodyState(energy_kcal=scenario.body.initial_energy_kcal,
                     sleep_debt_hours=scenario.body.initial_sleep_debt_hours, entertainment_need=0)
    return AgentState(agent_id=config.agent_id, name=config.name, location_id=config.home_location_id,
                      balance_account_id=account_id, skill_levels=config.skill_levels,
                      entertainment_preferences=config.entertainment_preferences,
                      body=body, visible_task_ids=visible_tasks,
                      visible_message_ids=visible_mail, next_decision_at=start)


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


def _remember_terminal_activity(state: WorldState, result: ActionResult) -> WorldState:
    """@brief 控制动作终结活动时同步更新最初动作的幂等终态。"""
    if result.activity_id is None or result.activity_id not in state.activities:
        return state
    activity = state.activities[result.activity_id]
    terminal = {ActivityStatus.COMPLETED, ActivityStatus.CANCELLED,
                ActivityStatus.FAILED, ActivityStatus.TRUNCATED}
    if activity.status not in terminal:
        return state
    final = _activity_terminal_result(activity, result.occurred_at)
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
                           request_event_id: str) -> EventDraft | None:
    """@brief 在控制动作终结另一原始活动时补齐原动作最终结果。"""
    if result.activity_id is None or result.activity_id not in state.activities:
        return None
    activity = state.activities[result.activity_id]
    terminal = {ActivityStatus.COMPLETED, ActivityStatus.CANCELLED,
                ActivityStatus.FAILED, ActivityStatus.TRUNCATED}
    if activity.status not in terminal or activity.origin_action_id == result.action_id:
        return None
    return _activity_terminal_draft(activity, result.occurred_at, [request_event_id])


def _activity_terminal_result(activity, occurred_at: int) -> ActionResult:
    """@brief 将活动生命周期终态编码为最初动作的最终结果。"""
    statuses = {ActivityStatus.COMPLETED: ActionStatus.COMPLETED,
                ActivityStatus.CANCELLED: ActionStatus.CANCELLED,
                ActivityStatus.FAILED: ActionStatus.FAILED,
                ActivityStatus.TRUNCATED: ActionStatus.CANCELLED}
    messages = {ActivityStatus.COMPLETED: "Activity completed",
                ActivityStatus.CANCELLED: "Activity cancelled",
                ActivityStatus.FAILED: "Activity failed",
                ActivityStatus.TRUNCATED: "Activity truncated at experiment end"}
    return ActionResult(action_id=activity.origin_action_id, agent_id=activity.agent_id,
                        status=statuses[activity.status], occurred_at=occurred_at,
                        activity_id=activity.activity_id, message=messages[activity.status],
                        data={"activity": activity.model_dump(mode="json")})


def _activity_terminal_draft(activity, occurred_at: int,
                             additional_causes: list[str]) -> EventDraft:
    """@brief 形成回链原请求且携带权威活动快照的生命周期终态。"""
    result = _activity_terminal_result(activity, occurred_at)
    data = result.model_dump(mode="json")
    data["activity"] = activity.model_dump(mode="json")
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


def _result_event_data(result: ActionResult, state: WorldState) -> dict:
    """@brief 确保活动受理事件携带固定 cutoff 可重建的权威状态。"""
    data = result.model_dump(mode="json")
    if result.activity_id and result.activity_id in state.activities:
        data["activity"] = state.activities[result.activity_id].model_dump(mode="json")
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


def _consumption_data(activity, before_agent, outcome) -> dict:
    """@brief 记录本区间实际使用、地点持有量与食品身体结算。"""
    request = ConsumeRequest.model_validate(activity.data["request"])
    before = held_quantity(before_agent, request.product_id)
    after = held_quantity(outcome.agent, request.product_id)
    interval = before - after
    if interval == 0 and activity.status == ActivityStatus.COMPLETED:
        interval = request.quantity
    return {"product_id": request.product_id, "location_id": before_agent.location_id,
            "interval_quantity": interval, "cumulative_quantity": outcome.consumed_quantity,
            "held_before": before, "held_after": after,
            "food_settlement": (_body_settlement_data(outcome.settlement)
                                if outcome.settlement is not None else None)}


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


def _calendar_context(scenario: RuntimeScenario, epoch: int) -> dict:
    """@brief 返回当前本地日期已声明的日历机会与约束。"""
    current_date = _datetime(epoch, scenario.timezone).date().isoformat()
    calendar = scenario.calendar
    return {"date": current_date,
            "spring_festival": current_date in calendar.spring_festival_dates,
            "makeup_workday": current_date in calendar.makeup_work_dates,
            "overtime_opportunity": current_date in calendar.overtime_opportunity_dates,
            "sick_leave": current_date == calendar.sick_leave_date,
            "personal_leave": current_date == calendar.personal_leave_date,
            "social_dinner_opportunity": current_date == calendar.social_dinner_date}


def _failure_draft(occurred_at: int, error: Exception | str, round_id: str | None) -> EventDraft:
    """@brief 形成可覆盖先前 run_ended 的致命运行失败证据。"""
    error_type = type(error).__name__ if isinstance(error, Exception) else "RuntimeError"
    return EventDraft(event_type="run_failed", occurred_at=occurred_at, visibility="system",
                      data={"error_type": error_type, "error": str(error), "round_id": round_id})


def _location_catalog(scenario: RuntimeScenario) -> list[dict]:
    """@brief 返回人物能够规划到达和营业条件的地点目录。"""
    fields = {"location_id", "name", "kind", "longitude", "latitude", "open_hour", "close_hour"}
    return [item.model_dump(mode="json", include=fields) for item in scenario.locations]


def _route_catalog(routes: tuple, body, origin_id: str, energy_kcal: float,
                   departure_second: int, semantics) -> list[dict]:
    """@brief 返回人物当前位置出发的冻结路线身份和出发时刻报价。

    duration_seconds 为按当前出发时刻 travel-time 系数折算的计划时长
    （引擎受理口径，人物照此提交 planned_duration_seconds）；
    free_flow_duration_seconds 保留冻结自由流时长，travel_time_factor
    公开本次折算系数；能量预算按折算后时长计算。
    """
    fields = {"route_id", "origin_id", "destination_id", "mode", "distance_meters", "duration_seconds",
              "fare_fen", "fare_source_kind"}
    result = []
    for item in routes:
        if item.origin_id != origin_id:
            continue
        planned = semantics.travel_planned_seconds(
            item.mode.value, item.duration_seconds, departure_second)
        data = item.model_dump(mode="json", include=fields)
        data.update(duration_seconds=planned,
                    free_flow_duration_seconds=item.duration_seconds,
                    travel_time_factor=semantics.travel_time_factor(
                        item.mode.value, departure_second))
        met = body.activity_met[f"travel:{item.mode.value}"]
        cost = minimum_energy_cost(ActivityKind.TRAVEL, planned, body, met)
        data.update(minimum_energy_cost_kcal=cost, energy_feasible=cost <= energy_kcal)
        result.append(data)
    return result


def _activity_energy_feasibility(agent, body) -> dict:
    """@brief 返回五类目标活动按当前能量可受理的最大整数墙钟秒数。"""
    kinds = (ActivityKind.WORK, ActivityKind.WATCH, ActivityKind.PLAY_GAME,
             ActivityKind.READ_MAIL, ActivityKind.BROWSE)
    maximums = {
        kind.value: maximum_energy_duration_seconds(kind, agent.body.energy_kcal, body)
        for kind in kinds
    }
    return {"maximum_duration_seconds_by_kind": maximums}


def _remaining_activity_energy(activity, agent, body) -> dict:
    """@brief 以暂停活动剩余墙钟时长计算当前恢复能量合同。"""
    remaining = max(0, activity.planned_duration_seconds - activity.elapsed_seconds)
    factors = activity_factors(activity, agent, body)
    cost = minimum_energy_cost(activity.kind, remaining, body, factors.met)
    feasible = activity.kind in ENERGY_RECOVERY_ACTIVITY_KINDS or cost <= agent.body.energy_kcal
    return {"remaining_duration_seconds": remaining, "minimum_energy_cost_kcal": cost,
            "resume_energy_feasible": feasible}


def _paused_activity_observation(activity, agent, body) -> dict:
    """@brief 公开暂停活动本身及当前剩余时长的恢复能量可行性。"""
    data = activity.model_dump(mode="json")
    data.update(_remaining_activity_energy(activity, agent, body))
    return data


def _sample_contents(contents, profile, agent) -> tuple:
    """@brief 每内容品类按 attention_prior x 偏好采 top-2，采样全局按权排序。

    分层菜单模式下动机求值不再接收全量目录（192 项）：采样保持品类覆盖
    （每 kind 至多 2 条、总量 ≤14），排序保证进入候选池截断的是最强候选。
    """
    def weight(item) -> float:
        return (profile.attention_prior.get(item.kind, 0)
                * agent.entertainment_preferences.get(item.item_id, 0.5))

    ranked = sorted(contents, key=lambda item: (-weight(item), item.item_id))
    per_kind: dict[str, int] = {}
    sample = []
    for item in ranked:
        taken = per_kind.get(item.kind, 0)
        if taken >= 2:
            continue
        per_kind[item.kind] = taken + 1
        sample.append(item)
    return tuple(sample)


def _content_catalog(scenario: RuntimeScenario, subtitles: dict[str, tuple[SubtitleCue, ...]],
                     titles: dict[str, str],
                     progress: dict[str, ContentProgress]) -> list[dict]:
    """@brief 返回人物可选择内容及其分段结构，不暴露本地路径和哈希。

    目录不再把内容呈现为一个整块时长：每条目带 segments_total 与
    typical_segment_seconds（网文=章、信息流=条、剧集=字幕行），已开始的
    条目并入 progress（位置、剩余、已完成段数），让模型看见"读一章/看
    一段"是合法的小步消费而非一整天的整块承诺。电影/剧集/网文/单片体验
    附带语料标题（titles），信息流与游戏条目以 experience_label/game_id
    自带身份、不注入标题。
    """
    fields = {"item_id", "kind", "duration_seconds", "entertainment_relief_per_hour", "experience_label"}
    result = []
    for item in scenario.contents:
        data = item.model_dump(mode="json", include=fields)
        cues = subtitles.get(item.item_id, ())
        total = len(cues)
        data["segments_total"] = total
        data["typical_segment_seconds"] = (
            round(item.duration_seconds / total, 1) if total else float(item.duration_seconds))
        if item.item_id in titles:
            data["title"] = titles[item.item_id]
        record = progress.get(item.item_id)
        if record is not None:
            completed = sum(1 for cue in cues if cue.end_seconds <= record.position_seconds)
            data["progress"] = {
                "position_seconds": record.position_seconds,
                "remaining_seconds": max(0.0, item.duration_seconds - record.position_seconds),
                "segments_total": total,
                "segments_completed": completed,
                "segments_remaining": max(0, total - completed),
                "current_segment": min(completed + 1, total),
            }
        result.append(data)
    return result


def _game_catalog(scenario: RuntimeScenario) -> list[dict]:
    """@brief 返回可新建 TextWorld 会话的稳定游戏身份。"""
    return [{"game_id": item.game_id, "seed": item.seed} for item in scenario.games]
