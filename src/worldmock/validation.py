"""@brief 在启动人物前验证完整场景、语料和实际数据消费者。"""

from __future__ import annotations
from worldmock.profile.compile import compile_persona
from worldmock.profile import coverage_targets
from worldmock import run_setup

import importlib.metadata
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from worldmock.catalog.admission import validate_task_assignees, validate_world_categories
from worldmock.catalog.loader import load_catalog
from worldmock.corpora.manifest import load_manifest
from worldmock.delivery.generation import EXPRESSIONS
from worldmock.delivery.rules import execute_fixture
from worldmock.errors import IntegrityError
from worldmock.footprints.assets import atomic_write
from worldmock.life.world_setup import load_scenario
from worldmock.profile.coverage import validate_coverage, require_coverage

#: 改名为 stage_model_identity 之前的清单键（带决策自述阶段的旧运行）。
#: 媒介品类闭集映射：世界内容目录 kind -> 中文媒介词（与 render_day 标签同口径）。


def validate_project(project_root: Path, scenario_path: Path, *,
                     persona_compiler=None, coverage_out: Path | None = None) -> dict:
    """@brief 全量来源校验、实际规则执行与 Schema 检查失败时阻止新运行。

    新增（修订合同）：世界素材品类准入、任务受理人校验、清单阶段身份
    一致性（validate_manifest_stage_identity）与 persona_coverage.json 覆盖率门禁（<80% 拒绝）。
    先核对实际导入的 AS2：包目录为空、导入失败或与锁定源码摘要不符即以 IntegrityError 拒绝
    （docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动「运行身份」）。结果另带 input_warnings：与新建运行同一组运行输入警告
    （docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要-运行输入警告 ），覆盖率门禁判定之前已逐条写到标准错误流。
    """

    run_setup.require_locked_as2()
    scenario, scenario_sha256 = load_scenario(scenario_path.resolve())
    corpus_root = Path(scenario.corpus_root)
    if not corpus_root.is_absolute():
        corpus_root = scenario_path.resolve().parent / corpus_root
    corpus = load_manifest(corpus_root, scenario.corpus_manifest_sha256)
    schemas = sorted((project_root / "labelkit-projects").rglob("*.json"))
    for path in schemas:
        Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))
    versions = {name: importlib.metadata.version(name) for name in
                ("agentsociety2", "ray", "litellm", "mcp", "datasets", "rule-engine")}
    _require_ray_worker_import()
    labelkit = (project_root / "vendor/labelkit/.venv/Scripts/labelkit.exe" if os.name == "nt"
                else project_root / "vendor/labelkit/.venv/bin/labelkit")
    if not labelkit.is_file():
        raise IntegrityError("The isolated LabelKit CLI environment is not installed")
    fixture = {"initial_state": {"deadline_seconds": 60},
               "events": [{"role": role, "timestamp_seconds": value} for role, value in
                          (("request", 0), ("accept", 1), ("complete", 2))],
               "expected": {"allowed": True}, "provenance": {"kind": "validation_fixture"}}
    execute_fixture(fixture, EXPRESSIONS)
    catalog_dir = scenario_path.resolve().parent.parent / "catalog"
    catalog = load_catalog(catalog_dir)
    _require_world_categories(catalog, corpus_root, scenario.map_dataset.locations_file.path)
    _require_task_assignees(scenario)
    run_setup._configure_process(project_root)
    identity = run_setup.validate_manifest_stage_identity()
    coverage = validate_persona_coverage(
        scenario_path, persona_compiler=persona_compiler, coverage_out=coverage_out,
        _scenario=scenario, _corpus_root=corpus_root)
    return {"valid": True, "scenario_id": scenario.scenario_id, "scenario_sha256": scenario_sha256,
            "corpus_entries": len(corpus.entries), "checked_schemas": len(schemas),
            "dependencies": versions, "model_identity": {"stages": sorted(identity["stages"]),
                                                          "consistent": True},
            "persona_coverage": coverage["agents"], "input_warnings": coverage["input_warnings"]}


def _require_ray_worker_import() -> None:
    """@brief 在独立进程中验证 Ray worker 可导入其原生依赖。"""
    try:
        result = subprocess.run(
            [sys.executable, "-c", "import ray"], capture_output=True, text=True,
            timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        logging.exception("Ray worker import check could not complete")
        raise IntegrityError("Ray worker import check failed") from error
    if result.returncode:
        logging.error("Ray worker import failed: %s", result.stderr.strip())
        raise IntegrityError("Ray worker cannot import ray in a clean process")








def validate_persona_coverage(scenario_path: Path, *,
                              persona_compiler=None,
                              coverage_out: Path | None = None,
                              _scenario: Any = None, _corpus_root: Path | None = None) -> dict:
    """@brief 校验场景人物卡片口味/习惯的世界可执行覆盖并写 persona_coverage.json，并给出运行输入警告。

    每个声明 background_story 的人物经画像编译（默认真实三次一致性抽取，
    persona_compiler 供确定性测试注入）得到 PersonaCard；可执行比例低于
    80% 时抛 PersonaCoverageError（校验失败，裁决 D4），缺失项列在报告与文件中。
    同一份编译结果经 run_setup.creation_inputs 计算与新建运行同一组输入警告（场景作息覆盖走同一个 apply_scenario_rhythm；
    docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要-运行输入警告 、2.1）：先逐条写到标准错误流，再做覆盖率门禁；返回值另带 input_warnings，
    persona_coverage.json 的结构不变。
    """

    scenario = _scenario if _scenario is not None else load_scenario(
        Path(scenario_path).resolve())[0]
    corpus_root = _corpus_root
    if corpus_root is None:
        corpus_root = Path(scenario.corpus_root)
        if not corpus_root.is_absolute():
            corpus_root = Path(scenario_path).resolve().parent / corpus_root
    catalog_dir = Path(scenario_path).resolve().parent.parent / "catalog"
    targets = coverage_targets.build_coverage_targets(scenario, load_catalog(catalog_dir), corpus_root)
    compile_story = persona_compiler or _compile_persona
    compiled = {agent.agent_id: compile_story(agent.background_story)
                for agent in scenario.agents if agent.background_story is not None}
    checked = [(agent_id, validate_coverage(result.card, targets)) for agent_id, result in compiled.items()]
    reports = [{"agent_id": agent_id, **report.model_dump()} for agent_id, report in checked]
    document = {"schema_version": 1, "scenario_id": scenario.scenario_id,
                "threshold": 0.8, "agents": reports}
    destination = Path(coverage_out) if coverage_out is not None else (
        Path(scenario_path).resolve().parent / "persona_coverage.json")
    atomic_write(destination, (json.dumps(document, ensure_ascii=False, indent=2)
                               + "\n").encode("utf-8"))
    warnings = run_setup.creation_inputs(Path(scenario_path), scenario, compiled)[1]
    for warning in warnings:
        logging.warning("Input warning %s (agent %s): %s", warning["code"], warning["agent_id"], warning["message"])
    for _, report in checked:
        require_coverage(report)
    return {**document, "input_warnings": warnings}


def _compile_persona(story: str) -> Any:
    """@brief 默认编译：A9 compile_persona（真实三次一致性抽取）。"""

    return compile_persona(story)








def _require_world_categories(catalog: Any, corpora_root: Path, locations_path: str) -> None:
    """@brief 世界素材品类准入：任一品类违例即拒绝（fail-closed，英文错误）。"""
    result = validate_world_categories(catalog, corpora_root, locations_path)
    if not result.ok:
        detail = "; ".join(f"{item.code}: {item.detail}" for item in result.violations)
        raise IntegrityError(f"World category admission rejected: {detail}")


def _require_task_assignees(scenario: Any) -> None:
    """@brief 场景任务受理人校验：tasks[].assignee_agent_id 必填。

    ScenarioTaskRef 是最小闭集 Schema（extra=forbid），只投影受理人
    校验三字段，不把完整合同字段灌进准入模型。
    """
    projection = ({"task_id": item.task_id, "assignee_agent_id": item.assignee_agent_id,
                   "input_path": item.input_path} for item in scenario.tasks)
    result = validate_task_assignees(projection)
    if not result.ok:
        detail = "; ".join(f"{item.code}: {item.detail}" for item in result.violations)
        raise IntegrityError(f"Task assignee admission rejected: {detail}")
