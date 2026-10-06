"""工具交互规格完整性、实际行为登记与源码变异门禁。"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from tests.acceptance_guard import DEFERRAL, acceptance_section, resolve_problem, stub_functions
from tests.tool_interaction_acceptance import BEHAVIOR, MUTATIONS, REPO_ROOT, run_behavior

FEATURE_SOURCES = (
    "src/worldmock/life/world_query.py", "src/worldmock/life/tool_groups.py",
    "src/worldmock/life/error_contract.py", "src/worldmock/life/inventory_recipe.py",
    "src/worldmock/life/request_errors.py",
    "src/worldmock/life/activity_evidence.py", "src/worldmock/life/callable_index.py",
    "src/worldmock/corpora/storefront.py",
    "src/worldmock/life/models.py", "src/worldmock/life/scenario.py", "src/worldmock/life/semantics.py",
    "src/worldmock/catalog/models.py", "src/worldmock/catalog/admission.py",
    "src/worldmock/exchange/types.py", "src/worldmock/exchange/constants.py",
    "src/worldmock/catalog/graph.py", "src/worldmock/life/travel.py", "src/worldmock/life/wayfinding.py",
    "src/worldmock/life/name_resolution.py", "src/worldmock/life/routing.py",
    "src/worldmock/life/named_purchase.py", "src/worldmock/life/storefront_state.py",
    "src/worldmock/life/work.py", "src/worldmock/life/observe.py", "src/worldmock/life/progress_layer.py",
    "src/worldmock/life/prompt_schemas.py", "src/worldmock/life/control_items.py",
    "src/worldmock/life/business_feedback.py", "src/worldmock/life/engine.py",
    "src/worldmock/life/session_actions.py", "src/worldmock/life/tool_layer.py",
    "src/worldmock/life/activity_control.py", "src/worldmock/life/interaction_menu.py",
    "custom/agents/life_agent.py", "custom/agents/life_feedback.py", "custom/envs/life_env.py",
    "custom/agents/life_continuation.py", "src/worldmock/life/todo_layer.py",
    "src/worldmock/life/motivation.py", "src/worldmock/footprints/purchases.py",
    "src/worldmock/footprints/quality.py",
)


def test_tool_interaction_every_spec_behavior_has_executable_tests():
    """规格行为逐项对应实际可收集测试，新增要求或缺项均直接失败。"""
    matrix = next(table for table in acceptance_section("tool-interaction", "实施与验收")["tables"]
                  if table[0][0] == "行为验收")
    required = {row[0] for row in matrix[1:]}
    assert set(BEHAVIOR) == required
    for behavior, nodes in BEHAVIOR.items():
        assert nodes, behavior
        for node in nodes:
            assert resolve_problem(node) is None, f"{behavior}: {node}: {resolve_problem(node)}"


def test_tool_interaction_sources_have_no_deferred_or_empty_implementation():
    """范围内生产文件不允许延期标记和空壳函数，测试登记不能用跳过代替验收。"""
    for relative in FEATURE_SOURCES:
        path = REPO_ROOT / relative
        assert path.is_file(), relative
        text = path.read_text(encoding="utf-8")
        assert not DEFERRAL.search(text), relative
        assert not stub_functions(ast.parse(text)), relative
    for nodes in BEHAVIOR.values():
        for node in nodes:
            assert resolve_problem(node) is None


def test_tool_interaction_declared_interface_contains_only_new_parameters():
    """新模型字段闭合；旧参数不存在于生产接口。"""
    from worldmock.life.models import StartActivityRequest
    from worldmock.catalog.models import WorkTopic
    from worldmock.life.scenario import LifeScenario
    from worldmock.life.world_query import QueryWorldRequest

    assert set(StartActivityRequest.model_fields) == {"items", "answering", "prefer"}
    assert set(QueryWorldRequest.model_fields) == {"name", "way", "category", "from_", "available_only", "offset"}
    assert set(WorkTopic.model_fields) == {"topic_id", "topic", "target_hours"}
    assert {"tasks", "map_dataset", "catalog_version"}.isdisjoint(LifeScenario.model_fields)


@pytest.fixture(scope="session")
def mutation_baseline(tmp_path_factory):
    """同一源码全部行为基础测试必须成功，随后才运行各个独立变异。"""
    nodes = tuple(dict.fromkeys(node for mutation in MUTATIONS for node in mutation.tests))
    directory = tmp_path_factory.mktemp("tool-interaction-baseline")
    result = run_behavior(nodes, directory)
    (directory / "baseline-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    assert result["exit_code"] == 0, result["stdout"] + result["stderr"]
    return result


@pytest.mark.parametrize("mutation", MUTATIONS, ids=lambda mutation: mutation.name)
def test_tool_interaction_source_mutation_is_killed(mutation, mutation_baseline, tmp_path):
    """每个非等价源码变异必须被行为断言杀死；未执行、存活、收集错误均不放行。"""
    result = run_behavior(mutation.tests, tmp_path, mutation)
    (tmp_path / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    assert result["original_sha256"] != result["mutated_sha256"]
    assert result["exit_code"] == 1, result["stdout"] + result["stderr"]
    assert "failed" in result["stdout"] and "ERROR collecting" not in result["stdout"]
