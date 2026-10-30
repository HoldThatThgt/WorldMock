"""@brief 日、月、年回顾的输入、提示、工具、守卫、结果、证据草稿与登记草稿（docs/design/06-agent.md「日、月、年回顾」）。

回顾是运行时在零点为每个人物做的独立后台任务（worldmock.runtime.lifecycle.runner.settle_pending_memory_digests），不经人物、
不是决策：本模块给出哪些期次要生成、每期的输入（日回顾是那天全部条目的全文按饮食、工作、生活切分并连续编号；月回顾是那个月
的日回顾、年回顾是那一年的月回顾，各加代码算出的次数统计）、中文固定模板（模块常量，由 code_sha256 覆盖）、唯一严格工具
submit_memory_digest、逐类的输出守卫与重试时附回的英文问题、带 purpose="memory_digest" 的 model_request/model_response
证据，以及经引擎系统登记入口与证据一次提交的 environment_event（data.kind="memory_digest_generated"）。一期有没有回顾只看
这条事件，不新增 AgentState 字段。

函数都是纯函数或只读足迹库，模块不保存世界状态、不写事件；只有两处进程内缓存：按足迹库对象与人物记下的「不需要」期次与
各已结束日子有输入的类别，以及按足迹库对象记下的目录名称。回顾正文是整理出来的回忆，不是核验事实，环境从不据它结算。
"""

from __future__ import annotations

import calendar
import copy
import json
import logging
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
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
from worldmock.resource_engine.catalog.models import Catalog
from worldmock.simulation_engine.observation.memory import days
from worldmock.simulation_engine.observation.memory.categories import FOOD_GOODS_CATEGORIES
from worldmock.simulation_engine.observation.memory.digests import registered_digests
from worldmock.simulation_engine.observation.memory.entries import TodayEntry, activity_facts, agent_events, entry_full
from worldmock.state.interaction.requests import contains_hex_identity
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
#: 日回顾的中文模板（docs/design/06-agent.md「日、月、年回顾」）；逐字原文只在这里。
DAY_DIGEST_PROMPT = (
    "你替模拟世界里的一个人物把他昨天的记录整理成回顾。输入按饮食、工作、生活三类给出他昨天的记录：「核验」是环境确认的经过，「当时查到」是他当时查询看到的结果，「自述」是他当时写下的话（「名称：」是收尾或"
    "暂停的总结，「进展·」是中途写的进展，「看法·」是看法，「记住：」是想记住的事，「正文：」是回复正文），每条带编号。每类写一段不超过 200 字的话，用他的第一人称：先写那天这类事经过了什么，以核验为"
    "准；再写他对具体的东西、地方、人和做法的看法、习惯和打算，以自述里的「看法」「记住」和总结为准：每条看法用一句短话写出对象的名字和态度（喜不喜欢、还想不想再来、打算保持还是改掉）；写不下时先写说了以后"
    "要照做、要换掉或不再做的，再写当天写过不止一次的；只写自述里有的看法，不从经过推测感受。在 refs 里写所依据的记录编号，至少一个。提到的东西只用输入里出现过的名字；饮食类写到的商品名只能取自核验，"
    "「当时查到」和自述里提到、核验里没有的商品不写名字，写看法时也一样；时刻、价钱和数量只写输入里出现过的，一律用阿拉伯数字；时长和次数用输入里的起止时刻表达，不要自己换算；写某个时刻时，同一句里提到的名"
    "字要和这个时刻出自同一条记录；饮食类要写出核验里当天收到的每样吃的、喝的，以及做饭用的那份食材包，写做饭的那一句里只写当天做饭用的那一份；不要补造输入里没有的经过、看法或别人说的话，也不要拿别的日子作"
    "比较。某一类没有记录时写 null。只调用一次 submit_memory_digest。"
)
#: 月回顾的中文模板；逐字原文只在这里。
MONTH_DIGEST_PROMPT = (
    "你替模拟世界里的一个人物把他上个月的日回顾整理成月回顾。输入按饮食、工作、生活三类给出那个月每一天的日回顾，每段带日期和编号，"
    "没整理出来的日子写着「未整理」；另附代码从事件算出的次数统计。每类写一段不超过 800 字的话，用他的第一人称，写这个月这类事的规律、"
    "习惯和变化，以及值得记住的事；把日回顾里反复出现、或说了以后要照做或要换掉的看法、习惯和打算，各用一句短话写出对象和态度，写明最早、"
    "最近是哪天的回顾写到的（日期照抄输入）、后来有没有变。在 refs 里写所依据的日回顾编号，至少一个。提到的东西只用输入里出现过的名字；"
    "时刻、价钱、数量和日期只写输入里出现过的，一律用阿拉伯数字；写到次数时照抄统计里的那一行，不要自己数；不要补造输入里没有的经过、"
    "看法或别人说的话。某一类没有输入时写 null。只调用一次 submit_memory_digest。"
)
#: 年回顾的中文模板；逐字原文只在这里。
YEAR_DIGEST_PROMPT = (
    "你替模拟世界里的一个人物把他上一年的月回顾整理成年回顾。输入按饮食、工作、生活三类给出那一年每个月的月回顾，每段带月份和编号，"
    "没整理出来的月份写着「未整理」；另附代码从事件算出的全年次数统计。每类写一段不超过 2000 字的话，用他的第一人称，写这一年这类事"
    "固定下来的习惯、口味和做法，哪些看法在哪个月变了；到年底仍成立的看法各用一句短话写出对象和态度，注明出自哪个月的回顾（月份照抄"
    "输入）。在 refs 里写所依据的月回顾编号，至少一个。提到的东西只用输入里出现过的名字；时刻、价钱、数量、日期和月份只写输入里出现过的，"
    "一律用阿拉伯数字；写到次数时照抄统计里的那一行，不要自己数；不要补造输入里没有的经过、看法或别人说的话。某一类没有输入时写 null。"
    "只调用一次 submit_memory_digest。"
)
#: 各级回顾的系统消息，按级取用。
DIGEST_PROMPTS = MappingProxyType({"day": DAY_DIGEST_PROMPT, "month": MONTH_DIGEST_PROMPT,
                                   "year": YEAR_DIGEST_PROMPT})
#: 数字守卫里「数量」写法认的单位。
QUANTITY_UNITS = ("分钟", "小时", "公里", "份", "个", "件", "杯", "瓶", "盒", "包", "袋", "碗", "串", "块", "片",
                  "条", "项", "次", "遍", "集", "页", "本", "天", "步")
#: 数字记号：六种完整写法，其余阿拉伯数字串由最后一项取成单独的记号；记号整段比较、不规范化。
NUMBER_TOKEN = re.compile(
    r"(?<![\d.:\-])(?:\d{4}-\d{2}-\d{2}(?![\d\-])"   # 日期 YYYY-MM-DD
    r"|\d{4}-\d{2}(?![\d\-])"                         # 月份 YYYY-MM
    r"|\d{1,2}月\d{1,2}日"                            # 日期 M月D日
    r"|\d{1,2}:\d{2}(?![\d:])"                        # 时刻 HH:MM
    r"|\d+(?:\.\d+)?\s?元"                            # 金额
    r"|\d+(?:\.\d+)?\s?(?:" + "|".join(QUANTITY_UNITS) + r"))"   # 数量
    r"|\d+(?:\.\d+)?")                                          # 其余阿拉伯数字
#: 工具的英文用途句。
_TOOL_DESCRIPTION = ("Submit one digest paragraph with its record references for each category, "
                     "or null for a category without records.")
#: 附回重试时的英文指令。
_CORRECTION_INSTRUCTION = "Return a complete new submit_memory_digest tool call using the unchanged schema."
#: 类别键 → 中文类别名（用户消息用中文名作键，工具参数与事件用英文键）。
_CATEGORY_NAMES = MappingProxyType(dict(zip(MEMORY_DIGEST_KEYS, MEMORY_CATEGORIES, strict=True)))
#: 工作类的类别键。
_WORK_KEY = MEMORY_DIGEST_KEYS[1]
#: 句：以。！？结尾的一段文字，末尾没有这三个标点的剩余部分也算一句。
_SENTENCE = re.compile(r"[^。！？]+[。！？]?")
#: 中文字符（U+4E00–U+9FFF）。
_CHINESE = re.compile(r"[一-鿿]")
#: 「做饭那一句」认的词。
_COOKING = "做饭"
#: 「次数照抄」守卫认的数量单位。
_COUNT_UNIT = "次"
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
    """@brief 交给模型的一行输入：编号、那一行、背后的事件身份、除编号以外的全部字符串值（\\n 相接）与核验字符串。"""

    #: n3、d2、m1。
    number: str
    #: 交给模型的那一行。
    payload: dict
    #: 这一行背后的事件身份。
    sources: tuple[str, ...]
    #: 这一行除「编号」以外的全部字符串值用 \n 接起来；只用于名称查找。
    text: str
    #: 日回顾这一行的核验字符串用 \n 接起来，月、年回顾为空串。
    verified: str


@dataclass(frozen=True, slots=True)
class DigestInputs:
    """@brief 一期回顾的输入：期次、用户消息、各类的行与次数统计行、饮食守卫的两份名单与三份目录名称。"""

    period: DigestPeriod
    payload: dict
    #: 类别键 → 行。
    rows: Mapping[str, tuple[DigestRow, ...]]
    #: 类别键 → 次数统计行，日回顾为空。
    counts: Mapping[str, tuple[str, ...]]
    #: 必写名称，只在日回顾非空。
    food_required: tuple[str, ...]
    #: 当天做饭用的食材包，只在日回顾非空。
    kits_cooked: tuple[str, ...]
    #: 目录名称（len ≥ 3，升序）。
    names: tuple[str, ...]
    #: 商品名称。
    goods_names: frozenset[str]
    #: 食材包名称。
    kit_names: frozenset[str]


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
#: 存储对象 → (目录, 目录名称, 商品名称, 食材包名称)；目录是同一对象时复用（Catalog 不能作散列键）。
_NAMES: WeakKeyDictionary[FootprintStore, tuple[Catalog, tuple[str, ...], frozenset[str], frozenset[str]]] = (
    WeakKeyDictionary())


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
    required, cooked = _food_names(scope, period.period, rows[MEMORY_DIGEST_KEYS[0]])
    names, goods, kits = _catalog_names(scope.layer.engine.store, scope.layer.catalog)
    counts = MappingProxyType({key: () for key in MEMORY_DIGEST_KEYS})
    return DigestInputs(period, payload, MappingProxyType(rows), counts, required, cooked, names, goods, kits)


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
            found.append(DigestRow(payload["编号"], payload, entry.event_ids, "\n".join(_strings(payload)),
                                   "\n".join(payload.get("核验", ()))))
        rows[key] = tuple(found)
    return rows


def _strings(payload: Mapping[str, Any]) -> tuple[str, ...]:
    """@brief 一行除「编号」以外的全部字符串值，按键序；列表逐项展开，「未整理」的布尔值不是字符串值。"""
    values: list[str] = []
    for key, value in payload.items():
        if key == "编号":
            continue
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, list):
            values.extend(item for item in value if isinstance(item, str))
    return tuple(values)


def _food_names(scope: _Scope, day: str, rows: Sequence[DigestRow]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """@brief 必写名称与当天做饭用的食材包。

    先是 D 当天本人收到的 meal、snack、drink 商品名称（按收货事件序号去重），再是 D 当天结束、用了收到的食材包的活动所用的
    食材包商品名称（按结束事件序号去重）；必写名称只留作子串出现在饮食各行核验里的，两组接起来再去重；做饭用的食材包是
    第二组不经核验过滤的完整名单。
    """
    layer = scope.layer
    catalog, window = layer.catalog, days.day_window(day, layer.config.timezone)
    cached = agent_events(layer.engine.store, layer.agent_id, catalog.kit_states())
    received = [catalog.goods[event.data["goods_id"]].name for event in cached.events["environment_event"]
                if event.data.get("kind") == "goods_received" and window.contains(event.occurred_at)
                and event.data["category"] in FOOD_GOODS_CATEGORIES]
    uses = activity_facts(cached).kits
    ended = sorted((ending.event_seq, uses[activity_id].goods_id) for activity_id, ending in cached.endings.items()
                   if window.contains(ending.occurred_at) and activity_id in uses
                   and uses[activity_id].goods_id is not None)
    cooked = tuple(dict.fromkeys(catalog.goods[goods_id].name for _, goods_id in ended))
    verified = [row.verified for row in rows]
    required = tuple(name for name in dict.fromkeys((*dict.fromkeys(received), *cooked))
                     if any(name in text for text in verified))
    return required, cooked


def _catalog_names(store: FootprintStore, catalog: Catalog) -> tuple[tuple[str, ...], frozenset[str], frozenset[str]]:
    """@brief 目录名称（商品名、内容标题、系列标题、app 名、地点名与显示名，去重后只留 len ≥ 3 的，升序）、其中的商品
    名称与食材包名称；按足迹库对象缓存，目录与缓存的是同一对象时直接取用，否则重算并替换。"""
    cached = _NAMES.get(store)
    if cached is not None and cached[0] is catalog:
        return cached[1], cached[2], cached[3]
    goods = {item.name for item in catalog.goods.values()}
    kits = {catalog.goods[goods_id].name for state in catalog.kit_states()
            for goods_id in catalog.callback_states[state].reset_on_goods}
    every = {*goods, *(item.title for item in catalog.contents.values()),
             *(item.series.title for item in catalog.contents.values() if item.series is not None),
             *(item.name for item in catalog.apps.values()),
             *(name for item in catalog.locations.values() for name in (item.name, item.display_name))}
    names = tuple(sorted(name for name in every if len(name) >= 3))
    found = (names, frozenset(name for name in names if name in goods),
             frozenset(name for name in names if name in goods and name in kits))
    _NAMES[store] = (catalog, *found)
    return found


def _month_periods(scope: _Scope) -> list[DigestPeriod]:
    """@brief 月回顾的候选：T 所在月的上一个日历月。"""
    return [DigestPeriod("month", days.shift_date(f"{scope.today[:7]}-01", -1)[:7])]


def _year_periods(scope: _Scope) -> list[DigestPeriod]:
    """@brief 年回顾的候选：T 的上一个日历年。"""
    return [DigestPeriod("year", f"{int(scope.today[:4]) - 1:04d}")]


def _summary_inputs(scope: _Scope, period: DigestPeriod) -> DigestInputs | None:
    """@brief 月、年回顾的输入：那个月每条已登记的日回顾（年回顾为那一年每条已登记的月回顾）按期次先后，三类之间连续
    编号，各类另附这一期的次数统计；三类都没有行时为 None（「不需要」）。行为空的类不给次数统计，也不给人物卡片与期次键。"""
    lower, key_name, block, prefix = _SUMMARIES[period.tier]
    layer = scope.layer
    events = sorted((key[1], event) for key, event in registered_digests(layer.engine.store, layer.agent_id).items()
                    if key[0] == lower and key[1].startswith(f"{period.period}-"))
    rows = _summary_rows(events, key_name, prefix)
    if not any(rows.values()):
        return None
    lines = days.count_lines(layer, *_period_days(period))
    counts = {key: lines[name] if rows[key] else () for key, name in _CATEGORY_NAMES.items()}
    payload = {name: {block: [row.payload for row in rows[key]], "次数统计": list(counts[key])}
               for key, name in _CATEGORY_NAMES.items()}
    names, goods, kits = _catalog_names(layer.engine.store, layer.catalog)
    return DigestInputs(period, payload, MappingProxyType(rows), MappingProxyType(counts), (), (), names, goods, kits)


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
            found.append(DigestRow(payload["编号"], payload, (event.event_id,), "\n".join(_strings(payload)), ""))
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

    没有输入的类必须是 null；此前已接受的类跳过；其余类为 null 时要求重写，否则跑全部守卫，一条问题都没有才接受。
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
    """@brief 一类段落按守卫表次序的全部问题：正文、依据、名称、日回顾饮食类的三道名称守卫、数字、数字同条与月、年回顾的
    次数照抄。"""
    text, tier = paragraph.text, inputs.period.tier
    limit = MEMORY_DIGEST_TEXT_LIMITS[tier]
    checks = ((not text.strip(), f"digests.{key}.text must not be empty"),
              (len(text) > limit, f"digests.{key}.text has {len(text)} characters; the {tier} digest limit is {limit}"),
              (_CHINESE.search(text) is None, f"digests.{key}.text must contain Chinese"),
              (contains_hex_identity(text), f"digests.{key}.text contains a hexadecimal identifier"))
    problems = [message for failed, message in checks if failed]
    problems.extend(_ref_problems(inputs, key, paragraph.refs))
    records = _records(inputs, key)
    problems.extend(f"digests.{key}: the name {name} does not appear in the {key} input"
                    for name in _written(text, inputs.names)
                    if not any(name in lookup for lookup, _ in records))
    if tier == "day" and key == MEMORY_DIGEST_KEYS[0]:
        problems.extend(_food_problems(inputs, text))
    problems.extend(_number_problems(inputs, key, text, records))
    if tier != "day":
        problems.extend(_count_problems(inputs, key, text))
    return problems


def _ref_problems(inputs: DigestInputs, key: str, refs: Sequence[str]) -> list[str]:
    """@brief 依据非空与依据归属：每个 ref 都是这一类某一行的编号；每个不合的 ref 按 refs 次序报一次。"""
    if not refs:
        return [f"digests.{key}.refs must cite at least one record number"]
    numbers = {row.number for row in inputs.rows[key]}
    return [f"digests.{key}.refs: {ref} is not a {key} record number" for ref in dict.fromkeys(refs)
            if ref not in numbers]


def _records(inputs: DigestInputs, key: str) -> list[tuple[str, frozenset[str]]]:
    """@brief 这一类的各条输入：每行与每条次数统计行各一条，带名称查找用的文字（行的 DigestRow.text、统计行本身；名称
    不含换行，在上面查子串即逐个字符串值查子串）与数字记号集合；记号对每个字符串值分别提取后取并集，从不在接起来的文字上
    提取。"""
    found = [(row.text, _strings(row.payload)) for row in inputs.rows[key]]
    found.extend((line, (line,)) for line in inputs.counts[key])
    return [(text, frozenset(token for value in strings for token in _tokens(value))) for text, strings in found]


def _written(text: str, names: Iterable[str]) -> list[str]:
    """@brief 正文写到的名称（作子串出现），按在正文里首次出现的位置排，位置相同按名称字符串排。"""
    return sorted((name for name in names if name in text), key=lambda name: (text.find(name), name))


def _tokens(text: str) -> list[str]:
    """@brief 一段文字的数字记号，按出现次序。"""
    return [match.group(0) for match in NUMBER_TOKEN.finditer(text)]


def _sentences(text: str) -> list[str]:
    """@brief 正文按。！？切出的各句；只由这三个标点组成的片段不是句。"""
    return _SENTENCE.findall(text)


def _food_problems(inputs: DigestInputs, text: str) -> list[str]:
    """@brief 日回顾饮食类的三道守卫：饮食核验名称、饮食覆盖与做饭那一句。"""
    food = inputs.rows[MEMORY_DIGEST_KEYS[0]]
    problems = [f"digests.food: the goods name {name} does not appear in the food verified lines"
                for name in _written(text, inputs.goods_names) if not any(name in row.verified for row in food)]
    missing = [name for name in inputs.food_required if name not in text]
    if missing:
        problems.append("digests.food must name every food received and every meal kit cooked that day; "
                        f"missing: {'、'.join(missing)}")
    used = "、".join(inputs.kits_cooked) or "none"
    for sentence in _sentences(text):
        if _COOKING in sentence:
            problems.extend(f"digests.food: the sentence about 做饭 names {kit}, but the meal kit cooked that day "
                            f"was {used}" for kit in _written(sentence, inputs.kit_names)
                            if kit not in inputs.kits_cooked)
    return problems


def _number_problems(inputs: DigestInputs, key: str, text: str,
                     records: Sequence[tuple[str, frozenset[str]]]) -> list[str]:
    """@brief 数字与数字同条：正文的每个记号都在这一类输入的记号集合里；写到目录名称的句子里，每个记号都要有一条输入
    同时含这个记号与这句写到的某个名称。每个不合的记号按出现次序、同一守卫里只报一次。"""
    known = frozenset(token for _, tokens in records for token in tokens)
    problems = [f"digests.{key}: {token} does not appear in the {key} input"
                for token in dict.fromkeys(_tokens(text)) if token not in known]
    apart: dict[str, None] = {}
    for sentence in _sentences(text):
        named = _written(sentence, inputs.names)
        for token in _tokens(sentence) if named else ():
            if token not in apart and not any(token in tokens and any(name in lookup for name in named)
                                              for lookup, tokens in records):
                apart[token] = None
    problems.extend(f"digests.{key}: {token} does not appear in the same input record as a name in its sentence"
                    for token in apart)
    return problems


def _count_problems(inputs: DigestInputs, key: str, text: str) -> list[str]:
    """@brief 次数照抄（月、年回顾）：单位为「次」的每个数字记号都落在正文里这一类某条次数统计行逐字出现的那一段之内；
    每个不合的记号按出现次序、只报一次。"""
    copied = [(start, start + len(line)) for line in inputs.counts[key] for start in _occurrences(text, line)]
    loose: dict[str, None] = {}
    for match in NUMBER_TOKEN.finditer(text):
        if match.group(0).endswith(_COUNT_UNIT) and not any(
                start <= match.start() and match.end() <= end for start, end in copied):
            loose.setdefault(match.group(0))
    return [f"digests.{key}: the count {token} must be copied with its whole count line" for token in loose]


def _occurrences(text: str, line: str) -> list[int]:
    """@brief 一行文字在正文里逐字出现的各个起点（可以重叠）。"""
    found, start = [], text.find(line)
    while start != -1:
        found.append(start)
        start = text.find(line, start + 1)
    return found


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
