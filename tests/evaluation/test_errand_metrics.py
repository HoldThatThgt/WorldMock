"""个人事务与收尾记忆指标的定义（docs/dev/SPEC-errands-workday.md 6.1、3.4 第 3 项；记录引擎设计「质量报告」）。

以已认证事件模型（footprints.models.Event）构造与导出 events.jsonl 同形的事件字典，事务快照取自 ErrandState，收尾请求经
control_items 读取：逐键核对 errand_metrics 与 conclusion_memories 的定义与边界（分母是记下的事务而不是当日目标、终态恰在
零点不算跨夜而截止恰在零点算、提醒后行动只认同一本地日且终态之前的被受理请求、办成须有本导出里更早且可复核的证据、
没有样本为 null），并经 build_quality_report 进入报告。真实请求、引擎与 SQLite 路径上的同一报告由
tests/footprints/test_errand_export.py 经认证导出逐值重算。
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from worldmock.evaluation.errand_metrics import conclusion_memories, errand_metrics
from worldmock.footprints.models import Event
from worldmock.footprints.quality import build_quality_report
from worldmock.life.errand_state import (AGENT_VISIBLE_KINDS, CANCELLED_KIND, COMPLETED_KIND, PROGRESSED_KIND,
                                         REGISTERED_KIND, REMINDED_KIND, UPDATED_KIND, ErrandState)

_ZONE = "Asia/Shanghai"
#: 正式目录里的商品身份（纯牛奶、素炒什锦食材包、素炒什锦、两种冰红茶）与两个任意的商品身份。
MILK, KIT, MEAL = "food-6923644266066", "ingredient-vegetable-kit", "meal-0030"
TEA, ICED, BREAD, JUICE = "food-6922507005019", "food-6944839902385", "food-bread", "food-juice"
#: errand_metrics 的十个键（不借用当日目标指标的任何键名）。
_KEYS = ("registered_count", "completed_count", "cancelled_count", "open_at_cutoff_count", "carried_overnight_count",
         "visible_observation_count", "expected_observation_count", "reminded_errand_count",
         "acted_after_reminder_count", "completed_without_evidence_count")
#: 当日目标报告里的计数键名（事务指标一个都不得使用）。
_GOAL_KEYS = {"goal_count", "progressed_count", "discarded_count", "family_progress_rate", "by_priority",
              "routine_goals", "consistency_rate", "block_count"}


def _at(when: str) -> int:
    """场景本地时刻「YYYY-MM-DD HH:MM[:SS]」的纪元秒。"""
    return int(datetime.fromisoformat(when).replace(tzinfo=ZoneInfo(_ZONE)).timestamp())


class _Log:
    """按事件顺序记下的已认证事件字典（Event 模型的 JSON 形状；事件序号、身份与状态版本依次编号）。"""

    def __init__(self) -> None:
        """空的事件序列。"""
        self.events: list[dict] = []

    def add(self, event_type: str, when: str, data: dict, **fields) -> dict:
        """追加一条事件；fields 可给 agent_id（缺省 1）、decision_id、action_id、caused_by 与 visibility（缺省 agent）。"""
        sequence = len(self.events) + 1
        event = Event(event_id=f"event-{sequence}", event_type=event_type, occurred_at=_at(when),
                      agent_id=fields.get("agent_id", 1), decision_id=fields.get("decision_id"),
                      action_id=fields.get("action_id"), caused_by=list(fields.get("caused_by", ())), data=data,
                      visibility=fields.get("visibility", "agent"), run_id="run", event_seq=sequence,
                      recorded_at="2026-01-05T00:00:00+00:00", wall_elapsed_ms=sequence,
                      state_version_before=sequence, state_version_after=sequence)
        self.events.append(event.model_dump(mode="json"))
        return self.events[-1]

    def errand(self, kind: str, when: str, snapshot: dict, **fields) -> dict:
        """一条事务事件（环境写出，决策与动作身份为空）：提醒与办成人物可见，其余系统可见；fields 另可给 caused_by、
        agent_id 与载荷的附加键（quantity_added、reason）。"""
        caused_by, agent_id = fields.pop("caused_by", ()), fields.pop("agent_id", 1)
        visibility = "agent" if kind in AGENT_VISIBLE_KINDS else "system"
        return self.add("environment_event", when, {"kind": kind, "errand": snapshot, **fields},
                        caused_by=caused_by, agent_id=agent_id, visibility=visibility)

    def observation(self, when: str, names: tuple[str, ...], agent_id: int = 1, decision_id: str = "observed") -> dict:
        """本人一次观察：待办层的事务行只给名称（指标只读名称）。"""
        rows = [{"name": name} for name in names]
        return self.add("observation", when, {"todo_layer": {"layer": "待办层", "paused_activities": [],
                                                             "errands": rows, "day_plan": None}},
                        agent_id=agent_id, decision_id=decision_id)

    def bought(self, when: str, product_id: str, **fields) -> dict:
        """一次购买的结果事件：顶层订单一个（status 缺省 delivered，收货人缺省本人）；event_type 缺省 action_result，
        reused 为真时是重放。"""
        agent_id, status = fields.get("agent_id", 1), fields.get("status", "delivered")
        order = {"order_id": f"order-{len(self.events) + 1}", "agent_id": agent_id, "product_id": product_id,
                 "quantity": 1, "status": status, "recipient_id": fields.get("recipient", f"agent:{agent_id}")}
        data = {"status": "completed", "data": {"orders": [order]}}
        if fields.get("reused"):
            data["reused"] = True
        return self.add(fields.get("event_type", "action_result"), when, data, agent_id=agent_id,
                        action_id=f"action-{len(self.events) + 1}")

    def accepted(self, when: str, data: dict, agent_id: int = 1) -> dict:
        """一次被受理的请求（action_accepted，带受理时的结果与活动快照）。"""
        return self.add("action_accepted", when, data, agent_id=agent_id, action_id=f"action-{len(self.events) + 1}")


def _snapshot(errand_id: str, name: str, product_id: str, registered: str, outcome: str = "obtain") -> dict:
    """记下时的事务快照（ErrandState 的 JSON 形状，数量 1、累计 0、没有备注与提醒）。"""
    return ErrandState(errand_id=errand_id, name=name, outcome=outcome, product_id=product_id, quantity=1,
                       progress=0.0, memo=None, registered_at=_at(registered), remind_at=None, reminded_at=None,
                       origin_action_id=f"action-{errand_id}", origin_event_id=f"request-{errand_id}").model_dump(
        mode="json")


def _done(snapshot: dict) -> dict:
    """办成事件的快照：累计量改为最终的累计量（数量）。"""
    return {**snapshot, "progress": float(snapshot["quantity"])}


def _consume(use_mode: str, items: tuple[str, ...] = (), output: str | None = None) -> dict:
    """消费活动的快照（ActivityState 的 JSON 形状里指标读到的部分）：请求的使用方式与各件商品，制作时的库存转换配方。"""
    details = {"request": {"use_mode": use_mode, "items": [{"product_id": item, "quantity": 1.0} for item in items]}}
    if output is not None:
        details["inventory_recipe"] = {"input_object_id": KIT, "input_quantity": "1", "output_object_id": output,
                                       "output_quantity": "1"}
    return {"activity_id": f"activity-{use_mode}", "kind": "consume", "status": "active", "data": details}


def _eaten(product_id: str) -> dict:
    """进食活动一次进度结算的 consumption：该商品本区间吃掉一份。"""
    return {"use_mode": "eat", "items": [{"product_id": product_id, "interval_quantity": 1.0}]}


def _report(log: _Log) -> dict:
    """同一批事件经 build_quality_report 得到的报告（运行窗口覆盖全部事件）。"""
    events = [Event.model_validate(row) for row in log.events]
    window = {"start": events[0].occurred_at - 60, "end": events[-1].occurred_at + 60, "timezone": _ZONE}
    return build_quality_report(events, [], {"run_id": "run", "source_group_id": "run", "window": window})


def test_counts_follow_their_definitions_and_the_identity_holds() -> None:
    """五条记下的事务：A 当天办成（证据是送达本人的订单），B 跨零点后取消，C 的办成恰在本地零点（不算跨夜），D 记下后
    到截止仍未完成（跨夜），E 零点后记下、到截止仍未完成（不跨夜）；另有一条导出里没有记下事件的事务办成，不进任何计数。
    办成 2、取消 1、截止时未完成 2，三者之和恒等于分母 5；更新与累计事件不影响计数。未完成期间本人的观察按事务各计
    一次（A 一次，D 与 E 同一次观察各一次），他人观察不计。截止恰在本地零点时仍未完成的算跨夜，早一秒不算。报告里的同名
    分组与直接计算逐值相同。"""
    log = _Log()
    a, b = _snapshot("a", "买牛奶", MILK, "2026-01-05 10:00"), _snapshot("b", "补食材包", KIT, "2026-01-05 22:00")
    c = _snapshot("c", "喝冰红茶", TEA, "2026-01-05 23:00", outcome="eat")
    d, e = _snapshot("d", "囤面包", BREAD, "2026-01-05 23:30"), _snapshot("e", "买果汁", JUICE, "2026-01-06 08:30")
    log.errand(REGISTERED_KIND, "2026-01-05 10:00", a)
    log.observation("2026-01-05 10:10", ("买牛奶",))
    log.errand(UPDATED_KIND, "2026-01-05 10:15", {**a, "memo": "下班路上买"})
    bought = log.bought("2026-01-05 10:30", MILK)
    log.errand(COMPLETED_KIND, "2026-01-05 10:30", _done(a), caused_by=[bought["event_id"]], quantity_added=1.0)
    log.errand(REGISTERED_KIND, "2026-01-05 22:00", b)
    log.errand(REGISTERED_KIND, "2026-01-05 23:00", c)
    log.errand(REGISTERED_KIND, "2026-01-05 23:30", d)
    eaten = log.add("activity_progress", "2026-01-06 00:00", {"interval_start": _at("2026-01-05 23:45"),
                                                              "consumption": _eaten(TEA)})
    log.errand(COMPLETED_KIND, "2026-01-06 00:00", _done(c), caused_by=[eaten["event_id"]], quantity_added=1.0)
    stray = _snapshot("x", "早不在了", ICED, "2026-01-04 10:00")
    gift = log.bought("2026-01-06 07:00", ICED)
    log.errand(PROGRESSED_KIND, "2026-01-06 07:00", stray, caused_by=[gift["event_id"]], quantity_added=0.5)
    log.errand(COMPLETED_KIND, "2026-01-06 07:00", _done(stray), caused_by=[gift["event_id"]], quantity_added=0.5)
    log.errand(CANCELLED_KIND, "2026-01-06 08:00", b, reason="不想买了")
    log.errand(REGISTERED_KIND, "2026-01-06 08:30", e)
    log.observation("2026-01-06 08:45", ("囤面包", "买果汁"))
    log.observation("2026-01-06 08:50", ("买牛奶", "补食材包", "囤面包", "买果汁"), agent_id=2)
    log.add("run_ended", "2026-01-06 09:00", {}, agent_id=None, visibility="system")

    metrics = errand_metrics(log.events, _ZONE)
    assert metrics == {"registered_count": 5, "completed_count": 2, "cancelled_count": 1, "open_at_cutoff_count": 2,
                       "carried_overnight_count": 2, "visible_observation_count": 3, "expected_observation_count": 3,
                       "reminded_errand_count": 0, "acted_after_reminder_count": 0,
                       "completed_without_evidence_count": 0}
    assert tuple(metrics) == _KEYS
    assert sum(metrics[key] for key in ("completed_count", "cancelled_count", "open_at_cutoff_count")) == 5
    assert _report(log)["errand_metrics"] == metrics
    for cutoff, carried in (("2026-01-06 00:00", 1), ("2026-01-05 23:59:59", 0)):
        late = _Log()
        late.errand(REGISTERED_KIND, "2026-01-05 23:00", _snapshot("f", "买面包", BREAD, "2026-01-05 23:00"))
        late.add("run_ended", cutoff, {}, agent_id=None, visibility="system")
        assert errand_metrics(late.events, _ZONE)["carried_overnight_count"] == carried


def test_the_denominator_is_registered_errands_not_goals() -> None:
    """同一天零点计划登记三条当日目标、修订又登记四条，而人物只记下两条事务（一条办成）：registered_count 是 2（有新增事件
    的不同事务数，不是目标数），截止时未完成 1；分组的键里没有任何当日目标指标的键名，事务计数也不写进
    day_plan_metrics。"""
    log = _Log()
    goals = [{"intent_kind": kind, "goal": f"{kind}一会儿", "registered_at": _at("2026-01-05 00:00"),
              "progressed_at": None, "progress_activity_id": None} for kind in ("休息", "用餐", "运动")]
    plan = {"day_end": _at("2026-01-06 00:00"), "goals": goals}
    log.add("environment_event", "2026-01-05 00:00", {"kind": "day_plan_generated", "day_plan": plan},
            visibility="system")
    revised = {**plan, "goals": [*goals, {**goals[0], "intent_kind": "工作", "goal": "写周报"}]}
    log.add("action_result", "2026-01-05 09:00", {"status": "completed", "data": {"day_plan": revised}},
            action_id="revise")
    milk = _snapshot("a", "买牛奶", MILK, "2026-01-05 10:00")
    log.errand(REGISTERED_KIND, "2026-01-05 10:00", milk)
    log.errand(REGISTERED_KIND, "2026-01-05 10:00", _snapshot("b", "补食材包", KIT, "2026-01-05 10:00"))
    bought = log.bought("2026-01-05 11:00", MILK)
    log.errand(COMPLETED_KIND, "2026-01-05 11:00", _done(milk), caused_by=[bought["event_id"]], quantity_added=1.0)

    report = _report(log)
    metrics = report["errand_metrics"]
    assert (metrics["registered_count"], metrics["completed_count"], metrics["open_at_cutoff_count"]) == (2, 1, 1)
    assert set(metrics) == set(_KEYS) and not set(metrics) & _GOAL_KEYS
    assert "errand" not in str(report["day_plan_metrics"])
    assert errand_metrics(log.events, _ZONE) == metrics


def test_visible_and_expected_observations_agree() -> None:
    """未完成期间（记下事件之后、终态事件之前）本人的每次观察都计入同期观察数，待办层有同名行的计入可见次数：照实呈现
    时二者相等；记下之前、终态之后的观察与他人的观察都不计。若某次观察的待办层漏了这条事务，同期观察数不变而可见次数
    少一，两项分开计量正是为了暴露这种不一致。"""
    def run(missing: bool) -> dict:
        """按顺序构造一条事务的未完成期与前后的观察；missing 时期间多一次漏列该事务的观察。"""
        log = _Log()
        milk = _snapshot("a", "买牛奶", MILK, "2026-01-05 10:00")
        log.observation("2026-01-05 09:50", ())
        log.errand(REGISTERED_KIND, "2026-01-05 10:00", milk)
        log.observation("2026-01-05 10:10", ("买牛奶",))
        log.observation("2026-01-05 10:20", ("买牛奶",))
        if missing:
            log.observation("2026-01-05 10:30", ())
        log.observation("2026-01-05 10:40", ("买牛奶",), agent_id=2)
        bought = log.bought("2026-01-05 11:00", MILK)
        log.errand(COMPLETED_KIND, "2026-01-05 11:00", _done(milk), caused_by=[bought["event_id"]], quantity_added=1.0)
        log.observation("2026-01-05 11:30", ("买牛奶",))
        return errand_metrics(log.events, _ZONE)

    faithful, defective = run(False), run(True)
    assert (faithful["visible_observation_count"], faithful["expected_observation_count"]) == (2, 2)
    assert (defective["visible_observation_count"], defective["expected_observation_count"]) == (2, 3)


def test_acted_after_reminder_needs_an_accepted_request_for_the_same_product() -> None:
    """八条被提醒过的事务里只有三条算提醒后行动：提醒后同一本地日、终态之前，本人被受理的请求指向同一商品——购买的顶层
    订单（在途的也算）、进食请求的商品（受理事件的活动快照）、制作配方的产出（并行开始的活动快照）。不算的：之后才写的
    动作结果与拒绝事件、重放的受理事件、指向别的商品的受理、跨过本地零点的受理、终态之后的受理、提醒之前的受理与他人的
    受理。没被提醒的事务不进这两项。"""
    log = _Log()
    names = {"e1": MILK, "e2": TEA, "e3": MEAL, "e4": KIT, "e5": ICED, "e6": BREAD, "e7": JUICE, "e8": "food-8",
             "e9": "food-9"}
    snapshots = {key: _snapshot(key, f"事务{key}", product, "2026-01-05 10:00",
                                outcome="eat" if key == "e2" else "obtain") for key, product in names.items()}
    for snapshot in snapshots.values():
        log.errand(REGISTERED_KIND, "2026-01-05 10:00", snapshot)
    log.accepted("2026-01-05 11:00", {"status": "completed", "data": {"orders": [
        {"product_id": JUICE, "status": "delivered"}]}})
    for key in ("e1", "e2", "e3", "e4", "e6", "e7", "e8"):
        log.errand(REMINDED_KIND, "2026-01-05 12:00", snapshots[key])
    log.errand(CANCELLED_KIND, "2026-01-05 12:05", snapshots["e6"], reason="不用了")
    log.bought("2026-01-05 12:10", MILK, status="pending", event_type="action_accepted")
    log.bought("2026-01-05 12:10", BREAD, event_type="action_accepted")
    log.bought("2026-01-05 12:10", "food-8", agent_id=2, event_type="action_accepted")
    log.accepted("2026-01-05 12:20", {"status": "in_progress", "activity": _consume("eat", (TEA,)),
                                      "data": {"activity": _consume("eat", (TEA,))}})
    log.accepted("2026-01-05 12:20", {"status": "in_progress", "data": {"activities": [
        _consume("use", (KIT,), output=MEAL), _consume("eat", (MILK,))]}})
    log.bought("2026-01-05 12:30", KIT)
    log.add("action_rejected", "2026-01-05 12:30", {"error_code": "OUT_OF_STOCK", "data": {"orders": [
        {"product_id": KIT, "status": "pending"}]}}, action_id="rejected")
    log.bought("2026-01-05 12:40", KIT, event_type="action_accepted", reused=True)
    log.errand(REMINDED_KIND, "2026-01-05 23:50", snapshots["e5"])
    log.bought("2026-01-06 00:10", ICED, event_type="action_accepted")
    log.bought("2026-01-06 00:20", "food-9", event_type="action_accepted")

    metrics = errand_metrics(log.events, _ZONE)
    assert (metrics["reminded_errand_count"], metrics["acted_after_reminder_count"]) == (8, 3)


def test_a_completion_without_checkable_evidence_is_counted() -> None:
    """办成事件须引用本导出里更早的一条事件，且该事件经结算同一读取（errands.evidence_rows）给出同一所有人、结果种类与
    商品的证据行：送达本人的订单结果与配送送达的入库行可复核；没有原因、原因不在导出里、原因的证据归别人（收货人是李凡）、
    原因排在办成之后、原因的进食是别的商品，这五条都计入 completed_without_evidence_count。"""
    log = _Log()
    cases = {key: _snapshot(key, f"事务{key}", product, "2026-01-05 10:00", outcome="eat" if key == "c6" else "obtain")
             for key, product in (("c1", MILK), ("c2", KIT), ("c3", TEA), ("c4", ICED), ("c5", BREAD), ("c6", JUICE),
                                  ("c7", MEAL))}
    for snapshot in cases.values():
        log.errand(REGISTERED_KIND, "2026-01-05 10:00", snapshot)

    def done(key: str, *causes: str) -> None:
        """写下 key 的办成事件，以 causes 为原因。"""
        log.errand(COMPLETED_KIND, "2026-01-05 11:00", _done(cases[key]), caused_by=list(causes), quantity_added=1.0)

    done("c1", log.bought("2026-01-05 11:00", MILK)["event_id"])
    done("c2")
    done("c3", "event-not-exported")
    done("c4", log.bought("2026-01-05 11:00", ICED, recipient="agent:2")["event_id"])
    done("c5", f"event-{len(log.events) + 2}")
    log.bought("2026-01-05 11:00", BREAD)
    eaten = log.add("activity_progress", "2026-01-05 11:00", {"interval_start": _at("2026-01-05 10:30"),
                                                              "consumption": _eaten(TEA)})
    done("c6", eaten["event_id"])
    arrival = log.add("environment_event", "2026-01-05 11:00", {
        "kind": "delivery_arrival", "orders": [{"order_id": "order-meal", "agent_id": 2, "recipient_id": "agent:1"}],
        "inventory_changes": [{"order_id": "order-meal", "product_id": MEAL, "quantity": 1}]}, agent_id=2)
    done("c7", arrival["event_id"])

    metrics = errand_metrics(log.events, _ZONE)
    assert (metrics["completed_count"], metrics["completed_without_evidence_count"]) == (7, 5)


def test_no_errand_or_no_conclusion_memory_gives_null() -> None:
    """导出里没有任何 errand_registered（只有一条记下不在导出里的事务的提醒）时 errand_metrics 为 null；收尾请求只写看法
    与偏好、重放的收尾与播报都不算收尾记忆时 conclusion_memories 为 null，报告里两组都是 null。有一条记下的事务而别无
    命中时各计数为 0、不为 null。"""
    log = _Log()
    log.observation("2026-01-05 10:00", ("买牛奶",), decision_id="decision-1")
    log.errand(REMINDED_KIND, "2026-01-05 10:05", _snapshot("a", "买牛奶", MILK, "2026-01-04 10:00"))
    view = {"type": "relationship", "about": "王磊", "text": "王磊做事总拖。", "importance": 0.5, "keywords": ["同事"]}
    memory = {"type": "routine", "text": "每晚十点前到家。", "importance": 0.3, "keywords": ["回家"]}
    for reused, memories in ((False, []), (True, [memory])):
        request = {"items": [{"activity_name": "等待", "summary": "等完了。", "memories": memories, "views": [view]}],
                   "errands": []}
        data = {"tool_name": "conclude_activity", "reused": reused, "request": request}
        log.add("action_requested", "2026-01-05 10:10", data, decision_id="decision-1", action_id="conclude")
    log.add("action_requested", "2026-01-05 10:20", {"tool_name": "report_status", "request": {
        "items": [{"activity_name": "等待", "summary": "还在等。"}], "errands": []}}, decision_id="decision-2")

    assert errand_metrics(log.events, _ZONE) is None and conclusion_memories(log.events) is None
    report = _report(log)
    assert (report["errand_metrics"], report["conclusion_memories"]) == (None, None)
    log.errand(REGISTERED_KIND, "2026-01-05 10:30", _snapshot("b", "补食材包", KIT, "2026-01-05 10:30"))
    assert errand_metrics(log.events, _ZONE) == {key: 1 if key in {"registered_count", "open_at_cutoff_count"} else 0
                                                 for key in _KEYS}


def test_conclusion_memories_count_routine_and_errand_overlap() -> None:
    """收尾记忆是非重放 conclude_activity 请求里 items[].memories 的条目（不论之后是否被受理），看法与偏好不计：六条里作息
    两条；文字包含同一决策观察里未完成事务的名称（待办层）或其商品名称（私有绑定）的两条——商品名只认未完成事务的商品，
    同一观察里别的商品名不算，另一决策的观察里没有事务、没有观察的决策也都不算重合。"""
    log = _Log()
    bindings = [{"name": "补一份食材包", "errand_id": "a", "outcome": "obtain", "product_id": KIT},
                {"name": "买冰红茶", "errand_id": "b", "outcome": "obtain", "product_id": TEA}]
    items = [{"name": "素炒什锦食材包", "product_id": KIT}, {"name": "康师傅 冰红茶", "product_id": TEA},
             {"name": "纯牛奶", "product_id": MILK}]
    first = log.observation("2026-01-05 21:00", ("补一份食材包", "买冰红茶"), decision_id="decision-1")
    first["data"].update(errand_bindings=bindings, errand_item_bindings=items)
    log.observation("2026-01-05 21:00", (), decision_id="decision-2")

    def memo(kind: str, text: str) -> dict:
        """一条收尾记忆（请求形状）。"""
        return {"type": kind, "text": text, "importance": 0.4, "keywords": ["生活"]}

    def concluded(decision_id: str, *groups: list[dict], reused: bool = False) -> dict:
        """一次收尾请求：每组记忆对应一项活动，每项另有一条提到事务名的看法。"""
        view = {"type": "preference", "about": "超市", "text": "补一份食材包还是去超市好。", "importance": 0.5,
                "keywords": ["超市"]}
        request = {"items": [{"activity_name": f"等待{index}", "summary": "收尾。", "memories": list(group),
                              "views": [view]} for index, group in enumerate(groups)], "errands": []}
        return log.add("action_requested", "2026-01-05 21:05", {"tool_name": "conclude_activity", "reused": reused,
                                                                "request": request}, decision_id=decision_id)

    concluded("decision-1", [memo("routine", "每晚十点前到家。"), memo("reflection", "家里的素炒什锦食材包又见底了。"),
                             memo("goal", "周末记得买冰红茶。")], [memo("routine", "喝纯牛奶前先热一下。")])
    concluded("decision-1", [memo("goal", "补一份食材包。")], reused=True)
    concluded("decision-2", [memo("goal", "明天也记得买冰红茶。")])
    log.add("action_rejected", "2026-01-05 21:05", {"error_code": "ACTIVITY_CONCLUSION_REQUIRED"},
            decision_id="decision-2")
    concluded("decision-3", [memo("identity", "我是会自己补食材包的人。")])

    expected = {"total_count": 6, "routine_count": 2, "errand_overlap_count": 2}
    assert conclusion_memories(log.events) == expected
    assert _report(log)["conclusion_memories"] == expected
