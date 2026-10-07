"""@brief 同秒续接的环境侧：从模型证据重建上一决策的末次行动请求与响应（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第二章「同秒续接」）。

成链条件：同一人物在同一模拟秒内的相邻两次决策，同一 session；两者之间没有反事实干预（反事实分支一律断链，普通恢复
照常续接）；此前各 assistant 回合都带非空 reasoning_content；上一决策以恰好一个世界请求结束。任一条件不满足、模型证据
缺失或运行开关 same_second_continuation 关闭时断链（null），下一次请求从新快照构造。

链对象在观察事件提交之后挂到观察返回体的私有键 continuation 上交给宿主：不另存、不导出，并列入禁止渲染的键表。
messages 是上一决策末次行动请求的全部消息（原样取自 model_request），assistant 是它的最终 assistant 消息，result 是
该世界请求的公开结果（与进度层 tool_call.result 同一投影），events 是上一决策观察之后同秒发生、且不是本结果的进度层
事件（其记忆查询已作为原生回合留在 messages 中，不再列出）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from worldmock.footprints.models import Event
from worldmock.footprints.store import FootprintStore
from worldmock.life.decision_start import is_action_request
from worldmock.life.models import REQUEST_MODELS, WorldState
from worldmock.life.progress_layer import records_after, session_bound

#: 观察返回体上的续接私有键。
CONTINUATION_KEY = "continuation"
#: 宿主与 AS2 构造消息时的键序（tool 消息为 role、tool_call_id、content；assistant 为 role、content、tool_calls、
#: reasoning_content；system 与 user 为 role、content）。
_MESSAGE_KEYS = ("role", "tool_call_id", "content", "tool_calls", "reasoning_content")


def continuation_enabled(manifest: Mapping[str, Any]) -> bool:
    """@brief 运行开关 same_second_continuation（清单 stages.action）；清单没有阶段身份（测试建库路径）时按缺省开。"""
    identity = manifest.get("stage_model_identity")
    if identity is None:
        return True
    return identity["stages"]["action"]["same_second_continuation"]


def same_second_chain(store: FootprintStore, state: WorldState, observations: Any,
                      agent_id: int, observation_event_id: str) -> dict | None:
    """@brief 本次观察对应决策的续接链：上一决策的末次行动请求、最终 assistant、世界请求的调用身份、公开结果与同秒新事件。

    读取唯一世界状态和观察构造职责的层上下文；任一断链条件成立时返回 None。
    """
    if not continuation_enabled(store.manifest):
        return None
    previous = _previous_observation(store, state, agent_id, observation_event_id)
    if previous is None or _session_changed(store, state, agent_id, previous):
        return None
    if _counterfactual_since(store, state, previous):
        return None
    evidence = _decision_evidence(store, agent_id, previous)
    if evidence is None:
        return None
    messages, assistant, requested = evidence
    context = observations._layer_context(state.agents[agent_id], [])
    records = records_after(context, store, previous.event_seq)
    own = [record for record, sources in records if requested.event_id in sources]
    if len(own) != 1:
        return None
    return {"messages": messages, "assistant": assistant, "tool_call_id": assistant["tool_calls"][0]["id"],
            "result": own[0]["result"], "events": [record for record, _ in records if record["type"] != "tool_call"]}


def _previous_observation(store: FootprintStore, state: WorldState, agent_id: int,
                          observation_event_id: str) -> Event | None:
    """@brief 本秒内本人紧邻本次观察之前的观察（属于另一决策）；本次是本秒首次观察或同一决策重放时为 None。"""
    observations = store.read_agent_events("observation", agent_id, store.last_event_seq_before(state.current_time))
    index = [event.event_id for event in observations].index(observation_event_id)
    if index == 0:
        return None
    current, previous = observations[index], observations[index - 1]
    return previous if previous.decision_id != current.decision_id else None


def _session_changed(store: FootprintStore, state: WorldState, agent_id: int, previous: Event) -> bool:
    """@brief 上一决策之后当前 session 换过：开启它的边界提交在上一观察之后（出发、途中结束出行），或开始晚于上一观察。"""
    started_at, lower = session_bound(store, state, agent_id)
    return started_at > previous.occurred_at or lower > previous.event_seq


def _counterfactual_since(store: FootprintStore, state: WorldState, previous: Event) -> bool:
    """@brief 上一决策之后有反事实干预：任一人物的干预事件在其后，或当前是反事实分支而上一观察来自父运行。"""
    if store.manifest.get("branch_kind") == "counterfactual" and previous.run_id != store.manifest["run_id"]:
        return True
    return any(event.data.get("kind") == "counterfactual_intervention" for agent_id in state.agents
               for event in store.read_agent_events("environment_event", agent_id, previous.event_seq))


def _decision_evidence(store: FootprintStore, agent_id: int, previous: Event) -> tuple[list, dict, Event] | None:
    """@brief 上一决策的末次行动请求消息、最终 assistant 与唯一的世界请求登记；证据缺失、不相邻或缺思考时为 None。"""
    after, decision_id = previous.event_seq, previous.decision_id
    registered = store.read_agent_events("action_requested", agent_id, after)
    if [event.decision_id for event in registered] != [decision_id]:
        return None
    requests = [event for event in store.read_agent_events("model_request", agent_id, after)
                if event.decision_id == decision_id and is_action_request(event.data.get("request"))]
    if not requests:
        return None
    responses = [event for event in store.read_agent_events("model_response", agent_id, requests[-1].event_seq)
                 if requests[-1].event_id in event.caused_by and event.data.get("phase") == "response"
                 and event.data.get("error") is None]
    assistant = _final_assistant(responses[0].data.get("response")) if len(responses) == 1 else None
    if assistant is None or assistant["tool_calls"][0]["function"]["name"] != registered[0].data["tool_name"]:
        return None
    messages = [_wire_message(item) for item in requests[-1].data["request"]["messages"]]
    if not all(_has_reasoning(item) for item in messages if item.get("role") == "assistant"):
        return None
    return messages, assistant, registered[0]


def _wire_message(message: Mapping[str, Any]) -> dict:
    """@brief 恢复一条已落库消息的发送键序：事件以规范 JSON（键排序）保存，续接前缀须与实际发出的请求逐字节相同。

    宿主与 AS2 构造消息的键序固定：role、tool_call_id、content、tool_calls、reasoning_content；工具调用为 id、type、
    function（name、arguments）。其余键按原样排在后面。
    """
    wire = _ordered(message, _MESSAGE_KEYS)
    if "tool_calls" in wire:
        wire["tool_calls"] = [_wire_call(call) for call in wire["tool_calls"]]
    return wire


def _wire_call(call: Mapping[str, Any]) -> dict:
    """@brief 一个工具调用的发送键序（id、type、function；function 为 name、arguments），形状与 AS2 回传的工具调用相同。"""
    wire = _ordered(call, ("id", "type", "function"))
    wire["function"] = _ordered(wire["function"], ("name", "arguments"))
    return wire


def _ordered(value: Mapping[str, Any], keys: tuple[str, ...]) -> dict:
    """@brief 按给定键序重排字典，未列出的键保持原有相对顺序排在后面。"""
    return {**{key: value[key] for key in keys if key in value},
            **{key: item for key, item in value.items() if key not in keys}}


def _final_assistant(response: Any) -> dict | None:
    """@brief 由持久化响应重建最终 assistant 消息（与 AS2 回传的形状与键序相同）；不是恰好一个世界请求或缺思考时为 None。"""
    choices = response.get("choices") if isinstance(response, dict) else None
    first = choices[0] if isinstance(choices, list) and choices else None
    message = first.get("message") if isinstance(first, dict) else None
    calls = message.get("tool_calls") if isinstance(message, dict) else None
    if not isinstance(calls, list) or len(calls) != 1 or not isinstance(calls[0], dict) or not _has_reasoning(message):
        return None
    function = calls[0].get("function")
    if not isinstance(function, dict) or function.get("name") not in REQUEST_MODELS:
        return None
    call = {"id": str(calls[0].get("id") or ""), "type": "function",
            "function": {"name": function["name"], "arguments": str(function.get("arguments") or "{}")}}
    return {"role": "assistant", "content": message.get("content"), "tool_calls": [call],
            "reasoning_content": message["reasoning_content"]}


def _has_reasoning(message: Mapping[str, Any]) -> bool:
    """@brief assistant 消息带非空 reasoning_content。"""
    reasoning = message.get("reasoning_content")
    return isinstance(reasoning, str) and bool(reasoning)
