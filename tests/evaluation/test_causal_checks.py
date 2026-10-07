"""@brief 因果评估遵守声明的可用字段，并拒绝已证实的矛盾。"""

from worldmock.evaluation.causal_checks import evaluate_causal_events


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


def test_causal_rules_detect_duplicate_and_future_cause():
    """重复事件身份与未来原因各自生成硬错误；缺失支持字段时安静跳过。"""
    first = {'source_event_id': 'e1', 'occurred_at': '2026-01-05T10:00:00+08:00',
             'ended_at': None, 'caused_by': [], 'source_pointer': 'events:1'}
    second = {'source_event_id': 'e1', 'occurred_at': '2026-01-05T09:00:00+08:00',
              'ended_at': None, 'caused_by': ['e1'], 'source_pointer': 'events:2'}
    findings = evaluate_causal_events([first, second], {'fields': ['occurred_at', 'caused_by']})
    assert {item['code'] for item in findings} == {'duplicate_event', 'future_cause'}
    sound = {'source_event_id': 'e2', 'occurred_at': '2026-01-05T11:00:00+08:00',
             'ended_at': None, 'caused_by': ['e1'], 'source_pointer': 'events:2'}
    assert evaluate_causal_events([first, sound],
                                  {'fields': ['occurred_at', 'caused_by']}) == []
    quiet = {'source_event_id': 'e1', 'occurred_at': '2026-01-05T10:00:00+08:00',
             'ended_at': None, 'state_version_before': None,
             'state_version_after': None, 'resource_changes': {'wallet': -5},
             'caused_by': [], 'source_pointer': 'events:1'}
    assert evaluate_causal_events([quiet], {'fields': list(quiet)}) == []
