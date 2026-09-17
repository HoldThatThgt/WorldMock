"""@brief taste_weights：卡片口味 x 内容 genre[] 确定性匹配，零随机。"""

from __future__ import annotations

from worldmock.profile.taste import compute_taste_weights

_ITEMS = [
    {"item_id": "movie-0002", "genre": ["剧情", "战争"]},
    {"item_id": "movie-0001", "genre": ["剧情", "历史"]},
    {"item_id": "novel-0007", "genre": ["悬疑"]},
    {"item_id": "feed-0001", "genre": []},
]


def test_weights_from_genre_matching():
    """@brief 权重 = |口味交集| / |条目 genre|：悬疑命中 1/1、剧情类 0/2。"""
    weights = compute_taste_weights(("悬疑",), _ITEMS)
    assert weights == {"feed-0001": 0.0, "movie-0001": 0.0, "movie-0002": 0.0,
                       "novel-0007": 1.0}


def test_partial_match_weight():
    weights = compute_taste_weights(("剧情",), _ITEMS)
    assert weights["movie-0001"] == 0.5 and weights["movie-0002"] == 0.5
    assert weights["novel-0007"] == 0.0


def test_deterministic_and_sorted_output():
    """@brief 同输入两次逐字节相同；输出按 item_id 排序。"""
    first = compute_taste_weights(("剧情", "悬疑"), _ITEMS)
    second = compute_taste_weights(("剧情", "悬疑"), _ITEMS)
    assert first == second
    assert list(first) == sorted(first)


def test_no_tastes_yield_zero_weights_not_defaults():
    """@brief 卡片无口味：全部条目权重 0（不默认抬升，取代随机浮点的 0.5 缺省）。"""
    weights = compute_taste_weights((), _ITEMS)
    assert set(weights.values()) == {0.0}


def test_items_without_genre_get_zero():
    assert compute_taste_weights(("剧情",), _ITEMS)["feed-0001"] == 0.0


def test_weights_stay_in_unit_interval():
    weights = compute_taste_weights(("剧情", "战争", "悬疑"), _ITEMS)
    assert all(0.0 <= value <= 1.0 for value in weights.values())
    assert weights["movie-0002"] == 1.0
