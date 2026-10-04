"""@brief 所有测试使用统一环境 profile，缺失部件不得静默放行。"""

import os
from pathlib import Path

import pytest

from scripts.bootstrap_environment import (MISSING_LABELKIT, MISSING_NODE, PROFILE_ENV,
                                           component_status, require_profile)

ROOT = Path(__file__).resolve().parents[1]

# 离线测试使用明确无效的本地配置值，不读取或要求供应商密钥。
os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "offline-test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://example.invalid")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")


def pytest_addoption(parser):
    """@brief 公开与引导一致的 release/dev profile 选择。"""
    parser.addoption("--environment-profile", choices=("release", "dev"),
                     default=os.environ.get(PROFILE_ENV, "release"))


def pytest_configure(config):
    """@brief 只登记实际必需组件的固定标记。"""
    config.addinivalue_line("markers", "requires_node: executes the actual Node.js viewer regression")
    config.addinivalue_line("markers", "requires_labelkit: executes the isolated LabelKit CLI")


def pytest_sessionstart(session):
    """@brief release 缺组件在收集前失败；dev 保留明确缺失状态。"""
    status = component_status(ROOT)
    session.config.worldmock_component_status = status
    try:
        require_profile(status, session.config.getoption("--environment-profile"))
    except RuntimeError as error:
        raise pytest.UsageError(str(error)) from error


def pytest_collection_modifyitems(config, items):
    """@brief dev 只跳过实际缺失组件的检查，每个跳过有固定英文原因。"""
    if config.getoption("--environment-profile") == "release":
        marked = [item.nodeid for item in items if item.get_closest_marker("xfail") is not None]
        if marked:
            raise pytest.UsageError("xfail cannot satisfy release acceptance: " + ", ".join(marked))
    missing = config.worldmock_component_status["missing_components"]
    reasons = {"Node": ("requires_node", MISSING_NODE), "LabelKit": ("requires_labelkit", MISSING_LABELKIT)}
    for name in missing:
        marker, reason = reasons[name]
        for item in items:
            if item.get_closest_marker(marker) is not None:
                item.add_marker(pytest.mark.skip(reason=reason))


def pytest_sessionfinish(session, exitstatus):
    """@brief dev 缺失部件不能以零退出码作为完整验收。"""
    if exitstatus == 0 and session.config.worldmock_component_status["missing_components"]:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
