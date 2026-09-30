"""@brief 人物卡片覆盖率校验：persona_coverage.json 结构（docs/CONTRACTS.md「人物卡片覆盖率产出」）。

每条习惯签名的 response 与每个口味条目必须映射到世界中至少一个可执行目标
或条目（映射目标清单作为输入由调用方给出目录/品类集）；可执行比例低于 80%
时校验失败并列出缺失项。这一校验防止画像写了播客而世界里没有播客。
匹配规则（确定性）：口味条目与目标品类集精确相等；习惯 response 与目标
关键词互为子串（"公园慢跑半小时" 命中关键词 "慢跑"）。App 名只作媒介的精确命中，
不以子串给习惯或内容题材算命中（docs/dev/SPEC-dayrun-fidelity.md 1.3：「B站视频」不因应用名「B站」算覆盖）。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from worldmock.errors import IntegrityError
from worldmock.profile.models import PersonaCard

#: 可执行比例阈值（docs/CONTRACTS.md「人物卡片覆盖率产出」合同常数：低于 0.8 校验失败）。
COVERAGE_THRESHOLD = 0.8
COVERAGE_SCHEMA_VERSION = 1


class CoverageTargets(BaseModel):
    """@brief 调用方给出的映射目标清单（世界目录/品类集，profile 不自行加载）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    habit_response_keywords: tuple[str, ...] = ()
    content_genres: tuple[str, ...] = ()
    food_kinds: tuple[str, ...] = ()
    media_kinds: tuple[str, ...] = ()
    # 有场景执行证据的应用别名：只作媒介条目的精确命中，不参与习惯与内容题材的子串匹配。
    app_names: tuple[str, ...] = ()


class MissingEntry(BaseModel):
    """@brief 一条缺失映射：类别与未映射值。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str
    value: str


class PersonaCoverageReport(BaseModel):
    """@brief persona_coverage.json 输出结构。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

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
    App 名只在媒介条目上精确命中；习惯 response 与内容题材不因含 App 名子串算命中。
    """
    checks: list[tuple[str, str, bool]] = []
    for habit in card.habits:
        checks.append(("habit_response", habit.response,
                       _keyword_hit(habit.response, targets.habit_response_keywords)))
    for genre in card.tastes.content_genres:
        checks.append(("content_genre", genre, genre in set(targets.content_genres)))
    for food in card.tastes.food_preferences:
        checks.append(("food_preference", food, _keyword_hit(food, targets.food_kinds)))
    for medium in card.tastes.media:
        checks.append(("media", medium, medium in set(targets.media_kinds + targets.app_names)))
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
