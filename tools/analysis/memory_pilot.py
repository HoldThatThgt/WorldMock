"""@brief 分层记忆试点指标：从一份经当前代码认证的单人导出确定性计算 M1–M12、检查项与回顾对照表。

口径的唯一说明在 docs/design/07-evaluation.md「分层记忆试点指标」。load_export 先调用 validate_export 认证导出，认证
通过后只读 manifest.json、events.jsonl、purchases.jsonl、quality.json 与 assets/<sha256>；不读运行目录与人物工作区，
除命令行 --output 指定的报告外不写任何文件，同一导出两次计算逐字节相同。本模块只导入标准库、tools.analysis 的两个模块、
validate_export、WorldMockError 与 worldmock.state 的共享取值，不加载 worldmock.simulation_engine、worldmock.agents、
worldmock.runtime 与 custom 下的任何模块，所以 M11 在结构上独立于运行时守卫。
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
import sys
import unicodedata
from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from tools.analysis.behavior.metrics import digest_identity_residue, digest_repetition
from tools.analysis.errors import EvaluationError
from worldmock.foundation.errors import WorldMockError
from worldmock.recording_engine.validation.validation import validate_export
from worldmock.state.interaction.control_items import CONTROL_TOOLS
from worldmock.state.model.exchange import MEMORY_DIGEST_PURPOSE
from worldmock.state.model.memory import MEMORY_CATEGORIES, MEMORY_DIGEST_KIND, MEMORY_DIGEST_TIERS
from worldmock.state.world.kinds import ActivityStatus

#: 试点报告的结构标识。
SCHEMA_VERSION = "memory-pilot-1"
#: 餐次时段（本地时刻，左闭右开），与人物无关。
MEAL_PERIODS = {"早饭": ("04:00", "10:30"), "午饭": ("10:30", "14:30"), "晚饭": ("16:30", "22:00")}
#: 八字回声的窗口长度。
ECHO_LENGTH = 8
#: 每天决策数的基线与允许的相对带宽。
DECISION_BASELINE_PER_DAY = 81
DECISION_BAND = 0.15
#: 拒绝上限：基线 6 次出自 5 天的运行，上限是它的 2 倍，按运行日数折算。
REJECTION_BASELINE = 6
REJECTION_BASELINE_DAYS = 5
REJECTION_FACTOR = 2
#: 回合数达到这个数的决策逐个列出。
SIX_TURNS = 6
#: 记忆层 token/字比的参照值与区间（研究目录 out3/tokens）。
TOKEN_RATIO_REFERENCE = 0.638
TOKEN_RATIO_RANGE = (0.630, 0.645)
#: 拟合 token/字比所需的最少差分对数。
MIN_TOKEN_PAIRS = 12
#: 回顾事件的类别键到中文类别（回顾事件的形状，不是判定规则）。
DIGEST_KEYS = {"food": "饮食", "work": "工作", "life": "生活"}

_LOGGER = logging.getLogger(__name__)
#: 本工具自己查出的输入问题的错误码。
_INPUT = "memory_pilot_input"
#: 必须登记在 environment_assets 里的冻结目录文件，次序即拒绝时的核对次序。
_CATALOG_FILES = ("catalog/merchant-catalog.jsonl", "catalog/content-catalog.jsonl", "catalog/app-catalog.jsonl",
                  "catalog/location.jsonl")
#: 登记了才读的回调目录。
_CALLBACKS = "catalog/callbacks.jsonl"
#: 食品商品的类别：与运行时类别推导的 FOOD_GOODS_CATEGORIES 同义，按 M11 的独立性要求在本模块另写、不导入运行时。
_FOOD_CATEGORIES = frozenset({"drink", "meal", "snack"})
#: 活动的四种终态：生命周期状态里除进行中与暂停以外的。
_TERMINAL = frozenset(ActivityStatus) - {ActivityStatus.ACTIVE, ActivityStatus.PAUSED}
#: 带活动终态快照、可作活动结束事件的三种事件（运行时观察求结束事件读的也是这三种）。
_ENDING_TYPES = frozenset({"activity_progress", "environment_event", "action_result"})
#: 决策的世界请求得到的三种结果事件。
_OUTCOME_TYPES = frozenset({"action_accepted", "action_result", "action_rejected"})
#: 之前没有重置时所用食材包的写法。
_HOME_FOOD = "家里原有的食材"
#: 记忆检索工具名与检索上限的错误码。
_SEARCH_TOOL = "search_memory"
_SEARCH_LIMIT = "MEMORY_SEARCH_LIMIT"
#: 检索结果的「提示」里表示碰到字数上限的两段文字。
_CAPPED_HINTS = ("范围太大", "加关键词缩小范围")
#: 规范化时删去的字符。
_NORM_DROP = re.compile(r"[\s\W_的了]")
#: 回合请求 call_id 里的回合号。
_TURN = re.compile(r":model:(\d+):")
#: 月、年回顾正文里的次数写法。
_COUNT = re.compile(r"\d+ ?次")
#: 回顾请求的 call_id。
_DIGEST_CALL = re.compile(r"memory-digest:[^:]+:(" + "|".join(MEMORY_DIGEST_TIERS) + r"):([^:]+):attempt:(\d+)")
#: 自述里看法项的前缀。
_VIEW_PREFIX = "看法·"
#: 回顾响应级的四句错误与截断错误（第 04 章「输出校验」）。
_RESPONSE_ERRORS = frozenset({
    "response must contain exactly one choice", "response must contain exactly one submit_memory_digest tool call",
    "the only tool call must be submit_memory_digest", "submit_memory_digest arguments must be a JSON string"})
_TRUNCATED = "memory digest output was truncated at max_tokens"
#: 两条空值检查与守卫表四行（正文非空、正文长度、依据非空、依据归属）的问题文字（原文列在
#: docs/design/07-evaluation.md 的 M10），次序即逐条匹配的次序。第四批裁定撤销的守卫没有模板，它们的文字计入 unrecognized。
_GUARD_TEMPLATES = (
    ("text_without_input", "digests.{key} must be null: the input has no {key} records"),
    ("null_with_input", "digests.{key} must not be null: the input has {key} records"),
    ("text_empty", "digests.{key}.text must not be empty"),
    ("text_length", "digests.{key}.text has {len(text)} characters; the {tier} digest limit is {limit}"),
    ("refs_empty", "digests.{key}.refs must cite at least one record number"),
    ("refs_category", "digests.{key}.refs: {ref} is not a {key} record number"),
)
#: token/字比拟合的六类字符。
_CHARACTER_CLASSES = ("han", "ascii_letter", "ascii_digit", "space", "ascii_other", "other")
#: 检查项：名称、报告路径、比较与阈值（字符串阈值是报告里的另一条路径）。M10 统计行照抄、M11 ① 的覆盖与 ② 的做饭错配只报告，
#: 不在这里（② 按「做饭」所在的整句计数，会把写对的日记也算成错配）。
_CHECKS = (
    ("cooking_lines_name_the_kit", "M2.named.value", "==", 1.0),
    ("yesterday_food_cell_visible", "M3.periods.all.value", "==", 1.0),
    ("search_validation_errors", "M5.validation_errors.value", "<=", 0.05),
    ("search_non_empty", "M5.non_empty.value", ">=", 0.70),
    ("search_is_read_only", "M5.non_model_events_between", "==", 0),
    ("memory_layer_budget", "M6.memory_layer_by_observation.max", "<=", 14000),
    ("progress_layer_median", "M6.layers.进度层.median", "<=", 3000),
    ("progress_layer_p90", "M6.layers.进度层.p90", "<=", 7000),
    ("user_message_median", "M6.layers.user.median", "<=", 22000),
    ("persona_layer_stable", "M6.persona_layer_identical.value", "==", 1.0),
    ("fresh_turn_cache_hit", "M7.fresh_decision_turn.value", ">=", 0.55),
    ("cost_per_day", "M8.per_day_usd", "<=", 0.22),
    ("decisions_per_day_band", "M9.days_outside_band", "==", 0),
    ("rejections", "M9.rejections", "<=", "M9.rejection_limit"),
    ("turns_exhausted", "M9.turns.exhausted", "==", 0),
    ("run_failures", "M9.run_failures", "==", 0),
    ("capacity_compressions", "M9.capacity_compressions", "==", 0),
    ("no_restarted_activities", "M9.restarted", "==", 0),
    ("chain_append_only", "M9.chain.append_violations", "==", 0),
    ("chain_without_persona_delta", "M9.chain.persona_deltas", "==", 0),
    ("digest_fallback_share", "M10.fallback.value", "<=", 0.10),
    ("one_day_digest_per_day", "M10.day_digest_problems", "==", 0),
    ("retired_calls", "M10.retired_calls", "==", 0),
    ("month_digests", "M10.periods.month", "==", "M10.expected.month"),
)
#: 检查值取列表长度的两条路径。
_LENGTH_PATHS = frozenset({"M9.days_outside_band", "M9.restarted"})


@dataclass(frozen=True)
class PilotExport:
    """@brief 一份认证导出里试点指标要读的全部事实（只读）。"""

    run_id: str
    agent_id: int
    zone: ZoneInfo
    days: tuple[date, ...]
    events: tuple[dict, ...]
    purchases: tuple[dict, ...]
    quality: dict
    manifest: dict
    goods: Mapping[str, dict]
    contents: Mapping[str, dict]
    food_states: frozenset[str]
    kits: frozenset[str]
    kit_states: frozenset[str]
    lexicon: tuple[str, ...]


@dataclass(frozen=True)
class Decision:
    """@brief 一次决策的事件分组。"""

    decision_id: str
    occurred_at: int
    day: date
    observation: dict | None
    requests: tuple[dict, ...]
    responses: tuple[dict, ...]
    actions: tuple[dict, ...]
    outcomes: tuple[dict, ...]


@dataclass(frozen=True)
class _Search:
    """@brief 一次 search_memory 调用：所在决策、响应事件、参数，以及有没有回答与回答解析出的值。"""

    decision: Decision
    response: dict
    arguments: dict | None
    answered: bool
    answer: Any


@dataclass(frozen=True)
class _Cooking:
    """@brief 一次凭食材包状态的做饭：活动、所用食材包（家里原有的食材时为 None）、收到时刻与活动的结束事件。"""

    activity_id: str
    name: str
    kit: str | None
    received_at: int | None
    ended: dict | None


def load_export(directory: Path) -> PilotExport:
    """@brief 先认证导出，再读试点指标要的五样：清单、事件、购买、质量报告与冻结目录素材。

    :param directory: worldmock export 的输出目录。
    :returns: 只读的 PilotExport。
    :raises IntegrityError: 认证失败时 validate_export 抛出的异常原样上抛。
    :raises EvaluationError: 导出不是单人运行，或冻结目录文件不在 environment_assets 里。
    """
    root = Path(directory)
    validate_export(root)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    frozen = manifest["frozen_run_manifest"]
    if len(frozen["agent_ids"]) != 1:
        raise _fail(f"memory_pilot reads single-person exports; the export has {len(frozen['agent_ids'])} agents")
    goods, contents, states = _frozen_catalog(root, frozen["environment_assets"])
    food_states = frozenset(state for state in states if any(
        row.get("consumes_state") == state and (row.get("completion_effects") or {}).get("food")
        for row in contents.values()))
    kit_states = frozenset(state for state in food_states if states[state].get("reset_on_goods"))
    kits = frozenset(item for state in kit_states for item in states[state]["reset_on_goods"])
    return PilotExport(
        run_id=manifest["run_id"], agent_id=int(frozen["agent_ids"][0]), zone=ZoneInfo(manifest["window"]["timezone"]),
        days=tuple(date.fromisoformat(day) for day in manifest["daily_coverage"]),
        events=tuple(sorted(_jsonl((root / "events.jsonl").read_bytes()), key=lambda event: event["event_seq"])),
        purchases=tuple(_jsonl((root / "purchases.jsonl").read_bytes())),
        quality=json.loads((root / "quality.json").read_text(encoding="utf-8")), manifest=manifest,
        goods=goods, contents=contents, food_states=food_states, kits=kits, kit_states=kit_states,
        lexicon=_lexicon(goods, contents, food_states, kits))


def _fail(message: str) -> EvaluationError:
    """@brief 本工具自己查出的输入问题（EvaluationError 构造时已写错误日志）。"""
    return EvaluationError(_INPUT, message)


def _jsonl(raw: bytes) -> list[dict]:
    """@brief 逐行解析 JSONL 为对象列表；按字节切行（与认证相同），字符串里的 U+2028 等字符不切开一行。"""
    return [json.loads(line) for line in raw.splitlines()]


def _frozen_catalog(root: Path, assets: Mapping[str, dict]) -> tuple[dict, dict, dict]:
    """@brief 按摘要读冻结目录：四个必需文件缺一即拒绝；返回商品、内容与回调状态（按身份），不经目录加载器。"""
    for name in _CATALOG_FILES:
        if name not in assets:
            raise _fail(f"Frozen environment asset {name} is missing from the export")

    def rows(name: str) -> list[dict]:
        """@brief 一个冻结目录文件的各行；未登记的可选文件为空。"""
        return _jsonl((root / "assets" / assets[name]["sha256"]).read_bytes()) if name in assets else []

    goods = {row["goods_id"]: row for row in rows(_CATALOG_FILES[0])}
    contents = {row["content_id"]: row for row in rows(_CATALOG_FILES[1])}
    states = {row["state"]: row for row in rows(_CALLBACKS) if "state" in row}
    return goods, contents, states


def _lexicon(goods: Mapping[str, dict], contents: Mapping[str, dict], food_states: frozenset[str],
             kits: frozenset[str]) -> tuple[str, ...]:
    """@brief 菜名词表：食品商品名、食材包名及其按「配」切出的菜名、食品内容摘要里「比如／例如」后的列举。

    :returns: 按长度降序、同长按字典序排列的去重词表。
    """
    names = [row["name"] for row in goods.values() if row.get("category") in _FOOD_CATEGORIES]
    for goods_id in kits:
        name = goods[goods_id]["name"]
        names.append(name)
        stem = name.removesuffix("一人份食材包") if name.endswith("一人份食材包") else name.removesuffix("食材包")
        names.extend(part for part in stem.split("配") if len(part) >= 2)
    for row in contents.values():
        if (row.get("completion_effects") or {}).get("food") or row.get("consumes_state") in food_states:
            names.extend(_summary_dishes(row.get("summary") or ""))
    return tuple(sorted(set(names), key=lambda name: (-len(name), name)))


def _summary_dishes(summary: str) -> list[str]:
    """@brief 摘要里第一个「比如」或「例如」之后、到第一个「，」「。」「；」「…」之前的列举，按「、」「和」「或」切开。"""
    starts = [summary.find(word) for word in ("比如", "例如") if word in summary]
    if not starts:
        return []
    listed = re.split(r"[，。；…]", summary[min(starts) + 2:], maxsplit=1)[0]
    return [part for part in re.split(r"[、和或]", listed) if len(part) >= 2]


def decisions(export: PilotExport) -> list[Decision]:
    """@brief 按 decision_started 的次序给出每次决策的事件分组。

    回合请求是本决策里 purpose 缺失或为 null 的 model_request，响应按 caused_by 找到；结果是 caused_by 含本决策某条
    世界请求的受理、结果与拒绝事件，按 event_seq 排列。
    """
    caused = _caused_index(export)
    own: dict[str, list[dict]] = {}
    for event in export.events:
        if event.get("decision_id"):
            own.setdefault(event["decision_id"], []).append(event)
    return [_decision(export, started, own.get(started["decision_id"], []), caused)
            for started in export.events if started["event_type"] == "decision_started"]


def _decision(export: PilotExport, started: dict, own: list[dict], caused: Mapping[str, list[dict]]) -> Decision:
    """@brief 一次决策的分组：观察、回合请求与响应、世界请求与它们的结果。"""
    requests = tuple(event for event in own
                     if event["event_type"] == "model_request" and event["data"].get("purpose") is None)
    responses = {event["event_seq"]: event for request in requests for event in caused.get(request["event_id"], ())
                 if event["event_type"] == "model_response"}
    actions = tuple(event for event in own if event["event_type"] == "action_requested")
    outcomes = {event["event_seq"]: event for action in actions for event in caused.get(action["event_id"], ())
                if event["event_type"] in _OUTCOME_TYPES}
    return Decision(
        decision_id=started["decision_id"], occurred_at=started["occurred_at"],
        day=_day(export, started["occurred_at"]),
        observation=next((event for event in own if event["event_type"] == "observation"), None),
        requests=requests, responses=tuple(responses[key] for key in sorted(responses)), actions=actions,
        outcomes=tuple(outcomes[key] for key in sorted(outcomes)))


def _caused_index(export: PilotExport) -> dict[str, list[dict]]:
    """@brief 事件身份 → caused_by 含它的事件（按 event_seq）。"""
    caused: dict[str, list[dict]] = {}
    for event in export.events:
        for cause in event.get("caused_by") or ():
            caused.setdefault(cause, []).append(event)
    return caused


def pilot_report(directory: Path) -> dict:
    """@brief 认证并读入一份导出，计算全部试点指标、检查项与回顾对照表。

    :param directory: worldmock export 的输出目录。
    :returns: 顶层键依次为 schema_version、run_id、window、agent_id、days、metrics、checks、digest_review 的报告。
    """
    export = load_export(directory)
    rows = decisions(export)
    metrics = {
        "M1": m1_past_reference(export, rows), "M2": m2_dinner_naming(export, rows),
        "M3": m3_past_visibility(export), "M4": m4_lunch_repeats(export), "M5": m5_searches(export, rows),
        "M6": m6_volume(export, rows), "M7": m7_cache(export), "M8": m8_cost(export),
        "M9": m9_guardrails(export, rows), "M10": m10_digests(export, rows), "M11": m11_digest_fidelity(export),
        "M12": m12_endorsed_repurchase(export, rows),
    }
    return {"schema_version": SCHEMA_VERSION, "run_id": export.run_id, "window": export.manifest["window"],
            "agent_id": export.agent_id, "days": [day.isoformat() for day in export.days], "metrics": metrics,
            "checks": pilot_checks(metrics), "digest_review": digest_review(export)}


# ---- 共用的口径 ----

def _day(export: PilotExport, epoch: int) -> date:
    """@brief 事件时刻在导出时区的本地日期。"""
    return datetime.fromtimestamp(epoch, export.zone).date()


def _time(export: PilotExport, epoch: int) -> str:
    """@brief 报告里的本地时刻 YYYY-MM-DD HH:MM。"""
    return datetime.fromtimestamp(epoch, export.zone).strftime("%Y-%m-%d %H:%M")


def _day_number(export: PilotExport, day: date) -> int:
    """@brief 运行日序号，第一个运行日为 1。"""
    return (day - export.days[0]).days + 1


def _meals_at(export: PilotExport, epoch: int) -> set[str]:
    """@brief 一个时刻落在哪些餐次时段里（本地时刻，左闭右开）。"""
    clock = datetime.fromtimestamp(epoch, export.zone).strftime("%H:%M")
    return {name for name, (start, end) in MEAL_PERIODS.items() if start <= clock < end}


def _ratio(numerator: int, denominator: int) -> dict:
    """@brief 比例 {numerator, denominator, value}，value 保留四位，分母为 0 时为 null。"""
    return {"numerator": numerator, "denominator": denominator,
            "value": round(numerator / denominator, 4) if denominator else None}


def _periods(export: PilotExport, rows: Sequence[tuple[date, bool]]) -> dict:
    """@brief 分段比例：全部、头三天与第 4 天以后各自累计分子分母；记录归哪天按运行日序号。"""
    counts = {"all": [0, 0], "days_1_3": [0, 0], "days_4_plus": [0, 0]}
    for day, hit in rows:
        for key in ("all", "days_1_3" if _day_number(export, day) <= 3 else "days_4_plus"):
            counts[key][0] += bool(hit)
            counts[key][1] += 1
    return {key: _ratio(*value) for key, value in counts.items()}


def _by_day(export: PilotExport, rows: Sequence[tuple[date, bool]]) -> dict:
    """@brief 每个运行日的比例（没有记录的日子分母为 0）。"""
    counts = {day: [0, 0] for day in export.days}
    for day, hit in rows:
        counts.setdefault(day, [0, 0])
        counts[day][0] += bool(hit)
        counts[day][1] += 1
    return {day.isoformat(): _ratio(*counts[day]) for day in sorted(counts)}


def _quantile(ordered: Sequence[float], q: float) -> float:
    """@brief 升序值的第 q 分位：下标 (n−1)·q 处的线性插值。"""
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _stats(values: Iterable[float]) -> dict:
    """@brief 统计量 {n, median, p90, max}，均保留一位小数；没有值时后三项为 null。"""
    ordered = sorted(values)
    if not ordered:
        return {"n": 0, "median": None, "p90": None, "max": None}
    return {"n": len(ordered), "median": round(float(_quantile(ordered, 0.5)), 1),
            "p90": round(float(_quantile(ordered, 0.9)), 1), "max": round(float(ordered[-1]), 1)}


def _norm(text: str) -> str:
    """@brief 规范化：NFKC 后删去空白、标点、下划线与「的」「了」。"""
    return _NORM_DROP.sub("", unicodedata.normalize("NFKC", text))


def _compact(value: Any) -> str:
    """@brief 紧凑 JSON（中文不转义，键序不变）。"""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _sorted_compact(value: Any) -> str:
    """@brief 键按字典序的紧凑 JSON：导出的事件是键已排序的规范 JSON，请求里的 tool 消息保持渲染键序，比较前统一键序。"""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _parse(text: Any) -> Any:
    """@brief 把字符串解析成 JSON 值；不是字符串或解析不了时为 None。"""
    try:
        return json.loads(text) if isinstance(text, str) else None
    except ValueError:
        return None


def _windows(text: str) -> set[str]:
    """@brief 文本的全部连续 ECHO_LENGTH 字窗口。"""
    return {text[index:index + ECHO_LENGTH] for index in range(len(text) - ECHO_LENGTH + 1)}


def _lcs(left: str, right: str) -> int:
    """@brief 最长公共子串的长度。"""
    best, previous = 0, [0] * (len(right) + 1)
    for char in left:
        current = [0] * (len(right) + 1)
        for index, other in enumerate(right, start=1):
            if char == other:
                current[index] = previous[index - 1] + 1
                best = max(best, current[index])
        previous = current
    return best


def _sentences(text: str) -> list[str]:
    """@brief 按「。」「！」「？」切句，每段连同结尾的标点算一句，最后不带标点的一段也算一句。"""
    return re.findall(r"[^。！？]+[。！？]?", text)


def _scan(text: str, names: Iterable[str]) -> list[str]:
    """@brief 从左到右认名称：每个位置取以它开头的最长一项，取到就跳过这一项的长度，取不到前进一字。"""
    heads: dict[str, list[str]] = {}
    for name in sorted({name for name in names if name}, key=lambda name: (-len(name), name)):
        heads.setdefault(name[0], []).append(name)
    found, index = [], 0
    while index < len(text):
        match = next((name for name in heads.get(text[index], ()) if text.startswith(name, index)), None)
        if match is not None:
            found.append(match)
        index += len(match) if match is not None else 1
    return found


def _strings(value: Any) -> list[str]:
    """@brief 一个 JSON 值里的全部字符串值（按出现次序，不含键名）。"""
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [text for item in value.values() for text in _strings(item)]
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    return []


def _date_forms(day: str) -> list[str]:
    """@brief 一个日期的三种写法：YYYY-MM-DD、M月D日（不补零）、MM-DD。"""
    moment = date.fromisoformat(day)
    return [day, f"{moment.month}月{moment.day}日", day[5:]]


def _is_food_goods(export: PilotExport, goods_id: str | None) -> bool:
    """@brief 商品是食品商品（meal、snack、drink）。"""
    return goods_id in export.goods and export.goods[goods_id].get("category") in _FOOD_CATEGORIES


def _goods_name(export: PilotExport, goods_id: str) -> str:
    """@brief 商品在冻结目录里的名称。"""
    return export.goods[goods_id]["name"]


# ---- 决策里的文字 ----

def _messages(request: dict) -> list[dict]:
    """@brief 一条模型请求的消息（只取对象）。"""
    body = request["data"].get("request")
    messages = body.get("messages") if isinstance(body, Mapping) else None
    return [item for item in messages if isinstance(item, Mapping)] if isinstance(messages, list) else []


def _role_content(request: dict, role: str) -> str:
    """@brief 请求里第一条给定角色消息的字符串内容；没有时为空串。"""
    message = next((item for item in _messages(request) if item.get("role") == role), None)
    return message["content"] if message is not None and isinstance(message.get("content"), str) else ""


def _response_message(response: dict) -> dict | None:
    """@brief 一条模型响应的 choices[0].message；形状不符时为 None。"""
    body = response["data"].get("response")
    choices = body.get("choices") if isinstance(body, Mapping) else None
    first = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], Mapping) else None
    message = first.get("message") if first is not None else None
    return message if isinstance(message, Mapping) else None


def _tool_calls(message: Mapping | None) -> list[Mapping]:
    """@brief 消息里的工具调用（只取对象）。"""
    calls = message.get("tool_calls") if message is not None else None
    return [call for call in calls if isinstance(call, Mapping)] if isinstance(calls, list) else []


def _call_arguments(call: Mapping) -> Any:
    """@brief 工具调用的参数原文；没有时为 None。"""
    function = call.get("function")
    return function.get("arguments") if isinstance(function, Mapping) else None


def _argument_texts(call: Mapping) -> list[str]:
    """@brief 模型文字里一个工具调用的部分：参数能解析成对象时 items 列表每项去掉 views 后各自紧凑序列化，其余键组成
    的对象紧凑序列化；解析不了时用原文。"""
    raw = _call_arguments(call)
    value = _parse(raw)
    if not isinstance(value, Mapping):
        return [raw] if isinstance(raw, str) else []
    items = value.get("items")
    if not isinstance(items, list):
        return [_compact(value)]
    texts = [_compact({name: part for name, part in entry.items() if name != "views"}
                      if isinstance(entry, Mapping) else entry) for entry in items]
    return [*texts, _compact({key: item for key, item in value.items() if key != "items"})]


def _model_text(row: Decision) -> str:
    """@brief 模型文字 T_d：各回合响应的 reasoning_content、content 与工具参数，按响应次序以换行连接。"""
    parts = []
    for response in row.responses:
        message = _response_message(response)
        parts.extend(message[key] for key in ("reasoning_content", "content")
                     if message is not None and isinstance(message.get(key), str))
        for call in _tool_calls(message):
            parts.extend(_argument_texts(call))
    return "\n".join(parts)


def _request_text(row: Decision) -> str:
    """@brief 请求文字 R_d：各回合请求全部消息的 content、reasoning_content 与工具参数，以换行连接。"""
    parts = []
    for request in row.requests:
        for message in _messages(request):
            parts.extend(message[key] for key in ("content", "reasoning_content") if isinstance(message.get(key), str))
            parts.extend(raw for raw in map(_call_arguments, _tool_calls(message)) if isinstance(raw, str))
    return "\n".join(parts)


def _reasoning_text(row: Decision) -> str:
    """@brief 思考文字 RS_d：各回合响应的 reasoning_content，以换行连接。"""
    messages = [_response_message(response) for response in row.responses]
    return "\n".join(message["reasoning_content"] for message in messages
                     if message is not None and isinstance(message.get("reasoning_content"), str))


def _summary_text(row: Decision) -> str:
    """@brief 总结文字 S_d：收尾、暂停与播报请求的 items[].summary，以换行连接。"""
    return "\n".join(item["summary"] for action in row.actions if action["data"].get("tool_name") in CONTROL_TOOLS
                     for item in _request_items(action) if isinstance(item.get("summary"), str))


def _request_items(action: dict) -> list[Mapping]:
    """@brief 世界请求参数里的 items（只取对象）。"""
    request = action["data"].get("request")
    items = request.get("items") if isinstance(request, Mapping) else None
    return [item for item in items if isinstance(item, Mapping)] if isinstance(items, list) else []


def _first_result(row: Decision, action: dict) -> dict | None:
    """@brief 一条世界请求的结果：caused_by 含它的 action_result 中 event_seq 最小的一条。"""
    return next((event for event in row.outcomes if event["event_type"] == "action_result"
                 and action["event_id"] in event.get("caused_by", ())), None)


def _completed(row: Decision, action: dict) -> dict | None:
    """@brief 结果为 completed 时返回这条结果，否则为 None。"""
    result = _first_result(row, action)
    return result if result is not None and result["data"].get("status") == "completed" else None


def _accepted(row: Decision) -> list[dict]:
    """@brief 本决策受理的活动快照（action_accepted 的 data.activity）。"""
    return [event["data"]["activity"] for event in row.outcomes
            if event["event_type"] == "action_accepted" and isinstance(event["data"].get("activity"), Mapping)]


def _memory_layer(event: dict | None) -> Mapping:
    """@brief 观察里的记忆层；没有时为空对象。"""
    layer = event["data"].get("memory_layer") if event is not None else None
    return layer if isinstance(layer, Mapping) else {}


def _today(event: dict | None) -> list:
    """@brief 观察里记忆层的「今天」列表。"""
    today = _memory_layer(event).get("今天")
    return today if isinstance(today, list) else []


def _user_layers(request: dict) -> list[Mapping]:
    """@brief 请求首个 user 消息解析出的层数组（只取带层名的对象）。"""
    layers = _parse(_role_content(request, "user"))
    return [layer for layer in layers if isinstance(layer, Mapping) and isinstance(layer.get("layer"), str)] \
        if isinstance(layers, list) else []


def _named_layer(request: dict, name: str) -> Mapping | None:
    """@brief 按层名取请求首个 user 消息里的一层。"""
    return next((layer for layer in _user_layers(request) if layer["layer"] == name), None)


def _fresh_requests(rows: Sequence[Decision]) -> list[dict]:
    """@brief 新快照决策的首个请求（消息恰为两条），按 event_seq 排列。"""
    return sorted((row.requests[0] for row in rows if row.requests and len(_messages(row.requests[0])) == 2),
                  key=lambda request: request["event_seq"])


def _commit_ends(export: PilotExport) -> dict[str, int]:
    """@brief 每次决策的提交末序号 C_j：从它最早的 action_result 起，前后版本都相同的连续事件里最大的序号。"""
    events, first = export.events, {}
    for index, event in enumerate(events):
        if event["event_type"] == "action_result" and event.get("decision_id"):
            first.setdefault(event["decision_id"], index)
    ends = {}
    for decision_id, index in first.items():
        versions = (events[index]["state_version_before"], events[index]["state_version_after"])
        end = index
        while end + 1 < len(events) and (events[end + 1]["state_version_before"],
                                         events[end + 1]["state_version_after"]) == versions:
            end += 1
        ends[decision_id] = events[end]["event_seq"]
    return ends


def _order_goods(export: PilotExport) -> dict[str, str]:
    """@brief 订单身份 → 商品身份（purchases.jsonl 的购买行）。"""
    return {row["order_id"]: row["goods_id"] for row in export.purchases if row.get("fact_kind") == "purchase"}


def _purchase_rows(export: PilotExport) -> list[dict]:
    """@brief purchases.jsonl 里 fact_kind 为 purchase 的行。"""
    return [row for row in export.purchases if row.get("fact_kind") == "purchase"]


def _observations(export: PilotExport) -> list[dict]:
    """@brief 全部观察事件（按 event_seq）。"""
    return [event for event in export.events if event["event_type"] == "observation"]


def _environment(export: PilotExport, kind: str) -> list[dict]:
    """@brief data.kind 为 kind 的环境事件（按 event_seq）。"""
    return [event for event in export.events
            if event["event_type"] == "environment_event" and event["data"].get("kind") == kind]


def _endings(export: PilotExport) -> dict[str, tuple[dict, Mapping]]:
    """@brief 活动身份 →（结束事件，终态快照）：带该活动终态快照的最早事件，与第 02 章「今天条目」放结束句的事件相同；
    activity_progress 与 environment_event（收尾、截断）带 data.activity，终态 action_result 另可带 data.data.activities。"""
    endings: dict[str, tuple[dict, Mapping]] = {}
    for event in export.events:
        if event["event_type"] not in _ENDING_TYPES:
            continue
        payload = event["data"].get("data")
        rows = payload.get("activities", []) if isinstance(payload, Mapping) else []
        for snapshot in (event["data"].get("activity"), *rows):
            if isinstance(snapshot, Mapping) and snapshot.get("status") in _TERMINAL:
                endings.setdefault(snapshot["activity_id"], (event, snapshot))
    return endings


def _cooking(export: PilotExport) -> list[_Cooking]:
    """@brief 做饭活动：凭 reset_on_goods 非空的食品状态的 consume_on_start；所用食材包是它之前同一状态最近的重置。"""
    by_id = {event["event_id"]: event for event in export.events}
    endings, names, resets, found = _endings(export), _activity_names(export), {}, []
    for event in _environment(export, "callback_effect"):
        data = event["data"]
        if data.get("type") == "reset_to_cap":
            resets[data["state"]] = event
        elif data.get("type") == "consume_on_start" and data.get("state") in export.kit_states:
            activity_id = by_id[event["caused_by"][0]]["activity_id"]
            reset = resets.get(data["state"])
            found.append(_Cooking(activity_id, names.get(activity_id, ""),
                                  reset["data"]["goods_id"] if reset is not None else None,
                                  reset["occurred_at"] if reset is not None else None,
                                  endings[activity_id][0] if activity_id in endings else None))
    return found


def _activity_names(export: PilotExport) -> dict[str, str]:
    """@brief 活动身份 → 最近一份活动快照里的名称。"""
    names = {}
    for event in export.events:
        activity = event["data"].get("activity")
        if isinstance(activity, Mapping) and isinstance(activity.get("activity_id"), str):
            names[activity["activity_id"]] = activity.get("name", "")
    return names


def _kit_name(export: PilotExport, cooking: _Cooking) -> str:
    """@brief 所用食材包的商品名，或「家里原有的食材」。"""
    return _goods_name(export, cooking.kit) if cooking.kit is not None else _HOME_FOOD


def _cooked_kits(export: PilotExport, cooking: Sequence[_Cooking], day: date) -> list[str]:
    """@brief 结束日期为 day 的做饭活动所用食材包名（家里原有的食材不计），按次序去重。"""
    return list(dict.fromkeys(_kit_name(export, item) for item in cooking if item.kit is not None
                              and item.ended is not None and _day(export, item.ended["occurred_at"]) == day))


def _self_reports(rows: Sequence[Decision]) -> dict[str, list[str]]:
    """@brief 活动的自述：结果为 completed 的收尾、暂停与播报，请求第 i 项的总结、看法与记忆归结果里第 i 项活动。"""
    reports: dict[str, list[str]] = {}
    for row in rows:
        for action in row.actions:
            result = _completed(row, action) if action["data"].get("tool_name") in CONTROL_TOOLS else None
            identities = (result["data"].get("data") or {}).get("activity_ids") or [] if result is not None else []
            for activity_id, item in zip(identities, _request_items(action)):
                texts = [item.get("summary"), *(entry.get("text") for entry in item.get("views") or []),
                         *(entry.get("text") for entry in item.get("memories") or [])]
                reports.setdefault(activity_id, []).extend(text for text in texts if isinstance(text, str))
    return reports


def _completed_views(export: PilotExport, rows: Sequence[Decision]) -> list[dict]:
    """@brief 已了结的看法：结果为 completed 的收尾请求里各 items[].views[]，日期是请求时刻的本地日期。"""
    views = []
    for row in rows:
        for action in row.actions:
            if action["data"].get("tool_name") != "conclude_activity" or _completed(row, action) is None:
                continue
            day = _day(export, action["occurred_at"])
            views.extend({"about": view.get("about", ""), "type": view.get("type"), "text": view.get("text", ""),
                          "day": day} for item in _request_items(action) for view in item.get("views") or []
                         if isinstance(view, Mapping))
    return views


# ---- 检索 ----

def _searches(rows: Sequence[Decision]) -> list[_Search]:
    """@brief 回合响应里的每次 search_memory 调用，回答是此后同一决策回合请求里第一条同调用身份的 tool 消息。"""
    found = []
    for row in rows:
        for response in row.responses:
            for call in _tool_calls(_response_message(response)):
                function = call.get("function")
                if not isinstance(function, Mapping) or function.get("name") != _SEARCH_TOOL:
                    continue
                answered, answer = _tool_answer(row, response["event_seq"], call.get("id"))
                arguments = _parse(function.get("arguments"))
                found.append(_Search(row, response, arguments if isinstance(arguments, dict) else None,
                                     answered, answer))
    return found


def _tool_answer(row: Decision, after: int, call_id: Any) -> tuple[bool, Any]:
    """@brief 响应之后同一决策回合请求里第一条 tool_call_id 等于调用身份的 tool 消息：（有没有回答，解析出的值）。"""
    for request in row.requests:
        if request["event_seq"] <= after:
            continue
        for message in _messages(request):
            if message.get("role") == "tool" and message.get("tool_call_id") == call_id:
                return True, _parse(message.get("content"))
    return False, None


def _search_status(search: _Search) -> str:
    """@brief 检索调用的归类：limit（第 4 次起被拒）、error（其余错误码或协议错误）、unanswered、success。"""
    code = search.answer.get("error_code") if isinstance(search.answer, Mapping) else None
    if code == _SEARCH_LIMIT:
        return "limit"
    if code is not None or search.response["data"].get("error"):
        return "error"
    return "success" if search.answered else "unanswered"


def _result_items(answer: Any) -> list:
    """@brief 检索结果项：「结果」为列表时取各项，为对象时按键序把各值的各项接起来。"""
    result = answer.get("结果") if isinstance(answer, Mapping) else None
    if isinstance(result, list):
        return list(result)
    if isinstance(result, Mapping):
        return [item for value in result.values() if isinstance(value, list) for item in value]
    return []


def _successful_items(rows: Sequence[Decision]) -> dict[str, list[Mapping]]:
    """@brief 决策身份 → 它全部成功检索的结果项（只取对象）。"""
    items: dict[str, list[Mapping]] = {}
    for search in _searches(rows):
        if _search_status(search) == "success":
            items.setdefault(search.decision.decision_id, []).extend(
                item for item in _result_items(search.answer) if isinstance(item, Mapping))
    return items


# ---- M1 ----

def m1_past_reference(export: PilotExport, rows: list[Decision]) -> dict:
    """@brief M1 往日引用：饮食决策中，思考或总结点到提示里往日回顾的菜名或日期的比例，按决策日期分段。

    :param export: 认证导出。
    :param rows: decisions(export)。
    :returns: {by_day, periods, decisions}。
    """
    orders, found = _order_goods(export), _successful_items(rows)
    entries, marks = [], []
    for row in rows:
        kinds = _food_kinds(export, row, orders)
        if kinds:
            entries.append(_past_reference(export, row, kinds, found.get(row.decision_id, [])))
            marks.append((row.day, entries[-1]["mentioned"]))
    return {"by_day": _by_day(export, marks), "periods": _periods(export, marks), "decisions": entries}


def _food_kinds(export: PilotExport, row: Decision, orders: Mapping[str, str]) -> list[str]:
    """@brief 饮食决策的种类：午饭、晚饭时段里的食品查询与购买，以及不限时段的做饭开始。"""
    meal = bool(_meals_at(export, row.occurred_at) & {"午饭", "晚饭"})
    kinds = set()
    for activity in _accepted(row):
        data = activity.get("data") if isinstance(activity.get("data"), Mapping) else {}
        if activity.get("kind") == "query" and meal and any(_food_target(export, item) for item in
                                                             data.get("targets") or []):
            kinds.add("query")
        if activity.get("kind") == "purchase" and meal and any(_food_or_kit(export, orders[order]) for order in
                                                               data.get("order_ids") or []):
            kinds.add("purchase")
        content = export.contents.get(data.get("content_id"), {})
        if activity.get("kind") == "interact" and not data.get("app_id") \
                and content.get("consumes_state") in export.kit_states:
            kinds.add("cooking")
    return [kind for kind in ("query", "purchase", "cooking") if kind in kinds]


def _food_target(export: PilotExport, item: Any) -> bool:
    """@brief 查询目标是食品类别，或是食品商品与食材包。"""
    return isinstance(item, Mapping) and (item.get("category") in _FOOD_CATEGORIES
                                          or _food_or_kit(export, item.get("goods_id")))


def _food_or_kit(export: PilotExport, goods_id: str | None) -> bool:
    """@brief 商品是食品商品或食材包。"""
    return _is_food_goods(export, goods_id) or goods_id in export.kits


def _past_reference(export: PilotExport, row: Decision, kinds: list[str], items: Sequence[Mapping]) -> dict:
    """@brief 一次饮食决策的往日名称、往日日期与是否被思考或总结点到。"""
    texts, dates = _past_texts(row, items)
    names = list(dict.fromkeys(name for text in texts for name in _scan(text, export.lexicon)))
    said = _reasoning_text(row) + "\n" + _summary_text(row)
    forms = [form for day in sorted(dates) for form in _date_forms(day)]
    return {"decision_id": row.decision_id, "time": _time(export, row.occurred_at), "kinds": kinds,
            "past_names": names, "past_dates": sorted(dates),
            "mentioned": any(name in said for name in names) or any(form in said for form in forms)}


def _past_texts(row: Decision, items: Sequence[Mapping]) -> tuple[list[str], set[str]]:
    """@brief 往日文字与往日日期：「近三天」各格的回顾，检索结果里往日项的回顾与「未整理」项条目里的全部字符串。"""
    texts, dates = [], set()
    recent = _memory_layer(row.observation).get("近三天")
    for cells in recent.values() if isinstance(recent, Mapping) else ():
        for cell in cells if isinstance(cells, list) else ():
            if isinstance(cell, Mapping) and isinstance(cell.get("回顾"), str) and cell["回顾"]:
                texts.append(cell["回顾"])
                dates.update([cell["日期"]] if isinstance(cell.get("日期"), str) else [])
    for item in items:
        if not {"日期", "月份", "年份"} & item.keys():
            continue
        found = [item["回顾"]] if isinstance(item.get("回顾"), str) else (
            _strings(item.get("条目")) if item.get("未整理") is True else [])
        texts.extend(found)
        if found and isinstance(item.get("日期"), str):
            dates.add(item["日期"])
    return texts, dates


# ---- M2 ----

def m2_dinner_naming(export: PilotExport, rows: list[Decision]) -> dict:
    """@brief M2 晚饭命名：用了食材包的做饭，后面的决策看得到的结束核验带食材包名与收到日的比例；自述菜名对不上的次数。

    :param export: 认证导出。
    :param rows: decisions(export)。
    :returns: {named, mismatches, activities}。
    """
    ends, reports = _commit_ends(export), _self_reports(rows)
    last_seen = {_day(export, event["occurred_at"]): event for event in _observations(export)}
    named, mismatches, activities = [], 0, []
    for item in _cooking(export):
        line = _line_named(export, rows, ends, last_seen, item)
        dishes = _mismatched_dishes(export, item, reports.get(item.activity_id, []))
        mismatches += bool(dishes)
        if line is not None:
            named.append(line)
        activities.append({
            "activity_id": item.activity_id, "name": item.name,
            "ended_at": _time(export, item.ended["occurred_at"]) if item.ended is not None else None,
            "kit": _kit_name(export, item),
            "received_on": _day(export, item.received_at).isoformat() if item.received_at is not None else None,
            "line_named": line, "mismatched_dishes": dishes})
    return {"named": _ratio(sum(named), len(named)), "mismatches": mismatches, "activities": activities}


def _line_named(export: PilotExport, rows: Sequence[Decision], ends: Mapping[str, int],
                last_seen: Mapping[date, dict], item: _Cooking) -> bool | None:
    """@brief 结束句写在当天第一个 C_k ≥ e 的决策 k 的条目里；k 之后当天还有决策才进分母，读当天最后一条观察的「今天」。

    :returns: 不进分母时为 None，否则为核验是否写出「，用M月D日收到的〈食材包名〉」。
    """
    if item.kit is None or item.ended is None:
        return None
    day = _day(export, item.ended["occurred_at"])
    same_day = [row for row in rows if row.day == day]
    index = next((place for place, row in enumerate(same_day)
                  if ends.get(row.decision_id, 0) >= item.ended["event_seq"]), None)
    if index is None or index == len(same_day) - 1:
        return None
    received = datetime.fromtimestamp(item.received_at, export.zone)
    phrase = f"，用{received.month}月{received.day}日收到的{_kit_name(export, item)}"
    return any(isinstance(line, str) and phrase in line for entry in _today(last_seen.get(day))
               if isinstance(entry, Mapping) for line in entry.get("核验") or [])


def _mismatched_dishes(export: PilotExport, item: _Cooking, reports: Sequence[str]) -> list[str]:
    """@brief 用了食材包的做饭，自述里认出的、不是所用食材包名子串的菜名（按次序去重）。"""
    if item.kit is None:
        return []
    kit = _kit_name(export, item)
    return list(dict.fromkeys(dish for text in reports for dish in _scan(text, export.lexicon) if dish not in kit))


# ---- M3、M4 ----

def m3_past_visibility(export: PilotExport) -> dict:
    """@brief M3 往日可见：前一天有饮食日回顾或饮食未整理时，第二个运行日起的观察里「近三天」饮食格写着那一天。

    :returns: {by_day, periods, unsorted}。
    """
    day_digests = _period_digests(export, "day")
    marks, unsorted = [], 0
    for event in _observations(export):
        day = _day(export, event["occurred_at"])
        previous = (day - timedelta(days=1)).isoformat()
        digest = day_digests.get(previous)
        if _day_number(export, day) < 2 or digest is None or not _recorded(digest, "food"):
            continue
        recent = _memory_layer(event).get("近三天")
        cells = recent.get("饮食") if isinstance(recent, Mapping) else None
        cell = next((cell for cell in cells if isinstance(cell, Mapping) and cell.get("日期") == previous), None) \
            if isinstance(cells, list) else None
        written = cell is not None and isinstance(cell.get("回顾"), str) and bool(cell["回顾"])
        unsettled = cell is not None and cell.get("未整理") is True
        unsorted += unsettled
        marks.append((day, written or unsettled))
    return {"by_day": _by_day(export, marks), "periods": _periods(export, marks), "unsorted": unsorted}


def m4_lunch_repeats(export: PilotExport) -> dict:
    """@brief M4 午饭重复：工作日午饭时段下单的 meal 份数里，同一商品此前三天内买过的份数。

    :returns: {workday_lunch_portions, repeated_portions, value, most_frequent, lunches}。
    """
    workdays = {day for day, event in _first_observations(export).items()
                if ((event["data"].get("environment_layer") or {}).get("duty") or {}).get("day") == "工作日"}
    bought: dict[date, set[str]] = {}
    for row in _purchase_rows(export):
        bought.setdefault(_day(export, row["created_at"]), set()).add(row["goods_id"])
    lunches, totals = [], {}
    for row in _purchase_rows(export):
        day = _day(export, row["created_at"])
        if row["category"] != "meal" or day not in workdays or "午饭" not in _meals_at(export, row["created_at"]):
            continue
        repeat = any(row["goods_id"] in bought.get(day - timedelta(days=back), ()) for back in (1, 2, 3))
        name = _goods_name(export, row["goods_id"])
        totals[name] = totals.get(name, 0) + row["quantity"]
        lunches.append({"time": _time(export, row["created_at"]), "name": name, "portions": row["quantity"],
                        "repeat": repeat})
    portions = sum(item["portions"] for item in lunches)
    repeated = sum(item["portions"] for item in lunches if item["repeat"])
    most = max(totals.items(), key=lambda item: item[1]) if totals else None
    return {"workday_lunch_portions": portions, "repeated_portions": repeated,
            "value": _ratio(repeated, portions)["value"],
            "most_frequent": {"name": most[0], "portions": most[1]} if most is not None else None,
            "lunches": lunches}


def _first_observations(export: PilotExport) -> dict[date, dict]:
    """@brief 每个本地日期的第一条观察。"""
    first: dict[date, dict] = {}
    for event in _observations(export):
        first.setdefault(_day(export, event["occurred_at"]), event)
    return first


# ---- M5 ----

def m5_searches(export: PilotExport, rows: list[Decision]) -> dict:
    """@brief M5 检索：调用次数、查「今天」的次数、非空比例、校验错误、两种上限、未回答与只读核对。

    :param export: 认证导出。
    :param rows: decisions(export)。
    :returns: 「分层记忆试点指标」M5 一节的十一个键。
    """
    searches = _searches(rows)
    statuses = [_search_status(item) for item in searches]
    succeeded = [item for item, status in zip(searches, statuses) if status == "success"]
    by_day = {day.isoformat(): {"calls": 0, "today_calls": 0} for day in export.days}
    codes: Counter[str] = Counter()
    for item in searches:
        today = str((item.arguments or {}).get("time", "")).startswith("今天")
        counts = by_day.setdefault(item.decision.day.isoformat(), {"calls": 0, "today_calls": 0})
        counts["calls"] += 1
        counts["today_calls"] += today
        if isinstance(item.answer, Mapping) and item.answer.get("error_code") is not None:
            codes[str(item.answer["error_code"])] += 1
    hints = [item.answer.get("提示", "") if isinstance(item.answer, Mapping) else "" for item in succeeded]
    return {
        "calls": len(searches), "today_calls": sum(day["today_calls"] for day in by_day.values()),
        "by_day": dict(sorted(by_day.items())), "succeeded": len(succeeded),
        "non_empty": _ratio(sum(bool(_result_items(item.answer)) for item in succeeded), len(succeeded)),
        "validation_errors": _ratio(statuses.count("error"), len(searches)),
        "error_codes": dict(sorted(codes.items())), "limit_refusals": statuses.count("limit"),
        "capped_results": sum(isinstance(hint, str) and any(text in hint for text in _CAPPED_HINTS)
                              for hint in hints),
        "unanswered": statuses.count("unanswered"),
        "non_model_events_between": _events_between(export, searches),
    }


def _events_between(export: PilotExport, searches: Sequence[_Search]) -> int:
    """@brief 每次检索所在响应与同一决策下一条回合请求之间，不是模型请求与响应的事件数之和。"""
    seqs = [event["event_seq"] for event in export.events]
    total = 0
    for search in searches:
        after = search.response["event_seq"]
        following = next((request["event_seq"] for request in search.decision.requests
                          if request["event_seq"] > after), None)
        if following is None:
            continue
        window = export.events[bisect_right(seqs, after):bisect_left(seqs, following)]
        total += sum(event["event_type"] not in {"model_request", "model_response"} for event in window)
    return total


# ---- M6 ----

def m6_volume(export: PilotExport, rows: list[Decision]) -> dict:
    """@brief M6 体量：新快照首个请求的各层大小、每条观察的记忆层大小、兜底次数、人设层稳定性与 token/字比。

    :param export: 认证导出。
    :param rows: decisions(export)。
    :returns: {layers, memory_layer_by_observation, fallback_rewrites, persona_layer_identical, token_ratio}。
    """
    fresh = _fresh_requests(rows)
    personas = [_compact(_named_layer(request, "人设层")) for request in fresh]
    return {
        "layers": _layer_sizes(fresh),
        "memory_layer_by_observation": _stats(len(_compact(event["data"].get("memory_layer")))
                                              for event in _observations(export)),
        "fallback_rewrites": _fallback_rewrites(export),
        "persona_layer_identical": _ratio(sum(layer == personas[0] for layer in personas), len(personas)),
        "token_ratio": _token_ratio(rows, fresh),
    }


def _layer_sizes(fresh: Sequence[dict]) -> dict:
    """@brief 按层名取层的紧凑序列化长度，另量记忆层三部分与 system、user、tools 三项，各出统计量。"""
    sizes: dict[str, list[int]] = {}
    for request in fresh:
        for layer in _user_layers(request):
            sizes.setdefault(layer["layer"], []).append(len(_compact(layer)))
            if layer["layer"] == "记忆层":
                for part in ("可查", "近三天", "今天"):
                    if part in layer:
                        sizes.setdefault(f"记忆层·{part}", []).append(len(_compact(layer[part])))
        body = request["data"].get("request") or {}
        for name, text in (("system", _role_content(request, "system")), ("user", _role_content(request, "user")),
                           ("tools", _compact(body.get("tools", [])))):
            sizes.setdefault(name, []).append(len(text))
    return {name: _stats(values) for name, values in sizes.items()}


def _fallback_rewrites(export: PilotExport) -> int:
    """@brief 兜底触发次数：「今天」不以同一本地日上一条观察的「今天」开头的观察数（当天第一条不比较）。"""
    previous: dict[date, list[str]] = {}
    count = 0
    for event in _observations(export):
        day = _day(export, event["occurred_at"])
        today = [_compact(entry) for entry in _today(event)]
        if day in previous and today[:len(previous[day])] != previous[day]:
            count += 1
        previous[day] = today
    return count


def _character_counts(text: str) -> list[int]:
    """@brief 六类字符的个数：汉字、ASCII 字母、ASCII 数字、空格、其余可打印 ASCII、其余字符。"""
    counts = [0] * len(_CHARACTER_CLASSES)
    for char in text:
        code = ord(char)
        if 0x3400 <= code <= 0x4DBF or 0x4E00 <= code <= 0x9FFF or 0xF900 <= code <= 0xFAFF:
            counts[0] += 1
        elif char.isascii() and char.isalpha():
            counts[1] += 1
        elif char.isascii() and char.isdigit():
            counts[2] += 1
        elif char == " ":
            counts[3] += 1
        elif 0x21 <= code <= 0x7E:
            counts[4] += 1
        else:
            counts[5] += 1
    return counts


def _token_ratio(rows: Sequence[Decision], fresh: Sequence[dict]) -> dict:
    """@brief 相邻新快照首个请求之差的无截距最小二乘，解出六类字符的 token 权重并折算记忆层的 token/字比。"""
    responses = {cause: response for row in rows for response in row.responses
                 for cause in response.get("caused_by") or ()}
    points = []
    for request in fresh:
        usage = ((responses.get(request["event_id"], {}).get("data", {}).get("response") or {}).get("usage") or {})
        text = _role_content(request, "system") + _role_content(request, "user") + _compact(
            (request["data"].get("request") or {}).get("tools", []))
        points.append((_character_counts(text), usage.get("prompt_tokens")))
    # 差分对只取相邻两条新快照；其中一条的响应没有整数 prompt_tokens 时这一对不计，不把隔开的两条接成一对。
    pairs = [([b - a for a, b in zip(left[0], right[0])], right[1] - left[1])
             for left, right in zip(points, points[1:]) if type(left[1]) is int and type(right[1]) is int]
    weights = _least_squares(pairs) if len(pairs) >= MIN_TOKEN_PAIRS else None
    memory = [0] * len(_CHARACTER_CLASSES)
    for request in fresh:
        layer = _named_layer(request, "记忆层")
        if layer is not None:
            memory = [total + count for total, count in zip(memory, _character_counts(_compact(layer)))]
    ratio = None if weights is None or not sum(memory) else round(
        sum(weight * count for weight, count in zip(weights, memory)) / sum(memory), 3)
    return {"pairs": len(pairs),
            "weights": None if weights is None else dict(zip(_CHARACTER_CLASSES, (round(w, 4) for w in weights))),
            "memory_layer_tokens_per_char": ratio, "reference": TOKEN_RATIO_REFERENCE,
            "reference_range": list(TOKEN_RATIO_RANGE),
            "within_range": None if ratio is None else TOKEN_RATIO_RANGE[0] <= ratio <= TOKEN_RATIO_RANGE[1]}


def _least_squares(pairs: Sequence[tuple[list[int], int]]) -> list[float] | None:
    """@brief 正规方程加部分主元的高斯消元；主元绝对值小于 1e-9 时为 None。"""
    size = len(_CHARACTER_CLASSES)
    matrix = [[float(sum(x[row] * x[column] for x, _ in pairs)) for column in range(size)]
              + [float(sum(x[row] * y for x, y in pairs))] for row in range(size)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(matrix[row][column]))
        if abs(matrix[pivot][column]) < 1e-9:
            return None
        matrix[column], matrix[pivot] = matrix[pivot], matrix[column]
        for row in range(size):
            if row != column:
                factor = matrix[row][column] / matrix[column][column]
                matrix[row] = [value - factor * base for value, base in zip(matrix[row], matrix[column])]
    return [matrix[row][size] / matrix[row][row] for row in range(size)]


# ---- M7、M8 ----

def _call_classes(export: PilotExport) -> dict[str, Mapping]:
    """@brief quality.json 的 model_usage.by_call_class，调用类 → 用量行。"""
    return {row["call_class"]: row for row in export.quality["model_usage"]["by_call_class"]}


def _hit_rate(row: Mapping) -> float | None:
    """@brief 一类调用的缓存命中率；priced_tokens 为 null 时为 null。"""
    priced = row["priced_tokens"]
    if priced is None:
        return None
    total = priced["cache_hit_input_tokens"] + priced["cache_miss_input_tokens"]
    return round(priced["cache_hit_input_tokens"] / total, 4) if total else None


def m7_cache(export: PilotExport) -> dict:
    """@brief M7 缓存：新决策回合的命中 token、未命中 token 与命中率，以及每个调用类的命中率。"""
    classes = _call_classes(export)
    fresh = classes["fresh_decision_turn"]
    priced = fresh["priced_tokens"]
    return {"fresh_decision_turn": {
        "hit_tokens": None if priced is None else priced["cache_hit_input_tokens"],
        "miss_tokens": None if priced is None else priced["cache_miss_input_tokens"],
        "value": _hit_rate(fresh)},
        "by_call_class": {name: _hit_rate(row) for name, row in classes.items()}}


def m8_cost(export: PilotExport) -> dict:
    """@brief M8 费用：按冻结的峰时价计每个调用类的费用，除以运行日数得每模拟日美元（六位小数）。"""
    usage = export.quality["model_usage"]
    peak = usage["pricing_snapshot"]["peak"]
    costs = {}
    for name, row in _call_classes(export).items():
        priced = row["priced_tokens"]
        costs[name] = 0.0 if priced is None else (
            priced["cache_hit_input_tokens"] * peak["cache_hit_input"]
            + priced["cache_miss_input_tokens"] * peak["cache_miss_input"]
            + priced["output_tokens"] * peak["output"]) / 1_000_000
    days = len(export.days)
    return {"days": days, "per_day_usd": round(sum(costs.values()) / days, 6),
            "by_call_class": {name: round(cost / days, 6) for name, cost in costs.items()},
            "complete": usage["estimated_cost"]["complete"]}


# ---- M9 ----

def m9_guardrails(export: PilotExport, rows: list[Decision]) -> dict:
    """@brief M9 护栏：每天决策数与带宽、拒绝与上限、回合、运行失败、容量压缩、同秒链与刚了结又重新开始。

    :param export: 认证导出。
    :param rows: decisions(export)。
    :returns: 「分层记忆试点指标」M9 一节的十个键。
    """
    per_day = {day.isoformat(): 0 for day in export.days}
    for row in rows:
        per_day[row.day.isoformat()] = per_day.get(row.day.isoformat(), 0) + 1
    low = round(DECISION_BASELINE_PER_DAY * (1 - DECISION_BAND), 2)
    high = round(DECISION_BASELINE_PER_DAY * (1 + DECISION_BAND), 2)
    limit = REJECTION_FACTOR * REJECTION_BASELINE * len(export.days) / REJECTION_BASELINE_DAYS
    return {
        "decisions_per_day": dict(sorted(per_day.items())), "decision_band": [low, high],
        "days_outside_band": [day for day, count in sorted(per_day.items()) if not low <= count <= high],
        "rejections": sum(event["event_type"] == "action_rejected" for event in export.events),
        "rejection_limit": round(limit, 4), "turns": _turn_report(export, rows),
        "run_failures": sum(event["event_type"] == "run_failed" for event in export.events),
        "capacity_compressions": sum(_condensed(request) for row in rows for request in row.requests),
        "chain": _chain_report(rows), "restarted": _restarts(export, rows),
    }


def _turns(row: Decision) -> int:
    """@brief 回合数：回合请求 call_id 里 :model:NNNN: 的不同 NNNN 的个数（截断重试是同一回合）。"""
    return len({match.group(1) for request in row.requests if (match := _TURN.search(request["data"]["call_id"]))})


def _turn_report(export: PilotExport, rows: Sequence[Decision]) -> dict:
    """@brief 回合：p99 与最大值只报告，回合数达到上限的决策数，回合数 ≥6 的决策逐个列出。"""
    limit = export.manifest["frozen_run_manifest"]["stage_model_identity"]["max_react_turns"]
    turns = {row.decision_id: _turns(row) for row in rows}
    ordered = sorted(turns.values())
    return {"max_react_turns": limit, "p99": round(float(_quantile(ordered, 0.99)), 1) if ordered else None,
            "max": ordered[-1] if ordered else None, "exhausted": sum(count >= limit for count in ordered),
            "six_or_more": [{"decision_id": row.decision_id, "time": _time(export, row.occurred_at),
                             "turns": turns[row.decision_id]} for row in rows if turns[row.decision_id] >= SIX_TURNS]}


def _condensed(request: dict) -> bool:
    """@brief 首个 user 消息的进度层里有没有 type 为 condensed 的记录。"""
    layer = _named_layer(request, "进度层")
    records = layer.get("events") if layer is not None else None
    return isinstance(records, list) and any(isinstance(item, Mapping) and item.get("type") == "condensed"
                                             for item in records)


def _chain_report(rows: Sequence[Decision]) -> dict:
    """@brief 同秒链：同秒决策数、其中的续接数与改用新快照数；续接的 changes.memory.added 必须恰是「今天」的追加，
    changes 不得带 relationships 或 preferences。"""
    followups = continuations = violations = deltas = 0
    for previous, row in zip(rows, rows[1:]):
        if row.occurred_at != previous.occurred_at:
            continue
        followups += 1
        if not row.requests or len(_messages(row.requests[0])) <= 2:
            continue
        continuations += 1
        changes = _last_tool_changes(row.requests[0])
        memory = changes.get("memory")
        added = memory.get("added") if isinstance(memory, Mapping) else None
        expected = [_sorted_compact(entry) for entry in [*_today(previous.observation), *(added or [])]]
        violations += expected != [_sorted_compact(entry) for entry in _today(row.observation)]
        deltas += bool({"relationships", "preferences"} & changes.keys())
    return {"same_second_followups": followups, "continuations": continuations,
            "fresh_followups": followups - continuations, "append_violations": violations, "persona_deltas": deltas}


def _last_tool_changes(request: dict) -> Mapping:
    """@brief 请求最后一条 tool 消息内容里的 changes；没有时为空对象。"""
    tools = [message for message in _messages(request) if message.get("role") == "tool"]
    payload = _parse(tools[-1].get("content")) if tools else None
    changes = payload.get("changes") if isinstance(payload, Mapping) else None
    return changes if isinstance(changes, Mapping) else {}


def _restarts(export: PilotExport, rows: Sequence[Decision]) -> list[dict]:
    """@brief 结束事件落在上一次与本次决策的观察之间的活动，本决策又受理了同一目标的活动。"""
    orders, listed = _order_goods(export), []
    endings = [(event["event_seq"], _compact(_target(export, snapshot, orders)))
               for event, snapshot in _endings(export).values()]
    for previous, row in zip(rows, rows[1:]):
        if previous.observation is None or row.observation is None:
            continue
        low, high = previous.observation["event_seq"], row.observation["event_seq"]
        ended = {target for seq, target in endings if low < seq < high}
        for activity in _accepted(row):
            target = _target(export, activity, orders)
            if _compact(target) in ended:
                listed.append({"decision_id": row.decision_id, "time": _time(export, row.occurred_at),
                               "target": target})
    return listed


def _target(export: PilotExport, activity: Mapping, orders: Mapping[str, str]) -> list:
    """@brief 活动的目标：app、地点内容（系列标题或内容身份）、购买商品、查询范围、出行目的地或睡眠。"""
    data = activity.get("data") if isinstance(activity.get("data"), Mapping) else {}
    kind = activity.get("kind")
    if kind == "interact" and data.get("app_id"):
        return ["app", data["app_id"]]
    if kind == "interact":
        series = export.contents.get(data.get("content_id"), {}).get("series") or {}
        return ["content", series.get("title") or data.get("content_id")]
    if kind == "purchase":
        return ["purchase", sorted(orders[order] for order in data.get("order_ids") or [])]
    if kind == "query":
        return ["query", data.get("app_id"), sorted(str(item.get("category") or item.get("goods_id"))
                                                     for item in data.get("targets") or [])]
    if kind == "travel":
        return ["travel", activity.get("destination_id")]
    return [str(kind)]


# ---- M10 ----

def m10_digests(export: PilotExport, rows: list[Decision]) -> dict:
    """@brief M10 回顾：调用、期次、重试、fallback、各守卫丢弃、引用、看法对象覆盖、统计照抄、期次问题与退役调用。

    :param export: 认证导出。
    :param rows: decisions(export)。
    :returns: 「分层记忆试点指标」M10 一节的十五个键。
    """
    events = _digest_events(export)
    requests = _digest_requests(export)
    missing, duplicates = _day_digest_problems(export, rows, events)
    periods = {tier: 0 for tier in MEMORY_DIGEST_TIERS}
    for event in events:
        periods[event["data"]["tier"]] += 1
    retired = sum(row["request_events"] for name, row in _call_classes(export).items()
                  if name in {"consolidation", "reflection"})
    plain = list(export.events)
    return {
        "calls_by_day": _digest_calls_by_day(export, requests), "periods": periods,
        "expected": _expected_periods(export), "retries": _digest_retries(requests),
        "fallback": _digest_fallback(events), "guard_discards": _guard_discards(export),
        "cited": _digest_cited(rows, events), "view_coverage": _view_coverage(export, requests, events),
        "statistics_copy_violations": _statistics_copies(export, requests, events),
        "day_digest_problems": len(missing) + len(duplicates), "missing_day_digests": missing,
        "duplicate_digests": duplicates, "retired_calls": retired,
        "repetition": digest_repetition(plain), "identity_residue": digest_identity_residue(plain),
    }


def _digest_events(export: PilotExport) -> list[dict]:
    """@brief 回顾登记事件（data.kind 为 memory_digest_generated 的环境事件）。"""
    return _environment(export, MEMORY_DIGEST_KIND)


def _period_digests(export: PilotExport, tier: str) -> dict[str, dict]:
    """@brief 一级回顾的期次 → 第一条回顾事件。"""
    found: dict[str, dict] = {}
    for event in _digest_events(export):
        if event["data"]["tier"] == tier:
            found.setdefault(event["data"]["period"], event)
    return found


def _recorded(event: dict, key: str) -> bool:
    """@brief 回顾事件里这一类有正文或在 fallback 里（这一期这一类有记录）。"""
    return event["data"]["digests"].get(key) is not None or key in event["data"]["fallback"]


def _digest_requests(export: PilotExport) -> dict[str, dict]:
    """@brief 回顾请求：call_id → purpose 为 memory_digest 的 model_request。"""
    return {event["data"]["call_id"]: event for event in export.events if event["event_type"] == "model_request"
            and event["data"].get("purpose") == MEMORY_DIGEST_PURPOSE}


def _digest_calls_by_day(export: PilotExport, requests: Mapping[str, dict]) -> dict:
    """@brief 有回顾请求的本地日期（升序）→ 按 call_id 里的级别计的请求数，三键都在。"""
    days: dict[str, dict[str, int]] = {}
    for call_id, request in requests.items():
        counts = days.setdefault(_day(export, request["occurred_at"]).isoformat(),
                                 {tier: 0 for tier in MEMORY_DIGEST_TIERS})
        counts[_DIGEST_CALL.fullmatch(call_id).group(1)] += 1
    return dict(sorted(days.items()))


def _digest_retries(requests: Mapping[str, dict]) -> int:
    """@brief 重试：每个 (tier, period) 的请求数减一之和。"""
    counts = Counter(_DIGEST_CALL.fullmatch(call_id).groups()[:2] for call_id in requests)
    return sum(count - 1 for count in counts.values())


def _expected_periods(export: PilotExport) -> dict:
    """@brief 应有的月、年回顾数：序号 ≥2 的运行日里「日」为 1 的日子，其中「月」也为 1 的日子。"""
    later = export.days[1:]
    return {"month": sum(day.day == 1 for day in later), "year": sum(day.day == 1 and day.month == 1 for day in later)}


def _digest_fallback(events: Sequence[dict]) -> dict:
    """@brief fallback 比例只数日回顾有记录的「类·日」；cells 列出全部级别落到 fallback 的格。"""
    cells, considered, fell = [], 0, 0
    for event in events:
        data = event["data"]
        for key in DIGEST_KEYS:
            if key in data["fallback"]:
                cells.append({"tier": data["tier"], "period": data["period"], "category": key})
            if data["tier"] == "day" and _recorded(event, key):
                considered += 1
                fell += key in data["fallback"]
    keys = list(DIGEST_KEYS)
    cells.sort(key=lambda cell: (MEMORY_DIGEST_TIERS.index(cell["tier"]), cell["period"],
                                 keys.index(cell["category"])))
    return {**_ratio(fell, considered), "cells": cells}


def _guard_pattern(template: str) -> re.Pattern[str]:
    """@brief 把问题文字转成整句模板：{key}、{tier} 换成闭集，其余占位换成 (.+)，其余字符按字面。"""
    fixed = {"{key}": "(" + "|".join(DIGEST_KEYS) + ")", "{tier}": "(" + "|".join(MEMORY_DIGEST_TIERS) + ")"}
    parts = re.split(r"(\{[^}]*\})", template)
    return re.compile("".join(fixed.get(part, "(.+)") if part.startswith("{") else re.escape(part)
                              for part in parts if part))


#: 各守卫的整句模板，次序同 _GUARD_TEMPLATES。
_GUARD_PATTERNS = tuple((name, _guard_pattern(template)) for name, template in _GUARD_TEMPLATES)


def _guard_discards(export: PilotExport) -> dict:
    """@brief 各守卫的丢弃次数：只读回顾的 response 证据里非空的 error，按「一次响应里的一类」计，十个键全部列出。"""
    counts = {name: 0 for name in ("response", "truncated", "schema", *(item[0] for item in _GUARD_TEMPLATES),
                                   "unrecognized")}
    for event in export.events:
        data = event["data"]
        if (event["event_type"] != "model_response" or data.get("purpose") != MEMORY_DIGEST_PURPOSE
                or data.get("phase") != "response" or not data.get("error")):
            continue
        found, unknown = _classify_error(data["call_id"], data["error"])
        for guard, _ in found:
            counts[guard] += 1
        counts["unrecognized"] += unknown
    return counts


def _classify_error(call_id: str, error: str) -> tuple[set[tuple[str, str]], int]:
    """@brief 一次回顾响应的错误：响应级、截断与参数结构各计一次；其余切成逐条问题按模板认守卫与类别键。

    :returns: （(守卫, 类别键) 的集合，没有模板匹配的问题条数）。
    """
    if error in _RESPONSE_ERRORS:
        return {("response", "")}, 0
    if error == _TRUNCATED:
        return {("truncated", "")}, 0
    if not error.startswith("digests."):
        return {("schema", "")}, 0
    found, unknown = set(), 0
    for problem in re.split(r"; (?=digests\.)", error):
        guard = next((name for name, pattern in _GUARD_PATTERNS if pattern.fullmatch(problem)), None)
        if guard is None:
            _LOGGER.error("Memory digest error %s matches no guard", call_id)
            unknown += 1
        else:
            found.add((guard, re.match(r"digests\.(food|work|life)", problem).group(1)))
    return found, unknown


def _digest_cited(rows: Sequence[Decision], events: Sequence[dict]) -> dict:
    """@brief 非空日回顾格中，在某次决策的近三天或检索结果里可见、且正文有八字窗口出现在该决策模型文字里的比例。"""
    cells = [(DIGEST_KEYS[key], event["data"]["period"], paragraph["text"]) for event in events
             if event["data"]["tier"] == "day" for key, paragraph in event["data"]["digests"].items()
             if paragraph is not None]
    found = _successful_items(rows)
    seen = []
    for row in rows:
        visible = {(category, cell.get("日期")) for category, cell in _recent_cells(row.observation)}
        visible |= {(item.get("类别"), item.get("日期")) for item in found.get(row.decision_id, [])
                    if isinstance(item.get("回顾"), str)}
        seen.append((visible, _windows(_model_text(row))))
    cited = sum(any((category, period) in visible and _windows(text) & windows for visible, windows in seen)
                for category, period, text in cells)
    return _ratio(cited, len(cells))


def _recent_cells(event: dict | None) -> list[tuple[str, Mapping]]:
    """@brief 观察记忆层「近三天」里的（类别，格）。"""
    recent = _memory_layer(event).get("近三天")
    return [(category, cell) for category, cells in (recent.items() if isinstance(recent, Mapping) else ())
            if isinstance(cells, list) for cell in cells if isinstance(cell, Mapping)]


def _digest_input(export: PilotExport, requests: Mapping[str, dict], tier: str, period: str) -> dict:
    """@brief 一期回顾 attempt:1 请求里按类别分开的输入：中文类别 → （条目行，次数统计行）。

    :raises EvaluationError: 取不到第 04 章规定的三类条目与统计行。
    """
    call_id = f"memory-digest:{export.agent_id}:{tier}:{period}:attempt:1"
    request = requests.get(call_id)
    payload = _parse(_role_content(request, "user")) if request is not None else None
    rows_key = {"month": "日回顾", "year": "月回顾"}.get(tier)
    categories = MEMORY_CATEGORIES
    if isinstance(payload, Mapping) and rows_key is None and all(isinstance(payload.get(name), list)
                                                                 for name in categories):
        return {name: (payload[name], []) for name in categories}
    if isinstance(payload, Mapping) and rows_key is not None and all(
            isinstance(payload.get(name), Mapping) and isinstance(payload[name].get(rows_key), list)
            and isinstance(payload[name].get("次数统计"), list) for name in categories):
        return {name: (payload[name][rows_key], payload[name]["次数统计"]) for name in categories}
    raise _fail(f"Memory digest request {call_id} does not hold the category entries")


def _view_coverage(export: PilotExport, requests: Mapping[str, dict], events: Sequence[dict]) -> dict:
    """@brief 看法对象覆盖率：日回顾 attempt:1 输入里各类「看法·对象：」的对象（每「类·日」去重）写进那一格正文的比例。"""
    day_events = {event["data"]["period"]: event for event in events if event["data"]["tier"] == "day"}
    total = written = 0
    for call_id in requests:
        match = _DIGEST_CALL.fullmatch(call_id)
        if match.group(1) != "day" or match.group(3) != "1":
            continue
        inputs = _digest_input(export, requests, "day", match.group(2))
        digests = day_events[match.group(2)]["data"]["digests"] if match.group(2) in day_events else {}
        for key, category in DIGEST_KEYS.items():
            subjects = list(dict.fromkeys(_view_subjects(inputs[category][0])))
            text = (digests.get(key) or {}).get("text") or ""
            total += len(subjects)
            written += sum(bool(text) and subject in text for subject in subjects)
    return _ratio(written, total)


def _view_subjects(rows: Sequence[Any]) -> list[str]:
    """@brief 输入行「自述」里以「看法·」开头的项的对象（「看法·」与第一个「：」之间）。"""
    return [note[len(_VIEW_PREFIX):note.index("：")] for row in rows if isinstance(row, Mapping)
            for note in row.get("自述") or [] if isinstance(note, str) and note.startswith(_VIEW_PREFIX)
            and "：" in note]


def _statistics_copies(export: PilotExport, requests: Mapping[str, dict], events: Sequence[dict]) -> int:
    """@brief 月、年回顾正文里「N 次」不是这一类某条次数统计行在正文里出现时的结尾的次数（只报告，不是检查项）。"""
    violations = 0
    for event in events:
        data = event["data"]
        if data["tier"] == "day":
            continue
        inputs = _digest_input(export, requests, data["tier"], data["period"])
        for key, paragraph in data["digests"].items():
            if paragraph is None:
                continue
            text, lines = paragraph["text"], [line for line in inputs[DIGEST_KEYS[key]][1] if isinstance(line, str)]
            spans = [(start, start + len(line)) for line in lines if line
                     for start in range(len(text)) if text.startswith(line, start)]
            violations += sum(not any(end == match.end() and start <= match.start() for start, end in spans)
                              for match in _COUNT.finditer(text))
    return violations


def _day_digest_problems(export: PilotExport, rows: Sequence[Decision],
                         events: Sequence[dict]) -> tuple[list[str], list[dict]]:
    """@brief 有决策的回顾日中没有日回顾事件的日期，与同一 (tier, period) 多于一条回顾事件的组。"""
    review_days = set(export.days[:-1])
    periods = {event["data"]["period"] for event in events if event["data"]["tier"] == "day"}
    missing = sorted({row.day.isoformat() for row in rows if row.day in review_days} - periods)
    counts = Counter((event["data"]["tier"], event["data"]["period"]) for event in events)
    duplicates = [{"tier": tier, "period": period, "events": count} for (tier, period), count in sorted(
        counts.items(), key=lambda item: (MEMORY_DIGEST_TIERS.index(item[0][0]), item[0][1])) if count > 1]
    return missing, duplicates


# ---- M11 ----

def m11_digest_fidelity(export: PilotExport) -> dict:
    """@brief M11 回顾保真：只用事件、购买与冻结目录独立重算饮食覆盖、做饭错配、餐次错配与外来菜名。

    :returns: {coverage, food_fallback, coverage_plus_fallback, cooking_mismatches, meal_mismatches,
        foreign_dishes, days}。
    """
    cooking, day_digests = _cooking(export), _period_digests(export, "day")
    received = _environment(export, "goods_received")
    days, covered, fell = [], 0, 0
    totals = {"cooking_mismatch": 0, "meal_mismatch": 0, "foreign": 0}
    for day in export.days[:-1]:
        event = day_digests.get(day.isoformat())
        text = ((event["data"]["digests"].get("food") or {}).get("text") if event is not None else None)
        fallback = event is not None and "food" in event["data"]["fallback"]
        report = _fidelity_day(export, day, cooking, received, text)
        for key in totals:
            totals[key] += len(report[key])
        if not report["expected"] and text is None and not fallback:
            continue
        covered += text is not None and not report["missing"]
        fell += fallback
        days.append({"date": day.isoformat(), "expected": report["expected"], "missing": report["missing"],
                     "fallback": fallback, **{key: report[key] for key in totals}})
    return {"coverage": _ratio(covered, len(days)), "food_fallback": _ratio(fell, len(days)),
            "coverage_plus_fallback": _ratio(covered + fell, len(days)),
            "cooking_mismatches": totals["cooking_mismatch"], "meal_mismatches": totals["meal_mismatch"],
            "foreign_dishes": totals["foreign"], "days": days}


def _fidelity_day(export: PilotExport, day: date, cooking: Sequence[_Cooking], received: Sequence[dict],
                  text: str | None) -> dict:
    """@brief 一个回顾日：F(D)、缺写的名字，以及饮食日回顾里的做饭错配、餐次错配与外来菜名。"""
    arrivals = [event for event in received if _day(export, event["occurred_at"]) == day]
    kits = _cooked_kits(export, cooking, day)
    food = [_goods_name(export, event["data"]["goods_id"]) for event in arrivals
            if _is_food_goods(export, event["data"]["goods_id"])]
    expected = list(dict.fromkeys([*food, *kits]))
    known = {_goods_name(export, row["goods_id"]) for row in _purchase_rows(export)
             if _day(export, row["created_at"]) == day}
    known |= {_goods_name(export, event["data"]["goods_id"]) for event in arrivals} | set(kits)
    body = text or ""
    kit_names = [_goods_name(export, goods_id) for goods_id in export.kits]
    food_names = [row["name"] for row in export.goods.values() if row.get("category") in _FOOD_CATEGORIES]
    return {
        "expected": expected, "missing": [name for name in expected if name not in body],
        "cooking_mismatch": [name for sentence in _sentences(body) if "做饭" in sentence
                             for name in _scan(sentence, kit_names) if name not in kits],
        "meal_mismatch": [name for sentence in _sentences(body)
                          for name in _meal_mismatches(export, sentence, food_names, arrivals)],
        "foreign": [dish for dish in _scan(body, export.lexicon) if not any(dish in name for name in known)],
    }


def _meal_mismatches(export: PilotExport, sentence: str, names: Sequence[str], arrivals: Sequence[dict]) -> list[str]:
    """@brief 一句写到早饭、午饭或晚饭时，句里认出的食品商品当天没有一次收货落在句中任一餐次的时段里。"""
    meals = {meal for meal in MEAL_PERIODS if meal in sentence}
    if not meals:
        return []
    return [name for name in _scan(sentence, names)
            if not any(_goods_name(export, event["data"]["goods_id"]) == name
                       and _meals_at(export, event["occurred_at"]) & meals for event in arrivals)]


# ---- M12 ----

def m12_endorsed_repurchase(export: PilotExport, rows: list[Decision]) -> dict:
    """@brief M12 表态食品回购率：第二个运行日起的食品购买被更早一天的偏好看法点名过的比例，按卡片与看法年龄分开。

    :param export: 认证导出。
    :param rows: decisions(export)。
    :returns: {purchases, endorsed, on_card, not_on_card, age, rows, earlier_view_reliance}。
    """
    views = _completed_views(export, rows)
    fresh = _fresh_requests(rows)
    card = _norm(_compact(_named_layer(fresh[0], "人设层"))) if fresh else ""
    catalog = [_norm(row["name"]) for row in export.goods.values()]
    listed, age = [], {"1-3": 0, "4+": 0}
    for row in _purchase_rows(export):
        day = _day(export, row["created_at"])
        if not _food_or_kit(export, row["goods_id"]) or day == export.days[0]:
            continue
        name = _norm(_goods_name(export, row["goods_id"]))
        support = [view for view in views if view["type"] == "preference" and view["day"] < day
                   and _names_goods(_norm(view["about"]), name, catalog)]
        if support:
            age["1-3" if (day - max(view["day"] for view in support)).days <= 3 else "4+"] += 1
        listed.append({"time": _time(export, row["created_at"]), "goods": _goods_name(export, row["goods_id"]),
                       "on_card": _lcs(name, card) >= min(4, len(name)),
                       "endorsed_by": [{"about": view["about"], "date": view["day"].isoformat()} for view in support]})
    endorsed = [item for item in listed if item["endorsed_by"]]
    return {"purchases": len(listed), "endorsed": _ratio(len(endorsed), len(listed)),
            "on_card": _ratio(sum(item["on_card"] for item in endorsed), len(listed)),
            "not_on_card": _ratio(sum(not item["on_card"] for item in endorsed), len(listed)),
            "age": age, "rows": listed, "earlier_view_reliance": _view_reliance(export, rows, views)}


def _names_goods(subject: str, goods: str, catalog: Sequence[str]) -> bool:
    """@brief 规范化后的看法对象是否点名规范化后的商品名：不足 3 字或能匹配 2 件以上目录商品时不点名。"""

    def matches(name: str) -> bool:
        """@brief 相等，或对象至少 4 字时互相包含。"""
        return subject == name or len(subject) >= 4 and (subject in name or name in subject)

    return len(subject) >= 3 and sum(map(matches, catalog)) <= 2 and matches(goods)


def _view_reliance(export: PilotExport, rows: Sequence[Decision], views: Sequence[dict]) -> dict:
    """@brief 看法依赖（旁证）：决策的模型文字点名或八字回声了更早日子的已了结看法，而请求文字里没有。"""
    listed = []
    for row in rows:
        earlier = [view for view in views if view["day"] < row.day and view["type"] in {"preference", "relationship"}]
        if not earlier:
            continue
        model, request = _model_text(row), _request_text(row)
        model_norm, request_norm, windows = _norm(model), _norm(request), _windows(model)
        relied = []
        for view in earlier:
            subject = _norm(view["about"])
            if len(subject) >= 2 and subject in model_norm and subject not in request_norm:
                relied.append({"about": view["about"], "date": view["day"].isoformat(), "by": "mention"})
            elif any(window not in request for window in _windows(view["text"]) & windows):
                relied.append({"about": view["about"], "date": view["day"].isoformat(), "by": "echo"})
        if relied:
            listed.append({"decision_id": row.decision_id, "time": _time(export, row.occurred_at), "views": relied})
    return {"decisions": len(rows), "relying": len(listed),
            "value": round(len(listed) / len(rows), 4) if rows else None, "rows": listed}


# ---- 检查项、对照表与命令行 ----

def pilot_checks(metrics: dict) -> list[dict]:
    """@brief 按提案对最终形态写出的阈值逐项判定；值为 null 时 pass 为 null，浮点数按报告里已舍入的值比较。

    :param metrics: 报告的 metrics（键 M1…M12）。
    :returns: 每项 {name, path, comparison, threshold, value, pass}。
    """
    checks = []
    for name, path, comparison, threshold in _CHECKS:
        value = _path_value(metrics, path)
        if path in _LENGTH_PATHS and value is not None:
            value = len(value)
        limit = _path_value(metrics, threshold) if isinstance(threshold, str) else threshold
        passed = None if value is None or limit is None else {
            "==": value == limit, "<=": value <= limit, ">=": value >= limit}[comparison]
        checks.append({"name": name, "path": path, "comparison": comparison, "threshold": limit, "value": value,
                       "pass": passed})
    return checks


def _path_value(metrics: Mapping, path: str) -> Any:
    """@brief 按点分路径取报告里的值；路径不存在时为 None。"""
    value: Any = metrics
    for key in path.split("."):
        value = value.get(key) if isinstance(value, Mapping) else None
    return value


def digest_review(export: PilotExport) -> list[dict]:
    """@brief 每天的回顾和当天核验的对照表：日回顾事件按期次升序，每类一行，带状态与 attempt:1 输入的全部核验行。"""
    requests = _digest_requests(export)
    rows = []
    for event in sorted((event for event in _digest_events(export) if event["data"]["tier"] == "day"),
                        key=lambda event: event["data"]["period"]):
        data, period = event["data"], event["data"]["period"]
        inputs = _digest_input(export, requests, "day", period)
        for key, category in DIGEST_KEYS.items():
            paragraph = data["digests"].get(key)
            status = "written" if paragraph is not None else "unsorted" if key in data["fallback"] else "no_records"
            verified = [] if status == "no_records" else [
                line for row in inputs[category][0] if isinstance(row, Mapping) for line in row.get("核验") or []]
            rows.append({"date": period, "category": category,
                         "digest": paragraph["text"] if paragraph is not None else None,
                         "status": status, "verified": verified})
    return rows


def main(argv: list[str] | None = None) -> int:
    """@brief 命令行：python -m tools.analysis.memory_pilot --export DIR [--output FILE]。

    没有 --output 时把报告写到标准输出；给了时文件必须不存在。认证失败与输入问题返回 1（异常构造时已写错误日志）。
    """
    parser = argparse.ArgumentParser(prog="python -m tools.analysis.memory_pilot",
                                     description="Compute the layered-memory pilot metrics of one authenticated "
                                                 "single-person export.")
    parser.add_argument("--export", required=True, type=Path, help="worldmock export output directory")
    parser.add_argument("--output", type=Path, help="report file to create; stdout when omitted")
    args = parser.parse_args(argv)
    try:
        if args.output is not None and args.output.exists():
            raise _fail(f"Output file already exists: {args.output}")
        text = json.dumps(pilot_report(args.export), ensure_ascii=False, indent=2) + "\n"
    except (EvaluationError, WorldMockError):
        return 1
    if args.output is None:
        sys.stdout.write(text)
    else:
        args.output.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
