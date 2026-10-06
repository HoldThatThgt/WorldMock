"""@brief 工具层只列名称的可调用索引 callable_index（docs/dev/SPEC-interaction-menu.md#dayrun-fidelity-呈现与浏览-只列名称的可调用索引 ，裁决 S5）。

工具层 targets 只是本轮展示的目标，按名调用（第三章）却不以展示为限；索引告诉人物此地此刻还有哪些名称可以直接写：

- lifestyle：此地此刻可做的生活活动名称（不带「活动：」前缀），取生活活动分区在资格段之后保留的全部条目，即本轮呈现窗口的
  presented 与 deferred（ROTATION_DEFERRED 不是排除）；地点类不符（ELIGIBILITY_LOCATION_KIND_MISMATCH，5.1）等被资格段排除
  的条目不在其中。有效菜单不许做生活活动时为空。
- contents：内容与游戏按品类给计数与至多 3 个示例名：取内容、游戏两个分区资格段保留的条目中有标题（可以写名称）、有效菜单
  也允许其动作的（内容与游戏会话为观看，可玩游戏为游玩）；品类写内容品类的中文名（readable.BROWSE_LABELS，游戏会话与
  可玩游戏同为「游戏」）。
- goods：此刻从这里能得到的商品按商品品类给计数与至多 3 个示例名（商品名，不带门店）：门店开着、报价已知、有货、人物有资格
  且购买规则已定义（discovery.supply_row 的 purchase_allowed），并且到店时门店就在这里，或远程下单能送到这里（这里在配送
  范围内、门店到这里有能走的配送路线，basket.delivery_expected_at）。有效菜单不许买东西时为空；品类写目录品类的名称。

「这里」与生活活动窗口、本轮有效菜单同一口径：人物当前地点（途中为出发地）。计数是该品类可以写的名称个数（同名只计一次），
与七层其余数量一样写成人读短语「N 个」（readable.count_phrase）；示例名先取本轮没有列为目标的，同一组内按候选契约的决胜键
sha256(场景|人物|日期|对象) 的顺序（同一天在同一地点不变，不是对象标识的字母序）；内容或游戏的名称与另一件内容或游戏同名时
按名调用会有歧义，不作示例。生活活动名称按同一顺序全部列出。

索引不是目标：只有名称、品类与计数，不带动作与条件，也不含内部身份；它不是候选条目，不计入单次呈现上限（上限只约束目标
条目），不写呈现留痕；它在工具层里，随 user 消息计入整次请求的容量计数（prompt_capacity，第四章预算不变）。名称与按名调用
同源：生活活动、内容与游戏就是观察私有的目录绑定 catalog_bindings 与本轮私有绑定里的对象（object_labels 的唯一写法去掉类别
前缀，名称解析按动词与方式把前缀补回，name_resolution），商品名按指名购买解析（named_purchase）；受理时照常做资格与准入判定。
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
from worldmock.life.discovery import DiscoveryContext, supply_row
from worldmock.life.object_labels import object_label
from worldmock.life.presentation import GAME_CATEGORY, PresentationWindow
from worldmock.life.readable import BROWSE_LABELS, count_phrase, local_date
from worldmock.life.tool_layer import LayerContext

#: 每个品类至多给出的示例名数。
MAX_EXAMPLES = 3
#: 索引的四个键，按这个顺序写出（query_categories 由 world_query.attach_world_query 在冻结查询事实时填入）。
INDEX_KEYS = ("lifestyle", "contents", "goods", "query_categories")


@dataclass(frozen=True, slots=True)
class IndexSources:
    """@brief 可调用索引的来源：生活活动、内容与游戏三个分区在资格段之后保留的对象身份（呈现窗口的 presented 与 deferred，
    被资格段排除的对象不在其中）。"""

    lifestyle: tuple[str, ...] = ()
    content: tuple[str, ...] = ()
    game: tuple[str, ...] = ()


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

    return IndexSources(lifestyle=retained("lifestyle"), content=retained("content"), game=retained("game"))


def callable_index(context: LayerContext, sources: IndexSources) -> dict[str, Any]:
    """@brief 工具层的 callable_index：{lifestyle: [名称], contents: [{category, count, examples}], goods: [同形],
    query_categories: [{category, count}]}；query_categories 先写空，由 world_query.attach_world_query 填入查询权限内各
    公开品类的完整条目数（query_category_index），查询筛选不改变这些计数（docs/dev/SPEC-interaction-menu.md#errands-workday-查询筛选与返回精简-请求 ）。

    :param context: 本次观察的层上下文（bindings 为本轮列出的交互对象，menu 为本轮有效菜单）。
    :param sources: 生活活动、内容与游戏三个分区资格段保留的对象（index_sources）。
    """
    order = _order(context)
    contents = _content_entries(context, sources)
    shared = frozenset(name for name, count in Counter(entry.name for entry in contents).items() if count > 1)
    return {"lifestyle": _lifestyle_names(context, sources.lifestyle, order),
            "contents": _categories(contents, order, shared),
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


def _content_entries(context: LayerContext, sources: IndexSources) -> list[_Entry]:
    """@brief 内容与游戏两个分区保留的条目中有标题、有效菜单也允许其动作的：内容按内容品类；游戏会话（只能观看）与可玩
    游戏（游玩）同属游戏品类。名称与目录绑定同一写法（object_labels），本轮列出的按其目标名判定。"""
    if not sources.content and not sources.game:
        return []
    allowed = context.menu.allowed_actions
    listed = {row["object"] for row in context.bindings}
    kinds = {item.item_id: item.kind for item in context.scenario.contents}
    games = {item.game_id: item.title for item in context.scenario.games}
    entries = []
    for identity in (*sources.content, *sources.game):
        playable = identity in games
        title = games[identity] if playable else context.rules.content_titles.get(identity)
        if title is None or ("play" if playable else "watch") not in allowed:
            continue
        category = GAME_CATEGORY if playable else kinds[identity]
        label = object_label("game" if category == GAME_CATEGORY else "content", title)
        entries.append(_Entry(BROWSE_LABELS[category], title, identity, label in listed))
    return entries


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
