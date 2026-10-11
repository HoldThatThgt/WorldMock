"""@brief 完整单人环境的纯加载、输入身份和名称引用准入。"""

from __future__ import annotations

import hashlib
import logging
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from pydantic import ValidationError

from worldmock.catalog.errors import RegistrationError
from worldmock.errors import EnvironmentInputError
from worldmock.input_models import ModelMetadata, PersonaConfig, RunConfig
from worldmock.profile.models import persona_card
from worldmock.profile.textgen import RenderBudgetExceededError, render_card

_LOGGER = logging.getLogger(__name__)
from worldmock.input_models import LEGACY_TEMPLATE_FIELDS

#: 校验库在错误位置里插入的联合分支与校验器标记（小写类型名后接方括号），不属于人设表的字段路径。
_VALIDATOR_TAG = re.compile(r"[a-z-]+\[.*\]")


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
    """@brief 从唯一人设文件读取原字节身份，严格检查人设表的每个字段和卡片文本的汉字上限，不调用模型。"""
    payload, digest = _read_toml(Path(path), "Persona", "PERSONA_INVALID")
    legacy = LEGACY_TEMPLATE_FIELDS.intersection(payload)
    if legacy:
        reason = "legacy template fields are deleted, merge them into the card: " + str(sorted(legacy))
        raise EnvironmentInputError("persona.toml", "PERSONA_INVALID", reason)
    try:
        persona = PersonaConfig.model_validate(payload)
    except ValidationError as error:
        reason = _persona_reason(error)
        raise EnvironmentInputError("persona.toml", "PERSONA_INVALID", reason) from error
    _require_card_text_within_budget(persona)
    return persona, digest


def _require_card_text_within_budget(persona: PersonaConfig) -> None:
    """@brief 用整张人设表构造一次卡片文本，超过汉字上限时在加载阶段拒绝；纯计算，不截断。"""
    try:
        render_card(persona_card(persona))
    except RenderBudgetExceededError as error:
        raise EnvironmentInputError("persona.toml", "PERSONA_INVALID", str(error)) from error


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
    """@brief 人设表的拒绝原因：只报告第一处，先写出错字段的点分路径；初始能量超上限沿用规定原因。

    路径只留字段名、表的键和数组下标，校验库插入的联合分支与校验器标记不写进去。
    缺少必填字段写「<字段> is required」，未知字段写「<字段> is not a persona.toml field」，
    其余写「<字段>: <英文说明>」，说明取校验器自己的原文或类型与范围检查的英文消息。
    """
    item = error.errors()[0]
    field = ".".join(str(part) for part in item["loc"]
                     if not (isinstance(part, str) and _VALIDATOR_TAG.fullmatch(part)))
    if item["loc"] == ("body", "initial_energy_kcal") and item["type"] == "less_than_equal":
        return "initial energy exceeds maximum energy"
    if item["type"] == "missing":
        return f"{field} is required"
    if item["type"] == "extra_forbidden":
        return f"{field} is not a persona.toml field"
    detail = item["ctx"]["error"] if item["type"] == "value_error" else item["msg"]
    return f"{field}: {detail}"


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
