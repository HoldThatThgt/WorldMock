"""@brief 当前工具接口与真实源码变异执行。"""

from __future__ import annotations
import json
import re
import pytest
from tests.tool_interaction_acceptance import MUTATIONS, run_behavior


def test_tool_interaction_declared_interface_contains_only_new_parameters():
    """新模型字段闭合；旧参数不存在于生产接口。"""
    from worldmock.life.models import StartActivityRequest, ActivityRequestItem
    from worldmock.catalog.models import Content
    from worldmock.life.scenario import LifeScenario
    from worldmock.life.world_query import QueryWorldRequest

    assert set(StartActivityRequest.model_fields) == {"items", "answering"}
    assert set(ActivityRequestItem.model_fields) == {"target", "way", "app", "to", "pay_app"}
    assert set(QueryWorldRequest.model_fields) == {"name", "way", "category", "app", "available_only", "offset"}
    assert {"content_id", "action_id", "app_ids", "title", "duration_seconds", "summary",
            "summary_source", "series"} == set(Content.model_fields)
    assert {"topic_id", "topic", "target_hours"}.isdisjoint(Content.model_fields)
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
    assert "AssertionError" in result["stdout"] or re.search(r"^E\s+assert\b", result["stdout"], re.MULTILINE)
