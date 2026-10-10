"""真实完整导出认证哈希、截止、覆盖与质量重算。"""

import hashlib
import json

import pytest

from tests.integration.footprints.test_acs_export import real_export
from worldmock.errors import IntegrityError
from worldmock.footprints.validation import validate_export


@pytest.fixture
def complete_export(real_export):
    return real_export


def test_export_accepts_actual_complete_authenticated_run(complete_export):
    manifest, events, activities = validate_export(complete_export)
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
