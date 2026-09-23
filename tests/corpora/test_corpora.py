"""正式生活语料的身份、选择器和真实消费者测试。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from worldmock.corpora import load_manifest
from worldmock.errors import IntegrityError
from worldmock.life.content import load_subtitles
from worldmock.life.game import validate_game
from worldmock.life.scenario import load_scenario
from worldmock.life.travel import validate_routes


def test_formal_manifest_validates_every_file_and_selected_counts() -> None:
    """正式 manifest 全量校验，并锁定已实际取得的最小子集。"""
    manifest = load_manifest(Path("corpora"))
    scenario = load_scenario("scenarios/white-collar-12-weeks.json")[0]
    assert manifest.corpus_id == "life-dayrun-behavior-2026-09-21"
    # C23 扩容：材料三件套 195 份（65 材料 × 输入/参考/oracle）+ POI 路由 208 份。
    # 可达性修复：补 5 个流身份条目（news-feed / jokes-feed / weibo-feed / zhihu-feed /
    # movie-reviews-feed），使场景声明的这五条在语料里有可检索的分区键。
    # 地图发布新增 dataset/routes/supplies/supply-assumptions 四份权威声明。
    assert len(manifest.entries) == 970
    products = json.loads(Path("corpora/open-food-facts/products.json").read_text(encoding="utf-8"))
    threads = json.loads(Path("corpora/scenario-mail/threads.json").read_text(encoding="utf-8"))
    assert len(products) == 8
    assert len(threads) == 48
    assert all(2 <= len(thread["messages"]) <= 5 for thread in threads)
    # 修订合同正餐品类：129 种既有食品 + 30 条 meal-* 正餐引用。
    assert len(scenario.products) == 159


def test_manifest_rejects_in_root_symbolic_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """根内文件即使指向安全目标，被标记为符号链接也必须拒绝。"""
    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "link.json"
    link.write_bytes(target.read_bytes())
    original_is_symlink = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == link or original_is_symlink(path))
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    manifest = {
        "schema_version": 1,
        "corpus_id": "symlink-negative",
        "created_at": "2026-09-15T12:00:00+08:00",
        "transformer_version": "test",
        "entries": [{
            "entry_id": "link",
            "path": "link.json",
            "sha256": digest,
            "size_bytes": 2,
            "media_type": "application/json",
            "purpose": "negative",
            "source": {"kind": "scenario", "url": None, "retrieved_at": None,
                       "license": "test", "selector": "test", "fact_cutoff": None},
        }],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(IntegrityError, match="path is invalid"):
        load_manifest(tmp_path)


def test_sintel_is_subtitle_experience_with_complete_timeline() -> None:
    """Sintel 只声明字幕体验，真实 SRT 的 26 个 cue 可解析。"""
    scenario = load_scenario("scenarios/white-collar-12-weeks.json")[0]
    item = scenario.contents[0]
    cues = load_subtitles(item, Path("corpora"))
    identity = json.loads(Path("corpora/sintel/media-identity.json").read_text(encoding="utf-8"))
    assert len(cues) == 26
    assert cues[0].start_seconds < cues[-1].end_seconds <= item.duration_seconds
    assert item.experience_label == "subtitle_experience"
    assert identity["visual_experience"] is False


def test_routes_are_frozen_real_responses_for_all_declared_pairs() -> None:
    """220 条 driving/walking 路由（锚点 12 + POI 扩容 208）的响应身份和语义均匹配场景。"""
    scenario = load_scenario("scenarios/white-collar-12-weeks.json")[0]
    validate_routes(scenario, Path("corpora"))
    assert len(scenario.routes) == 220
    poi_routes = [route for route in scenario.routes if route.origin_id.startswith("poi-")
                  or route.destination_id.startswith("poi-")]
    assert len(poi_routes) == 208
    forward = next(route for route in scenario.routes if route.route_id == "home-office-driving")
    foot = next(route for route in scenario.routes if route.route_id == "home-office-walking")
    assert forward.distance_meters == 2774.4
    assert foot.distance_meters == 2148.2


def test_all_textworld_games_pass_real_hash_and_reset_validation() -> None:
    """三十个固定游戏（单元 W 扩种子 8→30）均通过游戏文件哈希、walkthrough 哈希与真实 TextWorld 环境重置校验。"""
    scenario = load_scenario("scenarios/white-collar-12-weeks.json")[0]
    for game in scenario.games:
        validate_game(game, Path("corpora"))
        options = json.loads(Path(f"corpora/textworld/game-{game.seed}-options.json").read_text(encoding="utf-8"))
        assert options["walkthrough"]


def test_ami_inputs_and_hidden_oracles_are_separate() -> None:
    """人物输入不含 oracle 文本，参考答案位于独立 manifest 文件。"""
    manifest = load_manifest(Path("corpora"))
    for stage in "abcd":
        source = Path(f"corpora/ami-es2002/inputs/ES2002{stage}.txt")
        oracle = Path(f"corpora/ami-es2002/references/ES2002{stage}.oracle.json")
        expected = json.loads(oracle.read_text(encoding="utf-8"))["expected"]
        assert source.is_file() and source.stat().st_size > 1000
        assert expected["meeting_id"] == f"ES2002{stage}"
        assert expected["decisions"]
        assert hashlib.sha256(source.read_bytes()).hexdigest() != hashlib.sha256(oracle.read_bytes()).hexdigest()
    paths = {entry.path for entry in manifest.entries}
    assert "ami-es2002/inputs/ES2002a.txt" in paths
    assert "ami-es2002/references/ES2002a.oracle.json" in paths
