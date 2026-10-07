"""@brief 从同一冻结观察解析动作、商品与地点名称，不猜测应用或内容。"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

from worldmock.life import object_labels, vocabulary
from worldmock.life.error_contract import MAX_CANDIDATES, Candidate, ErrorItem, activity_candidate
from worldmock.life.interaction_menu import binding_action, named_supply_row
from worldmock.life.object_labels import object_label
from worldmock.life.readable import TRAVEL_WAYS
from worldmock.life.vocabulary import WAYS

_Entry = TypeVar("_Entry")
MAX_START_ITEMS = 2
_WAY_KINDS = {
    "步行": frozenset({"location"}), "驾车": frozenset({"location"}), "领取": frozenset({"location"}),
    "进行": frozenset({"action"}), "购买": frozenset({"supply"}),
    "睡觉": frozenset({"self"}), "等待": frozenset({"self"}),
}
_TRAVEL_MODES = {way: mode for mode, way in TRAVEL_WAYS.items()}


@dataclass(frozen=True, slots=True)
class Target:
    """@brief 本轮目标或完整目录中的可按名调用对象。"""

    label: str
    kind: str
    object_id: Any
    offers: tuple[tuple[str, str], ...]
    blocked: tuple[str | None, ...]
    notes: tuple[str | None, ...]
    listed: bool = True
    row: Mapping[str, Any] | None = None

    @property
    def feasible(self) -> bool:
        """@brief 候选排序优先此刻至少有一种方式可做的目标。"""
        return any(reason is None for reason in self.blocked)


@dataclass(frozen=True, slots=True)
class NameBook:
    """@brief 冻结的完整目标、本人地点、人物与可关联消息名称。"""

    targets: tuple[Target, ...]
    home: str | None = None
    here: str | None = None
    people: tuple[str, ...] = ()
    messages: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Failure:
    """@brief 名称或方式的完整错误项、可读原因与证据说明。"""

    item: ErrorItem
    sentence: str
    detail: str


@dataclass(frozen=True, slots=True)
class StartItem:
    """@brief 归一请求恰有目标、方式、应用、对方和支付应用五个字段。"""

    target: str
    way: str
    app: str | None
    to: str | None
    pay_app: str | None

    @property
    def action(self) -> str:
        """@brief 从方式唯一推出内部动词。"""
        return vocabulary.way_verb(self.way)


@dataclass(frozen=True, slots=True)
class StartRequest:
    """@brief 原子活动项与关联的未回复消息。"""

    items: tuple[StartItem, ...]
    answering: str | None


@dataclass(frozen=True, slots=True)
class StartOutcome:
    """@brief 名称解析后的请求、私有绑定行和全部名称错误。"""

    request: StartRequest | None
    rows: tuple[dict, ...] = ()
    failures: tuple[Failure, ...] = ()
    resolved: tuple[dict, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _Item:
    """@brief 一项选择或失败与无损归一记录。"""

    row: dict | None
    failure: Failure | None = None
    resolved: tuple[dict, ...] = ()


def lookup(written: str, targets: Sequence[Target], kinds: frozenset[str] = frozenset(),
           home: str | None = None) -> tuple[Target, ...]:
    """@brief 动作必须精确匹配登记名称；其他目标允许既有无损归一和本人住处写法。"""
    found = tuple(target for target in targets if target.label == written)
    if found:
        return found
    pool = tuple(target for target in targets if target.kind != "action" and (not kinds or target.kind in kinds))
    keys = object_labels._keys(written)
    for level in (0, 1):
        found = tuple(target for target in pool if object_labels._keys(target.label)[level] == keys[level])
        if found:
            return found
    if keys[2] in object_labels.HOME_WORDS and home is not None and (not kinds or "location" in kinds):
        return tuple(target for target in pool if target.label == home)
    return tuple(target for target in pool if object_labels._keys(target.label)[2] == keys[2])


def pick_way(target: Target, verb: str, way: str) -> str | None:
    """@brief 所写方式只能选中该目标声明的一种方式，同义词也必须唯一命中。"""
    hits = vocabulary.way_options(way) & {offered for owner, offered in target.offers if owner == verb}
    return next(iter(hits)) if len(hits) == 1 else None


def terms(text: str) -> frozenset[str]:
    """@brief 名称排名使用大小写归一后的单字与相邻二字。"""
    compact = "".join(text.split()).casefold()
    return frozenset(compact) | frozenset(compact[index:index + 2] for index in range(len(compact) - 1))


def dice(left: frozenset[str], right: frozenset[str]) -> float:
    """@brief 两组词项的 Dice 相似度，空集合得零。"""
    return 0.0 if not left or not right else 2 * len(left & right) / (len(left) + len(right))


def rank(written: str, entries: Iterable[_Entry], names: Callable[[_Entry], Sequence[str]],
         feasible: Callable[[_Entry], bool], limit: int = MAX_CANDIDATES) -> list[_Entry]:
    """@brief 可行性、名称相似度与完整名称决定稳定候选顺序，排名不替代所写名称。"""
    query, scored = terms(written), []
    for entry in entries:
        labels = tuple(names(entry))
        score = max((dice(query, terms(label)) for label in labels), default=0.0)
        shared = any(any(term in terms(label) for term in query if len(term) == 2) for label in labels)
        if score > 0 and (len(written) == 1 or shared):
            scored.append(((not feasible(entry), -score, labels[0]), entry))
    return [entry for _, entry in sorted(scored, key=lambda row: row[0])[:limit]]


def near_names(written: str, names: Iterable[str], limit: int = MAX_CANDIDATES) -> list[str]:
    """@brief 为名称拒绝返回近名列表，不自动修正人物输入。"""
    return rank(object_labels.bare_name(written), dict.fromkeys(names),
                lambda name: (object_labels.bare_name(name),), lambda name: True, limit)


def name_book(observation: Mapping[str, Any]) -> NameBook:
    """@brief 动作取完整登记表，商品与地点可以按名调用；人物和联系人只作为对方候选。"""
    rows = observation["interaction_bindings"]
    durations = {(target["name"], action["way"]): action["duration"]
                 for target in observation["tool_layer"]["targets"] for action in target["actions"]
                 if action.get("duration")}
    listed = tuple(_listed_target(row, durations) for row in rows)
    labels = {target.label for target in listed}
    actions = tuple(_action_target(row) for row in observation["actions"] if row["name"] not in labels)
    places = {row["location_id"]: object_label("location", row["name"]) for row in observation["locations"]}
    unlisted = tuple(_place_target(row, places[row["location_id"]]) for row in observation["locations"]
                     if places[row["location_id"]] not in labels)
    return NameBook(listed + actions + unlisted, places.get(observation["home_location_id"]),
                    places.get(observation["location_id"]),
                    tuple(dict.fromkeys([*(row["name"] for row in observation["person_bindings"]),
                                         *(row["object"] for row in rows if row["kind"] in {"person", "contact"})])),
                    tuple(row["name"] for row in observation["message_bindings"]))


def _listed_target(row: Mapping[str, Any], durations: Mapping[tuple[str, str], str]) -> Target:
    """@brief 同目标重复应用行合并为方式资格，具体应用仍由路由按所写名称选择。"""
    actions = tuple({(action["verb"], action["label"]): action for action in row["actions"]}.values())
    offers = tuple((action["verb"], action["label"]) for action in actions)
    return Target(row["object"], row["kind"], row["object_id"], offers,
                  tuple(action.get("note") for action in actions),
                  tuple(durations.get((row["object"], way)) for _, way in offers), row=row)


def _action_target(row: Mapping[str, Any]) -> Target:
    """@brief 所有登记动作均可解析，内容是否属于本人由实际受理判定。"""
    action = binding_action("进行", "do", category=row["category"], manner=row["manner"])
    binding = {"object": row["name"], "kind": "action", "object_id": row["action_id"],
               "category": row["category"], "app_ids": row["app_ids"], "manner": row["manner"],
               "communication_mode": row["communication_mode"], "actions": [action]}
    return Target(row["name"], "action", row["action_id"], (("interact", "进行"),), (None,), (None,), False, binding)


def _place_target(row: Mapping[str, Any], label: str) -> Target:
    """@brief 未展示地点可按名出行，只有驿站还提供领取方式。"""
    ways = (*_TRAVEL_MODES, *(("领取",) if row["kind"] == "parcel_station" else ()))
    actions = [binding_action(way, WAYS[way][1], travel_mode=_TRAVEL_MODES.get(way)) for way in ways]
    binding = {"object": label, "kind": "location", "object_id": row["location_id"], "actions": actions}
    return Target(label, "location", row["location_id"], tuple((WAYS[way][0], way) for way in ways),
                  tuple(None for _ in ways), tuple(None for _ in ways), False, binding)


def selection_row(target: Target, verb: str, way: str) -> dict:
    """@brief 从冻结绑定选方式，应用身份与对方由路由随后加入。"""
    action = next(item for item in target.row["actions"] if (item["verb"], item["label"]) == (verb, way))
    return {**target.row, "selected_action": dict(action)}


def fill_in(target: Target, item: StartItem, way: str | None = None) -> Candidate:
    """@brief 名称候选保留五个显式字段，仅把目标及不合方式改成声明值。"""
    chosen = way or pick_way(target, item.action, item.way)
    if chosen is None:
        chosen = next((offer[1] for offer, reason in zip(target.offers, target.blocked) if reason is None),
                      target.offers[0][1])
    position = target.offers.index((WAYS[chosen][0], chosen))
    return activity_candidate(target.label, chosen, (item.app, item.to, item.pay_app),
                              target.blocked[position] or _duration_note(target.notes[position]))


def _duration_note(note: str | None) -> str | None:
    """@brief 候选沿用冻结时长并转为英文。"""
    if note is None:
        return None
    if note == "不到 1 分钟":
        return "Less than one minute."
    return note.replace("约 ", "About ").replace(" 分钟", " minutes.").replace(" 小时", " hours.")


def near_candidates(book: NameBook, item: StartItem) -> tuple[Candidate, ...]:
    """@brief 同类近名仅提供完整请求，未展示的地点不作可达候选。"""
    kinds = frozenset().union(*(_WAY_KINDS[way] for way in vocabulary.way_options(item.way)))
    pool = (target for target in book.targets if (target.listed or target.kind == "action") and target.kind in kinds)
    chosen = rank(object_labels.bare_name(item.target), pool, lambda target: (object_labels.bare_name(target.label),),
                  lambda target: target.feasible)
    return tuple(fill_in(target, item) for target in chosen)


def resolve_start(book: NameBook, request: Any) -> StartOutcome:
    """@brief 汇总各项名称与方式错误；只归一目标、方式、人物和消息，不补应用或支付。"""
    items = [_resolve_item(book, request, index) for index in range(len(request.items))]
    normalized, records = [], []
    for raw, result in zip(request.items, items):
        label = result.row["object"] if result.row else raw.target
        way = result.row["selected_action"]["label"] if result.row else raw.way
        destination, record = _person(book, raw.to)
        normalized.append(StartItem(label, way, raw.app, destination, raw.pay_app))
        records.extend(record)
    answering, answered = _answering(book, request.answering)
    converted = StartRequest(tuple(normalized), answering)
    failures = tuple(item.failure for item in items if item.failure is not None)
    if failures:
        return StartOutcome(converted, failures=failures)
    resolved = (*(record for item in items for record in item.resolved), *records, *answered)
    return StartOutcome(converted, tuple(item.row for item in items), resolved=resolved)


def _resolve_item(book: NameBook, request: Any, index: int) -> _Item:
    """@brief 合法方式先查已知对象；未展示商品的购买交给唯一商品解析入口。"""
    raw = request.items[index]
    item = StartItem(raw.target, raw.way, raw.app, raw.to, raw.pay_app)
    options = vocabulary.way_options(item.way)
    if not options:
        reason = "The way is outside the declared closed set."
        error = ErrorItem(("items", index, "way"), item.way, "WAY_NOT_OFFERED", tuple(WAYS),
                          expected="An offered activity way is required.", why=reason)
        return _Item(None, Failure(error, reason, reason))
    way = next(iter(options))
    matches = lookup(item.target, book.targets, _WAY_KINDS[way], book.home)
    if len(matches) == 1:
        target = matches[0]
        chosen = pick_way(target, item.action, item.way)
        if chosen is None:
            return _Item(None, _failure(target, item, index, "WAY_NOT_OFFERED"))
        return _Item(selection_row(target, item.action, chosen),
                     resolved=(*_record(item.target, target.label), *_record(item.way, chosen)))
    if matches:
        return _Item(None, _ambiguous(matches, item, index))
    if way == "购买":
        return _Item(named_supply_row(item.target, "购买"), resolved=_record(item.way, "购买"))
    reason = f"No known object named {item.target} supports this way; changing spelling cannot create an object."
    error = ErrorItem(("items", index, "target"), item.target, "NAME_NOT_FOUND",
                      expected="A known object name in the selected way's category is required.", why=reason,
                      candidates=near_candidates(book, item))
    return _Item(None, Failure(error, reason, reason))


def _record(written: str, name: str) -> tuple[dict, ...]:
    """@brief 无损归一仅在写法实际变化时记证据。"""
    return () if written == name else ({"written": written, "as": name},)


def _failure(target: Target, item: StartItem, index: int, code: str) -> Failure:
    """@brief 已知对象的方式错误列出全部声明方式与可照填候选。"""
    ways = tuple(dict.fromkeys(way for _, way in target.offers))
    reason = f"The known object {target.label} does not support the written way."
    error = ErrorItem(("items", index, "way"), item.way, code, ways,
                      "A way offered by this object is required.", reason,
                      tuple(fill_in(target, item, way) for way in ways[:MAX_CANDIDATES]))
    return Failure(error, reason, reason)


def _ambiguous(matches: Sequence[Target], item: StartItem, index: int) -> Failure:
    """@brief 名称歧义只给声明过该方式的对象候选。"""
    reason = f"The name {item.target} identifies several known objects."
    candidates = tuple(fill_in(target, item) for target in matches[:MAX_CANDIDATES] if target.offers)
    error = ErrorItem(("items", index, "target"), item.target, "NAME_AMBIGUOUS",
                      expected="One unambiguous complete object name is required.", why=reason, candidates=candidates)
    return Failure(error, reason, reason)


def resolution_data(data: Mapping[str, Any], resolved: Sequence[dict], notes: Sequence[str]) -> dict[str, Any]:
    """@brief 把真实发生的名称归一与说明追加到结果。"""
    merged = dict(data)
    if resolved:
        merged["resolved"] = [*merged.get("resolved", []), *resolved]
    if notes:
        merged["notes"] = [*merged.get("notes", []), *notes]
    return merged


def _person(book: NameBook, destination: str | None) -> tuple[str | None, tuple[dict, ...]]:
    """@brief 对方和收货人沿用统一人物名称的无损归一，不猜未知人物。"""
    if destination is None or destination in book.people:
        return destination, ()
    match = object_labels.resolve_name(destination, book.people)
    return (match.name, _record(destination, match.name)) if match.name is not None else (destination, ())


def _answering(book: NameBook, answering: str | None) -> tuple[str | None, tuple[dict, ...]]:
    """@brief 可关联消息只按已冻结的消息名称解析。"""
    if answering is None or answering in book.messages:
        return answering, ()
    match = object_labels.resolve_name(answering, book.messages)
    return (match.name, _record(answering, match.name)) if match.name is not None else (answering, ())
