from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from tests.evaluation.conftest import _write_canonical, make_review_records
from worldmock.delivery import rules
from worldmock.evaluation.adapters import adapt_worldmock
from worldmock.evaluation.causality import evaluate_causality
from worldmock.evaluation.errors import EvaluationError
from worldmock.evaluation.metrics import shared_support
from worldmock.evaluation.runner import finalize_evaluation, prepare_evaluation
from worldmock.evaluation.scenario import load_scenario


def test_one_day_prepare_and_finalize_writes_complete_committed_delivery(
    evaluation_fixture, tmp_path: Path
) -> None:
    receipt = _prepare(evaluation_fixture)

    assert receipt["prepare_status"] == "awaiting_review"
    assert not (evaluation_fixture.output_root / "manifest.json").exists()
    assert len(list((evaluation_fixture.output_root / "review-packets").glob("*.json"))) == 3
    records = make_review_records(
        evaluation_fixture.output_root,
        tmp_path / "reviews",
        candidate_pattern=True,
    )
    manifest = finalize_evaluation(evaluation_fixture.output_root, records)

    assert manifest["committed"] is True
    assert manifest["result_status"] == "comparative_plausibility_only"
    assert manifest["daily_coverage"] == ["2026-01-05"]
    assert "report.md" in manifest["artifacts"]
    report = (evaluation_fixture.output_root / "report.md").read_text(encoding="utf-8")
    assert "只比较相对合理性" in report
    for path in (evaluation_fixture.output_root / "charts").glob("*.png"):
        assert path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_prepare_statistics_and_blind_sampling_are_byte_deterministic(
    evaluation_fixture, tmp_path: Path
) -> None:
    first = _prepare(evaluation_fixture)
    second_output = tmp_path / "second-output"
    second = prepare_evaluation(
        evaluation_fixture.labelkit_root,
        evaluation_fixture.worldmock_root,
        evaluation_fixture.scenario_path,
        second_output,
    )

    for name in ("metrics.json", "official-reference.json", "blind-review-pack.json"):
        assert first["prepared_artifacts"][name]["sha256"] == second["prepared_artifacts"][name]["sha256"]


def test_human_reference_changes_only_the_supported_result_status(
    evaluation_fixture, tmp_path: Path
) -> None:
    prepare_evaluation(
        evaluation_fixture.labelkit_root,
        evaluation_fixture.worldmock_root,
        evaluation_fixture.scenario_path,
        evaluation_fixture.output_root,
        evaluation_fixture.human_root,
    )
    records = make_review_records(evaluation_fixture.output_root, tmp_path / "reviews")

    manifest = finalize_evaluation(evaluation_fixture.output_root, records)

    assert manifest["result_status"] == "human_reference_comparison"
    result = json.loads((evaluation_fixture.output_root / "result.json").read_text(encoding="utf-8"))
    assert result["evidence_by_dimension"]["human_reference"] is not None


def test_prepared_artifact_tampering_blocks_finalize(evaluation_fixture, tmp_path: Path) -> None:
    _prepare(evaluation_fixture)
    metrics = evaluation_fixture.output_root / "metrics.json"
    metrics.write_bytes(metrics.read_bytes() + b" ")
    records = make_review_records(evaluation_fixture.output_root, tmp_path / "reviews")

    with pytest.raises(EvaluationError, match="size changed"):
        finalize_evaluation(evaluation_fixture.output_root, records)

    assert not (evaluation_fixture.output_root / "manifest.json").exists()


def test_production_causal_hard_error_disqualifies_only_its_candidate(
    evaluation_fixture, monkeypatch
) -> None:
    scenario = load_scenario(evaluation_fixture.scenario_path)
    view = adapt_worldmock(evaluation_fixture.worldmock_root, scenario)
    support = shared_support(view, view)

    def finding(events, fields):
        del fields
        return [{
            "code": "forced_error",
            "severity": "hard_error",
            "event_ids": [events[0]["source_event_id"]],
            "source_pointer": events[0]["source_pointer"],
            "message": "Forced causal failure",
        }]

    monkeypatch.setattr(rules, "evaluate_causal_events", finding)
    result = evaluate_causality(view, support)

    assert result.passed is False
    assert len(result.hard_errors) == 1


def test_no_shared_observation_returns_not_comparable(evaluation_fixture, tmp_path) -> None:
    """只有等待这类实体活动的运行与只含数字交互的参照没有共同观察通道（数字交互按活动类型判定）。"""
    root = tmp_path / "physical-export"
    _write_canonical(root, json.loads(evaluation_fixture.scenario_path.read_text(encoding="utf-8")),
                     "physical-run", 9, activity_type="wait")
    receipt = _prepare(replace(evaluation_fixture, worldmock_root=root))

    assert receipt["prepare_status"] == "not_comparable"


def _prepare(evaluation_fixture):
    return prepare_evaluation(
        evaluation_fixture.labelkit_root,
        evaluation_fixture.worldmock_root,
        evaluation_fixture.scenario_path,
        evaluation_fixture.output_root,
    )


