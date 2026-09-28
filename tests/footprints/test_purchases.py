"""购买事实导出（docs/dev/SPEC-persona-prompt.md 第七章「导出」、第六章「混合动作的购买记录遗漏」）。

purchases.jsonl 的成功购买取动作结果顶层与逐项结果的订单，按订单去重、与整体状态无关、重放回执不重复计；
尝试下单是方式为到店或远程下单的请求；应用浏览是方式为浏览的活动。导出清单升 2，校验从事件重算并逐行核对。
"""

from __future__ import annotations

import hashlib
import json

import pytest

from worldmock.errors import IntegrityError
from worldmock.footprints.export import export_run
from worldmock.footprints.models import EventDraft
from worldmock.footprints.purchases import BROWSE_ACTIVITY_KIND, ORDER_WAYS, project_purchase_facts
from worldmock.footprints.store import FootprintStore
from worldmock.footprints.validation import validate_export
from worldmock.life.interaction_menu import PURCHASE_WAYS, WAYS
from worldmock.life.models import ActionResult, ActionStatus, ActivityKind, DeliveryState
from worldmock.life.session_actions import _parallel_result_data

_WINDOW = {"start": 100, "end": 2000, "timezone": "UTC"}
_ORDER = DeliveryState(
    order_id="order-beef-noodles", agent_id=1, product_id="beef-noodles", supply_id="supply-beef-noodles",
    location_id="noodle-shop", seller_account_id="seller:noodle-shop", price_version="price-v1", route_id=None,
    source={"kind": "fixture"}, quantity=1, destination_id="office", fulfillment="delivery", created_at=110,
    expected_at=1900, status="pending", total_price_fen=2800).model_dump(mode="json")


def _mixed_eat_and_buy_result() -> dict:
    """「吃＋远程下单」并行受理的结果：整体 in_progress，订单在顶层 data.orders 与第二项逐项结果里（根因中的形状）。"""
    eat = ActionResult(action_id="eat-and-buy", agent_id=1, status=ActionStatus.IN_PROGRESS, occurred_at=110,
                       activity_id="activity-eat-and-buy", message="Meal started",
                       data={"activity": {"activity_id": "activity-eat-and-buy", "kind": "consume"}})
    buy = ActionResult(action_id="eat-and-buy", agent_id=1, status=ActionStatus.COMPLETED, occurred_at=110,
                       message="Purchase accepted", data={"orders": [_ORDER], "total_price_fen": 2800,
                                                          "inventory_changes": [], "category_matches": []})
    return ActionResult(action_id="eat-and-buy", agent_id=1, status=ActionStatus.IN_PROGRESS, occurred_at=110,
                        message="Activities started", data=_parallel_result_data([eat, buy])).model_dump(mode="json")


def _request(action_id: str, at: int, ways: list[str], reused: bool = False) -> EventDraft:
    """一次开始活动请求（重放时 reused 为真）。"""
    return EventDraft(event_type="action_requested", occurred_at=at, agent_id=1, decision_id=f"decision-{at}",
                      action_id=action_id, source_kind="model", data={
                          "tool_name": "start_activity", "request_sha256": "0" * 64, "reused": reused,
                          "request": {"targets": [f"对象{index}" for index, _ in enumerate(ways)],
                                      "actions": ["interact"] * len(ways), "ways": ways}})


def _result(request: EventDraft, data: dict, event_type: str = "action_result") -> EventDraft:
    """请求对应的结算事件。"""
    return EventDraft(event_type=event_type, occurred_at=request.occurred_at, agent_id=1,
                      decision_id=request.decision_id, action_id=request.action_id,
                      activity_id=data.get("activity_id"), caused_by=[request.event_id], data=data)


def _browse_snapshot(status: str, elapsed: int) -> dict:
    """浏览活动的权威快照。"""
    return {"activity_id": "activity-browse", "agent_id": 1, "kind": "browse", "started_at": 130,
            "elapsed_seconds": elapsed, "status": status, "ended_at": 130 + elapsed if elapsed else None}


def _commit(store: FootprintStore, at: int, drafts: list[EventDraft]) -> None:
    """把一组事件连同推进后的时钟与下一个版本提交。"""
    store.commit({"version": store.state["version"] + 1, "current_time": at}, drafts)


@pytest.fixture
def purchase_store(tmp_path):
    """混合的吃＋买、它的重放、只浏览和被拒的到店下单，按真实先后提交。"""
    store = FootprintStore.create(tmp_path / "run", {"run_id": "run", "source_group_id": "run", "agent_ids": [1],
                                                    "model_identity": {"name": "fixture"}, "window": _WINDOW},
                                  {"version": 0, "current_time": 100})
    mixed = _request("eat-and-buy", 110, ["食用", "远程下单"])
    _commit(store, 110, [mixed, _result(mixed, _mixed_eat_and_buy_result())])
    replay = _request("eat-and-buy", 120, ["食用", "远程下单"], reused=True)
    _commit(store, 120, [replay, _result(replay, {**_mixed_eat_and_buy_result(), "reused": True})])
    browse = _request("browse", 130, ["浏览"])
    accepted = ActionResult(action_id="browse", agent_id=1, status=ActionStatus.IN_PROGRESS, occurred_at=130,
                            activity_id="activity-browse", message="Activity started").model_dump(mode="json")
    _commit(store, 130, [browse, _result(browse, {**accepted, "activity": _browse_snapshot("active", 0)},
                                         "action_accepted"), _result(browse, accepted)])
    _commit(store, 1030, [EventDraft(event_type="activity_progress", occurred_at=1030, agent_id=1,
                                     activity_id="activity-browse", data={
                                         "activity": _browse_snapshot("completed", 900),
                                         "interval_start": 130, "interval_end": 1030})])
    pickup = _request("pickup", 1040, ["到店"])
    rejected = ActionResult(action_id="pickup", agent_id=1, status=ActionStatus.REJECTED, occurred_at=1040,
                            error_code="SUPPLY_NOT_VISIBLE", message="SUPPLY_NOT_VISIBLE").model_dump(mode="json")
    _commit(store, 1040, [pickup, _result(pickup, rejected)])
    yield store
    store.close()


def _events(store: FootprintStore) -> list[dict]:
    """与导出同形的事件行。"""
    return [event.model_dump() for event in store.read_events(store.last_event_seq)]


def _ids(events: list[dict], event_type: str, action_id: str) -> list[str]:
    """某动作某类事件的身份（按提交顺序）。"""
    return [event["event_id"] for event in events
            if event["event_type"] == event_type and event["action_id"] == action_id]


def test_order_ways_and_browse_kind_are_the_interaction_menu_values() -> None:
    """投影在记录层自带的方式与活动类型常量，与交互菜单的下单方式、浏览活动类型一致。"""
    assert set(ORDER_WAYS) == set(PURCHASE_WAYS.values())
    assert BROWSE_ACTIVITY_KIND == ActivityKind.BROWSE.value == WAYS["浏览"][1]


def test_mixed_eat_and_buy_in_progress_result_counts_its_order_once(purchase_store) -> None:
    """整体 in_progress 的并行结果：订单同时在顶层与逐项结果里，只计一次购买，来源是首个结算事件。"""
    events = _events(purchase_store)
    result = next(event for event in events if event["event_type"] == "action_result")["data"]
    assert result["status"] == "in_progress"
    assert result["data"]["orders"] == [_ORDER] == result["data"]["activity_results"][1]["orders"]
    purchases = [row for row in project_purchase_facts(events) if row["fact_kind"] == "purchase"]
    assert purchases == [{
        "fact_kind": "purchase", "agent_id": 1, "order_id": "order-beef-noodles", "product_id": "beef-noodles",
        "supply_id": "supply-beef-noodles", "quantity": 1, "total_price_fen": 2800, "fulfillment": "delivery",
        "recipient_id": None, "request_message_id": None, "created_at": 110, "action_id": "eat-and-buy",
        "source_event_id": _ids(events, "action_result", "eat-and-buy")[0]}]


def test_replayed_receipt_is_not_counted_again(purchase_store) -> None:
    """重放回执带着同一订单、重放请求不是新的下单：购买与尝试下单各只有原动作一条。"""
    events = _events(purchase_store)
    assert len(_ids(events, "action_result", "eat-and-buy")) == 2
    facts = [row for row in project_purchase_facts(events) if row.get("action_id") == "eat-and-buy"]
    assert [row["fact_kind"] for row in facts] == ["purchase", "order_attempt"]
    assert facts[1] == {"fact_kind": "order_attempt", "agent_id": 1, "action_id": "eat-and-buy",
                        "request_event_id": _ids(events, "action_requested", "eat-and-buy")[0],
                        "way": "远程下单", "status": "in_progress", "error_code": None, "occurred_at": 110}


def test_browse_only_counts_as_app_browse(purchase_store) -> None:
    """只浏览没有下单：不是购买也不是尝试下单，计为一次应用浏览，状态取最后一次快照。"""
    events = _events(purchase_store)
    facts = project_purchase_facts(events)
    assert not [row for row in facts if row.get("action_id") == "browse"]
    progress = next(event["event_id"] for event in events if event["event_type"] == "activity_progress")
    assert [row for row in facts if row["fact_kind"] == "app_browse"] == [{
        "fact_kind": "app_browse", "agent_id": 1, "activity_id": "activity-browse", "started_at": 130,
        "status": "completed", "source_event_id": progress}]


def test_rejected_order_counts_as_order_attempt(purchase_store) -> None:
    """被拒的到店下单没有购买，计为带状态与错误码的尝试下单。"""
    events = _events(purchase_store)
    facts = [row for row in project_purchase_facts(events) if row.get("action_id") == "pickup"]
    assert facts == [{"fact_kind": "order_attempt", "agent_id": 1, "action_id": "pickup",
                      "request_event_id": _ids(events, "action_requested", "pickup")[0], "way": "到店",
                      "status": "rejected", "error_code": "SUPPLY_NOT_VISIBLE", "occurred_at": 1040}]


def _complete_export(store: FootprintStore, tmp_path):
    """实验结束、检查点封存后导出完整目录。"""
    store.commit({"version": store.state["version"] + 1, "current_time": _WINDOW["end"]},
                 [EventDraft(event_type="run_ended", occurred_at=_WINDOW["end"])])
    reference = store.assets.put_json({"saved": "complete"}, {"kind": "fixture"})
    point = store.checkpoint(store.state, {"agent:1": reference, "life": reference})
    directory = tmp_path / "export"
    export_run(store, point.last_event_seq, directory)
    return directory


def test_export_writes_purchase_facts_under_manifest_version_two(purchase_store, tmp_path) -> None:
    """导出清单升 2，purchases.jsonl 进入工件闭集，逐行等于从导出事件的重算，并通过完整校验。"""
    directory = _complete_export(purchase_store, tmp_path)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    events = [json.loads(line) for line in (directory / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    rows = [json.loads(line) for line in (directory / "purchases.jsonl").read_text(encoding="utf-8").splitlines()]
    assert manifest["schema_version"] == 2
    assert set(manifest["artifacts"]) == {"events", "activities", "verified_experiences", "purchases"}
    assert manifest["artifacts"]["purchases"]["rows"] == 4 and manifest["artifacts"]["purchases"]["path"] == (
        "purchases.jsonl")
    assert rows == project_purchase_facts(events)
    assert [row["fact_kind"] for row in rows] == ["purchase", "order_attempt", "order_attempt", "app_browse"]
    assert validate_export(directory)[0]["schema_version"] == 2


@pytest.mark.parametrize("rehash", [False, True])
def test_validation_recomputes_purchase_facts_and_rejects_tampering(purchase_store, tmp_path, rehash) -> None:
    """改动 purchases.jsonl：未重算哈希时字节校验拒绝；重算哈希与行数后，从事件重算的逐行核对仍拒绝。"""
    directory = _complete_export(purchase_store, tmp_path)
    path = directory / "purchases.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    raw = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows if row["fact_kind"] != "purchase")
    path.write_bytes(raw.encode("utf-8"))
    if rehash:
        manifest_path = directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["artifacts"]["purchases"].update(rows=len(rows) - 1,
                                                  sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    message = "purchase facts disagree" if rehash else "artifact hash is invalid"
    with pytest.raises(IntegrityError, match=message):
        validate_export(directory)
