"""真实环境观察中的已形成关系看法决定宿主允许的主体。"""


from tests.integration.runtime.current_contract_fixtures import person, ScriptedModel
from tests.life.conftest import advance_until_ready, context, do_at, use_app
from tests.life.environment_runtime import act
from worldmock.agents.protocol.react_loop import ReactDecision
from worldmock.state.interaction.requests import ConcludeActivityRequest


def test_host_accepts_card_or_existing_relationship_and_rejects_unknown_subject(engine_factory):
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
    decision = context(engine, "conclusion-boundary")
    observed = engine.observe(decision)
    assert {row["person"] for row in observed["persona_views"]["relationships"]} == {"旧友"}
    agent = person(engine, decision, ScriptedModel([]))
    agent._context = decision
    agent._prepare(observed)
    agent.turn_tools(16, False)
    card_names = {item["name_or_appellation"] for item in agent.inputs.persona_card_struct["relationships"]}
    assert card_names
    assert "旧友" in agent._names["known_persons"]
    name = engine.context.state.activities[result.activity_id].name
    for subject in [*sorted(card_names), "旧友", "从未认识的陌生人"]:
        arguments = {"items": [{"activity_name": name, "summary": "这段内容结束了。", "memories": [],
            "views": [{"type": "relationship", "about": subject, "text": "我愿意继续联系。",
                       "importance": 0.5, "keywords": ["关系"]}]}], "errands": []}
        error = agent._validate([ReactDecision("conclude_activity", arguments, "")])
        if subject == "从未认识的陌生人":
            assert error.report.error_code == "SUBJECT_NOT_KNOWN"
        else:
            assert error is None
