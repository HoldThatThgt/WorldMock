"""@brief 卡片口味 x 内容 genre[] -> taste_weights（确定性匹配，零随机，
docs/dev/SPEC-life-realism.md#spec-life-realism-特性规格人物决策与画像「画像编译」）。

取代已删除的 entertainment_preferences 随机浮点：权重由卡片 content_genres
与每个内容、游戏条目的 genre[] 精确匹配得到，不进提示。原始权重是内部评分器与
浏览的排序信号；实际缓解另按有效偏好 f + (1 − f)·w 调幅度（activities.relief_preference，
docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要-下限缓解与累积口径 ）。匹配规则：weight = |条目 genre 与卡片口味的交集| / |条目 genre|；
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
