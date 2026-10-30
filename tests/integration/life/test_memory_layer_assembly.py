"""观察路径上的记忆层：「可查」的最早日期、「今天」每次决策只在末尾追加、兜底与上限、换存储对象后逐字节重算、
缓存在换日、换固定部分与条目变少时从头递推，以及整层一个观察分件。"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from tests.integration.runtime_fixtures import branch_of
from tests.life.conftest import activity_name, advance_until_ready, conclude_request, do_at, sleep, stage_day_plan
from tests.life.environment_runtime import act, observe_only, until_ended
from tests.life.observation_record import record_observations
from tests.unit.life.test_memory_layout import _entry
from worldmock.agents.prompt.narration import render_layer_json
from worldmock.agents.prompt.prompt_schemas import validate_layer
from worldmock.foundation.serialization import canonical_json, is_sha256
from worldmock.recording_engine.events.models import EventDraft
from worldmock.recording_engine.storage.store import FootprintStore
from worldmock.simulation_engine.observation.layers import memory_layer
from worldmock.simulation_engine.observation.memory import layout
from worldmock.simulation_engine.observation.memory.layout import assemble_layer, layer_chars

ZONE = "Asia/Shanghai"


def test_the_first_observation_names_the_run_start_date_and_has_no_entries_today(engine_factory):
    """新引擎第一次观察：「可查」是运行开始时刻的场景本地日期，「今天」为空，键序固定，通过记忆层校验。"""
    engine = engine_factory()
    layer = observe_only(engine, "first")["memory_layer"]
    config = engine.context.config
    assert layer["可查"] == {"最早日期": config.start_time.astimezone(ZoneInfo(config.timezone)).date().isoformat()}
    assert layer["今天"] == [] and list(layer) == ["layer", "可查", "近三天", "今天"]
    validate_layer(layer)


def _evening(persona, config):
    """运行配置：2026-01-05 20:00 开始，跨过零点。"""
    config["start_time"] = datetime.fromisoformat("2026-01-05T20:00:00+08:00")
    config["stop_time"] = datetime.fromisoformat("2026-01-06T20:00:00+08:00")


def test_the_available_date_stays_after_midnight_and_in_a_branch(engine_factory, tmp_path):
    """运行从 20:00 开始、睡过零点：零点前后两次观察的「可查」相同；此刻开出的恢复分支里第一次观察的「可查」也相同。"""
    engine = engine_factory(mutate=_evening)
    stage_day_plan(engine, submitted=True)
    before = observe_only(engine, "evening")["memory_layer"]["可查"]
    _, nap = act(engine, "night", sleep(300))
    until_ended(engine, nap.activity_id)
    assert engine.context.state.current_time > int(datetime.fromisoformat("2026-01-06T00:00:00+08:00").timestamp())
    after = observe_only(engine, "morning")["memory_layer"]["可查"]
    source = engine.context.store.read_last_event().event_id
    child = branch_of(engine, tmp_path, "resume", source)
    try:
        branched = observe_only(child, "branch")["memory_layer"]["可查"]
    finally:
        child.close()
    assert before == after == branched == {"最早日期": "2026-01-05"}


def test_each_decision_only_extends_today_at_the_end_of_the_rendered_layer(engine_factory):
    """同一天依次开始、收尾、开始，每次之前观察：后一次渲染出的记忆层以前一次去掉末尾 ]} 的文本开头，「今天」每次多一项。"""
    engine = engine_factory()
    rendered, sizes = [], []
    for label, step in (("rest", lambda: act(engine, "rest", do_at("家", "测试休息"))),
                        ("end", lambda: act(engine, "end", conclude_request(activity_name(engine, rest_id[0])),
                                            "conclude_activity")),
                        ("again", lambda: act(engine, "again", do_at("家", "测试休息")))):
        layer = observe_only(engine, label)["memory_layer"]
        rendered.append(render_layer_json(layer))
        sizes.append(len(layer["今天"]))
        _, result = step()
        if label == "rest":
            rest_id = [result.activity_id]
            assert advance_until_ready(engine)
    layer = observe_only(engine, "last")["memory_layer"]
    rendered.append(render_layer_json(layer))
    sizes.append(len(layer["今天"]))
    assert sizes == [0, 1, 2, 3]
    for earlier, later in zip(rendered, rendered[1:]):
        assert later.startswith(earlier[:-2])


def _three_decisions(engine):
    """当天前三次决策：开始测试休息，推进到它结束后收尾它（总结 60 字以上），再开始测试休息；返回此刻观察的记忆层。"""
    _, rest = act(engine, "rest", do_at("家", "测试休息"))
    assert advance_until_ready(engine)
    act(engine, "end", conclude_request(activity_name(engine, rest.activity_id), summary="歇" * 64),
        "conclude_activity")
    act(engine, "again", do_at("家", "测试休息"))
    return observe_only(engine, "fourth")["memory_layer"]


def _backstopped(engine, monkeypatch, caplog) -> tuple[dict, dict, int]:
    """清空缓存、把上限与目标都设成此刻记忆层的字数减一再观察：返回补丁前后的记忆层与补丁前的字数。"""
    before = _three_decisions(engine)
    size = layer_chars(before)
    memory_layer._FOLDS.clear()
    monkeypatch.setattr(layout, "MEMORY_LAYER_CHARS", size - 1)
    monkeypatch.setattr(layout, "MEMORY_BACKSTOP_CHARS", size - 1)
    caplog.clear()
    return before, observe_only(engine, "patched")["memory_layer"], size


def test_the_observation_path_applies_the_backstop_and_the_budget(engine_factory, monkeypatch, caplog):
    """上限与目标设成 L−1：「今天」仍三项，只有收尾那一项的「自述」换成「…」，没有计数行，恰写一条警告；上限设成固定部分
    的字数减一时观察以 RuntimeError 停止。"""
    engine = engine_factory()
    before, after, size = _backstopped(engine, monkeypatch, caplog)
    today, original = after["今天"], before["今天"]
    assert len(today) == 3 and "自述" not in original[0] and "自述" not in original[2]
    assert today[1]["自述"] == ["…"] and {key: today[1][key] for key in ("时刻", "类别", "核验")} == {
        key: original[1][key] for key in ("时刻", "类别", "核验")}
    assert today[0] == original[0] and today[2] == original[2] and not any("条数" in item for item in today)
    assert layer_chars(after) <= size - 1
    warnings = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
    assert warnings == [f"Memory layer backstop rewrote today from {size} to {layer_chars(after)} characters "
                        f"(target {size - 1})"]
    fixed = layer_chars(assemble_layer(after["可查"]["最早日期"], after["近三天"], []))
    memory_layer._FOLDS.clear()
    monkeypatch.setattr(layout, "MEMORY_LAYER_CHARS", fixed - 1)
    with pytest.raises(RuntimeError) as refused:
        observe_only(engine, "over")
    assert str(refused.value) == f"Memory layer exceeds {fixed - 1} characters"
    assert "Memory layer has" in caplog.text and "above its" in caplog.text


def test_a_new_store_rebuilds_today_byte_for_byte(engine_factory, monkeypatch, caplog, tmp_path):
    """触发一次兜底之后：恢复分支（新存储对象，缓存为空）与清空缓存后的父运行，此刻观察的记忆层与触发后的观察逐字节相同。"""
    engine = engine_factory()
    _, after, _ = _backstopped(engine, monkeypatch, caplog)
    triggered = render_layer_json(after)
    source = engine.context.store.read_last_event().event_id
    child = branch_of(engine, tmp_path, "resume", source)
    try:
        assert render_layer_json(observe_only(child, "branch")["memory_layer"]) == triggered
    finally:
        child.close()
    memory_layer._FOLDS.clear()
    assert render_layer_json(observe_only(engine, "parent")["memory_layer"]) == triggered


def _context(day: str):
    """只带人物、此刻与时区的替身上下文：此刻是 day 的 09:00。"""
    moment = int(datetime.fromisoformat(f"{day}T09:00:00+08:00").timestamp())
    return SimpleNamespace(agent_id=1, state=SimpleNamespace(current_time=moment),
                           config=SimpleNamespace(timezone=ZONE))


def _fresh(store, context, entries, recent):
    """清空缓存后以这些输入递推的结果。"""
    memory_layer._FOLDS.clear()
    return memory_layer._today(context, store, entries, "2026-01-05", recent)


def test_today_restarts_when_the_day_the_fixed_part_or_the_entry_count_changes(tmp_path, monkeypatch):
    """同一存储对象先在 D 日以三条条目递推；换到 D+1 日、固定部分变了、条目变少，结果都等于清空缓存后从头递推的结果。"""
    store = FootprintStore.create(tmp_path / "run", {"run_id": "run", "agent_ids": [1]},
                                  {"version": 0, "current_time": 0})
    first = [_entry(f"0{hour}:00", "生活", [f"第{hour}件"], [f"自述{hour}"]) for hour in range(7, 10)]
    other = [_entry(f"1{hour}:00", "工作", [f"另{hour}件"]) for hour in range(3)]
    recent = {"饮食": [{"日期": "2026-01-04", "星期": "星期日", "回顾": "饭" * 200}]}
    today = _context("2026-01-05")

    def restored():
        """恢复到 D 日以三条条目、recent={} 递推之后的状态。"""
        return _fresh(store, today, first, {})

    try:
        assert restored() == [entry.item for entry in first]
        assert memory_layer._today(_context("2026-01-06"), store, other, "2026-01-05", {}) == [
            entry.item for entry in other] == _fresh(store, _context("2026-01-06"), other, {})
        restored()
        size = layer_chars(assemble_layer("2026-01-05", {}, [entry.item for entry in first]))
        monkeypatch.setattr(layout, "MEMORY_LAYER_CHARS", size)
        monkeypatch.setattr(layout, "MEMORY_BACKSTOP_CHARS", size)
        changed = memory_layer._today(today, store, first, "2026-01-05", recent)
        assert changed == _fresh(store, today, first, recent) != [entry.item for entry in first]
        monkeypatch.undo()
        restored()
        assert memory_layer._today(today, store, first[:2], "2026-01-05", {}) == [
            entry.item for entry in first[:2]] == _fresh(store, today, first[:2], {})
    finally:
        store.close()


def test_each_changed_memory_layer_is_stored_as_one_whole_part(engine_factory, monkeypatch):
    """两次中间隔一次决策的观察：两份存储正文的 memory_layer 都是 sha256 且互不相同，parts 表里各有一行；读回的观察与
    构建时的记忆层在规范 JSON 下相同。"""
    recorded = record_observations(monkeypatch)
    engine = engine_factory()
    observe_only(engine, "before")
    act(engine, "rest", do_at("家", "测试休息"))
    observe_only(engine, "after")
    store = engine.context.store
    (first_id, first), (*_, (second_id, second)) = recorded[0], recorded
    references = []
    for identity, built in ((first_id, first), (second_id, second)):
        raw = json.loads(store.connection.execute("SELECT body FROM events WHERE event_id=?",
                                                  (identity,)).fetchone()[0])["data"]["memory_layer"]
        assert is_sha256(raw)
        assert store.connection.execute("SELECT COUNT(*) FROM parts WHERE sha256=?", (raw,)).fetchone()[0] == 1
        assert canonical_json(store.read_event(identity).data["memory_layer"]) == canonical_json(built["memory_layer"])
        references.append(raw)
    assert references[0] != references[1]
