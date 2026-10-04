"""@brief 就寝反思的严格工具通道：文案、唯一工具的 Schema、本地校验与校验重试的附回消息。

就寝反思的模型调用只带 submit_reflection 一个严格工具（docs/dev/SPEC-errands-workday.md 1.1）：响应须恰好一次该工具的
调用，message.content 不读取；条数、引用与正文都在本地校验，不合合同时抛 ReflectionOutputError，由记忆运行时
（custom/agents/life_memory.py）附回英文错误做有界重试。文案、Schema 与重试次数是本模块的常量，只由 code_sha256 绑定：
submit_reflection 不属于人物工具面，不进 tool_schema_sha256，文案不进 prompt_sha256。
"""

from __future__ import annotations

from worldmock.model_feedback import correction_message, schema_feedback, tool_arguments
import json
import logging
from typing import Annotated, Any

#: WorldMock 独立就寝反思每次最多提交五条洞见。
MEMORY_REFLECTION_MAX_INSIGHTS = 5
from pydantic import Field, ValidationError, WithJsonSchema, field_validator

from worldmock.life.models import ToolRequestModel, deepseek_strict_schema, validate_memory_text

#: 提交反思的唯一工具名。
REFLECTION_TOOL = "submit_reflection"
#: 输出不合合同时附回错误的重试次数上限：首次之外至多再请求 3 次（连同首次至多四次模型调用），与传输重试分开计数。
#: 数值与零点计划的校验重试相同，但各自独立，互不引用。
REFLECTION_VALIDATION_RETRIES = 3
#: 就寝反思的 system 文案（docs/CONTRACTS.md「请求组成」逐字登记，测试逐字比对）；条数上限归宿主规则
#: 宿主反思洞见闭集上限为五条。阶段 1 的文本（docs/dev/SPEC-errands-workday.md 1.1）原样保留、各行不重新
#: 折行，其后空一行追加 3.3 的两句：反思不记下、不办成也不取消个人事务，输入是人物自己的记录、不是核验过的结果。两句在
#: 文案里是不折行的一行，源码只为行宽拆成相邻的字符串。
REFLECTION_PROMPT = """
Reflect on a simulated person's recent memories.

Answer three questions about the person, drawing only on the supplied
episodes:
1. 最近一段时间，哪些人和事对这个人影响最大？
2. 这个人对自己、对身边的人有了哪些新的认识？
3. 接下来这个人最应该关心或改变什么？

Call submit_reflection exactly once with one to five insights. Each insight
is one first-person sentence in the person's language, and refs lists the ids
of the supplied episodes that support it. Do not write episode ids or long
digit strings inside the insight text. These insights are subjective; do not
present them as verified world facts.
""".strip() + "\n\n" + (
    "Reflection does not register, finish or cancel errands. The supplied episodes are the person's own notes, "
    "not verified results; do not state as a fact that something was obtained, eaten or finished.")
#: 附回重试时的英文指令。
_CORRECTION_INSTRUCTION = "Return a complete new submit_reflection tool call using the unchanged schema."

_LOGGER = logging.getLogger(__name__)


class ReflectionOutputError(ValueError):
    """@brief 反思输出不合合同（不是恰好一次 submit_reflection 调用，或条数、引用、正文校验失败）；英文单行原因附回重试。"""


class ReflectionInsight(ToolRequestModel):
    """@brief 一条主观洞见与它引用的输入记忆身份。"""

    insight: str = Field(pattern=r"\S")
    refs: list[str]

    @field_validator("insight")
    @classmethod
    def validate_insight(cls, insight: str) -> str:
        """@brief 洞见正文与收尾记忆的文字同一校验：至多一千字符，不含八位以上十六进制串。"""
        return validate_memory_text(insight)

    @field_validator("refs")
    @classmethod
    def require_references(cls, refs: list[str]) -> list[str]:
        """@brief 洞见至少引用一条记忆，引用不为空白，同一条洞见内不重复。"""
        if not refs or any(not ref.strip() for ref in refs) or len(refs) != len(set(refs)):
            _LOGGER.error("Reflection references must be nonempty and unique")
            raise ValueError("Reflection references must be nonempty and unique")
        return refs


class ReflectionRequest(ToolRequestModel):
    """@brief 就寝边界的一至五条主观洞见；条目 Schema 内联，不产生引用定义。"""

    insights: Annotated[list[ReflectionInsight], WithJsonSchema(
        {"type": "array", "items": ReflectionInsight.model_json_schema(mode="validation")})]

    @field_validator("insights")
    @classmethod
    def require_insights(cls, insights: list[ReflectionInsight]) -> list[ReflectionInsight]:
        """@brief 条数一至五条，上限直接取 AS2 常量。"""
        if not 1 <= len(insights) <= MEMORY_REFLECTION_MAX_INSIGHTS:
            _LOGGER.error("Reflection must contain one to five insights")
            raise ValueError("Reflection must contain one to five insights")
        return insights


def reflection_tool() -> dict[str, Any]:
    """@brief 就寝反思唯一的严格工具：参数是 ReflectionRequest 的内联 Schema，生成后不改写。"""
    return {"type": "function", "function": {
        "name": REFLECTION_TOOL, "strict": True,
        "description": "Record subjective reflections with references to the provided memories.",
        "parameters": deepseek_strict_schema(ReflectionRequest)}}


def reflection_insights(response: Any, episodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """@brief 把恰好一次 submit_reflection 调用的参数校验成可保存的洞见；不合合同时抛 ReflectionOutputError。

    自由文本、其他工具、多次调用、条数越界、引用为空或重复、引用不在本次输入之内、正文超长或含身份串都不接受。

    :param response: 模型原始响应；只读工具调用，不读 message.content。
    :param episodes: 本次交给模型的输入记忆（引用只能取它们的身份）。
    :returns: 固定为 type=reflection、source=reflection、importance=0.8、keywords=[] 的洞见条目。
    :raises ReflectionOutputError: 输出不合合同；消息是可附回模型的英文单行原因。
    """
    arguments = _tool_arguments(response)
    try:
        payload = ReflectionRequest.model_validate_json(arguments)
    except ValidationError as error:
        raise ReflectionOutputError(_schema_feedback(error)) from error
    known = {str(item["id"]) for item in episodes}
    unknown = {index: sorted(set(item.refs) - known) for index, item in enumerate(payload.insights)}
    problems = [f"insights.{index}.refs: memory ids {refs} are not among the supplied episodes"
                for index, refs in unknown.items() if refs]
    if problems:
        _LOGGER.error("Reflection cites unknown memory ids")
        raise ReflectionOutputError("; ".join(problems))
    return [{"type": "reflection", "importance": 0.8, "keywords": [], "text": item.insight,
             "source": "reflection", "refs": list(item.refs)} for item in payload.insights]


def reflection_correction(messages: list[dict[str, Any]], response: Any,
                          error: ReflectionOutputError) -> list[dict[str, Any]]:
    """@brief 在给定消息之后追加一条 user 消息，带上一次不合法的工具调用、英文校验错误与重新提交的指令。

    调用方每次都传原始请求的消息：重试总是基于原始消息，不逐次累积。

    :param messages: 原始请求的消息（不被修改）。
    :param response: 上一次不合合同的模型响应；只回显各工具调用的名称与参数，不读 message.content。
    :param error: 上一次的校验错误。
    :returns: 重试请求的消息。
    """
    echoed = [{"name": _text_or_none(getattr(call, "name", None)),
               "arguments": _text_or_none(getattr(call, "arguments", None))}
              for call in (getattr(item, "function", None) for item in _tool_calls(response))]
    message = correction_message(echoed, error, _CORRECTION_INSTRUCTION)
    return [*(dict(item) for item in messages), message]


def _tool_calls(response: Any) -> list[Any]:
    """@brief 响应唯一一个选择里的工具调用列表；没有选择、选择不唯一或没有工具调用时为空列表。"""
    choices = getattr(response, "choices", None)
    if not isinstance(choices, list) or len(choices) != 1:
        return []
    calls = getattr(getattr(choices[0], "message", None), "tool_calls", None)
    return list(calls) if isinstance(calls, list) else []


def _tool_arguments(response: Any) -> str:
    """@brief 原始记忆响应经唯一工具参数校验，不读取自由正文。"""
    return tool_arguments(_tool_calls(response), REFLECTION_TOOL, ReflectionOutputError)


def _schema_feedback(error: ValidationError) -> str:
    """@brief 保留反思根字段名称并共用 Schema 错误格式。"""
    return schema_feedback(error, "arguments")


def _text_or_none(value: Any) -> str | None:
    """@brief 回显内容只保留文本；其他形状回显为 null。"""
    return value if isinstance(value, str) else None
