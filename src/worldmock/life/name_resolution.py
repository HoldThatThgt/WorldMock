"""@brief 统一名称解析（docs/dev/SPEC-dayrun-fidelity.md 3.1）：宿主本地校验与环境受理共用的纯函数模块。

所有目录类对象（商品与供给、生活活动、内容、游戏、地点、联系人与人物、任务、活动）都可以直接写名称。解析分三段：

1. 精确匹配；
2. 无损归一，结果唯一就受理，归一记进受理结果的 details.resolved（[{written, as}]）：统一全半角与括号、去掉空白，比对时
   忽略「（模拟）」；去掉活动名的开始时刻后缀（START_SUFFIX，与记忆层的短名共用这一个正则）与邮件的到达时刻；按动词与
   方式增补或去掉「地点：」「活动：」「内容：」「持有物：」「工作：」「联系人：」等类别前缀（object_labels 的唯一写法）；
   「家」「住处」指本人的住处（home_location_id），其余写法只给候选；方式按闭集同义表 WAY_SYNONYMS 在目标列出的方式里
   恰好命中一个时受理，动词仍须是该方式所属的动词——动词与方式不一致时不替人物改，报 WAY_NOT_OFFERED，候选为该目标
   列出的（动词，方式），不由方式反推动词；商品目标写「购买」（或它的同义写法）时不论是否列出都是由系统定到店或远程
   下单的指名购买占位行（3.5）；与目标或当前所在地点一致的冗余 from、to 照样受理并在 details.notes 说明；
3. 相似度永不自动替换（人物 Prompt 规格裁决 #19）：跨类别按单字加二字的 Dice 系数排名，此刻可行的在前，至多 3 个候选，
   每个候选写成可以照填的请求 {target, action, way, from, to}（error_contract.Candidate，可另带 note，如「约 20 分钟」）。

宿主与环境在同一冻结观察上调用同一组函数（name_book、resolve_start、resolve_name），所以宿主判为可受理的，环境也受理
（tests/life/test_name_resolution.py 的性质测试）。分工：本轮私有绑定、目录绑定（catalog_bindings：本轮没有列出的生活
活动、内容与游戏，named_catalog）与冻结地点表（observation.locations）都在冻结观察里，名称是否存在由宿主判定，重试不花钱；
存在但没有列出的对象（目录绑定里的生活活动、内容与游戏，没有列出路线的地点）的资格与准入由环境受理时判定；指名购买的商品
（named_kind 为 supply）只由环境按名称解析（named_purchase，分段匹配与来源约束）。本模块只读名称与观察的私有绑定，不读世界
状态；names_store 与 written_place 供来源约束判定 from 写的门店（宿主 routing 与环境 purchase_offers 共用）。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, TypeVar

from worldmock.life.error_contract import MAX_CANDIDATES, Candidate, ErrorItem, numbered
from worldmock.life.interaction_menu import (CONTENT_WAYS, NAMED_PURCHASE_WAYS, SYSTEM_PURCHASE_WAY,
                                             VERB_WAY_SEPARATOR, WAYS, binding_action, named_supply_row)
from worldmock.life.object_labels import OBJECT_PREFIXES, object_label
from worldmock.life.readable import TRAVEL_WAYS
from worldmock.life.routing import fill_route

_Entry = TypeVar("_Entry")

#: 活动名的开始时刻后缀「（M月D日 HH:MM 起）」：记忆层的短名（memory_layer）与名称解析共用这一个正则。
START_SUFFIX = re.compile(r"（\d{1,2}月\d{1,2}日 \d{2}:\d{2} 起）")
#: 邮件名称的到达时刻后缀（object_labels.mail_name 的写法经归一后：半角括号、没有空白）。
_MAIL_STAMP = re.compile(r"\(\d{4}年\d{1,2}月\d{1,2}日\d{1,2}:\d{2}:\d{2}\)$")
#: 场景数据的「（模拟）」标记经归一后的写法；比对时忽略。
_SIMULATED = "(模拟)"
#: 统一括号：方括号、方头括号、六角括号与尖括号都归成半角圆括号（全角圆括号、全角方括号先经 NFKC 归成半角；书名号与
#: 引号不是括号，不动）。
_BRACKETS = str.maketrans({"[": "(", "]": ")", "【": "(", "】": ")", "〔": "(", "〕": ")", "〖": "(", "〗": ")",
                           "〈": "(", "〉": ")"})
#: 可以增补或去掉的类别前缀（object_labels 的唯一写法，归一后以半角冒号结尾）。
_PREFIXES = tuple(f"{prefix}:" for prefix in OBJECT_PREFIXES.values())
#: 指本人住处的写法（归一、去掉前缀之后）：解析为人物 home_location_id 的地点；其余写法只给候选。
HOME_WORDS = frozenset({"家", "住处"})
#: start_activity 一次至多的项数（与宿主本地校验同一上限）。
MAX_START_ITEMS = 2

#: 方式同义闭集表（接口合同「请求模型的闭集与字面边界」）：人物常写的说法 → 它可能指的方式。只有在目标列出的方式里恰好
#: 命中一个时才受理，动词仍须是该方式所属的动词（每个方式只属于一个动词，interaction_menu.WAYS）；表外的写法不受理。
WAY_SYNONYMS: dict[str, frozenset[str]] = {key: frozenset(value) for key, value in {
    "走路": {"步行"}, "走过去": {"步行"}, "步行前往": {"步行"},
    "开车": {"驾车"}, "自驾": {"驾车"}, "驾车前往": {"驾车"}, "开车前往": {"驾车"},
    "上班": {"工作"}, "办公": {"工作"},
    "看": {"观看", "接着看", "从头再看"}, "收看": {"观看", "接着看", "从头再看"}, "观看": {"接着看", "从头再看"},
    "继续看": {"接着看"}, "重看": {"从头再看"}, "重新看": {"从头再看"},
    "听": {"接着听", "从头再听"}, "收听": {"接着听", "从头再听"}, "继续听": {"接着听"}, "重听": {"从头再听"},
    "重新听": {"从头再听"},
    "玩": {"游玩"}, "玩游戏": {"游玩"},
    "聊天": {"交谈"}, "聊": {"交谈"}, "面谈": {"交谈"},
    "打电话": {"通话"}, "电话": {"通话"},
    "读": {"阅读"}, "看邮件": {"阅读"}, "查看": {"阅读", "浏览"},
    "逛": {"浏览"}, "逛逛": {"浏览"},
    "做": {"进行"}, "参加": {"进行"},
    "等": {"等待"}, "等着": {"等待"}, "等候": {"等待"},
    "到店购买": {"到店"}, "到店点单": {"到店"}, "堂食": {"到店"}, "自取": {"到店"},
    "外卖": {"远程下单"}, "点外卖": {"远程下单"}, "配送": {"远程下单"}, "送货上门": {"远程下单"},
    "网上下单": {"远程下单"}, "线上下单": {"远程下单"},
    "买": {"购买"}, "下单": {"购买"}, "下单购买": {"购买"}, "点单": {"购买"}, "订购": {"购买"},
    "做饭": {"制作"},
    "吃": {"食用"}, "吃饭": {"食用"}, "用餐": {"食用"}, "进食": {"食用"},
    "用": {"使用"},
    "送": {"给予"}, "给": {"给予"}, "赠送": {"给予"},
    "睡": {"睡觉"}, "睡眠": {"睡觉"}, "就寝": {"睡觉"},
}.items()}

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
#: 本轮没有列出也能按名调用的方式 → 类别（named_kind，docs/dev/SPEC-dayrun-fidelity.md 3.1 第 4 条；「购买」见 3.5）。
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
_SOLD_BY = "出售的"


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
class StartRequest:
    """@brief 归一后的 start_activity 请求：对象换成解析出的名称，方式换成目标列出的方式，冗余的 from、to 换成 null，
    to 与 answering 换成解析出的人物与消息名称；来源、去向与 answering 的组合由 routing 照常判定。"""

    targets: tuple[str, ...]
    actions: tuple[str, ...]
    ways: tuple[str, ...]
    from_: tuple[str | None, ...]
    to: tuple[str | None, ...]
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
class NameMatch:
    """@brief 一个名称在一组可写名称里的解析：唯一时 name 为解析出的名称（照写的就是原文），否则为 None；matches 为同一级的
    全部匹配（多于一个即有歧义，没有即找不到）。"""

    name: str | None
    matches: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Item:
    """@brief start_activity 一项的解析：选中的绑定行，或名称类失败；resolved 为这一项的归一记录。"""

    row: dict | None
    failure: Failure | None = None
    resolved: tuple[dict, ...] = ()


def fold(text: str) -> str:
    """@brief 无损归一的基本写法：统一全半角（NFKC）与括号，去掉全部空白，忽略「（模拟）」。"""
    unified = unicodedata.normalize("NFKC", text).translate(_BRACKETS)
    return "".join(unified.split()).replace(_SIMULATED, "")


@lru_cache(maxsize=8192)
def _keys(text: str) -> tuple[str, str, str]:
    """@brief 名称逐级放宽的比对键：归一写法；再去掉活动开始时刻（START_SUFFIX）与邮件到达时刻；再去掉类别前缀。"""
    folded = fold(text)
    named = _MAIL_STAMP.sub("", fold(START_SUFFIX.sub("", text)))
    return folded, named, _unprefixed(named)


def _unprefixed(key: str) -> str:
    """@brief 去掉归一写法开头的类别前缀（只去 object_labels 的前缀，前缀后没有名称时不去）。"""
    return next((key[len(prefix):] for prefix in _PREFIXES if key.startswith(prefix) and len(key) > len(prefix)), key)


def bare_name(text: str) -> str:
    """@brief 名称的最宽比对键：归一、去掉开始时刻与邮件时刻、去掉类别前缀（候选相似度也按它计算）。"""
    return _keys(text)[2]


def way_options(way: str) -> frozenset[str]:
    """@brief 所写方式可能指的方式：方式闭集中的原词（去掉空白、统一全半角后），加上同义表列出的；都不是时为空集（不是
    方式）。"""
    key = "".join(unicodedata.normalize("NFKC", way).split())
    return (frozenset({key}) if key in WAYS else frozenset()) | WAY_SYNONYMS.get(key, frozenset())


def named_kind(verb: str, way: str) -> str | None:
    """@brief 按「动词加方式」推出的按名调用类别（取代 is_named_purchase）：supply（取得的到店、远程下单与由系统定方式的
    「购买」）、lifestyle（进行）、content（观看与四种续看）、game（游玩）或 location（步行、驾车）；动词不是该方式所属的
    动词、方式不在闭集与同义表中，或推出不止一类时为 None。"""
    kinds = {_NAMED_WAYS[option] for option in way_options(way) if option in _NAMED_WAYS and WAYS[option][0] == verb}
    return next(iter(kinds)) if len(kinds) == 1 else None


def _hint(verb: str, way: str) -> frozenset[str]:
    """@brief 按动词与方式增补前缀时比对的类别：方式可能指的方式列在哪些类别上；方式不是方式时按动词推出。"""
    options = way_options(way)
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
    keys = _keys(written)
    for level in (0, 1):
        found = found or tuple(target for target in targets if _keys(target.label)[level] == keys[level])
    if found:
        return found
    if keys[2] in HOME_WORDS and home is not None and (not kinds or "location" in kinds):
        found = tuple(target for target in targets if target.label == home)
        if found:
            return found
    return tuple(target for target in targets
                 if (not kinds or target.kind in kinds) and _keys(target.label)[2] == keys[2])


def resolve_name(written: str, names: Iterable[str]) -> NameMatch:
    """@brief 在一组可写名称（活动名、任务名、回看名称、收件人、人物与消息名称）中解析所写的名称：精确匹配，再按归一写法、
    去掉开始时刻与邮件时刻、去掉类别前缀逐级放宽，第一级有匹配就停，唯一才受理。"""
    unique = tuple(dict.fromkeys(names))
    if written in unique:
        return NameMatch(written, (written,))
    keys = _keys(written)
    for level in (0, 1, 2):
        found = tuple(name for name in unique if _keys(name)[level] == keys[level])
        if found:
            return NameMatch(found[0] if len(found) == 1 else None, found)
    return NameMatch(None, ())


def pick_way(target: Target, verb: str, way: str) -> str | None:
    """@brief 目标列出的、与所写动词和方式相配的方式：照写的方式，或同义表在目标列出的方式里恰好命中的那一个；动词须是
    该方式所属的动词，不由方式反推动词（时间与能量规格裁决 #20）；不相配时为 None。"""
    if (verb, way) in target.offers:
        return way
    hits = way_options(way) & {offered for _, offered in target.offers}
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
        if score > 0:
            scored.append(((not feasible(entry), -score, labels[0]), entry))
    scored.sort(key=lambda row: row[0])
    return [entry for _, entry in scored[:limit]]


def near_names(written: str, names: Iterable[str], limit: int = MAX_CANDIDATES) -> list[str]:
    """@brief 一组可写名称中与所写名称最相近的几个（按去掉前缀后的归一写法比较），供不是 start_activity 的名称参数的原因
    说明使用。"""
    return rank(bare_name(written), dict.fromkeys(names), lambda name: (bare_name(name),), lambda name: True, limit)


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
    return Candidate(target.label, pair[0], pair[1], source, destination,
                     target.blocked[position] or target.notes[position])


def near_candidates(book: NameBook, written: str, verb: str, way: str, written_to: str | None) -> tuple[Candidate, ...]:
    """@brief 找不到的对象名称的候选：跨类别在本轮列出的对象与目录绑定中按相似度排名（此刻可行的在前，至多 3 个），各写成
    可以照填的请求；没有列出路线的地点不作候选（去不了）。"""
    pool = [target for target in book.targets if target.listed or target.kind in CATALOG_KINDS]
    chosen = rank(bare_name(written), pool, lambda target: (bare_name(target.label),),
                  lambda target: target.feasible)
    return tuple(fill_in(target, verb, way, written_to) for target in chosen)


def resolve_start(book: NameBook, request: Any) -> StartOutcome:
    """@brief start_activity 逐项解析对象与（动词，方式），并归一来源、去向与 answering：名称类失败逐项收集（全部报出）；
    都成立时给出逐项的绑定行（指名购买为占位行，由环境按名称解析商品）与受理时的归一记录和说明。

    :param request: 已通过请求模型校验的 start_activity 请求（targets、actions、ways、from_、to、answering）。
    """
    count = len(request.targets)
    if not 1 <= count <= MAX_START_ITEMS or len(request.actions) != count or len(request.ways) != count:
        return StartOutcome(None)
    items = [_resolve_item(book, request, index) for index in range(count)]
    labels = tuple(item.row["object"] if item.row is not None else request.targets[index]
                   for index, item in enumerate(items))
    ways = tuple(item.row["selected_action"]["label"] if item.row is not None else request.ways[index]
                 for index, item in enumerate(items))
    source, destination, notes, routed = _route_fields(book, request, labels)
    answering, answered = _answering(book, request.answering)
    normalized = StartRequest(labels, tuple(request.actions), ways, source, destination, answering)
    failures = tuple(item.failure for item in items if item.failure is not None)
    if failures:
        return StartOutcome(normalized, failures=failures)
    resolved = (*(record for item in items for record in item.resolved), *routed, *answered)
    return StartOutcome(normalized, tuple(item.row for item in items), (), resolved, notes)


def _resolve_item(book: NameBook, request: Any, index: int) -> _Item:
    """@brief 一项：对象唯一时再核对（动词，方式）；找不到而方式是到店、远程下单或「购买」时是指名购买（占位行）；其余为
    名称类失败。"""
    written, verb, way = request.targets[index], request.actions[index], request.ways[index]
    matches = lookup(written, book.targets, _hint(verb, way), book.home)
    if len(matches) == 1:
        return _chosen(matches[0], request, index)
    if matches:
        return _Item(None, _ambiguous(matches, request, index))
    purchase = way_options(way) & NAMED_PURCHASE_WAYS
    if named_kind(verb, way) == "supply" and len(purchase) == 1:
        chosen = next(iter(purchase))
        return _Item(named_supply_row(written, chosen), resolved=_record(way, chosen))
    return _Item(None, _not_found(book, request, index))


def _chosen(target: Target, request: Any, index: int) -> _Item:
    """@brief 对象已唯一：动词与方式相配时选中该动作（名称或方式经归一的记一条）；商品目标写「购买」（或它的同义写法）时
    是由系统定方式的占位行（docs/dev/SPEC-dayrun-fidelity.md 3.5：对任何商品目标都受理，工具层不列出它）；否则
    WAY_NOT_OFFERED。"""
    written, verb, way = request.targets[index], request.actions[index], request.ways[index]
    chosen = pick_way(target, verb, way)
    if chosen is None and target.kind == "supply" and verb == "obtain" and SYSTEM_PURCHASE_WAY in way_options(way):
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
    """@brief 该项所写的 to（数组不等长时为 None，候选照填时 obtain 缺省送给自己）。"""
    return request.to[index] if index < len(request.to) else None


def _not_found(book: NameBook, request: Any, index: int) -> Failure:
    """@brief NAME_NOT_FOUND：所写的名称不是任何可以操作的对象（方式也不是方式时一并说明），候选按相似度给出。"""
    written, verb, way = request.targets[index], request.actions[index], request.ways[index]
    candidates = near_candidates(book, written, verb, way, _to(request, index))
    sentence = f"{numbered(index, len(request.targets))}「{written}」不是可以操作的对象"
    if not way_options(way):
        sentence += f"，「{way}」也不是方式"
    return Failure(ErrorItem(index, "targets", written, "NAME_NOT_FOUND", candidates), sentence,
                   f"start_activity target is not a visible interactive object (targets[{index}])")


def _ambiguous(matches: Sequence[Target], request: Any, index: int) -> Failure:
    """@brief NAME_AMBIGUOUS：所写的名称归一后对应不止一个对象，候选是这些对象的完整名称（至多 3 个）。"""
    written, verb, way = request.targets[index], request.actions[index], request.ways[index]
    shown = matches[:MAX_CANDIDATES]
    candidates = tuple(fill_in(target, verb, way, _to(request, index)) for target in shown)
    names = "、".join(f"「{target.label}」" for target in shown) + ("等" if len(matches) > len(shown) else "")
    sentence = f"{numbered(index, len(request.targets))}「{written}」对应不止一个对象：{names}"
    return Failure(ErrorItem(index, "targets", written, "NAME_AMBIGUOUS", candidates), sentence,
                   f"start_activity target matches several objects (targets[{index}])")


def _way_failure(target: Target, request: Any, index: int) -> Failure:
    """@brief WAY_NOT_OFFERED：对象没有列出所写的（动词，方式），候选为它列出的（动词，方式）写成的可照填请求；所写的方式
    在对象上相配而动词不对时明细的参数名为 actions，否则为 ways。"""
    verb, way = request.actions[index], request.ways[index]
    listed = {offered for _, offered in target.offers}
    field, written = ("actions", verb) if len(way_options(way) & listed) == 1 else ("ways", way)
    pairs = tuple(dict.fromkeys(target.offers))[:MAX_CANDIDATES]
    candidates = tuple(fill_in(target, *pair, _to(request, index)) for pair in pairs)
    sentence = (f"{numbered(index, len(request.targets))}「{target.label}」没有列出"
                f"「{verb}{VERB_WAY_SEPARATOR}{way}」这种做法")
    return Failure(ErrorItem(index, field, written, "WAY_NOT_OFFERED", candidates), sentence,
                   f"start_activity verb and way are not offered by the chosen target (targets[{index}])")


def _route_fields(book: NameBook, request: Any, labels: Sequence[str]
                  ) -> tuple[tuple, tuple, tuple[str, ...], tuple[dict, ...]]:
    """@brief 逐项归一来源与去向：冗余的 from、to 换成 null 并写一句说明，obtain 与 consume 的 to 写的人物名按人物名称解析；
    from、to 与对象不等长时原样保留（由 routing 报出）。

    :returns: （from，to，说明，归一记录）。
    """
    count = len(request.targets)
    if len(request.from_) != count or len(request.to) != count:
        return tuple(request.from_), tuple(request.to), (), ()
    sources, destinations, notes, records = [], [], [], []
    for index in range(count):
        verb, lead = request.actions[index], f"{numbered(index, count)}的 " if count > 1 else ""
        source, note = _redundant_source(book, verb, labels[index], request.from_[index])
        destination, other = _redundant_destination(book, verb, labels[index], request.to[index])
        destination, record = _person(book, verb, destination)
        sources.append(source)
        destinations.append(destination)
        notes.extend(f"{lead}{text}" for text in (note, other) if text)
        records.extend(record)
    return tuple(sources), tuple(destinations), tuple(notes), tuple(records)


def _redundant_source(book: NameBook, verb: str, label: str, source: str | None) -> tuple[str | None, str | None]:
    """@brief 冗余的 from：出行、交互与睡觉写成当前所在地点（出发地、做这件事的地方），取得写成目标写明的门店；冗余时
    换成 null 并给一句说明，否则原样（其余写法由 routing 判定）。"""
    if source is None:
        return None, None
    if verb == "obtain" and names_store(label, source):
        return None, f"from「{source}」就是目标写明的门店，可以写 null"
    if verb in _NULL_ROUTED_VERBS and _same_place(book, source, book.here):
        return None, f"from「{source}」就是现在所在的地点，可以写 null"
    return source, None


def _redundant_destination(book: NameBook, verb: str, label: str,
                           destination: str | None) -> tuple[str | None, str | None]:
    """@brief 冗余的 to：出行写成目的地（目标本身），交互与睡觉写成目标本身或当前所在地点；冗余时换成 null 并给一句说明。"""
    if destination is None or verb not in _NULL_ROUTED_VERBS:
        return destination, None
    if _same_place(book, destination, label):
        return None, f"to「{destination}」就是目标本身，可以写 null"
    if verb != "travel" and _same_place(book, destination, book.here):
        return None, f"to「{destination}」就是现在所在的地点，可以写 null"
    return destination, None


def channel_key(name: str) -> str:
    """@brief 渠道称呼（App 名）的比较写法（docs/dev/SPEC-dayrun-fidelity.md 3.3 解析规则 1）：去掉全部空白、不分大小写，
    其余逐字比较（不做全半角与括号归一）。"""
    return "".join(name.split()).casefold()


def names_store(label: str, store: str) -> bool:
    """@brief 所写的门店名是否就是标签「<门店>出售的<商品>」写明的门店（按归一写法比较，也认去掉「地点：」前缀的写法）。"""
    folded = fold(label)
    return any(folded.startswith(f"{key}{_SOLD_BY}") for key in (fold(store), bare_name(store)) if key)


def written_place(written: str, places: Sequence[str], home: str | None = None) -> str | None:
    """@brief 所写的地点名唯一对应的地点显示名：精确，再按归一写法与去掉「地点：」前缀逐级比对（resolve_name，第一级
    有匹配就停）；「家」「住处」指本人住处 home。对应不上或不止一个时为 None。

    :param places: 场景地点的显示名（冻结地点表）。
    """
    if bare_name(written) in HOME_WORDS and home is not None:
        return home
    return resolve_name(written, places).name


def _same_place(book: NameBook, written: str, anchor: str | None) -> bool:
    """@brief 所写的名称是否就是 anchor（按去掉前缀后的归一写法比较；「家」「住处」按本人住处比较）。"""
    if anchor is None:
        return False
    key = bare_name(written)
    if key in HOME_WORDS and book.home is not None:
        return book.home == anchor
    return key == bare_name(anchor)


def _person(book: NameBook, verb: str, destination: str | None) -> tuple[str | None, tuple[dict, ...]]:
    """@brief obtain 与 consume 的 to 写的人物名：按人物名称解析，唯一时换成它（记一条归一记录），否则原样交给 routing
    （不是任何人物时环境以 RECIPIENT_NOT_FOUND 拒绝）。"""
    if destination is None or verb not in {"obtain", "consume"} or destination in book.people:
        return destination, ()
    match = resolve_name(destination, book.people)
    return (match.name, _record(destination, match.name)) if match.name is not None else (destination, ())


def _answering(book: NameBook, answering: str | None) -> tuple[str | None, tuple[dict, ...]]:
    """@brief answering 写的消息名：按可见直接消息的名称解析，唯一时换成它（记一条归一记录），否则原样交给 routing。"""
    if answering is None or answering in book.messages:
        return answering, ()
    match = resolve_name(answering, book.messages)
    return (match.name, _record(answering, match.name)) if match.name is not None else (answering, ())
