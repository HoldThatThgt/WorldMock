"""实际内容结算的来源、人物和应用身份不可伪造。"""

from copy import deepcopy

import pytest

from tests.footprints.recording import recorded_engine
from worldmock.foundation.errors import IntegrityError
from worldmock.recording_engine.projections.activities.app_usage import project_app_usage, validate_app_event_source


@pytest.mark.parametrize("fault,reason", [
    ("retired_kind", "retired activity kind"),
    ("different_owner", "owner disagrees"),
    ("different_app", "app disagrees"),
    ("model", "agent-visible environment"),
    ("private", "agent-visible environment"),
])
def test_actual_content_evidence_rejects_wrong_source_without_modifying_inputs(engine_factory, fault, reason):
    engine, _ = recorded_engine(engine_factory, purchases=False)
    store = engine.context.store
    event = next(row.model_dump(mode="json") for row in list(store.iter_events(store.last_event_seq))
                 if row.event_type == "activity_progress" and "content" in row.data)
    baseline = project_app_usage([event])
    original = deepcopy(event)
    corrupted = deepcopy(event)
    if fault == "retired_kind":
        corrupted["data"]["activity"]["kind"] = "action"
    elif fault == "different_owner":
        corrupted["data"]["activity"]["agent_id"] = 2
    elif fault == "different_app":
        corrupted["data"]["content"]["app_id"] = "douyin"
    elif fault == "model":
        corrupted["source_kind"] = "model"
    else:
        corrupted["visibility"] = "system"
    for consume in (validate_app_event_source, lambda row: project_app_usage([row])):
        with pytest.raises(IntegrityError, match=reason):
            consume(corrupted)
    assert event == original and project_app_usage([event]) == baseline
