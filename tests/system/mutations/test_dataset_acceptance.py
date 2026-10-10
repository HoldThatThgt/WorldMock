"""在隔离解释器中证明当前环境规则变异被真实行为断言杀死。"""

import re

import pytest

from tests.dataset_acceptance import MUTATIONS, RECEIPTS
from tests.mutation_runner import CHILD_ENTRY, RunSettings, run_behavior


@pytest.fixture(scope="session")
def dataset_mutation_baseline(tmp_path_factory):
    """全部杀手先在未变异的实际源码上通过。"""
    nodes = tuple(dict.fromkeys(node for mutation in MUTATIONS for node in mutation.tests))
    result = run_behavior((*nodes, "-q"), tmp_path_factory.mktemp("dataset-baseline"), None,
                          RunSettings(CHILD_ENTRY, RECEIPTS, 180))
    assert result["exit_code"] == 0, result["stdout"] + result["stderr"]


@pytest.mark.parametrize("mutation", MUTATIONS, ids=lambda mutation: mutation.name)
def test_current_source_mutation_is_killed(mutation, dataset_mutation_baseline, tmp_path):
    """编译、导入或收集错误不能冒充有效源码变异。"""
    result = run_behavior((*mutation.tests, "-q"), tmp_path, mutation,
                          RunSettings(CHILD_ENTRY, RECEIPTS, 180))
    output = result["stdout"] + result["stderr"]
    assert result["original_sha256"] != result["mutated_sha256"]
    assert result["exit_code"] == 1, output
    assert "FAILED " in output and "ERROR collecting" not in output
    assert "ImportError" not in output and "SyntaxError" not in output
    assert "AssertionError" in output or "DID NOT RAISE" in output or re.search(r"^E\s+assert\b", output, re.M)
