"""@brief 在启动人物前验证完整场景、语料和实际数据消费者。"""

from __future__ import annotations

import importlib.metadata
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from worldmock.corpora.manifest import load_manifest
from worldmock.delivery.generation import EXPRESSIONS
from worldmock.delivery.rules import execute_fixture
from worldmock.errors import IntegrityError
from worldmock.life.scenario import load_scenario


def validate_project(project_root: Path, scenario_path: Path) -> dict:
    """@brief 全量来源校验、实际规则执行与 Schema 检查失败时阻止新运行。"""
    scenario, scenario_sha256 = load_scenario(scenario_path.resolve())
    corpus_root = Path(scenario.corpus_root)
    if not corpus_root.is_absolute():
        corpus_root = scenario_path.resolve().parent / corpus_root
    corpus = load_manifest(corpus_root, scenario.corpus_manifest_sha256)
    schemas = sorted((project_root / "labelkit-projects").rglob("*.json"))
    for path in schemas:
        Draft202012Validator.check_schema(json.loads(path.read_text()))
    versions = {name: importlib.metadata.version(name) for name in
                ("agentsociety2", "ray", "litellm", "mcp", "datasets", "textworld", "rule-engine")}
    if not (project_root / "vendor/labelkit/.venv/bin/labelkit").is_file():
        raise IntegrityError("The isolated LabelKit CLI environment is not installed")
    fixture = {"initial_state": {"deadline_seconds": 60},
               "events": [{"role": role, "timestamp_seconds": value} for role, value in
                          (("request", 0), ("accept", 1), ("complete", 2))],
               "expected": {"allowed": True}, "provenance": {"kind": "validation_fixture"}}
    execute_fixture(fixture, EXPRESSIONS)
    return {"valid": True, "scenario_id": scenario.scenario_id, "scenario_sha256": scenario_sha256,
            "corpus_entries": len(corpus.entries), "checked_schemas": len(schemas), "dependencies": versions}
