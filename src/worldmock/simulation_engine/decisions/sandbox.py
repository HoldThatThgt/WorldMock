"""@brief 并发人物唤醒的只读隔离沙盒：不改共享世界、实时回传层差量并缓冲未提交证据。"""

from __future__ import annotations

import copy
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from worldmock.agents.prompt.tool_groups import compact
from worldmock.exchange_engine.definitions.types import GOODS_CATEGORY_LABELS
from worldmock.foundation.errors import BusinessRuleError, PreconditionError, ProtocolError
from worldmock.foundation.serialization import sha256_json
from worldmock.recording_engine.events.models import RESOURCE_FLOWS_FIELD, EventDraft
from worldmock.simulation_engine.actions.business_feedback import business_feedback
from worldmock.simulation_engine.actions.dispatcher import ActionHandlers
from worldmock.simulation_engine.actions.start_group import ToolInvocation
from worldmock.simulation_engine.activities.lifecycle import activity_control
from worldmock.simulation_engine.activities.search.search_activity import start_search
from worldmock.simulation_engine.activities.travel.navigation_foreground import settle_navigation
from worldmock.simulation_engine.body.body import apply_relaxation_change
from worldmock.simulation_engine.evidence.actions import (
    ActionRecordSpec,
    _remember_action,
    _result_draft_with_causes,
    _result_event_data,
)
from worldmock.simulation_engine.evidence.model import FAILED_EVIDENCE_TYPES
from worldmock.simulation_engine.observation.layers.observe import ObservationBuilder, layer_changes
from worldmock.simulation_engine.observation.layers.result_projection import public_result
from worldmock.simulation_engine.observation.memory import search as memory_search
from worldmock.simulation_engine.observation.targets import discovery
from worldmock.simulation_engine.observation.targets.tool_layer import (
    LayerContext,
    _binding_target,
    _travel_visible,
    annotated_bindings,
    with_bindings,
)
from worldmock.simulation_engine.observation.wording.activity_titles import activity_snapshot, title_index
from worldmock.simulation_engine.observation.wording.guidance import (
    Refusal,
    query_rejection,
    refusal_for_code,
    rejection_guidance,
)
from worldmock.simulation_engine.scheduling.callbacks.callbacks import CallbackCause, evaluate_callbacks
from worldmock.simulation_engine.scheduling.clock.rhythm import next_decision_at
from worldmock.simulation_engine.world.context import EngineContext
from worldmock.simulation_engine.world.world_updates import _put_agent, _request_sha, _resource_flows_data
from worldmock.state.decisions.barrier import AgentWakeupInput, SandboxOutcome, ToolDispatchResult
from worldmock.state.decisions.day_session import FIVE_LAYER_KEYS, DaySessionState
from worldmock.state.decisions.results import ActionResult
from worldmock.state.decisions.suspended import SuspendedLoopState
from worldmock.state.interaction.actions import SEARCH_TOOL, START_TOOLS
from worldmock.state.interaction.control_items import control_items
from worldmock.state.interaction.request_errors import argument_report
from worldmock.state.interaction.requests import (
    REQUEST_MODELS,
    LookupTargetsRequest,
    RecallTaskSummaryRequest,
    SearchMemoryRequest,
)
from worldmock.state.model.exchange import ModelCallEvidence
from worldmock.state.world.kinds import ActionStatus, ActivityKind, ActivityStatus
from worldmock.state.world.world import WorldState, active_activities

_LOGGER = logging.getLogger(__name__)
#: 单次唤醒记忆检索上限。
_MEMORY_SEARCH_LIMIT = 3


@dataclass(frozen=True, slots=True)
class _PendingStart:
    """@brief 同回合第一条开始工具执行前的沙盒回滚点，用于与同批次另一开始工具成组原子重放。"""

    tool_name: str
    spec: ActionRecordSpec
    invocation: ToolInvocation
    state_before: WorldState
    baseline_before: dict[str, Any]
    drafts_after_req: int
    ops_len: int
    accepted_len: int
    memories_len: int


class AgentSandbox:
    """@brief 实现 SandboxHandle 协议的沙盒执行器：在世界副本上结算动作、计算层差量与打包待提交产物。"""

    def __init__(self, context: EngineContext, actions: ActionHandlers,
                 observations: ObservationBuilder, wakeup: AgentWakeupInput) -> None:
        """@brief 深拷贝共享世界作为隔离沙盒并生成初始唤醒与观察证据草稿。"""
        self._context = context
        self._actions = actions
        self._observations = observations
        self._wakeup = wakeup
        self._agent_id = wakeup.agent_id
        self._now = wakeup.observed_at
        self._state: WorldState = context.state.model_copy(deep=True)
        self._cutoff_seq = context.store.last_event_seq
        self._baseline_obs = copy.deepcopy(wakeup.observation)
        self._obs_event_id = str(wakeup.observation["observation_event_id"])
        loop = wakeup.suspended_loop if wakeup.mode == "resume_search" else None
        self._memory_searches = loop.memory_search_count if loop is not None else 0
        self._accepted_ids = list(loop.accepted_action_ids) if loop is not None else []
        self._pending_memories = list(loop.pending_memories) if loop is not None else []
        self._drafts = _initial_wakeup_drafts(observations, wakeup, self._baseline_obs)
        self._operations: list[dict[str, Any]] = []
        self._request_records: dict[tuple[str, str], tuple[str, str]] = {}
        self._pending_start: _PendingStart | None = None
        self._loaded_target_keys: list[tuple[str, str]] = (
            list(wakeup.day_session.loaded_target_keys) if wakeup.day_session is not None else []
        )
        self._delivered_layers: dict[str, Any] = {
            key: copy.deepcopy(wakeup.observation[key])
            for key in FIVE_LAYER_KEYS if key in wakeup.observation
        }
        self._finished = False

    def search_memory(self, request: SearchMemoryRequest | Mapping[str, Any]) -> dict[str, Any]:
        """@brief 在冻结事件上界只读检索本人历史记忆并统计次数上限。"""
        self._require_open()
        self._pending_start = None
        req = (request if isinstance(request, SearchMemoryRequest)
               else SearchMemoryRequest.model_validate(request))
        if self._memory_searches >= _MEMORY_SEARCH_LIMIT:
            _LOGGER.error("Memory search limit reached in wakeup %s", self._wakeup.wakeup_id)
            return query_rejection(refusal_for_code("MEMORY_SEARCH_LIMIT").error())
        self._memory_searches += 1
        try:
            return memory_search.search_memory(
                self._context, self._agent_id, req, self._cutoff_seq, self._now,
            )
        except BusinessRuleError as error:
            return query_rejection(error)

    def recall_task_summary(self, request: RecallTaskSummaryRequest | Mapping[str, Any]) -> dict[str, Any]:
        """@brief 在当前沙盒状态与观察绑定的任务范围上只读回看阶段摘要。"""
        self._require_open()
        self._pending_start = None
        req = (request if isinstance(request, RecallTaskSummaryRequest)
               else RecallTaskSummaryRequest.model_validate(request))
        try:
            return self._actions.read_task_summary_on(self._state, self._agent_id, req, self._baseline_obs)
        except BusinessRuleError as error:
            return query_rejection(error)

    def lookup_targets(self, request: BaseModel | Mapping[str, Any]) -> dict[str, Any]:
        """@brief 零耗时只读检索地点、内容应用或商品目标，并将命中目标载入当前日会话工具层。"""
        self._require_open()
        self._pending_start = None
        req = (request if isinstance(request, LookupTargetsRequest)
               else LookupTargetsRequest.model_validate(request))
        pairs = self._lookup_pairs(req.kind, req.keyword)
        total = len(pairs)
        page = pairs[req.offset:req.offset + req.limit]
        self._record_loaded_targets(row for row, _ in page)
        targets = [compact(target) for _, target in page]
        has_more = req.offset + len(targets) < total
        next_offset = req.offset + len(targets) if has_more else None
        return {
            "ok": True,
            "result": {
                "total": total,
                "has_more": has_more,
                "next_offset": next_offset,
                "targets": targets,
                "loaded_count": len(targets),
            },
        }

    def compact_snapshot(self) -> dict[str, Any]:
        """@brief 清空会话已载入目标并按沙盒当前状态刷新权威五层与进度元数据，供日内 Compaction 使用。"""
        self._require_open()
        self._pending_start = None
        self._loaded_target_keys.clear()
        refreshed = self._observations.sandbox_layers(self._state, self._baseline_obs, ())
        self._baseline_obs = {**self._baseline_obs, **refreshed}
        layers = {key: copy.deepcopy(self._baseline_obs[key]) for key in FIVE_LAYER_KEYS if key in self._baseline_obs}
        self._delivered_layers = copy.deepcopy(layers)
        return {**layers, "progress_meta": copy.deepcopy(self._baseline_obs.get("progress_meta", []))}

    def dispatch_world_tool(self, tool_name: str, arguments: BaseModel | Mapping[str, Any],
                            call_id: str, note: str | None = None) -> ToolDispatchResult:
        """@brief 受理并在沙盒副本执行单次世界工具，同回合两个不同开始工具成组原子重算。"""
        self._require_open()
        if tool_name not in REQUEST_MODELS or tool_name == "recall_task_summary":
            _LOGGER.error("Unsupported sandbox world tool: %s", tool_name)
            raise ProtocolError(f"Unsupported sandbox world tool: {tool_name}")
        if (self._pending_start is not None and tool_name in START_TOOLS
                and tool_name != self._pending_start.tool_name):
            return self._dispatch_start_pair(tool_name, arguments, call_id, note)
        self._pending_start = None
        return self._run_world_tool(tool_name, arguments, call_id, note)

    def _dispatch_start_group(
        self, calls: Sequence[tuple[str, BaseModel | Mapping[str, Any], str, str | None]],
    ) -> tuple[ToolDispatchResult, ...]:
        """@brief 内部成组重算入口：原子校验并调度开始组，任一被拒则整组回滚。"""
        self._require_open()
        self._pending_start = None
        if len(calls) != 2 or {item[0] for item in calls} != set(START_TOOLS):
            _LOGGER.error("Start group dispatch requires distinct main and side start tools")
            raise ProtocolError("Start group dispatch requires distinct main and side start tools")
        invs = []
        for tool_name, arguments, call_id, note in calls:
            raw_req, req_sha = _extract_raw_request(tool_name, arguments)
            req_draft = _request_draft(self._wakeup, (call_id, tool_name, raw_req, req_sha, note), self._obs_event_id)
            self._drafts.append(req_draft)
            invs.append(ToolInvocation(self._agent_id, self._wakeup.wakeup_id, call_id, tool_name, raw_req,
                                       req_sha, req_draft.event_id, copy.deepcopy(self._baseline_obs), note))
        return self._execute_pair(invs[0], invs[1], self._baseline_obs)

    def record_model_exchange(self, exchange: ModelCallEvidence) -> None:
        """@brief 校验模型调用归属与请求完整性后追加模型交互证据草稿，并清空未配对开始回滚点。"""
        self._require_open()
        self._pending_start = None
        if exchange.agent_id != self._agent_id or exchange.wakeup_id != self._wakeup.wakeup_id:
            _LOGGER.error("Model exchange ownership mismatch in wakeup %s", self._wakeup.wakeup_id)
            raise ProtocolError("Model exchange ownership mismatch")
        digest = sha256_json(exchange.request)
        key = (exchange.wakeup_id, exchange.call_id)
        if exchange.phase == "request":
            event_id = uuid4().hex
            self._request_records[key] = (event_id, digest)
            self._drafts.append(_exchange_draft(exchange, self._now, event_id, [self._obs_event_id], True))
            return
        recorded = self._request_records.get(key)
        if recorded is None:
            _LOGGER.error("Model terminal %s lacks recorded request", exchange.call_id)
            raise ProtocolError("Model exchange lacks recorded request")
        if recorded[1] != digest:
            _LOGGER.error("Model %s for call %s carries a request that differs from its recorded request",
                          exchange.phase, exchange.call_id)
            raise ProtocolError("Model exchange request differs from its recorded request")
        self._request_records.pop(key)
        self._drafts.append(_exchange_draft(exchange, self._now, uuid4().hex, [recorded[0]], False))

    def finish_wakeup(
        self,
        suspended: SuspendedLoopState | None,
        day_session: DaySessionState,
        compaction_records: tuple[dict[str, Any], ...] = (),
    ) -> SandboxOutcome:
        """@brief 封口沙盒状态、补全会话载入集与已交付基准并返回不可变唤醒产出。"""
        self._require_open()
        self._pending_start = None
        self._finished = True
        updated_session = day_session.model_copy(update={
            "loaded_target_keys": tuple(self._loaded_target_keys),
            "last_observed_layers": copy.deepcopy(self._delivered_layers),
        })
        if suspended is not None:
            final_state = self._apply_suspend(suspended)
            return self._build_outcome("suspended", final_state, updated_session, compaction_records)
        agent = self._state.agents[self._agent_id]
        loop = agent.suspended_loop if _has_active_search(self._state, self._agent_id) else None
        cleared = agent.model_copy(update={"suspended_loop": loop, "pending_wake_reasons": []}, deep=True)
        snapped = self._observations.snapshot_decision_references(_put_agent(self._state, cleared), [self._agent_id])
        refreshed = snapped.agents[self._agent_id]
        next_at = next_decision_at(self._context, snapped, refreshed)
        final_state = _put_agent(snapped, refreshed.model_copy(update={"next_decision_at": next_at}))
        return self._build_outcome("completed", final_state, updated_session, compaction_records)

    def drain_evidence(self) -> tuple[EventDraft, ...]:
        """@brief 返回异常退出时允许落库的纯模型与唤醒证据草稿。"""
        return tuple(draft for draft in self._drafts if draft.event_type in FAILED_EVIDENCE_TYPES)

    def _lookup_pairs(self, kind: str | None, keyword: str | None) -> list[tuple[dict, dict]]:
        """@brief 在沙盒当前状态下构建符合分类与关键词过滤条件的候选目标对。"""
        agent = self._state.agents[self._agent_id]
        catalog_rows = self._observations._catalog_bindings(agent, self._state)
        base_ctx = self._observations._layer_context(
            agent, catalog_rows, self._state, frozenset(self._loaded_target_keys),
        )
        scoped = self._scoped_lookup_rows(base_ctx, catalog_rows, kind)
        annotated = annotated_bindings(with_bindings(base_ctx, scoped))
        target_ctx = with_bindings(base_ctx, annotated)
        pairs = [(row, _binding_target(target_ctx, row)) for row in annotated]
        if keyword is None:
            return pairs
        needle = keyword.strip().casefold()
        return [(row, target) for row, target in pairs
                if _matches_lookup_keyword(self._context.catalog, row, target, needle)]

    def _scoped_lookup_rows(self, ctx: LayerContext, catalog_rows: list[dict], kind: str | None) -> list[dict]:
        """@brief 按指定分类或默认顺序收集当前位置可达范围内的绑定行并排序。"""
        agent = self._state.agents[self._agent_id]
        origin = agent.location_id
        graph = self._context.catalog.graph
        current_city = graph.city_of(origin)
        hubs = dict(graph.intercity_edges_from(origin))
        loc_rows = [
            row for row in catalog_rows
            if row["kind"] == "location"
            and (graph.city_of(row["object_id"]) == current_city or row["object_id"] in hubs)
        ]
        loc_rows.sort(key=lambda r: (not _location_reachable(ctx, origin, r), r["object"], r["object_id"]))
        app_rows = sorted((r for r in catalog_rows if r["kind"] == "app"), key=lambda r: (r["object"], r["object_id"]))
        prod_rows = sorted(discovery.product_bindings(ctx), key=lambda r: (r["object"], r["object_id"]))
        buckets = {"location": loc_rows, "app": app_rows, "product": prod_rows}
        if kind is not None:
            return buckets[kind]
        return [*loc_rows, *app_rows, *prod_rows]

    def _record_loaded_targets(self, rows) -> None:
        """@brief 将命中目标并入会话已载入键集并同步刷新沙盒工具层与已交付基准。"""
        changed = False
        for row in rows:
            public_kind = "product" if row["kind"] == "supply" else row["kind"]
            key = (public_kind, row["object_id"])
            if key not in self._loaded_target_keys:
                self._loaded_target_keys.append(key)
                changed = True
        if changed:
            after = self._observations.sandbox_layers(self._state, self._baseline_obs, self._loaded_target_keys)
            self._baseline_obs = {**self._baseline_obs, **after}
            self._delivered_layers.update(
                {name: copy.deepcopy(after[name]) for name in FIVE_LAYER_KEYS if name in after}
            )

    def _require_open(self) -> None:
        """@brief 禁止在封口之后继续对同一个沙盒写入或查询。"""
        if self._finished:
            _LOGGER.error("Sandbox operation attempted after finish_wakeup")
            raise ProtocolError("Sandbox is already finished")

    def _run_world_tool(self, tool_name: str, arguments: BaseModel | Mapping[str, Any],
                        call_id: str, note: str | None) -> ToolDispatchResult:
        """@brief 在内存沙盒中校验参数、写请求草稿、分派执行并处理拒绝或成功结果。"""
        raw_req, req_sha = _extract_raw_request(tool_name, arguments)
        req_draft = _request_draft(self._wakeup, (call_id, tool_name, raw_req, req_sha, note), self._obs_event_id)
        spec = ActionRecordSpec(self._wakeup.wakeup_id, call_id, tool_name, req_sha, req_draft.event_id)
        inv = ToolInvocation(self._agent_id, self._wakeup.wakeup_id, call_id, tool_name, raw_req,
                             req_sha, req_draft.event_id, copy.deepcopy(self._baseline_obs), note)
        snapshot = self._capture_start(inv, spec, len(self._drafts) + 1) if tool_name in START_TOOLS else None
        self._drafts.append(req_draft)
        try:
            if isinstance(arguments, Mapping):
                _validate_mapping_request(tool_name, arguments)
            result = self._accept(inv)
        except BusinessRuleError as error:
            self._pending_start = None
            return self._reject((inv,), error)
        self._pending_start = snapshot
        return result

    def _dispatch_start_pair(self, tool_name: str, arguments: BaseModel | Mapping[str, Any],
                             call_id: str, note: str | None) -> ToolDispatchResult:
        """@brief 回滚同回合第一条开始工具的局部状态并经 StartGroups 整组重算两条开始调用。"""
        first = self._pending_start
        assert first is not None
        raw_req, req_sha = _extract_raw_request(tool_name, arguments)
        self._pending_start = None
        self._restore_start(first)
        req_draft = _request_draft(self._wakeup, (call_id, tool_name, raw_req, req_sha, note), self._obs_event_id)
        self._drafts.append(req_draft)
        inv2 = ToolInvocation(self._agent_id, self._wakeup.wakeup_id, call_id, tool_name, raw_req,
                              req_sha, req_draft.event_id, copy.deepcopy(first.baseline_before), note)
        return self._execute_pair(first.invocation, inv2, first.baseline_before)[1]

    def _execute_pair(self, inv1: ToolInvocation, inv2: ToolInvocation,
                      baseline_before: dict[str, Any]) -> tuple[ToolDispatchResult, ToolDispatchResult]:
        """@brief 以 StartGroups 原子重算两条开始调用，任一被拒时整组回滚并返回共享拒绝结果。"""
        pair_ids = [(uuid4().hex, uuid4().hex), (uuid4().hex, uuid4().hex)]
        try:
            changed, results, drafts = execute_start_group(
                self._context, self._actions, self._state, [inv1, inv2], pair_ids,
            )
        except BusinessRuleError as error:
            rejected = self._reject((inv1, inv2), error)
            return rejected, rejected
        self._actions.drain_causes()
        changed, _ = settle_navigation(self._state, changed, self._context.catalog)
        self._drafts.extend(drafts)
        changes = self._advance_layers(changed, update_delivered=False)
        m1 = self._record_operation(inv1, pair_ids[0], baseline_before)
        m2 = self._record_operation(inv2, pair_ids[1], baseline_before)
        return (self._dispatch_result(inv1.tool_name, results[0], changes, m1),
                self._dispatch_result(inv2.tool_name, results[1], changes, m2))

    def _capture_start(self, inv: ToolInvocation, spec: ActionRecordSpec, drafts_after_req: int) -> _PendingStart:
        """@brief 在首条开始调用执行前保存沙盒状态与证据切片点，供同组第二条到达时整组重放。"""
        return _PendingStart(
            tool_name=inv.tool_name, spec=spec, invocation=inv,
            state_before=self._state.model_copy(deep=True),
            baseline_before=copy.deepcopy(self._baseline_obs),
            drafts_after_req=drafts_after_req, ops_len=len(self._operations),
            accepted_len=len(self._accepted_ids), memories_len=len(self._pending_memories),
        )

    def _restore_start(self, pending: _PendingStart) -> None:
        """@brief 把沙盒状态、层快照、操作记录与首条请求之后的衍生草稿回滚至开始组起点。"""
        self._state = pending.state_before
        self._baseline_obs = pending.baseline_before
        del self._drafts[pending.drafts_after_req:]
        del self._operations[pending.ops_len:]
        del self._accepted_ids[pending.accepted_len:]
        del self._pending_memories[pending.memories_len:]

    def _accept(self, inv: ToolInvocation) -> ToolDispatchResult:
        """@brief 成功分派动作并生成受理证据、沙盒层差量及待重放操作描述。"""
        acc_id, res_id = uuid4().hex, uuid4().hex
        changed, result, drafts = execute_invocation(self._context, self._actions, self._state, inv, (acc_id, res_id))
        self._actions.drain_causes()
        changed, _ = settle_navigation(self._state, changed, self._context.catalog)
        self._drafts.extend(drafts)
        prior_obs = copy.deepcopy(self._baseline_obs)
        boundary = inv.tool_name in (*START_TOOLS, SEARCH_TOOL, "continue_activity")
        changes = self._advance_layers(changed, update_delivered=not boundary)
        memories = self._record_operation(inv, (acc_id, res_id), prior_obs)
        return self._dispatch_result(inv.tool_name, result, changes, memories)

    def _record_operation(self, inv: ToolInvocation, event_ids: tuple[str, str],
                          obs: dict[str, Any]) -> tuple[dict[str, Any], ...]:
        """@brief 登记单条已受理操作的记忆条目与供 BarrierMerger 重放的操作描述符。"""
        acc_id, res_id = event_ids
        memories = _extract_memories(inv.tool_name, inv.request)
        self._accepted_ids.append(inv.action_id)
        self._pending_memories.extend(memories)
        self._operations.append({
            "tool_name": inv.tool_name, "action_id": inv.action_id, "request": inv.request,
            "note": inv.note, "requested_event_id": inv.requested_event_id,
            "accepted_event_id": acc_id, "result_event_id": res_id,
            "observation": copy.deepcopy(obs),
        })
        return memories

    def _dispatch_result(self, tool_name: str, result: ActionResult, changes: dict[str, Any],
                         memories: tuple[dict[str, Any], ...]) -> ToolDispatchResult:
        """@brief 将单条动作受理结果封装为供循环层消费的 ToolDispatchResult，严禁将 activity_id 混入工具消息载荷。"""
        pub = public_result(result.model_dump(mode="json"), self._context.inputs, {})
        suspended = tool_name == SEARCH_TOOL
        terminal = suspended or tool_name in START_TOOLS or tool_name == "continue_activity"
        payload: dict[str, Any] = {"ok": True, "result": pub, "changes": changes, "events": []}
        return ToolDispatchResult(
            accepted=True, terminal_exit=terminal, suspended=suspended,
            activity_id=result.activity_id, tool_message_payload=payload, pending_memories=memories,
        )

    def _reject(self, invs: Sequence[ToolInvocation], error: BusinessRuleError) -> ToolDispatchResult:
        """@brief 将单条或成组业务拒绝转为拒绝脉冲、带请求回链的拒绝证据与公开工具响应。"""
        self._actions.resource_changes.clear()
        self._actions.drain_drafts()
        self._actions.drain_causes()
        guidance = _rejection_payload(invs[0].request, error, self._baseline_obs)
        flows, changes = self._apply_rejection_pulse()
        causes = [inv.requested_event_id for inv in invs]
        first_result: EventDraft | None = None
        last_payload: dict[str, Any] = {}
        for inv in invs:
            spec = ActionRecordSpec(inv.wakeup_id, inv.action_id, inv.tool_name, inv.request_sha256,
                                    inv.requested_event_id)
            result = ActionResult(
                action_id=inv.action_id, agent_id=self._agent_id, status=ActionStatus.REJECTED,
                occurred_at=self._now, message=guidance["reason"], error_code=str(error), data=guidance,
            )
            rej_draft = _rejected_draft(self._context, self._state, spec, (result, guidance, causes))
            res_draft = _result_draft_with_causes(spec, result, "action_result", causes)
            self._drafts.extend((rej_draft, res_draft))
            if first_result is None:
                first_result = res_draft
            last_payload = public_result(result.model_dump(mode="json"), self._context.inputs, {})
        if flows and first_result is not None:
            first_result.data[RESOURCE_FLOWS_FIELD] = flows
        return ToolDispatchResult(
            accepted=False, terminal_exit=False, suspended=False,
            tool_message_payload={"ok": False, "result": last_payload, "changes": changes, "events": []},
            pending_memories=(),
        )

    def _apply_rejection_pulse(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """@brief 同一人物同一模拟秒首次服务拒绝时施加 0.05 放松需要脉冲并返回资源分录与层差量。"""
        if _prior_pulse_applied(self._context, self._drafts, self._agent_id, self._now):
            return [], {}
        agent = self._state.agents[self._agent_id]
        updated_body = apply_relaxation_change(agent.body, 0.05)
        flows = _resource_flows_data(self._agent_id, self._now)
        changed = _put_agent(self._state, agent.model_copy(update={"body": updated_body}, deep=True))
        return flows, self._advance_layers(changed)

    def _advance_layers(self, changed: WorldState, *, update_delivered: bool = True) -> dict[str, Any]:
        """@brief 计算世界状态变更前后观察层差量，并滚动更新沙盒基准观察与已交付基准。"""
        after = self._observations.sandbox_layers(changed, self._baseline_obs, self._loaded_target_keys)
        diff = layer_changes(self._baseline_obs, after)
        self._state = changed
        self._baseline_obs = {**self._baseline_obs, **after}
        if update_delivered:
            self._delivered_layers.update(
                {name: copy.deepcopy(after[name]) for name in FIVE_LAYER_KEYS if name in after}
            )
        return diff

    def _apply_suspend(self, suspended: SuspendedLoopState) -> WorldState:
        """@brief 核验挂起搜索活动处于进行中并把挂起上下文写入沙盒人物副本。"""
        act = self._state.activities.get(suspended.activity_id)
        if (act is None or act.agent_id != self._agent_id or act.kind != ActivityKind.SEARCH
                or act.status != ActivityStatus.ACTIVE):
            _LOGGER.error("Suspend requested without active search activity %s", suspended.activity_id)
            raise ProtocolError("Suspended loop requires an active search activity")
        agent = self._state.agents[self._agent_id].model_copy(update={"suspended_loop": suspended}, deep=True)
        snapped = self._observations.snapshot_decision_references(_put_agent(self._state, agent), [self._agent_id])
        refreshed = snapped.agents[self._agent_id]
        return _put_agent(snapped, refreshed.model_copy(
            update={"next_decision_at": next_decision_at(self._context, snapped, refreshed)}))

    def _build_outcome(
        self, status: str, state: WorldState,
        day_session: DaySessionState, compaction_records: tuple[dict[str, Any], ...],
    ) -> SandboxOutcome:
        """@brief 组装交回时钟屏障提交的不可变唤醒结果对象。"""
        return SandboxOutcome(
            wakeup_id=self._wakeup.wakeup_id, agent_id=self._agent_id, status=status,
            state=state, evidence_drafts=tuple(self._drafts),
            observation_parts=dict(self._wakeup.observation_parts),
            accepted_action_ids=tuple(self._accepted_ids),
            pending_memories=tuple(self._pending_memories),
            verified_experiences=tuple(copy.deepcopy(self._wakeup.observation.get("verified_experiences", ()))),
            observation_event_id=self._obs_event_id,
            observation_payload=copy.deepcopy(self._wakeup.observation),
            memory_evidence=None, action_operations=tuple(self._operations),
            day_session=day_session, compaction_records=tuple(compaction_records),
        )


def execute_invocation(
    context: EngineContext,
    actions: ActionHandlers,
    state: WorldState,
    inv: ToolInvocation,
    event_ids: tuple[str, str],
) -> tuple[WorldState, ActionResult, list[EventDraft]]:
    """@brief 执行单条已解析调用并登记动作账本与受理/结果/回调证据，供沙盒与重放共用。"""
    if inv.tool_name == SEARCH_TOOL:
        changed, result = start_search(context, state, inv)
        changed, tail = _finalize_invocation(context, actions, changed, (inv, result, event_ids))
        return changed, result, tail
    if inv.tool_name in START_TOOLS:
        balance_before = state.accounts[state.agents[inv.agent_id].balance_account_id]
        changed, results = actions.dispatch(state, [inv])
        changed, drafts = _finalize_started(context, actions, changed, ([inv], results, [event_ids], balance_before))
        return changed, results[0], drafts
    changed, results = actions.dispatch(state, [inv])
    result = results[0]
    domain_drafts = actions.drain_drafts()
    domain_drafts.extend(activity_control.record_changes(state, changed, inv, actions.record_context))
    changed, tail = _finalize_invocation(context, actions, changed, (inv, result, event_ids))
    return changed, result, [*domain_drafts, *tail]


def execute_start_group(
    context: EngineContext,
    actions: ActionHandlers,
    state: WorldState,
    group: Sequence[ToolInvocation],
    event_ids: Sequence[tuple[str, str]],
) -> tuple[WorldState, list[ActionResult], list[EventDraft]]:
    """@brief 按开始组原子执行两条开始调用并登记账本、受理结果及阈值/动作回调草稿。"""
    balance_before = state.accounts[state.agents[group[0].agent_id].balance_account_id]
    changed, results = actions.dispatch(state, group)
    changed, drafts = _finalize_started(context, actions, changed, (group, results, event_ids, balance_before))
    return changed, results, drafts


def _finalize_started(
    context: EngineContext,
    actions: ActionHandlers,
    state: WorldState,
    batch: tuple[Sequence[ToolInvocation], Sequence[ActionResult], Sequence[tuple[str, str]], int],
) -> tuple[WorldState, list[EventDraft]]:
    """@brief 为已受理开始调用写入 accepted_event_id、账本与草稿，并绑定收货与动作回调因果。"""
    group, results, event_ids, balance_before = batch
    drafts: list[EventDraft] = []
    res_drafts: list[EventDraft] = []
    changed = state
    for inv, result, pair in zip(group, results, event_ids, strict=True):
        changed, tail = _finalize_invocation(context, actions, changed, (inv, result, pair))
        drafts.extend(tail)
        res_drafts.append(tail[1])
    main_res = next((d for inv, d in zip(group, res_drafts, strict=True) if inv.tool_name == START_TOOLS[0]),
                    res_drafts[0])
    drafts.extend(_start_effects(actions, main_res))
    changed, cb_drafts = _action_callbacks(
        context, actions, changed, (list(zip(group, res_drafts, strict=True)), balance_before),
    )
    drafts.extend(cb_drafts)
    return changed, drafts


def _start_effects(actions: ActionHandlers, received: EventDraft) -> list[EventDraft]:
    """@brief 将开始组暂存的收货与凭状态消耗草稿绑定到主调用的受理结果并登记回调起因。"""
    drafts: list[EventDraft] = []
    for draft in actions.drain_drafts():
        if draft.data.get("kind") == "goods_received" and not draft.caused_by:
            draft = draft.model_copy(update={"caused_by": [received.event_id]})
        elif draft.data.get("type") == "consume_on_start":
            action_kind = received.data["data"]["activities"][0]["kind"]
            cause = CallbackCause("action", action_kind, draft.agent_id, received.event_id,
                                  f"{received.action_id}:consume")
            actions.callback_causes[draft.event_id] = cause
            draft = draft.model_copy(update={"caused_by": [cause.source_event_id]})
        drafts.append(draft)
    return drafts


def _action_callbacks(
    context: EngineContext,
    actions: ActionHandlers,
    state: WorldState,
    batch: tuple[Sequence[tuple[ToolInvocation, EventDraft]], int],
) -> tuple[WorldState, list[EventDraft]]:
    """@brief 逐条受理开始调用求值动作回调，回链其受理结果并扣减净减少提示所需前余额。"""
    pairs, balance_before = batch
    if any(inv.tool_name not in START_TOOLS for inv, _ in pairs):
        return state, []
    drafts: list[EventDraft] = []
    before: int | None = balance_before
    changed = state
    ordered = sorted(pairs, key=lambda pair: START_TOOLS.index(pair[0].tool_name))
    for inv, result_draft in ordered:
        started = changed.activities[result_draft.activity_id]
        cause = CallbackCause("action", started.data["action"], inv.agent_id, result_draft.event_id,
                              f"{inv.action_id}:0", started.travel_method_id)
        outcome = evaluate_callbacks(changed, context.catalog, cause, balance_before=before)
        changed = changed.model_copy(update={"agents": outcome.state.agents, "accounts": outcome.state.accounts})
        drafts.extend(outcome.drafts)
        actions.callback_causes.update(outcome.causes)
        before = None
    return changed, drafts


def _rejected_draft(
    context: EngineContext,
    state: WorldState,
    spec: ActionRecordSpec,
    info: tuple[ActionResult, dict[str, Any], list[str]],
) -> EventDraft:
    """@brief 构造顶层带错误码、原因与进行中活动快照的 action_rejected 证据草稿。"""
    result, guidance, causes = info
    active = next(iter(active_activities(state, result.agent_id)), None)
    data = {"error_code": result.error_code, **guidance}
    if active is not None:
        data["activity"] = activity_snapshot(active, title_index(context.catalog))
    return EventDraft(
        event_type="action_rejected", occurred_at=result.occurred_at, agent_id=result.agent_id,
        wakeup_id=spec.wakeup_id, action_id=spec.action_id,
        activity_id=active.activity_id if active else None, caused_by=causes, data=data,
    )


def _rejection_payload(
    request: dict[str, Any], error: BusinessRuleError, observation: dict[str, Any],
) -> dict[str, Any]:
    """@brief 按领域拒绝错误码与冻结观察生成公开引导并定位请求参数。"""
    guidance = rejection_guidance(error)
    unmet = error.unmet if isinstance(error, PreconditionError) else []
    guidance.update(business_feedback(request, str(error), guidance, unmet, observation))
    return guidance


def _prior_pulse_applied(context: EngineContext, drafts: Sequence[EventDraft], agent_id: int, now: int) -> bool:
    """@brief 检查当前模拟秒内该人物是否已施加过拒绝放松脉冲（含已提交与沙盒本刻草稿）。"""
    for event in context.store.read_agent_events("action_result", agent_id):
        if event.occurred_at == now and event.data.get(RESOURCE_FLOWS_FIELD):
            return True
    return any(
        draft.event_type == "action_result" and draft.occurred_at == now and draft.data.get(RESOURCE_FLOWS_FIELD)
        for draft in drafts
    )


def _location_reachable(ctx: LayerContext, origin: str, row: dict[str, Any]) -> bool:
    """@brief 判断从当前停留或锚定地点是否存在直达或可用出行路线到达目标地点。"""
    destination = row["object_id"]
    if destination == origin:
        return True
    return any(
        _travel_visible(ctx, destination, action["travel_method_id"])
        for action in row.get("actions", ()) if action.get("action") == "travel"
    )


def _matches_lookup_keyword(catalog: Any, row: dict[str, Any], target: dict[str, Any], needle: str) -> bool:
    """@brief 忽略大小写匹配目标名称、地点或商品类别、动作描述与时段摘要。"""
    fields: list[str] = [str(target.get("name") or ""), str(target.get("state") or "")]
    if row["kind"] == "location":
        fields.append(catalog.locations[row["object_id"]].kind)
    elif row["kind"] == "supply":
        category = catalog.goods[row["object_id"]].category
        fields.extend((category, GOODS_CATEGORY_LABELS.get(category, "")))
    for action in target.get("actions", ()):
        for key in ("action", "detail", "note", "reason"):
            value = action.get(key)
            if value:
                fields.append(str(value))
    return any(needle in value.casefold() for value in fields if value)


def _has_active_search(state: WorldState, agent_id: int) -> bool:
    """@brief 判断人物当前是否存在尚未结束或取消的进行中搜索活动。"""
    return any(
        act.agent_id == agent_id and act.kind == ActivityKind.SEARCH and act.status == ActivityStatus.ACTIVE
        for act in state.activities.values()
    )


def _finalize_invocation(
    context: EngineContext,
    actions: ActionHandlers,
    state: WorldState,
    entry: tuple[ToolInvocation, ActionResult, tuple[str, str]],
) -> tuple[WorldState, list[EventDraft]]:
    """@brief 写回活动 accepted_event_id、登记动作账本并构造 action_accepted 与 action_result 草稿。"""
    inv, result, event_ids = entry
    acc_id, res_id = event_ids
    changed = _stamp_accepted_activities(state, inv.action_id, res_id)
    spec = ActionRecordSpec(inv.wakeup_id, inv.action_id, inv.tool_name,
                            inv.request_sha256, inv.requested_event_id)
    changed = _remember_action(changed, spec, result)
    data = _result_event_data(result, changed, title_index(context.catalog))
    acc = EventDraft(
        event_id=acc_id, event_type="action_accepted", occurred_at=result.occurred_at,
        agent_id=result.agent_id, wakeup_id=spec.wakeup_id, action_id=spec.action_id,
        activity_id=result.activity_id, caused_by=[spec.requested_event_id], data=data,
    )
    res = EventDraft(
        event_id=res_id, event_type="action_result", occurred_at=result.occurred_at,
        agent_id=result.agent_id, wakeup_id=spec.wakeup_id, action_id=spec.action_id,
        activity_id=result.activity_id, caused_by=[spec.requested_event_id], data=data,
        resource_changes=actions.resource_changes.pop(spec.action_id, {}),
    )
    return changed, [acc, res]


def _stamp_accepted_activities(state: WorldState, action_id: str, res_id: str) -> WorldState:
    """@brief 将开始动作生成的结果事件身份写入其新创建活动的 data.accepted_event_id。"""
    activities = dict(state.activities)
    updated = False
    for aid, activity in activities.items():
        if activity.origin_action_id == action_id:
            activities[aid] = activity.model_copy(update={"data": {**activity.data, "accepted_event_id": res_id}})
            updated = True
    return state.model_copy(update={"activities": activities}) if updated else state


def _initial_wakeup_drafts(observations: ObservationBuilder, wakeup: AgentWakeupInput,
                           baseline_obs: dict[str, Any]) -> list[EventDraft]:
    """@brief 为单次唤醒构建 decision_started 与 observation 两条初始证据草稿。"""
    started = EventDraft(
        event_type="decision_started", occurred_at=wakeup.observed_at, agent_id=wakeup.agent_id,
        wakeup_id=wakeup.wakeup_id, visibility="system", source_kind="environment",
        data={"wakeup_id": wakeup.wakeup_id, "world_state_version": wakeup.world_state_version,
              "wake_reasons": list(wakeup.observation["wake_reasons"])},
    )
    stored, _ = observations.stored_observation(baseline_obs)
    obs = EventDraft(
        event_id=baseline_obs["observation_event_id"], event_type="observation",
        occurred_at=wakeup.observed_at, agent_id=wakeup.agent_id, wakeup_id=wakeup.wakeup_id,
        caused_by=[started.event_id], visibility="agent", source_kind="environment",
        observed_at=wakeup.observed_at, data=stored,
    )
    return [started, obs]


def _extract_raw_request(tool_name: str, arguments: BaseModel | Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    """@brief 提取工具参数原始字典与哈希；若传入错类型请求模型则立即以协议故障拒绝。"""
    model = REQUEST_MODELS[tool_name]
    if isinstance(arguments, Mapping):
        raw_req = dict(arguments)
        return raw_req, sha256_json({"tool_name": tool_name, "request": raw_req})
    if type(arguments) is not model:
        _LOGGER.error("Tool request model mismatch for %s: got %s", tool_name, type(arguments).__name__)
        raise ProtocolError(f"Tool request model mismatch for {tool_name}")
    return arguments.model_dump(mode="json"), _request_sha(tool_name, arguments)


def _validate_mapping_request(tool_name: str, arguments: Mapping[str, Any]) -> None:
    """@brief 校验原始字典参数是否满足严格请求模型，不合规时转为 ACTION_REQUEST_INVALID。"""
    model = REQUEST_MODELS[tool_name]
    try:
        model.model_validate(arguments)
    except ValidationError as error:
        report = argument_report(tool_name, error, model, dict(arguments), "ACTION_REQUEST_INVALID")
        raise Refusal("ACTION_REQUEST_INVALID", report.reason, fix=report.fix or "", items=report.items).error()


def _request_draft(wakeup: AgentWakeupInput, meta: tuple[str, str, dict[str, Any], str, str | None],
                   obs_id: str) -> EventDraft:
    """@brief 构造带唤醒归属、请求摘要及可选自注的 action_requested 草稿。"""
    call_id, tool_name, raw_req, req_sha, note = meta
    data: dict[str, Any] = {"tool_name": tool_name, "request": raw_req, "request_sha256": req_sha}
    if note is not None:
        data["self_note"] = note
    return EventDraft(
        event_type="action_requested", occurred_at=wakeup.observed_at, agent_id=wakeup.agent_id,
        wakeup_id=wakeup.wakeup_id, action_id=call_id, caused_by=[obs_id],
        visibility="agent", source_kind="model", data=data,
    )


def _extract_memories(tool_name: str, request: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """@brief 从已受理的 conclude_activity 请求读取待落盘存储形态记忆列表。"""
    if tool_name != "conclude_activity":
        return ()
    return tuple(dict(memory) for item in control_items(tool_name, request) for memory in item.memories)


def _exchange_draft(exchange: ModelCallEvidence, occurred_at: int, event_id: str,
                    caused_by: list[str], is_request: bool) -> EventDraft:
    """@brief 把 ModelCallEvidence 转换为只存一次请求正文的 model_request 或 model_response 草稿。"""
    data: dict[str, Any] = {
        "call_id": exchange.call_id, "phase": exchange.phase, "attempt": exchange.attempt,
        "request": copy.deepcopy(exchange.request) if is_request else None,
        "response": copy.deepcopy(exchange.response), "error": exchange.error,
        "purpose": exchange.purpose, "transport_attempts": [dict(t) for t in exchange.transport_attempts],
    }
    if isinstance(exchange.response, dict) and "protocol_feedback" in exchange.response:
        data["protocol_feedback"] = copy.deepcopy(exchange.response["protocol_feedback"])
    return EventDraft(
        event_id=event_id, event_type="model_request" if is_request else "model_response",
        occurred_at=occurred_at, agent_id=exchange.agent_id, wakeup_id=exchange.wakeup_id,
        caused_by=caused_by, visibility="system", source_kind="model", data=data,
    )
