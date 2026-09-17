"""@brief 画像编译 v2：background_story -> PersonaCard + 大五 + 世界层动力学。"""

from worldmock.profile.compile import (
    LegacyTemplateFieldError,
    compile_persona,
)
from worldmock.profile.coverage import (
    CoverageTargets,
    PersonaCoverageError,
    PersonaCoverageReport,
    require_coverage,
    validate_coverage,
)
from worldmock.profile.extractor import (
    ExtractionError,
    PersonaExtraction,
    extract_persona,
)
from worldmock.profile.mapper import (
    DETERMINISTIC_SOURCE_MODEL,
    load_mapping_config,
    map_world_dynamics,
)
from worldmock.profile.models import (
    SCHEMA_VERSION,
    BigFiveProfile,
    CompiledPersona,
    PersonaCard,
    WorldDynamics,
)
from worldmock.profile.taste import compute_taste_weights
from worldmock.profile.textgen import render_card

__all__ = [
    "DETERMINISTIC_SOURCE_MODEL",
    "SCHEMA_VERSION",
    "BigFiveProfile",
    "CompiledPersona",
    "CoverageTargets",
    "ExtractionError",
    "LegacyTemplateFieldError",
    "PersonaCard",
    "PersonaCoverageError",
    "PersonaCoverageReport",
    "PersonaExtraction",
    "WorldDynamics",
    "compile_persona",
    "compute_taste_weights",
    "extract_persona",
    "load_mapping_config",
    "map_world_dynamics",
    "render_card",
    "require_coverage",
    "validate_coverage",
]
