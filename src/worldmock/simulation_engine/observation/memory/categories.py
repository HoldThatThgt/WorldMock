"""@brief 记忆的类别：饮食、工作、生活三类由冻结目录、人物工作安排与已提交事件确定性推出，不由模型标注、不存状态。

规则见 docs/design/06-agent.md「记忆的类别」：一项活动按开始时刻与开始内容依规则 0–7 第一条命中即为结果；商品、查询与
个人事务按商品；被拒的请求按能对上的目录行，对不上的购买与查询按目标文字；条目的「类别」串以主请求的类别打头，其余按
首次出现的次序以「、」相连。本模块只读冻结输入与已提交事件，不读世界状态，不写任何东西。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Literal, Mapping, Sequence
from weakref import WeakKeyDictionary
from zoneinfo import ZoneInfo

from worldmock.configuration.inputs.environment import EnvironmentInputs
from worldmock.configuration.persona.work_schedule import STATUS_ON_DUTY, duty_status, shift_on
from worldmock.exchange_engine.definitions.types import GOODS_CATEGORY_LABELS
from worldmock.foundation.errors import IntegrityError
from worldmock.foundation.time.readable import local_date
from worldmock.recording_engine.events.models import Event
from worldmock.recording_engine.projections.commerce.action_orders import placed_orders
from worldmock.recording_engine.storage.store import FootprintStore
from worldmock.resource_engine.catalog.models import App, Catalog, Content, channel_key
from worldmock.simulation_engine.activities.content.location_activities import location_units
from worldmock.state.interaction.actions import START_TOOLS
from worldmock.state.interaction.control_items import CONTROL_TOOLS
from worldmock.state.model.memory import MEMORY_CATEGORIES, MemoryCategory
from worldmock.state.world.activity import ActivityState
from worldmock.state.world.kinds import ActivityKind

#: 规则 2 的食品商品类别。
FOOD_GOODS_CATEGORIES = frozenset({"meal", "snack", "drink"})
#: 被拒的购买与查询对不上目录行时，目标文字含其中之一即归饮食：三个食品类别的中文名，再加提案逐字列出的九个字词。
FOOD_TEXT_MARKERS: tuple[str, ...] = (*(GOODS_CATEGORY_LABELS[key] for key in ("meal", "snack", "drink")),
                                      "饭", "菜", "面", "餐", "食", "饮", "咖啡", "奶", "茶")
_FOOD, _WORK, _LIFE = MEMORY_CATEGORIES


@dataclass(frozen=True, slots=True)
class Derivation:
    """@brief 一次推导的类别与命中的规则编号（0–7）。"""

    category: MemoryCategory
    rule: int


@dataclass(frozen=True, slots=True)
class RowCategory:
    """@brief 目录一行的静态推导：内容行或 app 行、行身份、类别与规则编号。"""

    kind: Literal["content", "app"]
    row_id: str
    category: MemoryCategory
    rule: int


@dataclass(frozen=True, slots=True)
class CategoryBook:
    """@brief 推导类别所需的冻结输入：目录、工作安排、时区、住处与班次地点、食品商品、食品单元与启用的类别。"""

    catalog: Catalog
    schedule: Any | None
    timezone: str
    home_id: str
    workplace_id: str | None
    workplace_kind: str | None
    food_goods: frozenset[str]
    food_names: tuple[str, ...]
    food_units: frozenset[str]
    categories: tuple[MemoryCategory, ...]


#: 存储对象 → （构建所用的环境输入，推导输入）；换成恢复后的新存储对象时重建。
_BOOKS: WeakKeyDictionary[FootprintStore, tuple[EnvironmentInputs, CategoryBook]] = WeakKeyDictionary()


def category_book(inputs: EnvironmentInputs) -> CategoryBook:
    """@brief 从冻结的环境输入算出推导类别的全部输入：食品商品与其名称、食品单元、班次地点类型与启用的类别；纯函数。"""
    catalog = inputs.catalog
    kits = {goods_id for name in catalog.food_states() for goods_id in catalog.callback_states[name].reset_on_goods}
    food_goods = frozenset(goods_id for goods_id, goods in catalog.goods.items()
                           if goods.category in FOOD_GOODS_CATEGORIES or goods_id in kits)
    workplace_id = inputs.workplace_location_id
    workplace_kind = None if workplace_id is None else catalog.locations[workplace_id].kind
    return CategoryBook(
        catalog, inputs.persona.work_schedule, inputs.config.timezone, inputs.home_location_id, workplace_id,
        workplace_kind, food_goods, tuple(catalog.goods[key].name for key in sorted(food_goods)),
        _food_units(catalog), _enabled_categories(catalog, inputs.persona.work_schedule))


def _food_units(catalog: Catalog) -> frozenset[str]:
    """@brief 食品单元里全部地点内容的身份：单元与 location_units 同一口径，某一集写了 food 或单元凭的是食品状态。"""
    states = catalog.food_states()
    return frozenset(item.content_id for kind in catalog.contents_by_location_kind
                     for unit in location_units(catalog, kind)
                     if unit.contents[0].consumes_state in states
                     or any(row.completion_effects is not None and row.completion_effects.food is not None
                            for row in unit.contents)
                     for item in unit.contents)


def _enabled_categories(catalog: Catalog, schedule: Any | None) -> tuple[MemoryCategory, ...]:
    """@brief 启用的类别：人物没有工作安排、目录也没有一行写「工作」覆盖时不启用工作类。"""
    rows = (*catalog.contents.values(), *catalog.apps.values())
    if schedule is not None or any(row.memory_category == _WORK for row in rows):
        return MEMORY_CATEGORIES
    return (_FOOD, _LIFE)


def memory_book(store: FootprintStore, inputs: EnvironmentInputs) -> CategoryBook:
    """@brief 观察路径的入口：这个存储对象已按同一份环境输入算过时直接返回，否则重新算出并记下。"""
    cached = _BOOKS.get(store)
    if cached is not None and cached[0] is inputs:
        return cached[1]
    book = category_book(inputs)
    _BOOKS[store] = (inputs, book)
    return book


def start_content_id(activity: ActivityState) -> str:
    """@brief interact 活动的开始内容：放完过内容时是放完的第一条，否则是 data 里的那一条（自动接续不改类别）。"""
    played = activity.data["played"]
    return played[0]["content_id"] if played else activity.data["content_id"]


def purchase_goods(activity: ActivityState, start_result: Event) -> tuple[str, ...]:
    """@brief 购买活动的商品：开始结果里这项活动各订单的商品，按订单身份升序；其余种类为空。

    :param start_result: 这项活动开始请求的第一条 action_result 事件。
    """
    if activity.kind != ActivityKind.PURCHASE:
        return ()
    orders = {order["order_id"]: order for order in placed_orders(start_result.data["data"])}
    identities = sorted(activity.data["order_ids"])
    if any(identity not in orders for identity in identities):
        logging.error("Purchase activity names an order that is not in its start result")
        raise IntegrityError("Purchase activity names an order that is not in its start result")
    return tuple(orders[identity]["goods_id"] for identity in identities)


def activity_derivation(book: CategoryBook, activity: ActivityState, goods_ids: Sequence[str]) -> Derivation:
    """@brief 一项活动的类别与命中的规则：按开始时刻、开始地点与开始内容依规则 0–7 第一条命中即为结果，不读世界状态。

    :param activity: 这项活动的一份快照（开始后不变的字段决定类别，任何一份快照结果相同）。
    :param goods_ids: purchase_goods 给出的购买商品；其余种类不读。
    """
    if activity.kind == ActivityKind.SLEEP:
        return Derivation(_LIFE, 1)
    if activity.kind == ActivityKind.PURCHASE:
        return Derivation(goods_category(book, goods_ids), 2)
    if activity.kind == ActivityKind.QUERY:
        return Derivation(query_category(book, activity.data["targets"]), 2)
    if activity.kind == ActivityKind.TRAVEL:
        commute = _commutes(book, activity.location_id, activity.destination_id, activity.started_at)
        return Derivation(_WORK, 4) if commute else Derivation(_LIFE, 7)
    content = book.catalog.contents[start_content_id(activity)]
    app_id = activity.data["app_id"]
    if app_id is not None:
        return _override(content, book.catalog.apps[app_id]) or _content_rule_six(book, content)
    return _location_rules(book, content, activity.location_id, activity.started_at) or Derivation(_LIFE, 7)


def activity_category(book: CategoryBook, activity: ActivityState, goods_ids: Sequence[str]) -> MemoryCategory:
    """@brief 一项活动的类别（activity_derivation 的类别）。"""
    return activity_derivation(book, activity, goods_ids).category


def goods_category(book: CategoryBook, goods_ids: Sequence[str]) -> MemoryCategory:
    """@brief 商品的类别：任一件是食品商品即饮食，否则生活；购买、送达、收货与个人事务共用。"""
    if not goods_ids:
        logging.error("Memory category needs at least one goods")
        raise IntegrityError("Memory category needs at least one goods")
    return _FOOD if any(goods_id in book.food_goods for goods_id in goods_ids) else _LIFE


def query_category(book: CategoryBook, targets: Sequence[Mapping[str, str]]) -> MemoryCategory:
    """@brief 查询的类别由目标定下：任一项是食品类别或食品商品即饮食，否则生活；答案不参与。"""
    food = any((item["kind"] == "category" and item["category"] in FOOD_GOODS_CATEGORIES)
               or (item["kind"] == "goods" and item["goods_id"] in book.food_goods) for item in targets)
    return _FOOD if food else _LIFE


def rejected_category(book: CategoryBook, store: FootprintStore, request: Event) -> MemoryCategory:
    """@brief 没有开始任何活动的世界请求的类别：对上目录行的按规则判定，对不上的购买与查询按目标文字，其余为生活。

    :param request: 这条请求的 action_requested 事件；只有被拒的出行才读这次决策的观察事件。
    """
    tool, arguments = request.data["tool_name"], request.data["request"]
    if tool not in START_TOOLS or arguments["action"] == "sleep":
        return _LIFE
    if tool == START_TOOLS[0] and arguments["action"] == "travel":
        return _rejected_travel(book, store, request)
    if tool == START_TOOLS[0]:
        return _rejected_location(book, arguments["target"], arguments["detail"], request.occurred_at)
    if arguments["action"] == "interact":
        app = next((row for row in book.catalog.apps.values()
                    if channel_key(row.name) == channel_key(arguments["target"][0])), None)
        return _LIFE if app is None else _app_row(book, app).category
    return _FOOD if any(_food_name(book, name) for name in arguments["target"]) else _LIFE


def request_category(book: CategoryBook, store: FootprintStore, request: Event, result: Event,
                     activity_categories: Mapping[str, MemoryCategory]) -> MemoryCategory:
    """@brief 一条世界请求的类别：开始了或点名了活动时取那项活动的类别，被拒时按被拒请求的规则，改计划为生活。

    :param result: 这条请求的第一条 action_result 事件。
    :param activity_categories: 调用方为本条目读到的每项活动算出的类别（活动身份 → 类别）。
    """
    tool = request.data["tool_name"]
    if tool in START_TOOLS:
        identity = result.activity_id
    elif tool in CONTROL_TOOLS:
        identity = result.data["data"]["activity_ids"][0] if result.data["status"] == "completed" else None
    else:
        return _LIFE
    if identity is None:
        return rejected_category(book, store, request)
    if identity not in activity_categories:
        logging.error("Memory category names an activity that was not read")
        raise IntegrityError("Memory category names an activity that was not read")
    return activity_categories[identity]


def primary_request(requests: Sequence[Event]) -> Event:
    """@brief 一次决策的主请求：开始组取 start_main_activity 那一条，否则取第一条。

    :param requests: 本次决策全部世界请求的 action_requested 事件，按事件序号排列。
    """
    if not requests:
        logging.error("Decision has no world request")
        raise IntegrityError("Decision has no world request")
    tools = [item.data["tool_name"] for item in requests]
    if set(START_TOOLS) <= set(tools):
        return requests[tools.index(START_TOOLS[0])]
    return requests[0]


def category_label(primary: MemoryCategory | None, parts: Iterable[MemoryCategory]) -> str:
    """@brief 条目的「类别」串：主请求的类别打头，其余按首次出现的次序以「、」相连；零点收尾条目没有主请求。"""
    labels = list(dict.fromkeys([*(() if primary is None else (primary,)), *parts]))
    if not labels:
        logging.error("Memory entry has no category")
        raise IntegrityError("Memory entry has no category")
    return "、".join(labels)


def row_categories(book: CategoryBook) -> tuple[RowCategory, ...]:
    """@brief 目录每一条内容行与 app 行的静态推导：内容行按身份升序在前，app 行按身份升序在后。"""
    contents = book.catalog.contents
    apps = book.catalog.apps
    rows = [("content", key, _content_row(book, contents[key])) for key in sorted(contents)]
    rows += [("app", key, _app_row(book, apps[key])) for key in sorted(apps)]
    return tuple(RowCategory(kind, key, derived.category, derived.rule) for kind, key, derived in rows)


def _content_row(book: CategoryBook, content: Content) -> Derivation:
    """@brief 一条内容行的静态推导：地点内容按规则 0、3、班次地点类型（规则 5）、7，app 内容按规则 0、6。"""
    if content.app_ids:
        return _override(content) or _content_rule_six(book, content)
    if book.workplace_kind is not None and book.workplace_kind in content.location_kinds:
        workplace = Derivation(_WORK, 5)
    else:
        workplace = Derivation(_LIFE, 7)
    return _override(content) or _food_unit(book, content) or workplace


def _app_row(book: CategoryBook, app: App) -> Derivation:
    """@brief 一条 app 行的类别：写了覆盖取覆盖（规则 0），否则按规则 6 的 app 行判定。"""
    return _override(app) or _app_rule_six(book, app)


def _override(*rows: Content | App) -> Derivation | None:
    """@brief 规则 0：依次看各行的 memory_category，第一个写了的即为结果；都没写为 None。"""
    for row in rows:
        if row.memory_category is not None:
            return Derivation(row.memory_category, 0)
    return None


def _food_unit(book: CategoryBook, content: Content) -> Derivation | None:
    """@brief 规则 3：地点内容属于食品单元时为饮食，否则为 None。"""
    return Derivation(_FOOD, 3) if content.content_id in book.food_units else None


def _location_rules(book: CategoryBook, content: Content, location_id: str, moment: int) -> Derivation | None:
    """@brief 在地点做事的规则 0、3、5：按开始内容、开始地点与开始时刻返回第一条命中的推导，都不命中为 None。

    规则 5 先看地点再看时钟：工作日在班次地点不论几点都归工作；不在家而当班中也归工作。在家做的事不经规则 5。
    """
    derived = _override(content) or _food_unit(book, content)
    if derived is not None:
        return derived
    day = _day(book, moment)
    shift = _shift(book, day)
    if shift is None:
        return None
    if location_id == book.workplace_id or (
            location_id != book.home_id
            and duty_status(shift, day, moment, ZoneInfo(book.timezone)) == STATUS_ON_DUTY):
        return Derivation(_WORK, 5)
    return None


def _commutes(book: CategoryBook, origin: str, destination: str | None, moment: int) -> bool:
    """@brief 规则 4：工作日在住处与班次地点之间的出行（任一方向）。"""
    return (book.workplace_id is not None and _shift(book, _day(book, moment)) is not None
            and {origin, destination} == {book.home_id, book.workplace_id})


def _content_rule_six(book: CategoryBook, content: Content) -> Derivation:
    """@brief 规则 6 的内容行判定：app 内容的前提要求班次地点类型时为工作，否则为生活。"""
    prerequisite = content.prerequisite
    if (book.workplace_kind is not None and prerequisite is not None
            and book.workplace_kind in prerequisite.location_kinds):
        return Derivation(_WORK, 6)
    return Derivation(_LIFE, 6)


def _app_rule_six(book: CategoryBook, app: App) -> Derivation:
    """@brief 规则 6 的 app 行判定：这个 app 有一条内容的前提要求班次地点类型时为工作，否则为生活。"""
    contents = book.catalog.contents_by_app.get(app.app_id, ())
    work = any(_content_rule_six(book, book.catalog.contents[key]).category == _WORK for key in contents)
    return Derivation(_WORK if work else _LIFE, 6)


def _day(book: CategoryBook, moment: int) -> date:
    """@brief 时刻所在的场景本地日期。"""
    return date.fromisoformat(local_date(moment, book.timezone))


def _shift(book: CategoryBook, day: date) -> Any | None:
    """@brief 那一天的班次；没有工作安排或那天不当班时为 None。"""
    return None if book.schedule is None else shift_on(book.schedule, day)


def _named_location(book: CategoryBook, name: str) -> Any:
    """@brief 显示名与 name 经 channel_key 相等的地点；对不上为 None。"""
    return next((place for place in book.catalog.locations.values()
                 if channel_key(place.display_name) == channel_key(name)), None)


def _rejected_travel(book: CategoryBook, store: FootprintStore, request: Event) -> MemoryCategory:
    """@brief 被拒出行：目的地对上时，出发地取这次决策唯一的观察事件里的位置，按规则 4 判定；对不上为生活。"""
    destination = _named_location(book, request.data["request"]["target"])
    if destination is None:
        return _LIFE
    observations = store.read_decision_observations(request.agent_id, request.decision_id, request.occurred_at)
    if len(observations) != 1:
        logging.error("Rejected request has no unique decision observation")
        raise IntegrityError("Rejected request has no unique decision observation")
    origin = observations[0].data["location_id"]
    return _WORK if _commutes(book, origin, destination.location_id, request.occurred_at) else _LIFE


def _rejected_location(book: CategoryBook, target: str, detail: str, moment: int) -> MemoryCategory:
    """@brief 被拒的在地点做事：地点与单元都对上时以单元第一条内容按规则 0、3、5 判定，否则为生活。"""
    place = _named_location(book, target)
    if place is None:
        return _LIFE
    unit = next((item for item in location_units(book.catalog, place.kind)
                 if channel_key(item.name) == channel_key(detail)), None)
    derived = None if unit is None else _location_rules(book, unit.contents[0], place.location_id, moment)
    return _LIFE if derived is None else derived.category


def _food_name(book: CategoryBook, name: str) -> bool:
    """@brief 被拒购买与查询的一个名称是否归饮食：先对商品类别名，再对商品名，都对不上时按文字规则。"""
    key = channel_key(name)
    category = next((code for code, label in GOODS_CATEGORY_LABELS.items() if channel_key(label) == key), None)
    if category is not None:
        return category in FOOD_GOODS_CATEGORIES
    goods = next((goods_id for goods_id, row in book.catalog.goods.items() if channel_key(row.name) == key), None)
    if goods is not None:
        return goods in book.food_goods
    return any(marker in name for marker in (*FOOD_TEXT_MARKERS, *book.food_names))
