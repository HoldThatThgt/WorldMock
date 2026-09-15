"""@brief 执行独立 LabelKit CLI，再通过语义、训练与规则门禁发布交付。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import NoReturn

from dotenv import dotenv_values

from worldmock.delivery.generation import EXPRESSIONS, create_catalog, generation_fixtures, verify_generation
from worldmock.delivery.replay import replay_windows
from worldmock.delivery.rules import execute_fixture
from worldmock.delivery.semantics import attach_facts, validate_annotation
from worldmock.delivery.training import load_training
from worldmock.delivery.windows import prepare_windows
from worldmock.errors import IntegrityError
from worldmock.footprints.assets import atomic_write
from worldmock.footprints.export import write_jsonl
from worldmock.footprints.models import canonical_json


def deliver(export_dir: Path, destination: Path, project_root: Path, replay_exports: tuple[Path, ...] = ()) -> dict:
    """@brief 任一环节失败保留诊断但不发布成功 manifest，不重用不完整产物。"""
    destination = destination.resolve()
    if destination.exists():
        raise IntegrityError("Delivery destination already exists")
    windows = prepare_windows(export_dir)
    if len({path.resolve() for path in replay_exports}) != len(replay_exports):
        raise IntegrityError("Delivery repeats a counterfactual replay export")
    for replay_dir in replay_exports:
        windows["dreaming"].extend(replay_windows(export_dir, replay_dir))
    destination.mkdir(parents=True)
    try:
        manifest = _execute_delivery(windows, destination, project_root.resolve())
        source_manifest = (export_dir / "manifest.json").read_bytes()
        manifest["source_export"] = {"path": str(export_dir.resolve()),
                                     "manifest_sha256": hashlib.sha256(source_manifest).hexdigest()}
        manifest["replay_exports"] = [_export_reference(path) for path in replay_exports]
        manifest["files"] = _file_inventory(destination)
        atomic_write(destination / "manifest.json", canonical_json(manifest))
        return manifest
    except Exception as error:
        logging.exception("Dataset delivery failed before publication")
        failure = {"status": "failed", "error_type": type(error).__name__}
        atomic_write(destination / "failure.json", canonical_json(failure))
        raise


def _export_reference(path: Path) -> dict:
    """@brief 将完整来源导出绑定到最终交付清单。"""
    digest = hashlib.sha256((path / "manifest.json").read_bytes()).hexdigest()
    return {"path": str(path.resolve()), "manifest_sha256": digest}


def _execute_delivery(windows: dict, destination: Path, project_root: Path) -> dict:
    """@brief 顺序完成真实加工与消费者验证，最终清单只引用通过的成品。"""
    workspace = destination / "labelkit"
    excluded = shutil.ignore_patterns("data", "out", "__pycache__")
    shutil.copytree(project_root / "labelkit-projects", workspace, ignore=excluded)
    (workspace / "data").mkdir()
    (workspace / "out").mkdir()
    receipts, records = {}, []
    for task, rows in windows.items():
        write_jsonl(workspace / "data" / f"{task}.jsonl", rows)
        receipts[task] = run_labelkit(task, workspace, project_root)
        records.extend(verify_process(task, workspace, rows))
    catalog = create_catalog(windows["action"][0])
    write_jsonl(workspace / "data" / "rules-catalog.jsonl", [catalog])
    receipts["rules"] = run_labelkit("rules", workspace, project_root)
    product = verify_generation(workspace / "out")
    fixtures = generation_fixtures(product, catalog, windows["action"][0]["provenance"])
    rule_results = [execute_fixture(fixture, EXPRESSIONS) for fixture in fixtures]
    _, training_receipt = load_training(records, destination / "training")
    outputs = {"training": write_jsonl(destination / "training.jsonl", records),
               "rules": write_jsonl(destination / "rules.jsonl", fixtures)}
    atomic_write(destination / "training-receipt.json", canonical_json(training_receipt))
    atomic_write(destination / "rule-results.json", canonical_json(rule_results))
    atomic_write(destination / "rule-expressions.json", canonical_json(EXPRESSIONS))
    return {"schema_version": 1, "committed": True, "run_id": records[0]["provenance"]["run_id"],
            "source_group_id": records[0]["provenance"]["source_group_id"], "artifacts": outputs,
            "labelkit": receipts, "training_receipt": training_receipt, "rule_results": rule_results,
            "semantic_review": {"method": "LabelKit separate verify calls", "same_model_bias": True}}


def _file_inventory(directory: Path) -> dict:
    """@brief 最终清单绑定所有加工输入、输出、模型评审及真实消费者文件。"""
    files = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise IntegrityError("Delivery contains a symbolic file")
        if path.is_file():
            raw = path.read_bytes()
            files[path.relative_to(directory).as_posix()] = {
                "size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    return files


def run_labelkit(task: str, workspace: Path, project_root: Path) -> dict:
    """@brief 独立依赖环境调用固定 CLI，凭据只通过进程环境传递。"""
    binary = project_root / "vendor/labelkit/.venv/bin/labelkit"
    command = [str(binary), "run", "--config", str(workspace / "config.toml"),
               "--project", str(workspace / f"{task}.toml"), "--strict", "--console", "plain"]
    environment = dict(os.environ)
    credentials = dotenv_values(Path.home() / ".config/worldmock/deepseek.env")
    environment.update({key: value for key, value in credentials.items()
                        if value is not None and key == "DEEPSEEK_API_KEY"})
    if not environment.get("DEEPSEEK_API_KEY"):
        raise IntegrityError("DEEPSEEK_API_KEY is required for LabelKit delivery")
    source_paths = (project_root / "src", project_root / "vendor/labelkit")
    environment["PYTHONPATH"] = os.pathsep.join(str(path) for path in source_paths)
    started = time.monotonic()
    with (workspace / f"{task}.log").open("w") as log:
        result = subprocess.run(command, env=environment, stdout=log, stderr=subprocess.STDOUT, check=False)
    if result.returncode != 0:
        raise IntegrityError(f"LabelKit {task} exited with status {result.returncode}")
    return {"exit_code": result.returncode, "wall_seconds": round(time.monotonic() - started, 3),
            "project_sha256": hashlib.sha256((workspace / f"{task}.toml").read_bytes()).hexdigest(),
            "config_sha256": hashlib.sha256((workspace / "config.toml").read_bytes()).hexdigest()}


def verify_process(task: str, workspace: Path, inputs: list[dict]) -> list[dict]:
    """@brief 验证 target-only process 的行身份和评审，再组装事实。"""
    output = workspace / "out" / f"{task}.jsonl"
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    identities = {record["provenance"]["window_id"] for record in inputs}
    if len(identities) != len(inputs) or len(rows) != len(inputs):
        _raise_integrity("LabelKit process did not preserve every input window")
    verified, seen = {}, set()
    for row in rows:
        line_no, original = _source_record(task, row, inputs, seen)
        if row["_meta"]["verification"]["verdict"] != "pass":
            _raise_integrity("LabelKit semantic verification did not pass")
        semantic = {"target": row["target"]}
        if validate_annotation(semantic, original):
            _raise_integrity("LabelKit output violated semantic or factual constraints")
        record = attach_facts(semantic, original)
        record["provenance"]["annotation_model"] = row["_meta"]["annotation"]["model"]
        record["provenance"]["labelkit_record_id"] = row["_meta"]["id"]
        verified[line_no] = record
    return [verified[line_no] for line_no in range(1, len(inputs) + 1)]


def _source_record(task: str, row: dict, inputs: list[dict], seen: set[int]) -> tuple[int, dict]:
    """@brief 用 LabelKit 来源位置和原始行摘要绑定语义候选。"""
    if set(row) != {"target", "_meta"}:
        _raise_integrity("LabelKit semantic output must contain only target and _meta")
    metadata = row.get("_meta")
    source = metadata.get("source") if isinstance(metadata, dict) else None
    line_no = source.get("line_no") if isinstance(source, dict) else None
    if source is None or source.get("file") != f"{task}.jsonl":
        _raise_integrity("LabelKit output source file does not match the process input")
    if type(line_no) is not int or line_no < 1 or line_no > len(inputs):
        _raise_integrity("LabelKit output source line is outside the process input")
    if line_no in seen:
        _raise_integrity("LabelKit output source line is duplicated")
    original = inputs[line_no - 1]
    expected = hashlib.sha256(canonical_json(original)).hexdigest()[:16]
    if metadata.get("id") != expected:
        _raise_integrity("LabelKit output record identity does not match its source line")
    seen.add(line_no)
    return line_no, original


def _raise_integrity(message: str) -> NoReturn:
    """@brief 记录交付边界错误并抛出统一完整性异常。"""
    logging.error("%s", message)
    raise IntegrityError(message)
