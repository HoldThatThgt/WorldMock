"""读完邮件的公开结果：邮件正文只在阅读动作的结果中出现（docs/dev/SPEC-persona-prompt.md 第一章「唯一出现原则」
「邮件正文 | 阅读动作的结果」，第八章「进度层」）。

真实 LifeEngine、SQLite、LifeEnv 与 LifeAgent，只替换远程模型传输：场景邮件到达后人物边等边读，读完的那一秒人物做一次
真实决策（收尾）。发给模型的请求里，进度层该阅读调用的结果 details.mail 写主题与正文；整次请求（system、六层 user 与
原生工具）中正文恰好出现一次：到达记录只有主题，工具层的邮件目标只写「已读」。
"""

from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("AGENTSOCIETY_LLM_API_KEY", "test-key")
os.environ.setdefault("AGENTSOCIETY_LLM_API_BASE", "https://api.deepseek.com/beta")
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from custom.envs.life_env import LifeEnv  # noqa: E402
from tests.integration.test_minimum_complete_path import _decide  # noqa: E402
from tests.life.conftest import (activity_name, advance_next, advance_until_ready, conclude_pending,  # noqa: E402
                                 conclude_request, context, engine_factory as _engine_factory, pending_names,
                                 progress_calls, stage_day_plan, start_activity, submit_round, tool_targets,
                                 wake_by_mail)
from worldmock.life.activity_control import pending_conclusions  # noqa: E402
from worldmock.life.models import ActivityStatus, ReportStatusRequest, active_activities  # noqa: E402


def _read_to_the_end(engine, reading_id: str) -> None:
    """推进到阅读结束：途中的决策机会先给别的刚结束的活动收尾，否则播报继续所有进行中的活动。"""
    for step in range(64):
        if engine.state.activities[reading_id].status != ActivityStatus.ACTIVE:
            return
        if not engine.scheduling_state().ready_agent_ids:
            advance_next(engine)
        elif pending_conclusions(engine.state, 1):
            conclude_pending(engine, f"mail-other-{step}")
        else:
            running = [activity_name(engine, item.activity_id) for item in active_activities(engine.state, 1)]
            submit_round(engine, {1: ("report_status", ReportStatusRequest(
                task=running, summary=["接着读。"] * len(running)))}, f"mail-report-{step}")
    raise AssertionError("the mail was not read to the end")


@pytest.mark.asyncio
async def test_the_mail_body_appears_once_in_the_request_as_the_read_result(_engine_factory, monkeypatch) -> None:
    """读完邮件后的真实请求：阅读调用的结果带主题与正文，正文在整次请求中只出现这一次。"""
    monkeypatch.setattr("worldmock.life.prompt_capacity.model_context_length", lambda _model: 131072)
    engine = _engine_factory()
    stage_day_plan(engine)
    message = engine.scenario.messages[0]
    wake_by_mail(engine, offset=60)
    submit_round(engine, {1: ("start_activity", start_activity("等待", "自己"))}, "mail-wait")
    assert advance_until_ready(engine)
    arrived = engine.observe(context(engine, "mail-arrived"))
    mail = next(row["object"] for row in arrived["interaction_bindings"] if row["kind"] == "mail")
    _, [reading] = submit_round(engine, {1: ("start_activity", start_activity("阅读", mail))}, "mail-read")
    assert reading.status == "in_progress", reading.error_code
    _read_to_the_end(engine, reading.activity_id)
    assert engine.state.activities[reading.activity_id].status == ActivityStatus.COMPLETED

    environment = LifeEnv()
    environment.engine = engine
    observation, [request], concluded = await _decide(environment, "mail-conclude", lambda seen: (
        "conclude_activity", conclude_request(*pending_names(seen)).model_dump(mode="json")))
    assert concluded.status == "completed", (concluded.error_code, concluded.message)

    [call] = [row for row in progress_calls(observation, "start_activity") if row["arguments"]["ways"] == ["阅读"]]
    assert call["result"]["status"] == "completed"
    assert call["result"]["details"]["mail"] == {"subject": message.subject, "body": message.body}
    assert json.loads(request["messages"][1]["content"])[5] == observation["progress_layer"]
    body = json.dumps(message.body, ensure_ascii=False)[1:-1]
    assert sum(str(item.get("content") or "").count(body) for item in request["messages"]) == 1
    assert body not in json.dumps(request["tools"], ensure_ascii=False)
    arrivals = [row for row in observation["progress_layer"]["events"]
                if row["type"] == "message_arrival" and row["kind"] == "mail"]
    assert arrivals and all(row["subject"] == message.subject and "text" not in row for row in arrivals)
    assert tool_targets(observation)[mail]["state"] == "已读"
