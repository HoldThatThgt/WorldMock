from worldmock.footprints.checkpoint import restore_branch
import json
import hashlib
import sqlite3
from pathlib import Path

import pytest

from worldmock.errors import IntegrityError
from worldmock.footprints.assets import AssetStore
from worldmock.footprints.checkpoint import restore_branch, restore_directory, snapshot_directory
from worldmock.footprints.models import EventDraft, canonical_json
from worldmock.footprints.store import FootprintStore


@pytest.fixture
def store(tmp_path):
    value = FootprintStore.create(tmp_path / 'parent',
                                  {'run_id': 'parent', 'agent_ids': [1], 'code': 'frozen', 'environment_assets': {}},
                                  {'version': 0, 'current_time': 100, 'balance': 50})
    yield value
    value.close()


def test_state_and_events_commit_together_and_reject_same_version_mutation(store):
    event = EventDraft(event_type='action_result', occurred_at=110, data={'paid': 5})
    state = {'version': 1, 'current_time': 110, 'balance': 45}
    saved = store.commit(state, [event])
    assert store.state == state
    assert saved[0].event_seq == 1
    assert (saved[0].state_version_before, saved[0].state_version_after) == (0, 1)
    with pytest.raises(IntegrityError):
        store.commit({**state, 'balance': 900}, [])
    assert store.state == state
    assert store.last_event_seq == 1


def test_same_world_observations_share_version_and_preserve_duplicate_behavior(store):
    drafts = [EventDraft(event_type='observation', occurred_at=100, data={'title': 'same'}) for _ in range(2)]
    result = store.commit(store.state, drafts)
    assert [event.event_seq for event in result] == [1, 2]
    assert result[0].event_id != result[1].event_id
    assert {event.state_version_after for event in result} == {0}


def test_agent_event_queries_respect_type_person_and_exclusive_boundary(store):
    drafts = [EventDraft(event_type=kind, occurred_at=100, agent_id=agent)
              for kind, agent in [('action_result', 1), ('observation', 1), ('action_result', 2),
                                  ('model_response', 1), ('action_result', 1), ('action_result', 1)]]
    store.commit(store.state, drafts)
    boundary = store.last_agent_event_seq('observation', 1)
    assert boundary == 2
    assert store.last_agent_event_seq('observation', 2) == 0
    assert [event.event_seq for event in store.read_agent_events('action_result', 1, boundary)] == [5, 6]
    query = """EXPLAIN QUERY PLAN SELECT body FROM events WHERE json_extract(body,'$.agent_id')=?
               AND json_extract(body,'$.event_type')=? AND event_seq>? ORDER BY event_seq"""
    plan = list(store.connection.execute(query, (1, 'action_result', boundary)))
    assert any('events_agent_type' in row[-1] for row in plan)


def test_action_request_cutoff_counts_only_thinking_requests_with_the_action_tools(store):
    """行动请求只看结构：不带 purpose、thinking 开启、原生 tools 含 start_activity；零点计划、没有工具或未开思考的
    请求都不算，提示文本不参与判定（docs/design/06-agent.md 第二章「单一行动阶段」）。"""
    def request(agent_id: int = 1, *, thinking: str = 'enabled', tools=('start_activity',), **data) -> EventDraft:
        body = {'messages': [{'role': 'user', 'content': '{"progress_layer": {}}'}],
                'extra_body': {'thinking': {'type': thinking}},
                'tools': [{'type': 'function', 'function': {'name': name}} for name in tools]}
        return EventDraft(event_type='model_request', occurred_at=100, agent_id=agent_id,
                          data={'phase': 'request', 'request': body, **data})

    store.commit(store.state, [request(),
                               request(purpose='plan_day', call_id='plan-day'),
                               request(thinking='disabled'),
                               request(tools=('answer_question',)),
                               request(2, tools=('observe', 'start_activity'))])

    assert store.last_agent_action_request_seq(1) == 1
    assert store.last_agent_action_request_seq(2) == 5
    assert store.last_agent_action_request_seq(3) == 0


def test_last_event_seq_before_bounds_by_occurrence_time(store):
    """按时间取区间下界：发生时刻严格早于给定时刻的最后一个事件序号，没有时为 0。"""
    drafts = [EventDraft(event_type='environment_event', occurred_at=moment, data={'kind': 'probe'})
              for moment in (100, 100, 120)]
    store.commit({'version': 1, 'current_time': 120, 'balance': 50}, drafts)

    assert store.last_event_seq_before(100) == 0
    assert store.last_event_seq_before(101) == 2
    assert store.last_event_seq_before(120) == 2
    assert store.last_event_seq_before(121) == 3


def test_commit_last_seq_groups_one_versioned_commit_without_later_same_version_appends(store):
    """一次提交的末事件序号（docs/design/06-agent.md 第三章「归属」）：推进版本的提交的每个事件都指向该提交的末事件；
    其后同版本追加的观察与下一次提交不算在内；最后一次提交止于当前末事件；不存在的参照事件是完整性错误。"""
    probe = {'kind': 'probe'}
    store.commit({'version': 1, 'current_time': 110, 'balance': 50},
                 [EventDraft(event_type='environment_event', occurred_at=110, data=probe) for _ in range(3)])
    store.commit(store.state, [EventDraft(event_type='observation', occurred_at=110, data=probe)])
    store.commit({'version': 2, 'current_time': 120, 'balance': 50},
                 [EventDraft(event_type='environment_event', occurred_at=120, data=probe) for _ in range(2)])

    assert [store.commit_last_seq(seq) for seq in (1, 2, 3)] == [3, 3, 3]
    assert [store.commit_last_seq(seq) for seq in (5, 6)] == [6, 6]
    with pytest.raises(IntegrityError, match='Commit reference event does not exist'):
        store.commit_last_seq(7)


def test_world_change_without_any_event_cannot_be_committed(store):
    with pytest.raises(IntegrityError):
        store.commit({'version': 1, 'current_time': 200, 'balance': 900}, [])
    assert store.state == {'version': 0, 'current_time': 100, 'balance': 50}
    assert store.last_event_seq == 0


@pytest.mark.parametrize('activity_id', [None, 'other'])
def test_inconsistent_activity_identity_fails_before_world_commit(store, activity_id):
    draft = EventDraft(event_type='action_accepted', occurred_at=110, activity_id=activity_id,
                       data={'activity': {'activity_id': 'actual'}})
    with pytest.raises(IntegrityError):
        store.commit({'version': 1, 'current_time': 110, 'balance': 45}, [draft])
    assert store.state['version'] == 0 and store.state['balance'] == 50
    assert store.last_event_seq == 0


def test_sql_failure_rolls_back_money_and_all_events(store, monkeypatch):
    original = store._insert_event
    calls = []
    def fail_second(event):
        calls.append(event)
        original(event)
        if len(calls) == 2:
            raise sqlite3.OperationalError('Injected disk failure')
    monkeypatch.setattr(store, '_insert_event', fail_second)
    events = [EventDraft(event_type='action_result', occurred_at=110) for _ in range(2)]
    with pytest.raises(sqlite3.OperationalError):
        store.commit({'version': 1, 'current_time': 110, 'balance': 30}, events)
    assert store.state['balance'] == 50
    assert store.last_event_seq == 0


@pytest.mark.parametrize('change', ['future', 'backwards', 'cause', 'observed_future'])
def test_invalid_time_and_causal_references_fail_without_state_change(store, change):
    fields = {'event_type': 'observation', 'occurred_at': 100}
    if change == 'future': fields['occurred_at'] = 101
    if change == 'backwards': fields['occurred_at'] = 99
    if change == 'cause': fields['caused_by'] = ['absent']
    if change == 'observed_future': fields['observed_at'] = 101
    with pytest.raises(IntegrityError):
        store.commit(store.state, [EventDraft(**fields)])
    assert store.last_event_seq == 0


def test_cause_in_same_transaction_and_immutable_database(store):
    first = EventDraft(event_type='action_requested', occurred_at=100)
    second = EventDraft(event_type='action_accepted', occurred_at=100, caused_by=[first.event_id])
    store.commit(store.state, [first, second])
    assert store.read_events(2)[1].caused_by == [first.event_id]
    with pytest.raises(sqlite3.IntegrityError):
        store.connection.execute('DELETE FROM events')
    with pytest.raises(IntegrityError):
        store.commit(store.state, [first])


def test_missing_or_damaged_asset_prevents_commit_and_checkpoint(store):
    reference = store.assets.put(b'evidence', {'media_type': 'text/plain', 'source': {'kind': 'fixture'}})
    (store.assets.root / reference.sha256).write_bytes(b'corrupt')
    event = EventDraft(event_type='model_response', occurred_at=100, artifact_refs=[reference])
    with pytest.raises(IntegrityError): store.commit(store.state, [event])
    with pytest.raises(IntegrityError): store.checkpoint(store.state, {'agent:1': reference, 'life': reference})
    assert store.last_event_seq == 0
    assert store.connection.execute('SELECT COUNT(*) FROM checkpoints').fetchone()[0] == 0


def test_complete_checkpoint_branch_preserves_parent_and_exact_workspace(store, tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    (workspace / 'MEMORY.md').write_text('earlier memory')
    reference = snapshot_directory(workspace, store.assets)
    point = store.checkpoint(store.state, {'agent:1': reference, 'life': reference})
    (workspace / 'MEMORY.md').write_text('later uncommitted memory')
    store.commit({'version': 1, 'current_time': 200, 'balance': 10},
                 [EventDraft(event_type='action_result', occurred_at=200)])
    before = {str(p.relative_to(store.run_dir)): p.read_bytes() for p in store.run_dir.rglob('*') if p.is_file()}
    child = restore_branch(store, point.checkpoint_id, tmp_path / 'child')
    try:
        assert child.state == {'version': 1, 'current_time': 100, 'balance': 50,
                               'run_id': child.manifest['run_id']}
        assert child.manifest['parent_run_id'] == 'parent'
        assert child.manifest['run_id'] != 'parent'
        assert child.last_event_seq == point.last_event_seq + 1
        restore_directory(reference, child.assets, tmp_path / 'restored-agent')
        assert (tmp_path / 'restored-agent' / 'MEMORY.md').read_text() == 'earlier memory'
        after = {str(p.relative_to(store.run_dir)): p.read_bytes() for p in store.run_dir.rglob('*') if p.is_file()}
        assert before == after
    finally:
        child.close()


def test_incomplete_checkpoint_never_creates_child(store, tmp_path):
    reference = store.assets.put_json({'memory': 'saved'}, {'kind': 'agent'})
    point = store.checkpoint(store.state, {'agent:1': reference, 'life': reference})
    (store.assets.root / reference.sha256).unlink()
    with pytest.raises(IntegrityError):
        restore_branch(store, point.checkpoint_id, tmp_path / 'must-not-exist')
    assert not (tmp_path / 'must-not-exist').exists()


def test_failed_checkpoint_transaction_does_not_publish_record(store, monkeypatch):
    reference = store.assets.put_json({'memory': 'saved'}, {'kind': 'agent'})
    def fail(event): raise sqlite3.OperationalError('Injected checkpoint failure')
    monkeypatch.setattr(store, '_insert_event', fail)
    with pytest.raises(sqlite3.OperationalError):
        store.checkpoint(store.state, {'agent:1': reference, 'life': reference})
    assert store.connection.execute('SELECT COUNT(*) FROM checkpoints').fetchone()[0] == 0
    assert store.last_event_seq == 0


@pytest.mark.parametrize('field,value', [('run_id', 'another-run'), ('current_time', 999), ('state_version', 10)])
def test_rehashed_checkpoint_with_inconsistent_world_is_rejected(store, tmp_path, field, value):
    reference = store.assets.put_json({'memory': 'saved'}, {'kind': 'agent'})
    point = store.checkpoint(store.state, {'agent:1': reference, 'life': reference})
    payload = point.model_dump(exclude={'record_sha256'})
    payload[field] = value
    raw = canonical_json(payload)
    store.connection.execute('DROP TRIGGER checkpoints_no_update')
    with store.connection:
        store.connection.execute('UPDATE checkpoints SET body=?,record_sha256=?',
                                 (raw.decode(), hashlib.sha256(raw).hexdigest()))
    with pytest.raises(IntegrityError):
        restore_branch(store, point.checkpoint_id, tmp_path / 'child')
    assert not (tmp_path / 'child').exists()


@pytest.mark.parametrize('field,value', [('event_type', 'observation'), ('occurred_at', 99),
                                       ('state_version_after', 1), ('run_id', 'another-run')])
def test_checkpoint_event_must_match_committed_identity_and_clock(store, field, value):
    reference = store.assets.put_json({'memory': 'saved'}, {'kind': 'agent'})
    point = store.checkpoint(store.state, {'agent:1': reference, 'life': reference})
    event = store.read_events(point.last_event_seq)[-1].model_dump()
    event[field] = value
    store.connection.execute('DROP TRIGGER events_no_update')
    with store.connection:
        store.connection.execute('UPDATE events SET body=?', (canonical_json(event).decode(),))
    with pytest.raises(IntegrityError):
        store.get_checkpoint(point.checkpoint_id)


def test_credentials_are_redacted_without_losing_model_task(monkeypatch):
    from worldmock.footprints.evidence import redact_evidence
    monkeypatch.setenv('DEEPSEEK_NODE_KEYS', 'private-credential-value')
    original = {'headers': {'Authorization': 'Bearer private-credential-value'},
                'messages': [{'role': 'user', 'content': 'Read the actual meeting transcript'}],
                'failure': 'Request private-credential-value failed'}
    result = redact_evidence(original)
    assert 'private-credential-value' not in json.dumps(result)
    assert result['messages'] == original['messages']
    assert original['headers']['Authorization'] == 'Bearer private-credential-value'


@pytest.mark.parametrize('bad_state', [
    {'version': 0, 'current_time': 'soon'},
    {'version': True, 'current_time': 100},
    {'version': 0, 'current_time': 100.5},
])
def test_create_rejects_non_integer_initial_clock_or_version(tmp_path, bad_state):
    """先红后绿回归（refute P0：写入路径不校验）——create 是检查点恢复分支的落盘入口。

    机制论证（为什么是缺陷不是特征）：commit 的 _validate_state 已要求 version 与
    current_time 为整数（store.py 结构合同），而 create 对同两个字段只查 version>=0 与
    current_time 键存在，非整数初始状态被静默持久化，直到下次加载才暴露——与
    FootprintStore 合同（docs/design/09-io-formats.md「数字足迹存储接口」）的写入路径校验口径不一致。
    """
    manifest = {'run_id': 'bad-shape', 'agent_ids': [1], 'code': 'frozen'}
    with pytest.raises(IntegrityError):
        FootprintStore.create(tmp_path / 'bad-run', manifest, bad_state)
