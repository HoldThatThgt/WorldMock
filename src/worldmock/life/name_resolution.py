"""@brief 冻结观察中开始调用的完整名称解析：对象名与 detail 都唯一才受理，不猜测实例或内容。"""

from __future__ import annotations
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar
from worldmock.exchange.types import GOODS_CATEGORY_LABELS
from worldmock.life import object_labels
from worldmock.life.action_contract import START_TOOLS, binding_action, start_tool
from worldmock.life.error_contract import MAX_CANDIDATES, Candidate, ErrorItem
from worldmock.life.sleep_plan import parse_sleep_minutes

_Entry = TypeVar("_Entry")
MAIN_TOOL, SIDE_TOOL = START_TOOLS
#: 只用于下单和查询的 app 在名称簿里的对象类型：没有内部身份、没有动作行、不当近名候选，只为退回时认得它。
ORDER_APP_KIND = "order_app"
#: 副工具各动作允许的对象类型：购买与查询认商品，interact 认内容 app。
_SIDE_KINDS = {"purchase": "supply", "interact": "app", "query": "supply"}


@dataclass(frozen=True, slots=True)
class Option:
    """@brief 对象的一条动作行：动作、detail、不可用原因、公开时长与候选排序用的别名。"""
    action: str
    detail: str | None
    blocked: str | None
    duration: str | None
    aliases: tuple[str, ...]
    row: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class Target:
    """@brief 冻结目标及其全部动作行。"""
    label: str
    kind: str
    object_id: Any
    options: tuple[Option, ...]
    listed: bool = True
    row: Mapping[str, Any] | None = None

    @property
    def feasible(self) -> bool:
        """@brief 候选优先至少有一条可用动作行的目标。"""
        return any(option.blocked is None for option in self.options)


@dataclass(frozen=True, slots=True)
class NameBook:
    """@brief 本轮绑定、完整地点与能打开的 app 名称，以及只用于下单和查询的 app 名称（order_app 对象）。"""
    targets: tuple[Target, ...]
    home: str | None = None
    here: str | None = None


@dataclass(frozen=True, slots=True)
class Failure:
    """@brief 完整名称错误与可提交候选。"""
    item: ErrorItem
    sentence: str
    detail: str


@dataclass(frozen=True, slots=True)
class StartItem:
    """@brief 一条开始调用展开出的一个解析项：主工具一项，副工具 target 的每个元素一项。"""
    tool: str
    action: str
    target: str
    detail: str | None = None
    #: 副工具 target 数组里的位置；主工具为 None。
    index: int | None = None
    #: 副工具所写的完整数组，购买候选替换出错元素时其余元素照写。
    written: tuple[str, ...] = ()
    #: 副工具 prefer 所写的原文，购买候选照抄；主工具的解析项与没有写意向时为 None。
    prefer: str | None = None


@dataclass(frozen=True, slots=True)
class StartOutcome:
    """@brief 一条开始调用解析出的绑定行、全部独立名称错误与无损归一记录。"""
    rows: tuple[dict, ...] = ()
    failures: tuple[Failure, ...] = ()
    resolved: tuple[dict, ...] = ()


def named_supply_row(name: str, action: str) -> dict:
    """@brief 未展示商品交给唯一指名商品解析入口；行上带着所写的动作（购买或查询）。"""
    binding = binding_action(action)
    return {"object": name, "kind": "named_supply", "object_id": None,
            "actions": [binding], "selected_action": binding}


def category_row(category: str) -> dict:
    """@brief 查询写的一个商品类别：类别键与中文名，不经绑定行。"""
    binding = binding_action("query")
    return {"object": GOODS_CATEGORY_LABELS[category], "kind": "category", "object_id": None, "category": category,
            "actions": [binding], "selected_action": binding}


def sleep_row(minutes: int) -> dict:
    """@brief 睡眠不经绑定行：只带解析出的分钟数，能不能睡由受理判定。"""
    action = binding_action("sleep")
    return {"object": None, "kind": "sleep", "object_id": None, "actions": [action],
            "selected_action": action, "minutes": minutes}


def lookup(written: str, targets: Sequence[Target], kinds: frozenset[str] = frozenset(),
           home: str | None = None) -> tuple[Target, ...]:
    """@brief 先匹配完整标签，再在允许类型中作三级无损归一。"""
    exact = tuple(target for target in targets if target.label == written)
    if exact:
        return exact
    pool = tuple(target for target in targets if not kinds or target.kind in kinds)
    keys = object_labels._keys(written)
    for level in (0, 1):
        found = tuple(target for target in pool if object_labels._keys(target.label)[level] == keys[level])
        if found:
            return found
    if keys[2] in object_labels.HOME_WORDS and home is not None:
        return tuple(target for target in pool if target.label == home)
    return tuple(target for target in pool if object_labels._keys(target.label)[2] == keys[2])


def terms(text: str) -> frozenset[str]:
    """@brief 名称排序使用单字和相邻双字。"""
    compact = "".join(text.split()).casefold()
    return frozenset(compact) | frozenset(compact[index:index + 2] for index in range(len(compact) - 1))


def dice(left: frozenset[str], right: frozenset[str]) -> float:
    """@brief 空集合相似度为零。"""
    return 0.0 if not left or not right else 2 * len(left & right) / (len(left) + len(right))


def rank(written: str, entries: Iterable[_Entry], names: Callable[[_Entry], Sequence[str]],
         feasible: Callable[[_Entry], bool], limit: int = MAX_CANDIDATES) -> list[_Entry]:
    """@brief 按可行性、相似度与完整名称稳定排序。"""
    query, scored = terms(written), []
    for entry in entries:
        labels = tuple(names(entry))
        score = max((dice(query, terms(label)) for label in labels), default=0.0)
        shared = any(any(term in terms(label) for term in query if len(term) == 2) for label in labels)
        if score > 0 and (len(written) == 1 or shared):
            scored.append(((not feasible(entry), -score, labels[0]), entry))
    return [entry for _, entry in sorted(scored, key=lambda row: row[0])[:limit]]


def near_names(written: str, names: Iterable[str], limit: int = MAX_CANDIDATES) -> list[str]:
    """@brief 近名仅供纠错，不自动更改输入。"""
    return rank(object_labels.bare_name(written), dict.fromkeys(names),
                lambda name: (object_labels.bare_name(name),), lambda name: True, limit)


def name_book(observation: Mapping[str, Any]) -> NameBook:
    """@brief 未展示地点和能打开的 app 可按名调用，功能 app 永不补入。

    两步绑定之后，按工具层 prefer_apps 的次序把还没有同名 app 对象的名称登记成 order_app 对象，排在最后；
    只与 app 对象比，名称一字不差才算已经能打开。这些对象只为把写错位置的 app 名退回给人物。
    """
    public = {row["name"]: row for row in observation["tool_layer"]["targets"]}
    targets = [_target(row, public.get(row["object"]), True) for row in observation["interaction_bindings"]]
    labels = {target.label for target in targets}
    for row in observation["catalog_bindings"]:
        if row["object"] not in labels:
            targets.append(_target(row, None, False))
    opened = {target.label for target in targets if target.kind == "app"}
    targets.extend(Target(name, ORDER_APP_KIND, None, (), False) for name in
                   observation["tool_layer"]["callable_index"]["prefer_apps"] if name not in opened)
    places = {row["location_id"]: row["name"] for row in observation["locations"]}
    return NameBook(tuple(targets), places[observation["home_location_id"]], places[observation["location_id"]])


def _target(row: Mapping, public: Mapping | None, listed: bool) -> Target:
    """@brief 私有资格与公开时长按同一动作和 detail 对齐。"""
    facts = {} if public is None else {(item["action"], item["detail"]): item for item in public["actions"]}
    options = tuple(Option(item["action"], item["detail"], item.get("note"),
                           facts.get((item["action"], item["detail"]), {}).get("duration"),
                           tuple(item.get("episode_titles", ())), item) for item in row["actions"])
    return Target(row["object"], row["kind"], row["object_id"], options, listed, row)


def _listed(options: Iterable[Option]) -> list[Option]:
    """@brief 列出的动作行：凭状态而未开放的单元行（私有绑定 locked 为真）不列在工具层，照写能解析到它、交给受理拒绝，
    但它不进候选，也不算这个对象这个动作列出的名称（docs/design/04-simulation-engine.md「出行、内容与查询」）。"""
    return [option for option in options if option.row.get("locked") is not True]


def selection_row(target: Target, option: Option) -> dict:
    """@brief 选中实际提供的那条动作行，不增加旧路由字段。"""
    return {**target.row, "selected_action": dict(option.row)}


def start_items(tool: str, request: Any) -> tuple[StartItem, ...]:
    """@brief 把调用展开成解析项：主工具一项，副工具的 target 每个元素一项（购买的重复元素各自成项）。

    副工具的每一项都带着 prefer 原文，只供候选照抄；prefer 不参加名称解析。
    """
    if tool == MAIN_TOOL:
        return (StartItem(tool, request.action, request.target, request.detail),)
    written = tuple(request.target)
    return tuple(StartItem(tool, request.action, name, None, index, written, request.prefer)
                 for index, name in enumerate(written))


def resolve_start(book: NameBook, tool: str, request: Any) -> StartOutcome:
    """@brief 逐项汇总一条开始调用的名称错误，任一失败时不返回任何绑定行。"""
    results = [_resolve_item(book, item) for item in start_items(tool, request)]
    failures = tuple(failure for _, failure, _ in results if failure is not None)
    rows = () if failures else tuple(row for row, _, _ in results)
    return StartOutcome(rows, failures, tuple(record for _, _, records in results for record in records))


def _kinds(item: StartItem) -> frozenset[str]:
    """@brief 各动作规整时允许的对象类型：主工具只认地点；副工具按动作各取一种（购买与查询认商品，interact 认能打开的
    app），三种都另认 order_app，好把写进 target 的只用于下单和查询的 app 退回。"""
    if item.tool == MAIN_TOOL:
        return frozenset({"location"})
    return frozenset({_SIDE_KINDS[item.action], ORDER_APP_KIND})


def _resolve_item(book: NameBook, item: StartItem) -> tuple:
    """@brief 睡眠只读分钟数；其余先唯一匹配对象，指名购买交给真实商品入口，主工具再解析 detail。"""
    if item.action == "sleep":
        minutes = parse_sleep_minutes(item.target)
        records = () if str(minutes) == item.target else ({"written": item.target, "as": str(minutes)},)
        return sleep_row(minutes), None, records
    if item.action == "query":
        return _resolve_query(book, item)
    matches = lookup(item.target, book.targets, _kinds(item), book.home)
    if len(matches) > 1:
        return None, _ambiguous_object(matches, item), ()
    if not matches:
        if item.action == "purchase":
            return named_supply_row(item.target, item.action), None, ()
        return None, _unknown_object(book, item), ()
    target = matches[0]
    if target.kind == ORDER_APP_KIND:
        return None, _order_app_failure(target, item), ()
    options = tuple(option for option in target.options
                    if option.action == item.action and start_tool(target.kind, option.action) == item.tool)
    if not options:
        return None, _unsupported(target, item), ()
    records = () if target.label == item.target else ({"written": item.target, "as": target.label},)
    if item.tool != MAIN_TOOL:
        return selection_row(target, options[0]), None, records
    return _resolve_detail(target, options, item, records)


def _resolve_query(book: NameBook, item: StartItem) -> tuple:
    """@brief 查询的一项按次序认：先在四个类别名里规整后唯一对上（类别优先）；再把本轮展示的商品和只用于下单和查询的
    app（order_app 对象）放在一起逐级规整匹配一次：唯一对上商品就是这件商品，唯一对上 order_app 给与打开、购买同一条
    退回，对上多个给 NAME_AMBIGUOUS 与查询候选（order_app 不出候选）；其余交给环境在全量商品里找。地点和能打开的 app
    不参加。

    类别名或展示的商品名规整后与所写不同时记一条归一。
    """
    category = object_labels.resolve_name(item.target, GOODS_CATEGORY_LABELS.values())
    if category.name is not None:
        key = next(key for key, label in GOODS_CATEGORY_LABELS.items() if label == category.name)
        records = () if category.name == item.target else ({"written": item.target, "as": category.name},)
        return category_row(key), None, records
    matches = lookup(item.target, tuple(target for target in book.targets if target.kind in _kinds(item)))
    if len(matches) > 1:
        return None, _ambiguous_object(matches, item), ()
    if not matches:
        return named_supply_row(item.target, item.action), None, ()
    target = matches[0]
    if target.kind == ORDER_APP_KIND:
        return None, _order_app_failure(target, item), ()
    records = () if target.label == item.target else ({"written": item.target, "as": target.label},)
    return {**target.row, "selected_action": binding_action(item.action)}, None, records


def _resolve_detail(target: Target, options: tuple[Option, ...], item: StartItem, records: tuple) -> tuple:
    """@brief detail 在该对象该动作各行的名称里逐级匹配，唯一才受理；归一后与所写不同的记一条。"""
    match = object_labels.resolve_name(item.detail, [option.detail for option in options])
    if match.name is None:
        return None, _detail_failure(target, options, item, match.matches), ()
    option = next(option for option in options if option.detail == match.name)
    changed = () if match.name == item.detail else ({"written": item.detail, "as": match.name},)
    return selection_row(target, option), None, (*records, *changed)


def _target_path(item: StartItem) -> tuple:
    """@brief 对象名在参数里的位置：主工具是 target，副工具是 target 数组的那个元素。"""
    return ("target",) if item.index is None else ("target", item.index)


def call_candidate(target: Target, option: Option, item: StartItem) -> Candidate:
    """@brief 对象的一条动作行写成可以照抄提交的完整调用；购买候选只替换所写数组里出错的那个元素。

    副工具的候选恒为 action、target、prefer 三个键：购买候选的 prefer 照被退回的请求所写原样带上（主工具的调用和
    打开 app 没有写过意向，就是 None），打开 app 的候选恒为 None。查询的候选是查询调用：只替换出错的那一项，prefer 照
    所写原样带上，note 为 None（查询在工具层没有动作行，没有可用性与时长可写）。
    """
    if item.action == "query":
        written = [target.label if position == item.index else name for position, name in enumerate(item.written)]
        return Candidate(SIDE_TOOL, {"action": "query", "target": written, "prefer": item.prefer}, None)
    note = option.blocked or option.duration
    if target.kind == "location":
        return Candidate(MAIN_TOOL, {"action": option.action, "target": target.label, "detail": option.detail}, note)
    names = [target.label]
    if item.index is not None and item.action == option.action == "purchase":
        names = [target.label if position == item.index else name for position, name in enumerate(item.written)]
    prefer = item.prefer if option.action == "purchase" else None
    return Candidate(SIDE_TOOL, {"action": option.action, "target": names, "prefer": prefer}, note)


def _pick(target: Target, item: StartItem) -> Option | None:
    """@brief 候选对象上要照抄的动作行：优先所写动作里 detail 唯一对上的一行，否则此刻可行的第一行。

    睡眠行不成候选：它的 target 是分钟数，引擎不替人物定时长；未开放的单元行不列出，同样不成候选。
    """
    usable = [option for option in _listed(target.options) if option.action != "sleep"]
    same = [option for option in usable if option.action == item.action]
    named = [option for option in same if option.detail is not None]
    if named and item.detail is not None:
        name = object_labels.resolve_name(item.detail, [option.detail for option in named]).name
        chosen = next((option for option in named if option.detail == name), None)
        if chosen is not None:
            return chosen
    pool = same or usable
    return next((option for option in pool if option.blocked is None), pool[0] if pool else None)


def _calls(targets: Sequence[Target], item: StartItem) -> tuple[Candidate, ...]:
    """@brief 至多三个候选对象各写成一条完整调用，没有可照抄动作行的对象略去。"""
    picked = ((target, _pick(target, item)) for target in targets[:MAX_CANDIDATES])
    return tuple(call_candidate(target, option, item) for target, option in picked if option is not None)


def near_candidates(book: NameBook, item: StartItem) -> tuple[Candidate, ...]:
    """@brief 近名候选只来自已展示且类型允许的目标。"""
    pool = (target for target in book.targets if target.listed and target.kind in _kinds(item))
    chosen = rank(object_labels.bare_name(item.target), pool,
                  lambda target: (object_labels.bare_name(target.label),), lambda target: target.feasible)
    return _calls(chosen, item)


def _unknown_object(book: NameBook, item: StartItem) -> Failure:
    """@brief 对象名找不到：给出本轮列出、类型允许的近名对象。"""
    why = f"No known object named {item.target} supports this action; changing spelling cannot create an object."
    error = ErrorItem(_target_path(item), item.target, "NAME_NOT_FOUND",
                      expected="A known object name in the selected action's category is required.", why=why,
                      candidates=near_candidates(book, item))
    return Failure(error, why, why)


def _ambiguous_object(matches: Sequence[Target], item: StartItem) -> Failure:
    """@brief 对象名对应多个已知对象：候选是各匹配对象。"""
    why = f"The name {item.target} identifies several known objects."
    error = ErrorItem(_target_path(item), item.target, "NAME_AMBIGUOUS",
                      expected="One unambiguous complete object name is required.", why=why,
                      candidates=_calls(matches, item))
    return Failure(error, why, why)


def _unsupported(target: Target, item: StartItem) -> Failure:
    """@brief 已知对象在这个工具下不提供所写动作：给出它提供的动作与可提交的完整调用（可以属于另一个工具）；
    候选不含睡眠行和未开放的单元行。"""
    why = f"The known object {target.label} does not support the written action."
    offered = tuple(dict.fromkeys(option.action for option in target.options
                                  if start_tool(target.kind, option.action) == item.tool))
    usable = [option for option in _listed(target.options) if option.action != "sleep"][:MAX_CANDIDATES]
    candidates = tuple(call_candidate(target, option, item) for option in usable)
    error = ErrorItem(("action",), item.action, "ACTION_NOT_OFFERED", offered,
                      "An action offered by this object is required.", why, candidates)
    return Failure(error, why, why)


def _order_app_failure(target: Target, item: StartItem) -> Failure:
    """@brief 名称唯一对上只用于下单和查询的 app：定位到所写动作，没有允许值和候选，原因里写登记的全名供照抄进 prefer。

    三种副工具动作与主工具共用同一句 why 与 expected。
    """
    why = (f"Write {target.label} in prefer of purchase or query, not in target: "
           "this app only takes orders and has no content to open.")
    error = ErrorItem(("action",), item.action, "ACTION_NOT_OFFERED", (),
                      "A target that the written action accepts is required; an app that only takes orders is "
                      "written in prefer.", why, ())
    return Failure(error, why, why)


def _detail_failure(target: Target, options: tuple[Option, ...], item: StartItem,
                    matches: tuple[str, ...]) -> Failure:
    """@brief detail 找不到或有歧义：allowed_values 是该对象该动作列出的全部行的 detail，候选是列出的近名行或各匹配行；
    未开放的单元行不列出，两者都不含它，即使它与所写的名称最接近。

    近名排序用的名称是行的 detail 加它的别名（系列各集标题），所以写了某一集的标题时所属系列排在最前。
    """
    listed = _listed(options)
    if matches:
        why = f"The name {item.detail} identifies several options of {target.label}."
        code, expected = "NAME_AMBIGUOUS", "One unambiguous complete detail name is required."
        chosen = [option for option in listed if option.detail in matches][:MAX_CANDIDATES]
    else:
        why = (f"{target.label} offers no {item.action} option named {item.detail}; "
               "changing spelling cannot create one.")
        code, expected = "NAME_NOT_FOUND", "A detail name listed for this object and action is required."
        chosen = rank(object_labels.bare_name(item.detail), listed,
                      lambda option: tuple(object_labels.bare_name(name) for name in (option.detail, *option.aliases)),
                      lambda option: option.blocked is None)
    candidates = tuple(call_candidate(target, option, item) for option in chosen)
    error = ErrorItem(("detail",), item.detail, code, tuple(option.detail for option in listed),
                      expected, why, candidates)
    return Failure(error, why, why)


def resolution_data(data: Mapping[str, Any], resolved: Sequence[dict], notes: Sequence[str]) -> dict[str, Any]:
    """@brief 仅追加实际发生的无损名称归一证据。"""
    result = dict(data)
    if resolved:
        result["resolved"] = [*result.get("resolved", []), *resolved]
    if notes:
        result["notes"] = [*result.get("notes", []), *notes]
    return result
