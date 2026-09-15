"""@brief 使用窗口内 RFC 6901 字段字典无损编码模型可见证据。"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
from collections import Counter
from typing import NoReturn

from jsonpointer import JsonPointerException, resolve_pointer

from worldmock.errors import IntegrityError
from worldmock.footprints.models import canonical_json

ENCODING_FIELD = "observation_value_encoding"
DICTIONARY_FIELD = "observation_value_dictionary"
REFERENCE_FIELD = "value_pointer"
ENCODING_KIND = "rfc6901_field_dictionary"
ENCODING_VERSION = 1
POINTER_RULE = (
    "Each observation data field whose value is an object containing only value_pointer is replaced by the "
    "complete value at that RFC 6901 pointer in this input."
)
_POINTER_PATTERN = re.compile(r"^/observation_value_dictionary/(0|[1-9][0-9]*)/value$")
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")


def encode_model_input(input_data: dict) -> dict:
    """@brief 为一个完整逻辑窗口生成始终显式存在的确定性模型输入表示。"""
    _validate_logical_input(input_data)
    candidates = _collect_candidates(input_data)
    selected = _select_profitable_candidates(candidates)
    encoded = _render_encoding(input_data, candidates, selected)
    literal = _render_encoding(input_data, candidates, ())
    if len(canonical_json(encoded)) >= len(canonical_json(literal)):
        encoded = literal
    decoded = decode_model_input(encoded)
    if canonical_json(decoded) != canonical_json(input_data):
        _invalid("Encoded evidence does not round-trip to its canonical input")
    return encoded


def decode_model_input(encoded_input: dict) -> dict:
    """@brief 严格解析单跳字段引用并恢复完整 canonical 逻辑窗口。"""
    source = copy.deepcopy(encoded_input)
    marker = _validate_encoding_marker(source)
    dictionary = _validate_dictionary(source)
    result = copy.deepcopy(source)
    used, allowed_sites = set(), set()
    for event_index, event in enumerate(_events(result)):
        data = event.get("data")
        if not isinstance(data, dict):
            _invalid("Encoded evidence event data must be an object")
        for field, value in tuple(data.items()):
            if not isinstance(value, dict) or REFERENCE_FIELD not in value:
                continue
            site = ("events", event_index, "data", field)
            allowed_sites.add(site)
            data[field], index = _resolve_value(source, dictionary, event, field, value)
            used.add(index)
    if set(_pointer_sites(source)) != allowed_sites:
        _invalid("Evidence contains a nested or misplaced value pointer")
    if used != set(range(len(dictionary))):
        _invalid("Evidence dictionary contains an unused entry")
    result.pop(ENCODING_FIELD)
    result.pop(DICTIONARY_FIELD)
    if _digest(result) != marker["decoded_input_sha256"]:
        _invalid("Decoded evidence disagrees with its canonical input digest")
    return result


def encode_model_record(record: dict) -> dict:
    """@brief 仅替换记录的模型可见 input，保留未来目标和来源字段原值。"""
    if not isinstance(record, dict) or not isinstance(record.get("input"), dict):
        _invalid("Delivery record requires an object input")
    result = copy.deepcopy(record)
    result["input"] = encode_model_input(record["input"])
    return result


def decode_model_record(record: dict) -> dict:
    """@brief 从实际 LabelKit 行恢复完整逻辑记录。"""
    if not isinstance(record, dict) or not isinstance(record.get("input"), dict):
        _invalid("Encoded delivery record requires an object input")
    result = copy.deepcopy(record)
    result["input"] = decode_model_input(record["input"])
    return result


def _validate_logical_input(input_data: dict) -> None:
    """@brief 拒绝会与唯一传输合同冲突的保留字段和引用形状。"""
    if not isinstance(input_data, dict) or not isinstance(input_data.get("events"), list):
        _invalid("Logical evidence input requires an events list")
    if ENCODING_FIELD in input_data or DICTIONARY_FIELD in input_data:
        _invalid("Logical evidence input uses a reserved encoding field")
    if _pointer_sites(input_data):
        _invalid("Logical evidence input uses the reserved value pointer field")
    _events(input_data)


def _collect_candidates(input_data: dict) -> dict[str, dict]:
    """@brief 收集同字段同完整值的重复 observation 顶层数据。"""
    occurrences = Counter()
    values = {}
    for event_index, event in enumerate(_events(input_data)):
        if event.get("event_type") != "observation":
            continue
        data = event.get("data")
        if not isinstance(data, dict):
            _invalid("Observation data must be an object")
        for field, value in data.items():
            identity = _digest([field, value])
            payload = canonical_json([field, value])
            previous = values.setdefault(identity, (payload, field, copy.deepcopy(value), []))
            if previous[0] != payload:
                _invalid("Observation dictionary digest collision")
            previous[3].append((event_index, field))
            occurrences[identity] += 1
    return {identity: {"field": item[1], "value": item[2], "locations": item[3]}
            for identity, item in values.items() if occurrences[identity] >= 2}


def _select_profitable_candidates(candidates: dict[str, dict]) -> tuple[str, ...]:
    """@brief 按最终数组位置逐项比较真实 canonical 字节收益。"""
    selected = []
    for identity in sorted(candidates):
        candidate = candidates[identity]
        position = len(selected)
        pointer = {REFERENCE_FIELD: f"/{DICTIONARY_FIELD}/{position}/value"}
        entry = {"field": candidate["field"], "sha256": identity, "value": candidate["value"]}
        count = len(candidate["locations"])
        inline_bytes = count * len(canonical_json(candidate["value"]))
        encoded_bytes = count * len(canonical_json(pointer)) + len(canonical_json(entry))
        encoded_bytes += 1 if selected else 0
        if inline_bytes > encoded_bytes:
            selected.append(identity)
    return tuple(selected)


def _render_encoding(input_data: dict, candidates: dict, selected: tuple[str, ...]) -> dict:
    """@brief 生成单一版本 envelope，空字典和共享字典使用同一形状。"""
    result = _canonical_clone(input_data)
    positions = {identity: index for index, identity in enumerate(selected)}
    for identity in selected:
        pointer = {REFERENCE_FIELD: f"/{DICTIONARY_FIELD}/{positions[identity]}/value"}
        for event_index, field in candidates[identity]["locations"]:
            result["events"][event_index]["data"][field] = copy.deepcopy(pointer)
    result[ENCODING_FIELD] = _encoding_marker(input_data)
    result[DICTIONARY_FIELD] = [
        {"field": candidates[item]["field"], "sha256": item, "value": candidates[item]["value"]}
        for item in selected
    ]
    return result


def _encoding_marker(input_data: dict) -> dict:
    """@brief 冻结表示版本、解析规则和解码后完整窗口摘要。"""
    return {"kind": ENCODING_KIND, "schema_version": ENCODING_VERSION, "standard": "RFC 6901",
            "rule": POINTER_RULE, "decoded_input_sha256": _digest(input_data)}


def _validate_encoding_marker(source: dict) -> dict:
    """@brief 验证唯一编码声明，不接受缺省或其他版本。"""
    if not isinstance(source, dict):
        _invalid("Encoded evidence input must be an object")
    marker = source.get(ENCODING_FIELD)
    expected = {"kind": ENCODING_KIND, "schema_version": ENCODING_VERSION,
                "standard": "RFC 6901", "rule": POINTER_RULE}
    if not isinstance(marker, dict) or set(marker) != set(expected) | {"decoded_input_sha256"}:
        _invalid("Evidence encoding marker has an invalid shape")
    if any(marker.get(key) != value for key, value in expected.items()):
        _invalid("Evidence encoding marker does not match the supported contract")
    if not isinstance(marker.get("decoded_input_sha256"), str):
        _invalid("Evidence encoding marker has no canonical input digest")
    if not _SHA256_PATTERN.fullmatch(marker["decoded_input_sha256"]):
        _invalid("Evidence encoding marker has an invalid canonical input digest")
    return marker


def _validate_dictionary(source: dict) -> list[dict]:
    """@brief 验证字典顺序、字段绑定、摘要和保留引用边界。"""
    dictionary = source.get(DICTIONARY_FIELD)
    if not isinstance(dictionary, list):
        _invalid("Evidence dictionary must be an array")
    identities = []
    for entry in dictionary:
        if not isinstance(entry, dict) or set(entry) != {"field", "sha256", "value"}:
            _invalid("Evidence dictionary entry has an invalid shape")
        if not isinstance(entry["field"], str) or not isinstance(entry["sha256"], str):
            _invalid("Evidence dictionary field and digest must be strings")
        if not _SHA256_PATTERN.fullmatch(entry["sha256"]):
            _invalid("Evidence dictionary entry has an invalid digest")
        if _pointer_sites(entry["value"]):
            _invalid("Evidence dictionary value contains a nested value pointer")
        if _digest([entry["field"], entry["value"]]) != entry["sha256"]:
            _invalid("Evidence dictionary entry digest does not match its value")
        identities.append(entry["sha256"])
    if identities != sorted(set(identities)):
        _invalid("Evidence dictionary entries are duplicated or out of order")
    return dictionary


def _resolve_value(source: dict, dictionary: list[dict], event: dict, field: str, value: dict) -> tuple[object, int]:
    """@brief 只在 observation 数据直接字段解析本记录字典的单跳指针。"""
    if event.get("event_type") != "observation" or set(value) != {REFERENCE_FIELD}:
        _invalid("Evidence value pointer is outside an observation data field")
    pointer = value[REFERENCE_FIELD]
    match = _POINTER_PATTERN.fullmatch(pointer) if isinstance(pointer, str) else None
    if match is None:
        _invalid("Evidence value pointer is outside its local dictionary")
    index = int(match.group(1))
    if index >= len(dictionary):
        _invalid("Evidence value pointer index is outside its local dictionary")
    try:
        resolved = resolve_pointer(source, pointer)
    except JsonPointerException as error:
        _invalid(f"Evidence value pointer cannot be resolved: {error}")
    entry = dictionary[index]
    if entry["field"] != field or _digest([field, resolved]) != entry["sha256"]:
        _invalid("Evidence value pointer disagrees with its field or digest")
    return copy.deepcopy(resolved), index


def _events(input_data: dict) -> list[dict]:
    """@brief 返回按原顺序保存的完整事件列表并拒绝错误 envelope。"""
    events = input_data.get("events") if isinstance(input_data, dict) else None
    if not isinstance(events, list) or any(not isinstance(event, dict) for event in events):
        _invalid("Evidence input requires an ordered event object list")
    return events


def _pointer_sites(value: object, path: tuple = ()) -> list[tuple]:
    """@brief 查找所有保留引用字段，供嵌套和错位引用闭集校验。"""
    sites = []
    if isinstance(value, dict):
        if REFERENCE_FIELD in value:
            sites.append(path)
        for key, item in value.items():
            sites.extend(_pointer_sites(item, path + (key,)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            sites.extend(_pointer_sites(item, path + (index,)))
    return sites


def _digest(value: object) -> str:
    """@brief 计算 canonical JSON 的完整 SHA-256。"""
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _canonical_clone(value: object) -> object:
    """@brief 以 canonical JSON 打断 Python 对象别名并保留全部 JSON 值。"""
    return json.loads(canonical_json(value))


def _invalid(message: str) -> NoReturn:
    """@brief 记录并拒绝不完整、越界或被篡改的证据表示。"""
    logging.error("%s", message)
    raise IntegrityError(message)
