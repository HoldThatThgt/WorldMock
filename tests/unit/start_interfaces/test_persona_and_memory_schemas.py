"""人设层与记忆层里今天条目的 Schema：人设层只由卡片构建、不带看法与偏好，亲疏闭集只有一处；今天条目接受【E8】的
形状，拒绝不合格的条目。"""

import copy
import inspect

import pytest

from custom.agents import life_agent
from tests.profile.conftest import make_card
from worldmock.agents.prompt import life_persona
from worldmock.agents.prompt.life_persona import persona_layer
from worldmock.agents.prompt.life_prompt import empty_memory_layer
from worldmock.agents.prompt.prompt_schemas import PERSONA_LAYER_SCHEMA, validate_layer
from worldmock.configuration.persona.persona_card import persona_card_struct
from worldmock.configuration.persona.persona_text import INTIMACY_LEVELS, intimacy_text

#: 人设层关系行的四个键，次序即渲染顺序。
_RELATIONSHIP_KEYS = ["person", "role", "closeness", "channels"]


def _card_persona() -> dict:
    """夹具人设表的卡片（关系依次是母亲、老周）构建出的人设层。"""
    return persona_layer({"persona_card_struct": persona_card_struct(make_card())}, "林舟")


def test_card_persona_layer_passes_and_carries_no_views():
    """卡片人设层的键依次是 layer、identity、rhythm、habits、tastes、coping、relationships；每行关系恰为四个键、按卡片
    顺序；人设层通过 Schema 校验。"""
    persona = _card_persona()
    assert list(persona) == ["layer", "identity", "rhythm", "habits", "tastes", "coping", "relationships"]
    assert [list(row) for row in persona["relationships"]] == [_RELATIONSHIP_KEYS] * 2
    assert [row["person"] for row in persona["relationships"]] == ["母亲", "老周"]
    validate_layer(persona)


@pytest.mark.parametrize("change", [
    lambda layer: layer.update(preferences=[]),
    lambda layer: layer["relationships"][0].update(view=None),
    lambda layer: layer["relationships"][0].update(role=None),
    lambda layer: layer["relationships"][0].update(closeness=None),
], ids=["preferences", "view", "null-role", "null-closeness"])
def test_persona_schema_rejects_views_and_view_only_rows(change):
    """人设层带顶层 preferences、关系行带 view、关系行的 role 或 closeness 为 null 都不合格；亲疏的 Schema 枚举就是
    INTIMACY_LEVELS，intimacy_text 的四档也读它。"""
    layer = copy.deepcopy(_card_persona())
    change(layer)
    with pytest.raises(ValueError):
        validate_layer(layer)
    closeness = PERSONA_LAYER_SCHEMA["properties"]["relationships"]["items"]["properties"]["closeness"]
    assert closeness == {"enum": list(INTIMACY_LEVELS)}
    assert [intimacy_text(value) for value in (30, 22, 21, 14, 13, 7, 6, 0)] == [
        INTIMACY_LEVELS[index] for index in (0, 0, 1, 1, 2, 2, 3, 3)]


def test_retired_persona_view_paths_are_gone():
    """看法进人设层的旧路径已删除：没有 _relationships 与 _persona_views，宿主改用 _relationship_persons；persona_layer
    只收档案与姓名；人设层 Schema 没有 preferences，关系行恰为四个键。"""
    assert not hasattr(life_persona, "_relationships")
    assert not hasattr(life_agent, "_persona_views") and hasattr(life_agent, "_relationship_persons")
    assert list(inspect.signature(persona_layer).parameters) == ["profile", "name"]
    assert "preferences" not in PERSONA_LAYER_SCHEMA["properties"]
    rows = PERSONA_LAYER_SCHEMA["properties"]["relationships"]["items"]["properties"]
    assert list(rows) == _RELATIONSHIP_KEYS


def _with_today(entry: dict) -> dict:
    """只有这一条今天条目的空记忆层。"""
    layer = empty_memory_layer()
    layer["今天"] = [entry]
    return layer


@pytest.mark.parametrize("entry", [
    {"时刻": "08:00", "类别": "生活", "核验": ["开始微信"]},
    {"时刻": "08:50", "类别": "工作、饮食", "自述": ["进展·处理消息与收尾：快收完了。"]},
    {"时刻": "11:50", "类别": "饮食", "核验": ["开始购买番茄牛肉刀削面"], "自述": ["正文：午饭点个面。"]},
    {"时刻": "23:59", "类别": "饮食、工作、生活", "核验": ["送达柠檬茶"], "自述": ["…"]},
], ids=["verified-only", "notes-only", "both", "ellipsis-notes"])
def test_today_entry_schema_accepts_the_entry_shapes(entry):
    """只带核验、只带自述、两者都带、自述为「…」的条目都通过记忆层校验。"""
    validate_layer(_with_today(entry))


@pytest.mark.parametrize("entry", [
    {"时刻": "08:00", "类别": "生活"},
    {"时刻": "24:00", "类别": "生活", "核验": ["开始微信"]},
    {"时刻": "08:00", "类别": "休息", "核验": ["开始微信"]},
    {"时刻": "08:00", "类别": "生活", "核验": []},
    {"时刻": "08:00", "类别": "生活", "核验": ["一" * 121]},
    {"时刻": "08:00", "类别": "生活", "核验": ["开始微信"], "条数": 1},
], ids=["neither", "midnight-clock", "rest-category", "empty-verified", "line-over-120", "with-count"])
def test_today_entry_schema_rejects_malformed_entries(entry):
    """既无核验也无自述、时刻 24:00、类别闭集外、核验为空列表、一项核验 121 字、条目带「条数」都不合格；同一处换成
    合格的条目时通过。"""
    validate_layer(_with_today({"时刻": "08:00", "类别": "生活", "核验": ["开始微信"]}))
    with pytest.raises(ValueError):
        validate_layer(_with_today(entry))
