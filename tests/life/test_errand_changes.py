"""个人事务变更的纯函数：校验、应用、提醒钟点换算与请求驱动的三种事务事件（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-记下更新取消 、2.3、
2.5、2.7）。

经真实引擎：正式场景与目录建立的 LifeEngine 上预置未完成事务（stage_errands 同一提交落库），再经 engine.observe 取得带
私有事务绑定的冻结观察，对它调用 errands.change_issues 与 apply_changes；request_drafts 用直接构造的 PendingAction，产出的
草稿经 FootprintStore 与世界状态同一提交落库。这些是补充证据：请求经引擎受理的验收在后续切片登记。
"""

from __future__ import annotations

from datetime import datetime

from tests import engine_fixture as engine_fixtures
from tests.catalog.conftest import read_payload, write_payload
from zoneinfo import ZoneInfo

import pytest

from tests.life.conftest import context, errand_add, errand_cancel, errand_update, stage_errands
from tests.life.test_errand_todo_layer import _errand
from worldmock.errors import BusinessRuleError, IntegrityError, PreconditionError
from worldmock.life.errand_state import MAX_OPEN_ERRANDS
from worldmock.life.errands import (
    ErrandOrigin,
    apply_changes,
    change_issues,
    change_refusal,
    remind_epoch,
    request_drafts,
)
from worldmock.life.guidance import rejection_guidance
from worldmock.life.models import PendingAction

#: 正式目录里的真实商品：零食、牛肉面与名称只差一个空格的两种柠檬茶（归一后同名）。
_SNACK, _MEAL = "chips", "beef-noodles"
_TEA, _TEA_JOINED = "lemon-tea", "lemon-tea-test"
_SNACK_ERRAND = "补一份薯片"
_ERRAND_EXPECTED = "The exact name of an open errand in the todo layer is required."
_ITEM_EXPECTED = "An exact known product name is required."
_ZONE = "Asia/Shanghai"


@pytest.fixture
def engine_factory(engine_factory, monkeypatch):
    """@brief 复制当前真实人工目录，明确登记一个合成饮料标签以验证名称归一歧义。"""
    original = engine_fixtures._mutated_scenario

    def with_declared_food(source, mutate, root):
        path = original(source, mutate, root)
        catalog = path.parent.parent / "catalog"
        payload = read_payload(catalog, "goods.json")
        payload["goods"].append({"goods_id": "lemon-tea-test", "name": "柠檬茶（模拟）", "category": "drink",
                                 "price_fen": 1200, "store_ids": "all"})
        for index in range(3):
            payload["goods"].append({"goods_id": f"fixture-daily-{index}", "name": f"模拟日用商品{index}",
                                     "category": "daily", "price_fen": 800, "store_ids": "all"})
        payload["goods"].sort(key=lambda food: food["goods_id"])
        write_payload(catalog, "goods.json", payload)
        return path

    monkeypatch.setattr(engine_fixtures, "_mutated_scenario", with_declared_food)
    return lambda: engine_factory(mutate=lambda document: None)


def _observe(engine, errands=(), round_id: str = "errand-changes") -> dict:
    """预置人物 1 的未完成事务后取得本轮冻结观察（带 errand_bindings 与 errand_item_bindings）。"""
    stage_errands(engine, 1, errands)
    return engine.observe(context(engine, round_id))


def _origin(engine, action_id: str = "action-x") -> ErrandOrigin:
    """此刻受理的来源：动作身份、请求事件身份、受理时刻与场景时区。"""
    return ErrandOrigin(action_id, f"event-{action_id}", engine.context.state.current_time, engine.context.scenario.timezone)


def _rows(issues) -> list[tuple]:
    """问题明细的可比形状：（路径、原值、明细码、合法值、expected、why、候选）；同时核对原因句就是 why。"""
    assert [sentence for _, sentence in issues] == [item.why for item, _ in issues]
    return [(item.path, item.written, item.error_code, item.allowed_values, item.expected, item.why, item.candidates)
            for item, _ in issues]


def _snack_errand(now: int, **fields):
    """一条已记下的「补一份薯片」（取得零食一份）。"""
    return _errand("action-a:errand:0", _SNACK_ERRAND, _SNACK, now - 120, **fields)


def test_unknown_or_ambiguous_errand_names_list_every_open_name_and_substitute_nothing(engine_factory) -> None:
    """更新或取消所写的事务名找不到报 NAME_NOT_FOUND，归一后对应多条报 NAME_AMBIGUOUS：路径到 errands[j].name，
    allowed_values 依待办层顺序列本人全部未完成事务名，没有候选，不替换；精确写法与无损归一后唯一的写法照常解析，回执写
    待办层里的名称。"""
    engine = engine_factory()
    now = engine.context.state.current_time
    observed = _observe(engine, [_snack_errand(now), _errand("action-a:errand:1", "囤 柠檬茶", _TEA, now - 60),
                                 _errand("action-a:errand:2", "囤柠檬茶", _TEA_JOINED, now - 60)])
    names = (_SNACK_ERRAND, "囤 柠檬茶", "囤柠檬茶")
    issues = change_issues([errand_update("补一份零食", memo="下班前下单"), errand_cancel("囤　柠檬茶", "不想喝了")],
                           observed)
    assert _rows(issues) == [
        (("errands", 0, "name"), "补一份零食", "NAME_NOT_FOUND", names, _ERRAND_EXPECTED,
         "The errand name 「补一份零食」 is not known.", ()),
        (("errands", 1, "name"), "囤　柠檬茶", "NAME_AMBIGUOUS", names, _ERRAND_EXPECTED,
         "The errand name 「囤　柠檬茶」 matches several open errands.", ())]
    accepted = [errand_update("囤柠檬茶", memo="周末再囤"), errand_cancel("补一份 薯片", "已经买了")]
    assert change_issues(accepted, observed) == []
    agent, receipt = apply_changes(engine.context.state.agents[1], accepted, observed, _origin(engine))
    assert receipt == [{"errand": "囤柠檬茶", "change": "更新"}, {"errand": _SNACK_ERRAND, "change": "取消"}]
    assert sorted(agent.errands) == ["action-a:errand:1", "action-a:errand:2"]
    assert agent.errands["action-a:errand:2"].memo == "周末再囤" and agent.errands["action-a:errand:1"].memo is None


def test_an_open_errand_named_by_two_updates_or_cancels_is_reported_at_the_later_entry(engine_factory) -> None:
    """同一条未完成事务在一次请求里被第二条更新或取消点名（写法不同但解析到同一条）时，只在较晚那一条的 name 报
    ARGUMENTS_INVALID，why 写明该事务名，不列合法值。"""
    engine = engine_factory()
    observed = _observe(engine, [_snack_errand(engine.context.state.current_time)])
    issues = change_issues([errand_update(_SNACK_ERRAND, memo="晚点买"), errand_cancel("补一份 薯片", "不买了")],
                           observed)
    assert _rows(issues) == [(
        ("errands", 1, "name"), "补一份 薯片", "ARGUMENTS_INVALID", (), _ERRAND_EXPECTED,
        "The open errand 「补一份薯片」 is named by more than one update or cancel in this request.", ())]


def test_unknown_or_ambiguous_items_name_products_in_why_and_never_in_allowed_values(engine_factory) -> None:
    """新增的商品名找不到时 why 写最接近的至多三个商品名（没有相近的就不写），归一后对应正式目录里的两种柠檬茶时报
    NAME_AMBIGUOUS 并在 why 列出两者；allowed_values 恒为空、没有候选。两种柠檬茶各自的精确写法都解析到自己。"""
    engine = engine_factory()
    observed = _observe(engine)
    issues = change_issues([errand_add("买牛肉", "牛肉"), errand_add("买茶", "柠　檬茶"), errand_add("买琴", "钢琴")],
                           observed)
    assert _rows(issues) == [
        (("errands", 0, "item"), "牛肉", "NAME_NOT_FOUND", (), _ITEM_EXPECTED,
         "No product is named 「牛肉」. Nearest product names: 「牛肉面」.", ()),
        (("errands", 1, "item"), "柠　檬茶", "NAME_AMBIGUOUS", (), _ITEM_EXPECTED,
         "The product name 「柠　檬茶」 matches several products: 「柠檬茶」、「柠檬茶（模拟）」.", ()),
        (("errands", 2, "item"), "钢琴", "NAME_NOT_FOUND", (), _ITEM_EXPECTED, "No product is named 「钢琴」.", ())]
    exact = [errand_add("一瓶柠檬茶", "柠檬茶"), errand_add("另一瓶柠檬茶", "柠檬茶（模拟）")]
    assert change_issues(exact, observed) == []
    agent, _ = apply_changes(engine.context.state.agents[1], exact, observed, _origin(engine))
    assert [agent.errands[f"action-x:errand:{index}"].product_id for index in (0, 1)] == [_TEA, _TEA_JOINED]


def test_added_names_must_differ_from_open_errands_and_from_every_earlier_addition(engine_factory) -> None:
    """新增的名称与未完成事务归一后相同、或与同请求较早的一条新增归一后相同（不论那一条有没有别的问题）都在
    errands[j].name 报 ARGUMENTS_INVALID，why 写明冲突的名称。"""
    engine = engine_factory()
    observed = _observe(engine, [_snack_errand(engine.context.state.current_time)])
    expected = "A name that no open errand uses is required."
    issues = change_issues([errand_add("补一份 薯片", "牛肉面")], observed)
    assert _rows(issues) == [(("errands", 0, "name"), "补一份 薯片", "ARGUMENTS_INVALID", (), expected,
                              "An open errand is already named 「补一份薯片」.", ())]
    issues = change_issues([errand_add("买茶", "牛肉"), errand_add("买 茶", "柠檬茶（模拟）")], observed)
    assert [(item.path, item.error_code) for item, _ in issues] == [
        (("errands", 0, "item"), "NAME_NOT_FOUND"), (("errands", 1, "name"), "ARGUMENTS_INVALID")]
    assert _rows(issues)[1][4:6] == (expected, "Two added errands share the name 「买茶」.")


def test_one_open_errand_per_outcome_and_product(engine_factory) -> None:
    """结果种类与商品都与一条未完成事务相同、或与同请求较早一条商品已解析的新增相同时，在 errands[j].item 报
    ARGUMENTS_INVALID；结果种类不同即可；较早一条商品没解析出来时不参与比较。"""
    engine = engine_factory()
    observed = _observe(engine, [_snack_errand(engine.context.state.current_time)])
    expected = "A different product is required."
    issues = change_issues([errand_add("再买一份零食", "薯片")], observed)
    assert _rows(issues) == [(("errands", 0, "item"), "薯片", "ARGUMENTS_INVALID", (), expected,
                              "The open errand 「补一份薯片」 already tracks this product.", ())]
    assert change_issues([errand_add("晚饭取得零食", "薯片")], observed)
    issues = change_issues([errand_add("茶一", "柠檬茶"), errand_add("茶二", "柠檬茶", quantity=2)], observed)
    assert _rows(issues) == [(("errands", 1, "item"), "柠檬茶", "ARGUMENTS_INVALID", (), expected,
                              "Two added errands track the same product.", ())]
    issues = change_issues([errand_add("茶一", "柠　檬茶"), errand_add("茶二", "柠檬茶")], observed)
    assert [(item.path, item.error_code) for item, _ in issues] == [(("errands", 0, "item"), "NAME_AMBIGUOUS")]


def test_a_ninth_open_errand_is_rejected_and_a_cancel_in_the_same_request_frees_its_place(engine_factory) -> None:
    """新增后未完成事务超过 8 条时在那条新增的 errands[j].change 报 ARGUMENTS_INVALID（上限只写在 why，expected 不带
    数字）；每条新增都计数，不论较早那一条有没有别的问题；同一请求里的取消先扣除，腾出的位置可以当场新增。"""
    engine = engine_factory()
    now = engine.context.state.current_time
    pairs = [food for food in sorted(engine.context.rules.goods) if food != _SNACK][:8]
    assert len(pairs) == MAX_OPEN_ERRANDS
    staged = [_errand(f"action-s:errand:{index}", f"事务{index}", product, now - 600)
              for index, product in enumerate(pairs)]
    capacity = ("ARGUMENTS_INVALID", "An addition that the open errands can still take is required.",
                f"This addition would exceed the limit of {MAX_OPEN_ERRANDS} open errands.")
    observed = _observe(engine, staged[:7], "errand-seven")
    issues = change_issues([errand_add("买牛肉", "牛肉"), errand_add(_SNACK_ERRAND, "薯片")], observed)
    assert [(item.path, item.written) for item, _ in issues] == [
        (("errands", 0, "item"), "牛肉"), (("errands", 1, "change"), "新增")]
    assert _rows(issues)[1][2:3] + _rows(issues)[1][4:6] == capacity
    observed = _observe(engine, staged, "errand-eight")
    issues = change_issues([errand_add(_SNACK_ERRAND, "薯片")], observed)
    assert [row[0] for row in _rows(issues)] == [("errands", 0, "change")]
    assert "8" not in issues[0][0].expected
    freed = [errand_add(_SNACK_ERRAND, "薯片"), errand_cancel("事务0", "用不着了")]
    assert change_issues(freed, observed) == []
    agent, _ = apply_changes(engine.context.state.agents[1], freed, observed, _origin(engine))
    assert len(agent.errands) == MAX_OPEN_ERRANDS and "action-s:errand:0" not in agent.errands


def test_cancelling_and_adding_again_with_the_original_name_and_another_quantity_is_judged_after_the_cancel(
        engine_factory) -> None:
    """同一请求取消旧事务并以原名、同一商品、新数量新增：按「取消、更新、新增」的次序求值，重名与同结果同商品都按扣除
    取消之后的集合判定，所以不论两条在请求里的先后都被受理；新事务以来源动作与条目下标为身份、零累计量、受理时刻记下，
    旧事务移出，回执按请求顺序。"""
    engine = engine_factory()
    now = engine.context.state.current_time
    observed = _observe(engine, [_snack_errand(now, progress=0.5)])
    changes = [errand_add(_SNACK_ERRAND, "薯片", quantity=2, memo="多买一份"),
               errand_cancel(_SNACK_ERRAND, "改成两份")]
    assert change_issues(changes, observed) == []
    agent, receipt = apply_changes(engine.context.state.agents[1], changes, observed, _origin(engine))
    assert receipt == [{"errand": _SNACK_ERRAND, "change": "新增"}, {"errand": _SNACK_ERRAND, "change": "取消"}]
    (errand,) = agent.errands.values()
    assert errand.model_dump() == {
        "errand_id": "action-x:errand:0", "name": _SNACK_ERRAND, "product_id": _SNACK, "quantity": 2,
        "progress": 0.0, "memo": "多买一份", "registered_at": now, "remind_at": None, "reminded_at": None,
        "origin_action_id": "action-x", "origin_event_id": "event-action-x"}
    assert change_issues(list(reversed(changes)), observed) == []


def test_update_replaces_memo_and_reminder_and_arms_the_reminder_again(engine_factory) -> None:
    """更新是替换语义：备注与提醒钟点照写（null 即清空），写了钟点按受理时刻重新换算并清掉已提醒时刻（重新计一次）；
    结果种类、商品、数量、累计量、记下时刻与来源都不变；原人物状态不被修改。"""
    engine = engine_factory()
    now = engine.context.state.current_time
    before = _snack_errand(now, memo="旧备注", remind_at=now - 60, reminded_at=now - 60, quantity=3, progress=1.0)
    observed = _observe(engine, [before])
    agent, receipt = apply_changes(engine.context.state.agents[1], [errand_update(_SNACK_ERRAND, remind_time="17:00")],
                                   observed, _origin(engine))
    updated = agent.errands[before.errand_id]
    assert (updated.memo, updated.remind_at, updated.reminded_at) == (None, remind_epoch("17:00", now, _ZONE), None)
    unchanged = ("name", "product_id", "quantity", "progress", "registered_at", "origin_action_id",
                 "origin_event_id")
    assert {key: getattr(updated, key) for key in unchanged} == {key: getattr(before, key) for key in unchanged}
    assert receipt == [{"errand": _SNACK_ERRAND, "change": "更新"}]
    assert engine.context.state.agents[1].errands[before.errand_id] == before
    agent, _ = apply_changes(engine.context.state.agents[1], [errand_update(_SNACK_ERRAND, memo="新备注")], observed,
                             _origin(engine))
    assert (agent.errands[before.errand_id].memo, agent.errands[before.errand_id].remind_at) == ("新备注", None)


@pytest.mark.parametrize("zone,now,clock,expected", [
    (_ZONE, (2026, 1, 5, 16, 30), "17:00", (2026, 1, 5, 17, 0)),
    (_ZONE, (2026, 1, 5, 16, 30), "16:30", (2026, 1, 6, 16, 30)),
    (_ZONE, (2026, 1, 5, 16, 30), "09:00", (2026, 1, 6, 9, 0)),
    (_ZONE, (2026, 1, 5, 23, 59), "00:00", (2026, 1, 6, 0, 0)),
    (_ZONE, (2026, 1, 5, 0, 0), "00:00", (2026, 1, 6, 0, 0)),
    (_ZONE, (2026, 1, 5, 0, 0), "23:59", (2026, 1, 5, 23, 59)),
    ("America/New_York", (2026, 3, 7, 22, 0), "21:00", (2026, 3, 8, 21, 0)),
])
def test_remind_time_is_today_only_when_strictly_later_than_acceptance(zone, now, clock, expected) -> None:
    """钟点换算为绝对时刻：今日时刻严格晚于受理时刻取今天，等于或早于都取下一本地日（含零点相邻的时刻）；下一本地日按
    本地日历换算（夏令时切换日相隔不是 86400 秒）；None 表示不设提醒。"""
    tz = ZoneInfo(zone)
    accepted = int(datetime(*now, tzinfo=tz).timestamp())
    assert remind_epoch(clock, accepted, zone) == int(datetime(*expected, tzinfo=tz).timestamp())
    assert remind_epoch(clock, accepted + 1, zone) - (accepted + 1) < 24 * 3600
    assert remind_epoch(None, accepted, zone) is None


def test_daylight_saving_day_is_a_calendar_day_not_a_fixed_interval() -> None:
    """夏令时开始的那天，下一本地日同一钟点距今日同一钟点只有 23 小时：换算按本地日历，不按今日时刻加 86400 秒。"""
    zone = "America/New_York"
    tz = ZoneInfo(zone)
    accepted = int(datetime(2026, 3, 7, 22, 0, tzinfo=tz).timestamp())
    today = int(datetime(2026, 3, 7, 21, 0, tzinfo=tz).timestamp())
    assert remind_epoch("21:00", accepted, zone) == today + 23 * 3600 != today + 86400


def test_apply_changes_receipt_follows_the_request_and_names_the_todo_layer_errand(engine_factory) -> None:
    """一次请求里的更新、新增、取消一并应用到人物状态的副本：回执按请求顺序，更新与取消写待办层里的名称（所写名称经无损
    归一解析），新增写所写的名称；新增身份取它在请求里的下标；原状态不变。"""
    engine = engine_factory()
    now = engine.context.state.current_time
    tea = _errand("action-b:errand:0", "囤柠檬茶", _TEA_JOINED, now - 60, quantity=3)
    observed = _observe(engine, [_snack_errand(now), tea])
    changes = [errand_update("囤 柠檬茶", memo="周末去超市"), errand_add("晚饭吃牛肉面", "牛肉面"),
               errand_cancel(" 补一份薯片", "家里还有")]
    agent, receipt = apply_changes(engine.context.state.agents[1], changes, observed, _origin(engine, "action-r"))
    assert receipt == [{"errand": "囤柠檬茶", "change": "更新"}, {"errand": "晚饭吃牛肉面", "change": "新增"},
                       {"errand": _SNACK_ERRAND, "change": "取消"}]
    assert sorted(agent.errands) == ["action-b:errand:0", "action-r:errand:1"]
    assert agent.errands["action-r:errand:1"].product_id == _MEAL
    assert sorted(engine.context.state.agents[1].errands) == ["action-a:errand:0", "action-b:errand:0"]


def test_a_binding_that_is_no_longer_an_open_errand_is_refused_without_items(engine_factory) -> None:
    """观察的事务绑定指向的事务已不在本人未完成集合（观察与世界对不上）时，应用只报一条不带明细的
    ACTION_REQUEST_INVALID；带问题的变更在应用时报与 change_refusal 相同的合并拒绝。"""
    engine = engine_factory()
    observed = _observe(engine, [_snack_errand(engine.context.state.current_time)])
    stage_errands(engine, 1, ())
    with pytest.raises(BusinessRuleError) as raised:
        apply_changes(engine.context.state.agents[1], [errand_cancel(_SNACK_ERRAND, "不买了")], observed, _origin(engine))
    assert type(raised.value) is BusinessRuleError and str(raised.value) == "ACTION_REQUEST_INVALID"
    invalid = [errand_cancel("补一份零食", "不买了")]
    with pytest.raises(PreconditionError) as refused:
        apply_changes(engine.context.state.agents[1], invalid, observed, _origin(engine))
    assert rejection_guidance(refused.value) == rejection_guidance(change_refusal(change_issues(invalid, observed)).error())


def test_change_refusal_merges_every_issue_into_one_action_request_invalid(engine_factory) -> None:
    """全部问题合并成一次 ACTION_REQUEST_INVALID：原因逐条以「; 」相连，下一步取该码的默认做法，改法按明细码各一句，
    公开明细的路径完整到 errands[j].<键>，都不带候选。"""
    engine = engine_factory()
    observed = _observe(engine, [_snack_errand(engine.context.state.current_time)])
    issues = change_issues([errand_update("补一份零食"), errand_add("再买一份", "薯片")], observed)
    refusal = change_refusal(issues)
    assert (refusal.code, refusal.next_step) == ("ACTION_REQUEST_INVALID", "Choose a known object and an offered way.")
    assert refusal.reason == "; ".join(sentence for _, sentence in issues)
    assert refusal.fix == "Use an exact name.; Correct every reported errand change."
    public = rejection_guidance(refusal.error())
    assert public["reason"] == refusal.reason and public["fix"] == refusal.fix
    assert [item["path"][-3:] for item in public["items"]] == [["errands", 0, "name"], ["errands", 1, "item"]]
    assert [item["path"][:4] for item in public["items"]] == [["tool_calls", 0, "function", "arguments"]] * 2
    assert all(item["candidates"] == [] for item in public["items"])


def _pending(engine, observed: dict, tool: str, changes: list[dict]) -> PendingAction:
    """直接构造的待结算动作：请求事件取这次观察事件（已落库），请求带一项活动与这些事务变更。"""
    request = {"items": [{"activity_name": "等待（1月5日 00:00 起）", "summary": "等完了。", "memories": [], "views": []}],
               "errands": changes}
    return PendingAction(run_id=engine.context.state.run_id, round_id="round-x", decision_id="decision-x", action_id="action-x",
                         agent_id=1, observed_at=engine.context.state.current_time, world_state_version=engine.context.state.version,
                         order=0, tool_name=tool, request=request, request_sha256="0" * 64,
                         requested_event_id=observed["observation_event_id"])


def test_request_drafts_write_one_system_event_per_change_without_decision_or_action_identity(engine_factory) -> None:
    """被受理的控制请求每条事务变更恰好一条 environment_event，按请求顺序：新增写 errand_registered（新事务快照），
    更新写 errand_updated（内容没有变化也写），取消写 errand_cancelled（移出前的快照另带原因）；都系统可见、人物为所有人、
    来源为环境，决策、动作与活动身份为空，caused_by 只有请求事件，载荷不含 activity 与 resource_changes；草稿与世界状态
    同一提交落库。不是控制工具时没有事务事件。"""
    engine = engine_factory()
    now = engine.context.state.current_time
    noted = _snack_errand(now, memo="下班前买")
    tea = _errand("action-b:errand:0", "囤柠檬茶", _TEA_JOINED, now - 60, quantity=3)
    observed = _observe(engine, [noted, tea])
    changes = [errand_add("晚饭吃牛肉面", "牛肉面"), errand_update(_SNACK_ERRAND, memo="下班前买"),
               errand_cancel("囤 柠檬茶", "不想喝了")]
    before = engine.context.state
    agent, _ = apply_changes(before.agents[1], changes, observed, _origin(engine))
    after = before.model_copy(deep=True)
    after.agents[1] = agent
    after.version += 1
    pending = _pending(engine, observed, "conclude_activity", changes)
    drafts = request_drafts(before, after, pending)
    assert [draft.data["kind"] for draft in drafts] == ["errand_registered", "errand_updated", "errand_cancelled"]
    assert [draft.data for draft in drafts] == [
        {"kind": "errand_registered", "errand": agent.errands["action-x:errand:0"].model_dump(mode="json")},
        {"kind": "errand_updated", "errand": noted.model_dump(mode="json")},
        {"kind": "errand_cancelled", "errand": tea.model_dump(mode="json"), "reason": "不想喝了"}]
    engine.context.store.commit(after.model_dump(mode="json"), drafts)
    stored = engine.context.store.read_agent_events("environment_event", 1)[-3:]
    assert [event.event_id for event in stored] == [draft.event_id for draft in drafts]
    for event in stored:
        assert (event.event_type, event.source_kind, event.visibility, event.agent_id) == (
            "environment_event", "environment", "system", 1)
        assert (event.decision_id, event.action_id, event.activity_id) == (None, None, None)
        assert event.caused_by == [observed["observation_event_id"]] and event.occurred_at == now
        assert event.state_version_after == after.version and not {"activity", "resource_changes"} & set(event.data)
    assert request_drafts(before, after, pending.model_copy(update={"tool_name": "start_activity"})) == []


def test_request_drafts_fault_when_an_accepted_change_has_no_errand_in_the_world(engine_factory) -> None:
    """请求驱动的事务事件只写世界里实际存在的事务：新增在受理后的世界里没有、更新或取消点名的名称在受理前本人未完成事务
    中解析不出唯一一条，都是完整性故障，不合成快照。"""
    engine = engine_factory()
    observed = _observe(engine, [_snack_errand(engine.context.state.current_time)])
    state = engine.context.state
    for changes in ([errand_add("晚饭吃牛肉面", "牛肉面")], [errand_update("补一份零食")],
                    [errand_cancel("补一份零食", "不买了")]):
        with pytest.raises(IntegrityError):
            request_drafts(state, state, _pending(engine, observed, "pause_activity", changes))
