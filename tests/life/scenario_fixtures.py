"""@brief 独立测试从人工环境建立完整场景副本，始终经过现行结构与目录准入。"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from tests.data_paths import ARTIFICIAL_ENVIRONMENT_ROOT


def read_scenario(name: str) -> dict:
    """@brief 读取人工环境的当前场景声明，不生成退役字段。"""
    return json.loads((ARTIFICIAL_ENVIRONMENT_ROOT / "scenarios" / name).read_text(encoding="utf-8"))


def write_scenario(document: dict, root: Path) -> Path:
    """@brief 复制完整目录与语料，场景使用可跨机器解析的相对引用。"""
    for name in ("catalog",):
        shutil.copytree(ARTIFICIAL_ENVIRONMENT_ROOT / name, root / name)
    scenarios = root / "scenarios"
    scenarios.mkdir(parents=True)
    path = scenarios / "scenario.json"
    payload = document
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def run_identity(document: dict, run_id: str):
    """@brief 运行身份绑定当前真实工具、提示、阶段和代码字节。"""
    from worldmock.life.models import RunIdentity
    from worldmock.run_setup import _model_identity, runtime_code_sha256

    return RunIdentity(
        run_id=run_id, source_group_id=f"group-{run_id}",
        agent_ids=tuple(person["agent_id"] for person in document["agents"]),
        start_at=datetime.fromisoformat(document["start_at"]).isoformat(),
        end_at=datetime.fromisoformat(document["end_at"]).isoformat(), timezone=document["timezone"],
        code_sha256=runtime_code_sha256(Path(__file__).resolve().parents[2]), model_identity=_model_identity())


def create_engine(name: str, mutate, root: Path, run_id: str):
    """@brief 对明确声明的场景变更执行严格准入并创建真实生活引擎。"""
    from worldmock.life.engine import LifeEngine

    document = read_scenario(name)
    mutate(document)
    path = write_scenario(document, root / f"environment-{run_id}")
    return LifeEngine.create(path, root / f"run-{run_id}", run_identity(document, run_id))
