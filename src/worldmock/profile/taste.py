"""@brief 卡片口味 x 内容 genre[] -> taste_weights（确定性匹配，零随机，规格 L1661-1662）。

取代已删除的 entertainment_preferences 随机浮点：权重由卡片 content_genres
与每个内容条目的 genre[] 精确匹配得到，只供内部评分器与浏览排序使用，
不进提示。匹配规则：weight = |条目 genre 与卡片口味的交集| / |条目 genre|；
无 genre 的条目权重 0；卡片无口味则全部条目权重 0（不默认抬升）。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence


def compute_taste_weights(content_genres: Sequence[str],
                          items: Iterable[Mapping]) -> dict[str, float]:
    """@brief 确定性口味权重：同（口味, 条目集）输入逐字节相同输出。

    @param content_genres 卡片口味的内容题材列表
    @param items 内容条目（每条须有 item_id 与 genre 列表）
    @return {item_id: weight}（按 item_id 排序，weight 保留 4 位小数 ∈ [0,1]）
    """
    tastes = set(content_genres)
    weights: dict[str, float] = {}
    for item in items:
        item_id = item["item_id"]
        genres = list(item.get("genre") or [])
        weight = (len(tastes.intersection(genres)) / len(genres)) if genres else 0.0
        weights[item_id] = round(weight, 4)
    return dict(sorted(weights.items()))
