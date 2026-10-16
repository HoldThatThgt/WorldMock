"""@brief 严格 JSON 输入、原子输出和文件身份校验。"""

from __future__ import annotations

from worldmock.footprints.assets import atomic_write
from worldmock.footprints.models import canonical_json, is_sha256
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from worldmock.evaluation.errors import EvaluationError
from worldmock.evaluation.models import ArtifactIdentity, JsonObject


def sha256_file(path: Path) -> str:
    """@brief 流式计算文件 SHA-256。"""
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise EvaluationError("missing_artifact", f"cannot read artifact: {path}") from exc
    return digest.hexdigest()


def read_json(path: Path) -> JsonObject:
    """@brief 严格读取一个 JSON 对象。"""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationError("invalid_json", f"cannot read JSON object: {path}") from exc
    if not isinstance(value, dict):
        raise EvaluationError("invalid_json", f"JSON root must be an object: {path}")
    return value


def read_jsonl(path: Path) -> list[JsonObject]:
    """@brief 严格读取 JSONL，拒绝空行和非对象。"""
    return list(iter_jsonl(path))


def iter_jsonl(path: Path) -> Iterator[JsonObject]:
    """@brief 严格逐行读取 JSONL，拒绝空行和非对象；一次只解析一行，供大文件流式使用。"""
    try:
        handle = path.open(encoding="utf-8")
    except OSError as exc:
        raise EvaluationError("missing_artifact", f"cannot open JSONL artifact: {path}") from exc
    with handle:
        for line_number, line in enumerate(handle, start=1):
            yield _parse_jsonl_row(line, path, line_number)


def _parse_jsonl_row(line: str, path: Path, line_number: int) -> JsonObject:
    """@brief 解析单行 JSONL 对象。"""
    if not line.strip():
        raise EvaluationError("invalid_jsonl", f"blank JSONL row at {path}:{line_number}")
    try:
        value = json.loads(line)
    except json.JSONDecodeError as exc:
        raise EvaluationError("invalid_jsonl", f"invalid JSONL row at {path}:{line_number}") from exc
    if not isinstance(value, dict):
        raise EvaluationError("invalid_jsonl", f"JSONL row must be an object at {path}:{line_number}")
    return value


def verify_artifact(path: Path, descriptor: Mapping[str, Any]) -> ArtifactIdentity:
    """@brief 核验文件行数、字节数和 SHA-256。"""
    expected_hash = descriptor.get("sha256")
    expected_rows = descriptor.get("rows")
    if not isinstance(expected_hash, str) or len(expected_hash) != 64:
        raise EvaluationError("invalid_manifest", "artifact sha256 must be a 64-character string")
    if expected_rows is not None and (isinstance(expected_rows, bool) or not isinstance(expected_rows, int)):
        raise EvaluationError("invalid_manifest", "artifact rows must be an integer")
    actual_hash = sha256_file(path)
    if actual_hash != expected_hash:
        raise EvaluationError("hash_mismatch", f"artifact SHA-256 mismatch: {path}")
    rows = _count_rows(path) if expected_rows is not None else None
    if rows != expected_rows:
        raise EvaluationError("row_mismatch", f"artifact row count mismatch: {path}")
    return ArtifactIdentity(path.resolve(), rows, actual_hash, path.stat().st_size)


def _count_rows(path: Path) -> int:
    """@brief 计算物理行数。"""
    try:
        with path.open("rb") as handle:
            return sum(1 for _ in handle)
    except OSError as exc:
        raise EvaluationError("missing_artifact", f"cannot count artifact rows: {path}") from exc


def stable_json_bytes(value: Any) -> bytes:
    """@brief 生成确定性的 UTF-8 JSON。"""
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, default=_json_default, allow_nan=False)
    return (text + "\n").encode("utf-8")


def _json_default(value: Any) -> Any:
    """@brief 序列化时间、路径和不可变集合。"""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def atomic_write_json(path: Path, value: Any) -> None:
    """@brief 原子写入确定性 JSON。"""
    write_artifact(path, stable_json_bytes(value))


def atomic_write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    """@brief 原子写入确定性 JSONL。"""
    payload = b"".join(
        canonical_json(row) + b"\n"
        for row in rows
    )
    write_artifact(path, payload)


def atomic_write_text(path: Path, text: str) -> None:
    """@brief 原子写入 UTF-8 文本。"""
    write_artifact(path, text.encode("utf-8"))





def write_artifact(path: Path, payload: bytes) -> None:
    """@brief 共用字节原子发布并保留评估错误分类。"""
    try:
        atomic_write(path, payload)
    except OSError as error:
        raise EvaluationError("write_failed", f"cannot write evaluation artifact: {path}") from error
