"""@brief 旧修复链和新交付清单的真实性校验。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from pydantic import ValidationError

from worldmock.errors import IntegrityError
from worldmock.evaluation.errors import EvaluationError
from worldmock.evaluation.io import read_json, read_jsonl, sha256_file, verify_artifact
from worldmock.evaluation.models import ArtifactIdentity, EvaluationScenario, JsonObject, TimeWindow, parse_datetime
from worldmock.footprints.models import Checkpoint, canonical_json
from worldmock.footprints.validation import validate_export


@dataclass(frozen=True)
class VerifiedDelivery:
    """@brief 保存校验后的交付路径与身份。"""

    run_id: str
    dataset_id: str
    window: TimeWindow
    event_path: Path
    activity_path: Path
    artifacts: Mapping[str, ArtifactIdentity]
    evidence: Mapping[str, Any]
    accepted_run_ids: tuple[str, ...]


def validate_labelkit_delivery(root: Path, scenario: EvaluationScenario) -> VerifiedDelivery:
    """@brief 验证旧 manifest、原始文件、修复报告和最终审计的完整链。"""
    paths = _labelkit_paths(root)
    manifest = read_json(paths["manifest"])
    _validate_labelkit_manifest(manifest, scenario)
    artifacts = _verify_labelkit_artifacts(paths, manifest, scenario)
    repair = read_json(paths["repair_report"])
    audit = read_json(paths["final_audit"])
    _validate_repair_chain(repair, manifest, artifacts)
    _validate_final_audit(audit, manifest, artifacts)
    evidence = _labelkit_evidence(manifest, repair, audit, artifacts)
    return VerifiedDelivery(
        run_id=str(manifest["run_id"]),
        dataset_id=f"labelkit:{manifest['run_id']}",
        window=scenario.window,
        event_path=paths["repaired_stream"],
        activity_path=paths["repaired_main"],
        artifacts=artifacts,
        evidence=evidence,
        accepted_run_ids=(str(manifest["run_id"]),),
    )


def _labelkit_paths(root: Path) -> dict[str, Path]:
    """@brief 返回冻结的旧交付文件路径。"""
    resolved = root.resolve()
    return {
        "manifest": resolved / "out/white-collar-12-weeks.manifest.json",
        "repair_report": resolved / "repaired/white-collar-12-weeks.repair-report.json",
        "final_audit": resolved / "repaired/white-collar-12-weeks.final-audit.json",
        "repaired_main": resolved / "repaired/white-collar-12-weeks.jsonl",
        "repaired_stream": resolved / "repaired/white-collar-12-weeks.stream.jsonl",
    }


def _validate_labelkit_manifest(manifest: JsonObject, scenario: EvaluationScenario) -> None:
    """@brief 验证旧清单身份和提交状态。"""
    expected = scenario.expected_labelkit
    if manifest.get("artifacts_committed") is not True:
        raise EvaluationError("invalid_input", "LabelKit artifacts are not committed")
    _match(manifest, expected, "run_id", "LabelKit run ID")
    _match(manifest, expected, "delivery_digest", "LabelKit delivery digest")
    if manifest.get("schema_version") != 1:
        raise EvaluationError("invalid_input", "LabelKit manifest schema version must be 1")
    for name in ("main", "stream", "report"):
        if not isinstance(manifest.get(name), dict):
            raise EvaluationError("invalid_input", f"LabelKit manifest {name} descriptor is missing")


def _match(actual: Mapping[str, Any], expected: Mapping[str, Any], key: str, label: str) -> None:
    """@brief 验证冻结身份字段。"""
    if actual.get(key) != expected.get(key):
        raise EvaluationError("identity_mismatch", f"{label} does not match the evaluation scenario")


def _verify_labelkit_artifacts(
    paths: Mapping[str, Path], manifest: JsonObject, scenario: EvaluationScenario
) -> dict[str, ArtifactIdentity]:
    """@brief 核验旧交付原始、修复和审计文件。"""
    raw_main = _manifest_artifact(manifest["main"], paths["manifest"].parent.parent)
    raw_stream = _manifest_artifact(manifest["stream"], paths["manifest"].parent.parent)
    raw_report = _manifest_artifact(manifest["report"], paths["manifest"].parent.parent)
    expected = scenario.expected_labelkit
    _expect_hash(raw_main, expected["raw_main_sha256"], "raw main")
    _expect_hash(raw_stream, expected["raw_stream_sha256"], "raw stream")
    repaired_main = _verify_fixed(paths["repaired_main"], expected["repaired_main_sha256"], expected["main_rows"])
    repaired_stream = _verify_fixed(
        paths["repaired_stream"], expected["repaired_stream_sha256"], expected["stream_rows"]
    )
    repair_report = _verify_fixed(paths["repair_report"], expected["repair_report_sha256"], None)
    final_audit = _verify_fixed(paths["final_audit"], expected["final_audit_sha256"], None)
    return {
        "raw_main": raw_main,
        "raw_stream": raw_stream,
        "raw_report": raw_report,
        "repaired_main": repaired_main,
        "repaired_stream": repaired_stream,
        "repair_report": repair_report,
        "final_audit": final_audit,
    }


def _manifest_artifact(descriptor: Any, root: Path) -> ArtifactIdentity:
    """@brief 按旧清单中的受限路径核验工件。"""
    if not isinstance(descriptor, dict) or not isinstance(descriptor.get("path"), str):
        raise EvaluationError("invalid_input", "LabelKit artifact descriptor is invalid")
    path = Path(descriptor["path"]).resolve()
    _require_within(path, root.resolve())
    return verify_artifact(path, descriptor)


def _require_within(path: Path, root: Path) -> None:
    """@brief 拒绝交付目录之外的清单路径。"""
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise EvaluationError("invalid_manifest", "artifact path escapes the delivery root") from exc


def _verify_fixed(path: Path, digest: Any, rows: Any) -> ArtifactIdentity:
    """@brief 用冻结摘要核验固定路径工件。"""
    return verify_artifact(path, {"sha256": digest, "rows": rows})


def _expect_hash(identity: ArtifactIdentity, expected: Any, label: str) -> None:
    """@brief 验证场景冻结的工件摘要。"""
    if identity.sha256 != expected:
        raise EvaluationError("identity_mismatch", f"LabelKit {label} hash does not match the scenario")


def _validate_repair_chain(
    repair: JsonObject, manifest: JsonObject, artifacts: Mapping[str, ArtifactIdentity]
) -> None:
    """@brief 验证修复报告同时绑定原始和修复文件。"""
    source = repair.get("source")
    repaired = repair.get("repaired")
    if not isinstance(source, dict) or not isinstance(repaired, dict):
        raise EvaluationError("invalid_input", "LabelKit repair report is missing hash sections")
    expected = {
        "main_sha256": artifacts["raw_main"].sha256,
        "stream_sha256": artifacts["raw_stream"].sha256,
    }
    actual = {
        "main_sha256": artifacts["repaired_main"].sha256,
        "stream_sha256": artifacts["repaired_stream"].sha256,
    }
    if source != expected or repaired != actual:
        raise EvaluationError("repair_chain_mismatch", "LabelKit repair hash chain is invalid")
    if repair.get("frames") != manifest["stream"].get("rows"):
        raise EvaluationError("repair_chain_mismatch", "LabelKit repair frame count is invalid")


def _validate_final_audit(
    audit: JsonObject, manifest: JsonObject, artifacts: Mapping[str, ArtifactIdentity]
) -> None:
    """@brief 验证最终审计状态、身份和修复后行数。"""
    if audit.get("status") != "pass" or audit.get("mode") != "full":
        raise EvaluationError("audit_failed", "LabelKit final audit did not pass in full mode")
    if audit.get("findings") != [] or audit.get("finding_counts") != {}:
        raise EvaluationError("audit_failed", "LabelKit final audit contains findings")
    summary = audit.get("summary")
    delivery = summary.get("delivery") if isinstance(summary, dict) else None
    rows = summary.get("rows") if isinstance(summary, dict) else None
    if not isinstance(delivery, dict) or not isinstance(rows, dict):
        raise EvaluationError("audit_failed", "LabelKit final audit summary is incomplete")
    if delivery.get("run_id") != manifest.get("run_id"):
        raise EvaluationError("audit_failed", "LabelKit audit run ID does not match manifest")
    if delivery.get("delivery_digest") != manifest.get("delivery_digest"):
        raise EvaluationError("audit_failed", "LabelKit audit digest does not match manifest")
    expected = (artifacts["repaired_main"].rows, artifacts["repaired_stream"].rows)
    if (rows.get("main_rows"), rows.get("stream_rows")) != expected:
        raise EvaluationError("audit_failed", "LabelKit audit row counts do not match repaired files")


def _labelkit_evidence(
    manifest: JsonObject,
    repair: JsonObject,
    audit: JsonObject,
    artifacts: Mapping[str, ArtifactIdentity],
) -> JsonObject:
    """@brief 组装可写入评估报告的旧交付证据。"""
    return {
        "manifest_committed": True,
        "run_id": manifest["run_id"],
        "delivery_digest": manifest["delivery_digest"],
        "repair_source": repair["source"],
        "repair_output": repair["repaired"],
        "final_audit": {"status": audit["status"], "mode": audit["mode"], "findings": 0},
        "input_events": artifacts["repaired_stream"].rows,
        "input_activities": artifacts["repaired_main"].rows,
    }


def validate_canonical_delivery(
    root: Path, scenario: EvaluationScenario, source_kind: str
) -> VerifiedDelivery:
    """@brief 验证 WorldMock 或真人参照的共同交付清单。"""
    resolved = root.resolve()
    manifest_path = resolved / "manifest.json"
    manifest = read_json(manifest_path)
    _validate_canonical_manifest(manifest, scenario, source_kind)
    artifacts = _verify_canonical_artifacts(resolved, manifest, source_kind)
    window = _manifest_window(manifest)
    checkpoint = _validated_checkpoint(manifest, window) if source_kind == "worldmock" else None
    if source_kind == "worldmock":
        try:
            validate_export(resolved)
        except (IntegrityError, ValueError, KeyError, OSError) as exc:
            raise EvaluationError("invalid_input", str(exc)) from exc
    evidence = {
        "manifest_committed": True,
        "run_id": manifest["run_id"],
        "input_events": artifacts["events"].rows,
        "input_activities": artifacts["activities"].rows,
        "daily_coverage": manifest["daily_coverage"],
        "source_run_ids": list(_source_run_ids(manifest)),
        "parent_run_id": manifest.get("parent_run_id"),
        "checkpoint_id": checkpoint.checkpoint_id if checkpoint else None,
        "complete_export_authenticated": source_kind == "worldmock",
    }
    return VerifiedDelivery(
        run_id=manifest["run_id"],
        dataset_id=f"{source_kind}:{manifest['run_id']}",
        window=window,
        event_path=artifacts["events"].path,
        activity_path=artifacts["activities"].path,
        artifacts=artifacts,
        evidence=evidence,
        accepted_run_ids=_source_run_ids(manifest),
    )


def _validate_canonical_manifest(
    manifest: JsonObject, scenario: EvaluationScenario, source_kind: str
) -> None:
    """@brief 验证共同交付清单的固定模式。"""
    if manifest.get("committed") is not True:
        raise EvaluationError("invalid_input", "delivery manifest is not committed")
    version = 2 if source_kind == "worldmock" else 1
    if manifest.get("schema_version") != version:
        raise EvaluationError("invalid_input", f"delivery schema_version must be {version}")
    if source_kind == "worldmock" and manifest.get("experiment_completed") is not True:
        raise EvaluationError("invalid_input", "WorldMock export must declare experiment_completed")
    if source_kind == "worldmock" and not isinstance(manifest.get("checkpoint"), dict):
        raise EvaluationError("invalid_input", "completed WorldMock export checkpoint is missing")
    if source_kind == "worldmock" and not isinstance(manifest.get("asset_index"), dict):
        raise EvaluationError("invalid_input", "completed WorldMock export asset_index is missing")
    if not isinstance(manifest.get("run_id"), str) or not manifest["run_id"]:
        raise EvaluationError("invalid_input", "delivery run_id is missing")
    sources = _source_run_ids(manifest)
    if manifest["run_id"] not in sources:
        raise EvaluationError("invalid_input", "delivery run_id must be declared in source_run_ids")
    parent = manifest.get("parent_run_id")
    if parent is not None and parent not in sources:
        raise EvaluationError("invalid_input", "parent_run_id must be declared in source_run_ids")
    window = _manifest_window(manifest)
    if window != scenario.window:
        raise EvaluationError("not_comparable", "delivery window does not match scenario")
    coverage = manifest.get("daily_coverage")
    expected = [value.isoformat() for value in scenario.window.days]
    if coverage != expected:
        raise EvaluationError("invalid_input", "daily coverage does not match the declared window")
    artifacts = manifest.get("artifacts")
    required = {"events", "activities", "verified_experiences", "purchases"} if source_kind == "worldmock" else {
        "events", "activities"}
    if not isinstance(artifacts, dict) or set(artifacts) != required:
        raise EvaluationError("invalid_input", "delivery artifact set is invalid")


def _source_run_ids(manifest: JsonObject) -> tuple[str, ...]:
    """@brief 读取恢复分支声明的祖先运行身份。"""
    values = manifest.get("source_run_ids", [])
    if not isinstance(values, list) or not all(isinstance(item, str) and item for item in values):
        raise EvaluationError("invalid_input", "source_run_ids must be a string array")
    if len(values) != len(set(values)):
        raise EvaluationError("invalid_input", "source_run_ids must not contain duplicates")
    parent = manifest.get("parent_run_id")
    if parent is not None and (not isinstance(parent, str) or not parent):
        raise EvaluationError("invalid_input", "parent_run_id must be a non-empty string or null")
    return tuple(values)


def _manifest_window(manifest: JsonObject) -> TimeWindow:
    """@brief 读取共同清单的时间窗口。"""
    value = manifest.get("window")
    if not isinstance(value, dict):
        raise EvaluationError("invalid_input", "delivery window is missing")
    return TimeWindow(
        parse_datetime(value.get("start"), "manifest.window.start"),
        parse_datetime(value.get("end"), "manifest.window.end"),
        str(value.get("timezone", "")),
    )


def _verify_canonical_artifacts(
    root: Path, manifest: JsonObject, source_kind: str
) -> dict[str, ArtifactIdentity]:
    """@brief 核验共同清单中的相对工件路径。"""
    verified: dict[str, ArtifactIdentity] = {}
    for name in ("events", "activities"):
        descriptor = manifest["artifacts"][name]
        if not isinstance(descriptor, dict):
            raise EvaluationError("invalid_input", f"{name} artifact descriptor is invalid")
        relative = descriptor.get("path")
        if not isinstance(relative, str) or Path(relative).is_absolute():
            raise EvaluationError("invalid_manifest", f"{name} artifact path must be relative")
        path = (root / relative).resolve()
        _require_within(path, root)
        verified[name] = verify_artifact(path, descriptor)
    if source_kind == "worldmock":
        verified.update(_verify_checkpoint_assets(root, manifest))
    return verified


def _validated_checkpoint(manifest: JsonObject, window: TimeWindow) -> Checkpoint:
    """@brief 验证清单检查点模式、记录哈希、运行身份和完成时间。"""
    try:
        checkpoint = Checkpoint.model_validate(manifest["checkpoint"])
    except ValidationError as exc:
        raise EvaluationError("invalid_input", "WorldMock checkpoint schema is invalid") from exc
    payload = checkpoint.model_dump(exclude={"record_sha256"})
    if hashlib.sha256(canonical_json(payload)).hexdigest() != checkpoint.record_sha256:
        raise EvaluationError("hash_mismatch", "WorldMock checkpoint record hash is invalid")
    if checkpoint.run_id != manifest["run_id"]:
        raise EvaluationError("identity_mismatch", "WorldMock checkpoint run_id differs from manifest")
    expected_time = int(window.end.timestamp())
    if checkpoint.current_time != expected_time:
        raise EvaluationError("invalid_input", "WorldMock checkpoint is not at the declared window end")
    frozen = manifest.get("frozen_run_manifest")
    if not isinstance(frozen, dict):
        raise EvaluationError("invalid_input", "WorldMock frozen run manifest is missing")
    if hashlib.sha256(canonical_json(frozen)).hexdigest() != checkpoint.manifest_sha256:
        raise EvaluationError("hash_mismatch", "WorldMock checkpoint manifest hash is invalid")
    return checkpoint


def _verify_checkpoint_assets(root: Path, manifest: JsonObject) -> dict[str, ArtifactIdentity]:
    """@brief 核验资产索引和检查点引用的每一个不可变快照。"""
    descriptor = manifest["asset_index"]
    if not isinstance(descriptor, dict) or descriptor.get("path") != "asset-index.jsonl":
        raise EvaluationError("invalid_input", "WorldMock asset_index descriptor is invalid")
    index_path = (root / "asset-index.jsonl").resolve()
    identity = verify_artifact(index_path, descriptor)
    rows = read_jsonl(index_path)
    indexed = {row.get("sha256"): row for row in rows}
    if len(indexed) != len(rows):
        raise EvaluationError("invalid_input", "WorldMock asset_index contains duplicate identities")
    snapshots = manifest["checkpoint"].get("snapshots", {})
    if not isinstance(snapshots, dict) or not snapshots:
        raise EvaluationError("invalid_input", "WorldMock checkpoint snapshots are missing")
    verified: dict[str, ArtifactIdentity] = {"asset_index": identity}
    for name, reference in snapshots.items():
        verified[f"checkpoint_snapshot:{name}"] = _verify_snapshot(root, reference, indexed)
    return verified


def _verify_snapshot(
    root: Path, reference: Any, indexed: Mapping[Any, JsonObject]
) -> ArtifactIdentity:
    """@brief 复核一个检查点快照内容、大小和资产索引身份。"""
    if not isinstance(reference, dict) or indexed.get(reference.get("sha256")) != reference:
        raise EvaluationError("invalid_input", "checkpoint snapshot is absent from asset_index")
    digest = reference["sha256"]
    path = (root / "assets" / digest).resolve()
    _require_within(path, root)
    if sha256_file(path) != digest or path.stat().st_size != reference.get("size_bytes"):
        raise EvaluationError("hash_mismatch", "checkpoint snapshot content is invalid")
    return ArtifactIdentity(path, None, digest, path.stat().st_size)
