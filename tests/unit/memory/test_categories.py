"""记忆的类别：三类闭集、推导的冻结输入、一项活动的规则 0–7、商品与查询、被拒请求、主请求与「类别」串、静态推导表。

活动快照用 ActivityState 直接构造，购买商品直接传入；被拒请求与观察写进真实足迹库再读回；不构造 WorldState。
样例环境的人物星期一到星期五 09:00–18:00 在公司上班，12:00–13:00 休息；2026-01-05 是星期一，2026-01-10 是星期六。
"""

import inspect
from datetime import datetime
from types import SimpleNamespace
from typing import get_args

import pytest
import tomlkit

from tests.frozen_inputs import fixture_metadata
from tests.unit.life import test_today_entry_events as entry_events
from tests.life.environment_inputs import (
    catalog_rows,
    copy_environment,
    grocery_environment,
    meal_environment,
    set_content,
    write_catalog_rows,
)
from worldmock.configuration.inputs.environment import load_environment
from worldmock.foundation.errors import IntegrityError
from worldmock.recording_engine.events.models import EventDraft
from worldmock.recording_engine.storage.store import FootprintStore
from worldmock.simulation_engine.observation.memory.entries import (
    agent_events,
    build_closing_entry,
    build_entry,
    decision_commits,
)
from worldmock.simulation_engine.observation.memory.categories import (
    Derivation,
    RowCategory,
    activity_derivation,
    category_book,
    category_label,
    goods_category,
    memory_book,
    primary_request,
    purchase_goods,
    rejected_category,
    request_category,
    row_categories,
)
from worldmock.state.model.memory import MEMORY_CATEGORIES, MemoryCategory
from worldmock.state.world.activity import ActivityState

MONDAY, SATURDAY = "2026-01-05", "2026-01-10"
#: 样例副本给 park 类型加的一条不补能、不凭状态的地点内容，其余字段照 c-chat。
C_WALK = {"content_id": "c-walk", "duration_seconds": 600, "exertion": "light", "location_kinds": ["park"],
          "pushable": False, "relaxation_relief": "none", "social_relief": "high", "summary": "公园散步的测试用合成摘要。",
          "summary_source": {"kind": "synthetic", "notice": "测试用合成摘要", "url": None}, "title": "公园散步"}
#: 一条前提要求 office 的哔哩哔哩内容。
OFFICE_DOC = {"app_ids": ["bilibili"], "content_id": "c-office-doc", "duration_seconds": 600, "exertion": "light",
              "prerequisite": {"location_kinds": ["office"]}, "pushable": False, "relaxation_relief": "none",
              "social_relief": "none", "summary": "办公室文档的测试用合成摘要。",
              "summary_source": {"kind": "synthetic", "notice": "测试用合成摘要", "url": None}, "title": "办公室文档"}
#: 名字带「茶」字的非食品商品：只差空白与大小写的名称对上它时按商品判定，不落到文字规则。
TEA_SET = {"category": "goods", "goods_id": "tea-set", "name": "茶具 Set", "price_fen": 3000,
           "store_kinds": ["convenience_store"]}


def at(day: str, clock: str) -> int:
    """上海时区的本地日期与 HH:MM 写成纪元秒。"""
    return int(datetime.fromisoformat(f"{day}T{clock}:00+08:00").timestamp())


def add_rows(root, name, key, *rows):
    """副本的某个目录文件追加几行，全部行按身份升序写回并刷新清单，返回根目录。"""
    merged = [*catalog_rows(root, name), *rows]
    write_catalog_rows(root, name, sorted(merged, key=lambda row: row[key]))
    return root


def set_app(root, app_id, **fields):
    """在副本里改一个 app 行的几个字段并刷新清单。"""
    rows = catalog_rows(root, "app-catalog.jsonl")
    next(row for row in rows if row["app_id"] == app_id).update(fields)
    write_catalog_rows(root, "app-catalog.jsonl", rows)


def without_work_schedule(root):
    """副本的人设去掉整个工作安排。"""
    path = root / "persona.toml"
    document = tomlkit.parse(path.read_text(encoding="utf-8"))
    del document["work_schedule"]
    path.write_text(tomlkit.dumps(document), encoding="utf-8", newline="\n")
    return root


def sample(tmp_path, *contents):
    """样例环境的副本加上 c-walk 与给出的内容行。"""
    return add_rows(copy_environment(tmp_path / "input"), "content-catalog.jsonl", "content_id", C_WALK, *contents)


def inputs_of(root):
    """按生产入口加载副本。"""
    return load_environment(root, metadata_loader=fixture_metadata)


def book_of(root):
    """副本的推导输入。"""
    return category_book(inputs_of(root))


def activity(kind, location_id, started_at, destination_id=None, **data) -> ActivityState:
    """一项进行中活动的快照：只有判定会读的字段取用例给的值。"""
    return ActivityState(activity_id="a-1", agent_id=1, location_id=location_id, kind=kind, status="active",
                         started_at=started_at, planned_end_at=started_at + 600, last_settled_at=started_at,
                         elapsed_seconds=0, planned_duration_seconds=600, origin_action_id="act-1",
                         origin_decision_id="d-1", origin_request_event_id="e-1", destination_id=destination_id,
                         data=data)


def doing(content_id, location_id, started_at, played=()):
    """在地点做事的快照。"""
    return activity("interact", location_id, started_at, app_id=None, content_id=content_id, played=list(played))


def using(app_id, content_id, location_id, started_at, played=()):
    """用内容 app 的快照。"""
    return activity("interact", location_id, started_at, app_id=app_id, content_id=content_id, played=list(played))


def derive(book, snapshot, goods_ids=()):
    """一项活动的推导（类别与规则编号）。"""
    found = activity_derivation(book, snapshot, goods_ids)
    return found.category, found.rule


@pytest.fixture
def store(tmp_path):
    store = FootprintStore.create(tmp_path / "run", {"run_id": "run", "agent_ids": [1]},
                                  {"version": 0, "current_time": at(MONDAY, "10:00")})
    yield store
    store.close()


def requested(store, tool, request, observed=None, observations=1, decision="d-1"):
    """在足迹库里写一条世界请求（被拒的出行另带这次决策的观察事件），返回读回的 action_requested 事件。"""
    moment = store.state["current_time"]
    drafts = [EventDraft(event_type="observation", occurred_at=moment, agent_id=1, decision_id=decision,
                         observed_at=moment, data={"location_id": observed})
              for _ in range(observations if observed is not None else 0)]
    action_id = f"{decision}:action:{store.last_event_seq + len(drafts) + 1:04d}"
    drafts.append(EventDraft(event_type="action_requested", occurred_at=moment, agent_id=1, decision_id=decision,
                             action_id=action_id, source_kind="model", data={"tool_name": tool, "request": request}))
    return store.append_evidence(drafts)[-1]


def main(action, target, detail=None):
    """start_main_activity 的请求参数。"""
    return {"action": action, "target": target, "detail": detail}


def side(action, *targets):
    """start_side_activity 的请求参数。"""
    return {"action": action, "target": list(targets), "prefer": None}


def test_memory_categories_are_food_work_life_in_this_order():
    assert MEMORY_CATEGORIES == ("饮食", "工作", "生活") == get_args(MemoryCategory)


def test_food_goods_are_meal_snack_drink_and_resets_of_food_states(tmp_path):
    """groceries 由「做饭」凭着、写了 food，扑克牌重置它：扑克牌与三类食品商品是食品商品，纸巾、平台笔记本不是。"""
    book = book_of(grocery_environment(tmp_path / "input"))
    assert {"playing-cards", "beef-noodles", "chips", "lemon-tea"} <= book.food_goods
    assert not {"tissue", "online-notebook"} & book.food_goods
    assert book.food_names == tuple(book.catalog.goods[key].name for key in sorted(book.food_goods))
    assert book.food_names == ("牛肉面", "薯片", "柠檬茶", "扑克牌", "饭团")


def test_a_state_consumed_without_a_food_effect_is_not_a_food_state(tmp_path):
    """detergent 由纸巾重置、由一条不补能的家中内容凭着：纸巾不是食品商品，那条内容不在食品单元里。"""
    root = grocery_environment(tmp_path / "input")
    write_catalog_rows(root, "callbacks.jsonl", sorted([*catalog_rows(root, "callbacks.jsonl"), {
        "state": "detergent", "initial": 1, "cap": 1, "reset_on_goods": ["tissue"]}], key=lambda row: row["state"]))
    set_content(root, "c-chat", consumes_state="detergent")
    book = book_of(root)
    assert "tissue" not in book.food_goods and "c-chat" not in book.food_units
    assert "cook" in book.food_units


def test_the_whole_dinner_series_is_one_food_unit(tmp_path):
    """「做晚饭」三集都在食品单元里，包括没有 food、也不凭状态的第 3 集《洗碗收拾厨房》。"""
    book = book_of(meal_environment(tmp_path / "input"))
    assert {"dinner-1-cook", "dinner-2-eat", "dinner-3-wash", "c-rest"} <= book.food_units
    assert "c-chat" not in book.food_units
    assert book.catalog.contents["dinner-3-wash"].completion_effects is None


def test_sleep_is_life_even_at_the_workplace_during_duty(tmp_path):
    book = book_of(sample(tmp_path))
    assert activity_derivation(book, activity("sleep", "office", at(MONDAY, "10:00")), ()) == Derivation("生活", 1)


def test_start_content_override_wins_over_the_location_rules(tmp_path):
    root = sample(tmp_path)
    set_content(root, "topic-sales", memory_category="饮食")
    assert derive(book_of(root), doing("topic-sales", "office", at(MONDAY, "10:00"))) == ("饮食", 0)


def test_app_override_applies_when_the_start_content_has_none_and_content_override_wins(tmp_path):
    root = sample(tmp_path)
    set_app(root, "bilibili", memory_category="工作")
    snapshot = using("bilibili", "c-article", "home", at(MONDAY, "20:00"))
    assert derive(book_of(root), snapshot) == ("工作", 0)
    set_content(root, "c-article", memory_category="生活")
    assert derive(book_of(root), snapshot) == ("生活", 0)


def test_purchase_is_food_when_any_goods_is_food(tmp_path):
    book = book_of(sample(tmp_path))
    snapshot = activity("purchase", "home", at(MONDAY, "10:00"), order_ids=["o-1"])
    assert derive(book, snapshot, ["tissue"]) == ("生活", 2)
    assert derive(book, snapshot, ["tissue", "lemon-tea"]) == ("饮食", 2)
    with pytest.raises(IntegrityError, match="^Memory category needs at least one goods$"):
        activity_derivation(book, snapshot, [])


def test_purchase_goods_come_from_the_start_result_orders():
    """商品取开始结果里这项活动的订单，按订单身份升序；领取的快递不算这次下的单。"""
    orders = [{"order_id": "o-2", "goods_id": "lemon-tea", "fulfillment": "delivery", "status": "pending"},
              {"order_id": "o-1", "goods_id": "tissue", "fulfillment": "delivery", "status": "pending"},
              {"order_id": "o-3", "goods_id": "chips", "fulfillment": "station_pickup", "status": "delivered"}]
    start = SimpleNamespace(data={"data": {"orders": orders}})
    moment = at(MONDAY, "10:00")
    assert purchase_goods(activity("purchase", "home", moment, order_ids=["o-2", "o-1"]), start) == (
        "tissue", "lemon-tea")
    for identities in (["o-2", "o-3"], ["o-1", "o-9"]):
        with pytest.raises(IntegrityError, match="^Purchase activity names an order that is not in its start result$"):
            purchase_goods(activity("purchase", "home", moment, order_ids=identities), start)
    assert purchase_goods(doing("c-chat", "home", moment), start) == ()


def test_query_category_is_fixed_by_targets_not_by_the_answer(tmp_path):
    book = book_of(sample(tmp_path))
    moment = at(MONDAY, "10:00")
    answer = {"rows": [{"goods_id": "beef-noodles"}, {"goods_id": "lemon-tea"}]}
    snapshot = activity("query", "home", moment, targets=[{"kind": "category", "category": "goods"}], answer=answer)
    assert derive(book, snapshot) == ("生活", 2)
    assert derive(book, activity("query", "home", moment, targets=[{"kind": "category", "category": "drink"}])) == (
        "饮食", 2)
    assert derive(book, activity("query", "home", moment, targets=[{"kind": "goods", "goods_id": "beef-noodles"}])) == (
        "饮食", 2)


def test_commute_between_home_and_workplace_on_a_workday_is_work(tmp_path):
    book = book_of(sample(tmp_path))
    for origin, destination in (("home", "office"), ("office", "home")):
        assert derive(book, activity("travel", origin, at(MONDAY, "08:30"), destination)) == ("工作", 4)
        assert derive(book, activity("travel", origin, at(SATURDAY, "08:30"), destination)) == ("生活", 7)
    assert derive(book, activity("travel", "office", at(MONDAY, "12:10"), "park")) == ("生活", 7)


def test_activity_at_the_workplace_on_a_workday_is_work_at_any_hour(tmp_path):
    book = book_of(sample(tmp_path))
    for clock in ("07:00", "12:30", "23:00"):
        assert derive(book, doing("topic-sales", "office", at(MONDAY, clock))) == ("工作", 5)
    assert derive(book, doing("topic-sales", "office", at(SATURDAY, "10:00"))) == ("生活", 7)


def test_away_from_home_during_duty_is_work_and_during_a_break_is_life(tmp_path):
    book = book_of(sample(tmp_path))
    assert derive(book, doing("c-walk", "park", at(MONDAY, "10:00"))) == ("工作", 5)
    assert derive(book, doing("c-walk", "park", at(MONDAY, "12:30"))) == ("生活", 7)
    assert derive(book, doing("c-chat", "home", at(MONDAY, "10:00"))) == ("生活", 7)


def test_a_food_unit_at_the_workplace_on_duty_is_food_before_work(tmp_path, store):
    root = sample(tmp_path)
    set_content(root, "topic-sales", completion_effects={"food": "meal"})
    book = book_of(root)
    assert activity_derivation(book, doing("topic-sales", "office", at(MONDAY, "10:00")), ()) == Derivation("饮食", 3)
    assert RowCategory("content", "topic-sales", "饮食", 3) in row_categories(book)
    request = requested(store, "start_main_activity", main("interact", "公司", "季度销售数据整理"))
    assert rejected_category(book, store, request) == "饮食"


def test_a_rejected_location_request_uses_the_content_override(tmp_path, store):
    root = sample(tmp_path)
    set_content(root, "c-chat", memory_category="工作")
    request = requested(store, "start_main_activity", main("interact", "家", "测试闲聊"))
    assert rejected_category(book_of(root), store, request) == "工作"


def test_app_content_requiring_the_workplace_kind_is_work_and_other_apps_are_life(tmp_path):
    """前提要求 office 的哔哩哔哩内容是工作（规则 6）；其余每条哔哩哔哩内容在公司、在岗时段都是生活（规则 6）。"""
    book = book_of(sample(tmp_path, OFFICE_DOC))
    assert derive(book, using("bilibili", "c-office-doc", "office", at(MONDAY, "10:00"))) == ("工作", 6)
    others = [content_id for content_id in book.catalog.contents_by_app["bilibili"] if content_id != "c-office-doc"]
    assert others
    for content_id in others:
        assert derive(book, using("bilibili", content_id, "office", at(MONDAY, "10:00"))) == ("生活", 6), content_id


def test_category_follows_the_start_moment_and_the_start_content(tmp_path):
    """自动接到第二条之后类别仍按开始的那一条；08:50 开始、跨过 09:00 上班时刻的事按开始那一刻判定。"""
    book = book_of(sample(tmp_path, OFFICE_DOC))
    played = [{"content_id": "c-office-doc", "from_position": 0, "to_position": 600}]
    assert derive(book, using("bilibili", "c-article", "office", at(MONDAY, "10:00"), played)) == ("工作", 6)
    assert derive(book, using("bilibili", "c-article", "office", at(MONDAY, "10:00"))) == ("生活", 6)
    walk = doing("c-walk", "park", at(MONDAY, "08:50")).model_copy(update={"planned_end_at": at(MONDAY, "09:30")})
    assert derive(book, walk) == ("生活", 7)


def test_without_a_work_schedule_nothing_derives_work_and_work_is_disabled(tmp_path):
    book = book_of(without_work_schedule(sample(tmp_path, OFFICE_DOC)))
    assert (book.schedule, book.workplace_id, book.workplace_kind) == (None, None, None)
    assert derive(book, activity("travel", "home", at(MONDAY, "08:30"), "office")) == ("生活", 7)
    assert derive(book, doing("topic-sales", "office", at(MONDAY, "10:00"))) == ("生活", 7)
    assert derive(book, using("bilibili", "c-office-doc", "office", at(MONDAY, "10:00"))) == ("生活", 6)
    assert book.categories == ("饮食", "生活")


def test_a_work_override_enables_the_work_category_without_a_schedule(tmp_path):
    root = without_work_schedule(sample(tmp_path))
    set_content(root, "c-chat", memory_category="工作")
    assert book_of(root).categories == MEMORY_CATEGORIES


def test_errand_lines_take_the_category_of_the_errand_goods(tmp_path):
    book = book_of(sample(tmp_path))
    assert (goods_category(book, ["lemon-tea"]), goods_category(book, ["tissue"])) == ("饮食", "生活")
    with pytest.raises(IntegrityError, match="^Memory category needs at least one goods$"):
        goods_category(book, [])


@pytest.mark.parametrize("action,names,category", [
    ("query", ["盖浇饭"], "饮食"),
    ("query", ["一人份食材包"], "饮食"),
    ("query", ["奶茶"], "饮食"),
    ("query", ["饮料"], "饮食"),
    ("query", ["商品"], "生活"),
    ("purchase", ["纸巾"], "生活"),
    ("purchase", ["洗衣液"], "生活"),
    ("purchase", ["纸巾", "冰美式"], "生活"),
    ("purchase", ["纸巾", "柠檬茶"], "饮食"),
    ("purchase", ["茶具set"], "生活"),
    ("query", ["饮 料"], "饮食"),
    ("purchase", [" 纸 巾 ", "柠 檬茶"], "饮食"),
])
def test_rejected_purchase_and_query_use_rows_then_the_text_rule(tmp_path, store, action, names, category):
    """名称先对商品类别名、再对商品名（去空白、不分大小写），都对不上才看文字；任一个名称为饮食即饮食。"""
    book = book_of(add_rows(sample(tmp_path), "merchant-catalog.jsonl", "goods_id", TEA_SET))
    assert rejected_category(book, store, requested(store, "start_side_activity", side(action, *names))) == category


def test_rejected_start_requests_resolve_catalog_rows(tmp_path, store):
    root = sample(tmp_path)
    set_app(root, "bilibili", memory_category="饮食")
    book = book_of(root)
    cases = [
        (main("travel", "公司", "步行"), "工作"),
        (main("travel", "火星", "步行"), "生活"),
        (main("interact", "公司", "季度销售数据整理"), "工作"),
        (main("interact", "公司", "开会"), "生活"),
        (main("sleep", "480"), "生活"),
    ]
    for number, (request, category) in enumerate(cases, start=1):
        event = requested(store, "start_main_activity", request, observed="home", decision=f"d-{number}")
        assert rejected_category(book, store, event) == category, request
    for number, (name, category) in enumerate((("哔哩哔哩", "饮食"), ("不存在的 app", "生活")), start=10):
        event = requested(store, "start_side_activity", side("interact", name), decision=f"d-{number}")
        assert rejected_category(book, store, event) == category, name
    twice = requested(store, "start_main_activity", main("travel", "公司", "步行"), observed="home", observations=2,
                      decision="d-twice")
    with pytest.raises(IntegrityError, match="^Rejected request has no unique decision observation$"):
        rejected_category(book, store, twice)


def test_rejected_control_requests_and_plan_revisions_are_life(tmp_path, store):
    root = sample(tmp_path)
    set_content(root, "topic-sales", memory_category="工作")
    book = book_of(root)
    for number, tool in enumerate(("report_status", "pause_activity", "conclude_activity", "revise_day_plan")):
        event = requested(store, tool, {"items": [{"activity_name": "季度销售数据整理"}]}, decision=f"d-{number}")
        assert rejected_category(book, store, event) == "生活", tool


def test_primary_request_of_a_start_group_is_the_main_slot(store):
    side_request = requested(store, "start_side_activity", side("purchase", "纸巾"), decision="d-1")
    main_request = requested(store, "start_main_activity", main("sleep", "480"), decision="d-1")
    conclude = requested(store, "conclude_activity", {"items": []}, decision="d-2")
    assert primary_request([side_request, main_request]) is main_request
    assert primary_request([conclude]) is conclude
    with pytest.raises(IntegrityError, match="^Decision has no world request$"):
        primary_request([])


def test_request_category_follows_the_started_or_named_activity(tmp_path, store):
    root = sample(tmp_path)
    set_app(root, "bilibili", memory_category="饮食")
    book = book_of(root)
    named = {"a-1": "工作", "a-2": "生活"}
    start = requested(store, "start_main_activity", main("interact", "公司", "季度销售数据整理"), decision="d-1")
    refused_app = requested(store, "start_side_activity", side("interact", "哔哩哔哩"), decision="d-2")
    conclude = requested(store, "conclude_activity", {"items": []}, decision="d-3")
    revise = requested(store, "revise_day_plan", {"goals": []}, decision="d-4")
    accepted = SimpleNamespace(activity_id="a-2", data={"status": "in_progress", "data": {}})
    rejected = SimpleNamespace(activity_id=None, data={"status": "rejected", "data": {}})
    completed = SimpleNamespace(activity_id=None,
                                data={"status": "completed", "data": {"activity_ids": ["a-1", "a-2"]}})
    assert request_category(book, store, start, accepted, named) == "生活"
    assert request_category(book, store, refused_app, rejected, named) == "饮食"
    assert request_category(book, store, conclude, completed, named) == "工作"
    assert request_category(book, store, conclude, rejected, named) == "生活"
    assert request_category(book, store, revise, rejected, named) == "生活"
    with pytest.raises(IntegrityError, match="^Memory category names an activity that was not read$"):
        request_category(book, store, start, SimpleNamespace(activity_id="a-9", data={}), named)
    parameters = inspect.signature(request_category).parameters
    assert list(parameters) == ["book", "store", "request", "result", "activity_categories"]
    assert not any("WorldState" in str(item.annotation) for item in parameters.values())


def test_category_label_puts_the_primary_first_and_dedupes_in_order():
    assert category_label("工作", ["饮食", "工作"]) == "工作、饮食"
    assert category_label("饮食", ["饮食"]) == "饮食"
    assert category_label(None, ["生活", "饮食", "生活"]) == "生活、饮食"
    with pytest.raises(IntegrityError, match="^Memory entry has no category$"):
        category_label(None, [])


def test_row_categories_list_contents_then_apps_with_rules(tmp_path):
    root = add_rows(meal_environment(tmp_path / "input"), "content-catalog.jsonl", "content_id", OFFICE_DOC)
    book = book_of(root)
    rows = row_categories(book)
    assert [(row.kind, row.row_id) for row in rows] == [*(("content", key) for key in sorted(book.catalog.contents)),
                                                        *(("app", key) for key in sorted(book.catalog.apps))]
    found = {(row.kind, row.row_id): (row.category, row.rule) for row in rows}
    assert found[("content", "topic-sales")] == ("工作", 5)
    assert [found[("content", key)] for key in ("dinner-1-cook", "dinner-2-eat", "dinner-3-wash", "c-rest")] == [
        ("饮食", 3)] * 4
    assert found[("content", "c-office-doc")] == ("工作", 6)
    assert (found[("app", "bilibili")], found[("app", "douyin")]) == (("工作", 6), ("生活", 6))
    assert found[("content", "c-chat")] == ("生活", 7)


def test_memory_book_is_built_once_per_store_and_rebuilt_for_new_inputs(tmp_path, store):
    root = sample(tmp_path)
    first = inputs_of(root)
    book = memory_book(store, first)
    assert memory_book(store, first) is book
    second = inputs_of(root)
    rebuilt = memory_book(store, second)
    assert rebuilt is not book and rebuilt == category_book(second)
    assert memory_book(store, second) is rebuilt


def _state(activity_id: str, action: str, location: str, start: str, kind: str = "interact", name: str = "",
           **data) -> dict:
    """一项进行中活动的快照（JSON 形状），开始于星期一的 start。"""
    started = at(MONDAY, start)
    return ActivityState(activity_id=activity_id, agent_id=1, location_id=location, kind=kind, status="active",
                         started_at=started, planned_end_at=started + 3600, last_settled_at=started,
                         elapsed_seconds=0, planned_duration_seconds=3600, origin_action_id=action,
                         origin_decision_id="d", origin_request_event_id="e",
                         name=name or f"进行{activity_id}（1月5日 {start} 起）", data=data).model_dump(mode="json")


def _ended(snapshot: dict, clock: str) -> dict:
    """同一活动按计划结束时的终态快照。"""
    moment = at(MONDAY, clock)
    return {**snapshot, "status": "completed", "ended_at": moment, "last_settled_at": moment,
            "boundary_type": "planned_end"}


def _office(log) -> dict:
    """星期一 10:00 在公司开始季度销售数据整理。"""
    work = _state("work", "d-work:a", "office", "10:00", app_id=None, content_id="topic-sales", played=[])
    entry_events.start(log, "d-work", at(MONDAY, "10:00"), work)
    return work


def _chat(log) -> dict:
    """星期一 10:00 在家开始测试闲聊。"""
    chat = _state("chat", "d-chat:a", "home", "10:00", app_id=None, content_id="c-chat", played=[])
    entry_events.start(log, "d-chat", at(MONDAY, "10:00"), chat)
    return chat


def _conclude(log, activity: dict, views=(), memories=(), summary="收尾。") -> None:
    """10:30 受理的收尾：一项，点名这项活动，带看法与普通记忆。"""
    item = {"activity_name": activity["name"], "summary": summary, "memories": list(memories), "views": list(views)}
    request = log.add("action_requested", at(MONDAY, "10:30"), {"tool_name": "conclude_activity",
                      "request": {"items": [item], "errands": []}, "self_note": None},
                      decision_id="d-end", action_id="d-end:a")
    log.add("action_result", at(MONDAY, "10:30"), {"status": "completed", "data": {
        "activity_ids": [activity["activity_id"]]}}, decision_id="d-end", action_id=request.action_id)
    log.seal()


def _view(about: str, text: str) -> dict:
    """一条关系看法。"""
    return {"type": "relationship", "about": about, "text": text, "importance": 0.5, "keywords": []}


def _order(order_id: str, goods_id: str, paid: bool) -> dict:
    """一张外卖订单的快照：已付款时带付款时刻。"""
    return {"order_id": order_id, "agent_id": 1, "goods_id": goods_id, "quantity": 1, "total_price_fen": 1200,
            "fulfillment": "delivery", "status": "pending", "destination_id": "home",
            "paid_at": at(MONDAY, "10:05") if paid else None, "expected_at": at(MONDAY, "10:30")}


def _case_ending(log):
    """活动结束句：公司里的季度销售数据整理按计划结束后改计划 → 那项活动的类别。"""
    work = _office(log)
    entry_events.ended(log, at(MONDAY, "10:20"), _ended(work, "10:20"))
    entry_events.revise(log, "d-plan", at(MONDAY, "10:30"))
    return ["工作", "生活"], [], "生活、工作"


def _case_purchase(log):
    """购买行只按已付款订单的商品：已付款的纸巾、未付款的柠檬茶 → 生活。"""
    orders = [_order("o-1", "tissue", True), _order("o-2", "lemon-tea", False)]
    buying = _state("buy", "d-buy:a", "home", "10:00", kind="purchase", order_ids=["o-1", "o-2"])
    request = log.add("action_requested", at(MONDAY, "10:00"), {"tool_name": "start_side_activity", "request": {
        "action": "purchase", "target": ["纸巾", "柠檬茶"], "prefer": None}}, decision_id="d-buy", action_id="d-buy:a")
    log.add("action_result", at(MONDAY, "10:00"), {"status": "in_progress", "activity": buying, "data": {
        "orders": [{**order, "paid_at": None} for order in orders]}}, decision_id="d-buy", action_id=request.action_id,
        activity_id="buy")
    log.seal()
    log.add("action_result", at(MONDAY, "10:05"), {"status": "completed", "activity": _ended(buying, "10:05"),
            "data": {"orders": orders}}, action_id=request.action_id, activity_id="buy")
    log.seal()
    entry_events.revise(log, "d-plan", at(MONDAY, "10:30"))
    return ["生活", "生活"], [], "生活"


def _case_delivery(log):
    """送达行与待取行：送达柠檬茶 → 饮食；纸巾到取货点 → 生活。"""
    log.add("environment_event", at(MONDAY, "10:10"), {"kind": "delivery_arrival", "orders": [
        {"agent_id": 1, "goods_id": "lemon-tea", "status": "delivered"},
        {"agent_id": 1, "goods_id": "tissue", "status": "ready_for_pickup"}]})
    log.seal()
    entry_events.revise(log, "d-plan", at(MONDAY, "10:30"))
    return ["饮食", "生活", "生活"], [], "生活、饮食"


def _case_query(log):
    """查完行：查询柠檬茶做完 → 那项查询活动的类别（饮食）。"""
    asking = _state("ask", "d-ask:a", "home", "10:00", kind="query", app_id=None, scoped=False,
                    targets=[{"kind": "goods", "goods_id": "lemon-tea"}], name="查询柠檬茶（1月5日 10:00 起）")
    entry_events.start(log, "d-ask", at(MONDAY, "10:00"), asking)
    done = {**_ended(asking, "10:05"), "data": {**asking["data"], "answer": {"rows": [], "omitted": 0}}}
    entry_events.ended(log, at(MONDAY, "10:05"), done)
    entry_events.revise(log, "d-plan", at(MONDAY, "10:30"))
    return ["饮食", "生活"], [], "生活、饮食"


def _case_errand(log):
    """办成与新增个人事务：按事务商品，柠檬茶 → 饮食，纸巾 → 生活。"""
    chat = _chat(log)
    log.add("environment_event", at(MONDAY, "10:10"), {"kind": "errand_completed", "errand": {
        "name": "备好饮料", "product_id": "lemon-tea"}})
    log.seal()
    request = entry_events.control(log, "d-report", at(MONDAY, "10:30"), "report_status", [chat["name"]], ["chat"])
    log.add("environment_event", at(MONDAY, "10:30"), {"kind": "errand_registered", "errand": {
        "name": "买纸巾", "product_id": "tissue"}}, caused_by=[request.event_id], visibility="system")
    log.seal()
    log.ends = {seq: log.last_event_seq if seq >= request.event_seq else end for seq, end in log.ends.items()}
    return ["饮食", "生活"], ["生活"], "生活、饮食"


def _case_pause(log):
    """暂停行与暂停的总结：被暂停活动的类别（公司里的工作）。"""
    work = _office(log)
    entry_events.control(log, "d-pause", at(MONDAY, "10:30"), "pause_activity", [work["name"]], ["work"])
    return ["工作"], ["工作"], "工作"


def _case_start(log):
    """开始组的开始行：取槽位 1 那一项（公司里的工作），不看同组购买柠檬茶。"""
    work = _state("work", "d-pair:a1", "office", "10:00", app_id=None, content_id="topic-sales", played=[])
    tea = _state("buy", "d-pair:a2", "office", "10:00", kind="purchase", order_ids=["o-1"])
    for tool, body, activity in (("start_side_activity", {"action": "purchase", "target": ["柠檬茶"], "prefer": None},
                                  tea), ("start_main_activity", {"action": "interact", "target": "公司",
                                                                 "detail": "季度销售数据整理"}, work)):
        request = log.add("action_requested", at(MONDAY, "10:00"), {"tool_name": tool, "request": body},
                          decision_id="d-pair", action_id=activity["origin_action_id"])
        log.add("action_result", at(MONDAY, "10:00"), {"status": "in_progress", "activity": activity, "data": {
            "orders": [_order("o-1", "lemon-tea", False)]}}, decision_id="d-pair", action_id=request.action_id,
            activity_id=activity["activity_id"])
    log.seal()
    return ["工作"], [], "工作"


def _case_rejection(log):
    """被拒行与它的正文：被拒的购买柠檬茶 → 饮食；被拒的控制请求的被拒行与正文都是生活。"""
    request = log.add("action_requested", at(MONDAY, "10:00"), {"tool_name": "start_side_activity", "request": {
        "action": "purchase", "target": ["柠檬茶"], "prefer": None}, "self_note": "想喝柠檬茶。"},
        decision_id="d-buy", action_id="d-buy:a")
    log.add("action_result", at(MONDAY, "10:00"), {"status": "rejected", "data": {}}, decision_id="d-buy",
            action_id=request.action_id)
    log.seal()
    return ["饮食"], ["饮食"], "饮食"


def _case_rejected_control(log):
    """被拒的控制请求只有被拒行与正文，二者都是生活。"""
    request = log.add("action_requested", at(MONDAY, "10:00"), {"tool_name": "report_status", "request": {
        "items": [{"activity_name": "季度销售数据整理", "summary": "继续。"}], "errands": []},
        "self_note": "看看进度。"}, decision_id="d-report", action_id="d-report:a")
    log.add("action_result", at(MONDAY, "10:00"), {"status": "rejected", "data": {}}, decision_id="d-report",
            action_id=request.action_id)
    log.seal()
    return ["生活"], ["生活"], "生活"


def _case_revise(log):
    """改了今日计划：生活。"""
    entry_events.revise(log, "d-plan", at(MONDAY, "10:30"))
    return ["生活"], [], "生活"


def _case_fallback(log):
    """兜底行：播报公司里的工作而总结空白 → 这次请求第一项所点名活动的类别。"""
    work = _office(log)
    entry_events.control(log, "d-report", at(MONDAY, "10:30"), "report_status", [work["name"]], ["work"], [" "])
    return ["工作"], [], "工作"


def _case_summary(log):
    """收尾的总结与普通记忆（含 routine）：所点名活动的类别（公司里的工作）。"""
    work = _office(log)
    entry_events.ended(log, at(MONDAY, "10:20"), _ended(work, "10:20"))
    memories = [{"type": "commitment", "text": "明天接着做。", "importance": 0.5, "keywords": []},
                {"type": "routine", "text": "上午先整理数据。", "importance": 0.5, "keywords": []}]
    _conclude(log, work, memories=memories)
    return ["工作"], ["工作", "工作", "工作"], "工作"


def _case_progress(log):
    """进展：所点名活动的类别（家里的测试闲聊）。"""
    chat = _chat(log)
    entry_events.control(log, "d-report", at(MONDAY, "10:30"), "report_status", [chat["name"]], ["chat"])
    return [], ["生活"], "生活"


def _case_work_view(log):
    """收尾公司里的季度销售数据整理时写对老周的关系看法 → 这一项为工作。"""
    work = _office(log)
    entry_events.ended(log, at(MONDAY, "10:20"), _ended(work, "10:20"))
    _conclude(log, work, views=[_view("老周", "他的数据很准。")])
    return ["工作"], ["工作", "工作"], "工作"


def _case_home_view(log):
    """收尾在家的测试闲聊时写对同一对象的看法 → 生活。"""
    chat = _chat(log)
    entry_events.ended(log, at(MONDAY, "10:10"), _ended(chat, "10:10"))
    _conclude(log, chat, views=[_view("老周", "聊得很开心。")])
    return ["生活"], ["生活", "生活"], "生活"


def _case_report_with_delivery(log):
    """工作日在公司播报季度销售数据整理，窗口里送达柠檬茶：行 [饮食]，项 [工作]，类别串「工作、饮食」。"""
    work = _office(log)
    log.add("environment_event", at(MONDAY, "10:10"), {"kind": "delivery_arrival", "orders": [
        {"agent_id": 1, "goods_id": "lemon-tea", "status": "delivered"}]})
    log.seal()
    entry_events.control(log, "d-report", at(MONDAY, "10:30"), "report_status", [work["name"]], ["work"])
    return ["饮食"], ["工作"], "工作、饮食"


def _case_body(log):
    """正文：本次决策主请求的类别（开始公司里的工作）。"""
    work = _state("work", "d-work:a", "office", "10:00", app_id=None, content_id="topic-sales", played=[])
    entry_events.start(log, "d-work", at(MONDAY, "10:00"), work, note="先把数据理一遍。")
    return ["工作"], ["工作"], "工作"


def _entry_of(inputs, log):
    """经 agent_events、decision_commits 与 build_entry 逐次建条目，返回最后一条；窗口下界是上一次决策的提交末序号。"""
    book = memory_book(log, inputs)
    cached = agent_events(log, 1, inputs.catalog.kit_states())
    entry, lower = None, 0
    for decision in decision_commits(log, cached, log.last_event_seq):
        entry, lower = build_entry(book, log, cached, decision, lower), decision.commit
    return entry


def _case_crossing(log):
    """跨过零点行（零点收尾条目）：那项活动的类别（公司里的工作）。"""
    _office(log)
    return ["工作"], [], "工作"


@pytest.mark.parametrize("case", [
    _case_ending, _case_purchase, _case_delivery, _case_query, _case_errand, _case_pause, _case_start,
    _case_rejection, _case_revise, _case_crossing, _case_fallback, _case_summary, _case_progress, _case_work_view,
    _case_home_view, _case_body, _case_report_with_delivery, _case_rejected_control,
], ids=lambda case: case.__name__.removeprefix("_case_"))
def test_line_and_item_categories_follow_the_two_tables(tmp_path, case):
    """在手写事件上建条目：各行、各项与条目的类别照「记忆的类别」的核验行表与自述项表。"""
    inputs = inputs_of(sample(tmp_path))
    log = entry_events.Log()
    lines, items, label = case(log)
    if case is _case_crossing:
        book, cached = memory_book(log, inputs), agent_events(log, 1, inputs.catalog.kit_states())
        entry = build_closing_entry(book, cached, MONDAY, (log.last_event_seq, log.last_event_seq))
    else:
        entry = _entry_of(inputs, log)
    assert [line.category for line in entry.lines] == lines
    assert [item.category for item in entry.items] == items
    assert entry.category == label
