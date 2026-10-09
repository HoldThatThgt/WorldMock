"""@brief 评估场景 JSON 的严格读取。"""

from __future__ import annotations

from worldmock.footprints.models import is_sha256
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from worldmock.evaluation.errors import EvaluationError
from worldmock.evaluation.io import read_json
from worldmock.evaluation.models import EvaluationScenario, SharedProfile, TimeWindow, parse_datetime


def load_scenario(path: Path) -> EvaluationScenario:
    """@brief 从 JSON 读取并验证评估场景。"""
    value = read_json(path)
    _require_keys(value, _ROOT_KEYS, "scenario")
    window = _load_window(value["window"])
    bootstrap = _require_mapping(value["bootstrap"], "bootstrap")
    review = _require_mapping(value["review"], "review")
    mappings = _require_mapping(value["category_mappings"], "category_mappings")
    _require_keys(bootstrap, {"repetitions", "block_days"}, "bootstrap")
    _require_keys(review, {"reviewer_count", "dimensions"}, "review")
    _require_keys(
        mappings,
        {"labelkit_sequence_class", "labelkit_pattern"},
        "category_mappings",
    )
    scenario = EvaluationScenario(
        scenario_id=_require_string(value["scenario_id"], "scenario_id"),
        schema_version=_require_string(value["schema_version"], "schema_version"),
        window=window,
        bootstrap_repetitions=_positive_int(bootstrap.get("repetitions"), "bootstrap.repetitions"),
        block_days=_positive_int(bootstrap.get("block_days"), "bootstrap.block_days"),
        reviewer_count=_positive_int(review.get("reviewer_count"), "review.reviewer_count"),
        review_dimensions=_string_tuple(review.get("dimensions"), "review.dimensions"),
        shared_profile=_load_shared_profile(value["shared_profile"], window),
        special_dates=_string_mapping(value["special_dates"], "special_dates"),
        labelkit_class_map=_string_mapping(mappings.get("labelkit_sequence_class"), "labelkit class map"),
        labelkit_pattern_map=_string_mapping(mappings.get("labelkit_pattern"), "labelkit pattern map"),
        official_domain_map=_tuple_mapping(value["official_domain_map"], "official_domain_map"),
        official_reference=_require_mapping(value["official_reference"], "official_reference"),
        expected_labelkit=_require_mapping(value["expected_labelkit"], "expected_labelkit"),
    )
    _validate_scenario(scenario)
    return scenario


def _validate_scenario(scenario: EvaluationScenario) -> None:
    """@brief 验证周块、三评审、特殊日期和官方领域引用。"""
    if scenario.block_days != 7:
        raise EvaluationError("invalid_scenario", "bootstrap.block_days must be seven")
    if scenario.reviewer_count != 3:
        raise EvaluationError("invalid_scenario", "review.reviewer_count must be three")
    for value, calendar_class in scenario.special_dates.items():
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise EvaluationError("invalid_scenario", "special date key is invalid") from exc
        if parsed not in scenario.window.days or not calendar_class:
            raise EvaluationError("invalid_scenario", "special date is outside the declared window")
    if "eat" in scenario.official_domain_map:
        raise EvaluationError("invalid_scenario", "Eating is not a current evaluation domain")
    _validate_official_reference(scenario)
    _validate_expected_labelkit(scenario.expected_labelkit)


def _validate_official_reference(scenario: EvaluationScenario) -> None:
    """@brief 验证公开参照领域、持续时间语义和两个边际。"""
    reference = scenario.official_reference
    if set(reference) != {"source", "domains"} or not isinstance(reference["domains"], dict):
        raise EvaluationError("invalid_scenario", "official_reference schema is invalid")
    domains = reference["domains"]
    if not domains or any(name not in domains for values in scenario.official_domain_map.values() for name in values):
        raise EvaluationError("invalid_scenario", "official domain mapping references an unknown domain")
    for name, value in domains.items():
        if not isinstance(value, dict) or not isinstance(value.get("label_zh"), str):
            raise EvaluationError("invalid_scenario", f"official domain {name} is invalid")
        _string_tuple(value.get("supported_duration_kinds"), f"official domain {name} duration kinds")
        for cohort in ("urban", "working_age"):
            _validate_reference_margin(value.get(cohort), name, cohort)


def _validate_reference_margin(value: Any, domain: str, cohort: str) -> None:
    """@brief 验证一个官方参与率和参与者分钟数边际。"""
    if not isinstance(value, dict) or set(value) != {"participant_minutes", "participation_rate"}:
        raise EvaluationError("invalid_scenario", f"official domain {domain} {cohort} margin is invalid")
    minutes = value["participant_minutes"]
    rate = value["participation_rate"]
    numbers = (int, float)
    if isinstance(minutes, bool) or not isinstance(minutes, numbers) or minutes < 0:
        raise EvaluationError("invalid_scenario", "official participant_minutes must be non-negative")
    if isinstance(rate, bool) or not isinstance(rate, numbers) or not 0 <= rate <= 1:
        raise EvaluationError("invalid_scenario", "official participation_rate must be between zero and one")


def _validate_expected_labelkit(value: Mapping[str, Any]) -> None:
    """@brief 验证冻结旧交付身份、行数和全部哈希字段。"""
    hashes = {
        "delivery_digest", "raw_main_sha256", "raw_stream_sha256", "repaired_main_sha256",
        "repaired_stream_sha256", "repair_report_sha256", "final_audit_sha256",
    }
    required = hashes | {"run_id", "main_rows", "stream_rows"}
    if set(value) != required:
        raise EvaluationError("invalid_scenario", "expected_labelkit schema is invalid")
    if not isinstance(value["run_id"], str) or not value["run_id"]:
        raise EvaluationError("invalid_scenario", "expected LabelKit run_id is invalid")
    if any(not is_sha256(value[field]) for field in hashes):
        raise EvaluationError("invalid_scenario", "expected LabelKit SHA-256 is invalid")
    for field in ("main_rows", "stream_rows"):
        if isinstance(value[field], bool) or not isinstance(value[field], int) or value[field] <= 0:
            raise EvaluationError("invalid_scenario", f"expected LabelKit {field} is invalid")





def _load_window(value: Any) -> TimeWindow:
    """@brief 读取声明时间窗口。"""
    window = _require_mapping(value, "window")
    _require_keys(window, {"start", "end", "timezone"}, "window")
    return TimeWindow(
        parse_datetime(window["start"], "window.start"),
        parse_datetime(window["end"], "window.end"),
        _require_string(window["timezone"], "window.timezone"),
    )


def _load_shared_profile(value: Any, window: TimeWindow) -> SharedProfile:
    """@brief 读取且冻结双方已证实的单人白领粗画像。"""
    profile = _require_mapping(value, "shared_profile")
    _require_keys(profile, {"person_count", "occupation_class", "timezone"}, "shared_profile")
    result = SharedProfile(
        _positive_int(profile.get("person_count"), "shared_profile.person_count"),
        _require_string(profile.get("occupation_class"), "shared_profile.occupation_class"),
        _require_string(profile.get("timezone"), "shared_profile.timezone"),
    )
    if result.person_count != 1 or result.occupation_class != "white_collar":
        raise EvaluationError("invalid_scenario", "shared_profile must describe one white-collar person")
    if result.timezone != window.timezone:
        raise EvaluationError("invalid_scenario", "shared_profile timezone must match the evaluation window")
    return result


def _require_keys(value: Mapping[str, Any], keys: set[str], name: str) -> None:
    """@brief 验证对象含全部必需键。"""
    missing = sorted(keys - value.keys())
    unexpected = sorted(value.keys() - keys)
    if missing or unexpected:
        detail = f"missing={missing}, unexpected={unexpected}"
        raise EvaluationError("invalid_scenario", f"{name} fields are invalid: {detail}")


def _require_mapping(value: Any, name: str) -> dict[str, Any]:
    """@brief 验证普通字符串键对象。"""
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise EvaluationError("invalid_scenario", f"{name} must be an object")
    return value


def _require_string(value: Any, name: str) -> str:
    """@brief 验证非空字符串。"""
    if not isinstance(value, str) or not value:
        raise EvaluationError("invalid_scenario", f"{name} must be a non-empty string")
    return value


def _require_int(value: Any, name: str) -> int:
    """@brief 验证整数。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise EvaluationError("invalid_scenario", f"{name} must be an integer")
    return value


def _positive_int(value: Any, name: str) -> int:
    """@brief 验证正整数。"""
    parsed = _require_int(value, name)
    if parsed <= 0:
        raise EvaluationError("invalid_scenario", f"{name} must be positive")
    return parsed


def _string_tuple(value: Any, name: str) -> tuple[str, ...]:
    """@brief 验证非空且不重复的字符串数组。"""
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
        raise EvaluationError("invalid_scenario", f"{name} must be a non-empty string array")
    if len(value) != len(set(value)):
        raise EvaluationError("invalid_scenario", f"{name} must not contain duplicates")
    return tuple(value)


def _string_mapping(value: Any, name: str) -> dict[str, str]:
    """@brief 验证字符串到字符串的映射。"""
    mapping = _require_mapping(value, name)
    if not all(isinstance(item, str) and item for item in mapping.values()):
        raise EvaluationError("invalid_scenario", f"{name} values must be non-empty strings")
    return mapping


def _tuple_mapping(value: Any, name: str) -> dict[str, tuple[str, ...]]:
    """@brief 验证字符串到字符串数组的映射。"""
    mapping = _require_mapping(value, name)
    return {key: _string_tuple(item, f"{name}.{key}") for key, item in mapping.items()}


_ROOT_KEYS = {
    "scenario_id",
    "schema_version",
    "window",
    "bootstrap",
    "review",
    "shared_profile",
    "special_dates",
    "category_mappings",
    "official_domain_map",
    "official_reference",
    "expected_labelkit",
}
