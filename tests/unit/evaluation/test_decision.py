"""最终评估结论的资格门禁测试。"""

from __future__ import annotations

import pytest

from worldmock.evaluation.decision import decide_result


def test_unanimous_evidence_can_support_an_eligible_candidate() -> None:
    """双方都无硬错误时，一致方向可以形成相对支持结论。"""
    result = decide_result(*_inputs("left", left_errors=0, right_errors=0))

    assert result["status"] == "comparative_plausibility_only"
    assert result["outcome"] == "left_more_supported"


@pytest.mark.parametrize(
    ("direction", "left_errors", "right_errors"),
    (("left", 1, 0), ("right", 0, 1)),
)
def test_causal_hard_error_blocks_the_affected_candidate_from_winning(
    direction: str,
    left_errors: int,
    right_errors: int,
) -> None:
    """即使其余维度一致，存在因果硬错误的候选也不能成为优胜者。"""
    result = decide_result(*_inputs(direction, left_errors, right_errors))

    eligibility = result["evidence_by_dimension"]["causal_eligibility"]
    assert result["outcome"] == "mixed"
    assert eligibility[f"{direction}_eligible"] is False
    assert eligibility["automatic_winner"] is None
    assert "not empirical validation" in result["claim_boundary"]


def _inputs(direction: str, left_errors: int, right_errors: int) -> tuple[dict, ...]:
    """形成所有可用方向都指向同一候选的最小输入。"""
    support = {"comparable": True}
    causality = {
        "left": {"hard_error_count": left_errors},
        "right": {"hard_error_count": right_errors},
    }
    metrics = {"human_reference": None}
    official = {
        "domains": {
            "paid_work": {"pair_alignment": {"status": "ok", "result": direction}},
        },
    }
    reviews = {
        "reliability": {"by_dimension": {"temporal": {"status": "ok", "alpha": 1.0}}},
        "preference": {
            "by_dimension": {
                "temporal": {
                    "week_block_confidence_interval": _interval(direction),
                },
            },
        },
        "reviewer_dependence": "model_sessions_may_be_correlated",
    }
    return support, causality, metrics, official, reviews


def _interval(direction: str) -> dict[str, float | str]:
    """形成排除零且方向明确的周块区间。"""
    if direction == "left":
        return {"status": "ok", "lower": 0.1, "upper": 0.5}
    return {"status": "ok", "lower": -0.5, "upper": -0.1}
