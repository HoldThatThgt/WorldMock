import pytest

from worldmock.errors import IntegrityError
from worldmock.footprints.ledger import validate_account_history
from worldmock.footprints.models import EventDraft
from worldmock.footprints.store import FootprintStore


@pytest.fixture
def funded(tmp_path):
    accounts = {'person:1': 100, 'merchant': 200}
    store = FootprintStore.create(tmp_path / 'run', {'run_id': 'run', 'agent_ids': [1]},
                                  {'version': 0, 'current_time': 100, 'accounts': accounts})
    injection = EventDraft(event_type='environment_event', occurred_at=100, source_kind='scenario',
                           data={'kind': 'initial_funds_injected', 'resource_changes': {
                               'account:person:1': 100, 'account:merchant': 200, 'account:external:scenario': -300}})
    store.commit(store.state, [injection])
    yield store
    store.close()


def transfer(changes):
    return EventDraft(event_type='action_result', occurred_at=101, data={'resource_changes': changes})


def test_balanced_transfer_reconstructs_actual_final_accounts(funded):
    state = {'version': 1, 'current_time': 101, 'accounts': {'person:1': 90, 'merchant': 210}}
    funded.commit(state, [transfer({'account:person:1': -10, 'account:merchant': 10})])
    validate_account_history(funded.read_events(funded.last_event_seq), funded.state)
    assert funded.state['accounts'] == state['accounts']


@pytest.mark.parametrize('kind', ['missing', 'duplicated', 'wrong_amount', 'one_sided', 'unknown', 'fraction'])
def test_missing_or_false_ledger_cannot_commit_changed_accounts(funded, kind):
    changes = {'account:person:1': -10, 'account:merchant': 10}
    if kind == 'missing': changes = {}
    if kind == 'wrong_amount': changes = {'account:person:1': -20, 'account:merchant': 20}
    if kind == 'one_sided': changes = {'account:person:1': -10}
    if kind == 'unknown': changes = {'account:person:1': -10, 'account:unknown': 10}
    if kind == 'fraction': changes = {'account:person:1': -10.0, 'account:merchant': 10.0}
    events = [transfer(changes), transfer(changes)] if kind == 'duplicated' else [transfer(changes)]
    with pytest.raises(IntegrityError):
        funded.commit({'version': 1, 'current_time': 101, 'accounts': {'person:1': 90, 'merchant': 210}}, events)
    assert funded.state['accounts'] == {'person:1': 100, 'merchant': 200}
    assert funded.last_event_seq == 1


def test_new_funds_require_explicit_external_counterparty(funded):
    new_state = {'version': 1, 'current_time': 101, 'accounts': {'person:1': 150, 'merchant': 200}}
    funded.commit(new_state, [transfer({'account:person:1': 50, 'account:external:scenario': -50})])
    validate_account_history(funded.read_events(funded.last_event_seq), funded.state)


def test_final_checkpoint_balance_must_equal_complete_event_history(funded):
    wrong = {'accounts': {'person:1': 90, 'merchant': 210}}
    with pytest.raises(IntegrityError, match='reconstruct'):
        validate_account_history(funded.read_events(funded.last_event_seq), wrong)


def test_reused_receipt_cannot_record_a_second_payment(funded):
    state = {'version': 1, 'current_time': 101, 'accounts': {'person:1': 90, 'merchant': 210}}
    funded.commit(state, [transfer({'account:person:1': -10, 'account:merchant': 10})])
    with pytest.raises(IntegrityError):
        funded.commit(state, [transfer({'account:person:1': -10, 'account:merchant': 10})])
    assert funded.last_event_seq == 2
