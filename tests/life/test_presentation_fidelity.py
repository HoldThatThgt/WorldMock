"""生活活动的地点类资格、排序与轮转（docs/dev/SPEC-dayrun-fidelity.md 5.1，裁决 S8、S4；验收台账「呈现」）。

全部经真实引擎的观察路径（engine.observe 与它装配的呈现窗口）：生活活动窗口按当前地点施加地点类资格，排除项带
ELIGIBILITY_LOCATION_KIND_MISMATCH；排序键为（非零口味，−口味×注意力，−相关度，哈希决胜键），零口味时的顺序是
`sha256(场景|人物|日期|对象)` 的顺序，随日期变化；相关度只取目录时间窗、当日未推进目标的活动家族与人设层习惯签名
（反应文本冻结在运行清单）；轮转按每个对象的 last_presented_at，换新名额留给该分区内最久未呈现的对象，不共享偏移量，
`⌈N ÷ k⌉` 的上界按分区成立。呈现留痕与时钟推进用引擎提交路径的同一对函数（stage_presentation、flush_presentation）。
"""

from __future__ import annotations

import math
from datetime import datetime
from zoneinfo import ZoneInfo

from agentsociety2.society import DirectToolCall

from tests.life.conftest import context, stage_day_plan, start_activity, tool_targets
from worldmock.footprints.models import EventDraft
from worldmock.life.activities import location_kinds_allow
from worldmock.life.candidates import ORDER_KEY_VERSION, tiebreak_digest
from worldmock.life.engine import LifeEngine
from worldmock.life.models import PlanGoalInput, ReviseDayPlanRequest
from worldmock.life.relevance import MANIFEST_KEY
from worldmock.life.rotation import rotation_quota_slots
from worldmock.life.schedule import PlanGoal
from worldmock.life.state_commit import flush_presentation, stage_presentation

_DAY = 86_400
#: 习惯签名的反应文本：命中生活活动「晨间洗漱」的条目名。
_HABIT = "起床后先晨间洗漱"


def _epoch(engine: LifeEngine, day: int, hour: int) -> int:
    """场景时区 2026 年 1 月 day 日 hour 点的纪元秒。"""
    return int(datetime(2026, 1, day, hour, tzinfo=ZoneInfo(engine.scenario.timezone)).timestamp())


def _stage(engine: LifeEngine, moment: int | None = None, location: str | None = None,
           stamps: dict[str, int] | None = None) -> None:
    """把没有活动的隔离运行置于 moment（地点 location、呈现留痕 stamps），经存储提交（版本加一）。"""
    staged = engine.state.model_copy(deep=True)
    now = staged.current_time if moment is None else moment
    staged.current_time = now
    staged.pending_events = [event for event in staged.pending_events if event.due_at > now]
    agent = staged.agents[1]
    staged.agents[1] = agent.model_copy(update={"next_decision_at": now, "body_settled_at": now,
                                                "location_id": location or agent.location_id}, deep=True)
    if stamps is not None:
        staged.presentation_state = {1: dict(stamps)}
    staged.version += 1
    engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=now, visibility="system", data={"kind": "test_staged"})])
    engine.state = staged


def _present(engine: LifeEngine, label: str) -> dict:
    """一次呈现：真实观察（窗口与留痕由观察算出），随后按决策轮提交与时钟推进的同一对函数并入留痕、时钟前进一分钟。"""
    observed = engine.observe(context(engine, label))
    staged = flush_presentation(stage_presentation(engine.state, engine._pending_presentation))
    engine._pending_presentation.clear()
    engine.state = staged
    _stage(engine, engine.state.current_time + 60)
    return observed


def _round(engine: LifeEngine, name: str, tool: str, request) -> tuple[dict, object]:
    """一轮真实决策：观察、登记并结算，返回本轮观察与人物的结果。"""
    decision = context(engine, name)
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    engine.submit_action(DirectToolCall(decision, f"action-{name}", tool, request))
    return observed, engine.commit_decision_round(name)[0]


def _lifestyle(observed: dict) -> list[str]:
    """本轮工具层对应的生活活动绑定，按呈现顺序。"""
    return [row["object_id"] for row in observed["interaction_bindings"] if row["kind"] == "lifestyle"]


def _allowed(engine: LifeEngine, place: str) -> list[str]:
    """该地点的地点类允许的全部生活活动（目录声明的地点类与地点声明相容）。"""
    kinds = frozenset(engine.rules.locations[place].location_kinds)
    return sorted(item.activity_id for item in engine.lifestyle_items
                  if location_kinds_allow(kinds, engine.rules.activity_location_kinds[item.activity_id]))


def _hashed(engine: LifeEngine, ids, day: str) -> list[str]:
    """按决胜键 sha256(场景|人物|日期|对象) 的顺序排列。"""
    return sorted(ids, key=lambda item: tiebreak_digest(engine.scenario.scenario_id, 1, day, item))


def test_lifestyle_window_excludes_location_kind_mismatches_with_the_reason_code(engine_factory) -> None:
    """生活活动窗口按当前地点取候选：地点类不符的条目不进窗口，以 ELIGIBILITY_LOCATION_KIND_MISMATCH 记为排除项（每个
    这样的条目一条，计数一致）；窗口里的条目都能在这里做，工具层不再给它们标「这里不适合做这件事」；换到办公室，
    窗口随地点换成办公室允许的条目。"""
    engine = engine_factory()
    stage_day_plan(engine)
    for place in ("home", "office"):
        _stage(engine, location=place)
        allowed = set(_allowed(engine, place))
        window = engine._menu_windows(engine.state.agents[1])["lifestyle"]
        shown = [row["activity_id"] for row in window.items]
        assert shown and set(shown) <= allowed
        mismatched = {item.object_id for item in window.excluded if item.reason == "ELIGIBILITY_LOCATION_KIND_MISMATCH"}
        assert mismatched == {item.activity_id for item in engine.lifestyle_items} - allowed
        assert window.exclusion_counts["ELIGIBILITY_LOCATION_KIND_MISMATCH"] == len(mismatched) > 0
        deferred = {item.object_id for item in window.excluded if item.reason == "ROTATION_DEFERRED"}
        assert deferred == allowed - set(shown)
        observed = engine.observe(context(engine, f"kinds-{place}"))
        names = [row["object"] for row in observed["interaction_bindings"] if row["kind"] == "lifestyle"]
        assert _lifestyle(observed) == shown
        assert all(action["reason"] != "这里不适合做这件事" for name in names
                   for action in tool_targets(observed)[name]["actions"])
    assert "wellness-morning-wash" in _allowed(engine, "home") and "wellness-morning-wash" not in _allowed(engine,
                                                                                                          "office")


def test_zero_taste_order_is_not_alphabetical_and_changes_with_the_date(engine_factory) -> None:
    """零口味（引擎直驱没有口味权重）、零相关度（零点：目录时间窗都不覆盖、没有目标与习惯）时，生活活动窗口按
    sha256(场景|人物|日期|对象) 排序，不是对象标识的字母序；同一时刻换到第二天，顺序随之改变。"""
    engine = engine_factory()
    stage_day_plan(engine)
    assert engine.state.agents[1].taste_weights == {}
    allowed = _allowed(engine, "home")
    first = _lifestyle(engine.observe(context(engine, "hash-day-1")))
    assert first == _hashed(engine, allowed, "2026-01-05")[:20]
    assert first != allowed[:20] and first != sorted(first)
    _stage(engine, engine.state.current_time + _DAY)
    second = _lifestyle(engine.observe(context(engine, "hash-day-2")))
    assert second == _hashed(engine, allowed, "2026-01-06")[:20]
    assert second != first


def test_relevance_puts_time_window_goal_family_and_habit_hits_ahead(engine_factory) -> None:
    """相关度只取人物自己的事实与目录事实：07:00 在家，当日未推进的「运动」目标给瑜伽 +2（它的时间窗 6–21 点再 +2），
    冻结在运行清单的习惯签名「起床后先晨间洗漱」给晨间洗漱 +1（时间窗 5–10 点 +2）；其余按目录时间窗覆盖此刻 +2，
    同分的按哈希决胜。窗口依次是 4 分、3 分、2 分的条目，此刻不在时间窗里的条目排在它们之后。"""
    engine = engine_factory(create_options={MANIFEST_KEY: {1: [_HABIT]}})
    assert engine.store.manifest[MANIFEST_KEY] == {"1": [_HABIT]}
    _stage(engine, _epoch(engine, 5, 7))
    stage_day_plan(engine, goals=(PlanGoal(intent_kind="运动", goal="练一会儿瑜伽",
                                           registered_at=engine.state.current_time),))
    windows = {item.activity_id: item.time_window for item in engine.lifestyle_items}
    in_window = [item for item in _allowed(engine, "home") if windows[item][0] <= 7 < windows[item][1]]
    order = _lifestyle(engine.observe(context(engine, "relevance")))
    assert order[:2] == ["yoga", "wellness-morning-wash"]
    rest = [item for item in in_window if item not in {"yoga", "wellness-morning-wash"}]
    assert order[2:] == _hashed(engine, rest, "2026-01-05")[:18]
    plain = engine_factory()
    _stage(plain, _epoch(plain, 5, 7))
    stage_day_plan(plain)
    assert _lifestyle(plain.observe(context(plain, "relevance-plain")))[:2] != ["yoga", "wellness-morning-wash"]


def test_goal_families_keep_the_plan_at_the_start_of_the_second(engine_factory) -> None:
    """相关度的当日未推进目标取该秒开始时的计划（同一秒各轮沿用该秒首轮的呈现窗口）：07:00 的「运动」目标让瑜伽排第一；
    本秒首轮用 revise_day_plan 把它换成「家务」，同一秒下一轮的生活活动窗口逐项不变；到下一秒，瑜伽不再因目标加分，
    此刻在时间窗内的家务与宠物类条目（家务意图的两个家族）排到最前。"""
    engine = engine_factory()
    _stage(engine, _epoch(engine, 5, 7))
    stage_day_plan(engine, goals=(PlanGoal(intent_kind="运动", goal="练一会儿瑜伽",
                                           registered_at=engine.state.current_time),))
    now = engine.state.current_time
    first, revised = _round(engine, "family-revise", "revise_day_plan", ReviseDayPlanRequest(goals=[
        PlanGoalInput(intent_kind="家务", goal="收拾房间", remind_time=None, around_time=None),]))
    assert revised.status == "completed" and engine.state.current_time == now
    assert [goal.intent_kind for goal in engine.state.agents[1].day_plan.goals] == ["家务"]
    second, _ = _round(engine, "family-same-second", "start_activity", start_activity("观看", "不存在的对象"))
    assert engine.state.current_time == now
    assert _lifestyle(first)[0] == "yoga" and _lifestyle(second) == _lifestyle(first)
    _stage(engine, now + 1)
    later = _lifestyle(engine.observe(context(engine, "family-next-second")))
    items = {item.activity_id: item for item in engine.lifestyle_items}
    chores = [item for item in _allowed(engine, "home") if items[item].category in {"household", "pet"}
              and (items[item].time_window or (0, 0))[0] <= 7 < (items[item].time_window or (0, 0))[1]]
    assert chores and later[0] != "yoga"
    assert later[:len(chores)] == _hashed(engine, chores, "2026-01-05")[:20]


def test_refresh_quota_goes_to_the_longest_unpresented_lifestyle_entries(engine_factory) -> None:
    """轮转以每个对象的 last_presented_at 判定：别的条目刚呈现过时，换新名额依次给从未呈现的、上次呈现最早的两个（哪怕
    它们的排序键在最后）；只改上次呈现时刻，名额就跟着换人。"""
    engine = engine_factory()
    stage_day_plan(engine)
    now = engine.state.current_time
    ranked = _hashed(engine, _allowed(engine, "home"), "2026-01-05")
    never, oldest, older, old = ranked[-4:]
    stamps = {item: now - 60 for item in ranked if item != never}
    stamps.update({oldest: now - 30 * _DAY, older: now - 10 * _DAY, old: now - 8 * _DAY})
    _stage(engine, stamps=stamps)
    slots = rotation_quota_slots(engine.scenario.candidate_presentation_limit, engine.scenario.rotation_quota_ratio)
    order = _lifestyle(engine.observe(context(engine, "quota")))
    assert slots == 3 and order[:slots] == [never, oldest, older]
    assert old not in order and order[slots:] == ranked[:17]
    stamps[old] = now - 40 * _DAY
    _stage(engine, stamps=stamps)
    assert _lifestyle(engine.observe(context(engine, "quota-swapped")))[:slots] == [never, old, oldest]


def test_rotation_has_no_shared_offset_across_partitions(engine_factory) -> None:
    """不共享偏移量：内容分区的对象全部刚呈现过，生活活动分区的窗口与没有任何留痕时逐项相同（旧实现按人物已留痕的
    对象数定游标，别的分区一呈现，本分区的换新名额就换人）。"""
    engine = engine_factory()
    stage_day_plan(engine)
    before = _lifestyle(engine.observe(context(engine, "partition-before")))
    stamps = {item.item_id: engine.state.current_time - 60 for item in engine.scenario.contents}
    _stage(engine, stamps=stamps)
    assert len(engine.state.presentation_state[1]) == len(engine.scenario.contents)
    assert _lifestyle(engine.observe(context(engine, "partition-after"))) == before


def test_the_ceiling_n_over_k_bound_holds_per_partition(engine_factory) -> None:
    """连续 ⌈N ÷ k⌉ 次呈现之后，内容、游戏、生活活动三个分区各自的每个可呈现对象至少出现一次（N 取该分区资格段保留的
    对象数，k 由场景常量导出），呈现经真实观察，留痕按提交路径并入。"""
    engine = engine_factory()
    stage_day_plan(engine)
    slots = rotation_quota_slots(engine.scenario.candidate_presentation_limit, engine.scenario.rotation_quota_ratio)
    windows = engine._menu_windows(engine.state.agents[1])
    pools = {kind: {row.get("activity_id") or row.get("game_id") or row.get("item_id") for row in window.items}
             | set(window.deferred) for kind, window in windows.items()}
    bounds = {kind: math.ceil(len(pool) / slots) for kind, pool in pools.items()}
    assert len(pools["lifestyle"]) == len(_allowed(engine, "home"))
    first_seen: dict[str, dict[str, int]] = {kind: {} for kind in pools}
    for step in range(max(bounds.values())):
        observed = _present(engine, f"bound-{step}")
        for row in observed["interaction_bindings"]:
            if row["kind"] in first_seen:
                first_seen[row["kind"]].setdefault(row["object_id"], step)
    for kind, pool in pools.items():
        assert set(first_seen[kind]) == pool, kind
        assert max(first_seen[kind].values()) < bounds[kind], (kind, bounds[kind])


def test_order_key_version_is_two() -> None:
    """排序键加相关度、决胜键改哈希：ORDER_KEY_VERSION 为 2（只参与游标身份，升版使在途游标失效）。"""
    assert ORDER_KEY_VERSION == 2
