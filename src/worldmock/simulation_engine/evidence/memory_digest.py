"""@brief 日、月、年回顾的输入、提示、工具、守卫、结果、证据草稿与登记草稿（docs/design/06-agent.md「日、月、年回顾」）。

回顾是运行时在零点为每个人物做的独立后台任务（worldmock.runtime.lifecycle.runner.settle_pending_memory_digests），不经人物、
不是决策：本模块给出哪些期次要生成、每期的输入（日回顾是那天全部条目的全文按饮食、工作、生活切分并连续编号；月回顾是那个月
的日回顾、年回顾是那一年的月回顾，各加代码算出的次数统计）、中文固定模板（模块常量，由 code_sha256 覆盖）、唯一严格工具
submit_memory_digest、逐类的格式与长度守卫（正文非空、正文长度、依据非空、依据归属）与重试时附回的英文问题、带
purpose="memory_digest" 的 model_request/model_response 证据，以及经引擎系统登记入口与证据一次提交的 environment_event
（data.kind="memory_digest_generated"）。一期有没有回顾只看这条事件，不新增 AgentState 字段。

函数都是纯函数或只读足迹库，模块不保存世界状态、不写事件；只有一处进程内缓存：按足迹库对象与人物记下的「不需要」期次与
各已结束日子有输入的类别。回顾是日记式的回忆，记要紧的事和当时的感受，不是核验事实：正文写的名称、时刻、价钱、数量、日期
和次数都不检查，环境从不据它结算。
"""

from __future__ import annotations

import calendar
import copy
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Annotated, Any
from weakref import WeakKeyDictionary

from pydantic import ValidationError, WithJsonSchema

from worldmock.agents.prompt.prompt_capacity import CONTEXT_THRESHOLD, model_context_length, request_token_count
from worldmock.configuration.inputs.models import ModelMetadata
from worldmock.configuration.persona.persona_card import persona_card
from worldmock.configuration.persona.persona_text import render_card
from worldmock.foundation.errors import ProtocolError
from worldmock.foundation.model_feedback import correction_message, schema_feedback, tool_arguments
from worldmock.foundation.time.readable import local_date
from worldmock.recording_engine.events.models import Event, EventDraft
from worldmock.recording_engine.storage.store import FootprintStore
from worldmock.simulation_engine.observation.memory import days
from worldmock.simulation_engine.observation.memory.digests import registered_digests
from worldmock.simulation_engine.observation.memory.entries import TodayEntry, entry_full
from worldmock.state.interaction.schema import ToolRequestModel, deepseek_strict_schema
from worldmock.state.model.exchange import MEMORY_DIGEST_PURPOSE
from worldmock.state.model.memory import (
    MEMORY_CATEGORIES,
    MEMORY_DIGEST_KEYS,
    MEMORY_DIGEST_KIND,
    MEMORY_DIGEST_TEXT_LIMITS,
)

if TYPE_CHECKING:
    from worldmock.simulation_engine.observation.layers.observe import ObservationBuilder
    from worldmock.simulation_engine.observation.targets.tool_layer import LayerContext
    from worldmock.simulation_engine.world.context import EngineContext

#: 回顾的生成上限（docs/design/06-agent.md「宿主模型协议」）；冻结元数据的 max_output_tokens 更小时用它。
MEMORY_DIGEST_MAX_TOKENS = 8000
#: 提交各类回顾段落的唯一工具名。
MEMORY_DIGEST_TOOL = "submit_memory_digest"
#: 日回顾的中文模板：写成昨天的日记，记要紧的事和当时的感受（docs/design/06-agent.md「日、月、年回顾」）；逐字原文只在这里。
DAY_DIGEST_PROMPT = (
    "你替模拟世界里的一个人物写他昨天的日记。输入按饮食、工作、生活三类给出他昨天的记录：「核验」是环境确认的经过，「当时查到」是他当时查询看到的结果，「自述」是他当时写下的话（「名称：」是收尾或暂停的总结，「进展·」是中途写的进展，"
    "「看法·」是看法，「记住：」是想记住的事，「正文：」是回复正文），每条带编号。每类写一段日记，150 到 220 字为宜，连标点在内不能超过 250 字，用他的第一人称，像人回想昨天那样只记要紧的事和当时的感受，不记流水账："
    "经过以核验为准，挑要紧的写，吃了什么、做了什么、去了哪里、和谁打了交道，这些东西写出名字；时间只说早上、中午、下午、晚上，不写钟点，「八点十一」「九点一刻」这样的说法也不写；价钱、数量和花了多久只在对他要紧时顺口带一句，"
    "比如「有点贵」「等了挺久」，不逐项列出；感受和看法以自述为准，写他喜不喜欢、累不累、还想不想再来、打算保持还是改掉，不替他编没写过的感受；字数紧时先省经过的细节，感受和看法留着。只凭昨天的记录写，不补造没发生的事或别人说的话，"
    "不拿别的日子作比较，不写编号、订单号或其他内部标识。在 refs 里写所依据的记录编号，至少一个。某一类没有记录时写 null。只调用一次 submit_memory_digest。"
)
#: 月回顾的中文模板：把上个月的日记整理成这个月的回忆；逐字原文只在这里。
MONTH_DIGEST_PROMPT = (
    "你替模拟世界里的一个人物把他上个月的日记整理成这个月的回忆。输入按饮食、工作、生活三类给出那个月每一天的日回顾，每段带日期和编号，没整理出来的日子写着「未整理」；另附代码从事件算出的次数统计，让你知道哪些事常有。"
    "每类写一段不超过 800 字（标点也算）的话，用他的第一人称，像人回想上个月那样写：这类事有什么规律和习惯，常吃什么、常做什么、常去哪里，有什么变化，哪些事值得记住；他对具体的东西、地方、人和做法的看法和打算，喜欢的、不喜欢的、"
    "说了要保持或要改掉的，后来有没有变。时间说月初、月中、月底、某个周末这样的说法，次数说几乎每天、常常、偶尔一两次这样的说法，不照抄日期和统计行。只凭输入写，不补造输入里没有的经过、看法或别人说的话，不写编号或其他内部标识。"
    "在 refs 里写所依据的日回顾编号，至少一个。某一类没有输入时写 null。只调用一次 submit_memory_digest。"
)
#: 年回顾的中文模板：把上一年的月回顾整理成这一年的回忆；逐字原文只在这里。
YEAR_DIGEST_PROMPT = (
    "你替模拟世界里的一个人物把他上一年的月回顾整理成这一年的回忆。输入按饮食、工作、生活三类给出那一年每个月的月回顾，每段带月份和编号，没整理出来的月份写着「未整理」；另附代码从事件算出的全年次数统计，让你知道哪些事常有。"
    "每类写一段不超过 2000 字（标点也算）的话，用他的第一人称，像人回想过去一年那样写：这一年这类事固定下来的习惯、口味和做法，哪些看法后来变了、大约什么时候变的，到年底还成立的看法和打算，哪些事值得记住。时间说年初、春天、"
    "几月份、年底这样的说法，次数说几乎每天、常常、偶尔这样的说法，不照抄统计行。只凭输入写，不补造输入里没有的经过、看法或别人说的话，不写编号或其他内部标识。在 refs 里写所依据的月回顾编号，至少一个。"
    "某一类没有输入时写 null。只调用一次 submit_memory_digest。"
)
#: 各级回顾的系统消息，按级取用。
DIGEST_PROMPTS = MappingProxyType({"day": DAY_DIGEST_PROMPT, "month": MONTH_DIGEST_PROMPT,
                                   "year": YEAR_DIGEST_PROMPT})
#: 工具的英文用途句。
_TOOL_DESCRIPTION = ("Submit one digest paragraph with its record references for each category, "
                     "or null for a category without records.")
#: 附回重试时的英文指令。
_CORRECTION_INSTRUCTION = "Return a complete new submit_memory_digest tool call using the unchanged schema."
#: 类别键 → 中文类别名（用户消息用中文名作键，工具参数与事件用英文键）。
_CATEGORY_NAMES = MappingProxyType(dict(zip(MEMORY_DIGEST_KEYS, MEMORY_CATEGORIES, strict=True)))
#: 工作类的类别键。
_WORK_KEY = MEMORY_DIGEST_KEYS[1]
#: 月、年回顾的输入：级 → (下一级, 行里期次的键, 类别值里下一级回顾的键, 编号前缀)。
_SUMMARIES = MappingProxyType({"month": ("day", "日期", "日回顾", "d"), "year": ("month", "月份", "月回顾", "m")})

_LOGGER = logging.getLogger(__name__)


class MemoryDigestOutputError(ValueError):
    """@brief 一次回顾尝试不合格；文字是附回模型的英文问题列表。"""


class DigestParagraph(ToolRequestModel):
    """@brief 一类回顾：正文与所依据的输入行编号。"""

    text: str
    refs: list[str]


#: 一类的值：段落或 null，内联写出、不产生 $defs。
_Paragraph = Annotated[DigestParagraph | None, WithJsonSchema(
    {"anyOf": [DigestParagraph.model_json_schema(mode="validation"), {"type": "null"}]})]


class MemoryDigestRequest(ToolRequestModel):
    """@brief submit_memory_digest 的参数：饮食、工作、生活三类各一段或 null；长度与条数上限由守卫执行。"""

    food: _Paragraph
    work: _Paragraph
    life: _Paragraph


@dataclass(frozen=True, slots=True)
class DigestPeriod:
    """@brief 一期回顾：级（day、month、year）与期（YYYY-MM-DD、YYYY-MM、YYYY）。"""

    tier: str
    period: str

    def call_id(self, agent_id: int, attempt: int) -> str:
        """@brief 这一期第 attempt 次调用的身份；去掉 :attempt:{n} 的前缀是这一期的调用前缀。"""
        return f"{_call_prefix(self, agent_id)}:attempt:{attempt}"


@dataclass(frozen=True, slots=True)
class DigestRow:
    """@brief 交给模型的一行输入：编号、那一行与这一行背后的事件身份。"""

    #: n3、d2、m1。
    number: str
    #: 交给模型的那一行。
    payload: dict
    #: 这一行背后的事件身份。
    sources: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DigestInputs:
    """@brief 一期回顾的输入：期次、用户消息（月、年回顾的次数统计行只在这里）与各类的行。"""

    period: DigestPeriod
    payload: dict
    #: 类别键 → 行。
    rows: Mapping[str, tuple[DigestRow, ...]]


@dataclass(frozen=True, slots=True)
class DigestOutcome:
    """@brief 一期回顾的结果：事件 data.digests 的值与没能整理的类别键。"""

    period: DigestPeriod
    digests: Mapping[str, dict | None]
    fallback: tuple[str, ...]


@dataclass(slots=True)
class _Pending:
    """@brief 一个人物的进程内缓存：记为「不需要」的期次与各已结束日子有输入的类别键；两者都不会再变，从不失效。"""

    unneeded: set[DigestPeriod] = field(default_factory=set)
    presence: dict[str, frozenset[str]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _Scope:
    """@brief 一次 pending_inputs 的只读输入：层上下文、本人缓存、此刻、今天（T）与第 1 号事件的本地日期（F）。"""

    layer: LayerContext
    pending: _Pending
    now: int
    today: str
    first_day: str


#: 存储对象 → 人物 → 「不需要」的期次与各日有输入的类别；恢复和分支换成新的存储对象，从头建立。
_PENDING: WeakKeyDictionary[FootprintStore, dict[int, _Pending]] = WeakKeyDictionary()


def _call_prefix(period: DigestPeriod, agent_id: int) -> str:
    """@brief 这一期的调用前缀 memory-digest:{agent_id}:{tier}:{period}。"""
    return f"memory-digest:{agent_id}:{period.tier}:{period.period}"


def memory_digest_tool() -> dict[str, Any]:
    """@brief 唯一严格工具 submit_memory_digest：英文用途句，参数是 MemoryDigestRequest 的 DeepSeek strict Schema。"""
    return {"type": "function", "function": {
        "name": MEMORY_DIGEST_TOOL, "strict": True, "description": _TOOL_DESCRIPTION,
        "parameters": deepseek_strict_schema(MemoryDigestRequest)}}


def pending_inputs(context: EngineContext, observations: ObservationBuilder, agent_id: int,
                   tier: str) -> list[DigestInputs]:
    """@brief 这一级的候选期次中未登记、有输入的各期输入，按期次先后；没有输入的期次记进「不需要」缓存。

    已登记的期次与已记为「不需要」的期次都跳过；层上下文取 observations._layer_context(agent, [])。
    """
    store, zone, now = context.store, context.config.timezone, context.state.current_time
    scope = _Scope(observations._layer_context(context.state.agents[agent_id], []),
                   _PENDING.setdefault(store, {}).setdefault(agent_id, _Pending()), now, local_date(now, zone),
                   local_date(next(store.iter_events(1)).occurred_at, zone))
    periods, assemble = _TIERS[tier]
    registered = registered_digests(store, agent_id)
    found = []
    for period in periods(scope):
        if (period.tier, period.period) in registered or period in scope.pending.unneeded:
            continue
        inputs = assemble(scope, period)
        if inputs is None:
            scope.pending.unneeded.add(period)
        else:
            found.append(inputs)
    return found


def _day_periods(scope: _Scope) -> list[DigestPeriod]:
    """@brief 日回顾的候选日：日历上的 T−1、T−2、T−3，与从 T−1 往前看到 F 为止最先遇到的三个工作类有输入的日子，
    取并集、剔除早于 F 的日子，按日期从早到晚。"""
    work, day = [], days.shift_date(scope.today, -1)
    while day >= scope.first_day and len(work) < 3:
        if _WORK_KEY in _day_presence(scope, day):
            work.append(day)
        day = days.shift_date(day, -1)
    chosen = {*days.recent_dates(scope.today), *work}
    return [DigestPeriod("day", day) for day in sorted(chosen) if day >= scope.first_day]


def _day_presence(scope: _Scope, day: str) -> frozenset[str]:
    """@brief 已结束的日子 D 有输入的类别键：D 已登记时看那条事件（非 null 或在 fallback 里），未登记时看 D 的全部条目
    里有没有这一类的部分；按条目的判定第一次算出后记进缓存。"""
    event = registered_digests(scope.layer.engine.store, scope.layer.agent_id).get(("day", day))
    if event is not None:
        return frozenset(key for key in MEMORY_DIGEST_KEYS
                         if event.data["digests"][key] is not None or key in event.data["fallback"])
    if day not in scope.pending.presence:
        entries = _day_entries(scope, day)
        scope.pending.presence[day] = frozenset(key for key, name in _CATEGORY_NAMES.items()
                                                if any(entry_full(entry, name) is not None for entry in entries))
    return scope.pending.presence[day]


def _day_entries(scope: _Scope, day: str) -> tuple[TodayEntry, ...]:
    """@brief D 的全部条目：各次决策的条目按决策次序，最后是零点收尾条目（第 02 章）。"""
    layer = scope.layer
    return days.past_day_entries(layer.inputs, layer.engine.store, layer.agent_id, day, scope.now)


def _day_inputs(scope: _Scope, period: DigestPeriod) -> DigestInputs | None:
    """@brief 日回顾的输入：人物卡片与三类各行（D 的全部条目按类切分、三类之间连续编号）；那天没有条目时为 None。"""
    entries = _day_entries(scope, period.period)
    if not entries:
        return None
    rows = _day_rows(entries)
    payload = {"人物卡片": render_card(persona_card(scope.layer.persona)),
               **{name: [row.payload for row in rows[key]] for key, name in _CATEGORY_NAMES.items()}}
    return DigestInputs(period, payload, MappingProxyType(rows))


def _day_rows(entries: Sequence[TodayEntry]) -> dict[str, tuple[DigestRow, ...]]:
    """@brief 三类各行：每条条目这一类的部分（全文按第 02 章的切法）一行，先饮食、再工作、后生活连续编号 n1、n2…；
    一行的键依次是编号、时刻、核验、当时查到、自述，空列表的键省略；行背后的事件是所属条目的来源事件身份。"""
    rows, number = {}, 0
    for key, name in _CATEGORY_NAMES.items():
        found = []
        for entry in entries:
            part = entry_full(entry, name)
            if part is None:
                continue
            number += 1
            payload = {"编号": f"n{number}", **{item: value for item, value in part.items() if item != "类别"}}
            found.append(DigestRow(payload["编号"], payload, entry.event_ids))
        rows[key] = tuple(found)
    return rows


def _month_periods(scope: _Scope) -> list[DigestPeriod]:
    """@brief 月回顾的候选：T 所在月的上一个日历月。"""
    return [DigestPeriod("month", days.shift_date(f"{scope.today[:7]}-01", -1)[:7])]


def _year_periods(scope: _Scope) -> list[DigestPeriod]:
    """@brief 年回顾的候选：T 的上一个日历年。"""
    return [DigestPeriod("year", f"{int(scope.today[:4]) - 1:04d}")]


def _summary_inputs(scope: _Scope, period: DigestPeriod) -> DigestInputs | None:
    """@brief 月、年回顾的输入：那个月每条已登记的日回顾（年回顾为那一年每条已登记的月回顾）按期次先后，三类之间连续
    编号，各类另附这一期的次数统计（只让模型知道哪些事常有，正文不照抄、也不检查）；三类都没有行时为 None（「不需要」）。
    行为空的类不给次数统计，也不给人物卡片与期次键。"""
    lower, key_name, block, prefix = _SUMMARIES[period.tier]
    layer = scope.layer
    events = sorted((key[1], event) for key, event in registered_digests(layer.engine.store, layer.agent_id).items()
                    if key[0] == lower and key[1].startswith(f"{period.period}-"))
    rows = _summary_rows(events, key_name, prefix)
    if not any(rows.values()):
        return None
    lines = days.count_lines(layer, *_period_days(period))
    payload = {name: {block: [row.payload for row in rows[key]], "次数统计": list(lines[name] if rows[key] else ())}
               for key, name in _CATEGORY_NAMES.items()}
    return DigestInputs(period, payload, MappingProxyType(rows))


def _summary_rows(events: Sequence[tuple[str, Event]], key_name: str,
                  prefix: str) -> dict[str, tuple[DigestRow, ...]]:
    """@brief 月、年回顾三类各行：每条下一级回顾这一类有正文时写 {编号, 期次, 回顾}，未整理时写 {编号, 期次, 未整理}，
    这一类没有输入时不出行；先饮食、再工作、后生活连续编号；行背后的事件是那条下一级回顾事件。"""
    rows, number = {}, 0
    for key in MEMORY_DIGEST_KEYS:
        found = []
        for period, event in events:
            digest = event.data["digests"][key]
            if digest is None and key not in event.data["fallback"]:
                continue
            number += 1
            payload = {"编号": f"{prefix}{number}", key_name: period,
                       **({"回顾": digest["text"]} if digest is not None else {"未整理": True})}
            found.append(DigestRow(payload["编号"], payload, (event.event_id,)))
        rows[key] = tuple(found)
    return rows


def _period_days(period: DigestPeriod) -> tuple[str, str]:
    """@brief 月、年这一期的首日与末日（闭区间，次数统计的区间）。"""
    if period.tier == "year":
        return f"{period.period}-01-01", f"{period.period}-12-31"
    year, month = (int(part) for part in period.period.split("-"))
    return f"{period.period}-01", f"{period.period}-{calendar.monthrange(year, month)[1]:02d}"


#: 级 → (候选期次, 一期的输入装配)，按 MEMORY_DIGEST_TIERS 的次序。
_TIERS: Mapping[str, tuple[Callable[[_Scope], list[DigestPeriod]],
                           Callable[[_Scope, DigestPeriod], DigestInputs | None]]] = MappingProxyType({
    "day": (_day_periods, _day_inputs), "month": (_month_periods, _summary_inputs),
    "year": (_year_periods, _summary_inputs)})


def prepare_digest_request(inputs: DigestInputs, model_name: str, metadata: ModelMetadata) -> dict[str, Any]:
    """@brief 一期回顾的请求：中文固定模板、紧凑不排序的 JSON 用户消息、唯一严格工具，思考关闭、底层不重试。

    容量预检：整次请求（含输出预留）超过窗口的 85% 时写错误日志并抛 RuntimeError，这是运行故障，输入不裁剪、不留证据。
    """
    messages = [{"role": "system", "content": DIGEST_PROMPTS[inputs.period.tier]},
                {"role": "user", "content": json.dumps(inputs.payload, ensure_ascii=False, separators=(",", ":"))}]
    request = {"model": model_name, "messages": messages, "stream": False, "tools": [memory_digest_tool()],
               "tool_choice": "required", "max_tokens": min(MEMORY_DIGEST_MAX_TOKENS, metadata.max_output_tokens),
               "max_retries": 0, "extra_body": {"thinking": {"type": "disabled"}}}
    if request_token_count(request) > CONTEXT_THRESHOLD * model_context_length(model_name, metadata):
        _LOGGER.error("Memory digest request exceeds the model context")
        raise RuntimeError("memory digest request capacity exceeded")
    return request


def prepare_digest_correction(request: dict[str, Any], raw: dict[str, Any],
                              error: MemoryDigestOutputError) -> dict[str, Any]:
    """@brief 重试请求：原始请求的消息之后接一条附回前次响应与英文问题的纠正消息；每次重试都基于原始请求。"""
    corrected = copy.deepcopy(request)
    corrected["messages"] = [*request["messages"], correction_message(raw, error, _CORRECTION_INSTRUCTION)]
    return corrected


def check_attempt(inputs: DigestInputs, raw: dict[str, Any],
                  accepted: Mapping[str, DigestParagraph]) -> tuple[dict[str, DigestParagraph],
                                                                    MemoryDigestOutputError | None]:
    """@brief 一次尝试的校验与接受：响应级第一处不合即为错误、不接受任何类；之后按 food、work、life 逐类校验。

    没有输入的类必须是 null；此前已接受的类跳过；其余类为 null 时要求重写，否则跑守卫表的四道守卫，一条问题都没有才接受。
    @return (新的已接受类, 这次尝试的错误；全部通过时为 None)
    """
    kept = dict(accepted)
    try:
        request = _parse(raw)
    except MemoryDigestOutputError as error:
        return kept, error
    problems: list[str] = []
    for key in MEMORY_DIGEST_KEYS:
        value = getattr(request, key)
        if not inputs.rows[key]:
            if value is not None:
                problems.append(f"digests.{key} must be null: the input has no {key} records")
        elif key not in kept:
            found = ([f"digests.{key} must not be null: the input has {key} records"] if value is None
                     else _guards(inputs, key, value))
            problems.extend(found)
            if not found:
                kept[key] = value
    return kept, (MemoryDigestOutputError("; ".join(problems)) if problems else None)


def _parse(raw: dict[str, Any]) -> MemoryDigestRequest:
    """@brief 响应级检查：恰一个 choice、没有在 max_tokens 截断、恰一个 submit_memory_digest 调用、参数合 Schema。"""
    choices = raw.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        _LOGGER.error("Memory digest response does not contain exactly one choice")
        raise MemoryDigestOutputError("response must contain exactly one choice")
    if choices[0].get("finish_reason") == "length":
        _LOGGER.error("Memory digest output was truncated at max_tokens")
        raise MemoryDigestOutputError("memory digest output was truncated at max_tokens")
    message = choices[0].get("message")
    calls = message.get("tool_calls") if isinstance(message, dict) else None
    arguments = tool_arguments(calls, MEMORY_DIGEST_TOOL, MemoryDigestOutputError)
    try:
        return MemoryDigestRequest.model_validate_json(arguments)
    except ValidationError as error:
        raise MemoryDigestOutputError(schema_feedback(error, "arguments")) from error


def _guards(inputs: DigestInputs, key: str, paragraph: DigestParagraph) -> list[str]:
    """@brief 一类段落按守卫表次序的全部问题：正文非空、正文长度、依据非空、依据归属。

    回顾是日记式的回忆，只查格式与长度：正文写的名称、时刻、价钱、数量、日期与次数都不检查。正文长度按 len 计，标点也算；
    超出时问题文字写明连标点的字数、这一级的上限和至少要删去的字数。
    """
    text, tier = paragraph.text, inputs.period.tier
    size, limit = len(text), MEMORY_DIGEST_TEXT_LIMITS[tier]
    checks = ((not text.strip(), f"digests.{key}.text must not be empty"),
              (size > limit, f"digests.{key}.text has {size} characters counting punctuation; the {tier} digest limit "
                             f"is {limit}; shorten it by at least {size - limit}"))
    problems = [message for failed, message in checks if failed]
    problems.extend(_ref_problems(inputs, key, paragraph.refs))
    return problems


def _ref_problems(inputs: DigestInputs, key: str, refs: Sequence[str]) -> list[str]:
    """@brief 依据非空与依据归属：每个 ref 都是这一类某一行的编号；每个不合的 ref 按 refs 次序报一次。"""
    if not refs:
        return [f"digests.{key}.refs must cite at least one record number"]
    numbers = {row.number for row in inputs.rows[key]}
    return [f"digests.{key}.refs: {ref} is not a {key} record number" for ref in dict.fromkeys(refs)
            if ref not in numbers]


def digest_outcome(inputs: DigestInputs, accepted: Mapping[str, DigestParagraph]) -> DigestOutcome:
    """@brief 正文、来源、依据与 fallback：接受的类写原文与两种身份列表（来源按行序去重，依据按 refs 次序去重），其余为
    null；有输入而从未被接受的类按 food、work、life 次序列入 fallback。"""
    digests: dict[str, dict | None] = {}
    for key in MEMORY_DIGEST_KEYS:
        paragraph = accepted.get(key)
        if paragraph is None:
            digests[key] = None
            continue
        rows = {row.number: row for row in inputs.rows[key]}
        digests[key] = {"text": paragraph.text,
                        "source_event_ids": list(dict.fromkeys(source for row in rows.values()
                                                               for source in row.sources)),
                        "cited_event_ids": list(dict.fromkeys(source for ref in paragraph.refs
                                                              for source in rows[ref].sources))}
    fallback = tuple(key for key in MEMORY_DIGEST_KEYS if inputs.rows[key] and key not in accepted)
    return DigestOutcome(inputs.period, MappingProxyType(digests), fallback)


def request_draft(now: int, agent_id: int, link: tuple[str, int, str | None], request: dict[str, Any]) -> EventDraft:
    """@brief 日、月、年回顾一次模型请求的系统私有证据（重试请求回链前次响应）。

    @param link (call_id, attempt, previous_event_id)
    """
    call_id, attempt, previous_event_id = link
    data = {"phase": "request", "purpose": MEMORY_DIGEST_PURPOSE, "call_id": call_id, "attempt": attempt,
            "request": request, "response": None, "error": None}
    return EventDraft(event_type="model_request", occurred_at=now, agent_id=agent_id,
                      caused_by=[previous_event_id] if previous_event_id else [], visibility="system",
                      source_kind="model", data=data)


def response_draft(now: int, agent_id: int, link: tuple[str, int, str, tuple[dict, ...]],
                   raw: dict[str, Any], error: BaseException | None) -> EventDraft:
    """@brief 日、月、年回顾一次模型响应的证据：原始响应、用量与这次尝试的校验错误（合格时为空）及这次调用的失败发送。

    @param link (call_id, attempt, request_event_id, transport_attempts)
    """
    call_id, attempt, request_event_id, transport_attempts = link
    data = {"phase": "response", "purpose": MEMORY_DIGEST_PURPOSE, "call_id": call_id, "attempt": attempt,
            "request": None, "response": raw, "usage": raw.get("usage"),
            "error": str(error) if error is not None else None, "transport_attempts": list(transport_attempts)}
    return EventDraft(event_type="model_response", occurred_at=now, agent_id=agent_id,
                      caused_by=[request_event_id], visibility="system", source_kind="model", data=data)


def failure_draft(now: int, agent_id: int, link: tuple[str, int, str, tuple[dict, ...]],
                  error: BaseException) -> EventDraft:
    """@brief 日、月、年回顾的传输或配置失败证据（运行故障前先保存）及这次调用的失败发送。"""
    call_id, attempt, request_event_id, transport_attempts = link
    data = {"phase": "failure", "purpose": MEMORY_DIGEST_PURPOSE, "call_id": call_id, "attempt": attempt,
            "request": None, "response": None, "error": repr(error), "transport_attempts": list(transport_attempts)}
    return EventDraft(event_type="model_response", occurred_at=now, agent_id=agent_id,
                      caused_by=[request_event_id], visibility="system", source_kind="model", data=data)


def registration_draft(context: EngineContext, agent_id: int, outcome: DigestOutcome,
                       causes: list[str]) -> EventDraft:
    """@brief 一期回顾的登记事件 memory_digest_generated；这一期已登记时写错误日志并抛协议错误（「已登记」检查的唯一位置）。

    信封：environment_event、来源 model、可见性 system、无资金变化，起因是最后一次响应；data 恰为 kind、tier、period、
    digests（food、work、life 三键）与 fallback。
    """
    period = outcome.period
    if (period.tier, period.period) in registered_digests(context.store, agent_id):
        _LOGGER.error("Memory digest was already generated for %s", _call_prefix(period, agent_id))
        raise ProtocolError("Memory digest was already generated for this period")
    data = {"kind": MEMORY_DIGEST_KIND, "tier": period.tier, "period": period.period,
            "digests": {key: copy.deepcopy(outcome.digests[key]) for key in MEMORY_DIGEST_KEYS},
            "fallback": list(outcome.fallback)}
    return EventDraft(event_type="environment_event", occurred_at=context.state.current_time, agent_id=agent_id,
                      caused_by=list(causes), visibility="system", source_kind="model", data=data)
