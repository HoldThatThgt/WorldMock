"""@brief 评估随机性只取认证世界检查点并使用登记的带键随机流。"""

import copy
import hashlib
import hmac
import json
from datetime import date
from pathlib import Path

import pytest

from worldmock.evaluation import metrics, review
from worldmock.evaluation.errors import EvaluationError
from worldmock.evaluation.io import stable_json_bytes
from worldmock.evaluation.randomness import checkpoint_random_state
from worldmock.evaluation.scenario import load_scenario
from worldmock.evaluation.validation import validate_canonical_delivery
from worldmock.footprints.models import sha256_json
from worldmock.life.randomness import random_stream

SCENARIO = Path(__file__).resolve().parents[3] / "tests/evaluation/fixtures/scenario.json"


@pytest.mark.parametrize("value", [None, "", 0, [], {}])
def test_checkpoint_random_root_is_required(value):
    with pytest.raises(EvaluationError, match="WorldMock checkpoint random_state is missing") as failure:
        checkpoint_random_state({"random_state": value})
    assert failure.value.code == "invalid_input"


def test_checkpoint_random_root_is_not_replaced_by_a_derived_seed():
    assert checkpoint_random_state({"random_state": "frozen-runtime-root"}) == "frozen-runtime-root"


@pytest.mark.parametrize("section", ["bootstrap", "review"])
def test_retired_evaluation_seed_fields_are_rejected(section, tmp_path):
    value = json.loads(SCENARIO.read_text(encoding="utf-8"))
    value[section]["seed"] = "retired"
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(EvaluationError) as failure:
        load_scenario(path)
    assert failure.value.code == "invalid_scenario"
    assert str(failure.value) == f"{section} fields are invalid: missing=[], unexpected=['seed']"


def test_authenticated_checkpoint_missing_random_root_is_rejected(evaluation_fixture):
    path = evaluation_fixture.worldmock_root / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["checkpoint"]["state"].pop("random_state")
    checkpoint = manifest["checkpoint"]
    checkpoint["record_sha256"] = sha256_json({key: value for key, value in checkpoint.items()
                                             if key != "record_sha256"})
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(EvaluationError, match="WorldMock checkpoint random_state is missing"):
        validate_canonical_delivery(evaluation_fixture.worldmock_root,
                                    load_scenario(evaluation_fixture.scenario_path), "worldmock")


def test_review_draws_and_anonymous_references_use_registered_streams():
    root = "frozen-runtime-root"
    first = date(2026, 1, 5)
    candidates = [first, date(2026, 1, 6)]
    assert review._sample_day(candidates, root, first, "regular_workday") == random_stream(
        root, "review-day:2026-01-05:regular_workday").choice(candidates)
    assert review._swap_sides(root, "reviewer-01", "item-a") == bool(random_stream(
        root, "review-swap:reviewer-01:item-a").getrandbits(1))
    mask = review._EvidenceMask("reviewer-01", "right_candidate", root, {})
    key = random_stream(root, "review-mask:reviewer-01").getrandbits(256).to_bytes(32, "big")
    expected = "object-" + hmac.new(key, b"right_candidate\0order\0purchase-a", hashlib.sha256).hexdigest()
    assert review._masked_ref(mask, "order", "purchase-a") == expected


def test_metric_and_review_bootstrap_use_separate_registered_streams(monkeypatch):
    scenario = load_scenario(SCENARIO)
    root = "frozen-runtime-root"
    observed = []
    original = metrics.np.random.default_rng
    monkeypatch.setattr(metrics.np.random, "default_rng", lambda seed: observed.append(seed) or original(seed))
    first = metrics._bootstrap_interval("rhythm", [1, 2, 3], scenario, root)
    second = metrics._bootstrap_interval("rhythm", [1, 2, 3], scenario, root)
    choices = [("2026-01-05", "left_candidate"), ("2026-01-12", "right_candidate")]
    review._preference_interval(choices, "continuity", scenario, root)
    assert stable_json_bytes(first) == stable_json_bytes(second)
    assert observed == [random_stream(root, "bootstrap-metric:rhythm").getrandbits(64)] * 2 + [
        random_stream(root, "bootstrap-review:continuity").getrandbits(64)]
