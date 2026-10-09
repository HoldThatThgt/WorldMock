"""完整真实足迹的区间、所有权和唯一收货不变量。"""

from copy import deepcopy
import json

import pytest

from tests.integration.footprints.test_acs_export import real_export
from worldmock.errors import IntegrityError
from worldmock.footprints.invariants import validate_footprint_facts
from worldmock.footprints.validation import validate_export


@pytest.fixture
def facts(real_export):
    manifest, events, activities = validate_export(real_export)
    def rows(name):
        return [json.loads(line) for line in (real_export / name).read_bytes().splitlines()]
    return events, activities, rows("purchases.jsonl"), rows("app-usage.jsonl"), manifest["checkpoint"]["state"]


def test_current_facts_pass_all_invariants(facts):
    validate_footprint_facts(*facts)


@pytest.mark.parametrize("kind", ["purchase", "receipt", "order_attempt", "app_use", "content_consumed"])
@pytest.mark.parametrize("fault", ["missing_source", "other_owner"])
def test_each_fact_requires_its_real_owned_source(facts, kind, fault):
    events, activities, purchases, apps, state = deepcopy(facts)
    row = next(row for row in purchases + apps if row["fact_kind"] == kind)
    key = "request_event_id" if kind == "order_attempt" else "source_event_id"
    if fault == "missing_source":
        row[key] = "absent"
    else:
        row["agent_id"] = 2
    with pytest.raises(IntegrityError, match="missing or owned"):
        validate_footprint_facts(events, activities, purchases, apps, state)


@pytest.mark.parametrize("fault", ["receipt_missing", "receipt_duplicate", "goods_missing", "receipt_money",
                                     "overlap", "content_outside", "content_backwards", "extra_app_field"])
def test_recomputed_fact_integrity_rejects_false_receipts_and_intervals(facts, fault):
    events, activities, purchases, apps, state = deepcopy(facts)
    receipt = next(row for row in purchases if row["fact_kind"] == "receipt")
    consumed = next(row for row in apps if row["fact_kind"] == "content_consumed")
    use = next(row for row in apps if row["fact_kind"] == "app_use")
    if fault == "receipt_missing":
        purchases.remove(receipt)
    elif fault == "receipt_duplicate":
        purchases.append(deepcopy(receipt))
    elif fault == "goods_missing":
        next(row for row in events if row["event_id"] == receipt["source_event_id"])["data"]["kind"] = "omitted"
    elif fault == "receipt_money":
        next(row for row in events if row["event_id"] == receipt["source_event_id"])["resource_changes"] = {
            "account:agent:1": -10, "account:external:scenario": 10}
    elif fault == "overlap":
        use["segments"].append(deepcopy(use["segments"][0]))
        next(row for row in activities if row["activity_id"] == use["activity_id"])["segments"] = use["segments"]
    elif fault == "content_outside":
        consumed["interval_start"] -= 1
    elif fault == "content_backwards":
        consumed["position_after"] = -1
    else:
        use["device"] = "phone"
    with pytest.raises(IntegrityError):
        validate_footprint_facts(events, activities, purchases, apps, state)
