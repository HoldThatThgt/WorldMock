"""正式生活语料的身份、选择器和真实消费者测试。"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from worldmock.corpora import load_manifest
from worldmock.errors import IntegrityError
from worldmock.life.content import load_subtitles
from worldmock.life.game import new_game_session, run_game_command, validate_game, verify_game_progress
from worldmock.life.scenario import load_scenario
from worldmock.life.travel import validate_routes


def test_formal_manifest_validates_every_file_and_selected_counts() -> None:
    """正式 manifest 全量校验，并锁定已实际取得的最小子集。"""
    manifest = load_manifest(Path("corpora"))
    scenario = load_scenario("scenarios/white-collar-12-weeks.json")[0]
    assert manifest.corpus_id == "life-full-2026-09-15"
    assert len(manifest.entries) == 51
    products = json.loads(Path("corpora/open-food-facts/products.json").read_text())
    threads = json.loads(Path("corpora/scenario-mail/threads.json").read_text())
    assert len(products) == 8
    assert len(threads) == 48
    assert all(2 <= len(thread["messages"]) <= 5 for thread in threads)
    assert len(scenario.products) == 16


def test_manifest_rejects_in_root_symbolic_link_before_resolve(tmp_path: Path) -> None:
    """指向根内普通文件的符号链接仍被拒绝。"""
    target = tmp_path / "target.json"
    target.write_text("{}")
    link = tmp_path / "link.json"
    os.symlink(target.name, link)
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
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(IntegrityError, match="path is invalid"):
        load_manifest(tmp_path)


def test_sintel_is_subtitle_experience_with_complete_timeline() -> None:
    """Sintel 只声明字幕体验，真实 SRT 的 26 个 cue 可解析。"""
    scenario = load_scenario("scenarios/white-collar-12-weeks.json")[0]
    item = scenario.contents[0]
    cues = load_subtitles(item, Path("corpora"))
    identity = json.loads(Path("corpora/sintel/media-identity.json").read_text())
    assert len(cues) == 26
    assert cues[0].start_seconds < cues[-1].end_seconds <= item.duration_seconds
    assert item.experience_label == "subtitle_experience"
    assert identity["visual_experience"] is False


def test_routes_are_frozen_real_responses_for_all_declared_pairs() -> None:
    """十二条 driving/walking 路由的响应身份和语义均匹配场景。"""
    scenario = load_scenario("scenarios/white-collar-12-weeks.json")[0]
    validate_routes(scenario, Path("corpora"))
    assert len(scenario.routes) == 12
    forward = next(route for route in scenario.routes if route.route_id == "home-office-driving")
    foot = next(route for route in scenario.routes if route.route_id == "home-office-walking")
    assert forward.distance_meters == 2774.4
    assert foot.distance_meters == 2148.2


def test_all_textworld_games_use_real_reset_step_and_replay() -> None:
    """八个固定游戏均通过 TextWorld 1.7 环境执行完整 walkthrough 并可重放。"""
    scenario = load_scenario("scenarios/white-collar-12-weeks.json")[0]
    for game in scenario.games:
        validate_game(game, Path("corpora"))
        options = json.loads(Path(f"corpora/textworld/game-{game.seed}-options.json").read_text())
        progress = new_game_session(f"session-{game.seed}", game, Path("corpora"))
        for command in options["walkthrough"]:
            outcome = run_game_command(progress, command, game, Path("corpora"))
            progress = outcome.progress
        assert outcome.done and progress.won
        verify_game_progress(progress, game, Path("corpora"))


def test_ami_inputs_and_hidden_oracles_are_separate() -> None:
    """人物输入不含 oracle 文本，参考答案位于独立 manifest 文件。"""
    manifest = load_manifest(Path("corpora"))
    for stage in "abcd":
        source = Path(f"corpora/ami-es2002/inputs/ES2002{stage}.txt")
        oracle = Path(f"corpora/ami-es2002/references/ES2002{stage}.oracle.json")
        expected = json.loads(oracle.read_text())["expected"]
        assert source.is_file() and source.stat().st_size > 1000
        assert expected["meeting_id"] == f"ES2002{stage}"
        assert expected["decisions"]
        assert hashlib.sha256(source.read_bytes()).hexdigest() != hashlib.sha256(oracle.read_bytes()).hexdigest()
    paths = {entry.path for entry in manifest.entries}
    assert "ami-es2002/inputs/ES2002a.txt" in paths
    assert "ami-es2002/references/ES2002a.oracle.json" in paths
