"""@brief 人物卡片覆盖率校验：persona_coverage.json 结构（docs/design/06-agent.md「人物卡片覆盖率产出」）。

习惯签名、食品偏好与媒介必须映射到登记目录中的可执行目标。内容题材仍保留为画像散文，不参与覆盖率。
可执行比例低于 80% 时校验失败并列出缺失项。习惯、食品名与动作名按双向子串匹配，应用名精确匹配。
"""

from __future__ import annotations

from worldmock.base_models import FrozenModel
from pydantic import Field

from worldmock.errors import IntegrityError
from worldmock.profile.models import PersonaCard

#: 可执行比例阈值（docs/design/06-agent.md「人物卡片覆盖率产出」合同常数：低于 0.8 校验失败）。
COVERAGE_THRESHOLD = 0.8
COVERAGE_SCHEMA_VERSION = 1


class CoverageTargets(FrozenModel):
    """@brief 调用方给出的登记目录映射目标清单，profile 不自行加载。"""


    habit_response_keywords: tuple[str, ...] = ()
    food_kinds: tuple[str, ...] = ()
    #: 全部登记动作的名称，按双向子串匹配媒介偏好。
    action_names: tuple[str, ...] = ()
    # 全部登记应用的名称，只作媒介条目的精确命中。
    app_names: tuple[str, ...] = ()


class MissingEntry(FrozenModel):
    """@brief 一条缺失映射：类别与未映射值。"""


    kind: str
    value: str


class PersonaCoverageReport(FrozenModel):
    """@brief persona_coverage.json 输出结构。"""


    schema_version: int = COVERAGE_SCHEMA_VERSION
    total_items: int = Field(ge=0)
    covered_items: int = Field(ge=0)
    executable_ratio: float = Field(ge=0.0, le=1.0)
    passed: bool
    missing: tuple[MissingEntry, ...]


class PersonaCoverageError(IntegrityError):
    """@brief 覆盖率低于阈值：校验失败，缺失项列在报告与消息中。"""


def validate_coverage(card: PersonaCard,
                      targets: CoverageTargets) -> PersonaCoverageReport:
    """@brief 校验卡片习惯 response 与口味条目的世界可执行覆盖。

    total_items 为 0（无习惯且无口味）时无可校验项：按缺省通过（比例 1.0）。
    内容题材不计入分母。App 名只在媒介条目上精确命中。
    """
    checks: list[tuple[str, str, bool]] = []
    for habit in card.habits:
        checks.append(("habit_response", habit.response,
                       _keyword_hit(habit.response, targets.habit_response_keywords)))
    for food in card.tastes.food_preferences:
        checks.append(("food_preference", food, _keyword_hit(food, targets.food_kinds)))
    for medium in card.tastes.media:
        checks.append(("media", medium, _keyword_hit(medium, targets.action_names) or medium in targets.app_names))
    total = len(checks)
    covered = sum(1 for _, _, hit in checks if hit)
    ratio = (covered / total) if total else 1.0
    missing = tuple(MissingEntry(kind=kind, value=value)
                    for kind, value, hit in checks if not hit)
    return PersonaCoverageReport(
        total_items=total,
        covered_items=covered,
        executable_ratio=round(ratio, 4),
        passed=ratio >= COVERAGE_THRESHOLD,
        missing=missing,
    )


def require_coverage(report: PersonaCoverageReport) -> None:
    """@brief 门禁入口：可执行比例低于 80% 抛 PersonaCoverageError 并列缺失项。"""
    if not report.passed:
        missing = ", ".join(f"{item.kind}={item.value}" for item in report.missing)
        raise PersonaCoverageError(
            f"PERSONA_COVERAGE_FAILED: executable_ratio={report.executable_ratio} "
            f"below {COVERAGE_THRESHOLD}; missing: [{missing}]")


def _keyword_hit(response: str, keywords: tuple[str, ...]) -> bool:
    """@brief 习惯 response 命中判定：response 与任一关键词互为子串。"""
    return any(keyword in response or response in keyword
               for keyword in keywords if keyword)
