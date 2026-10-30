"""试点指标单元测试的最小导出形状。

PilotExportBuilder 在临时目录写出 tools.analysis.memory_pilot 读的五样：manifest.json（带 frozen_run_manifest 的
agent_ids、environment_assets、stage_model_identity.max_react_turns 与 window）、events.jsonl、purchases.jsonl、
quality.json（带 model_usage）与 assets/，并按需追加决策、观察、请求、响应、世界请求、回顾事件与目录行。活动进入终态时
同一提交里另写给开始它的世界请求的第二条 action_result（src/worldmock/simulation_engine/evidence/actions.py 的
_activity_terminal_draft）；时钟推进里结束的活动在它之前有进入终态的 activity_progress，收尾结束（取消）的进行中活动在它
之前是带取消快照的 activity_concluded 环境事件、没有 activity_progress；时钟推进里已结束、待收尾的活动在后来的收尾提交里
另有一条带同一终态快照的 activity_concluded，不再有终态结果。收尾的这两种写法与真实提交相同
（src/worldmock/simulation_engine/activities/lifecycle/activity_control.py 的 record_changes）。最小形状通不过真实认证，
所以单元测试用 skip_authentication 把 validate_export 换成只记录所收目录的替身；真实认证由集成测试覆盖。
"""

from __future__ import annotations

import copy
import hashlib
import json
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

#: 测试目录的商品：四件食品、两份食材包与两件日用品（洗衣液把「laundry」置满）。
GOODS = (
    {"category": "meal", "goods_id": "noodle", "name": "番茄牛肉刀削面", "price_fen": 2800},
    {"category": "meal", "goods_id": "beef-rice", "name": "土豆牛肉饭", "price_fen": 2600},
    {"category": "drink", "goods_id": "coffee", "name": "美式咖啡", "price_fen": 1830},
    {"category": "snack", "goods_id": "chips", "name": "薯片", "price_fen": 800},
    {"category": "goods", "goods_id": "kit-chestnut", "name": "板栗烧鸡配清炒娃娃菜一人份食材包", "price_fen": 3900},
    {"category": "goods", "goods_id": "kit-wings", "name": "可乐鸡翅配清炒菌菇一人份食材包", "price_fen": 3600},
    {"category": "goods", "goods_id": "tissue", "name": "纸巾", "price_fen": 600},
    {"category": "goods", "goods_id": "detergent", "name": "洗衣液", "price_fen": 2500},
)
#: 测试目录的内容：凭食材的做饭系列两集、公司午饭、一部 app 剧、公园散步、凭「pantry」的吃零食（食品内容）与凭
#: 「laundry」的洗衣服（不是食品内容）。
CONTENTS = (
    {"completion_effects": {"food": "meal"}, "consumes_state": "groceries", "content_id": "cook-1",
     "location_kinds": ["home"], "series": {"episode": 1, "title": "做晚饭"},
     "summary": "用买来的食材做一顿饭，比如番茄炒蛋、清炒上海青，吃完收拾。", "title": "备菜做饭"},
    {"consumes_state": "groceries", "content_id": "cook-2", "location_kinds": ["home"],
     "series": {"episode": 2, "title": "做晚饭"}, "summary": "吃饭。", "title": "吃饭"},
    {"content_id": "office-lunch", "location_kinds": ["office"], "summary": "在公司吃午饭，例如盒饭和面条。",
     "title": "公司吃午饭"},
    {"app_ids": ["bilibili"], "content_id": "drama-1", "series": {"episode": 1, "title": "测试剧集"},
     "summary": "测试剧集第一集。", "title": "测试剧集第一集"},
    {"content_id": "walk", "location_kinds": ["park"], "summary": "绕着公园走一圈。", "title": "公园散步"},
    {"completion_effects": {"food": "snack"}, "consumes_state": "pantry", "content_id": "pantry-snack",
     "location_kinds": ["home"], "summary": "从柜子里拿点零食吃。", "title": "吃零食"},
    {"consumes_state": "laundry", "content_id": "laundry", "location_kinds": ["home"], "summary": "用洗衣液洗一桶衣服。",
     "title": "洗衣服"},
)
#: 测试目录的回调状态：两份食材包都把「groceries」置满（食品状态，凭食材包）；「pantry」是没有重置商品的食品状态；
#: 「laundry」由洗衣液置满，只有不带饮食效果的内容凭它，不是食品状态。
CALLBACKS = ({"cap": 1, "initial": 0, "reset_on_goods": ["kit-chestnut", "kit-wings"], "state": "groceries"},
             {"cap": 3, "initial": 3, "reset_on_goods": [], "state": "pantry"},
             {"cap": 1, "initial": 0, "reset_on_goods": ["detergent"], "state": "laundry"})
#: 七个调用类。
CALL_CLASSES = ("fresh_decision_turn", "turn_with_history", "truncation_retry", "consolidation", "reflection",
                "plan_day", "memory_digest")
#: 冻结的峰时与非峰时价（每百万 token 美元）。
PRICING = {"off_peak": {"cache_hit_input": 0.003, "cache_miss_input": 0.15, "output": 0.6},
           "peak": {"cache_hit_input": 0.006, "cache_miss_input": 0.3, "output": 1.2}}
#: 「近三天」三类为空的记忆层。
EMPTY_RECENT = {"饮食": [], "工作": [], "生活": []}
#: 活动终态 → 给开始它的世界请求写的终态结果状态（截断记为取消）。
TERMINAL_RESULTS = {"completed": "completed", "cancelled": "cancelled", "failed": "failed", "truncated": "cancelled"}


def compact(value: Any) -> str:
    """紧凑 JSON，与提示层同一写法。"""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def memory_layer(today=(), recent=None, earliest="2026-10-08") -> dict:
    """最终形状的记忆层：layer、可查、近三天、今天。"""
    return {"layer": "记忆层", "可查": {"最早日期": earliest}, "近三天": recent or dict(EMPTY_RECENT),
            "今天": list(today)}


def entry(clock: str, category: str, checked=(), notes=()) -> dict:
    """一条今天条目；没有的核验或自述不写。"""
    item = {"时刻": clock, "类别": category}
    if checked:
        item["核验"] = list(checked)
    if notes:
        item["自述"] = list(notes)
    return item


def cell(day: str, text: str | None = None, unsorted: bool = False) -> dict:
    """「近三天」的一格：有回顾或未整理。"""
    if unsorted:
        return {"日期": day, "星期": "星期一", "回顾": None, "未整理": True}
    return {"日期": day, "星期": "星期一", "回顾": text}


def activity(activity_id: str, kind: str, *, name: str = "", status: str = "active",
             destination_id: str | None = None, **data: Any) -> dict:
    """活动快照：身份、种类、状态、名称、目的地与 data。"""
    return {"activity_id": activity_id, "kind": kind, "status": status, "name": name,
            "destination_id": destination_id, "data": dict(data)}


def search_call(call_id: str, arguments: dict) -> dict:
    """一次 search_memory 工具调用。"""
    return {"id": call_id, "type": "function",
            "function": {"name": "search_memory", "arguments": json.dumps(arguments, ensure_ascii=False)}}


def tool_call(call_id: str, name: str, arguments: dict) -> dict:
    """一次世界请求工具调用。"""
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}


class PilotExportBuilder:
    """在临时目录写出试点指标读的最小导出；事件按追加次序编号，提交内的事件共用一次版本加一。"""

    def __init__(self, root: Path, *, days: int = 3, start: str = "2026-10-08", agents: int = 1,
                 max_react_turns: int = 16, timezone: str = "Asia/Shanghai", agent_id: int = 1) -> None:
        self.root = Path(root) / "export"
        self.days = tuple(date.fromisoformat(start) + timedelta(days=index) for index in range(days))
        self.zone = ZoneInfo(timezone)
        self.agent_ids = list(range(agent_id, agent_id + agents))
        self.max_react_turns = max_react_turns
        #: 清单 window.end 的 epoch 秒；None 时取最后一个运行日的 23:00（daily_coverage 始终是这几天）。
        self.window_end: int | None = None
        self.events: list[dict] = []
        self.purchases: list[dict] = []
        self.call_classes = {name: {"call_class": name, "request_events": 0, "priced_tokens": None}
                             for name in CALL_CLASSES}
        self.complete = True
        self.catalog = copy.deepcopy({"catalog/merchant-catalog.jsonl": list(GOODS),
                                      "catalog/content-catalog.jsonl": list(CONTENTS),
                                      "catalog/app-catalog.jsonl": [{"app_id": "bilibili", "name": "哔哩哔哩"}],
                                      "catalog/location.jsonl": [{"location_id": "home", "name": "家"}],
                                      "catalog/callbacks.jsonl": list(CALLBACKS)})
        self.omitted: set[str] = set()
        self.version = 0
        self._committing = False
        self._turns: dict[str, int] = {}
        self._orders = 0
        #: 活动身份 → （开始它的 action_requested，受理快照）；已写过终态结果的活动 → 终态快照；已收尾的活动另记。
        self._starts: dict[str, tuple[dict, dict]] = {}
        self._ended: dict[str, dict] = {}
        self._wrapped: set[str] = set()

    # ---- 时间与事件 ----

    def at(self, day: int, clock: str) -> int:
        """第 day 个运行日（从 1 起）的本地时刻 HH:MM 或 HH:MM:SS 的 epoch 秒。"""
        parts = [int(part) for part in clock.split(":")]
        moment = datetime.combine(self.days[day - 1], datetime.min.time(), self.zone).replace(
            hour=parts[0], minute=parts[1], second=parts[2] if len(parts) > 2 else 0)
        return int(moment.timestamp())

    def iso(self, day: int) -> str:
        """第 day 个运行日的 ISO 日期。"""
        return self.days[day - 1].isoformat()

    def add(self, event_type: str, occurred_at: int, data: dict | None = None, *, decision_id: str | None = None,
            action_id: str | None = None, activity_id: str | None = None, caused_by=()) -> dict:
        """追加一条事件；在 commit() 里的事件版本为 (v, v+1)，否则为 (v, v)。"""
        seq = len(self.events) + 1
        after = self.version + 1 if self._committing else self.version
        event = {"event_id": f"evt-{seq:05d}", "event_seq": seq, "event_type": event_type,
                 "occurred_at": occurred_at, "agent_id": self.agent_ids[0], "decision_id": decision_id,
                 "action_id": action_id, "activity_id": activity_id, "caused_by": list(caused_by),
                 "data": data or {}, "run_id": "run-pilot", "state_version_before": self.version,
                 "state_version_after": after, "visibility": "agent", "source_kind": "environment"}
        self.events.append(event)
        return event

    @contextmanager
    def commit(self):
        """一次版本加一的提交：其中追加的事件共用前后版本；已在提交里时并进这一次。"""
        if self._committing:
            yield
            return
        self._committing = True
        try:
            yield
        finally:
            self._committing = False
            self.version += 1

    # ---- 决策 ----

    def decision(self, day: int, clock: str, *, memory: dict | None = None, environment: dict | None = None,
                 progress: dict | None = None, decision_id: str | None = None) -> str:
        """一次决策的开头：decision_started 与它的观察。"""
        decision_id = decision_id or f"decision-{len(self._turns) + 1:03d}"
        self._turns[decision_id] = 0
        moment = self.at(day, clock)
        self.add("decision_started", moment, {"round_id": decision_id, "round_order": 0,
                                              "world_state_version": self.version, "wake_reasons": []},
                 decision_id=decision_id)
        self.observation(moment, decision_id=decision_id, memory=memory, environment=environment,
                         progress=progress)
        return decision_id

    def observation(self, at: int, *, decision_id: str | None = None, memory: dict | None = None,
                    environment: dict | None = None, progress: dict | None = None) -> dict:
        """一条观察：记忆层、环境层与进度层。"""
        data = {"memory_layer": memory if memory is not None else memory_layer(),
                "environment_layer": environment if environment is not None else {"layer": "环境层", "duty": None},
                "progress_layer": progress if progress is not None else {"layer": "进度层", "events": []}}
        return self.add("observation", at, data, decision_id=decision_id)

    def request(self, decision_id: str, at: int, layers=None, *, history=(), turn: int | None = None,
                attempt: int = 1, system: str = "生活系统层", tools=None) -> dict:
        """一条回合请求：system、user（层数组）与续接历史；call_id 带回合号。"""
        if turn is None:
            self._turns[decision_id] = self._turns.get(decision_id, 0) + 1
            turn = self._turns[decision_id]
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": compact(list(layers or [memory_layer()]))}, *history]
        body = {"messages": messages, "tools": tools if tools is not None else [{"type": "function",
                "function": {"name": "start_main_activity"}}]}
        return self.add("model_request", at, {"phase": "request", "call_id":
                        f"{decision_id}:model:{turn:04d}:attempt:{attempt}", "attempt": attempt, "request": body},
                        decision_id=decision_id)

    def response(self, request: dict, *, reasoning: str | None = None, content: str | None = None,
                 tool_calls=(), prompt_tokens: int | None = None, error: str | None = None,
                 phase: str = "response") -> dict:
        """请求的响应：choices[0].message 带思考、正文与工具调用。"""
        message: dict[str, Any] = {"role": "assistant", "content": content}
        if reasoning is not None:
            message["reasoning_content"] = reasoning
        if tool_calls:
            message["tool_calls"] = list(tool_calls)
        body = {"choices": [{"message": message}], "usage": {"prompt_tokens": prompt_tokens}}
        return self.add("model_response", request["occurred_at"],
                        {"phase": phase, "call_id": request["data"]["call_id"], "request": None,
                         "response": body if phase == "response" else None, "error": error},
                        decision_id=request["decision_id"], caused_by=[request["event_id"]])

    def turn(self, decision_id: str, at: int, *, layers=None, history=(), **reply: Any) -> tuple[dict, dict]:
        """一个回合：请求与它的响应。"""
        request = self.request(decision_id, at, layers, history=history)
        return request, self.response(request, **reply)

    def search(self, decision_id: str, at: int, arguments: dict, answer: Any = None, *, call_id: str = "call-s",
               error: str | None = None, reasoning: str | None = None, answered: bool = True) -> dict:
        """一次检索：带 search_memory 调用的回合，回答在下一条回合请求的 tool 消息里（answered 为假时没有下一条）。"""
        call = search_call(call_id, arguments)
        _, response = self.turn(decision_id, at, reasoning=reasoning, tool_calls=[call], error=error)
        if answered:
            history = [{"role": "assistant", "content": None, "tool_calls": [call]},
                       {"role": "tool", "tool_call_id": call_id, "content": compact(answer)}]
            self.request(decision_id, at, history=history)
        return response

    def action(self, decision_id: str, at: int, tool_name: str, request: dict | None = None) -> dict:
        """一条世界请求 action_requested。"""
        action_id = f"action-{len(self.events) + 1:05d}"
        return self.add("action_requested", at, {"tool_name": tool_name, "request": request or {}},
                        decision_id=decision_id, action_id=action_id)

    def world(self, decision_id: str, at: int, tool_name: str, request: dict | None = None, *,
              activities=(), status: str = "completed", activity_ids=None) -> dict:
        """一条世界请求与它的提交：每项受理活动一条 action_accepted，再一条 action_result；结果为 completed 的收尾
        其后给每项所收的活动写 activity_concluded（_concluded）。"""
        action = self.action(decision_id, at, tool_name, request)
        with self.commit():
            for snapshot in activities:
                self.add("action_accepted", at, {"status": "accepted", "activity": snapshot},
                         decision_id=decision_id, action_id=action["action_id"],
                         activity_id=snapshot["activity_id"], caused_by=[action["event_id"]])
                self._starts[snapshot["activity_id"]] = (action, snapshot)
            self.add("action_result", at, {"status": status, "data": {"activity_ids": list(activity_ids or [])}},
                     decision_id=decision_id, action_id=action["action_id"], caused_by=[action["event_id"]])
            if tool_name == "conclude_activity" and status == "completed":
                for activity_id in activity_ids or []:
                    self._concluded(action, activity_id, at)
        return action

    def _concluded(self, action: dict, activity_id: str, at: int) -> None:
        """收尾一项开始过、还没收尾的活动：一条 activity_concluded 环境事件（决策、动作与原因都是这次收尾）。进行中的活动
        由此取消，事件带取消快照，再给开始它的请求写终态结果，原因另链这次收尾；已结束、待收尾（completed、failed、
        cancelled）的活动终态不变，事件带它的终态快照，不再写终态结果。都不写 activity_progress。"""
        if activity_id not in self._starts or activity_id in self._wrapped:
            return
        ended = self._ended.get(activity_id)
        if ended is not None and ended["status"] not in {"completed", "failed", "cancelled"}:
            return
        self._wrapped.add(activity_id)
        final = ended or {**self._starts[activity_id][1], "status": "cancelled"}
        self.add("environment_event", at, {"kind": "activity_concluded", "activity_id": activity_id, "activity": final},
                 decision_id=action["decision_id"], action_id=action["action_id"], activity_id=activity_id,
                 caused_by=[action["event_id"]])
        self._terminal(activity_id, "cancelled", at, [action["event_id"]])

    def _terminal(self, activity_id: str, status: str, at: int, causes=()) -> None:
        """开始过、还没终结的活动进入终态：给开始它的请求写第二条 action_result（带原决策身份与活动快照）。"""
        if activity_id not in self._starts or activity_id in self._ended:
            return
        start, snapshot = self._starts[activity_id]
        final = {**snapshot, "status": status}
        self._ended[activity_id] = final
        self.add("action_result", at, {"status": TERMINAL_RESULTS[status], "activity": final,
                                       "data": {"activity": final, "orders": []}},
                 decision_id=start["decision_id"], action_id=start["action_id"], activity_id=activity_id,
                 caused_by=[start["event_id"], *causes])

    def reject(self, decision_id: str, at: int, tool_name: str = "start_main_activity") -> dict:
        """一条被拒的世界请求。"""
        action = self.action(decision_id, at, tool_name)
        with self.commit():
            return self.add("action_rejected", at, {"error_code": "SUPPLY_NOT_FOUND"}, decision_id=decision_id,
                            action_id=action["action_id"], caused_by=[action["event_id"]])

    def progress(self, snapshot: dict, at: int, status: str = "completed", causes=()) -> dict:
        """一次提交里的活动进度事件，快照换成给定状态；开始过的活动带开始它的决策与请求，进入终态时同一提交里再写
        终态结果（causes 是终态结果另链的原因）。"""
        start = self._starts[snapshot["activity_id"]][0] if snapshot["activity_id"] in self._starts else None
        origin = {} if start is None else {"decision_id": start["decision_id"], "action_id": start["action_id"],
                                           "caused_by": [start["event_id"]]}
        with self.commit():
            event = self.add("activity_progress", at, {"activity": {**snapshot, "status": status}},
                             activity_id=snapshot["activity_id"], **origin)
            if status in TERMINAL_RESULTS:
                self._terminal(snapshot["activity_id"], status, at, causes)
        return event

    def environment(self, kind: str, at: int, caused_by=(), **data: Any) -> dict:
        """一条环境事件。"""
        return self.add("environment_event", at, {"kind": kind, **data}, caused_by=caused_by)

    def received(self, goods_id: str, at: int) -> dict:
        """一次本人收货 goods_received。"""
        category = next(row["category"] for row in self.catalog["catalog/merchant-catalog.jsonl"]
                        if row["goods_id"] == goods_id)
        return self.environment("goods_received", at, goods_id=goods_id, category=category, quantity=1)

    def kit_arrives(self, goods_id: str, at: int, state: str = "groceries") -> dict:
        """重置商品到手：收货与把 state（默认 groceries）置满的 reset_to_cap。"""
        receipt = self.received(goods_id, at)
        return self.environment("callback_effect", at, caused_by=[receipt["event_id"]], type="reset_to_cap",
                                rule_id=None, state=state, goods_id=goods_id, before=0, after=1)

    def cook(self, decision_id: str, at: int, activity_id: str, content_id: str = "cook-1",
             state: str = "groceries") -> dict:
        """开始凭状态的内容（默认在家做饭）：受理活动，同一提交里写 state 的 consume_on_start；返回活动快照。"""
        snapshot = activity(activity_id, "interact", name=f"{activity_id}做饭", action="interact",
                            content_id=content_id)
        action = self.world(decision_id, at, "start_main_activity", activities=[snapshot])
        accepted = next(event for event in self.events if event["event_type"] == "action_accepted"
                        and event["caused_by"] == [action["event_id"]])
        self.environment("callback_effect", at, caused_by=[accepted["event_id"]], type="consume_on_start",
                         rule_id=None, state=state, content_id=content_id, before=1, after=0)
        return snapshot

    def conclude(self, decision_id: str, at: int, items: list[dict], activity_ids: list[str],
                 status: str = "completed") -> dict:
        """一次收尾：items 带 summary、views、memories，结果的 data.data.activity_ids 与它们一一对应。"""
        return self.world(decision_id, at, "conclude_activity", {"items": items}, status=status,
                          activity_ids=activity_ids)

    # ---- 回顾 ----

    def digest(self, tier: str, period: str, at: int, texts: dict | None = None, fallback=()) -> dict:
        """一条回顾登记事件：texts 是类别键 → 正文，未给的类为 null。"""
        digests = {key: ({"text": texts[key], "source_event_ids": ["evt-00001"], "cited_event_ids": ["evt-00001"]}
                         if (texts or {}).get(key) is not None else None) for key in ("food", "work", "life")}
        event = self.environment("memory_digest_generated", at, tier=tier, period=period, digests=digests,
                                 fallback=list(fallback))
        event.update(visibility="system", source_kind="model")
        return event

    def digest_request(self, tier: str, period: str, at: int, payload: dict, attempt: int = 1) -> dict:
        """一次回顾请求：用户消息是按类别分开的输入。"""
        call_id = f"memory-digest:{self.agent_ids[0]}:{tier}:{period}:attempt:{attempt}"
        body = {"messages": [{"role": "system", "content": "回顾提示"}, {"role": "user", "content": compact(payload)}]}
        return self.add("model_request", at, {"phase": "request", "purpose": "memory_digest", "call_id": call_id,
                                              "attempt": attempt, "request": body, "response": None, "error": None})

    def digest_response(self, request: dict, error: str | None = None, phase: str = "response") -> dict:
        """一次回顾响应或传输失败证据。"""
        return self.add("model_response", request["occurred_at"],
                        {"phase": phase, "purpose": "memory_digest", "call_id": request["data"]["call_id"],
                         "attempt": request["data"]["attempt"], "request": None, "response": None, "error": error},
                        caused_by=[request["event_id"]])

    # ---- 购买、质量报告与目录 ----

    def purchase(self, goods_id: str, at: int, quantity: int = 1, received_at: int | None = None) -> str:
        """purchases.jsonl 的一行购买；给了 received_at 时另写这张订单的收货行（带商品、类别与份数，没有 created_at）。
        返回订单身份。"""
        self._orders += 1
        order_id = f"order-{self._orders:03d}"
        category = next(row["category"] for row in self.catalog["catalog/merchant-catalog.jsonl"]
                        if row["goods_id"] == goods_id)
        self.purchases.append({"fact_kind": "purchase", "agent_id": self.agent_ids[0], "order_id": order_id,
                               "goods_id": goods_id, "category": category, "quantity": quantity,
                               "created_at": at})
        if received_at is not None:
            self.purchases.append({"fact_kind": "receipt", "agent_id": self.agent_ids[0], "order_id": order_id,
                                   "received_at": received_at, "goods_id": goods_id, "category": category,
                                   "quantity": quantity, "provided": [], "applied": []})
        return order_id

    def order_attempt(self, at: int, status: str = "rejected") -> None:
        """purchases.jsonl 的一行下单尝试（没有商品、订单与 created_at）。"""
        self.purchases.append({"fact_kind": "order_attempt", "agent_id": self.agent_ids[0],
                               "action_id": f"action-attempt-{len(self.purchases) + 1:03d}", "action": "purchase",
                               "status": status, "error_code": None, "occurred_at": at})

    def usage(self, call_class: str, *, hit: int, miss: int, out: int = 0, request_events: int = 0) -> None:
        """一个调用类的可计价 token。"""
        self.call_classes[call_class] = {
            "call_class": call_class, "request_events": request_events,
            "priced_tokens": {"cache_hit_input_tokens": hit, "cache_miss_input_tokens": miss, "output_tokens": out}}

    def build(self) -> Path:
        """写出导出目录并返回它。"""
        self.root.mkdir(parents=True)
        (self.root / "assets").mkdir()
        assets = {}
        for name, rows in self.catalog.items():
            if name in self.omitted:
                continue
            raw = "".join(compact(row) + "\n" for row in rows).encode("utf-8")
            digest = hashlib.sha256(raw).hexdigest()
            (self.root / "assets" / digest).write_bytes(raw)
            assets[name] = {"sha256": digest, "size_bytes": len(raw), "media_type": "text/plain",
                            "source": {"kind": "environment_input", "path": name}}
        start = datetime.combine(self.days[0], datetime.min.time(), self.zone)
        end = (datetime.combine(self.days[-1], datetime.min.time(), self.zone) + timedelta(hours=23)
               if self.window_end is None else datetime.fromtimestamp(self.window_end, self.zone))
        window = {"start": start.isoformat(), "end": end.isoformat(), "timezone": self.zone.key}
        manifest = {"run_id": "run-pilot", "window": window, "daily_coverage": [day.isoformat() for day in self.days],
                    "frozen_run_manifest": {"run_id": "run-pilot", "agent_ids": self.agent_ids,
                                            "environment_assets": assets,
                                            "stage_model_identity": {"max_react_turns": self.max_react_turns}}}
        quality = {"schema_version": 10, "model_usage": {
            "by_call_class": list(self.call_classes.values()), "pricing_snapshot": PRICING,
            "estimated_cost": {"complete": self.complete}}}
        (self.root / "manifest.json").write_text(compact(manifest), encoding="utf-8")
        (self.root / "events.jsonl").write_text("".join(compact(event) + "\n" for event in self.events),
                                                encoding="utf-8")
        (self.root / "purchases.jsonl").write_text("".join(compact(row) + "\n" for row in self.purchases),
                                                   encoding="utf-8")
        (self.root / "quality.json").write_text(compact(quality), encoding="utf-8")
        return self.root


@pytest.fixture
def skip_authentication(monkeypatch) -> list[Path]:
    """把 memory_pilot 的 validate_export 换成只记录所收目录的替身（最小形状通不过真实认证）。"""
    seen: list[Path] = []
    monkeypatch.setattr("tools.analysis.memory_pilot.validate_export", lambda directory: seen.append(Path(directory)))
    return seen
