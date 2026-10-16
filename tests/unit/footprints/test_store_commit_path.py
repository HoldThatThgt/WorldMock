"""存储提交路径：一步世界推进、证据追加、检查点与已提交世界缓存（docs/design/05-recording-engine.md「权威事实与投影」）。"""

import hashlib
from types import SimpleNamespace

import pytest

import worldmock.footprints.store as store_module
from worldmock.errors import IntegrityError
from worldmock.footprints.models import Event, EventDraft, canonical_json, sha256_json
from worldmock.footprints.store import FootprintStore

INITIAL = {"version": 0, "current_time": 100, "accounts": {"agent:1": 100, "external:scenario": -100}}
MANIFEST = {"run_id": "run", "agent_ids": [1]}
#: 计数器把输入正文（解析）或输出字节（编码）至少这么长的调用算作整份世界的一次解析或编码。
WORLD_SIZED = 200_000


@pytest.fixture
def store(tmp_path):
    """最小世界的新运行：两个账户、尚无事件。"""
    value = FootprintStore.create(tmp_path / "run", MANIFEST, INITIAL)
    yield value
    value.close()


def _row(store: FootprintStore) -> tuple:
    """库里 world_state 的版本与正文原文。"""
    return store.connection.execute("SELECT version, body FROM world_state").fetchone()


def _stored_body(store: FootprintStore, checkpoint_id: str) -> bytes:
    """检查点行存储的正文字节。"""
    row = store.connection.execute("SELECT body FROM checkpoints WHERE checkpoint_id=?", (checkpoint_id,)).fetchone()
    return row[0].encode()


def _observation(moment: int) -> EventDraft:
    """人物 1 在 moment 的一条观察证据。"""
    return EventDraft(event_type="observation", occurred_at=moment, observed_at=moment, agent_id=1, data={})


def _probe(moment: int) -> EventDraft:
    """moment 的一条无资金变化的环境事件，作一步推进的因果事件。"""
    return EventDraft(event_type="environment_event", occurred_at=moment, visibility="system",
                      data={"kind": "probe"})


def _versions(write) -> list[tuple[int, int]] | str:
    """执行一次写入：成功时返回各事件的前后版本对，被拒时返回错误文本，两种结局都能直接比较。"""
    try:
        return [(event.state_version_before, event.state_version_after) for event in write()]
    except IntegrityError as error:
        return str(error)


def _snapshots(store: FootprintStore) -> dict:
    """冻结清单要求的全部检查点参与者快照。"""
    reference = store.assets.put_json({"memory": "saved"}, {"kind": "agent"})
    return {"agent:1": reference, "life": reference}


def test_evidence_append_leaves_the_world_row_untouched_and_keeps_the_version(store):
    """证据追加只写事件：两条观察依次得到序号 1、2，前后版本都是已提交版本 0，世界行逐字节不变。"""
    before = _row(store)
    saved = store.append_evidence([_observation(100), _observation(100)])
    assert [event.event_seq for event in saved] == [1, 2]
    assert [(event.state_version_before, event.state_version_after) for event in saved] == [(0, 0), (0, 0)]
    assert _row(store) == before


def test_version_step_writes_the_canonical_world_and_advances_one_version(store):
    """一步推进把新世界的规范 JSON 写进世界行，版本加一，事件版本对是推进前后。"""
    new = {**INITIAL, "version": 1, "current_time": 110}
    saved = store.commit(store.state, new, [_probe(110)])
    assert _row(store) == (1, canonical_json(new).decode())
    assert [(event.state_version_before, event.state_version_after) for event in saved] == [(0, 1)]


def test_version_step_refuses_a_base_that_differs_from_the_committed_world(store, caplog):
    """出发世界与已提交世界差一个值即拒绝这一步并写错误日志，不写事件、不改世界行。"""
    before = _row(store)
    base = {**store.state, "current_time": 101}
    with pytest.raises(IntegrityError, match="^Engine world differs from the committed world$"):
        store.commit(base, {**INITIAL, "version": 1, "current_time": 110}, [_probe(110)])
    assert "Engine world differs from the committed world at version 0" in [
        record.getMessage() for record in caplog.records]
    assert store.last_event_seq == 0
    assert _row(store) == before


def test_consecutive_version_steps_and_appends_follow_the_cached_world(store):
    """缓存随每步推进换成新世界：之后的证据与下一步按 v1 记版本；以已被取代的 v1 为出发世界的再一步被拒绝。"""
    initial, first = store.state, {**INITIAL, "version": 1, "current_time": 110}
    outcomes = [
        _versions(lambda: store.commit(initial, first, [_probe(110)])),
        _versions(lambda: store.append_evidence([_observation(110)])),
        _versions(lambda: store.commit(first, {**INITIAL, "version": 2, "current_time": 120}, [_probe(120)])),
        _versions(lambda: store.commit(first, {**INITIAL, "version": 3, "current_time": 130}, [_probe(130)])),
    ]
    assert outcomes == [[(0, 1)], [(1, 1)], [(1, 2)], "Engine world differs from the committed world"]


def test_evidence_append_refuses_money_and_initial_funding(store):
    """证据追加不能带资金变化；初始注资只属于创建世界的那一步推进，空库上的注资证据同样被拒。"""
    money = EventDraft(event_type="action_result", occurred_at=100, agent_id=1,
                       resource_changes={"account:agent:1": -5, "account:external:scenario": 5})
    with pytest.raises(IntegrityError, match="Account balances disagree with committed financial events"):
        store.append_evidence([money])
    funding = EventDraft(event_type="environment_event", occurred_at=100, visibility="evaluation",
                         source_kind="scenario", data={"kind": "initial_funds_injected"},
                         resource_changes={"account:agent:1": 100, "account:external:scenario": -100})
    with pytest.raises(IntegrityError, match="Initial funding must be the first event of world creation"):
        store.append_evidence([funding])
    assert store.last_event_seq == 0


def test_evidence_append_refuses_an_empty_list(store, caplog):
    """没有事件的证据追加被拒绝并写错误日志。"""
    with pytest.raises(IntegrityError, match="^Evidence append requires at least one event$"):
        store.append_evidence([])
    assert "Evidence append received no event" in [record.getMessage() for record in caplog.records]


def test_evidence_append_refuses_an_event_earlier_than_the_last_event(store):
    """证据的发生时刻不能早于最近事件：推进到 120 的那一步之后，110 的观察被拒。"""
    store.append_evidence([_observation(100)])
    store.commit(store.state, {**INITIAL, "version": 1, "current_time": 120}, [_probe(120)])
    with pytest.raises(IntegrityError, match="Event time is out of committed world order"):
        store.append_evidence([_observation(110)])


def test_last_event_time_sees_events_inserted_outside_the_write_path(store):
    """最近事件时刻每次从库里取：像恢复分支那样绕过写入路径插入的 120 事件之后，110 的证据被拒、120 的被接受。

    在载入时缓存、或由 commit 保存的最近事件时刻都会接受 110。
    """
    store.append_evidence([_observation(100)])
    store.commit(store.state, {**INITIAL, "version": 1, "current_time": 130}, [_probe(100)])
    draft = _probe(120)
    with store.connection:
        store._insert_event(Event(**draft.model_dump(), run_id="run", event_seq=store.last_event_seq + 1,
                                  recorded_at="2026-10-15T00:00:00+00:00", wall_elapsed_ms=0,
                                  state_version_before=1, state_version_after=1))
    with pytest.raises(IntegrityError, match="Event time is out of committed world order"):
        store.append_evidence([_observation(110)])
    saved = store.append_evidence([_observation(120)])
    assert [(event.state_version_before, event.state_version_after) for event in saved] == [(1, 1)]


@pytest.mark.parametrize("write", ["append", "step", "checkpoint"])
def test_a_write_after_another_connection_changed_the_world_is_refused(store, write):
    """别的连接推进了世界之后，本存储的证据追加、一步推进或检查点都在事务内按预期版本被拒并整体回滚。"""
    first = {**INITIAL, "version": 1, "current_time": 110}
    store.commit(store.state, first, [_probe(110)])
    other = FootprintStore(store.run_dir)
    other.commit(first, {**first, "version": 2}, [_probe(110)])
    other.close()
    snapshots = _snapshots(store)
    sequence = store.last_event_seq
    attempts = {"append": lambda: store.append_evidence([_observation(110)]),
                "step": lambda: store.commit(first, {**first, "version": 2}, [_probe(110)]),
                "checkpoint": lambda: store.checkpoint(first, snapshots)}
    with pytest.raises(IntegrityError, match="^World state was changed outside this store$"):
        attempts[write]()
    assert store.last_event_seq == sequence
    assert store.connection.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0] == 0


def test_checkpoint_body_and_record_hash_are_the_canonical_payload(store):
    """检查点正文就是载荷的规范 JSON，record_sha256 是这份字节的 SHA-256，与 sha256_json(载荷) 相同。"""
    first = {**INITIAL, "version": 1, "current_time": 110}
    store.commit(store.state, first, [_probe(110)])
    point = store.checkpoint(first, _snapshots(store))
    payload = point.model_dump(exclude={"record_sha256"})
    assert _stored_body(store, point.checkpoint_id) == canonical_json(payload)
    assert point.record_sha256 == sha256_json(payload) == hashlib.sha256(
        _stored_body(store, point.checkpoint_id)).hexdigest()
    assert store.get_checkpoint(point.checkpoint_id) == point


@pytest.fixture
def counted(tmp_path, monkeypatch):
    """一份填充到 20 万字符以上的世界：新建后关闭，装上整份世界解析与编码的计数器，再重新打开。

    返回（存储，计数，创建时的世界，下一步的新世界）；计数只记整份世界大小的 json.loads 与 canonical_json 调用。
    存储里的 sha256_json 也经过同一个编码计数器，按载荷再算一次摘要（重新编码整份世界）同样计入。
    """
    initial = {**INITIAL, "filler": "x" * WORLD_SIZED}
    FootprintStore.create(tmp_path / "padded", MANIFEST, initial).close()
    counts = {"loads": 0, "encodes": 0}
    loads, encode = store_module.json.loads, store_module.canonical_json

    def counting_loads(text, *args, **kwargs):
        counts["loads"] += len(text) >= WORLD_SIZED
        return loads(text, *args, **kwargs)

    def counting_encode(value):
        encoded = encode(value)
        counts["encodes"] += len(encoded) >= WORLD_SIZED
        return encoded

    monkeypatch.setattr(store_module, "json", SimpleNamespace(loads=counting_loads))
    monkeypatch.setattr(store_module, "canonical_json", counting_encode)
    monkeypatch.setattr(store_module, "sha256_json", lambda value: hashlib.sha256(counting_encode(value)).hexdigest())
    value = FootprintStore(tmp_path / "padded")
    yield value, counts, initial, {**initial, "version": 1, "current_time": 110}
    value.close()


def test_cache_loads_once_at_the_first_write(counted):
    """只读的打开与查询不解析世界；第一次证据追加解析一次，之后的追加、推进与检查点不再解析。"""
    store, counts, initial, new = counted
    store.last_event_seq
    store.last_agent_event_seq("observation", 1)
    store.read_agent_events("observation", 1)
    store.last_event_seq_before(1000)
    assert counts["loads"] == 0
    store.append_evidence([_observation(100)])
    assert counts["loads"] == 1
    store.append_evidence([_observation(100)])
    store.commit(initial, new, [_probe(110)])
    store.checkpoint(new, _snapshots(store))
    assert counts["loads"] == 1


def test_evidence_append_neither_parses_nor_encodes_the_world(counted):
    """缓存载入之后，一次证据追加既不解析也不编码整份世界。"""
    store, counts, _, _ = counted
    store.append_evidence([_observation(100)])
    counts.update(loads=0, encodes=0)
    store.append_evidence([_observation(100)])
    assert counts == {"loads": 0, "encodes": 0}


def test_version_step_parses_nothing_and_encodes_the_base_and_the_new_world(counted):
    """缓存载入之后，一步推进不解析世界，只编码出发世界与新世界各一次。"""
    store, counts, initial, new = counted
    store.append_evidence([_observation(100)])
    counts.update(loads=0, encodes=0)
    store.commit(initial, new, [_probe(110)])
    assert counts == {"loads": 0, "encodes": 2}


def test_checkpoint_parses_nothing_and_encodes_the_world_and_the_payload_once(counted):
    """缓存载入之后，检查点不解析世界，只编码比较用的世界与载荷各一次。"""
    store, counts, initial, new = counted
    store.commit(initial, new, [_probe(110)])
    snapshots = _snapshots(store)
    counts.update(loads=0, encodes=0)
    store.checkpoint(new, snapshots)
    assert counts == {"loads": 0, "encodes": 2}
