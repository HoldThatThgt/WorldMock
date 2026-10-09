"""当前资金、回调和冻结输入合同拒绝真实不一致数据。"""

import copy
import json
from types import SimpleNamespace

import pytest

from worldmock.errors import IntegrityError
from worldmock.footprints.environment_events import validate_environment_event
from worldmock.footprints.frozen_environment import validate_frozen_environment
from worldmock.footprints.ledger import validate_account_history, validate_account_transition
from worldmock.footprints.models import EventDraft, validate_resource_flows


def _funding(amount=100):
    return EventDraft(event_type="environment_event", occurred_at=100, source_kind="scenario",
                      data={"kind": "initial_funds_injected"},
                      resource_changes={"account:agent:1": amount, "account:external:scenario": -amount})


def _money(amount):
    return EventDraft(event_type="action_result", occurred_at=101,
                      resource_changes={"account:agent:1": amount, "account:external:scenario": -amount})


@pytest.mark.parametrize("case", ["identity", "late_funding", "reordered_funding", "wrong_initial_amount"])
def test_account_transition_rejects_false_initial_or_changed_identity(case):
    before = {"accounts": {"agent:1": 100, "external:scenario": -100}}
    after = copy.deepcopy(before)
    events, initial = [_funding()], True
    if case == "identity":
        after["accounts"] = {"agent:2": 100, "external:scenario": -100}
    elif case == "late_funding":
        initial = False
    elif case == "reordered_funding":
        events.insert(0, _money(0))
    else:
        events = [_funding(99)]
    with pytest.raises(IntegrityError):
        validate_account_transition(before, after, events, initial)


@pytest.mark.parametrize("case", ["missing", "duplicated", "not_first", "negative_prefix"])
def test_account_history_requires_one_first_funding_and_nonnegative_intermediate_balance(case):
    events = [_funding()]
    final = {"accounts": {"agent:1": 100, "external:scenario": -100}}
    if case == "missing":
        events = [_money(100)]
    elif case == "duplicated":
        events.append(_funding())
    elif case == "not_first":
        events.insert(0, _money(0))
    else:
        events.extend([_money(-110), _money(110)])
    with pytest.raises(IntegrityError):
        validate_account_history(events, final)


@pytest.mark.parametrize("payload", [None, [{}], [{"resource": "", "owner": "agent:1", "direction": "source",
    "amount": 1, "cause": "fact"}], [{"resource": "energy", "owner": "agent:1", "direction": "invalid",
    "amount": 1, "cause": "fact"}], [{"resource": "energy", "owner": "agent:1", "direction": "source",
    "amount": float("inf"), "cause": "fact"}]])
def test_resource_flows_reject_malformed_or_nonfinite_amounts_and_log(payload, caplog):
    with pytest.raises(ValueError):
        validate_resource_flows(payload)
    assert any(record.levelname == "ERROR" for record in caplog.records)


def test_callback_reminder_requires_its_current_closed_fields():
    event = SimpleNamespace(data={"kind": "callback_reminder", "rule_id": "rule", "app_id": "app"},
                            source_kind="environment", visibility="agent", resource_changes={})
    with pytest.raises(ValueError, match="Callback reminder"):
        validate_environment_event(event)


@pytest.mark.parametrize("case", ["asset_set", "persona", "catalog_identity", "catalog_file", "map"])
def test_frozen_environment_rejects_manifest_identity_mismatch(engine_factory, case):
    engine = engine_factory()
    store = engine.context.store
    frozen = copy.deepcopy(store.manifest)
    if case == "asset_set":
        frozen["environment_assets"].pop("persona.toml")
    elif case == "persona":
        frozen["persona_sha256"] = "0" * 64
    elif case == "catalog_identity":
        frozen["environment_identity"]["environment_version"] = "different"
    elif case == "map":
        frozen["environment_identity"]["map_sha256"] = "0" * 64
    else:
        manifest = json.loads((engine.context.inputs.root / "catalog/manifest.json").read_bytes())
        manifest["files"][0]["sha256"] = "0" * 64
        reference = store.assets.put(json.dumps(manifest).encode(),
                                     {"media_type": "application/json", "source": {"kind": "test_manifest"}})
        frozen["environment_assets"]["catalog/manifest.json"] = reference.model_dump(mode="json")
    with pytest.raises(IntegrityError):
        validate_frozen_environment(store.run_dir, frozen, [], [])
