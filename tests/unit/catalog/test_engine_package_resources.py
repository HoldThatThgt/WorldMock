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


def test_catalog_has_four_required_files_three_optional_names_and_no_engine_rule_copies(catalog_dir):
    """目录必需四个作者文件，回调、距离声明和出行方式可以不放；夹具恰为清单加五个文件，生成的地图与固定规则不进入发布清单。"""
    catalog = load_catalog(catalog_dir, 7)
    assert REQUIRED_CATALOG_FILES == {
        "app-catalog.jsonl", "content-catalog.jsonl", "location.jsonl", "merchant-catalog.jsonl"}
    assert OPTIONAL_CATALOG_FILES == {"callbacks.jsonl", "map.jsonl", "travel-method.jsonl"}
    assert {entry.path for entry in catalog.manifest.files} == REQUIRED_CATALOG_FILES | {"travel-method.jsonl"}
    assert {path.name for path in catalog_dir.iterdir()} == REQUIRED_CATALOG_FILES | {
        "manifest.json", "travel-method.jsonl"}
    assert catalog.callback_states == {} and catalog.callback_rules == {}
    assert catalog.apps_by_travel_method["walking"] == ("amap",)
    assert catalog.apps_by_action["purchase"] == ("alipay", "wechat")


def test_fixture_travel_method_file_is_written_byte_for_byte(catalog_dir):
    """夹具的出行方式文件与导航 app 一行逐字节固定：键按升序、不带空格、每行以 LF 结尾。"""
    assert (catalog_dir / "travel-method.jsonl").read_bytes() == (
        '{"exertion":"light","name":"公共交通","price_per_km_fen":0,"speed_meters_per_second":5,'
        '"start_price_fen":200,"travel_method_id":"bus"}\n'
        '{"exertion":"light","name":"驾车","price_per_km_fen":200,"speed_meters_per_second":7,'
        '"start_price_fen":500,"travel_method_id":"driving"}\n'
        '{"exertion":"light","name":"打车","price_per_km_fen":300,"speed_meters_per_second":7,'
        '"start_price_fen":1400,"travel_method_id":"taxi"}\n').encode("utf-8")
    assert ('{"app_id":"amap","name":"高德地图","role":"function",'
            '"travel_method_ids":["bus","driving","taxi","walking"]}\n').encode("utf-8") in (
        catalog_dir / "app-catalog.jsonl").read_bytes()


def _project_with_data_package(tmp_path, monkeypatch):
    """临时项目：包内数据目录的副本加两份根依赖文件；返回（项目根, 数据目录）。"""
    from worldmock import run_setup

    project = tmp_path / "project"
    data = project / "src/worldmock/data"
    shutil.copytree(ROOT / "src/worldmock/data", data, ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copyfile(ROOT / name, project / name)
    # AS2 的固定身份属于独立门禁，此处只隔离其输入以检查真实代码哈希入口。
    monkeypatch.setattr(run_setup, "require_locked_as2", lambda: [])
    return project, data


@pytest.mark.parametrize("relative", PACKAGED_FILES)
def test_runtime_code_identity_covers_each_packaged_rule_file(tmp_path, monkeypatch, relative):
    """@brief 每份包内规则字节变化都会改变生产运行代码摘要，目录不需规则哈希。"""
    from worldmock import run_setup

    project, data = _project_with_data_package(tmp_path, monkeypatch)
    before = run_setup.runtime_code_sha256(project)
    with (data / relative).open("ab") as handle:
        handle.write(b"\nchanged packaged bytes\n")
    assert run_setup.runtime_code_sha256(project) != before


def test_runtime_code_identity_covers_a_non_python_rule_file_in_the_data_package(tmp_path, monkeypatch):
    """@brief 包内数据目录下不是 Python 源码的规则文件也进代码摘要：加一份、改它的字节、在子目录里再加一份都会改变
    摘要，__pycache__ 里的文件不算。仓库里现在没有这样的文件，所以用临时项目里的文件证明这条分支。"""
    from worldmock import run_setup

    project, data = _project_with_data_package(tmp_path, monkeypatch)
    empty = run_setup.runtime_code_sha256(project)
    (data / "rules.toml").write_bytes(b"limit = 1\n")
    added = run_setup.runtime_code_sha256(project)
    (data / "rules.toml").write_bytes(b"limit = 2\n")
    changed = run_setup.runtime_code_sha256(project)
    (data / "tables").mkdir()
    (data / "tables/extra.json").write_bytes(b"{}\n")
    nested = run_setup.runtime_code_sha256(project)
    assert len({empty, added, changed, nested}) == 4
    (data / "__pycache__").mkdir()
    (data / "__pycache__/rules.cpython-312.pyc").write_bytes(b"cache")
    assert run_setup.runtime_code_sha256(project) == nested
