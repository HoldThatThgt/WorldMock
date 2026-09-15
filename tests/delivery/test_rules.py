import pytest

from worldmock.delivery.rules import evaluate_causal_events, execute_fixture
from worldmock.errors import IntegrityError


EXPRESSIONS = {'allowed': 'complete and ordered and not duplicate and elapsed_seconds <= deadline_seconds'}


@pytest.mark.parametrize(('events', 'expected'), [
    ([('request', 0), ('accept', 5), ('complete', 10)], True),
    ([('request', 0), ('complete', 10)], False),
    ([('request', 0), ('complete', 5), ('accept', 10)], False),
    ([('request', 0), ('accept', 5), ('complete', 100)], False),
    ([('request', 0), ('accept', 5), ('complete', 10), ('complete', 11)], False),
])
def test_real_rule_engine_positive_missing_reordered_timeout_and_duplicate(events, expected):
    fixture = {'initial_state': {'deadline_seconds': 60},
               'events': [{'role': role, 'timestamp_seconds': timestamp} for role, timestamp in events],
               'expected': {'allowed': expected}, 'provenance': {'source_group_id': 'fixture'}}
    assert execute_fixture(fixture, EXPRESSIONS)['actual'] == fixture['expected']
    fixture['expected'] = {'allowed': not expected}
    with pytest.raises(IntegrityError): execute_fixture(fixture, EXPRESSIONS)


def test_common_causal_rules_respect_missing_support_and_detect_contradictions():
    event = {'source_event_id': 'bad', 'occurred_at': '2026-01-05T10:00:00+08:00',
             'ended_at': '2026-01-05T09:00:00+08:00', 'caused_by': ['absent'],
             'state_version_before': 1, 'state_version_after': 3,
             'resource_changes': {'account:person': -5, 'account:merchant': 3}, 'source_pointer': 'events:1'}
    assert evaluate_causal_events([event], {'fields': []}) == []
    findings = evaluate_causal_events([event], {'fields': list(event)})
    assert {item['code'] for item in findings} == {
        'negative_duration', 'missing_cause', 'state_version_jump', 'money_not_conserved'}
    assert all(item['event_ids'] == ['bad'] for item in findings)
