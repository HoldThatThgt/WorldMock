"""@brief 编排认证、统计、盲评准备、解盲和最终不可变评估交付。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping, Sequence

from worldmock.evaluation.adapters import adapt_human_reference, adapt_labelkit, adapt_worldmock
from worldmock.evaluation.aggregate_reference import evaluate_official_reference
from worldmock.evaluation.causality import evaluate_causality
from worldmock.evaluation.decision import decide_result
from worldmock.evaluation.errors import EvaluationError
from worldmock.evaluation.io import atomic_write_json, atomic_write_text, read_json, sha256_file, stable_json_bytes
from worldmock.evaluation.metrics import evaluate_metrics, shared_support
from worldmock.evaluation.models import DatasetView, EvaluationScenario, JsonObject
from worldmock.evaluation.reporting import ReportBundle, render_report, write_charts
from worldmock.evaluation.review import create_review_material, ingest_reviews, write_review_material
from worldmock.evaluation.scenario import load_scenario

_PREPARED_FILES = (
    "input-validation.json",
    "mapping-report.json",
    "causal-validation.json",
    "metrics.json",
    "official-reference.json",
    "blind-review-pack.json",
    "unblinding-key.json",
)


def prepare_evaluation(
    labelkit_root: Path,
    worldmock_root: Path,
    scenario_path: Path,
    output_root: Path,
    human_reference_root: Path | None = None,
) -> JsonObject:
    """@brief 认证输入、计算确定性指标并生成三个隔离盲评包。"""
    _require_new_output(output_root)
    scenario = load_scenario(scenario_path)
    left = adapt_labelkit(labelkit_root, scenario)
    right = adapt_worldmock(worldmock_root, scenario)
    human = adapt_human_reference(human_reference_root, scenario) if human_reference_root else None
    support = shared_support(left, right)
    causality = _causal_results(left, right, support)
    metrics = evaluate_metrics(left, right, scenario, human)
    official = evaluate_official_reference(left, right, scenario)
    validation = _input_validation(scenario_path, scenario, left, right, human)
    mapping = _mapping_report(left, right, human)
    material = create_review_material(left, right, support, scenario)
    outputs = {
        "input-validation.json": validation,
        "mapping-report.json": mapping,
        "causal-validation.json": causality,
        "metrics.json": metrics,
        "official-reference.json": official,
    }
    _write_prepare_outputs(output_root, outputs)
    write_review_material(output_root, material)
    receipt = _prepare_receipt(output_root, scenario_path, support, human is not None)
    atomic_write_json(output_root / "prepare-receipt.json", receipt)
    return receipt


def finalize_evaluation(prepared_root: Path, review_paths: Sequence[Path]) -> JsonObject:
    """@brief 校验准备工件和评审收据，最后写入图表、报告及提交清单。"""
    root = prepared_root.resolve()
    if (root / "manifest.json").exists():
        raise EvaluationError("output_exists", "evaluation output is already finalized")
    receipt = read_json(root / "prepare-receipt.json")
    _validate_receipt(root, receipt)
    if receipt.get("prepare_status") != "awaiting_review":
        raise EvaluationError("not_comparable", "prepared evaluation has no comparable observation support")
    scenario = _load_receipt_scenario(receipt)
    documents = _load_prepared_documents(root)
    reviews = ingest_reviews(review_paths, root, scenario)
    atomic_write_json(root / "review-results.json", reviews)
    result = decide_result(
        documents["metrics"]["support"], documents["causality"], documents["metrics"],
        documents["official"], reviews,
    )
    atomic_write_json(root / "result.json", result)
    charts = write_charts(root, documents["metrics"], documents["official"], reviews)
    report = render_report(_report_bundle(documents, reviews, result, charts))
    atomic_write_text(root / "report.md", report)
    manifest = _final_manifest(root, receipt, scenario, result)
    atomic_write_json(root / "manifest.json", manifest)
    _verify_final_manifest(root, manifest)
    return manifest


def _require_new_output(root: Path) -> None:
    """@brief 要求准备输出路径尚不存在，避免混入旧评估工件。"""
    if root.exists():
        raise EvaluationError("output_exists", "evaluation output directory already exists")


def _causal_results(
    left: DatasetView, right: DatasetView, support: JsonObject
) -> JsonObject:
    """@brief 对两方分别调用生产检查器且不信任输入清单结论。"""
    return {
        "schema_version": "causal-validation-1",
        "checker": "worldmock.evaluation.causal_checks.evaluate_causal_events",
        "support": support,
        "left": evaluate_causality(left, support).to_dict(),
        "right": evaluate_causality(right, support).to_dict(),
    }


def _input_validation(
    scenario_path: Path,
    scenario: EvaluationScenario,
    left: DatasetView,
    right: DatasetView,
    human: DatasetView | None,
) -> JsonObject:
    """@brief 记录场景与输入工件的实际行数和哈希身份。"""
    return {
        "schema_version": "evaluation-input-validation-1",
        "scenario": {
            "path": str(scenario_path.resolve()),
            "sha256": sha256_file(scenario_path.resolve()),
            "scenario_id": scenario.scenario_id,
            "schema_version": scenario.schema_version,
        },
        "left": _view_identity(left),
        "right": _view_identity(right),
        "human_reference": _view_identity(human) if human else None,
    }


def _view_identity(view: DatasetView) -> JsonObject:
    """@brief 序列化一个已经实际认证的数据视图身份。"""
    return {
        "source_kind": view.source_kind,
        "dataset_id": view.dataset_id,
        "run_id": view.run_id,
        "window": _window_dict(view),
        "artifacts": {name: identity.to_dict() for name, identity in sorted(view.artifacts.items())},
        "validation": dict(view.validation),
    }


def _window_dict(view: DatasetView) -> JsonObject:
    """@brief 返回候选声明窗口。"""
    return {
        "start": view.window.start.isoformat(),
        "end": view.window.end.isoformat(),
        "timezone": view.window.timezone,
    }


def _mapping_report(
    left: DatasetView, right: DatasetView, human: DatasetView | None
) -> JsonObject:
    """@brief 保存每一输入的事件守恒、未知类别与自然日覆盖。"""
    return {
        "schema_version": "evaluation-mapping-report-1",
        "left": left.mapping_report(),
        "right": right.mapping_report(),
        "human_reference": human.mapping_report() if human else None,
    }


def _write_prepare_outputs(root: Path, values: Mapping[str, JsonObject]) -> None:
    """@brief 写入盲评前即可完全复算的确定性评估工件。"""
    for name, value in values.items():
        atomic_write_json(root / name, value)


def _prepare_receipt(
    root: Path, scenario_path: Path, support: Mapping[str, Any], has_human: bool
) -> JsonObject:
    """@brief 绑定准备阶段全部输入和输出，供最终阶段拒绝中途改写。"""
    artifacts = _prepared_artifacts(root)
    return {
        "schema_version": "evaluation-prepare-receipt-1",
        "prepare_status": "awaiting_review" if support.get("comparable") else "not_comparable",
        "scenario_path": str(scenario_path.resolve()),
        "scenario_sha256": sha256_file(scenario_path.resolve()),
        "has_human_reference": has_human,
        "prepared_artifacts": artifacts,
    }


def _prepared_artifacts(root: Path) -> JsonObject:
    """@brief 枚举固定准备工件和全部隔离评审包。"""
    paths = [root / name for name in _PREPARED_FILES]
    paths.extend(sorted((root / "review-packets").glob("*.json")))
    return {path.relative_to(root).as_posix(): _artifact_descriptor(root, path) for path in paths}


def _artifact_descriptor(root: Path, path: Path) -> JsonObject:
    """@brief 计算输出工件的相对路径、字节数与 SHA-256。"""
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise EvaluationError("invalid_manifest", "evaluation artifact escapes output root") from exc
    return {"path": relative, "bytes": resolved.stat().st_size, "sha256": sha256_file(resolved)}


def _validate_receipt(root: Path, receipt: JsonObject) -> None:
    """@brief 验证准备收据模式、固定工件哈希和目录洁净度。"""
    required = {
        "schema_version", "prepare_status", "scenario_path", "scenario_sha256",
        "has_human_reference", "prepared_artifacts",
    }
    if set(receipt) != required or receipt.get("schema_version") != "evaluation-prepare-receipt-1":
        raise EvaluationError("invalid_input", "evaluation prepare receipt schema is invalid")
    artifacts = receipt.get("prepared_artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        raise EvaluationError("invalid_input", "evaluation prepare artifact list is invalid")
    for descriptor in artifacts.values():
        _verify_output_artifact(root, descriptor)
    expected = set(artifacts) | {"prepare-receipt.json"}
    actual = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
    if actual != expected:
        raise EvaluationError("invalid_input", "evaluation prepare directory contains unexpected files")


def _verify_output_artifact(root: Path, descriptor: Any) -> None:
    """@brief 复核准备工件路径、大小和哈希。"""
    if not isinstance(descriptor, dict) or set(descriptor) != {"path", "bytes", "sha256"}:
        raise EvaluationError("invalid_input", "prepared artifact descriptor is invalid")
    path = (root / str(descriptor["path"])).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise EvaluationError("invalid_input", "prepared artifact path escapes output") from exc
    if not path.is_file() or path.stat().st_size != descriptor["bytes"]:
        raise EvaluationError("invalid_input", "prepared artifact size changed")
    if sha256_file(path) != descriptor["sha256"]:
        raise EvaluationError("hash_mismatch", "prepared artifact hash changed")


def _load_receipt_scenario(receipt: JsonObject) -> EvaluationScenario:
    """@brief 按收据路径和哈希重新读取评估场景。"""
    path = Path(receipt["scenario_path"])
    if sha256_file(path) != receipt["scenario_sha256"]:
        raise EvaluationError("hash_mismatch", "evaluation scenario hash changed after prepare")
    return load_scenario(path)


def _load_prepared_documents(root: Path) -> dict[str, JsonObject]:
    """@brief 读取最终报告使用的已认证准备工件。"""
    return {
        "validation": read_json(root / "input-validation.json"),
        "mapping": read_json(root / "mapping-report.json"),
        "causality": read_json(root / "causal-validation.json"),
        "metrics": read_json(root / "metrics.json"),
        "official": read_json(root / "official-reference.json"),
    }


def _report_bundle(
    documents: Mapping[str, JsonObject],
    reviews: JsonObject,
    result: JsonObject,
    charts: Sequence[Path],
) -> ReportBundle:
    """@brief 组装中文报告的不可变输入。"""
    return ReportBundle(
        documents["validation"], documents["mapping"], documents["causality"],
        documents["metrics"], documents["official"], reviews, result, charts,
    )


def _final_manifest(
    root: Path,
    receipt: JsonObject,
    scenario: EvaluationScenario,
    result: Mapping[str, Any],
) -> JsonObject:
    """@brief 以当前全部输出哈希派生运行身份并声明最终提交。"""
    paths = sorted(path for path in root.rglob("*") if path.is_file() and path.name != "manifest.json")
    artifacts = {path.relative_to(root).as_posix(): _artifact_descriptor(root, path) for path in paths}
    identity = {
        "scenario_sha256": receipt["scenario_sha256"],
        "artifacts": {name: value["sha256"] for name, value in artifacts.items()},
    }
    run_id = f"evaluation-{hashlib.sha256(stable_json_bytes(identity)).hexdigest()[:24]}"
    return {
        "schema_version": "evaluation-delivery-1",
        "committed": True,
        "run_id": run_id,
        "result_status": result["status"],
        "result_outcome": result["outcome"],
        "window": {
            "start": scenario.window.start.isoformat(),
            "end": scenario.window.end.isoformat(),
            "timezone": scenario.window.timezone,
        },
        "daily_coverage": [value.isoformat() for value in scenario.window.days],
        "artifacts": artifacts,
    }


def _verify_final_manifest(root: Path, manifest: Mapping[str, Any]) -> None:
    """@brief 回读每个最终工件，确认提交清单确实对应落盘内容。"""
    if manifest.get("committed") is not True:
        raise EvaluationError("write_failed", "evaluation manifest is not committed")
    for descriptor in manifest["artifacts"].values():
        _verify_output_artifact(root, descriptor)
    actual = read_json(root / "manifest.json")
    if actual != manifest:
        raise EvaluationError("write_failed", "evaluation manifest changed after write")
