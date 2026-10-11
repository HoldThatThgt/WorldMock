"""@brief 冻结观察中开始调用的完整名称解析：对象名与 detail 都唯一才受理，不猜测实例或内容。"""

from __future__ import annotations
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar
from worldmock.life import object_labels
from worldmock.life.action_contract import START_TOOLS, binding_action, start_tool
from worldmock.life.error_contract import MAX_CANDIDATES, Candidate, ErrorItem
from worldmock.life.sleep_plan import parse_sleep_minutes

_Entry = TypeVar("_Entry")
MAIN_TOOL, SIDE_TOOL = START_TOOLS


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
    """@brief 本轮绑定和完整地点、内容 app 名称。"""
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


@dataclass(frozen=True, slots=True)
class StartOutcome:
    """@brief 一条开始调用解析出的绑定行、全部独立名称错误与无损归一记录。"""
    rows: tuple[dict, ...] = ()
    failures: tuple[Failure, ...] = ()
    resolved: tuple[dict, ...] = ()


def named_supply_row(name: str) -> dict:
    """@brief 未展示商品交给唯一指名商品解析入口。"""
    action = binding_action("purchase")
    return {"object": name, "kind": "named_supply", "object_id": None,
            "actions": [action], "selected_action": action}


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
    """@brief 未展示地点和内容 app 可按名调用，功能 app 永不补入。"""
    public = {row["name"]: row for row in observation["tool_layer"]["targets"]}
    targets = [_target(row, public.get(row["object"]), True) for row in observation["interaction_bindings"]]
    labels = {target.label for target in targets}
    for row in observation["catalog_bindings"]:
        if row["object"] not in labels:
            targets.append(_target(row, None, False))
    places = {row["location_id"]: row["name"] for row in observation["locations"]}
    return NameBook(tuple(targets), places[observation["home_location_id"]], places[observation["location_id"]])


def _target(row: Mapping, public: Mapping | None, listed: bool) -> Target:
    """@brief 私有资格与公开时长按同一动作和 detail 对齐。"""
    facts = {} if public is None else {(item["action"], item["detail"]): item for item in public["actions"]}
    options = tuple(Option(item["action"], item["detail"], item.get("note"),
                           facts.get((item["action"], item["detail"]), {}).get("duration"),
                           tuple(item.get("episode_titles", ())), item) for item in row["actions"])
    return Target(row["object"], row["kind"], row["object_id"], options, listed, row)


def selection_row(target: Target, option: Option) -> dict:
    """@brief 选中实际提供的那条动作行，不增加旧路由字段。"""
    return {**target.row, "selected_action": dict(option.row)}


def start_items(tool: str, request: Any) -> tuple[StartItem, ...]:
    """@brief 把调用展开成解析项：主工具一项，副工具的 target 每个元素一项（购买的重复元素各自成项）。"""
    if tool == MAIN_TOOL:
        return (StartItem(tool, request.action, request.target, request.detail),)
    written = tuple(request.target)
    return tuple(StartItem(tool, request.action, name, None, index, written) for index, name in enumerate(written))


def resolve_start(book: NameBook, tool: str, request: Any) -> StartOutcome:
    """@brief 逐项汇总一条开始调用的名称错误，任一失败时不返回任何绑定行。"""
    results = [_resolve_item(book, item) for item in start_items(tool, request)]
    failures = tuple(failure for _, failure, _ in results if failure is not None)
    rows = () if failures else tuple(row for row, _, _ in results)
    return StartOutcome(rows, failures, tuple(record for _, _, records in results for record in records))


def _kinds(item: StartItem) -> frozenset[str]:
    """@brief 各动作允许的对象类型：主工具只认地点，购买只认商品，副工具的 interact 只认内容 app。"""
    if item.tool == MAIN_TOOL:
        return frozenset({"location"})
    return frozenset({"supply"}) if item.action == "purchase" else frozenset({"app"})


def _resolve_item(book: NameBook, item: StartItem) -> tuple:
    """@brief 睡眠只读分钟数；其余先唯一匹配对象，指名购买交给真实商品入口，主工具再解析 detail。"""
    if item.action == "sleep":
        minutes = parse_sleep_minutes(item.target)
        records = () if str(minutes) == item.target else ({"written": item.target, "as": str(minutes)},)
        return sleep_row(minutes), None, records
    matches = lookup(item.target, book.targets, _kinds(item), book.home)
    if len(matches) > 1:
        return None, _ambiguous_object(matches, item), ()
    if not matches:
        if item.action == "purchase":
            return named_supply_row(item.target), None, ()
        return None, _unknown_object(book, item), ()
    target = matches[0]
    options = tuple(option for option in target.options
                    if option.action == item.action and start_tool(target.kind, option.action) == item.tool)
    if not options:
        return None, _unsupported(target, item), ()
    records = () if target.label == item.target else ({"written": item.target, "as": target.label},)
    if item.tool != MAIN_TOOL:
        return selection_row(target, options[0]), None, records
    return _resolve_detail(target, options, item, records)


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
    """@brief 对象的一条动作行写成可以照抄提交的完整调用；购买候选只替换所写数组里出错的那个元素。"""
    note = option.blocked or option.duration
    if target.kind == "location":
        return Candidate(MAIN_TOOL, {"action": option.action, "target": target.label, "detail": option.detail}, note)
    names = [target.label]
    if item.index is not None and item.action == option.action == "purchase":
        names = [target.label if position == item.index else name for position, name in enumerate(item.written)]
    return Candidate(SIDE_TOOL, {"action": option.action, "target": names}, note)


def _pick(target: Target, item: StartItem) -> Option | None:
    """@brief 候选对象上要照抄的动作行：优先所写动作里 detail 唯一对上的一行，否则此刻可行的第一行。

    睡眠行不成候选：它的 target 是分钟数，引擎不替人物定时长。
    """
    usable = [option for option in target.options if option.action != "sleep"]
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
    """@brief 已知对象在这个工具下不提供所写动作：给出它提供的动作与可提交的完整调用（可以属于另一个工具）。"""
    why = f"The known object {target.label} does not support the written action."
    offered = tuple(dict.fromkeys(option.action for option in target.options
                                  if start_tool(target.kind, option.action) == item.tool))
    usable = [option for option in target.options if option.action != "sleep"][:MAX_CANDIDATES]
    candidates = tuple(call_candidate(target, option, item) for option in usable)
    error = ErrorItem(("action",), item.action, "ACTION_NOT_OFFERED", offered,
                      "An action offered by this object is required.", why, candidates)
    return Failure(error, why, why)


def _detail_failure(target: Target, options: tuple[Option, ...], item: StartItem,
                    matches: tuple[str, ...]) -> Failure:
    """@brief detail 找不到或有歧义：allowed_values 是该对象该动作全部行的 detail，候选是近名行或各匹配行。

    近名排序用的名称是行的 detail 加它的别名（系列各集标题），所以写了某一集的标题时所属系列排在最前。
    """
    if matches:
        why = f"The name {item.detail} identifies several options of {target.label}."
        code, expected = "NAME_AMBIGUOUS", "One unambiguous complete detail name is required."
        chosen = [option for option in options if option.detail in matches][:MAX_CANDIDATES]
    else:
        why = (f"{target.label} offers no {item.action} option named {item.detail}; "
               "changing spelling cannot create one.")
        code, expected = "NAME_NOT_FOUND", "A detail name listed for this object and action is required."
        chosen = rank(object_labels.bare_name(item.detail), options,
                      lambda option: tuple(object_labels.bare_name(name) for name in (option.detail, *option.aliases)),
                      lambda option: option.blocked is None)
    candidates = tuple(call_candidate(target, option, item) for option in chosen)
    error = ErrorItem(("detail",), item.detail, code, tuple(option.detail for option in options),
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
