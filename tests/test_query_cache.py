from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import threading
from wowflipper.models import Auction, Snapshot, Valuation
from wowflipper.query import MarketQuery
from wowflipper.repository import Repository
from wowflipper.web import create_app


def fixture_repo(tmp_path):
    repo = Repository(tmp_path)
    now = datetime.now(timezone.utc) - timedelta(minutes=1)
    repo.cache_put('realms', [{'slug': 'agamaggan', 'house_id': 1}])
    value = Valuation(2, 'Recipe', 10000000, Decimal('.2'), Decimal('2'), 10000000, 10000000, now)
    auction = Auction('us', 1, 1, 2, 1, 1000000, None, category='recipe', metadata={'simple_identity': True, 'tradeability': True})
    repo.publish(Snapshot('tsm:us', now, now, None, 'tsm', (value,)))
    repo.publish(Snapshot('auctions:us:1', now, now, None, 'blizzard', (auction,)))
    return repo, now


def count_reads(repo, monkeypatch):
    reads = []
    original = repo.latest
    def latest(*args, **kwargs):
        reads.append(args[0])
        return original(*args, **kwargs)
    monkeypatch.setattr(repo, 'latest', latest)
    return reads


def test_reuses_analysis_and_invalidates_settings_snapshots_and_realms(tmp_path, monkeypatch):
    repo, _ = fixture_repo(tmp_path)
    reads = count_reads(repo, monkeypatch)
    first = MarketQuery(repo).page({})
    assert first['total'] == 1
    assert MarketQuery(repo).page({}) == first
    assert len(reads) == 3
    repo.save_settings(replace(repo.load_settings(), min_profit=20000000))
    assert MarketQuery(repo).page({})['total'] == 0
    assert len(reads) == 6
    repo.save_settings(replace(repo.load_settings(), min_profit=1))
    snap = repo.latest('tsm:us')
    repo.publish(replace(snap, source_time=None))
    assert MarketQuery(repo).page({})['total'] == 0
    repo.cache_put('realms', [])
    assert MarketQuery(repo).exclusions_page({})['total'] == 0


def test_concurrent_requests_build_once(tmp_path, monkeypatch):
    repo, _ = fixture_repo(tmp_path)
    reads = count_reads(repo, monkeypatch)
    barrier = threading.Barrier(6)
    def request(_):
        barrier.wait()
        return MarketQuery(repo).page({})
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(request, range(6)))
    assert all(result == results[0] for result in results)
    assert len(reads) == 3


def test_exact_freshness_boundary_and_clock_rewind(tmp_path, monkeypatch):
    import wowflipper.query as module
    repo, source_time = fixture_repo(tmp_path)
    current = [source_time + timedelta(hours=2)]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return current[0]
    monkeypatch.setattr(module, 'datetime', Clock)
    assert MarketQuery(repo).page({})['total'] == 1
    current[0] += timedelta(microseconds=1)
    assert MarketQuery(repo).page({})['total'] == 0
    current[0] -= timedelta(minutes=1)
    assert MarketQuery(repo).page({})['total'] == 1


def test_state_uses_metadata_without_loading_records_or_payload(tmp_path, monkeypatch):
    fixture_repo(tmp_path)
    app = create_app(tmp_path, testing=True)
    def forbidden(*args, **kwargs): raise AssertionError('state loaded full snapshot')
    monkeypatch.setattr(app.extensions['repository'], 'latest', forbidden)
    response = app.test_client().get('/api/state')
    assert response.status_code == 200
    assert len(response.json['sources']) == 2


def test_publication_by_another_repository_invalidates(tmp_path):
    repo, _ = fixture_repo(tmp_path)
    assert MarketQuery(repo).page({})['total'] == 1
    writer = Repository(tmp_path)
    snapshot = writer.latest('auctions:us:1')
    writer.publish(replace(snapshot, records=()))
    assert MarketQuery(repo).page({})['total'] == 0


def test_publication_during_build_retries_consistent_revision(tmp_path, monkeypatch):
    repo, _ = fixture_repo(tmp_path)
    original = repo.latest
    published = []
    def latest(source, **kwargs):
        snapshot = original(source, **kwargs)
        if source == 'tsm:us' and not published:
            published.append(repo.publish(replace(snapshot, source_time=None)))
        return snapshot
    monkeypatch.setattr(repo, 'latest', latest)
    assert MarketQuery(repo).page({})['total'] == 0
    assert MarketQuery(repo).page({})['total'] == 0


def test_record_freshness_expires_before_snapshot(tmp_path, monkeypatch):
    import wowflipper.query as module
    repo, now = fixture_repo(tmp_path)
    snapshot = repo.latest('tsm:us')
    repo.publish(replace(snapshot, records=(replace(snapshot.records[0], updated_at=now-timedelta(hours=47)),)))
    current = [now + timedelta(hours=1)]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return current[0]
    monkeypatch.setattr(module, 'datetime', Clock)
    assert MarketQuery(repo).page({})['total'] == 1
    current[0] += timedelta(microseconds=1)
    assert MarketQuery(repo).page({})['total'] == 0


def test_destination_expiry_and_query_options_do_not_load_snapshots(tmp_path, monkeypatch):
    import wowflipper.query as module
    repo, now = fixture_repo(tmp_path)
    repo.cache_put('realms', [{'slug':'agamaggan','house_id':1}, {'slug':'destination','house_id':2}])
    repo.save_settings(replace(repo.load_settings(), selling_realms=('destination',)))
    snapshot = repo.latest('auctions:us:1')
    repo.publish(replace(snapshot, source_key='auctions:us:2', source_time=now-timedelta(hours=1), records=(replace(snapshot.records[0], house_id=2, auction_id=2),)))
    current = [now + timedelta(hours=1)]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return current[0]
    monkeypatch.setattr(module, 'datetime', Clock)
    reads = count_reads(repo, monkeypatch)
    comparison = MarketQuery(repo).page({})['rows'][0]['comparisons'][0]
    assert comparison['listing_count'] == 1
    assert MarketQuery(repo).page({'q':'nothing'})['total'] == 0
    assert len(reads) == 4
    current[0] += timedelta(microseconds=1)
    comparison = MarketQuery(repo).page({})['rows'][0]['comparisons'][0]
    assert comparison['listing_count'] == 0
    assert 'stale_destination_evidence' in comparison['risks']
    assert len(reads) == 8


def test_analysis_does_not_load_or_retain_raw_payloads(tmp_path, monkeypatch):
    repo, _ = fixture_repo(tmp_path)
    from pathlib import Path
    def forbidden(*args, **kwargs): raise AssertionError('read raw payload')
    monkeypatch.setattr(Path, 'read_bytes', forbidden)
    query = MarketQuery(repo)
    assert query.page({})['total'] == 1
    assert query.tsm.raw_payload == b''
    assert all(snapshot.raw_payload == b'' for snapshot in query.snapshots.values())


def test_settings_preserve_existing_undiscovered_realms(tmp_path):
    repo, _ = fixture_repo(tmp_path)
    repo.save_settings(replace(repo.load_settings(), buying_realms=('legacy-realm',)))
    client = create_app(tmp_path, testing=True).test_client()
    csrf = client.get('/api/state').json['csrf']
    headers = {'X-CSRF-Token':csrf}
    response = client.post('/api/settings', json={'min_profit':2}, headers=headers)
    assert response.status_code == 200
    assert response.json['buying_realms'] == ['legacy-realm']
    response = client.post('/api/settings', json={'buying_realms':['arbitrary-unknown']}, headers=headers)
    assert response.status_code == 400


def test_new_memberships_require_verified_us_realms(tmp_path):
    repo, _ = fixture_repo(tmp_path)
    repo.cache_put('realms', [{'slug':'agamaggan','house_id':1,'category':'Oceania'}, {'slug':'verified-us','house_id':2,'category':'United States','is_auction_realm':True}, {'slug':'unknown','house_id':3}])
    client = create_app(tmp_path, testing=True).test_client()
    csrf = client.get('/api/state').json['csrf']
    headers = {'X-CSRF-Token':csrf}
    assert client.post('/api/settings', json={'min_profit':3}, headers=headers).status_code == 200
    assert client.post('/api/settings', json={'selling_realms':['agamaggan']}, headers=headers).status_code == 400
    assert client.post('/api/settings', json={'selling_realms':['unknown']}, headers=headers).status_code == 400
    assert client.post('/api/settings', json={'selling_realms':['verified-us']}, headers=headers).status_code == 200


def test_extreme_age_settings_do_not_overflow_cache_deadline(tmp_path):
    repo, _ = fixture_repo(tmp_path)
    repo.save_settings(replace(repo.load_settings(), max_auction_age_hours=100000000, max_tsm_age_hours=100000000))
    assert MarketQuery(repo).page({})['total'] == 1
    assert MarketQuery(repo).page({})['total'] == 1


def test_cold_build_crossing_valuation_deadline_is_retried(tmp_path, monkeypatch):
    import wowflipper.query as module
    repo, now = fixture_repo(tmp_path)
    repo.save_settings(replace(repo.load_settings(), max_auction_age_hours=72))
    current = [now + timedelta(hours=48)-timedelta(microseconds=1)]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return current[0]
    monkeypatch.setattr(module, 'datetime', Clock)
    original = module.index_destinations
    def slow_index(snapshots):
        current[0] = now + timedelta(hours=48, microseconds=1)
        return original(snapshots)
    monkeypatch.setattr(module, 'index_destinations', slow_index)
    assert MarketQuery(repo).page({})['total'] == 0


def test_category_selection_filters_before_totals_pages_and_csv(tmp_path):
    import csv, io
    repo, _ = fixture_repo(tmp_path)
    repo.save_settings(replace(repo.load_settings(), categories=('recipe','mount','toy')))
    auctions = repo.latest('auctions:us:1')
    values = repo.latest('tsm:us')
    repo.publish(replace(auctions, records=tuple(replace(auctions.records[0], auction_id=i, item_id=i+10, category=category) for i, category in enumerate(('recipe','toy','mount','recipe'), 1))))
    repo.publish(replace(values, records=tuple(replace(values.records[0], item_id=i+10, name=name) for i, name in enumerate(('A Recipe','B Toy','C Mount','D Recipe'), 1))))
    client = create_app(tmp_path, testing=True).test_client()
    def get(query): return client.get('/api/results'+query)
    query='?categories=recipe,mount&sort=name&direction=asc&page_size=2&page=2'
    result=get(query).json
    assert result['total']==3
    assert result['pages']==2
    assert [r['valuation']['name'] for r in result['rows']]==['D Recipe']
    exported=list(csv.DictReader(io.StringIO(client.get('/api/export'+query).text)))
    assert [r['Item'] for r in exported]==['D Recipe']
    assert get('?category=toy').json['total']==1
    assert get('?category=all').json['total']==4
    assert get('').json['total']==4
    assert get('?categories=mount&category=toy').json['rows'][0]['auction']['category']=='mount'
    empty=get('?categories=&category=toy').json
    assert empty['rows']==[] and empty['total']==0 and empty['pages']==1
    assert list(csv.DictReader(io.StringIO(client.get('/api/export?categories=').text)))==[]
    for invalid in ('unknown','recipe,unknown','recipe,,mount','all'):
        assert get('?categories='+invalid).status_code==400
        assert client.get('/api/export?categories='+invalid).status_code==400
