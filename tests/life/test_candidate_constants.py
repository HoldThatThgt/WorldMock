"""场景层候选与呈现常量的声明、默认值与拒绝规则测试。

覆盖 docs/CONTRACTS.md「候选与呈现合同」的「场景常量」一节：
五个常量在场景层声明（不落 catalog/manifest.json），其中 `rotation_stale_seconds`
无缺省、必须声明，其余四项取契约默认值。
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest
from pydantic import ValidationError

from worldmock.errors import IntegrityError
from worldmock.life.scenario import LifeScenario, load_scenario
from worldmock.life.semantics import load_runtime_scenario


_FORMAL = Path("scenarios/white-collar-12-weeks.json")

#: 契约默认值（docs/CONTRACTS.md「候选与呈现合同」的「场景常量」一节）。
_CONTRACT_DEFAULTS = {
    "candidate_presentation_limit": 20,
    "candidate_pool_limit": 1000,
    "rotation_quota_ratio": 0.15,
    "retrieval_index_threshold": 1000,
}

#: 三个生活场景文档；evaluation.json 不是瘦身场景文档（由 worldmock.evaluation
#: 的独立加载器读取，键集封闭且不含生活运行参数），因此不在此列。
_LIFE_SCENARIOS = (
    "white-collar-12-weeks.json",
    "multi-person.json",
    "multi-person-half-day.json",
)


def _payload() -> dict:
    """读取正式场景为每个反例提供独立对象。"""
    return json.loads(_FORMAL.read_text(encoding="utf-8"))


def _assert_invalid(payload: dict, message: str) -> None:
    """确认场景文档在启动前以预期原因失败。"""
    with pytest.raises(ValidationError, match=message):
        LifeScenario.model_validate(payload)


@pytest.mark.parametrize("name", _LIFE_SCENARIOS)
def test_candidate_constants_are_declared_in_every_life_scenario(name: str) -> None:
    """三份生活场景都能加载，且五个候选常量取到契约默认值与声明的阈值。"""
    scenario, digest = load_scenario(Path("scenarios") / name)

    assert len(digest) == 64
    for field, expected in _CONTRACT_DEFAULTS.items():
        assert getattr(scenario, field) == expected
    # 无缺省项：场景必须自己声明，且声明值须与「一个人物的作息周期」口径一致。
    assert scenario.rotation_stale_seconds == 604_800


def test_evaluation_scenario_is_not_a_life_scenario_document() -> None:
    """`scenarios/evaluation.json` 不是瘦身场景文档，不在候选常量的声明范围内。

    它由 `worldmock.evaluation.scenario.load_scenario` 读取（键集封闭、只描述评估
    窗口与抽样），既不经过 `load_scenario`，也不是 `LifeScenario`；把候选常量塞进
    去会同时破坏那个加载器与评估层的职责边界。
    """
    evaluation = json.loads(Path("scenarios/evaluation.json").read_text(encoding="utf-8"))

    assert "candidate_presentation_limit" not in evaluation
    assert "rotation_stale_seconds" not in evaluation
    with pytest.raises(IntegrityError, match="Scenario validation failed"):
        load_scenario(Path("scenarios/evaluation.json"))


def test_candidate_constants_are_reachable_from_assembled_semantics() -> None:
    """组装结果透出五个常量，下游不必各自解析场景文档。"""
    semantics, _ = load_runtime_scenario(_FORMAL)
    constants = semantics.candidate_constants

    assert constants.presentation_limit == semantics.scenario.candidate_presentation_limit == 20
    assert constants.pool_limit == semantics.scenario.candidate_pool_limit == 1000
    assert constants.rotation_quota_ratio == semantics.scenario.rotation_quota_ratio == 0.15
    assert constants.rotation_stale_seconds == semantics.scenario.rotation_stale_seconds == 604_800
    assert constants.retrieval_index_threshold == semantics.scenario.retrieval_index_threshold == 1000


def test_missing_rotation_stale_seconds_is_rejected() -> None:
    """`rotation_stale_seconds` 无缺省：缺失即拒载，不得落回任何隐含阈值。"""
    payload = _payload()
    del payload["rotation_stale_seconds"]

    _assert_invalid(payload, "rotation_stale_seconds")
    assert "rotation_stale_seconds" not in _CONTRACT_DEFAULTS


@pytest.mark.parametrize("value", [0, -1, -604_800])
def test_rotation_stale_seconds_must_be_positive(value: int) -> None:
    """阈值必须为正：零或负数会让每个对象恒被判为「长期未呈现」，轮转失去意义。"""
    _assert_invalid(_replace_field("rotation_stale_seconds", value), "greater than 0")


def _replace_field(field: str, value: object) -> dict:
    """替换一个顶层候选常量并返回反例。"""
    payload = _payload()
    payload[field] = value
    return payload


@pytest.mark.parametrize("value", [0, -1, -0.5])
def test_rotation_quota_ratio_must_be_positive(value: float) -> None:
    """比例的闭下界为零：零不给任何轮转名额，等于取消轮转。"""
    _assert_invalid(_replace_field("rotation_quota_ratio", value), "greater than 0")


@pytest.mark.parametrize("value", [1, 1.0, 1.5, 2])
def test_rotation_quota_ratio_upper_bound_is_open(value: float) -> None:
    """比例的上界取开区间：比例 1 把全部名额留给未呈现对象，排序键失效。"""
    _assert_invalid(_replace_field("rotation_quota_ratio", value), "less than 1")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_rotation_quota_ratio_rejects_non_finite_values(value: float) -> None:
    """`NaN` 与无穷必须拒绝：`NaN` 与任何数比较都为假，轮转判据会静默恒假。"""
    assert not math.isfinite(value)

    with pytest.raises(ValidationError, match="finite number"):
        LifeScenario.model_validate(_replace_field("rotation_quota_ratio", value))


@pytest.mark.parametrize(
    "field",
    ["candidate_presentation_limit", "candidate_pool_limit", "retrieval_index_threshold"],
)
@pytest.mark.parametrize("value", [0, -1, -1000])
def test_count_constants_must_be_positive(field: str, value: int) -> None:
    """三个数量类常量必须为正整数，零与负数一律拒绝。"""
    _assert_invalid(_replace_field(field, value), "greater than 0")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("candidate_presentation_limit", 1001),
        ("candidate_pool_limit", 100_001),
        ("retrieval_index_threshold", 100_001),
    ],
)
def test_count_constants_have_an_upper_bound(field: str, value: int) -> None:
    """数量类常量的上界拒绝超界取值：呈现上限超千条、池上限超十万条。

    池上限是闭式等待界 `⌈N ÷ k⌉ ÷ D` 的输入，十万条池在 k=3、D=71 下最坏等待
    约 470 天，已超出正式实验窗口，界就退化成不断言。
    """
    _assert_invalid(_replace_field(field, value), "less than or equal to")


def test_rotation_quota_ratio_and_presentation_limit_must_leave_a_quota() -> None:
    """比例与呈现上限的组合必须至少留出一个轮转名额。"""
    _assert_invalid(_replace_field("candidate_presentation_limit", 3),
                    "leave no rotation quota")
