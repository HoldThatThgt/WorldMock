"""真实适配入口只解析一次世界事件，再由统计和公开投影直接消费同一模型。"""

import json
from datetime import date
from types import SimpleNamespace

from tests.evaluation.conftest import make_evaluation_fixture
from worldmock.evaluation.adapters import adapt_worldmock
from worldmock.evaluation import adapters
from worldmock.evaluation import metrics, review
from worldmock.evaluation.scenario import load_scenario
from worldmock.footprints.models import Event


def test_canonical_adapter_validates_each_event_once_for_statistics_and_public_evidence(tmp_path, monkeypatch):
    evaluation_fixture = make_evaluation_fixture(tmp_path)
    scenario = load_scenario(evaluation_fixture.scenario_path)
    calls, adapter_calls, projected = [], [], []
    public = adapters.worldmock_public_evidence
    def counted_public(events, activities, mapping):
        projected.extend(events)
        return public(events, activities, mapping)
    monkeypatch.setattr(adapters, "worldmock_public_evidence", counted_public)
    parse = adapters.parse_world_event
    def counted_parse(row):
        adapter_calls.append(row["event_id"])
        return parse(row)
    monkeypatch.setattr(adapters, "parse_world_event", counted_parse)
    validate = Event.model_validate
    def counted(cls, row, **kwargs):
        calls.append(row['event_id'])
        return validate(row, **kwargs)
    monkeypatch.setattr(Event, 'model_validate', classmethod(counted))
    view = adapt_worldmock(evaluation_fixture.worldmock_root, scenario)
    rows = [json.loads(line) for line in (evaluation_fixture.worldmock_root / 'events.jsonl').read_text().splitlines()]
    identities = [row['event_id'] for row in rows]
    assert adapter_calls == identities
    assert calls == identities * 2  # 独立导出身份认证与一次适配解析各核验一次，公开投影不再次核验。
    assert view.events
    assert [event.event_id for event in projected] == identities
    assert all(isinstance(event, Event) for event in projected)


def test_metric_and_review_date_blocks_share_full_window_partition():
    scenario = SimpleNamespace(window=SimpleNamespace(days=[date(2026, 1, day) for day in range(1, 9)]), block_days=3)
    expected = [(date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)),
                (date(2026, 1, 4), date(2026, 1, 5), date(2026, 1, 6)),
                (date(2026, 1, 7), date(2026, 1, 8))]
    assert metrics.date_blocks(scenario) == review.date_blocks(scenario) == expected
    assert review.date_blocks is metrics.date_blocks
