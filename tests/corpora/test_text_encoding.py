"""@brief 语料准备的文本读写必须显式 UTF-8，冻结地图的声明文字与路线来源不随本机码页走样。

Python 读取文本时若不给 encoding，会按区域设置的码页解码（本机 Windows 为 cp936）。地图声明与
路线目录是含非 ASCII 文字的 UTF-8 JSON（如 OpenStreetMap 署名中的「©」），按码页解码常常不报错，而是静默
得到乱码，再随发布的 dataset.json 与语料清单写出。这里用 io.text_encoding 把缺省编码模拟成 GBK，
断言与测试机的区域设置无关；另用 AST 核对 corpora 包内的全部文本读写都显式给出编码。
"""

from __future__ import annotations

import ast
import importlib
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace

import worldmock.corpora as corpora_package
from worldmock.corpora import map_prepare

_REPO = Path(__file__).resolve().parents[2]
_CORPORA_SOURCE = _REPO / "src" / "worldmock" / "corpora"
_FROZEN = _REPO / "corpora"
_FETCH = "worldmock.corpora.fetch"
_TEXT_CALLS = {"read_text", "write_text", "open"}
_LICENSE = "© OpenStreetMap 贡献者（ODbL-1.0）"


def _simulate_gbk_locale(monkeypatch) -> None:
    """@brief 让未指明编码的 Path.read_text 按 GBK 解码，模拟中文 Windows 的区域设置。"""
    real = io.text_encoding

    def gbk_locale(encoding, stacklevel=2):
        return "gbk" if encoding is None else real(encoding, stacklevel + 1)

    monkeypatch.setattr(io, "text_encoding", gbk_locale)


def _is_binary_open(call: ast.Call) -> bool:
    """@brief open 调用是否以二进制模式打开（位置参数或 mode 关键字中含 b）。"""
    modes = [arg for arg in call.args[1:2] if isinstance(arg, ast.Constant)]
    modes += [keyword.value for keyword in call.keywords
              if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant)]
    return any(isinstance(mode.value, str) and "b" in mode.value for mode in modes)


def _implicit_encoding_calls(path: Path, base: Path = _REPO) -> list[str]:
    """@brief 列出文件中未显式给出 encoding 的文本读写调用（路径相对 base 显示）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name not in _TEXT_CALLS or any(keyword.arg == "encoding" for keyword in node.keywords):
            continue
        if name == "open" and (_is_binary_open(node) or isinstance(func, ast.Attribute)):
            continue
        found.append(f"{path.relative_to(base).as_posix()}:{node.lineno} {name}")
    return found


def test_corpora_text_io_names_its_encoding() -> None:
    """语料包内每一次文本读写都显式给出编码，不依赖运行机器的区域设置。"""
    offenders = [call for path in sorted(_CORPORA_SOURCE.rglob("*.py")) for call in _implicit_encoding_calls(path)]
    assert offenders == []


def test_implicit_encoding_scan_flags_text_reads_and_skips_binary(tmp_path) -> None:
    """扫描本身能识别缺省编码的文本读写，并放过二进制读写与已给编码的调用。"""
    sample = tmp_path / "sample.py"
    sample.write_text(
        "from pathlib import Path\n"
        "Path('a').read_text()\n"
        "Path('a').write_text('x')\n"
        "open('a')\n"
        "Path('a').read_text(encoding='utf-8')\n"
        "open('a', 'rb')\n"
        "open('a', mode='wb')\n", encoding="utf-8")
    assert _implicit_encoding_calls(sample, tmp_path) == [
        "sample.py:2 read_text",
        "sample.py:3 write_text",
        "sample.py:4 open",
    ]


def test_prepared_map_dataset_keeps_declaration_text_under_a_gbk_locale(monkeypatch, tmp_path) -> None:
    """GBK 区域设置下准备地图，发布的 dataset.json 与冻结声明的来源说明逐字相同。"""
    _simulate_gbk_locale(monkeypatch)
    real_load = map_prepare.load_map_dataset
    loaded = []

    def load_frozen_only(root, reference):
        """@brief 只核验冻结目录；发布副本的路线在此不取网络，不再二次核验。"""
        loaded.append(reference)
        return real_load(root, reference) if len(loaded) == 1 else None

    monkeypatch.setattr(map_prepare, "load_map_dataset", load_frozen_only)
    monkeypatch.setattr("worldmock.corpora.storefront.validate_assortments", lambda dataset, fare: None)
    monkeypatch.setattr(map_prepare, "_prepare_routes", lambda root, dataset, fare: [])
    destination = tmp_path / "prepared"
    map_prepare.prepare_map(destination, _FROZEN, 20260105)
    frozen = json.loads((_FROZEN / "zhangjiang-map/20260105/dataset.json").read_text(encoding="utf-8"))
    published = json.loads((destination / "zhangjiang-map/20260105/dataset.json").read_text(encoding="utf-8"))
    assert "©" in frozen["source"]["notice"]
    assert published["source"] == frozen["source"]
    changed = {"version", "prepared_at", "locations_file", "supplies_file", "routes_file"}
    assert {key: value for key, value in published.items() if key not in changed} == {
        key: value for key, value in frozen.items() if key not in changed}
    assert published["version"] == frozen["version"] + 1


def _textworld_stub() -> ModuleType:
    """@brief fetch 导入时需要的最小 textworld 替身；本组用例不生成游戏。"""
    textworld = ModuleType("textworld")
    textworld.generator = ModuleType("textworld.generator")
    textworld.generator.make_game = lambda options: None
    textworld.EnvInfos = SimpleNamespace
    textworld.GameOptions = SimpleNamespace
    return textworld


def _fresh_fetch(monkeypatch) -> ModuleType:
    """@brief 注入 textworld 替身后全新导入 fetch；撤销时还原原模块，不泄漏给其他测试。"""
    textworld = _textworld_stub()
    monkeypatch.setitem(sys.modules, "textworld", textworld)
    monkeypatch.setitem(sys.modules, "textworld.generator", textworld.generator)
    monkeypatch.setitem(sys.modules, _FETCH, None)
    monkeypatch.delitem(sys.modules, _FETCH)
    monkeypatch.setattr(corpora_package, "fetch", None, raising=False)
    return importlib.import_module(_FETCH)


def test_route_manifest_entry_keeps_declared_source_text_under_a_gbk_locale(monkeypatch, tmp_path) -> None:
    """GBK 区域设置下登记路线响应，清单条目逐字沿用路线目录声明的来源许可与选择说明。"""
    fetch = _fresh_fetch(monkeypatch)
    _simulate_gbk_locale(monkeypatch)
    route_id = "张江-家到办公室-driving"
    relative = f"zhangjiang-map/routes/{route_id}.json"
    declared = {"url": "https://routing.example/route/v1/driving", "retrieved_at": "2026-09-01T08:00:00+08:00",
                "license": _LICENSE, "fact_cutoff": "2026-09-01"}
    catalogue = {"schema_version": 1, "routes": [{"route_id": route_id, "response_path": relative,
                                                  "routing_profile": "driving", "source": declared}]}
    (tmp_path / "zhangjiang-map/routes").mkdir(parents=True)
    (tmp_path / "zhangjiang-map/routes.json").write_text(json.dumps(catalogue, ensure_ascii=False),
                                                         encoding="utf-8")
    response = tmp_path / relative
    response.write_text('{"code": "Ok"}', encoding="utf-8")
    entry = fetch._manifest_entry(tmp_path, response, datetime(2026, 9, 2, tzinfo=timezone.utc))
    assert entry.source.license == _LICENSE
    assert entry.source.selector == f"route {route_id}; profile=driving"
    assert entry.source.url == declared["url"]
