"""@brief 新环境包两层闭集检查的真实文件拒绝行为。"""

from itertools import combinations
from pathlib import Path

import pytest

from worldmock.resource_engine.registration.admission import validate_environment_entries
from worldmock.resource_engine.registration.errors import RegistrationError


CATALOG_FILES = (
    "manifest.json", "app-catalog.jsonl", "content-catalog.jsonl", "location.jsonl", "merchant-catalog.jsonl",
)
OPTIONAL_FILES = ("callbacks.jsonl", "map.jsonl", "travel-method.jsonl")


@pytest.fixture
def environment_root(tmp_path):
    """@brief 构造真实闭集文件，不补齐尚未确认的人设与目录字段。"""
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    for name in CATALOG_FILES:
        (catalog / name).write_bytes(b"")
    (tmp_path / "persona.toml").write_bytes(b"")
    (tmp_path / "config.toml").write_bytes(b"")
    return tmp_path


def test_environment_boundary_accepts_exact_entries_without_parsing_them(environment_root):
    """@brief 闭集检查独立于各输入严格模型，允许它们各自汇总诊断。"""
    validate_environment_entries(environment_root)


@pytest.mark.parametrize("present", [subset for size in range(len(OPTIONAL_FILES) + 1)
                                     for subset in combinations(OPTIONAL_FILES, size)])
def test_environment_boundary_accepts_any_subset_of_optional_catalog_files(environment_root, present):
    """@brief 可选文件放与不放都在闭集内，闭集检查不要求它们存在。"""
    for name in present:
        (environment_root / "catalog" / name).write_bytes(b"")
    validate_environment_entries(environment_root)


@pytest.mark.parametrize("relative", [
    "README.md", "sources", "corpora", "scenarios", "catalog/map.json", "catalog/actions.jsonl",
    "catalog/travel_method.jsonl", "catalog/travel-methods.jsonl", "catalog/callback.jsonl", "catalog/map.jsonl.bak",
    "catalog/location-type.jsonl", "catalog/item_callback.py", "catalog/persona.toml", "catalog/config.toml",
    "catalog/apps.json", "catalog/locations.json", "catalog/goods.json", "catalog/actions.json",
    "catalog/contents.json",
])
def test_environment_boundary_rejects_extra_entries_with_actual_path(environment_root, relative, caplog):
    """@brief 根目录与目录内退役输入均拒绝，错误详情指出真实额外路径。"""
    extra = environment_root / relative
    extra.write_bytes(b"not accepted")
    with pytest.raises(RegistrationError) as captured:
        validate_environment_entries(environment_root)
    assert captured.value.code == "ENVIRONMENT_STALE_FILE"
    assert captured.value.message == "The environment contains an entry outside its declared package."
    assert captured.value.detail == str(extra)
    assert "ENVIRONMENT_STALE_FILE" in caplog.text


def test_environment_boundary_reports_directory_itself_without_walking_it(environment_root, monkeypatch):
    """@brief 包外目录以直接条目拒绝，不读取目录里的内容。"""
    extra = environment_root / "sources"
    extra.mkdir()
    original = Path.iterdir

    def guarded_iterdir(path):
        if path == extra:
            raise AssertionError("An undeclared directory must not be traversed")
        return original(path)

    monkeypatch.setattr(Path, "iterdir", guarded_iterdir)
    with pytest.raises(RegistrationError) as captured:
        validate_environment_entries(environment_root)
    assert captured.value.detail == str(extra)


@pytest.mark.parametrize("target", ["root", "catalog"])
def test_environment_boundary_rejects_linked_directories(environment_root, monkeypatch, target):
    """@brief 环境根或目录是链接时不遍历，也不接受其内容。"""
    linked = environment_root if target == "root" else environment_root / "catalog"
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == linked or original(path))
    with pytest.raises(RegistrationError) as captured:
        validate_environment_entries(environment_root)
    assert captured.value.code == "ENVIRONMENT_IDENTITY_MISMATCH"
    assert captured.value.detail == str(linked)


def test_environment_boundary_leaves_missing_declared_files_to_input_loaders(environment_root):
    """@brief 缺少输入不伪装为包外条目，不提前掩盖其他可检查输入。"""
    (environment_root / "persona.toml").unlink()
    for name in CATALOG_FILES:
        (environment_root / "catalog" / name).unlink()
    (environment_root / "catalog").rmdir()
    validate_environment_entries(environment_root)


def test_environment_boundary_reports_unreadable_root(tmp_path, monkeypatch):
    """@brief 文件系统读取失败记录稳定文件边界错误与真实路径。"""
    original = Path.iterdir

    def guarded_iterdir(path):
        if path == tmp_path:
            raise OSError("Directory access denied")
        return original(path)

    monkeypatch.setattr(Path, "iterdir", guarded_iterdir)
    with pytest.raises(RegistrationError) as captured:
        validate_environment_entries(tmp_path)
    assert captured.value.code == "ENVIRONMENT_IDENTITY_MISMATCH"
    assert str(tmp_path) in captured.value.detail
