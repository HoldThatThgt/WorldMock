"""人物动作只提交可读选择；引擎使用冻结观察和领域合同生成执行参数。"""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from worldmock.errors import BusinessRuleError
from worldmock.life.activity_control import PHASE_ACTIVE, PHASE_CONCLUDED, PHASE_PAUSED, PHASE_PENDING, activity_phase
from worldmock.life.vocabulary import LIFE_VERBS, PURCHASE_WAYS, USE_WAYS, WAYS
from worldmock.life.readable import TRAVEL_WAYS
from worldmock.life.interaction_menu import binding_action
from worldmock.life.models import (ActivityKind, ActivityStatus, ConcludeActivityRequest, PauseActivityRequest,
                                   ReportStatusRequest, SessionTaskKind, StartActivityRequest)
from worldmock.life.name_resolution import name_book, resolve_start
from worldmock.life.routing import combination_error, require_recipients, routed_selection, routing_names
from worldmock.life.schedule import PlanGoal
from tests.life.conftest import (advance_until_ready, conclude_request, pause_request, report_request,
                                 stage_day_plan, wake_by_mail)
from tests.life.test_location_sessions import _round, _stage, _two_parallel, _until_ended
from tests.life.test_parallel_admission import _engine, _observe
from tests.life.test_progress_layer_projection import _begin_pair


def _selection(observation, request):
    """@brief 按生产纯函数逐项解析冻结绑定与路由，不模拟引擎或目录。"""
    outcome = resolve_start(name_book(observation), request)
    if outcome.failures:
        return None
    names = routing_names(observation)
    error = combination_error(outcome.request, names)
    if error is not None and error[0] not in {"RECIPIENT_NOT_FOUND", "SUPPLY_SOURCE_MISMATCH"}:
        return None
    return routed_selection(list(outcome.rows), outcome.request, names)


#: 第五章「请求形状与菜单」的方式短语闭集，按动词分组（只有一种方式的对象也要照填）。
SPEC_WAYS_BY_VERB = {
    "travel": {"步行", "驾车"},
    "interact": {"工作", "打开", "交谈", "通话", "阅读", "进行", "等待"},
    "obtain": {"到店", "远程下单", "购买"},
    "consume": {"食用", "使用", "给予"},
    "sleep": {"睡觉"},
}


def _office_and_self_bindings() -> list[dict]:
    """@brief 本轮私有绑定：可步行前往的城西办公室与「自己」（等待）。"""
    return [{"object": "城西办公室", "object_id": "office", "kind": "location",
             "actions": [binding_action("步行", "travel_to", travel_mode="walking")]},
            {"object": "自己", "object_id": "agent:1", "kind": "self",
             "actions": [binding_action("等待", "wait")]}]


def _request(*items, **fields):
    """@brief 对象、方式、来源、去向在同一项，购买声明中文偏好，其余明确空值。"""
    purchase = any(item[1] in {"到店", "远程下单", "购买"} for item in items)
    payload = {"items": [{"target": item[0], "way": item[1], "from": item[2], "to": item[3]} for item in items],
               "answering": None, "prefer": "最快拿到" if purchase else None}
    return StartActivityRequest.model_validate({**payload, **fields})


def test_start_activity_schema_rejects_model_supplied_calculation():
    """@brief 只接受逐项可读选择和中文购买偏好，必填可空字段必须出现，拒绝任何模型回填的计算字段和旧数组。"""
    request = _request(("城西办公室", "步行", None, None))
    assert request.model_dump() == {"items": [{"target": "城西办公室", "way": "步行", "from": None, "to": None}],
                                    "answering": None, "prefer": None}
    assert request.items[0].from_ is None and request.items[0].to is None
    purchased = _request(("x", "到店", None, None), prefer="到手价最低")
    assert purchased.model_dump()["items"][0]["from"] is None
    for prefer in ("fastest", "cheapest", "slowest"):
        with pytest.raises(ValidationError):
            _request(("x", "购买", None, None), prefer=prefer)
    for missing in ("items", "answering", "prefer"):
        payload = request.model_dump()
        del payload[missing]
        with pytest.raises(ValidationError):
            StartActivityRequest.model_validate(payload)
    for missing in ("target", "way", "from", "to"):
        payload = request.model_dump()
        del payload["items"][0][missing]
        with pytest.raises(ValidationError):
            StartActivityRequest.model_validate(payload)
    with pytest.raises(ValidationError):
        StartActivityRequest.model_validate({"targets": ["城西办公室"], "actions": ["travel"], "ways": ["步行"],
                                             "from": [None], "to": [None], "answering": None, "prefer": None})
    for field, value in {"price_version": "v1", "planned_duration_seconds": 600,
                         "destination_id": "office", "quantity": 2}.items():
        with pytest.raises(ValidationError):
            _request(("城西办公室", "步行", None, None), **{field: value})


def test_ways_vocabulary_maps_each_way_to_exactly_one_life_verb():
    """五个生活动词与方式短语闭集逐项对应第五章；旧的整句短语（步行前往、到店购买、配送到当前位置）不再是方式。"""
    assert LIFE_VERBS == ("travel", "interact", "obtain", "consume", "sleep")
    assert {verb: {way for way, (owner, _) in WAYS.items() if owner == verb} for verb in LIFE_VERBS} == (
        SPEC_WAYS_BY_VERB)
    assert not {"步行前往", "驾车前往", "到店购买", "配送到当前位置"} & set(WAYS)
    assert {WAYS[way] for way in TRAVEL_WAYS.values()} == {("travel", "travel_to")}
    assert {WAYS[way] for way in PURCHASE_WAYS.values()} == {("obtain", "purchase")}
    assert {WAYS[way] for way in USE_WAYS.values()} == {("consume", "consume")}
    assert not {"制作", "观看", "游玩", "浏览", "接着看", "从头再看", "接着听", "从头再听"} & set(WAYS)
    assert WAYS["给予"] == ("consume", "consume")


@pytest.mark.parametrize("items", [
    (),
    (("城西办公室", "步行", None, None), ("城西办公室", "步行", None, None)),
    (("城西办公室", "步行", None, None), ("自己", "等待", None, None), ("城东公园", "步行", None, None)),
])
def test_invalid_item_count_and_duplicate_targets_are_schema_errors(items):
    """@brief 空批次、超额和相同收货人的重复目标先由严格模型拒绝，不进入名称解析。"""
    with pytest.raises(ValidationError):
        _request(*items)


@pytest.mark.parametrize("target,way", [
    ("城东公园", "步行"),
    ("城西办公室", "驾车"),
    ("城西办公室", "沿着河边慢慢走"),
    ("自己", "阅读"),
], ids=["unlisted-object", "way-not-offered", "free-text-way", "self-way-not-offered"])
def test_unresolvable_selection_is_rejected_as_invalid_request(engine_factory, target, way):
    """@brief 结构合法的逐项请求仍按冻结观察拒绝未知对象或未列方式，每次保留精确名称错误。"""
    observation = {"interaction_bindings": _office_and_self_bindings()}
    request = _request((target, way, None, None))
    assert _selection(observation, request) is None
    (failure,) = resolve_start(name_book(observation), request).failures
    assert failure.item.error_code in {"NAME_NOT_FOUND", "WAY_NOT_OFFERED"}
    engine = _engine(engine_factory)
    _, rejected = _round(engine, "invalid-selection", "start_activity", request)
    assert rejected.status == "rejected" and rejected.error_code == "ACTION_REQUEST_INVALID"
    assert len(rejected.data["items"]) == 1
    assert rejected.data["items"][0]["error_code"] in {"NAME_NOT_FOUND", "WAY_NOT_OFFERED"}


def test_two_offered_selections_resolve_item_by_item_to_internal_actions():
    """两项逐项映射到各自的绑定行与内部标准动作，顺序与请求一致。"""
    selected = _selection({"interaction_bindings": _office_and_self_bindings()},
                          _request(("自己", "等待", None, None), ("城西办公室", "步行", None, None)))
    assert [(row["object_id"], row["selected_action"]["verb"], row["selected_action"]["label"],
             row["selected_action"]["canonical_action"]) for row in selected] == [
        ("agent:1", "interact", "等待", "wait"), ("office", "travel", "步行", "travel_to")]


_SUPPLY, _NOODLES = "一心面馆出售的牛肉面", "持有物：牛肉面"
_PERSON, _COLLEAGUE = "人物：李凡", "联系人：王磊"
#: 不在身边的同世界人物：只在私有绑定 person_bindings 里，不是本轮的交互对象。
_ELSEWHERE = "人物：赵敏"
_MESSAGE = "王磊 10:35 的消息"


def _routing_observation() -> dict:
    """@brief 本轮私有绑定：一件当前食品供给、一件可食用的手边食品、办公室、同处的人、同事联系人、「自己」，
    同世界全部其他人物（同处的李凡与不在身边的赵敏），王磊的一条可见直接消息，以及冻结地点表里的两家门店（一心面馆、四季便利）。"""
    rows = [
        {"object": _SUPPLY, "object_id": "beef-noodles", "kind": "supply",
         "actions": [binding_action("远程下单", "purchase", fulfillment="delivery")]},
        {"object": _NOODLES, "object_id": "beef-noodles", "kind": "holding",
         "actions": [binding_action("食用", "consume", use_mode="eat"), binding_action("给予", "consume")]},
        *_office_and_self_bindings(),
        {"object": _PERSON, "object_id": "agent:2", "kind": "person", "actions": [binding_action("交谈", "chat")]},
        {"object": _COLLEAGUE, "object_id": "contact-wanglei", "kind": "contact",
         "actions": [binding_action("通话", "call")]}]
    return {"interaction_bindings": rows,
            "person_bindings": [{"name": _PERSON, "person_id": "agent:2"}, {"name": _ELSEWHERE, "person_id": "agent:3"}],
            "message_bindings": [{"name": _MESSAGE, "message_id": "reply-1", "sender_id": "contact-wanglei"}],
            "locations": [{"location_id": "noodle-1", "name": "一心面馆"}, {"location_id": "store", "name": "四季便利"}]}


@pytest.mark.parametrize("request_items,answering,routed", [
    ([(_SUPPLY, "远程下单", None, None)], None, [("agent:1", True, None, None)]),
    ([(_SUPPLY, "远程下单", None, _COLLEAGUE)], _MESSAGE, [("contact-wanglei", False, "reply-1", _MESSAGE)]),
    ([(_SUPPLY, "远程下单", None, None), (_SUPPLY, "远程下单", None, _COLLEAGUE)], _MESSAGE,
     [("agent:1", True, None, None), ("contact-wanglei", False, "reply-1", _MESSAGE)]),
    ([(_SUPPLY, "远程下单", None, _PERSON)], None, [("agent:2", False, None, None)]),
    ([(_NOODLES, "食用", None, None)], None, [("agent:1", True, None, None)]),
    ([(_NOODLES, "给予", None, _PERSON)], None, [("agent:2", False, None, None)]),
    ([(_NOODLES, "食用", None, None), (_NOODLES, "给予", None, _COLLEAGUE)], None,
     [("agent:1", True, None, None), ("contact-wanglei", False, None, None)]),
    ([("城西办公室", "步行", None, None)], None, [("agent:1", True, None, None)]),
    ([(_SUPPLY, "远程下单", None, _ELSEWHERE)], None, [("agent:3", False, None, None)]),
    ([(_NOODLES, "给予", None, _ELSEWHERE)], None, [("agent:3", False, None, None)]),
], ids=["obtain-self", "obtain-contact-answering", "same-supply-two-recipients", "obtain-person", "eat-self",
        "give-person", "eat-and-give", "travel-null", "obtain-person-elsewhere",
        "give-person-elsewhere"])
def test_legal_from_to_answering_combinations_route_each_item(request_items, answering, routed):
    """合法组合（第五章「来源与去向」表）：宿主不报错，环境逐项写上收货人、是否自己、关联的消息与 answering；
    answering 只关联 to 等于发件人的项。不在身边的同世界人物也是人物（同处与否由给予、配送各自判定）。"""
    request = _request(*request_items, answering=answering)
    observation = _routing_observation()
    assert combination_error(request, routing_names(observation)) is None
    selection = _selection(observation, request)
    assert [(row["recipient_id"], row["to_self"], row["request_message_id"], row["answering"])
            for row in selection] == routed
    require_recipients(selection)


@pytest.mark.parametrize("request_items,answering,message", [
    ([("城西办公室", "步行", None, "自己")], None, "This way acts on the caller and requires to=null."),
    ([("城西办公室", "步行", "自己", None)], None, "This way requires from=null."),
    ([(_NOODLES, "食用", "自己", None)], None, "This way requires from=null."),
    ([(_NOODLES, "食用", None, _PERSON)], None, "This way acts on the caller and requires to=null."),
    ([(_NOODLES, "给予", None, None)], None, "Giving requires a known other person."),
    ([(_SUPPLY, "远程下单", None, _COLLEAGUE)], "王磊 9:00 的消息",
     "answering requires a known unanswered message."),
    ([(_SUPPLY, "远程下单", None, None)], _MESSAGE,
     "An item must address the message sender."),
    ([("城西办公室", "步行", None, None)], _MESSAGE,
     "An item must address the message sender."),
], ids=["to-on-travel", "from-on-travel", "consume-from-self", "eat-to-person",
        "give-to-null",
        "answering-unknown", "answering-no-sender-item", "answering-on-travel"])
def test_illegal_combinations_are_protocol_errors_and_invalid_requests(request_items, answering, message):
    """非法组合：宿主得到英文协议错误原文；绕过宿主时环境解析不出选择，以 ACTION_REQUEST_INVALID 业务拒绝。"""
    _assert_invalid(_request(*request_items, answering=answering), message)


def test_listed_label_with_another_known_place_in_from_is_a_source_mismatch():
    """列出的标签「<门店>出售的<商品>」写了另一个已知地点（冻结地点表里的门店名）作 from：共用判定给 SUPPLY_SOURCE_MISMATCH
    （宿主据此在本轮纠正，tests/integration/test_storefront_tools.py）；绕过宿主时环境照常解析出选择，由指名购买带候选拒绝。
    App 称呼与不认识的写法不在此列，交给环境按渠道与来源解析；写的正是标签里的门店（含带「地点：」前缀的写法）在名称解析中按
    保留原始来源并说明可填 null（docs/dev/SPEC-life-runtime.md#tool-interaction-工具请求「名称归一与错误合同」）。"""
    observation = _routing_observation()
    names = routing_names(observation)
    other = _request((_SUPPLY, "远程下单", "四季便利", None))
    assert combination_error(other, names) == (
        "SUPPLY_SOURCE_MISMATCH", "The target names store 一心面馆, but from names the different place 四季便利.")
    [row] = _selection(observation, other)
    assert (row["object"], row["from"], row["recipient_id"]) == (_SUPPLY, "四季便利", "agent:1")
    for source in ("盒马App", "饿了么"):
        assert combination_error(_request((_SUPPLY, "远程下单", source, None)), names) is None
    own = _request((_SUPPLY, "远程下单", "地点：一心面馆", None))
    assert combination_error(own, names) is None
    assert [row["from"] for row in _selection(observation, own)] == ["地点：一心面馆"]
    assert resolve_start(name_book(observation), own).notes == (
        "The target already names 一心面馆; from may be null.",)


def test_from_and_to_need_one_item_per_target():
    """@brief 来源与去向是逐项可空字符串，旧平行数组不能填入任何一项。"""
    request = _request((_SUPPLY, "远程下单", None, None), (_NOODLES, "给予", None, _PERSON))
    assert [(item.from_, item.to) for item in request.items] == [(None, None), (None, _PERSON)]
    for field in ("from", "to"):
        for value in ([], [None], [None, _PERSON]):
            payload = request.model_dump()
            payload["items"][0][field] = value
            with pytest.raises(ValidationError):
                StartActivityRequest.model_validate(payload)


def _assert_invalid(request, message: str) -> None:
    """@brief 宿主与环境共用的判定给出这条英文原文；环境解析不出选择，以 ACTION_REQUEST_INVALID 拒绝。"""
    observation = _routing_observation()
    assert combination_error(request, routing_names(observation)) == ("ACTION_REQUEST_INVALID", message)
    assert _selection(observation, request) is None


@pytest.mark.parametrize("recipient", ["人物：陌生人", "自己"], ids=["unknown-person", "legacy-self-string"])
def test_unknown_recipient_is_recipient_not_found_before_admission(recipient):
    """去向不是任何人物（不是本轮的人物、联系人，也不是同世界人物）：共用判定给 RECIPIENT_NOT_FOUND，这是拒绝码，宿主
    放行（tests/integration/test_storefront_tools.py）；环境照常解析出选择（收货人为空），在整组准入之前以
    RECIPIENT_NOT_FOUND 业务拒绝；同一请求另有不合法组合时先报 ACTION_REQUEST_INVALID。"""
    observation = _routing_observation()
    unknown = _request((_NOODLES, "给予", None, recipient))
    assert combination_error(unknown, routing_names(observation)) == (
        "RECIPIENT_NOT_FOUND", "The recipient is not a known other person; use null for the caller.")
    selection = _selection(observation, unknown)
    assert [(row["to"], row["recipient_id"]) for row in selection] == [(recipient, None)]
    with pytest.raises(BusinessRuleError, match="RECIPIENT_NOT_FOUND"):
        require_recipients(selection)
    both = _request((_NOODLES, "给予", None, "人物：陌生人"), ("城西办公室", "步行", None, "自己"))
    assert combination_error(both, routing_names(observation))[0] == "ACTION_REQUEST_INVALID"


def test_route_choice_preserves_mode_but_engine_derives_exact_seconds(engine_factory):
    """真实图上出行方式决定精确秒数，人物看到的分钟短语不能回填执行时长。"""
    engine = _engine(engine_factory, location="home")
    observed = _observe(engine, "travel-choice")
    row = next(row for row in observed["interaction_bindings"]
               if row["kind"] == "location" and row["object_id"] == "office")
    _, result = _round(engine, "travel-start", "start_activity", _request((row["object"], "步行", None, None)))
    assert result.status == "in_progress", result.error_code
    activity = engine.context.state.activities[result.activity_id]
    assert (activity.kind, activity.destination_id, activity.travel_mode) == (ActivityKind.TRAVEL, "office", "walking")
    assert activity.planned_duration_seconds == 2729
    assert activity.data["distance_meters"] == 3274
    assert activity.planned_duration_seconds != math.ceil(activity.planned_duration_seconds / 60) * 60
    assert TRAVEL_WAYS == {"walking": "步行", "driving": "驾车"}


def test_purchase_uses_each_observed_quote_and_single_unit(engine_factory):
    """当前菜单食品使用目录固定价，各买一份；同批配送只收一次真实配送费。"""
    engine = _engine(engine_factory, location="home")
    observed = _observe(engine, "fixed-price-choice")
    rows = [row for row in observed["interaction_bindings"] if row["kind"] == "supply"
            and row["store_id"] == "noodle-1" and row["food_id"] in {"beef-noodles", "chips"}]
    assert len(rows) == 2
    request = _request(*((row["object"], "远程下单", "美团", None) for row in rows))
    _, result = _round(engine, "fixed-price-purchase", "start_activity", request)
    assert result.status == "completed", result.error_code
    orders = [engine.context.state.deliveries[row["order_id"]] for row in result.data["orders"]]
    assert [(order.food_id, order.quantity, order.unit_price_fen, order.recipient_id) for order in orders] == [
        (row["food_id"], 1, engine.context.semantics.catalog.foods[row["food_id"]].price_fen, "agent:1") for row in rows]
    assert all(order.fulfillment == "delivery" and order.destination_id == "home" for order in orders)
    assert [order.delivery_fee_fen for order in orders] == [500, 0]
    assert all(order.total_price_fen == order.unit_price_fen + order.delivery_fee_fen for order in orders)
    assert "activity_results" not in result.data


def test_purchase_preserves_chosen_pickup_when_delivery_is_also_available(engine_factory):
    """门店同时提供到店与远程下单，执行保留人物选择的到店方式、当前固定价和一份数量。"""
    engine = _engine(engine_factory, location="noodle-1")
    observed = _observe(engine, "pickup-choice")
    row = next(row for row in observed["interaction_bindings"] if row["kind"] == "supply"
               and row["store_id"] == "noodle-1" and row["food_id"] == "beef-noodles")
    assert {action["label"] for action in row["actions"]} >= {"到店", "远程下单"}
    _, result = _round(engine, "pickup-start", "start_activity", _request((row["object"], "到店", None, None)))
    assert result.status == "completed", result.error_code
    (receipt,) = result.data["orders"]
    order = engine.context.state.deliveries[receipt["order_id"]]
    assert (order.fulfillment, order.app_id, order.quantity, order.unit_price_fen) == ("pickup", None, 1, 2800)
    assert order.created_at == order.expected_at == engine.context.state.current_time
    assert order.delivery_fee_fen == 0


def test_report_two_activities_validates_all_before_updating_anything(engine_factory):
    """真实播报分别写两项进行中活动，随后混入已收尾名称时整条拒绝，另一项不会部分写入。"""
    engine = engine_factory()
    names = _begin_pair(engine, "atomic-report-start")
    ids = [next(item.activity_id for item in engine.context.state.activities.values() if item.name == name)
           for name in names]
    _, result = _round(engine, "atomic-report", "report_status", report_request(*names, summary=["还在等", "看到一段"]))
    assert result.status == "completed" and result.data == {"activity_ids": ids, "errands": []}
    now = engine.context.state.current_time
    assert [(engine.context.state.activities[identity].progress_note,
             engine.context.state.activities[identity].report_anchor_at) for identity in ids] == [
        ("还在等", now), ("看到一段", now)]
    # 经真实结束和收尾后，已收尾名称不再属于本轮控制合同。
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine)
    _, concluded = _round(engine, "atomic-report-conclude", "conclude_activity",
                          conclude_request(names[1], summary="先关掉应用", memories=[[]]))
    assert concluded.status == "completed"
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine)
    before = {key: value.model_dump(mode="json") for key, value in engine.context.state.activities.items()}
    _, rejected = _round(engine, "atomic-report-reject", "report_status",
                          report_request(*names, summary=["不应写入", "也不应写入"]))
    assert rejected.status == "rejected" and rejected.error_code == "ACTION_REQUEST_INVALID"
    assert [(item["written"], item["error_code"]) for item in rejected.data["items"]] == [
        (name, "ACTION_REQUEST_INVALID") for name in names]
    assert {key: value.model_dump(mode="json") for key, value in engine.context.state.activities.items()} == before


def test_control_requests_pair_each_task_with_its_summary():
    """三条控制工具一项一个对象：活动名与自述写在同一对象里，不再靠下标对齐三组数组；一至两项、活动名互不相同且非空、
    自述非空；结束工具每项另有必填的 memories 与 views（都可以是空列表）；旧的平行数组形状不再受理
    （docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要 第六章）；请求级 errands 必填（可以是空列表），缺它的请求不受理
    （docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-记下更新取消 ）。"""
    item = {"activity_name": "工作写会议纪要（1月5日 09:00 起）", "summary": "先写完提纲"}
    other = {"activity_name": "休息（1月5日 10:00 起）", "summary": "歇一会儿"}
    for model in (ReportStatusRequest, PauseActivityRequest):
        assert model(items=[item, other], errands=[]).model_dump(mode="json") == {
            "items": [item, other], "errands": []}
        for items in ([], [item, other, {**other, "activity_name": "吃饭"}], [item, item], [{**item, "summary": " "}],
                      [{**item, "activity_name": " "}], [{"activity_name": item["activity_name"]}],
                      [{**item, "memories": []}]):
            with pytest.raises(ValidationError):
                model(items=items, errands=[])
        with pytest.raises(ValidationError):
            model(task=[item["activity_name"]], summary=[item["summary"]])
        with pytest.raises(ValidationError, match="errands"):
            model(items=[item])
    concluded = {**item, "memories": [], "views": []}
    assert ConcludeActivityRequest(items=[concluded], errands=[]).model_dump(mode="json") == {
        "items": [concluded], "errands": []}
    for missing in ("memories", "views"):
        with pytest.raises(ValidationError):
            ConcludeActivityRequest(items=[{key: value for key, value in concluded.items() if key != missing}],
                                    errands=[])
    with pytest.raises(ValidationError, match="errands"):
        ConcludeActivityRequest(items=[concluded])
    with pytest.raises(ValidationError):
        ConcludeActivityRequest(task=[item["activity_name"]], summary=[item["summary"]], memories=[[]])


def test_report_writes_original_summary_to_independent_day_goal_note(engine_factory):
    """真实播报沿用人物原文写入关联目标，不改变登记时刻和此前已核验的推进身份。"""
    engine = _engine(engine_factory, location="home")
    now = engine.context.state.current_time
    goal = PlanGoal(intent_kind="休息", goal="午后稍作休息", registered_at=now - 100,
                    progressed_at=now - 50, progress_activity_id="earlier-rest")
    stage_day_plan(engine, goals=(goal,))
    _, started = _round(engine, "goal-rest-start", "start_activity", _request(("自己", "等待", None, None)))
    assert started.status == "in_progress"
    identity = started.activity_id
    def attach_goal(state):
        state.activities[identity] = state.activities[identity].model_copy(update={
            "task_kind": SessionTaskKind.GOAL, "task_ref": "休息", "plan_goal_intent_kind": "休息"})
    _stage(engine, attach_goal)
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine)
    name = engine.context.state.activities[identity].name
    _, result = _round(engine, "goal-rest-report", "report_status", report_request(name, summary="还想再歇一会儿"))
    assert result.status == "completed"
    current = engine.context.state.agents[1].day_plan.goals[0]
    assert current.progress_note == engine.context.state.activities[identity].progress_note == "还想再歇一会儿"
    assert (current.registered_at, current.progressed_at, current.progress_activity_id) == (
        goal.registered_at, goal.progressed_at, goal.progress_activity_id)


def _pending_and_paused(engine):
    """@brief 真实开始等待与应用、暂停应用，再另开应用；等待结束时保留未点名的运行活动。"""
    stage_day_plan(engine)
    ended_id, paused_id = _two_parallel(engine, "selective-start")
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine)
    paused = engine.context.state.activities[paused_id]
    _, result = _round(engine, "selective-pause", "pause_activity", pause_request(paused.name, summary="先停一会儿"))
    assert result.status == "completed"
    wake_by_mail(engine, offset=60)
    assert advance_until_ready(engine)
    observed = _observe(engine, "selective-other-choice")
    app = next(row for row in observed["interaction_bindings"] if row["kind"] == "app"
               and row["object_id"] != paused.target_id)
    _, running = _round(engine, "selective-other", "start_activity", _request((app["object"], "打开", None, None)))
    assert running.status == "in_progress", running.error_code
    _until_ended(engine, ended_id, "selective-end")
    return ended_id, paused_id, running.activity_id


def test_conclude_ends_only_selected_activities_with_own_summaries(engine_factory):
    """真实收尾保留已结束终态、取消暂停活动，未点名活动不变；记忆身份来自真实观察与活动会话。"""
    engine = engine_factory()
    ended_id, paused_id, running_id = _pending_and_paused(engine)
    before = engine.context.state.activities
    ended, paused, running = (before[identity] for identity in (ended_id, paused_id, running_id))
    assert (activity_phase(ended), activity_phase(paused), activity_phase(running)) == (
        PHASE_PENDING, PHASE_PAUSED, PHASE_ACTIVE)
    now = engine.context.state.current_time
    observed, result = _round(engine, "selective-conclude", "conclude_activity",
                               conclude_request(ended.name, paused.name, summary=["等完了", "先不看了"], memories=[[], []]))
    assert result.status == "completed", result.error_code
    done, stopped = (engine.context.state.activities[identity] for identity in (ended_id, paused_id))
    assert (done.status, done.ended_at, done.concluded_at, done.progress_note) == (
        ActivityStatus.COMPLETED, ended.ended_at, now, "等完了")
    assert (stopped.status, stopped.interruption_reason, stopped.concluded_at, stopped.progress_note) == (
        ActivityStatus.CANCELLED, "agent_cancelled", now, "先不看了")
    assert (activity_phase(done), activity_phase(stopped)) == (PHASE_CONCLUDED, PHASE_CONCLUDED)
    assert engine.context.state.activities[running_id] == running
    assert "task" not in result.data and "items" not in result.data
    assert result.data["activity_ids"] == [ended_id, paused_id]
    assert result.data["memory_sources"] == [
        {"task": item.name, "activity_id": item.activity_id, "session_id": item.session_ids[-1],
         "verified_event_ids": [row["source_event_id"] for row in observed["verified_experiences"]
                                if row["activity_id"] == item.activity_id]} for item in (ended, paused)]
    assert result.data["outcome"] and result.data["errands"] == []


def test_conclude_naming_none_of_the_just_ended_activities_is_rejected(engine_factory):
    """真实待收尾活动存在时，只收尾暂停活动会点名必须先收尾的活动，且全组活动不变。"""
    engine = engine_factory()
    ended_id, paused_id, _ = _pending_and_paused(engine)
    ended, paused = (engine.context.state.activities[identity] for identity in (ended_id, paused_id))
    assert (activity_phase(ended), activity_phase(paused)) == (PHASE_PENDING, PHASE_PAUSED)
    before = {key: value.model_dump(mode="json") for key, value in engine.context.state.activities.items()}
    _, rejected = _round(engine, "selective-reject", "conclude_activity",
                          conclude_request(paused.name, summary="先不看了", memories=[[]]))
    assert rejected.status == "rejected" and rejected.error_code == "ACTIVITY_CONCLUSION_REQUIRED"
    assert rejected.data["reason"] == f"Conclude the finished activities {ended.name} first."
    assert {key: value.model_dump(mode="json") for key, value in engine.context.state.activities.items()} == before
