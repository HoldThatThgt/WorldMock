"""联系人回复不虚构到场（docs/dev/SPEC-dayrun-fidelity.md 7.3，裁决 D11）：请求事实、后验守卫、同一结算内的一次重试与兜底句。

守卫样本取自一日运行的真实回复（tests/fixtures/dayrun-contact-replies.json）：WC 王磊 6 条回复里的 5 条越界回复与 BIZ
刘洋的「我9点到公司」各命中登记的类别，7 条无害回复不命中（两次 4a5b0e6 运行只有 2 条无害回复，另 5 条取自同一数据集
的白领 deepseek-flash-after-wake-fix 运行）。重试、兜底、请求字段与每条来信一条回复事件经真实引擎的发消息决策与回复
结算验证；质量报告的 contact_reply_guard 从导出的同批事件计数。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from agentsociety2.society import DirectToolCall

from tests.life.conftest import advance_next, context, start_activity, submit_round
from worldmock.footprints.export import export_run
from worldmock.life.models import SendMessageActionRequest
from worldmock.life.readable import iso_time
from worldmock.life.reply_guard import FALLBACK_REPLY, GUARD_PATTERNS, GUARD_REASONS, correction_text, guard_matches
from worldmock.runtime import _CONTACT_REPLY_PROMPT, settle_pending_model_replies

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SAMPLES = json.loads((_REPO_ROOT / "tests" / "fixtures" / "dayrun-contact-replies.json").read_text(encoding="utf-8"))
OVERREACH = _SAMPLES["overreach"]
HARMLESS = _SAMPLES["harmless"]
#: 白领场景里人物 1 的 model 策略老友联系人（没有收货地点）与夹具加的 model 策略同事联系人（收货地点是办公室）。
_FRIEND = "contact-chenxi-oldfriend"
_COLLEAGUE = "contact-chenxi-wanglei"


def _sample_id(sample: dict) -> str:
    """@brief 样本的用例名：运行（wc、biz，另一次白领运行记 wc-awf）加事件序号。"""
    run = sample["run"]
    tag = ("biz" if run.startswith("0002") else "wc") + ("-awf" if "after-wake" in run else "")
    return f"{tag}-{sample['event_seq']}"


def _with_model_colleague(document: dict) -> None:
    """@brief 场景副本里给人物 1 加 model 策略的同事联系人王磊，收货地点是办公室。"""
    agent = next(item for item in document["agents"] if item["agent_id"] == 1)
    agent["contacts"].append({"contact_id": _COLLEAGUE, "salutation": "王磊", "relation": "同事", "closeness": 16,
                              "channels": ["workplace-IM"], "reply_policy": "model", "receive_location_id": "office"})


class _ScriptedReplies:
    """联系人回复模型桩：依次返回脚本里的回复正文并保存每次请求。"""

    model_name = "deepseek-flash"

    def __init__(self, *texts: str):
        self.texts = list(texts)
        self.calls: list[dict] = []

    async def call(self, request: dict) -> dict:
        """@brief 保存请求，返回下一条脚本回复。"""
        self.calls.append(request)
        return {"model": self.model_name, "choices": [{"message": {"content": self.texts.pop(0)}}],
                "usage": {"prompt_tokens": 40, "completion_tokens": 20}}


def _send(engine, round_id: str, contact_id: str = _FRIEND, text: str = "周五晚上一起吃饭？") -> int:
    """@brief 真实决策轮：人物 1 按本轮私有绑定里的联系人名称发一条消息；返回发出时刻。"""
    decision = context(engine, round_id)
    engine.begin_decision_round([decision])
    observed = engine.observe(decision)
    recipient = next(row["object"] for row in observed["interaction_bindings"] if row["object_id"] == contact_id)
    engine.submit_action(DirectToolCall(decision, f"action-{round_id}", "send_message",
                                        SendMessageActionRequest(recipient_name=recipient, text=text)))
    [result] = engine.commit_decision_round(round_id)
    assert result.status == "completed", (result.error_code, result.message)
    return engine.state.current_time


def _reply_events(engine) -> list:
    """@brief 全部 model_npc_reply 回复事件（事件序升序）。"""
    return [event for event in engine.store.read_events(engine.store.last_event_seq)
            if event.data.get("kind") == "model_npc_reply"]


def _scheduled_texts(engine) -> list[str]:
    """@brief 预排送达的联系人回复正文（按到期时刻与序号）。"""
    return [item.data["message"]["text"] for item in engine.state.pending_events if item.kind == "message_arrival"]


@pytest.mark.parametrize("sample", OVERREACH, ids=_sample_id)
def test_real_overreach_reply_hits_its_registered_categories(sample) -> None:
    """真实越界回复命中登记的类别：替对方分工、约当面、声称已完成并赶来、声称在路上、声称到场并保证结果（WC 王磊），
    「我9点到公司」（BIZ 刘洋）。"""
    assert guard_matches(sample["reply"]) == sample["categories"]


@pytest.mark.parametrize("sample", HARMLESS, ids=_sample_id)
def test_real_harmless_reply_passes_the_guard(sample) -> None:
    """真实无害回复（安排自己的事、关心对方、以后碰头的打算）不命中任何类别。"""
    assert guard_matches(sample["reply"]) == []


def test_contract_registers_the_template_guard_closed_set_and_fallback_verbatim() -> None:
    """接口合同「联系人回复」逐字登记 system 中文模板、四类正则闭集、纠正句与各类说法、兜底句。"""
    text = (_REPO_ROOT / "docs" / "CONTRACTS.md").read_text(encoding="utf-8")
    section = text.split("\n### 联系人回复\n", 1)[1].split("\n### 社交拒绝错误码\n", 1)[0]
    assert f"```text\n{_CONTACT_REPLY_PROMPT}\n```" in section
    registered = re.search(r"逐字；类别码在前）：\n\n```text\n(.*?)\n```", section, flags=re.DOTALL)
    assert registered is not None
    expected = [f"{code}: {pattern.pattern}" for code, pattern in GUARD_PATTERNS.items()]
    assert registered.group(1).splitlines() == expected
    assert all(f"| `{code}` | {reason} |" in section for code, reason in GUARD_REASONS.items())
    assert correction_text(["guaranteeing"]).replace(GUARD_REASONS["guaranteeing"], "<说法>") in section
    assert f"「{FALLBACK_REPLY}」" in section


def test_samples_cover_every_category_and_the_fallback_passes() -> None:
    """样本是规格点名的 5 条 WC 越界与 1 条 BIZ 越界加 7 条无害回复，四个类别都有真实样本命中；兜底句自身不命中。"""
    assert [sample["contact"] for sample in OVERREACH] == ["王磊"] * 5 + ["刘洋"]
    assert len(HARMLESS) == 7
    assert {code for sample in OVERREACH for code in sample["categories"]} == set(GUARD_PATTERNS)
    assert guard_matches(FALLBACK_REPLY) == []


@pytest.mark.parametrize("sample", OVERREACH, ids=_sample_id)
async def test_overreach_reply_is_retried_once_in_the_same_settlement(engine_factory, sample) -> None:
    """越界回复命中后在同一次结算内重试一次：重试请求是原请求附被拦下的回复与纠正，其余参数不变；只写一条回复事件，
    attempts 记两次尝试的请求、回复与命中类别，重试通过时不用兜底句（guard_applied 为 false）。"""
    engine = engine_factory()
    sent_at = _send(engine, "guard-retry")
    harmless = HARMLESS[0]["reply"]
    client = _ScriptedReplies(sample["reply"], harmless)
    [settled] = await settle_pending_model_replies(engine, client=client)
    first, second = client.calls
    assert second["messages"] == [*first["messages"], {"role": "assistant", "content": sample["reply"]},
                                  {"role": "user", "content": correction_text(sample["categories"])}]
    assert {key: value for key, value in second.items() if key != "messages"} == {
        key: value for key, value in first.items() if key != "messages"}
    [event] = _reply_events(engine)
    assert event.occurred_at == sent_at and event.data["reply_to_message_id"] == settled["reply_to_message_id"]
    assert [(item["request"], item["text"], item["matched"]) for item in event.data["attempts"]] == [
        (first, sample["reply"], sample["categories"]), (second, harmless, [])]
    assert event.data["guard_applied"] is False
    assert (event.data["request"], event.data["reply"]["text"]) == (second, harmless)
    assert _scheduled_texts(engine) == [harmless]


async def test_fallback_reply_appears_only_when_both_attempts_hit(engine_factory) -> None:
    """首次不命中就不重试也不用兜底句；首次与重试都命中时用固定兜底句，事件带 guard_applied。"""
    engine = engine_factory()
    _send(engine, "guard-clean")
    clean = _ScriptedReplies(HARMLESS[1]["reply"])
    await settle_pending_model_replies(engine, client=clean)
    _send(engine, "guard-fallback")
    stubborn = _ScriptedReplies(OVERREACH[4]["reply"], OVERREACH[5]["reply"])
    await settle_pending_model_replies(engine, client=stubborn)
    assert (len(clean.calls), len(stubborn.calls)) == (1, 2)
    kept, replaced = _reply_events(engine)
    assert (kept.data["reply"]["text"], kept.data["guard_applied"]) == (HARMLESS[1]["reply"], False)
    assert [item["matched"] for item in kept.data["attempts"]] == [[]]
    assert (replaced.data["reply"]["text"], replaced.data["guard_applied"]) == (FALLBACK_REPLY, True)
    assert [item["matched"] for item in replaced.data["attempts"]] == [OVERREACH[4]["categories"],
                                                                      OVERREACH[5]["categories"]]
    assert _scheduled_texts(engine) == [HARMLESS[1]["reply"], FALLBACK_REPLY]


async def test_reply_request_carries_sender_name_now_presence_and_receive_location(engine_factory) -> None:
    """回复请求带发件人姓名、场景本地时刻、presence=remote 与联系人的收货地名（没有收货地点时为 null）；待回复登记与
    模型请求同一组事实；system 提示是登记的中文模板。"""
    engine = engine_factory(mutate=_with_model_colleague)
    sent_at = _send(engine, "guard-facts", contact_id=_COLLEAGUE, text="中午一起点外卖吗？")
    client = _ScriptedReplies("好啊，你先点，我看看菜单。")
    await settle_pending_model_replies(engine, client=client)
    [request] = client.calls
    assert request["messages"][0] == {"role": "system", "content": _CONTACT_REPLY_PROMPT}
    office = next(item.name for item in engine.scenario.locations if item.location_id == "office")
    expected = {"sender_name": engine.state.agents[1].name, "now": iso_time(sent_at, engine.scenario.timezone),
                "presence": "remote", "receive_location": office}
    payload = json.loads(request["messages"][1]["content"])
    assert {key: payload[key] for key in expected} == expected
    assert payload["incoming_message"] == "中午一起点外卖吗？"
    [pending] = [event.data["request"] for event in engine.store.read_agent_events("environment_event", 1)
                 if event.data.get("kind") == "model_npc_reply_pending"]
    assert {key: pending[key] for key in expected} == expected
    _send(engine, "guard-facts-friend")
    friend = _ScriptedReplies("周五可以，到时候再约。")
    await settle_pending_model_replies(engine, client=friend)
    other = json.loads(friend.calls[0]["messages"][1]["content"])
    assert (other["receive_location"], other["presence"]) == (None, "remote")


async def test_each_incoming_message_gets_one_reply_event_even_after_a_retry(engine_factory) -> None:
    """重试仍只写一条回复事件，幂等键仍是来信的消息身份：再结算零调用、零事件。"""
    engine = engine_factory()
    _send(engine, "guard-once")
    [message_id] = list(engine.state.agents[1].outbox)
    client = _ScriptedReplies(OVERREACH[1]["reply"], HARMLESS[2]["reply"])
    first = await settle_pending_model_replies(engine, client=client)
    second = await settle_pending_model_replies(engine, client=client)
    assert (len(first), second, len(client.calls)) == (1, [], 2)
    [event] = _reply_events(engine)
    assert event.data["reply_to_message_id"] == message_id
    assert event.data["reply"]["message_id"] == f"reply-{message_id}"
    assert len(event.data["attempts"]) == 2


async def test_quality_report_counts_guard_hits_retries_and_fallbacks(engine_factory, tmp_path: Path) -> None:
    """导出的 quality.json 的 contact_reply_guard：命中按尝试计、类别按尝试计，重试与兜底按回复计。"""
    engine = engine_factory()
    _send(engine, "quality-retry")
    await settle_pending_model_replies(engine, client=_ScriptedReplies(OVERREACH[0]["reply"], HARMLESS[3]["reply"]))
    _send(engine, "quality-fallback")
    await settle_pending_model_replies(engine, client=_ScriptedReplies(OVERREACH[4]["reply"], OVERREACH[5]["reply"]))
    submit_round(engine, {1: ("start_activity", start_activity("等待", "自己"))}, "quality-wait")
    advance_next(engine)
    reference = engine.store.assets.put_json({"agent_id": 1}, {"kind": "contact_reply_guard_test"})
    checkpoint = engine.checkpoint({"agent:1": reference})
    export_run(engine.store, checkpoint.last_event_seq, tmp_path / "export")
    quality = json.loads((tmp_path / "export" / "quality.json").read_text(encoding="utf-8"))
    assert quality["contact_reply_guard"] == {
        "replies": 2, "replies_without_guard_evidence": 0, "attempts": 4, "hits": 3, "retries": 2, "fallbacks": 1,
        "hits_by_category": {"arriving": 2, "done_or_bringing": 1, "taking_over": 1, "guaranteeing": 1}}
