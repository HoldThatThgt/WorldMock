"""@brief 全仓测试共享会话输入、自动标记和分片回执。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from worldmock.catalog.loader import load_catalog

from tests.data_paths import ARTIFICIAL_ENVIRONMENT_ROOT
from tests.engine_fixture import engine_factory  # noqa: F401
from tests.frozen_inputs import FrozenInputs
from tests.tier_support import TIERS, classify, selected, source_dependencies


@pytest.fixture
def catalog_dir(tmp_path: Path) -> Path:
    """@brief 复制真实人工目录，允许测试独立制造登记反例。"""
    target = tmp_path / "catalog"
    shutil.copytree(ARTIFICIAL_ENVIRONMENT_ROOT / "catalog", target)
    return target


@pytest.fixture
def loaded_catalog(catalog_dir: Path):
    """@brief 通过生产加载器登记真实人工目录。"""
    return load_catalog(catalog_dir)


@pytest.fixture(scope="session")
def frozen_inputs():
    """@brief 每个 pytest 会话独立缓存；退出前核对文件与对象的完整摘要。"""
    cache = FrozenInputs()
    yield cache
    cache.verify()


def pytest_addoption(parser):
    """@brief 添加运行脚本使用的收集、选择与回执参数。"""
    group = parser.getgroup("worldmock")
    group.addoption("--worldmock-tier", choices=TIERS, default="full")
    group.addoption("--worldmock-changed", action="append", default=[])
    group.addoption("--worldmock-manifest")
    group.addoption("--worldmock-selected")
    group.addoption("--worldmock-receipt")
    group.addoption("--environment-root", type=Path, help="explicit published environment for behavior regression")


def pytest_configure(config):
    """@brief 初始化本进程回执；标记在 pyproject.toml 中统一声明。"""
    config._worldmock_collected = []
    config._worldmock_reports = {}
    if config.getoption("--environment-root") is not None:
        from tests.data_paths import select_environment

        try:
            select_environment(config.getoption("--environment-root"))
        except ValueError as error:
            raise pytest.UsageError(str(error)) from error


def pytest_collection_modifyitems(config, items):
    """@brief 自动标记后按完整节点身份选择；未知节点、重复节点均拒绝收集。"""
    requested_path = config.getoption("--worldmock-selected")
    selection = None if requested_path is None else json.loads(Path(requested_path).read_text("utf-8"))
    if selection is not None and not isinstance(selection, dict):
        raise pytest.UsageError("Selected tests require the current run identity and nodes")
    if selection is not None and selection["run_id"] != Path(requested_path).parent.parent.name:
        raise pytest.UsageError("Selected tests belong to a different run")
    requested_nodes = None if selection is None else selection["nodes"]
    requested = None if requested_nodes is None else set(requested_nodes)
    if requested is not None and len(requested) != len(requested_nodes):
        raise pytest.UsageError("Selected tests contain duplicate node ids")
    records, kept, dropped = [], [], []
    for item in items:
        path = item.path.relative_to(config.rootpath).as_posix()
        fixtures = {name: definitions[-1].func for name, definitions in
                    item._fixtureinfo.name2fixturedefs.items() if definitions}
        markers = classify(item.path, item.fixturenames, item.originalname or item.name,
                           fixture_functions=fixtures, test_function=item.obj,
                           parameter_values=getattr(getattr(item, "callspec", None), "params", {}))
        for marker in markers:
            item.add_marker(getattr(pytest.mark, marker))
        record = {"nodeid": item.nodeid, "file": path, "markers": sorted(markers),
                  "imports": sorted(source_dependencies(str(item.path))[0])}
        records.append(record)
        choose = (item.nodeid in requested if requested is not None else
                  selected(record, config.getoption("--worldmock-tier"), config.getoption("--worldmock-changed")))
        (kept if choose else dropped).append(item)
    nodes = [record["nodeid"] for record in records]
    if len(nodes) != len(set(nodes)):
        raise pytest.UsageError("Test collection contains duplicate node ids")
    if requested is not None and requested - set(nodes):
        raise pytest.UsageError(f"Selected tests were not collected: {sorted(requested - set(nodes))}")
    config._worldmock_collected = [item.nodeid for item in kept]
    target = config.getoption("--worldmock-manifest")
    if target:
        manifest = {"run_id": Path(target).parent.name, "records": records}
        Path(target).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if dropped:
        config.hook.pytest_deselected(items=dropped)
    items[:] = kept


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """@brief 记录 setup/call/teardown 的真实结果和用时，缺 call 的跳过仍有明确证据。"""
    outcome = yield
    report = outcome.get_result()
    entry = item.config._worldmock_reports.setdefault(item.nodeid, {"duration": 0.0, "phases": {}})
    entry["duration"] += report.duration
    entry["phases"][report.when] = {"outcome": report.outcome, "duration": report.duration}


def pytest_sessionfinish(session, exitstatus):
    """@brief 每个子进程写独立回执；崩溃或被终止后的缺失回执不能冒充成功。"""
    target = session.config.getoption("--worldmock-receipt")
    if target:
        value = {"run_id": Path(target).parent.parent.name,
                 "exit_code": int(exitstatus), "collected": session.config._worldmock_collected,
                 "tests": session.config._worldmock_reports}
        Path(target).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
