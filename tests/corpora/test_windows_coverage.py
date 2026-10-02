"""@brief Windows 可运行的语料补测：textworld 替身下的 prepare_corpora 与 manifest 身份校验。

TextWorld 1.7.0 没有 Windows wheel，而 fetch 在导入时即依赖它：此处向 sys.modules 注入最小
textworld 替身后全新导入 fetch；只对网络（urlopen）与地图路由准备打桩，暂存、清单、复核、
改名发布与失败清理全部真实执行。manifest 用例不导入 textworld。
"""

from __future__ import annotations

import hashlib
import importlib
import io
import json
import logging
import sys
import zipfile
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from pydantic import ValidationError

import worldmock.corpora as corpora_package
from worldmock.corpora.manifest import CorpusManifest, load_manifest
from worldmock.errors import IntegrityError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_FETCH = "worldmock.corpora.fetch"
_USER_AGENT = "WorldMock/1.0 (research corpus preparation)"


# ---------------------------------------------------------------------------
# textworld 替身、来源与地图打桩
# ---------------------------------------------------------------------------


class _StubGame:
    """@brief make_game 替身：记住生成参数，保存为带 walkthrough 的游戏文件。"""

    def __init__(self, options):
        self.options = options
        self.metadata = {"walkthrough": ["go east", "take coin"]}

    def save(self, path: str) -> None:
        """@brief 写出种子、房间数与 walkthrough，供环境替身加载。"""
        payload = {"seed": self.options.seeds, "nb_rooms": self.options.nb_rooms, **self.metadata}
        Path(path).write_text(json.dumps(payload), encoding="utf-8")


class _StubEnvironment:
    """@brief textworld.start 替身：从游戏文件加载 walkthrough，逐条执行至终态。"""

    def __init__(self, path: str, wins: bool):
        self.walkthrough = json.loads(Path(path).read_text(encoding="utf-8"))["walkthrough"]
        self.wins = wins
        self.steps: list[str] = []
        self.closed = False

    def _state(self, won: bool) -> SimpleNamespace:
        """@brief 每一步都允许 walkthrough 中的命令。"""
        return SimpleNamespace(admissible_commands=list(self.walkthrough), won=won)

    def reset(self) -> SimpleNamespace:
        """@brief 回到开局。"""
        self.steps.clear()
        return self._state(False)

    def step(self, command: str) -> tuple[SimpleNamespace, int, bool]:
        """@brief 走完全部 walkthrough 即终局；wins 决定终局是否胜利。"""
        self.steps.append(command)
        done = self.steps == self.walkthrough
        return self._state(done and self.wins), 1, done

    def close(self) -> None:
        """@brief 记录环境已释放。"""
        self.closed = True


def _textworld_stub() -> ModuleType:
    """@brief 最小 textworld 替身：只提供 fetch 用到的 EnvInfos/GameOptions/make_game/start。"""
    textworld = ModuleType("textworld")
    textworld.generator = ModuleType("textworld.generator")
    textworld.generator.make_game = _StubGame
    textworld.EnvInfos = SimpleNamespace
    textworld.GameOptions = SimpleNamespace
    textworld.wins = True
    textworld.environments = []

    def start(path: str, infos=None) -> _StubEnvironment:
        """@brief 启动并登记一个环境替身。"""
        environment = _StubEnvironment(path, textworld.wins)
        textworld.environments.append(environment)
        return environment

    textworld.start = start
    return textworld


def _off_payload(code: str) -> dict:
    """@brief Open Food Facts 单品响应：条码一致且含份量与每份热量。"""
    return {"status": 1, "product": {"code": code, "product_name": f"product {code}", "serving_size": "100 g",
                                     "nutriments": {"energy-kcal_serving": 150}}}


def _sintel_srt() -> bytes:
    """@brief 26 条 cue 且以 00:10:29,800 收尾的完整字幕时间线。"""
    cues = [f"{index + 1}\n00:00:{index:02d},000 --> 00:00:{index:02d},900\nline {index}\n\n"
            for index in range(25)]
    cues.append("26\n00:10:29,800 --> 00:10:31,000\nfinal line\n")
    return "".join(cues).encode("utf-8")


def _ami_archive() -> bytes:
    """@brief ES2002a-d 四人逐词标注与含决定、行动项的摘要标注包。"""
    content = io.BytesIO()
    with zipfile.ZipFile(content, mode="w") as archive:
        for stage in "abcd":
            for speaker in "ABCD":
                archive.writestr(f"words/ES2002{stage}.{speaker}.words.xml",
                                 f"<nite><w>speaker</w><w>{speaker}</w></nite>")
            archive.writestr(f"abstractive/ES2002{stage}.abssumm.xml",
                             "<summary><decisions><s>Ship the remote</s></decisions>"
                             "<actions><s>Draft the budget</s></actions></summary>")
    return content.getvalue()


class _Sources:
    """@brief urlopen 替身：只应答固定来源并记录请求；命中 fail 片段即断网。"""

    def __init__(self):
        self.requests: list[tuple[str, str | None, float | None]] = []
        self.fail: str | None = None

    def __call__(self, request, timeout=None) -> io.BytesIO:
        """@brief 按 URL 返回来源字节；未知 URL 视为越界访问。"""
        url = request.full_url
        self.requests.append((url, request.get_header("User-agent"), timeout))
        if self.fail and self.fail in url:
            raise OSError(f"network unavailable: {url}")
        if "openfoodfacts.org" in url:
            code = url.split("/product/")[1].split(".json")[0]
            return io.BytesIO(json.dumps(_off_payload(code)).encode("utf-8"))
        if url.endswith("sintel_en.srt"):
            return io.BytesIO(_sintel_srt())
        if "ami_public_manual" in url:
            return io.BytesIO(_ami_archive())
        raise AssertionError(f"unexpected source requested: {url}")


def _map_stub(calls: list[tuple[Path, Path, int]]):
    """@brief 地图路由准备替身：记录调用并写出探针 OSM 文件。"""

    def prepare_map(destination: Path, frozen_root: Path, random_seed: int) -> None:
        """@brief 只在给定目录内写出地图产物。"""
        calls.append((Path(destination), Path(frozen_root), random_seed))
        folder = Path(destination) / "zhangjiang-map"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "zhangjiang-probe.osm").write_bytes(b'<osm version="0.6"/>')

    return prepare_map


@pytest.fixture
def pipeline(monkeypatch):
    """@brief 注入 textworld 替身后全新导入 fetch，并对网络与地图路由打桩。"""
    textworld = _textworld_stub()
    monkeypatch.setitem(sys.modules, "textworld", textworld)
    monkeypatch.setitem(sys.modules, "textworld.generator", textworld.generator)
    # 先登记原值再移除：撤销时依次还原，替身绑定的 fetch 不会泄漏给其他测试。
    monkeypatch.setitem(sys.modules, _FETCH, None)
    monkeypatch.delitem(sys.modules, _FETCH)
    monkeypatch.setattr(corpora_package, "fetch", None, raising=False)
    fetch = importlib.import_module(_FETCH)
    sources = _Sources()
    monkeypatch.setattr("urllib.request.urlopen", sources)
    map_calls: list[tuple[Path, Path, int]] = []
    monkeypatch.setattr("worldmock.corpora.map_prepare.prepare_map", _map_stub(map_calls))
    return SimpleNamespace(prepare=fetch.prepare_corpora, textworld=textworld, sources=sources,
                           map_calls=map_calls)


# ---------------------------------------------------------------------------
# prepare_corpora：暂存、复核、原子发布与失败清理
# ---------------------------------------------------------------------------


def test_prepare_corpora_publishes_verified_tree_through_hidden_staging(pipeline, tmp_path):
    destination = tmp_path / "corpus"

    manifest = pipeline.prepare(destination)

    published = {path.relative_to(destination).as_posix() for path in destination.rglob("*") if path.is_file()}
    assert {entry.path for entry in manifest.entries} == published - {"manifest.json"}
    assert {"manifest.json", "open-food-facts/products.json", "sintel/sintel_en.srt",
            "ami-es2002/inputs/ES2002a.txt", "ami-es2002/references/ES2002d.oracle.json",
            "zhangjiang-map/zhangjiang-probe.osm", "textworld/game-1208-options.json",
            "scenario-catalog/catalog.json", "scenario-mail/threads.json", "LICENSES.md"} <= published
    assert load_manifest(destination).model_dump(mode="json") == manifest.model_dump(mode="json")
    [(staging, frozen_root, first_seed), (other_staging, other_root, second_seed)] = pipeline.map_calls
    assert (first_seed, second_seed) == (20260105, 20260106)
    assert (staging, frozen_root) == (other_staging, other_root)
    assert staging.parent == tmp_path.resolve() and staging.name.startswith(".corpus.staging-")
    assert frozen_root == PROJECT_ROOT / "corpora"
    assert [path.name for path in tmp_path.iterdir()] == ["corpus"]
    games = pipeline.textworld.environments
    assert len(games) == 8 and all(game.closed and game.steps == game.walkthrough for game in games)
    assert len(pipeline.sources.requests) == 10
    assert {(agent, timeout) for _, agent, timeout in pipeline.sources.requests} == {(_USER_AGENT, 60)}


def test_prepare_corpora_refuses_existing_destination_before_fetching(pipeline, tmp_path):
    destination = tmp_path / "corpus"
    destination.mkdir()
    (destination / "keep.txt").write_text("published", encoding="utf-8")

    with pytest.raises(IntegrityError, match="already exists"):
        pipeline.prepare(destination)

    assert [path.name for path in tmp_path.iterdir()] == ["corpus"]
    assert (destination / "keep.txt").read_text(encoding="utf-8") == "published"
    assert pipeline.sources.requests == []


@pytest.mark.parametrize("breakage, message", [
    ("network", "Source download failed"),
    ("textworld", "did not win"),
])
def test_prepare_corpora_removes_staging_and_publishes_nothing_on_failure(pipeline, tmp_path, caplog,
                                                                          breakage, message):
    if breakage == "network":
        pipeline.sources.fail = "sintel_en.srt"
    else:
        pipeline.textworld.wins = False

    with pytest.raises(IntegrityError, match=message):
        pipeline.prepare(tmp_path / "corpus")

    assert list(tmp_path.iterdir()) == []
    [record] = [item for item in caplog.records if item.getMessage() == "Corpus preparation failed"]
    assert record.levelno == logging.ERROR and record.exc_info[0] is IntegrityError


# ---------------------------------------------------------------------------
# manifest：身份唯一性与全量复核（不导入 textworld）
# ---------------------------------------------------------------------------


def _entry(path: str, content: bytes, entry_id: str | None = None) -> dict:
    """@brief 与内容一致的最小 manifest 条目。"""
    return {"entry_id": entry_id or path.replace("/", ":"), "path": path,
            "sha256": hashlib.sha256(content).hexdigest(), "size_bytes": len(content),
            "media_type": "text/plain", "purpose": "fixture",
            "source": {"kind": "scenario", "license": "WorldMock-project", "selector": "complete frozen file"}}


def _manifest(entries: list[dict], created_at: str = "2026-09-15T08:00:00+08:00") -> dict:
    """@brief 最小 manifest 载荷。"""
    return {"schema_version": 1, "corpus_id": "fixture", "created_at": created_at,
            "transformer_version": "fixture-1", "entries": entries}


def _write_tree(root: Path) -> bytes:
    """@brief 写出两份语料文件及登记它们的 manifest，返回 manifest 字节。"""
    files = {"notes/a.txt": b"alpha\n", "b.txt": b"beta\n"}
    for relative, content in files.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_bytes(content)
    raw = json.dumps(_manifest([_entry(path, content) for path, content in files.items()])).encode("utf-8")
    (root / "manifest.json").write_bytes(raw)
    return raw


def test_manifest_accepts_unique_identities_with_aware_creation_time():
    manifest = CorpusManifest.model_validate(_manifest([_entry("a.txt", b"a"), _entry("b.txt", b"b")]))

    assert [entry.entry_id for entry in manifest.entries] == ["a.txt", "b.txt"]
    assert manifest.created_at.utcoffset().total_seconds() == 8 * 3600


@pytest.mark.parametrize("payload, message", [
    (_manifest([_entry("a.txt", b"a")], created_at="2026-09-15T08:00:00"), "timezone-aware"),
    (_manifest([_entry("a.txt", b"a", "same"), _entry("b.txt", b"b", "same")]), "must be unique"),
    (_manifest([_entry("a.txt", b"a", "first"), _entry("a.txt", b"a", "second")]), "must be unique"),
], ids=["naive-created-at", "duplicate-entry-id", "duplicate-path"])
def test_manifest_rejects_naive_creation_time_and_duplicate_identities(payload, message):
    with pytest.raises(ValidationError, match=message):
        CorpusManifest.model_validate(payload)


def test_load_manifest_verifies_every_entry_and_accepts_matching_pin(tmp_path):
    raw = _write_tree(tmp_path)

    manifest = load_manifest(tmp_path, expected_sha256=hashlib.sha256(raw).hexdigest())

    assert sorted(entry.path for entry in manifest.entries) == ["b.txt", "notes/a.txt"]


def test_load_manifest_rejects_missing_or_symlinked_manifest(tmp_path, monkeypatch):
    with pytest.raises(IntegrityError, match="missing or is a symbolic link"):
        load_manifest(tmp_path)
    _write_tree(tmp_path)
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda self: self.name == "manifest.json" or original(self))

    with pytest.raises(IntegrityError, match="missing or is a symbolic link"):
        load_manifest(tmp_path)


def test_load_manifest_rejects_pin_mismatch(tmp_path):
    _write_tree(tmp_path)

    with pytest.raises(IntegrityError, match="hash mismatch"):
        load_manifest(tmp_path, expected_sha256="0" * 64)


@pytest.mark.parametrize("raw", [
    b"{not json",
    json.dumps(_manifest([], created_at="2026-09-15T08:00:00")).encode("utf-8"),
], ids=["malformed-json", "naive-created-at"])
def test_load_manifest_wraps_parse_and_schema_failures(tmp_path, raw):
    (tmp_path / "manifest.json").write_bytes(raw)

    with pytest.raises(IntegrityError, match="validation failed") as caught:
        load_manifest(tmp_path)

    assert isinstance(caught.value.__cause__, ValueError)


def test_load_manifest_rejects_entry_content_drift(tmp_path):
    _write_tree(tmp_path)
    (tmp_path / "b.txt").write_bytes(b"BETA\n")

    with pytest.raises(IntegrityError, match="identity mismatch"):
        load_manifest(tmp_path)
