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

from worldmock.foundation.errors import IntegrityError
from worldmock.foundation.atomic_write import atomic_write
from worldmock.foundation.serialization import canonical_json
from worldmock.runtime.monitoring.status import failure_message, observed_model_usage, read_status, write_status


def build_parser() -> argparse.ArgumentParser:
    """@brief 公开明确的新建、恢复、只读查询和足迹导出命令。"""
    parser = argparse.ArgumentParser(prog="worldmock", description="Auditable AS2 life simulation")
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="validate the complete environment inputs")
    validate.add_argument("--environment", type=Path, required=True)
    for name in ("run", "resume", "replay"):
        command = commands.add_parser(name, help="run a new world" if name == "run" else "restore into a new branch")
        command.add_argument("--environment", type=Path, required=True)
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
    return parser




def main(argv: list[str] | None = None) -> int:
    """@brief 成功输出机器可读结果，异常记录英文日志并返回非零状态。"""
    arguments = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        result = _dispatch(arguments)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        if arguments.command == "validate":
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
        from worldmock.configuration.inputs.validation import validate_environment
        return validate_environment(arguments.environment)
    logging.error("Unknown command: %s", command)
    raise ValueError(f"Unknown command: {command}")


def _run_command(arguments) -> dict:
    """@brief 新建或恢复都使用真实 AS2 入口，只在完整返回后标记完成；回执的 token_stats 由运行库里已落盘的模型响应
    累计（此时环境进程与 Ray 都已关闭）。"""
    from worldmock.runtime.lifecycle.runner import ResumeRequest, RunRequest, resume_world, run_world

    root, output = arguments.project_root.resolve(), arguments.output.resolve()
    environment = arguments.environment.absolute()
    if output.exists():
        raise IntegrityError("Run destination already exists")
    try:
        if arguments.command == "run":
            identity = uuid4().hex
            request = RunRequest(root, environment, output, identity, identity,
                                 arguments.same_second_continuation == "on")
            result = asyncio.run(run_world(request))
        else:
            request = ResumeRequest(root, environment, arguments.parent.resolve(), arguments.checkpoint, output)
            result = (_run_replay(request, arguments.intervention) if arguments.command == "replay"
                      else asyncio.run(resume_world(request)))
        receipt = json.loads(json.dumps(dataclasses.asdict(result), default=str))
        receipt["token_stats"] = observed_model_usage(output)
        atomic_write(output / "result.json", canonical_json(receipt))
        write_status(output, "completed", "stopped")
        return receipt
    except BaseException as error:
        if output.exists():
            state = "interrupted" if isinstance(error, KeyboardInterrupt) else "failed"
            write_status(output, state, "stopped", type(error).__name__, failure_message(error))
        raise


def _run_replay(base_request, intervention_path: Path):
    """@brief 按 UTF-8 显式读取严格世界条件干预，实际重演始终创建独立运行。"""
    from worldmock.state.decisions.results import CounterfactualIntervention
    from worldmock.runtime.lifecycle.runner import CounterfactualRequest, replay_world

    intervention = CounterfactualIntervention.model_validate_json(intervention_path.read_text(encoding="utf-8"))
    return asyncio.run(replay_world(CounterfactualRequest(base_request, intervention)))


def _export_command(arguments) -> dict:
    """@brief 在读取开始时冻结截止序号，并关闭持久连接。"""
    from worldmock.recording_engine.export.export import export_run
    from worldmock.recording_engine.storage.store import FootprintStore

    store = FootprintStore(arguments.run_dir)
    try:
        cutoff = store.last_event_seq if arguments.cutoff is None else arguments.cutoff
        return export_run(store, cutoff, arguments.output)
    finally:
        store.close()




if __name__ == "__main__":
    raise SystemExit(main())
