"""@brief 验证 LabelKit trace 与模型输入行的成功处理关系。"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import NoReturn

from worldmock.errors import IntegrityError

_EVENT_FIELDS = {"ts", "run_id", "batch_no", "stage", "ev", "record_ids", "payload"}
_RUN_ID = re.compile(r"[0-9a-f]{12}")
_ZERO_COUNTS = ("bad_input", "dropped_dup", "dropped_lowq", "dropped_verify", "failed")


def completed_trace_sha256(path: Path, expected_model_inputs: dict[str, str],
                           config_digest: str, project_digest: str,
                           expected_models: dict[str, str]) -> str:
    """@brief 验证单次成功运行及逐记录最终通过后返回原始 trace 摘要。"""
    raw = _read_trace(path)
    rows = _parse_rows(raw, path.name)
    _validate_envelopes(rows)
    _validate_lifecycle(rows, len(expected_model_inputs), config_digest, project_digest)
    _validate_processing(rows, expected_model_inputs, expected_models)
    return hashlib.sha256(raw).hexdigest()


def _read_trace(path: Path) -> bytes:
    """@brief 读取非空 trace 原始字节，保留哈希输入。"""
    try:
        raw = path.read_bytes()
    except OSError as error:
        _raise_integrity(f"Cannot read LabelKit trace {path.name}: {error}")
    if not raw.strip():
        _raise_integrity("LabelKit trace is empty")
    return raw


def _parse_rows(raw: bytes, name: str) -> list[dict]:
    """@brief 将 trace JSONL 严格解析为对象行。"""
    rows = []
    try:
        for line in raw.decode("utf-8").splitlines():
            row = json.loads(line)
            if not isinstance(row, dict):
                _raise_integrity("LabelKit trace row must be a JSON object")
            rows.append(row)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        _raise_integrity(f"Cannot parse LabelKit trace {name}: {error}")
    if not rows:
        _raise_integrity("LabelKit trace has no events")
    return rows


def _validate_envelopes(rows: list[dict]) -> None:
    """@brief 校验事件字段、类型与全文件唯一运行标识。"""
    run_ids = set()
    for row in rows:
        if set(row) != _EVENT_FIELDS:
            _raise_integrity("LabelKit trace event has an invalid envelope")
        run_ids.add(row["run_id"])
        valid = (
            isinstance(row["ts"], str) and bool(row["ts"])
            and type(row["batch_no"]) is int and row["batch_no"] >= 0
            and isinstance(row["stage"], str) and bool(row["stage"])
            and isinstance(row["ev"], str) and bool(row["ev"])
            and _valid_record_ids(row["record_ids"])
            and isinstance(row["payload"], dict)
        )
        if not valid:
            _raise_integrity("LabelKit trace event has invalid field types")
    if len(run_ids) != 1 or _RUN_ID.fullmatch(next(iter(run_ids), "")) is None:
        _raise_integrity("LabelKit trace must contain one valid run_id")


def _valid_record_ids(value: object) -> bool:
    """@brief 判断记录标识列表是否为非空字符串组成的唯一列表。"""
    return (
        isinstance(value, list)
        and all(isinstance(item, str) and bool(item) for item in value)
        and len(value) == len(set(value))
    )


def _validate_lifecycle(rows: list[dict], expected_count: int,
                        config_digest: str, project_digest: str) -> None:
    """@brief 校验首尾生命周期、成功退出及输入输出数量守恒。"""
    starts = [row for row in rows if row["ev"] == "run.start"]
    ends = [row for row in rows if row["ev"] == "run.end"]
    if len(starts) != 1 or starts[0] is not rows[0] or not _run_event(starts[0]):
        _raise_integrity("LabelKit trace must start with one run.start event")
    start_payload = starts[0]["payload"]
    start_identity = {
        "config_digest": config_digest,
        "project_digest": project_digest,
        "trace_schema_version": 1,
    }
    if any(start_payload.get(key) != value for key, value in start_identity.items()):
        _raise_integrity("LabelKit trace run identity does not match its configuration")
    if not isinstance(start_payload.get("tool_version"), str) or not start_payload["tool_version"]:
        _raise_integrity("LabelKit trace schema version is unsupported")
    if len(ends) != 1 or ends[0] is not rows[-1] or not _run_event(ends[0]):
        _raise_integrity("LabelKit trace must end with one run.end event")
    payload = ends[0]["payload"]
    counts = payload.get("counts")
    if payload.get("exit_code") != 0 or not isinstance(counts, dict):
        _raise_integrity("LabelKit trace did not record a successful run end")
    expected = {"scanned": expected_count, "ingested": expected_count, "emitted": expected_count}
    if any(counts.get(key) != value for key, value in expected.items()):
        _raise_integrity("LabelKit trace counts do not preserve every source record")
    if any(counts.get(key) != 0 for key in _ZERO_COUNTS):
        _raise_integrity("LabelKit trace contains rejected or failed source records")


def _run_event(row: dict) -> bool:
    """@brief 判断事件是否具有运行级事件外形。"""
    return row["stage"] == "run" and row["batch_no"] == 0 and row["record_ids"] == []


def _validate_processing(rows: list[dict], expected_model_inputs: dict[str, str],
                         expected_models: dict[str, str]) -> None:
    """@brief 按事件顺序绑定每轮生成、独立评审、终态裁决与非绑定修复调用。"""
    by_digest = {digest: record_id for record_id, digest in expected_model_inputs.items()}
    if not expected_model_inputs or len(by_digest) != len(expected_model_inputs):
        _raise_integrity("LabelKit model inputs are not uniquely traceable")
    profiles = {record_id: set() for record_id in expected_model_inputs}
    rounds = {record_id: 0 for record_id in expected_model_inputs}
    verdicts = {}
    last_repair_at = -1
    last_verdict_at = -1
    for position, row in enumerate(rows):
        if row["ev"] == "llm.call":
            if _is_output_repair_call(row):
                _validate_repair_call(row, expected_models)
                last_repair_at = position
            else:
                record_id, profile = _model_call_identity(row, by_digest, expected_models)
                if verdicts.get(record_id) == "pass":
                    _raise_integrity("LabelKit trace has a model call after a passing verdict")
                profiles[record_id].add(profile)
        elif row["ev"] == "verify.verdict":
            record_id, verdict = _accept_verdict(row, rounds, profiles)
            verdicts[record_id] = verdict
            last_verdict_at = position
    if set(verdicts) != set(expected_model_inputs) or any(value != "pass" for value in verdicts.values()):
        _raise_integrity("LabelKit trace has no final passing verdict for every source record")
    if any(value for value in profiles.values()):
        _raise_integrity("LabelKit trace has unreviewed model calls after its final verdict")
    if last_repair_at > last_verdict_at:
        _raise_integrity("LabelKit trace has a schema repair call after its final verdict")


def _is_output_repair_call(row: dict) -> bool:
    """@brief 判断模型调用是否为不含源数据的模型输出 schema 修复请求。"""
    texts = _safe_message_texts(row["payload"].get("gen_ai.input.messages"))
    if not texts:
        return False
    if any("[待标注数据] " in text or "[原始数据] " in text for text in texts):
        return False
    return any("[原始输出]" in text for text in texts)


def _validate_repair_call(row: dict, expected_models: dict[str, str]) -> None:
    """@brief 校验非绑定修复调用的传输证据与模型身份；被修复载荷可为任意文本，不做解析。"""
    payload = row["payload"]
    expected_model = expected_models.get(payload.get("profile"))
    if row["stage"] != "llm" or row["record_ids"] or not _valid_model_evidence(payload, expected_model, {"user"}):
        _raise_integrity("LabelKit trace contains an unsuccessful model call")


def _safe_message_texts(messages: object) -> list[str]:
    """@brief 展开可安全解析的消息文本，遇到无效外形时返回空列表。"""
    texts: list[str] = []
    if not isinstance(messages, list):
        return texts
    for message in messages:
        if not isinstance(message, dict):
            return []
        content = message.get("content")
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            texts.extend(part["text"] for part in content
                         if isinstance(part, dict) and isinstance(part.get("text"), str))
    return texts


def _model_call_identity(row: dict, by_digest: dict[str, str],
                         expected_models: dict[str, str]) -> tuple[str, str]:
    """@brief 将一次成功模型调用映射到唯一实际输入及角色。"""
    payload = row["payload"]
    profile = payload.get("profile")
    digest = _model_input_digest(payload)
    expected_model = expected_models.get(profile)
    if row["stage"] != "llm" or row["record_ids"] or not _valid_model_evidence(payload, expected_model, {"system", "user"}):
        _raise_integrity("LabelKit trace contains an unsuccessful model call")
    if profile not in {"default", "judge"} or digest not in by_digest:
        _raise_integrity("LabelKit trace model call is not bound to a source record")
    return by_digest[digest], profile


def _valid_model_evidence(payload: dict, expected_model: str | None,
                          required_input_roles: set[str]) -> bool:
    """@brief 判断 full trace 是否保留零重试请求、响应与用量证据。"""
    model = payload.get("gen_ai.request.model")
    latency = payload.get("latency_ms")
    input_tokens = payload.get("gen_ai.usage.input_tokens")
    output_tokens = payload.get("gen_ai.usage.output_tokens")
    output_messages = payload.get("gen_ai.output.messages")
    input_messages = payload.get("gen_ai.input.messages")
    scalars = (
        payload.get("status") == "ok"
        and payload.get("retries") == 0
        and isinstance(expected_model, str) and model == expected_model
        and type(latency) is int and latency >= 0
        and type(input_tokens) is int and input_tokens >= 0
        and type(output_tokens) is int and output_tokens >= 0
    )
    return bool(
        scalars
        and _valid_messages(input_messages, required_input_roles)
        and _valid_messages(output_messages, {"assistant"})
    )


def _valid_messages(messages: object, required_roles: set[str]) -> bool:
    """@brief 判断 trace 消息保留指定角色及非空文本内容。"""
    if not isinstance(messages, list) or not messages:
        return False
    roles = {message.get("role") for message in messages if isinstance(message, dict)}
    texts = _message_texts(messages)
    return required_roles.issubset(roles) and bool(texts) and all(bool(text) for text in texts)


def _accept_verdict(row: dict, rounds: dict[str, int], profiles: dict[str, set]) -> tuple[str, str]:
    """@brief 校验本轮调用完整后接受顺序递增的评审裁决。"""
    ids, payload = row["record_ids"], row["payload"]
    round_no, verdict = payload.get("round"), payload.get("verdict")
    if row["stage"] != "verify" or len(ids) != 1 or ids[0] not in rounds:
        _raise_integrity("LabelKit trace verdict refers to an invalid source record")
    record_id = ids[0]
    if profiles[record_id] != {"default", "judge"}:
        _raise_integrity("LabelKit trace lacks generation or review before a verdict")
    if type(round_no) is not int or round_no != rounds[record_id] + 1 or verdict not in {"pass", "fail"}:
        _raise_integrity("LabelKit trace verdict sequence is invalid")
    rounds[record_id] = round_no
    profiles[record_id].clear()
    return record_id, verdict


def _model_input_digest(payload: dict) -> str:
    """@brief 从生成或评审提示中提取唯一完整模型输入并计算摘要。"""
    messages = payload.get("gen_ai.input.messages")
    if not isinstance(messages, list):
        _raise_integrity("LabelKit trace model call has no complete request messages")
    candidates = []
    for text in _message_texts(messages):
        if "[待标注数据] " in text:
            candidates.append(_marked_json_digest(text, "[待标注数据] "))
        if "[原始数据] " in text:
            candidates.append(_marked_json_digest(text, "[原始数据] "))
    if len(candidates) != 1:
        _raise_integrity("LabelKit trace model call must contain one complete source input")
    return candidates[0]


def _marked_json_digest(text: str, marker: str) -> str:
    """@brief 解析提示标记后的首个完整 JSON 对象并计算 canonical 摘要。"""
    raw = text.split(marker, 1)[1].lstrip()
    try:
        value, _ = json.JSONDecoder().raw_decode(raw)
    except json.JSONDecodeError as error:
        _raise_integrity(f"LabelKit trace source input is invalid JSON: {error}")
    if not isinstance(value, dict):
        _raise_integrity("LabelKit trace source input must be a JSON object")
    canonical = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _message_texts(messages: list[object]) -> list[str]:
    """@brief 展开 OpenAI 消息中的字符串或文本内容块。"""
    texts = []
    for message in messages:
        if not isinstance(message, dict):
            _raise_integrity("LabelKit trace request message is invalid")
        content = message.get("content")
        if isinstance(content, str):
            texts.append(content)
            continue
        if not isinstance(content, list):
            _raise_integrity("LabelKit trace request content is invalid")
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                texts.append(part["text"])
    return texts


def _raise_integrity(message: str) -> NoReturn:
    """@brief 记录 trace 完整性错误并终止发布。"""
    logging.error("%s", message)
    raise IntegrityError(message)
