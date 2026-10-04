"""人物动作只提交可读选择；引擎使用冻结观察和领域合同生成执行参数。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from worldmock.errors import BusinessRuleError
from worldmock.life.activities import change_activity
from worldmock.life.activity_control import PHASE_ACTIVE, PHASE_CONCLUDED, PHASE_PAUSED, PHASE_PENDING, activity_phase
from worldmock.life.interaction_menu import (
    LIFE_VERBS, PURCHASE_WAYS, TRAVEL_WAYS, USE_WAYS, WAYS, binding_action,
)
from worldmock.life.models import (ActionResult, ActivityKind, ActivityState, ActivityStatus,
                                   ConcludeActivityRequest, PauseActivityRequest, ReportStatusRequest, SessionTaskKind,
                                   StartActivityRequest)
from worldmock.life.routing import combination_error, require_recipients, routing_names
from worldmock.life.schedule import DayPlan, PlanGoal
from worldmock.life.session_actions import SessionActionMixin
from tests.life.conftest import conclude_request, report_request


class _ActionHarness(SessionActionMixin):
    """仅为动作解析与派生参数测试提供被调用的领域边界。"""

    def __init__(self, observation: dict | None = None):
        """@brief 绑定一条已提交观察，捕获不改状态的领域调用。"""
        event = SimpleNamespace(decision_id="decision", occurred_at=100, data=observation)
        self.store = SimpleNamespace(read_agent_events=lambda *_: [event])
        # 控制判定的地点名表与受理提示所用的路线；本组用例都在同一停留内
        self.scenario = SimpleNamespace(locations=(), routes=())
        self.semantics = SimpleNamespace(scenario=self.scenario)
        self.rules = SimpleNamespace(tasks={}, items={})  # 本组控制活动没有工作或物品产出，两个目录明确为空。
        self.calls = []

    def _purchase(self, state, pending, request, named=frozenset(), channels=None):
        """@brief 捕获生成的整单请求（列出的供给没有指名购买解析出的身份，也不经渠道），返回只含订单数的最小结果。"""
        assert named == frozenset() and set((channels or {}).values()) <= {None}
        self.calls.append(request)
        return state, ActionResult(action_id="action", agent_id=1, status="completed", occurred_at=100,
                                   message="Purchase accepted", data={"orders": [], "count": len(request.items)})

    def _consume(self, state, pending, request):
        """@brief 捕获生成的商品使用请求。"""
        self.calls.append(request)
        return state, None

    def _start(self, state, pending, request):
        """@brief 捕获生成的定时活动请求。"""
        self.calls.append(request)
        return state, None

    def _change(self, state, pending, request):
        """@brief 经真实领域转换（activities.change_activity）改变所选活动，与引擎同一路径。"""
        agent, activity = change_activity(state.agents[pending.agent_id], state.activities[request.activity_id],
                                          request, state.current_time, 60)
        return state.model_copy(update={"activities": {**state.activities, activity.activity_id: activity},
                                        "agents": {pending.agent_id: agent}}), None


class _Agent:
    """仅提供身份、所在地点、（空的）持有物与当日计划的可复制人物。"""

    agent_id = 1
    location_id = "home"
    inventory: dict = {}

    def __init__(self, day_plan=None):
        """@brief 保存人物明确登记的目标。"""
        self.day_plan = day_plan

    def model_copy(self, update=None, deep=False):
        """@brief 模拟人物状态的不可变更新。"""
        return _Agent((update or {}).get("day_plan", self.day_plan))


class _State:
    """供活动控制测试使用的可复制状态：人物的活动、当日计划与当前时刻。"""

    def __init__(self, activities, agent=None):
        """@brief 保存权威活动及当前时刻。"""
        self.activities = activities
        self.current_time = 200
        self.agents = {1: agent or _Agent()}

    def model_copy(self, update, deep=False):
        """@brief 模拟 Pydantic 状态不可变更新。"""
        return _State(update.get("activities", self.activities), update.get("agents", self.agents)[1])


def _pending(request: StartActivityRequest | None = None):
    """@brief 返回与冻结观察对应的 start_activity 动作身份（给出请求时带登记的请求参数，拒绝时环境按它重算名称解析）。"""
    return SimpleNamespace(agent_id=1, decision_id="decision", observed_at=100, action_id="action",
                           tool_name="start_activity",
                           request=request.model_dump(mode="json") if request is not None else None)


def _activity(identity, name, status=ActivityStatus.ACTIVE, **fields):
    """@brief 建立带可读名称的真实活动模型：100 开始，已结算到 150，当前时刻为 200。"""
    ended = status not in {ActivityStatus.ACTIVE, ActivityStatus.PAUSED}
    return ActivityState(activity_id=identity, agent_id=1, location_id="home", kind=ActivityKind.WAIT,
                         status=status, started_at=100, ended_at=150 if ended else None, planned_end_at=400,
                         last_settled_at=150, elapsed_seconds=50, planned_duration_seconds=300,
                         origin_action_id=f"origin-{identity}", origin_decision_id="origin-decision",
                         origin_request_event_id=f"request-{identity}", session_ids=["session-1-0001"],
                         name=name, **fields)


def _bindings(*activities, **extra):
    """@brief 冻结观察中本人可见活动的私有绑定（名称、活动身份与阶段）。"""
    return {"activity_bindings": [{"name": item.name, "activity_id": item.activity_id,
                                   "phase": activity_phase(item)} for item in activities], **extra}


#: 第五章「请求形状与菜单」的方式短语闭集，按动词分组（只有一种方式的对象也要照填）。
SPEC_WAYS_BY_VERB = {
    "travel": {"步行", "驾车"},
    "interact": {"工作", "观看", "游玩", "交谈", "通话", "阅读", "浏览", "进行", "等待",
                 "接着看", "从头再看", "接着听", "从头再听"},
    "obtain": {"到店", "远程下单", "购买", "制作"},
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
    assert WAYS["制作"] == ("obtain", "consume")
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
    ("自己", "睡觉"),
], ids=["unlisted-object", "way-not-offered", "free-text-way", "self-way-not-offered"])
def test_unresolvable_selection_is_rejected_as_invalid_request(target, way):
    """@brief 结构合法的逐项请求仍按冻结观察拒绝未知对象或未列方式，每次保留精确名称错误。"""
    harness = _ActionHarness({"interaction_bindings": _office_and_self_bindings()})
    request = _request((target, way, None, None))
    selection = harness._start_selection(_pending(request), request)
    assert selection is None
    with pytest.raises(BusinessRuleError, match="ACTION_REQUEST_INVALID") as rejected:
        harness._start_activity(None, _pending(request), selection)
    items = (getattr(rejected.value, "suggested", None) or {}).get("items", [])
    assert len(items) == 1
    assert all(item["error_code"] in {"NAME_NOT_FOUND", "WAY_NOT_OFFERED"} for item in items)


def test_two_offered_selections_resolve_item_by_item_to_internal_actions():
    """两项逐项映射到各自的绑定行与内部标准动作，顺序与请求一致。"""
    harness = _ActionHarness({"interaction_bindings": _office_and_self_bindings()})
    selected = harness._start_selection(_pending(), _request(("自己", "等待", None, None),
                                                             ("城西办公室", "步行", None, None)))
    assert [(row["object_id"], row["selected_action"]["verb"], row["selected_action"]["label"],
             row["selected_action"]["canonical_action"]) for row in selected] == [
        ("agent:1", "interact", "等待", "wait"), ("office", "travel", "步行", "travel_to")]


_SUPPLY, _NOODLES, _KIT = "集市出售的牛肉面", "持有物：牛肉面", "持有物：素炒什锦食材包"
_PERSON, _COLLEAGUE = "人物：李凡", "联系人：王磊"
#: 不在身边的同世界人物：只在私有绑定 person_bindings 里，不是本轮的交互对象。
_ELSEWHERE = "人物：赵敏"
_MESSAGE = "王磊 10:35 的消息"


def _routing_observation() -> dict:
    """@brief 本轮私有绑定：一件供给、两件手边持有物（可吃的与可制作的）、办公室、同处的人、同事联系人、「自己」，
    同世界全部其他人物（同处的李凡与不在身边的赵敏），王磊的一条可见直接消息，以及冻结地点表里的两家门店（集市、好德）。"""
    rows = [
        {"object": _SUPPLY, "object_id": "supply-noodles", "kind": "supply",
         "actions": [binding_action("远程下单", "purchase", fulfillment="delivery")]},
        {"object": _NOODLES, "object_id": "noodles", "kind": "holding",
         "actions": [binding_action("食用", "consume", use_mode="eat"), binding_action("给予", "consume")]},
        {"object": _KIT, "object_id": "ingredient-vegetable-kit", "kind": "holding",
         "actions": [binding_action("制作", "consume", use_mode="use"), binding_action("给予", "consume")]},
        *_office_and_self_bindings(),
        {"object": _PERSON, "object_id": "agent:2", "kind": "person", "actions": [binding_action("交谈", "chat")]},
        {"object": _COLLEAGUE, "object_id": "contact-wanglei", "kind": "contact",
         "actions": [binding_action("通话", "call")]}]
    return {"interaction_bindings": rows,
            "person_bindings": [{"name": _PERSON, "person_id": "agent:2"}, {"name": _ELSEWHERE, "person_id": "agent:3"}],
            "message_bindings": [{"name": _MESSAGE, "message_id": "reply-1", "sender_id": "contact-wanglei"}],
            "locations": [{"location_id": "market", "name": "集市"}, {"location_id": "haode", "name": "好德"}]}


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
    ([(_KIT, "制作", None, None)], None, [("agent:1", True, None, None)]),
    ([("城西办公室", "步行", None, None)], None, [("agent:1", True, None, None)]),
    ([(_SUPPLY, "远程下单", None, _ELSEWHERE)], None, [("agent:3", False, None, None)]),
    ([(_NOODLES, "给予", None, _ELSEWHERE)], None, [("agent:3", False, None, None)]),
], ids=["obtain-self", "obtain-contact-answering", "same-supply-two-recipients", "obtain-person", "eat-self",
        "give-person", "eat-and-give", "make-self", "travel-null", "obtain-person-elsewhere",
        "give-person-elsewhere"])
def test_legal_from_to_answering_combinations_route_each_item(request_items, answering, routed):
    """合法组合（第五章「来源与去向」表）：宿主不报错，环境逐项写上收货人、是否自己、关联的消息与 answering；
    answering 只关联 to 等于发件人的项。不在身边的同世界人物也是人物（同处与否由给予、配送各自判定）。"""
    request = _request(*request_items, answering=answering)
    observation = _routing_observation()
    assert combination_error(request, routing_names(observation)) is None
    selection = _ActionHarness(observation)._start_selection(_pending(), request)
    assert [(row["recipient_id"], row["to_self"], row["request_message_id"], row["answering"])
            for row in selection] == routed
    require_recipients(selection)


@pytest.mark.parametrize("request_items,answering,message", [
    ([("城西办公室", "步行", None, "自己")], None, "This way acts on the caller and requires to=null."),
    ([("城西办公室", "步行", "自己", None)], None, "This way requires from=null."),
    ([(_NOODLES, "食用", "自己", None)], None, "This way requires from=null."),
    ([(_NOODLES, "食用", None, _PERSON)], None, "This way acts on the caller and requires to=null."),
    ([(_NOODLES, "给予", None, None)], None, "Giving requires a known other person."),
    ([(_KIT, "制作", None, _PERSON)], None, "This way acts on the caller and requires to=null."),
    ([(_KIT, "制作", "自己", None)], None, "This way requires from=null."),
    ([(_SUPPLY, "远程下单", None, _COLLEAGUE)], "王磊 9:00 的消息",
     "answering requires a known unanswered message."),
    ([(_SUPPLY, "远程下单", None, None)], _MESSAGE,
     "An item must address the message sender."),
    ([("城西办公室", "步行", None, None)], _MESSAGE,
     "An item must address the message sender."),
], ids=["to-on-travel", "from-on-travel", "consume-from-self", "eat-to-person",
        "give-to-null", "make-to-person", "make-from-self",
        "answering-unknown", "answering-no-sender-item", "answering-on-travel"])
def test_illegal_combinations_are_protocol_errors_and_invalid_requests(request_items, answering, message):
    """非法组合：宿主得到英文协议错误原文；绕过宿主时环境解析不出选择，以 ACTION_REQUEST_INVALID 业务拒绝。"""
    _assert_invalid(_request(*request_items, answering=answering), message)


def test_listed_label_with_another_known_place_in_from_is_a_source_mismatch():
    """列出的标签「<门店>出售的<商品>」写了另一个已知地点（冻结地点表里的门店名）作 from：共用判定给 SUPPLY_SOURCE_MISMATCH
    （宿主据此在本轮纠正，tests/integration/test_storefront_tools.py）；绕过宿主时环境照常解析出选择，由指名购买带候选拒绝。
    App 称呼与不认识的写法不在此列，交给环境按渠道与来源解析；写的正是标签里的门店（含带「地点：」前缀的写法）在名称解析中按
    保留原始来源并说明可填 null（docs/dev/SPEC-tool-interaction.md「名称归一与错误合同」）。"""
    observation = _routing_observation()
    names = routing_names(observation)
    other = _request((_SUPPLY, "远程下单", "好德", None))
    assert combination_error(other, names) == (
        "SUPPLY_SOURCE_MISMATCH", "The target names store 集市, but from names the different place 好德.")
    [row] = _ActionHarness(observation)._start_selection(_pending(other), other)
    assert (row["object"], row["from"], row["recipient_id"]) == (_SUPPLY, "好德", "agent:1")
    for source in ("盒马App", "饿了么"):
        assert combination_error(_request((_SUPPLY, "远程下单", source, None)), names) is None
    own = _request((_SUPPLY, "远程下单", "地点：集市", None))
    assert combination_error(own, names) is None
    harness = _ActionHarness(observation)
    assert [row["from"] for row in harness._start_selection(_pending(own), own)] == ["地点：集市"]
    assert harness._start_outcome(_pending(own)).notes == ("The target already names 集市; from may be null.",)


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
    harness = _ActionHarness(observation)
    assert harness._start_selection(_pending(request), request) is None
    with pytest.raises(BusinessRuleError, match="ACTION_REQUEST_INVALID"):
        harness._start_activity(None, _pending(request), None)


@pytest.mark.parametrize("recipient", ["人物：陌生人", "自己"], ids=["unknown-person", "legacy-self-string"])
def test_unknown_recipient_is_recipient_not_found_before_admission(recipient):
    """去向不是任何人物（不是本轮的人物、联系人，也不是同世界人物）：共用判定给 RECIPIENT_NOT_FOUND，这是拒绝码，宿主
    放行（tests/integration/test_storefront_tools.py）；环境照常解析出选择（收货人为空），在整组准入之前以
    RECIPIENT_NOT_FOUND 业务拒绝；同一请求另有不合法组合时先报 ACTION_REQUEST_INVALID。"""
    observation = _routing_observation()
    unknown = _request((_NOODLES, "给予", None, recipient))
    assert combination_error(unknown, routing_names(observation)) == (
        "RECIPIENT_NOT_FOUND", "The recipient is not a known other person; use null for the caller.")
    selection = _ActionHarness(observation)._start_selection(_pending(), unknown)
    assert [(row["to"], row["recipient_id"]) for row in selection] == [(recipient, None)]
    with pytest.raises(BusinessRuleError, match="RECIPIENT_NOT_FOUND"):
        require_recipients(selection)
    both = _request((_NOODLES, "给予", None, "人物：陌生人"), ("城西办公室", "步行", None, "自己"))
    assert combination_error(both, routing_names(observation))[0] == "ACTION_REQUEST_INVALID"


def test_route_choice_preserves_mode_but_engine_derives_exact_seconds(monkeypatch):
    """所选方式经私有绑定决定出行方式；展示给人物的分钟数不能成为执行时长。"""
    row = {"object": "城西办公室", "object_id": "office", "kind": "location",
           "actions": [binding_action("步行", "travel_to", travel_mode="walking"),
                       binding_action("驾车", "travel_to", travel_mode="driving")]}
    harness = _ActionHarness({"interaction_bindings": [row]})
    harness.scenario = SimpleNamespace()
    harness.semantics = SimpleNamespace(travel_planned_seconds=lambda *_: 673)
    monkeypatch.setattr("worldmock.life.travel.route_for", lambda *_: SimpleNamespace(duration_seconds=610))
    state = SimpleNamespace(agents={1: SimpleNamespace(location_id="home")}, current_time=100)
    selected = harness._start_selection(_pending(), _request(("城西办公室", "步行", None, None)))
    assert selected[0]["selected_action"]["canonical_action"] == "travel_to"
    harness._start_travel_action(state, _pending(), selected[0]["object_id"], selected[0])
    assert harness.calls[0].planned_duration_seconds == 673
    assert harness.calls[0].destination_id == "office"
    assert harness.calls[0].travel_mode == "walking"
    assert TRAVEL_WAYS["walking"] == "步行" and TRAVEL_WAYS["driving"] == "驾车"


def test_purchase_uses_each_observed_quote_and_single_unit():
    """两种供给可有不同报价版本，人物无需猜测或回填。"""
    harness = _ActionHarness()
    supply = SimpleNamespace(location_id="shop", fulfillment_modes=("delivery",),
                             delivery_destination_ids=("home",))
    harness.rules = SimpleNamespace(supplies={"first": supply, "second": supply})
    state = SimpleNamespace(agents={1: SimpleNamespace(location_id="home")}, activities={})
    selected = binding_action(PURCHASE_WAYS["delivery"], "purchase", fulfillment="delivery")
    bindings = [{"object_id": "first", "selected_action": selected, "recipient_id": "agent:1",
                 "quote": {"price_version": "first-v2", "price_fen": 200}},
                {"object_id": "second", "selected_action": selected, "recipient_id": "agent:1",
                 "quote": {"price_version": "second-v7", "price_fen": 500}}]
    _, result = harness._start_purchase_action(state, _pending(), bindings)
    request = harness.calls[0]
    assert len(harness.calls) == 1 and result.data == {"orders": [], "count": 2}
    assert [(item.supply_id, item.quantity, item.price_version, item.recipient_id) for item in request.items] == [
        ("first", 1, "first-v2", "agent:1"), ("second", 1, "second-v7", "agent:1")]
    assert request.fulfillment == "delivery"
    assert request.destination_id == "home"
    assert selected["label"] == "远程下单" and selected["verb"] == "obtain"


def test_purchase_preserves_chosen_pickup_when_delivery_is_also_available():
    """同一供给支持两种履约时，执行不擅自改成人物未选择的方式。"""
    harness = _ActionHarness()
    supply = SimpleNamespace(location_id="home", fulfillment_modes=("pickup", "delivery"),
                             delivery_destination_ids=("home",))
    harness.rules = SimpleNamespace(supplies={"offer": supply})
    state = SimpleNamespace(agents={1: SimpleNamespace(location_id="home")}, activities={})
    binding = {"object_id": "offer", "selected_action": binding_action("到店", "purchase", fulfillment="pickup"),
               "recipient_id": "agent:1", "quote": {"price_version": "current-v3", "price_fen": 350}}
    harness._start_purchase_action(state, _pending(), [binding])
    assert harness.calls[0].fulfillment == "pickup"
    assert harness.calls[0].items[0].price_version == "current-v3"


def test_nonfood_object_uses_catalog_mode_and_one_unit():
    """非食品的“使用”选择沿目录合同进入 use，而非被误作进食。"""
    harness = _ActionHarness()
    harness.rules = SimpleNamespace(items={
        "umbrella": SimpleNamespace(use_modes=("use",), use_duration_seconds=300)})
    bindings = [{"selected_action": binding_action(USE_WAYS["use"], "consume", use_mode="use")}]
    harness._start_consume_action(_State({}), _pending(), ["umbrella"], bindings)
    request = harness.calls[0]
    assert request.use_mode == "use"
    assert request.items[0].quantity == 1
    assert bindings[0]["selected_action"]["label"] == "使用"


def test_report_two_activities_validates_all_before_updating_anything():
    """一条播报逐项覆盖两项进行中活动并重置各自播报锚点；任一项已收尾时整条拒绝，不会部分写入。

    时间与能量规格第七章「活动的生命周期」：report_status 的 task[] 逐项点名活动；接口合同「请求字段与结算检查」：
    名称须对应本人进行中、暂停中或待收尾的活动，所有状态转换先验证再原子更新。
    """
    first = _activity("morning", "工作写会议纪要（1月5日 09:00 起）")
    second = _activity("afternoon", "工作核对决议（1月5日 09:00 起）")
    harness = _ActionHarness(_bindings(first, second))
    request = report_request(first.name, second.name, summary=["先整理议题", "再核对决议"])
    changed, result = harness._report_status(
        _State({first.activity_id: first, second.activity_id: second}), _pending(), request)
    assert result.status == "completed"
    assert result.data == {"task": [first.name, second.name], "activity_ids": ["morning", "afternoon"], "errands": []}
    assert [(changed.activities[item.activity_id].progress_note,
             changed.activities[item.activity_id].report_anchor_at) for item in (first, second)] == [
        ("先整理议题", 200), ("再核对决议", 200)]

    concluded = _activity("afternoon", second.name, ActivityStatus.COMPLETED, concluded_at=180)
    original = _State({first.activity_id: first, concluded.activity_id: concluded})
    with pytest.raises(BusinessRuleError, match="ACTION_REQUEST_INVALID"):
        _ActionHarness(_bindings(first, concluded))._report_status(original, _pending(), request)
    assert (original.activities[first.activity_id].progress_note,
            original.activities[first.activity_id].report_anchor_at) == ("", None)


def test_control_requests_pair_each_task_with_its_summary():
    """三条控制工具一项一个对象：活动名与自述写在同一对象里，不再靠下标对齐三组数组；一至两项、活动名互不相同且非空、
    自述非空；结束工具每项另有必填的 memories 与 views（都可以是空列表）；旧的平行数组形状不再受理
    （docs/dev/SPEC-dayrun-fidelity.md 第六章）；请求级 errands 必填（可以是空列表），缺它的请求不受理
    （docs/dev/SPEC-errands-workday.md 2.2）。"""
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


def test_report_writes_original_summary_to_independent_day_goal_note():
    """主观目标进展沿用人物原文，不改登记与客观活动证据；播报按活动名点名，写到活动及其关联目标。"""
    goal = PlanGoal(intent_kind="休息", goal="午后稍作休息", registered_at=100,
                    progressed_at=150, progress_activity_id="completed-activity")
    plan = DayPlan(day_end=500, goals=(goal,))
    waiting = _activity("rest", "等待（1月5日 13:00 起）", task_kind=SessionTaskKind.GOAL, task_ref="休息",
                        plan_goal_intent_kind="休息")
    harness = _ActionHarness(_bindings(waiting))
    changed, _ = harness._report_status(
        _State({waiting.activity_id: waiting}, _Agent(plan)), _pending(),
        report_request(waiting.name, summary="还想再歇一会儿"))
    result = changed.agents[1].day_plan.goals[0]
    assert result.progress_note == "还想再歇一会儿"
    assert changed.activities[waiting.activity_id].progress_note == "还想再歇一会儿"
    assert (result.registered_at, result.progressed_at, result.progress_activity_id) == (
        goal.registered_at, goal.progressed_at, goal.progress_activity_id)


def test_conclude_ends_only_selected_activities_with_own_summaries():
    """结束只触及逐项点名的活动，未点名的活动保持原状（时间与能量规格第七章「conclude_activity 的效果」）。

    待收尾活动不改终态，只写总结并置 concluded_at；暂停的活动先转为 cancelled（agent_cancelled）再收尾；
    记忆来源由系统逐项登记（活动、所属会话与本轮观察中该活动的已核验经历），模型不填身份。
    """
    ended = _activity("ended", "等待（1月5日 00:00 起）", ActivityStatus.COMPLETED)
    running = _activity("running", "观看午间新闻（1月5日 00:01 起）")
    paused = _activity("paused", "工作写会议纪要（1月5日 00:02 起）", ActivityStatus.PAUSED)
    experiences = [{"source_event_id": "event-waited", "activity_id": "ended"},
                   {"source_event_id": "event-watched", "activity_id": "running"}]
    harness = _ActionHarness(_bindings(ended, running, paused, verified_experiences=experiences))
    state = _State({item.activity_id: item for item in (ended, running, paused)})
    request = conclude_request(ended.name, paused.name, summary=["等完了", "先不写了"], memories=[[], []])
    changed, result = harness._conclude_activity(state, _pending(), request)
    done, stopped = changed.activities["ended"], changed.activities["paused"]
    assert (done.status, done.concluded_at, done.progress_note) == (ActivityStatus.COMPLETED, 200, "等完了")
    assert (stopped.status, stopped.interruption_reason, stopped.concluded_at, stopped.progress_note) == (
        ActivityStatus.CANCELLED, "agent_cancelled", 200, "先不写了")
    assert (activity_phase(done), activity_phase(stopped)) == (PHASE_CONCLUDED, PHASE_CONCLUDED)
    assert changed.activities["running"] == running and activity_phase(running) == PHASE_ACTIVE
    assert result.data["task"] == [ended.name, paused.name]
    assert result.data["activity_ids"] == ["ended", "paused"]
    assert result.data["memory_sources"] == [
        {"task": ended.name, "activity_id": "ended", "session_id": "session-1-0001",
         "verified_event_ids": ["event-waited"]},
        {"task": paused.name, "activity_id": "paused", "session_id": "session-1-0001", "verified_event_ids": []}]


def test_conclude_naming_none_of_the_just_ended_activities_is_rejected():
    """有刚结束、待收尾的活动时只结束别的活动，以 ACTIVITY_CONCLUSION_REQUIRED 拒绝并点名待收尾活动，不改任何活动。"""
    ended = _activity("ended", "等待（1月5日 00:00 起）", ActivityStatus.COMPLETED)
    paused = _activity("paused", "工作写会议纪要（1月5日 00:02 起）", ActivityStatus.PAUSED)
    assert (activity_phase(ended), activity_phase(paused)) == (PHASE_PENDING, PHASE_PAUSED)
    state = _State({item.activity_id: item for item in (ended, paused)})
    with pytest.raises(BusinessRuleError, match="ACTIVITY_CONCLUSION_REQUIRED") as raised:
        _ActionHarness(_bindings(ended, paused))._conclude_activity(
            state, _pending(), conclude_request(paused.name, summary="先不写了", memories=[[]]))
    assert str(raised.value.__cause__) == f"Conclude the finished activities {ended.name} first."
    assert state.activities == {"ended": ended, "paused": paused}
