"""@brief 主观记忆存储：条目的十个键与 LF 行、只在字节变化时写出、归一化、连续编号、二十四小时内同类型相似合并、
写前校验与严格读回（docs/design/09-io-formats.md「人物工作区」）。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from worldmock.agents.memory.life_memory import MemoryStep, save_step
from worldmock.agents.memory.life_memory_store import (
    EPISODES_FILE,
    PERSISTED_EPISODE_FIELDS,
    WorldMockMemoryStore,
    bigram_jaccard,
    read_episodes,
)

_NOW = datetime(2026, 10, 5, 8, tzinfo=timezone.utc)


def _memory(text="我想每天去图书馆读书", kind="goal", importance=0.2, keywords=("读书",), subject=None):
    """@brief conclude_activity 经 control_items 读出的一条存储形状记忆 {type, text, importance, keywords, subject}。"""
    return {"type": kind, "text": text, "importance": importance, "keywords": list(keywords), "subject": subject}


def _store(tmp_path: Path, records=()) -> WorldMockMemoryStore:
    """@brief 人物 1 在临时运行目录下的记忆存储。"""
    return WorldMockMemoryStore(tmp_path / "agents" / "agent_0001", 1, records)


def _lines(store: WorldMockMemoryStore) -> list[dict]:
    """@brief 条目文件按换行符切出的每一行（JSON 解码）。"""
    content = store.episodes_path.read_bytes().decode("utf-8")
    assert content == "" or content.endswith("\n")
    return [json.loads(line) for line in content.split("\n")[:-1]]


def test_new_records_have_the_ten_fields_in_order_on_lf_lines(tmp_path):
    """@brief 新条目恰为十个键、按写出次序，每条一行 json.dumps(ensure_ascii=False) 加 LF；返回的条目与文件相同。"""
    store = _store(tmp_path)
    written = store.append_episodes([_memory(), _memory("答应同事周五交初稿", "commitment", 0.7, ("工作",))], _NOW)
    raw = store.episodes_path.read_bytes()
    assert b"\r" not in raw
    assert raw.decode("utf-8") == "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in written)
    assert [list(item) for item in _lines(store)] == [list(PERSISTED_EPISODE_FIELDS)] * 2
    assert _lines(store)[0] == {
        "id": "m_000001", "agent_id": 1, "time": "2026-10-05T08:00:00+00:00", "type": "goal", "importance": 0.2,
        "keywords": ["读书"], "text": "我想每天去图书馆读书", "source": "activity_conclusion", "merge_count": 1,
        "epistemic_status": "subjective"}
    assert [item["id"] for item in written] == ["m_000001", "m_000002"]
    assert store.episodes_path == store.workspace / EPISODES_FILE


def test_episodes_file_is_written_only_when_its_bytes_change(tmp_path, monkeypatch):
    """@brief 第一次没有条目的保存写出空文件；之后空写入不碰文件，新建与合并改变字节时整份写出。"""
    writes = []
    original = Path.write_bytes
    monkeypatch.setattr(Path, "write_bytes", lambda path, data: writes.append(path.name) or original(path, data))
    store = _store(tmp_path)
    assert store.append_episodes([], _NOW) == []
    assert store.episodes_path.read_bytes() == b"" and writes == ["episodes.jsonl"]
    assert store.append_episodes([], _NOW) == []
    assert writes == ["episodes.jsonl"]
    store.append_episodes([_memory()], _NOW)
    before = store.episodes_path.read_bytes()
    assert writes == ["episodes.jsonl"] * 2
    assert store.append_episodes([], _NOW + timedelta(hours=1)) == []
    assert store.episodes_path.read_bytes() == before and writes == ["episodes.jsonl"] * 2
    store.append_episodes([_memory()], _NOW + timedelta(hours=1))
    assert writes == ["episodes.jsonl"] * 3 and store.episodes_path.read_bytes() != before


def test_record_normalization_collapses_text_and_dedupes_keywords(tmp_path):
    """@brief 正文空白折叠，关键词逐项去空白、去空串并按首次出现去重；重要度转成浮点；来源与认知状态由系统固定，
    看法的对象不写入。"""
    memory = _memory("  想去\t附近\n  散步  ", "relationship", 1, (" 散步 ", "", "散步", "公园", 3), subject="王磊")
    (record,) = _store(tmp_path).append_episodes([{**memory, "source": "invented", "epistemic_status": "x"}], _NOW)
    assert record == {"id": "m_000001", "agent_id": 1, "time": _NOW.isoformat(), "type": "relationship",
                      "importance": 1.0, "keywords": ["散步", "公园", "3"], "text": "想去 附近 散步",
                      "source": "activity_conclusion", "merge_count": 1, "epistemic_status": "subjective"}
    assert type(record["importance"]) is float and "subject" not in record


def test_entry_identities_are_contiguous_and_merges_take_no_identity(tmp_path):
    """@brief 第 n 行的编号恰为 m_{n:06d}；合并不新建条目也不占编号，下一条新建接着当前条目数编号。"""
    store = _store(tmp_path)
    store.append_episodes([_memory("早上跑了五公里"), _memory("给妈妈回了电话", "routine")], _NOW)
    (merged,) = store.append_episodes([_memory("早上跑了五公里。")], _NOW + timedelta(minutes=1))
    assert (merged["id"], merged["merge_count"]) == ("m_000001", 2)
    (created,) = store.append_episodes([_memory("把书架整理好了", "identity")], _NOW + timedelta(minutes=2))
    assert created["id"] == "m_000003"
    assert [item["id"] for item in _lines(store)] == ["m_000001", "m_000002", "m_000003"]
    assert store.episode_ids() == {"m_000001", "m_000002", "m_000003"}


@pytest.mark.parametrize("age,merged", [(timedelta(hours=24), True),
                                         (timedelta(hours=24, microseconds=1), False),
                                         (timedelta(seconds=-1), False)])
def test_merge_window_has_a_closed_twenty_four_hour_boundary(tmp_path, age, merged):
    """@brief 合并窗口是闭区间 [0, 24 小时]：恰好 24 小时合并，多一微秒或时刻倒退都新建。"""
    store = _store(tmp_path)
    (first,) = store.append_episodes([_memory()], _NOW)
    (second,) = store.append_episodes([_memory()], _NOW + age)
    assert (first["id"] == second["id"]) is merged
    assert len(store.episode_ids()) == (1 if merged else 2)
    assert second["time"] == (_NOW + age).isoformat()
    assert len(read_episodes(store.workspace, 1)) == (1 if merged else 2)


@pytest.mark.parametrize("text,similarity,merged", [("abcd", 0.6, True), ("abc", 0.4, False)])
def test_merge_similarity_uses_the_frozen_threshold(tmp_path, text, similarity, merged):
    """@brief 规范化正文的相邻字符对 Jaccard 不小于 0.6 才合并。"""
    store = _store(tmp_path)
    store.append_episodes([_memory("abcdef")], _NOW)
    assert bigram_jaccard("abcdef", text) == similarity
    store.append_episodes([_memory(text)], _NOW)
    assert len(store.episode_ids()) == (1 if merged else 2)


def test_memories_with_incomparable_time_zones_are_not_merged(tmp_path):
    """@brief 不带时区与带时区的时刻无法相减，按窗口外处理：同一条记忆新建第二条。"""
    store = _store(tmp_path)
    (first,) = store.append_episodes([_memory()], _NOW.replace(tzinfo=None))
    (second,) = store.append_episodes([_memory()], _NOW)
    assert first["id"] != second["id"]
    assert len(read_episodes(store.workspace, 1)) == 2


@pytest.mark.parametrize("left,right,similarity", [("", "文字", 0.0), ("文字", "", 0.0), ("a", "a", 0.0),
                                                    (" \t\n", "文字", 0.0), ("同一句话", "同一句话", 1.0),
                                                    ("完全不同", "毫无关系", 0.0)])
def test_short_or_empty_text_cannot_count_as_similar(left, right, similarity):
    """@brief 没有相邻字符对的短文本或空文本相似度为零，不能算作相似；相同正文为 1，毫不相干为 0。"""
    assert bigram_jaccard(left, right) == similarity


def test_memories_of_different_types_are_not_merged(tmp_path):
    """@brief 正文相同、在窗口内，类型不同就不合并。"""
    store = _store(tmp_path)
    text = "我在楼下的面馆吃了一碗牛肉面，味道很好"
    store.append_episodes([_memory(text)], _NOW)
    store.append_episodes([_memory(text, "routine")], _NOW + timedelta(hours=1))
    assert [item["type"] for item in _lines(store)] == ["goal", "routine"]


def test_a_memory_merges_into_one_created_earlier_in_the_same_call(tmp_path):
    """@brief 同一次调用里两条相似记忆：只新建一条，合并计数为 2；步尾保存把它只算作新建。"""
    store = _store(tmp_path)
    step = MemoryStep("林舟", (), "observation-1",
                      (_memory("我在楼下的面馆吃了一碗牛肉面，味道很好"), _memory("我在楼下的面馆吃了一碗牛肉面，味道不错")),
                      _NOW)
    outcome = save_step(store, step)
    assert [(item["id"], item["merge_count"]) for item in outcome.created_episodes] == [("m_000001", 2)]
    assert outcome.merged_episodes == ()
    assert [item["merge_count"] for item in _lines(store)] == [2]


@pytest.mark.parametrize("text", ["", " \t\n"])
def test_an_invalid_new_record_leaves_file_and_records_unchanged(tmp_path, text, caplog):
    """@brief 一次调用里先有一条合法记忆、再有一条空正文的记忆：写前校验以空正文拒绝，条目文件与存储里的条目都保持
    调用之前的样子。"""
    store = _store(tmp_path)
    store.append_episodes([_memory("愿意去散步", keywords=("散步",))], _NOW)
    before, identities = store.episodes_path.read_bytes(), store.episode_ids()
    with pytest.raises(ValueError, match="^Memory episode line 3 has empty text fields$"):
        store.append_episodes([_memory("答应同事周五交初稿", "commitment"), _memory(text)], _NOW)
    assert store.episodes_path.read_bytes() == before and store.episode_ids() == identities == {"m_000001"}
    assert "Memory episode line 3 has empty text fields" in caplog.text


def test_returned_records_cannot_change_the_store(tmp_path):
    """@brief 存储深拷贝交给它的条目，返回的新建与合并条目也是副本：改动它们不改变存储写出的内容。"""
    store = _store(tmp_path)
    (written,) = store.append_episodes([_memory("愿意去散步", keywords=("散步",))], _NOW)
    written["keywords"].append("fabricated")
    (merged,) = store.append_episodes([_memory("愿意去散步", keywords=("散步",))], _NOW)
    merged["text"] = "改过的正文"
    records = read_episodes(store.workspace, 1)
    assert records[0]["keywords"] == ["散步"] and records[0]["text"] == "愿意去散步"
    source = [dict(records[0])]
    restored = _store(tmp_path, source)
    source[0]["merge_count"] = 9
    restored.append_episodes([], _NOW)
    assert read_episodes(restored.workspace, 1) == records


def test_read_back_accepts_what_the_store_wrote(tmp_path):
    """@brief 关键词里的 U+2028、U+0085 原样写出；按换行符切行的严格读回读得回存储写出的文件（含一次合并），
    splitlines 则会把这一行切开。"""
    store = _store(tmp_path)
    first, second = store.append_episodes(
        [_memory("夜里散步回家", keywords=("夜 晚", "散\u0085步")), _memory("答应同事周五交初稿", "commitment")],
        _NOW)
    (merged,) = store.append_episodes([_memory("夜里散步回家。")], _NOW + timedelta(minutes=5))
    content = store.episodes_path.read_bytes().decode("utf-8")
    assert " " in content and "\u0085" in content
    assert len(content.splitlines()) > 2
    assert read_episodes(store.workspace, 1) == (merged, second)
    assert merged["id"] == first["id"] and merged["keywords"] == ["夜 晚", "散\u0085步"]


def _corrupt(record: dict, change: dict) -> bytes:
    """@brief 一条合法条目改写若干键后的一行文件字节。"""
    return (json.dumps({**record, **change}, ensure_ascii=False) + "\n").encode("utf-8")


@pytest.mark.parametrize("content,error,message", [
    (lambda record: _corrupt(record, {}) + b"  \n", ValueError, "line 2 is blank"),
    (lambda record: b"[]\n", ValueError, "line 1 has an invalid schema"),
    (lambda record: _corrupt(record, {"refs": []}), ValueError, "line 1 has an invalid schema"),
    (lambda record: _corrupt(record, {"id": "m_000002"}), ValueError, "line 1 has a non-contiguous identity"),
    (lambda record: _corrupt(record, {"agent_id": 2}), ValueError, "line 1 has inconsistent identity"),
    (lambda record: _corrupt(record, {"agent_id": True}), ValueError, "line 1 has inconsistent identity"),
    (lambda record: _corrupt(record, {"merge_count": 0}), ValueError, "line 1 has invalid counters"),
    (lambda record: _corrupt(record, {"type": "event"}), ValueError, "line 1 has an invalid type"),
    (lambda record: _corrupt(record, {"epistemic_status": "objective"}), ValueError,
     "line 1 has an invalid epistemic status"),
    (lambda record: _corrupt(record, {"source": "step_result"}), ValueError, "line 1 has an invalid source"),
    (lambda record: _corrupt(record, {"importance": True}), ValueError, "line 1 has invalid importance"),
    (lambda record: _corrupt(record, {"importance": 1.5}), ValueError, "line 1 has invalid importance"),
    (lambda record: _corrupt(record, {"text": ""}), ValueError, "line 1 has empty text fields"),
    (lambda record: _corrupt(record, {"time": 0}), ValueError, "line 1 has empty text fields"),
    (lambda record: _corrupt(record, {"keywords": [1]}), ValueError, "line 1 has invalid string lists"),
    (lambda record: _corrupt(record, {"keywords": "读书"}), ValueError, "line 1 has invalid string lists"),
    (lambda record: b"\xff\n", UnicodeDecodeError, "utf-8"),
    (lambda record: b"{not json\n", json.JSONDecodeError, "Expecting"),
])
def test_read_back_rejects_each_corruption_without_repair(tmp_path, content, error, message):
    """@brief 严格读回逐项拒绝：空行、键集合、编号、人物、计数、类型、认知状态、来源、重要度、文字与关键词各有自己的
    英文错误；不能解码与不是 JSON 的行照原异常抛出；拒绝时不修复、不改文件。"""
    store = _store(tmp_path)
    (record,) = store.append_episodes([_memory()], _NOW)
    store.episodes_path.write_bytes(content(record))
    before = store.episodes_path.read_bytes()
    with pytest.raises(error, match=message):
        read_episodes(store.workspace, 1)
    assert store.episodes_path.read_bytes() == before


@pytest.mark.parametrize("identities", [("m_000001", "m_000003"), ("m_000001", "m_000001"), ("m_000002",),
                                        ("m_00000000_000001",)])
def test_read_back_rejects_non_contiguous_or_duplicate_identities(tmp_path, identities, caplog):
    """@brief 断号、重号、不从 m_000001 起与旧式编号都以 non-contiguous identity 拒绝：一条核对同时保证连续与唯一。"""
    store = _store(tmp_path)
    (record,) = store.append_episodes([_memory()], _NOW)
    store.episodes_path.write_bytes(b"".join(_corrupt(record, {"id": identity}) for identity in identities))
    with pytest.raises(ValueError, match=r"^Memory episode line \d has a non-contiguous identity$"):
        read_episodes(store.workspace, 1)
    assert "non-contiguous identity" in caplog.text
