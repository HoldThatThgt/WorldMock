"""@brief 只读已冻结素材认证目录浏览，外部原始输入不参与导出。"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from worldmock.catalog.identity import OPTIONAL_CATALOG_FILES, REQUIRED_CATALOG_FILES, _update_frame
from worldmock.errors import IntegrityError
from worldmock.footprints.models import AssetRef, canonical_json

#: 冻结素材里不随目录清单变化的四个名字。
FIXED_ENVIRONMENT_ASSETS = frozenset({"persona.toml", "config.toml", "map.json", "catalog/manifest.json"})


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
    _validate_app_sources(events, apps)
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


def _validate_app_sources(events: list[dict], apps: dict) -> None:
    """@brief 系统应用事实匹配冻结真实应用，自动功能触点匹配声明具体动作。"""
    for event in events:
        data = event["data"]
        if event["event_type"] != "environment_event" or data.get("kind") not in {
                "app_touch", "payment_notification", "app_push"} or "app_id" not in data:
            continue
        app = apps.get(data["app_id"])
        if app is None:
            _invalid("System app event references an unregistered frozen app")
        if data["kind"] == "app_touch" and data["rule_id"] is None:
            if app["role"] != "function" or data["action"] not in app["actions"]:
                _invalid("Automatic app touch disagrees with its frozen function app")
        if data["kind"] == "payment_notification" and (app["role"] != "function"
                or "purchase" not in app["actions"]):
            _invalid("Payment notification disagrees with its frozen function app")
