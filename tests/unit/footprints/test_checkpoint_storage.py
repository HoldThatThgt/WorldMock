"""检查点行的存储形式：正文是规范载荷的 zlib 流（级别 1），record_sha256 是未压缩载荷的摘要；读取先解压再核对，
行不存在、正文不是二进制、解不开或摘要不符都按损坏的记录拒绝（docs/design/05-recording-engine.md
「冻结输入、检查点与分支」）。"""

import hashlib
import logging
import zlib

import pytest

from worldmock.foundation.errors import IntegrityError
from worldmock.foundation.serialization import canonical_json
from worldmock.recording_engine.storage.store import FootprintStore

#: 损坏或缺失的检查点记录的错误原文。
_DAMAGED = "^Checkpoint is missing or its record is damaged$"


@pytest.fixture
def store(tmp_path):
    value = FootprintStore.create(tmp_path / 'parent',
                                  {'run_id': 'parent', 'agent_ids': [1], 'code': 'frozen', 'environment_assets': {}},
                                  {'version': 0, 'current_time': 100, 'balance': 50})
    yield value
    value.close()


def _point(store):
    """以同一份占位素材提交一个共同检查点。"""
    reference = store.assets.put_json({'memory': 'saved'}, {'kind': 'agent'})
    return store.checkpoint(store.state, {'agent:1': reference, 'life': reference})


def _row(store, checkpoint_id: str) -> tuple:
    """检查点行的（正文, record_sha256）。"""
    return store.connection.execute('SELECT body, record_sha256 FROM checkpoints WHERE checkpoint_id=?',
                                    (checkpoint_id,)).fetchone()


def test_checkpoint_row_is_the_zlib_stream_of_the_canonical_payload(store):
    """行正文是二进制的 zlib 流，头两字节 78 01；解压后恰为检查点载荷的规范 JSON，它的 SHA-256 就是记录摘要与列里的
    record_sha256；读回的检查点与写入时返回的逐字段相同。"""
    point = _point(store)
    body, digest = _row(store, point.checkpoint_id)
    assert isinstance(body, bytes) and body[:2] == b'\x78\x01'
    payload = zlib.decompress(body)
    assert payload == canonical_json(point.model_dump(exclude={'record_sha256'}))
    assert hashlib.sha256(payload).hexdigest() == point.record_sha256 == digest
    assert store.get_checkpoint(point.checkpoint_id) == point


def _rewrite(store, checkpoint_id: str, body, digest: str) -> None:
    """去掉不可更新的触发器后改写这一行。"""
    store.connection.execute('DROP TRIGGER checkpoints_no_update')
    with store.connection:
        store.connection.execute('UPDATE checkpoints SET body=?, record_sha256=? WHERE checkpoint_id=?',
                                 (body, digest, checkpoint_id))


@pytest.mark.parametrize('damage,logged', [
    ('text', 'body is not a compressed record'),
    ('truncated', 'does not decompress'),
    ('other-payload', 'record hash does not match'),
    ('zero-digest', 'record hash does not match'),
])
def test_damaged_checkpoint_rows_are_refused(store, caplog, damage, logged):
    """早期代码写下的文本正文、截断的 zlib 流、别的载荷的 zlib 流配原摘要，以及原正文配全零摘要：读取都以损坏的记录
    拒绝，并写出各自的英文错误日志；不存在的检查点同样拒绝并记下它不存在。"""
    point = _point(store)
    body, digest = _row(store, point.checkpoint_id)
    payload = zlib.decompress(body)
    other = canonical_json({**point.model_dump(exclude={'record_sha256'}), 'current_time': 999})
    rewritten = {'text': (payload.decode(), digest), 'truncated': (body[:-5], digest),
                 'other-payload': (zlib.compress(other, 1), digest), 'zero-digest': (body, '0' * 64)}[damage]
    _rewrite(store, point.checkpoint_id, *rewritten)
    caplog.set_level(logging.ERROR)
    with pytest.raises(IntegrityError, match=_DAMAGED):
        store.get_checkpoint(point.checkpoint_id)
    assert logged in caplog.text
    caplog.clear()
    with pytest.raises(IntegrityError, match=_DAMAGED):
        store.get_checkpoint('no-such-checkpoint')
    assert 'does not exist' in caplog.text
