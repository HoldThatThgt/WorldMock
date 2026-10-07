"""@brief 执行独立 LabelKit CLI，再通过语义、训练与规则门禁发布交付。"""

from __future__ import annotations

from worldmock.footprints.models import sha256_json
import hashlib
import json
import logging
import os
import shutil
import subprocess
import time
import tomllib
from pathlib import Path
from typing import NoReturn

from dotenv import dotenv_values

from worldmock.delivery.evidence_encoding import (
    ENCODING_FIELD,
    decode_model_record,
    encode_model_record,
)
from worldmock.delivery.generation import (
    EXPRESSIONS,
    create_catalog,
    generation_fixtures,
    verify_generation,
)
from worldmock.delivery.replay import replay_windows
from worldmock.delivery.rules import execute_fixture
from worldmock.delivery.semantics import attach_facts, validate_annotation
from worldmock.delivery.trace_validation import completed_trace_sha256
from worldmock.delivery.training import load_training
from worldmock.delivery.windows import _window_identity, prepare_windows
from worldmock.errors import IntegrityError
from worldmock.footprints.assets import atomic_write
from worldmock.footprints.export import write_jsonl
from worldmock.footprints.models import canonical_json
from worldmock.life.models import WORLD_SCHEMA_VERSION

SEMANTIC_TASKS = frozenset({"action", "intent", "dreaming"})


def deliver(export_dir: Path, destination: Path, project_root: Path, replay_exports: tuple[Path, ...] = ()) -> dict:
    """@brief 任一环节失败保留诊断但不发布成功 manifest，不重用不完整产物。"""
    destination = destination.resolve()
    if destination.exists():
        raise IntegrityError("Delivery destination already exists")
    _require_current_world_schema(export_dir)
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
        source = json.loads(source_manifest)
        manifest["environment_identity"] = source["frozen_run_manifest"]["environment_identity"]
        shutil.copyfile(export_dir / "app-usage.jsonl", destination / "app-usage.jsonl")
        manifest["artifacts"]["app_usage"] = source["artifacts"]["app_usage"]
        manifest["replay_exports"] = [_export_reference(path) for path in replay_exports]
        manifest["files"] = _file_inventory(destination)
        atomic_write(destination / "manifest.json", canonical_json(manifest))
        return manifest
    except Exception as error:
        logging.exception("Dataset delivery failed before publication")
        failure = {"status": "failed", "error_type": type(error).__name__}
        atomic_write(destination / "failure.json", canonical_json(failure))
        raise


def _require_current_world_schema(export_dir: Path) -> None:
    """@brief 构造窗口前核对导出最终检查点的世界结构版本等于当前代码（10），旧结构观察不按七层原文解释。"""
    try:
        manifest = json.loads((export_dir / "manifest.json").read_bytes())
        schema_version = manifest["checkpoint"]["state"]["schema_version"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise IntegrityError("Delivery source export has no final checkpoint state") from error
    if schema_version != WORLD_SCHEMA_VERSION:
        raise IntegrityError("Delivery source export world schema version does not match the current code")


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
        write_model_inputs(task, rows, workspace)
        receipts[task] = run_labelkit(task, workspace, project_root, rows)
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
    return {"schema_version": 3, "committed": True, "run_id": records[0]["provenance"]["run_id"],
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


def run_labelkit(task: str, workspace: Path, project_root: Path, inputs: list[dict] | None = None) -> dict:
    """@brief 独立依赖环境调用固定 CLI，凭据只通过进程环境传递。"""
    if task in SEMANTIC_TASKS:
        if inputs is None:
            raise IntegrityError("Semantic LabelKit execution requires its complete logical inputs")
        encoded, _ = _load_source_bundle(task, workspace, trace_required=False)
        _verify_decoded_sources(encoded, inputs)
    elif inputs is not None:
        raise IntegrityError("Non-semantic LabelKit execution cannot receive semantic model inputs")
    binary = (project_root / "vendor/labelkit/.venv/Scripts/labelkit.exe" if os.name == "nt"
              else project_root / "vendor/labelkit/.venv/bin/labelkit")
    command = [str(binary), "run", "--config", str(workspace / "config.toml"),
               "--project", str(workspace / f"{task}.toml"), "--strict", "--console", "plain"]
    environment = dict(os.environ)
    credentials = dotenv_values(Path.home() / ".config/worldmock/deepseek.env")
    pool = credentials.get("DEEPSEEK_NODE_KEYS")
    first_key = pool.split(",")[0].strip() if isinstance(pool, str) else ""
    if not first_key:
        raise IntegrityError("DEEPSEEK_NODE_KEYS is required for LabelKit delivery")
    # labelkit-projects/config.toml 冻结声明 api_key_env = "DEEPSEEK_API_KEY"：
    # 子进程变量名是 labelkit 侧接口，此处只桥接池首 key，不改名。
    environment["DEEPSEEK_API_KEY"] = first_key
    source_paths = (project_root / "src", project_root / "vendor/labelkit")
    environment["PYTHONPATH"] = os.pathsep.join(str(path) for path in source_paths)
    started = time.monotonic()
    with (workspace / f"{task}.log").open("w") as log:
        result = subprocess.run(command, env=environment, stdout=log, stderr=subprocess.STDOUT, check=False)
    if result.returncode != 0:
        raise IntegrityError(f"LabelKit {task} exited with status {result.returncode}")
    trace_sha256 = _bind_source_trace(task, workspace) if task in SEMANTIC_TASKS else None
    receipt = {"exit_code": result.returncode, "wall_seconds": round(time.monotonic() - started, 3),
               "project_sha256": hashlib.sha256((workspace / f"{task}.toml").read_bytes()).hexdigest(),
               "config_sha256": hashlib.sha256((workspace / "config.toml").read_bytes()).hexdigest()}
    if trace_sha256 is not None:
        receipt["trace_sha256"] = trace_sha256
    return receipt


def write_model_inputs(task: str, inputs: list[dict], workspace: Path) -> dict:
    """@brief 以唯一字段字典格式写入 LabelKit 行和可核验来源索引。"""
    if task not in SEMANTIC_TASKS or not inputs:
        raise IntegrityError("Semantic model inputs require a supported task and non-empty records")
    encoded = [encode_model_record(record) for record in inputs]
    sources = [_model_source(task, line_no, row) for line_no, row in enumerate(encoded, 1)]
    data_receipt = write_jsonl(workspace / "data" / f"{task}.jsonl", encoded)
    index_receipt = write_jsonl(workspace / "data" / f"{task}.source-index.jsonl", sources)
    return {"model_inputs": data_receipt, "source_index": index_receipt}


def verify_process(task: str, workspace: Path, inputs: list[dict]) -> list[dict]:
    """@brief 验证 target-only process 的行身份和评审，再组装事实。"""
    output = workspace / "out" / f"{task}.jsonl"
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    encoded, sources = _load_source_bundle(task, workspace, trace_required=True)
    _verify_decoded_sources(encoded, inputs)
    identities = {record["provenance"]["window_id"] for record in inputs}
    if len(identities) != len(inputs) or len(rows) != len(inputs):
        raise IntegrityError("LabelKit process did not preserve every input window")
    verified, seen = {}, set()
    for row in rows:
        line_no, original, source = _source_record(task, row, inputs, sources, seen)
        if row["_meta"]["verification"]["verdict"] != "pass":
            raise IntegrityError("LabelKit semantic verification did not pass")
        semantic = {"target": row["target"]}
        if validate_annotation(semantic, original):
            raise IntegrityError("LabelKit output violated semantic or factual constraints")
        record = attach_facts(semantic, original)
        record["provenance"]["annotation_model"] = row["_meta"]["annotation"]["model"]
        record["provenance"]["labelkit_record_id"] = row["_meta"]["id"]
        record["provenance"]["model_input"] = _published_model_source(source)
        verified[line_no] = record
    return [verified[line_no] for line_no in range(1, len(inputs) + 1)]


def _source_record(task: str, row: dict, inputs: list[dict], sources: list[dict],
                   seen: set[int]) -> tuple[int, dict, dict]:
    """@brief 用 LabelKit 来源位置和原始行摘要绑定语义候选。"""
    if set(row) != {"target", "_meta"}:
        raise IntegrityError("LabelKit semantic output must contain only target and _meta")
    metadata = row.get("_meta")
    source = metadata.get("source") if isinstance(metadata, dict) else None
    line_no = source.get("line_no") if isinstance(source, dict) else None
    if source is None or source.get("file") != f"{task}.jsonl":
        raise IntegrityError("LabelKit output source file does not match the process input")
    if type(line_no) is not int or line_no < 1 or line_no > len(inputs):
        raise IntegrityError("LabelKit output source line is outside the process input")
    if line_no in seen:
        raise IntegrityError("LabelKit output source line is duplicated")
    original = inputs[line_no - 1]
    source = sources[line_no - 1]
    if metadata.get("id") != source["labelkit_record_id"]:
        raise IntegrityError("LabelKit output record identity does not match its source line")
    seen.add(line_no)
    return line_no, original, source


def _model_source(task: str, line_no: int, encoded: dict) -> dict:
    """@brief 从实际编码行重建逻辑窗口、模型摘要和 LabelKit 行身份。"""
    decoded = decode_model_record(encoded)
    if decoded.get("task") != task:
        raise IntegrityError("Encoded model record task does not match its source file")
    window_id = decoded.get("provenance", {}).get("window_id")
    if window_id != _window_identity(task, decoded["input"]):
        raise IntegrityError("Encoded model record has an invalid logical window identity")
    model_record_sha256 = sha256_json(encoded)
    source = {"schema_version": 1, "task": task, "source_line": line_no,
            "window_id": window_id,
            "decoded_input_sha256": encoded["input"][ENCODING_FIELD]["decoded_input_sha256"],
            "model_input_sha256": sha256_json(encoded["input"]),
            "model_record_sha256": model_record_sha256,
            "labelkit_record_id": model_record_sha256[:16],
            "trace_path": f"out/{task}.trace.jsonl"}
    if task in {"action", "intent"}:
        provenance = decoded["provenance"]
        prompt_digest = sha256_json(decoded["input"]["decision_prompt"])
        if provenance.get("decision_prompt_sha256") != prompt_digest:
            raise IntegrityError("Decision prompt source digest disagrees with the recorded request")
        source["decision_prompt_event_id"] = provenance["decision_prompt_event_id"]
        source["decision_prompt_sha256"] = prompt_digest
    return source


def _load_source_bundle(task: str, workspace: Path, trace_required: bool) -> tuple[list[dict], list[dict]]:
    """@brief 读取并重算实际编码行来源索引，按生命周期校验 trace 绑定。"""
    encoded = _read_jsonl(workspace / "data" / f"{task}.jsonl")
    sources = _read_jsonl(workspace / "data" / f"{task}.source-index.jsonl")
    if not encoded or len(encoded) != len(sources):
        raise IntegrityError("Model input source index does not preserve every encoded line")
    trace_sha256 = None
    if trace_required:
        trace_sha256 = _completed_trace_digest(task, workspace, encoded)
    for line_no, (row, source) in enumerate(zip(encoded, sources, strict=True), 1):
        expected = _model_source(task, line_no, row)
        if trace_required:
            expected["trace_sha256"] = trace_sha256
        if source != expected:
            raise IntegrityError("Model input source index disagrees with its encoded line or trace")
    return encoded, sources


def _bind_source_trace(task: str, workspace: Path) -> str:
    """@brief 成功运行后把全部输入行绑定到同一实际完整 trace 摘要。"""
    encoded, sources = _load_source_bundle(task, workspace, trace_required=False)
    digest = _completed_trace_digest(task, workspace, encoded)
    completed = [dict(source, trace_sha256=digest) for source in sources]
    write_jsonl(workspace / "data" / f"{task}.source-index.jsonl", completed)
    return digest


def _completed_trace_digest(task: str, workspace: Path, encoded: list[dict]) -> str:
    """@brief 将完整 trace 绑定到实际配置、项目与全部模型输入。"""
    try:
        config_raw = (workspace / "config.toml").read_bytes()
        project_raw = (workspace / f"{task}.toml").read_bytes()
    except OSError as error:
        raise IntegrityError(f"Cannot read LabelKit trace identity files: {error}")
    return completed_trace_sha256(
        workspace / f"out/{task}.trace.jsonl", _trace_model_inputs(encoded),
        "sha256:" + hashlib.sha256(config_raw).hexdigest(),
        "sha256:" + hashlib.sha256(project_raw).hexdigest(), _trace_models(config_raw),
    )


def _trace_models(raw: bytes) -> dict[str, str]:
    """@brief 从已绑定配置读取生成与独立评审的固定模型名称。"""
    try:
        config = tomllib.loads(raw.decode("utf-8"))
        models = {profile: config["llm"][profile]["model"] for profile in ("default", "judge")}
    except (UnicodeDecodeError, KeyError, TypeError, tomllib.TOMLDecodeError) as error:
        raise IntegrityError(f"Cannot read LabelKit model identities: {error}")
    if not all(isinstance(model, str) and model for model in models.values()):
        raise IntegrityError("LabelKit model identities are invalid")
    return models


def _trace_model_inputs(encoded: list[dict]) -> dict[str, str]:
    """@brief 将实际编码行身份绑定到模型可见 input 的 canonical 摘要。"""
    result = {}
    for row in encoded:
        record_sha256 = sha256_json(row)
        input_sha256 = sha256_json(row["input"])
        result[record_sha256[:16]] = input_sha256
    if len(result) != len(encoded):
        raise IntegrityError("Encoded model record identities are duplicated")
    return result


def _verify_decoded_sources(encoded: list[dict], inputs: list[dict]) -> None:
    """@brief 证明实际模型行逐条恢复为本次内存中的完整 canonical 窗口。"""
    if len(encoded) != len(inputs):
        raise IntegrityError("Encoded model inputs do not preserve every logical window")
    for wire, original in zip(encoded, inputs, strict=True):
        if canonical_json(decode_model_record(wire)) != canonical_json(original):
            raise IntegrityError("Decoded model input differs from its complete logical window")


def _published_model_source(source: dict) -> dict:
    """@brief 将模型输入、实际行和 trace 绑定写入最终训练来源。"""
    result = {key: source[key] for key in (
        "decoded_input_sha256", "model_input_sha256", "model_record_sha256",
        "labelkit_record_id", "source_line", "trace_path", "trace_sha256",
    )}
    for key in ("decision_prompt_event_id", "decision_prompt_sha256"):
        if key in source:
            result[key] = source[key]
    result["trace_path"] = f"labelkit/{source['trace_path']}"
    return result


def _read_jsonl(path: Path) -> list[dict]:
    """@brief 读取 canonical JSONL，缺失或无效行明确终止交付。"""
    try:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    except (OSError, json.JSONDecodeError) as error:
        raise IntegrityError(f"Cannot read model source file {path.name}: {error}")
