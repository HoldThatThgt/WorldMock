"""当前资金、回调和冻结输入合同拒绝真实不一致数据。"""

import copy
import hashlib
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


CALLBACK_ROW = b'{"cap":3,"initial":1,"reset_on_goods":[],"state":"stock"}\n'
MAP_ROW = b'{"a":"home","b":"office","length_m":12600}\n'
FIXED_ASSETS = {"persona.toml", "config.toml", "map.json", "catalog/manifest.json"}
REQUIRED_ASSETS = {"catalog/app-catalog.jsonl", "catalog/content-catalog.jsonl", "catalog/location.jsonl",
                   "catalog/merchant-catalog.jsonl"}


def _engine_with(engine_factory, tmp_path, files):
    """在夹具副本里放入可选文件并刷新清单，再由真实引擎冻结这份环境。"""
    from tests.life.environment_inputs import copy_environment
    from worldmock.catalog.identity import refresh_manifest_identity

    root = copy_environment(tmp_path / "optional-input")
    for name, content in files.items():
        (root / "catalog" / name).write_bytes(content)
    refresh_manifest_identity(root / "catalog")
    return engine_factory(root)


def _frozen_manifest(store, frozen):
    path = store.run_dir / "assets" / frozen["environment_assets"]["catalog/manifest.json"]["sha256"]
    return json.loads(path.read_bytes())


def _put(store, frozen, name, content):
    reference = store.assets.put(content, {"media_type": "text/plain", "source": {"kind": "test_frozen_input"}})
    frozen["environment_assets"][name] = reference.model_dump(mode="json")


@pytest.mark.parametrize("files", [{}, {"callbacks.jsonl": b""}, {"callbacks.jsonl": CALLBACK_ROW},
                                   {"map.jsonl": b""}, {"map.jsonl": MAP_ROW},
                                   {"callbacks.jsonl": CALLBACK_ROW, "map.jsonl": MAP_ROW}],
                         ids=["required-only", "empty-callbacks", "callbacks", "empty-map", "map", "both"])
def test_frozen_asset_names_follow_the_frozen_manifest_file_table(engine_factory, tmp_path, files):
    """冻结素材恰为四个固定名字加清单登记的每个目录文件；带与不带可选文件都通过冻结核对。"""
    engine = _engine_with(engine_factory, tmp_path, files)
    store = engine.context.store
    assert set(store.manifest["environment_assets"]) == (
        FIXED_ASSETS | REQUIRED_ASSETS | {f"catalog/{name}" for name in files})
    validate_frozen_environment(store.run_dir, store.manifest, [], [])
    for name, content in files.items():
        reference = store.manifest["environment_assets"][f"catalog/{name}"]
        assert (store.run_dir / "assets" / reference["sha256"]).read_bytes() == content
    frozen_map = store.run_dir / "assets" / store.manifest["environment_assets"]["map.json"]["sha256"]
    assert hashlib.sha256(frozen_map.read_bytes()).hexdigest() == engine.context.catalog.map_sha256


@pytest.mark.parametrize("case", ["extra_optional", "extra_unknown", "missing_required", "missing_optional",
                                  "missing_map", "missing_manifest"])
def test_frozen_asset_set_must_equal_the_frozen_manifest(engine_factory, tmp_path, case):
    engine = _engine_with(engine_factory, tmp_path, {"map.jsonl": MAP_ROW})
    store = engine.context.store
    frozen = copy.deepcopy(store.manifest)
    if case == "extra_optional":
        _put(store, frozen, "catalog/callbacks.jsonl", b"")
    elif case == "extra_unknown":
        _put(store, frozen, "catalog/travel-method.jsonl", b"")
    else:
        frozen["environment_assets"].pop({
            "missing_required": "catalog/location.jsonl", "missing_optional": "catalog/map.jsonl",
            "missing_map": "map.json", "missing_manifest": "catalog/manifest.json"}[case])
    with pytest.raises(IntegrityError, match="^Frozen environment asset set is invalid$"):
        validate_frozen_environment(store.run_dir, frozen, [], [])


@pytest.mark.parametrize("case", ["required_unlisted", "outside_listed"])
def test_frozen_manifest_file_table_must_stay_inside_the_catalog_closure(engine_factory, tmp_path, case):
    """冻结清单缺必需文件或列出闭集外路径时，素材名字即使与它一致也不被接受。"""
    engine = _engine_with(engine_factory, tmp_path, {})
    store = engine.context.store
    frozen = copy.deepcopy(store.manifest)
    manifest = _frozen_manifest(store, frozen)
    if case == "required_unlisted":
        manifest["files"] = [entry for entry in manifest["files"] if entry["path"] != "location.jsonl"]
        frozen["environment_assets"].pop("catalog/location.jsonl")
    else:
        empty = {"path": "travel-method.jsonl", "sha256": hashlib.sha256(b"").hexdigest(), "size_bytes": 0}
        manifest["files"] = sorted([*manifest["files"], empty], key=lambda entry: entry["path"])
        _put(store, frozen, "catalog/travel-method.jsonl", b"")
    _put(store, frozen, "catalog/manifest.json", json.dumps(manifest).encode())
    with pytest.raises(IntegrityError, match="^Frozen catalog file set is invalid$"):
        validate_frozen_environment(store.run_dir, frozen, [], [])


@pytest.mark.parametrize("name", ["app-catalog.jsonl", "map.jsonl"])
@pytest.mark.parametrize("republished", [False, True], ids=["bytes-only", "with-file-record"])
def test_changed_frozen_catalog_byte_is_caught_by_file_identity_or_catalog_digest(
        engine_factory, tmp_path, name, republished):
    """改一个冻结目录字节：文件记录没跟着改时文件身份不符，跟着改了则目录摘要不符。"""
    engine = _engine_with(engine_factory, tmp_path, {"map.jsonl": MAP_ROW})
    store = engine.context.store
    frozen = copy.deepcopy(store.manifest)
    original = store.run_dir / "assets" / frozen["environment_assets"][f"catalog/{name}"]["sha256"]
    changed = original.read_bytes() + b" "
    _put(store, frozen, f"catalog/{name}", changed)
    if republished:
        manifest = _frozen_manifest(store, frozen)
        entry = next(item for item in manifest["files"] if item["path"] == name)
        entry.update(sha256=hashlib.sha256(changed).hexdigest(), size_bytes=len(changed))
        _put(store, frozen, "catalog/manifest.json", json.dumps(manifest).encode())
    expected = "Frozen catalog content digest is invalid" if republished else "Frozen catalog file identity is invalid"
    with pytest.raises(IntegrityError, match=f"^{expected}$"):
        validate_frozen_environment(store.run_dir, frozen, [], [])
