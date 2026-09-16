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
    """@brief 公开明确的新建、恢复、只读查询和交付命令。"""
    parser = argparse.ArgumentParser(prog="worldmock", description="Auditable AS2 life simulation")
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare-corpora", help="fetch and validate the frozen source corpus")
    prepare.add_argument("--output", type=Path, required=True)
    validate = commands.add_parser("validate", help="validate scenario, corpus, dependencies, and delivery contracts")
    validate.add_argument("--scenario", type=Path, required=True)
    for name in ("run", "resume", "replay"):
        command = commands.add_parser(name, help="run a new world" if name == "run" else "restore into a new branch")
        command.add_argument("--scenario", type=Path, required=True)
        command.add_argument("--output", type=Path, required=True)
        if name != "run":
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
    deliver = commands.add_parser("deliver", help="execute LabelKit, training loader, and rule fixtures")
    deliver.add_argument("--export", type=Path, required=True)
    deliver.add_argument("--output", type=Path, required=True)
    deliver.add_argument("--replay-export", type=Path, action="append", default=[])
    _profile_arguments(commands)
    _evaluation_arguments(commands)
    return parser


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
    """@brief 画像编译器：NL→大五→动机档案 与编译一致性自检。"""
    profile = commands.add_parser("profile", help="compile persona text into a motivation profile")
    profile_commands = profile.add_subparsers(dest="profile_command", required=True)
    compile_command = profile_commands.add_parser(
        "compile", help="persona text -> big five -> motivation profile + canonical text")
    compile_command.add_argument("--text", required=True,
                                 help="persona text file path, or - to read stdin")
    compile_command.add_argument("--runs", type=int, default=5,
                                 help="independent LLM extraction runs for consensus")
    compile_command.add_argument("--out", type=Path, help="write result JSON here instead of stdout")
    check = profile_commands.add_parser(
        "check", help="compile 3x and assert lattice consistency + big-five mode stability")
    check.add_argument("--text", required=True, help="persona text file path, or - to read stdin")
    check.add_argument("--runs", type=int, default=3,
                       help="independent LLM extraction runs per compile (cost control)")


def main(argv: list[str] | None = None) -> int:
    """@brief 成功输出机器可读结果，异常记录英文日志并返回非零状态。"""
    arguments = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        result = _dispatch(arguments)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
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
    if command == "deliver":
        from worldmock.delivery.pipeline import deliver
        return deliver(arguments.export, arguments.output, arguments.project_root, tuple(arguments.replay_export))
    if command == "validate":
        from worldmock.validation import validate_project
        return validate_project(arguments.project_root, arguments.scenario)
    if command == "prepare-corpora":
        from worldmock.corpora.fetch import prepare_corpora
        return prepare_corpora(arguments.output).model_dump(mode="json")
    if command == "profile":
        return _profile_command(arguments)
    return _evaluate_command(arguments)


def _profile_command(arguments) -> dict:
    """@brief 画像编译：真实提取 → 确定性映射 → 同源规范文本；check 断言一致性。"""
    from worldmock.profile.extractor import extract_big_five
    from worldmock.profile.mapper import map_profile
    from worldmock.profile.models import BIG_FIVE_DIMENSIONS
    from worldmock.profile.textgen import render_persona_text

    persona_text = _read_persona_text(arguments.text)
    if arguments.profile_command == "compile":
        extraction = extract_big_five(persona_text, n_runs=arguments.runs)
        profile = map_profile(
            extraction.profile, persona_text=persona_text,
            source_model=extraction.model,
            consensus_runs=extraction.runs, consensus_agreement=extraction.agreement)
        result = {
            "profile": json.loads(profile.model_dump_json()),
            "canonical_persona_text": render_persona_text(profile),
            "warnings": (["big_five_agreement_below_0.6"]
                         if extraction.warning else []),
            "per_dimension": extraction.per_dimension,
        }
        if arguments.out is not None:
            atomic_write(arguments.out, (json.dumps(result, ensure_ascii=False, indent=2)
                                         + "\n").encode("utf-8"))
            return {"written": str(arguments.out), "warnings": result["warnings"]}
        return result

    compiles = [
        _compile_once(persona_text, arguments.runs, extract_big_five, map_profile)
        for _ in range(3)
    ]
    from worldmock.profile.models import (
        BETA_PRESENT_BIAS_RANGE, BETA_PRESENT_BIAS_STEP, EMOTION_SENSITIVITY_RANGE,
        EMOTION_SENSITIVITY_STEP, LOSS_AVERSION_RANGE, LOSS_AVERSION_STEP,
        MotivationProfile, TAU_GRID, WEIGHT_GRID_STEP, iterate_lattice, value_on_grid)

    grid_total = grid_hits = 0
    for profile, _ in compiles:
        # 独立重推导：从序列化 JSON 重新解析数值并复核格点（不信任模型实例内部）。
        MotivationProfile.model_validate_json(profile.model_dump_json())
        emitted = json.loads(profile.model_dump_json())
        for name, value in iterate_lattice(profile):
            grid_total += 1
            raw = emitted
            for part in name.split("."):
                raw = raw[part]
            if name == "tau":
                ok = any(abs(raw - grid) <= 1e-6 for grid in TAU_GRID)
            elif name == "beta_present_bias":
                ok = value_on_grid(raw, *BETA_PRESENT_BIAS_RANGE, BETA_PRESENT_BIAS_STEP)
            elif name == "emotion_sensitivity":
                ok = value_on_grid(raw, *EMOTION_SENSITIVITY_RANGE, EMOTION_SENSITIVITY_STEP)
            elif name.startswith("loss_aversion."):
                ok = value_on_grid(raw, *LOSS_AVERSION_RANGE, LOSS_AVERSION_STEP)
            else:
                ok = value_on_grid(raw, 0.0, 1.0, WEIGHT_GRID_STEP)
            grid_hits += int(ok)
    mode_hits: dict[str, float] = {}
    for dimension in BIG_FIVE_DIMENSIONS:
        scores = [profile.big_five.model_dump()[dimension] for profile, _ in compiles]
        counts: dict[int, int] = {}
        for score in scores:
            counts[score] = counts.get(score, 0) + 1
        mode_hits[dimension] = max(counts.values()) / len(scores)
    failed = [dimension for dimension, rate in mode_hits.items() if rate < 2 / 3]
    report = {
        "grid_consistency": (grid_hits / grid_total if grid_total else 0.0),
        "big_five_mode_hit": mode_hits,
        "profiles": [json.loads(profile.model_dump_json()) for profile, _ in compiles],
        "canonical_texts": [text for _, text in compiles],
    }
    if report["grid_consistency"] != 1.0 or failed:
        raise IntegrityError(
            f"PROFILE_CHECK_FAILED: grid_consistency={report['grid_consistency']}, "
            f"dimensions below 2/3 mode hit: {failed}. Summary: "
            + json.dumps({k: report[k] for k in ("grid_consistency", "big_five_mode_hit")},
                         ensure_ascii=False))
    return report


def _compile_once(persona_text: str, runs: int, extract, mapping):
    """@brief 单次编译：提取 → 映射 → 规范文本（check 命令复用）。"""
    from worldmock.profile.textgen import render_persona_text

    extraction = extract(persona_text, n_runs=runs)
    profile = mapping(
        extraction.profile, persona_text=persona_text,
        source_model=extraction.model,
        consensus_runs=extraction.runs, consensus_agreement=extraction.agreement)
    return profile, render_persona_text(profile)


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
            request = RunRequest(root, scenario, output, identity, identity)
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
