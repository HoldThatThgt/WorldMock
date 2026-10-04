"""@brief C4f 分支覆盖补测：corpora/fetch.py（全 mock HTTP）与 manifest.py。

零真实网络请求：urlopen 全程打桩，TextWorld 生成/执行打桩；每个下载校验、
校验失败与重试边界都有正/反例。
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import zipfile
from types import SimpleNamespace
import requests
from xml.etree import ElementTree
from pathlib import Path

import pytest

import worldmock.corpora.fetch as fetch_module
from worldmock.corpora.fetch import (
    _download,
    _download_json,
    _exercise_game,
    _manifest_entry,
    _normalize_off,
    _off_product,
    _source_for,
    prepare_corpora,
)
from worldmock.corpora.manifest import CorpusSource, load_manifest
from worldmock.corpora.map_prepare import _route_url, _validate_response
from worldmock.life.map_models import RouteConfig
from tests.life.test_map_dataset import route
from worldmock.errors import IntegrityError

PROJECT_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# mock HTTP 基础设施
# ---------------------------------------------------------------------------


class _FakeResponse:
    """@brief urlopen 的最小替身：上下文管理器 + 一次性 read。"""

    def __init__(self, content: bytes):
        self._content = content

    def read(self) -> bytes:
        return self._content

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _sintel_content() -> bytes:
    """@brief 26 个 cue 且含锚点时间戳的合法 SRT 字节。"""
    cues = [f"{index + 1}\n00:00:{index:02d},000 --> 00:00:{index:02d},900\nline {index}\n"
            for index in range(25)]
    cues.append("26\n00:10:29,800 --> 00:10:30,000\nfinal\n")
    return "".join(cues).encode("utf-8")


def _word_xml(words: list[str]) -> bytes:
    """@brief AMI words XML：根元素下若干 <w> 子节点。"""
    body = "".join(f"<w>{word}</w>" for word in words)
    return f"<root>{body}</root>".encode("utf-8")


def _abssumm_xml() -> bytes:
    """@brief AMI 摘要 XML：decisions 与 actions 各至少一句。"""
    return (b"<summary><decisions><decision>Buy a whiteboard</decision></decisions>"
            b"<actions><action>Order markers</action></actions></summary>")


def _ami_zip(*, missing_stage: str | None = None, empty_speaker: bool = False,
             empty_oracle: bool = False) -> bytes:
    """@brief 构造 ES2002a-d 完整（或定向残缺）的 AMI 标注包。"""
    content = io.BytesIO()
    with zipfile.ZipFile(content, mode="w") as archive:
        for stage in "abcd":
            if stage == missing_stage:
                continue
            for speaker in "ABCD":
                words = [] if empty_speaker and speaker == "A" else ["word-one", "word-two"]
                archive.writestr(f"words/ES2002{stage}.{speaker}.words.xml", _word_xml(words))
            summary = _abssumm_xml() if not empty_oracle else b"<summary></summary>"
            archive.writestr(f"abstractive/ES2002{stage}.abssumm.xml", summary)
    return content.getvalue()


def _off_response(code: str, *, status: int = 1, mismatch: bool = False) -> dict:
    return {"status": status, "product": {
        "code": "0000000000000" if mismatch else code,
        "product_name_zh": "测试食品", "product_name": "test product",
        "serving_size": "100 g", "nutriments": {"energy-kcal_serving": 150},
    }}


def _route_response() -> dict:
    return {"code": "Ok", "routes": [{
        "distance": 2774.4, "duration": 600.0,
        "geometry": {"type": "LineString", "coordinates": [[121.6, 31.2], [121.61, 31.21]]},
        "legs": [{"steps": [{"mode": "driving"}]}],
    }]}


class _FakeGame:
    """@brief make_game 替身：可保存、携带 walkthrough 元数据。"""

    def __init__(self, walkthrough=("look", "win")):
        self.metadata = {"walkthrough": list(walkthrough)}

    def save(self, path: str):
        Path(path).write_text(json.dumps(self.metadata), encoding="utf-8")


class _FakeState:
    def __init__(self, commands, won=True):
        self.admissible_commands = list(commands)
        self.won = won


class _FakeEnvironment:
    """@brief textworld.start 替身：reset/step 走完 walkthrough 即胜。"""

    def __init__(self, walkthrough, *, admissible=True, won=True):
        self._walkthrough = list(walkthrough)
        self._admissible = admissible
        self._won = won
        self.closed = False
        self._commands = (self._walkthrough + ["inventory"]) if admissible else ["inventory"]

    def reset(self):
        return _FakeState(self._commands, won=self._won)

    def step(self, command):
        return _FakeState(self._commands, won=self._won), 0.0, True

    def close(self):
        self.closed = True


def _install_network(monkeypatch, *, off_override=None, sintel=None, ami=None, osm=None,
                     route=None, fail_substrings=()):
    """@brief 按 URL 前缀路由的 urlopen 桩；fail_substrings 命中即抛错。"""

    def fake_urlopen(request, timeout=None):
        url = request.full_url
        for fragment in fail_substrings:
            if fragment in url:
                raise OSError(f"network refused: {fragment}")
        if "openfoodfacts.org" in url:
            code = url.split("/product/")[1].split(".")[0]
            payload = off_override(code, url) if callable(off_override) else _off_response(code)
            return _FakeResponse(json.dumps(payload).encode("utf-8"))
        if "sintel_en.srt" in url:
            return _FakeResponse(sintel if sintel is not None else _sintel_content())
        if "ami_public_manual" in url:
            return _FakeResponse(ami if ami is not None else _ami_zip())
        if "openstreetmap.org/api" in url:
            return _FakeResponse(osm if osm is not None else b"<osm>" + b"n" * 100_000)
        if "/route/v1/driving/" in url:
            return _FakeResponse(json.dumps(route if route is not None else _route_response())
                                 .encode("utf-8"))
        raise AssertionError(f"unexpected URL requested: {url}")

    def fake_route_get(url, **kwargs):
        for fragment in fail_substrings:
            if fragment in url:
                raise requests.ConnectionError(f"network refused: {fragment}")
        payload = route if route is not None else _route_response()
        if route is None and "/route/v1/foot/" in url:
            payload["routes"][0]["legs"][0]["steps"][0]["mode"] = "walking"
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("worldmock.corpora.map_prepare.requests.get", fake_route_get)
    monkeypatch.setattr("worldmock.corpora.map_prepare.time.sleep", lambda seconds: None)
    return fake_urlopen


def _install_textworld(monkeypatch, game_factory=None, environment_factory=None):
    """@brief 打桩 TextWorld 生成与执行（本地确定性替身，不触网络）。"""

    def fake_make_game(options):
        if game_factory is not None:
            return game_factory(options)
        return _FakeGame()

    def fake_start(path, infos=None):
        if environment_factory is not None:
            return environment_factory(path)
        return _FakeEnvironment(["look", "win"])

    monkeypatch.setattr(fetch_module, "make_game", fake_make_game)
    monkeypatch.setattr("textworld.start", fake_start)


# ---------------------------------------------------------------------------
# prepare_corpora 端到端（全 mock）
# ---------------------------------------------------------------------------


def test_prepare_corpora_publishes_validated_manifest_with_mock_sources(tmp_path, monkeypatch):
    destination = tmp_path / "corpus"
    _install_network(monkeypatch)
    _install_textworld(monkeypatch)

    manifest = prepare_corpora(destination)

    assert manifest.corpus_id == "life-full-2026-09-15"
    assert (destination / "open-food-facts" / "products.json").is_file()
    assert (destination / "sintel" / "sintel_en.srt").is_file()
    assert (destination / "ami-es2002" / "inputs" / "ES2002a.txt").is_file()
    assert (destination / "zhangjiang-map" / "zhangjiang-probe.osm").is_file()
    routes = sorted((destination / "zhangjiang-map" / "routes").iterdir())
    assert len(routes) == 220  # 十一地点的全部有向地点对与两种方式。
    assert (destination / "textworld" / "game-1201.json").is_file()
    assert (destination / "scenario-catalog" / "catalog.json").is_file()
    reloaded = load_manifest(destination)
    assert len(reloaded.entries) == len(manifest.entries)
    assert not [item for item in tmp_path.iterdir() if item.name.startswith(".corpus.staging-")]


def test_prepare_corpora_rejects_existing_destination(tmp_path, monkeypatch):
    destination = tmp_path / "corpus"
    destination.mkdir()
    with pytest.raises(IntegrityError, match="already exists"):
        prepare_corpora(destination)


@pytest.mark.parametrize("fail_fragment", ["openfoodfacts", "sintel", "ami_public",
                                           "openstreetmap", "route/v1"])
def test_prepare_corpora_cleans_staging_when_any_download_fails(tmp_path, monkeypatch,
                                                                fail_fragment):
    _install_network(monkeypatch, fail_substrings=(fail_fragment,))
    _install_textworld(monkeypatch)
    with pytest.raises(IntegrityError):
        prepare_corpora(tmp_path / "corpus")
    assert not (tmp_path / "corpus").exists()
    assert not [item for item in tmp_path.iterdir() if ".staging-" in item.name]


# ---------------------------------------------------------------------------
# OFF / Sintel / AMI / OSM 校验反例
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("response, match", [
    ({"status": 0}, "unavailable"),
    ({"status": 1, "product": "not-a-dict"}, "unavailable"),
    (_off_response("6923644266066", mismatch=True), "identity mismatch"),
])
def test_off_product_rejects_unavailable_or_mismatched_responses(response, match):
    with pytest.raises(IntegrityError, match=match):
        _off_product(response, "6923644266066")


@pytest.mark.parametrize("product, match", [
    ({"product_name_zh": "", "product_name": " ", "serving_size": "1 g",
      "nutriments": {"energy-kcal_serving": 5}}, "no name"),
    ({"product_name": "name", "serving_size": "  ", "nutriments": {"energy-kcal_serving": 5}},
     "no serving size"),
    ({"product_name": "name", "serving_size": "1 g", "nutriments": {"energy-kcal_serving": True}},
     "no serving energy"),
    ({"product_name": "name", "serving_size": "1 g", "nutriments": {"energy-kcal_serving": -1}},
     "no serving energy"),
    ({"product_name": "name", "serving_size": "1 g", "nutriments": "not-a-dict"},
     "no serving energy"),
])
def test_normalize_off_rejects_incomplete_products(product, match):
    with pytest.raises(IntegrityError, match=match):
        _normalize_off(product, "6923644266066")


def test_normalize_off_accepts_and_projects_a_valid_product():
    normalized = _normalize_off(_off_response("6923644266066")["product"], "6923644266066")
    assert normalized == {"barcode": "6923644266066", "name": "测试食品",
                          "serving_size": "100 g", "energy_kcal_serving": 150,
                          "source_kind": "open_food_facts"}


def test_fetch_sintel_rejects_incomplete_timeline(tmp_path, monkeypatch):
    short = b"1\n00:00:01,000 --> 00:00:02,000\nhello\n"
    _install_network(monkeypatch, sintel=short)
    with pytest.raises(IntegrityError, match="timeline is incomplete"):
        fetch_module._fetch_sintel(tmp_path)


def test_fetch_ami_rejects_invalid_archive(tmp_path, monkeypatch):
    _install_network(monkeypatch, ami=b"this is not a zip archive")
    with pytest.raises(IntegrityError, match="archive is invalid"):
        fetch_module._fetch_ami(tmp_path)


@pytest.mark.parametrize("zip_builder, match", [
    (lambda: _ami_zip(missing_stage="c"), "annotation is incomplete"),
    (lambda: _ami_zip(empty_speaker=True), "is empty"),
    (lambda: _ami_zip(empty_oracle=True), "oracle is incomplete"),
])
def test_normalize_ami_rejects_incomplete_annotations(tmp_path, zip_builder, match):
    archive_path = tmp_path / "archive.zip"
    archive_path.write_bytes(zip_builder())
    with zipfile.ZipFile(archive_path) as archive:
        with pytest.raises(IntegrityError, match=match):
            fetch_module._normalize_ami(archive, tmp_path)


def test_fetch_geodata_delegates_to_the_reviewed_map_preparation(tmp_path, monkeypatch):
    """地图准备使用已审阅的完整地点集合并传播发布校验错误。"""
    calls = []
    def reject(destination, frozen_root, random_seed):
        calls.append((destination, frozen_root, random_seed))
        raise IntegrityError("Map file hash mismatch")
    monkeypatch.setattr("worldmock.corpora.map_prepare.prepare_map", reject)
    with pytest.raises(IntegrityError, match="hash mismatch"):
        fetch_module._fetch_geodata(tmp_path)
    assert calls == [(tmp_path, PROJECT_ROOT / "corpora", 20260105)]

@pytest.mark.parametrize("response, match", [
    ({"code": "Ok", "routes": []}, "preparation failed"),
    ({"code": "Ok", "routes": [{"distance": 0, "duration": 5, "geometry": {"type": "x"}}]},
     "incomplete"),
    ({"code": "Ok", "routes": [{"distance": 5, "duration": 5}]}, "incomplete"),
])
def test_validate_route_rejects_unusable_responses(response, match):
    with pytest.raises(IntegrityError, match=match):
        _validate_response(response, RouteConfig.model_validate(route("home", "office")))


def test_route_url_requests_full_geometry_with_steps():
    declared = RouteConfig.model_validate(route("home", "office"))
    places = {"home": SimpleNamespace(longitude=121.6142, latitude=31.2025),
              "office": SimpleNamespace(longitude=121.6278, latitude=31.2108)}
    url = _route_url(declared, places)
    assert "overview=full" in url and "geometries=geojson" in url and "steps=true" in url
    assert "/route/v1/foot/121.6142,31.2025;121.6278,31.2108" in url


def test_route_no_route_response_is_an_explicit_unreachable_result():
    """NoRoute 是可发布的不可达证据，空结果与未知错误不可冒充不可达。"""
    _validate_response({"code": "NoRoute"}, RouteConfig.model_validate(route("home", "office")))

def _write_game(tmp_path, name="game.json"):
    path = tmp_path / name
    path.write_text("{}", encoding="utf-8")
    return path


def test_exercise_game_rejects_missing_walkthrough(tmp_path):
    with pytest.raises(IntegrityError, match="no walkthrough"):
        _exercise_game(_write_game(tmp_path), [])


def test_exercise_game_rejects_inadmissible_walkthrough(monkeypatch, tmp_path):
    def fake_start(path, infos=None):
        return _FakeEnvironment(["look"], admissible=False)

    monkeypatch.setattr("textworld.start", fake_start)
    with pytest.raises(IntegrityError, match="not admissible"):
        _exercise_game(_write_game(tmp_path), ["look"])


def test_exercise_game_rejects_unwon_walkthrough(monkeypatch, tmp_path):
    def fake_start(path, infos=None):
        return _FakeEnvironment(["look"], won=False)

    monkeypatch.setattr("textworld.start", fake_start)
    with pytest.raises(IntegrityError, match="did not win"):
        _exercise_game(_write_game(tmp_path), ["look"])


def test_exercise_game_closes_environment_after_success(monkeypatch, tmp_path):
    environment = _FakeEnvironment(["look", "win"])

    monkeypatch.setattr("textworld.start", lambda path, infos=None: environment)
    _exercise_game(_write_game(tmp_path), ["look", "win"])
    assert environment.closed is True


# ---------------------------------------------------------------------------
# 下载与 JSON 防护
# ---------------------------------------------------------------------------


def test_download_json_rejects_invalid_and_non_object_payloads(monkeypatch):
    monkeypatch.setattr(fetch_module, "_download", lambda url: b"{not json")
    with pytest.raises(IntegrityError, match="invalid JSON"):
        _download_json("https://example/one")
    monkeypatch.setattr(fetch_module, "_download", lambda url: b"[1,2]")
    with pytest.raises(IntegrityError, match="non-object JSON"):
        _download_json("https://example/two")


def test_download_wraps_transport_errors_and_rejects_empty_bodies(monkeypatch):
    def raising_urlopen(request, timeout=None):
        raise OSError("connection reset")

    monkeypatch.setattr("urllib.request.urlopen", raising_urlopen)
    with pytest.raises(IntegrityError, match="download failed"):
        _download("https://example/fail")
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda request, timeout=None: _FakeResponse(b""))
    with pytest.raises(IntegrityError, match="download was empty"):
        _download("https://example/empty")


# ---------------------------------------------------------------------------
# 清单条目与来源绑定
# ---------------------------------------------------------------------------


def test_manifest_entry_overrides_srt_media_type(tmp_path):
    srt = tmp_path / "notes.srt"
    srt.write_bytes(b"1\n00:00:01,000 --> 00:00:02,000\nx\n")
    retrieved_at = fetch_module.datetime.now(fetch_module.ZoneInfo("Asia/Shanghai"))
    entry = _manifest_entry(tmp_path, srt, retrieved_at)
    assert entry.media_type == "application/x-subrip"
    assert entry.entry_id == "notes.srt"
    assert entry.size_bytes == srt.stat().st_size


@pytest.mark.parametrize("relative, kind, url_fragment", [
    ("open-food-facts/products.json", "external_dataset", "openfoodfacts"),
    ("sintel/sintel_en.srt", "external_media", "sintel"),
    ("ami-es2002/inputs/ES2002a.txt", "external_dataset", "ami_public"),
    ("textworld/game-1201.json", "generated_with_textworld", "TextWorld"),
    ("zhangjiang-map/routes/home-office-walking.json", "external_geodata", "routed-foot"),
    ("zhangjiang-map/routes/home-office-driving.json", "external_geodata", "osrm"),
    ("zhangjiang-map/zhangjiang-probe.osm", "external_geodata", "openstreetmap"),
])
def test_source_for_binds_each_corpus_prefix(relative, kind, url_fragment):
    retrieved_at = fetch_module.datetime.now(fetch_module.ZoneInfo("Asia/Shanghai"))
    source, purpose = _source_for(relative, retrieved_at)
    assert isinstance(source, CorpusSource)
    assert source.kind == kind
    assert url_fragment in (source.url or "")
    assert purpose


def test_source_for_defaults_to_scenario_authored_material():
    retrieved_at = fetch_module.datetime.now(fetch_module.ZoneInfo("Asia/Shanghai"))
    source, purpose = _source_for("scenario-mail/threads.json", retrieved_at)
    assert source.url is None and source.retrieved_at is None
    assert source.license == "WorldMock-project"
    assert purpose == "scenario-authored material"


# ---------------------------------------------------------------------------
# manifest.py 缺口
# ---------------------------------------------------------------------------


def _entry_dict(path: str, content: bytes, entry_id: str | None = None):
    import hashlib
    return {"entry_id": entry_id or path.replace("/", ":"), "path": path,
            "sha256": hashlib.sha256(content).hexdigest(), "size_bytes": len(content),
            "media_type": "application/json", "purpose": "test",
            "source": {"kind": "scenario", "url": None, "retrieved_at": None,
                       "license": "test", "selector": "test", "fact_cutoff": None}}


def _manifest_dict(entries, **overrides):
    payload = {"schema_version": 1, "corpus_id": "test-corpus",
               "created_at": "2026-09-15T12:00:00+08:00",
               "transformer_version": "test", "entries": entries}
    payload.update(overrides)
    return payload


def _write_manifest(root: Path, manifest):
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_manifest_rejects_naive_created_at_and_duplicate_identities(tmp_path):
    (tmp_path / "a.json").write_text("{}")
    entry = _entry_dict("a.json", b"{}")
    naive = _manifest_dict([entry], created_at="2026-09-15T12:00:00")
    _write_manifest(tmp_path, naive)
    with pytest.raises(Exception, match="validation failed"):
        load_manifest(tmp_path)
    duplicated = _manifest_dict([entry, dict(entry)])
    _write_manifest(tmp_path, duplicated)
    with pytest.raises(Exception, match="validation failed"):
        load_manifest(tmp_path)


def test_load_manifest_rejects_missing_manifest_and_hash_pin_mismatch(tmp_path):
    with pytest.raises(Exception, match="missing or is a symbolic link"):
        load_manifest(tmp_path)
    (tmp_path / "a.json").write_text("{}")
    _write_manifest(tmp_path, _manifest_dict([_entry_dict("a.json", b"{}")]))
    with pytest.raises(Exception, match="hash mismatch"):
        load_manifest(tmp_path, expected_sha256="0" * 64)


def test_load_manifest_rejects_malformed_manifest_json(tmp_path):
    (tmp_path / "manifest.json").write_text("{ not valid json")
    with pytest.raises(Exception, match="validation failed"):
        load_manifest(tmp_path)


def test_load_manifest_rejects_entry_content_drift(tmp_path):
    (tmp_path / "a.json").write_text("{}")
    stale = _entry_dict("a.json", b"different bytes")
    _write_manifest(tmp_path, _manifest_dict([stale]))
    with pytest.raises(Exception, match="identity mismatch"):
        load_manifest(tmp_path)


def test_load_manifest_accepts_a_minimal_valid_tree(tmp_path):
    (tmp_path / "a.json").write_text("{}")
    _write_manifest(tmp_path, _manifest_dict([_entry_dict("a.json", b"{}")]))
    manifest = load_manifest(tmp_path)
    assert manifest.entries[0].path == "a.json"
