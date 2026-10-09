"""@brief 从真实登记目录构建地点、食品与内容应用的画像覆盖目标。"""

from __future__ import annotations

from worldmock.environment import EnvironmentInputs
from worldmock.profile.coverage import CoverageTargets

def build_coverage_targets(inputs: EnvironmentInputs) -> CoverageTargets:
    """@brief 从唯一环境目录形成地点显示名、食品及完整内容应用名称。"""
    catalog = inputs.catalog
    names = {item.display_name for item in catalog.locations.values()}
    return CoverageTargets(habit_response_keywords=tuple(sorted(names)),
                           food_kinds=tuple(sorted({item.name for item in catalog.goods.values()
                                                    if item.category in {"meal", "snack", "drink"}})),
                           app_names=tuple(sorted({item.name for item in catalog.apps.values()
                                                   if item.role == "content"})))
