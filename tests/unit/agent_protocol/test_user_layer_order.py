"""user 数组的层序只由 USER_LAYER_NAMES 决定；宿主按层名取层，进度层必须是最后一层；只读问答尚无快照时的空记忆层由
记忆层的组装函数构建。"""

import logging

import pytest

from worldmock.agents.prompt.life_prompt import empty_memory_layer, last_progress_layer, life_user_layers, user_layer
from worldmock.agents.prompt.prompt_schemas import USER_LAYER_NAMES
from worldmock.simulation_engine.observation.memory.layout import assemble_layer

#: 观察里由环境构建的五层所在的键与层名。
_OBSERVATION_KEYS = {"memory_layer": "记忆层", "todo_layer": "待办层", "tool_layer": "工具层",
                     "environment_layer": "环境层", "progress_layer": "进度层"}


def _observation() -> dict:
    """五个键各放一个只带层名与键名标记的层对象。"""
    return {key: {"layer": name, "marker": key} for key, name in _OBSERVATION_KEYS.items()}


def _persona() -> dict:
    """宿主构建的人设层替身。"""
    return {"layer": "人设层", "marker": "persona"}


def test_user_layer_names_are_persona_memory_todo_tool_environment_progress():
    """层序：整次运行不变的人设层在前，进度层在最后。"""
    assert USER_LAYER_NAMES == ("人设层", "记忆层", "待办层", "工具层", "环境层", "进度层")


def test_life_user_layers_follow_user_layer_names():
    """六层按 USER_LAYER_NAMES 的次序返回：第一项就是传入的人设层，其余各项就是观察里的同一对象。"""
    observation, persona = _observation(), _persona()
    layers = life_user_layers(observation, persona)
    assert [layer["layer"] for layer in layers] == list(USER_LAYER_NAMES)
    assert layers[0] is persona
    by_name = {name: key for key, name in _OBSERVATION_KEYS.items()}
    assert all(layer is observation[by_name[layer["layer"]]] for layer in layers[1:])


@pytest.mark.parametrize("key", list(_OBSERVATION_KEYS))
@pytest.mark.parametrize("broken", ["missing", "misnamed"])
def test_a_missing_or_misnamed_observation_layer_is_refused(key, broken, caplog):
    """观察缺某一层或层名写错：以英文错误停止，错误日志同文。"""
    observation = _observation()
    if broken == "missing":
        del observation[key]
    else:
        observation[key]["layer"] = "别的层"
    message = f"LifeAgent observation layer {key} is unavailable"
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError, match=f"^{message}$"):
        life_user_layers(observation, _persona())
    assert message in caplog.messages


def test_user_layer_returns_the_one_layer_with_that_name():
    """按层名取层：每个层名都取到 life_user_layers 结果里的那一个对象。"""
    layers = life_user_layers(_observation(), _persona())
    for layer in layers:
        assert user_layer(layers, layer["layer"]) is layer


@pytest.mark.parametrize("count", [0, 2])
def test_user_layer_refuses_a_missing_or_repeated_layer(count, caplog):
    """没有或不止一层叫「记忆层」：以英文错误停止，错误日志写出同名层的个数。"""
    layers = [layer for layer in life_user_layers(_observation(), _persona()) if layer["layer"] != "记忆层"]
    layers[1:1] = [{"layer": "记忆层"} for _ in range(count)]
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError, match="^User layers must carry exactly one 记忆层$"):
        user_layer(layers, "记忆层")
    assert f"User layers carry {count} layers named 记忆层" in caplog.messages


def test_the_progress_layer_must_be_the_last_user_layer(caplog):
    """正确层序时返回最后一层；进度层挪到第一位或层数组为空时以英文错误停止，错误日志写出最后一层的层名。"""
    layers = life_user_layers(_observation(), _persona())
    assert last_progress_layer(layers) is layers[-1]
    moved = [layers[-1], *layers[:-1]]
    for broken, last in ((moved, "环境层"), ([], "None")):
        caplog.clear()
        with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError,
                                                           match="^Progress layer must be the last user layer$"):
            last_progress_layer(broken)
        assert f"User layers end with {last} instead of 进度层" in caplog.messages


def test_empty_memory_layer_is_the_assembled_empty_layer():
    """空记忆层恰为四个键的组装结果：「可查」「近三天」是空对象，「今天」是空列表；每次调用返回新对象。"""
    first = empty_memory_layer()
    assert first == assemble_layer(None, {}, []) == {"layer": "记忆层", "可查": {}, "近三天": {}, "今天": []}
    assert empty_memory_layer() is not first
