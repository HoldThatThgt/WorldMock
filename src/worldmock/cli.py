"""@brief WorldMock 的唯一命令入口与成功发布边界。"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import logging
import sys
from pathlib import Path
from uuid import uuid4

from worldmock.errors import IntegrityError
from worldmock.footprints.assets import atomic_write
from worldmock.footprints.models import canonical_json
from worldmock.status import read_status, write_status


def build_parser() -> argparse.ArgumentParser:
    """@brief 公开明确的新建、恢复、只读查询和足迹导出命令。"""
    parser = argparse.ArgumentParser(prog="worldmock", description="Auditable AS2 life simulation")
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="validate scenario, catalog, dependencies, and runtime contracts")
    validation_source = validate.add_mutually_exclusive_group(required=True)
    validation_source.add_argument("--scenario", type=Path)
    validation_source.add_argument("--environment", type=Path, help="validate a published environment directory")
    validate.add_argument("--coverage-out", type=Path,
                          help="write persona_coverage.json here (default: beside the scenario)")
    for name in ("run", "resume", "replay"):
        command = commands.add_parser(name, help="run a new world" if name == "run" else "restore into a new branch")
        command.add_argument("--scenario", type=Path, required=True)
        command.add_argument("--output", type=Path, required=True)
        if name == "run":
            # 同秒续接开关只在新建运行时给出；恢复与重放继承父清单的值，不能覆盖（第二章「同秒续接」）。
            command.add_argument("--same-second-continuation", choices=("on", "off"), default="on")
        else:
            command.add_argument("--parent", type=Path, required=True)
            command.add_argument("--checkpoint", required=True)
        if name == "replay":
            command.add_argument("--intervention", type=Path, required=True)
    status = commands.add_parser("status", help="read confirmed world state without advancing time")
    status.add_argument("--run-dir", type=Path, required=True)
    export = commands.add_parser("export", help="export one immutable event cutoff")
    export.add_argument("--run-dir", type=Path, required=True)
    export.add_argument("--cutoff", type=int)
    export.add_argument("--output", type=Path, required=True)
    _profile_arguments(commands)
    _evaluation_arguments(commands)
    _environment_arguments(commands)
    return parser


def _environment_arguments(commands) -> None:
    """@brief 环境发布命令只生成或逐字节检查已声明地点的地图。"""
    environment = commands.add_parser("environment", help="generate and verify published environment data")
    operations = environment.add_subparsers(dest="environment_command", required=True)
    generate = operations.add_parser("generate-map", help="generate the deterministic map for registered places")
    generate.add_argument("--locations", type=Path, required=True)
    generate.add_argument("--seed", type=int, required=True)
    generate.add_argument("--output", type=Path, required=True)
    generate.add_argument("--check", action="store_true", help="compare existing output without writing it")


def _evaluation_arguments(commands) -> None:
    """@brief 评估输入显式指向完整交付与配置，可在盲评后继续。"""
    evaluate = commands.add_parser("evaluate", help="compare authenticated digital footprints")
    evaluate.add_argument("--worldmock", type=Path)
    evaluate.add_argument("--legacy", type=Path)
    evaluate.add_argument("--scenario", type=Path)
    evaluate.add_argument("--output", type=Path, required=True)
    evaluate.add_argument("--reviews", type=Path, nargs="*")
    evaluate.add_argument("--human-reference", type=Path)


def _profile_arguments(commands) -> None:
    """@brief 画像编译 v2：background_story -> PersonaCard + 大五 + 世界层动力学。"""
    profile = commands.add_parser("profile", help="compile persona cards (profile v2)")
    profile_commands = profile.add_subparsers(dest="profile_command", required=True)
    compile_command = profile_commands.add_parser(
        "compile", help="background story -> persona card + big five + world dynamics")
    compile_command.add_argument("--text", required=True,
                                 help="persona text file path, or - to read stdin")
    compile_command.add_argument("--runs", type=int, default=3,
                                 help="independent LLM extraction runs for the consensus")
    compile_command.add_argument("--out", type=Path, help="write result JSON here instead of stdout")
    check = profile_commands.add_parser(
        "check", help="compile 3x and assert card consistency + big-five mode stability")
    check.add_argument("--text", required=True, help="persona text file path, or - to read stdin")
    check.add_argument("--runs", type=int, default=3,
                       help="independent LLM extraction runs per compile (cost control)")
    validate_command = profile_commands.add_parser(
        "validate", help="validate scenario persona coverage (writes persona_coverage.json)")
    validate_command.add_argument("--scenario", type=Path, required=True)
    validate_command.add_argument("--coverage-out", type=Path,
                                  help="write persona_coverage.json here (default: beside the scenario)")


def main(argv: list[str] | None = None) -> int:
    """@brief 成功输出机器可读结果，异常记录英文日志并返回非零状态。"""
    arguments = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        result = _dispatch(arguments)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        if arguments.command == "validate" and arguments.environment is not None:
            return 0 if result["valid"] else 1
        return 0
    except KeyboardInterrupt:
        logging.error("WorldMock command interrupted")
        return 130
    except Exception:
        logging.exception("WorldMock command failed")
        return 1


def _dispatch(arguments) -> dict:
    """@brief 按命令连接实际生产模块，不提供替代执行路径。"""
    command = arguments.command
    if command in ("run", "resume", "replay"):
        return _run_command(arguments)
    if command == "status":
        return read_status(arguments.run_dir)
    if command == "export":
        return _export_command(arguments)
    if command == "validate":
        from worldmock.validation import validate_environment, validate_project
        if arguments.environment is not None:
            return validate_environment(arguments.environment)
        return validate_project(arguments.project_root, arguments.scenario,
                                coverage_out=arguments.coverage_out)
    if command == "profile":
        return _profile_command(arguments)
    if command == "environment":
        return _environment_command(arguments)
    return _evaluate_command(arguments)


def _environment_command(arguments) -> dict:
    """@brief 地点输入经真实严格模型，地图发布和检查走同一个生产生成器。"""
    from worldmock.catalog.errors import RegistrationError
    from worldmock.catalog.loader import parse_catalog_model
    from worldmock.catalog.mapgen import generate_map, publish_map
    from worldmock.catalog.models import LocationsFile

    try:
        payload = json.loads(arguments.locations.read_bytes())
    except (UnicodeError, json.JSONDecodeError) as error:
        logging.error("Location document is invalid: %s", error)
        raise RegistrationError("ENVIRONMENT_KIND_INVALID") from error
    locations = parse_catalog_model(payload, LocationsFile)
    graph = generate_map([place.location_id for place in locations.locations], arguments.seed)
    publish_map(graph, arguments.output, check=arguments.check)
    return {"valid": True, "checked": arguments.check, "output": str(arguments.output),
            "nodes": len(graph.nodes), "edges": len(graph.edges), "seed": graph.seed}


def _profile_command(arguments) -> dict:
    """@brief 画像编译 v2：compile/check=三次一致性口径、validate=覆盖率输出。"""
    from worldmock.profile.extractor import extract_persona
    from worldmock.profile.textgen import render_card

    if arguments.profile_command == "validate":
        from worldmock.validation import validate_persona_coverage
        document = validate_persona_coverage(
            arguments.scenario, coverage_out=arguments.coverage_out)
        return {"scenario_id": document["scenario_id"],
                "coverage_path": str(arguments.coverage_out
                                     or arguments.scenario.resolve().parent / "persona_coverage.json"),
                "agents": document["agents"]}

    persona_text = _read_persona_text(arguments.text)
    if arguments.profile_command == "compile":
        compiled = _compile_once(persona_text, arguments.runs, extract_persona)
        result = {
            "schema_version": json.loads(compiled.model_dump_json())["schema_version"],
            "card": json.loads(compiled.card.model_dump_json()),
            "big_five": json.loads(compiled.big_five.model_dump_json()),
            "world_dynamics": json.loads(compiled.world_dynamics.model_dump_json()),
            "source": json.loads(compiled.source.model_dump_json()),
            "warnings": list(compiled.warnings),
            "persona_card_text": render_card(compiled.card),
        }
        if arguments.out is not None:
            atomic_write(arguments.out, (json.dumps(result, ensure_ascii=False, indent=2)
                                         + "\n").encode("utf-8"))
            return {"written": str(arguments.out), "warnings": result["warnings"]}
        return result
    return _profile_check_report(persona_text, arguments.runs, extract_persona)


def _profile_check_report(persona_text: str, runs: int, extract) -> dict:
    """@brief check：三次独立编译，卡片与大五众数占比均需 ≥2/3。"""
    from worldmock.profile.models import BIG_FIVE_DIMENSIONS
    from worldmock.profile.textgen import render_card

    compiles = [_compile_once(persona_text, runs, extract) for _ in range(3)]
    card_hits = _mode_share([json.dumps(json.loads(compiled.card.model_dump_json()),
                                        ensure_ascii=False, sort_keys=True)
                             for compiled in compiles])
    mode_hits: dict[str, float] = {}
    for dimension in BIG_FIVE_DIMENSIONS:
        scores = [compiled.big_five.model_dump()[dimension] for compiled in compiles]
        mode_hits[dimension] = _mode_share(scores)
    failed = [dimension for dimension, rate in mode_hits.items() if rate < 2 / 3]
    report = {
        "card_mode_hit": card_hits,
        "big_five_mode_hit": mode_hits,
        "cards": [json.loads(compiled.card.model_dump_json()) for compiled in compiles],
        "persona_card_texts": [render_card(compiled.card) for compiled in compiles],
    }
    if card_hits < 2 / 3 or failed:
        raise IntegrityError(
            f"PROFILE_CHECK_FAILED: card_mode_hit={card_hits}, "
            f"dimensions below 2/3 mode hit: {failed}. Summary: "
            + json.dumps({key: report[key] for key in ("card_mode_hit", "big_five_mode_hit")},
                         ensure_ascii=False))
    return report


def _mode_share(values: list) -> float:
    """@brief 众数占比 = 最频繁值次数 / 样本数（确定性）。"""
    counts: dict[str, int] = {}
    for value in values:
        key = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
        counts[key] = counts.get(key, 0) + 1
    return max(counts.values()) / len(values)


def _compile_once(persona_text: str, runs: int, extract):
    """@brief 单次编译：三次一致性抽取 -> compile_persona（卡片+大五+动力学）。"""
    from worldmock.profile.compile import compile_persona

    extraction = extract(persona_text, n_runs=runs)
    return compile_persona(persona_text, extraction=extraction)


def _read_persona_text(source: str) -> str:
    """@brief 读取画像文本：文件路径或 -（stdin）。"""
    if source == "-":
        return sys.stdin.read()
    path = Path(source)
    if not path.is_file():
        raise IntegrityError(f"persona text file not found: {source}")
    return path.read_text(encoding="utf-8")


def _run_command(arguments) -> dict:
    """@brief 新建或恢复都使用真实 AS2 入口，只在完整返回后标记完成。"""
    from worldmock.runtime import ResumeRequest, RunRequest, resume_world, run_world

    root, output, scenario = arguments.project_root.resolve(), arguments.output.resolve(), arguments.scenario.resolve()
    if output.exists():
        raise IntegrityError("Run destination already exists")
    try:
        if arguments.command == "run":
            identity = uuid4().hex
            write_status(output, "created", "stopped")
            request = RunRequest(root, scenario, output, identity, identity,
                                 arguments.same_second_continuation == "on")
            result = asyncio.run(run_world(request))
        else:
            request = ResumeRequest(root, scenario, arguments.parent.resolve(), arguments.checkpoint, output)
            result = (_run_replay(request, arguments.intervention) if arguments.command == "replay"
                      else asyncio.run(resume_world(request)))
        receipt = json.loads(json.dumps(dataclasses.asdict(result), default=str))
        atomic_write(output / "result.json", canonical_json(receipt))
        write_status(output, "completed", "stopped")
        return receipt
    except BaseException as error:
        if output.exists():
            state = "interrupted" if isinstance(error, KeyboardInterrupt) else "failed"
            write_status(output, state, "stopped", type(error).__name__)
        raise


def _run_replay(base_request, intervention_path: Path):
    """@brief 显式读取严格世界条件干预，实际重演始终创建独立运行。"""
    from worldmock.life.models import CounterfactualIntervention
    from worldmock.runtime import CounterfactualRequest, replay_world

    intervention = CounterfactualIntervention.model_validate_json(intervention_path.read_text())
    return asyncio.run(replay_world(CounterfactualRequest(base_request, intervention)))


def _export_command(arguments) -> dict:
    """@brief 在读取开始时冻结截止序号，并关闭持久连接。"""
    from worldmock.footprints.export import export_run
    from worldmock.footprints.store import FootprintStore

    store = FootprintStore(arguments.run_dir)
    try:
        cutoff = store.last_event_seq if arguments.cutoff is None else arguments.cutoff
        return export_run(store, cutoff, arguments.output)
    finally:
        store.close()


def _evaluate_command(arguments) -> dict:
    """@brief 调用正式评估工具；同一输入只生成一套已认证比较。"""
    from worldmock.evaluation.runner import finalize_evaluation, prepare_evaluation

    if arguments.reviews:
        if any((arguments.worldmock, arguments.legacy, arguments.scenario, arguments.human_reference)):
            raise IntegrityError("Finalizing prepared reviews accepts only --output and --reviews")
        return finalize_evaluation(arguments.output, arguments.reviews)
    if not all((arguments.worldmock, arguments.legacy, arguments.scenario)):
        raise IntegrityError("Preparing evaluation requires --worldmock, --legacy, and --scenario")
    return prepare_evaluation(arguments.legacy, arguments.worldmock, arguments.scenario,
                              arguments.output, arguments.human_reference)


if __name__ == "__main__":
    raise SystemExit(main())
