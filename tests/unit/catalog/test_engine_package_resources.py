"""@brief 规则只从包读取，目录身份与代码身份各自钉住所属真实字节。"""

import shutil
from pathlib import Path

import pytest

from worldmock.catalog.identity import OPTIONAL_CATALOG_FILES, REQUIRED_CATALOG_FILES
from worldmock.catalog.loader import load_catalog


ROOT = Path(__file__).resolve().parents[3]
PACKAGED_FILES = tuple(path.relative_to(ROOT / "src/worldmock/data") for path in
                      (ROOT / "src/worldmock/data").rglob("*")
                      if path.is_file() and "__pycache__" not in path.parts)


def test_catalog_has_four_required_files_two_optional_names_and_no_engine_rule_copies(catalog_dir):
    """目录必需四个作者文件，回调和距离声明可以不放；生成的地图与固定规则不进入发布清单。"""
    catalog = load_catalog(catalog_dir, 7)
    assert REQUIRED_CATALOG_FILES == {
        "app-catalog.jsonl", "content-catalog.jsonl", "location.jsonl", "merchant-catalog.jsonl"}
    assert OPTIONAL_CATALOG_FILES == {"callbacks.jsonl", "map.jsonl"}
    assert {entry.path for entry in catalog.manifest.files} == REQUIRED_CATALOG_FILES
    assert {path.name for path in catalog_dir.iterdir()} == REQUIRED_CATALOG_FILES | {"manifest.json"}
    assert catalog.callback_states == {} and catalog.callback_rules == {}
    assert catalog.apps_by_action["walk"] == ("amap",)
    assert catalog.apps_by_action["purchase"] == ("alipay", "wechat")


@pytest.mark.parametrize("relative", PACKAGED_FILES)
def test_runtime_code_identity_covers_each_packaged_rule_file(tmp_path, monkeypatch, relative):
    """@brief 每份包内规则字节变化都会改变生产运行代码摘要，目录不需规则哈希。"""
    from worldmock import run_setup

    project = tmp_path / "project"
    data = project / "src/worldmock/data"
    shutil.copytree(ROOT / "src/worldmock/data", data, ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copyfile(ROOT / name, project / name)
    # AS2 的固定身份属于独立门禁，此处只隔离其输入以检查真实代码哈希入口。
    monkeypatch.setattr(run_setup, "require_locked_as2", lambda: [])
    before = run_setup.runtime_code_sha256(project)
    with (data / relative).open("ab") as handle:
        handle.write(b"\nchanged packaged bytes\n")
    assert run_setup.runtime_code_sha256(project) != before
