"""@brief 已严格校验人设与真实抽取结果的纯画像编译。"""

from __future__ import annotations

import logging
from typing import Mapping

from worldmock.input_models import PersonaConfig

from worldmock.errors import WorldMockError
from worldmock.profile.extractor import PersonaExtraction
from worldmock.profile.mapper import load_mapping_config, map_world_dynamics
from worldmock.profile.models import (
    PROMPT_VERSION,
    CompiledPersona,
    Consensus,
    PersonaSource,
)

def compile_persona(source: PersonaConfig, *,
                    extraction: PersonaExtraction,
                    persona_sha256: str,
                    config: Mapping | None = None) -> CompiledPersona:
    """@brief 编译：三次一致性抽取 -> 卡片 + 大五 -> 世界层动力学。

    @param source 已通过纯准入的人设声明
    @param extraction 预取的共识结果（None 时真实调用提取层）
    @param config 映射规则表（None 时加载 persona-mapping.toml）
    @return 当前版本的 CompiledPersona
    @exception WorldMockError 输入不是严格人设声明
    """
    if not isinstance(source, PersonaConfig):
        logging.error("Persona compilation requires validated persona inputs")
        raise WorldMockError("Persona compilation requires validated persona inputs")
    if config is None:
        config = load_mapping_config()
    return CompiledPersona(
        card=extraction.card,
        big_five=extraction.big_five,
        world_dynamics=map_world_dynamics(extraction.big_five, config),
        source=PersonaSource(
            background_story_sha256=persona_sha256,
            model=extraction.model,
            prompt_version=PROMPT_VERSION,
            consensus=Consensus(runs=extraction.runs,
                                agreement=extraction.agreement),
        ),
        warnings=_warnings(extraction),
    )


def _warnings(extraction: PersonaExtraction) -> tuple[str, ...]:
    """@brief 编译警告：共识标黄字段（低于 2/3 众数占比）一条在前，其后逐条接下班与到家锚点的降级说明。

    没有一致性警告时降级说明照样列出（docs/design/06-agent.md「人物身份与画像」）。
    """
    agreement: tuple[str, ...] = ()
    if extraction.warning:
        below = sorted(name for name, share in extraction.per_field.items()
                       if share < 2 / 3)
        agreement = (f"field_agreement_below_two_thirds: {below}",)
    return (*agreement, *extraction.demotion_notes)
