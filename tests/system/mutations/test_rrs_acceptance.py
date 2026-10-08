"""@brief 资源与规则的真实源码变异执行。"""

from __future__ import annotations
import re
import pytest
from tests import rrs_acceptance as registration
from tests.mutation_runner import CHILD_ENTRY, RunSettings, run_behavior


@pytest.fixture(scope="session")
def rrs_mutation_baseline(tmp_path_factory):
    """@brief 当前所有真实行为杀手先在独立解释器中通过，再执行源码变异。"""
    nodes = tuple(dict.fromkeys(node for mutation in registration.MUTATIONS for node in mutation.tests))
    options = ("-q", "--environment-profile", "dev", "-p", "no:cacheprovider")
    directory = tmp_path_factory.mktemp("rrs-mutation-baseline")
    result = run_behavior((*nodes, *options), directory, None,
                          RunSettings(CHILD_ENTRY, registration.RECEIPTS, 600))
    assert result["exit_code"] == 0, result["stdout"] + result["stderr"]
    return result


@pytest.mark.parametrize("mutation", registration.MUTATIONS, ids=lambda mutation: mutation.name)
def test_resource_rule_source_mutation_is_killed(mutation, rrs_mutation_baseline, tmp_path):
    """@brief 有效变异须由实际行为断言杀死，语法、导入、收集错误不能计为证据。"""
    options = ("-q", "--environment-profile", "dev", "-p", "no:cacheprovider")
    result = run_behavior((*mutation.tests, *options), tmp_path, mutation,
                          RunSettings(CHILD_ENTRY, registration.RECEIPTS, 180))
    assert result["original_sha256"] != result["mutated_sha256"]
    assert result["exit_code"] == 1, result["stdout"] + result["stderr"]
    assert "FAILED " in result["stdout"] and "ERROR collecting" not in result["stdout"]
    assert ("AssertionError" in result["stdout"]
            or re.search(r"^E\s+assert\b", result["stdout"], re.MULTILINE)
            or re.search(r"^E\s+Failed: DID NOT RAISE\b", result["stdout"], re.MULTILINE))
    assert "ImportError" not in result["stdout"] + result["stderr"]
