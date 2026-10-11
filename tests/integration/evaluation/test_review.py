from __future__ import annotations


import json
import re
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from tests.evaluation.conftest import make_evaluation_fixture, make_review_records
import worldmock.evaluation.review as review_module
from worldmock.evaluation.adapters import adapt_labelkit, adapt_worldmock
from worldmock.evaluation.errors import EvaluationError
from worldmock.evaluation.metrics import shared_support
from worldmock.evaluation.models import DatasetView, PublicEvidence
from worldmock.evaluation.review import create_review_material, ingest_reviews, write_review_material
from worldmock.evaluation.scenario import load_scenario


def test_blind_packets_are_deterministic_and_hide_source_identity(evaluation_fixture) -> None:
    scenario, left, right, support = _review_inputs(evaluation_fixture)

    first = create_review_material(left, right, support, scenario)
    second = create_review_material(left, right, support, scenario)

    assert first == second
    assert len(first.packets) == 3
    serialized = json.dumps(first.packets, ensure_ascii=False)
    for forbidden in ("run_id", "dataset_id", "provider", "source_pointer", "unblinding-key"):
        assert forbidden not in serialized
    assert first.key["reviewer_mappings"]
    packet = first.packets[0]
    assert packet["shared_profile"] == {
        "person_count": 1,
        "occupation_class": "white_collar",
        "timezone": "Asia/Shanghai",
    }
    activities = packet["items"][0]["left"]["activities"]
    assert all(set(row) == {"action", "start", "end", "status", "location"}
               for row in activities)
    assert packet["items"][0]["left"]["public_evidence"] == []


def test_formal_item_selection_covers_twelve_weeks_and_every_special_date() -> None:
    scenario = load_scenario(Path(__file__).resolve().parents[3] / 'tests/evaluation' / "fixtures/scenario.json")
    empty = DatasetView("test", "test", "test", scenario.window, (), (), (), {}, {})
    support = {"shared_channels": []}

    seeded = replace(empty, validation={"random_state": "evaluation-random-root"})
    material = create_review_material(empty, seeded, support, scenario)
    items = material.packets[0]["items"]

    assert sum(item["scope"] == "week" for item in items) == 12
    selected_specials = {
        item["dates"][0]
        for item in items
        if item["scope"] == "special_day"
    }
    assert selected_specials == set(scenario.special_dates)
    coverage = material.index["selection_coverage"]
    for scope in ("regular_workday", "ordinary_weekend"):
        missing = coverage["missing_ordinary_samples"][scope]
        assert sum(item["scope"] == scope for item in items) == 12 - len(missing)
    assert coverage["missing_ordinary_samples"]["regular_workday"] == ["2026-02-16"]


def test_review_ingest_unblinds_after_all_records_and_computes_alpha(
    evaluation_fixture, tmp_path: Path
) -> None:
    scenario, left, right, support = _review_inputs(evaluation_fixture)
    output = tmp_path / "prepared"
    write_review_material(output, create_review_material(left, right, support, scenario))
    records = make_review_records(output, tmp_path / "reviews", candidate_pattern=True)

    result = ingest_reviews(records, output, scenario)

    assert len(result["records"]) == 3
    assert result["records"][0]["raw_response"].startswith("Raw response")
    dimensions = result["reliability"]["by_dimension"]
    for name in ("temporal_plausibility", "persona_consistency", "missing_expected_activity"):
        assert dimensions[name]["status"] == "ok"
        assert dimensions[name]["alpha"] == pytest.approx(1.0)
    for name in ("causal_continuity", "long_term_consequences", "routine_and_variation"):
        assert dimensions[name]["status"] == "not_applicable"
        assert dimensions[name]["alpha"] is None
    groups = result["reviewer_dependence"]["same_model_session_groups"]
    assert len(groups["test-provider:test-model"]) == 3


def test_missing_review_dimension_is_rejected(evaluation_fixture, tmp_path: Path) -> None:
    scenario, left, right, support = _review_inputs(evaluation_fixture)
    output = tmp_path / "prepared"
    write_review_material(output, create_review_material(left, right, support, scenario))
    records = make_review_records(output, tmp_path / "reviews", candidate_pattern=True)
    record = json.loads(records[0].read_text(encoding="utf-8"))
    record["ratings"][0]["dimensions"].pop(scenario.review_dimensions[0])
    records[0].write_text(json.dumps(record), encoding="utf-8")

    with pytest.raises(EvaluationError, match="dimensions are incomplete"):
        ingest_reviews(records, output, scenario)


def test_review_prompt_hash_must_be_canonical_sha256(evaluation_fixture, tmp_path: Path) -> None:
    scenario, left, right, support = _review_inputs(evaluation_fixture)
    output = tmp_path / "prepared"
    write_review_material(output, create_review_material(left, right, support, scenario))
    records = make_review_records(output, tmp_path / "reviews", candidate_pattern=True)
    record = json.loads(records[0].read_text(encoding="utf-8"))
    record["prompt_sha256"] = "G" * 64
    records[0].write_text(json.dumps(record), encoding="utf-8")

    with pytest.raises(EvaluationError, match="prompt SHA-256 is invalid"):
        ingest_reviews(records, output, scenario)


def test_tampered_blind_packet_is_rejected_before_unblinding(
    evaluation_fixture, tmp_path: Path
) -> None:
    scenario, left, right, support = _review_inputs(evaluation_fixture)
    output = tmp_path / "prepared"
    write_review_material(output, create_review_material(left, right, support, scenario))
    records = make_review_records(output, tmp_path / "reviews", candidate_pattern=True)
    packet = output / "review-packets/reviewer-01.json"
    packet.write_bytes(packet.read_bytes() + b" ")

    with pytest.raises(EvaluationError, match="packet hash is invalid"):
        ingest_reviews(records, output, scenario)


def test_object_references_are_candidate_and_reviewer_scoped_but_stable_across_days(
    tmp_path: Path, monkeypatch
) -> None:
    fixture = make_evaluation_fixture(tmp_path, days=7)
    scenario, left, right, _ = _review_inputs(fixture)
    left = replace(left, public_evidence=_object_history(left, "order", "shared-private-order"))
    right = replace(right, public_evidence=_object_history(right, "order", "shared-private-order"))
    support = shared_support(left, right)
    original = review_module._swap_sides

    first = create_review_material(left, right, support, scenario)
    left_refs = _candidate_refs(first, 0, "left_candidate")
    right_refs = _candidate_refs(first, 0, "right_candidate")
    reviewer_two_refs = _candidate_refs(first, 1, "left_candidate")

    assert len(left_refs) == len(right_refs) == len(reviewer_two_refs) == 1
    assert left_refs != right_refs
    assert left_refs != reviewer_two_refs
    assert "shared-private-order" not in json.dumps(first.packets)

    monkeypatch.setattr(
        review_module,
        "_swap_sides",
        lambda seed, slot, item_id: not original(seed, slot, item_id),
    )
    flipped = create_review_material(left, right, support, scenario)
    assert _candidate_refs(flipped, 0, "left_candidate") == left_refs
    assert _candidate_refs(flipped, 0, "right_candidate") == right_refs


def test_judgeability_requires_two_sided_state_changes_and_persistent_long_term_objects(
    tmp_path: Path,
) -> None:
    fixture = make_evaluation_fixture(tmp_path, days=7)
    scenario, left, right, _ = _review_inputs(fixture)
    left_order = tuple(
        replace(item, state="requested" if left.window.localize(item.occurred_at).weekday() < 5 else "delivered")
        for item in _object_history(left, "order", "left-order")
    )
    right_static = tuple(replace(item, state="requested") for item in _object_history(right, "order", "right-order"))
    one_sided = create_review_material(
        replace(left, public_evidence=left_order),
        replace(right, public_evidence=right_static),
        shared_support(
            replace(left, public_evidence=left_order),
            replace(right, public_evidence=right_static),
        ),
        scenario,
    )
    week = next(item for item in one_sided.packets[0]["items"] if item["scope"] == "week")
    selected_states = {
        frozenset(row["state"] for row in week[side]["public_evidence"])
        for side in ("left", "right")
    }
    assert selected_states == {frozenset({"requested"}), frozenset({"requested", "delivered"})}
    assert week["judgeability"]["causal_continuity"]["judgeable"] is False
    assert week["judgeability"]["long_term_consequences"]["judgeable"] is False

    left_route = _object_history(left, "route", "left-route")
    right_route = _object_history(right, "route", "right-route")
    route_support = shared_support(
        replace(left, public_evidence=left_route),
        replace(right, public_evidence=right_route),
    )
    routes = create_review_material(
        replace(left, public_evidence=left_route),
        replace(right, public_evidence=right_route),
        route_support,
        scenario,
    )
    route_week = next(item for item in routes.packets[0]["items"] if item["scope"] == "week")
    assert route_week["judgeability"]["causal_continuity"]["judgeable"] is True
    assert route_week["judgeability"]["long_term_consequences"]["judgeable"] is False

    left_place = tuple(
        replace(item, action="visit", interaction="visit", object_type="workplace",
                state="observed" if index == 0 else "viewed")
        for index, item in enumerate(left_route)
    )
    right_place = tuple(
        replace(item, action="visit", interaction="visit", object_type="workplace",
                state="observed" if index == 0 else "viewed")
        for index, item in enumerate(right_route)
    )
    place_left = replace(left, public_evidence=left_place)
    place_right = replace(right, public_evidence=right_place)
    places = create_review_material(
        place_left, place_right, shared_support(place_left, place_right), scenario
    )
    place_week = next(item for item in places.packets[0]["items"] if item["scope"] == "week")
    assert place_week["judgeability"]["causal_continuity"]["judgeable"] is False


def test_repeated_same_state_frames_collapse_only_within_their_real_session(
    evaluation_fixture,
) -> None:
    scenario, left, right, _ = _review_inputs(evaluation_fixture)
    start = left.window.start + timedelta(hours=9)
    repeated = tuple(
        PublicEvidence(
            start + timedelta(seconds=index), "browsing", "view", "content", "viewed",
            None, {}, (f"event-{index}",), "session-one",
        )
        for index in range(100)
    )
    other_session = PublicEvidence(
        start + timedelta(minutes=2), "browsing", "view", "content", "viewed",
        None, {}, ("event-other",), "session-two",
    )
    left = replace(left, public_evidence=(*repeated, other_session))
    right = replace(right, public_evidence=(*repeated, other_session))

    material = create_review_material(left, right, shared_support(left, right), scenario)
    rows = material.packets[0]["items"][0]["left"]["public_evidence"]

    assert len(rows) == 2
    assert all(row["state"] == "viewed" for row in rows)


def test_unjudgeable_dimension_rejects_a_candidate_preference(
    evaluation_fixture, tmp_path: Path
) -> None:
    scenario, left, right, support = _review_inputs(evaluation_fixture)
    output = tmp_path / "prepared"
    write_review_material(output, create_review_material(left, right, support, scenario))
    records = make_review_records(output, tmp_path / "reviews")
    record = json.loads(records[0].read_text(encoding="utf-8"))
    record["ratings"][0]["dimensions"]["causal_continuity"] = {
        "choice": "left",
        "rationale": "One side has more visible evidence.",
    }
    records[0].write_text(json.dumps(record), encoding="utf-8")

    with pytest.raises(EvaluationError, match="must use the unjudgeable choice"):
        ingest_reviews(records, output, scenario)


def _review_inputs(evaluation_fixture):
    scenario = load_scenario(evaluation_fixture.scenario_path)
    left = adapt_labelkit(evaluation_fixture.labelkit_root, scenario)
    right = adapt_worldmock(evaluation_fixture.worldmock_root, scenario)
    return scenario, left, right, shared_support(left, right)


def _object_history(view: DatasetView, object_type: str, object_key: str) -> tuple[PublicEvidence, ...]:
    """构造跨日真实状态变化，并在其后保留重复观察。"""
    values = []
    for index, day in enumerate(view.window.days):
        state = "requested" if index == 0 else "completed"
        values.append(PublicEvidence(
            view.window.start + timedelta(days=index, hours=10),
            "purchase" if object_type == "order" else "travel",
            "order" if object_type == "order" else "travel",
            object_type,
            state,
            object_key,
            {},
            (f"event-{object_type}-{index}",),
            f"session-{object_type}",
        ))
    return tuple(values)


def _candidate_refs(material, reviewer_index: int, candidate: str) -> set[str]:
    """按私有映射取一个真实候选在指定评审槽的匿名引用。"""
    packet = material.packets[reviewer_index]
    mappings = material.key["reviewer_mappings"][packet["reviewer_slot"]]
    references = set()
    for item in packet["items"]:
        mapping = mappings[item["item_id"]]
        side = "left" if mapping["left"] == candidate else "right"
        references.update(
            row["object_ref"]
            for row in item[side]["public_evidence"]
            if row.get("object_ref") is not None
        )
    return references


@pytest.mark.parametrize("retired", ["category", "companion"])
def test_packet_two_rejects_retired_activity_fields(evaluation_fixture, retired):
    """五字段合同在读取边界拒绝旧分类和同伴。"""
    scenario, left, right, support = _review_inputs(evaluation_fixture)
    material = create_review_material(left, right, support, scenario)
    row = dict(material.packets[0]["items"][0]["left"]["activities"][0])
    row[retired] = "retired"
    with pytest.raises(EvaluationError, match="blind review activity timeline schema is invalid"):
        review_module._validate_candidate_timeline(
            {"activities": [row], "public_evidence": []}, {})


def test_review_packet_two_preserves_action_and_masks_actual_location(evaluation_fixture):
    """动作不被分类，地点只显示本评审本候选作用域的匿名引用。"""
    scenario, left, right, support = _review_inputs(evaluation_fixture)
    material = create_review_material(left, right, support, scenario)
    assert {packet["schema_version"] for packet in material.packets} == {"blind-review-packet-2"}
    timeline = [row for packet in material.packets for item in packet["items"]
                for side in ("left", "right") for row in item[side]["activities"]]
    assert any(row["action"] == "interact" for row in timeline)
    assert all(set(row) == {"action", "start", "end", "status", "location"} for row in timeline)
    locations = [row["location"] for row in timeline if row["location"] is not None]
    assert locations and all(re.fullmatch(r"object-[0-9a-f]{64}", value) for value in locations)


def test_ingest_is_self_contained_after_source_runs_are_removed(evaluation_fixture, tmp_path):
    """冻结包持有自己的已认证随机根，独立收据读取不再访问源运行。"""
    import shutil
    scenario, left, right, support = _review_inputs(evaluation_fixture)
    output = tmp_path / "standalone"
    write_review_material(output, create_review_material(left, right, support, scenario))
    records = make_review_records(output, tmp_path / "records")
    shutil.rmtree(evaluation_fixture.worldmock_root)
    shutil.rmtree(evaluation_fixture.labelkit_root)
    result = ingest_reviews(records, output, scenario)
    assert len(result["records"]) == scenario.reviewer_count
    key = json.loads((output / "unblinding-key.json").read_text(encoding="utf-8"))
    assert key["random_state"] == right.validation["random_state"]


def test_packet_one_is_rejected_before_standalone_review_ingestion(evaluation_fixture):
    """旧包版本不能通过当前验证，不能靠旧字段兼容读取。"""
    scenario, left, right, support = _review_inputs(evaluation_fixture)
    material = create_review_material(left, right, support, scenario)
    packet = dict(material.packets[0], schema_version="blind-review-packet-1")
    with pytest.raises(EvaluationError, match="blind review packet schema is invalid"):
        review_module._validate_packet(packet, material.index["packets"][0], material.index, scenario)


@pytest.mark.parametrize("value,accepted", [
    ("walking", True), ("driving", True), ("transit", True), (None, True),
    ("taxi", False), ("bus", False), ("bike", False), ("步行", False), ("Walking", False)])
def test_blind_review_travel_mode_closed_set_keeps_exactly_three_words(value, accepted):
    """盲评的出行方式公开闭集保持 walking、driving、transit 三个词；数据集出行方式的身份或名称不是其中的值。"""
    row = {"at": "2026-01-05T09:00:00+08:00", "object_type": "route", "interaction": "travel", "travel_mode": value}
    timeline = {"activities": [], "public_evidence": [row]}
    fields = {"route": ["interaction", "travel_mode"]}
    if accepted:
        review_module._validate_candidate_timeline(timeline, fields)
    else:
        with pytest.raises(EvaluationError, match="^blind review public evidence value is outside the closed set$"):
            review_module._validate_candidate_timeline(timeline, fields)
