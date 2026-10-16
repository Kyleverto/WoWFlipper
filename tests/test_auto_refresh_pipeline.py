"""Conditional background checks retain source evidence without rewriting it."""
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import json
import threading
import pytest

from wowflipper.models import Auction, Snapshot, Valuation
from wowflipper.refresh import RefreshService
from wowflipper.repository import Repository
from wowflipper.settings import Settings
from wowflipper.sources.blizzard import BlizzardClient
from wowflipper.sources.http import HTTPClient, Response


def fixture(tmp_path):
    repo=Repository(tmp_path); now=datetime.now(timezone.utc)
    valuation=Valuation(2,'Recipe',10000000,Decimal('.5'),Decimal('5'),10000000,10000000,now)
    repo.publish(Snapshot('tsm:us',now,now,'tsm-header','fixture',(valuation,)))
    realms=[{'slug':slug,'house_id':house,'category':'United States','is_auction_realm':True} for slug,house in [('home',1),('away',2)]]
    repo.cache_put('realms',realms); repo.cache_timed('realms',realms)
    settings=replace(Settings(),buying_realms=('home',),selling_realms=('away',),categories=('recipe',))
    class Client:
        updates={}; checked=[]; item_calls=[]
        def auctions(self,house,previous):
            return Snapshot(f'auctions:us:{house}',now,now,f'header-{house}','fixture',
                (Auction('us',house,house,2,1,1000 if house==1 else 2000,None,now),))
        def auction_update(self,house,previous_header=None):
            self.checked.append((house,previous_header)); return self.updates.get(house)
        def item(self,item): self.item_calls.append(item); return {'category':'recipe','tradeability':True}
    class TSM:
        update=None
        def region(self,previous): return self.update or replace(previous,fetched_at=datetime.now(timezone.utc))
    client=Client(); tsm=TSM(); service=RefreshService(repo,client,tsm)
    service.run(settings)
    return repo,settings,now,client,tsm,service


def test_light_auction_update_304_needs_only_header_and_no_json_decode():
    calls=[]
    def transport(method,url,headers,*args): calls.append(headers); return Response(304,{},b'not JSON')
    client=BlizzardClient(HTTPClient(transport)); client._token='fixture'; client._expires=10**20
    assert client.auction_update(7,'previous-header') is None
    assert calls[0]['If-Modified-Since']=='previous-header'
    with pytest.raises(ValueError): client.auction_update(7)


def test_all_304_skips_house_decoding_metadata_analysis_and_publication(tmp_path,monkeypatch):
    repo,settings,now,client,tsm,service=fixture(tmp_path)
    revision=repo.analysis_revision(); previous_run=service.status_snapshot()['run_id']
    original=repo.latest; calls=[]
    def latest(source,**kwargs):
        calls.append(source)
        assert not source.startswith('auctions:')
        return original(source,**kwargs)
    monkeypatch.setattr(repo,'latest',latest)
    monkeypatch.setattr(repo,'publish',lambda snapshot:pytest.fail('Unchanged checks must not publish'))
    monkeypatch.setattr(repo,'cache_fresh_many',lambda *args:pytest.fail('Unchanged checks must not enrich'))
    status=service.run(settings,changed_only=True)
    assert repo.analysis_revision()==revision and client.item_calls==[2]
    assert sorted(client.checked)==[(1,'header-1'),(2,'header-2')]
    assert status['changed_sources']==[] and status['completed_at'] and status['run_id']!=previous_run
    assert all(status['sources'][key]['state']=='unchanged' for key in ('tsm:us','auctions:us:1','auctions:us:2'))


def test_one_changed_house_retains_unchanged_destination_without_republishing_it(tmp_path):
    from wowflipper.query import MarketQuery
    repo,settings,now,client,tsm,service=fixture(tmp_path)
    old=repo.latest('auctions:us:2'); buy=repo.latest('auctions:us:1')
    client.updates={1:replace(buy,records=(replace(buy.records[0],buyout=500),),raw_payload=b'{"changed":true}')}
    result=service.run(settings,changed_only=True)
    assert result['changed_sources']==['auctions:us:1']
    assert repo.latest('auctions:us:2').id==old.id
    rows=MarketQuery(repo).rows({})
    assert rows[0]['auction']['buyout']==500
    assert rows[0]['comparisons'][0]['listing_count']==1 and rows[0]['comparisons'][0]['lowest_unit_ask']=='2000'


def test_new_valuation_reassesses_retained_auctions_and_only_publishes_changed_classification(tmp_path):
    repo,settings,now,client,tsm,service=fixture(tmp_path)
    prior=repo.latest('tsm:us'); house_ids={h:repo.latest(f'auctions:us:{h}').id for h in (1,2)}
    tsm.update=replace(prior,records=(replace(prior.records[0],avg_sale_price=20000000),),raw_payload=b'new CSV')
    result=service.run(settings,changed_only=True)
    assert result['changed_sources']==['tsm:us']
    assert all(repo.latest(f'auctions:us:{h}').id==house_ids[h] for h in (1,2))
    assert repo.latest('tsm:us').records[0].avg_sale_price==20000000


def test_settings_change_reassesses_retained_unknown_item_when_every_house_304(tmp_path):
    repo,settings,now,client,tsm,service=fixture(tmp_path)
    for house in (1,2):
        old=repo.latest(f'auctions:us:{house}')
        repo.publish(replace(old,records=(replace(old.records[0],category='unknown',metadata={'simple_identity':False}),)))
    result=service.run(replace(settings,min_profit=settings.min_profit+1),changed_only=True)
    assert set(result['changed_sources'])=={'auctions:us:1','auctions:us:2'}
    assert all(repo.latest(f'auctions:us:{h}').records[0].category=='recipe' for h in (1,2))


def test_light_provenance_excludes_records_and_payload_and_preference_only_revision_is_stable(tmp_path,monkeypatch):
    repo,settings,now,client,tsm,service=fixture(tmp_path)
    entry=repo.latest_provenance('auctions:us:1')
    assert entry['http_last_modified']=='header-1' and entry['snapshot_id']
    assert 'records' not in entry and 'raw_payload' not in entry
    assert repo.latest_provenance('missing') is None
    previous=repo.analysis_revision()
    # Scheduler fields are available once Task B adds them; raw cache exercise
    # keeps this repository contract independent of its concurrent implementation.
    prefs=settings.to_dict(); prefs.update(auto_refresh=True,sound_alerts=True,desktop_alerts=True)
    repo.cache_put('settings',prefs)
    assert repo.analysis_revision()==previous
    repo.cache_put('settings',{**prefs,'min_profit':settings.min_profit+1})
    assert repo.analysis_revision()!=previous


def test_new_buy_metadata_updates_retained_destination_competition(tmp_path):
    from wowflipper.query import MarketQuery
    repo,settings,now,client,tsm,service=fixture(tmp_path)
    region=repo.latest('tsm:us')
    tsm.update=replace(region,records=region.records+(replace(region.records[0],item_id=3,name='New recipe'),),raw_payload=b'CSV with another item')
    destination=repo.latest('auctions:us:2')
    unknown=Auction('us',2,30,3,1,3000,None,now,'unknown',{'simple_identity':True,'tradeability':None})
    repo.publish(replace(destination,records=destination.records+(unknown,)))
    buy=repo.latest('auctions:us:1')
    client.updates={1:replace(buy,records=buy.records+(replace(unknown,house_id=1,auction_id=31,buyout=1500,metadata=None,category=None),),raw_payload=b'new buy item')}
    result=service.run(settings,changed_only=True)
    assert set(result['changed_sources'])=={'tsm:us','auctions:us:1','auctions:us:2'}
    assert client.item_calls==[2,3]
    assert repo.latest('auctions:us:2').records[-1].category=='recipe'
    row=next(row for row in MarketQuery(repo).rows({}) if row['auction']['item_id']==3)
    assert row['comparisons'][0]['listing_count']==1 and row['comparisons'][0]['lowest_unit_ask']=='3000'


def test_repeated_200_with_same_normalized_source_preserves_snapshot_ids(tmp_path):
    repo,settings,now,client,tsm,service=fixture(tmp_path)
    previous={house:repo.latest(f'auctions:us:{house}') for house in (1,2)}
    client.updates={house:replace(snapshot,fetched_at=now+timedelta(seconds=120),raw_payload=b'different formatting') for house,snapshot in previous.items()}
    result=service.run(settings,changed_only=True)
    assert result['changed_sources']==[]
    assert all(repo.latest(f'auctions:us:{house}').id==snapshot.id for house,snapshot in previous.items())
    assert all(result['sources'][f'auctions:us:{house}']['state']=='unchanged' for house in previous)


def test_preference_only_changes_do_not_reprocess_unchanged_houses(tmp_path,monkeypatch):
    repo,settings,now,client,tsm,service=fixture(tmp_path)
    prefs=replace(settings,auto_refresh=True,sound_alerts=True,desktop_alerts=True)
    original=repo.latest
    def latest(source,**kwargs):
        assert not source.startswith('auctions:')
        return original(source,**kwargs)
    monkeypatch.setattr(repo,'latest',latest)
    result=service.run(prefs,changed_only=True)
    assert result['changed_sources']==[]


def test_stale_source_timestamp_is_not_freshened_by_successful_304(tmp_path):
    repo,settings,now,client,tsm,service=fixture(tmp_path)
    stale=repo.latest('auctions:us:1')
    source_time=now-timedelta(days=1)
    repo.publish(replace(stale,source_time=source_time,records=tuple(replace(row,source_time=source_time) for row in stale.records)))
    identity=repo.latest('auctions:us:1').id
    result=service.run(settings,changed_only=True)
    retained=repo.latest('auctions:us:1')
    assert result['sources']['auctions:us:1']['state']=='unchanged'
    assert retained.id==identity and retained.source_time==source_time


def test_light_update_200_keeps_raw_provenance_and_validates_schema():
    raw=json.dumps({'auctions':[{'id':1,'item':{'id':2,'context':0},'quantity':1,'buyout':1000}]}).encode()
    modified='Mon, 12 Oct 2026 00:00:00 GMT'
    client=BlizzardClient(HTTPClient(lambda *args:Response(200,{'Last-Modified':modified},raw)))
    client._token='fixture'; client._expires=10**20
    snapshot=client.auction_update(7)
    assert snapshot.raw_payload==raw and snapshot.http_last_modified==modified
    assert snapshot.records[0].house_id==7 and snapshot.records[0].source_time==snapshot.source_time
    assert snapshot.records[0].variant_key=='{"context":0}'
    client.http=HTTPClient(lambda *args:Response(200,{},b'{"not_auctions":[]}'))
    with pytest.raises(ValueError): client.auction_update(7)


def test_async_conditional_check_and_manual_refresh_share_singleton_lock(tmp_path):
    repo,settings,now,client,tsm,service=fixture(tmp_path)
    entered=threading.Event(); gate=threading.Event()
    def update(house,previous_header=None):
        entered.set(); assert gate.wait(5); return None
    client.auction_update=update
    task=service.start(settings,changed_only=True)
    try:
        assert entered.wait(5)
        with pytest.raises(RuntimeError,match='already running'): service.run(settings)
        with pytest.raises(RuntimeError,match='already running'): service.start(settings,changed_only=True)
    finally: gate.set()
    result=task.result(5)
    assert result['completed_at'] and not result['running'] and result['changed_sources']==[]


def test_conditional_global_failure_defers_unstarted_houses_and_keeps_old_snapshots(tmp_path):
    from wowflipper.sources.http import SourceError
    repo,settings,now,client,tsm,service=fixture(tmp_path)
    realms=[{'slug':f'h{house}','house_id':house,'category':'United States','is_auction_realm':True} for house in range(1,6)]
    repo.cache_put('realms',realms); repo.cache_timed('realms',realms)
    old={house:repo.publish(Snapshot(f'auctions:us:{house}',now,now,f'h-{house}','fixture',())) for house in range(1,6)}
    barrier=threading.Barrier(3); calls=[]
    def update(house,previous_header=None):
        calls.append(house); barrier.wait(timeout=5)
        raise SourceError('private rate limit response',code='rate_limited',status=429)
    client.auction_update=update
    result=service.run(replace(settings,buying_realms=tuple(r['slug'] for r in realms),selling_realms=()),changed_only=True)
    assert sorted(calls)==[1,2,3]
    for house in range(1,6):
        source=result['sources'][f'auctions:us:{house}']
        assert source['state']==('failed' if house<=3 else 'skipped')
        assert source['error_code']=='rate_limited'
        assert repo.latest(f'auctions:us:{house}').id==old[house]
    assert result['changed_sources']==[] and 'private' not in json.dumps(result)


def test_all_304_stable_configuration_does_not_drain_toy_warmup(tmp_path):
    repo,settings,now,client,tsm,service=fixture(tmp_path)
    repo.cache_put('toys:us',{'items':[2],'remaining':list(range(100))})
    settings=replace(settings,categories=('recipe','toy'))
    client.toy_items=lambda *args,**kwargs:{'items':[2],'pending':100}
    service.run(settings)
    client.toy_items=lambda *args,**kwargs:pytest.fail('Unchanged checks must not drain toy metadata')
    result=service.run(settings,changed_only=True)
    assert result['sources']['toys:us']['state']=='unchanged'
    assert result['sources']['toys:us']['pending']==100


def test_changed_auto_check_advances_one_bounded_toy_batch_then_reclassifies_items(tmp_path):
    repo,settings,now,client,tsm,service=fixture(tmp_path)
    settings=replace(settings,categories=('recipe','toy'))
    repo.cache_timed('item:us:2',{'category':'unknown','tradeability':True})
    calls=[]
    def toys(repository):
        calls.append(True)
        return {'items':[2],'pending':100,'details_fetched':50,'details_attempted':50}
    client.toy_items=toys
    result=service.run(settings,changed_only=True)
    assert len(calls)==1
    assert result['sources']['toys:us']['details_attempted']==50 and result['sources']['toys:us']['pending']==100
    assert all(repo.latest(f'auctions:us:{house}').records[0].category=='toy' for house in (1,2))
