"""@brief 已登记内容的标题直接投影；场景旧结构仍由严格模型拒绝。"""

import pytest
from pydantic import ValidationError

from tests.life.purchase_worlds import registered_world
from worldmock.life.content import load_content_titles
from worldmock.life.scenario import LifeScenario, RuntimeScenario


def test_content_titles_project_actual_registered_environment():
    """@brief 生产标题投影保留真实登记条目的身份和标题，不再读取字幕或旧内容池。"""
    semantics, _ = registered_world()
    assert load_content_titles(semantics.scenario.contents) == {
        identity: item.title for identity, item in semantics.catalog.contents.items()}
    assert load_content_titles(semantics.scenario.contents)["c-long"] == "测试长视频"


@pytest.mark.parametrize("model", [LifeScenario, RuntimeScenario])
def test_old_scenario_schema_is_rejected(model):
    """@brief 已退役场景结构在严格模型边界明确拒绝。"""
    with pytest.raises(ValidationError) as error:
        model.model_validate({"schema_version": 5})
    assert any(item["loc"] == ("schema_version",) and item["type"] == "literal_error"
               for item in error.value.errors())
