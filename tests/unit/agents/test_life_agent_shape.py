"""LifeAgent 的形状：无状态构造、冻结人设输入与运行模型身份、两类沙盒句柄与实现一致、旧胶水与只读问答都已删除。"""

from __future__ import annotations

import ast
import builtins
import copy
import inspect
import io
import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.integration.runtime.current_contract_fixtures import inputs
from worldmock.agents.life_agent import LifeAgent, PersonInputs, person_inputs
from worldmock.agents.protocol.engine_handle import SandboxHandle, SearchCatalogHandle
from worldmock.configuration.persona.persona_card import persona_card, persona_card_struct
from worldmock.configuration.persona.persona_text import render_card
from worldmock.runtime.framework.setup import model_identity
from worldmock.simulation_engine.activities.search.search_catalog import SearchCatalogView
from worldmock.simulation_engine.decisions.sandbox import AgentSandbox
from worldmock.state.identity import ModelIdentity

#: 仓库里的生产源码根。
_SOURCE = Path(__file__).resolve().parents[3] / "src" / "worldmock"
#: 觉醒沙盒句柄声明的七个同步方法。
_SANDBOX_METHODS = {"search_memory", "recall_task_summary", "lookup_targets", "dispatch_world_tool",
                    "record_model_exchange", "compact_snapshot", "finish_wakeup"}
#: 搜索目录句柄声明的四个同步方法。
_SEARCH_METHODS = {"search_goods", "inspect_goods_offers", "search_places", "validate_submission"}


def _identity() -> ModelIdentity:
    """夹具配置冻结出的运行期多档模型身份（不含凭据）。"""
    frozen = inputs()
    return model_identity(frozen.config, frozen.model_metadata)


def test_life_agent_construction_reads_no_file_and_calls_no_service(monkeypatch) -> None:
    """构造只保存冻结 PersonInputs，无任何可变实例状态，不读写文件也不持有工作区路径。"""
    frozen = person_inputs(inputs().persona_by_id[1])

    def refuse(*arguments, **keywords):
        """构造期间任何打开文件的尝试都让用例失败。"""
        raise AssertionError("LifeAgent construction opened a file")

    for owner, name in ((builtins, "open"), (io, "open"), (os, "open"), (Path, "open"), (Path, "read_text"),
                        (Path, "write_text"), (Path, "read_bytes"), (Path, "write_bytes"), (Path, "mkdir")):
        monkeypatch.setattr(owner, name, refuse)
    agent = LifeAgent(frozen)
    monkeypatch.undo()
    assert agent.inputs == frozen
    assert set(vars(agent)) == {"inputs"}
    assert not any(isinstance(value, Path) for value in vars(agent).values())


def test_person_inputs_take_the_card_and_name_from_the_persona_table_and_the_identity_from_the_manifest() -> None:
    """冻结输入逐值等于人设表与卡片函数；ModelIdentity 含多档路由与 action/search 指纹且不可变。"""
    persona = inputs().persona_by_id[1]
    identity = _identity()
    frozen = person_inputs(persona)
    card = persona_card(persona)
    assert isinstance(frozen, PersonInputs)
    assert (frozen.agent_id, frozen.name) == (persona.agent_id, persona.name)
    assert frozen.persona_card == render_card(card)
    assert frozen.persona_card_struct == persona_card_struct(card)
    assert set(identity.prompt_sha256s) == {"action", "search"}
    assert set(identity.tool_schema_sha256s) == {"action", "search"}
    dumped = identity.model_dump(mode="json")
    original = copy.deepcopy(dumped)
    dumped["profiles"][next(iter(dumped["profiles"]))]["effective_max_tokens"] = 1
    assert identity.model_dump(mode="json") == original
    with pytest.raises(ValidationError):
        identity.profiles = {}
    with pytest.raises(AttributeError):
        frozen.name = "别人"


def test_engine_handles_declare_exact_sync_methods_matching_sandbox_and_search_view() -> None:
    """沙盒句柄与搜索目录句柄声明的同步方法集合和参数签名与仿真实现完全一致。"""
    sandbox_declared = {name for name, value in vars(SandboxHandle).items()
                        if inspect.isfunction(value) and not name.startswith("_")}
    assert sandbox_declared == _SANDBOX_METHODS
    for name in sorted(_SANDBOX_METHODS):
        proto, impl = getattr(SandboxHandle, name), getattr(AgentSandbox, name)
        assert list(inspect.signature(impl).parameters) == list(inspect.signature(proto).parameters), name
        assert not inspect.iscoroutinefunction(impl) and not inspect.iscoroutinefunction(proto), name
    search_declared = {name for name, value in vars(SearchCatalogHandle).items()
                       if inspect.isfunction(value) and not name.startswith("_")}
    assert search_declared == _SEARCH_METHODS
    for name in sorted(_SEARCH_METHODS):
        proto, impl = getattr(SearchCatalogHandle, name), getattr(SearchCatalogView, name)
        assert list(inspect.signature(impl).parameters) == list(inspect.signature(proto).parameters), name
        assert not inspect.iscoroutinefunction(impl) and not inspect.iscoroutinefunction(proto), name


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
               "readonly_layers", "persona_card_text", "ReactDecision", "DecisionContext", "EngineHandle",
               "DirectToolCall", "PendingAction", "DecisionRoundState", "ActionReceipt", "SameMomentPresentation",
               "ReadQueryRequest", "ModelConfig", "continuation_enabled"}
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
