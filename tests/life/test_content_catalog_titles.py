"""browse_activities 内容品类结果的 title 注入：电影/剧集/网文/单片带语料标题。"""

from __future__ import annotations

import json
from pathlib import Path

from worldmock.life.content import load_content_titles
from worldmock.life.scenario import load_scenario

from .conftest import browse_all


def _browsed(engine_factory, category: str,
             scenario_name: str = "white-collar-12-weeks.json") -> list[dict]:
    """下钻一个内容品类并读取条目（观察只带 activity_summary 计数）。"""
    engine = engine_factory(scenario_name)
    return browse_all(engine, category)


def test_title_sources_cover_movies_dramas_novels_and_single_experience() -> None:
    """四类有语料标题的内容按条目注入，信息流与游戏不注入。"""
    scenario, _ = load_scenario(Path("scenarios/white-collar-12-weeks.json"))
    titles = load_content_titles(scenario.contents, Path("corpora"))
    by_id = {item.item_id: item.kind for item in scenario.contents}
    titled_kinds = {by_id[item_id] for item_id in titles}
    assert titled_kinds == {"feature_film", "drama_series", "podcast_episode", "webnovel_excerpt",
                            "subtitle_experience"}
    assert titles["podcast-nasa-space-habitat-excerpt"] == "Space Habitat (English interview excerpt)"
    assert titles["sintel-en-subtitles"] == "Sintel"
    movie_titles = {row["item_id"]: row["title"] for row
                    in json.loads(Path("corpora/movies/items.json").read_text())}
    assert titles["movie-0001"] == movie_titles["movie-0001"]


def test_browsed_catalog_entries_carry_titles(engine_factory) -> None:
    """品类下钻条目带 title 字段，查看器不再显示"电影 #0039"。"""
    movies = _browsed(engine_factory, "feature_film")
    by_id = {entry["item_id"]: entry for entry in movies}
    assert by_id["movie-0001"]["title"] == "一球定江山"
    singles = _browsed(engine_factory, "subtitle_experience")
    assert {entry["item_id"]: entry for entry in singles}["sintel-en-subtitles"]["title"] == "Sintel"
    feeds = _browsed(engine_factory, "news_feed")
    assert all("title" not in entry for entry in feeds)
    games = _browsed(engine_factory, "game_session")
    assert all("title" not in entry for entry in games)


def test_multi_person_scenario_titles_match(engine_factory) -> None:
    """多人物场景的品类下钻结果同样注入标题。"""
    movies = _browsed(engine_factory, "feature_film", "multi-person.json")
    assert len(movies) == 90
    assert all(entry.get("title") for entry in movies)
