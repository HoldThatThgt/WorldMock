"""@brief 编译入口：background_story -> 卡片 + 大五 + 世界层动力学（编排，零数值）。

旧模板字段 occupation/routine_preference/consumption_preference/risk_preference
已删除不保留别名（规格 L1661）：输入档案携带任一旧字段即明确报错，不静默
兼容；旧 v1 九参数档案（schema_version=1）同样被 schema 拒绝。
"""

from __future__ import annotations

from typing import Mapping

from worldmock.errors import WorldMockError
from worldmock.profile.extractor import PersonaExtraction, extract_persona
from worldmock.profile.mapper import load_mapping_config, map_world_dynamics
from worldmock.profile.models import (
    LEGACY_TEMPLATE_FIELDS,
    PROMPT_VERSION,
    CompiledPersona,
    Consensus,
    PersonaSource,
    background_story_sha256,
)

#: 编译入口需要的最小人物文档键。
STORY_FIELD = "background_story"


class LegacyTemplateFieldError(WorldMockError):
    """@brief 输入档案携带已删除的旧模板字段：明确报错，不静默兼容。"""


def compile_persona(source: str | Mapping, *,
                    extraction: PersonaExtraction | None = None,
                    config: Mapping | None = None) -> CompiledPersona:
    """@brief 编译：三次一致性抽取 -> 卡片 + 大五 -> 世界层动力学。

    @param source background_story 文本，或含 background_story 的人物文档
    @param extraction 预取的共识结果（None 时真实调用提取层）
    @param config 映射规则表（None 时加载 persona-mapping.toml）
    @return CompiledPersona（schema v2）
    @exception LegacyTemplateFieldError 文档携带旧模板字段
    """
    story = _resolve_story(source)
    if config is None:
        config = load_mapping_config()
    if extraction is None:
        extraction = extract_persona(story)
    return CompiledPersona(
        card=extraction.card,
        big_five=extraction.big_five,
        world_dynamics=map_world_dynamics(extraction.big_five, config),
        source=PersonaSource(
            background_story_sha256=background_story_sha256(story),
            model=extraction.model,
            prompt_version=PROMPT_VERSION,
            consensus=Consensus(runs=extraction.runs,
                                agreement=extraction.agreement),
        ),
        warnings=_warnings(extraction),
    )


def _resolve_story(source: str | Mapping) -> str:
    """@brief 输入归一：文本直用；文档先拒旧模板字段再取 background_story。"""
    if isinstance(source, str):
        story = source.strip()
        if not story:
            raise WorldMockError("background story is empty")
        return story
    legacy = LEGACY_TEMPLATE_FIELDS.intersection(source)
    if legacy:
        raise LegacyTemplateFieldError(
            f"legacy template fields are deleted, merge them into the card: "
            f"{sorted(legacy)}")
    if STORY_FIELD not in source:
        raise WorldMockError(f"agent document must carry {STORY_FIELD}")
    story = str(source[STORY_FIELD]).strip()
    if not story:
        raise WorldMockError("background story is empty")
    return story


def _warnings(extraction: PersonaExtraction) -> tuple[str, ...]:
    """@brief 共识标黄字段（低于 2/3 众数占比）列进 warnings。"""
    if not extraction.warning:
        return ()
    below = sorted(name for name, share in extraction.per_field.items()
                   if share < 2 / 3)
    return (f"field_agreement_below_two_thirds: {below}",)
