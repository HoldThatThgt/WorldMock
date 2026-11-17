"""LifeAgent 的形状：构造不做事、冻结输入取自人设表与运行清单、引擎句柄恰为五个同步方法、旧胶水与只读问答都已删除。"""

from __future__ import annotations

import ast
import builtins
import copy
import inspect
import io
import os
from pathlib import Path

import pytest

from tests.integration.runtime.current_contract_fixtures import inputs
from worldmock.agents.life_agent import LifeAgent, PersonInputs, person_inputs
from worldmock.agents.protocol.engine_handle import EngineHandle
from worldmock.configuration.persona.persona_card import persona_card, persona_card_struct
from worldmock.configuration.persona.persona_text import render_card
from worldmock.runtime.framework.setup import stage_model_identity
from worldmock.simulation_engine.engine import LifeEngine

#: 仓库里的生产源码根。
_SOURCE = Path(__file__).resolve().parents[3] / "src" / "worldmock"
#: 引擎句柄恰好声明的五个方法。
_HANDLE_METHODS = {"observe", "submit_action", "read_query", "record_model_exchange", "record_memory_update"}


class _Recorder:
    """任何属性访问都记下名字并返回一个记录调用的函数；构造之后应当一次也没被碰过。"""

    def __init__(self) -> None:
        self.touched: list[str] = []

    def __getattr__(self, name: str):
        """记下被访问的名字。"""
        self.touched.append(name)
        return lambda *arguments, **keywords: self.touched.append(f"{name}()")


def _identity() -> dict:
    """夹具配置冻结出的行动阶段模型身份（运行清单 stage_model_identity 的写法）。"""
    frozen = inputs()
    return stage_model_identity(frozen.config.model, frozen.model_metadata)


def test_life_agent_construction_reads_no_file_and_calls_no_service(monkeypatch) -> None:
    """构造只把四个参数存为同名公开属性、把本次决策的状态置为初值；不读写文件，不碰引擎、模型客户端与记忆存储，
    也不持有工作区路径。"""
    frozen = person_inputs(inputs().persona, _identity())
    engine, client, memory = _Recorder(), _Recorder(), _Recorder()

    def refuse(*arguments, **keywords):
        """构造期间任何打开文件的尝试都让用例失败。"""
        raise AssertionError("LifeAgent construction opened a file")

    for owner, name in ((builtins, "open"), (io, "open"), (os, "open"), (Path, "open"), (Path, "read_text"),
                        (Path, "write_text"), (Path, "read_bytes"), (Path, "write_bytes"), (Path, "mkdir")):
        monkeypatch.setattr(owner, name, refuse)
    agent = LifeAgent(frozen, engine, client, memory)
    monkeypatch.undo()
    assert (agent.inputs, agent.engine, agent.model_client, agent.memory) == (frozen, engine, client, memory)
    assert engine.touched == client.touched == memory.touched == []
    assert (agent._context, agent._names, agent._verified_experiences, agent._observation_event_id) == (
        None, {}, [], "")
    assert (agent._turn_models, agent._action_count, agent._world_calls, agent._memory_searches) == ({}, 0, 0, 0)
    assert (agent._call_positions, agent._memories) == ({}, [])
    assert not any(isinstance(value, Path) for value in vars(agent).values())


def test_person_inputs_take_the_card_and_name_from_the_persona_table_and_the_identity_from_the_manifest() -> None:
    """冻结输入逐值等于人设表与三处卡片函数；模型身份与清单的映射相等却不是同一对象，改一方的嵌套值不影响另一方；
    回合上限、续接开关与请求参数都在模型身份里。"""
    persona, identity = inputs().persona, _identity()
    frozen = person_inputs(persona, identity)
    card = persona_card(persona)
    assert isinstance(frozen, PersonInputs)
    assert (frozen.agent_id, frozen.name) == (persona.agent_id, persona.name)
    assert frozen.persona_card == render_card(card)
    assert frozen.persona_card_struct == persona_card_struct(card)
    assert frozen.model_identity == identity and frozen.model_identity is not identity
    original = copy.deepcopy(identity)
    frozen.model_identity["stages"]["action"]["max_tokens"] = 1
    assert identity == original
    identity["stages"]["action"]["same_second_continuation"] = not original["stages"]["action"][
        "same_second_continuation"]
    assert frozen.model_identity["stages"]["action"]["same_second_continuation"] == original["stages"]["action"][
        "same_second_continuation"]
    fresh = person_inputs(persona, original)
    assert fresh.model_identity["max_react_turns"] == original["max_react_turns"]
    request = LifeAgent(fresh, None, None, None).turn_request([], [], False)
    assert request["model"] == original["requested_model"]
    assert request["max_tokens"] == original["stages"]["action"]["max_tokens"]
    with pytest.raises(AttributeError):
        frozen.name = "别人"


def test_engine_handle_declares_the_five_methods_that_life_engine_provides() -> None:
    """引擎句柄的成员恰为五个方法；LifeEngine 各有同名方法，参数名相同，两边都不是协程函数。"""
    declared = {name for name, value in vars(EngineHandle).items() if inspect.isfunction(value)
                and not name.startswith("_")}
    assert declared == _HANDLE_METHODS
    for name in sorted(_HANDLE_METHODS):
        handle, engine = getattr(EngineHandle, name), getattr(LifeEngine, name)
        assert list(inspect.signature(engine).parameters) == list(inspect.signature(handle).parameters), name
        assert not inspect.iscoroutinefunction(engine) and not inspect.iscoroutinefunction(handle), name


def _defined_names(tree: ast.Module) -> set[str]:
    """一个模块里定义的全部函数名、类名与模块级赋值名。"""
    names = {node.name for node in ast.walk(tree)
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(
            node, ast.AnnAssign) else []
        names |= {target.id for target in targets if isinstance(target, ast.Name)}
    return names


def test_retired_person_glue_and_readonly_path_are_gone() -> None:
    """删去的胶水、只读问答与走不到的核对都不在生产源码里：没有这些名字的函数、类或模块级赋值，源码文字里没有这些
    报错与文件名；LifeAgent 没有框架入口；life_context.py 不存在。"""
    retired = {"HostFeedback", "feedback_report", "feedback_content", "ReadonlyFinishRequest", "READONLY_SYSTEM_LAYER",
               "readonly_messages", "empty_memory_layer", "PROMPT_SNAPSHOT_FILE", "write_prompt_snapshot",
               "readonly_layers", "persona_card_text"}
    texts = ("Environment is not initialized", "LLM is not initialized", "Model request evidence is unavailable",
             "Native tool call id is unavailable", "readonly decision state", "ReAct decision requires",
             "life_prompt_snapshot")
    found, mentioned = set(), set()
    for path in sorted(_SOURCE.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        found |= _defined_names(ast.parse(source)) & retired
        mentioned |= {text for text in texts if text in source}
    assert found == set() and mentioned == set()
    for name in ("description", "init_description", "_load_world_description", "from_workspace", "restore",
                 "to_workspace", "persist_agent_json", "step", "ask"):
        assert not hasattr(LifeAgent, name), name
    assert not (_SOURCE / "agents" / "prompt" / "life_context.py").exists()
