"""@brief 多数字人环境的纯加载、输入身份、双维路由与名称引用准入。"""

from __future__ import annotations

import hashlib
import logging
import re
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from pydantic import ValidationError

from worldmock.configuration.inputs.models import (
    LEGACY_TEMPLATE_FIELDS,
    ModelMetadata,
    PersonaConfig,
    RunConfig,
)
from worldmock.configuration.persona.persona_card import persona_card
from worldmock.configuration.persona.persona_text import RenderBudgetExceededError, render_card
from worldmock.foundation.errors import EnvironmentInputError
from worldmock.resource_engine.registration.errors import RegistrationError

_LOGGER = logging.getLogger(__name__)
#: 校验库在错误位置里插入的联合分支与校验器标记（小写类型名后接方括号），不属于人设表的字段路径。
_VALIDATOR_TAG = re.compile(r"[a-z-]+\[.*\]")
_NO_PERSONAS_REASON = "Environment package requires at least one persona file under personas/"


@dataclass(frozen=True)
class LoadedPersona:
    """@brief 单个人设文件的解析模型与原字节身份。"""

    persona: PersonaConfig
    relative_path: str
    toml_text: str
    sha256: str


@dataclass(frozen=True)
class EnvironmentInputs:
    """@brief 已校验多人物环境与分别冻结的输入字节身份。"""

    root: Path
    catalog: Any
    personas: tuple[PersonaConfig, ...]
    persona_by_id: Mapping[int, PersonaConfig]
    persona_paths: Mapping[int, str]
    persona_tomls: Mapping[int, str]
    persona_sha256s: Mapping[int, str]
    config: RunConfig
    config_sha256: str
    model_metadata: Mapping[str, ModelMetadata]
    home_location_ids: Mapping[int, str]
    initial_location_ids: Mapping[int, str]
    workplace_location_ids: Mapping[int, str | None]


def parse_persona_file(path: Path | str) -> tuple[PersonaConfig, str]:
    """@brief 从单个人设文件读取原字节身份，严格检查全部字段和卡片文本汉字上限，不调用模型。"""
    entry = _parse_persona_entry(Path(path))
    return entry.persona, entry.sha256


def parse_personas_dir(personas_dir: Path | str) -> tuple[LoadedPersona, ...]:
    """@brief 按 ASCII 文件名升序扫描 personas/*.toml，校验每份人设与全局唯一性，最终按 agent_id 升序返回。"""
    directory = Path(personas_dir)
    if directory.is_symlink() or not directory.is_dir():
        raise EnvironmentInputError("personas", "PERSONA_INVALID", _NO_PERSONAS_REASON)
    files = [entry for entry in sorted(directory.iterdir(), key=lambda item: item.name)
             if entry.name.endswith(".toml") and entry.name != ".toml"]
    if not files:
        raise EnvironmentInputError("personas", "PERSONA_INVALID", _NO_PERSONAS_REASON)
    loaded, seen_ids, seen_names = [], set(), set()
    for path in files:
        entry = _parse_persona_entry(path)
        _check_persona_uniqueness(entry, seen_ids, seen_names)
        loaded.append(entry)
    return tuple(sorted(loaded, key=lambda item: item.persona.agent_id))


def _check_persona_uniqueness(entry: LoadedPersona, seen_ids: set[int], seen_names: set[str]) -> None:
    """@brief 校验人物编号与去空白后的姓名在全目录内唯一，冲突归属后加载的文件条目。"""
    persona = entry.persona
    if persona.agent_id in seen_ids:
        raise EnvironmentInputError(entry.relative_path, "PERSONA_INVALID",
                                    f"duplicate agent_id: {persona.agent_id}")
    normalized_name = persona.name.strip()
    if normalized_name in seen_names:
        raise EnvironmentInputError(entry.relative_path, "PERSONA_INVALID",
                                    f"duplicate persona name: {normalized_name}")
    seen_ids.add(persona.agent_id)
    seen_names.add(normalized_name)


def _parse_persona_entry(path: Path) -> LoadedPersona:
    """@brief 解析单个人设 TOML 文件并校验卡片汉字预算，错误归属 personas/<filename>。"""
    relative = f"personas/{path.name}"
    payload, raw_text, digest = _read_toml(path, "Persona", "PERSONA_INVALID", relative)
    legacy = LEGACY_TEMPLATE_FIELDS.intersection(payload)
    if legacy:
        reason = "legacy template fields are deleted, merge them into the card: " + str(sorted(legacy))
        raise EnvironmentInputError(relative, "PERSONA_INVALID", reason)
    try:
        persona = PersonaConfig.model_validate(payload)
    except ValidationError as error:
        raise EnvironmentInputError(relative, "PERSONA_INVALID", _persona_reason(error)) from error
    _require_card_text_within_budget(persona, relative)
    return LoadedPersona(persona=persona, relative_path=relative, toml_text=raw_text, sha256=digest)


def _require_card_text_within_budget(persona: PersonaConfig, report_path: str) -> None:
    """@brief 用整张人设表构造一次卡片文本，超过汉字上限时在加载阶段拒绝；纯计算，不截断。"""
    try:
        render_card(persona_card(persona))
    except RenderBudgetExceededError as error:
        raise EnvironmentInputError(report_path, "PERSONA_INVALID", str(error)) from error


def parse_run_config_file(path: Path | str) -> tuple[RunConfig, str]:
    """@brief 运行配置仅接受真实 TOML 类型和已经裁决的字段。"""
    payload, _, digest = _read_toml(Path(path), "Run config", "RUN_CONFIG_INVALID", "config.toml")
    try:
        return RunConfig.model_validate(payload), digest
    except ValidationError as error:
        raise EnvironmentInputError("config.toml", "RUN_CONFIG_INVALID", _config_reason(error)) from error


def _read_toml(path: Path, label: str, code: str, report_path: str) -> tuple[dict, str, str]:
    """@brief 原文字节只允许 UTF-8 无 BOM 与 LF，文件失败先记录后拒绝。"""
    if not path.is_file() or path.is_symlink():
        raise EnvironmentInputError(report_path, code, f"{label} file is missing or is a symbolic link")
    try:
        content = path.read_bytes()
        if content.startswith(b"\xef\xbb\xbf") or b"\r" in content:
            reason = f"{label} file must be UTF-8 without BOM and use LF line endings"
            raise EnvironmentInputError(report_path, code, reason)
        text = content.decode("utf-8")
        payload = tomllib.loads(text)
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        reason = "Persona file validation failed" if code == "PERSONA_INVALID" else "Run config validation failed"
        raise EnvironmentInputError(report_path, code, reason) from error
    return payload, text, hashlib.sha256(content).hexdigest()


def _persona_reason(error: ValidationError) -> str:
    """@brief 人设表的拒绝原因：只报告第一处，先写出错字段的点分路径；未知字段提示 is not a persona field。"""
    item = error.errors()[0]
    field = ".".join(str(part) for part in item["loc"]
                     if not (isinstance(part, str) and _VALIDATOR_TAG.fullmatch(part)))
    if item["loc"] == ("body", "initial_energy_kcal") and item["type"] == "less_than_equal":
        return "initial energy exceeds maximum energy"
    if item["type"] == "missing":
        return f"{field} is required"
    if item["type"] == "extra_forbidden":
        return f"{field} is not a persona field"
    detail = item["ctx"]["error"] if item["type"] == "value_error" else item["msg"]
    return f"{field}: {detail}"


def _config_reason(error: ValidationError) -> str:
    """@brief 结构版本、运行窗口、压缩阈值与各档案 context_window 的明确失败保留规定的英文原因，其余失败用统一原因。"""
    for item in error.errors():
        if item["loc"] == ("schema_version",):
            return "schema_version is required" if item["type"] == "missing" else f"schema_version: {item['msg']}"
    reasons = (
        "start_time must be timezone-aware", "stop_time must be timezone-aware",
        "start_time and stop_time must fall on whole hours", "run time interval or timezone is invalid",
        "compact_trigger_tokens must be strictly less than int(context_window * 0.85)",
    )
    for reason in reasons:
        if reason in str(error):
            return reason
    if any(len(item["loc"]) == 3 and item["loc"][0] == "models" and item["loc"][2] == "context_window"
           and item["type"] == "greater_than_equal" for item in error.errors()):
        return "context_window must be at least 262144 tokens"
    return "Run config validation failed"


def _validate_agent_overrides(config: RunConfig, personas: tuple[PersonaConfig, ...]) -> None:
    """@brief 校验模型路由中的人物覆盖只引用已加载的真实人物编号。"""
    known = {str(persona.agent_id) for persona in personas}
    unknown = sorted(set(config.model_routing.agent_overrides) - known, key=int)
    if unknown:
        raise EnvironmentInputError("config.toml", "RUN_CONFIG_INVALID",
                                    f"agent_overrides references unknown agent_id: {unknown[0]}")


def load_environment(root: Path | str, *, metadata_loader: Callable | None = None) -> EnvironmentInputs:
    """@brief 全部输入准入后生成内存地图，不请求生成模型或创建运行资产。"""
    from worldmock.resource_engine.registration.admission import validate_environment_entries
    from worldmock.resource_engine.catalog.loader import load_catalog
    from worldmock.configuration.model.model_metadata import load_active_metadata

    directory = Path(root).absolute()
    validate_environment_entries(directory)
    entries = parse_personas_dir(directory / "personas")
    personas = tuple(entry.persona for entry in entries)
    config, config_digest = parse_run_config_file(directory / "config.toml")
    _validate_agent_overrides(config, personas)
    metadata = _load_config_metadata(config, metadata_loader, load_active_metadata)
    catalog = load_catalog(directory / "catalog", config.seed)
    homes, initials, workplaces = _resolve_personas_locations(personas, catalog)
    for home_id in homes.values():
        _require_parcel_station(catalog, home_id)
    return _assemble_inputs(directory, catalog, entries, config, (config_digest, metadata, homes, initials, workplaces))


def _load_config_metadata(
    config: RunConfig, metadata_loader: Callable | None, loader_fn: Callable,
) -> dict[str, ModelMetadata]:
    """@brief 加载激活模型档案元数据，将值校验错误统一归属 config.toml。"""
    try:
        return loader_fn(config, metadata_loader=metadata_loader)
    except (ValueError, OSError) as error:
        raise EnvironmentInputError("config.toml", "RUN_CONFIG_INVALID", str(error)) from error


def _assemble_inputs(
    directory: Path,
    catalog: Any,
    entries: tuple[LoadedPersona, ...],
    config: RunConfig,
    resolved: tuple[str, dict[str, ModelMetadata], dict[int, str], dict[int, str], dict[int, str | None]],
) -> EnvironmentInputs:
    """@brief 构造所有嵌套字典与列表全部只读的 EnvironmentInputs。"""
    config_digest, metadata, homes, initials, workplaces = resolved
    return EnvironmentInputs(
        root=directory,
        catalog=catalog,
        personas=tuple(entry.persona for entry in entries),
        persona_by_id=MappingProxyType({entry.persona.agent_id: entry.persona for entry in entries}),
        persona_paths=MappingProxyType({entry.persona.agent_id: entry.relative_path for entry in entries}),
        persona_tomls=MappingProxyType({entry.persona.agent_id: entry.toml_text for entry in entries}),
        persona_sha256s=MappingProxyType({entry.persona.agent_id: entry.sha256 for entry in entries}),
        config=config,
        config_sha256=config_digest,
        model_metadata=MappingProxyType(dict(metadata)),
        home_location_ids=MappingProxyType(homes),
        initial_location_ids=MappingProxyType(initials),
        workplace_location_ids=MappingProxyType(workplaces),
    )


def _resolve_personas_locations(
    personas: tuple[PersonaConfig, ...], catalog: Any,
) -> tuple[dict[int, str], dict[int, str], dict[int, str | None]]:
    """@brief 对全部人物依次解析居住地、初始地与工作地，返回按人物编号索引的映射。"""
    homes: dict[int, str] = {}
    initials: dict[int, str] = {}
    workplaces: dict[int, str | None] = {}
    for persona in personas:
        home_id, initial_id, work_id = _resolve_persona_locations(persona, catalog)
        homes[persona.agent_id] = home_id
        initials[persona.agent_id] = initial_id
        workplaces[persona.agent_id] = work_id
    return homes, initials, workplaces


def _resolve_persona_locations(persona: PersonaConfig, catalog: Any) -> tuple[str, str, str | None]:
    """@brief 住宿、起点与工作地点均按完整显示名解析到真实地点；人物自己的家必须标了可睡。"""
    from worldmock.resource_engine.catalog.models import channel_key

    by_name = {channel_key(place.display_name): place for place in catalog.locations.values()}
    home = by_name.get(channel_key(persona.home_location))
    if home is None or not home.is_home:
        _LOGGER.error("Invalid persona home_location: %s", persona.home_location)
        raise RegistrationError("HOME_LOCATION_REQUIRED", "persona.home_location")
    if not home.sleepable:
        _LOGGER.error("Persona home_location is not sleepable: %s", persona.home_location)
        raise RegistrationError("HOME_NOT_SLEEPABLE", "persona.home_location")
    initial = by_name.get(channel_key(persona.initial_location))
    if initial is None:
        detail = "Persona location reference is unknown or ambiguous: initial_location"
        _LOGGER.error(detail)
        raise RegistrationError("ENVIRONMENT_REF_UNKNOWN", detail)
    workplace = None
    if persona.work_schedule is not None:
        workplace = by_name.get(channel_key(persona.work_schedule.workplace))
        if workplace is None:
            detail = "Persona location reference is unknown or ambiguous: work_schedule.workplace"
            _LOGGER.error(detail)
            raise RegistrationError("ENVIRONMENT_REF_UNKNOWN", detail)
    return home.location_id, initial.location_id, workplace.location_id if workplace else None


def _require_parcel_station(catalog: Any, home_location_id: str) -> None:
    """@brief 非即时线上商品要求住处同城存在驿站，所有驿站都有对应地点类型内容。"""
    home_city = catalog.locations[home_location_id].city_id
    stations = [place for place in catalog.locations.values() if place.accepts_parcels]
    if any(goods.app_ids and not goods.instant for goods in catalog.goods.values()):
        if not any(station.city_id == home_city for station in stations):
            _LOGGER.error("Home city %s has no parcel station for non-instant app goods", home_city)
            raise RegistrationError("PARCEL_STATION_REQUIRED", f"home city {home_city} has no parcel station")
    for station in stations:
        if not catalog.contents_by_location_kind.get(station.kind):
            _LOGGER.error("Parcel station %s has no location content", station.location_id)
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
