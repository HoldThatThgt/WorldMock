"""@brief ModelEvidenceRecorder 的独立生活运行职责。"""

from __future__ import annotations
from worldmock.life.decision_coordinator import DecisionCoordinator
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentsociety2.society import DecisionContext

import logging
from typing import Any
from worldmock.errors import ProtocolError
from worldmock.footprints.models import EventDraft
from worldmock.life.memory_evidence import _memory_evidence_data, due_reflection, memory_write_points
from worldmock.life.decision_start import maybe_action_request_start
from worldmock.life.models import validated_payload

from worldmock.life.engine_context import EngineContext


class ModelEvidenceRecorder:
    """@brief 显式依赖上下文的ModelEvidenceRecorder。"""

    def __init__(self, context: EngineContext, decisions: DecisionCoordinator):
        """@brief 绑定决策开始簿记并独立保存模型请求索引。"""
        self.context, self.decisions = context, decisions
        self.request_events: dict[tuple[str, str], str] = {}

    def record_model_exchange(self, exchange: Any) -> None:
        """@brief 保存实际模型请求、响应或失败，并保持生活状态不变。

        本次决策的首个行动请求与其 decision_started 同批提交，decision_started 在前
        （docs/design/06-agent.md「宿主模型协议」）。
        """
        context = exchange.context
        self.decisions.validate_context(context)
        if (
            exchange.phase not in {"request", "response", "failure"}
            or exchange.attempt < 1
            or (not isinstance(exchange.call_id, str))
            or (not exchange.call_id)
        ):
            raise ProtocolError("Model exchange phase or attempt is invalid")
        key = (context.decision_id, exchange.call_id)
        cause = []
        if exchange.phase == "request" and key in self.request_events:
            raise ProtocolError("Model request call was recorded twice")
        if exchange.phase != "request":
            if key not in self.request_events:
                raise ProtocolError("Model response has no recorded request")
            cause = [self.request_events[key]]
        data = {
            "phase": exchange.phase, "call_id": exchange.call_id, "attempt": exchange.attempt,
            "request": exchange.request, "response": exchange.response, "error": exchange.error,
        }
        if isinstance(exchange.response, dict) and "protocol_feedback" in exchange.response:
            data["protocol_feedback"] = exchange.response["protocol_feedback"]
        draft = EventDraft(
            event_type="model_request" if exchange.phase == "request" else "model_response",
            occurred_at=self.context.state.current_time,
            agent_id=context.agent_id,
            decision_id=context.decision_id,
            caused_by=cause,
            visibility="system",
            source_kind="model",
            data=data,
        )
        start = maybe_action_request_start(
            self.decisions.pending_decision_starts, context.agent_id, self.context.state.current_time, exchange
        )
        event = self.context.store.commit(
            validated_payload(self.context.state), [item for item in (start, draft) if item is not None])[-1]
        if exchange.phase == "request":
            self.request_events[key] = event.event_id
        else:
            self.request_events.pop(key)
        self._ops_exchange(context, exchange)

    def _ops_exchange(self, context: "DecisionContext", exchange: Any) -> None:
        """@brief 写模型交换操作日志，带响应中可用的模型名与 token 用量。"""
        response = exchange.response if isinstance(exchange.response, dict) else {}
        usage = response.get("usage") or {}
        self.context.ops.log(
            self.context.state.current_time,
            "agent_runtime",
            "error" if exchange.phase == "failure" else "info",
            {"code": "MODEL_EXCHANGE",
            "detail": f"Model {exchange.phase} recorded for call {exchange.call_id}",
            "agent_id": context.agent_id,
            "decision_id": context.decision_id,
            "model": response.get("model"),
            "tokens_in": usage.get("prompt_tokens"),
            "tokens_out": usage.get("completion_tokens")},
        )

    def record_memory_update(self, evidence: Any) -> None:
        """@brief 保存人物已落盘记忆的枚举操作、内容身份、写入点（trigger）与就寝反思的归属（reflection）。

        按来源观察是否为空分两路：为空是取观察之前的就寝反思登记，非空是步尾登记；两路都由环境按已提交事实核对。
        """
        self.decisions.validate_context(evidence.context)
        try:
            data = _memory_evidence_data(evidence)
            reflecting = data["observation_event_id"] is None
            register = self._reflection_registration if reflecting else self._tail_registration
            data.update(register(evidence.context, data), decision_id=evidence.context.decision_id)
        except (ProtocolError, KeyError, AttributeError, TypeError) as exc:
            logging.error("Memory update evidence rejected: %s", exc)
            raise ProtocolError("Memory update evidence is invalid") from exc
        draft = EventDraft(
            event_type="memory_updated",
            occurred_at=self.context.state.current_time,
            agent_id=evidence.context.agent_id,
            decision_id=evidence.context.decision_id,
            visibility="system",
            source_kind="environment",
            data=data,
        )
        self.context.store.commit(validated_payload(self.context.state), [draft])
        self.context.ops.log(
            self.context.state.current_time,
            "memory",
            "info",
            {"code": "MEMORY_UPDATED",
            "detail": f"Memory update recorded {len(data['episodes'])} episodes",
            "agent_id": evidence.context.agent_id,
            "decision_id": evidence.context.decision_id},
        )

    def _reflection_registration(self, context: "DecisionContext", data: dict) -> dict:
        """@brief 就寝反思登记：此刻该反思、这次决策还没有观察、这一时刻还没登记过反思，且只写了反思条目。"""
        store, now = self.context.store, self.context.state.current_time
        due = due_reflection(self.context.state, context.agent_id)
        observed = store.read_decision_observations(context.agent_id, context.decision_id, now)
        earlier = store.read_agent_events("memory_updated", context.agent_id, store.last_event_seq_before(now))
        if (due is None or observed or any(event.data.get("reflection") is not None for event in earlier)
                or data["operation"] != "append_episodes" or data["verified_event_ids"]
                or data["memory_before_sha256"] != data["memory_sha256"]
                or any(item.get("source") != "reflection" for item in data["episodes"])):
            raise ProtocolError("Reflection memory update is not due or carries other content")
        return {"trigger": ["reflection"], "reflection": due}

    def _tail_registration(self, context: "DecisionContext", data: dict) -> dict:
        """@brief 步尾登记：观察属于本决策、事实窗口一致、没有反思条目；写入点只可能是 activity_conclusion。"""
        store = self.context.store
        observation = next((event for event in reversed(store.read_agent_events("observation", context.agent_id))
                            if event.event_id == data["observation_event_id"]
                            and event.decision_id == context.decision_id), None)
        if observation is None:
            raise ProtocolError("Memory update observation is invalid")
        visible_ids = {row["source_event_id"] for row in observation.data.get("verified_experiences", [])}
        if set(data["verified_event_ids"]) != visible_ids:
            raise ProtocolError("Memory update source window is invalid")
        if any(item.get("source") == "reflection" for item in data["episodes"]):
            raise ProtocolError("Step memory update carries a reflection episode")
        requests = store.read_agent_events("action_requested", context.agent_id)
        written = bool(data["created_episode_ids"] or data["merged_episode_ids"])
        return {"trigger": memory_write_points(requests, context.decision_id, written), "reflection": None}
