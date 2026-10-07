"""@brief 同一商品集合的品类计数与真实导航、支付应用名称。"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from worldmock.exchange.types import AppPurpose, GOODS_CATEGORY_LABELS
from worldmock.life.candidates import tiebreak_digest
from worldmock.life.discovery import DiscoveryContext, product_rows
from worldmock.life.readable import count_phrase, local_date
from worldmock.life.tool_layer import LayerContext

#: 每个品类至多给出的示例名数。
MAX_EXAMPLES = 3
#: 索引的三个键，按这个顺序写出（query_categories 由 world_query.attach_world_query 在冻结查询事实时填入）。
INDEX_KEYS = ("goods", "navigation_apps", "payment_apps", "query_categories")


@dataclass(frozen=True, slots=True)
class _Entry:
    """@brief 索引的一项：品类的写法、名称、对象标识（排序用）与本轮是否已列为目标。"""

    category: str
    name: str
    identity: str
    listed: bool




def callable_index(context: LayerContext) -> dict[str, Any]:
    """@brief 商品品类不受呈现窗口裁剪，完整查询品类计数由查询冻结时填写。"""
    order = _order(context)
    catalog = context.semantics.catalog
    navigation = catalog.apps_by_purpose[AppPurpose.NAVIGATION] if "travel_to" in context.menu.allowed_actions else ()
    payment = catalog.apps_by_purpose[AppPurpose.PAYMENT] if "purchase" in context.menu.allowed_actions else ()
    return {"goods": _categories(_goods_entries(context), order, frozenset()),
            "navigation_apps": [catalog.apps[identity].name for identity in navigation],
            "payment_apps": [catalog.apps[identity].name for identity in payment], "query_categories": []}


def query_category_index(entries: Iterable[Mapping[str, Any]]) -> list[dict]:
    """@brief 本轮查询权限内公开品类标签与完整对象计数，不按呈现窗口截断。"""
    counts = Counter(entry["category"] for entry in entries if entry["category"] is not None)
    return [{"category": category, "count": counts[category]} for category in sorted(counts)]


def _order(context: LayerContext) -> Callable[[str], str]:
    """@brief 排序键：候选契约的决胜键 sha256(场景|人物|日期|对象)（日期取场景时区的本地日期），同一天不变、不是字母序。"""
    day = local_date(context.state.current_time, context.scenario.timezone)
    scenario = str(context.scenario.scenario_id)
    return lambda identity: tiebreak_digest(scenario, context.agent_id, day, identity)






def _goods_entries(context: LayerContext) -> list[_Entry]:
    """@brief 从观察和查询共用的真实商品集合构建固定类别索引。"""
    if "purchase" not in context.menu.allowed_actions:
        return []
    found = DiscoveryContext(context.state, context.scenario, context.semantics,
                             context.state.agents[context.agent_id])
    listed = {row["goods_id"] for row in context.bindings if row["kind"] == "supply"}
    catalog = context.semantics.catalog
    return [_Entry(GOODS_CATEGORY_LABELS[catalog.goods[row["goods_id"]].category], row["name"],
                   row["goods_id"], row["goods_id"] in listed) for row in product_rows(found)]


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
