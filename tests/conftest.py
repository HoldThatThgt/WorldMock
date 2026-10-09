"""@brief 测试共享输入与正式环境验收。"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

import pytest

from scripts.bootstrap_environment import MISSING_NODE, PROFILE_ENV, component_status, require_profile

from tests.data_paths import ARTIFICIAL_ENVIRONMENT_ROOT
from tests.engine_fixture import engine_factory  # noqa: F401
from tests.frozen_inputs import FrozenInputs


ROOT = Path(__file__).resolve().parents[1]
SKIPPED = pytest.StashKey[bool]()

# 离线测试使用明确无效的本地配置值，不读取或要求供应商密钥。
os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "offline-test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://example.invalid")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")


@pytest.fixture
def catalog_dir(tmp_path: Path) -> Path:
    """@brief 复制真实人工目录，允许测试独立制造登记反例。"""
    target = tmp_path / "catalog"
    shutil.copytree(ARTIFICIAL_ENVIRONMENT_ROOT / "catalog", target)
    return target


@pytest.fixture
def loaded_catalog(catalog_dir: Path):
    """@brief 通过生产加载器登记真实人工目录。"""
    from worldmock.catalog.loader import load_catalog

    return load_catalog(catalog_dir, 7)


@pytest.fixture(scope="session")
def frozen_inputs():
    """@brief 每个 pytest 会话独立缓存；退出前核对文件与对象的完整摘要。"""
    cache = FrozenInputs()
    yield cache
    cache.verify()


def pytest_addoption(parser):
    """@brief 只选择测试所需的运行环境能力。"""
    parser.addoption("--environment-profile", choices=("release", "dev"),
                     default=os.environ.get(PROFILE_ENV, "release"))


def pytest_configure(config):
    """@brief 登记真实环境依赖，并初始化本次会话的跳过状态。"""
    config.addinivalue_line("markers", "requires_node: executes the actual Node.js viewer regression")
    config.stash[SKIPPED] = False


def pytest_sessionstart(session):
    """@brief release 缺组件在收集前失败；dev 保留明确缺失状态。"""
    status = component_status(ROOT)
    session.config.worldmock_component_status = status
    try:
        require_profile(status, session.config.getoption("--environment-profile"))
    except RuntimeError as error:
        raise pytest.UsageError(str(error)) from error


def pytest_collection_modifyitems(config, items):
    """@brief 正式验收拒绝预期失败；开发环境仅标记确实缺失的 Node 消费者。"""
    if config.getoption("--environment-profile") == "release":
        marked = [item.nodeid for item in items if item.get_closest_marker("xfail") is not None]
        if marked:
            logging.error("Expected failures cannot satisfy release acceptance: %s", ", ".join(marked))
            raise pytest.UsageError("xfail cannot satisfy release acceptance: " + ", ".join(marked))
    if "Node" in config.worldmock_component_status["missing_components"]:
        for item in items:
            if item.get_closest_marker("requires_node") is not None:
                item.add_marker(pytest.mark.skip(reason=MISSING_NODE))


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """@brief 只记录是否发生跳过，正式验收不以跳过代替通过。"""
    outcome = yield
    if outcome.get_result().skipped:
        item.config.stash[SKIPPED] = True


def pytest_sessionfinish(session, exitstatus):
    """@brief 缺组件或正式验收跳过均使当前 pytest 会话失败。"""
    missing = session.config.worldmock_component_status["missing_components"]
    release = session.config.getoption("--environment-profile") == "release"
    skipped = session.config.stash.get(SKIPPED, False)
    if exitstatus == 0 and (missing or release and skipped):
        logging.error("Test acceptance requires available components and no release skips")
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
