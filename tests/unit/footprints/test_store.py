from worldmock.recording_engine.storage.checkpoint import restore_branch
import json
import hashlib
import sqlite3
import zlib
from pathlib import Path

import pytest

from worldmock.foundation.errors import IntegrityError
from worldmock.recording_engine.storage.assets import AssetStore
from worldmock.recording_engine.storage.checkpoint import restore_branch, restore_directory, snapshot_directory
from worldmock.recording_engine.events.models import EventDraft
from worldmock.foundation.serialization import canonical_json
from worldmock.recording_engine.storage.store import FootprintStore


def _outcome(write):
    """执行一次写入：成功时返回事件序号，被拒时返回错误文本，两种结局都能直接比较。"""
    try:
        return [event.event_seq for event in write()]
    except IntegrityError as error:
        return str(error)


@pytest.fixture
def store(tmp_path):
    value = FootprintStore.create(tmp_path / 'parent',
                                  {'run_id': 'parent', 'agent_ids': [1], 'code': 'frozen', 'environment_assets': {},
                                   'lineage': [], 'window': {'start': 100, 'end': 1000, 'timezone': 'UTC'}},
                                  {'version': 0, 'current_time': 100, 'balance': 50})
    yield value
    value.close()


def test_state_and_events_commit_together_and_reject_same_version_mutation(store):
    event = EventDraft(event_type='action_result', occurred_at=110, data={'paid': 5})
    state = {'version': 1, 'current_time': 110, 'balance': 45}
    saved = store.commit(store.state, state, [event])
    assert store.state == state
    assert saved[0].event_seq == 1
    assert (saved[0].state_version_before, saved[0].state_version_after) == (0, 1)
    with pytest.raises(IntegrityError):
        store.commit(state, {**state, 'balance': 900}, [])
    assert store.state == state
    assert store.last_event_seq == 1


def test_same_world_observations_share_version_and_preserve_duplicate_behavior(store):
    drafts = [EventDraft(event_type='observation', occurred_at=100, data={'title': 'same'}) for _ in range(2)]
    result = store.append_evidence(drafts)
    assert [event.event_seq for event in result] == [1, 2]
    assert result[0].event_id != result[1].event_id
    assert {event.state_version_after for event in result} == {0}


def test_agent_event_queries_respect_type_person_and_exclusive_boundary(store):
    drafts = [EventDraft(event_type=kind, occurred_at=100, agent_id=agent)
              for kind, agent in [('action_result', 1), ('observation', 1), ('action_result', 2),
                                  ('model_response', 1), ('action_result', 1), ('action_result', 1)]]
    store.append_evidence(drafts)
    boundary = store.last_agent_event_seq('observation', 1)
    assert boundary == 2
    assert store.last_agent_event_seq('observation', 2) == 0
    assert [event.event_seq for event in store.read_agent_events('action_result', 1, boundary)] == [5, 6]
    query = """EXPLAIN QUERY PLAN SELECT body FROM events WHERE json_extract(body,'$.agent_id')=?
               AND json_extract(body,'$.event_type')=? AND event_seq>? ORDER BY event_seq"""
    plan = list(store.connection.execute(query, (1, 'action_result', boundary)))
    assert any('events_agent_type' in row[-1] for row in plan)


def test_action_request_cutoff_counts_only_thinking_requests_with_the_action_tools(store):
    """行动请求只看结构：不带 purpose、thinking 开启、原生 tools 含 start_main_activity；零点计划、没有该工具（只有
    start_side_activity 也不算）或未开思考的请求都不算，提示文本不参与判定（docs/design/06-agent.md 第二章「单一行动阶段」）。"""
    def request(agent_id: int = 1, *, thinking: str = 'enabled', tools=('start_main_activity',), **data) -> EventDraft:
        body = {'messages': [{'role': 'user', 'content': '{"progress_layer": {}}'}],
                'extra_body': {'thinking': {'type': thinking}},
                'tools': [{'type': 'function', 'function': {'name': name}} for name in tools]}
        return EventDraft(event_type='model_request', occurred_at=100, agent_id=agent_id,
                          data={'phase': 'request', 'request': body, **data})

    store.append_evidence([request(),
                           request(purpose='plan_day', call_id='plan-day'),
                           request(thinking='disabled'),
                           request(tools=('answer_question',)),
                           request(tools=('start_side_activity',)),
                           request(2, tools=('observe', 'start_main_activity', 'start_side_activity'))])

    assert store.last_agent_action_request_seq(1) == 1
    assert store.last_agent_action_request_seq(2) == 6
    assert store.last_agent_action_request_seq(3) == 0


def test_last_event_seq_before_bounds_by_occurrence_time(store):
    """按时间取区间下界：发生时刻严格早于给定时刻的最后一个事件序号，没有时为 0；空库与跨多次提交的同刻事件同样成立。"""
    assert store.last_event_seq_before(1000) == 0
    drafts = [EventDraft(event_type='environment_event', occurred_at=moment, data={'kind': 'probe'})
              for moment in (100, 100, 120)]
    store.commit(store.state, {'version': 1, 'current_time': 120, 'balance': 50}, drafts)

    assert store.last_event_seq_before(100) == 0
    assert store.last_event_seq_before(101) == 2
    assert store.last_event_seq_before(120) == 2
    assert store.last_event_seq_before(121) == 3
    later = [EventDraft(event_type='environment_event', occurred_at=moment, data={'kind': 'probe'})
             for moment in (120, 120, 130, 130, 130, 150)]
    store.commit(store.state, {'version': 2, 'current_time': 150, 'balance': 50}, later)

    assert [store.last_event_seq_before(moment) for moment in (100, 120, 121, 130, 131, 150, 151)] == [
        0, 2, 5, 5, 8, 8, 9]


def test_decision_observations_match_agent_decision_and_moment_exactly(store):
    """决策观察按人物、决策与发生时刻精确匹配，同一秒的重复观察全部保留供唯一性复核；更早或更晚时刻、别的人物或别的
    决策的观察都不在其中，那一秒没有观察时为空。"""
    def observation(moment: int, decision_id: str, agent_id: int = 1) -> EventDraft:
        return EventDraft(event_type='observation', occurred_at=moment, observed_at=moment, agent_id=agent_id,
                          decision_id=decision_id, data={})

    store.commit(store.state, {'version': 1, 'current_time': 120, 'balance': 50}, [
        observation(100, 'decision-a'), observation(110, 'decision-a'), observation(110, 'decision-b'),
        observation(110, 'decision-a', agent_id=2), observation(110, 'decision-a'), observation(120, 'decision-a'),
    ])

    def seqs(agent_id: int, decision_id: str, moment: int) -> list[int]:
        return [event.event_seq for event in store.read_decision_observations(agent_id, decision_id, moment)]

    assert seqs(1, 'decision-a', 110) == [2, 5]
    assert seqs(1, 'decision-a', 100) == [1]
    assert seqs(1, 'decision-a', 120) == [6]
    assert seqs(2, 'decision-a', 110) == [4]
    assert seqs(1, 'decision-b', 120) == []
    assert seqs(1, 'decision-a', 105) == []


def test_commit_last_seq_groups_one_versioned_commit_without_later_same_version_appends(store):
    """一次提交的末事件序号（docs/design/06-agent.md 第三章「归属」）：推进版本的提交的每个事件都指向该提交的末事件；
    其后同版本追加的观察与下一次提交不算在内；最后一次提交止于当前末事件；不存在的参照事件是完整性错误。"""
    probe = {'kind': 'probe'}
    store.commit(store.state, {'version': 1, 'current_time': 110, 'balance': 50},
                 [EventDraft(event_type='environment_event', occurred_at=110, data=probe) for _ in range(3)])
    store.append_evidence([EventDraft(event_type='observation', occurred_at=110, data=probe)])
    store.commit(store.state, {'version': 2, 'current_time': 120, 'balance': 50},
                 [EventDraft(event_type='environment_event', occurred_at=120, data=probe) for _ in range(2)])

    assert [store.commit_last_seq(seq) for seq in (1, 2, 3)] == [3, 3, 3]
    assert [store.commit_last_seq(seq) for seq in (5, 6)] == [6, 6]
    with pytest.raises(IntegrityError, match='Commit reference event does not exist'):
        store.commit_last_seq(7)


def test_read_event_returns_the_event_and_rejects_an_unknown_identity(store, caplog):
    """按身份读单条事件：读到的就是写入的那一条；库里没有这个身份时写英文错误日志并抛 IntegrityError。"""
    saved = store.commit(store.state, {'version': 1, 'current_time': 110, 'balance': 50}, [
        EventDraft(event_type='action_requested', occurred_at=110, agent_id=1, data={'tool_name': 'probe'}),
        EventDraft(event_type='action_result', occurred_at=110, agent_id=1, data={'status': 'completed'})])
    assert [store.read_event(event.event_id) for event in saved] == saved
    with pytest.raises(IntegrityError) as failure:
        store.read_event('event-unknown')
    assert str(failure.value) == 'Event does not exist'
    assert 'Event event-unknown does not exist' in [record.getMessage() for record in caplog.records]


def test_read_last_event_rejects_an_empty_store_and_returns_latest_without_writes(store, caplog):
    """空库没有最后事件，须记录错误并拒绝；有事件时返回最新一条，两种读取都不改世界或事件。"""
    before = store.state
    with caplog.at_level('ERROR'), pytest.raises(IntegrityError, match='^Run has no committed event$'):
        store.read_last_event()
    assert any(record.levelname == 'ERROR' and record.getMessage() == 'Run has no committed event'
               for record in caplog.records)
    assert store.state == before and store.last_event_seq == 0
    assert list(store.iter_events(0)) == []

    saved = store.append_evidence([
        EventDraft(event_type='environment_event', occurred_at=100, data={'kind': 'earlier'}),
        EventDraft(event_type='environment_event', occurred_at=100, data={'kind': 'latest'}),
    ])
    assert store.read_last_event() == saved[-1]
    assert store.state == before and store.last_event_seq == 2
    assert list(store.iter_events(2)) == saved


def test_world_change_without_any_event_cannot_be_committed(store):
    with pytest.raises(IntegrityError):
        store.commit(store.state, {'version': 1, 'current_time': 200, 'balance': 900}, [])
    assert store.state == {'version': 0, 'current_time': 100, 'balance': 50}
    assert store.last_event_seq == 0


@pytest.mark.parametrize('activity_id', [None, 'other'])
def test_inconsistent_activity_identity_fails_before_world_commit(store, activity_id):
    draft = EventDraft(event_type='action_accepted', occurred_at=110, activity_id=activity_id,
                       data={'activity': {'activity_id': 'actual'}})
    with pytest.raises(IntegrityError):
        store.commit(store.state, {'version': 1, 'current_time': 110, 'balance': 45}, [draft])
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
        store.commit(store.state, {'version': 1, 'current_time': 110, 'balance': 30}, events)
    assert store.state['balance'] == 50
    assert store.last_event_seq == 0
    assert _outcome(lambda: store.commit(store.state, {'version': 1, 'current_time': 110, 'balance': 30},
                                         events)) == [1, 2]
    assert store.state == {'version': 1, 'current_time': 110, 'balance': 30}
    assert store.last_event_seq == 2


@pytest.mark.parametrize('change', ['future', 'backwards', 'cause', 'observed_future'])
def test_invalid_time_and_causal_references_fail_without_state_change(store, change):
    fields = {'event_type': 'observation', 'occurred_at': 100}
    if change == 'future': fields['occurred_at'] = 101
    if change == 'backwards': fields['occurred_at'] = 99
    if change == 'cause': fields['caused_by'] = ['absent']
    if change == 'observed_future': fields['observed_at'] = 101
    with pytest.raises(IntegrityError):
        store.append_evidence([EventDraft(**fields)])
    assert store.last_event_seq == 0


def test_cause_in_same_transaction_and_immutable_database(store):
    first = EventDraft(event_type='action_requested', occurred_at=100)
    second = EventDraft(event_type='action_accepted', occurred_at=100, caused_by=[first.event_id])
    store.append_evidence([first, second])
    assert list(store.iter_events(2))[1].caused_by == [first.event_id]
    with pytest.raises(sqlite3.IntegrityError):
        store.connection.execute('DELETE FROM events')
    with pytest.raises(IntegrityError):
        store.append_evidence([first])


def test_missing_or_damaged_asset_prevents_commit_and_checkpoint(store):
    reference = store.assets.put(b'evidence', {'media_type': 'text/plain', 'source': {'kind': 'fixture'}})
    (store.assets.root / reference.sha256).write_bytes(b'corrupt')
    event = EventDraft(event_type='model_response', occurred_at=100, artifact_refs=[reference])
    with pytest.raises(IntegrityError): store.append_evidence([event])
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
    store.commit(store.state, {'version': 1, 'current_time': 200, 'balance': 10},
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
                                 (zlib.compress(raw), hashlib.sha256(raw).hexdigest()))
    with pytest.raises(IntegrityError, match="disagrees with its world snapshot"):
        restore_branch(store, point.checkpoint_id, tmp_path / 'child')
    assert not (tmp_path / 'child').exists()


@pytest.mark.parametrize('field,value', [('event_type', 'observation'), ('occurred_at', 99),
                                       ('state_version_after', 1), ('run_id', 'another-run')])
def test_checkpoint_event_must_match_committed_identity_and_clock(store, field, value):
    reference = store.assets.put_json({'memory': 'saved'}, {'kind': 'agent'})
    point = store.checkpoint(store.state, {'agent:1': reference, 'life': reference})
    event = list(store.iter_events(point.last_event_seq))[-1].model_dump()
    event[field] = value
    store.connection.execute('DROP TRIGGER events_no_update')
    with store.connection:
        store.connection.execute('UPDATE events SET body=?', (canonical_json(event).decode(),))
    with pytest.raises(IntegrityError):
        store.get_checkpoint(point.checkpoint_id)


def test_credentials_are_redacted_without_losing_model_task(monkeypatch):
    from worldmock.recording_engine.events.model_evidence import redact_evidence
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

    机制论证（为什么是缺陷不是特征）：commit 的版本步核对已要求 version 与
    current_time 为整数（store.py 结构合同），而 create 对同两个字段只查 version>=0 与
    current_time 键存在，非整数初始状态被静默持久化，直到下次加载才暴露——与
    FootprintStore 合同（docs/design/09-io-formats.md「数字足迹存储接口」）的写入路径校验口径不一致。
    """
    manifest = {'run_id': 'bad-shape', 'agent_ids': [1], 'code': 'frozen'}
    with pytest.raises(IntegrityError):
        FootprintStore.create(tmp_path / 'bad-run', manifest, bad_state)


# ---- 整段历史的分页读取与按索引读取（长时运行成本第四章） ----


def _events(store, count, **fields):
    """追加 count 条同版本证据事件，返回写下的事件。"""
    return store.append_evidence([EventDraft(event_type='action_result', occurred_at=100, agent_id=1, **fields)
                                  for _ in range(count)])


def test_iter_events_pages_by_sequence_and_rejects_an_outside_range(store, monkeypatch, caplog):
    from tests.footprints.statement_log import StatementLog
    monkeypatch.setattr('worldmock.recording_engine.storage.store.EVENT_PAGE_SIZE', 2)
    written = _events(store, 5)
    log = StatementLog(store.connection)
    monkeypatch.setattr(store, 'connection', log)
    page = 'SELECT event_seq,event_id,body FROM events WHERE event_seq>?'
    assert list(store.iter_events(5)) == written
    assert [parameters for sql, parameters in log.statements if sql.startswith(page)] == [(0, 2), (2, 4), (4, 5)]
    assert list(store.iter_events(5, 2)) == written[2:]
    for after, cutoff in [(0, 6), (-1, 3), (4, 3)]:
        log.statements.clear()
        caplog.clear()
        with pytest.raises(IntegrityError, match='^Event cutoff is outside the committed sequence$'):
            store.iter_events(cutoff, after)
        assert f'Event range ({after}, {cutoff}] is outside the committed sequence' in caplog.messages
        assert not [sql for sql, _ in log.statements if sql.startswith(page)]


def test_paged_reads_reject_a_row_that_disagrees_with_its_body(store, caplog):
    (first,) = _events(store, 1)
    body = first.model_dump()
    body.update(event_seq=2, event_id='body-identity')
    with store.connection:
        store.connection.execute('INSERT INTO events VALUES(?,?,?)',
                                 (2, 'column-identity', canonical_json(body).decode()))
    assert list(store.iter_events(1)) == [first]
    with pytest.raises(IntegrityError, match='^Event row disagrees with its body$'):
        list(store.iter_events(2))
    assert 'Event row 2 disagrees with its body' in caplog.messages


def test_has_event_reads_the_type_index_and_stops_at_the_first_match(store, monkeypatch):
    from tests.footprints.statement_log import StatementLog
    assert store.has_event('run_failed') is False
    store.append_evidence([EventDraft(event_type='run_failed', occurred_at=100, visibility='system',
                                      data={'error_type': 'RuntimeError', 'error': 'boom', 'round_id': None})])
    log = StatementLog(store.connection)
    monkeypatch.setattr(store, 'connection', log)
    assert store.has_event('run_failed') is True
    assert store.has_event('observation') is False
    sql, parameters = log.statements[0]
    assert sql.endswith('LIMIT 1')
    assert 'USING COVERING INDEX events_agent_type' in log.plan(sql, parameters)


def test_find_event_returns_none_for_an_unknown_identity(store, caplog):
    (first,) = _events(store, 1)
    assert store.find_event(first.event_id) == first
    assert store.find_event('event-unknown') is None
    with pytest.raises(IntegrityError, match='^Event does not exist$'):
        store.read_event('event-unknown')
    assert 'Event event-unknown does not exist' in caplog.messages


def test_first_own_event_seq_walks_back_over_the_branch_suffix(store, tmp_path):
    _events(store, 2)
    assert store.first_own_event_seq() == 1
    reference = store.assets.put_json({'memory': 'saved'}, {'kind': 'agent'})
    point = store.checkpoint(store.state, {'agent:1': reference, 'life': reference})
    child = restore_branch(store, point.checkpoint_id, tmp_path / 'child')
    try:
        branch = list(child.iter_events(child.last_event_seq))[-1]
        assert branch.data['kind'] == 'branch_created' and branch.event_seq == point.last_event_seq + 1
        assert child.first_own_event_seq() == branch.event_seq
        child.manifest['run_id'] = 'another-run'
        assert child.first_own_event_seq() == child.last_event_seq + 1
    finally:
        child.close()


def test_first_action_result_is_the_earliest_result_after_the_boundary(store, caplog):
    def result(action_id, event_type='action_result'):
        return EventDraft(event_type=event_type, occurred_at=100, agent_id=1, action_id=action_id)
    store.append_evidence([result('a', 'action_requested'), result('b'), result('a'), result('b'),
                           result('a', 'observation'), result('a')])
    assert store.first_action_result_seq(1, 'a', 0) == 3
    assert store.first_action_result_seq(1, 'a', 3) == 6
    assert store.first_action_result_seq(1, 'a', 6) is None
    assert store.first_action_result_seq(2, 'a', 0) is None
    assert store.first_action_result_seq(1, 'unknown', 0) is None
    for after in (-1, store.last_event_seq + 1):
        caplog.clear()
        with pytest.raises(IntegrityError, match='^Agent event boundary is outside the committed sequence$'):
            store.first_action_result_seq(1, 'a', after)
        assert f'Agent event boundary {after} is outside the committed sequence' in caplog.messages


#: 第四章之前的经历候选语句（OR 一次取全部候选再倒序排），用来核对两段索引子查询的合并结果。
_EXPERIENCE_SOURCES_WITH_OR = """
    SELECT body FROM events WHERE json_extract(body,'$.agent_id')=?
    AND (json_extract(body,'$.event_type')='activity_progress'
         OR (json_extract(body,'$.event_type')='environment_event'
             AND json_extract(body,'$.data.kind')='goods_received'))
    AND event_seq<? ORDER BY event_seq DESC LIMIT ?
"""


def _experience_receipt(agent_id, number):
    """一条收货环境事件（与已核验经历测试同一载荷）。"""
    data = {"kind": "goods_received", "order_id": f"order-{agent_id}-{number}", "payer_id": agent_id,
            "goods_id": "rice", "category": "meal", "quantity": 1, "destination_id": "restaurant",
            "recipient_location_id": "restaurant", "recipient_asleep": False, "provided": 600, "applied": 540,
            "efficiency": 0.9, "resource_flows": []}
    return EventDraft(event_type='environment_event', occurred_at=100, agent_id=agent_id, data=data)


def test_recent_experience_sources_equal_the_single_query_with_or(store):
    from worldmock.recording_engine.events.models import Event
    drafts = []
    for number in range(40):
        activity = {'activity_id': f'activity-{number}', 'agent_id': 1, 'kind': 'sleep'}
        drafts.append(EventDraft(event_type='activity_progress', occurred_at=100, agent_id=1,
                                 activity_id=f'activity-{number}',
                                 data={'interval_start': 90, 'interval_end': 100, 'activity': activity}))
        if number % 4 == 0:
            drafts.append(_experience_receipt(1, number))
            drafts.append(_experience_receipt(2, number))
        if number % 7 == 0:
            drafts.append(EventDraft(event_type='environment_event', occurred_at=100, agent_id=1,
                                     data={'kind': 'activity_paused', 'activity_id': f'activity-{number}'}))
            drafts.append(EventDraft(event_type='action_result', occurred_at=100, agent_id=1))
    store.append_evidence(drafts)
    last = store.last_event_seq
    assert len([draft for draft in drafts if draft.data.get('kind') == 'goods_received' and draft.agent_id == 1]) == 10
    for before_seq in range(1, last + 2):
        for limit in (1, 5, 32):
            rows = store.connection.execute(_EXPERIENCE_SOURCES_WITH_OR, (1, before_seq, limit))
            expected = [Event.model_validate_json(row[0]) for row in rows]
            assert store.read_recent_experience_sources(1, before_seq, limit) == expected


def test_store_connection_waits_120_seconds_for_a_reader(store):
    assert store.connection.execute('PRAGMA busy_timeout').fetchone()[0] == 120000


def test_event_prefix_digest_covers_sequence_identity_and_body(store, tmp_path):
    _events(store, 3)
    copy = FootprintStore.create(tmp_path / 'copy', {'run_id': 'copy', 'agent_ids': [1]},
                                 {'version': 0, 'current_time': 100})
    try:
        rows = store.connection.execute('SELECT event_seq,event_id,body FROM events ORDER BY event_seq').fetchall()
        with copy.connection:
            copy.connection.executemany('INSERT INTO events VALUES(?,?,?)', rows)
        assert copy.event_prefix_digest(3) == store.event_prefix_digest(3)
        assert store.event_prefix_digest(3)[0] == 3 and store.event_prefix_digest(2)[0] == 2
        assert store.event_prefix_digest(2) != store.event_prefix_digest(3)
        copy.connection.execute('DROP TRIGGER events_no_update')
        with copy.connection:
            copy.connection.execute('UPDATE events SET body=? WHERE event_seq=2', (rows[1][2] + ' ',))
        assert copy.event_prefix_digest(3) != store.event_prefix_digest(3)
        with copy.connection:
            copy.connection.execute('UPDATE events SET body=?,event_id=? WHERE event_seq=2', (rows[1][2], 'other'))
        assert copy.event_prefix_digest(3) != store.event_prefix_digest(3)
        with copy.connection:
            copy.connection.execute('UPDATE events SET event_id=? WHERE event_seq=2', (rows[1][1],))
        assert copy.event_prefix_digest(3) == store.event_prefix_digest(3)
    finally:
        copy.close()
