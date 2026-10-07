"""真实适配入口只解析一次世界事件，再由统计和公开投影直接消费同一模型。"""

import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from types import SimpleNamespace

from tests.evaluation.conftest import _write_canonical, _write_json, _write_labelkit
from worldmock.evaluation.adapters import adapt_worldmock
from worldmock.evaluation import adapters
from worldmock.evaluation import metrics, review
from worldmock.evaluation.scenario import load_scenario
from worldmock.footprints.models import Event


def _canonical_world(tmp_path):
    """@brief 用人工映射和真实导出建立完整自然日输入，不借用正式评估场景。"""
    start = datetime(2026, 1, 5, tzinfo=ZoneInfo("Asia/Shanghai"))
    end = start + timedelta(days=1)
    document = {
        "scenario_id": "artificial-event-parsing", "schema_version": "evaluation-scenario-1",
        "window": {"start": start.isoformat(), "end": end.isoformat(), "timezone": "Asia/Shanghai"},
        "bootstrap": {"seed": 7, "repetitions": 100, "block_days": 7},
        "review": {"seed": "artificial-review", "reviewer_count": 3, "dimensions": ["continuity"]},
        "shared_profile": {"person_count": 1, "occupation_class": "white_collar", "timezone": "Asia/Shanghai"},
        "special_dates": {},
        "category_mappings": {"labelkit_sequence_class": {}, "labelkit_pattern": {},
                              "worldmock_activity": {"use_app": "internet_use"}},
        "official_domain_map": {"internet_use": ["internet_use"]},
        "official_reference": {"source": "Artificial unit-test reference; no empirical claim", "domains": {
            "internet_use": {"label_zh": "人工浏览参照", "supported_duration_kinds": ["world_internet_session"],
                             "urban": {"participant_minutes": 120, "participation_rate": 1},
                             "working_age": {"participant_minutes": 120, "participation_rate": 1}}}},
        "expected_labelkit": _write_labelkit(tmp_path / "labelkit", start, 1),
    }
    path = tmp_path / "evaluation.json"
    _write_json(path, document)
    worldmock_root = tmp_path / "worldmock"
    _write_canonical(worldmock_root, document, "world-run", 9)
    return load_scenario(path), worldmock_root


def test_canonical_adapter_validates_each_event_once_for_statistics_and_public_evidence(tmp_path, monkeypatch):
    scenario, worldmock_root = _canonical_world(tmp_path)
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
    view = adapt_worldmock(worldmock_root, scenario)
    rows = [json.loads(line) for line in (worldmock_root / 'events.jsonl').read_text().splitlines()]
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
