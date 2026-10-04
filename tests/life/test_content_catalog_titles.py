"""内容与游戏对象的人类可读名称合同。"""

from __future__ import annotations

from pathlib import Path

from worldmock.life.content import load_content_titles
from worldmock.life.world_setup import load_scenario


def test_every_content_item_has_corpus_title() -> None:
    """@brief 所有内容品类从语料身份目录取得标题。"""
    scenario, _ = load_scenario(Path("scenarios/white-collar-12-weeks.json"))
    titles = load_content_titles(scenario.contents, Path("corpora"))
    assert set(titles) == {item.item_id for item in scenario.contents}
    assert titles["podcast-nasa-space-habitat-excerpt"] == "Space Habitat (English interview excerpt)"
    assert titles["sintel-en-subtitles"] == "Sintel"
    assert titles["movie-0001"] == "一球定江山"
    assert titles["game-0001"] == "Age of Empires II: Definitive Edition"
    assert titles["news-feed"] == "中文新闻流（1000 条标题+摘要）"


def test_presentation_pools_keep_titles_for_every_content_and_game(engine_factory) -> None:
    """@brief 呈现窗口的每个内容与游戏候选都有可读名称。"""
    engine = engine_factory()
    agent = engine.context.state.agents[1]
    for entry in (*engine.observations._content_pool(agent), *engine.observations._game_pool(agent)):
        assert entry.row["title"].strip()
        assert entry.row["title"] != entry.object_id


def test_multi_person_scenario_titles_match(engine_factory) -> None:
    """@brief 多人物场景的内容候选同样从语料注入名称。"""
    engine = engine_factory("multi-person.json")
    agent = engine.context.state.agents[1]
    movies = [entry for entry in engine.observations._content_pool(agent) if entry.category == "feature_film"]
    assert len(movies) == 90
    assert all(entry.row["title"] for entry in movies)
