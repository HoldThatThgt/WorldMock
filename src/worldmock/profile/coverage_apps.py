"""@brief 用冻结手机映射证明画像中的应用名称对应可执行行为，并从同一投影派生购买渠道。

场景可选钉定手机配置（phone_simulation_config 与其 sha256）；WorldMock 只读它的投影 PhoneSimulationCoverage。覆盖率
核验取有执行证据的应用别名（verified_app_names）；购买渠道（docs/dev/SPEC-map-and-storefront.md#dayrun-fidelity-按名调用与指名购买-购买渠道app-名解析为成员门店 ，裁
决 S7）由卖家账户→
App（purchase_apps_by_seller）与 App→称呼（app_display_names）派生（purchase_channels）：channel_id 取 App 标识，names
取其称呼，成员门店取该 App 的卖家有供给的场景地点。渠道只是门店集合的称呼，不带交付语义；配置已由 sha256 钉进场景与
清单，运行身份不变。核验（verified_phone_config）依次检查身份（语料清单登记与场景钉定的 sha256）、结构、场景身份与执行
证据，任一不成立即 IntegrityError。
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from worldmock.corpora.manifest import CorpusManifest
from worldmock.errors import IntegrityError


class PhoneSimulationCoverage(BaseModel):
    """@brief 手机配置中与人物覆盖核验、购买渠道有关的冻结投影。"""

    model_config = ConfigDict(extra="ignore", frozen=True)

    schema_version: Literal[1]
    scenario_id: str = Field(min_length=1)
    agent_id: int = Field(gt=0)
    timezone: str = Field(min_length=1)
    activity_apps: dict[str, str]
    purchase_apps_by_seller: dict[str, str]
    app_display_names: dict[str, tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class PurchaseChannel:
    """@brief 一个购买渠道（docs/dev/SPEC-map-and-storefront.md#dayrun-fidelity-按名调用与指名购买-购买渠道app-名解析为成员门店 ）：App 是门店的入口，不是交付对象。

    :param channel_id: App 标识（订单载荷与 purchases.jsonl 的 channel_id）。
    :param names: App 的称呼（from 写它时解析为成员门店，去掉空白、不分大小写比较）。
    :param store_location_ids: 成员门店：该 App 的卖家有供给的场景地点，按身份排序。
    """

    channel_id: str
    names: tuple[str, ...]
    store_location_ids: tuple[str, ...]


def verified_phone_config(scenario: Any, corpus_root: Path) -> PhoneSimulationCoverage | None:
    """@brief 场景钉定的手机配置经身份、结构、场景身份与执行证据核验后的投影；场景没有钉定时为 None。

    :raises IntegrityError: 路径越出语料根、语料清单与场景钉定的 sha256 对不上、结构不符、指向别的场景或人物、映射的
        行为没有执行证据，或应用别名为空或重复。
    """
    relative = scenario.phone_simulation_config
    if relative is None:
        return None
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
    aliases = [alias for app_id in _executable_apps(config, scenario)
               for alias in config.app_display_names.get(app_id, ())]
    if any(not alias.strip() for alias in aliases) or len(aliases) != len(set(aliases)):
        logging.error("Phone simulation app names are empty or duplicated")
        raise IntegrityError("Phone simulation app names are invalid")
    return config


def verified_app_names(scenario: Any, corpus_root: Path) -> tuple[str, ...]:
    """@brief 仅返回具有场景执行证据的应用别名（场景没有钉定手机配置时为空）。"""
    config = verified_phone_config(scenario, corpus_root)
    if config is None:
        return ()
    return tuple(sorted(alias for app_id in _executable_apps(config, scenario)
                        for alias in config.app_display_names.get(app_id, ())))


def purchase_channels(config: PhoneSimulationCoverage, scenario: Any) -> tuple[PurchaseChannel, ...]:
    """@brief 从手机配置投影派生购买渠道：每个购买 App 一个渠道，称呼取 app_display_names，成员门店取映射到它的卖家
    在场景中有供给的地点（选入的地点），按 App 标识排序；准入（称呼唯一、不与地点重名、成员门店有供给）由加载时的
    life.load_checks 判定。"""
    sellers: dict[str, set[str]] = {}
    for seller, app_id in config.purchase_apps_by_seller.items():
        sellers.setdefault(app_id, set()).add(seller)
    selected = {location.location_id for location in scenario.locations}
    return tuple(PurchaseChannel(
        channel_id=app_id, names=tuple(config.app_display_names.get(app_id, ())),
        store_location_ids=tuple(sorted({supply.location_id for supply in scenario.supplies
                                         if supply.seller_account_id in owners and supply.location_id in selected})))
        for app_id, owners in sorted(sellers.items()))


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
