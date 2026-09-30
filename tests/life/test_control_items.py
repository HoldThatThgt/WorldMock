"""活动控制工具请求的归一读取（docs/dev/SPEC-dayrun-fidelity.md 第六章「迁移」与验收台账「控制工具」）。

control_items 同时读现行一项一个对象的形状与运行记录里的三代旧形状（数据集真实记录拷贝在
tests/fixtures/control-request-generations.json）；环境侧的读者（记忆层 self_notes、人设层看法与偏好、进度层参数、零点
计划输入、记忆写入点）对旧运行的事件读出与现行形状相同的结果，不再直接读 task、memories。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.life.conftest import (activity_name, advance_next, conclude_request, pause_request, report_request,
                                 stage_day_plan, start_activity, wake_by_mail)
from tests.life.test_memory_layer import _act, _memo, _view, _walk
from worldmock.errors import IntegrityError
from worldmock.life import memory_layer, progress_layer
from worldmock.life.control_items import CONTROL_TOOLS, ControlItem, control_items, control_request
from worldmock.life.day_plan_inputs import plan_inputs
from worldmock.life.memory_evidence import conclusion_has_memories, memory_write_points

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "control-request-generations.json"
_WAIT = "等待（1月5日 08:00 起）"
_WORK = "工作写会议纪要（1月5日 09:00 起）"
_PLAIN = {"type": "routine", "text": "每天歇一会儿。", "importance": 0.4, "keywords": ["休息"]}
_FRIEND = {"type": "relationship", "about": "沈昊", "text": "沈昊一向靠谱。", "importance": 0.6, "keywords": ["朋友"]}
_TASTE = {"type": "preference", "about": "拿铁", "text": "下午想喝拿铁。", "importance": 0.3, "keywords": ["咖啡"]}


def _stored(entry: dict, subject: str | None) -> dict:
    """接口合同「记忆字段」的存储形状 {type, text, importance, keywords, subject}。"""
    return {"type": entry["type"], "text": entry["text"], "importance": entry["importance"],
            "keywords": entry["keywords"], "subject": subject}


def _legacy(tool: str, request: dict, *, subject: bool = True, memories: bool = True) -> dict:
    """把现行请求改写成旧形状：task[]、summary[] 两组平行数组，结束另带 memories[][]（看法并入该项记忆之后，按 subject
    写对象）；subject 为假时记忆没有这个字段，memories 为假时是最早不带记忆的形状。"""
    items = request["items"]
    body = {"task": [item["activity_name"] for item in items], "summary": [item["summary"] for item in items]}
    if tool != "conclude_activity" or not memories:
        return body

    def old(entry: dict, about: str | None) -> dict:
        """一条旧记忆（subject 为假时去掉对象字段）。"""
        stored = _stored(entry, about)
        return stored if subject else {key: value for key, value in stored.items() if key != "subject"}

    body["memories"] = [[*(old(entry, None) for entry in item["memories"]),
                         *(old(view, view["about"]) for view in item["views"])] for item in items]
    return body


def test_current_shape_items_carry_views_as_subject_memories() -> None:
    """现行形状一项一个对象：结束工具每项先普通记忆（subject 为 null）、后看法（about 写作 subject）；播报与暂停没有记忆；
    进度层与交付窗口的现行形状改写只留活动名与自述（进度层另留普通记忆），看法与偏好一律不带。"""
    request = conclude_request(_WORK, _WAIT, summary=["纪要写完了。", "等完了。"], memories=[[_PLAIN], []],
                               views=[[_FRIEND, _TASTE], []])
    work, wait = control_items("conclude_activity", request)
    assert (work.activity_name, work.summary, wait.activity_name, wait.summary) == (
        _WORK, "纪要写完了。", _WAIT, "等完了。")
    assert work.memories == (_stored(_PLAIN, None), _stored(_FRIEND, "沈昊"), _stored(_TASTE, "拿铁"))
    assert (work.plain_memories, work.views) == ((_stored(_PLAIN, None),), work.memories[1:])
    assert wait == ControlItem(_WAIT, "等完了。")
    assert control_items("conclude_activity", request.model_dump(mode="json")) == (work, wait)
    assert control_request("conclude_activity", request, memories=True) == {"items": [
        {"activity_name": _WORK, "summary": "纪要写完了。", "memories": [_PLAIN]},
        {"activity_name": _WAIT, "summary": "等完了。", "memories": []}]}
    assert control_request("conclude_activity", request, memories=False) == {"items": [
        {"activity_name": _WORK, "summary": "纪要写完了。"}, {"activity_name": _WAIT, "summary": "等完了。"}]}
    for tool, model in (("report_status", report_request), ("pause_activity", pause_request)):
        read = control_items(tool, model(_WAIT, _WORK, summary=["还在等。", "写到一半。"]))
        assert read == (ControlItem(_WAIT, "还在等。"), ControlItem(_WORK, "写到一半。"))
        assert control_request(tool, model(_WAIT), memories=True) == {"items": [
            {"activity_name": _WAIT, "summary": model(_WAIT).items[0].summary}]}


def test_recorded_generations_read_their_parallel_arrays() -> None:
    """数据集三代真实记录（只有 task 与 summary、记忆没有 subject、记忆带必填可空的 subject）都读出逐项的活动名与自述；
    旧记忆按存储形状读出，没有 subject 的更早记录对象为 null，带 subject 的 relationship 读成看法。"""
    records = json.loads(_FIXTURE.read_text(encoding="utf-8"))["records"]
    assert {record["run"] for record in records} == {
        "2026-09-28-deepseek-flash-after-wake-fix", "2026-09-28-c9a4fc1-day-r3", "2026-09-28-4a5b0e6-day"}
    assert {record["tool_name"] for record in records} == set(CONTROL_TOOLS)
    for record in records:
        tool, request = record["tool_name"], record["request"]
        items = control_items(tool, request)
        assert [(item.activity_name, item.summary) for item in items] == list(zip(request["task"], request["summary"]))
        groups = request.get("memories", [[] for _ in request["task"]]) if tool == "conclude_activity" else [
            [] for _ in request["task"]]
        assert [list(item.memories) for item in items] == [
            [_stored(memory, memory.get("subject")) for memory in group] for group in groups]
    subjects = [memory["subject"] for record in records if record["tool_name"] == "conclude_activity"
                for item in control_items("conclude_activity", record["request"]) for memory in item.views]
    assert subjects == ["父亲"]


def test_equivalent_old_and_current_requests_read_the_same_items() -> None:
    """同一内容的现行请求与旧形状读出同一结果：带 subject 的旧形状完全相同；没有 subject 的更早记录只有看法的对象为 null；
    最早不带记忆的结束请求只有活动名与自述。"""
    for tool in CONTROL_TOOLS:
        request = (conclude_request(_WORK, _WAIT, memories=[[_PLAIN], []], views=[[_FRIEND], [_TASTE]])
                   if tool == "conclude_activity" else report_request(_WORK, _WAIT))
        current = control_items(tool, request)
        body = request.model_dump(mode="json")
        assert control_items(tool, _legacy(tool, body)) == current
        unnamed = control_items(tool, _legacy(tool, body, subject=False))
        assert [[{**memory, "subject": None} for memory in item.memories] for item in current] == [
            list(item.memories) for item in unnamed]
        assert [(item.activity_name, item.summary, item.memories) for item in control_items(
            tool, _legacy(tool, body, memories=False))] == [(item.activity_name, item.summary, ()) for item in current]


@pytest.mark.parametrize("tool,request_body", [
    ("start_activity", {"items": []}),
    ("report_status", ["不是对象"]),
    ("report_status", {"session_ids": ["session-1"], "summary": "最早的会话形状"}),
    ("report_status", {"items": {"activity_name": _WAIT, "summary": "继续。"}}),
    ("pause_activity", {"items": [{"activity_name": _WAIT}]}),
    ("pause_activity", {"items": [{"activity_name": 7, "summary": "先停。"}]}),
    ("conclude_activity", {"items": [{"activity_name": _WAIT, "summary": "等完了。", "memories": []}]}),
    ("conclude_activity", {"items": [{"activity_name": _WAIT, "summary": "等完了。", "memories": ["一句话"],
                                      "views": []}]}),
    ("conclude_activity", {"items": [{"activity_name": _WAIT, "summary": "等完了。", "memories": [],
                                      "views": [{**_FRIEND, "about": None}]}]}),
    ("conclude_activity", {"task": [_WAIT, _WORK], "summary": ["等完了。"], "memories": [[], []]}),
    ("conclude_activity", {"task": [_WAIT], "summary": ["等完了。"], "memories": [[_PLAIN], [_PLAIN]]}),
    ("conclude_activity", {"task": [_WAIT], "summary": ["等完了。"], "memories": [[{**_PLAIN, "subject": 3}]]}),
    ("report_status", {"task": [7], "summary": ["继续。"]}),
])
def test_unrecognised_or_malformed_requests_fail_as_integrity_errors(tool: str, request_body) -> None:
    """认不出的形状（含最早的 session_ids 形状与非控制工具）与缺字段、类型不符、平行数组逐项对不齐（记忆组数与活动数
    不同也不按 zip 截断）的记录按数据不一致失败，不猜测。"""
    with pytest.raises(IntegrityError):
        control_items(tool, request_body)


def _readers(engine) -> dict:
    """环境侧读控制请求的全部读者此刻的结果：记忆层、看法与偏好、进度层、零点计划的收尾记录与记忆写入点。"""
    memory_layer._CACHE.clear()
    context = engine._layer_context(engine.state.agents[1], (), [])
    memory = memory_layer.memory_view(context, engine.store)
    requests = engine.store.read_agent_events("action_requested", 1)
    conclusions = [event for event in requests if event.data["tool_name"] == "conclude_activity"]
    observation = SimpleNamespace(data={"wake_reasons": []})
    return {"memory_layer": memory.layer, "persona_views": memory_layer.persona_views(context, engine.store),
            "progress_layer": progress_layer.progress_view(context, engine.store, memory.facts).layer,
            "plan": plan_inputs(engine, 1).payload["收尾记录"],
            "write_points": [memory_write_points(requests, event.decision_id, observation, True)
                             for event in conclusions],
            "has_memories": [conclusion_has_memories(event.data["request"]) for event in conclusions]}


def _old_run(engine, monkeypatch, **shape) -> None:
    """让存储读出的控制请求变成旧形状（旧运行的事件），其余事件原样。"""
    read = engine.store.read_agent_events

    def legacy_events(event_type, agent_id, after=0):
        """action_requested 中三个控制工具的请求改写成旧形状。"""
        events = read(event_type, agent_id, after)
        if event_type != "action_requested":
            return events
        return [event.model_copy(update={"data": {**event.data, "request": _legacy(
            event.data["tool_name"], event.data["request"], **shape)}})
                if event.data.get("tool_name") in CONTROL_TOOLS else event for event in events]

    monkeypatch.setattr(engine.store, "read_agent_events", legacy_events)


def test_readers_give_old_run_events_the_same_result(engine_factory, monkeypatch) -> None:
    """真实运行里暂停、恢复并带记忆与看法结束一段等待后出门：记忆层 self_notes、人设层看法与偏好、进度层参数、零点计划的
    收尾记录与记忆写入点对同一批事件的旧形状读出与现行形状相同的结果；没有 subject 的更早记录不产生看法与偏好（它们在
    改形前同样读不出对象），其余读者结果不变。"""
    engine = engine_factory()
    stage_day_plan(engine)
    friend = engine.scenario.agents[0].contacts[0].salutation
    _, waited = _act(engine, "g-wait", "start_activity", start_activity("等待", "自己"))
    name = activity_name(engine, waited.activity_id)
    wake_by_mail(engine, offset=120)
    advance_next(engine)
    _act(engine, "g-pause", "pause_activity", pause_request(name, summary="先停一下"))
    _act(engine, "g-resume", "report_status", report_request(name, summary="接着等"))
    wake_by_mail(engine, offset=120)
    advance_next(engine)
    _act(engine, "g-conclude", "conclude_activity", conclude_request(
        name, summary="等完了", memories=[[_memo("routine", "每天歇一会儿")]],
        views=[[_view("relationship", "沈昊靠谱", friend), _view("preference", "喜欢安静", "环境")]]))
    current = _readers(engine)
    assert current["persona_views"]["relationships"] == [{"person": friend, "view": "沈昊靠谱"}]
    assert [row["想记住的"] for row in current["plan"]] == [["每天歇一会儿", "沈昊靠谱", "喜欢安静"]]
    assert current["write_points"] == [["activity_conclusion"]] and current["has_memories"] == [True]
    _walk(engine, "g-walk")
    current = _readers(engine)
    notes = [note["text"] for entry in current["memory_layer"]["earlier_today"] for note in entry["self_notes"]]
    assert notes == ["先停一下", "等完了", "每天歇一会儿"]
    with monkeypatch.context() as patched:
        _old_run(engine, patched)
        assert _readers(engine) == current
    with monkeypatch.context() as patched:
        _old_run(engine, patched, subject=False)
        unnamed = _readers(engine)
    assert unnamed == {**current, "persona_views": {"relationships": [], "preferences": []}}
