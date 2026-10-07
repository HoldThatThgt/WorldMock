"""@brief 新建运行与验证共用的真实目录画像覆盖目标。"""

from __future__ import annotations


from worldmock.catalog.models import Catalog
from worldmock.exchange.types import GoodsCategory
from worldmock.life.scenario import RuntimeScenario
from worldmock.profile.coverage import CoverageTargets


def build_coverage_targets(scenario: RuntimeScenario, catalog: Catalog) -> CoverageTargets:
    """@brief 从登记的动作、地点、食品和应用名称构造覆盖目标，不建立内容分类。

    @param scenario 已通过真实加载器组装的运行场景。
    @param catalog 当前环境的真实登记目录。
    @return 习惯关键词、食品名、全部登记动作名和应用名。
    """
    habit_keywords = {item.name for item in catalog.actions.values()}
    habit_keywords.update(item.display_name for item in catalog.locations.values())
    return CoverageTargets(
        habit_response_keywords=tuple(sorted(habit_keywords)),
        food_kinds=tuple(sorted({item.name for item in catalog.goods.values()
                                 if item.category in {GoodsCategory.MEAL, GoodsCategory.SNACK, GoodsCategory.DRINK}})),
        action_names=tuple(sorted({item.name for item in catalog.actions.values()})),
        app_names=tuple(sorted({item.name for item in catalog.apps.values()})),
    )
