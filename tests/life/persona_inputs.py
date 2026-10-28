"""在冻结环境副本的 persona.toml 里改人设表的字段，以及不落盘的人设表副本。"""

import tomllib
from pathlib import Path

import tomlkit

from tests.data_paths import ENVIRONMENT_ROOT

#: 没有缺省值、缺了就拒绝加载的人设表字段（点分路径）；数组里的字段取第一项。
REQUIRED_PERSONA_FIELDS = (
    "schema_version", "agent_id", "name", "background_story", "home_location", "initial_location",
    "initial_balance_fen", "initial_relaxation_need", "initial_social_need", "social_need_growth_per_hour",
    "chronotype_midsleep", "sleep_need_hours", "wake_window", "leave_home_window", "lunch_window", "dinner_window",
    "bedtime_window", "coping", "identity", "identity.age", "identity.occupation", "identity.living_arrangement",
    "situation", "situation.busy_this_week", "situation.cares_about", "relationships",
    "relationships.0.name_or_appellation", "relationships.0.role", "relationships.0.intimacy",
    "relationships.0.channels", "habits", "habits.0.trigger", "habits.0.response", "habits.0.time_window",
    "tastes", "tastes.content_genres", "tastes.food_preferences", "tastes.media", "body", "body.mass_kg",
    "body.initial_energy_kcal", "body.initial_sleep_debt_hours",
)


def edit_persona(root: Path, mutate) -> Path:
    """用 mutate(document) 改环境副本的人设表（tomlkit 文档，保留 LF 与表结构），返回 persona.toml 的路径。"""
    path = Path(root) / "persona.toml"
    document = tomlkit.parse(path.read_text(encoding="utf-8"))
    mutate(document)
    path.write_bytes(tomlkit.dumps(document).encode("utf-8"))
    return path


def declare(root: Path, **values) -> Path:
    """给环境副本的人设表写顶层字段：时段写成两个小时数的数组，值为 None 表示删掉这个键。"""
    def mutate(document):
        for name, value in values.items():
            if value is None:
                document.pop(name, None)
            else:
                document[name] = list(value) if isinstance(value, tuple) else value

    return edit_persona(root, mutate)


def drop_field(document, dotted: str) -> None:
    """按点分路径删掉人设表里的一个键；数字段是数组下标。"""
    *parents, last = dotted.split(".")
    node = document
    for part in parents:
        node = node[int(part)] if part.isdigit() else node[part]
    del node[last]


def persona_document(**values) -> dict:
    """夹具人设表的普通字典副本，顶层字段按 values 替换，值为 None 表示删掉这个键。"""
    document = tomllib.loads((ENVIRONMENT_ROOT / "persona.toml").read_text(encoding="utf-8"))
    for name, value in values.items():
        if value is None:
            document.pop(name, None)
        else:
            document[name] = list(value) if isinstance(value, tuple) else value
    return document


def persona_with(**values):
    """经真实严格模型校验的人设表：夹具人物加上要验证的顶层字段。"""
    from worldmock.configuration.inputs.models import PersonaConfig

    return PersonaConfig.model_validate(persona_document(**values))
