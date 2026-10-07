"""候选排序的相关度层（life.relevance，docs/dev/SPEC-interaction-menu.md#dayrun-fidelity-呈现与浏览-生活活动的地点类资格与排序 ，裁决 S4）：纯函数单元测试。

三项相加：目录时间窗覆盖此刻 +2，属于当日未推进目标的活动家族 +2（与推进判定同一张意图 → 活动家族表），人设层习惯签名
的反应文本逐字包含条目名或品类 +1；不采用「今天已经做过 −1」「在家时的家务与自理类 +1」与例行名额。习惯签名的反应文本
冻结在运行清单，形状在冻结时校验。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from worldmock.errors import IntegrityError
from worldmock.life.relevance import (GOAL_FAMILY_POINTS, HABIT_POINTS, MANIFEST_KEY, TIME_WINDOW_POINTS,
                                      RelevanceFacts, entry_family, entry_relevance, frozen_habit_responses,
                                      goal_families, intent_families, manifest_habit_texts, pool_relevance)
from worldmock.life.schedule import INTENT_ACTIVITY_FAMILIES, PlanGoal


def _entry(object_id: str, kind: str = "lifestyle", category: str = "household", **row) -> SimpleNamespace:
    """池条目的最小视图：身份、候选类别、品类与呈现行。"""
    return SimpleNamespace(object_id=object_id, kind=kind, category=category, row=row)


def test_points_are_the_three_adopted_terms() -> None:
    """三项分值：时间窗 +2、当日未推进目标的家族 +2、习惯签名命中 +1。"""
    assert (TIME_WINDOW_POINTS, GOAL_FAMILY_POINTS, HABIT_POINTS) == (2, 2, 1)


def test_goal_families_come_from_unprogressed_goals_through_the_intent_table() -> None:
    """只算未推进的目标，家族取推进判定所用的同一张意图 → 活动家族表；没有今日计划时为空。"""
    goals = (PlanGoal(intent_kind="家务", goal="洗碗", registered_at=10),
             PlanGoal(intent_kind="娱乐", goal="看电影", registered_at=10, progressed_at=20,
                      progress_activity_id="activity-1"))
    assert goal_families(goals) == INTENT_ACTIVITY_FAMILIES["家务"]
    assert goal_families(None) == frozenset() == goal_families(())


def test_intent_families_unite_the_table_rows_and_skip_unknown_intents() -> None:
    """一组意图（观察侧取该秒开始时待办层写出的未推进目标）对应的家族并集；表外意图没有家族。"""
    assert intent_families(["运动", "家务"]) == INTENT_ACTIVITY_FAMILIES["运动"] | INTENT_ACTIVITY_FAMILIES["家务"]
    assert intent_families(["不存在的意图"]) == frozenset() == intent_families(())


@pytest.mark.parametrize(("kind", "category", "family"), [
    ("lifestyle", "sport", "lifestyle:sport"), ("product", "foods", "consume"),
    ("task", "work_contract", "work")])
def test_entry_family_matches_the_progress_families(kind, category, family) -> None:
    """条目的活动家族：生活活动按品类，其余按候选类别对应的活动类型（与推进判定的家族同名）。"""
    assert entry_family(kind, category) == family


def test_entry_relevance_adds_time_window_goal_family_and_habit_hits() -> None:
    """时间窗是半开区间；家族命中与习惯命中（条目名或品类，不分大小写）各自加分；空名不算命中。"""
    facts = RelevanceFacts(hour=21.0, goal_families=frozenset({"lifestyle:household"}),
                           habit_texts=("吃完饭就洗碗", "周末 SPORT 一小时"))
    dishes = _entry("housework-dishes", name="洗碗")
    assert entry_relevance(dishes, facts, (6, 22)) == 2 + 2 + 1
    assert entry_relevance(dishes, facts, (6, 21)) == 2 + 1
    assert entry_relevance(_entry("yoga", category="sport", name="瑜伽"), facts, (6, 21)) == 1
    assert entry_relevance(_entry("tea", category="wellness", name="泡茶"), facts, (8, 22)) == 2
    assert entry_relevance(_entry("movie-0001", kind="task", category="work_contract", title="洗碗工"), facts) == 0
    assert entry_relevance(_entry("blank", category="", name=""), facts) == 0
    quiet = RelevanceFacts(hour=3.0)
    assert entry_relevance(dishes, quiet, (6, 22)) == 0


def test_pool_relevance_reads_time_windows_only_for_lifestyle_entries() -> None:
    """池内逐条给分；目录时间窗只用于生活活动（内容、商品没有时间窗）。"""
    facts = RelevanceFacts(hour=9.0)
    entries = (_entry("wash", name="晨间洗漱"), _entry("wash", kind="product", category="foods", title="新闻"))
    assert pool_relevance(entries[:1], facts, {"wash": (5, 10)}) == {"wash": TIME_WINDOW_POINTS}
    assert pool_relevance(entries[1:], facts, {"wash": (5, 10)}) == {"wash": 0}
    assert pool_relevance(entries[:1], facts) == {"wash": 0}


def test_habit_responses_are_frozen_in_a_checked_shape() -> None:
    """冻结进运行清单的形状：人物编号（字符串，按编号排序）→ 反应文本列表；null 原样保留（引擎直驱）；编号不是正整数、
    文本不是非空字符串或值是单个字符串都拒绝。"""
    assert frozen_habit_responses(None) is None
    assert frozen_habit_responses({10: ["看书"], 2: ["散步", "泡茶"]}) == {"2": ["散步", "泡茶"], "10": ["看书"]}
    for broken in ({0: ["看书"]}, {"one": ["看书"]}, {1: "看书"}, {1: ["  "]}, {1: [3]}):
        with pytest.raises(IntegrityError):
            frozen_habit_responses(broken)


def test_manifest_habit_texts_are_empty_without_frozen_cards() -> None:
    """清单没有该键、键为 null 或人物没有卡片时，这一项没有文本。"""
    assert manifest_habit_texts({}, 1) == () == manifest_habit_texts({MANIFEST_KEY: None}, 1)
    assert manifest_habit_texts({MANIFEST_KEY: {"1": ["散步"]}}, 2) == ()
    assert manifest_habit_texts({MANIFEST_KEY: {"1": ["散步"]}}, 1) == ("散步",)
