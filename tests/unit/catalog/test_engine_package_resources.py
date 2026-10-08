"""@brief 规则只从包读取，目录身份与代码身份各自钉住所属真实字节。"""

import shutil
from pathlib import Path

import pytest

from worldmock.catalog.identity import REQUIRED_CATALOG_FILES
from worldmock.catalog.loader import load_catalog


ROOT = Path(__file__).resolve().parents[3]
PACKAGED_FILES = tuple(path.relative_to(ROOT / "src/worldmock/data") for path in
                      (ROOT / "src/worldmock/data").rglob("*")
                      if path.is_file() and "__pycache__" not in path.parts)


def test_catalog_has_only_six_object_files_and_no_engine_rule_copies(catalog_dir):
    """@brief 目录不再提供规则副本或语料，导航与支付应用也不需要虚构内容。"""
    catalog = load_catalog(catalog_dir)
    assert REQUIRED_CATALOG_FILES == {
        "apps.json", "locations.json", "goods.json", "actions.json", "contents.json", "map.json"}
    assert len(catalog.manifest.files) == 6
    assert {path.name for path in catalog_dir.iterdir()} == REQUIRED_CATALOG_FILES | {"manifest.json"}
    assert not catalog.actions_by_app["amap"]
    assert not catalog.actions_by_app["alipay"]


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
