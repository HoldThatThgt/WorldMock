"""@brief 缺组件不能放行，可变测试世界必须与正式冻结输入隔离。"""

from tests.data_paths import ARTIFICIAL_ENVIRONMENT_ROOT

import ast
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import bootstrap_environment as environment
from tests import conftest as profiles

ROOT = Path(__file__).resolve().parents[3]


def _collection_config(missing):
    """@brief 提供环境选择参数，只隔离环境部件的可用性。"""
    options = {"--environment-profile": "dev"}
    return SimpleNamespace(worldmock_component_status={"missing_components": missing},
                           rootpath=ROOT, getoption=options.__getitem__, stash=pytest.Stash())


class _CollectionItem:
    """@brief 模拟 pytest 环境依赖标记。"""

    def __init__(self, needs):
        self.needs, self.marks = needs, []
        self.path = Path(__file__).resolve()
        self.name = self.originalname = "test_" + ("_".join(sorted(needs)) or "ordinary")
        self.nodeid = "test_environment_profiles.py::" + self.name

    def get_closest_marker(self, marker):
        return marker if marker in self.needs else None

    def add_marker(self, marker):
        self.marks.append(marker)


@pytest.mark.parametrize("missing", [["Node"], ["Chinese font"], ["Node", "Chinese font"]])
def test_release_rejects_each_missing_component(missing):
    with pytest.raises(RuntimeError, match="Required release components are missing"):
        environment.require_profile({"missing_components": missing}, "release")
    environment.require_profile({"missing_components": missing}, "dev")


def test_dev_marks_only_tests_that_need_a_missing_component():
    items = [_CollectionItem({"requires_node"}), _CollectionItem(set())]
    config = _collection_config(["Node"])
    profiles.pytest_collection_modifyitems(config, items)
    assert [bool(item.marks) for item in items] == [True, False]
    assert items[0].marks[0].kwargs["reason"] == environment.MISSING_NODE
    session = SimpleNamespace(config=config, exitstatus=0)
    profiles.pytest_sessionfinish(session, 0)
    assert session.exitstatus == pytest.ExitCode.TESTS_FAILED


def test_chinese_font_missing_never_adds_skip_and_keeps_dev_nonpassing():
    """@brief 字体缺失保留评估实际执行，不新增 skip，且不能取得开发环境最终通过。"""
    items = [_CollectionItem({"requires_node"}), _CollectionItem(set())]
    status = {"missing_components": ["Chinese font"]}
    config = _collection_config(status["missing_components"])
    profiles.pytest_collection_modifyitems(config, items)
    assert [item.marks for item in items] == [[], []]
    session = SimpleNamespace(config=config, exitstatus=0)
    profiles.pytest_sessionfinish(session, 0)
    assert session.exitstatus == pytest.ExitCode.TESTS_FAILED
    with pytest.raises(RuntimeError, match="Required release components are missing: Chinese font"):
        environment.require_profile(status, "release")


def test_dev_known_missing_consumers_keep_exact_skips_when_font_is_also_missing():
    """@brief 混合缺失只标记 Node 消费者，字体不制造额外跳过。"""
    items = [_CollectionItem({"requires_node"}), _CollectionItem(set())]
    config = _collection_config(["Node", "Chinese font"])
    profiles.pytest_collection_modifyitems(config, items)
    assert [len(item.marks) for item in items] == [1, 0]
    assert items[0].marks[0].kwargs["reason"] == environment.MISSING_NODE


def test_release_rejects_expected_failure_even_when_it_could_pass():
    item = SimpleNamespace(nodeid="test_unaccepted", get_closest_marker=lambda marker: marker)
    config = SimpleNamespace(getoption=lambda name: "release")
    with pytest.raises(pytest.UsageError, match="x" + "fail cannot satisfy release acceptance"):
        profiles.pytest_collection_modifyitems(config, [item])


def test_dev_environment_receipt_records_missing_without_absolute_paths(tmp_path, monkeypatch):
    requirements = tmp_path / "requirements"
    requirements.mkdir()
    (requirements / "lock.txt").write_bytes(b"locked\n")
    (tmp_path / ".python-version").write_text("3.12.13\n")
    monkeypatch.setattr(environment, "component_status",
                        lambda root: {"node_version": None, "missing_components": ["Node", "Chinese font"]})
    monkeypatch.setattr(environment.subprocess, "check_output",
                        lambda *args, **kwargs: json.dumps({"implementation": "CPython", "version": "3.12.13"}))
    result = environment.write_receipt(tmp_path, "dev", {"as2_source_sha256": "a" * 64})
    assert result["passed"] is False
    assert result["missing_components"] == ["Node", "Chinese font"]
    content = (tmp_path / ".work/environment-receipt.json").read_text()
    assert str(tmp_path) not in content
    assert json.loads(content) == result




def test_mutable_test_tree_copies_do_not_use_hard_links():
    violations = []
    for path in (ROOT / "tests").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                value = keyword.value
                if keyword.arg == "copy_function" and isinstance(value, ast.Attribute):
                    if isinstance(value.value, ast.Name) and value.value.id == "os" and value.attr == "link":
                        violations.append(path.relative_to(ROOT).as_posix())
    assert violations == []
