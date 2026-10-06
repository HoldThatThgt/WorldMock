"""@brief 同一冻结观察上的确定性名称、方式和完整请求候选。"""

from __future__ import annotations
from worldmock.catalog.models import channel_key
from worldmock.life import object_labels
from worldmock.life import vocabulary

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

from worldmock.life.error_contract import MAX_CANDIDATES, Candidate, ErrorItem, activity_candidate
from worldmock.life.vocabulary import CONTENT_WAYS, NAMED_PURCHASE_WAYS, SYSTEM_PURCHASE_WAY, WAYS
from worldmock.life.interaction_menu import binding_action, named_supply_row
from worldmock.life.object_labels import object_label
from worldmock.life.readable import TRAVEL_WAYS
from worldmock.life.routing import fill_route

_Entry = TypeVar("_Entry")

#: 活动名的开始时刻后缀「（M月D日 HH:MM 起）」：记忆层的短名（memory_layer）与名称解析共用这一个正则。
#: 邮件名称的到达时刻后缀（object_labels.mail_name 的写法经归一后：半角括号、没有空白）。
#: 场景数据的「（模拟）」标记经归一后的写法；比对时忽略。
#: 统一括号：方括号、方头括号、六角括号与尖括号都归成半角圆括号（全角圆括号、全角方括号先经 NFKC 归成半角；书名号与
#: 引号不是括号，不动）。
#: 可以增补或去掉的类别前缀（object_labels 的唯一写法，归一后以半角冒号结尾）。
#: 指本人住处的写法（归一、去掉前缀之后）：解析为人物 home_location_id 的地点；其余写法只给候选。
#: start_activity 一次至多的项数（与宿主本地校验同一上限）。
MAX_START_ITEMS = 2

#: 方式同义闭集表（接口合同「请求模型的闭集与字面边界」）：人物常写的说法 → 它可能指的方式。只有在目标列出的方式里恰好
#: 命中一个时才受理，动词仍须是该方式所属的动词（每个方式只属于一个动词，vocabulary.WAYS）；表外的写法不受理。

#: 看内容的四种方式（内容对象与只能观看的游戏对象列出其中一种）。
_WATCH_WAYS = frozenset(CONTENT_WAYS.values()) | {"观看"}
#: 方式 → 列出这种方式的私有绑定类别（按动词与方式增补前缀、限定比对范围的依据）。
_WAY_KINDS: dict[str, frozenset[str]] = {
    **{way: frozenset({"content", "game"}) for way in _WATCH_WAYS},
    "步行": frozenset({"location"}), "驾车": frozenset({"location"}), "工作": frozenset({"work_task"}),
    "游玩": frozenset({"game"}), "交谈": frozenset({"person"}), "通话": frozenset({"contact"}),
    "阅读": frozenset({"mail"}), "浏览": frozenset({"catalog"}), "进行": frozenset({"lifestyle"}),
    "等待": frozenset({"self"}), "到店": frozenset({"supply"}), "远程下单": frozenset({"supply"}),
    "购买": frozenset({"supply"}),
    "制作": frozenset({"holding"}), "食用": frozenset({"holding"}), "使用": frozenset({"holding"}),
    "给予": frozenset({"holding"}), "睡觉": frozenset({"self"}),
}
#: 动词 → 它的方式列在哪些类别上（所写方式不是方式时按动词增补前缀）。
_VERB_KINDS: dict[str, frozenset[str]] = {
    verb: frozenset().union(*(_WAY_KINDS[way] for way, (owner, _) in WAYS.items() if owner == verb))
    for verb in {owner for owner, _ in WAYS.values()}}
#: 本轮没有列出也能按名调用的方式 → 类别（named_kind，docs/dev/SPEC-map-and-storefront.md#dayrun-fidelity-按名调用与指名购买-统一名称解析  第 4 条；「购买」见 3.5
#: ）。
_NAMED_WAYS: dict[str, str] = {**{way: "content" for way in _WATCH_WAYS},
                               **{way: "supply" for way in NAMED_PURCHASE_WAYS},
                               "进行": "lifestyle", "游玩": "game", "步行": "location", "驾车": "location"}
#: named_kind 的全部取值：商品、生活活动、内容、游戏与地点。
NAMED_KINDS = ("supply", "lifestyle", "content", "game", "location")
#: 目录绑定（catalog_bindings）里的私有绑定类别：本轮没有列出的生活活动、内容与游戏。
CATALOG_KINDS = frozenset({"lifestyle", "content", "game"})
#: 出行方式 → 冻结路线的出行方式（没有列出路线的地点按所写方式找路线，由环境判定）。
_TRAVEL_MODES = {way: mode for mode, way in TRAVEL_WAYS.items()}
#: from、to 都应为 null 的动词：写成目标本身或当前所在地点时是冗余的。
_NULL_ROUTED_VERBS = frozenset({"travel", "interact", "sleep"})
#: 取得时 from 与目标写明的门店一致的判定（供给标签「<门店>出售的<商品>」，object_labels.supply_label）。


@dataclass(frozen=True, slots=True)
class Target:
    """@brief 一个可以写名称调用的对象。

    :param label: 唯一写法：工具层目标名、目录绑定名，或冻结地点表的「地点：<显示名>」。
    :param kind: 私有绑定类别（location、supply、holding、lifestyle、content、game、person、contact 等）。
    :param object_id: 内部身份（只供环境解析，不进提示）。
    :param offers: 列出的（动词，方式）；没有列出路线的地点为步行与驾车（路线由环境判定）。
    :param blocked: 与 offers 逐项对应的此刻不可用的原因（可用为 None）。
    :param notes: 与 offers 逐项对应的候选说明（如「约 20 分钟」），没有时为 None。
    :param listed: 是否在本轮工具层列出（目录绑定与地点表的为 False）。
    :param row: 本轮私有绑定行（列出的对象）；目录绑定与地点表的为 None，选中时按方式补出同形的动作。
    """

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
        """@brief 此刻是否至少有一个列出的动作可以做（候选排序：可行的在前）。"""
        return any(reason is None for reason in self.blocked)


@dataclass(frozen=True, slots=True)
class NameBook:
    """@brief 一次冻结观察里 start_activity 可以写的全部对象，以及住处、当前所在地点、去向与所回应消息的名称。

    :param targets: 本轮列出的对象、目录绑定与没有列出的地点（名称互不相同）。
    :param home: 本人住处的地点名（「地点：<显示名>」），观察没有住处时为 None。
    :param here: 当前所在地点的地点名，没有时为 None。
    :param people: to 可以写的人物名（本轮人物与联系人、同世界人物与「自己」，routing 同一口径）。
    :param messages: answering 可以写的可见直接消息名称。
    """

    targets: tuple[Target, ...]
    home: str | None = None
    here: str | None = None
    people: tuple[str, ...] = ()
    messages: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Failure:
    """@brief 一项名称类失败：逐项明细（码为 NAME_NOT_FOUND、NAME_AMBIGUOUS 或 WAY_NOT_OFFERED，带候选）、写给人物的一句
    原因（多项请求带「第 N 项」）与英文说明（证据用）。"""

    item: ErrorItem
    sentence: str
    detail: str


@dataclass(frozen=True, slots=True)
class StartItem:
    """@brief 归一后的一项活动；内部动作由合法方式唯一推导。"""

    target: str
    way: str
    from_: str | None
    to: str | None

    @property
    def action(self) -> str:
        """@brief 从方式闭集得到内部动词。"""
        return vocabulary.way_verb(self.way)


@dataclass(frozen=True, slots=True)
class StartRequest:
    """@brief 一项一个对象的归一请求。"""

    items: tuple[StartItem, ...]
    answering: str | None


@dataclass(frozen=True, slots=True)
class StartOutcome:
    """@brief start_activity 的名称解析结果。

    :param request: 归一后的请求；数组项数不在一至二或对象、动词、方式不等长时为 None（由调用方报形状错误）。
    :param rows: 逐项选中的绑定行（selected_action 为所选动作；指名购买为 named_supply 占位行），有失败时为空。
    :param failures: 逐项的名称类失败，按请求中的先后。
    :param resolved: 受理时写进 details.resolved 的归一记录 [{written, as}]。
    :param notes: 受理时写进 details.notes 的说明（冗余的 from、to）。
    """

    request: StartRequest | None
    rows: tuple[dict, ...] = ()
    failures: tuple[Failure, ...] = ()
    resolved: tuple[dict, ...] = ()
    notes: tuple[str, ...] = ()




@dataclass(frozen=True, slots=True)
class _Item:
    """@brief start_activity 一项的解析：选中的绑定行，或名称类失败；resolved 为这一项的归一记录。"""

    row: dict | None
    failure: Failure | None = None
    resolved: tuple[dict, ...] = ()














def named_kind(verb: str, way: str) -> str | None:
    """@brief 按「动词加方式」推出的按名调用类别（取代 is_named_purchase）：supply（取得的到店、远程下单与由系统定方式的
    「购买」）、lifestyle（进行）、content（观看与四种续看）、game（游玩）或 location（步行、驾车）；动词不是该方式所属的
    动词、方式不在闭集与同义表中，或推出不止一类时为 None。"""
    kinds = {_NAMED_WAYS[option] for option in vocabulary.way_options(way)
             if option in _NAMED_WAYS and WAYS[option][0] == verb}
    return next(iter(kinds)) if len(kinds) == 1 else None


def _hint(verb: str, way: str) -> frozenset[str]:
    """@brief 按动词与方式增补前缀时比对的类别：方式可能指的方式列在哪些类别上；方式不是方式时按动词推出。"""
    options = vocabulary.way_options(way)
    if options:
        return frozenset().union(*(_WAY_KINDS[option] for option in options))
    return _VERB_KINDS.get(verb, frozenset())


def lookup(written: str, targets: Sequence[Target], kinds: frozenset[str] = frozenset(),
           home: str | None = None) -> tuple[Target, ...]:
    """@brief 按名称找对象：先精确匹配，再依次按归一写法、去掉开始时刻与邮件时刻、增补或去掉类别前缀逐级放宽，第一级有匹配
    就停；「家」「住处」指本人住处；按前缀比对时只看 kinds 中的类别（按动词与方式推出，为空时看全部类别）。

    :returns: 匹配到的对象；多于一个即名称有歧义，空即找不到。
    """
    found = tuple(target for target in targets if target.label == written)
    keys = object_labels._keys(written)
    for level in (0, 1):
        found = found or tuple(target for target in targets if object_labels._keys(target.label)[level] == keys[level])
    if found:
        return found
    if keys[2] in object_labels.HOME_WORDS and home is not None and (not kinds or "location" in kinds):
        found = tuple(target for target in targets if target.label == home)
        if found:
            return found
    return tuple(target for target in targets
                 if (not kinds or target.kind in kinds) and object_labels._keys(target.label)[2] == keys[2])




def pick_way(target: Target, verb: str, way: str) -> str | None:
    """@brief 目标列出的、与所写动词和方式相配的方式：照写的方式，或同义表在目标列出的方式里恰好命中的那一个；动词须是
    该方式所属的动词，不由方式反推动词（时间与能量规格裁决 #20）；不相配时为 None。"""
    if (verb, way) in target.offers:
        return way
    hits = vocabulary.way_options(way) & {offered for _, offered in target.offers}
    if len(hits) != 1:
        return None
    chosen = next(iter(hits))
    return chosen if (verb, chosen) in target.offers else None


def terms(text: str) -> frozenset[str]:
    """@brief 名称的词项：去掉空白、统一大小写后的单字与相邻二字（确定性，不用向量检索与近似最近邻）。"""
    compact = "".join(text.split()).casefold()
    return frozenset(compact) | frozenset(compact[index:index + 2] for index in range(len(compact) - 1))


def dice(left: frozenset[str], right: frozenset[str]) -> float:
    """@brief 两组词项的 Dice 系数：2|A∩B| / (|A|+|B|)，任一为空时为 0。"""
    if not left or not right:
        return 0.0
    return 2 * len(left & right) / (len(left) + len(right))


def rank(written: str, entries: Iterable[_Entry], names: Callable[[_Entry], Sequence[str]],
         feasible: Callable[[_Entry], bool], limit: int = MAX_CANDIDATES) -> list[_Entry]:
    """@brief 相似度排名（跨类别）：此刻可行的在前，其次按所写名称与各项名称的单字加二字 Dice 系数（取各名称中的最大者）
    降序、第一个名称升序；相似度为 0 的不列，至多 limit 个。从不据此替换人物写的名称。

    :param names: 一项的名称（第一个是它的写法，其余是也可比对的名称，如商品名）。
    :param feasible: 一项此刻是否可行。
    """
    query = terms(written)
    scored = []
    for entry in entries:
        labels = tuple(names(entry))
        score = max((dice(query, terms(label)) for label in labels), default=0.0)
        shared = any(any(term in terms(label) for term in query if len(term) == 2) for label in labels)
        if score > 0 and (len(written) == 1 or shared):
            scored.append(((not feasible(entry), -score, labels[0]), entry))
    scored.sort(key=lambda row: row[0])
    return [entry for _, entry in scored[:limit]]


def near_names(written: str, names: Iterable[str], limit: int = MAX_CANDIDATES) -> list[str]:
    """@brief 一组可写名称中与所写名称最相近的几个（按去掉前缀后的归一写法比较），供不是 start_activity 的名称参数的原因
    说明使用。"""
    return rank(object_labels.bare_name(written), dict.fromkeys(names),
                lambda name: (object_labels.bare_name(name),), lambda name: True, limit)


def name_book(observation: Mapping[str, Any]) -> NameBook:
    """@brief 从一次冻结观察的私有绑定取 start_activity 可以写的全部对象：本轮列出的交互对象（interaction_bindings，候选
    说明取工具层同一动作的时长）、目录绑定（catalog_bindings）与冻结地点表中没有列出的地点（locations），以及住处
    （home_location_id）、当前所在地点（location_id）、去向与 answering 的名称；缺失的键按空处理（宿主另行校验形状）。"""
    rows = list(observation.get("interaction_bindings") or [])
    durations = _durations(observation.get("tool_layer") or {})
    listed = tuple(_listed_target(row, durations) for row in rows)
    labels = {target.label for target in listed}
    catalog = tuple(_catalog_target(row) for row in observation.get("catalog_bindings") or []
                    if row["object"] not in labels)
    places = {row["location_id"]: object_label("location", row["name"]) for row in observation.get("locations") or []
              if isinstance(row, Mapping) and isinstance(row.get("name"), str)}
    unlisted = tuple(_place_target(identity, label) for identity, label in places.items() if label not in labels)
    people = (*(row["name"] for row in observation.get("person_bindings") or []),
              *(row["object"] for row in rows if row["kind"] in {"person", "contact", "self"}))
    return NameBook(targets=listed + catalog + unlisted, home=places.get(observation.get("home_location_id")),
                    here=places.get(observation.get("location_id")), people=tuple(dict.fromkeys(people)),
                    messages=tuple(row["name"] for row in observation.get("message_bindings") or []))


def _durations(tool_layer: Mapping[str, Any]) -> dict[tuple[str, str, str], str]:
    """@brief 工具层各目标动作的时长短语：（目标名，动词，方式）→「约 N 分钟」（没有时不记）。"""
    return {(target["name"], action["action"], action["way"]): action["duration"]
            for target in tool_layer.get("targets") or [] for action in target.get("actions") or []
            if action.get("duration")}


def _listed_target(row: Mapping[str, Any], durations: Mapping[tuple[str, str, str], str]) -> Target:
    """@brief 本轮列出的交互对象：列出的动作、各动作不可用的原因（私有绑定 note 与工具层 reason 同一文本）与时长说明。"""
    actions = row["actions"]
    offers = tuple((action["verb"], action["label"]) for action in actions)
    return Target(label=row["object"], kind=row["kind"], object_id=row.get("object_id"), offers=offers,
                  blocked=tuple(action.get("note") for action in actions),
                  notes=tuple(durations.get((row["object"], *offer)) for offer in offers), row=row)


def _catalog_target(row: Mapping[str, Any]) -> Target:
    """@brief 目录绑定的一项（本轮没有列出的生活活动、内容或游戏）：一个动作，此刻不能做时带单独提交的第一条拒绝原因
    （named_catalog 按工具层可用性同一判定写下）。"""
    way = row["way"]
    return Target(label=row["object"], kind=row["kind"], object_id=row["object_id"], offers=((WAYS[way][0], way),),
                  blocked=(row.get("reason"),), notes=(row.get("note"),), listed=False)


def _place_target(identity: str, label: str) -> Target:
    """@brief 冻结地点表中本轮没有列出路线的地点：按名调用时由环境按所写方式找路线并判定，不作候选。"""
    offers = tuple(("travel", way) for way in _TRAVEL_MODES)
    return Target(label=label, kind="location", object_id=identity, offers=offers,
                  blocked=tuple("没有列出去那里的路线" for _ in offers), notes=tuple(None for _ in offers), listed=False)


def selection_row(target: Target, verb: str, way: str) -> dict:
    """@brief 选中目标与（动词，方式）的绑定行副本（selected_action 为所选动作）：列出的对象取本轮私有绑定行；目录绑定与
    没有列出的地点按方式补出与观察投影同形的动作（出行方式取方式对应的冻结路线方式）。"""
    if target.row is not None:
        action = next(item for item in target.row["actions"] if (item["verb"], item["label"]) == (verb, way))
        return {**target.row, "selected_action": action}
    if target.kind == "location":
        action = binding_action(way, "travel_to", travel_mode=_TRAVEL_MODES[way])
    else:
        action = binding_action(way, WAYS[way][1])
    return {"object": target.label, "kind": target.kind, "object_id": target.object_id, "actions": [action],
            "selected_action": action}


def fill_in(target: Target, verb: str, way: str, written_to: str | None) -> Candidate:
    """@brief 目标写成可以照填的请求：所写的动词与方式在目标上相配时照用，否则取第一个此刻可用的（都不可用时取第一个）
    列出的（动词，方式）；from、to 照所选动作填（routing.fill_route）；说明取该动作此刻不可用的原因，否则取时长说明。"""
    chosen = pick_way(target, verb, way)
    offered = target.offers
    if chosen is not None:
        pair = (verb, chosen)
    else:
        pair = next((offer for offer, reason in zip(offered, target.blocked) if reason is None), offered[0])
    position = offered.index(pair)
    source, destination = fill_route(pair[0], pair[1], written_to)
    return activity_candidate(target.label, pair[1], (source, destination),
                              target.blocked[position] or _duration_note(target.notes[position]))


def _duration_note(note: str | None) -> str | None:
    """@brief 候选时长沿用冻结说明并转为英文。"""
    if note is None:
        return None
    if note == "不到 1 分钟":
        return "Less than one minute."
    return note.replace("约 ", "About ").replace(" 分钟", " minutes.").replace(" 小时", " hours.")


def _acquisition_candidates(book: NameBook, written: str) -> tuple[Candidate, ...]:
    """@brief 未持有食品的下一步只使用同一冻结观察中可购买或制作的对象。"""
    pool = [target for target in book.targets if target.listed and
            any(verb == "obtain" or way == "制作" for verb, way in target.offers)]
    chosen = rank(object_labels.bare_name(written), pool, lambda target: (object_labels.bare_name(target.label),),
                  lambda target: target.feasible)
    candidates = []
    for target in chosen:
        pair = next(pair for pair in target.offers if pair[0] == "obtain" or pair[1] == "制作")
        candidate = fill_in(target, *pair, None)
        note = candidate.note or "Acquire the item before consuming it."
        candidates.append(Candidate(candidate.tool, candidate.arguments, note))
    return tuple(candidates)


def near_candidates(book: NameBook, written: str, verb: str, way: str, written_to: str | None) -> tuple[Candidate, ...]:
    """@brief 找不到的对象名称的候选：跨类别在本轮列出的对象与目录绑定中按相似度排名（此刻可行的在前，至多 3 个），各写成
    可以照填的请求；没有列出路线的地点不作候选（去不了）。"""
    pool = [target for target in book.targets if (target.listed or target.kind in CATALOG_KINDS)
            and target.kind in _hint(verb, way)]
    chosen = rank(object_labels.bare_name(written), pool, lambda target: (object_labels.bare_name(target.label),),
                  lambda target: target.feasible)
    return tuple(fill_in(target, verb, way, written_to) for target in chosen)


def resolve_start(book: NameBook, request: Any) -> StartOutcome:
    """@brief 收集每项名称和方式错误，全部合法后组装归一请求。"""
    resolved_items = [_resolve_item(book, request, index) for index in range(len(request.items))]
    failures = tuple(item.failure for item in resolved_items if item.failure is not None)
    labels = [item.row["object"] if item.row else request.items[index].target
              for index, item in enumerate(resolved_items)]
    ways = [item.row["selected_action"]["label"] if item.row else request.items[index].way
            for index, item in enumerate(resolved_items)]
    normalized, records = [], []
    for item, label, way in zip(request.items, labels, ways):
        destination, record = _person(book, vocabulary.way_verb(way), item.to)
        normalized.append(StartItem(label, way, item.from_, destination))
        records.extend(record)
    answering, answered = _answering(book, request.answering)
    converted = StartRequest(tuple(normalized), answering)
    if failures:
        return StartOutcome(converted, failures=failures)
    resolved = (*(record for item in resolved_items for record in item.resolved), *records, *answered)
    return StartOutcome(converted, tuple(item.row for item in resolved_items), (), resolved,
                        _source_notes(book, converted))


def _source_notes(book: NameBook, request: StartRequest) -> tuple[str, ...]:
    """@brief 完整商品标签与同店来源一致时说明来源可以为空。"""
    places = tuple(target.label for target in book.targets if target.kind == "location")
    notes = []
    for item in request.items:
        if item.action != "obtain" or item.from_ is None:
            continue
        store = object_labels.written_place(item.from_, places, book.home)
        if store is not None and object_labels.names_store(item.target, store):
            notes.append(f"The target already names {object_labels.bare_name(store)}; from may be null.")
    return tuple(notes)


def _resolve_item(book: NameBook, request: Any, index: int) -> _Item:
    """@brief 一项：对象唯一时再核对（动词，方式）；找不到而方式是到店、远程下单或「购买」时是指名购买（占位行）；其余为
    名称类失败。"""
    item = request.items[index]
    written, verb, way = item.target, vocabulary.way_verb(item.way), item.way
    if not verb:
        reason = "The way is outside the declared closed set."
        error = ErrorItem(("items", index, "way"), way, "WAY_NOT_OFFERED", tuple(WAYS),
                          expected="An offered activity way is required.", why=reason)
        return _Item(None, Failure(error, reason, reason))
    matches = lookup(written, book.targets, _hint(verb, way), book.home)
    if len(matches) == 1:
        return _chosen(matches[0], request, index)
    if matches:
        return _Item(None, _ambiguous(matches, request, index))
    purchase = vocabulary.way_options(way) & NAMED_PURCHASE_WAYS
    if named_kind(verb, way) == "supply" and len(purchase) == 1:
        chosen = next(iter(purchase))
        return _Item(named_supply_row(written, chosen), resolved=_record(way, chosen))
    return _Item(None, _not_found(book, request, index))


def _chosen(target: Target, request: Any, index: int) -> _Item:
    """@brief 对象已唯一：动词与方式相配时选中该动作（名称或方式经归一的记一条）；商品目标写「购买」（或它的同义写法）时
    是由系统定方式的占位行（docs/dev/SPEC-map-and-storefront.md#dayrun-fidelity-按名调用与指名购买-更简单的购买请求裁决-s10 ：对任何商品目标都受理，工具层不列出它）；否则
    WAY_NOT_OFFERED。"""
    item = request.items[index]
    written, verb, way = item.target, vocabulary.way_verb(item.way), item.way
    chosen = pick_way(target, verb, way)
    if (chosen is None and target.kind == "supply" and verb == "obtain"
            and SYSTEM_PURCHASE_WAY in vocabulary.way_options(way)):
        return _Item(named_supply_row(target.label, SYSTEM_PURCHASE_WAY),
                     resolved=(*_record(written, target.label), *_record(way, SYSTEM_PURCHASE_WAY)))
    if chosen is None:
        return _Item(None, _way_failure(target, request, index))
    return _Item(selection_row(target, verb, chosen), resolved=(*_record(written, target.label), *_record(way, chosen)))


def _record(written: str, name: str) -> tuple[dict, ...]:
    """@brief 一条归一记录 {written, as}；照写的没有记录。"""
    return () if written == name else ({"written": written, "as": name},)


def resolution_data(data: Mapping[str, Any], resolved: Sequence[dict], notes: Sequence[str]) -> dict[str, Any]:
    """@brief 受理结果的 data 追加名称归一 resolved（[{written, as}]）与说明 notes（字符串列表）：接在已有的同名键之后，
    两者都没有时原样返回副本（公开结果按 result_projection 原样写进 details）。"""
    merged = dict(data)
    if resolved:
        merged["resolved"] = [*merged.get("resolved", []), *resolved]
    if notes:
        merged["notes"] = [*merged.get("notes", []), *notes]
    return merged


def _to(request: Any, index: int) -> str | None:
    """@brief 读取这一项的收货人；null 一律表示本人。"""
    return request.items[index].to


def _not_found(book: NameBook, request: Any, index: int) -> Failure:
    """@brief 未知名称仅在所选方式的对象类别中提供近名候选。"""
    item = request.items[index]
    verb = vocabulary.way_verb(item.way)
    candidates = near_candidates(book, item.target, verb, item.way, item.to)
    acquisition = verb == "consume" and item.way == "食用" and not candidates
    if acquisition:
        candidates = _acquisition_candidates(book, item.target)
    reason = f"No known object named {item.target} supports this way; changing spelling cannot create an object."
    expected = ("A held edible item is required; purchase or make it first." if acquisition and candidates
                else "A known object name in the selected way's category is required.")
    error = ErrorItem(("items", index, "target"), item.target, "NAME_NOT_FOUND",
                      expected=expected,
                      why=reason, candidates=candidates)
    return Failure(error, reason, reason)


def _ambiguous(matches: Sequence[Target], request: Any, index: int) -> Failure:
    """@brief 名称歧义返回完整可提交候选。"""
    item = request.items[index]
    candidates = tuple(fill_in(target, vocabulary.way_verb(item.way), item.way, item.to)
                       for target in matches[:MAX_CANDIDATES])
    reason = f"The name {item.target} identifies several known objects."
    error = ErrorItem(("items", index, "target"), item.target, "NAME_AMBIGUOUS",
                      expected="One unambiguous complete object name is required.", why=reason,
                      candidates=candidates)
    return Failure(error, reason, reason)


def _way_failure(target: Target, request: Any, index: int) -> Failure:
    """@brief 已知对象不支持方式时返回该对象的全部合法方式。"""
    item = request.items[index]
    pairs = tuple(dict.fromkeys(target.offers))
    candidates = tuple(fill_in(target, *pair, item.to) for pair in pairs[:MAX_CANDIDATES])
    reason = f"The known object {target.label} does not support the written way."
    error = ErrorItem(("items", index, "way"), item.way, "WAY_NOT_OFFERED",
                      tuple(pair[1] for pair in pairs), "A way offered by this object is required.", reason,
                      candidates)
    return Failure(error, reason, reason)










def resolve_source(written: str, places: Mapping[str, str], home: str | None,
                   channels: Sequence[ChannelRow]) -> SourceMatch | None:
    """@brief 所写购买来源的范围：购买与查询共用的纯函数，不读世界与环境上下文（docs/dev/SPEC-interaction-menu.md#errands-workday-查询筛选与返回精简-来源解析 ，
    docs/dev/SPEC-map-and-storefront.md#dayrun-fidelity-按名调用与指名购买-购买渠道app-名解析为成员门店  解析规则 1）。地点显示名（精确、无损归一或去掉「地点：」前缀后唯一，w
    ritten_place；
    「家」「住处」指住处）是该显示名的地点；否则是渠道称呼（channel_key 比较）时是该渠道的成员门店并记下渠道身份；都不是
    时为 None。null 来源不归本函数（购买侧是全部门店，查询侧是不按来源筛选）。

    :param places: 场景全部地点：地点身份 → 显示名。
    :param home: 本人住处的地点身份；没有时为 None。
    :param channels: 渠道表（ChannelRow），按声明顺序；先命中的渠道生效。
    """
    place = object_labels.written_place(written, tuple(places.values()), places.get(home))
    if place is not None:
        return SourceMatch(frozenset(identity for identity, name in places.items() if name == place), None, place)
    key = channel_key(written)
    row = next((item for item in channels if key in {channel_key(name) for name in item[1]}), None)
    return None if row is None else SourceMatch(frozenset(row[2]), row[0], written)


def near_source_names(written: str, places: Mapping[str, str], stores: Iterable[str],
                      channels: Sequence[ChannelRow], feasible: Callable[[frozenset[str]], bool]) -> list[str]:
    """@brief 与所写来源最相近的至多 3 个来源名（纯函数）：stores 中各门店的显示名（按地点身份排序）与各渠道的第一个称呼
    （其余称呼也参与比对），按 rank 排名（可行的在前）；一个也不相近时按同一顺序取前几个可行的来源。

    :param places: 地点身份 → 显示名（须含 stores 中的每一处）。
    :param stores: 可作候选的门店（地点身份，可重复）。
    :param channels: 渠道表（ChannelRow）；没有称呼的渠道不作候选。
    :param feasible: 一个来源范围（门店身份集合）是否可行。
    """
    entries = [*((places[store], (places[store],), frozenset({store})) for store in sorted(set(stores))),
               *((row[1][0], tuple(row[1]), frozenset(row[2])) for row in channels if row[1])]
    chosen = rank(written, entries, lambda entry: entry[1], lambda entry: feasible(entry[2]))
    return [entry[0] for entry in chosen or [entry for entry in entries if feasible(entry[2])][:MAX_CANDIDATES]]


def _same_place(book: NameBook, written: str, anchor: str | None) -> bool:
    """@brief 所写的名称是否就是 anchor（按去掉前缀后的归一写法比较；「家」「住处」按本人住处比较）。"""
    if anchor is None:
        return False
    key = object_labels.bare_name(written)
    if key in object_labels.HOME_WORDS and book.home is not None:
        return book.home == anchor
    return key == object_labels.bare_name(anchor)


def _person(book: NameBook, verb: str, destination: str | None) -> tuple[str | None, tuple[dict, ...]]:
    """@brief obtain 与 consume 的 to 写的人物名：按人物名称解析，唯一时换成它（记一条归一记录），否则原样交给 routing
    （不是任何人物时环境以 RECIPIENT_NOT_FOUND 拒绝）。"""
    if destination is None or verb not in {"obtain", "consume"} or destination in book.people:
        return destination, ()
    match = object_labels.resolve_name(destination, book.people)
    return (match.name, _record(destination, match.name)) if match.name is not None else (destination, ())


def _answering(book: NameBook, answering: str | None) -> tuple[str | None, tuple[dict, ...]]:
    """@brief answering 写的消息名：按可见直接消息的名称解析，唯一时换成它（记一条归一记录），否则原样交给 routing。"""
    if answering is None or answering in book.messages:
        return answering, ()
    match = object_labels.resolve_name(answering, book.messages)
    return (match.name, _record(answering, match.name)) if match.name is not None else (answering, ())
