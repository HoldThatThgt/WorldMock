"""@brief 内容标题由环境声明并直接用于人物呈现。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from worldmock.life.content import load_content_titles
from worldmock.life.scenario import ContentItemConfig, LifeScenario, RuntimeScenario


def _item(**changes) -> ContentItemConfig:
    """@brief 人工内容条目不依赖引擎中的正式来源目录。"""
    return ContentItemConfig.model_validate({
        "item_id": "external-story", "title": "外部环境中的故事", "kind": "webnovel_excerpt",
        "duration_seconds": 600, "timeline_path": "stories/example.srt", "timeline_sha256": "1" * 64,
        "entertainment_relief_per_hour": 0.3, "experience_label": "人工内容", **changes})


def test_content_title_is_projected_from_each_environments_declaration() -> None:
    """@brief 相同内容身份在不同环境中使用各自标题，不查询来源专属文件。"""
    mild = _item(title="  甲环境中的故事  ")
    other = _item(title="乙环境中的故事")
    assert load_content_titles((mild,)) == {"external-story": "甲环境中的故事"}
    assert load_content_titles((other,)) == {"external-story": "乙环境中的故事"}


@pytest.mark.parametrize("title", ["", " ", "\n\t", None, 3])
def test_content_title_requires_nonempty_text_and_logs_error(caplog, title) -> None:
    """@brief 空白或无效标题不能被身份编号或旧语料元数据替代。"""
    with pytest.raises(ValidationError, match="content title must be a nonempty string"):
        _item(title=title)
    assert any(record.levelname == "ERROR" for record in caplog.records)


def test_content_title_is_required() -> None:
    """@brief 缺失环境标题直接拒绝，不从旧来源元数据推断。"""
    payload = _item().model_dump()
    del payload["title"]
    with pytest.raises(ValidationError) as error:
        ContentItemConfig.model_validate(payload)
    assert any(item["loc"] == ("title",) and item["type"] == "missing" for item in error.value.errors())


@pytest.mark.parametrize("model", [LifeScenario, RuntimeScenario])
def test_old_scenario_schema_is_rejected(model) -> None:
    """@brief 内容标题发布到现行结构后，旧场景版本不能被加载。"""
    with pytest.raises(ValidationError) as error:
        model.model_validate({"schema_version": 5})
    assert any(item["loc"] == ("schema_version",) and item["type"] == "literal_error"
               for item in error.value.errors())


def test_presentation_pools_keep_titles_for_every_content_and_game(engine_factory) -> None:
    """@brief 呈现窗口的每个内容与游戏候选都有可读名称。"""
    engine = engine_factory()
    agent = engine.context.state.agents[1]
    for entry in (*engine.observations._content_pool(agent), *engine.observations._game_pool(agent)):
        assert entry.row["title"].strip()
        assert entry.row["title"] != entry.object_id


def test_multi_person_scenario_titles_match(engine_factory) -> None:
    """@brief 多人物环境的内容候选直接呈现场景声明的标题。"""
    engine = engine_factory("multi-person.json")
    agent = engine.context.state.agents[1]
    titles = {item.item_id: item.title for item in engine.context.scenario.contents}
    entries = engine.observations._content_pool(agent)
    assert entries
    assert all(entry.row["title"] == titles[entry.object_id] for entry in entries)
