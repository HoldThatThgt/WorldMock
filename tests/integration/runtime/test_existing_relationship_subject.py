"""真实环境观察中的已形成关系看法决定宿主允许的主体。"""

from agentsociety2.agent.base import ReactDecision, ReactLoopRequest

from tests.integration.runtime.current_contract_fixtures import environment, person, ScriptedModel
from tests.life.conftest import advance_until_ready, context, do_at, use_app
from tests.life.environment_runtime import act
from worldmock.agents.life_feedback import feedback_report
from worldmock.life.models import ConcludeActivityRequest


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
    env = environment(engine)
    observed = engine.observe(decision)
    assert {row["person"] for row in observed["persona_views"]["relationships"]} == {"旧友"}
    agent = person(env, decision, ScriptedModel([]))
    agent.prepare_react_loop(ReactLoopRequest(0, decision.observed_at,
        [{"turn": 0, "action": "observe", "ok": True, "data": observed}]))
    card_names = {item["name_or_appellation"] for item in agent._profile["persona_card_struct"]["relationships"]}
    assert card_names
    assert "旧友" in agent._life_names["known_persons"]
    name = engine.context.state.activities[result.activity_id].name
    for subject in [*sorted(card_names), "旧友", "从未认识的陌生人"]:
        arguments = {"items": [{"activity_name": name, "summary": "这段内容结束了。", "memories": [],
            "views": [{"type": "relationship", "about": subject, "text": "我愿意继续联系。",
                       "importance": 0.5, "keywords": ["关系"]}]}], "errands": []}
        error = agent.validate_react_decisions([ReactDecision("", "conclude_activity", arguments, "")], False)
        if subject == "从未认识的陌生人":
            assert feedback_report(error).error_code == "SUBJECT_NOT_KNOWN"
        else:
            assert error == ""
