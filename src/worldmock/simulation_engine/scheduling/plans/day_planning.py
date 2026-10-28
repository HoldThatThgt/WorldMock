"""@brief 零点计划 plan_day 的请求、输出校验、模型证据与系统登记（docs/design/04-simulation-engine.md）。

plan_day 是运行时的独立后台任务（worldmock.runtime.settle_pending_day_plans），不是决策、不调用人物：
本模块给出请求模板与输出 Schema（模块常量，由 code_sha256 覆盖，不进运行清单的阶段键）、输出不合法时附回的
英文错误、带 purpose="plan_day" 的 model_request/model_response 证据，以及经引擎系统登记入口与计划一次提交的
environment_event（data.kind="day_plan_generated"）。幂等键为既有字段 AgentState.plan_submitted_dates。
"""

from __future__ import annotations

from worldmock.foundation.model_feedback import correction_message, schema_feedback, tool_arguments
import copy
import json
import logging
from datetime import datetime
from typing import Any, Sequence
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from worldmock.configuration.inputs.models import ModelMetadata

from worldmock.foundation.errors import ProtocolError
from worldmock.recording_engine.events.models import EventDraft
from worldmock.state.model.exchange import PLAN_DAY_PURPOSE
from worldmock.state.interaction.requests import PlanDayRequest
from worldmock.state.world.world import WorldState
from worldmock.state.interaction.schema import deepseek_strict_schema
from worldmock.agents.prompt.prompt_capacity import CONTEXT_THRESHOLD, model_context_length, request_token_count
from worldmock.state.planning.schedule import (
    DayPlan,
    RemindTimeInvalid,
    day_end_seconds,
    initial_day_plan,
    registered_goals,
)

#: 零点计划的生成上限（docs/design/06-agent.md「宿主模型协议」）。
PLAN_DAY_MAX_TOKENS = 8000
#: 提交当日目标清单的唯一工具名。
PLAN_DAY_TOOL = "submit_day_plan"
#: 输入中人物收尾自述的标注（与观察中的自述同一说法）。
SELF_REPORT_NOTE = "你自己记下的，未经核验"
#: 零点计划的中文模板（docs/design/06-agent.md「零点计划」）。
PLAN_DAY_PROMPT = """
根据一个人的真实档案与背景、当天工作安排、已核验经历、已结算动作结果、明确为主观的收尾记录、
未完成个人事务、本人在途订单以及上一天的目标，为他写今天的目标清单。
只调用一次 submit_day_plan，提交完整的零至八条中文目标，按优先级排列，意图不重复。记录中没有的事情不要编造。
goal 写今天特别要做的事情，不超过三百字；平常每天的作息习惯不重复列目标。
remind_time 是当天本地 HH:MM，必须晚于生成时刻且早于次日零点，不需要提醒时填 null。
提醒只是醒着时到点请他决策，不会打断睡眠。around_time 写当天大致 HH:MM 或 null，不触发提醒；
正文尽量用上午、午饭后或先后关系说明时段。
个人事务已经记在待办中，无需抄成目标；计划不创建、完成或取消个人事务，也不判定目标是否办成。
上班安排的 day 是今天上不上班的唯一依据，不看星期几：休息日没有通勤、打卡和上班，即使是星期一到星期五；工作日照 hours 与 place 上班，即使是周末。\
工作安排是固定世界事实，今日目标不能改变班次；不要安排在班次中离开工作地点，已声明休息时段除外。
""".strip()
#: 附回重试时的英文指令。
_CORRECTION_INSTRUCTION = "Return a complete new submit_day_plan tool call using the unchanged schema."

_LOGGER = logging.getLogger(__name__)


class PlanDayOutputError(ValueError):
    """@brief 模型输出不是合法计划（JSON、条数、意图唯一性、文字长度、remind_time 格式或时刻、around_time 格式）；
    英文说明附回重试。"""


def plan_date(now: int, timezone_name: str) -> str:
    """@brief 生成时刻所在的场景本地日期（plan_submitted_dates 的取值）。"""
    return datetime.fromtimestamp(now, ZoneInfo(timezone_name)).date().isoformat()


def agents_missing_plan(state: WorldState, timezone_name: str) -> list[int]:
    """@brief 缺当日计划的人物（按编号排序）；运行已到终点时为空，零点计划不执行。"""
    if state.current_time >= state.end_time:
        return []
    date = plan_date(state.current_time, timezone_name)
    return [agent_id for agent_id in sorted(state.agents)
            if date not in state.agents[agent_id].plan_submitted_dates]


def prepare_plan_day(payload: dict[str, Any], model_name: str) -> dict[str, Any]:
    """@brief 形成零点计划请求：中文模板、JSON 输入、唯一严格工具，思考关闭、无内部重试且给出 max_tokens。"""
    tool = {"type": "function", "function": {
        "name": PLAN_DAY_TOOL, "strict": True, "description": "Submit today's complete ordered goal list.",
        "parameters": deepseek_strict_schema(PlanDayRequest)}}
    messages = [{"role": "system", "content": PLAN_DAY_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False, sort_keys=True)}]
    return {"model": model_name, "messages": messages, "stream": False, "tools": [tool],
            "tool_choice": "required", "max_tokens": PLAN_DAY_MAX_TOKENS, "max_retries": 0,
            "extra_body": {"thinking": {"type": "disabled"}}}


def fit_plan_day(payload: dict[str, Any], sentences: Sequence[str], model_name: str,
                 metadata: ModelMetadata) -> tuple[dict, int]:
    """@brief 整次请求达到窗口 85% 时先裁掉较早的当日经历句子；仍超限按容量故障处理。

    @return (请求, 实际保留的经历句子条数；保留的是最后这些条)
    """
    limit = CONTEXT_THRESHOLD * model_context_length(model_name, metadata)
    kept = list(sentences)
    while True:
        request = prepare_plan_day({**payload, "已核验经历": kept}, model_name)
        request["max_tokens"] = min(PLAN_DAY_MAX_TOKENS, metadata.max_output_tokens)
        if request_token_count(request) <= limit:
            return request, len(kept)
        if not kept:
            _LOGGER.error("Plan day request exceeds the model context after trimming experiences")
            raise RuntimeError("plan_day request capacity exceeded")
        kept = kept[1:]


def prepare_plan_day_correction(request: dict[str, Any], raw: dict[str, Any],
                                error: PlanDayOutputError) -> dict[str, Any]:
    """@brief 在原始请求之后附上前次响应与英文错误，要求重新提交完整清单。"""
    message = correction_message(raw, error, _CORRECTION_INSTRUCTION)
    corrected = copy.deepcopy(request)
    corrected["messages"] = [*request["messages"], message]
    return corrected


def parse_plan_day(raw: dict[str, Any], now: int, timezone_name: str) -> PlanDayRequest:
    """@brief 严格解析唯一工具调用并按第四章校验目标；不合法时抛出可附回的 PlanDayOutputError。"""
    arguments = _tool_arguments(raw)
    try:
        request = PlanDayRequest.model_validate_json(arguments)
    except ValidationError as error:
        raise PlanDayOutputError(_schema_feedback(error)) from error
    try:
        generated_plan(request, now, timezone_name)
    except RemindTimeInvalid as error:
        clock = f"{datetime.fromtimestamp(now, ZoneInfo(timezone_name)):%H:%M}"
        raise PlanDayOutputError(
            f"every remind_time must be later than the generation time {clock} "
            "and earlier than the next midnight; use null when no reminder is needed") from error
    return request


def generated_plan(request: PlanDayRequest, now: int, timezone_name: str) -> DayPlan:
    """@brief 以生成时刻登记目标：提醒换算为当日绝对时刻并须晚于生成时刻、早于次日零点；大致时刻只换算、不另校验
    （纯函数，登记与重试共用）。"""
    return initial_day_plan(registered_goals(request.goals, now, timezone_name), day_end_seconds(now, timezone_name))


def registration(state: WorldState, agent_id: int, request: PlanDayRequest,
                 links: tuple[Sequence[str], Sequence[str]],
                 timezone_name: str) -> tuple[WorldState, DayPlan, EventDraft]:
    """@brief 系统登记：写 day_plan 与 plan_submitted_dates，并形成 day_plan_generated 登记事件。

    当日已登记为协议错误（调用方先按 plan_submitted_dates 拦下）；目标不合法同为协议错误（调用方已按同一纯函数校验）。

    @param links (输入来源事件身份, 直接原因事件身份)
    """
    sources, causes = links
    now = state.current_time
    date = plan_date(now, timezone_name)
    agent = state.agents[agent_id]
    if date in agent.plan_submitted_dates:
        raise ProtocolError("Day plan was already generated for this local day")
    try:
        plan = generated_plan(request, now, timezone_name)
    except ValueError as error:
        raise ProtocolError("Generated day plan is invalid") from error
    staged = state.model_copy(deep=True)
    staged.agents[agent_id] = agent.model_copy(
        update={"day_plan": plan, "plan_submitted_dates": set(agent.plan_submitted_dates) | {date}}, deep=True)
    draft = EventDraft(event_type="environment_event", occurred_at=now, agent_id=agent_id,
                       caused_by=list(causes), visibility="system", source_kind="model",
                       data={"kind": "day_plan_generated", "day_plan": plan.model_dump(mode="json"),
                             "source_event_ids": list(dict.fromkeys(sources))})
    return staged, plan, draft


def request_draft(now: int, agent_id: int, link: tuple[str, int, str | None],
                  request: dict[str, Any]) -> EventDraft:
    """@brief 一次零点计划模型请求的系统私有证据（重试请求回链前次响应）。"""
    call_id, attempt, previous_event_id = link
    data = {"phase": "request", "purpose": PLAN_DAY_PURPOSE, "call_id": call_id, "attempt": attempt,
            "request": request, "response": None, "error": None}
    return EventDraft(event_type="model_request", occurred_at=now, agent_id=agent_id,
                      caused_by=[previous_event_id] if previous_event_id else [], visibility="system",
                      source_kind="model", data=data)


def response_draft(now: int, agent_id: int, link: tuple[str, int, str, tuple[dict, ...]],
                   raw: dict[str, Any], error: BaseException | None) -> EventDraft:
    """@brief 一次零点计划模型响应的证据：原始响应、用量与本地校验错误（合法时为空）及这次调用的失败发送。"""
    call_id, attempt, request_event_id, transport_attempts = link
    data = {"phase": "response", "purpose": PLAN_DAY_PURPOSE, "call_id": call_id, "attempt": attempt,
            "request": None, "response": raw, "usage": raw.get("usage"),
            "error": str(error) if error is not None else None, "transport_attempts": list(transport_attempts)}
    return EventDraft(event_type="model_response", occurred_at=now, agent_id=agent_id,
                      caused_by=[request_event_id], visibility="system", source_kind="model", data=data)


def failure_draft(now: int, agent_id: int, link: tuple[str, int, str, tuple[dict, ...]],
                  error: BaseException) -> EventDraft:
    """@brief 零点计划的传输或配置失败证据（运行故障前先保存）及这次调用的失败发送。"""
    call_id, attempt, request_event_id, transport_attempts = link
    data = {"phase": "failure", "purpose": PLAN_DAY_PURPOSE, "call_id": call_id, "attempt": attempt,
            "request": None, "response": None, "error": repr(error), "transport_attempts": list(transport_attempts)}
    return EventDraft(event_type="model_response", occurred_at=now, agent_id=agent_id,
                      caused_by=[request_event_id], visibility="system", source_kind="model", data=data)


def _tool_arguments(raw: dict[str, Any]) -> str:
    """@brief 取得唯一零点计划调用的 JSON 参数。"""
    choices = raw.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        _LOGGER.error("Plan day response does not contain exactly one choice")
        raise PlanDayOutputError("response must contain exactly one choice")
    message = choices[0].get("message")
    calls = message.get("tool_calls") if isinstance(message, dict) else None
    return tool_arguments(calls, PLAN_DAY_TOOL, PlanDayOutputError)


def _schema_feedback(error: ValidationError) -> str:
    """@brief 保留零点计划的根字段名称并共用 Schema 错误格式。"""
    return schema_feedback(error, "goals")
