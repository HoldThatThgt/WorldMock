"""@brief 工具层可调用索引：可做的生活活动、此地可得到的食品与查询权限内的完整品类计数。

生活活动取资格段保留的全部名称，曝光轮转不会缩减按名调用范围。食品按固定品类给出人读计数与至多三个示例名；
完整查询品类由 world_query 填入。应用始终作为目标列出，不进入候选、索引或曝光轮转。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from worldmock.exchange.types import FOOD_CATEGORY_LABELS
from worldmock.life.admission import present_location
from worldmock.life.purchase_offers import menu_foods
from worldmock.life.candidates import tiebreak_digest
from worldmock.life.discovery import DiscoveryContext
from worldmock.life.presentation import PresentationWindow
from worldmock.life.readable import count_phrase, local_date
from worldmock.life.tool_layer import LayerContext

#: 每个品类至多给出的示例名数。
MAX_EXAMPLES = 3
#: 索引的三个键，按这个顺序写出（query_categories 由 world_query.attach_world_query 在冻结查询事实时填入）。
INDEX_KEYS = ("lifestyle", "goods", "query_categories")


@dataclass(frozen=True, slots=True)
class IndexSources:
    """@brief 可调用索引的来源：生活活动分区在资格段之后保留的对象身份（呈现窗口的 presented 与 deferred，
    被资格段排除的对象不在其中）。"""

    lifestyle: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _Entry:
    """@brief 索引的一项：品类的写法、名称、对象标识（排序用）与本轮是否已列为目标。"""

    category: str
    name: str
    identity: str
    listed: bool


def index_sources(windows: Mapping[str, PresentationWindow]) -> IndexSources:
    """@brief 从本轮呈现窗口取来源：每个分区的 presented 加 deferred 就是资格段保留的全部条目（被推迟只是本次名额已满）。"""

    def retained(kind: str) -> tuple[str, ...]:
        """@brief 一个分区保留的对象身份；本轮没有该分区的窗口时为空。"""
        window = windows.get(kind)
        return (*window.presented, *window.deferred) if window is not None else ()

    return IndexSources(lifestyle=retained("lifestyle"))


def callable_index(context: LayerContext, sources: IndexSources) -> dict[str, Any]:
    """@brief 工具层的 callable_index：{lifestyle: [名称], goods: [{category, count, examples}],
    query_categories: [{category, count}]}；query_categories 先写空，由 world_query.attach_world_query 填入查询权限内各
    公开品类的完整条目数（query_category_index），查询筛选不改变这些计数（docs/dev/SPEC-interaction-menu.md#errands-workday-查询筛选与返回精简-请求 ）。

    :param context: 本次观察的层上下文（bindings 为本轮列出的交互对象，menu 为本轮有效菜单）。
    :param sources: 生活活动分区资格段保留的对象（index_sources）。
    """
    order = _order(context)
    return {"lifestyle": _lifestyle_names(context, sources.lifestyle, order),
            "goods": _categories(_goods_entries(context), order, frozenset()), "query_categories": []}


def query_category_index(entries: Iterable[Mapping[str, Any]]) -> list[dict]:
    """@brief 本轮查询权限内公开品类标签与完整对象计数，不按呈现窗口截断。"""
    counts = Counter(entry["category"] for entry in entries if entry["category"] is not None)
    return [{"category": category, "count": counts[category]} for category in sorted(counts)]


def obtainable_here(context: DiscoveryContext, store_id: str) -> bool:
    """@brief 实际位于门店可到店，站定时绑定 app 的店可配送到当前地点。"""
    present = present_location(context.state, context.agent.agent_id)
    return present is not None and (present == store_id or bool(context.semantics.catalog.apps_by_store[store_id]))


def _order(context: LayerContext) -> Callable[[str], str]:
    """@brief 排序键：候选契约的决胜键 sha256(场景|人物|日期|对象)（日期取场景时区的本地日期），同一天不变、不是字母序。"""
    day = local_date(context.state.current_time, context.scenario.timezone)
    scenario = str(context.scenario.scenario_id)
    return lambda identity: tiebreak_digest(scenario, context.agent_id, day, identity)


def _lifestyle_names(context: LayerContext, identities: Sequence[str], order: Callable[[str], str]) -> list[str]:
    """@brief 此地此刻可做的生活活动名称：资格段保留的条目按决胜键排列；有效菜单不许做生活活动时为空。"""
    if "do" not in context.menu.allowed_actions:
        return []
    names = {item.activity_id: item.name for item in context.lifestyle_items}
    return [names[identity] for identity in sorted(identities, key=order)]




def _goods_entries(context: LayerContext) -> list[_Entry]:
    """@brief 从真实固定菜单构建可得到食品的名称索引，按固定食品类别汇总。"""
    if "purchase" not in context.menu.allowed_actions:
        return []
    found = DiscoveryContext(context.state, context.scenario, context.semantics,
                             context.state.agents[context.agent_id])
    listed = {row["food_id"] for row in context.bindings if row["kind"] == "supply"}
    entries = {}
    for store_id, food in menu_foods(context):
        if food.food_id not in entries and obtainable_here(found, store_id):
            entries[food.food_id] = _Entry(FOOD_CATEGORY_LABELS[food.category], food.name,
                                          food.food_id, food.food_id in listed)
    return list(entries.values())


def _categories(entries: Iterable[_Entry], order: Callable[[str], str], shared: frozenset[str]) -> list[dict]:
    """@brief 按品类汇总（品类按写法排序）：计数是可以写的名称个数（同名只计一次，写成「N 个」）；示例名至多 MAX_EXAMPLES
    个，先取本轮没有列为目标的，同一组内按决胜键；按名调用有歧义的名称（shared）不作示例。"""
    groups: dict[str, dict[str, _Entry]] = {}
    for entry in entries:
        groups.setdefault(entry.category, {}).setdefault(entry.name, entry)
    rows = []
    for category in sorted(groups):
        ranked = sorted(groups[category].values(), key=lambda entry: (entry.listed, order(entry.identity)))
        examples = [entry.name for entry in ranked if entry.name not in shared][:MAX_EXAMPLES]
        rows.append({"category": category, "count": count_phrase(len(ranked)), "examples": examples})
    return rows
