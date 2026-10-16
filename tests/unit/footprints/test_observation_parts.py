"""观察分件：拆分、同一事务写入、唯一解码换回、损坏即失败、分支原样复制。"""

import copy
import hashlib
import json
import logging
import sqlite3

import pytest

from worldmock.errors import IntegrityError
from worldmock.footprints.checkpoint import restore_branch, snapshot_directory
from worldmock.footprints.models import EventDraft, canonical_json, is_sha256
from worldmock.footprints.observation_parts import split_observation
from worldmock.footprints.store import FootprintStore

#: 五行活动绑定所指活动的开始本地日期：三个日期，相同日期相邻。
START_DATES = ["2026-01-04", "2026-01-04", "2026-01-05", "2026-01-06", "2026-01-06"]
#: 整表各存为一个观察分件的六个键（逐字写出）。
WHOLE_KEYS = ("catalog_bindings", "errand_item_bindings", "known_activity_actions", "locations", "memory_layer",
              "persona_views")


@pytest.fixture
def store(tmp_path):
    value = FootprintStore.create(tmp_path / 'parent',
                                  {'run_id': 'parent', 'agent_ids': [1], 'code': 'frozen', 'environment_assets': {}},
                                  {'version': 0, 'current_time': 100, 'balance': 50})
    yield value
    value.close()


def _binding(number: int) -> dict:
    """一行活动绑定，字段与 observe 构建的相同。"""
    return {"activity_id": f"activity-{number}", "name": f"活动{number}", "note_labels": [f"标注{number}"],
            "phase": "concluded" if number % 2 else "pending_conclusion", "resumable": number == 3, "title": None}


def _observation() -> dict:
    """五行活动绑定（三个日期）、内容各不相同的六张整表、内联的待办、目标与工具层，以及带非整数浮点的身体。"""
    return {
        "activity_bindings": [_binding(number) for number in range(1, 6)],
        "catalog_bindings": [{"object": "家", "kind": "location", "object_id": "home", "actions": []}],
        "errand_item_bindings": [{"name": "牛奶", "product_id": "milk"}],
        "known_activity_actions": ["sleep", "travel", "interact"],
        "locations": [{"location_id": "home", "name": "家"}],
        "memory_layer": {"facts": [{"text": "记得买牛奶", "weight": 0.1 + 0.2}]},
        "persona_views": {"relationships": [], "preferences": ["喜欢咖啡"]},
        "todo_layer": {"goals": ["买牛奶"]},
        "goal_bindings": [{"task_name": "买牛奶", "intent_kind": "购物"}],
        "tool_layer": {"tools": ["start_main_activity"]},
        "body": {"levels": {"energy": "充足"}, "energy_kcal": 1234.5},
    }


def _stored(store, data, dates=START_DATES, decision_id="decision-a", **envelope) -> str:
    """拆分后经 append_observation 写入，返回观察的事件身份。"""
    stored, parts = split_observation(data, dates)
    return store.append_observation(EventDraft(event_type="observation", occurred_at=100, observed_at=100, agent_id=1,
                                               decision_id=decision_id, data=stored, **envelope), parts)


def _raw(store, event_id) -> dict:
    """运行库里存下的事件正文（不经解码）。"""
    row = store.connection.execute("SELECT body FROM events WHERE event_id=?", (event_id,)).fetchone()
    return json.loads(row[0])


def _count(store, table: str) -> int:
    """同一连接上某张表的行数。"""
    return store.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_split_groups_bindings_by_start_date_and_stores_six_tables_once():
    data = _observation()
    stored, parts = split_observation(data, START_DATES)
    rows = data["activity_bindings"]
    assert isinstance(stored["activity_bindings"], list) and len(stored["activity_bindings"]) == 3
    assert all(is_sha256(ref) for ref in stored["activity_bindings"])
    for key in ("catalog_bindings", "errand_item_bindings", "known_activity_actions", "locations", "memory_layer",
                "persona_views"):
        assert is_sha256(stored[key])
    assert len(parts) == 9
    expected = dict(zip(stored["activity_bindings"], [rows[0:2], rows[2:3], rows[3:5]]))
    expected.update({stored[key]: data[key] for key in WHOLE_KEYS})
    assert all(hashlib.sha256(body.encode()).hexdigest() == key for key, body in parts.items())
    assert {key: json.loads(body) for key, body in parts.items()} == expected
    assert stored["todo_layer"] is data["todo_layer"] and stored["goal_bindings"] is data["goal_bindings"]


def test_split_leaves_the_observation_untouched():
    data = _observation()
    before, values = copy.deepcopy(data), dict(data)
    split_observation(data, START_DATES)
    assert data == before
    assert list(data) == list(values) and all(data[key] is value for key, value in values.items())


@pytest.mark.parametrize("case, message, logged", [
    ("missing-table", "Observation lacks a table stored as a part",
     "Observation lacks the tables stored as parts: ['memory_layer']"),
    ("date-count", "Observation start dates do not match its activity bindings",
     "Observation start dates (4) do not match its activity bindings (5)"),
], ids=["missing-table", "date-count"])
def test_split_refuses_missing_tables_and_mismatched_dates(case, message, logged, caplog):
    data, dates = _observation(), START_DATES
    if case == "missing-table":
        del data["memory_layer"]
    else:
        dates = START_DATES[:4]
    with caplog.at_level(logging.ERROR), pytest.raises(IntegrityError, match=f"^{message}$"):
        split_observation(data, dates)
    assert logged in caplog.messages


def test_append_and_read_round_trip_the_observation_bytes(store):
    data = _observation()
    identity = _stored(store, data)
    read = store.read_event(identity).data
    assert canonical_json(read) == canonical_json(data)
    assert list(read) == sorted(data)
    assert [row["activity_id"] for row in read["activity_bindings"]] == [f"activity-{n}" for n in range(1, 6)]
    raw = _raw(store, identity)["data"]
    assert len(raw["activity_bindings"]) == 3 and all(is_sha256(ref) for ref in raw["activity_bindings"])
    assert all(is_sha256(raw[key]) for key in WHOLE_KEYS)


def test_every_read_method_returns_the_complete_observation(store):
    data = _observation()
    identity = _stored(store, data)
    reads = [store.read_event(identity), *store.read_agent_events("observation", 1),
             *store.read_decision_observations(1, "decision-a", 100), *list(store.iter_events(store.last_event_seq)),
             store.find_event(identity)]
    assert len(reads) == 5
    assert all(canonical_json(event.data) == canonical_json(data) for event in reads)


def test_identical_tables_are_stored_once(store):
    first = _observation()
    _stored(store, first)
    assert _count(store, "parts") == 9
    second = _observation()
    second["activity_bindings"].append(_binding(6))
    _stored(store, second, [*START_DATES, "2026-01-07"], "decision-b")
    assert _count(store, "parts") == 10


@pytest.mark.parametrize("case, message", [
    ("missing", "Observation part is missing"),
    ("damaged", "Observation part content does not match its sha256"),
    ("malformed", "Stored observation part reference is invalid"),
], ids=["missing", "damaged", "malformed"])
def test_damaged_missing_or_malformed_parts_fail_loudly(store, case, message, caplog):
    data = _observation()
    identity = _stored(store, data)
    raw = _raw(store, identity)
    ref = raw["data"]["memory_layer"]
    with store.connection:
        if case == "missing":
            store.connection.execute("DROP TRIGGER parts_no_delete")
            store.connection.execute("DELETE FROM parts WHERE sha256=?", (ref,))
            logged = f"Observation part {ref} is missing"
        elif case == "damaged":
            store.connection.execute("DROP TRIGGER parts_no_update")
            store.connection.execute("UPDATE parts SET body=? WHERE sha256=?", ('{"facts":[]}', ref))
            logged = f"Observation part {ref} does not match its sha256"
        else:
            store.connection.execute("DROP TRIGGER events_no_update")
            raw["data"]["memory_layer"] = data["memory_layer"]
            store.connection.execute("UPDATE events SET body=? WHERE event_id=?",
                                     (canonical_json(raw).decode(), identity))
            logged = "Stored observation key memory_layer carries an invalid part reference"
    with caplog.at_level(logging.ERROR), pytest.raises(IntegrityError, match=f"^{message}$"):
        store.read_event(identity)
    assert logged in caplog.messages


def test_parts_are_immutable(store):
    _stored(store, _observation())
    for statement in ("UPDATE parts SET body='[]'", "DELETE FROM parts"):
        with pytest.raises(sqlite3.IntegrityError, match="Observation parts are immutable"):
            with store.connection:
                store.connection.execute(statement)
    assert _count(store, "parts") == 9


@pytest.mark.parametrize("case", ["refused-inside-the-write", "refused-before-the-write"],
                         ids=["refused-inside-the-write", "refused-before-the-write"])
def test_parts_and_their_observation_share_one_transaction(store, case, monkeypatch, caplog):
    envelope = {}
    if case == "refused-inside-the-write":
        def refuse(event):
            raise sqlite3.OperationalError("injected")

        monkeypatch.setattr(store, "_insert_event", refuse)
        expected, message = sqlite3.OperationalError, "^injected$"
    else:
        envelope = {"caused_by": ["missing-cause"]}
        expected, message = IntegrityError, "^Event cause does not exist$"
    with caplog.at_level(logging.ERROR), pytest.raises(expected, match=message):
        _stored(store, _observation(), **envelope)
    assert _count(store, "parts") == 0
    assert store.last_event_seq == 0
    assert store.connection.in_transaction is False
    assert "Observation append failed; no observation or new part was committed" in caplog.messages


def test_an_observation_whose_part_is_not_stored_is_refused(store, caplog):
    stored, _ = split_observation(_observation(), START_DATES)
    with caplog.at_level(logging.ERROR), pytest.raises(IntegrityError, match="^Observation part is missing$"):
        store.append_evidence([EventDraft(event_type="observation", occurred_at=100, data=stored)])
    assert f"Observation part {stored['activity_bindings'][0]} is missing" in caplog.messages
    assert _count(store, "events") == 0


def test_a_part_whose_body_does_not_match_its_key_is_refused(store, caplog):
    stored, parts = split_observation(_observation(), START_DATES)
    parts[stored["memory_layer"]] = '{"facts":[]}'
    draft = EventDraft(event_type="observation", occurred_at=100, data=stored)
    with caplog.at_level(logging.ERROR), pytest.raises(
            IntegrityError, match="^Observation part content does not match its sha256$"):
        store.append_observation(draft, parts)
    assert f"Observation part {stored['memory_layer']} does not match its sha256" in caplog.messages
    assert (_count(store, "parts"), _count(store, "events")) == (0, 0)


def test_append_observation_refuses_other_events(store, caplog):
    stored, parts = split_observation(_observation(), START_DATES)
    with caplog.at_level(logging.ERROR), pytest.raises(
            IntegrityError, match="^Observation append requires an observation event$"):
        store.append_observation(EventDraft(event_type="action_result", occurred_at=100), parts)
    assert "Observation append received a action_result event" in caplog.messages
    assert (_count(store, "parts"), _count(store, "events")) == (0, 0)


def test_branch_copies_the_parts_and_the_stored_rows(store, tmp_path):
    identity = _stored(store, _observation())
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    (workspace / 'MEMORY.md').write_text('earlier memory')
    reference = snapshot_directory(workspace, store.assets)
    point = store.checkpoint(store.state, {'agent:1': reference, 'life': reference})
    later = _observation()
    later["activity_bindings"].append(_binding(6))
    _stored(store, later, [*START_DATES, "2026-01-07"], "decision-b")
    child = restore_branch(store, point.checkpoint_id, tmp_path / 'child')
    try:
        seq = store.read_event(identity).event_seq
        bodies = [side.connection.execute("SELECT body FROM events WHERE event_seq=?", (seq,)).fetchone()[0]
                  for side in (store, child)]
        assert bodies[0] == bodies[1]
        assert is_sha256(json.loads(bodies[1])["data"]["memory_layer"])
        parts = [side.connection.execute("SELECT sha256, body FROM parts ORDER BY sha256").fetchall()
                 for side in (store, child)]
        assert parts[0] == parts[1] and len(parts[1]) == 10
        assert child.read_event(identity).data == store.read_event(identity).data
    finally:
        child.close()
