"""@brief ModelEvidenceRecorder 的独立生活运行职责。"""

from __future__ import annotations
from worldmock.simulation_engine.decisions.decision_coordinator import DecisionCoordinator
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentsociety2.society import DecisionContext

import logging
import time
from typing import Any
from worldmock.foundation.errors import IntegrityError, ProtocolError
from worldmock.recording_engine.storage.opslog import transport_wait_record
from worldmock.recording_engine.events.models import EventDraft
from worldmock.foundation.serialization import sha256_json
from worldmock.recording_engine.events.memory_evidence import _memory_evidence_data, memory_write_points
from worldmock.simulation_engine.evidence.memory import due_reflection
from worldmock.simulation_engine.decisions.decision_start import maybe_action_request_start

from worldmock.simulation_engine.world.context import EngineContext

#: 失败发送记录的字段闭集。
_ATTEMPT_FIELDS = frozenset({"attempt", "error", "failed_after_ms", "wait_ms"})


def _attempt_fields(exchange: Any) -> dict:
    """@brief 核对证据带来的失败发送并给出写入信封的字段：请求证据不得带，返回空；响应与失败证据每项恰含四个字段、
    序号从 1 起连续，返回 transport_attempts 的副本。"""
    attempts = exchange.transport_attempts
    if exchange.phase == "request" and attempts:
        logging.error("Model request evidence must not carry transport attempts: %s", exchange.call_id)
        raise ProtocolError("Model request evidence must not carry transport attempts")
    valid = isinstance(attempts, tuple) and all(
        isinstance(item, dict) and set(item) == _ATTEMPT_FIELDS for item in attempts)
    if not valid or [item["attempt"] for item in attempts] != list(range(1, len(attempts) + 1)):
        logging.error("Model exchange transport attempts are invalid: %s", exchange.call_id)
        raise ProtocolError("Model exchange transport attempts are invalid")
    if exchange.phase == "request":
        return {}
    return {"transport_attempts": [dict(item) for item in attempts]}


class ModelEvidenceRecorder:
    """@brief 显式依赖上下文的ModelEvidenceRecorder。"""

    def __init__(self, context: EngineContext, decisions: DecisionCoordinator):
        """@brief 绑定决策开始簿记并独立保存模型请求索引。"""
        self.context, self.decisions = context, decisions
        #: (决策身份, 调用身份) → (请求事件身份, 请求规范 JSON 的 sha256)；响应或失败登记后移除。
        self.request_events: dict[tuple[str, str], tuple[str, str]] = {}

    def record_model_exchange(self, exchange: Any) -> None:
        """@brief 保存实际模型请求、响应或失败，并保持生活状态不变。

        本次决策的首个行动请求与其 decision_started 同批提交，decision_started 在前
        （docs/design/06-agent.md「宿主模型协议」）。
        请求只存在请求事件里；响应与失败事件的 request 为 null，经 caused_by 指向请求事件，并须带来与登记时摘要相同的请求
        （docs/design/05-recording-engine.md「事件与来源」）。响应与失败证据在信封顶层带 transport_attempts，不进 response。
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
        digest = sha256_json(exchange.request)
        cause = self._request_cause(key, exchange, digest)
        data = {
            "phase": exchange.phase, "call_id": exchange.call_id, "attempt": exchange.attempt,
            "request": exchange.request if exchange.phase == "request" else None,
            "response": exchange.response, "error": exchange.error,
        }
        if isinstance(exchange.response, dict) and "protocol_feedback" in exchange.response:
            data["protocol_feedback"] = exchange.response["protocol_feedback"]
        data.update(_attempt_fields(exchange))
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
        started = time.perf_counter()
        event = self.context.store.append_evidence([item for item in (start, draft) if item is not None])[-1]
        commit_ms = round((time.perf_counter() - started) * 1000, 1)
        if exchange.phase == "request":
            self.request_events[key] = (event.event_id, digest)
        else:
            self.request_events.pop(key)
        self._ops_exchange(context, exchange, commit_ms)

    def _request_cause(self, key: tuple[str, str], exchange: Any, digest: str) -> list[str]:
        """@brief 请求阶段不得重复登记，原因为空；响应与失败须有本进程登记的请求，且带来的请求摘要与登记时相同，
        原因是那条请求事件。"""
        if exchange.phase == "request":
            if key in self.request_events:
                raise ProtocolError("Model request call was recorded twice")
            return []
        if key not in self.request_events:
            raise ProtocolError("Model response has no recorded request")
        event_id, recorded = self.request_events[key]
        if digest != recorded:
            logging.error("Model %s for call %s carries a request that differs from its recorded request",
                          exchange.phase, exchange.call_id)
            raise ProtocolError("Model exchange request differs from its recorded request")
        return [event_id]

    def _ops_exchange(self, context: "DecisionContext", exchange: Any, commit_ms: float) -> None:
        """@brief 写模型交换运维日志（模型名、用量与这次证据写入耗时）；有失败发送时写传输等待，响应带协议纠错时写协议拒绝。"""
        response = exchange.response if isinstance(exchange.response, dict) else {}
        usage = response.get("usage") or {}
        ids = {"agent_id": context.agent_id, "decision_id": context.decision_id}
        ops, now = self.context.ops, self.context.state.current_time
        ops.log(now, "agent_runtime", "error" if exchange.phase == "failure" else "info",
                {"code": "MODEL_EXCHANGE", "detail": f"Model {exchange.phase} recorded for call {exchange.call_id}",
                 **ids, "model": response.get("model"), "tokens_in": usage.get("prompt_tokens"),
                 "tokens_out": usage.get("completion_tokens"), "commit_ms": commit_ms})
        if exchange.transport_attempts:
            ops.log(now, "agent_runtime", "warn", {**transport_wait_record(exchange.call_id,
                                                                            exchange.transport_attempts), **ids})
        feedback = response.get("protocol_feedback")
        if exchange.phase == "response" and isinstance(feedback, dict):
            ops.log(now, "agent_runtime", "warn",
                    {"code": "MODEL_PROTOCOL_REJECTED", **ids, "call_id": exchange.call_id,
                     "detail": f"Model response for call {exchange.call_id} was rejected: {feedback['error_code']}",
                     "error_code": feedback["error_code"]})

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
        self.context.store.append_evidence([draft])
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
        """@brief 步尾登记：观察属于本决策、事实窗口一致、没有反思条目；写入点只可能是 activity_conclusion。

        观察按事件身份读取；本决策的行动请求都登记在它的唯一观察之后，所以只读观察之后的请求。
        """
        store = self.context.store
        try:
            observation = store.read_event(data["observation_event_id"])
        except IntegrityError as exc:
            raise ProtocolError("Memory update observation is invalid") from exc
        if (observation.event_type != "observation" or observation.agent_id != context.agent_id
                or observation.decision_id != context.decision_id):
            raise ProtocolError("Memory update observation is invalid")
        visible_ids = {row["source_event_id"] for row in observation.data.get("verified_experiences", [])}
        if set(data["verified_event_ids"]) != visible_ids:
            raise ProtocolError("Memory update source window is invalid")
        if any(item.get("source") == "reflection" for item in data["episodes"]):
            raise ProtocolError("Step memory update carries a reflection episode")
        requests = store.read_agent_events("action_requested", context.agent_id, observation.event_seq)
        written = bool(data["created_episode_ids"] or data["merged_episode_ids"])
        return {"trigger": memory_write_points(requests, context.decision_id, written), "reflection": None}
