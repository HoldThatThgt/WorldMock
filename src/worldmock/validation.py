"""@brief 完整环境纯校验。"""

from __future__ import annotations

import logging
from pathlib import Path

from worldmock.catalog.errors import RegistrationError
from worldmock.errors import EnvironmentInputError


def validate_environment(environment_root: Path, *, metadata_loader=None) -> dict:
    """@brief 各输入独立校验并汇总错误，不调用生成模型，不创建运行资产。"""
    from worldmock.catalog.admission import validate_environment_entries
    from worldmock.catalog.loader import load_catalog, validate_catalog_files
    from worldmock.environment import parse_persona_file, parse_run_config_file

    root, errors = Path(environment_root).absolute(), []
    _checked("catalog", lambda: validate_environment_entries(root), errors)
    persona = _checked("persona.toml", lambda: parse_persona_file(root / "persona.toml"), errors)
    config = _checked("config.toml", lambda: parse_run_config_file(root / "config.toml"), errors)
    catalog_valid = _checked("catalog", lambda: (validate_catalog_files(root / "catalog"), True)[1], errors)
    catalog = None
    if config is not None:
        _checked("config.toml", lambda: _check_metadata(config[0], metadata_loader), errors)
        if catalog_valid:
            catalog = _checked("catalog", lambda: load_catalog(root / "catalog", config[0].seed), errors)
    if persona is not None and catalog is not None:
        _checked("persona.toml", lambda: _check_locations(persona[0], catalog), errors)
    if errors:
        return {"valid": False, "errors": errors}
    manifest = catalog.manifest
    return {"valid": True, "environment_id": manifest.environment_id,
            "environment_version": manifest.environment_version}


def _check_metadata(config, metadata_loader):
    """@brief 只读服务能力失败归属运行配置，不泄露凭据。"""
    from worldmock.model_endpoint import load_model_metadata

    try:
        return (metadata_loader or load_model_metadata)(config.model)
    except (ValueError, OSError) as error:
        raise EnvironmentInputError("config.toml", "RUN_CONFIG_INVALID", str(error)) from error


def _check_locations(persona, catalog) -> None:
    """@brief 唯一人物引用和驿站要求复用纯加载的真实准入。"""
    from worldmock.environment import _require_parcel_station, _resolve_persona_locations

    _resolve_persona_locations(persona, catalog)
    _require_parcel_station(catalog)


def _checked(relative: str, operation, errors: list):
    """@brief 保存固定错误码和原因，登记失败的原因后接详情，继续检查其他独立输入。"""
    try:
        return operation()
    except (RegistrationError, EnvironmentInputError, OSError, ValueError) as error:
        if isinstance(error, RegistrationError):
            code, reason = error.code, error.message + (f" {error.detail}" if error.detail else "")
        elif isinstance(error, EnvironmentInputError):
            relative, code, reason = error.path, error.code, error.reason
        else:
            code, reason = type(error).__name__, str(error)
        logging.error("Environment validation failed for %s: %s", relative, reason)
        item = {"path": relative, "code": code, "reason": reason}
        if item not in errors:
            errors.append(item)
        return None
