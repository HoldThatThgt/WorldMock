"""观察 content_catalog 的 title 注入：电影/剧集/网文/单片带语料标题。"""

from __future__ import annotations

import json
from pathlib import Path

from worldmock.life.content import load_content_titles
from worldmock.life.scenario import load_scenario

from .conftest import context


def _catalog(engine_factory, scenario_name: str = "white-collar-12-weeks.json") -> list[dict]:
    """读取首轮观察的 content_catalog（只观察不提交，不产生状态写入）。"""
    engine = engine_factory(scenario_name)
    contexts = [context(engine, "discover", agent_id)
                for agent_id in sorted(engine.state.agents)]
    engine.begin_decision_round(contexts)
    return engine.observe(contexts[0])["content_catalog"]


def test_title_sources_cover_movies_dramas_novels_and_single_experience() -> None:
    """四类有语料标题的内容按条目注入，信息流与游戏不注入。"""
    scenario, _ = load_scenario(Path("scenarios/white-collar-12-weeks.json"))
    titles = load_content_titles(scenario.contents, Path("corpora"))
    by_id = {item.item_id: item.kind for item in scenario.contents}
    titled_kinds = {by_id[item_id] for item_id in titles}
    assert titled_kinds == {"feature_film", "drama_series", "webnovel_excerpt",
                            "subtitle_experience"}
    assert titles["sintel-en-subtitles"] == "Sintel"
    movie_titles = {row["item_id"]: row["title"] for row
                    in json.loads(Path("corpora/movies/items.json").read_text())}
    assert titles["movie-0001"] == movie_titles["movie-0001"]


def test_observation_content_catalog_carries_titles(engine_factory) -> None:
    """观察目录条目带 title 字段，查看器不再显示"电影 #0039"。"""
    catalog = _catalog(engine_factory)
    by_id = {entry["item_id"]: entry for entry in catalog}
    assert by_id["movie-0001"]["title"] == "一球定江山"
    assert by_id["sintel-en-subtitles"]["title"] == "Sintel"
    assert "title" not in by_id["news-feed"]
    assert "title" not in by_id["game-0001"]


def test_multi_person_scenario_titles_match(engine_factory) -> None:
    """多人物场景的观察目录同样注入标题。"""
    catalog = _catalog(engine_factory, "multi-person.json")
    movies = [entry for entry in catalog if entry["kind"] == "feature_film"]
    assert len(movies) == 90
    assert all(entry.get("title") for entry in movies)
