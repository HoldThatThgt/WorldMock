"""@brief 候选排序的相关度层（docs/dev/SPEC-interaction-menu.md#dayrun-fidelity-呈现与浏览-生活活动的地点类资格与排序 ，裁决 S4）。

排序键为（非零口味，−口味×注意力，−相关度，哈希决胜键）；相关度只取人物自己的事实与目录事实，三项相加：

- 目录 `time_window` 覆盖此刻（场景时区的本地时刻落在 [start_hour, end_hour) 内）：+2；只有生活活动目录声明时间窗；
- 条目属于当日未推进目标的活动家族：+2；家族取零点计划推进判定所用的同一张意图 → 活动家族表
  （`schedule.INTENT_ACTIVITY_FAMILIES`：生活活动按「lifestyle:<品类>」，其余按候选类别对应的活动类型）；目标取该秒
  开始时的今日计划（同一秒各轮的呈现窗口不变，本秒内改计划或推进目标从下一秒起才进入排序，由观察侧取）；
- 人设层习惯签名的反应文本命中条目名或品类（逐字包含，不分大小写）：+1。

不采用「今天已经做过 −1」「在家时的家务与自理类 +1」与例行名额（裁决 S4 的另一选项）：前者以互动记录排序，后两者是系统
替人物做的偏好。习惯签名来自新建运行时编译的人物卡片，冻结在运行清单 `persona_habit_responses`（人物编号 → 反应文本），
恢复与重放随父清单继承；引擎直驱建立的运行没有卡片，该键为 null，这一项恒为 0。本模块是纯函数，不读写世界状态。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from worldmock.errors import IntegrityError
from worldmock.life.models import ActivityKind
from worldmock.life.schedule import INTENT_ACTIVITY_FAMILIES

#: 目录时间窗覆盖此刻的相关度。
TIME_WINDOW_POINTS = 2
#: 属于当日未推进目标的活动家族的相关度。
GOAL_FAMILY_POINTS = 2
#: 人设层习惯签名的反应文本命中条目名或品类的相关度。
HABIT_POINTS = 1
#: 运行清单中冻结习惯签名反应文本的键。
MANIFEST_KEY = "persona_habit_responses"
#: 候选类别 → 活动家族（生活活动另按品类细分为「lifestyle:<品类>」）。
_KIND_FAMILIES = {"product": ActivityKind.CONSUME.value, "content": ActivityKind.WATCH.value,
                  "game": ActivityKind.PLAY_GAME.value, "task": ActivityKind.WORK.value}


@dataclass(frozen=True, slots=True)
class RelevanceFacts:
    """@brief 一次取候选的相关度原料：场景时区的本地时刻、当日未推进目标的活动家族与习惯签名的反应文本。"""

    hour: float
    goal_families: frozenset[str] = frozenset()
    habit_texts: tuple[str, ...] = ()


def intent_families(intents: Iterable[str]) -> frozenset[str]:
    """@brief 一组意图对应的活动家族并集（零点计划推进判定所用的同一张表；表外意图没有家族）。"""
    return frozenset(family for intent in intents for family in INTENT_ACTIVITY_FAMILIES.get(intent, ()))


def goal_families(goals: Iterable[Any] | None) -> frozenset[str]:
    """@brief 当日未推进（progressed_at 为空）的目标所属意图对应的活动家族并集；没有今日计划时为空。"""
    return intent_families(goal.intent_kind for goal in goals or () if goal.progressed_at is None)


def entry_family(kind: str, category: str) -> str:
    """@brief 候选条目的活动家族：生活活动为「lifestyle:<品类>」，其余按候选类别对应的活动类型。"""
    return f"lifestyle:{category}" if kind == "lifestyle" else _KIND_FAMILIES[kind]


def entry_relevance(entry: Any, facts: RelevanceFacts, window: tuple[int, int] | None = None) -> int:
    """@brief 一个池条目（presentation.PoolEntry）的相关度。

    :param entry: 池条目：类别 kind、品类 category 与呈现行 row（名称取行的 name 或 title）。
    :param facts: 本次取候选的相关度原料。
    :param window: 目录声明的时间窗（start_hour, end_hour）；没有声明时为 None。
    """
    score = 0
    if window is not None and window[0] <= facts.hour < window[1]:
        score += TIME_WINDOW_POINTS
    if entry_family(entry.kind, entry.category) in facts.goal_families:
        score += GOAL_FAMILY_POINTS
    words = [word for word in (entry.row.get("name") or entry.row.get("title"), entry.category) if word]
    if any(str(word).casefold() in text.casefold() for text in facts.habit_texts for word in words):
        score += HABIT_POINTS
    return score


def pool_relevance(entries: Iterable[Any], facts: RelevanceFacts,
                   windows: Mapping[str, tuple[int, int]] | None = None) -> dict[str, int]:
    """@brief 池内各条目的相关度（对象标识 → 分）；windows 为生活活动目录的时间窗（活动标识 → (start, end)）。"""
    declared = windows or {}
    return {entry.object_id: entry_relevance(entry, facts, declared.get(entry.object_id) if entry.kind == "lifestyle"
                                             else None) for entry in entries}


def frozen_habit_responses(value: Mapping[Any, Sequence[str]] | None) -> dict[str, list[str]] | None:
    """@brief 冻结进运行清单的习惯签名反应文本：人物编号（写成字符串）→ 反应文本列表；None 原样保留（引擎直驱）。

    :raises IntegrityError: 人物编号不是正整数，或反应文本不是非空字符串。
    """
    if value is None:
        return None
    frozen: dict[str, list[str]] = {}
    for key, texts in value.items():
        if not str(key).isdigit() or int(key) <= 0 or isinstance(texts, str):
            logging.error("Persona habit responses are keyed by a non-agent id or are not a list")
            raise IntegrityError("persona habit responses must map agent ids to text lists")
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            logging.error("Persona habit responses contain an empty or non-text item")
            raise IntegrityError("persona habit responses must be non-empty texts")
        frozen[str(int(key))] = list(texts)
    return dict(sorted(frozen.items(), key=lambda item: int(item[0])))


def manifest_habit_texts(manifest: Mapping[str, Any], agent_id: int) -> tuple[str, ...]:
    """@brief 运行清单中该人物的习惯签名反应文本；键缺失或为 null（引擎直驱）时为空。"""
    return tuple((manifest.get(MANIFEST_KEY) or {}).get(str(agent_id), ()))
