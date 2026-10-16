"""WorldMock 故障类穿过进程（pickle、Ray）后保留类型、参数与实例字典，环境 actor 的错误以原始类型到达驱动。"""

import ast
import logging
import pickle
from pathlib import Path

import pytest
from ray import cloudpickle
from ray.exceptions import RayError, RayTaskError

import worldmock.catalog.errors  # noqa: F401  # 登记 RegistrationError 子类
from worldmock.catalog.errors import RegistrationError
from worldmock.errors import (BusinessRuleError, EnvironmentInputError, IntegrityError, PreconditionError,
                              ProtocolError, WorldMockError)
from worldmock.status import failure_message

ROOT = Path(__file__).resolve().parents[3]

#: 每个故障类的构造参数；新增子类必须加在这里。
SAMPLES = {
    WorldMockError: ("generic failure",),
    IntegrityError: ("Checkpoint is missing or its record is damaged",),
    EnvironmentInputError: ("config.toml", "RUN_CONFIG_INVALID", "model context metadata is unavailable"),
    ProtocolError: ("Model exchange transport attempts are invalid",),
    BusinessRuleError: ("ACTION_REQUEST_INVALID",),
    PreconditionError: ("PRECONDITION_UNMET", ["required_location_id"], {"tool": "start_main_activity"}, "不在家"),
    RegistrationError: ("MAP_TOO_SMALL", "only one place"),
}


def _subclasses(root: type) -> set[type]:
    """递归收集 root 的全部子类（含 root）。"""
    found = {root}
    for child in root.__subclasses__():
        found |= _subclasses(child)
    return found


def _declared_error_classes() -> set[str]:
    """按名字在 src/worldmock 与 custom 的源码里传递地找出派生自 WorldMockError 的类。"""
    classes = []
    for directory in ("src/worldmock", "custom"):
        for path in (ROOT / directory).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ClassDef):
                    bases = {getattr(base, "id", getattr(base, "attr", None)) for base in node.bases}
                    classes.append((node.name, bases))
    names, grown = {"WorldMockError"}, True
    while grown:
        grown = False
        for name, bases in classes:
            if name not in names and bases & names:
                names.add(name)
                grown = True
    return names


def _same(left: BaseException, right: BaseException) -> bool:
    """两个异常的类型、参数与实例字典逐值相同。"""
    return type(left) is type(right) and left.args == right.args and vars(left) == vars(right)


def test_every_worldmock_error_survives_pickle_and_ray_transport():
    """每个故障类经 pickle 与 ray.cloudpickle 往返后类型、args 与 vars() 不变，带 add_note 附加说明的实例也一样；
    子类清单由运行时与源码两边核对，新增故障类必须登记到 SAMPLES。"""
    assert _subclasses(WorldMockError) == set(SAMPLES)
    assert _declared_error_classes() == {kind.__name__ for kind in SAMPLES}
    for kind, arguments in SAMPLES.items():
        plain = kind(*arguments)
        noted = kind(*arguments)
        noted.add_note("context")
        for error in (plain, noted):
            for module in (pickle, cloudpickle):
                restored = module.loads(module.dumps(error))
                assert _same(restored, error), (kind.__name__, module.__name__)
        assert pickle.loads(pickle.dumps(noted)).__notes__ == ["context"]


def test_actor_error_reaches_the_driver_as_its_own_type():
    """环境 actor 的 EnvironmentInputError 经 Ray 字节往返后仍是任务错误的原因，as_instanceof_cause 得到它的
    双重类型，status.json 的一行原因写出原始错误。"""
    cause = EnvironmentInputError("config.toml", "RUN_CONFIG_INVALID", "model context metadata is unavailable")
    error = RayTaskError("LifeEnvActor.init", "Traceback (most recent call last): ...", cause,
                         proctitle="p", pid=1, ip="127.0.0.1")
    restored = RayError.from_bytes(error.to_bytes())
    assert isinstance(restored, RayTaskError)
    assert isinstance(restored.cause, EnvironmentInputError)
    assert (restored.cause.path, restored.cause.code, restored.cause.reason) == (
        "config.toml", "RUN_CONFIG_INVALID", "model context metadata is unavailable")
    dual = restored.as_instanceof_cause()
    assert type(dual).__name__ == "RayTaskError(EnvironmentInputError)"
    assert failure_message(dual) == "EnvironmentInputError: model context metadata is unavailable"


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
