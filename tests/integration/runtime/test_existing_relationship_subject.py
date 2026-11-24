"""真实环境观察中的已形成关系看法决定宿主允许的主体。"""

import json

import pytest

from tests.integration.runtime.current_contract_fixtures import ToolResponse, decide, person
from tests.life.conftest import MAIN_TOOL, advance_until_ready, do_at, main_arguments, use_app
from tests.life.environment_runtime import act, until_ended
from worldmock.state.interaction.requests import ConcludeActivityRequest


@pytest.mark.asyncio
async def test_host_accepts_card_or_existing_relationship_and_rejects_unknown_subject(engine_factory):
    engine = engine_factory()
    _, prior = act(engine, "prior-content", do_at("家", "测试休息"))
    assert prior.status == "in_progress"
    old_name = engine.context.state.activities[prior.activity_id].name
    advance_until_ready(engine)
    request = ConcludeActivityRequest.model_validate({"items": [{"activity_name": old_name,
        "summary": "想起之前的旧友。", "memories": [], "views": [{"type": "relationship", "about": "旧友",
        "text": "他很可靠。", "importance": 0.5, "keywords": ["关系"]}]}], "errands": []})
    _, concluded = act(engine, "prior-view", request, "conclude_activity")
    assert concluded.status == "completed"
    _, result = act(engine, "real-content", use_app("哔哩哔哩"))
    assert result.status == "in_progress"
    assert advance_until_ready(engine)
    agent = person(1, engine.context.inputs)
    card_names = sorted({item["name_or_appellation"] for item in agent.inputs.persona_card_struct["relationships"]})
    assert card_names
    name = engine.context.state.activities[result.activity_id].name
    subjects = [*card_names, "旧友", "从未认识的陌生人"]

    def _conclude_args(subject: str) -> dict:
        return {"items": [{"activity_name": name, "summary": "这段内容结束了。", "memories": [],
                "views": [{"type": "relationship", "about": subject, "text": "我愿意继续联系。",
                           "importance": 0.5, "keywords": ["关系"]}]}], "errands": []}

    responses = [ToolResponse("conclude_activity", _conclude_args("从未认识的陌生人"), "call-unknown")]
    for index, subject in enumerate([*card_names, "旧友"]):
        tool_name = "conclude_activity" if index == 0 else "conclude_activity"
        responses.append(ToolResponse(tool_name, _conclude_args(subject), f"call-ok-{index}"))
    # 未知主体触发 SUBJECT_NOT_KNOWN 纠错，卡片关系与已有看法「旧友」正常收尾结束
    observed, requests, _, _ = await decide(engine, "subject-check", [
        ToolResponse("conclude_activity", _conclude_args("从未认识的陌生人"), "call-unknown"),
        ToolResponse("conclude_activity", _conclude_args("旧友"), "call-known"),
        ToolResponse(MAIN_TOOL, main_arguments("sleep", "60"), "call-sleep"),
    ])
    assert {row["person"] for row in observed["persona_views"]["relationships"]} == {"旧友"}
    assert len(requests) == 3
    assert json.loads(requests[1]["messages"][-1]["content"])["error_code"] == "SUBJECT_NOT_KNOWN"

    # 对照另一个新局验证卡片关系主体「母亲」直接收尾成功
    engine2 = engine_factory()
    _, started2 = act(engine2, "card-content", use_app("哔哩哔哩"))
    until_ended(engine2, started2.activity_id)
    name2 = engine2.context.state.activities[started2.activity_id].name
    args_card = {"items": [{"activity_name": name2, "summary": "卡片关系测试。", "memories": [],
                 "views": [{"type": "relationship", "about": card_names[0], "text": "我愿意继续联系。",
                            "importance": 0.5, "keywords": ["关系"]}]}], "errands": []}
    _, reqs2, _, _ = await decide(engine2, "card-ok", [
        ToolResponse("conclude_activity", args_card, "call-card"),
        ToolResponse(MAIN_TOOL, main_arguments("sleep", "60"), "call-sleep2"),
    ])
    assert len(reqs2) == 2
    assert engine2.context.state.activities[started2.activity_id].status == "completed"
