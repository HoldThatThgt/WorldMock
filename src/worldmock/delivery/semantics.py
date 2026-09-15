"""@brief LabelKit 纯函数钩子：语义有证据，事实字段逐字节透传。"""

from __future__ import annotations

import copy
import logging

from worldmock.errors import IntegrityError


def complete_annotation(obj: dict, record: dict | None) -> dict:
    """@brief 模型只写语义，工程补齐未经模型修改的输入、动作和来源。"""
    if record is None:
        logging.error("Semantic postprocessor received no original record")
        raise ValueError("Semantic postprocessor requires the original record")
    result = {"task": record["task"], "input": copy.deepcopy(record["input"]),
              "target": copy.deepcopy(obj["target"]), "provenance": copy.deepcopy(record["provenance"])}
    return result


def validate_annotation(obj: dict, record: dict | None) -> list[str]:
    """@brief 独立验证来源引用、假设标签和事实透传，不把结构通过等同于语义正确。"""
    errors = []
    if record is None:
        errors.append("Original record is required for semantic validation")
    else:
        for key in ("task", "input", "provenance"):
            if obj[key] != record[key]:
                errors.append(f"Original {key} was modified")
        if "facts" in obj["target"]:
            errors.append("Future target facts cannot enter semantic annotation or review")
        errors.extend(_validate_claims(obj))
    for error in errors:
        logging.error("Semantic validation failed: %s", error)
    return errors


def attach_facts(obj: dict, record: dict) -> dict:
    """@brief 语义评审通过之后才回填原始目标，生成与评审均无法读取未来答案。"""
    if validate_annotation(obj, record):
        raise IntegrityError("Cannot attach facts to an invalid semantic annotation")
    result = copy.deepcopy(obj)
    result["target"]["facts"] = copy.deepcopy(record["target"])
    return result


def _validate_claims(obj: dict) -> list[str]:
    """@brief 事实和推断必须引用实际输入，反事实必须明确存在假设。"""
    errors = []
    known = {event["event_id"] for event in obj["input"]["events"]}
    prerequisite = obj["input"].get("prerequisite_state")
    if prerequisite and prerequisite["event_id"]:
        known.add(prerequisite["event_id"])
    for claim in obj["target"]["claims"]:
        citations = set(claim["evidence_event_ids"])
        if not citations <= known:
            errors.append("Semantic claim cites an event outside its knowledge window")
        if claim["kind"] != "hypothesis" and not citations:
            errors.append("Fact or inference has no supporting event")
    if obj["input"].get("mode") == "counterfactual":
        if not any(claim["kind"] == "hypothesis" for claim in obj["target"]["claims"]):
            errors.append("Counterfactual output does not declare a hypothesis")
    return errors
