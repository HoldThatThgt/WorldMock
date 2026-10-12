"""进度层结果明细：Schema 认得的键与形状，退役的键与写错的形状被拒；终态结果按活动快照投影出 contents。"""

from __future__ import annotations

import pytest

from tests.data_paths import ENVIRONMENT_ROOT
from worldmock.life.prompt_schemas import validate_layer
from worldmock.life.result_projection import public_result

_AT = "2026-01-05T08:00:00+08:00"
_CONTENT = {"app": "Keep", "title": "测试慢跑", "summary": "测试慢跑的测试用合成摘要。", "duration": "约 20 分钟",
            "pushed": False}
_DONE = {"title": "备菜", "summary": "备菜的测试用合成摘要。", "duration": "约 20 分钟", "finished": True}
_UNDONE = {"title": "吃饭", "summary": "", "duration": "约 25 分钟", "finished": False}
_DELIVERED = {"product": "柠檬茶", "quantity": "1 份", "channel": "饿了么", "destination": "家",
              "expected_at": "2026-01-05T08:25:00+08:00", "paid": "17 元"}
_IN_STORE = {"product": "柠檬茶", "quantity": "1 份", "channel": "到店", "destination": "四季便利",
             "expected_at": _AT, "price": "12 元"}
_ORDERABLE = {"product": "柠檬茶", "route": "外卖", "store": "四季便利", "apps": ["美团外卖"], "price": "12 元",
              "delivery_fee": "5 元", "expected_at": "2026-01-05T08:25:00+08:00", "now": "可以下单",
              "reason": None}
_NOT_ORDERABLE = {"product": "冰美式咖啡", "route": None, "store": None, "apps": [], "price": "22 元",
                  "delivery_fee": None, "expected_at": None, "now": "不可下单", "reason": "这个 app 里没有"}
_ANSWERED = {"app": "美团外卖", "scope": "只列经这个 app 能买的", "rows": [_ORDERABLE, _NOT_ORDERABLE],
             "omitted": "还有 3 件没列"}
_UNFINISHED = {"app": "饿了么", "state": "没有查完"}
_EVERY_KEY = {
    "purchases": [_DELIVERED], "planned": [{"name": "进行测试休息（1月5日 08:00 起）", "duration": "约 15 分钟"}],
    "content": {**_CONTENT, "app": None}, "contents": [_DONE],
    "query": {"app": None, "scope": "列出全部途径", "rows": [_ORDERABLE]},
    "arrival": {"place": "公司", "access": "进入"}, "recall": {"date": "2026-01-04"},
    "reason": "测试原因。", "next_step": "测试下一步。", "fix": "测试改法。", "items": [{"written": "咖啡"}],
    "outcome": [{"activity": "进行测试休息（1月5日 08:00 起）", "outcome": "提前结束"}],
    "resolved": [{"written": "柠檬 茶", "as": "柠檬茶"}], "notes": ["测试提示。"],
    "errands": [{"errand": "备好饮料", "change": "新增"}],
}


def _layer(details: dict) -> dict:
    """只有一条请求记录的进度层，这条记录的结果明细是 details。"""
    result = {"occurred_at": _AT, "status": "completed", "error_code": None, "message": "Activity completed",
              "details": details}
    record = {"occurred_at": _AT, "type": "tool_call", "tool": "start_side_activity",
              "arguments": {"action": "interact", "target": ["Keep"]}, "result": result}
    return {"layer": "进度层", "session_started_at": _AT, "rejected_today": [], "events": [record]}


def _problem(details: dict) -> str | None:
    """校验带这份明细的进度层：通过时为 None，不通过时是校验错误的信息。"""
    try:
        validate_layer(_layer(details))
    except ValueError as error:
        return str(error)
    return None


@pytest.mark.parametrize("details", [
    pytest.param({"content": _CONTENT, "planned": [{"name": "使用Keep", "duration": "约 20 分钟"}]}, id="content"),
    pytest.param({"contents": [_DONE, _UNDONE]}, id="contents"),
    pytest.param({"purchases": [_DELIVERED, _IN_STORE]}, id="purchase-channel"),
    pytest.param({"query": _ANSWERED}, id="query-answered"),
    pytest.param({"query": _UNFINISHED}, id="query-unfinished"),
    pytest.param(_EVERY_KEY, id="every-key"),
])
def test_result_details_schema_accepts_every_listed_key(details):
    """带 content、contents、购买行的 channel、查完与没有查完两种 query 的进度层，以及键表全部键同时出现的进度层，
    各自通过层校验。"""
    assert _problem(details) is None


@pytest.mark.parametrize("details", [
    pytest.param({"purchases": [{key: value for key, value in _DELIVERED.items() if key != "channel"}]},
                 id="purchase-without-channel"),
    pytest.param({"contents": []}, id="empty-contents"),
    pytest.param({"contents": [{**_DONE, "app": "Keep"}]}, id="contents-row-with-an-extra-key"),
    pytest.param({"goods": [{"name": "冰美式咖啡", "price": "22 元"}]}, id="retired-goods"),
    pytest.param({"app": "美团外卖"}, id="retired-app"),
    pytest.param({"duration": "约 3 分钟"}, id="retired-duration"),
])
def test_result_details_schema_rejects_retired_keys(details):
    """购买行缺 channel、contents 是空数组、contents 的行多一个键、带 goods、app、duration 三个退役键之一，
    各自被层校验拒绝。"""
    with pytest.raises(ValueError, match=r"Prompt layer 进度层 violates its schema at \['events', 0\]"):
        validate_layer(_layer(details))


def _row(content_id: str, finished: bool) -> dict:
    """样例环境里一条内容在 contents 里的一行（标题、简介、登记全长按目录写死）。"""
    titles = {"c-drama-1": "测试剧集第一集", "c-drama-2": "测试剧集第二集", "c-drama-3": "测试剧集第三集",
              "c-rest": "测试休息", "c-run": "测试慢跑"}
    durations = {"c-drama-1": "约 30 分钟", "c-drama-2": "约 30 分钟", "c-drama-3": "约 30 分钟",
                 "c-rest": "约 15 分钟", "c-run": "约 20 分钟"}
    title = titles[content_id]
    return {"title": title, "summary": f"{title}的测试用合成摘要。", "duration": durations[content_id],
            "finished": finished}


def _interact(status: str, played: tuple[str, ...], current: str, positions: tuple[float, float],
              app_id: str | None = "bilibili") -> dict:
    """一项 interact 活动的手工快照：已做到头的各条、当前这条、（开始或恢复时的位置，当前位置）。"""
    start, media = positions
    return {"kind": "interact", "status": status, "media_position_seconds": media,
            "data": {"app_id": app_id, "content_id": current, "start_position_seconds": start,
                     "played": [{"content_id": key, "from_position": 0.0, "to_position": 1800} for key in played]}}


def _terminal(status: str, activity: dict) -> dict:
    """活动终结时写下的第二条结果：结果的 data 只有活动快照与订单。"""
    messages = {"completed": "Activity completed", "cancelled": "Activity cancelled", "failed": "Activity failed"}
    return {"occurred_at": 1767571200, "status": status, "message": messages[status],
            "data": {"activity": activity, "orders": []}}


_DRAMA = ("c-drama-1", "c-drama-2")


@pytest.mark.parametrize("result,expected", [
    pytest.param(_terminal("cancelled", _interact("cancelled", _DRAMA, "c-drama-3", (0.0, 600.0))),
                 [_row("c-drama-1", True), _row("c-drama-2", True), _row("c-drama-3", False)],
                 id="several-played-then-the-advanced-current-one"),
    pytest.param(_terminal("cancelled", _interact("cancelled", _DRAMA[:1], "c-drama-2", (0.0, 0.0))),
                 [_row("c-drama-1", True)], id="current-one-not-advanced"),
    pytest.param(_terminal("cancelled", _interact("cancelled", (), "c-rest", (300.0, 300.0), None)), None,
                 id="nothing-advanced-since-resuming-writes-no-key"),
    pytest.param(_terminal("cancelled", _interact("cancelled", (), "c-rest", (0.0, 0.0), None)), None,
                 id="nothing-done-writes-no-key"),
    pytest.param(_terminal("completed", _interact("completed", (*_DRAMA, "c-drama-3"), "c-drama-3", (0.0, 1800.0))),
                 [_row("c-drama-1", True), _row("c-drama-2", True), _row("c-drama-3", True)],
                 id="completed-does-not-repeat-the-last-one"),
    pytest.param(_terminal("cancelled", _interact("truncated", (), "c-rest", (0.0, 420.0), None)),
                 [_row("c-rest", False)], id="truncated-at-run-end"),
    pytest.param(_terminal("failed", _interact("failed", (), "c-run", (0.0, 100.0), "keep")),
                 [_row("c-run", False)], id="failed"),
    pytest.param(_terminal("completed", {"kind": "travel", "status": "completed", "media_position_seconds": 0.0,
                                         "data": {"arrival": None}}), None, id="travel-writes-no-key"),
    pytest.param({"occurred_at": 1767571200, "status": "completed", "message": "Activities concluded",
                  "data": {"activity_ids": ["activity-a"], "errands": [], "outcome": []}}, None,
                 id="control-result-writes-no-key"),
])
def test_public_result_contents_follow_the_snapshot(frozen_inputs, result, expected):
    """终态结果按活动快照列出做过的内容：played 各条在前、finished 为真；状态不是 completed 且当前这条确有推进时再加
    一行、finished 为假；一行都没有、不是 interact 活动或是控制结果时不写 contents；每行恰为四个键、按次序写出。"""
    details = public_result(result, frozen_inputs.record(ENVIRONMENT_ROOT)["inputs"])["details"]
    if expected is None:
        assert "contents" not in details
    else:
        assert details == {"contents": expected}
        assert all(list(row) == ["title", "summary", "duration", "finished"] for row in details["contents"])
        validate_layer(_layer(details))

