"""@file     test_branch_coverage.py
@brief    C4d 覆盖率达标单元：evaluation 簇缺失分支与公共接口的行为测试。

只新增测试，不修改生产代码；统计类断言使用可手算的小夹具。
规格依据：`docs/design/01-architecture.md`「真实性评估与结论边界」（真实性评估合同）
与「测试矩阵」的行为指标行。
"""

from __future__ import annotations


from worldmock.evaluation.adapters import parse_world_event
import hashlib
import json
import re
import sys
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pytest

from tests.evaluation.conftest import make_evaluation_fixture, make_review_records
import worldmock.evaluation.aggregate_reference as aggregate_module
import worldmock.evaluation.causality as causality_module
import worldmock.evaluation.io as io_module
import worldmock.evaluation.metrics as metrics_module
import worldmock.evaluation.reporting as reporting_module
import worldmock.evaluation.review as review_module
import worldmock.evaluation.runner as runner_module
from worldmock.evaluation.adapters import adapt_labelkit, adapt_worldmock
from worldmock.evaluation.causality import evaluate_causality
from worldmock.evaluation.decision import decide_result
from worldmock.evaluation.errors import EvaluationError
from worldmock.evaluation.io import (
    atomic_write_json,
    atomic_write_jsonl,
    atomic_write_text,
    read_json,
    read_jsonl,
    sha256_file,
    stable_json_bytes,
    verify_artifact,
)
from worldmock.evaluation.metrics import (
    evaluate_metrics,
    shared_support,
)
from worldmock.evaluation.models import (
    ActivitySession,
    DatasetView,
    DayRecord,
    EvidenceEvent,
    PublicEvidence,
    TimeWindow,
    activity_intervals,
    parse_datetime,
    parse_utc_seconds,
)
from worldmock.evaluation.public_evidence import (
    labelkit_public_evidence,
    worldmock_activity_action,
    worldmock_public_evidence,
)
from worldmock.evaluation.reporting import ReportBundle, render_report
from worldmock.evaluation.review import create_review_material, ingest_reviews, write_review_material
from worldmock.evaluation.runner import finalize_evaluation, prepare_evaluation
from worldmock.evaluation.scenario import load_scenario
from worldmock.evaluation.validation import validate_canonical_delivery, validate_labelkit_delivery
from worldmock.footprints.models import Checkpoint, canonical_json

ZONE = ZoneInfo("Asia/Shanghai")
WINDOW = TimeWindow(
    datetime(2026, 1, 5, tzinfo=ZONE), datetime(2026, 1, 7, tzinfo=ZONE), "Asia/Shanghai"
)


def _session(
    category: str = "browsing",
    started: datetime | None = None,
    channels: tuple[str, ...] = ("digital_interaction",),
    duration_kind: str = "observed_digital_session",
    domains: tuple[str, ...] = ("internet_use",),
) -> ActivitySession:
    """构造一个最小活动会话夹具。"""
    start = started or datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=1)
    return ActivitySession(
        "session-1", "user", category, start, end, 3600.0, "observed", channels, None,
        duration_kind, domains, (), "pointer",
    )


def _evidence(
    category: str = "purchase",
    interaction: str = "order",
    object_type: str = "order",
    state: str | None = "requested",
    object_key: str | None = "order-key",
    occurred: datetime | None = None,
    attributes: dict[str, str] | None = None,
) -> PublicEvidence:
    """构造一条最小公开证据夹具。"""
    return PublicEvidence(
        occurred or datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc), category, interaction,
        object_type, state, object_key, attributes or {}, ("event-1",), "session-1",
    )


def _view(
    activities: tuple[ActivitySession, ...] = (),
    events: tuple[EvidenceEvent, ...] = (),
    public: tuple[PublicEvidence, ...] = (),
    days: tuple[DayRecord, ...] = (),
) -> DatasetView:
    """构造一个最小数据视图夹具。"""
    return DatasetView(
        "test", "test-dataset", "test-run", WINDOW, events, activities, days, {}, {"random_state": "real-frozen-test-root"}, public,
    )


def _public_event(
    sequence: int,
    event_type: str,
    data: dict[str, Any],
    action_id: str | None = None,
    activity_id: str | None = None,
    run_id: str = "public-test-run",
) -> dict[str, Any]:
    """构造通过生产 Event 模型校验的公开投影输入。"""
    from worldmock.footprints.models import Event

    event = Event(
        run_id=run_id,
        event_id=f"public-event-{sequence}",
        event_seq=sequence,
        event_type=event_type,
        occurred_at=int(datetime(2026, 1, 5, tzinfo=timezone.utc).timestamp()) + sequence,
        agent_id=1,
        action_id=action_id,
        activity_id=activity_id,
        data=data,
        recorded_at="2026-01-05T00:00:00+00:00",
        wall_elapsed_ms=0,
        state_version_before=sequence - 1,
        state_version_after=sequence,
    )
    return event.model_dump(mode="json")


# ---------------------------------------------------------------------------
# io.py：严格 JSON 输入、原子输出与文件身份校验（`docs/design/01-architecture.md`「真实性评估与结论边界」的输入身份合同）
# ---------------------------------------------------------------------------


def test_sha256_file_reports_missing_artifact(tmp_path: Path) -> None:
    with pytest.raises(EvaluationError, match="cannot read artifact"):
        sha256_file(tmp_path / "absent.json")
    present = tmp_path / "ok.json"
    present.write_bytes(b"payload")
    assert sha256_file(present) == hashlib.sha256(b"payload").hexdigest()


def test_read_json_rejects_unreadable_invalid_and_non_object(tmp_path: Path) -> None:
    with pytest.raises(EvaluationError, match="cannot read JSON object"):
        read_json(tmp_path / "missing.json")
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(EvaluationError, match="cannot read JSON object"):
        read_json(broken)
    array = tmp_path / "array.json"
    array.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(EvaluationError, match="JSON root must be an object"):
        read_json(array)


def test_read_jsonl_rejects_missing_blank_invalid_and_non_object_rows(tmp_path: Path) -> None:
    with pytest.raises(EvaluationError, match="cannot open JSONL artifact"):
        read_jsonl(tmp_path / "missing.jsonl")
    blank = tmp_path / "blank.jsonl"
    blank.write_text('{"a": 1}\n\n', encoding="utf-8")
    with pytest.raises(EvaluationError, match="blank JSONL row"):
        read_jsonl(blank)
    broken = tmp_path / "broken.jsonl"
    broken.write_text('{"a": 1}\n{oops}\n', encoding="utf-8")
    with pytest.raises(EvaluationError, match="invalid JSONL row"):
        read_jsonl(broken)
    scalar = tmp_path / "scalar.jsonl"
    scalar.write_text("[1]\n", encoding="utf-8")
    with pytest.raises(EvaluationError, match="JSONL row must be an object"):
        read_jsonl(scalar)
    good = tmp_path / "good.jsonl"
    good.write_text('{"b": 2}\n', encoding="utf-8")
    assert read_jsonl(good) == [{"b": 2}]


def test_verify_artifact_rejects_invalid_descriptor_and_row_mismatch(tmp_path: Path) -> None:
    target = tmp_path / "rows.jsonl"
    target.write_bytes(b'{"x": 1}\n{"x": 2}\n')
    digest = hashlib.sha256(b'{"x": 1}\n{"x": 2}\n').hexdigest()
    with pytest.raises(EvaluationError, match="sha256 must be a 64-character string"):
        verify_artifact(target, {"sha256": "tooshort"})
    with pytest.raises(EvaluationError, match="rows must be an integer"):
        verify_artifact(target, {"sha256": digest, "rows": True})
    with pytest.raises(EvaluationError, match="row count mismatch"):
        verify_artifact(target, {"sha256": digest, "rows": 3})
    identity = verify_artifact(target, {"sha256": digest, "rows": 2})
    assert identity.rows == 2 and identity.sha256 == digest
    assert verify_artifact(target, {"sha256": digest}).rows is None
    with pytest.raises(EvaluationError, match="SHA-256 mismatch"):
        verify_artifact(target, {"sha256": "0" * 64})
    with pytest.raises(EvaluationError, match="cannot count artifact rows"):
        io_module._count_rows(tmp_path / "vanish.jsonl")


def test_stable_json_bytes_serializes_time_paths_and_collections() -> None:
    payload = {
        "when": datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc),
        "day": date(2026, 1, 5),
        "where": Path("/tmp/x"),
        "tags": {"b", "a"},
        "frozen": frozenset({"z"}),
        "pair": (1, 2),
    }
    text = stable_json_bytes(payload).decode("utf-8")
    decoded = json.loads(text)
    assert decoded["when"] == "2026-01-05T10:00:00+00:00"
    assert decoded["day"] == "2026-01-05"
    assert decoded["where"] == str(Path("/tmp/x"))
    assert set(decoded["tags"]) == {"a", "b"}
    assert decoded["frozen"] == ["z"]
    assert decoded["pair"] == [1, 2]
    with pytest.raises(TypeError, match="unsupported JSON value"):
        stable_json_bytes({"bad": object()})


def test_atomic_writers_produce_deterministic_files_and_survive_os_error(
    tmp_path: Path, monkeypatch
) -> None:
    rows = [{"b": 2, "a": 1}, {"a": 3}]
    target = tmp_path / "out.jsonl"
    atomic_write_jsonl(target, rows)
    assert target.read_bytes() == (
        b'{"a":1,"b":2}\n{"a":3}\n'
    )
    json_target = tmp_path / "nested" / "out.json"
    atomic_write_json(json_target, {"z": 1, "a": 2})
    assert json.loads(json_target.read_text(encoding="utf-8")) == {"a": 2, "z": 1}
    text_target = tmp_path / "note.txt"
    atomic_write_text(text_target, "hello")
    assert text_target.read_text(encoding="utf-8") == "hello"

    def _boom(source: Any, destination: Any) -> None:
        raise OSError("disk gone")

    monkeypatch.setattr(io_module.os, "replace", _boom)
    with pytest.raises(EvaluationError, match="cannot write evaluation artifact"):
        atomic_write_text(tmp_path / "fail.txt", "x")


# ---------------------------------------------------------------------------
# models.py：时间解析、窗口不变量与时长属性（接口全行覆盖）
# ---------------------------------------------------------------------------


def test_parse_datetime_rejects_missing_invalid_and_naive_values() -> None:
    assert parse_datetime("2026-01-05T10:00:00Z", "f") == datetime(
        2026, 1, 5, 10, 0, tzinfo=timezone.utc
    )
    with pytest.raises(EvaluationError, match="must be an ISO-8601 string"):
        parse_datetime(5, "f")
    with pytest.raises(EvaluationError, match="must be an ISO-8601 string"):
        parse_datetime("", "f")
    with pytest.raises(EvaluationError, match="is not valid ISO-8601"):
        parse_datetime("not-a-date", "f")
    with pytest.raises(EvaluationError, match="must include a timezone"):
        parse_datetime("2026-01-05T10:00:00", "f")


def test_parse_utc_seconds_rejects_non_integer_and_out_of_range() -> None:
    assert parse_utc_seconds(0, "f").tzinfo is not None
    with pytest.raises(EvaluationError, match="must be integer UTC seconds"):
        parse_utc_seconds(True, "f")
    with pytest.raises(EvaluationError, match="must be integer UTC seconds"):
        parse_utc_seconds("5", "f")
    with pytest.raises(EvaluationError, match="outside supported range"):
        parse_utc_seconds(10**19, "f")


def test_time_window_enforces_zone_order_and_midnight_alignment() -> None:
    with pytest.raises(EvaluationError, match="window timezone is unknown"):
        TimeWindow(datetime(2026, 1, 5, tzinfo=ZONE), datetime(2026, 1, 6, tzinfo=ZONE), "Mars/Phobos")
    with pytest.raises(EvaluationError, match="start must precede end"):
        TimeWindow(datetime(2026, 1, 6, tzinfo=ZONE), datetime(2026, 1, 5, tzinfo=ZONE), "Asia/Shanghai")
    with pytest.raises(EvaluationError, match="start must be local midnight"):
        TimeWindow(
            datetime(2026, 1, 5, 1, 0, tzinfo=ZONE), datetime(2026, 1, 6, tzinfo=ZONE), "Asia/Shanghai"
        )
    with pytest.raises(EvaluationError, match="end must be local midnight"):
        TimeWindow(
            datetime(2026, 1, 5, tzinfo=ZONE), datetime(2026, 1, 6, 1, 0, tzinfo=ZONE), "Asia/Shanghai"
        )
    window = TimeWindow(
        datetime(2026, 1, 5, tzinfo=ZONE), datetime(2026, 1, 7, tzinfo=ZONE), "Asia/Shanghai"
    )
    assert window.days == (date(2026, 1, 5), date(2026, 1, 6))
    assert window.contains(datetime(2026, 1, 5, 12, 0, tzinfo=ZONE))
    assert not window.contains(datetime(2026, 1, 7, 0, 0, tzinfo=ZONE))
    assert window.localize(datetime(2026, 1, 5, 1, 0, tzinfo=timezone.utc)).tzinfo.key == "Asia/Shanghai"


def test_duration_seconds_uses_authenticated_elapsed_time() -> None:
    start = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)
    spanned = replace(_session(), started_at=start, ended_at=start + timedelta(hours=2), elapsed_seconds=3600)
    assert spanned.duration_seconds == 3600.0
    inverted = replace(spanned, elapsed_seconds=0)
    assert inverted.duration_seconds == 0.0


# ---------------------------------------------------------------------------
# causality.py：生产因果检查器合同（finding 冻结模式与硬错误门槛）
# ---------------------------------------------------------------------------


def _finding(**overrides: Any) -> dict[str, Any]:
    value = {
        "code": "order_without_payment",
        "severity": "hard_error",
        "event_ids": ["event-1"],
        "source_pointer": "events.jsonl:1",
        "message": "order never paid",
    }
    value.update(overrides)
    return value


def test_evaluate_causality_passes_hard_errors_through(_=None) -> None:
    import worldmock.evaluation.causality as causality

    original = causality.evaluate_causal_events
    try:
        causality.evaluate_causal_events = lambda events, support: [_finding()]
        result = evaluate_causality(_view(), {})
        assert result.passed is False
        assert result.hard_errors[0]["code"] == "order_without_payment"
        assert result.to_dict()["hard_error_count"] == 1
    finally:
        causality.evaluate_causal_events = original


def test_evaluate_causality_reports_unavailable_checker(monkeypatch) -> None:
    import importlib
    import worldmock.evaluation.causality as causality

    with monkeypatch.context() as patch:
        patch.setitem(sys.modules, "worldmock.evaluation.causal_checks", None)
        with pytest.raises(EvaluationError, match="causal evaluator is unavailable"):
            importlib.reload(causality)
    importlib.reload(causality)


@pytest.mark.parametrize(
    "payload, match",
    [
        ({}, "findings must be an array"),
        ([{}], "finding schema is invalid"),
        ([_finding(code=5)], "finding code is invalid"),
        ([_finding(event_ids="event-1")], "event_ids is invalid"),
        ([_finding(event_ids=[1])], "event_ids is invalid"),
        ([_finding(severity="soft_warning")], "severity is invalid"),
    ],
)
def test_evaluate_causality_rejects_invalid_finder_payloads(
    monkeypatch, payload: Any, match: str
) -> None:
    import worldmock.evaluation.causality as causality

    monkeypatch.setattr(causality, "evaluate_causal_events", lambda events, support: payload)
    with pytest.raises(EvaluationError, match=match):
        evaluate_causality(_view(), {})


def test_evaluate_causality_wraps_checker_failure(monkeypatch) -> None:
    import worldmock.evaluation.causality as causality

    def _explode(events: Any, support: Any) -> None:
        raise RuntimeError("checker bug")

    monkeypatch.setattr(causality, "evaluate_causal_events", _explode)
    with pytest.raises(EvaluationError, match="causal evaluator failed"):
        evaluate_causality(_view(), {})


# ---------------------------------------------------------------------------
# decision.py：结论规则（不可比较、区间方向与一致方向，`docs/design/01-architecture.md`「真实性评估与结论边界」）
# ---------------------------------------------------------------------------


def _decision_inputs() -> tuple[dict[str, Any], ...]:
    support = {"comparable": True, "shared_channels": ["digital_interaction"]}
    causality = {"left": {"hard_error_count": 0}, "right": {"hard_error_count": 0}}
    official = {"domains": {"internet_use": {"pair_alignment": {"status": "ok", "result": "left"}}}}
    reviews = {
        "preference": {"by_dimension": {"temporal_plausibility": {
            "week_block_confidence_interval": {"status": "ok", "lower": 0.2, "upper": 0.5},
        }}},
        "reliability": {"by_dimension": {"temporal_plausibility": {"status": "ok", "alpha": 0.9}}},
        "reviewer_dependence": {"model_review_count": 3, "human_review_count": 0},
    }
    return support, causality, official, reviews


def test_decide_result_returns_not_comparable_without_shared_support() -> None:
    support, causality, official, reviews = _decision_inputs()
    result = decide_result({"comparable": False, "shared_channels": []}, causality, {}, official, reviews)
    assert result["status"] == "not_comparable" and result["outcome"] == "no_comparison"
    assert result["missing_support"] == {"shared_channels": []}


def test_decide_result_uses_interval_and_alpha_gates_for_directions() -> None:
    support, causality, official, reviews = _decision_inputs()
    reviews["preference"]["by_dimension"]["temporal_plausibility"][
        "week_block_confidence_interval"] = {"status": "ok", "lower": -0.5, "upper": -0.1}
    result = decide_result(support, causality, {}, {"domains": {}}, reviews)
    dimension = result["evidence_by_dimension"]["blind_review"]["directions"]["temporal_plausibility"]
    assert dimension["raw_direction"] == "right"
    assert dimension["reliability_status"] == "adequate"
    assert result["status"] == "comparative_plausibility_only"

    reviews["reliability"]["by_dimension"]["temporal_plausibility"] = {"status": "ok", "alpha": 0.5}
    weak = decide_result(support, causality, {}, {"domains": {}}, reviews)
    weak_dimension = weak["evidence_by_dimension"]["blind_review"]["directions"]["temporal_plausibility"]
    assert weak_dimension["accepted_direction"] == "mixed"
    assert weak_dimension["reliability_status"] == "insufficient"


def test_decide_result_declares_right_winner_only_when_all_directions_agree() -> None:
    support, causality, _, reviews = _decision_inputs()
    official = {"domains": {"internet_use": {"pair_alignment": {"status": "ok", "result": "right"}}}}
    reviews["preference"]["by_dimension"]["temporal_plausibility"][
        "week_block_confidence_interval"] = {"status": "ok", "lower": -0.5, "upper": -0.2}
    result = decide_result(support, causality, {}, official, reviews)
    assert result["outcome"] == "right_more_supported"

    hard = {"left": {"hard_error_count": 1}, "right": {"hard_error_count": 0}}
    blocked = decide_result(support, hard, {}, official, reviews)
    assert blocked["outcome"] == "mixed"
    assert blocked["evidence_by_dimension"]["causal_eligibility"]["left_eligible"] is False


def test_decide_result_human_reference_distances_and_claim_boundaries() -> None:
    support, causality, official, reviews = _decision_inputs()
    metrics = {"human_reference": {"status": "ok", "week_hour_rhythm": {
        "left_distance": 0.2, "right_distance": 0.4,
    }}}
    result = decide_result(support, causality, metrics, {"domains": {}}, reviews)
    human = result["evidence_by_dimension"]["human_reference"]
    assert human["directions"]["week_hour_rhythm"] == "left"
    assert result["status"] == "human_reference_comparison"
    assert "matched human observation window" in result["claim_boundary"]


# ---------------------------------------------------------------------------
# metrics.py：分维度统计的缺失分支（对比口径与人类参照支持掩码）
# ---------------------------------------------------------------------------


def test_week_hour_histogram_counts_observed_actual_actions() -> None:
    view = _view(activities=(_session(category="secret"),))
    histogram = metrics_module._week_hour_histogram(view, ("secret",))
    assert sum(histogram) == 1.0


def test_day_type_contrast_returns_none_without_both_day_types() -> None:
    workday = DayRecord(date(2026, 1, 5), "regular_workday", (), 0, ())
    assert metrics_module._day_type_contrast((workday,), (1.0, 2.0)) is None
    block = (date(2026, 1, 5),)
    values = {date(2026, 1, 5): 3.0}
    classes = {date(2026, 1, 5): "regular_workday"}
    assert metrics_module._block_contrast(block, values, classes) is None








def test_duration_group_keeps_non_digital_kinds() -> None:
    assert metrics_module._duration_group("world_activity") == "world_activity"
    assert metrics_module._duration_group("world_internet_session") == "digital_session"


def test_human_metrics_report_not_comparable_without_shared_support(tmp_path: Path) -> None:
    fixture = make_evaluation_fixture(tmp_path)
    scenario = load_scenario(fixture.scenario_path)
    left = adapt_labelkit(fixture.labelkit_root, scenario)
    right = adapt_worldmock(fixture.worldmock_root, scenario)
    empty_human = _view()
    result = metrics_module._human_metrics(left, right, empty_human, scenario)
    assert result == {"status": "not_comparable", "reason": "no shared observation support"}
    full = evaluate_metrics(left, right, scenario, empty_human)
    assert full["human_reference"]["status"] == "not_comparable"


def test_shared_public_fields_report_empty_intersection_for_disjoint_objects() -> None:
    left = _view(public=(_evidence(category="purchase"),))
    right = _view(public=(_evidence(category="travel", interaction="travel",
                                     object_type="route"),))
    shared, coverage = metrics_module._shared_public_fields(left, right)
    assert shared == {}
    assert coverage["left"]["order"]["interaction"] == {"present": 1, "eligible": 1}


# ---------------------------------------------------------------------------
# aggregate_reference.py：官方聚合参照诊断（支持语义与边际支配）
# ---------------------------------------------------------------------------


def test_domain_diagnostic_reports_unsupported_duration_semantics(tmp_path: Path) -> None:
    fixture = make_evaluation_fixture(tmp_path)
    scenario = load_scenario(fixture.scenario_path)
    reference = scenario.official_reference["domains"]["personal_essential"]
    view = _view(activities=(_session(domains=("personal_essential",),
                                        duration_kind="mysterious_kind"),))
    result = aggregate_module._domain_diagnostic(view, "personal_essential", reference)
    assert result == {"status": "unsupported", "reason": "no_supported_duration_semantics"}


def test_activity_intervals_and_slot_helpers_use_authoritative_session_boundary() -> None:
    session = _session()
    assert activity_intervals(session, ()) == (
        (session.started_at, session.ended_at),
    )
    day_start = datetime(2026, 1, 5, tzinfo=ZONE)
    assert aggregate_module._overlapping_slots(
        datetime(2026, 1, 6, 12, 0, tzinfo=ZONE), datetime(2026, 1, 6, 13, 0, tzinfo=ZONE), day_start
    ) == ()
    assert aggregate_module._overlapping_slots(
        datetime(2026, 1, 5, 9, 0, tzinfo=ZONE), datetime(2026, 1, 5, 9, 20, tzinfo=ZONE), day_start
    ) == (36, 37)


@pytest.mark.parametrize(
    "left, right, expected",
    [
        ({"participation_rate_absolute": 0.1, "participant_minutes_absolute": 5.0},
         {"participation_rate_absolute": 0.2, "participant_minutes_absolute": 6.0}, "left"),
        ({"participation_rate_absolute": 0.3, "participant_minutes_absolute": 7.0},
         {"participation_rate_absolute": 0.2, "participant_minutes_absolute": 6.0}, "right"),
        ({"participation_rate_absolute": 0.1, "participant_minutes_absolute": 5.0},
         {"participation_rate_absolute": 0.1, "participant_minutes_absolute": 5.0}, "tie"),
        ({"participation_rate_absolute": 0.1, "participant_minutes_absolute": 9.0},
         {"participation_rate_absolute": 0.2, "participant_minutes_absolute": 5.0}, "mixed"),
    ],
)
def test_cohort_alignment_directions(left: dict, right: dict, expected: str) -> None:
    assert aggregate_module._cohort_alignment(left, right) == expected


def test_pair_alignment_requires_both_supported_domains() -> None:
    unsupported = {"status": "unsupported"}
    assert aggregate_module._pair_alignment(unsupported, {"status": "ok"}) == {
        "status": "not_applicable", "result": None,
    }
    deviation = lambda rate, minutes: {
        "urban": {"participation_rate_absolute": rate, "participant_minutes_absolute": minutes},
        "working_age": {"participation_rate_absolute": rate, "participant_minutes_absolute": minutes},
    }
    left = {"status": "ok", "reference_deviation": deviation(0.1, 5.0)}
    right = {"status": "ok", "reference_deviation": deviation(0.2, 6.0)}
    assert aggregate_module._pair_alignment(left, right)["result"] == "left"
    assert aggregate_module._pair_alignment(right, left)["result"] == "right"
    assert aggregate_module._pair_alignment(left, left)["result"] == "tie"
    mixed = {"status": "ok", "reference_deviation": deviation(0.1, 9.0)}
    crossed = {"status": "ok", "reference_deviation": deviation(0.2, 5.0)}
    assert aggregate_module._pair_alignment(mixed, crossed)["result"] == "mixed"


# ---------------------------------------------------------------------------
# reporting.py：图表生成与中文声明边界
# ---------------------------------------------------------------------------


def test_chinese_font_missing_stops_chart_generation(monkeypatch) -> None:
    monkeypatch.setattr(reporting_module, "_FONT_PATHS", (Path("/nonexistent/font.ttc"),))
    with pytest.raises(EvaluationError, match="Chinese chart font is unavailable"):
        reporting_module._chinese_font()


def test_official_chart_renders_placeholder_without_supported_domains(tmp_path: Path) -> None:
    from matplotlib.font_manager import FontProperties

    official = {"domains": {"internet_use": {
        "left": {"status": "unsupported"}, "right": {"status": "unsupported"},
    }}}
    reporting_module._official_chart(tmp_path / "empty.png", official, FontProperties())
    assert (tmp_path / "empty.png").is_file()


def test_save_figure_failure_raises_chart_failed(tmp_path: Path) -> None:
    import matplotlib.pyplot as plt

    figure = plt.figure()
    with pytest.raises(EvaluationError, match="evaluation chart generation failed"):
        reporting_module._save_figure(figure, Path("/nonexistent-dir/chart.png"))


def test_claim_text_covers_all_result_statuses() -> None:
    assert "不能声称" in reporting_module._claim_text({"status": "comparative_plausibility_only"})
    assert "真人参照" in reporting_module._claim_text({"status": "human_reference_comparison"})
    assert "共同观察范围不足" in reporting_module._claim_text({"status": "not_comparable"})
# ---------------------------------------------------------------------------
# scenario.py：评估场景严格读取的全部拒绝分支（`docs/design/01-architecture.md`「真实性评估与结论边界」的声明窗口合同）
# ---------------------------------------------------------------------------

FORMAL_SCENARIO = Path(__file__).resolve().parents[3] / 'tests/evaluation' / "fixtures/scenario.json"


def _load_mutated_scenario(tmp_path: Path, mutate: Callable[[dict], None]) -> None:
    scenario = json.loads(FORMAL_SCENARIO.read_text(encoding="utf-8"))
    mutate(scenario)
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(scenario, ensure_ascii=False), encoding="utf-8")
    load_scenario(path)


@pytest.mark.parametrize(
    "mutate, match",
    [
        (lambda s: s["bootstrap"].update(block_days=8), "block_days must be seven"),
        (lambda s: s["review"].update(reviewer_count=4), "reviewer_count must be three"),
        (lambda s: s["special_dates"].update({"not-a-date": "holiday"}), "special date key is invalid"),
        (lambda s: s["special_dates"].update({"2030-01-01": "holiday"}),
         "special date is outside the declared window"),
        (lambda s: s["special_dates"].update({"2026-01-24": ""}),
         "special_dates values must be non-empty strings"),
        (lambda s: s.update(official_reference={"source": "x"}), "official_reference schema is invalid"),
        (lambda s: s["official_reference"].update(domains=[]), "official_reference schema is invalid"),
        (lambda s: s["official_domain_map"].update(browsing=("ghost_domain",)),
         "references an unknown domain"),
        (lambda s: s["official_reference"]["domains"].update(personal_essential="x"),
         "official domain personal_essential is invalid"),
        (lambda s: s["official_reference"]["domains"]["personal_essential"].update(
            supported_duration_kinds="x"), "duration kinds must be a non-empty string array"),
        (lambda s: s["official_reference"]["domains"]["personal_essential"].update(
            urban={"participant_minutes": 1}), "urban margin is invalid"),
        (lambda s: s["official_reference"]["domains"]["personal_essential"]["urban"].update(
            participant_minutes=-1), "participant_minutes must be non-negative"),
        (lambda s: s["official_reference"]["domains"]["personal_essential"]["urban"].update(
            participation_rate=1.5), "participation_rate must be between zero and one"),
        (lambda s: s["expected_labelkit"].update(extra=1), "expected_labelkit schema is invalid"),
        (lambda s: s["expected_labelkit"].update(run_id=""), "expected LabelKit run_id is invalid"),
        (lambda s: s["expected_labelkit"].update(delivery_digest="xyz"),
         "expected LabelKit SHA-256 is invalid"),
        (lambda s: s["expected_labelkit"].update(main_rows=0), "expected LabelKit main_rows is invalid"),
        (lambda s: s["expected_labelkit"].update(stream_rows=True),
         "expected LabelKit stream_rows is invalid"),
        (lambda s: s.update(window="x"), "window must be an object"),
        (lambda s: s.update(scenario_id=""), "scenario_id must be a non-empty string"),
        (lambda s: s["bootstrap"].update(seed="seven"), "bootstrap fields are invalid"),
        (lambda s: s["bootstrap"].update(repetitions=0), "bootstrap.repetitions must be positive"),
        (lambda s: s["review"].update(dimensions=[]), "review.dimensions must be a non-empty"),
        (lambda s: s["review"].update(dimensions=["a", "a"]),
         "review.dimensions must not contain duplicates"),
        (lambda s: s["special_dates"].update({"2026-01-24": 5}),
         "special_dates values must be non-empty strings"),
    ],
)
def test_load_scenario_rejects_invalid_mutations(
    tmp_path: Path, mutate: Callable[[dict], None], match: str
) -> None:
    with pytest.raises(EvaluationError, match=re.escape(match)):
        _load_mutated_scenario(tmp_path, mutate)


# ---------------------------------------------------------------------------
# validation.py：LabelKit 修复链与共同交付清单的校验分支
# ---------------------------------------------------------------------------


def _mutated_fixture(
    tmp_path: Path,
    *,
    relative: str | None = None,
    rows_mutate: Callable[[list], None] | None = None,
    json_mutate: Callable[[dict], None] | None = None,
    scenario_field: str | None = None,
    refresh_manifest_main: bool = False,
    days: int = 2,
):
    """生成夹具并按行/按对象改写一个 LabelKit 工件，同步刷新冻结哈希。"""
    fixture = make_evaluation_fixture(tmp_path, days=days)
    target = fixture.labelkit_root / relative
    if rows_mutate is not None:
        rows = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
        rows_mutate(rows)
        raw = b"".join(canonical_json(row) + b"\n" for row in rows)
    else:
        value = json.loads(target.read_text(encoding="utf-8"))
        json_mutate(value)
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    target.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    scenario = json.loads(fixture.scenario_path.read_text(encoding="utf-8"))
    if relative in (REPAIRED_STREAM, REPAIRED_MAIN):
        repair_path = fixture.labelkit_root / REPAIR_REPORT
        repair = json.loads(repair_path.read_text(encoding="utf-8"))
        key = "stream_sha256" if relative == REPAIRED_STREAM else "main_sha256"
        repair["repaired"][key] = digest
        repair_raw = json.dumps(repair, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
        repair_path.write_bytes(repair_raw)
        scenario["expected_labelkit"]["repair_report_sha256"] = hashlib.sha256(repair_raw).hexdigest()
    if scenario_field:
        scenario["expected_labelkit"][scenario_field] = digest
    fixture.scenario_path.write_text(json.dumps(scenario, ensure_ascii=False), encoding="utf-8")
    if refresh_manifest_main:
        manifest_path = fixture.labelkit_root / "out/white-collar-12-weeks.manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["main"]["sha256"] = digest
        manifest["main"]["rows"] = raw.count(b"\n")
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return fixture


REPAIRED_STREAM = "repaired/white-collar-12-weeks.stream.jsonl"
REPAIRED_MAIN = "repaired/white-collar-12-weeks.jsonl"
REPAIR_REPORT = "repaired/white-collar-12-weeks.repair-report.json"
FINAL_AUDIT = "repaired/white-collar-12-weeks.final-audit.json"
OUT_MANIFEST = "out/white-collar-12-weeks.manifest.json"
OUT_MAIN = "out/white-collar-12-weeks.jsonl"


def _labelkit_mutations() -> list[tuple[str, Callable[[Path], None], str, str]]:
    """返回 (定位, 工件改写器, 期望错误信息, 场景哈希字段) 的用例表。"""
    return [
        ("stream-duplicate-event", lambda rows: rows[1]["_meta"]["event"].update(
            event_id=rows[0]["_meta"]["event"]["event_id"]),
         "LabelKit event IDs must be unique", "repaired_stream_sha256"),
        ("stream-ghost-owner", lambda rows: rows[1]["_meta"]["event"].update(
            owner_sequence_id="ghost-sequence"), "LabelKit event owner does not exist",
         "repaired_stream_sha256"),
        ("stream-system-actor", lambda rows: rows[1]["_meta"]["event"].update(actor="system"),
         "LabelKit event actor must be user", "repaired_stream_sha256"),
        ("stream-unknown-data-name", lambda rows: rows[1]["payload"].update(dataName="telepathyEvent"),
         "unsupported LabelKit dataName", "repaired_stream_sha256"),
        ("stream-outside-window",
         lambda rows: rows[-1]["_meta"]["event"].update(timestamp="2027-01-01T09:00:00+08:00")
         or rows[-1]["payload"].update(
             startTime=int(datetime(2027, 1, 1, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp() * 1000)),
         "event occurs outside the declared window", "repaired_stream_sha256"),
        ("stream-ends-outside-window",
         lambda rows: rows[-1]["payload"].update(duration=900_000_000),
         "event ends outside the declared window", "repaired_stream_sha256"),
        ("stream-day-not-covered",
         lambda rows: rows[1]["_meta"]["event"].update(
             timestamp=rows[0]["_meta"]["event"]["timestamp"])
         or rows[1]["payload"].update(startTime=rows[0]["payload"]["startTime"]),
         "do not cover the declared window", "repaired_stream_sha256"),
        ("main-duplicate-sequence", lambda rows: rows[1]["_meta"].update(id=rows[0]["_meta"]["id"]),
         "LabelKit sequence IDs must be unique", "repaired_main_sha256"),
        ("main-member-ids-not-strings", lambda rows: rows[0]["_meta"]["stream"].update(member_ids=[7]),
         "member_ids must be non-empty strings", "repaired_main_sha256"),
        ("main-member-count-mismatch",
         lambda rows: rows[0]["_meta"]["stream"].update(member_count=2),
         "member count is invalid", "repaired_main_sha256"),
        ("main-members-mismatch",
         lambda rows: rows[0]["_meta"]["stream"].update(members=[{"index": 0, "id": "ghost", "label": "app"}]),
         "members do not match member_ids", "repaired_main_sha256"),
        ("stream-owner-order-mismatch",
         lambda rows: rows[1]["_meta"]["event"].update(
             owner_sequence_id=rows[0]["_meta"]["event"]["owner_sequence_id"]),
         "owner event order does not match", "repaired_stream_sha256"),
    ]


@pytest.mark.parametrize(
    "name, mutate_rows, match, field",
    [(name, mutate, match, field) for name, mutate, match, field in _labelkit_mutations()],
)
def test_adapt_labelkit_rejects_broken_repaired_chains(
    tmp_path: Path, name: str, mutate_rows: Callable[[list], None], match: str, field: str
) -> None:
    relative = REPAIRED_STREAM if field == "repaired_stream_sha256" else REPAIRED_MAIN
    fixture = _mutated_fixture(
        tmp_path, relative=relative, rows_mutate=mutate_rows, scenario_field=field
    )
    scenario = load_scenario(fixture.scenario_path)
    with pytest.raises(EvaluationError, match=re.escape(match)):
        adapt_labelkit(fixture.labelkit_root, scenario)


def test_labelkit_public_action_outside_window_is_rejected(tmp_path: Path) -> None:
    def _add_late_action(rows: list) -> None:
        rows[0]["_meta"]["generation"]["sequence_class"] = "food_order"
        rows[0]["actionInfo"] = {"timestamp": 1_799_280_000_000, "fulfillmentMode": "delivery"}

    fixture = _mutated_fixture(
        tmp_path, relative=REPAIRED_MAIN, rows_mutate=_add_late_action,
        scenario_field="repaired_main_sha256",
    )
    scenario = load_scenario(fixture.scenario_path)
    with pytest.raises(EvaluationError, match="public evidence occurs outside the declared window"):
        adapt_labelkit(fixture.labelkit_root, scenario)


@pytest.mark.parametrize(
    "mutate, match",
    [
        (lambda m: m.update(artifacts_committed=False), "artifacts are not committed"),
        (lambda m: m.update(schema_version=2), "schema version must be 1"),
        (lambda m: m.update(main="x"), "manifest main descriptor is missing"),
        (lambda m: m["main"].update(path=7), "artifact descriptor is invalid"),
        (lambda m: m["main"].update(path="/etc/passwd"), "artifact path escapes the delivery root"),
        (lambda m: m.update(run_id="ghost-run"),
         "LabelKit run ID does not match the evaluation scenario"),
    ],
)
def test_labelkit_manifest_frozen_fields_cannot_be_relaxed(
    tmp_path: Path, mutate: Callable[[dict], None], match: str
) -> None:
    fixture = _mutated_fixture(tmp_path, relative=OUT_MANIFEST, json_mutate=mutate)
    scenario = load_scenario(fixture.scenario_path)
    with pytest.raises(EvaluationError, match=re.escape(match)):
        validate_labelkit_delivery(fixture.labelkit_root, scenario)


def test_labelkit_raw_main_hash_must_match_scenario(tmp_path: Path) -> None:
    def _pad(rows: list) -> None:
        rows.append({"bundleName": "com.example.browser", "_meta": {"id": "extra", "generation": {},
                                                                    "stream": {}}})

    fixture = _mutated_fixture(
        tmp_path, relative=OUT_MAIN, rows_mutate=_pad, refresh_manifest_main=True
    )
    scenario = load_scenario(fixture.scenario_path)
    with pytest.raises(EvaluationError, match="raw main hash does not match the scenario"):
        validate_labelkit_delivery(fixture.labelkit_root, scenario)


@pytest.mark.parametrize(
    "relative, field, mutate, match",
    [
        (REPAIR_REPORT, "repair_report_sha256", lambda v: v.pop("source"),
         "missing hash sections"),
        (REPAIR_REPORT, "repair_report_sha256", lambda v: v["source"].update(main_sha256="0" * 64),
         "hash chain is invalid"),
        (REPAIR_REPORT, "repair_report_sha256", lambda v: v.update(frames=v["frames"] + 1),
         "frame count is invalid"),
        (FINAL_AUDIT, "final_audit_sha256", lambda v: v.update(findings=[{"code": "x"}]),
         "final audit contains findings"),
        (FINAL_AUDIT, "final_audit_sha256", lambda v: v.pop("summary"), "summary is incomplete"),
        (FINAL_AUDIT, "final_audit_sha256",
         lambda v: v["summary"]["delivery"].update(run_id="ghost"), "run ID does not match manifest"),
        (FINAL_AUDIT, "final_audit_sha256",
         lambda v: v["summary"]["delivery"].update(delivery_digest="ghost"),
         "digest does not match manifest"),
        (FINAL_AUDIT, "final_audit_sha256",
         lambda v: v["summary"]["rows"].update(main_rows=v["summary"]["rows"]["main_rows"] + 1),
         "row counts do not match"),
    ],
)
def test_labelkit_repair_and_audit_chain_is_frozen(
    tmp_path: Path, relative: str, field: str, mutate: Callable[[dict], None], match: str
) -> None:
    fixture = _mutated_fixture(
        tmp_path, relative=relative, json_mutate=mutate, scenario_field=field
    )
    scenario = load_scenario(fixture.scenario_path)
    with pytest.raises(EvaluationError, match=re.escape(match)):
        validate_labelkit_delivery(fixture.labelkit_root, scenario)


# --- WorldMock/共同交付清单 ---


def _canonical_case(tmp_path: Path, mutate_manifest: Callable[[dict], None]) -> tuple:
    fixture = make_evaluation_fixture(tmp_path)
    manifest_path = fixture.worldmock_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mutate_manifest(manifest)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return fixture, manifest


def _rehash_checkpoint(manifest: dict) -> None:
    checkpoint = Checkpoint.model_validate(manifest["checkpoint"])
    payload = checkpoint.model_dump(exclude={"record_sha256"})
    manifest["checkpoint"]["record_sha256"] = hashlib.sha256(canonical_json(payload)).hexdigest()


@pytest.mark.parametrize(
    "mutate, match",
    [
        (lambda m: m.update(committed=False), "manifest is not committed"),
        (lambda m: m.update(schema_version=1), "schema_version must be 6"),
        (lambda m: m.update(schema_version=5), "schema_version must be 6"),
        (lambda m: m["artifacts"].pop("location_stays"), "delivery artifact set is invalid"),
        (lambda m: m.update(experiment_completed=False), "experiment_completed"),
        (lambda m: m.update(checkpoint="x"), "checkpoint is missing"),
        (lambda m: m.update(asset_index="x"), "asset_index is missing"),
        (lambda m: m.update(run_id=""), "run_id is missing"),
        (lambda m: m.update(run_id="ghost-run"), "run_id must be declared in source_run_ids"),
        (lambda m: m["artifacts"].update(extra={}), "delivery artifact set is invalid"),
        (lambda m: m.update(parent_run_id="ghost"), "parent_run_id must be declared"),
        (lambda m: m.update(parent_run_id=5), "parent_run_id must be a non-empty string or null"),
        (lambda m: m["window"].update(start="2026-01-04T00:00:00+08:00"),
         "delivery window does not match scenario"),
        (lambda m: m["artifacts"].update(events="x"), "events artifact descriptor is invalid"),
        (lambda m: m["artifacts"]["events"].update(path=str(Path(Path.cwd().anchor) / "abs/path.json")),
         "path must be relative"),
        (lambda m: m.update(source_run_ids="x"), "source_run_ids must be a string array"),
        (lambda m: m.update(source_run_ids=["world-run", "world-run"]),
         "source_run_ids must not contain duplicates"),
        (lambda m: m.update(window="x"), "delivery window is missing"),
        (lambda m: m["checkpoint"].update(run_id=5), "checkpoint schema is invalid"),
        (lambda m: (m.pop("frozen_run_manifest"), _rehash_checkpoint(m)),
         "frozen run manifest is missing"),
        (lambda m: (m["frozen_run_manifest"].update(source_group_id="ghost"), _rehash_checkpoint(m)),
         "manifest hash is invalid"),
        (lambda m: m["asset_index"].update(path="other-index.jsonl"), "asset_index descriptor is invalid"),
        (lambda m: (m["checkpoint"].update(snapshots={}), _rehash_checkpoint(m)),
         "checkpoint snapshots are missing"),
    ],
)
def test_canonical_manifest_contract_enforcement(
    tmp_path: Path, mutate: Callable[[dict], None], match: str
) -> None:
    fixture, _ = _canonical_case(tmp_path, mutate)
    scenario = load_scenario(fixture.scenario_path)
    with pytest.raises(EvaluationError, match=re.escape(match)):
        validate_canonical_delivery(fixture.worldmock_root, scenario, "worldmock")


def test_canonical_asset_index_rejects_duplicate_identities(tmp_path: Path) -> None:
    fixture = make_evaluation_fixture(tmp_path)
    index_path = fixture.worldmock_root / "asset-index.jsonl"
    rows = [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines()]
    rows.append(dict(rows[0]))
    raw = b"".join(canonical_json(row) + b"\n" for row in rows)
    index_path.write_bytes(raw)
    manifest_path = fixture.worldmock_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["asset_index"]["sha256"] = hashlib.sha256(raw).hexdigest()
    manifest["asset_index"]["rows"] = len(rows)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    scenario = load_scenario(fixture.scenario_path)
    with pytest.raises(EvaluationError, match="contains duplicate identities"):
        validate_canonical_delivery(fixture.worldmock_root, scenario, "worldmock")


def test_canonical_snapshot_must_match_asset_index_and_content(tmp_path: Path) -> None:
    fixture, _ = _canonical_case(tmp_path, lambda m: None)
    scenario = load_scenario(fixture.scenario_path)

    def _run(expect: str) -> None:
        with pytest.raises(EvaluationError, match=expect):
            validate_canonical_delivery(fixture.worldmock_root, scenario, "worldmock")

    manifest_path = fixture.worldmock_root / "manifest.json"
    pristine = manifest_path.read_text(encoding="utf-8")
    manifest = json.loads(pristine)
    snapshot = next(iter(manifest["checkpoint"]["snapshots"].values()))
    snapshot.update(sha256="0" * 64, size_bytes=1)
    _rehash_checkpoint(manifest)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    _run("checkpoint snapshot is absent from asset_index")

    manifest_path.write_text(pristine, encoding="utf-8")
    manifest = json.loads(pristine)
    reference = next(iter(manifest["checkpoint"]["snapshots"].values()))
    digest = reference["sha256"]
    (fixture.worldmock_root / "assets" / digest).write_bytes(b"tampered bytes")
    _run("checkpoint snapshot content is invalid")


# ---------------------------------------------------------------------------
# adapters.py：共同适配的守恒、通道与身份分支
# ---------------------------------------------------------------------------


def _delivery_stub(scenario, run_id: str = "world-run", rows: int = 1):
    from worldmock.evaluation.validation import VerifiedDelivery

    identity = io_module.ArtifactIdentity(Path("events.jsonl"), rows, "0" * 64, 0)
    return VerifiedDelivery(
        run_id=run_id, dataset_id="world-dataset", window=scenario.window,
        event_path=Path("events.jsonl"), activity_path=Path("activities.jsonl"),
        artifacts={"events": identity}, evidence={"complete_export_authenticated": False},
        accepted_run_ids=(run_id,),
    )


def _event_row(sequence: int, *, run_id: str = "public-test-run", event_id: str | None = None,
               occurred: datetime | None = None, family: str = "observation") -> dict[str, Any]:
    from worldmock.footprints.models import Event

    occurred = occurred or datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)
    event = Event(
        run_id=run_id, event_id=event_id or f"event-{sequence}", event_seq=sequence,
        event_type=family, occurred_at=int(occurred.timestamp()), agent_id=1, data={},
        recorded_at="2026-01-05T00:00:00+00:00", wall_elapsed_ms=0,
        state_version_before=sequence - 1, state_version_after=sequence,
    )
    return event.model_dump(mode="json")


def _evidence_event(event_id: str, *, family: str = "activity_progress", channel: str = "activity",
                    activity_id: str | None = None, caused_by: tuple[str, ...] = (),
                    checkpoint_id: str | None = None,
                    occurred: datetime | None = None) -> EvidenceEvent:
    occurred = occurred or datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)
    return EvidenceEvent(
        "world-dataset", event_id, "user", occurred, None, family, channel, "browsing",
        activity_id, None, None, caused_by, None, None, {}, None, "events.jsonl:1", "activity",
        "agent", "environment", checkpoint_id,
    )


def test_world_event_identity_and_schema_rejections(tmp_path: Path) -> None:
    import worldmock.evaluation.adapters as adapters

    scenario = load_scenario(make_evaluation_fixture(tmp_path).scenario_path)
    delivery = _delivery_stub(scenario)
    with pytest.raises(EvaluationError, match="does not match Event schema"):
        adapters.parse_world_event({"event_id": "broken"})
    event = adapters.parse_world_event(_event_row(1))
    rogue = _delivery_stub(scenario, run_id="other-run")
    with pytest.raises(EvaluationError, match="run_id is not declared"):
        adapters._require_world_event_identity(event, 1, rogue)
    sequenced = adapters.parse_world_event(_event_row(5, run_id="world-run"))
    with pytest.raises(EvaluationError, match="event_seq must be contiguous"):
        adapters._require_world_event_identity(sequenced, 1, delivery)


def test_world_common_channel_masks_private_and_unmatched_events(tmp_path: Path) -> None:
    import worldmock.evaluation.adapters as adapters

    scenario = load_scenario(make_evaluation_fixture(tmp_path).scenario_path)
    base = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)
    digital = _session(channels=("digital_interaction",))
    physical = _session(channels=("physical_activity",))

    def _model(family: str, *, visibility: str = "agent", source_kind: str = "environment"):
        from worldmock.footprints.models import Event

        return Event(
            run_id="r", event_id="e", event_seq=1, event_type=family,
            occurred_at=int(base.timestamp()), agent_id=1, data={},
            recorded_at="2026-01-05T00:00:00+00:00", wall_elapsed_ms=0, visibility=visibility,
            source_kind=source_kind, state_version_before=0, state_version_after=1,
        )

    assert adapters._world_common_channel(_model("model_request"), None) == "private_model"
    assert adapters._world_common_channel(_model("observation", source_kind="model"), None) == "private_model"
    assert adapters._world_common_channel(_model("checkpoint_created", visibility="system"), None) == "private_system"
    assert adapters._world_common_channel(_model("activity_progress"), digital) == "digital_interaction"
    assert adapters._world_common_channel(_model("activity_progress"), physical) == "physical_activity"
    assert adapters._world_common_channel(_model("checkpoint_created"), None) == "lifecycle"
    assert adapters._world_common_channel(_model("observation"), None) == "unmatched_visible"


def test_resource_changes_must_be_numeric_object() -> None:
    import worldmock.evaluation.adapters as adapters

    assert adapters._resource_changes({"resource_changes": {"attention": -1.5}}) == {"attention": -1.5}
    with pytest.raises(EvaluationError, match="resource_changes must be an object"):
        adapters._resource_changes({"resource_changes": "x"})
    with pytest.raises(EvaluationError, match="resource changes must be numeric"):
        adapters._resource_changes({"resource_changes": {"attention": "high"}})


def test_adapt_world_events_enforces_identity_order_and_window(tmp_path: Path) -> None:
    import worldmock.evaluation.adapters as adapters

    scenario = load_scenario(make_evaluation_fixture(tmp_path).scenario_path)
    from worldmock.evaluation.validation import VerifiedDelivery

    identity = io_module.ArtifactIdentity(Path("events.jsonl"), 99, "0" * 64, 0)
    delivery = VerifiedDelivery(
        run_id="world-run", dataset_id="world-dataset", window=scenario.window,
        event_path=Path("events.jsonl"), activity_path=Path("activities.jsonl"),
        artifacts={"events": identity}, evidence={}, accepted_run_ids=("ancestor-run", "world-run"),
    )
    with pytest.raises(EvaluationError, match="run_id is not declared"):
        adapters._adapt_world_events([parse_world_event(row) for row in [_event_row(1, run_id="rogue-run")]], delivery, scenario, {})
    prefix_break = [
        _event_row(1, run_id="ancestor-run"),
        _event_row(2, run_id="world-run"),
        _event_row(3, run_id="ancestor-run"),
    ]
    with pytest.raises(EvaluationError, match="ancestor events must form a prefix"):
        adapters._adapt_world_events([parse_world_event(row) for row in prefix_break], delivery, scenario, {})
    duplicate = [_event_row(1, run_id="world-run"), _event_row(2, run_id="world-run",
                                                                event_id="event-1")]
    with pytest.raises(EvaluationError, match="event IDs must be unique"):
        adapters._adapt_world_events([parse_world_event(row) for row in duplicate], _delivery_stub(scenario), scenario, {})
    outside = _event_row(1, run_id="world-run", occurred=datetime(2026, 6, 1, tzinfo=timezone.utc))
    with pytest.raises(EvaluationError, match="event occurs outside the declared window"):
        adapters._adapt_world_events([parse_world_event(row) for row in [outside]], _delivery_stub(scenario), scenario, {})
    late_end = _event_row(1, run_id="world-run")
    late_end["data"]["ended_at"] = int(datetime(2027, 1, 1, tzinfo=timezone.utc).timestamp())
    with pytest.raises(EvaluationError, match="event ends outside the declared window"):
        adapters._adapt_world_events([parse_world_event(row) for row in [late_end]], _delivery_stub(scenario), scenario, {})


def test_world_activity_rows_are_strictly_validated(tmp_path: Path) -> None:
    import worldmock.evaluation.adapters as adapters
    from tests.evaluation.conftest import _world_activity

    scenario = load_scenario(make_evaluation_fixture(tmp_path).scenario_path)
    delivery = _delivery_stub(scenario)
    start = scenario.window.start + timedelta(hours=9)
    base, _ = _world_activity("world-run", 1, start)
    assert adapters._adapt_world_activity(base, 1, delivery, scenario).action == "interact"

    missing = dict(base)
    missing.pop("status")
    with pytest.raises(EvaluationError, match="is missing fields"):
        adapters._adapt_world_activity(missing, 1, delivery, scenario)

    not_array = dict(base, segments="x")
    with pytest.raises(EvaluationError, match="segments must be an array"):
        adapters._adapt_world_activity(not_array, 1, delivery, scenario)

    overlap = dict(base, segments=[
        {"started_at": base["segments"][0]["started_at"], "ended_at": base["segments"][0]["ended_at"]},
        {"started_at": base["segments"][0]["started_at"] + 1800,
         "ended_at": base["segments"][0]["ended_at"] + 1800},
    ])
    with pytest.raises(EvaluationError, match="segments overlap or precede start"):
        adapters._adapt_world_activity(overlap, 1, delivery, scenario)

    elapsed = dict(base, elapsed_seconds=base["elapsed_seconds"] + 1)
    with pytest.raises(EvaluationError, match="do not match elapsed_seconds"):
        adapters._adapt_world_activity(elapsed, 1, delivery, scenario)

    zero = dict(base, segments=[{"started_at": base["segments"][0]["started_at"],
                                 "ended_at": base["segments"][0]["started_at"]}],
                elapsed_seconds=0)
    with pytest.raises(EvaluationError, match="positive duration"):
        adapters._adapt_world_activity(zero, 1, delivery, scenario)

    no_end = dict(base, ended_at=None)
    with pytest.raises(EvaluationError, match="terminal WorldMock activity must declare ended_at"):
        adapters._adapt_world_activity(no_end, 1, delivery, scenario)

    early = dict(base, status="active",
                 ended_at=base["segments"][0]["ended_at"] - 1800)
    with pytest.raises(EvaluationError, match="end precedes actual segments"):
        adapters._adapt_world_activity(early, 1, delivery, scenario)

    planned = dict(base, planned_end_at=base["started_at"])
    with pytest.raises(EvaluationError, match="planned_end_at must follow started_at"):
        adapters._adapt_world_activity(planned, 1, delivery, scenario)

    late = int((scenario.window.end + timedelta(days=1)).timestamp())
    outside = dict(base, started_at=late, ended_at=late + 7_200,
                   planned_end_at=late + 7_200, elapsed_seconds=7_200,
                   segments=[{"started_at": late, "ended_at": late + 7_200}])
    with pytest.raises(EvaluationError, match="activity is outside the declared window"):
        adapters._adapt_world_activity(outside, 1, delivery, scenario)

    duplicate_rows = [base, dict(base)]
    with pytest.raises(EvaluationError, match="activity IDs must be unique"):
        adapters._adapt_world_activities(duplicate_rows, delivery, scenario)


def test_world_links_and_completion_guards(tmp_path: Path) -> None:
    import worldmock.evaluation.adapters as adapters

    scenario = load_scenario(make_evaluation_fixture(tmp_path).scenario_path)
    delivery = _delivery_stub(scenario)
    event = _evidence_event("e1", activity_id="a1")
    session = replace(_session(), activity_id="a1", caused_by=("e1",))
    adapters._validate_world_links([event], [session], delivery)

    dangling = replace(event, caused_by=("ghost",))
    with pytest.raises(EvaluationError, match="caused_by references a missing event"):
        adapters._validate_world_links([dangling], [session], delivery)
    missing_activity = replace(event, activity_id="ghost")
    with pytest.raises(EvaluationError, match="event references a missing activity"):
        adapters._validate_world_links([missing_activity], [session], delivery)
    orphan_source = replace(session, caused_by=("ghost",))
    with pytest.raises(EvaluationError, match="activity source event is missing"):
        adapters._validate_world_links([event], [orphan_source], delivery)
    foreign_source = replace(session, caused_by=("e1",), activity_id="other")
    with pytest.raises(EvaluationError, match="source event identity differs"):
        adapters._validate_world_links([event], [session, foreign_source], delivery)
    short = _delivery_stub(scenario, rows=99)
    with pytest.raises(EvaluationError, match="did not preserve every row"):
        adapters._validate_world_links([event], [session], short)

    end = scenario.window.end
    running = _evidence_event("end-1", family="run_ended", channel="lifecycle",
                              checkpoint_id=None, occurred=end)
    checkpoint = _evidence_event("cp-1", family="checkpoint_created", channel="lifecycle",
                                 checkpoint_id="cp", occurred=end)
    delivered = _delivery_stub(scenario)
    delivered.evidence.update(checkpoint_id="cp")
    adapters._validate_world_completion([running, checkpoint], scenario, delivered)
    with pytest.raises(EvaluationError, match="run_ended must occur once"):
        adapters._validate_world_completion([checkpoint], scenario, delivered)
    with pytest.raises(EvaluationError, match="completion checkpoint does not match"):
        adapters._validate_world_completion([running, replace(checkpoint, checkpoint_id=None)],
                                            scenario, delivered)
    with pytest.raises(EvaluationError, match="checkpoint must follow run_ended"):
        adapters._validate_world_completion([checkpoint, running], scenario, delivered)
    failed = _evidence_event("boom", family="run_failed", channel="lifecycle", occurred=end)
    with pytest.raises(EvaluationError, match="contains run_failed"):
        adapters._validate_world_completion([running, failed, checkpoint], scenario, delivered)


def test_adapter_value_helpers_reject_invalid_inputs() -> None:
    import worldmock.evaluation.adapters as adapters

    assert adapters._mapping({"a": 1}, "f") == {"a": 1}
    with pytest.raises(EvaluationError, match="must be an object"):
        adapters._mapping(5, "f")
    with pytest.raises(EvaluationError, match="must be a non-empty string"):
        adapters._text("", "f")
    with pytest.raises(EvaluationError, match="optional text field"):
        adapters._optional_text("")
    assert adapters._optional_text(None) is None
    with pytest.raises(EvaluationError, match="must be a string array"):
        adapters._string_list([1], "f")
    with pytest.raises(EvaluationError, match="must not contain duplicates"):
        adapters._string_list(["a", "a"], "f")
    assert adapters._person_id(None) == "system"
    with pytest.raises(EvaluationError, match="agent_id must be"):
        adapters._person_id(1.5)
    assert adapters._integer_or_none(None, "f") is None
    assert adapters._integer_or_none(3, "f") == 3
    with pytest.raises(EvaluationError, match="must be a non-negative integer"):
        adapters._integer_or_none(-1, "f")
    with pytest.raises(EvaluationError, match="must be a non-negative number"):
        adapters._nonnegative_number(-1, "f")
    with pytest.raises(EvaluationError, match="must be integer epoch milliseconds"):
        adapters._milliseconds_time("soon", "f")
    with pytest.raises(EvaluationError, match="outside supported range"):
        adapters._milliseconds_time(10**19, "f")
    with pytest.raises(EvaluationError, match="must be a non-negative integer"):
        adapters._nonnegative_int(-1, "f")
    with pytest.raises(EvaluationError, match="payload time does not match"):
        adapters._validate_labelkit_payload_time(
            {"startTime": 1_767_600_000_777}, "appUsageEvent",
            datetime.fromtimestamp(1_767_600_000, timezone.utc), 1,
        )
    start = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)
    assert adapters._labelkit_event_end({"duration": 1000}, "appUsageEvent", start, 1) == (
        start + timedelta(seconds=1)
    )
    assert adapters._labelkit_event_end(
        {"updateTime": 1_767_600_000_000}, "screenMemoryEvent", start, 1
    ) == datetime.fromtimestamp(1_767_600_000, timezone.utc)
    assert adapters._labelkit_event_end(
        {"endTime": 1_767_600_060_000}, "poiEvent", start, 1
    ) == datetime.fromtimestamp(1_767_600_060, timezone.utc)
    assert adapters._labelkit_event_end({}, "pasteBoardEvent", start, 1) == start
    assert adapters._activity_overlaps(
        replace(_session(), started_at=start, ended_at=start + timedelta(hours=1)),
        start, start + timedelta(hours=1), (),
    )
# ---------------------------------------------------------------------------
# validation.py：检查点完成时间必须落在评估窗口右端（直接路径）
# ---------------------------------------------------------------------------


def test_checkpoint_time_must_match_evaluation_window(tmp_path: Path) -> None:
    import worldmock.evaluation.validation as validation

    fixture = make_evaluation_fixture(tmp_path)
    manifest = json.loads((fixture.worldmock_root / "manifest.json").read_text(encoding="utf-8"))
    scenario = load_scenario(fixture.scenario_path)
    shifted = TimeWindow(
        scenario.window.start - timedelta(days=1),
        scenario.window.end - timedelta(days=1),
        scenario.window.timezone,
    )
    with pytest.raises(EvaluationError, match="not at the declared window end"):
        validation._validated_checkpoint(manifest, shifted)


# ---------------------------------------------------------------------------
# review.py：盲评包校验、收据校验、解盲统计（`docs/design/01-architecture.md`「真实性评估与结论边界」的盲评合同）
# ---------------------------------------------------------------------------


def _prepare_review(tmp_path: Path, days: int = 1):
    fixture = make_evaluation_fixture(tmp_path, days=days)
    scenario = load_scenario(fixture.scenario_path)
    left = adapt_labelkit(fixture.labelkit_root, scenario)
    right = adapt_worldmock(fixture.worldmock_root, scenario)
    output = tmp_path / "prepared"
    write_review_material(output, create_review_material(left, right, shared_support(left, right), scenario))
    records = make_review_records(output, tmp_path / "reviews", candidate_pattern=True)
    return scenario, output, records


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _resync_packet_hashes(output: Path, records: list[Path]) -> None:
    index = _read_json(output / "blind-review-pack.json")
    for position, descriptor in enumerate(index["packets"]):
        packet_path = output / "review-packets" / f"{descriptor['reviewer_slot']}.json"
        digest = hashlib.sha256(packet_path.read_bytes()).hexdigest()
        descriptor["sha256"] = digest
        record = _read_json(records[position])
        record["packet_sha256"] = digest
        _write_json(records[position], record)
    _write_json(output / "blind-review-pack.json", index)


def _reject_ingest(scenario, output: Path, records: list[Path], match: str) -> None:
    with pytest.raises(EvaluationError, match=re.escape(match)):
        ingest_reviews(records, output, scenario)


@pytest.mark.parametrize(
    "mutate, match",
    [
        (lambda i: i.update(schema_version="x"), "blind review index schema is invalid"),
        (lambda i: i.update(dimensions=[]), "review dimensions do not match scenario"),
        (lambda i: i.update(shared_profile={}), "review shared profile does not match scenario"),
        (lambda i: i.update(public_fields_by_object_type=[]), "public field support must be an object"),
        (lambda i: i.update(public_fields_by_object_type={"c": "x"}),
         "public field support contains invalid fields"),
        (lambda i: i.update(public_fields_by_object_type={"c": ["interaction", "bogus"]}),
         "public field support contains invalid fields"),
        (lambda i: i.update(public_fields_by_object_type={"c": ["interaction", "interaction"]}),
         "public field support contains invalid fields"),
        (lambda i: i.update(selection_coverage={}), "selection coverage does not match scenario"),
        (lambda i: i.update(packets=i["packets"][:2]), "review packet count does not match scenario"),
        (lambda i: i["packets"][0].update(reviewer_slot="rogue"), "review packet slot is invalid"),
        (lambda i: i["packets"][0].update(reviewer_slot="reviewer-/../../../outside"), "review packet slot is invalid"),
        (lambda i: i["packets"][0].update(reviewer_slot="reviewer-\\..\\..\\..\\outside"),
         "review packet slot is invalid"),
        (lambda i: i["packets"][0].update(reviewer_slot="reviewer-01/../reviewer-02"), "review packet slot is invalid"),
        (lambda i: i["packets"][0].update(reviewer_slot="reviewer-1"), "review packet slot is invalid"),
    ],
)
def test_ingest_rejects_tampered_review_index(
    tmp_path: Path, mutate: Callable[[dict], None], match: str
) -> None:
    scenario, output, records = _prepare_review(tmp_path)
    index = _read_json(output / "blind-review-pack.json")
    mutate(index)
    _write_json(output / "blind-review-pack.json", index)
    _reject_ingest(scenario, output, records, match)


@pytest.mark.parametrize(
    "mutate, match",
    [
        (lambda k: k.update(schema_version="x"), "unblinding key does not match review pack"),
        (lambda k: k.update(pack_id="0" * 64), "unblinding key does not match review pack"),
        (lambda k: k["reviewer_mappings"].pop("reviewer-01"),
         "unblinding key item coverage is invalid"),
        (lambda k: k["reviewer_mappings"]["reviewer-01"].pop(next(
            iter(k["reviewer_mappings"]["reviewer-01"]))), "unblinding key item coverage is invalid"),
        (lambda k: next(iter(k["reviewer_mappings"]["reviewer-01"].values())).update(left_side="x"),
         "unblinding mapping schema is invalid"),
        (lambda k: next(iter(k["reviewer_mappings"]["reviewer-01"].values())).update(
            right=next(iter(k["reviewer_mappings"]["reviewer-01"].values()))["left"]),
            "unblinding candidate mapping is invalid"),
    ],
)
def test_ingest_rejects_tampered_unblinding_key(
    tmp_path: Path, mutate: Callable[[dict], None], match: str
) -> None:
    scenario, output, records = _prepare_review(tmp_path)
    key = _read_json(output / "unblinding-key.json")
    mutate(key)
    _write_json(output / "unblinding-key.json", key)
    _reject_ingest(scenario, output, records, match)


def _mutate_first_packet(output: Path, records: list[Path], mutate: Callable[[dict], None]) -> None:
    index = _read_json(output / "blind-review-pack.json")
    slot = index["packets"][0]["reviewer_slot"]
    packet_path = output / "review-packets" / f"{slot}.json"
    packet = _read_json(packet_path)
    mutate(packet)
    _write_json(packet_path, packet)
    _resync_packet_hashes(output, records)


@pytest.mark.parametrize(
    "mutate, match",
    [
        (lambda p: p.update(schema_version="x"), "blind review packet schema is invalid"),
        (lambda p: p["items"][0].update(source_pointer="leak"),
         "blind review packet exposes source identity"),
        (lambda p: p["items"][0].update(dataset_id="leak"),
         "blind review packet exposes source identity"),
        (lambda p: p.update(pack_id="0" * 64), "blind review packet identity is invalid"),
        (lambda p: p.update(dimensions=[]), "blind review packet dimensions are invalid"),
        (lambda p: p.update(shared_profile={}), "blind review packet shared profile is invalid"),
        (lambda p: p.update(public_fields_by_object_type={"c": ["interaction"]}),
         "blind review packet public field support is invalid"),
        (lambda p: p.update(items=[]), "blind review packet item count is invalid"),
        (lambda p: p["items"][0].pop("judgeability"), "blind review item schema is invalid"),
        (lambda p: p["items"][0].update(item_id=""), "blind review item ID is invalid"),
        (lambda p: p["items"][1].update(item_id=p["items"][0]["item_id"]),
         "blind review packet item IDs repeat"),
        (lambda p: p["items"][0]["left"].pop("activities"),
         "blind review candidate timeline schema is invalid"),
        (lambda p: p["items"][0]["left"].update(activities="x"),
         "blind review candidate collections must be arrays"),
        (lambda p: p["items"][0]["left"]["activities"][0].pop("location"),
         "blind review activity timeline schema is invalid"),
        (lambda p: p["items"][0]["left"]["activities"][0].update(location=7),
         "blind review activity timeline schema is invalid"),
        (lambda p: p["items"][0]["left"]["activities"][0].update(companion="agent:2"),
         "blind review activity timeline schema is invalid"),
        (lambda p: p["items"][0]["judgeability"]["temporal_plausibility"].update(judgeable=False),
         "judgeability disagrees with evidence"),
        (lambda p: p["items"][0]["judgeability"]["temporal_plausibility"].update(reason="bogus"),
         "blind review judgeability value is invalid"),
        (lambda p: p["items"][0]["judgeability"]["temporal_plausibility"].update(extra=1),
         "blind review judgeability schema is invalid"),
        (lambda p: p["items"][0]["judgeability"].pop("temporal_plausibility"),
         "blind review judgeability dimensions are invalid"),
    ],
)
def test_ingest_rejects_tampered_blind_packets(
    tmp_path: Path, mutate: Callable[[dict], None], match: str
) -> None:
    scenario, output, records = _prepare_review(tmp_path)
    _mutate_first_packet(output, records, mutate)
    _reject_ingest(scenario, output, records, match)


def test_ingest_rejects_invalid_public_evidence_rows(tmp_path: Path) -> None:
    scenario, output, records = _prepare_review(tmp_path)
    fields = {"order": ["interaction", "object_ref"]}

    def _support(packet: dict) -> None:
        packet["public_fields_by_object_type"] = fields
        packet["items"][0]["left"]["public_evidence"] = [
            {"at": "2026-01-05T09:00:00+08:00", "object_type": "order",
             "interaction": "order", "object_ref": "object-" + "0" * 64}
        ]

    index = _read_json(output / "blind-review-pack.json")
    index["public_fields_by_object_type"] = fields
    _write_json(output / "blind-review-pack.json", index)
    for descriptor in index["packets"]:
        packet_path = output / "review-packets" / f"{descriptor['reviewer_slot']}.json"
        packet = _read_json(packet_path)
        _support(packet)
        _write_json(packet_path, packet)
    _resync_packet_hashes(output, records)
    result = ingest_reviews(records, output, scenario)
    assert result["decoded_ratings"], "valid public evidence rows must ingest cleanly"

    def _with_missing_values(packet: dict) -> None:
        packet["items"][0]["left"]["public_evidence"] = [
            {"at": "2026-01-05T09:00:00+08:00", "object_type": "order",
             "interaction": None, "object_ref": None}
        ]

    _mutate_first_packet(output, records, _with_missing_values)
    result = ingest_reviews(records, output, scenario)
    assert result["decoded_ratings"], "missing public values must stay missing, not fabricated"

    def _row(**overrides: Any) -> dict:
        row = {"at": "2026-01-05T09:00:00+08:00", "object_type": "order",
               "interaction": "order", "object_ref": None}
        row.update(overrides)
        return row

    cases = [
        (_row(interaction="explode"), "value is outside the closed set"),
        (_row(object_ref="raw-order"), "object reference is invalid"),
        (_row(at=5), "public evidence time is invalid"),
        ("not-a-dict", "public evidence schema is invalid"),
        (_row(extra="x"), "public evidence fields are invalid"),
    ]
    for row, match in cases:
        def _install(packet: dict, row=row) -> None:
            packet["items"][0]["left"]["public_evidence"] = [row]

        _mutate_first_packet(output, records, _install)
        _reject_ingest(scenario, output, records, match)


@pytest.mark.parametrize(
    "mutate, match",
    [
        (lambda r: r.pop("reviewer_slot"), "review record slots are invalid or duplicated"),
        (lambda r: r.update(reviewer_slot="reviewer-99"), "review record is missing for a reviewer slot"),
        (lambda r: r.update(schema_version="x"), "review record schema is invalid"),
        (lambda r: r.update(pack_id="0" * 64), "review record pack_id is invalid"),
        (lambda r: r.update(packet_sha256="0" * 64), "review record packet hash is invalid"),
        (lambda r: r.update(raw_response="  "), "raw review response must be preserved as text"),
        (lambda r: r.update(reviewer_kind="robot"), "reviewer identity is invalid"),
        (lambda r: r.update(model={"provider": "x"}), "model reviewer metadata is invalid"),
        (lambda r: r.update(model={"provider": "x", "name": ""}), "model reviewer metadata is invalid"),
        (lambda r: r.update(reviewer_kind="human"), "human reviewer must not declare model metadata"),
        (lambda r: r.update(ratings=[]), "review ratings do not cover every item"),
        (lambda r: r["ratings"][0].update(extra=1), "review rating schema is invalid"),
        (lambda r: r["ratings"][0].update(item_id=""), "review item IDs are invalid"),
        (lambda r: r["ratings"][0].update(item_id="ghost-item"),
         "review rating references an unknown item"),
        (lambda r: r["ratings"][0]["dimensions"]["temporal_plausibility"].update(extra=1),
         "review dimension decision schema is invalid"),
        (lambda r: r["ratings"][0]["dimensions"]["temporal_plausibility"].update(choice="maybe"),
         "review choice is outside the closed set"),
        (lambda r: r["ratings"][0]["dimensions"]["temporal_plausibility"].update(rationale="  "),
         "review rationale must be non-empty"),
    ],
)
def test_ingest_rejects_tampered_review_records(
    tmp_path: Path, mutate: Callable[[dict], None], match: str
) -> None:
    scenario, output, records = _prepare_review(tmp_path)
    record = _read_json(records[0])
    mutate(record)
    _write_json(records[0], record)
    _reject_ingest(scenario, output, records, match)


def test_ingest_rejects_duplicate_slots_sessions_and_short_submissions(tmp_path: Path) -> None:
    scenario, output, records = _prepare_review(tmp_path)
    _reject_ingest(scenario, output, records[:2], "review record count does not match reviewer slots")

    record = _read_json(records[1])
    record.update(reviewer_slot="reviewer-01")
    _write_json(records[1], record)
    _reject_ingest(scenario, output, records, "review record slots are invalid or duplicated")

    scenario, output, records = _prepare_review(tmp_path / "second")
    record = _read_json(records[1])
    record.update(reviewer_session_id=_read_json(records[0])["reviewer_session_id"])
    _write_json(records[1], record)
    _reject_ingest(scenario, output, records, "reviewer session IDs must be unique")


def test_ingest_accepts_human_reviewers_and_bootstraps_multi_block_preferences(
    tmp_path: Path,
) -> None:
    scenario, output, records = _prepare_review(tmp_path, days=14)
    human = _read_json(records[0])
    human.update(reviewer_kind="human", model=None)
    _write_json(records[0], human)

    result = ingest_reviews(records, output, scenario)

    dependence = result["reviewer_dependence"]
    assert dependence["human_review_count"] == 1 and dependence["model_review_count"] == 2
    interval = result["preference"]["by_dimension"]["temporal_plausibility"][
        "week_block_confidence_interval"
    ]
    assert interval["status"] == "ok"
    assert interval["repetitions"] == scenario.bootstrap_repetitions
    assert interval["lower"] <= interval["upper"]


def test_written_packet_hash_drift_is_detected(tmp_path: Path, monkeypatch) -> None:
    fixture = make_evaluation_fixture(tmp_path)
    scenario = load_scenario(fixture.scenario_path)
    left = adapt_labelkit(fixture.labelkit_root, scenario)
    right = adapt_worldmock(fixture.worldmock_root, scenario)
    material = create_review_material(left, right, shared_support(left, right), scenario)
    monkeypatch.setattr(review_module, "sha256_file", lambda path: "0" * 64)
    with pytest.raises(EvaluationError, match="written blind review packet hash changed"):
        write_review_material(tmp_path / "drift", material)


def test_reliability_reports_not_applicable_when_raters_agree_nowhere(tmp_path: Path) -> None:
    scenario, output, records = _prepare_review(tmp_path)
    for path in records:
        record = _read_json(path)
        for rating in record["ratings"]:
            rating["dimensions"]["temporal_plausibility"]["choice"] = "unjudgeable"
            rating["dimensions"]["temporal_plausibility"]["rationale"] = "No evidence to judge."
        _write_json(path, record)

    result = ingest_reviews(records, output, scenario)

    dimension = result["reliability"]["by_dimension"]["temporal_plausibility"]
    assert dimension["alpha"] is None
    summary = result["preference"]["by_dimension"]["temporal_plausibility"]
    assert summary["left_preference_proportion"] is None
    assert summary["unjudgeable_proportion"] == 1.0


# ---------------------------------------------------------------------------
# runner.py：准备收据与最终清单完整性（评估产物合同，`docs/design/01-architecture.md`「真实性评估与结论边界」）
# ---------------------------------------------------------------------------


def test_prepare_rejects_existing_output_directory(tmp_path: Path) -> None:
    with pytest.raises(EvaluationError, match="evaluation output directory already exists"):
        prepare_evaluation(
            tmp_path / "labelkit", tmp_path / "worldmock", tmp_path / "scenario.json", tmp_path
        )


def test_finalize_enforces_prepare_receipt_integrity(tmp_path: Path) -> None:
    fixture = make_evaluation_fixture(tmp_path)
    output = tmp_path / "out"
    prepare_evaluation(fixture.labelkit_root, fixture.worldmock_root, fixture.scenario_path, output)
    records = make_review_records(output, tmp_path / "reviews", candidate_pattern=True)
    scenario = load_scenario(fixture.scenario_path)

    receipt_path = output / "prepare-receipt.json"
    metrics_path = output / "metrics.json"
    pristine_receipt = receipt_path.read_bytes()
    pristine_metrics = metrics_path.read_bytes()
    pristine_scenario = fixture.scenario_path.read_bytes()

    def _restore() -> None:
        receipt_path.write_bytes(pristine_receipt)
        metrics_path.write_bytes(pristine_metrics)
        fixture.scenario_path.write_bytes(pristine_scenario)
        for stray in (output / "rogue.txt", output / "manifest.json"):
            stray.unlink(missing_ok=True)

    try:
        (output / "manifest.json").write_text("{}", encoding="utf-8")
        with pytest.raises(EvaluationError, match="evaluation output is already finalized"):
            finalize_evaluation(output, records)
        _restore()

        receipt = json.loads(pristine_receipt)
        receipt["prepare_status"] = "not_comparable"
        _write_json(receipt_path, receipt)
        with pytest.raises(EvaluationError, match="no comparable observation support"):
            finalize_evaluation(output, records)
        _restore()

        receipt = json.loads(pristine_receipt)
        receipt.pop("schema_version")
        _write_json(receipt_path, receipt)
        with pytest.raises(EvaluationError, match="prepare receipt schema is invalid"):
            finalize_evaluation(output, records)
        _restore()

        receipt = json.loads(pristine_receipt)
        receipt["prepared_artifacts"] = {}
        _write_json(receipt_path, receipt)
        with pytest.raises(EvaluationError, match="prepare artifact list is invalid"):
            finalize_evaluation(output, records)
        _restore()

        (output / "rogue.txt").write_text("rogue", encoding="utf-8")
        with pytest.raises(EvaluationError, match="unexpected files"):
            finalize_evaluation(output, records)
        _restore()

        receipt = json.loads(pristine_receipt)
        first = next(iter(receipt["prepared_artifacts"]))
        receipt["prepared_artifacts"][first] = {}
        _write_json(receipt_path, receipt)
        with pytest.raises(EvaluationError, match="prepared artifact descriptor is invalid"):
            finalize_evaluation(output, records)
        _restore()

        receipt = json.loads(pristine_receipt)
        first = next(iter(receipt["prepared_artifacts"]))
        receipt["prepared_artifacts"][first]["path"] = "../../evil.json"
        _write_json(receipt_path, receipt)
        with pytest.raises(EvaluationError, match="prepared artifact path escapes output"):
            finalize_evaluation(output, records)
        _restore()

        flipped = bytes([pristine_metrics[0] ^ 1]) + pristine_metrics[1:]
        metrics_path.write_bytes(flipped)
        with pytest.raises(EvaluationError, match="prepared artifact hash changed"):
            finalize_evaluation(output, records)
        _restore()

        fixture.scenario_path.write_bytes(pristine_scenario + b"\n")
        with pytest.raises(EvaluationError, match="evaluation scenario hash changed after prepare"):
            finalize_evaluation(output, records)
        _restore()
    finally:
        _restore()

    manifest = finalize_evaluation(output, records)
    assert manifest["committed"] is True
    assert (output / "report.md").is_file()
    assert scenario.window.end.isoformat() in json.dumps(manifest["window"])


def test_verify_final_manifest_rejects_uncommitted_and_drifted_state(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(EvaluationError, match="evaluation manifest is not committed"):
        runner_module._verify_final_manifest(tmp_path, {"committed": False, "artifacts": {}})
    with pytest.raises(EvaluationError, match="evaluation manifest changed after write"):
        runner_module._verify_final_manifest(tmp_path, {"committed": True, "artifacts": {}})
# ---------------------------------------------------------------------------
# public_evidence.py：闭集公开结构证据的提取分支（`docs/design/01-architecture.md`「真实性评估与结论边界」的公开证据合同）
# ---------------------------------------------------------------------------


def _legacy_main_row(sequence_id: str, sequence_class: str, action: dict | None = None) -> dict:
    return {
        **({"actionInfo": action} if action is not None else {}),
        "_meta": {"id": sequence_id, "generation": {"sequence_class": sequence_class},
                  "stream": {"member_ids": []}},
    }


def _legacy_stream_row(event_id: str, owner: str, timestamp: Any, payload: dict) -> dict:
    return {"payload": payload,
            "_meta": {"event": {"event_id": event_id, "owner_sequence_id": owner,
                                "actor": "user", "timestamp": timestamp}}}


def test_legacy_check_in_declares_only_company_workplace_sessions() -> None:
    stamp = 1_767_600_000_000
    rows = [
        _legacy_main_row("s1", "check_in",
                         {"checkInType": "start", "checkInPoiType": "COMPANY", "timestamp": stamp}),
        _legacy_main_row("s2", "check_in",
                         {"checkInType": "end", "checkInPoiType": "COMPANY",
                          "timestamp": stamp + 1_000}),
        _legacy_main_row("s3", "check_in",
                         {"checkInType": "nap", "checkInPoiType": "COMPANY", "timestamp": stamp}),
        _legacy_main_row("s4", "check_in",
                         {"checkInType": "start", "checkInPoiType": "HOME", "timestamp": stamp}),
    ]
    evidence = labelkit_public_evidence(rows, [], {"s1": "work", "s2": "work", "s3": "work",
                                                   "s4": "work"})
    assert [(item.interaction, item.object_type) for item in evidence] == [
        ("check_in", "workplace"), ("check_out", "workplace"),
    ]
    assert all(item.attributes == {"place_type": "workplace"} for item in evidence)


def test_legacy_navigation_keeps_only_supported_travel_modes() -> None:
    rows = [
        _legacy_main_row("s1", "navigation",
                         {"travelMode": "walking", "timestamp": 1_767_600_000_000}),
        _legacy_main_row("s2", "navigation",
                         {"travelMode": "driving", "timestamp": 1_767_600_001_000}),
    ]
    evidence = labelkit_public_evidence(rows, [], {"s1": "travel", "s2": "travel"})
    assert [item.attributes for item in evidence] == [{"travel_mode": "walking"}, {}]


def test_legacy_screen_objects_keep_identity_only_for_known_pages() -> None:
    stamp = "2026-01-05T09:00:00+08:00"
    rows = [
        _legacy_stream_row("e1", "s1", stamp,
                           {"dataName": "screenMemoryEvent", "status": "Finish",
                            "pageId": "attendance-result", "entityId": "workplace-key"}),
        _legacy_stream_row("e2", "s2", stamp,
                           {"dataName": "screenMemoryEvent", "status": "Finish",
                            "pageId": "route-overview", "entityId": "route-key"}),
        _legacy_stream_row("e3", "s3", stamp,
                           {"dataName": "screenMemoryEvent", "status": "Finish",
                            "pageId": "feed", "entityId": "ignored"}),
    ]
    evidence = labelkit_public_evidence([], rows, {"s1": "work", "s2": "travel", "s3": "browsing"})
    assert {(item.object_type, item.object_key) for item in evidence} == {
        ("workplace", "workplace-key"), ("route", "route-key"), ("content", None),
    }
    attendance = next(item for item in evidence if item.object_type == "workplace")
    assert attendance.attributes == {"place_type": "workplace"}


def test_legacy_commute_events_project_arrival_and_leave_only() -> None:
    stamp = "2026-01-05T09:00:00+08:00"
    rows = [
        _legacy_stream_row("e1", "s1", stamp,
                           {"dataName": "commutePeriodEvent", "periodType": "ARRIVE_COMPANY"}),
        _legacy_stream_row("e2", "s2", stamp,
                           {"dataName": "commutePeriodEvent", "periodType": "LEAVE_COMPANY"}),
        _legacy_stream_row("e3", "s3", stamp,
                           {"dataName": "commutePeriodEvent", "periodType": "WALK_OUTSIDE"}),
    ]
    evidence = labelkit_public_evidence([], rows, {"s1": "commute", "s2": "commute",
                                                    "s3": "commute"})
    assert [(item.interaction, item.state) for item in evidence] == [
        ("arrive", "observed"), ("leave", "observed"),
    ]


def test_legacy_stream_rows_require_known_owner_and_valid_timestamps() -> None:
    stamp = "2026-01-05T09:00:00+08:00"
    paste = {"dataName": "pasteBoardEvent"}
    orphan = labelkit_public_evidence(
        [], [_legacy_stream_row("e1", "ghost", stamp, paste)], {"s1": "browsing"}
    )
    assert orphan == ()
    with pytest.raises(EvaluationError, match="timestamp must be ISO-8601"):
        labelkit_public_evidence([], [_legacy_stream_row("e1", "s1", 5, paste)], {"s1": "b"})
    with pytest.raises(EvaluationError, match="timestamp must include timezone"):
        labelkit_public_evidence(
            [], [_legacy_stream_row("e1", "s1", "2026-01-05T09:00:00", paste)], {"s1": "b"}
        )
    with pytest.raises(EvaluationError, match="timestamp must be epoch milliseconds"):
        labelkit_public_evidence(
            [_legacy_main_row("s1", "food_order", {"timestamp": "soon"})], [], {"s1": "purchase"}
        )


def test_day_type_contrast_computes_means_when_both_types_present() -> None:
    work = DayRecord(date(2026, 1, 5), "regular_workday", (), 0, ())
    weekend = DayRecord(date(2026, 1, 10), "ordinary_weekend", (), 0, ())
    assert metrics_module._day_type_contrast((work, weekend), (3.0, 1.0)) == 2.0
    values = {date(2026, 1, 5): 3.0, date(2026, 1, 10): 1.0}
    classes = {date(2026, 1, 5): "regular_workday", date(2026, 1, 10): "ordinary_weekend"}
    assert metrics_module._block_contrast((date(2026, 1, 5), date(2026, 1, 10)), values,
                                          classes) == 2.0


def test_filtered_hour_histogram_skips_unselected_dates() -> None:
    activity = _session(category="browsing", started=datetime(2026, 1, 5, 9, 0, tzinfo=ZONE))
    view = _view(activities=(activity,))
    histogram = metrics_module._filtered_hour_histogram(view, ("browsing",), {date(2026, 1, 6)})
    assert histogram == [0.0] * 168




def test_public_status_converges_source_specific_states() -> None:
    assert review_module._public_status("cancelled") == "cancelled"
    assert review_module._public_status("truncated") == "cancelled"
    assert review_module._public_status("active") == "started"
    assert review_module._public_status("paused") == "paused"
    assert review_module._public_status("observed") == "observed"


def test_checkpoint_run_id_must_match_manifest(tmp_path: Path) -> None:
    import worldmock.evaluation.validation as validation

    fixture = make_evaluation_fixture(tmp_path)
    manifest_path = fixture.worldmock_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["checkpoint"]["run_id"] = "ghost-run"
    _rehash_checkpoint(manifest)
    scenario = load_scenario(fixture.scenario_path)
    with pytest.raises(EvaluationError, match="checkpoint run_id differs from manifest"):
        validation._validated_checkpoint(manifest, scenario.window)


def test_decide_result_reports_mixed_when_interval_crosses_zero() -> None:
    support, causality, official, reviews = _decision_inputs()
    reviews["preference"]["by_dimension"]["temporal_plausibility"][
        "week_block_confidence_interval"] = {"status": "ok", "lower": -0.2, "upper": 0.3}
    result = decide_result(support, causality, {}, {"domains": {}}, reviews)
    dimension = result["evidence_by_dimension"]["blind_review"]["directions"]["temporal_plausibility"]
    assert dimension["raw_direction"] == "mixed"


def test_domain_diagnostic_reports_no_observed_duration_outside_window(tmp_path: Path) -> None:
    fixture = make_evaluation_fixture(tmp_path)
    scenario = load_scenario(fixture.scenario_path)
    reference = scenario.official_reference["domains"]["personal_essential"]
    away = _session(domains=("personal_essential",), duration_kind="world_activity",
                    started=datetime(2026, 3, 1, 9, 0, tzinfo=ZONE))
    result = aggregate_module._domain_diagnostic(_view(activities=(away,)),
                                                 "personal_essential", reference)
    assert result == {"status": "unsupported", "reason": "no_observed_duration"}
