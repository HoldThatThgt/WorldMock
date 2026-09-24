"""@brief 用冻结手机映射证明画像中的应用名称对应可执行行为。"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from worldmock.corpora.manifest import CorpusManifest
from worldmock.errors import IntegrityError


class PhoneSimulationCoverage(BaseModel):
    """@brief 手机配置中与人物覆盖核验有关的冻结投影。"""

    model_config = ConfigDict(extra="ignore", frozen=True)

    schema_version: Literal[1]
    scenario_id: str = Field(min_length=1)
    agent_id: int = Field(gt=0)
    timezone: str = Field(min_length=1)
    activity_apps: dict[str, str]
    purchase_apps_by_seller: dict[str, str]
    app_display_names: dict[str, tuple[str, ...]]


def verified_app_names(scenario: Any, corpus_root: Path) -> tuple[str, ...]:
    """@brief 仅返回具有场景执行证据的应用别名。"""
    relative = scenario.phone_simulation_config
    if relative is None:
        return ()
    root = Path(corpus_root).resolve()
    path = _validated_path(root, relative)
    content = path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    manifest = CorpusManifest.model_validate(
        json.loads((root / "manifest.json").read_text(encoding="utf-8")))
    entries = [entry for entry in manifest.entries if entry.path == relative]
    if (len(entries) != 1 or entries[0].sha256 != digest
            or digest != scenario.phone_simulation_config_sha256):
        logging.error("Phone simulation config identity is not pinned by the scenario and corpus")
        raise IntegrityError("Phone simulation config identity is invalid")
    try:
        config = PhoneSimulationCoverage.model_validate(json.loads(content))
    except (ValidationError, ValueError) as error:
        logging.exception("Phone simulation config schema is invalid")
        raise IntegrityError("Phone simulation config schema is invalid") from error
    if (config.scenario_id != scenario.scenario_id or config.timezone != scenario.timezone
            or config.agent_id not in {agent.agent_id for agent in scenario.agents}):
        logging.error("Phone simulation config references a different scenario or person")
        raise IntegrityError("Phone simulation config scenario identity is invalid")
    app_ids = _executable_apps(config, scenario)
    aliases = [alias for app_id in app_ids for alias in config.app_display_names.get(app_id, ())]
    if any(not alias.strip() for alias in aliases) or len(aliases) != len(set(aliases)):
        logging.error("Phone simulation app names are empty or duplicated")
        raise IntegrityError("Phone simulation app names are invalid")
    return tuple(sorted(aliases))


def _validated_path(root: Path, relative: str) -> Path:
    """@brief 只读取语料根内的普通文件，不跟随符号链接。"""
    path = Path(relative)
    candidate = root / path
    resolved = candidate.resolve()
    current = candidate
    if path.is_absolute() or ".." in path.parts or root not in resolved.parents or not resolved.is_file():
        logging.error("Phone simulation config path is outside the corpus")
        raise IntegrityError("Phone simulation config path is invalid")
    while current != root:
        if current.is_symlink():
            logging.error("Phone simulation config path contains a symbolic link")
            raise IntegrityError("Phone simulation config path is invalid")
        current = current.parent
    return resolved


def _executable_apps(config: PhoneSimulationCoverage, scenario: Any) -> set[str]:
    """@brief 用实际路线、内容和有库存供给核验每条应用映射。"""
    available = {
        "driving_travel": any(route.mode == "driving" and route.status == "reachable"
                              for route in scenario.routes),
        "watch": bool(scenario.contents),
    }
    if any(not available.get(kind, False) for kind in config.activity_apps):
        logging.error("Phone activity app maps to an unavailable scenario action")
        raise IntegrityError("Phone activity app is not executable")
    sellers = {supply.seller_account_id for supply in scenario.supplies
               if supply.initial_stock > 0}
    if not set(config.purchase_apps_by_seller) <= sellers:
        logging.error("Phone purchase app maps to a seller without available supply")
        raise IntegrityError("Phone purchase app is not executable")
    app_ids = set(config.activity_apps.values()) | set(config.purchase_apps_by_seller.values())
    if any(not config.app_display_names.get(app_id) for app_id in app_ids):
        logging.error("Phone app has no human-readable display name")
        raise IntegrityError("Phone app display name is missing")
    return app_ids
