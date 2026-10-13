"""@brief 只读已冻结素材认证目录浏览，外部原始输入不参与导出。"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from worldmock.catalog.identity import OPTIONAL_CATALOG_FILES, REQUIRED_CATALOG_FILES, _update_frame
from worldmock.catalog.loader import parse_catalog_lines
from worldmock.catalog.models import App, Content, Goods, Location, TravelMethod
from worldmock.errors import IntegrityError
from worldmock.footprints.display_wording import DisplayNames, display_names
from worldmock.footprints.models import AssetRef, canonical_json

#: 冻结素材里不随目录清单变化的四个名字。
FIXED_ENVIRONMENT_ASSETS = frozenset({"persona.toml", "config.toml", "map.json", "catalog/manifest.json"})
#: 显示名称读的五份目录与各自的行模型，次序即 display_names 的入参次序；出行方式文件可选。
_NAME_CATALOGS = (("app-catalog.jsonl", App), ("content-catalog.jsonl", Content), ("location.jsonl", Location),
                  ("merchant-catalog.jsonl", Goods), ("travel-method.jsonl", TravelMethod))


def frozen_display_names(root: Path, frozen: dict) -> DisplayNames:
    """@brief 从运行目录或导出目录的冻结素材建显示名称表，与导出认证同一套读取与登记时同一套行模型。

    @param root 运行目录或导出目录，素材在其 assets/<sha256>。
    @param frozen 运行清单（导出时是 frozen_run_manifest），取 environment_assets。
    @throws IntegrityError 素材缺失、字节或集合与清单不符，或某一行不合当前的行模型（带文件名与行号）。
    """
    inputs, _ = _frozen_inputs(root, frozen["environment_assets"])
    rows = [parse_catalog_lines(inputs.get(f"catalog/{name}", b""), name, model) for name, model in _NAME_CATALOGS]
    return display_names(*rows)


def validate_frozen_environment(root: Path, frozen: dict, activities: list[dict], events: list[dict]) -> None:
    """@brief 核对冻结人设、配置和目录文件，并用实际内容索引认证空内容浏览。"""
    inputs, manifest = _frozen_inputs(root, frozen["environment_assets"])
    for name in ("persona", "config"):
        if hashlib.sha256(inputs[f"{name}.toml"]).hexdigest() != frozen[f"{name}_sha256"]:
            _invalid("Frozen environment input identity disagrees with its run manifest")
    identity = frozen["environment_identity"]
    for key in ("environment_id", "environment_version", "rules_version", "catalog_content_sha256"):
        if manifest[key] != identity[key]:
            _invalid("Frozen catalog identity disagrees with its run manifest")
    for record in manifest["files"]:
        raw = inputs[f"catalog/{record['path']}"]
        if len(raw) != record["size_bytes"] or hashlib.sha256(raw).hexdigest() != record["sha256"]:
            _invalid("Frozen catalog file identity is invalid")
    if hashlib.sha256(inputs["map.json"]).hexdigest() != identity["map_sha256"]:
        _invalid("Frozen map identity disagrees with its run manifest")
    _validate_catalog_digest(inputs, manifest)
    apps = {row["app_id"]: row for row in _rows(inputs["catalog/app-catalog.jsonl"])}
    contents = _rows(inputs["catalog/content-catalog.jsonl"])
    methods = {row["activity_id"]: row["travel_method_id"] for row in activities}
    rules = {row["rule_id"] for row in _rows(inputs.get("catalog/callbacks.jsonl", b"")) if "rule_id" in row}
    _validate_app_sources(events, apps, methods, rules)
    _validate_browse(activities, apps, contents)


def _frozen_inputs(root: Path, assets: dict) -> tuple[dict, dict]:
    """@brief 先读冻结清单：素材名字恰为四个固定名字加文件表登记的每个目录文件，文件表不缺必需文件也不出闭集。"""
    if "catalog/manifest.json" not in assets:
        _invalid("Frozen environment asset set is invalid")
    manifest = json.loads(_read_input(root, assets["catalog/manifest.json"]))
    listed = {record["path"] for record in manifest["files"]}
    if set(assets) != FIXED_ENVIRONMENT_ASSETS | {f"catalog/{relative}" for relative in listed}:
        _invalid("Frozen environment asset set is invalid")
    if not REQUIRED_CATALOG_FILES <= listed <= REQUIRED_CATALOG_FILES | OPTIONAL_CATALOG_FILES:
        _invalid("Frozen catalog file set is invalid")
    return {name: _read_input(root, value) for name, value in assets.items()}, manifest


def _read_input(root: Path, value: dict) -> bytes:
    """@brief 素材只按摘要定位，读取时再次核对实际字节。"""
    reference = AssetRef.model_validate(value)
    path = root / "assets" / reference.sha256
    if not path.is_file() or path.is_symlink():
        _invalid("Frozen environment asset is missing")
    raw = path.read_bytes()
    if len(raw) != reference.size_bytes or hashlib.sha256(raw).hexdigest() != reference.sha256:
        _invalid("Frozen environment asset identity is invalid")
    return raw


def _rows(raw: bytes) -> list[dict]:
    """@brief 已认证目录行保持原始 JSONL 对象，不创建推测分类。"""
    return [json.loads(line) for line in raw.splitlines()]


def _validate_browse(activities: list[dict], apps: dict, contents: list[dict]) -> None:
    """@brief 空内容只允许真实平台空显式索引与声明的目录浏览时长。"""
    for activity in activities:
        if activity["activity_type"] != "interact" or activity["data"].get("content_id") is not None:
            continue
        app_id = activity["data"].get("app_id")
        app = apps.get(app_id)
        if (app is None or any(app_id in content.get("app_ids", ()) for content in contents)
                or app.get("browse_duration_seconds") is None
                or activity["planned_duration_seconds"] != app["browse_duration_seconds"]):
            _invalid("Directory browse disagrees with its frozen app content contract")


def _invalid(message: str) -> None:
    """@brief 冻结身份故障阻止投影消费。"""
    raise IntegrityError(message)


def _validate_catalog_digest(inputs: dict, manifest: dict) -> None:
    """@brief 冻结目录按文件表登记的全部文件使用发布层同一分帧摘要，不另造内容身份算法。"""
    digest = hashlib.sha256()
    for relative in sorted(record["path"] for record in manifest["files"]):
        _update_frame(digest, relative, inputs[f"catalog/{relative}"])
    payload = dict(manifest)
    payload.pop("catalog_content_sha256")
    _update_frame(digest, "manifest.json", canonical_json(payload))
    if digest.hexdigest() != manifest["catalog_content_sha256"]:
        _invalid("Frozen catalog content digest is invalid")


def _validate_app_sources(events: list[dict], apps: dict, methods: dict, rules: set[str]) -> None:
    """@brief 系统应用事实匹配冻结真实应用的角色与绑定，回调足迹匹配冻结的回调规则。

    导航的 app 是绑定了那趟出行的出行方式的功能 app，下单的 app 是内容 app，支付的 app 是绑定了购买的功能 app
    或内容 app（直付）；「支付成功」的 app 同支付，订单通知的 app 是内容 app；回调足迹的规则身份是冻结的
    callbacks.jsonl 里登记的规则，没有冻结这份文件时不得有回调足迹。

    :param methods: 活动身份到它保存的出行方式身份（非出行活动为 None）。
    :param rules: 冻结的回调规则身份；没有冻结 callbacks.jsonl 时为空。
    """
    for event in events:
        data = event["data"]
        if event["event_type"] != "environment_event" or data.get("kind") not in {
                "app_touch", "app_foreground", "payment_notification", "order_notification",
                "app_push"} or "app_id" not in data:
            continue
        app = apps.get(data["app_id"])
        if app is None:
            _invalid("System app event references an unregistered frozen app")
        if data["kind"] == "app_touch" and data["rule_id"] not in rules:
            _invalid("App touch step disagrees with its source")
        content = app["role"] == "content"
        pays = content or app["role"] == "function" and "purchase" in app.get("actions", ())
        if data["kind"] == "app_foreground":
            navigates = (app["role"] == "function"
                         and methods.get(event.get("activity_id")) in app.get("travel_method_ids", ()))
            if not {"navigation": navigates, "ordering": content, "payment": pays}[data["step"]]:
                _invalid("Foreground app disagrees with its frozen app role or binding")
        if data["kind"] == "payment_notification" and not pays:
            _invalid("Payment notification disagrees with its frozen app")
        if data["kind"] == "order_notification" and not content:
            _invalid("Order notification disagrees with its frozen content app")
