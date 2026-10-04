"""@brief 缺组件不能放行，跨卷复制和唯一辅助函数的行为验证。"""

import ast
import errno
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import bootstrap_environment as environment
from tests import conftest as profiles
from tests.file_copy import link_or_copy

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("missing", [["Node"], ["LabelKit"], ["Node", "LabelKit"]])
def test_release_rejects_each_missing_component(missing):
    with pytest.raises(RuntimeError, match="Required release components are missing"):
        environment.require_profile({"missing_components": missing}, "release")
    environment.require_profile({"missing_components": missing}, "dev")


def test_dev_marks_only_tests_that_need_a_missing_component():
    class Item:
        def __init__(self, needs):
            self.needs, self.marks = needs, []

        def get_closest_marker(self, marker):
            return marker if marker in self.needs else None

        def add_marker(self, marker):
            self.marks.append(marker)

    items = [Item({"requires_node"}), Item({"requires_labelkit"}), Item(set())]
    config = SimpleNamespace(worldmock_component_status={"missing_components": ["LabelKit"]},
                             getoption=lambda name: "dev")
    profiles.pytest_collection_modifyitems(config, items)
    assert [bool(item.marks) for item in items] == [False, True, False]
    assert items[1].marks[0].kwargs["reason"] == environment.MISSING_LABELKIT
    session = SimpleNamespace(config=config, exitstatus=0)
    profiles.pytest_sessionfinish(session, 0)
    assert session.exitstatus == pytest.ExitCode.TESTS_FAILED


def test_release_rejects_xfail_even_when_it_could_xpass():
    item = SimpleNamespace(nodeid="test_unaccepted", get_closest_marker=lambda marker: marker)
    config = SimpleNamespace(getoption=lambda name: "release")
    with pytest.raises(pytest.UsageError, match="xfail cannot satisfy release acceptance"):
        profiles.pytest_collection_modifyitems(config, [item])


def test_dev_environment_receipt_records_missing_without_absolute_paths(tmp_path, monkeypatch):
    requirements = tmp_path / "requirements"
    requirements.mkdir()
    (requirements / "lock.txt").write_bytes(b"locked\n")
    monkeypatch.setattr(environment, "component_status",
                        lambda root: {"node_version": None, "missing_components": ["Node", "LabelKit"]})
    monkeypatch.setattr(environment.subprocess, "check_output", lambda *args, **kwargs: "Python 3.12.13\n")
    result = environment.write_receipt(tmp_path, "dev", {"as2_source_sha256": "a" * 64})
    assert result["passed"] is False
    assert result["missing_components"] == ["Node", "LabelKit"]
    content = (tmp_path / ".work/environment-receipt.json").read_text()
    assert str(tmp_path) not in content
    assert json.loads(content) == result


def test_cross_volume_copy_preserves_real_bytes_and_is_independent(tmp_path, monkeypatch):
    source, target = tmp_path / "source", tmp_path / "target"
    source.write_bytes(b"frozen\x00corpus\r\n")
    monkeypatch.setattr(os, "link", lambda *args: (_ for _ in ()).throw(OSError(errno.EXDEV, "cross volume")))
    link_or_copy(str(source), str(target))
    assert target.read_bytes() == source.read_bytes()
    target.write_bytes(b"changed")
    assert source.read_bytes() == b"frozen\x00corpus\r\n"


def test_same_volume_copy_uses_a_link_and_other_errors_propagate(tmp_path, monkeypatch):
    source, target = tmp_path / "source", tmp_path / "target"
    source.write_bytes(b"frozen")
    link_or_copy(str(source), str(target))
    assert source.stat().st_ino == target.stat().st_ino
    monkeypatch.setattr(os, "link", lambda *args: (_ for _ in ()).throw(PermissionError("denied")))
    with pytest.raises(PermissionError, match="denied"):
        link_or_copy(str(source), str(tmp_path / "another"))


def test_all_test_tree_copies_use_the_cross_volume_helper():
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
