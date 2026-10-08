"""@brief 动作与内容的真实源码变异执行。"""

from __future__ import annotations
import re
from tests import acs_acceptance as registration
from tests.mutation_runner import CHILD_ENTRY, RunSettings, run_behavior


def test_action_content_source_mutations_are_killed(tmp_path) -> None:
    """@brief 先跑未变异行为基线，再逐个证明源码变异被断言杀死而非导入或收集错误。"""
    assert registration.MUTATIONS
    nodes = tuple(dict.fromkeys(node for mutation in registration.MUTATIONS for node in mutation.tests))
    options = ("-q", "--environment-profile", "dev", "-p", "no:cacheprovider")
    directory = tmp_path / "baseline"
    directory.mkdir()
    baseline = run_behavior((*nodes, *options), directory, None,
                            RunSettings(CHILD_ENTRY, registration.RECEIPTS, 1200))
    assert baseline["exit_code"] == 0, baseline["stdout"] + baseline["stderr"]
    for mutation in registration.MUTATIONS:
        directory = tmp_path / mutation.name
        directory.mkdir()
        result = run_behavior((*mutation.tests, *options), directory, mutation,
                              RunSettings(CHILD_ENTRY, registration.RECEIPTS, 300))
        assert result["original_sha256"] != result["mutated_sha256"]
        assert result["exit_code"] == 1, result["stdout"] + result["stderr"]
        assert "FAILED " in result["stdout"] and "ERROR collecting" not in result["stdout"]
        assert ("AssertionError" in result["stdout"]
                or re.search(r"^E\s+assert\b", result["stdout"], re.MULTILINE)
                or re.search(r"^E\s+Failed: DID NOT RAISE\b", result["stdout"], re.MULTILINE))
        assert "ImportError" not in result["stdout"] + result["stderr"]
