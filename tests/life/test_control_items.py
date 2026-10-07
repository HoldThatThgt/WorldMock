"""@brief 活动控制工具只读取现行逐项请求，旧平行数组记录明确拒绝。

环境侧的记忆、看法与偏好、进度、零点计划和记忆写入点读取同一批真实 items 事件；冻结的旧运行记录仅作为拒绝样本。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel

from tests.life.conftest import (activity_name, advance_next, conclude_request, pause_request, report_request,
                                 stage_day_plan, start_activity, wake_by_mail)
from tests.life.routing_fixtures import with_colleague
from tests.life.test_memory_layer import _act, _memo, _view, _walk
from worldmock.errors import IntegrityError
from worldmock.life import memory_layer, progress_layer
from worldmock.life.control_items import CONTROL_TOOLS, ControlItem, control_errands, control_items, control_request
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


def test_current_shape_items_carry_views_as_subject_memories() -> None:
    """现行形状一项一个对象：结束工具每项先普通记忆（subject 为 null）、后看法（about 写作 subject）；播报与暂停没有记忆；
    进度层的现行请求参数保留活动名、自述与普通记忆，看法与偏好与请求级 errands 一律不带。"""
    request = conclude_request(_WORK, _WAIT, summary=["纪要写完了。", "等完了。"], memories=[[_PLAIN], []],
                               views=[[_FRIEND, _TASTE], []])
    work, wait = control_items("conclude_activity", request)
    assert (work.activity_name, work.summary, wait.activity_name, wait.summary) == (
        _WORK, "纪要写完了。", _WAIT, "等完了。")
    assert work.memories == (_stored(_PLAIN, None), _stored(_FRIEND, "沈昊"), _stored(_TASTE, "拿铁"))
    assert (work.plain_memories, work.views) == ((_stored(_PLAIN, None),), work.memories[1:])
    assert wait == ControlItem(_WAIT, "等完了。")
    assert control_items("conclude_activity", request.model_dump(mode="json")) == (work, wait)
    assert control_request("conclude_activity", request) == {"items": [
        {"activity_name": _WORK, "summary": "纪要写完了。", "memories": [_PLAIN]},
        {"activity_name": _WAIT, "summary": "等完了。", "memories": []}]}
    assert "errands" not in control_request("conclude_activity", request)
    for tool, model in (("report_status", report_request), ("pause_activity", pause_request)):
        read = control_items(tool, model(_WAIT, _WORK, summary=["还在等。", "写到一半。"]))
        assert read == (ControlItem(_WAIT, "还在等。"), ControlItem(_WORK, "写到一半。"))
        assert control_request(tool, model(_WAIT)) == {"items": [
            {"activity_name": _WAIT, "summary": model(_WAIT).items[0].summary}]}


def test_recorded_parallel_array_requests_are_rejected() -> None:
    """@brief 三种冻结旧记录无论记忆字段是否齐全，都按旧请求形状拒绝。"""
    records = json.loads(_FIXTURE.read_text(encoding="utf-8"))["records"]
    assert {record["run"] for record in records} == {
        "2026-09-28-deepseek-flash-after-wake-fix", "2026-09-28-c9a4fc1-day-r3", "2026-09-28-4a5b0e6-day"}
    assert {record["tool_name"] for record in records} == set(CONTROL_TOOLS)
    for record in records:
        tool, request = record["tool_name"], record["request"]
        assert "items" not in request and len(request["task"]) == len(request["summary"])
        with pytest.raises(IntegrityError, match=f"{tool} request shape is not recognised"):
            control_items(tool, request)


@pytest.mark.parametrize("tool", CONTROL_TOOLS)
def test_current_requests_preserve_each_items_content_and_order(tool: str) -> None:
    """@brief 三个工具的模型和 JSON 都保留逐项顺序，自述和记忆不会跨活动串用。"""
    summaries = ["纪要这一项。", "等待这一项。"]
    if tool == "conclude_activity":
        request = conclude_request(_WORK, _WAIT, summary=summaries, memories=[[_PLAIN], []],
                                   views=[[_FRIEND], [_TASTE]])
    else:
        builder = report_request if tool == "report_status" else pause_request
        request = builder(_WORK, _WAIT, summary=summaries)
    items = control_items(tool, request)
    assert control_items(tool, request.model_dump(mode="json")) == items
    assert [(item.activity_name, item.summary) for item in items] == list(zip((_WORK, _WAIT), summaries, strict=True))
    if tool == "conclude_activity":
        assert items[0].memories == (_stored(_PLAIN, None), _stored(_FRIEND, "沈昊"))
        assert items[1].memories == (_stored(_TASTE, "拿铁"),)
    else:
        assert all(item.memories == () for item in items)


@pytest.mark.parametrize("tool,request_body", [
    ("start_activity", {"items": []}),
    ("report_status", ["不是对象"]),
    ("report_status", {"session_ids": ["session-1"], "summary": "最早的会话形状"}),
    ("report_status", {"items": {"activity_name": _WAIT, "summary": "继续。"}, "errands": []}),
    ("pause_activity", {"items": [{"activity_name": _WAIT}], "errands": []}),
    ("pause_activity", {"items": [{"activity_name": 7, "summary": "先停。"}], "errands": []}),
    ("conclude_activity", {"items": [{"activity_name": _WAIT, "summary": "等完了。", "memories": []}], "errands": []}),
    ("conclude_activity", {"items": [{"activity_name": _WAIT, "summary": "等完了。", "memories": ["一句话"],
                                      "views": []}], "errands": []}),
    ("conclude_activity", {"items": [{"activity_name": _WAIT, "summary": "等完了。", "memories": [],
                                      "views": [{**_FRIEND, "about": None}]}], "errands": []}),
    ("conclude_activity", {"task": [_WAIT, _WORK], "summary": ["等完了。"], "memories": [[], []]}),
    ("conclude_activity", {"task": [_WAIT], "summary": ["等完了。"], "memories": [[_PLAIN], [_PLAIN]]}),
    ("conclude_activity", {"task": [_WAIT], "summary": ["等完了。"], "memories": [[{**_PLAIN, "subject": 3}]]}),
    ("report_status", {"task": [7], "summary": ["继续。"]}),
])
def test_unrecognised_or_malformed_requests_fail_as_integrity_errors(tool: str, request_body) -> None:
    """@brief 旧数组、最早会话形状、非控制工具以及现行形状的缺字段和类型错误都明确拒绝。"""
    with pytest.raises(IntegrityError):
        control_items(tool, request_body)


@pytest.mark.parametrize("tool", CONTROL_TOOLS)
def test_a_current_request_without_errands_is_rejected(tool: str) -> None:
    """@brief 带 items 而缺请求级 errands 的控制请求是旧形状记录（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-校验与错误 、2.9）：唯一读取
    入口 control_items 以完整性故障拒绝，经它读取的投影（进度层参数 control_request）、请求级变更的读取
    （control_errands）与收尾记忆的判定随之拒绝，不留读取分支；errands 不是列表同样拒绝。带空 errands 的同一请求照常读出。"""
    builder = {"report_status": report_request, "pause_activity": pause_request, "conclude_activity": conclude_request}
    current = builder[tool](_WAIT, summary="等完了。").model_dump(mode="json")
    assert current["errands"] == []
    assert control_items(tool, current) == (ControlItem(_WAIT, "等完了。"),)
    lacking = {"items": current["items"]}
    readers = [lambda request: control_items(tool, request), lambda request: control_errands(tool, request),
               lambda request: control_request(tool, request)]
    if tool == "conclude_activity":
        readers.append(conclusion_has_memories)
    for read in readers:
        with pytest.raises(IntegrityError, match=f"{tool} request lacks errands"):
            read(lacking)
        with pytest.raises(IntegrityError, match=f"{tool} request field must be a list"):
            read({**lacking, "errands": None})


_ERRAND_KEYS = ["change", "name", "item", "quantity", "memo", "remind_time", "reason"]
_ERRANDS = [
    {"change": "新增", "name": "补一份素炒什锦食材包", "item": "素炒什锦食材包", "quantity": 1,
     "memo": "下班前下单送到家", "remind_time": "17:00", "reason": None},
    {"change": "更新", "name": "囤冰红茶", "item": None, "quantity": None, "memo": "周末去超市",
     "remind_time": None, "reason": None},
    {"change": "取消", "name": "晚饭吃掉素炒什锦", "item": None, "quantity": None, "memo": None,
     "remind_time": None, "reason": "改在公司吃了"},
]


class _ErrandChange(BaseModel):
    """测试用的事务变更模型：七个键，声明顺序与请求形状相反（读出的键序不取决于模型或请求里的写法顺序）。"""

    reason: str | None
    remind_time: str | None
    memo: str | None
    quantity: int | None
    item: str | None
    name: str
    change: str


class _ErrandRequest(BaseModel):
    """测试用的控制请求模型：逐项内容与请求级 errands。"""

    items: list[dict[str, Any]]
    errands: list[_ErrandChange]


@pytest.mark.parametrize("tool", CONTROL_TOOLS)
def test_control_errands_reads_every_change_in_order(tool: str) -> None:
    """@brief control_errands 是请求级事务变更的读取入口：模型（嵌套的变更模型按 JSON 形状导出）与 JSON 形状都按请求顺序
    读出每条变更，每条恰好七键、键序固定、值照写；没有变更时为空。不是控制工具、请求不是对象、缺 errands、errands 不是
    列表、某条不是对象、某条缺一个键都是完整性故障。"""
    body = {"items": [{"activity_name": _WAIT, "summary": "等完了。"}], "errands": _ERRANDS}
    read = control_errands(tool, {"errands": [dict(reversed(entry.items())) for entry in _ERRANDS], "items": []})
    assert read == tuple(_ERRANDS) and all(list(entry) == _ERRAND_KEYS for entry in read)
    model = _ErrandRequest.model_validate(body)
    assert control_errands(tool, model) == tuple(_ERRANDS) == control_errands(tool, model.model_dump(mode="json"))
    builder = {"report_status": report_request, "pause_activity": pause_request, "conclude_activity": conclude_request}
    real = builder[tool](_WAIT, errands=_ERRANDS)
    assert control_errands(tool, real) == tuple(_ERRANDS) == control_errands(tool, real.model_dump(mode="json"))
    assert control_errands(tool, {**body, "errands": []}) == ()
    lacking = {key: value for key, value in _ERRANDS[2].items() if key != "reason"}
    for wrong_tool, request, message in [
        ("start_activity", body, "Tool is not an activity control tool: start_activity"),
        (tool, ["不是对象"], f"{tool} request is not an object"),
        (tool, {"items": body["items"]}, f"{tool} request lacks errands"),
        (tool, {**body, "errands": _ERRANDS[0]}, f"{tool} request field must be a list"),
        (tool, {**body, "errands": ["新增"]}, f"{tool} request entry is not an object"),
        (tool, {**body, "errands": [_ERRANDS[0], lacking]}, f"{tool} request lacks reason"),
    ]:
        with pytest.raises(IntegrityError, match=message):
            control_errands(wrong_tool, request)


def _readers(engine) -> dict:
    """环境侧读控制请求的全部读者此刻的结果：记忆层、看法与偏好、进度层、零点计划的收尾记录与记忆写入点。"""
    memory_layer._CACHE.clear()
    context = engine.observations._layer_context(engine.context.state.agents[1], [])
    memory = memory_layer.memory_view(context, engine.context.store)
    requests = engine.context.store.read_agent_events("action_requested", 1)
    conclusions = [event for event in requests if event.data["tool_name"] == "conclude_activity"]
    observation = SimpleNamespace(data={"wake_reasons": []})
    return {"memory_layer": memory.layer, "persona_views": memory_layer.persona_views(context, engine.context.store),
            "progress_layer": progress_layer.progress_view(context, engine.context.store, memory.facts).layer,
            "plan": plan_inputs(engine.context, engine.observations, engine.midnight_rhythm_pending, 1).payload["收尾记录"],
            "write_points": [memory_write_points(requests, event.decision_id, observation, True)
                             for event in conclusions],
            "has_memories": [conclusion_has_memories(event.data["request"]) for event in conclusions]}


def test_readers_project_current_items_events_consistently(engine_factory) -> None:
    """@brief 真实暂停、恢复、结束和出门后，各读者从逐项事件提取自述、记忆与看法。"""
    engine = engine_factory(mutate=with_colleague)
    stage_day_plan(engine)
    friend = engine.context.scenario.agents[0].contacts[0].salutation
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
    controls = [event for event in engine.context.store.read_agent_events("action_requested", 1)
                if event.data["tool_name"] in CONTROL_TOOLS]
    assert {event.data["tool_name"] for event in controls} == set(CONTROL_TOOLS)
    assert all("items" in event.data["request"] and "task" not in event.data["request"] for event in controls)
