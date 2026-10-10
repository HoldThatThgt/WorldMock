import pytest

from worldmock.errors import IntegrityError
from worldmock.footprints.ledger import validate_account_history
from worldmock.footprints.models import EventDraft
from worldmock.footprints.store import FootprintStore


@pytest.fixture
def funded(tmp_path):
    accounts = {'agent:1': 100, 'external:scenario': -100}
    store = FootprintStore.create(tmp_path / 'run', {'run_id': 'run', 'agent_ids': [1]},
                                  {'version': 0, 'current_time': 100, 'accounts': accounts})
    injection = EventDraft(event_type='environment_event', occurred_at=100, source_kind='scenario',
                           data={'kind': 'initial_funds_injected'},
                           resource_changes={'account:agent:1': 100, 'account:external:scenario': -100})
    store.commit(store.state, [injection])
    yield store
    store.close()


def transfer(changes):
    return EventDraft(event_type='action_result', occurred_at=101, resource_changes=changes)


def test_balanced_transfer_reconstructs_actual_final_accounts(funded):
    state = {'version': 1, 'current_time': 101, 'accounts': {'agent:1': 90, 'external:scenario': -90}}
    funded.commit(state, [transfer({'account:agent:1': -10, 'account:external:scenario': 10})])
    validate_account_history(funded.read_events(funded.last_event_seq), funded.state)
    assert funded.state['accounts'] == state['accounts']


@pytest.mark.parametrize('kind', ['missing', 'duplicated', 'wrong_amount', 'one_sided', 'unknown', 'fraction'])
def test_missing_or_false_ledger_cannot_commit_changed_accounts(funded, kind):
    changes = {'account:agent:1': -10, 'account:external:scenario': 10}
    if kind == 'missing': changes = {}
    if kind == 'wrong_amount': changes = {'account:agent:1': -20, 'account:external:scenario': 20}
    if kind == 'one_sided': changes = {'account:agent:1': -10}
    if kind == 'unknown': changes = {'account:agent:1': -10, 'account:unknown': 10}
    if kind == 'fraction': changes = {'account:agent:1': -10.0, 'account:external:scenario': 10.0}
    events = [transfer(changes), transfer(changes)] if kind == 'duplicated' else [transfer(changes)]
    with pytest.raises(IntegrityError):
        funded.commit({'version': 1, 'current_time': 101, 'accounts': {'agent:1': 90, 'external:scenario': -90}}, events)
    assert funded.state['accounts'] == {'agent:1': 100, 'external:scenario': -100}
    assert funded.last_event_seq == 1


def test_new_funds_require_explicit_external_counterparty(funded):
    new_state = {'version': 1, 'current_time': 101, 'accounts': {'agent:1': 150, 'external:scenario': -150}}
    funded.commit(new_state, [transfer({'account:agent:1': 50, 'account:external:scenario': -50})])
    validate_account_history(funded.read_events(funded.last_event_seq), funded.state)


def test_final_checkpoint_balance_must_equal_complete_event_history(funded):
    wrong = {'accounts': {'agent:1': 90, 'external:scenario': -90}}
    with pytest.raises(IntegrityError, match='reconstruct'):
        validate_account_history(funded.read_events(funded.last_event_seq), wrong)


def test_reused_receipt_cannot_record_a_second_payment(funded):
    state = {'version': 1, 'current_time': 101, 'accounts': {'agent:1': 90, 'external:scenario': -90}}
    funded.commit(state, [transfer({'account:agent:1': -10, 'account:external:scenario': 10})])
    with pytest.raises(IntegrityError):
        funded.commit(state, [transfer({'account:agent:1': -10, 'account:external:scenario': 10})])
    assert funded.last_event_seq == 2
