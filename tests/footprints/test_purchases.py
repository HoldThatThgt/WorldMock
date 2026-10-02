"""购买事实导出（docs/dev/SPEC-persona-prompt.md 第七章「导出」、第六章「混合动作的购买记录遗漏」）。

purchases.jsonl 的成功购买取动作结果顶层与逐项结果的订单，按订单去重、与整体状态无关、重放回执不重复计；
尝试下单是方式为到店、远程下单或「购买」的请求，所写方式按宿主与环境同一张方式同义表归一（「外卖」「自取」等同义写法同样
计入，way 记归一后的方式）；应用浏览是方式为浏览的活动。导出清单升 2，校验从事件重算并逐行核对。
"""

from __future__ import annotations

import hashlib
import json

import pytest

from tests.life.conftest import engine_factory as _engine_factory  # noqa: F401
from worldmock.errors import IntegrityError
from worldmock.footprints.export import export_run
from worldmock.footprints.models import EventDraft
from worldmock.footprints.purchases import BROWSE_ACTIVITY_KIND, ORDER_WAYS, project_purchase_facts
from worldmock.footprints.store import FootprintStore
from worldmock.footprints.validation import validate_export
from worldmock.life.interaction_menu import NAMED_PURCHASE_WAYS, PURCHASE_WAYS, WAYS
from worldmock.life.models import ActionResult, ActionStatus, ActivityKind, DeliveryState, active_activities
from worldmock.life.name_resolution import WAY_SYNONYMS, way_options
from worldmock.life.session_actions import _parallel_result_data

_WINDOW = {"start": 100, "end": 2000, "timezone": "UTC"}
_ORDER = DeliveryState(
    order_id="order-beef-noodles", agent_id=1, product_id="beef-noodles", supply_id="supply-beef-noodles",
    location_id="noodle-shop", seller_account_id="seller:noodle-shop", price_version="price-v1", route_id=None,
    source={"kind": "fixture"}, quantity=1, destination_id="office", fulfillment="delivery", created_at=110,
    expected_at=1900, status="pending", total_price_fen=2800, recipient_id="contact-wanglei",
    request_message_id="reply-message-1").model_dump(mode="json")


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
                          "request": {"items": [{"target": f"对象{index}", "way": way, "from": None, "to": None}
                                                for index, way in enumerate(ways)],
                                      "answering": None, "prefer": "最快拿到" if any(
                                          way_options(way) & NAMED_PURCHASE_WAYS for way in ways) else None}})


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
    """投影在记录层自带的方式与活动类型常量，与交互菜单的下单方式（到店、远程下单与由系统定方式的「购买」）、浏览活动
    类型一致。"""
    assert set(ORDER_WAYS) == NAMED_PURCHASE_WAYS == {*PURCHASE_WAYS.values(), "购买"}
    assert BROWSE_ACTIVITY_KIND == ActivityKind.BROWSE.value == WAYS["浏览"][1]


def test_purchase_rows_carry_the_order_channel_or_no_channel() -> None:
    """purchases.jsonl 每行记订单载荷的 channel_id（from 解析为渠道时的 App 标识，docs/dev/SPEC-dayrun-fidelity.md 3.3
    解析规则）；原生订单未通过应用渠道取得时记 null；方式为「购买」的请求计为尝试下单。"""
    request = {"event_type": "action_requested", "agent_id": 1, "action_id": "buy", "event_id": "request-buy",
               "occurred_at": 110, "data": {"tool_name": "start_activity", "reused": False,
                                            "request": {"items": [{"target": "午餐", "way": "购买",
                                                                    "from": None, "to": None}],
                                                        "answering": None, "prefer": "最快拿到"}}}
    orders = [{**_ORDER, "channel_id": "hema"}, {**_ORDER, "order_id": "direct", "channel_id": None}]
    result = {"event_type": "action_result", "agent_id": 1, "action_id": "buy", "event_id": "result-buy",
              "data": {"status": "completed", "error_code": None, "data": {"orders": orders}}}
    facts = project_purchase_facts([request, result])
    assert [(row["order_id"], row["channel_id"]) for row in facts if row["fact_kind"] == "purchase"] == [
        ("order-beef-noodles", "hema"), ("direct", None)]
    assert [(row["way"], row["status"]) for row in facts if row["fact_kind"] == "order_attempt"] == [
        ("购买", "completed")]


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
        "recipient_id": "contact-wanglei", "request_message_id": "reply-message-1", "channel_id": None,
        "created_at": 110, "action_id": "eat-and-buy",
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


def _until_noon(document: dict) -> None:
    """实验截到当日中午：去掉任务、账单与工资，只留一封与任务无关、改到窗口内清晨的邮件（供唤醒引用）。"""
    mail = next(item for item in document["messages"] if item.get("related_task_id") is None)
    document.update(end_at="2026-01-05T12:00:00+08:00", tasks=[], bills=[], payrolls=[],
                    messages=[{**mail, "sent_at": "2026-01-05T06:00:00+08:00"}])


def _run_to_end_and_export(engine, directory) -> tuple[list[dict], list[dict]]:
    """人物照常等到实验结束（就绪时收尾、播报或开始等待），封存检查点后导出并完整校验：（导出事件，purchases.jsonl 各行）。"""
    from tests.life.conftest import advance_next
    from tests.life.test_dayrun_restaurants import _keep_waiting
    from tests.life.test_meal_outcome import _round

    for step in range(64):
        if engine.state.current_time == engine.state.end_time:
            break
        if engine.scheduling_state().ready_agent_ids:
            _round(engine, f"day-{step}", *_keep_waiting(engine))
        else:
            advance_next(engine)
    assert engine.state.current_time == engine.state.end_time
    point = engine.checkpoint({"agent:1": engine.store.assets.put_json({"agent_id": 1}, {"kind": "purchases"})})
    export_run(engine.store, point.last_event_seq, directory)
    _, events, _ = validate_export(directory)
    rows = [json.loads(line) for line in (directory / "purchases.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows == project_purchase_facts(events)
    return events, rows


def test_real_run_export_counts_eat_and_remote_order_once_despite_a_replay(_engine_factory, tmp_path) -> None:
    """真实 LifeEngine 与 SQLite（场景截到 1月5日 12:00）：同一请求里吃手边的食物并远程下单（整体 in_progress，订单在顶层
    与逐项结果里），邮件唤醒时（还在吃）同一动作身份重放一次（重放回执再带同一订单），随后过到实验结束；导出的 purchases.jsonl
    只有这一笔购买（来源是首个结算事件）与一次尝试下单，逐行等于从导出事件的重算，并通过完整校验（第七章「导出」、
    第六章「混合动作的购买记录遗漏」）。"""
    from agentsociety2.society import DirectToolCall

    from tests.life.conftest import advance_next, context, stage_day_plan, start_request, wake_by_mail
    from tests.life.test_dayrun_restaurants import _stage
    from tests.life.test_meal_outcome import _food

    engine = _engine_factory(mutate=_until_noon)
    stage_day_plan(engine)
    food = _food(engine)
    _stage(engine, "market", 1500, inventory={"market": {food.product_id: 2.0}})
    first = context(engine, "mixed")
    engine.begin_decision_round([first])
    observed = engine.observe(first)
    supply = next(row["object"] for row in observed["interaction_bindings"] if row["kind"] == "supply"
                  and any(action["label"] == "远程下单" and action["note"] is None for action in row["actions"]))
    request = start_request(targets=[f"持有物：{food.name}", supply], actions=["consume", "obtain"],
                            ways=["食用", "远程下单"])
    engine.submit_action(DirectToolCall(first, "action-mixed", "start_activity", request))
    [mixed] = engine.commit_decision_round("mixed")
    [order] = mixed.data["orders"]
    assert mixed.status == "in_progress" and mixed.data["activity_results"][1]["orders"] == [order]
    wake_by_mail(engine, offset=300)
    advance_next(engine)
    assert [item.kind for item in active_activities(engine.state, 1)] == [ActivityKind.CONSUME]
    decision = context(engine, "replay")
    engine.begin_decision_round([decision])
    engine.observe(decision)
    engine.submit_action(DirectToolCall(decision, "action-mixed", "start_activity", request))
    engine.commit_decision_round("replay")
    _, rows = _run_to_end_and_export(engine, tmp_path / "real-export")
    results = [event for event in engine.store.read_agent_events("action_result", 1)
               if event.action_id == "action-mixed"]
    [replayed] = [event for event in results if event.decision_id == "decision-replay-1"]
    assert replayed.data["reused"] is True
    assert replayed.data["data"]["orders"] == results[0].data["data"]["orders"] == [order]
    requested = [event for event in engine.store.read_agent_events("action_requested", 1)
                 if event.action_id == "action-mixed"]
    assert [event.data["reused"] for event in requested] == [False, True]
    assert [row for row in rows if row["fact_kind"] == "purchase"] == [{
        "fact_kind": "purchase", "agent_id": 1, "order_id": order["order_id"], "product_id": order["product_id"],
        "supply_id": order["supply_id"], "quantity": order["quantity"], "total_price_fen": order["total_price_fen"],
        "fulfillment": "delivery", "recipient_id": "agent:1", "request_message_id": None, "channel_id": None,
        "created_at": order["created_at"], "action_id": "action-mixed", "source_event_id": results[0].event_id}]
    assert order["channel_id"] is None
    assert [(row["action_id"], row["way"], row["status"], row["request_event_id"]) for row in rows
            if row["fact_kind"] == "order_attempt"] == [("action-mixed", "远程下单", "in_progress",
                                                          requested[0].event_id)]


def test_real_run_export_counts_orders_written_with_way_synonyms(_engine_factory, tmp_path) -> None:
    """真实 LifeEngine 与 SQLite（场景截到 1月5日 12:00），人在家：同一次冻结观察先经宿主本地校验、再由环境受理，人物用方式
    同义表里的写法下单——「外卖」（远程下单，受理并送到家）与「自取」（到店，人不在店里被拒）——另用「等着」开始等待；宿主都
    放行（与环境同一张同义表）。随后过到实验结束，导出的 purchases.jsonl 把两次下单与原词写的一样计为尝试下单，way 记归一后的
    「远程下单」「到店」，带各自的状态与错误码；等待不是下单，不计。逐行等于从导出事件的重算，并通过完整校验
    （docs/dev/SPEC-dayrun-fidelity.md 3.1 方式同义、第九章导出）。"""
    from tests.life.conftest import stage_day_plan, start_arguments
    from tests.life.purchase_worlds import decide
    from tests.life.test_dayrun_restaurants import _stage

    engine = _engine_factory(mutate=_until_noon)
    stage_day_plan(engine)
    _stage(engine, "home", 1500)
    _, error, takeout = decide(engine, "takeout", start_arguments(["纯牛奶"], ["obtain"], ["外卖"]))
    assert error == "" and (takeout.status, takeout.error_code) == ("completed", None)
    [order] = takeout.data["orders"]
    assert (order["fulfillment"], order["destination_id"]) == ("delivery", "home")
    _, error, pickup = decide(engine, "self-pickup", start_arguments(["纯牛奶"], ["obtain"], ["自取"]))
    assert error == "" and (pickup.status, pickup.error_code) == ("rejected", "ACTIVITY_PRECONDITION_UNMET")
    _, error, waiting = decide(engine, "waiting", start_arguments(["自己"], ["interact"], ["等着"]))
    assert error == "" and waiting.status == "in_progress"
    events, rows = _run_to_end_and_export(engine, tmp_path / "synonym-export")
    requested = {event["action_id"]: event["event_id"] for event in events
                 if event["event_type"] == "action_requested" and event["agent_id"] == 1}
    assert [(row["order_id"], row["action_id"]) for row in rows if row["fact_kind"] == "purchase"] == [
        (order["order_id"], "action-takeout")]
    assert [(row["action_id"], row["request_event_id"], row["way"], row["status"], row["error_code"]) for row in rows
            if row["fact_kind"] == "order_attempt"] == [
        ("action-takeout", requested["action-takeout"], "远程下单", "completed", None),
        ("action-self-pickup", requested["action-self-pickup"], "到店", "rejected", "ACTIVITY_PRECONDITION_UNMET")]


#: 同义表里指向下单方式的写法（与原词、去空白与全角的写法）→ 归一后的下单方式。
_ORDER_SPELLINGS = {
    "到店": "到店", "自取": "到店", "堂食": "到店", "到店购买": "到店", "到店点单": "到店",
    "远程下单": "远程下单", "外卖": "远程下单", "点外卖": "远程下单", "配送": "远程下单", "送货上门": "远程下单",
    "网上下单": "远程下单", "线上下单": "远程下单", " 远程 下单 ": "远程下单", "外　卖": "远程下单",
    "购买": "购买", "买": "购买", "下单": "购买", "下单购买": "购买", "点单": "购买", "订购": "购买",
}


def test_order_attempts_classify_written_ways_through_the_way_synonym_table() -> None:
    """尝试下单按宿主与环境同一张方式同义表归一所写方式（docs/dev/SPEC-dayrun-fidelity.md 3.1）：原词、同义写法与去空白、
    全角的写法都计入并记归一后的下单方式；同义表里其余写法（指向吃、逛、等待、制作等）、其余方式与不是方式的写法都不计；
    一项请求里第一项不是下单方式时取其后第一个下单方式。表里每个指向下单方式的写法都在上面列出。"""
    others = [way for way in {*WAYS, *WAY_SYNONYMS, "随便买点", "Takeout"} if way not in _ORDER_SPELLINGS]
    ways = [*_ORDER_SPELLINGS, *others]
    events = [{"event_type": "action_requested", "agent_id": 1, "action_id": f"action-{index}",
               "event_id": f"request-{index}", "occurred_at": 100 + index,
               "data": {"tool_name": "start_activity", "reused": False,
                        "request": {"items": [{"target": "午餐", "way": way, "from": None, "to": None}],
                                    "answering": None, "prefer": "最快拿到"}}}
              for index, way in enumerate(ways)]
    mixed = {**events[0], "action_id": "action-mixed", "event_id": "request-mixed",
             "data": {**events[0]["data"], "request": {
                 "items": [{"target": "午餐", "way": way, "from": None, "to": None} for way in ["吃", "外卖"]],
                 "answering": None, "prefer": "最快拿到"}}}
    attempts = [row for row in project_purchase_facts([*events, mixed]) if row["fact_kind"] == "order_attempt"]
    assert [(row["action_id"], row["way"]) for row in attempts] == [
        *((f"action-{index}", way) for index, way in enumerate(_ORDER_SPELLINGS.values())),
        ("action-mixed", "远程下单")]
    assert {way for way in WAY_SYNONYMS if way_options(way) & NAMED_PURCHASE_WAYS} <= set(_ORDER_SPELLINGS)


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
