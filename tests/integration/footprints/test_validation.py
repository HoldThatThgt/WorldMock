"""真实完整导出认证哈希、截止、覆盖与质量重算。"""

import hashlib
import json

import pytest

from tests.footprints.recording import artifact_rows, write_manifest, write_rows
from tests.integration.footprints.test_acs_export import real_export
from worldmock.foundation.errors import IntegrityError
from worldmock.recording_engine.validation.validation import validate_export


@pytest.fixture
def complete_export(real_export):
    return real_export


def test_export_accepts_actual_complete_authenticated_run(complete_export):
    manifest, activities = validate_export(complete_export)
    events = artifact_rows(complete_export, "events")
    assert manifest["experiment_completed"] is True and events and activities


@pytest.mark.parametrize('field', ['version', 'committed', 'completed', 'rows', 'path', 'cutoff', 'window',
                                  'coverage', 'source_runs', 'checkpoint_hash', 'missing_asset', 'event_hash'])
def test_incomplete_or_tampered_export_is_rejected(complete_export, field):
    path = complete_export / 'manifest.json'
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if field == 'version': manifest['schema_version'] = 999
    if field == 'committed': manifest['committed'] = False
    if field == 'completed': manifest['experiment_completed'] = False
    if field == 'rows': manifest['artifacts']['events']['rows'] = 999
    if field == 'path': manifest['artifacts']['events']['path'] = 'wrong.jsonl'
    if field == 'cutoff': manifest['cutoff_event_seq'] = 999
    if field == 'window': manifest['window']['end'] = '2026-03-30T00:00:00+00:00'
    if field == 'coverage': manifest['daily_coverage'] = []
    if field == 'source_runs': manifest['source_run_ids'] = ['invented']
    if field == 'checkpoint_hash': manifest['checkpoint']['record_sha256'] = '0' * 64
    if field == 'missing_asset': next((complete_export / 'assets').iterdir()).unlink()
    if field == 'event_hash': (complete_export / 'events.jsonl').write_text('{}\n')
    path.write_text(json.dumps(manifest))
    with pytest.raises(IntegrityError): validate_export(complete_export)


@pytest.mark.parametrize('rehash', [False, True])
def test_quality_report_requires_bytes_and_actual_event_recomputation(complete_export, rehash):
    path = complete_export / 'quality.json'
    quality = json.loads(path.read_text(encoding="utf-8"))
    quality['actions']['unique_action_ids'] = 999
    path.write_text(json.dumps(quality))
    if rehash:
        manifest_path = complete_export / 'manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest['quality_report'].update(size_bytes=path.stat().st_size,
                                           sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(IntegrityError): validate_export(complete_export)


@pytest.mark.parametrize("alteration", ["version_two", "missing_app_usage", "extra_artifact"])
def test_export_rejects_retired_version_or_noncurrent_artifact_set(complete_export, alteration):
    """@brief 当前五文件合同是封闭集合；旧版本、遗漏和额外文件都不能进入下游。"""
    path = complete_export / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if alteration == "version_two":
        manifest["schema_version"] = 2
    elif alteration == "missing_app_usage":
        manifest["artifacts"].pop("app_usage")
    else:
        manifest["artifacts"]["invented"] = dict(manifest["artifacts"]["events"])
    path.write_text(json.dumps(manifest))
    with pytest.raises(IntegrityError):
        validate_export(complete_export)


# ---- 两遍读取的导出认证（长时运行成本第四章） ----


def _forge_observation(directory, **fields):
    """给第一条观察的 data 加上字段，按真实字节重签 events.jsonl 并写回清单。"""
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    rows = artifact_rows(directory, "events")
    observation = next(row for row in rows if row["event_type"] == "observation")
    observation["data"].update(fields)
    write_rows(directory, manifest, "events", rows)
    write_manifest(directory, manifest)


def test_forged_observation_with_initial_funds_kind_is_rejected(complete_export):
    _forge_observation(complete_export, kind="initial_funds_injected")
    with pytest.raises(IntegrityError, match="^Complete account history requires one initial funding event$"):
        validate_export(complete_export)


def test_forged_observation_with_a_retired_activity_snapshot_is_rejected(complete_export):
    _forge_observation(complete_export, activity={"activity_id": "x", "agent_id": 1, "kind": "wait"})
    with pytest.raises(IntegrityError, match="^Activity snapshot uses a retired activity kind$"):
        validate_export(complete_export)


def test_tampered_events_with_an_invalid_line_report_the_hash_first(complete_export):
    with (complete_export / "events.jsonl").open("ab") as stream:
        stream.write(b"not json\n")
    with pytest.raises(Exception) as failure:
        validate_export(complete_export)
    assert (type(failure.value), str(failure.value)) == (IntegrityError, "Export artifact hash is invalid")


def test_events_changed_between_the_two_reads_are_rejected(complete_export, monkeypatch, caplog):
    from worldmock.recording_engine.validation import validation

    original = validation._authenticated_events
    path = complete_export / "events.jsonl"

    def authenticated_then_rewritten(root, descriptor):
        digest = original(root, descriptor)
        raw = path.read_bytes()
        end = raw.index(b"\n")
        path.write_bytes(raw[:end] + b" " + raw[end:])
        return digest

    monkeypatch.setattr(validation, "_authenticated_events", authenticated_then_rewritten)
    with pytest.raises(IntegrityError, match="^Export artifact hash is invalid$"):
        validate_export(complete_export)
    assert "Export events changed between the two reads" in caplog.messages


def test_row_count_uses_the_same_line_split_as_before(complete_export):
    path = complete_export / "events.jsonl"
    raw = path.read_bytes()
    raw = raw[:10] + b"\r" + raw[10:]
    path.write_bytes(raw)
    manifest = json.loads((complete_export / "manifest.json").read_text(encoding="utf-8"))
    manifest["artifacts"]["events"].update(sha256=hashlib.sha256(raw).hexdigest(), rows=len(raw.splitlines()))
    write_manifest(complete_export, manifest)
    with pytest.raises(json.JSONDecodeError) as before:
        [json.loads(line) for line in raw.splitlines()]
    with pytest.raises(json.JSONDecodeError) as failure:
        validate_export(complete_export)
    assert str(failure.value) == str(before.value)
