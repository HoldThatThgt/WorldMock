"""@brief 零点计划 plan_day 的请求、输出校验、模型证据与系统登记（docs/dev/SPEC-time-and-energy.md 第四章）。

plan_day 是运行时的独立后台任务（worldmock.runtime.settle_pending_day_plans），不是决策、不调用人物：
本模块给出请求模板与输出 Schema（模块常量，由 code_sha256 覆盖，不进运行清单的阶段键）、输出不合法时附回的
英文错误、带 purpose="plan_day" 的 model_request/model_response 证据，以及经引擎系统登记入口与计划一次提交的
environment_event（data.kind="day_plan_generated"）。幂等键为既有字段 AgentState.plan_submitted_dates。
"""

from __future__ import annotations

import copy
import json
import logging
from datetime import datetime
from typing import Any, Sequence
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from worldmock.errors import ProtocolError
from worldmock.footprints.models import EventDraft
from worldmock.life.models import PlanDayRequest, WorldState, deepseek_strict_schema
from worldmock.life.prompt_capacity import CONTEXT_THRESHOLD, model_context_length, request_token_count
from worldmock.life.schedule import (DayPlan, RemindTimeInvalid, day_end_seconds, initial_day_plan,
                                     registered_goals)

#: 零点计划请求与响应的用途标记；交付窗口、决策 token 统计与行为指标的决策请求按它排除。
PLAN_DAY_PURPOSE = "plan_day"
#: 零点计划的生成上限（docs/CONTRACTS.md「生成参数」）。
PLAN_DAY_MAX_TOKENS = 8000
#: 输出不合法时附回错误的重试次数上限：首次之外至多再请求 3 次，与传输重试预算分开计数。
PLAN_DAY_VALIDATION_RETRIES = 3
#: 提交当日目标清单的唯一工具名。
PLAN_DAY_TOOL = "submit_day_plan"
#: 输入中人物收尾自述的标注（与观察中的自述同一说法）。
SELF_REPORT_NOTE = "你自己记下的，未经核验"
#: 零点计划的中文模板（docs/CONTRACTS.md「请求组成」）。
PLAN_DAY_PROMPT = """
你要为一个人写出他这一天的当日目标清单。用户消息是一份 JSON，只含世界已经记下的事实：人物档案与背景原文、当天的上班安排、
回顾时段里已核验的经历、他本人已结算的动作结果、他收尾时自己记下的话（未经核验，只代表他的想法），
以及此刻他能看到的待办任务、还没办完的个人事务、收件箱、在途订单、联系人和上一天目标的推进情况。
按下面的规矩写：
一、写零到八条目标，按优先级从高到低排列；每条从给定的意图里选一个，同一意图只写一条。
二、goal 用一句中文写他打算做什么，可以写粗略的时段和要提前做的准备，不超过三百字；记录里没有的事不要编造。
三、remind_time 只在他需要在某个时刻被提醒或叫醒时填写，取当天本地时间 HH:MM，必须晚于生成时刻、早于当天结束；
不需要提醒就填 null。他收尾时写明了第二天几点起床或几点出门的，就给对应的目标定这个时刻的提醒。
四、只调用一次 submit_day_plan，提交完整清单。
五、人物档案的作息里已经有的事（睡觉、起床、上下班、吃饭）不写，除非今天与平常不同或需要提醒；写的时候写清楚哪里不同。
六、每条目标都写大致时刻 around_time：他打算在当天几点前后做这件事，取当天本地时间 HH:MM，只知道大致时段的写时段的中点；
大致时刻不会提醒他，需要提醒的照第三条填 remind_time。
七、个人事务已经记在待办里，不必照抄成目标；今天打算去办的，可以写一条目标说明什么时候办。
八、输入里有上班安排的日子，目标不要安排在上班时间离开上班地点，写明的休息时段和工作要求去别处的外出除外。
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


def fit_plan_day(payload: dict[str, Any], sentences: Sequence[str], model_name: str) -> tuple[dict, int]:
    """@brief 整次请求达到窗口 85% 时先裁掉较早的当日经历句子；仍超限按容量故障处理。

    @return (请求, 实际保留的经历句子条数；保留的是最后这些条)
    """
    limit = CONTEXT_THRESHOLD * model_context_length(model_name)
    kept = list(sentences)
    while True:
        request = prepare_plan_day({**payload, "已核验经历": kept}, model_name)
        if request_token_count(request) <= limit:
            return request, len(kept)
        if not kept:
            _LOGGER.error("Plan day request exceeds the model context after trimming experiences")
            raise RuntimeError("plan_day request capacity exceeded")
        kept = kept[1:]


def prepare_plan_day_correction(request: dict[str, Any], raw: dict[str, Any],
                                error: PlanDayOutputError) -> dict[str, Any]:
    """@brief 在原始请求之后附上前次响应与英文错误，要求重新提交完整清单。"""
    feedback = {"PREVIOUS_INVALID_RESPONSE": raw, "VALIDATION_ERROR": str(error),
                "INSTRUCTION": _CORRECTION_INSTRUCTION}
    message = {"role": "user", "content": json.dumps(
        feedback, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)}
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


def response_draft(now: int, agent_id: int, link: tuple[str, int, str],
                   raw: dict[str, Any], error: BaseException | None) -> EventDraft:
    """@brief 一次零点计划模型响应的证据：原始响应、用量与本地校验错误（合法时为空）。"""
    call_id, attempt, request_event_id = link
    data = {"phase": "response", "purpose": PLAN_DAY_PURPOSE, "call_id": call_id, "attempt": attempt,
            "request": None, "response": raw, "usage": raw.get("usage"),
            "error": str(error) if error is not None else None}
    return EventDraft(event_type="model_response", occurred_at=now, agent_id=agent_id,
                      caused_by=[request_event_id], visibility="system", source_kind="model", data=data)


def failure_draft(now: int, agent_id: int, link: tuple[str, int, str], error: BaseException) -> EventDraft:
    """@brief 零点计划的传输或配置失败证据（运行故障前先保存）。"""
    call_id, attempt, request_event_id = link
    data = {"phase": "failure", "purpose": PLAN_DAY_PURPOSE, "call_id": call_id, "attempt": attempt,
            "request": None, "response": None, "error": repr(error)}
    return EventDraft(event_type="model_response", occurred_at=now, agent_id=agent_id,
                      caused_by=[request_event_id], visibility="system", source_kind="model", data=data)


def _tool_arguments(raw: dict[str, Any]) -> str:
    """@brief 取唯一 submit_day_plan 调用的参数文本；结构不符是可附回的输出错误。"""
    choices = raw.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise PlanDayOutputError("response must contain exactly one choice")
    message = choices[0].get("message")
    calls = message.get("tool_calls") if isinstance(message, dict) else None
    if not isinstance(calls, list) or len(calls) != 1 or not isinstance(calls[0], dict):
        raise PlanDayOutputError("response must contain exactly one submit_day_plan tool call")
    function = calls[0].get("function")
    if not isinstance(function, dict) or function.get("name") != PLAN_DAY_TOOL:
        raise PlanDayOutputError("the only tool call must be submit_day_plan")
    arguments = function.get("arguments")
    if not isinstance(arguments, str):
        raise PlanDayOutputError("submit_day_plan arguments must be a JSON string")
    return arguments


def _schema_feedback(error: ValidationError) -> str:
    """@brief 把请求模型的校验错误压成一行英文说明（位置与原因），附回模型重试。"""
    parts = []
    for item in error.errors():
        location = ".".join(str(part) for part in item.get("loc", ())) or "goals"
        parts.append(f"{location}: {item.get('msg', 'invalid value')}")
    return "; ".join(parts)
