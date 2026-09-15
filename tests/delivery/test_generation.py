import hashlib
import json

import pytest

from worldmock.delivery.generation import verify_generation
from worldmock.errors import IntegrityError


def committed_output(directory):
    digest = '96756efb1cf4c8ffa9769d2aff83e66125327adaf3d7b5a6472dda4e319ee038'
    manifest = {'schema_version': 1, 'run_id': 'fixture-run',
                'delivery_digest': digest, 'artifacts_committed': True}
    contents = {'main': b'{"summary":"Fixture"}\n', 'stream': b'{"payload":"Fixture"}\n',
                'report': json.dumps({'generate': {'sequence': {'run_id': 'fixture-run',
                                                               'delivery_digest': digest}}}).encode()}
    for name, raw in contents.items():
        path = directory / f'{name}.json'
        path.write_bytes(raw)
        manifest[name] = {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest()}
        if name != 'report': manifest[name]['rows'] = 1
    (directory / 'rules.manifest.json').write_text(json.dumps(manifest))
    return manifest


def test_committed_sequence_digest_is_verified_from_actual_bytes(tmp_path):
    committed_output(tmp_path)
    manifest, main, stream = verify_generation(tmp_path)
    assert manifest['run_id'] == 'fixture-run'
    assert main == [{'summary': 'Fixture'}]
    assert stream == [{'payload': 'Fixture'}]


@pytest.mark.parametrize('corruption', ['uncommitted', 'hash', 'rows', 'report_identity', 'digest', 'outside'])
def test_invalid_sequence_is_rejected_before_consumer(tmp_path, corruption):
    manifest = committed_output(tmp_path)
    if corruption == 'uncommitted': manifest['artifacts_committed'] = False
    if corruption == 'hash': (tmp_path / 'main.json').write_text('{"summary":"changed"}\n')
    if corruption == 'rows': manifest['stream']['rows'] = 2
    if corruption in {'report_identity', 'digest'}:
        report = {'generate': {'sequence': {'run_id': 'wrong' if corruption == 'report_identity' else 'fixture-run',
                                            'delivery_digest': '0' * 64 if corruption == 'digest'
                                            else manifest['delivery_digest']}}}
        raw = json.dumps(report).encode()
        (tmp_path / 'report.json').write_bytes(raw)
        manifest['report']['sha256'] = hashlib.sha256(raw).hexdigest()
        if corruption == 'digest': manifest['delivery_digest'] = '0' * 64
    if corruption == 'outside': manifest['main']['path'] = str(tmp_path.parent / 'main.json')
    (tmp_path / 'rules.manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(IntegrityError): verify_generation(tmp_path)
