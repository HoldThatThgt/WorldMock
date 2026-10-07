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


def test_chinese_font_missing_never_adds_skip_and_keeps_dev_nonpassing():
    """@brief 字体缺失保留评估实际执行，不新增 skip，且不能取得开发环境最终通过。"""
    class Item:
        def __init__(self, needs):
            self.needs, self.marks = needs, []

        def get_closest_marker(self, marker):
            return marker if marker in self.needs else None

        def add_marker(self, marker):
            self.marks.append(marker)

    items = [Item({"requires_node"}), Item({"requires_labelkit"}), Item(set())]
    status = {"missing_components": ["Chinese font"]}
    config = SimpleNamespace(worldmock_component_status=status, getoption=lambda name: "dev")
    profiles.pytest_collection_modifyitems(config, items)
    assert [item.marks for item in items] == [[], [], []]
    session = SimpleNamespace(config=config, exitstatus=0)
    profiles.pytest_sessionfinish(session, 0)
    assert session.exitstatus == pytest.ExitCode.TESTS_FAILED
    with pytest.raises(RuntimeError, match="Required release components are missing: Chinese font"):
        environment.require_profile(status, "release")


def test_dev_known_missing_consumers_keep_exact_skips_when_font_is_also_missing():
    """@brief 混合缺失仍只标记现有 Node 与 LabelKit 消费者，字体不制造额外跳过。"""
    class Item:
        def __init__(self, needs):
            self.needs, self.marks = needs, []

        def get_closest_marker(self, marker):
            return marker if marker in self.needs else None

        def add_marker(self, marker):
            self.marks.append(marker)

    items = [Item({"requires_node"}), Item({"requires_labelkit"}), Item(set())]
    config = SimpleNamespace(worldmock_component_status={"missing_components": ["Node", "LabelKit", "Chinese font"]},
                             getoption=lambda name: "dev")
    profiles.pytest_collection_modifyitems(config, items)
    assert [len(item.marks) for item in items] == [1, 1, 0]
    assert items[0].marks[0].kwargs["reason"] == environment.MISSING_NODE
    assert items[1].marks[0].kwargs["reason"] == environment.MISSING_LABELKIT


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
                        lambda root: {"node_version": None, "missing_components": ["Node", "LabelKit"]})
    monkeypatch.setattr(environment.subprocess, "check_output",
                        lambda *args, **kwargs: json.dumps({"implementation": "CPython", "version": "3.12.13"}))
    result = environment.write_receipt(tmp_path, "dev", {"as2_source_sha256": "a" * 64})
    assert result["passed"] is False
    assert result["missing_components"] == ["Node", "LabelKit"]
    content = (tmp_path / ".work/environment-receipt.json").read_text()
    assert str(tmp_path) not in content
    assert json.loads(content) == result


def test_purchase_world_edits_leave_formal_input_bytes_and_inodes_unchanged(tmp_path):
    """@brief 新目录构造器实际改食品与地点并登记后，源环境字节和文件身份保持不变。"""
    from tests.catalog.conftest import read_payload, write_payload
    from worldmock.life.world_setup import load_runtime_scenario

    source_root, world = tmp_path / "source", tmp_path / "world"
    shutil.copytree(ARTIFICIAL_ENVIRONMENT_ROOT, source_root)
    shutil.copytree(source_root, world)
    inputs = [path.relative_to(source_root) for path in source_root.rglob("*") if path.is_file()]
    before = {relative: ((source_root / relative).read_bytes(), (source_root / relative).stat().st_ino)
              for relative in inputs}
    catalog = world / "catalog"
    locations = read_payload(catalog, "locations.json")
    locations["locations"][0]["name"] = "隔离地点"
    write_payload(catalog, "locations.json", locations)
    foods = read_payload(catalog, "foods.json")
    foods["foods"][0]["price_fen"] += 123
    write_payload(catalog, "foods.json", foods)
    isolated, _ = load_runtime_scenario(world / "scenarios/single-person.json")
    original, _ = load_runtime_scenario(source_root / "scenarios/single-person.json")
    assert isolated.catalog.locations[locations["locations"][0]["location_id"]].name == "隔离地点"
    food_id = foods["foods"][0]["food_id"]
    assert isolated.catalog.foods[food_id].price_fen == original.catalog.foods[food_id].price_fen + 123
    for relative, (content, inode) in before.items():
        source_file, copied = source_root / relative, world / relative
        assert source_file.read_bytes() == content
        assert source_file.stat().st_ino == inode
        assert copied.stat().st_ino != inode
        copied.write_bytes(b"isolated edit\n")
        assert source_file.read_bytes() == content


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
