"""@brief 使用成熟规则引擎执行独立预期，并在支持范围内检查因果约束。"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import rule_engine

from worldmock.errors import IntegrityError


def evaluate_causal_events(events: list[dict], support: dict) -> list[dict]:
    """@brief 按声明支持字段检查事件身份、时钟、直接原因和资源结算。"""
    fields = set(support["fields"])
    findings = []
    seen = {}
    for event in events:
        identity = event["source_event_id"]
        if identity in seen:
            findings.append(_finding("duplicate_event", event, "Event identity occurs more than once"))
        if "occurred_at" in fields:
            _check_times(event, findings)
        if "caused_by" in fields:
            _check_causes(event, seen, findings)
        if {"state_version_before", "state_version_after"} <= fields:
            _check_versions(event, findings)
        if "resource_changes" in fields:
            _check_resources(event, findings)
        seen[identity] = event
    return findings


def _finding(code: str, event: dict, message: str) -> dict:
    """@brief 给出可回查原事件的确定性硬错误。"""
    return {"code": code, "severity": "hard_error", "event_ids": [event["source_event_id"]],
            "source_pointer": event["source_pointer"], "message": message}


def _check_times(event: dict, findings: list[dict]) -> None:
    """@brief 对真实起止时间执行非负时长规则，不把缺失终点推测成活动。"""
    start = datetime.fromisoformat(event["occurred_at"])
    end = event["ended_at"]
    if end is not None and datetime.fromisoformat(end) < start:
        findings.append(_finding("negative_duration", event, "Event ends before it starts"))


def _check_causes(event: dict, seen: dict, findings: list[dict]) -> None:
    """@brief 原因必须存在于当前事件之前，且不能来自未来。"""
    for identity in event["caused_by"]:
        if identity not in seen:
            findings.append(_finding("missing_cause", event, "Direct cause is absent or occurs later"))
        elif datetime.fromisoformat(seen[identity]["occurred_at"]) > datetime.fromisoformat(event["occurred_at"]):
            findings.append(_finding("future_cause", event, "Direct cause occurs in the future"))


def _check_versions(event: dict, findings: list[dict]) -> None:
    """@brief 单次环境事务只能保持版本或推进一个版本。"""
    before, after = event["state_version_before"], event["state_version_after"]
    if before is not None and after is not None and after - before not in (0, 1):
        findings.append(_finding("state_version_jump", event, "State version is discontinuous"))


def _check_resources(event: dict, findings: list[dict]) -> None:
    """@brief 只对完整声明的资金双边变化检查守恒。"""
    money = {key: value for key, value in event["resource_changes"].items() if key.startswith("account:")}
    if money and (len(money) < 2 or sum(money.values()) != 0):
        findings.append(_finding("money_not_conserved", event, "Account changes do not balance"))


def execute_fixture(fixture: dict, expressions: dict[str, str]) -> dict:
    """@brief 在严格字段环境中执行独立冻结的规则，比较非引擎生成的预期。"""
    facts = sequence_facts(fixture["initial_state"], fixture["events"])
    context = rule_engine.Context(type_resolver=rule_engine.type_resolver_from_dict(facts))
    actual = {}
    try:
        for name, expression in expressions.items():
            actual[name] = bool(rule_engine.Rule(expression, context=context).evaluate(facts))
    except Exception:
        logging.exception("Rule fixture execution failed")
        raise
    if actual != fixture["expected"]:
        raise IntegrityError("Rule fixture output does not match its independent expected result")
    return {"passed": True, "actual": actual, "provenance": fixture["provenance"]}


def sequence_facts(initial_state: dict, events: list[dict]) -> dict:
    """@brief 从明确请求、受理和完成事件计算时序事实，不生成预期结论。"""
    times = {}
    duplicate = False
    for event in events:
        role = event["role"]
        duplicate = duplicate or role in times
        times[role] = event["timestamp_seconds"]
    complete = all(role in times for role in ("request", "accept", "complete"))
    ordered = complete and times["request"] <= times["accept"] <= times["complete"]
    elapsed = times["complete"] - times["accept"] if complete else -1
    return {"complete": complete, "ordered": ordered, "duplicate": duplicate,
            "elapsed_seconds": elapsed, "deadline_seconds": initial_state["deadline_seconds"]}
