"""@brief 完整单人环境的纯加载、输入身份和名称引用准入。"""

from __future__ import annotations

import hashlib
import logging
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from pydantic import ValidationError

from worldmock.catalog.errors import RegistrationError
from worldmock.errors import EnvironmentInputError
from worldmock.input_models import ModelMetadata, PersonaConfig, RunConfig

_LOGGER = logging.getLogger(__name__)
from worldmock.input_models import LEGACY_TEMPLATE_FIELDS


@dataclass(frozen=True)
class EnvironmentInputs:
    """@brief 已校验单人环境与分别冻结的输入字节身份。"""

    root: Path
    catalog: Any
    persona: PersonaConfig
    config: RunConfig
    persona_sha256: str
    config_sha256: str
    model_metadata: ModelMetadata
    home_location_id: str
    initial_location_id: str
    workplace_location_id: str | None


def parse_persona_file(path: Path | str) -> tuple[PersonaConfig, str]:
    """@brief 从唯一人设文件读取原字节身份并严格检查正文与初值。"""
    payload, digest = _read_toml(Path(path), "Persona", "PERSONA_INVALID")
    legacy = LEGACY_TEMPLATE_FIELDS.intersection(payload)
    if legacy:
        reason = "legacy template fields are deleted, merge them into the card: " + str(sorted(legacy))
        raise EnvironmentInputError("persona.toml", "PERSONA_INVALID", reason)
    try:
        return PersonaConfig.model_validate(payload), digest
    except ValidationError as error:
        reason = _persona_reason(error)
        raise EnvironmentInputError("persona.toml", "PERSONA_INVALID", reason) from error


def parse_run_config_file(path: Path | str) -> tuple[RunConfig, str]:
    """@brief 运行配置仅接受真实 TOML 类型和已经裁决的字段。"""
    payload, digest = _read_toml(Path(path), "Run config", "RUN_CONFIG_INVALID")
    try:
        return RunConfig.model_validate(payload), digest
    except ValidationError as error:
        reason = _config_reason(error)
        raise EnvironmentInputError("config.toml", "RUN_CONFIG_INVALID", reason) from error


def _read_toml(path: Path, label: str, code: str) -> tuple[dict, str]:
    """@brief 原文字节只允许 UTF-8 无 BOM 与 LF，文件失败先记录后拒绝。"""
    if not path.is_file() or path.is_symlink():
        raise EnvironmentInputError(path.name, code, f"{label} file is missing or is a symbolic link")
    try:
        content = path.read_bytes()
        if content.startswith(b"\xef\xbb\xbf") or b"\r" in content:
            reason = f"{label} file must be UTF-8 without BOM and use LF line endings"
            raise EnvironmentInputError(path.name, code, reason)
        payload = tomllib.loads(content.decode("utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        reason = "Persona file validation failed" if code == "PERSONA_INVALID" else "Run config validation failed"
        raise EnvironmentInputError(path.name, code, reason) from error
    return payload, hashlib.sha256(content).hexdigest()


def _persona_reason(error: ValidationError) -> str:
    """@brief 人物的明确初值与正文失败使用规定原因；模型级的取值错误（作息声明自相矛盾）原样返回英文原因。"""
    if "background_story must contain at least 100 characters" in str(error):
        return "background_story must contain at least 100 characters"
    for item in error.errors():
        if item["loc"] == () and item["type"] == "value_error":
            return str(item["ctx"]["error"])
    if any(item["loc"] == ("body", "initial_energy_kcal") and item["type"] == "less_than_equal"
           for item in error.errors()):
        return "initial energy exceeds maximum energy"
    return "Persona file validation failed"


def _config_reason(error: ValidationError) -> str:
    """@brief 运行窗口与上下文的明确失败保留规定的英文原因。"""
    reasons = (
        "start_time must be timezone-aware", "stop_time must be timezone-aware",
        "start_time and stop_time must fall on whole hours", "run time interval or timezone is invalid",
    )
    for reason in reasons:
        if reason in str(error):
            return reason
    if any(item["loc"] == ("model", "context_window") and item["type"] == "greater_than_equal"
           for item in error.errors()):
        return "context_window must be at least 262144 tokens"
    return "Run config validation failed"


def load_environment(root: Path | str, *, metadata_loader: Callable | None = None) -> EnvironmentInputs:
    """@brief 全部输入准入后生成内存地图，不请求生成模型或创建运行资产。"""
    from worldmock.catalog.admission import validate_environment_entries
    from worldmock.catalog.loader import load_catalog
    from worldmock.model_endpoint import load_model_metadata

    directory = Path(root).absolute()
    validate_environment_entries(directory)
    persona, persona_digest = parse_persona_file(directory / "persona.toml")
    config, config_digest = parse_run_config_file(directory / "config.toml")
    metadata = (metadata_loader or load_model_metadata)(config.model)
    catalog = load_catalog(directory / "catalog", config.seed)
    home, initial, workplace = _resolve_persona_locations(persona, catalog)
    _require_parcel_station(catalog)
    return EnvironmentInputs(directory, catalog, persona, config, persona_digest, config_digest,
                             metadata, home, initial, workplace)


def _resolve_persona_locations(persona: PersonaConfig, catalog: Any) -> tuple[str, str, str | None]:
    """@brief 住宿、起点与工作地点均按完整显示名解析到真实地点；人物自己的家必须标了可睡。"""
    from worldmock.catalog.models import channel_key

    by_name = {channel_key(place.display_name): place for place in catalog.locations.values()}
    home = by_name.get(channel_key(persona.home_location))
    if home is None or not home.is_home:
        raise RegistrationError("HOME_LOCATION_REQUIRED", "persona.home_location")
    if not home.sleepable:
        raise RegistrationError("HOME_NOT_SLEEPABLE", "persona.home_location")
    initial = by_name.get(channel_key(persona.initial_location))
    if initial is None:
        detail = "Persona location reference is unknown or ambiguous: initial_location"
        raise RegistrationError("ENVIRONMENT_REF_UNKNOWN", detail)
    workplace = None
    if persona.work_schedule is not None:
        workplace = by_name.get(channel_key(persona.work_schedule.workplace))
        if workplace is None:
            detail = "Persona location reference is unknown or ambiguous: work_schedule.workplace"
            raise RegistrationError("ENVIRONMENT_REF_UNKNOWN", detail)
    return home.location_id, initial.location_id, workplace.location_id if workplace else None


def _require_parcel_station(catalog: Any) -> None:
    """@brief app 侧商品要求实际驿站，所有驿站都有对应地点类型内容。"""
    stations = [place for place in catalog.locations.values() if place.accepts_parcels]
    if any(goods.app_ids for goods in catalog.goods.values()) and not stations:
        raise RegistrationError("PARCEL_STATION_REQUIRED", "app goods require a parcel station")
    for station in stations:
        if not catalog.contents_by_location_kind.get(station.kind):
            raise RegistrationError("PARCEL_STATION_REQUIRED", station.location_id)


def environment_identity(inputs: EnvironmentInputs) -> dict[str, Any]:
    """@brief 人物和运行配置的身份独立于既有五字段环境身份。"""
    manifest = inputs.catalog.manifest
    return {
        "environment_id": manifest.environment_id,
        "environment_version": manifest.environment_version,
        "rules_version": manifest.rules_version,
        "catalog_content_sha256": manifest.catalog_content_sha256,
        "map_sha256": inputs.catalog.map_sha256,
    }
