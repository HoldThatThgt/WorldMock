"""@brief 严格人设、共识结果与社交增长映射形成唯一画像。"""

import pytest

from tests.profile.conftest import make_payload
from tests.profile.model_fixture import Response, ScriptedClient, extracted, persona
from worldmock.errors import WorldMockError
from worldmock.profile.compile import compile_persona
from worldmock.profile.mapper import load_mapping_config, map_world_dynamics
from worldmock.profile.models import CompiledPersona


def compile_payloads(payloads):
    source = persona()
    extraction = extracted(source.background_story, client=ScriptedClient([Response(item) for item in payloads]))
    return compile_persona(source, extraction=extraction, persona_sha256="a" * 64)


def test_compiled_contract_contains_card_traits_current_dynamics_and_input_identity():
    compiled = compile_payloads([make_payload()] * 3)
    assert isinstance(compiled, CompiledPersona)
    result = compiled.model_dump()
    assert set(result) == {"schema_version", "card", "big_five", "world_dynamics", "source", "warnings"}
    assert result["schema_version"] == 4
    assert result["card"]["identity"]["occupation"] == "产品经理"
    assert result["world_dynamics"] == {"social_need_growth_per_hour": 0.09}
    assert compiled.source.background_story_sha256 == "a" * 64
    assert compiled.source.model == "test-model"
    assert compiled.source.prompt_version == "card-v4"
    assert compiled.source.consensus.runs == 3
    assert compiled.warnings == ()


def test_same_inputs_produce_identical_compiled_bytes():
    assert compile_payloads([make_payload()] * 3).model_dump_json() == compile_payloads(
        [make_payload()] * 3).model_dump_json()


@pytest.mark.parametrize("source", ["story", "   ", {"background_story": "story"}, {"occupation": "工程师"}])
def test_compiler_rejects_unvalidated_inputs(source):
    extraction = extracted("story", client=ScriptedClient([Response(make_payload())] * 3))
    with pytest.raises(WorldMockError, match="validated persona inputs"):
        compile_persona(source, extraction=extraction, persona_sha256="a" * 64)


def test_low_agreement_is_exposed_without_changing_the_story():
    compiled = compile_payloads([make_payload(), make_payload(coping="去江边散步"), make_payload(coping="听老歌")])
    assert compiled.warnings == ("field_agreement_below_two_thirds: ['coping']",)


def test_world_dynamics_are_the_exact_current_mapping():
    compiled = compile_payloads([make_payload()] * 3)
    assert compiled.world_dynamics == map_world_dynamics(compiled.big_five, load_mapping_config())


def test_work_anchor_demotion_notes_follow_consensus_warnings():
    changes = {"work_anchors": {"leave_work": "07:00-07:30", "arrive_home": "23:30-24:00"}}
    compiled = compile_payloads([make_payload(**changes), make_payload(coping="散步", **changes),
                                make_payload(coping="听歌", **changes)])
    assert compiled.warnings == (
        "field_agreement_below_two_thirds: ['coping']",
        "work_anchor_demoted: leave_work start 7 is outside leave_home 8..bedtime 23",
        "work_anchor_demoted: arrive_home start 23.5 is outside leave_home 8..bedtime 23",
    )
    assert compiled.card.time_anchors.leave_work is None
    assert compiled.card.time_anchors.arrive_home is None
