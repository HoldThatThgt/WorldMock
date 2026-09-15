"""@brief 将真实经历绑定到 LabelKit 序列扰动，并核验最终成功产物。"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from worldmock.errors import IntegrityError
from worldmock.footprints.models import canonical_json

EXPECTED_VARIANTS = {"positive": True, "missing_accept": False,
                     "complete_before_accept": False, "completion_timeout": False}
EXPRESSIONS = {"allowed": "complete and ordered and not duplicate and elapsed_seconds <= deadline_seconds"}


def create_catalog(record: dict) -> dict:
    """@brief 以实际请求作为描述与来源，生成分支使用明确独立的六十秒确认规则。"""
    request = record["target"]["request"]
    origin = {"run_id": record["provenance"]["run_id"], "source_event_ids": [request["event_id"]],
              "source_action_id": request["action_id"], "description": canonical_json(request["data"]).decode()}
    return {"initial_state": {"request": {"id": request["action_id"], "status": "new", "accepted": False},
                              "origin": origin, "sla": {"max_wait_us": 60_000_000}},
            "actors": {"person": {"goal": {"task": "提出所描述的生活请求"}, "identity": {"kind": "person"},
                                  "style": {"tone": "natural"}},
                       "environment": {"goal": {"task": "依据实际状态受理或确认请求"},
                                       "identity": {"kind": "service"}, "style": {"tone": "concise"}}},
            "shared_facts": {"public": {"timezone": "Asia/Shanghai"}, "hidden": {}},
            "style": {"language": "zh-CN"},
            "time_context": {"timezone": "Asia/Shanghai", "rule": "Complete within 60 seconds after acceptance"}}


def verify_generation(directory: Path) -> tuple[dict, list[dict], list[dict]]:
    """@brief 验证成功清单、报告身份、全部字节与行数以及交付摘要。"""
    manifest = json.loads((directory / "rules.manifest.json").read_text())
    if manifest.get("artifacts_committed") is not True or manifest["schema_version"] != 1:
        raise IntegrityError("LabelKit sequence artifacts were not committed")
    contents = {}
    for name in ("main", "stream", "report"):
        descriptor = manifest[name]
        path = Path(descriptor["path"]).resolve()
        if directory.resolve() not in path.parents:
            raise IntegrityError("Sequence artifact escapes its output directory")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != descriptor["sha256"]:
            raise IntegrityError("Sequence artifact hash does not match its manifest")
        contents[name] = json.loads(raw) if name == "report" else [json.loads(line) for line in raw.splitlines()]
        if name != "report" and len(contents[name]) != descriptor["rows"]:
            raise IntegrityError("Sequence artifact row count does not match its manifest")
    sequence = contents["report"]["generate"]["sequence"]
    if any(sequence[name] != manifest[name] for name in ("run_id", "delivery_digest")):
        raise IntegrityError("Sequence report and manifest identities disagree")
    digest = _delivery_digest(contents["main"] + contents["stream"])
    if digest != manifest["delivery_digest"]:
        raise IntegrityError("Sequence canonical delivery digest is invalid")
    return manifest, contents["main"], contents["stream"]


def _delivery_digest(rows: list[dict]) -> str:
    """@brief 按 LabelKit 公开的长度定界规范复算内容身份。"""
    digest = hashlib.sha256(b"labelkit:v1.20:delivery\n")
    for row in rows:
        value = copy.deepcopy(row)
        run = value.get("_meta", {}).get("run", {})
        for key in ("started_at", "finished_at", "duration_ms"):
            run.pop(key, None)
        raw = canonical_json(value)
        digest.update(str(len(raw)).encode() + b":" + raw)
    return digest.hexdigest()


def generation_fixtures(product: tuple, catalog: dict, source: dict) -> list[dict]:
    """@brief 从实际成员构造夹具，预期由冻结变体规范独立提供。"""
    manifest, main, stream = product
    event_by_id = {row["_meta"]["event"]["event_id"]: row for row in stream}
    if len(event_by_id) != len(stream):
        raise IntegrityError("Generated event IDs are duplicated")
    fixtures, consumed, variants = [], [], []
    for row in main:
        generation = row["_meta"]["generation"]
        variant = generation["variant"]
        if variant not in EXPECTED_VARIANTS:
            raise IntegrityError("Generated sequence uses an undeclared variant")
        identities = row["_meta"]["stream"]["member_ids"]
        events = [_fixture_event(event_by_id[identity], row["_meta"]["id"]) for identity in identities]
        provenance = {**source, "labelkit_run_id": manifest["run_id"], "variant": variant,
                      "world_branch_id": generation["world_branch_id"], "generated_event_ids": identities,
                      "generation_kind": "synthetic_sequence", "rule_version": "action-confirmation-2026-09-15"}
        fixtures.append({"initial_state": {"deadline_seconds": catalog["initial_state"]["sla"]["max_wait_us"] / 1e6},
                         "events": events, "expected": {"allowed": EXPECTED_VARIANTS[variant]},
                         "provenance": provenance})
        consumed.extend(identities)
        variants.append(variant)
    if sorted(variants) != sorted(EXPECTED_VARIANTS) or sorted(consumed) != sorted(event_by_id):
        raise IntegrityError("Generated variants or event membership are incomplete")
    return fixtures


def _fixture_event(row: dict, owner: str) -> dict:
    """@brief 保留生成框架的真实角色、逻辑时间与身份。"""
    event = row["_meta"]["event"]
    if event["owner_sequence_id"] != owner or event["role"] not in ("request", "accept", "complete"):
        raise IntegrityError("Generated event has an invalid owner or role")
    return {"event_id": event["event_id"], "role": event["role"],
            "timestamp_seconds": event["logical_time_us"] / 1e6, "payload": row["payload"]}
