from __future__ import annotations

import json
from pathlib import Path

import pytest

from worldmock.evaluation.errors import EvaluationError
from worldmock.evaluation.scenario import load_scenario


FORMAL_SCENARIO = Path(__file__).parents[2] / "scenarios/evaluation.json"


def test_formal_scenario_freezes_only_the_verified_shared_profile() -> None:
    scenario = load_scenario(FORMAL_SCENARIO)

    assert scenario.shared_profile.to_dict() == {
        "person_count": 1,
        "occupation_class": "white_collar",
        "timezone": "Asia/Shanghai",
    }


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("person_count", 2, "one white-collar person"),
        ("occupation_class", "student", "one white-collar person"),
        ("timezone", "UTC", "timezone must match"),
    ],
)
def test_shared_profile_rejects_unverified_or_mismatched_claims(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    document = json.loads(FORMAL_SCENARIO.read_text(encoding="utf-8"))
    document["shared_profile"][field] = value
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(EvaluationError, match=message):
        load_scenario(path)


def test_shared_profile_rejects_additional_private_fields(tmp_path: Path) -> None:
    document = json.loads(FORMAL_SCENARIO.read_text(encoding="utf-8"))
    document["shared_profile"]["preference"] = "private-canary"
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(EvaluationError, match="shared_profile fields are invalid"):
        load_scenario(path)
