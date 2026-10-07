"""@brief 商品相关度只命中真实商品名称与类别；没有持续商品活动或生活动作时间窗。"""

from types import SimpleNamespace
import pytest
from worldmock.errors import IntegrityError
from worldmock.life.relevance import (HABIT_POINTS, MANIFEST_KEY, entry_relevance,
                                      pool_relevance, frozen_habit_responses, manifest_habit_texts)


def _entry(identity, name, category="meal"):
    """@brief 当前商品候选的真实呈现行。"""
    return SimpleNamespace(object_id=identity, kind="product", category=category, row={"name": name})


def test_product_habit_relevance_uses_name_or_category_without_double_counting():
    """@brief 名称或类别命中冻结反应文本加一分，双重命中不叠加，不合成活动家族。"""
    assert HABIT_POINTS == 1
    dishes = _entry("noodles", "牛肉面")
    assert entry_relevance(dishes, ("中午点牛肉面",)) == 1
    assert entry_relevance(dishes, ("MEAL后休息",)) == 1
    assert entry_relevance(dishes, ("MEAL牛肉面",)) == 1
    assert entry_relevance(dishes, ("散步",)) == 0
    assert entry_relevance(dishes, ()) == 0
    assert entry_relevance(_entry("blank", "", ""), ("所有商品",)) == 0


def test_product_pool_relevance_keeps_every_current_goods_identity():
    """@brief 商品集合不变，排序分只来自本人的冻结习惯。"""
    entries = (_entry("noodles", "牛肉面"), _entry("tea", "柠檬茶", "drink"))
    assert pool_relevance(entries, ("我常喝柠檬茶",)) == {"noodles": 0, "tea": 1}
    assert pool_relevance(entries, ()) == {"noodles": 0, "tea": 0}


def test_actual_goods_window_uses_frozen_habit_relevance(engine_factory):
    """@brief 真实观察商品窗口采用清单冻结的习惯分，保留全部商品真实集合。"""
    engine = engine_factory(create_options={"persona_habit_responses": {1: ["中午买牛肉面"]}})
    window = engine.observations._window_rows(engine.context.state.agents[1])["window"]
    assert window.items[0]["name"] == "牛肉面"
    assert len(window.items) == len({row["goods_id"] for row in window.items})
    assert all(row["goods_id"] in engine.context.semantics.catalog.goods for row in window.items)


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
