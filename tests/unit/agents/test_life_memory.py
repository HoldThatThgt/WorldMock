"""@brief 步尾记忆保存与 MEMORY.md 视图：固定模板与常量自述、4000 字预算、只在内容变化时写出、按步前身份区分新建与
合并，以及「记忆已更新」证据的登记条件；收货经历只呈现真实商品、份数与来源。"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from worldmock.agents.memory.life_memory import (
    MemoryStep,
    _episode_snapshot,
    memory_update_evidence,
    render_memory_view,
    save_step,
)
from worldmock.agents.memory.life_memory_store import WorldMockMemoryStore
from worldmock.state.model.memory import valid_subjective_snapshot

_NOW = datetime(2026, 10, 5, 8, tzinfo=timezone.utc)
_WAKEUP_ID = "run:wake:100:v1:a1"
_AGENT_ID = 1
#: 「人物自述（未经核验）」一段的常量写法。
_SELF = "\n\n## 人物自述（未经核验）\n> 暂无人物自述。\n"


def _received(source="receipt-event"):
    """@brief 一个已经认证的本人收货事实，与世界投影的封闭字段一致。"""
    return {"kind": "received", "agent_id": 1, "activity_id": None, "source_event_id": source,
            "event_seq": 2, "occurred_at": 100, "order_id": "order-1", "goods_id": "rice-ball",
            "category": "meal", "quantity": 1}


def _memory(text, kind="goal"):
    """@brief conclude_activity 经 control_items 读出的一条存储形状记忆。"""
    return {"type": kind, "text": text, "importance": 0.5, "keywords": ["日常"], "subject": None}


def _store(tmp_path: Path) -> WorldMockMemoryStore:
    """@brief 人物 1 的空记忆存储（首次决策之前）。"""
    return WorldMockMemoryStore(tmp_path / "agents" / "agent_0001", 1, ())


def _step(identity="林舟", facts=(), memories=(), now=_NOW, observation="observation-1"):
    """@brief 一次步尾保存的输入。"""
    return MemoryStep(identity, tuple(facts), observation, tuple(memories), now)


def test_received_memory_line_preserves_actual_goods_quantity_and_source():
    """@brief 记忆的收到句来自规范收货事实，不附加用餐时长或进食活动；自述段是常量。"""
    text = render_memory_view("测试人物", [_received()])
    assert "- 100：收到 rice-ball 1 份。来源事件 receipt-event。" in text
    assert "吃了" not in text and "consumed" not in text and "进食" not in text
    assert text.endswith(_SELF)


def test_retired_consumed_kind_cannot_render_as_a_verified_fact(caplog):
    """@brief 旧进食经历没有可用的记忆分支，拒绝时保留英文错误日志。"""
    with pytest.raises(ValueError, match="Verified experience kind is invalid"):
        render_memory_view("测试人物", [{**_received(), "kind": "consumed"}])
    assert "Verified experience kind is invalid" in caplog.text


def test_memory_view_matches_the_exact_template_with_the_constant_self_description():
    """@brief 视图恰为三段：身份逐行引用、已核验经历一条一行（没有时写「暂无已核验经历」）、自述段恒为「暂无人物自述」。"""
    assert render_memory_view("林舟\n上海白领", []) == (
        "# Memory\n\n## 身份\n> 林舟\n> 上海白领\n\n## 已核验经历\n- 暂无已核验经历。" + _SELF)
    assert render_memory_view("林舟", [_received("a"), _received("b")]) == (
        "# Memory\n\n## 身份\n> 林舟\n\n## 已核验经历\n- 100：收到 rice-ball 1 份。来源事件 a。\n"
        "- 100：收到 rice-ball 1 份。来源事件 b。" + _SELF)


def test_a_view_over_4000_characters_is_refused_and_nothing_is_written(tmp_path, caplog):
    """@brief 整份视图 4001 字时步尾保存拒绝，工作区里既没有 MEMORY.md 也没有条目文件；恰为 4000 字时照常写出。"""
    base = len(render_memory_view("", []))
    store = _store(tmp_path)
    with pytest.raises(ValueError, match="^Verified memory view exceeds its character budget$"):
        save_step(store, _step("人" * (4001 - base - 2), memories=[_memory("想早点睡")]))
    assert "Verified memory view exceeds its character budget" in caplog.text
    assert not store.memory_md_path.exists() and not store.episodes_path.exists()
    save_step(store, _step("人" * (4000 - base - 2), memories=[_memory("想早点睡")]))
    assert len(store.memory_md_path.read_text(encoding="utf-8")) == 4000
    assert store.episode_ids() == {"m_000001"}


def test_first_step_save_has_no_previous_view_digest_and_writes_no_template(tmp_path):
    """@brief 人物第一次步尾保存：写入前摘要为 None，MEMORY.md 恰为视图的 UTF-8 字节（LF，不先写英文模板），没有记忆时
    写出空的条目文件；证据是写入前摘要为 None 的视图刷新。"""
    store = _store(tmp_path)
    outcome = save_step(store, _step(facts=[_received()]))
    assert outcome.memory_before_sha256 is None
    assert outcome.created_episodes == () and outcome.merged_episodes == ()
    view = store.memory_md_path.read_bytes()
    assert view == render_memory_view("林舟", [_received()]).encode("utf-8")
    assert b"\r" not in view and b"Identity And Profile" not in view
    assert store.episodes_path.read_bytes() == b""
    evidence = memory_update_evidence(_WAKEUP_ID, _AGENT_ID, store, outcome)
    assert (evidence.wakeup_id, evidence.agent_id) == (_WAKEUP_ID, _AGENT_ID)
    assert (evidence.operation, evidence.memory_before_sha256) == ("refresh_summary", None)
    assert evidence.observation_event_id == "observation-1" and evidence.verified_event_ids == ("receipt-event",)


def test_view_is_written_only_when_its_content_changes(tmp_path, monkeypatch):
    """@brief 视图字节不变时不写 MEMORY.md，也不登记证据；已核验经历变了才整份重写。"""
    writes = []
    original = Path.write_bytes
    monkeypatch.setattr(Path, "write_bytes", lambda path, data: writes.append(path.name) or original(path, data))
    store = _store(tmp_path)
    save_step(store, _step())
    assert writes.count("MEMORY.md") == 1
    again = save_step(store, _step())
    assert writes.count("MEMORY.md") == 1 and store.write_memory_view(render_memory_view("林舟", [])) is False
    assert memory_update_evidence(_WAKEUP_ID, _AGENT_ID, store, again) is None
    changed = save_step(store, _step(facts=[_received()]))
    assert writes.count("MEMORY.md") == 2 and "receipt-event" in store.memory_md_path.read_text(encoding="utf-8")
    assert memory_update_evidence(_WAKEUP_ID, _AGENT_ID, store, changed).operation == "refresh_summary"


def test_step_save_classifies_created_and_merged_against_identities_before_the_step(tmp_path):
    """@brief 返回的条目按步前的条目身份分组：不在步前集合里的是新建，在的是合并；已核验事件身份按首次出现去重。"""
    store = _store(tmp_path)
    first = save_step(store, _step(memories=[_memory("我在楼下的面馆吃了一碗牛肉面，味道很好")]))
    assert [item["id"] for item in first.created_episodes] == ["m_000001"] and first.merged_episodes == ()
    second = save_step(store, _step(
        facts=[_received("a"), _received("b"), _received("a")],
        memories=[_memory("我在楼下的面馆吃了一碗牛肉面，味道不错"), _memory("答应同事周五交初稿", "commitment")],
        now=_NOW + timedelta(minutes=1), observation="observation-2"))
    assert [item["id"] for item in second.created_episodes] == ["m_000002"]
    assert [(item["id"], item["merge_count"]) for item in second.merged_episodes] == [("m_000001", 2)]
    assert second.verified_event_ids == ("a", "b") and second.observation_event_id == "observation-2"
    assert second.memory_before_sha256 is not None


def test_no_update_evidence_when_nothing_was_written_and_the_view_is_unchanged(tmp_path):
    """@brief 有新建或合并就登记 append_episodes；既没有条目变化、视图也没变时不登记；只有视图变化时登记 refresh_summary，
    写入前摘要等于上一次写出的视图摘要。"""
    store = _store(tmp_path)
    written = save_step(store, _step(memories=[_memory("想早点睡")]))
    appended = memory_update_evidence(_WAKEUP_ID, _AGENT_ID, store, written)
    assert (appended.operation, appended.created_episode_ids, appended.merged_episode_ids) == (
        "append_episodes", ("m_000001",), ())
    assert appended.episodes == ({"id": "m_000001", "type": "goal", "text": "想早点睡", "epistemic_status": "subjective"},)
    assert memory_update_evidence(_WAKEUP_ID, _AGENT_ID, store, save_step(store, _step())) is None
    refreshed = memory_update_evidence(_WAKEUP_ID, _AGENT_ID, store, save_step(store, _step(facts=[_received()])))
    assert refreshed.operation == "refresh_summary" and refreshed.created_episode_ids == ()
    assert refreshed.memory_before_sha256 == appended.memory_sha256 != refreshed.memory_sha256


@pytest.mark.parametrize("snapshot", [None, [], "subjective", 1])
def test_memory_snapshots_are_exactly_four_subjective_keys(snapshot):
    """@brief 共用的快照校验拒绝非对象；合法条目的快照恰为四个基本键，非主观类型以快照不合法拒绝；带 source 的五键快照
    不合法。"""
    assert valid_subjective_snapshot(snapshot) is False
    valid = {"id": "memory", "type": "goal", "text": "我想读书", "epistemic_status": "subjective"}
    assert _episode_snapshot({**valid, "agent_id": 1, "merge_count": 2}) == valid
    with pytest.raises(RuntimeError, match="^Subjective memory snapshot is invalid$"):
        _episode_snapshot({**valid, "type": "event"})
    assert valid_subjective_snapshot({**valid, "type": "reflection", "source": "reflection"}) is False
