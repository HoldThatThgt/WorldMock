"""四个娱乐语料品类（片库/网文/剧集/段子）的完整性与场景准入测试。"""

from __future__ import annotations

import json
import hashlib
from pathlib import Path

from worldmock.corpora import load_manifest
from worldmock.life.content import load_subtitles
from worldmock.life.scenario import ContentItemConfig, load_scenario

SCENARIOS = ("scenarios/white-collar-12-weeks.json", "scenarios/multi-person.json")


def _items(name: str) -> list[dict]:
    return json.loads(Path(f"corpora/{name}/items.json").read_text(encoding="utf-8"))


def _manifest_index() -> dict[str, dict]:
    manifest = load_manifest(Path("corpora"))
    return {entry.path: entry for entry in manifest.entries}


def _scenario_item(scenario, item_id: str) -> ContentItemConfig:
    return next(item for item in scenario.contents if item.item_id == item_id)


def test_movie_library_size_fields_and_manifest_agreement() -> None:
    """片库 60-150 部，字段齐全，每张卡片 SRT 与 manifest 哈希一致且可解析。"""
    movies = _items("movies")
    assert 60 <= len(movies) <= 150
    index = _manifest_index()
    scenario = load_scenario(SCENARIOS[0])[0]
    required = {"item_id", "title", "year", "genres", "directors", "douban_score",
                "runtime_minutes", "duration_seconds", "timeline_path"}
    ids = set()
    for movie in movies:
        assert required <= set(movie)
        assert 1920 <= movie["year"] <= 2026
        assert 40 <= movie["runtime_minutes"] <= 240
        assert movie["duration_seconds"] == movie["runtime_minutes"] * 60
        assert movie["douban_score"] >= 6.5
        ids.add(movie["item_id"])
        entry = index[movie["timeline_path"]]
        digest = hashlib.sha256(Path(f"corpora/{movie['timeline_path']}").read_bytes()).hexdigest()
        assert entry.sha256 == digest
        cues = load_subtitles(_scenario_item(scenario, movie["item_id"]), Path("corpora"))
        assert len(cues) == 1
        assert cues[0].start_seconds == 0
        assert cues[0].end_seconds == movie["duration_seconds"]
        assert "剧情简介" in cues[0].text and movie["title"] in cues[0].text
    assert len(ids) == len(movies)


def test_no_random_preference_floats_survive_in_both_scenarios() -> None:
    """修订合同 L1661：随机偏好浮点删除——两场景人物不再携带偏好浮点键。"""
    movies = {movie["item_id"] for movie in _items("movies")}
    assert movies, "movie corpus must not be empty for the negative guard to bite"
    for path in SCENARIOS:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        for agent in doc["agents"]:
            assert "entertainment_preferences" not in agent, (
                f"{path} agent {agent['agent_id']} still carries random preference floats")


def test_webnovel_excerpts_are_bounded_and_annotated() -> None:
    """网文 5-8 本、单本 ≤2MB、标注节选，cue 数与章节数一致。"""
    novels = _items("novels")
    assert 5 <= len(novels) <= 8
    scenario = load_scenario(SCENARIOS[0])[0]
    for novel in novels:
        path = Path(f"corpora/{novel['timeline_path']}")
        assert path.stat().st_size <= 2 * 1024 * 1024
        assert novel["excerpt"] is True and "节选" in novel["note"]
        assert novel["duration_seconds"] > 0
        cues = load_subtitles(_scenario_item(scenario, novel["item_id"]), Path("corpora"))
        assert len(cues) == novel["chapters_selected"]
        assert cues[-1].end_seconds == novel["duration_seconds"]
        # duration = sum(max(1, round(chapter_chars/5)))：与 5 字/秒只差逐章取整误差
        assert abs(novel["duration_seconds"] - round(novel["total_chars"] / 5)) <= novel["chapters_selected"]


def test_drama_dialogue_streams_parse() -> None:
    """视觉小说对白流 3-12 部（单元 W 扩至 10），SRT 可解析且 label 传达剧集。"""
    dramas = _items("drama")
    assert 3 <= len(dramas) <= 12
    scenario = load_scenario(SCENARIOS[0])[0]
    for drama in dramas:
        item = _scenario_item(scenario, drama["item_id"])
        assert item.experience_label == "drama_series_watch"
        cues = load_subtitles(item, Path("corpora"))
        assert len(cues) == drama["lines_selected"]
        # 新 drama 语料（zh 整体替换）时长规则 = round(末 cue 结束秒)，
        # 毫秒尾差合法（10/10 条目按此核对）。
        assert round(cues[-1].end_seconds) == item.duration_seconds
        assert all(cue.text for cue in cues)


def test_joke_feed_entries_fit_short_read_window() -> None:
    """段子 ≤1 万条、每条 15-40 秒，feed SRT 与条目一一对应。"""
    jokes = _items("jokes")
    assert len(jokes) <= 10_000
    assert all(15 <= joke["estimated_read_seconds"] <= 40 for joke in jokes)
    ids = [joke["item_id"] for joke in jokes]
    assert len(set(ids)) == len(ids)
    scenario = load_scenario(SCENARIOS[0])[0]
    feed = _scenario_item(scenario, "jokes-feed")
    cues = load_subtitles(feed, Path("corpora"))
    assert len(cues) == len(jokes)
    assert feed.experience_label == "casual_joke_feed_scroll"
    assert cues[-1].end_seconds == feed.duration_seconds
    assert feed.duration_seconds == sum(joke["estimated_read_seconds"] for joke in jokes)


def test_both_scenarios_admit_all_entertainment_contents() -> None:
    """两场景准入：validate 全链路通过，全部新内容条目可交叉引用。"""
    movie_ids = {movie["item_id"] for movie in _items("movies")}
    novel_ids = {novel["item_id"] for novel in _items("novels")}
    drama_ids = {drama["item_id"] for drama in _items("drama")}
    extra_ids = novel_ids | drama_ids | {"jokes-feed"}
    for path in SCENARIOS:
        scenario = load_scenario(path)[0]
        item_ids = {item.item_id for item in scenario.contents}
        assert movie_ids | extra_ids <= item_ids
