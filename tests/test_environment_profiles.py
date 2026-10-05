"""@brief 缺组件不能放行，可变测试世界必须与正式冻结输入隔离。"""

import ast
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import bootstrap_environment as environment
from tests import conftest as profiles

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


def test_purchase_world_edits_leave_formal_input_bytes_and_inodes_unchanged(tmp_path, monkeypatch):
    """@brief 真实购买夹具改地图、供给和清单后，源文件字节及文件身份始终不变。"""
    from tests.life import purchase_worlds

    source_root = tmp_path / "source"
    for name in ("corpora", "catalog", "scenarios"):
        shutil.copytree(ROOT / name, source_root / name)
    monkeypatch.setattr(purchase_worlds, "ROOT", source_root)
    monkeypatch.setattr(purchase_worlds, "SCENARIO", source_root / "scenarios/white-collar-12-weeks.json")
    corpus = source_root / "corpora"
    relative_paths = ["manifest.json", "zhangjiang-map/locations.json",
                      "zhangjiang-map/20260105/dataset.json", "zhangjiang-map/20260105/supplies.json"]
    before = {relative: ((corpus / relative).read_bytes(), (corpus / relative).stat().st_ino)
              for relative in relative_paths}
    locations = json.loads((corpus / relative_paths[1]).read_text("utf-8"))["locations"]
    supplies = json.loads((corpus / relative_paths[3]).read_text("utf-8"))["supplies"]
    location_id, supply_id = locations[0]["location_id"], supplies[0]["supply_id"]
    world = purchase_worlds.build_world(tmp_path / "world", {}, places={location_id: "隔离地点"},
                                       supplies={supply_id: {"required_qualifications": ["isolation-oracle"]}})
    changed_locations = json.loads((world.corpus / relative_paths[1]).read_text("utf-8"))["locations"]
    changed_supplies = json.loads((world.corpus / relative_paths[3]).read_text("utf-8"))["supplies"]
    assert changed_locations[0]["name"] == "隔离地点"
    assert changed_supplies[0]["required_qualifications"] == ["isolation-oracle"]
    for relative, (content, inode) in before.items():
        source, copied = corpus / relative, world.corpus / relative
        assert source.read_bytes() == content
        assert source.stat().st_ino == inode
        assert copied.stat().st_ino != inode
        copied.write_bytes(b"isolated edit\n")
        assert source.read_bytes() == content


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
