"""原始人设字段覆盖编译卡片，实际警告冻结并进入导出。"""
from dataclasses import replace
import json
import pytest
from tests.integration.runtime.current_contract_fixtures import inputs, compiled_persona
from tests.life.conftest import stage_day_plan, do_at, advance_next
from tests.life.environment_runtime import act
from tests.profile.conftest import make_card
from worldmock.run_setup import creation_inputs
from worldmock.profile.injection import apply_persona_rhythm
from worldmock.footprints.export import export_run

@pytest.mark.parametrize("field,value", [("chronotype_midsleep", 4.75), ("sleep_need_hours", 8.0),
    ("lunch_window", (12.0, 12.5)), ("dinner_window", (19.0, 20.0)), ("bedtime_window", (23.5, 24.5))])
def test_explicit_persona_rhythm_overrides_card_and_records_warning(field, value):
    frozen = inputs()
    persona = frozen.persona.model_copy(update={field: value})
    result = apply_persona_rhythm(make_card(), persona)
    warnings = [row for row in result.warnings if row["details"]["field"] == field]
    assert warnings and warnings[0]["code"] == "CARD_FIELD_OVERRIDDEN"
    repeated = apply_persona_rhythm(result.card, persona)
    assert repeated.card == result.card

def test_unset_rhythm_window_keeps_compiled_card_value():
    frozen = inputs()
    assert "leave_work_window" not in frozen.persona.model_fields_set
    card = make_card()
    result = apply_persona_rhythm(card, frozen.persona)
    assert result.card.time_anchors.leave_work == card.time_anchors.leave_work
    assert not any(row["details"]["field"] == "leave_work_window" for row in result.warnings)

def test_actual_input_warnings_are_frozen_and_exported(engine_factory, tmp_path):
    frozen = inputs()
    compiled = compiled_persona(frozen)
    compiled = compiled.model_copy(update={"warnings": ("field_agreement_below_two_thirds: ['coping']",)})
    specs, warnings = creation_inputs(frozen, {1: compiled})
    assert specs[0]["profile"]["persona_card_struct"]
    assert any(row["code"] == "PERSONA_AGREEMENT_LOW" for row in warnings)
    engine = engine_factory(create_options={"input_warnings": warnings})
    assert engine.context.store.manifest["input_warnings"] == warnings
    stage_day_plan(engine)
    act(engine, "content", do_at("家", "测试休息"))
    advance_next(engine)
    export_run(engine.context.store, engine.context.store.last_event_seq, tmp_path / "export")
    quality = json.loads((tmp_path / "export/quality.json").read_text(encoding="utf-8"))
    assert quality["input_warnings"]["warning_count"] == len(warnings)
    assert quality["input_warnings"]["recorded"] is True
