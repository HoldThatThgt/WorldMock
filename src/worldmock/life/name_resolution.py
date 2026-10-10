"""@brief 冻结观察中的动作与完整名称解析，不猜测实例或内容。"""

from __future__ import annotations
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
import unicodedata
from typing import Any, TypeVar
from worldmock.life import object_labels
from worldmock.life.action_contract import ACTIVITY_ACTIONS, TRAVEL_ACTIONS, binding_action
from worldmock.life.error_contract import MAX_CANDIDATES, Candidate, ErrorItem

_Entry = TypeVar("_Entry")
_ACTION_KINDS = {**{action: frozenset({"location"}) for action in TRAVEL_ACTIONS},
                 "sleep": frozenset({"location"}), "purchase": frozenset({"supply"}),
                 "interact": frozenset({"location", "app"})}


@dataclass(frozen=True, slots=True)
class Target:
    """@brief 冻结目标及其真实动作与公开资格。"""
    label: str
    kind: str
    object_id: Any
    actions: tuple[str, ...]
    blocked: tuple[str | None, ...]
    notes: tuple[str | None, ...]
    listed: bool = True
    row: Mapping[str, Any] | None = None

    @property
    def feasible(self) -> bool:
        """@brief 候选优先至少有一个可用动作的目标。"""
        return any(reason is None for reason in self.blocked)


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
    """@brief 无损归一后的动作与对象名。"""
    action: str
    target: str


@dataclass(frozen=True, slots=True)
class StartRequest:
    """@brief 一次原子开始请求的全部项。"""
    items: tuple[StartItem, ...]


@dataclass(frozen=True, slots=True)
class StartOutcome:
    """@brief 解析后绑定与所有独立名称错误。"""
    request: StartRequest | None
    rows: tuple[dict, ...] = ()
    failures: tuple[Failure, ...] = ()
    resolved: tuple[dict, ...] = ()
    notes: tuple[str, ...] = ()


def named_supply_row(name: str) -> dict:
    """@brief 未展示商品交给唯一指名商品解析入口。"""
    action = binding_action("purchase")
    return {"object": name, "kind": "named_supply", "object_id": None,
            "actions": [action], "selected_action": action}


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
    """@brief 私有资格与公开时长按同一动作位置对齐。"""
    actions = tuple(item["action"] for item in row["actions"])
    facts = {} if public is None else {item["action"]: item for item in public["actions"]}
    return Target(row["object"], row["kind"], row["object_id"], actions,
                  tuple(item.get("note") for item in row["actions"]),
                  tuple(facts.get(action, {}).get("duration") for action in actions), listed, row)


def selection_row(target: Target, action: str) -> dict:
    """@brief 选择实际提供的动作，不增加旧路由字段。"""
    selected = next(item for item in target.row["actions"] if item["action"] == action)
    return {**target.row, "selected_action": dict(selected)}


def fill_in(target: Target, item: StartItem, action: str | None = None) -> Candidate:
    """@brief 候选只含动作与对象，支持原动作时保留原动作。"""
    chosen = action or (item.action if item.action in target.actions else next(
        (offered for offered, reason in zip(target.actions, target.blocked) if reason is None), target.actions[0]))
    position = target.actions.index(chosen)
    return Candidate("start_activity", {"items": [{"action": chosen, "target": target.label}]},
                     target.blocked[position] or target.notes[position])


def near_candidates(book: NameBook, item: StartItem) -> tuple[Candidate, ...]:
    """@brief 近名候选只来自已展示且类型允许的目标。"""
    pool = (target for target in book.targets if target.listed and target.kind in _ACTION_KINDS[item.action])
    chosen = rank(object_labels.bare_name(item.target), pool,
                  lambda target: (object_labels.bare_name(target.label),), lambda target: target.feasible)
    return tuple(fill_in(target, item) for target in chosen if target.actions)


def resolve_start(book: NameBook, request: Any) -> StartOutcome:
    """@brief 逐项汇总名称错误，任一失败时不派发任何项。"""
    results = [_resolve_item(book, item, index) for index, item in enumerate(request.items)]
    rows = tuple(row for row, _, _ in results if row is not None)
    failures = tuple(failure for _, failure, _ in results if failure is not None)
    normalized = tuple(StartItem(row["selected_action"]["action"] if row else item.action,
                                 row["object"] if row else item.target)
                       for item, (row, _, _) in zip(request.items, results))
    return StartOutcome(StartRequest(normalized), () if failures else rows, failures,
                        tuple(record for _, _, records in results for record in records))


def _resolve_item(book: NameBook, item: Any, index: int) -> tuple:
    """@brief 合法动作先查对象，指名购买交给真实商品入口。"""
    normalized = "".join(unicodedata.normalize("NFKC", item.action).split())
    if normalized not in ACTIVITY_ACTIONS:
        why = "The action is outside the declared closed set."
        error = ErrorItem(("items", index, "action"), item.action, "ACTION_NOT_OFFERED", ACTIVITY_ACTIONS,
                          "An offered activity action is required.", why)
        return None, Failure(error, why, why), ()
    row, failure, records = _resolve_known(book, StartItem(normalized, item.target), index)
    if failure is not None and failure.item.path[-1] == "action":
        failure = replace(failure, item=replace(failure.item, written=item.action))
    changed = () if normalized == item.action else ({"written": item.action, "as": normalized},)
    return row, failure, (*changed, *records)


def _resolve_known(book: NameBook, item: StartItem, index: int) -> tuple:
    """@brief 已知动作按允许目标类型完成唯一名称匹配。"""
    matches = lookup(item.target, book.targets, _ACTION_KINDS[item.action], book.home)
    if len(matches) == 1:
        target = matches[0]
        if item.action not in target.actions:
            return None, _unsupported(target, item, index), ()
        records = () if target.label == item.target else ({"written": item.target, "as": target.label},)
        return selection_row(target, item.action), None, records
    if matches:
        why = f"The name {item.target} identifies several known objects."
        candidates = tuple(fill_in(target, item) for target in matches[:MAX_CANDIDATES] if target.actions)
        error = ErrorItem(("items", index, "target"), item.target, "NAME_AMBIGUOUS",
                          expected="One unambiguous complete object name is required.", why=why, candidates=candidates)
        return None, Failure(error, why, why), ()
    if item.action == "purchase":
        return named_supply_row(item.target), None, ()
    why = f"No known object named {item.target} supports this action; changing spelling cannot create an object."
    error = ErrorItem(("items", index, "target"), item.target, "NAME_NOT_FOUND",
                      expected="A known object name in the selected action's category is required.", why=why,
                      candidates=near_candidates(book, item))
    return None, Failure(error, why, why), ()


def _unsupported(target: Target, item: Any, index: int) -> Failure:
    """@brief 已知目标不支持动作时提供完整合法请求。"""
    why = f"The known object {target.label} does not support the written action."
    candidates = tuple(fill_in(target, item, action) for action in target.actions[:MAX_CANDIDATES])
    error = ErrorItem(("items", index, "action"), item.action, "ACTION_NOT_OFFERED", target.actions,
                      "An action offered by this object is required.", why, candidates)
    return Failure(error, why, why)


def resolution_data(data: Mapping[str, Any], resolved: Sequence[dict], notes: Sequence[str]) -> dict[str, Any]:
    """@brief 仅追加实际发生的无损名称归一证据。"""
    result = dict(data)
    if resolved:
        result["resolved"] = [*result.get("resolved", []), *resolved]
    if notes:
        result["notes"] = [*result.get("notes", []), *notes]
    return result
