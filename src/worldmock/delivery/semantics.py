"""@brief LabelKit 纯函数钩子：语义有证据，事实字段逐字节透传。"""

from __future__ import annotations

import copy
import json
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
    target_ids = _target_event_ids(record["target"])
    if target_ids:
        source_ids = result["provenance"]["source_event_ids"]
        result["provenance"]["source_event_ids"] = list(dict.fromkeys(source_ids + target_ids))
        result["provenance"]["target_event_ids"] = target_ids
    return result


def _target_event_ids(facts: dict) -> list[str]:
    """@brief 在审核通过后提取真实动作请求及结果身份。"""
    request = facts.get("request")
    outcomes = facts.get("outcomes")
    if not isinstance(request, dict) or not isinstance(outcomes, list):
        return []
    identities = [request.get("event_id")]
    identities.extend(outcome.get("event_id") for outcome in outcomes if isinstance(outcome, dict))
    return [identity for identity in identities if isinstance(identity, str)]


def _validate_claims(obj: dict) -> list[str]:
    """@brief 事实和推断必须引用实际输入，反事实必须明确存在假设。"""
    errors = []
    allowed = _visible_evidence_ids(obj["input"])
    if obj["input"].get("allowed_evidence_event_ids") != allowed:
        errors.append("Allowed evidence event IDs disagree with the visible input events")
    known = set(allowed)
    invalid = set()
    missing = False
    for claim in obj["target"]["claims"]:
        citations = set(claim["evidence_event_ids"])
        invalid.update(citations - known)
        if claim["kind"] != "hypothesis" and not citations:
            missing = True
    if invalid:
        errors.insert(0, _citation_error(invalid, allowed))
    if missing:
        errors.append(_missing_citation_error(allowed))
    if obj["input"].get("mode") == "counterfactual":
        if not any(claim["kind"] == "hypothesis" for claim in obj["target"]["claims"]):
            errors.append("Counterfactual output does not declare a hypothesis")
    return errors


def _visible_evidence_ids(input_data: dict) -> list[str]:
    """@brief 从直接原始事件与前置观察中重建唯一引用闭集。"""
    identities = [event["event_id"] for event in input_data["events"]]
    prerequisite = input_data.get("prerequisite_state")
    if prerequisite and prerequisite["event_id"]:
        identities.append(prerequisite["event_id"])
    return list(dict.fromkeys(identities))


def _citation_error(invalid: set[str], allowed: list[str]) -> str:
    """@brief 仅向返工回传非法引用和当前允许的事件身份。"""
    rejected = json.dumps(sorted(invalid), ensure_ascii=False, separators=(",", ":"))
    accepted = json.dumps(allowed, ensure_ascii=False, separators=(",", ":"))
    return ("Semantic claim cites an event outside its knowledge window; "
            f"invalid_evidence_event_ids={rejected}; allowed_evidence_event_ids={accepted}")


def _missing_citation_error(allowed: list[str]) -> str:
    """@brief 缺少事实引用时仅回传当前允许的事件身份。"""
    accepted = json.dumps(allowed, ensure_ascii=False, separators=(",", ":"))
    return f"Fact or inference has no supporting event; allowed_evidence_event_ids={accepted}"
