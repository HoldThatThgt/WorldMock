"""@brief 完整环境纯校验与显式画像覆盖校验。"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from pathlib import Path

from worldmock.catalog.errors import RegistrationError
from worldmock.errors import EnvironmentInputError
from worldmock.footprints.assets import atomic_write


def validate_environment(environment_root: Path, *, metadata_loader=None) -> dict:
    """@brief 各输入独立校验并汇总错误，不生成画像或运行资产。"""
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
    """@brief 保存固定错误码和原因，继续检查其他独立输入。"""
    try:
        return operation()
    except (RegistrationError, EnvironmentInputError, OSError, ValueError) as error:
        if isinstance(error, RegistrationError):
            code, reason = error.code, error.message
        elif isinstance(error, EnvironmentInputError):
            relative, code, reason = error.path, error.code, error.reason
        else:
            code, reason = type(error).__name__, str(error)
        logging.error("Environment validation failed for %s: %s", relative, reason)
        item = {"path": relative, "code": code, "reason": reason}
        if item not in errors:
            errors.append(item)
        return None


def validate_persona_coverage(environment_root: Path, *, persona_compiler=None,
                              coverage_out: Path | None = None) -> dict:
    """@brief 完整纯准入后编译唯一人物，并把真实覆盖结果写到显式工作目录。"""
    from worldmock.environment import load_environment

    inputs = load_environment(environment_root)
    return asyncio.run(_persona_coverage(inputs, persona_compiler, coverage_out))


async def _persona_coverage(inputs, persona_compiler, coverage_out):
    """@brief 一份编译结果供覆盖门禁与运行输入警告共同读取。"""
    from worldmock import run_setup
    from worldmock.profile.coverage import require_coverage, validate_coverage
    from worldmock.profile.coverage_targets import build_coverage_targets

    run_setup._configure_process(Path(__file__).resolve().parents[2], inputs.config.model)
    result = (persona_compiler(inputs.persona) if persona_compiler else
              run_setup._compile_persona(
                  inputs.persona, inputs.config.model, inputs.model_metadata, inputs.persona_sha256))
    if inspect.isawaitable(result):
        result = await result
    report = validate_coverage(result.card, build_coverage_targets(inputs))
    identity = inputs.persona.agent_id
    document = {"schema_version": 1, "persona_sha256": inputs.persona_sha256, "threshold": 0.8,
                "agents": [{"agent_id": identity, **report.model_dump()}]}
    destination = Path(coverage_out) if coverage_out else Path(".work/persona_coverage.json")
    atomic_write(destination, (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    warnings = run_setup.creation_inputs(inputs, {identity: result})[1]
    for warning in warnings:
        logging.warning("Input warning %s (agent %s): %s", warning["code"], identity, warning["message"])
    require_coverage(report)
    return {**document, "coverage_path": str(destination), "input_warnings": warnings}
