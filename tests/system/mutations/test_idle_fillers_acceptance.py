"""@brief 等待与工作起止的真实源码变异执行。"""

from __future__ import annotations
import json
import pytest
from tests.idle_fillers_acceptance import MUTATIONS, run_behavior


def _killer_nodes() -> list[str]:
    """全部登记变异的杀手节点（去重，保持登记顺序）。"""
    return list(dict.fromkeys(node for mutation in MUTATIONS for node in mutation.tests))


@pytest.fixture(scope="session")
def idle_fillers_mutation_baseline(tmp_path_factory):
    """同一源码上全部杀手用例必须先通过，随后才运行各个独立变异。"""
    directory = tmp_path_factory.mktemp("idle-fillers-baseline")
    result = run_behavior(tuple(_killer_nodes()), directory)
    (directory / "baseline-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    assert result["exit_code"] == 0, result["stdout"] + result["stderr"]
    return result


@pytest.mark.parametrize("mutation", MUTATIONS, ids=lambda mutation: mutation.name)
def test_idle_fillers_source_mutation_is_killed(mutation, idle_fillers_mutation_baseline, tmp_path):
    """每个非等价源码变异必须被行为断言杀死；未执行、存活、收集错误均不放行。"""
    result = run_behavior(mutation.tests, tmp_path, mutation)
    (tmp_path / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    assert result["original_sha256"] != result["mutated_sha256"]
    assert result["exit_code"] == 1, result["stdout"] + result["stderr"]
    assert "failed" in result["stdout"] and "ERROR collecting" not in result["stdout"]
