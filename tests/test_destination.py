from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from wowflipper.models import Auction, Candidate, Valuation
from wowflipper.portfolio import Portfolio, serialize
from wowflipper.repository import Repository
from wowflipper.web import create_app


@pytest.fixture
def portfolio(tmp_path):
    repo = Repository(tmp_path)
    repo.save_settings(replace(repo.load_settings(), selling_realms=('agamaggan', 'area-52'), max_copies=10))
    auction = Auction('us', 1, 1, 100, 3, 1000001, None, datetime.now(timezone.utc), 'toy',
                      {'simple_identity': True, 'tradeability': True})
    valuation = Valuation(100, 'Toy', 2000000, Decimal('.1'), Decimal('1'), 2000000, 2000000,
                          datetime.now(timezone.utc))
    candidate = Candidate(('us', 1, 1), auction, valuation, 6000000, 4999999, Decimal('4'),
                          ('agamaggan',), (), 'ok')
    return Portfolio(repo, lambda key: candidate if tuple(key) == candidate.key else None)


def selected(p, strategy='general'):
    return p.select(('us', 1, 1), strategy, 'agamaggan', 'select')


def test_destination_change_preserves_selection_and_financial_state(portfolio):
    original = selected(portfolio, 'longer-term')
    summary = portfolio.summary()
    changed = portfolio.change_destination(original['key'], 'area-52', 'destination')
    assert changed == dict(original, destination='area-52')
    assert portfolio.shopping()[0]['destination'] == 'area-52'
    assert portfolio.summary() == summary
    assert portfolio.history() == []
    purchase = portfolio.record({'type': 'purchase', 'key': original['key']}, 'purchase')
    assert purchase['destination'] == 'area-52'
    assert purchase['strategy'] == 'longer-term'


def test_destination_replay_after_purchase_and_settings_removal(portfolio):
    original = selected(portfolio)
    changed = portfolio.change_destination(original['key'], 'area-52', 'destination')
    portfolio.record({'type': 'purchase', 'key': original['key']}, 'purchase')
    portfolio.repository.save_settings(replace(portfolio.repository.load_settings(), selling_realms=()))
    assert portfolio.change_destination(original['key'], 'area-52', 'destination') == changed
    with pytest.raises(ValueError, match='different action'):
        portfolio.change_destination(original['key'], 'agamaggan', 'destination')
    with pytest.raises(ValueError, match='different action'):
        portfolio.change_destination(('us', 1, 2), 'area-52', 'destination')
    with pytest.raises(ValueError, match='different action'):
        portfolio.remove(original['key'], 'destination')


@pytest.mark.parametrize('destination', [None, '', 'unknown', 7, [], {}])
def test_invalid_destination_rolls_back_and_request_can_be_retried(portfolio, destination):
    original = selected(portfolio)
    with pytest.raises(ValueError, match='configured'):
        portfolio.change_destination(original['key'], destination, 'destination')
    assert portfolio.shopping()[0]['destination'] == original['destination']
    assert portfolio.change_destination(original['key'], 'area-52', 'destination')['destination'] == 'area-52'


@pytest.mark.parametrize('state', ['missing', 'removed', 'purchased'])
def test_destination_change_requires_current_selection(portfolio, state):
    if state != 'missing':
        selected(portfolio)
    if state == 'removed':
        portfolio.remove(('us', 1, 1), 'remove')
    if state == 'purchased':
        portfolio.record({'type': 'purchase', 'key': ['us', 1, 1]}, 'purchase')
    history = portfolio.history()
    summary = portfolio.summary()
    with pytest.raises(ValueError, match='selected'):
        portfolio.change_destination(('us', 1, 1), 'area-52', 'destination')
    assert portfolio.history() == history
    assert portfolio.summary() == summary


@pytest.mark.parametrize('request_id', [None, '', '   ', 1])
def test_destination_requires_request_id(portfolio, request_id):
    selected(portfolio)
    with pytest.raises(ValueError, match='request ID'):
        portfolio.change_destination(('us', 1, 1), 'area-52', request_id)
    assert portfolio.shopping()[0]['destination'] == 'agamaggan'


@pytest.mark.parametrize('invalidity', ['stale', 'quantity', 'reserve', 'exposure'])
def test_plan_can_be_edited_but_purchase_still_revalidates(portfolio, invalidity):
    original = selected(portfolio)
    settings = portfolio.repository.load_settings()
    if invalidity == 'stale':
        portfolio.candidate_provider = lambda key: None
    elif invalidity == 'quantity':
        portfolio.repository.save_settings(replace(settings, max_copies=2))
    elif invalidity == 'reserve':
        portfolio.repository.save_settings(replace(settings, reserve=settings.capital))
    else:
        portfolio.repository.save_settings(replace(settings, item_exposure=1000000))
    assert portfolio.change_destination(original['key'], 'area-52', 'destination') == dict(original, destination='area-52')
    assert not portfolio.shopping()[0]['valid']
    with pytest.raises(ValueError):
        portfolio.record({'type': 'purchase', 'key': original['key']}, 'purchase')
    assert portfolio.history() == []
    assert portfolio.summary()['cash'] == settings.capital


def test_legacy_destination_can_be_fixed_preserving_additional_fields(portfolio):
    original = selected(portfolio, 'stronger-demand')
    original['legacy_note'] = 'preserve this metadata'
    with portfolio.repository.connect() as db:
        db.execute('UPDATE portfolio_selected SET data=? WHERE key=?', (serialize(original), serialize(original['key'])))
    portfolio.repository.save_settings(replace(portfolio.repository.load_settings(), selling_realms=('area-52',)))
    assert not portfolio.shopping()[0]['valid']
    assert portfolio.change_destination(original['key'], 'area-52', 'destination') == dict(original, destination='area-52')
    assert portfolio.shopping()[0]['valid']


def test_concurrent_destination_retry_is_idempotent(portfolio):
    original = selected(portfolio)
    other = Portfolio(portfolio.repository, portfolio.candidate_provider)
    with ThreadPoolExecutor(2) as pool:
        changed = list(pool.map(lambda p: p.change_destination(original['key'], 'area-52', 'destination'), [portfolio, other]))
    assert changed[0] == changed[1] == dict(original, destination='area-52')
    assert portfolio.history() == []


@pytest.fixture
def demo_client(tmp_path):
    from wowflipper.demo import seed_demo
    seed_demo(tmp_path)
    app = create_app(tmp_path, testing=True)
    repo = app.extensions['repository']
    repo.save_settings(replace(repo.load_settings(), selling_realms=('agamaggan', 'area-52')))
    client = app.test_client()
    headers = {'X-CSRF-Token': client.get('/api/state').json['csrf']}
    row = client.get('/api/results').json['rows'][0]
    assert client.post('/api/shopping', json={'key': row['key'], 'destination': 'agamaggan', 'request_id': 'select'}, headers=headers).status_code == 200
    return client, headers, row


def test_destination_route_and_purchase_replay(demo_client):
    client, headers, row = demo_client
    data = {'key': row['key'], 'destination': 'area-52', 'request_id': 'destination'}
    changed = client.post('/api/shopping/destination', json=data, headers=headers)
    assert changed.status_code == 200
    assert changed.json['destination'] == 'area-52'
    assert client.get('/api/ledger').json == []
    purchase = client.post('/api/ledger', json={'event': {'type': 'purchase', 'key': row['key']}, 'request_id': 'purchase'}, headers=headers)
    assert purchase.status_code == 200
    assert purchase.json['destination'] == 'area-52'
    assert client.post('/api/shopping/destination', json=data, headers=headers).json == changed.json
    assert client.post('/api/shopping/destination', json=dict(data, request_id='new'), headers=headers).status_code == 400


def test_destination_route_guard_and_schema(demo_client):
    client, headers, row = demo_client
    path = '/api/shopping/destination'
    data = {'key': row['key'], 'destination': 'area-52', 'request_id': 'destination'}
    assert client.post(path, json=data).status_code == 403
    assert client.post(path, json=data, headers=dict(headers, Origin='https://example.invalid')).status_code == 403
    assert client.post(path, json=data, headers=dict(headers, Host='evil.invalid')).status_code == 403
    for invalid in (dict(data, destination='unknown'), dict(data, unexpected=1), {'destination': 'area-52'}, dict(data, key=['us', 0, 1])):
        assert client.post(path, json=invalid, headers=headers).status_code == 400
    assert client.get('/api/shopping').json[0]['destination'] == 'agamaggan'
