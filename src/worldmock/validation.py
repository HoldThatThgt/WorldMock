"""@brief 在启动人物前验证完整场景、目录和模拟运行条件。"""

from __future__ import annotations

import importlib.metadata
import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

from worldmock.errors import IntegrityError
from worldmock.footprints.assets import atomic_write



def validate_project(project_root: Path, scenario_path: Path, *,
                     persona_compiler=None, coverage_out: Path | None = None) -> dict:
    """@brief 真实场景加载后核对 AS2 锁、运行依赖、阶段身份和八成画像覆盖门禁。"""
    from worldmock import run_setup
    from worldmock.life.world_setup import load_runtime_scenario

    run_setup.require_locked_as2()
    semantics, scenario_sha256 = load_runtime_scenario(scenario_path.resolve())
    scenario = semantics.scenario
    versions = {name: importlib.metadata.version(name) for name in
                ("agentsociety2", "ray", "litellm", "mcp")}
    _require_ray_worker_import()
    run_setup._configure_process(project_root)
    identity = run_setup.validate_manifest_stage_identity()
    coverage = validate_persona_coverage(
        scenario_path, persona_compiler=persona_compiler, coverage_out=coverage_out,
        _scenario=scenario)
    return {"valid": True, "scenario_id": scenario.scenario_id, "scenario_sha256": scenario_sha256,
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
                              _scenario: Any = None) -> dict:
    """@brief 校验场景人物卡片口味/习惯的世界可执行覆盖并写 persona_coverage.json，并给出运行输入警告。

    每个声明 background_story 的人物经画像编译（默认真实三次一致性抽取，
    persona_compiler 供确定性测试注入）得到 PersonaCard；可执行比例低于
    80% 时抛 PersonaCoverageError（校验失败，裁决 D4），缺失项列在报告与文件中。
    同一份编译结果经 run_setup.creation_inputs 计算与新建运行同一组输入警告（场景作息覆盖走同一个 apply_scenario_rhythm；
    docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要-运行输入警告 、2.1）：先逐条写到标准错误流，再做覆盖率门禁；返回值另带 input_warnings，
    persona_coverage.json 的结构不变。
    """

    from worldmock import run_setup
    from worldmock.life.semantics import resolve_catalog_dir
    from worldmock.life.world_setup import load_runtime_scenario
    from worldmock.catalog.loader import load_catalog
    from worldmock.profile import coverage_targets
    from worldmock.profile.coverage import require_coverage, validate_coverage

    if _scenario is None:
        semantics, _ = load_runtime_scenario(Path(scenario_path).resolve())
        scenario, catalog = semantics.scenario, semantics.catalog
    else:
        scenario = _scenario
        catalog = load_catalog(resolve_catalog_dir(scenario_path))
    targets = coverage_targets.build_coverage_targets(scenario, catalog)
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

    from worldmock.profile.compile import compile_persona

    return compile_persona(story)


def validate_environment(environment_root: Path) -> dict:
    """@brief 校验真实环境目录、地图重生成与全部场景，并汇总每处失败。"""
    from worldmock.catalog.loader import load_catalog
    from worldmock.catalog.mapgen import require_regenerated_map

    root, errors = Path(environment_root).resolve(), []
    catalog = None
    try:
        catalog = load_catalog(root / "catalog")
        require_regenerated_map(catalog.map_data, (root / "catalog/map.json").read_bytes())
    except (IntegrityError, OSError, ValueError) as error:
        _environment_failure("catalog", error, errors)
    scenarios = _validate_environment_scenarios(root, errors)
    if errors:
        return {"valid": False, "errors": errors, "scenarios": scenarios}
    return {"valid": True, "environment_id": catalog.manifest.environment_id,
            "environment_version": catalog.manifest.environment_version,
            "scenarios": scenarios}


def _validate_environment_scenarios(root: Path, errors: list[dict]) -> list[str]:
    """@brief 所有场景各自走真实加载器，单个失败不隐藏其他场景的失败。"""
    from worldmock.life.world_setup import load_runtime_scenario

    paths = sorted((root / "scenarios").rglob("*.json"))
    if not paths:
        _environment_failure("scenarios", IntegrityError("The environment contains no scenarios"), errors)
    valid = []
    for path in paths:
        relative = path.relative_to(root).as_posix()
        try:
            load_runtime_scenario(path, root / "catalog")
        except (IntegrityError, OSError, ValueError) as error:
            _environment_failure(relative, error, errors)
        else:
            valid.append(relative)
    return valid


def _environment_failure(relative: str, error: Exception, errors: list[dict]) -> None:
    """@brief 保留唯一登记码或完整性异常名，并以英文日志报告实际原因。"""
    from worldmock.catalog.errors import RegistrationError

    code = error.code if isinstance(error, RegistrationError) else type(error).__name__
    reason = str(error)
    logging.error("Environment validation failed for %s: %s", relative, reason)
    errors.append({"path": relative, "code": code, "reason": reason})
