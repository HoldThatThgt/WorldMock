"""活动进度来源即使没有活动快照也必须满足人物可见的环境事实边界。"""

import pytest

from worldmock.foundation.errors import IntegrityError
from worldmock.recording_engine.projections.activities.app_usage import validate_app_event_source


@pytest.mark.parametrize("source_kind,visibility", [("agent", "agent"), ("environment", "system")])
def test_progress_without_a_snapshot_still_rejects_the_wrong_source(source_kind, visibility):
    """不能用缺少快照的进度正文绕过来源与可见性核验。"""
    event = {"event_type": "activity_progress", "agent_id": 1, "source_kind": source_kind,
             "visibility": visibility, "data": {}}
    with pytest.raises(IntegrityError, match="Activity progress must be an agent-visible environment event"):
        validate_app_event_source(event)
