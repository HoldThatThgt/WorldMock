"""@brief 新建运行与验证共用的真实目录画像覆盖目标。"""

from __future__ import annotations

from pathlib import Path

from worldmock.catalog.models import Catalog
from worldmock.exchange.types import CONTENT_KIND_LABELS
from worldmock.life.content import load_lifestyle_items
from worldmock.life.scenario import RuntimeScenario
from worldmock.profile.coverage import CoverageTargets


def build_coverage_targets(scenario: RuntimeScenario, catalog: Catalog, corpora_root: Path) -> CoverageTargets:
    """@brief 从已登记地点、食品和应用名及固定内容类型标签构造覆盖目标，不读取旧手机或题材配置。

    @param scenario 已通过真实加载器组装的运行场景。
    @param catalog 当前环境的真实登记目录。
    @param corpora_root 当前场景已经核验身份的生活活动语料根。
    @return 习惯关键词、食品名、五个固定内容类型标签和全部登记应用名。
    """
    lifestyle = load_lifestyle_items(corpora_root, scenario.body.activity_met)
    habit_keywords = {item.name for item in lifestyle}
    habit_keywords.update(item.display_name for item in catalog.locations.values())
    return CoverageTargets(
        habit_response_keywords=tuple(sorted(habit_keywords)),
        food_kinds=tuple(sorted({item.name for item in catalog.foods.values()})),
        media_kinds=tuple(CONTENT_KIND_LABELS.values()),
        app_names=tuple(sorted({item.name for item in catalog.apps.values()})),
    )
