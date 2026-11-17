"""WorldMock 故障类：生产代码里没有任何类定义打包钩子，构造故障仍写恰好一条英文错误日志。"""

import ast
import logging
from pathlib import Path

import pytest

from worldmock.foundation.errors import EnvironmentInputError, IntegrityError, PreconditionError, ProtocolError
from worldmock.resource_engine.registration.errors import RegistrationError

ROOT = Path(__file__).resolve().parents[3]

#: 只为跨进程传递异常而存在的四种打包钩子。
PICKLING_HOOKS = frozenset({"__reduce__", "__reduce_ex__", "__getstate__", "__setstate__"})


def test_production_code_defines_no_pickling_hooks():
    """src/worldmock 下没有任何类的直接成员是四种打包钩子之一；三个曾经定义 __reduce__ 的故障类自己不再带它。"""
    offenders = []
    for path in sorted((ROOT / "src" / "worldmock").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.ClassDef):
                continue
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) and member.name in PICKLING_HOOKS:
                    offenders.append(f"{path.relative_to(ROOT).as_posix()}:{node.name}.{member.name}")
    assert offenders == []
    for kind in (EnvironmentInputError, PreconditionError, RegistrationError):
        assert "__reduce__" not in vars(kind), kind.__name__


@pytest.mark.parametrize("build", [
    lambda: IntegrityError("Run heartbeat is invalid"),
    lambda: ProtocolError("Model request evidence must not carry transport attempts"),
    lambda: EnvironmentInputError("config.toml", "RUN_CONFIG_INVALID", "model context metadata is unavailable"),
    lambda: RegistrationError("MAP_TOO_SMALL"),
])
def test_fault_classes_still_log_when_constructed(build, caplog):
    """构造故障仍写恰好一条英文 ERROR「WorldMock failure: …」：多数抛出点只靠它留下错误日志。"""
    with caplog.at_level(logging.ERROR):
        build()
    records = [record for record in caplog.records if record.getMessage().startswith("WorldMock failure: ")]
    assert len(records) == 1 and records[0].levelno == logging.ERROR
