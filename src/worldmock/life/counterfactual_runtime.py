"""@brief CounterfactualRuntime 的独立生活运行职责。"""

from __future__ import annotations
from worldmock.life.world_ops import _current_branch_event
from worldmock.life.world_ops import _request_sha

from worldmock.errors import ProtocolError
from worldmock.footprints.models import EventDraft
from worldmock.life.models import CounterfactualIntervention, CounterfactualReceipt

from worldmock.life.engine_context import EngineContext


class CounterfactualRuntime:
    """@brief 显式依赖上下文的CounterfactualRuntime。"""

    def __init__(self, context: EngineContext):
        """@brief 只持有当前分支的唯一状态上下文。"""
        self.context = context

    def apply_counterfactual(self, intervention: CounterfactualIntervention, round_open: bool) -> CounterfactualReceipt:
        """@brief 在新反事实分支首次决策前提交一次对等余额干预。"""
        if type(intervention) is not CounterfactualIntervention:
            raise ProtocolError("Counterfactual intervention must use the strict life model")
        digest = _request_sha("counterfactual_intervention", intervention)
        self._validate_counterfactual(intervention, digest, round_open)
        agent = self.context.state.agents[intervention.agent_id]
        before = self.context.state.accounts[agent.balance_account_id]
        staged = self.context.state.model_copy(deep=True)
        staged.accounts[agent.balance_account_id] = intervention.balance_fen
        staged.accounts["external:scenario"] += before - intervention.balance_fen
        staged.version = self.context.state.version + 1
        changes = {
            f"account:{agent.balance_account_id}": intervention.balance_fen - before,
            "account:external:scenario": before - intervention.balance_fen,
        }
        data = {
            "kind": "counterfactual_intervention",
            "intervention_kind": intervention.kind,
            "hypothesis": intervention.hypothesis,
            "source_event_ids": list(intervention.source_event_ids),
            "intervention_sha256": digest,
            "before_balance_fen": before,
            "after_balance_fen": intervention.balance_fen,
        }
        draft = EventDraft(
            event_type="environment_event",
            occurred_at=staged.current_time,
            agent_id=intervention.agent_id,
            caused_by=list(intervention.source_event_ids),
            visibility="evaluation",
            source_kind="scenario",
            data=data,
            resource_changes=changes,
        )
        event = self.context.commit_step(staged, [draft])[0]
        return CounterfactualReceipt(
            intervention_sha256=digest,
            event_id=event.event_id,
            agent_id=intervention.agent_id,
            before_balance_fen=before,
            after_balance_fen=intervention.balance_fen,
            world_state_version=staged.version,
        )

    def _validate_counterfactual(self, intervention: CounterfactualIntervention, digest: str, round_open: bool) -> None:
        """@brief 核对分支身份、唯一时序及父检查点以前的人物可见证据。"""
        manifest = self.context.store.manifest
        if (
            manifest.get("branch_kind") != "counterfactual"
            or manifest.get("intervention_sha256") != digest
            or (not manifest.get("parent_checkpoint_id"))
        ):
            raise ProtocolError("Counterfactual branch manifest is invalid")
        events = self.context.store.read_events(self.context.store.last_event_seq)
        branch = _current_branch_event(events, manifest)
        if branch is None:
            raise ProtocolError("Counterfactual branch creation evidence is invalid")
        child_events = [event for event in events if event.event_seq > branch.event_seq]
        if round_open or any(
            (
                event.event_type == "decision_started"
                or (event.event_type == "environment_event" and event.data.get("kind") == "counterfactual_intervention")
                for event in child_events
            )
        ):
            raise ProtocolError("Counterfactual intervention must precede the first decision and occur once")
        self._validate_counterfactual_sources(intervention, events)

    def _validate_counterfactual_sources(self, intervention, events) -> None:
        """@brief 要求全部来源来自父检查点截止内且对目标人物可见。"""
        branch = _current_branch_event(events, self.context.store.manifest)
        if branch is None or intervention.agent_id not in self.context.state.agents:
            raise ProtocolError("Counterfactual branch or target agent is invalid")
        sources = {event.event_id: event for event in events if event.event_seq < branch.event_seq}
        for event_id in intervention.source_event_ids:
            event = sources.get(event_id)
            if event is None or event.visibility != "agent" or event.agent_id not in {None, intervention.agent_id}:
                raise ProtocolError("Counterfactual source is not visible to the target agent")
