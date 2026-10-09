"""@brief 源码变异的真实锚点和行为测试独立于历史验收台账。"""

import pytest

from tests import dataset_acceptance
from tests.acceptance_guard import resolve_problem
from tests.mutation_runner import mutated_source


@pytest.mark.parametrize("registration", [
    dataset_acceptance,
], ids=lambda registration: registration.__name__.rsplit(".", 1)[-1])
def test_source_mutations_have_unique_compilable_anchors_and_existing_killers(registration):
    """@brief 每个变异改变可编译源码，且对应实际存在、未跳过的行为测试。"""
    mutations = registration.MUTATIONS
    assert mutations
    assert len({mutation.name for mutation in mutations}) == len(mutations)
    for mutation in mutations:
        original, changed = mutated_source(mutation)
        assert original != changed, mutation.name
        assert mutation.tests, mutation.name
        for node in mutation.tests:
            assert resolve_problem(node) is None, (mutation.name, node, resolve_problem(node))


@pytest.mark.parametrize("body", [
    "def helper():\n    assert True\n",
    "import pytest\n@pytest.mark.skip\ndef test_real():\n    assert True\n",
    "import pytest\npytestmark = pytest.mark.skip\ndef test_real():\n    assert True\n",
    "import pytest\ndef test_real():\n    pytest.skip('unavailable')\n",
])
def test_mutation_killer_must_resolve_to_an_unskipped_test(tmp_path, body):
    """@brief 缺失的测试和明确跳过的测试不能充当变异杀手。"""
    (tmp_path / "test_sample.py").write_text(body, encoding="utf-8")
    assert resolve_problem("test_sample.py::test_real", tmp_path) is not None


def test_mutation_killer_resolves_parameterized_tests(tmp_path):
    """@brief 参数化节点仍解析到真实测试体，不依赖额外登记表。"""
    (tmp_path / "test_sample.py").write_text("def test_real():\n    assert True\n", encoding="utf-8")
    assert resolve_problem("test_sample.py::test_real[example]", tmp_path) is None
